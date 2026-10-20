"""ثبت گزارش بدون افشای رازها."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from app.core.settings import settings

_REDACT = re.compile(
    r"(?i)(password|secret|token|node[-_]?key|authorization|x-node-key)(\s*[=:]\s*)(\S+)"
)
_CONFIGURED = False


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _REDACT.sub(r"\1\2***", record.msg)
        if record.args:
            redacted = []
            for arg in record.args:
                if isinstance(arg, str):
                    redacted.append(_REDACT.sub(r"\1\2***", arg))
                else:
                    redacted.append(arg)
            record.args = tuple(redacted)
        return True


def configure_logging() -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("panel")
    logger.setLevel(settings.log_level)
    if _CONFIGURED:
        return logger
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    redact = RedactFilter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(redact)
    logger.addHandler(stream)
    settings.ensure_directories()
    file_handler = RotatingFileHandler(
        settings.log_dir / "panel.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redact)
    logger.addHandler(file_handler)
    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("panel")
