"""تنظیمات قابل تغییر از پنل. مقدار پیش‌فرض تا زمان ذخیره در پایگاه استفاده می‌شود."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import AppError

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_lock = threading.Lock()
_cache: dict[str, Any] | None = None


@dataclass(frozen=True)
class SettingField:
    key: str
    label: str
    help: str
    kind: str
    default: Any
    group: str
    minimum: float | None = None
    maximum: float | None = None


GROUPS = (
    ("connection", "Node connection"),
    ("alerts", "Alerts"),
    ("retention", "Data retention"),
    ("charts", "Charts and rollup"),
    ("security", "Request limits"),
    ("display", "Display"),
)

FIELDS: tuple[SettingField, ...] = (
    SettingField("report_interval_seconds", "Report interval", "How often a node is expected to report. Seconds.", "int", 30, "connection", 5, 3600),
    SettingField("stale_after_seconds", "Stale after", "An online node is marked delayed after this many seconds.", "int", 90, "connection", 10, 86400),
    SettingField("offline_after_seconds", "Offline after", "A node is offline when no valid report arrives in this time. Keep this longer than Stale after.", "int", 180, "connection", 10, 86400),
    SettingField("max_clock_skew_seconds", "Clock skew", "Samples and requests outside this skew are rejected. Seconds.", "int", 120, "connection", 10, 3600),
    SettingField("max_sample_gap_seconds", "Max sample gap", "A longer gap is not stored as zero traffic. Seconds.", "int", 600, "connection", 5, 86400),
    SettingField("max_plausible_bps", "Max plausible speed", "Faster intervals are invalid. Bytes per second.", "float", 12_500_000_000, "connection", 1_000_000, 1_000_000_000_000),
    SettingField("cpu_alert_percent", "CPU threshold", "Percent.", "float", 90, "alerts", 1, 100),
    SettingField("ram_alert_percent", "RAM threshold", "Percent.", "float", 90, "alerts", 1, 100),
    SettingField("disk_alert_percent", "Disk threshold", "Percent.", "float", 90, "alerts", 1, 100),
    SettingField("traffic_spike_multiplier", "Traffic spike factor", "Compared with the median of recent speeds.", "float", 3, "alerts", 1.1, 100),
    SettingField("traffic_spike_min_bytes", "Spike minimum volume", "Bytes. Smaller bursts do not raise a spike alert.", "int", 50_000_000, "alerts", 0, 10_000_000_000_000),
    SettingField("alert_cooldown_seconds", "Alert cooldown", "Seconds before the same alert can fire again.", "int", 900, "alerts", 60, 86400),
    SettingField("alert_hysteresis", "Recovery margin", "An alert clears when usage falls below threshold times this value.", "float", 0.95, "alerts", 0.5, 0.99),
    SettingField("raw_retention_days", "Raw sample retention", "Days.", "int", 14, "retention", 1, 3650),
    SettingField("hourly_retention_days", "Hourly retention", "Days.", "int", 180, "retention", 1, 3650),
    SettingField("daily_retention_days", "Daily retention", "Days.", "int", 730, "retention", 1, 3650),
    SettingField("nonce_retention_hours", "Nonce retention", "Hours.", "int", 48, "retention", 1, 720),
    SettingField("chart_raw_max_hours", "Raw chart window", "Ranges up to this many hours draw every report. At least 48 hours stay this detailed.", "int", 48, "charts", 1, 168),
    SettingField("chart_hourly_max_days", "Hourly chart window", "Longer ranges use daily buckets. Days.", "int", 45, "charts", 1, 365),
    SettingField("aggregate_lookback_hours", "Rollup lookback", "Recent hours that are aggregated again.", "int", 48, "charts", 1, 168),
    SettingField("task_interval_seconds", "Task interval", "Seconds. Applies on the next scheduler loop.", "int", 30, "charts", 10, 3600),
    SettingField("session_ttl_seconds", "Admin session lifetime", "Seconds. Applies to the next login.", "int", 60 * 60 * 12, "security", 300, 60 * 60 * 24 * 30),
    SettingField("login_rate_limit", "Login attempt limit", "Count per window.", "int", 10, "security", 1, 1000),
    SettingField("login_rate_window_seconds", "Login window", "Seconds.", "int", 60, "security", 10, 3600),
    SettingField("ingest_rate_limit", "Ingest limit", "Requests per node per window.", "int", 120, "security", 10, 10000),
    SettingField("ingest_rate_window_seconds", "Ingest window", "Seconds.", "int", 60, "security", 10, 3600),
    SettingField("max_body_bytes", "Max request body", "Bytes.", "int", 1_048_576, "security", 4096, 10_485_760),
    SettingField("timezone", "Time zone", "Used for today, yesterday, and this month. Example: Asia/Tehran", "str", "Asia/Tehran", "display"),
    SettingField("page_size_default", "Default page size", "Rows.", "int", 20, "display", 1, 100),
    SettingField("page_size_max", "Max page size", "Rows.", "int", 100, "display", 1, 500),
)

BY_KEY = {field.key: field for field in FIELDS}
GROUP_TITLES = dict(GROUPS)


def has_key(name: str) -> bool:
    return name in BY_KEY


def reset_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _store(value: Any) -> str:
    if isinstance(value, float):
        return format(value, ".12g")
    if isinstance(value, int):
        return str(value)
    return str(value)


def _parse(field: SettingField, raw: Any) -> Any:
    if field.kind == "str":
        if not isinstance(raw, str):
            raise AppError("validation_error", f"{field.label} must be text.", 422)
        value = raw.strip()
        if not value or len(value) > 64:
            raise AppError("validation_error", f"{field.label} is invalid.", 422)
        if field.key == "timezone":
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise AppError("validation_error", f"Unknown time zone: {value}", 422) from exc
        return value

    if isinstance(raw, bool) or raw is None:
        raise AppError("validation_error", f"{field.label} must be a number.", 422)
    if isinstance(raw, str):
        text = raw.strip().translate(_DIGITS).replace("٬", "").replace(",", "").replace(" ", "")
        if not text:
            raise AppError("validation_error", f"Enter a value for {field.label}.", 422)
        raw = text
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise AppError("validation_error", f"{field.label} must be a number.", 422) from exc
    if field.kind == "int":
        if not number.is_integer():
            raise AppError("validation_error", f"{field.label} must be a whole number.", 422)
        number = int(number)
    if field.minimum is not None and number < field.minimum:
        raise AppError("validation_error", f"{field.label} must be at least {field.minimum:g}.", 422)
    if field.maximum is not None and number > field.maximum:
        raise AppError("validation_error", f"{field.label} must be at most {field.maximum:g}.", 422)
    return number


def _check_relations(values: dict[str, Any]) -> None:
    report = values["report_interval_seconds"]
    if values["stale_after_seconds"] < report:
        raise AppError("validation_error", "Stale threshold cannot be shorter than the report interval.", 422)
    if values["offline_after_seconds"] < report:
        raise AppError("validation_error", "Offline threshold cannot be shorter than the report interval.", 422)
    if values["offline_after_seconds"] <= values["stale_after_seconds"]:
        raise AppError("validation_error", "Offline threshold must be longer than the stale threshold.", 422)
    if values["max_sample_gap_seconds"] < report:
        raise AppError("validation_error", "Sample gap cannot be shorter than the report interval.", 422)
    if values["page_size_default"] > values["page_size_max"]:
        raise AppError("validation_error", "Default page size cannot exceed the maximum page size.", 422)


def _read_rows() -> dict[str, str]:
    from app.models.entities import PanelSetting

    return {row.key: row.value for row in PanelSetting.select()}


def _fill(rows: dict[str, str]) -> dict[str, Any]:
    loaded: dict[str, Any] = {}
    for field in FIELDS:
        raw = rows.get(field.key)
        loaded[field.key] = field.default if raw is None else _parse(field, raw)
    return loaded


def _try_load() -> None:
    global _cache
    from app.db.database import db

    if not getattr(db, "database", None):
        return
    opened = False
    try:
        if db.is_closed():
            db.connect(reuse_if_open=True)
            opened = True
        loaded = _fill(_read_rows())
    except Exception:
        return
    finally:
        if opened and not db.is_closed():
            db.close()
    with _lock:
        _cache = loaded


def get_value(name: str) -> Any:
    field = BY_KEY[name]
    with _lock:
        cached = None if _cache is None else _cache.get(name, field.default)
        missing = _cache is None
    if not missing:
        return cached
    _try_load()
    with _lock:
        if _cache is None:
            return field.default
        return _cache.get(name, field.default)


def ensure_runtime_settings() -> None:
    """کلیدهای نبوده را با مقدار پیش‌فرض می‌سازد و حافظه را تازه می‌کند."""
    global _cache
    from app.models.entities import PanelSetting
    from app.utils.time import utcnow

    now = utcnow()
    for field in FIELDS:
        PanelSetting.get_or_create(
            key=field.key,
            defaults={"value": _store(field.default), "updated_at": now},
        )
    loaded = _fill(_read_rows())
    with _lock:
        _cache = loaded


def snapshot() -> dict[str, Any]:
    from app.core.settings import settings

    groups = []
    for group_id, title in GROUPS:
        fields = []
        for field in FIELDS:
            if field.group != group_id:
                continue
            fields.append(
                {
                    "key": field.key,
                    "label": field.label,
                    "help": field.help,
                    "type": field.kind,
                    "value": get_value(field.key),
                    "min": field.minimum,
                    "max": field.maximum,
                }
            )
        groups.append({"id": group_id, "title": title, "fields": fields})
    return {"startup": settings.startup_summary(), "groups": groups}


def update_values(raw_values: dict[str, Any], *, actor_id: str | None, ip_address: str | None) -> dict[str, Any]:
    global _cache
    unknown = [key for key in raw_values if key not in BY_KEY]
    if unknown:
        raise AppError("validation_error", "Unknown setting.", 422)
    merged = {field.key: get_value(field.key) for field in FIELDS}
    changed: dict[str, Any] = {}
    for key, raw in raw_values.items():
        parsed = _parse(BY_KEY[key], raw)
        if parsed != merged[key]:
            changed[key] = parsed
            merged[key] = parsed
    _check_relations(merged)
    if not changed:
        return snapshot()

    from app.db.database import db
    from app.models.entities import PanelSetting
    from app.services.audit import write_audit
    from app.utils.time import utcnow

    now = utcnow()
    with db.atomic():
        for key, value in changed.items():
            stored = _store(value)
            PanelSetting.insert(key=key, value=stored, updated_at=now).on_conflict(
                conflict_target=[PanelSetting.key],
                update={PanelSetting.value: stored, PanelSetting.updated_at: now},
            ).execute()
        write_audit(
            actor_type="user",
            actor_id=actor_id,
            action="settings_updated",
            entity_type="settings",
            ip_address=ip_address,
            details={"keys": sorted(changed)},
        )
    with _lock:
        _cache = merged
    return snapshot()
