"""ثبت Heartbeat و نمونه‌های واقعی Agent."""

from __future__ import annotations

from peewee import IntegrityError

from app.core.settings import settings
from app.core.errors import AppError
from app.db.database import db
from app.models import (
    NetworkCounterSample,
    NetworkInterface,
    Node,
    NodeMetricSample,
    RequestNonce,
    TrafficInterval,
)
from app.schemas.ingest import HeartbeatIn, MetricsIn
from app.services.alerts import evaluate_sample, evaluate_traffic_spike, recover
from app.services.present import percent
from app.services.traffic import CounterPoint, compute_traffic_delta
from app.utils.time import as_utc, isoformat, utcnow


def _checked_time(collected_at) -> tuple:
    collected = as_utc(collected_at)
    now = utcnow()
    if (collected - now).total_seconds() > settings.max_clock_skew_seconds:
        raise AppError("clock_skew", "The sample time is in the future.", 422)
    if (now - collected).total_seconds() > max(settings.max_clock_skew_seconds, 3600):
        raise AppError("stale_sample", "The sample is older than allowed.", 422)
    return collected, now


def _remember_nonce(node: Node, nonce: str, now) -> None:
    exists = RequestNonce.select().where((RequestNonce.node == node) & (RequestNonce.nonce == nonce)).exists()
    if exists:
        raise AppError("replay", "This request was already recorded.", 409)
    RequestNonce.create(node=node, nonce=nonce, created_at=now)


def ingest_heartbeat(node: Node, payload: HeartbeatIn, header_nonce: str) -> dict:
    if payload.nonce != header_nonce:
        raise AppError("unauthorized", "The request nonce does not match.", 401)
    collected, now = _checked_time(payload.collected_at)
    duplicate = RequestNonce.select().where((RequestNonce.node == node) & (RequestNonce.nonce == payload.nonce)).exists()
    if duplicate:
        return {"duplicate": True, "collected_at": isoformat(collected)}
    with db.atomic():
        _remember_nonce(node, payload.nonce, now)
        node.last_seen_at = now
        node.agent_version = payload.agent_version
        if payload.os_name:
            node.os_name = payload.os_name
        if payload.kernel_version:
            node.kernel_version = payload.kernel_version
        node.updated_at = now
        node.save()
    recover(node, "offline", "The node is connected again.")
    recover(node, "auth_error", "Node authentication succeeded again.")
    return {"duplicate": False, "collected_at": isoformat(collected), "received_at": isoformat(now)}


def ingest_metrics(node: Node, payload: MetricsIn, header_nonce: str) -> dict:
    if payload.nonce != header_nonce:
        raise AppError("unauthorized", "The request nonce does not match.", 401)
    collected, now = _checked_time(payload.collected_at)
    if NodeMetricSample.select().where((NodeMetricSample.node == node) & (NodeMetricSample.collected_at == collected)).exists():
        return {"duplicate": True, "collected_at": isoformat(collected)}
    interfaces = [item for item in payload.interfaces if item.name != "lo"]
    if not interfaces:
        raise AppError("no_interface", "No network interface other than loopback was sent.", 422)
    names = [item.name for item in interfaces]
    if len(names) != len(set(names)):
        raise AppError("duplicate_interface", "A network interface name was repeated.", 422)
    try:
        with db.atomic():
            _remember_nonce(node, payload.nonce, now)
            sample = NodeMetricSample.create(
                node=node,
                collected_at=collected,
                received_at=now,
                cpu_percent=payload.cpu_percent,
                ram_total_bytes=payload.ram_total_bytes,
                ram_used_bytes=payload.ram_used_bytes,
                ram_free_bytes=payload.ram_free_bytes,
                disk_total_bytes=payload.disk_total_bytes,
                disk_used_bytes=payload.disk_used_bytes,
                disk_free_bytes=payload.disk_free_bytes,
                load_1=payload.load_1,
                load_5=payload.load_5,
                load_15=payload.load_15,
                uptime_seconds=payload.uptime_seconds,
                agent_version=payload.agent_version,
                os_name=payload.os_name,
                kernel_version=payload.kernel_version,
            )
            previous_sample = (
                NodeMetricSample.select()
                .where((NodeMetricSample.node == node) & (NodeMetricSample.collected_at < collected))
                .order_by(NodeMetricSample.collected_at.desc())
                .first()
            )
            valid_rx = 0
            valid_tx = 0
            saw_valid = False
            saw_gap = False
            saw_reset = False
            for item in interfaces:
                interface, _created = NetworkInterface.get_or_create(node=node, name=item.name)
                previous = (
                    NetworkCounterSample.select()
                    .where(
                        (NetworkCounterSample.interface == interface)
                        & (NetworkCounterSample.collected_at < collected)
                    )
                    .order_by(NetworkCounterSample.collected_at.desc())
                    .first()
                )
                NetworkCounterSample.create(
                    node=node,
                    interface=interface,
                    metric_sample=sample,
                    collected_at=collected,
                    rx_bytes=item.rx_bytes,
                    tx_bytes=item.tx_bytes,
                    rx_packets=item.rx_packets,
                    tx_packets=item.tx_packets,
                    rx_errors=item.rx_errors,
                    tx_errors=item.tx_errors,
                    rx_dropped=item.rx_dropped,
                    tx_dropped=item.tx_dropped,
                )
                if previous is None:
                    continue
                delta = compute_traffic_delta(
                    CounterPoint(previous.collected_at, previous.rx_bytes, previous.tx_bytes),
                    CounterPoint(collected, item.rx_bytes, item.tx_bytes),
                    max_gap_seconds=settings.max_sample_gap_seconds,
                    max_plausible_bps=settings.max_plausible_bps,
                )
                TrafficInterval.create(
                    node=node,
                    interface_name=item.name,
                    period_start=previous.collected_at,
                    period_end=collected,
                    duration_seconds=max(delta.duration_seconds, 0),
                    rx_bytes=delta.rx_bytes,
                    tx_bytes=delta.tx_bytes,
                    rx_bps=delta.rx_bps,
                    tx_bps=delta.tx_bps,
                    reset_detected=delta.reset_detected,
                    valid=delta.valid,
                    reason=delta.reason,
                )
                if delta.valid and delta.rx_bytes is not None and delta.tx_bytes is not None:
                    saw_valid = True
                    saw_reset = saw_reset or delta.reset_detected
                    valid_rx += delta.rx_bytes
                    valid_tx += delta.tx_bytes
                elif delta.reason == "gap":
                    saw_gap = True
            duration = None
            if previous_sample is not None:
                duration = (collected - previous_sample.collected_at).total_seconds()
            node_interval = None
            if duration is not None and duration > settings.max_sample_gap_seconds:
                node_interval = TrafficInterval.create(
                    node=node,
                    interface_name="*",
                    period_start=previous_sample.collected_at,
                    period_end=collected,
                    duration_seconds=duration,
                    rx_bytes=None,
                    tx_bytes=None,
                    rx_bps=None,
                    tx_bps=None,
                    reset_detected=False,
                    valid=False,
                    reason="gap",
                )
            elif saw_valid and duration and duration > 0:
                node_interval = TrafficInterval.create(
                    node=node,
                    interface_name="*",
                    period_start=previous_sample.collected_at if previous_sample else collected,
                    period_end=collected,
                    duration_seconds=duration,
                    rx_bytes=valid_rx,
                    tx_bytes=valid_tx,
                    rx_bps=valid_rx / duration,
                    tx_bps=valid_tx / duration,
                    reset_detected=saw_reset,
                    valid=True,
                    reason="reset" if saw_reset else "ok",
                )
            elif saw_gap and duration:
                node_interval = TrafficInterval.create(
                    node=node,
                    interface_name="*",
                    period_start=previous_sample.collected_at,
                    period_end=collected,
                    duration_seconds=duration,
                    rx_bytes=None,
                    tx_bytes=None,
                    rx_bps=None,
                    tx_bps=None,
                    reset_detected=False,
                    valid=False,
                    reason="gap",
                )
            node.last_seen_at = now
            node.agent_version = payload.agent_version
            node.os_name = payload.os_name
            node.kernel_version = payload.kernel_version
            node.last_cpu_percent = payload.cpu_percent
            node.last_ram_total_bytes = payload.ram_total_bytes
            node.last_ram_used_bytes = payload.ram_used_bytes
            node.last_ram_free_bytes = payload.ram_free_bytes
            node.last_disk_total_bytes = payload.disk_total_bytes
            node.last_disk_used_bytes = payload.disk_used_bytes
            node.last_disk_free_bytes = payload.disk_free_bytes
            node.last_load_1 = payload.load_1
            node.last_load_5 = payload.load_5
            node.last_load_15 = payload.load_15
            node.last_uptime_seconds = payload.uptime_seconds
            if node_interval is not None and node_interval.valid:
                node.last_rx_bps = node_interval.rx_bps
                node.last_tx_bps = node_interval.tx_bps
            node.updated_at = now
            node.save()
    except IntegrityError:
        return {"duplicate": True, "collected_at": isoformat(collected)}

    ram_pct = percent(payload.ram_used_bytes, payload.ram_total_bytes)
    disk_pct = percent(payload.disk_used_bytes, payload.disk_total_bytes)
    evaluate_sample(node, cpu=payload.cpu_percent, ram_percent=ram_pct, disk_percent=disk_pct)
    if node_interval is not None and node_interval.valid and node_interval.rx_bytes is not None:
        evaluate_traffic_spike(
            node,
            node_interval.rx_bytes,
            node_interval.tx_bytes or 0,
            node_interval.rx_bps or 0,
            node_interval.tx_bps or 0,
        )
    recover(node, "offline", "The node is connected again.")
    recover(node, "auth_error", "Node authentication succeeded again.")
    recover(node, "ingest_error", "Node reports are being stored again.")
    return {
        "duplicate": False,
        "collected_at": isoformat(collected),
        "received_at": isoformat(now),
        "traffic_valid": bool(node_interval and node_interval.valid),
        "gap": bool(node_interval and node_interval.reason == "gap"),
    }


def note_ingest_error(node: Node, message: str) -> None:
    from app.services.alerts import raise_alert

    raise_alert(
        node=node,
        alert_type="ingest_error",
        severity="warning",
        message=message or "The node report could not be stored.",
    )
