"""ورود مدیر و نشست کوکی."""

from __future__ import annotations

from datetime import timedelta

from app.core.settings import settings
from app.core.errors import AppError
from app.core.security import hash_password, hash_token, new_session_token, verify_password

_DUMMY_PASSWORD_HASH = hash_password("unused-timing-padding")
from app.models import Session, User
from app.services.audit import write_audit
from app.utils.time import isoformat, utcnow


def ensure_admin() -> None:
    if User.select().exists():
        return
    if not settings.admin_username or not settings.admin_password:
        from app.logging_setup import get_logger

        get_logger().warning(
            "No admin user exists. Set ADMIN_USERNAME and ADMIN_PASSWORD in config.py."
        )
        return
    if len(settings.admin_password) < 10:
        raise AppError("weak_password", "The admin password must be at least 10 characters.", 500)
    now = utcnow()
    user = User.create(
        username=settings.admin_username,
        password_hash=hash_password(settings.admin_password),
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    write_audit(
        actor_type="system",
        actor_id=None,
        action="admin_bootstrapped",
        entity_type="user",
        entity_id=str(user.id),
        details={"username": user.username},
    )


def login(username: str, password: str, *, ip_address: str | None, user_agent: str | None) -> tuple[User, str, str]:
    user = User.get_or_none(User.username == username.strip())
    stored = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_ok = verify_password(password, stored)
    if user is None or not user.is_active or not password_ok:
        write_audit(
            actor_type="anonymous",
            actor_id=None,
            action="login_failed",
            entity_type="user",
            entity_id=None,
            ip_address=ip_address,
            details={"username": username.strip()[:64]},
        )
        raise AppError("invalid_credentials", "The username or password is incorrect.", 401)
    now = utcnow()
    token = new_session_token()
    csrf = new_session_token()
    Session.create(
        user=user,
        token_hash=hash_token(token),
        csrf_hash=hash_token(csrf),
        expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
        created_at=now,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
    )
    write_audit(
        actor_type="user",
        actor_id=str(user.id),
        action="login",
        entity_type="user",
        entity_id=str(user.id),
        ip_address=ip_address,
    )
    return user, token, csrf


def logout(session: Session, *, ip_address: str | None) -> None:
    session.revoked_at = utcnow()
    session.save()
    write_audit(
        actor_type="user",
        actor_id=str(session.user_id),
        action="logout",
        entity_type="user",
        entity_id=str(session.user_id),
        ip_address=ip_address,
    )


def user_payload(user: User) -> dict:
    return {
        "username": user.username,
        "is_active": user.is_active,
        "created_at": isoformat(user.created_at),
    }
