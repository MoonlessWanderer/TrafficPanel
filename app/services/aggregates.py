"""تجمیع پنج‌دقیقه‌ای، ساعتی و روزانه و نگهداری داده‌ها.

ساعت‌های قدیمی‌تر از مرز نگهداری خام دوباره از روی نمونه‌های حذف‌شده محاسبه نمی‌شوند
تا آمار تاریخی کوچک نشود.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timedelta

from peewee import IntegrityError, fn

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


def _upsert_five_minute(node_id: int, interface_name: str, bucket: datetime, values: dict) -> None:
    row = TrafficFiveMinuteAggregate.get_or_none(
        (TrafficFiveMinuteAggregate.node == node_id)
        & (TrafficFiveMinuteAggregate.interface_name == interface_name)
        & (TrafficFiveMinuteAggregate.bucket_start == bucket)
    )
    if row is None:
        try:
            TrafficFiveMinuteAggregate.create(
                node=node_id,
                interface_name=interface_name,
                bucket_start=bucket,
                **values,
            )
            return
        except IntegrityError:
            row = TrafficFiveMinuteAggregate.get(
                (TrafficFiveMinuteAggregate.node == node_id)
                & (TrafficFiveMinuteAggregate.interface_name == interface_name)
                & (TrafficFiveMinuteAggregate.bucket_start == bucket)
            )
    for key, value in values.items():
        setattr(row, key, value)
    row.save()


def _upsert_hourly(node_id: int, interface_name: str, bucket: datetime, values: dict) -> None:
    row = TrafficHourlyAggregate.get_or_none(
        (TrafficHourlyAggregate.node == node_id)
        & (TrafficHourlyAggregate.interface_name == interface_name)
        & (TrafficHourlyAggregate.bucket_start == bucket)
    )
    if row is None:
        TrafficHourlyAggregate.create(
            node=node_id,
            interface_name=interface_name,
            bucket_start=bucket,
            **values,
        )
        return
    for key, value in values.items():
        setattr(row, key, value)
    row.save()


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


def _parse_bucket(value) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text)


def _five_minute_expr(column: str) -> str:
    return f"datetime((CAST(strftime('%s', {column}) AS INTEGER) / 300) * 300, 'unixepoch')"


def refresh_five_minute(start: datetime, end: datetime) -> int:
    start = as_utc(start)
    end = as_utc(end)
    raw_begin = utcnow() - timedelta(days=settings.raw_retention_days)
    window_start = max(five_minute_bucket(start), five_minute_bucket(raw_begin))
    if window_start >= end:
        return 0
    bucket = _five_minute_expr("period_end")
    traffic_rows = db.execute_sql(
        f"""
        SELECT node_id, interface_name, {bucket} AS bucket,
               SUM(rx_bytes), SUM(tx_bytes), MAX(rx_bps), MAX(tx_bps),
               SUM(duration_seconds), COUNT(*)
        FROM traffic_interval
        WHERE valid = 1 AND rx_bytes IS NOT NULL
          AND period_end >= ? AND period_end < ?
        GROUP BY node_id, interface_name, bucket
        """,
        (window_start, end),
    ).fetchall()
    resource_bucket = _five_minute_expr("collected_at")
    resource_rows = db.execute_sql(
        f"""
        SELECT node_id, {resource_bucket} AS bucket,
               AVG(cpu_percent), MAX(cpu_percent), MIN(cpu_percent),
               AVG(CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END),
               MAX(CASE WHEN ram_total_bytes > 0 THEN ram_used_bytes * 100.0 / ram_total_bytes END),
               MAX(ram_total_bytes), AVG(ram_used_bytes),
               AVG(CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END),
               MAX(CASE WHEN disk_total_bytes > 0 THEN disk_used_bytes * 100.0 / disk_total_bytes END),
               MAX(disk_total_bytes), AVG(disk_used_bytes), COUNT(*)
        FROM node_metric_sample
        WHERE collected_at >= ? AND collected_at < ?
        GROUP BY node_id, bucket
        """,
        (window_start, end),
    ).fetchall()
    count = 0
    for node_id, interface_name, bucket_value, rx, tx, rx_max, tx_max, dur, cnt in traffic_rows:
        duration = float(dur or 0)
        rx_bytes = int(rx or 0)
        tx_bytes = int(tx or 0)
        _upsert_five_minute(
            node_id,
            interface_name,
            _parse_bucket(bucket_value),
            {
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_bps_avg": (rx_bytes / duration) if duration else None,
                "tx_bps_avg": (tx_bytes / duration) if duration else None,
                "rx_bps_max": rx_max,
                "tx_bps_max": tx_max,
                "duration_seconds": duration,
                "sample_count": int(cnt or 0),
            },
        )
        count += 1
    for node_id, bucket_value, cpu_avg, cpu_max, cpu_min, ram_avg, ram_max, ram_total, ram_used, disk_avg, disk_max, disk_total, disk_used, cnt in resource_rows:
        _upsert_five_minute(
            node_id,
            "*",
            _parse_bucket(bucket_value),
            {
                "cpu_avg": cpu_avg,
                "cpu_max": cpu_max,
                "cpu_min": cpu_min,
                "ram_percent_avg": ram_avg,
                "ram_percent_max": ram_max,
                "ram_total_bytes": ram_total,
                "ram_used_avg": ram_used,
                "disk_percent_avg": disk_avg,
                "disk_percent_max": disk_max,
                "disk_total_bytes": disk_total,
                "disk_used_avg": disk_used,
                "resource_samples": int(cnt or 0),
            },
        )
        count += 1
    return count


def refresh_hourly(start: datetime, end: datetime) -> int:
    start = as_utc(start)
    end = as_utc(end)
    split = retention_split()
    window_start = max(hour_bucket(start), split)
    if window_start >= end:
        return 0
    bucket = fn.strftime("%Y-%m-%d %H:00:00", TrafficInterval.period_end)
    traffic_rows = (
        TrafficInterval.select(
            TrafficInterval.node,
            TrafficInterval.interface_name,
            bucket.alias("bucket"),
            fn.SUM(TrafficInterval.rx_bytes).alias("rx"),
            fn.SUM(TrafficInterval.tx_bytes).alias("tx"),
            fn.MAX(TrafficInterval.rx_bps).alias("rx_max"),
            fn.MAX(TrafficInterval.tx_bps).alias("tx_max"),
            fn.SUM(TrafficInterval.duration_seconds).alias("dur"),
            fn.COUNT(TrafficInterval.id).alias("cnt"),
        )
        .where(
            (TrafficInterval.valid == True)  # noqa: E712
            & (TrafficInterval.period_end >= window_start)
            & (TrafficInterval.period_end < end)
            & (TrafficInterval.rx_bytes.is_null(False))
        )
        .group_by(TrafficInterval.node, TrafficInterval.interface_name, bucket)
        .dicts()
    )
    resource_bucket = fn.strftime("%Y-%m-%d %H:00:00", NodeMetricSample.collected_at)
    ram_percent = NodeMetricSample.ram_used_bytes * 100.0 / fn.NULLIF(NodeMetricSample.ram_total_bytes, 0)
    disk_percent = NodeMetricSample.disk_used_bytes * 100.0 / fn.NULLIF(NodeMetricSample.disk_total_bytes, 0)
    resource_rows = (
        NodeMetricSample.select(
            NodeMetricSample.node,
            resource_bucket.alias("bucket"),
            fn.AVG(NodeMetricSample.cpu_percent).alias("cpu_avg"),
            fn.MAX(NodeMetricSample.cpu_percent).alias("cpu_max"),
            fn.MIN(NodeMetricSample.cpu_percent).alias("cpu_min"),
            fn.AVG(ram_percent).alias("ram_avg"),
            fn.MAX(ram_percent).alias("ram_max"),
            fn.MAX(NodeMetricSample.ram_total_bytes).alias("ram_total"),
            fn.AVG(NodeMetricSample.ram_used_bytes).alias("ram_used"),
            fn.AVG(disk_percent).alias("disk_avg"),
            fn.MAX(disk_percent).alias("disk_max"),
            fn.MAX(NodeMetricSample.disk_total_bytes).alias("disk_total"),
            fn.AVG(NodeMetricSample.disk_used_bytes).alias("disk_used"),
            fn.COUNT(NodeMetricSample.id).alias("cnt"),
        )
        .where(
            (NodeMetricSample.collected_at >= window_start)
            & (NodeMetricSample.collected_at < end)
        )
        .group_by(NodeMetricSample.node, resource_bucket)
        .dicts()
    )
    count = 0
    for row in traffic_rows:
        duration = float(row["dur"] or 0)
        rx = int(row["rx"] or 0)
        tx = int(row["tx"] or 0)
        _upsert_hourly(
            row["node"],
            row["interface_name"],
            _parse_bucket(row["bucket"]),
            {
                "rx_bytes": rx,
                "tx_bytes": tx,
                "rx_bps_avg": (rx / duration) if duration else None,
                "tx_bps_avg": (tx / duration) if duration else None,
                "rx_bps_max": row["rx_max"],
                "tx_bps_max": row["tx_max"],
                "duration_seconds": duration,
                "sample_count": int(row["cnt"] or 0),
            },
        )
        count += 1
    for row in resource_rows:
        _upsert_hourly(
            row["node"],
            "*",
            _parse_bucket(row["bucket"]),
            {
                "cpu_avg": row["cpu_avg"],
                "cpu_max": row["cpu_max"],
                "cpu_min": row["cpu_min"],
                "ram_percent_avg": row["ram_avg"],
                "ram_percent_max": row["ram_max"],
                "ram_total_bytes": row["ram_total"],
                "ram_used_avg": row["ram_used"],
                "disk_percent_avg": row["disk_avg"],
                "disk_percent_max": row["disk_max"],
                "disk_total_bytes": row["disk_total"],
                "disk_used_avg": row["disk_used"],
                "resource_samples": int(row["cnt"] or 0),
            },
        )
        count += 1
    return count


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
