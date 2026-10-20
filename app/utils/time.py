"""زمان UTC برای ذخیره و مرزهای محلی برای فیلترهای تقویمی."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.settings import settings
from app.core.errors import AppError

PRESETS = {"1h", "6h", "24h", "7d", "30d", "today", "yesterday", "month", "custom"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_db_datetime(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid time: {value}")


def retention_split(now: datetime | None = None) -> datetime:
    """ابتدای ساعتی که داده‌های خام قدیمی‌تر از آن ممکن است حذف شده باشند."""
    moment = now or utcnow()
    raw_begin = moment - timedelta(days=settings.raw_retention_days)
    return raw_begin.replace(minute=0, second=0, microsecond=0)


def resolve_range(
    preset: str | None,
    start: datetime | None,
    end: datetime | None,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, str]:
    moment = now or utcnow()
    if start is not None:
        start = as_utc(start)
    if end is not None:
        end = as_utc(end)
    chosen = (preset or "").strip().lower()
    if chosen in {"", "custom"} and start is None and end is None:
        chosen = "1h"
    elif chosen in {"", "custom"} and (start is not None or end is not None):
        chosen = "custom"
    if chosen not in PRESETS:
        raise AppError("invalid_range", "Unknown time range.", 422)
    if chosen == "custom":
        if start is None or end is None:
            raise AppError("invalid_range", "A custom range needs a start and an end.", 422)
        if end <= start:
            raise AppError("invalid_range", "The end time must be after the start time.", 422)
        if end - start > timedelta(days=366):
            raise AppError("invalid_range", "The range cannot be longer than 366 days.", 422)
        return start, end, "custom"

    zone = ZoneInfo(settings.timezone)
    local_now = moment.replace(tzinfo=timezone.utc).astimezone(zone)
    if chosen == "1h":
        return moment - timedelta(hours=1), moment, chosen
    if chosen == "6h":
        return moment - timedelta(hours=6), moment, chosen
    if chosen == "24h":
        return moment - timedelta(hours=24), moment, chosen
    if chosen == "7d":
        return moment - timedelta(days=7), moment, chosen
    if chosen == "30d":
        return moment - timedelta(days=30), moment, chosen
    if chosen == "today":
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start_local.astimezone(timezone.utc).replace(tzinfo=None), moment, chosen
    if chosen == "yesterday":
        today_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_local = today_local - timedelta(days=1)
        end_local = today_local
        return (
            start_local.astimezone(timezone.utc).replace(tzinfo=None),
            end_local.astimezone(timezone.utc).replace(tzinfo=None),
            chosen,
        )
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start.astimezone(timezone.utc).replace(tzinfo=None), moment, chosen


def five_minute_bucket(value: datetime) -> datetime:
    value = as_utc(value).replace(second=0, microsecond=0)
    return value.replace(minute=(value.minute // 5) * 5)


def five_minute_slots(start: datetime, end: datetime) -> list[datetime]:
    start = as_utc(start)
    end = as_utc(end)
    if start >= end:
        return []
    cursor = five_minute_bucket(start)
    if cursor < start:
        cursor += timedelta(minutes=5)
    slots: list[datetime] = []
    while cursor < end:
        slots.append(cursor)
        cursor += timedelta(minutes=5)
    return slots


def hour_bucket(value: datetime) -> datetime:
    value = as_utc(value)
    return value.replace(minute=0, second=0, microsecond=0)


def local_day_start(value: datetime) -> datetime:
    value = as_utc(value)
    zone = ZoneInfo(settings.timezone)
    aware = value.replace(tzinfo=timezone.utc).astimezone(zone)
    start_local = aware.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)
