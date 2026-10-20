"""آماده‌سازی پوشه‌ها، کلید، پایگاه‌داده و مدیر اولیه. اجرای دوباره بی‌اثر است."""

from __future__ import annotations

from app.core.settings import settings
from app.db.database import DatabaseError, connect_db, db
from app.logging_setup import configure_logging, get_logger
from app.models.entities import ALL_MODELS
from app.services.auth_service import ensure_admin
from app.services.runtime_settings import ensure_runtime_settings


def _ensure_node_removed_column() -> None:
    columns = {row[1] for row in db.execute_sql("PRAGMA table_info(node)").fetchall()}
    if "deleted_at" not in columns:
        db.execute_sql("ALTER TABLE node ADD COLUMN deleted_at DATETIME")


def bootstrap() -> None:
    settings.ensure_directories()
    settings.ensure_secret()
    settings.validate_runtime()
    configure_logging()
    try:
        connect_db(settings.database_path)
        db.create_tables(ALL_MODELS, safe=True)
        _ensure_node_removed_column()
        ensure_runtime_settings()
        ensure_admin()
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"Database setup failed: {exc}") from exc
    get_logger().info("Database ready: %s", settings.database_path)
