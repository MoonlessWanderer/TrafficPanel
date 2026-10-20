"""به‌روزرسانی تنظیمات پنل."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    values: dict[str, str | int | float] = Field(default_factory=dict)
