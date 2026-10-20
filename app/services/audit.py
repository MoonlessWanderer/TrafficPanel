"""ثبت عملیات حساس، بدون رمز و کلید."""

from __future__ import annotations

from typing import Any

from app.models import AuditLog
from app.utils.jsonbox import dump_json
from app.utils.time import utcnow

SENSITIVE_KEYS = {"password", "key", "node_key", "secret", "token", "csrf", "authorization"}


def _clean(details: dict[str, Any] | None) -> str | None:
    if not details:
        return None
    cleaned = {key: value for key, value in details.items() if key.lower() not in SENSITIVE_KEYS}
    return dump_json(cleaned)


def write_audit(
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    AuditLog.create(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        details=_clean(details),
        created_at=utcnow(),
    )
