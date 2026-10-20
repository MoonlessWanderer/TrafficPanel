"""پاسخ یکدست API."""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, meta: dict | None = None) -> dict:
    payload: dict[str, Any] = {"ok": True, "data": data}
    if meta is not None:
        payload["meta"] = meta
    return payload


def fail(code: str, message: str, details: dict | None = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"ok": False, "error": error}
