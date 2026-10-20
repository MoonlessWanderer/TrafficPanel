from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_TAG = re.compile(r"^[\w\u0600-\u06FF .-]{1,32}$", re.UNICODE)


def _tags(value: list[str]) -> list[str]:
    if len(value) > 20:
        raise ValueError("At most 20 tags are allowed.")
    cleaned: list[str] = []
    for item in value:
        text = item.strip()
        if not text:
            continue
        if not _TAG.match(text):
            raise ValueError("A tag may contain only letters, digits, dots, hyphens, and spaces.")
        if text not in cleaned:
            cleaned.append(text)
    return cleaned


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list)
    group_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("A node name is required.")
        return text

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        return _tags(value)


class NodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("A node name is required.")
        return text

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _tags(value)
