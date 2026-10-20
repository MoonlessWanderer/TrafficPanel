from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.schemas.nodes import _tags

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    color: str = "#0f6e6b"
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("A group name is required.")
        return text

    @field_validator("color")
    @classmethod
    def clean_color(cls, value: str) -> str:
        if not _COLOR.match(value):
            raise ValueError("Color must look like #RRGGBB.")
        return value.lower()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        return _tags(value)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = None
    tags: list[str] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("A group name is required.")
        return text

    @field_validator("color")
    @classmethod
    def clean_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _COLOR.match(value):
            raise ValueError("Color must look like #RRGGBB.")
        return value.lower()

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _tags(value)


