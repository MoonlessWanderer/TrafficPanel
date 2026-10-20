"""آمار، نمودار و فیلتر. تجمیع در SQL انجام می‌شود و تاریخچه خام به حافظه نمی‌آید."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from peewee import fn

from app.core.settings import settings
from app.core.errors import AppError
from app.db.database import db
from app.models import (
    Alert,
    AuditLog,
    NetworkCounterSample,
    NetworkInterface,
    Node,
    NodeGroup,
    NodeGroupMembership,
    NodeMetricSample,
    TrafficInterval,
)
from app.services.aggregates import refresh_five_minute
from app.services.filters import QueryFilters
from app.services.nics import counted_interface, counted_sql, is_virtual_interface
from app.services.present import group_payload, node_payload, node_status, percent, status_cutoff
from app.utils.jsonbox import load_json
from app.utils.time import as_utc, five_minute_slots, hour_bucket, isoformat, local_day_start, utcnow
from app.utils.units import format_bps, format_bytes

SPEED_METHOD = (
    "Average speed for a node is valid bytes divided by the duration of those intervals. "
    "Gaps are left out of the denominator, and missing data is not treated as zero. "
    "Across nodes, download and upload speed are the sum of those averages. "
    "The maximum stays the highest speed recorded on one node."
)


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0) if False else value.replace(tzinfo=None)
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text)


def range_meta(filters: QueryFilters, bucket: str | None = None) -> dict:
    payload = {
        "from": isoformat(filters.start),
        "to": isoformat(filters.end),
        "preset": filters.preset,
        "timezone": settings.timezone,
        "speed_method": SPEED_METHOD,
    }
    if bucket is not None:
        payload["bucket"] = bucket
    return payload


def structural_query(filters: QueryFilters):
    query = Node.select()
    if filters.node_ids:
        query = query.where(Node.node_id.in_(filters.node_ids))
    if filters.group_ids:
        members = NodeGroupMembership.select(NodeGroupMembership.node).where(
            NodeGroupMembership.group.in_(filters.group_ids)
        )
        query = query.where(Node.id.in_(members))
    if filters.q:
        safe = filters.q.replace("%", "").replace("_", "")
        query = query.where(Node.name.contains(safe) | Node.node_id.contains(safe))
    cutoff = status_cutoff()
    if filters.status == "disabled":
        query = query.where(Node.is_enabled == False)  # noqa: E712
    elif filters.status == "online":
        query = query.where((Node.is_enabled == True) & (Node.last_seen_at >= cutoff))  # noqa: E712
    elif filters.status == "offline":
        query = query.where(
            (Node.is_enabled == True)  # noqa: E712
            & (Node.last_seen_at.is_null(True) | (Node.last_seen_at < cutoff))
        )
    if filters.last_seen_after is not None:
        query = query.where(Node.last_seen_at >= filters.last_seen_after)
    if filters.last_seen_before is not None:
        query = query.where(Node.last_seen_at <= filters.last_seen_before)
    return query


def _metric_filters_active(filters: QueryFilters) -> bool:
    return any(
        value is not None
        for value in (
            filters.traffic_min,
            filters.traffic_max,
            filters.cpu_min,
            filters.cpu_max,
            filters.ram_min,
            filters.ram_max,
            filters.disk_min,
            filters.disk_max,
        )
    )


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _merge_point(existing: dict | None, incoming: dict) -> dict:
    if existing is None:
        return incoming
    if incoming["count"] >= existing["count"]:
        return incoming
    return existing


def per_node_traffic(node_ids: list[int], start: datetime, end: datetime) -> dict[int, dict]:
    if not node_ids:
        return {}
    ph = _placeholders(len(node_ids))
    interval_sql = f"""
        SELECT node_id, strftime('%Y-%m-%d %H:00:00', period_end) AS bucket,
               COALESCE(SUM(rx_bytes), 0), COALESCE(SUM(tx_bytes), 0),
               COALESCE(SUM(duration_seconds), 0), MAX(rx_bps), MAX(tx_bps), COUNT(*)
        FROM traffic_interval
        WHERE {counted_sql()} AND valid = 1
          AND period_end > ? AND period_end <= ?
          AND node_id IN ({ph})
        GROUP BY node_id, bucket
    """
    hourly_sql = f"""
        SELECT node_id, bucket_start,
               COALESCE(rx_bytes, 0), COALESCE(tx_bytes, 0),
               COALESCE(duration_seconds, 0), rx_bps_max, tx_bps_max, sample_count
        FROM traffic_hourly_aggregate
        WHERE {counted_sql()} AND sample_count > 0
          AND bucket_start >= ? AND bucket_start < ?
          AND node_id IN ({ph})
    """
    chosen: dict[tuple[int, str], dict] = {}
    for node_id, bucket, rx, tx, dur, rx_max, tx_max, count in db.execute_sql(
        interval_sql, [start, end, *node_ids]
    ).fetchall():
        key = (int(node_id), _parse_dt(bucket).strftime("%Y-%m-%d %H:00:00"))
        chosen[key] = _merge_point(
            chosen.get(key),
            {
                "rx": int(rx or 0),
                "tx": int(tx or 0),
                "duration": float(dur or 0),
                "rx_max": rx_max,
                "tx_max": tx_max,
                "count": int(count or 0),
            },
        )
    hour_start = hour_bucket(start)
    for node_id, bucket, rx, tx, dur, rx_max, tx_max, count in db.execute_sql(
        hourly_sql, [hour_start, end, *node_ids]
    ).fetchall():
        key = (int(node_id), _parse_dt(bucket).strftime("%Y-%m-%d %H:00:00"))
        chosen[key] = _merge_point(
            chosen.get(key),
            {
                "rx": int(rx or 0),
                "tx": int(tx or 0),
                "duration": float(dur or 0),
                "rx_max": rx_max,
                "tx_max": tx_max,
                "count": int(count or 0),
            },
        )
    totals: dict[int, dict] = {}
    for (node_id, _bucket), point in chosen.items():
        item = totals.setdefault(
            node_id,
            {"rx": 0, "tx": 0, "duration": 0.0, "rx_max": None, "tx_max": None, "count": 0},
        )
        item["rx"] += point["rx"]
        item["tx"] += point["tx"]
        item["duration"] += point["duration"]
        item["count"] += point["count"]
        if point["rx_max"] is not None:
            item["rx_max"] = point["rx_max"] if item["rx_max"] is None else max(item["rx_max"], point["rx_max"])
        if point["tx_max"] is not None:
            item["tx_max"] = point["tx_max"] if item["tx_max"] is None else max(item["tx_max"], point["tx_max"])
    return totals


def per_node_resources(node_ids: list[int], start: datetime, end: datetime) -> dict[int, dict]:
    if not node_ids:
        return {}
    ph = _placeholders(len(node_ids))
    sample_sql = f"""
        SELECT node_id, strftime('%Y-%m-%d %H:00:00', collected_at) AS bucket,
               AVG(cpu_percent), MAX(cpu_percent), MIN(cpu_percent),
               AVG(CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END),
               MAX(CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END),
               AVG(CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END),
               MAX(CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END),
               COUNT(*)
        FROM node_metric_sample
        WHERE collected_at > ? AND collected_at <= ?
          AND node_id IN ({ph})
        GROUP BY node_id, bucket
    """
    hourly_sql = f"""
        SELECT node_id, bucket_start, cpu_avg, cpu_max, cpu_min,
               ram_percent_avg, ram_percent_max, disk_percent_avg, disk_percent_max, resource_samples
        FROM traffic_hourly_aggregate
        WHERE interface_name = '*' AND resource_samples > 0
          AND bucket_start >= ? AND bucket_start < ?
          AND node_id IN ({ph})
    """
    chosen: dict[tuple[int, str], dict] = {}
    for row in db.execute_sql(sample_sql, [start, end, *node_ids]).fetchall():
        node_id, bucket, cpu_avg, cpu_max, cpu_min, ram_avg, ram_max, disk_avg, disk_max, count = row
        key = (int(node_id), str(bucket))
        chosen[key] = _merge_point(
            chosen.get(key),
            {
                "cpu_avg": cpu_avg,
                "cpu_max": cpu_max,
                "cpu_min": cpu_min,
                "ram_avg": ram_avg,
                "ram_max": ram_max,
                "disk_avg": disk_avg,
                "disk_max": disk_max,
                "count": int(count or 0),
            },
        )
    for row in db.execute_sql(hourly_sql, [hour_bucket(start), end, *node_ids]).fetchall():
        node_id, bucket, cpu_avg, cpu_max, cpu_min, ram_avg, ram_max, disk_avg, disk_max, count = row
        normalized = _parse_dt(bucket).strftime("%Y-%m-%d %H:00:00")
        key = (int(node_id), normalized)
        chosen[key] = _merge_point(
            chosen.get(key),
            {
                "cpu_avg": cpu_avg,
                "cpu_max": cpu_max,
                "cpu_min": cpu_min,
                "ram_avg": ram_avg,
                "ram_max": ram_max,
                "disk_avg": disk_avg,
                "disk_max": disk_max,
                "count": int(count or 0),
            },
        )
    totals: dict[int, dict] = {}
    for (node_id, _bucket), point in chosen.items():
        item = totals.setdefault(
            node_id,
            {
                "cpu_weight": 0.0,
                "cpu_sum": 0.0,
                "cpu_max": None,
                "cpu_min": None,
                "ram_weight": 0.0,
                "ram_sum": 0.0,
                "ram_max": None,
                "disk_weight": 0.0,
                "disk_sum": 0.0,
                "disk_max": None,
                "count": 0,
            },
        )
        weight = point["count"]
        item["count"] += weight
        if point["cpu_avg"] is not None and weight:
            item["cpu_sum"] += point["cpu_avg"] * weight
            item["cpu_weight"] += weight
        if point["ram_avg"] is not None and weight:
            item["ram_sum"] += point["ram_avg"] * weight
            item["ram_weight"] += weight
        if point["disk_avg"] is not None and weight:
            item["disk_sum"] += point["disk_avg"] * weight
            item["disk_weight"] += weight
        if point["cpu_max"] is not None:
            item["cpu_max"] = point["cpu_max"] if item["cpu_max"] is None else max(item["cpu_max"], point["cpu_max"])
        if point["cpu_min"] is not None:
            item["cpu_min"] = point["cpu_min"] if item["cpu_min"] is None else min(item["cpu_min"], point["cpu_min"])
        if point["ram_max"] is not None:
            item["ram_max"] = point["ram_max"] if item["ram_max"] is None else max(item["ram_max"], point["ram_max"])
        if point["disk_max"] is not None:
            item["disk_max"] = point["disk_max"] if item["disk_max"] is None else max(item["disk_max"], point["disk_max"])
    rendered = {}
    for node_id, item in totals.items():
        rendered[node_id] = {
            "cpu_avg": (item["cpu_sum"] / item["cpu_weight"]) if item["cpu_weight"] else None,
            "cpu_max": item["cpu_max"],
            "cpu_min": item["cpu_min"],
            "ram_percent_avg": (item["ram_sum"] / item["ram_weight"]) if item["ram_weight"] else None,
            "ram_percent_max": item["ram_max"],
            "disk_percent_avg": (item["disk_sum"] / item["disk_weight"]) if item["disk_weight"] else None,
            "disk_percent_max": item["disk_max"],
            "count": item["count"],
            "has_data": item["count"] > 0,
        }
    return rendered


def _kind_value(traffic: dict | None, kind: str) -> int | None:
    if not traffic or traffic["count"] <= 0:
        return None
    if kind == "rx":
        return traffic["rx"]
    if kind == "tx":
        return traffic["tx"]
    return traffic["rx"] + traffic["tx"]


def _within(value, low, high) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _matches(node: Node, traffic: dict | None, filters: QueryFilters) -> bool:
    if not _within(_kind_value(traffic, filters.traffic_kind), filters.traffic_min, filters.traffic_max):
        return False
    if not _within(node.last_cpu_percent, filters.cpu_min, filters.cpu_max):
        return False
    if not _within(percent(node.last_ram_used_bytes, node.last_ram_total_bytes), filters.ram_min, filters.ram_max):
        return False
    if not _within(percent(node.last_disk_used_bytes, node.last_disk_total_bytes), filters.disk_min, filters.disk_max):
        return False
    return True


def _sort_value(node: Node, filters: QueryFilters, traffic: dict | None, reverse: bool):
    key = filters.sort.lstrip("-")
    if key == "name":
        value = node.name.lower()
        return (0, tuple(-ord(char) for char in value) if reverse else value)
    if key == "status":
        order = {"online": 0, "offline": 1, "disabled": 2}
        value = order[node_status(node)]
        return (0, -value if reverse else value)
    if key == "last_seen":
        if node.last_seen_at is None:
            return (1, 0)
        stamp = node.last_seen_at.timestamp()
        return (0, -stamp if reverse else stamp)
    if key == "traffic":
        value = _kind_value(traffic, filters.traffic_kind)
    elif key == "cpu":
        value = node.last_cpu_percent
    else:
        value = percent(node.last_ram_used_bytes, node.last_ram_total_bytes)
    if value is None:
        return (1, 0)
    return (0, -value if reverse else value)


def matching_nodes(filters: QueryFilters) -> tuple[list[Node], dict, dict]:
    nodes = list(structural_query(filters))
    ids = [node.id for node in nodes]
    traffic = per_node_traffic(ids, filters.start, filters.end)
    resources = per_node_resources(ids, filters.start, filters.end)
    if _metric_filters_active(filters):
        nodes = [node for node in nodes if _matches(node, traffic.get(node.id), filters)]
    reverse = filters.sort.startswith("-")
    nodes.sort(key=lambda node: _sort_value(node, filters, traffic.get(node.id), reverse))
    return nodes, traffic, resources


def _traffic_public(traffic: dict | None) -> dict:
    if not traffic or traffic["count"] <= 0:
        return {
            "rx_bytes": None,
            "tx_bytes": None,
            "total_bytes": None,
            "rx_text": None,
            "tx_text": None,
            "total_text": None,
            "rx_bps_avg": None,
            "tx_bps_avg": None,
            "rx_bps_max": None,
            "tx_bps_max": None,
            "rx_bps_avg_text": None,
            "tx_bps_avg_text": None,
            "rx_bps_max_text": None,
            "tx_bps_max_text": None,
            "duration_seconds": None,
            "has_data": False,
        }
    rx = traffic["rx"]
    tx = traffic["tx"]
    duration = traffic["duration"]
    rx_avg = (rx / duration) if duration else None
    tx_avg = (tx / duration) if duration else None
    return {
        "rx_bytes": rx,
        "tx_bytes": tx,
        "total_bytes": rx + tx,
        "rx_text": format_bytes(rx),
        "tx_text": format_bytes(tx),
        "total_text": format_bytes(rx + tx),
        "rx_bps_avg": rx_avg,
        "tx_bps_avg": tx_avg,
        "rx_bps_max": traffic["rx_max"],
        "tx_bps_max": traffic["tx_max"],
        "rx_bps_avg_text": format_bps(rx_avg),
        "tx_bps_avg_text": format_bps(tx_avg),
        "rx_bps_max_text": format_bps(traffic["rx_max"]),
        "tx_bps_max_text": format_bps(traffic["tx_max"]),
        "duration_seconds": duration,
        "has_data": True,
    }


def _mean(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return sum(usable) / len(usable)


def _sum_known(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return float(sum(usable))


def _combine_traffic(nodes: list[Node], traffic: dict[int, dict]) -> dict:
    blocks = [_traffic_public(traffic.get(node.id)) for node in nodes]
    active = [block for block in blocks if block["has_data"]]
    if not active:
        empty = _traffic_public(None)
        empty["nodes_with_data"] = 0
        empty["nodes_without_data"] = len(nodes)
        return empty
    rx = sum(block["rx_bytes"] or 0 for block in active)
    tx = sum(block["tx_bytes"] or 0 for block in active)
    rx_avg = _sum_known([block["rx_bps_avg"] for block in active])
    tx_avg = _sum_known([block["tx_bps_avg"] for block in active])
    rx_max_values = [block["rx_bps_max"] for block in active if block["rx_bps_max"] is not None]
    tx_max_values = [block["tx_bps_max"] for block in active if block["tx_bps_max"] is not None]
    return {
        "rx_bytes": rx,
        "tx_bytes": tx,
        "total_bytes": rx + tx,
        "rx_text": format_bytes(rx),
        "tx_text": format_bytes(tx),
        "total_text": format_bytes(rx + tx),
        "rx_bps_avg": rx_avg,
        "tx_bps_avg": tx_avg,
        "rx_bps_max": max(rx_max_values) if rx_max_values else None,
        "tx_bps_max": max(tx_max_values) if tx_max_values else None,
        "rx_bps_avg_text": format_bps(rx_avg),
        "tx_bps_avg_text": format_bps(tx_avg),
        "rx_bps_max_text": format_bps(max(rx_max_values) if rx_max_values else None),
        "tx_bps_max_text": format_bps(max(tx_max_values) if tx_max_values else None),
        "duration_seconds": sum(block["duration_seconds"] or 0 for block in active),
        "has_data": True,
        "nodes_with_data": len(active),
        "nodes_without_data": len(nodes) - len(active),
    }


def _combine_resources(nodes: list[Node], resources: dict[int, dict]) -> dict:
    rows = [resources.get(node.id) for node in nodes]
    active = [row for row in rows if row and row.get("has_data")]
    if not active:
        return {
            "cpu_avg": None,
            "cpu_max": None,
            "cpu_min": None,
            "ram_percent_avg": None,
            "ram_percent_max": None,
            "disk_percent_avg": None,
            "disk_percent_max": None,
            "has_data": False,
            "nodes_with_data": 0,
        }
    cpu_max = [row["cpu_max"] for row in active if row["cpu_max"] is not None]
    cpu_min = [row["cpu_min"] for row in active if row["cpu_min"] is not None]
    ram_max = [row["ram_percent_max"] for row in active if row["ram_percent_max"] is not None]
    disk_max = [row["disk_percent_max"] for row in active if row["disk_percent_max"] is not None]
    return {
        "cpu_avg": _mean([row["cpu_avg"] for row in active]),
        "cpu_max": max(cpu_max) if cpu_max else None,
        "cpu_min": min(cpu_min) if cpu_min else None,
        "ram_percent_avg": _mean([row["ram_percent_avg"] for row in active]),
        "ram_percent_max": max(ram_max) if ram_max else None,
        "disk_percent_avg": _mean([row["disk_percent_avg"] for row in active]),
        "disk_percent_max": max(disk_max) if disk_max else None,
        "has_data": True,
        "nodes_with_data": len(active),
    }


def _disk_capacity(nodes: list[Node]) -> dict:
    known = [node for node in nodes if node.last_disk_total_bytes is not None and node.last_disk_used_bytes is not None]
    if not known:
        return {
            "total_bytes": None,
            "used_bytes": None,
            "free_bytes": None,
            "total_text": None,
            "used_text": None,
            "nodes_with_data": 0,
            "nodes_without_data": len(nodes),
            "has_data": False,
        }
    total = sum(node.last_disk_total_bytes or 0 for node in known)
    used = sum(node.last_disk_used_bytes or 0 for node in known)
    free = sum(node.last_disk_free_bytes or 0 for node in known if node.last_disk_free_bytes is not None)
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total_text": format_bytes(total, binary=True),
        "used_text": format_bytes(used, binary=True),
        "free_text": format_bytes(free, binary=True),
        "nodes_with_data": len(known),
        "nodes_without_data": len(nodes) - len(known),
        "has_data": True,
    }


def list_nodes(filters: QueryFilters) -> dict:
    nodes, traffic, resources = matching_nodes(filters)
    nodes = [node for node in nodes if node.deleted_at is None]
    total = len(nodes)
    start = (filters.page - 1) * filters.page_size
    page = nodes[start : start + filters.page_size]
    items = []
    for node in page:
        payload = node_payload(node)
        payload["range_traffic"] = _traffic_public(traffic.get(node.id))
        payload["range_resources"] = resources.get(node.id) or {"has_data": False}
        items.append(payload)
    return {
        "items": items,
        "meta": {
            **range_meta(filters),
            "page": filters.page,
            "page_size": filters.page_size,
            "total": total,
        },
    }


def _status_counts(nodes: list[Node]) -> dict:
    counts = {"total": 0, "online": 0, "offline": 0, "disabled": 0}
    for node in nodes:
        status = node_status(node)
        if status == "removed":
            continue
        counts["total"] += 1
        counts[status] += 1
    return counts


def _recent_alerts(node_ids: list[int], filters: QueryFilters) -> list[dict]:
    if not node_ids:
        return []
    query = Alert.select().where(
        (Alert.node.in_(node_ids))
        & (Alert.opened_at <= filters.end)
        & ((Alert.resolved_at.is_null(True)) | (Alert.resolved_at >= filters.start))
    )
    return [_alert_payload(alert) for alert in query.order_by(Alert.last_triggered_at.desc()).limit(8)]


def _alert_payload(alert: Alert) -> dict:
    return {
        "id": alert.id,
        "node_id": alert.node.node_id if alert.node_id else None,
        "node_name": alert.node.name if alert.node_id else None,
        "node_status": node_status(alert.node) if alert.node_id else None,
        "type": alert.alert_type,
        "severity": alert.severity,
        "status": alert.status,
        "message": alert.message,
        "resolution": alert.resolution,
        "opened_at": isoformat(alert.opened_at),
        "resolved_at": isoformat(alert.resolved_at),
        "last_triggered_at": isoformat(alert.last_triggered_at),
    }


def _recent_activity(node_ids: list[int], filters: QueryFilters) -> list[dict]:
    query = AuditLog.select().where(
        (AuditLog.created_at >= filters.start) & (AuditLog.created_at <= filters.end)
    )
    if node_ids:
        public_ids = [node.node_id for node in Node.select().where(Node.id.in_(node_ids))]
        query = query.where(
            AuditLog.entity_id.in_(public_ids) | AuditLog.entity_type.not_in(["node"])
        )
    rows = []
    for item in query.order_by(AuditLog.created_at.desc()).limit(10):
        rows.append(
            {
                "action": item.action,
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "created_at": isoformat(item.created_at),
                "details": load_json(item.details, {}),
            }
        )
    return rows


def dashboard(filters: QueryFilters) -> dict:
    nodes, traffic, resources = matching_nodes(filters)
    ids = [node.id for node in nodes]
    open_alerts = 0
    if ids:
        open_alerts = (
            Alert.select(fn.COUNT(fn.DISTINCT(Alert.node)))
            .where((Alert.status == "open") & (Alert.node.in_(ids)))
            .scalar()
            or 0
        )
    group_query = NodeGroup.select()
    if filters.group_ids:
        group_query = group_query.where(NodeGroup.id.in_(filters.group_ids))
    groups = list(group_query)
    ranked_nodes = sorted(
        nodes,
        key=lambda node: _kind_value(traffic.get(node.id), "total") or -1,
        reverse=True,
    )
    top_nodes = []
    for node in ranked_nodes:
        block = _traffic_public(traffic.get(node.id))
        if not block["has_data"]:
            continue
        top_nodes.append(
            {
                "node_id": node.node_id,
                "name": node.name,
                "status": node_status(node),
                "total_bytes": block["total_bytes"],
                "total_text": block["total_text"],
                "cpu_avg": (resources.get(node.id) or {}).get("cpu_avg"),
            }
        )
        if len(top_nodes) == 5:
            break
    memberships = NodeGroupMembership.select().where(NodeGroupMembership.node.in_(ids or [0]))
    members_by_group: dict[int, list[int]] = defaultdict(list)
    for membership in memberships:
        members_by_group[membership.group_id].append(membership.node_id)
    top_groups = []
    for group in groups:
        member_ids = members_by_group.get(group.id, [])
        member_nodes = [node for node in nodes if node.id in member_ids]
        block = _combine_traffic(member_nodes, traffic)
        if not block["has_data"]:
            continue
        top_groups.append(
            {
                "id": group.id,
                "name": group.name,
                "color": group.color,
                "total_bytes": block["total_bytes"],
                "total_text": block["total_text"],
            }
        )
    top_groups.sort(key=lambda item: item["total_bytes"], reverse=True)
    counts = _status_counts(nodes)
    counts["alerting"] = open_alerts
    return {
        "range": range_meta(filters),
        "nodes": counts,
        "groups": {"total": len(groups)},
        "traffic": _combine_traffic(nodes, traffic),
        "resources": _combine_resources(nodes, resources),
        "disk": _disk_capacity(nodes),
        "top_nodes": top_nodes,
        "top_groups": top_groups[:5],
        "alerts": _recent_alerts(ids, filters),
        "activity": _recent_activity(ids, filters),
    }


def _slots(filters: QueryFilters, bucket: str) -> list[datetime]:
    slots: list[datetime] = []
    if bucket == "hour":
        cursor = hour_bucket(filters.start)
        while cursor < filters.end:
            slots.append(cursor)
            cursor += timedelta(hours=1)
        return slots
    if bucket == "day":
        cursor = local_day_start(filters.start)
        while cursor < filters.end:
            slots.append(cursor)
            cursor += timedelta(days=1)
        return slots
    return slots


def _series_from_points(slots: list[datetime], points: dict[datetime, dict], fields: list[str]) -> dict[str, list]:
    columns = {field: [] for field in fields}
    labels = []
    for slot in slots:
        labels.append(isoformat(slot))
        point = points.get(slot)
        for field in fields:
            columns[field].append(None if point is None else point.get(field))
    return {"labels": labels, "columns": columns}


def _hourly_points(node_ids: list[int], filters: QueryFilters) -> dict[datetime, dict]:
    if not node_ids:
        return {}
    ph = _placeholders(len(node_ids))
    points: dict[datetime, dict] = {}
    interval_sql = f"""
        SELECT strftime('%Y-%m-%d %H:00:00', period_end) AS bucket,
               SUM(rx_bytes), SUM(tx_bytes), SUM(duration_seconds), MAX(rx_bps), MAX(tx_bps), COUNT(*)
        FROM traffic_interval
        WHERE {counted_sql()} AND valid = 1
          AND period_end > ? AND period_end <= ? AND node_id IN ({ph})
        GROUP BY bucket
    """
    for bucket, rx, tx, dur, rx_max, tx_max, count in db.execute_sql(
        interval_sql, [filters.start, filters.end, *node_ids]
    ).fetchall():
        slot = _parse_dt(bucket)
        duration = float(dur or 0)
        points[slot] = {
            "rx": int(rx or 0),
            "tx": int(tx or 0),
            "rx_bps": (int(rx or 0) / duration) if duration else None,
            "tx_bps": (int(tx or 0) / duration) if duration else None,
            "count": int(count or 0),
            "rx_max": rx_max,
            "tx_max": tx_max,
        }
    hourly_sql = f"""
        SELECT bucket_start, SUM(rx_bytes), SUM(tx_bytes), SUM(duration_seconds),
               MAX(rx_bps_max), MAX(tx_bps_max), SUM(sample_count)
        FROM traffic_hourly_aggregate
        WHERE {counted_sql()} AND sample_count > 0
          AND bucket_start >= ? AND bucket_start < ? AND node_id IN ({ph})
        GROUP BY bucket_start
    """
    for bucket, rx, tx, dur, rx_max, tx_max, count in db.execute_sql(
        hourly_sql, [hour_bucket(filters.start), filters.end, *node_ids]
    ).fetchall():
        slot = hour_bucket(_parse_dt(bucket))
        incoming = {
            "rx": int(rx or 0),
            "tx": int(tx or 0),
            "rx_bps": (int(rx or 0) / float(dur)) if dur else None,
            "tx_bps": (int(tx or 0) / float(dur)) if dur else None,
            "count": int(count or 0),
            "rx_max": rx_max,
            "tx_max": tx_max,
        }
        current = points.get(slot)
        if current is None or incoming["count"] > current["count"]:
            points[slot] = incoming
    return points


def _raw_points(node_ids: list[int], filters: QueryFilters) -> tuple[list[str], dict[str, list]]:
    if not node_ids:
        return [], {"rx": [], "tx": [], "rx_bps": [], "tx_bps": []}
    rows = list(
        TrafficInterval.select()
        .where(
            (TrafficInterval.node.in_(node_ids))
            & counted_interface(TrafficInterval.interface_name)
            & (TrafficInterval.period_end > filters.start)
            & (TrafficInterval.period_end <= filters.end)
        )
        .order_by(TrafficInterval.period_end)
    )
    grouped: dict[datetime, dict] = {}
    for row in rows:
        point = grouped.setdefault(
            row.period_end,
            {"rx": 0, "tx": 0, "rx_bps": 0.0, "tx_bps": 0.0, "valid": False, "gap": False},
        )
        if row.valid and row.rx_bytes is not None:
            point["valid"] = True
            point["rx"] += row.rx_bytes
            point["tx"] += row.tx_bytes or 0
            point["rx_bps"] += row.rx_bps or 0
            point["tx_bps"] += row.tx_bps or 0
        elif row.reason == "gap":
            point["gap"] = True
    labels: list[str] = []
    columns = {"rx": [], "tx": [], "rx_bps": [], "tx_bps": []}
    previous: datetime | None = None
    for moment, point in grouped.items():
        if previous is not None and (moment - previous).total_seconds() > settings.max_sample_gap_seconds:
            labels.append(isoformat(moment))
            for key in columns:
                columns[key].append(None)
        labels.append(isoformat(moment))
        if point["valid"]:
            columns["rx"].append(point["rx"])
            columns["tx"].append(point["tx"])
            columns["rx_bps"].append(point["rx_bps"])
            columns["tx_bps"].append(point["tx_bps"])
        else:
            for key in columns:
                columns[key].append(None)
        previous = moment
    return labels, columns


def _chart_bounds(filters: QueryFilters) -> tuple[datetime, datetime]:
    earliest = utcnow() - timedelta(days=int(settings.five_minute_retention_days))
    start = max(as_utc(filters.start), earliest)
    end = as_utc(filters.end)
    if start >= end:
        return end, end
    return start, end


def _prepare_five_minute(filters: QueryFilters) -> list[datetime]:
    start, end = _chart_bounds(filters)
    if start < end:
        refresh_five_minute(start, end)
    return five_minute_slots(start, end)


def _five_traffic_points(node_ids: list[int], slots: list[datetime]) -> dict[datetime, dict]:
    if not node_ids or not slots:
        return {}
    ph = _placeholders(len(node_ids))
    sql = f"""
        SELECT bucket_start, SUM(rx_bytes), SUM(tx_bytes), SUM(rx_bps_avg), SUM(tx_bps_avg), SUM(sample_count)
        FROM traffic_five_minute_aggregate
        WHERE {counted_sql()} AND sample_count > 0
          AND bucket_start >= ? AND bucket_start < ? AND node_id IN ({ph})
        GROUP BY bucket_start
    """
    points: dict[datetime, dict] = {}
    for bucket, rx, tx, rx_bps, tx_bps, count in db.execute_sql(
        sql, [slots[0], slots[-1] + timedelta(minutes=5), *node_ids]
    ).fetchall():
        points[_parse_dt(bucket).replace(microsecond=0)] = {
            "rx": int(rx or 0),
            "tx": int(tx or 0),
            "rx_bps": None if rx_bps is None else float(rx_bps),
            "tx_bps": None if tx_bps is None else float(tx_bps),
            "count": int(count or 0),
        }
    return points


def _five_resource_points(node_ids: list[int], slots: list[datetime]) -> dict[datetime, dict]:
    if not node_ids or not slots:
        return {}
    ph = _placeholders(len(node_ids))
    sql = f"""
        SELECT bucket_start,
               SUM(cpu_avg * resource_samples) / SUM(resource_samples),
               MAX(cpu_max), MIN(cpu_min),
               SUM(ram_percent_avg * resource_samples) / SUM(resource_samples),
               SUM(disk_percent_avg * resource_samples) / SUM(resource_samples),
               SUM(resource_samples)
        FROM traffic_five_minute_aggregate
        WHERE interface_name = '*' AND resource_samples > 0
          AND bucket_start >= ? AND bucket_start < ? AND node_id IN ({ph})
        GROUP BY bucket_start
    """
    points: dict[datetime, dict] = {}
    for bucket, cpu_avg, cpu_max, cpu_min, ram, disk, count in db.execute_sql(
        sql, [slots[0], slots[-1] + timedelta(minutes=5), *node_ids]
    ).fetchall():
        points[_parse_dt(bucket).replace(microsecond=0)] = {
            "cpu_avg": cpu_avg,
            "cpu_max": cpu_max,
            "cpu_min": cpu_min,
            "ram_percent": ram,
            "disk_percent": disk,
            "count": int(count or 0),
        }
    if len(node_ids) == 1:
        _overlay_latest_resources(points, node_ids[0], slots[0], slots[-1] + timedelta(minutes=5))
    return points


def _overlay_latest_resources(points: dict[datetime, dict], node_id: int, start: datetime, end: datetime) -> None:
    """یک نود: هر گام پنج‌دقیقه‌ای آخرین گزارش همان گام را نشان می‌دهد، نه میانگین را."""
    sql = """
        SELECT bucket_start, cpu_percent, ram_percent, disk_percent
        FROM (
            SELECT datetime((CAST(strftime('%s', collected_at) AS INTEGER) / 300) * 300, 'unixepoch') AS bucket_start,
                   cpu_percent,
                   CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END AS ram_percent,
                   CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END AS disk_percent,
                   ROW_NUMBER() OVER (
                       PARTITION BY datetime((CAST(strftime('%s', collected_at) AS INTEGER) / 300) * 300, 'unixepoch')
                       ORDER BY collected_at DESC
                   ) AS rn
            FROM node_metric_sample
            WHERE collected_at >= ? AND collected_at < ? AND node_id = ?
        ) ranked
        WHERE rn = 1
    """
    for bucket, cpu, ram, disk in db.execute_sql(sql, [start, end, node_id]).fetchall():
        slot = _parse_dt(bucket).replace(microsecond=0)
        current = points.setdefault(slot, {"cpu_max": None, "cpu_min": None, "count": 1})
        current["cpu_avg"] = cpu
        current["ram_percent"] = ram
        current["disk_percent"] = disk


def _traffic_datasets(columns: dict[str, list]) -> list[dict]:
    return [
        {"key": "rx", "label": "Download", "unit": "bytes", "data": columns["rx"]},
        {"key": "tx", "label": "Upload", "unit": "bytes", "data": columns["tx"]},
        {"key": "rx_bps", "label": "Download speed", "unit": "bps", "data": columns["rx_bps"]},
        {"key": "tx_bps", "label": "Upload speed", "unit": "bps", "data": columns["tx_bps"]},
    ]


def traffic_chart(filters: QueryFilters, mode: str = "total") -> dict:
    nodes, _traffic, _resources = matching_nodes(filters)
    if mode == "nodes" and len(nodes) > 8:
        raise AppError("too_many", "Select at most 8 nodes to compare.", 422)
    if mode == "groups":
        return _group_traffic_chart(filters, nodes)
    if mode == "nodes":
        return _node_traffic_chart(filters, nodes)
    slots = _prepare_five_minute(filters)
    points = _five_traffic_points([node.id for node in nodes], slots)
    packed = _series_from_points(slots, points, ["rx", "tx", "rx_bps", "tx_bps"])
    return {
        "range": range_meta(filters, "5min"),
        "labels": packed["labels"],
        "datasets": _traffic_datasets(packed["columns"]),
    }


def _daily_points(node_ids: list[int], filters: QueryFilters) -> dict[datetime, dict]:
    hourly = _hourly_points(node_ids, filters)
    points: dict[datetime, dict] = {}
    for slot, point in hourly.items():
        day = local_day_start(slot)
        current = points.setdefault(day, {"rx": 0, "tx": 0, "duration": 0.0, "count": 0, "rx_max": None, "tx_max": None})
        current["rx"] += point["rx"]
        current["tx"] += point["tx"]
        if point["rx_bps"] is not None and point["count"]:
            current["duration"] += point["rx"] / point["rx_bps"] if point["rx_bps"] else 0
        current["count"] += point["count"]
        if point["rx_max"] is not None:
            current["rx_max"] = point["rx_max"] if current["rx_max"] is None else max(current["rx_max"], point["rx_max"])
        if point["tx_max"] is not None:
            current["tx_max"] = point["tx_max"] if current["tx_max"] is None else max(current["tx_max"], point["tx_max"])
    rendered = {}
    for day, point in points.items():
        rendered[day] = {
            "rx": point["rx"] if point["count"] else None,
            "tx": point["tx"] if point["count"] else None,
            "rx_bps": (point["rx"] / point["duration"]) if point["duration"] else None,
            "tx_bps": (point["tx"] / point["duration"]) if point["duration"] else None,
            "count": point["count"],
        }
    return rendered


def _node_traffic_chart(filters: QueryFilters, nodes: list[Node]) -> dict:
    slots = _prepare_five_minute(filters)
    labels = [isoformat(slot) for slot in slots]
    datasets = []
    for node in nodes:
        points = _five_traffic_points([node.id], slots)
        data = [None if points.get(slot) is None else points[slot]["rx"] + points[slot]["tx"] for slot in slots]
        label = "Removed node" if node_status(node) == "removed" else node.name
        datasets.append({"key": node.node_id, "label": label, "unit": "bytes", "data": data})
    return {"range": range_meta(filters, "5min"), "labels": labels, "datasets": datasets}


def _group_traffic_chart(filters: QueryFilters, nodes: list[Node]) -> dict:
    groups = list(NodeGroup.select())
    if filters.group_ids:
        groups = [group for group in groups if group.id in filters.group_ids]
    if len(groups) > 8:
        raise AppError("too_many", "Select at most 8 groups to compare.", 422)
    node_by_id = {node.id: node for node in nodes}
    memberships = NodeGroupMembership.select()
    slots = _prepare_five_minute(filters)
    labels = [isoformat(slot) for slot in slots]
    datasets = []
    for group in groups:
        member_ids = [
            membership.node_id
            for membership in memberships
            if membership.group_id == group.id and membership.node_id in node_by_id
        ]
        points = _five_traffic_points(member_ids, slots)
        data = []
        for slot in slots:
            point = points.get(slot)
            data.append(None if point is None else (point["rx"] or 0) + (point["tx"] or 0))
        datasets.append({"key": str(group.id), "label": group.name, "unit": "bytes", "color": group.color, "data": data})
    return {"range": range_meta(filters, "5min"), "labels": labels, "datasets": datasets}


def resource_chart(filters: QueryFilters) -> dict:
    nodes, _traffic, _resources = matching_nodes(filters)
    slots = _prepare_five_minute(filters)
    points = _five_resource_points([node.id for node in nodes], slots)
    packed = _series_from_points(slots, points, ["cpu_avg", "cpu_max", "cpu_min", "ram_percent", "disk_percent"])
    return {"range": range_meta(filters, "5min"), "labels": packed["labels"], "datasets": _resource_datasets(packed["columns"])}


def _raw_resources(node_ids: list[int], filters: QueryFilters) -> dict:
    if not node_ids:
        columns = {"cpu_avg": [], "cpu_max": [], "ram_percent": [], "disk_percent": []}
        return {"range": range_meta(filters, "raw"), "labels": [], "datasets": _resource_datasets(columns)}
    rows = (
        NodeMetricSample.select(
            NodeMetricSample.collected_at,
            fn.AVG(NodeMetricSample.cpu_percent).alias("cpu_avg"),
            fn.MAX(NodeMetricSample.cpu_percent).alias("cpu_max"),
            fn.AVG(NodeMetricSample.ram_used_bytes * 100.0 / fn.NULLIF(NodeMetricSample.ram_total_bytes, 0)).alias("ram"),
            fn.AVG(NodeMetricSample.disk_used_bytes * 100.0 / fn.NULLIF(NodeMetricSample.disk_total_bytes, 0)).alias("disk"),
        )
        .where(
            (NodeMetricSample.node.in_(node_ids))
            & (NodeMetricSample.collected_at > filters.start)
            & (NodeMetricSample.collected_at <= filters.end)
        )
        .group_by(NodeMetricSample.collected_at)
        .order_by(NodeMetricSample.collected_at)
        .dicts()
    )
    labels = []
    columns = {"cpu_avg": [], "cpu_max": [], "ram_percent": [], "disk_percent": []}
    for row in rows:
        labels.append(isoformat(row["collected_at"]))
        columns["cpu_avg"].append(row["cpu_avg"])
        columns["cpu_max"].append(row["cpu_max"])
        columns["ram_percent"].append(row["ram"])
        columns["disk_percent"].append(row["disk"])
    return {"range": range_meta(filters, "raw"), "labels": labels, "datasets": _resource_datasets(columns)}


def _bucket_resources(node_ids: list[int], filters: QueryFilters, bucket: str) -> dict:
    slots = _slots(filters, bucket)
    points: dict[datetime, dict] = {}
    if node_ids:
        resources = per_node_resources(node_ids, filters.start, filters.end)
        # برای محور زمانی، نمونه‌ها را ساعتی از SQL دوباره گروه‌بندی می‌کنیم.
        ph = _placeholders(len(node_ids))
        sql = f"""
            SELECT strftime('%Y-%m-%d %H:00:00', collected_at) AS bucket,
                   AVG(cpu_percent), MAX(cpu_percent), MIN(cpu_percent),
                   AVG(CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END),
                   AVG(CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END),
                   COUNT(*)
            FROM node_metric_sample
            WHERE collected_at > ? AND collected_at <= ? AND node_id IN ({ph})
            GROUP BY bucket
        """
        for row in db.execute_sql(sql, [filters.start, filters.end, *node_ids]).fetchall():
            slot, cpu_avg, cpu_max, cpu_min, ram, disk, count = row
            points[_parse_dt(slot)] = {
                "cpu_avg": cpu_avg,
                "cpu_max": cpu_max,
                "cpu_min": cpu_min,
                "ram_percent": ram,
                "disk_percent": disk,
                "count": int(count or 0),
            }
        hourly_sql = f"""
            SELECT bucket_start, SUM(cpu_avg * resource_samples) / SUM(resource_samples),
                   MAX(cpu_max), MIN(cpu_min),
                   SUM(ram_percent_avg * resource_samples) / SUM(resource_samples),
                   SUM(disk_percent_avg * resource_samples) / SUM(resource_samples),
                   SUM(resource_samples)
            FROM traffic_hourly_aggregate
            WHERE interface_name = '*' AND resource_samples > 0
              AND bucket_start >= ? AND bucket_start < ? AND node_id IN ({ph})
            GROUP BY bucket_start
        """
        for row in db.execute_sql(hourly_sql, [hour_bucket(filters.start), filters.end, *node_ids]).fetchall():
            slot = hour_bucket(_parse_dt(row[0]))
            incoming = {
                "cpu_avg": row[1],
                "cpu_max": row[2],
                "cpu_min": row[3],
                "ram_percent": row[4],
                "disk_percent": row[5],
                "count": int(row[6] or 0),
            }
            current = points.get(slot)
            if current is None or incoming["count"] > current["count"]:
                points[slot] = incoming
        _ = resources
    if bucket == "day":
        daily: dict[datetime, dict] = {}
        for slot, point in points.items():
            day = local_day_start(slot)
            bucket_points = daily.setdefault(day, [])
            bucket_points.append(point)
        points = {}
        for day, items in daily.items():
            weight = sum(item["count"] for item in items)
            points[day] = {
                "cpu_avg": sum((item["cpu_avg"] or 0) * item["count"] for item in items) / weight if weight else None,
                "cpu_max": max(item["cpu_max"] for item in items if item["cpu_max"] is not None),
                "cpu_min": min(item["cpu_min"] for item in items if item["cpu_min"] is not None),
                "ram_percent": sum((item["ram_percent"] or 0) * item["count"] for item in items if item["ram_percent"] is not None) / weight if weight else None,
                "disk_percent": sum((item["disk_percent"] or 0) * item["count"] for item in items if item["disk_percent"] is not None) / weight if weight else None,
                "count": weight,
            }
    packed = _series_from_points(slots, points, ["cpu_avg", "cpu_max", "cpu_min", "ram_percent", "disk_percent"])
    return {"range": range_meta(filters, bucket), "labels": packed["labels"], "datasets": _resource_datasets(packed["columns"])}


def _resource_datasets(columns: dict[str, list]) -> list[dict]:
    return [
        {"key": "cpu_avg", "label": "CPU", "unit": "percent", "data": columns.get("cpu_avg", [])},
        {"key": "ram_percent", "label": "RAM", "unit": "percent", "data": columns.get("ram_percent", [])},
        {"key": "disk_percent", "label": "Disk", "unit": "percent", "data": columns.get("disk_percent", [])},
    ]


def node_detail_stats(node: Node, filters: QueryFilters) -> dict:
    traffic = per_node_traffic([node.id], filters.start, filters.end)
    resources = per_node_resources([node.id], filters.start, filters.end)
    return {
        "node": node_payload(node),
        "range": range_meta(filters),
        "traffic": _traffic_public(traffic.get(node.id)),
        "resources": resources.get(node.id) or {"has_data": False},
        "disk": _disk_capacity([node]),
        "interfaces": _latest_interfaces(node),
    }


def _latest_interfaces(node: Node) -> list[dict]:
    rows = (
        NetworkCounterSample.select(NetworkCounterSample, NetworkInterface)
        .join(NetworkInterface)
        .where(NetworkCounterSample.node == node)
        .order_by(NetworkCounterSample.collected_at.desc())
    )
    seen = set()
    items = []
    for row in rows:
        name = row.interface.name
        if name in seen or is_virtual_interface(name):
            continue
        seen.add(name)
        interval = (
            TrafficInterval.select()
            .where(
                (TrafficInterval.node == node)
                & (TrafficInterval.interface_name == name)
                & (TrafficInterval.period_end == row.collected_at)
            )
            .first()
        )
        items.append(
            {
                "name": name,
                "collected_at": isoformat(row.collected_at),
                "rx_bytes": row.rx_bytes,
                "tx_bytes": row.tx_bytes,
                "rx_packets": row.rx_packets,
                "tx_packets": row.tx_packets,
                "rx_errors": row.rx_errors,
                "tx_errors": row.tx_errors,
                "rx_dropped": row.rx_dropped,
                "tx_dropped": row.tx_dropped,
                "rx_text": format_bytes(row.rx_bytes),
                "tx_text": format_bytes(row.tx_bytes),
                "rx_bps": None if interval is None else interval.rx_bps,
                "tx_bps": None if interval is None else interval.tx_bps,
                "rx_bps_text": None if interval is None else format_bps(interval.rx_bps),
                "tx_bps_text": None if interval is None else format_bps(interval.tx_bps),
                "valid": None if interval is None else interval.valid,
                "reason": None if interval is None else interval.reason,
            }
        )
    return items


def node_samples(node: Node, filters: QueryFilters) -> dict:
    query = NodeMetricSample.select().where(
        (NodeMetricSample.node == node)
        & (NodeMetricSample.collected_at > filters.start)
        & (NodeMetricSample.collected_at <= filters.end)
    )
    total = query.count()
    rows = query.order_by(NodeMetricSample.collected_at.desc()).paginate(filters.page, filters.page_size)
    items = []
    for row in rows:
        items.append(
            {
                "collected_at": isoformat(row.collected_at),
                "received_at": isoformat(row.received_at),
                "cpu_percent": row.cpu_percent,
                "ram_percent": percent(row.ram_used_bytes, row.ram_total_bytes),
                "disk_percent": percent(row.disk_used_bytes, row.disk_total_bytes),
                "ram_used_bytes": row.ram_used_bytes,
                "ram_total_bytes": row.ram_total_bytes,
                "disk_used_bytes": row.disk_used_bytes,
                "disk_total_bytes": row.disk_total_bytes,
                "load_1": row.load_1,
                "load_5": row.load_5,
                "load_15": row.load_15,
                "uptime_seconds": row.uptime_seconds,
                "agent_version": row.agent_version,
            }
        )
    return {
        "items": items,
        "meta": {**range_meta(filters), "page": filters.page, "page_size": filters.page_size, "total": total},
    }


def group_detail_stats(group: NodeGroup, filters: QueryFilters) -> dict:
    member_ids = [
        membership.node_id
        for membership in NodeGroupMembership.select().where(NodeGroupMembership.group == group)
    ]
    narrowed = QueryFilters(**{**filters.__dict__, "group_ids": [group.id]})
    nodes, traffic, resources = matching_nodes(narrowed)
    nodes = [node for node in nodes if node.id in member_ids]
    ranking = []
    total_bytes = 0
    known = []
    for node in nodes:
        block = _traffic_public(traffic.get(node.id))
        if block["has_data"]:
            known.append((node, block))
            total_bytes += block["total_bytes"] or 0
    for node, block in sorted(known, key=lambda item: item[1]["total_bytes"] or 0, reverse=True):
        share = None if not total_bytes else (block["total_bytes"] / total_bytes) * 100
        ranking.append(
            {
                "node_id": node.node_id,
                "name": node.name,
                "status": node_status(node),
                "rx_bytes": block["rx_bytes"],
                "tx_bytes": block["tx_bytes"],
                "total_bytes": block["total_bytes"],
                "total_text": block["total_text"],
                "share_percent": share,
                "cpu_percent": node.last_cpu_percent,
            }
        )
    counts = _status_counts(nodes)
    members = [
        {"node_id": node.node_id, "name": node.name}
        for node in Node.select()
        .join(NodeGroupMembership)
        .where(NodeGroupMembership.group == group)
        .order_by(Node.name)
    ]
    return {
        "group": group_payload(group),
        "range": range_meta(filters),
        "counts": counts,
        "members": members,
        "traffic": _combine_traffic(nodes, traffic),
        "resources": _combine_resources(nodes, resources),
        "disk": _disk_capacity(nodes),
        "ranking": ranking,
    }


def compare_nodes(filters: QueryFilters) -> dict:
    if len(filters.node_ids) < 2:
        raise AppError("missing_nodes", "Select at least two nodes to compare.", 422)
    if len(filters.node_ids) > 8:
        raise AppError("too_many", "Select at most 8 nodes to compare.", 422)
    nodes, traffic, resources = matching_nodes(filters)
    items = []
    for node in nodes:
        items.append(
            {
                "node": node_payload(node, include_groups=False),
                "traffic": _traffic_public(traffic.get(node.id)),
                "resources": resources.get(node.id) or {"has_data": False},
            }
        )
    return {"range": range_meta(filters), "items": items, "chart": traffic_chart(filters, mode="nodes")}


def compare_groups(filters: QueryFilters) -> dict:
    if len(filters.group_ids) < 2:
        raise AppError("missing_groups", "Select at least two groups to compare.", 422)
    if len(filters.group_ids) > 8:
        raise AppError("too_many", "Select at most 8 groups to compare.", 422)
    items = []
    for group_id in filters.group_ids:
        group = NodeGroup.get_or_none(NodeGroup.id == group_id)
        if group is None:
            continue
        single = QueryFilters(**{**filters.__dict__, "group_ids": [group_id], "node_ids": []})
        nodes, traffic, resources = matching_nodes(single)
        items.append(
            {
                "group": group_payload(group, with_counts=True),
                "traffic": _combine_traffic(nodes, traffic),
                "resources": _combine_resources(nodes, resources),
                "disk": _disk_capacity(nodes),
            }
        )
    chart_filters = QueryFilters(**{**filters.__dict__, "node_ids": []})
    return {"range": range_meta(filters), "items": items, "chart": _group_traffic_chart(chart_filters, matching_nodes(chart_filters)[0])}


def list_alerts(filters: QueryFilters, status: str | None) -> dict:
    query = Alert.select()
    if status in {"open", "resolved"}:
        query = query.where(Alert.status == status)
    nodes, _traffic, _resources = matching_nodes(filters)
    if filters.node_ids or filters.group_ids or filters.q or filters.status:
        query = query.where(Alert.node.in_([node.id for node in nodes] or [0]))
    query = query.where((Alert.opened_at <= filters.end) & ((Alert.resolved_at.is_null(True)) | (Alert.resolved_at >= filters.start) | (Alert.status == "open")))
    total = query.count()
    rows = query.order_by(Alert.last_triggered_at.desc()).paginate(filters.page, filters.page_size)
    return {
        "items": [_alert_payload(alert) for alert in rows],
        "meta": {**range_meta(filters), "page": filters.page, "page_size": filters.page_size, "total": total},
    }


def export_traffic_csv(filters: QueryFilters) -> str:
    nodes, traffic, _resources = matching_nodes(filters)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "node_id",
            "name",
            "status",
            "rx_bytes",
            "tx_bytes",
            "total_bytes",
            "rx_bps_avg",
            "tx_bps_avg",
            "cpu_percent",
            "ram_percent",
            "disk_percent",
            "from",
            "to",
        ]
    )
    for node in nodes[:50000]:
        block = _traffic_public(traffic.get(node.id))
        ram_percent = percent(node.last_ram_used_bytes, node.last_ram_total_bytes)
        disk_percent = percent(node.last_disk_used_bytes, node.last_disk_total_bytes)
        writer.writerow(
            [
                node.node_id,
                node.name,
                node_status(node),
                "" if block["rx_bytes"] is None else block["rx_bytes"],
                "" if block["tx_bytes"] is None else block["tx_bytes"],
                "" if block["total_bytes"] is None else block["total_bytes"],
                "" if block["rx_bps_avg"] is None else round(block["rx_bps_avg"], 3),
                "" if block["tx_bps_avg"] is None else round(block["tx_bps_avg"], 3),
                "" if node.last_cpu_percent is None else round(node.last_cpu_percent, 2),
                "" if ram_percent is None else ram_percent,
                "" if disk_percent is None else disk_percent,
                isoformat(filters.start),
                isoformat(filters.end),
            ]
        )
    return "\ufeff" + buffer.getvalue()
