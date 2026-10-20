"""آماده‌سازی پوشه‌ها، کلید، پایگاه‌داده و مدیر اولیه. اجرای دوباره بی‌اثر است."""

from __future__ import annotations

from app.core.settings import settings
from app.db.database import DatabaseError, connect_db
from app.db.migrations import run_migrations
from app.logging_setup import configure_logging, get_logger
from app.services.auth_service import ensure_admin
from app.services.runtime_settings import ensure_runtime_settings


def bootstrap() -> None:
    settings.ensure_directories()
    settings.ensure_secret()
    settings.validate_runtime()
    configure_logging()
    try:
        connect_db(settings.database_path)
        run_migrations()
        ensure_runtime_settings()
        ensure_admin()
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"Database setup failed: {exc}") from exc
    get_logger().info("Database ready: %s", settings.database_path)
