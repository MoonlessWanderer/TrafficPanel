"""مهاجرت نسخه‌دار. اجرای دوباره، جدول یا نسخه تکراری نمی‌سازد."""

from __future__ import annotations

from app.db.database import DatabaseError, db
from app.models.entities import ALL_MODELS, SchemaVersion
from app.utils.time import utcnow


def _migration_001() -> None:
    db.create_tables(ALL_MODELS, safe=True)


def _migration_002() -> None:
    from app.models.entities import PanelSetting

    db.create_tables([PanelSetting], safe=True)


MIGRATIONS = [(1, _migration_001), (2, _migration_002)]


def run_migrations() -> None:
    try:
        db.create_tables([SchemaVersion], safe=True)
        current = 0
        latest = SchemaVersion.select().order_by(SchemaVersion.version.desc()).first()
        if latest is not None:
            current = latest.version
        for version, apply in MIGRATIONS:
            if version <= current:
                continue
            with db.atomic():
                apply()
                SchemaVersion.create(version=version, applied_at=utcnow())
    except DatabaseError:
        raise
    except Exception as exc:
        raise DatabaseError(f"Database migration failed: {exc}") from exc
