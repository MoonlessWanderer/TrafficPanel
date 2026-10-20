"""Load startup variables from config.py. Panel settings stay in the database."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import config


class SettingsError(Exception):
    """Startup configuration is missing or invalid."""


def _path(raw: str, default: Path) -> Path:
    path = Path(raw) if raw else default
    if not path.is_absolute():
        path = Path(config.__file__).resolve().parent / path
    return path


class Settings:
    def __init__(self) -> None:
        self.base_dir = Path(config.__file__).resolve().parent
        self.secret_key: str | None = None
        self.reload()

    def reload(self) -> None:
        import config as cfg

        self.base_dir = Path(cfg.__file__).resolve().parent
        self.env = (cfg.ENV or "development").strip().lower()
        if self.env not in {"development", "production", "test"}:
            raise SettingsError("ENV must be development, production, or test.")

        self.host = (cfg.HOST or "127.0.0.1").strip()
        try:
            self.port = int(cfg.PORT)
        except (TypeError, ValueError) as exc:
            raise SettingsError("PORT must be an integer.") from exc
        if not 1 <= self.port <= 65535:
            raise SettingsError("PORT must be between 1 and 65535.")

        self.data_dir = _path(cfg.DATA_DIR, self.base_dir / "data")
        self.reports_dir = _path(cfg.REPORTS_DIR, self.data_dir / "reports")
        self.log_dir = _path(cfg.LOG_DIR, self.base_dir / "logs")
        self.database_path = _path(cfg.DATABASE_PATH, self.data_dir / "panel.db")

        self.secret_key = (cfg.SECRET_KEY or "").strip() or None
        self.admin_username = (cfg.ADMIN_USERNAME or "").strip() or None
        self.admin_password = cfg.ADMIN_PASSWORD or None

        self.session_cookie_name = (cfg.SESSION_COOKIE or "vps_session").strip()
        self.csrf_cookie_name = (cfg.CSRF_COOKIE or "vps_csrf").strip()
        if cfg.COOKIE_SECURE is None:
            self.cookie_secure = self.env == "production"
        else:
            self.cookie_secure = bool(cfg.COOKIE_SECURE)

        self.log_level = (cfg.LOG_LEVEL or "INFO").strip().upper()
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise SettingsError("LOG_LEVEL is invalid.")
        try:
            self.log_max_bytes = int(cfg.LOG_MAX_BYTES)
            self.log_backup_count = int(cfg.LOG_BACKUP_COUNT)
        except (TypeError, ValueError) as exc:
            raise SettingsError("Log size settings must be integers.") from exc
        if self.log_max_bytes < 10_000 or self.log_backup_count < 1:
            raise SettingsError("Log size or backup count is invalid.")

        self.ssl_certfile = (cfg.SSL_CERTFILE or "").strip() or None
        self.ssl_keyfile = (cfg.SSL_KEYFILE or "").strip() or None
        self.trusted_hosts = [item.strip() for item in (cfg.TRUSTED_HOSTS or "").split(",") if item.strip()]

        self.static_dir = self.base_dir / "static"
        self.templates_dir = self.base_dir / "templates"
        self.app_name = "VPS Monitor"
        self.app_version = "1.0.0"

        from app.services.runtime_settings import reset_cache

        reset_cache()

    def __getattr__(self, name: str):
        from app.services.runtime_settings import get_value, has_key

        if has_key(name):
            return get_value(name)
        raise AttributeError(name)

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.reports_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                try:
                    os.chmod(directory, 0o750)
                except OSError:
                    pass

    def ensure_secret(self) -> None:
        if self.secret_key:
            if len(self.secret_key) < 32:
                raise SettingsError("SECRET_KEY must be at least 32 characters.")
            return
        if self.env == "production":
            raise SettingsError("Set SECRET_KEY in config.py to at least 32 characters for production.")
        self.ensure_directories()
        secret_path = self.data_dir / ".secret_key"
        if secret_path.is_file():
            value = secret_path.read_text(encoding="utf-8").strip()
            if len(value) < 32:
                raise SettingsError("The local key file is incomplete. Check data/.secret_key.")
            self.secret_key = value
            return
        import secrets

        value = secrets.token_urlsafe(48)
        secret_path.write_text(value + "\n", encoding="utf-8")
        try:
            if os.name != "nt":
                os.chmod(secret_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        self.secret_key = value

    def validate_runtime(self) -> None:
        if self.database_path.exists() and self.database_path.is_dir():
            raise SettingsError("DATABASE_PATH points to a directory, not a file.")
        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            raise SettingsError("SSL_CERTFILE and SSL_KEYFILE must both be set in config.py.")
        if self.ssl_certfile and not Path(self.ssl_certfile).is_file():
            raise SettingsError("TLS certificate file was not found.")
        if self.ssl_keyfile and not Path(self.ssl_keyfile).is_file():
            raise SettingsError("TLS key file was not found.")
        if not self.secret_key:
            raise SettingsError("The application secret is not ready.")

    def startup_summary(self) -> list[dict[str, str]]:
        return [
            {"label": "Environment", "value": self.env},
            {"label": "Address", "value": f"{self.host}:{self.port}"},
            {"label": "Database", "value": str(self.database_path)},
            {"label": "Log", "value": f"{self.log_level} · {self.log_dir}"},
            {"label": "Secure cookie", "value": "Yes" if self.cookie_secure else "No"},
        ]


settings = Settings()
