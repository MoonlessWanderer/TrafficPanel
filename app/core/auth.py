"""جداسازی دسترسی مدیر و Agent."""

from __future__ import annotations

import hmac
import time

from fastapi import Request

from app.core.settings import settings
from app.core.errors import AppError
from app.core.rate_limit import rate_limiter
from app.core.security import hash_password, hash_token, verify_password
from app.models import Node, Session  # Node is used by require_node
from app.services.alerts import raise_alert
from app.services.audit import write_audit
from app.services.present import active_credential
from app.utils.time import utcnow

_DUMMY_KEY_HASH = hash_password("dummy-node-key-not-used")


def client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def resolve_session(request: Request) -> tuple | None:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    session = Session.get_or_none(Session.token_hash == hash_token(raw))
    if session is None or session.revoked_at is not None or session.expires_at <= utcnow():
        return None
    user = session.user
    if not user.is_active:
        return None
    return user, session


def require_user(request: Request):
    resolved = resolve_session(request)
    if resolved is None:
        raise AppError("unauthorized", "Sign in to continue.", 401)
    user, session = resolved
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        header = request.headers.get("x-csrf-token", "")
        cookie = request.cookies.get(settings.csrf_cookie_name, "")
        if not header or not cookie or not hmac.compare_digest(header, cookie):
            raise AppError("csrf", "The request could not be confirmed. Refresh the page.", 403)
        if not hmac.compare_digest(hash_token(header), session.csrf_hash):
            raise AppError("csrf", "The request could not be confirmed. Refresh the page.", 403)
    request.state.user = user
    request.state.session = session
    return user


def require_node(request: Request) -> Node:
    node_id = request.headers.get("x-node-id", "").strip()
    key = request.headers.get("x-node-key", "")
    timestamp_raw = request.headers.get("x-node-timestamp", "").strip()
    nonce = request.headers.get("x-node-nonce", "").strip()
    if not node_id or not key or not timestamp_raw or not nonce:
        raise AppError("unauthorized", "Node authentication is incomplete.", 401)
    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise AppError("unauthorized", "Node authentication is incomplete.", 401) from exc
    if abs(time.time() - timestamp) > settings.max_clock_skew_seconds:
        raise AppError("clock_skew", "The node clock is too far from the panel.", 401)
    # A shared ceiling stops a flood from spending scrypt on every guess.
    # It is separate from the per-node budget, which starts only after the key matches.
    rate_limiter.check(
        "ingest-attempt",
        max(settings.ingest_rate_limit * 20, settings.ingest_rate_limit),
        settings.ingest_rate_window_seconds,
    )
    node = Node.get_or_none(Node.node_id == node_id)
    credential = active_credential(node) if node is not None else None
    stored = credential.key_hash if credential is not None else _DUMMY_KEY_HASH
    if node is None or credential is None or not verify_password(key, stored):
        if node is not None:
            raise_alert(
                node=node,
                alert_type="auth_error",
                severity="warning",
                message="A node failed to authenticate.",
            )
            write_audit(
                actor_type="node",
                actor_id=node.node_id,
                action="node_auth_failed",
                entity_type="node",
                entity_id=node.node_id,
                ip_address=client_ip(request),
            )
        else:
            write_audit(
                actor_type="anonymous",
                actor_id=None,
                action="node_auth_failed",
                entity_type="node",
                entity_id=None,
                ip_address=client_ip(request),
            )
        raise AppError("unauthorized", "The node id or key is not valid.", 401)
    if not node.is_enabled:
        raise AppError("node_disabled", "This node is disabled.", 403)
    rate_limiter.check(
        f"ingest:{node.node_id}",
        settings.ingest_rate_limit,
        settings.ingest_rate_window_seconds,
    )
    request.state.node = node
    request.state.node_nonce = nonce
    return node
