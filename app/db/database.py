"""اتصال SQLite. مسیر همیشه از تنظیمات مرکزی می‌آید."""

from __future__ import annotations

from pathlib import Path

from peewee import SqliteDatabase

db = SqliteDatabase(None)


class DatabaseError(Exception):
    """پایگاه‌داده آماده نیست."""


def connect_db(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not db.is_closed():
            db.close()
        db.init(
            str(path),
            pragmas={
                "journal_mode": "wal",
                "foreign_keys": 1,
                "busy_timeout": 5000,
                "synchronous": 1,
                "cache_size": -8000,
            },
            check_same_thread=False,
        )
        db.connect(reuse_if_open=True)
        db.execute_sql("SELECT 1")
    except Exception as exc:
        raise DatabaseError(f"Could not connect to the database ({path}): {exc}") from exc


def connection():
    """اتصال نخ فعلی را برای درخواست یا وظیفه پس‌زمینه باز می‌کند."""
    if db.is_closed():
        db.connect(reuse_if_open=True)
    return db
