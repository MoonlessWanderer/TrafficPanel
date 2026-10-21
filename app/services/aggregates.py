"""تجمیع پنج‌دقیقه‌ای، ساعتی و روزانه و نگهداری داده‌ها.

ساعت‌های قدیمی‌تر از مرز نگهداری خام دوباره از روی نمونه‌های حذف‌شده محاسبه نمی‌شوند
تا آمار تاریخی کوچک نشود.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta

from app.core.settings import settings
from app.db.database import connection, db
from app.models import (
    NetworkCounterSample,
    NodeMetricSample,
    RequestNonce,
    Session,
    TrafficDailyAggregate,
    TrafficFiveMinuteAggregate,
    TrafficHourlyAggregate,
    TrafficInterval,
)
from app.services.alerts import scan_connections
from app.utils.time import as_utc, five_minute_bucket, hour_bucket, local_day_start, retention_split, utcnow

_lock = threading.Lock()
_last_retention: datetime | None = None


_AGGREGATE_COLUMNS = """
    node_id, interface_name, bucket_start,
    rx_bytes, tx_bytes, rx_bps_avg, tx_bps_avg, rx_bps_max, tx_bps_max,
    duration_seconds, sample_count,
    cpu_avg, cpu_max, cpu_min,
    ram_percent_avg, ram_percent_max, ram_total_bytes, ram_used_avg,
    disk_percent_avg, disk_percent_max, disk_total_bytes, disk_used_avg,
    resource_samples
"""

_TRAFFIC_UPDATE = """
    rx_bytes = excluded.rx_bytes,
    tx_bytes = excluded.tx_bytes,
    rx_bps_avg = excluded.rx_bps_avg,
    tx_bps_avg = excluded.tx_bps_avg,
    rx_bps_max = excluded.rx_bps_max,
    tx_bps_max = excluded.tx_bps_max,
    duration_seconds = excluded.duration_seconds,
    sample_count = excluded.sample_count
"""

_RESOURCE_UPDATE = """
    cpu_avg = excluded.cpu_avg,
    cpu_max = excluded.cpu_max,
    cpu_min = excluded.cpu_min,
    ram_percent_avg = excluded.ram_percent_avg,
    ram_percent_max = excluded.ram_percent_max,
    ram_total_bytes = excluded.ram_total_bytes,
    ram_used_avg = excluded.ram_used_avg,
    disk_percent_avg = excluded.disk_percent_avg,
    disk_percent_max = excluded.disk_percent_max,
    disk_total_bytes = excluded.disk_total_bytes,
    disk_used_avg = excluded.disk_used_avg,
    resource_samples = excluded.resource_samples
"""


def _write_aggregate(table: str, select_sql: str, update_sql: str, params: tuple) -> int:
    cursor = db.execute_sql(
        f"""
        INSERT INTO {table} ({_AGGREGATE_COLUMNS})
        {select_sql}
        ON CONFLICT(node_id, interface_name, bucket_start) DO UPDATE SET
        {update_sql}
        """,
        params,
    )
    return max(cursor.rowcount, 0)


def _upsert_daily(node_id: int, interface_name: str, bucket: datetime, values: dict) -> None:
    row = TrafficDailyAggregate.get_or_none(
        (TrafficDailyAggregate.node == node_id)
        & (TrafficDailyAggregate.interface_name == interface_name)
        & (TrafficDailyAggregate.bucket_start == bucket)
    )
    if row is None:
        TrafficDailyAggregate.create(
            node=node_id,
            interface_name=interface_name,
            bucket_start=bucket,
            **values,
        )
        return
    for key, value in values.items():
        setattr(row, key, value)
    row.save()


def _five_minute_expr(column: str) -> str:
    return f"datetime((CAST(strftime('%s', {column}) AS INTEGER) / 300) * 300, 'unixepoch')"


def _traffic_select(bucket_sql: str, time_column: str) -> str:
    return f"""
        SELECT node_id, interface_name, {bucket_sql},
               COALESCE(SUM(rx_bytes), 0), COALESCE(SUM(tx_bytes), 0),
               CASE WHEN COALESCE(SUM(duration_seconds), 0) > 0
                    THEN SUM(rx_bytes) * 1.0 / SUM(duration_seconds) END,
               CASE WHEN COALESCE(SUM(duration_seconds), 0) > 0
                    THEN SUM(tx_bytes) * 1.0 / SUM(duration_seconds) END,
               MAX(rx_bps), MAX(tx_bps),
               COALESCE(SUM(duration_seconds), 0), COUNT(*),
               NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0
        FROM traffic_interval
        WHERE valid = 1 AND rx_bytes IS NOT NULL
          AND {time_column} >= ? AND {time_column} < ?
        GROUP BY node_id, interface_name, {bucket_sql}
    """


def _resource_select(bucket_sql: str, time_column: str) -> str:
    ram = "CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END"
    disk = "CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END"
    return f"""
        SELECT node_id, '*', {bucket_sql},
               0, 0, NULL, NULL, NULL, NULL, 0, 0,
               AVG(cpu_percent), MAX(cpu_percent), MIN(cpu_percent),
               AVG({ram}), MAX({ram}), MAX(ram_total_bytes), AVG(ram_used_bytes),
               AVG({disk}), MAX({disk}), MAX(disk_total_bytes), AVG(disk_used_bytes),
               COUNT(*)
        FROM node_metric_sample
        WHERE {time_column} >= ? AND {time_column} < ?
        GROUP BY node_id, {bucket_sql}
    """


def refresh_five_minute(start: datetime, end: datetime) -> int:
    start = as_utc(start)
    end = as_utc(end)
    raw_begin = utcnow() - timedelta(days=settings.raw_retention_days)
    window_start = max(five_minute_bucket(start), five_minute_bucket(raw_begin))
    if window_start >= end:
        return 0
    traffic_bucket = _five_minute_expr("period_end")
    resource_bucket = _five_minute_expr("collected_at")
    with db.atomic():
        traffic = _write_aggregate(
            "traffic_five_minute_aggregate",
            _traffic_select(traffic_bucket, "period_end"),
            _TRAFFIC_UPDATE,
            (window_start, end),
        )
        resources = _write_aggregate(
            "traffic_five_minute_aggregate",
            _resource_select(resource_bucket, "collected_at"),
            _RESOURCE_UPDATE,
            (window_start, end),
        )
    return traffic + resources


def refresh_hourly(start: datetime, end: datetime) -> int:
    start = as_utc(start)
    end = as_utc(end)
    window_start = max(hour_bucket(start), retention_split())
    if window_start >= end:
        return 0
    traffic_bucket = "strftime('%Y-%m-%d %H:00:00', period_end)"
    resource_bucket = "strftime('%Y-%m-%d %H:00:00', collected_at)"
    with db.atomic():
        traffic = _write_aggregate(
            "traffic_hourly_aggregate",
            _traffic_select(traffic_bucket, "period_end"),
            _TRAFFIC_UPDATE,
            (window_start, end),
        )
        resources = _write_aggregate(
            "traffic_hourly_aggregate",
            _resource_select(resource_bucket, "collected_at"),
            _RESOURCE_UPDATE,
            (window_start, end),
        )
    return traffic + resources


def _weighted(items, value_name: str, weight_name: str):
    total_weight = 0
    total = 0.0
    for item in items:
        weight = getattr(item, weight_name) or 0
        value = getattr(item, value_name)
        if value is None or weight <= 0:
            continue
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return total / total_weight


def refresh_daily(start: datetime, end: datetime) -> int:
    start = as_utc(start)
    end = as_utc(end)
    fresh = utcnow() - timedelta(days=settings.hourly_retention_days)
    window_start = max(local_day_start(start), local_day_start(fresh))
    rows = TrafficHourlyAggregate.select().where(
        (TrafficHourlyAggregate.bucket_start >= window_start)
        & (TrafficHourlyAggregate.bucket_start < end)
    )
    grouped: dict[tuple, list] = defaultdict(list)
    for row in rows:
        day = local_day_start(row.bucket_start)
        grouped[(row.node_id, row.interface_name, day)].append(row)
    count = 0
    for (node_id, interface_name, day), items in grouped.items():
        traffic_items = [item for item in items if (item.sample_count or 0) > 0]
        resource_items = [item for item in items if (item.resource_samples or 0) > 0 and interface_name == "*"]
        duration = sum(item.duration_seconds or 0 for item in traffic_items)
        rx = sum(item.rx_bytes or 0 for item in traffic_items)
        tx = sum(item.tx_bytes or 0 for item in traffic_items)
        rx_max = max((item.rx_bps_max for item in traffic_items if item.rx_bps_max is not None), default=None)
        tx_max = max((item.tx_bps_max for item in traffic_items if item.tx_bps_max is not None), default=None)
        cpu_max = max((item.cpu_max for item in resource_items if item.cpu_max is not None), default=None)
        cpu_min = min((item.cpu_min for item in resource_items if item.cpu_min is not None), default=None)
        ram_max = max((item.ram_percent_max for item in resource_items if item.ram_percent_max is not None), default=None)
        disk_max = max((item.disk_percent_max for item in resource_items if item.disk_percent_max is not None), default=None)
        ram_total = max((item.ram_total_bytes for item in resource_items if item.ram_total_bytes is not None), default=None)
        disk_total = max((item.disk_total_bytes for item in resource_items if item.disk_total_bytes is not None), default=None)
        _upsert_daily(
            node_id,
            interface_name,
            day,
            {
                "rx_bytes": rx if traffic_items else 0,
                "tx_bytes": tx if traffic_items else 0,
                "rx_bps_avg": (rx / duration) if duration else None,
                "tx_bps_avg": (tx / duration) if duration else None,
                "rx_bps_max": rx_max,
                "tx_bps_max": tx_max,
                "duration_seconds": duration,
                "sample_count": sum(item.sample_count or 0 for item in traffic_items),
                "cpu_avg": _weighted(resource_items, "cpu_avg", "resource_samples"),
                "cpu_max": cpu_max,
                "cpu_min": cpu_min,
                "ram_percent_avg": _weighted(resource_items, "ram_percent_avg", "resource_samples"),
                "ram_percent_max": ram_max,
                "ram_total_bytes": ram_total,
                "ram_used_avg": _weighted(resource_items, "ram_used_avg", "resource_samples"),
                "disk_percent_avg": _weighted(resource_items, "disk_percent_avg", "resource_samples"),
                "disk_percent_max": disk_max,
                "disk_total_bytes": disk_total,
                "disk_used_avg": _weighted(resource_items, "disk_used_avg", "resource_samples"),
                "resource_samples": sum(item.resource_samples or 0 for item in resource_items),
            },
        )
        count += 1
    return count


def run_retention(now: datetime | None = None) -> None:
    moment = now or utcnow()
    raw_cutoff = moment - timedelta(days=settings.raw_retention_days)
    refresh_five_minute(raw_cutoff - timedelta(minutes=10), moment + timedelta(minutes=1))
    refresh_hourly(raw_cutoff - timedelta(hours=2), moment + timedelta(minutes=1))
    refresh_daily(raw_cutoff - timedelta(days=2), moment + timedelta(minutes=1))
    TrafficInterval.delete().where(TrafficInterval.period_end < raw_cutoff).execute()
    NetworkCounterSample.delete().where(NetworkCounterSample.collected_at < raw_cutoff).execute()
    NodeMetricSample.delete().where(NodeMetricSample.collected_at < raw_cutoff).execute()
    five_cutoff = moment - timedelta(days=settings.five_minute_retention_days)
    TrafficFiveMinuteAggregate.delete().where(TrafficFiveMinuteAggregate.bucket_start < five_cutoff).execute()
    hourly_cutoff = moment - timedelta(days=settings.hourly_retention_days)
    refresh_daily(hourly_cutoff - timedelta(days=2), moment + timedelta(minutes=1))
    TrafficHourlyAggregate.delete().where(TrafficHourlyAggregate.bucket_start < hourly_cutoff).execute()
    daily_cutoff = moment - timedelta(days=settings.daily_retention_days)
    TrafficDailyAggregate.delete().where(TrafficDailyAggregate.bucket_start < daily_cutoff).execute()
    nonce_cutoff = moment - timedelta(hours=settings.nonce_retention_hours)
    RequestNonce.delete().where(RequestNonce.created_at < nonce_cutoff).execute()
    Session.delete().where(Session.expires_at < moment).execute()


def run_maintenance() -> None:
    global _last_retention
    if not _lock.acquire(blocking=False):
        return
    try:
        connection()
        now = utcnow()
        refresh_five_minute(now - timedelta(hours=2), now + timedelta(minutes=1))
        refresh_hourly(now - timedelta(hours=settings.aggregate_lookback_hours), now + timedelta(minutes=1))
        refresh_daily(now - timedelta(days=3), now + timedelta(minutes=1))
        scan_connections()
        due = _last_retention is None or now - _last_retention >= timedelta(hours=1)
        if due:
            run_retention(now)
            _last_retention = now
    finally:
        if not db.is_closed():
            db.close()
        _lock.release()
