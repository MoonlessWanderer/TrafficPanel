"""هشدار با جلوگیری از تکرار، دوره سکوت و ثبت بازیابی."""

from __future__ import annotations

from datetime import timedelta

from app.core.settings import settings
from app.models import Alert, Node
from app.services.audit import write_audit
from app.utils.jsonbox import dump_json
from app.utils.time import utcnow


def _fingerprint(alert_type: str, node: Node | None) -> str:
    identity = node.node_id if node is not None else "panel"
    return f"{alert_type}:{identity}"


def raise_alert(
    *,
    node: Node | None,
    alert_type: str,
    severity: str,
    message: str,
    details: dict | None = None,
) -> Alert | None:
    now = utcnow()
    fingerprint = _fingerprint(alert_type, node)
    current = (
        Alert.select()
        .where((Alert.fingerprint == fingerprint) & (Alert.status == "open"))
        .first()
    )
    if current is not None:
        current.last_triggered_at = now
        current.save()
        return current
    cooling = (
        Alert.select()
        .where((Alert.fingerprint == fingerprint) & (Alert.cooldown_until.is_null(False)) & (Alert.cooldown_until > now))
        .order_by(Alert.id.desc())
        .first()
    )
    if cooling is not None:
        return None
    alert = Alert.create(
        node=node,
        alert_type=alert_type,
        severity=severity,
        status="open",
        message=message,
        fingerprint=fingerprint,
        opened_at=now,
        last_triggered_at=now,
        cooldown_until=now + timedelta(seconds=settings.alert_cooldown_seconds),
        details=dump_json(details or {}),
    )
    write_audit(
        actor_type="system",
        actor_id=None,
        action="alert_opened",
        entity_type="alert",
        entity_id=str(alert.id),
        details={"type": alert_type, "node_id": node.node_id if node else None},
    )
    return alert


def resolve_alert_row(alert: Alert, resolution: str, *, actor_type: str = "system", actor_id: str | None = None) -> Alert:
    if alert.status == "resolved":
        return alert
    now = utcnow()
    alert.status = "resolved"
    alert.resolved_at = now
    alert.resolution = resolution
    alert.cooldown_until = now + timedelta(seconds=settings.alert_cooldown_seconds)
    alert.save()
    write_audit(
        actor_type=actor_type,
        actor_id=actor_id,
        action="alert_resolved",
        entity_type="alert",
        entity_id=str(alert.id),
        details={"type": alert.alert_type, "resolution": resolution},
    )
    return alert


def recover(node: Node | None, alert_type: str, resolution: str) -> None:
    fingerprint = _fingerprint(alert_type, node)
    current = (
        Alert.select()
        .where((Alert.fingerprint == fingerprint) & (Alert.status == "open"))
        .first()
    )
    if current is not None:
        resolve_alert_row(current, resolution)


def scan_connections() -> None:
    now = utcnow()
    offline_after = timedelta(seconds=settings.offline_after_seconds)
    for node in Node.select():
        if not node.is_enabled:
            recover(node, "offline", "The node is disabled, so the connection alert was closed.")
            continue
        seen = node.last_seen_at
        if seen is None:
            if now - node.created_at >= offline_after:
                raise_alert(
                    node=node,
                    alert_type="offline",
                    severity="warning",
                    message="The node is not connected.",
                    details={"reason": "never_reported"},
                )
            continue
        if now - seen > offline_after:
            raise_alert(
                node=node,
                alert_type="offline",
                severity="warning",
                message="The node is not connected.",
                details={"last_seen_at": seen.isoformat()},
            )
        else:
            recover(node, "offline", "The node is connected again.")


def evaluate_sample(node: Node, *, cpu: float, ram_percent: float | None, disk_percent: float | None) -> None:
    hysteresis = settings.alert_hysteresis
    if cpu >= settings.cpu_alert_percent:
        raise_alert(node=node, alert_type="high_cpu", severity="warning", message="CPU usage is above the configured threshold.", details={"cpu": round(cpu, 2)})
    elif cpu < settings.cpu_alert_percent * hysteresis:
        recover(node, "high_cpu", "CPU usage returned to the normal range.")
    if ram_percent is not None:
        if ram_percent >= settings.ram_alert_percent:
            raise_alert(node=node, alert_type="high_ram", severity="warning", message="RAM usage is above the configured threshold.", details={"ram_percent": round(ram_percent, 2)})
        elif ram_percent < settings.ram_alert_percent * hysteresis:
            recover(node, "high_ram", "RAM usage returned to the normal range.")
    if disk_percent is not None:
        if disk_percent >= settings.disk_alert_percent:
            raise_alert(node=node, alert_type="disk_full", severity="critical", message="Disk space is running out.", details={"disk_percent": round(disk_percent, 2)})
        elif disk_percent < settings.disk_alert_percent * hysteresis:
            recover(node, "disk_full", "Free disk space returned to a safe range.")


def evaluate_traffic_spike(node: Node, rx_bytes: int, tx_bytes: int, rx_bps: float, tx_bps: float) -> None:
    from app.models import TrafficInterval

    total = rx_bytes + tx_bytes
    if total < settings.traffic_spike_min_bytes:
        recover(node, "traffic_spike", "Traffic volume returned to its recent pattern.")
        return
    previous = list(
        TrafficInterval.select()
        .where(
            (TrafficInterval.node == node)
            & (TrafficInterval.interface_name == "*")
            & (TrafficInterval.valid == True)  # noqa: E712
        )
        .order_by(TrafficInterval.period_end.desc())
        .limit(13)
    )
    # آخرین ردیف همین نمونه است.
    baseline = [row for row in previous[1:13] if row.rx_bps is not None and row.tx_bps is not None]
    if len(baseline) < 4:
        return
    speeds = sorted((row.rx_bps or 0) + (row.tx_bps or 0) for row in baseline)
    median = speeds[len(speeds) // 2]
    current_speed = rx_bps + tx_bps
    if median > 0 and current_speed > median * settings.traffic_spike_multiplier:
        raise_alert(
            node=node,
            alert_type="traffic_spike",
            severity="warning",
            message="Traffic on this node is unusually high compared with its recent pattern.",
            details={"current_bps": round(current_speed, 2), "median_bps": round(median, 2)},
        )
    else:
        recover(node, "traffic_spike", "Node traffic returned to its recent pattern.")
