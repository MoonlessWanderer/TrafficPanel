"""خواندن فیلترهای قابل اشتراک از query string."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import Request

from app.core.settings import settings
from app.core.errors import AppError
from app.utils.time import as_utc, resolve_range

SORTS = {
    "name",
    "-name",
    "traffic",
    "-traffic",
    "status",
    "-status",
    "cpu",
    "-cpu",
    "ram",
    "-ram",
    "last_seen",
    "-last_seen",
}
STATUSES = {"online", "offline", "disabled"}
KINDS = {"rx", "tx", "total"}
BUCKETS = {"auto", "raw", "hour", "day"}


@dataclass
class QueryFilters:
    start: datetime
    end: datetime
    preset: str
    node_ids: list[str]
    group_ids: list[int]
    q: str | None
    status: str | None
    traffic_kind: str
    traffic_min: int | None
    traffic_max: int | None
    cpu_min: float | None
    cpu_max: float | None
    ram_min: float | None
    ram_max: float | None
    disk_min: float | None
    disk_max: float | None
    last_seen_after: datetime | None
    last_seen_before: datetime | None
    sort: str
    page: int
    page_size: int
    bucket: str


def _parse_dt(value: str | None) -> datetime | None:
    if value is None or value.strip() == "":
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AppError("invalid_range", "Time values must use ISO 8601.", 422) from exc
    return as_utc(parsed)


def _optional_float(value: str | None, name: str) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise AppError("invalid_filter", f"{name} must be a number.", 422) from exc


def _optional_int(value: str | None, name: str) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise AppError("invalid_filter", f"{name} must be a whole number.", 422) from exc


def parse_filters(request: Request) -> QueryFilters:
    params = request.query_params
    preset = params.get("preset")
    start, end, resolved = resolve_range(preset, _parse_dt(params.get("from")), _parse_dt(params.get("to")))
    node_ids: list[str] = []
    for value in params.getlist("node_ids"):
        node_ids.extend(part.strip() for part in value.split(",") if part.strip())
    group_ids: list[int] = []
    for value in params.getlist("group_ids"):
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                group_ids.append(int(part))
            except ValueError as exc:
                raise AppError("invalid_filter", "Group id must be a number.", 422) from exc
    status = params.get("status") or None
    if status and status not in STATUSES:
        raise AppError("invalid_filter", "Status must be online, offline, or disabled.", 422)
    kind = params.get("traffic") or "total"
    if kind not in KINDS:
        raise AppError("invalid_filter", "Traffic type must be rx, tx, or total.", 422)
    sort = params.get("sort") or "name"
    if sort not in SORTS:
        raise AppError("invalid_filter", "Unknown sort field.", 422)
    bucket = params.get("bucket") or "auto"
    if bucket not in BUCKETS:
        raise AppError("invalid_filter", "Unknown chart bucket.", 422)
    try:
        page = int(params.get("page") or "1")
        page_size = int(params.get("page_size") or str(settings.page_size_default))
    except ValueError as exc:
        raise AppError("invalid_filter", "Pagination is invalid.", 422) from exc
    if page < 1:
        raise AppError("invalid_filter", "Page number must start at 1.", 422)
    page_size = min(max(page_size, 1), settings.page_size_max)
    q = (params.get("q") or "").strip() or None
    return QueryFilters(
        start=start,
        end=end,
        preset=resolved,
        node_ids=node_ids,
        group_ids=group_ids,
        q=q,
        status=status,
        traffic_kind=kind,
        traffic_min=_optional_int(params.get("traffic_min"), "minimum traffic"),
        traffic_max=_optional_int(params.get("traffic_max"), "maximum traffic"),
        cpu_min=_optional_float(params.get("cpu_min"), "minimum CPU"),
        cpu_max=_optional_float(params.get("cpu_max"), "maximum CPU"),
        ram_min=_optional_float(params.get("ram_min"), "minimum RAM"),
        ram_max=_optional_float(params.get("ram_max"), "maximum RAM"),
        disk_min=_optional_float(params.get("disk_min"), "minimum disk"),
        disk_max=_optional_float(params.get("disk_max"), "maximum disk"),
        last_seen_after=_parse_dt(params.get("last_seen_after")),
        last_seen_before=_parse_dt(params.get("last_seen_before")),
        sort=sort,
        page=page,
        page_size=page_size,
        bucket=bucket,
    )


def choose_bucket(filters: QueryFilters) -> str:
    if filters.bucket != "auto":
        return filters.bucket
    span = (filters.end - filters.start).total_seconds()
    if span <= settings.chart_raw_max_hours * 3600:
        return "raw"
    if span <= settings.chart_hourly_max_days * 86400:
        return "hour"
    return "day"
