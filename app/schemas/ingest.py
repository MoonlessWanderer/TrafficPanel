from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InterfaceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=64)
    rx_bytes: int = Field(ge=0)
    tx_bytes: int = Field(ge=0)
    rx_packets: int = Field(default=0, ge=0)
    tx_packets: int = Field(default=0, ge=0)
    rx_errors: int | None = Field(default=None, ge=0)
    tx_errors: int | None = Field(default=None, ge=0)
    rx_dropped: int | None = Field(default=None, ge=0)
    tx_dropped: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        text = value.strip()
        if not text or any(char.isspace() for char in text):
            raise ValueError("The network interface name is invalid.")
        return text


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    collected_at: datetime
    agent_version: str = Field(min_length=1, max_length=32)
    os_name: str | None = Field(default=None, max_length=128)
    kernel_version: str | None = Field(default=None, max_length=128)


class MetricsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    collected_at: datetime
    cpu_percent: float = Field(ge=0, le=100)
    ram_total_bytes: int = Field(ge=0)
    ram_used_bytes: int = Field(ge=0)
    ram_free_bytes: int = Field(ge=0)
    disk_total_bytes: int = Field(ge=0)
    disk_used_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    load_1: float = Field(ge=0)
    load_5: float = Field(ge=0)
    load_15: float = Field(ge=0)
    uptime_seconds: float = Field(ge=0)
    agent_version: str = Field(min_length=1, max_length=32)
    os_name: str = Field(min_length=1, max_length=128)
    kernel_version: str = Field(min_length=1, max_length=128)
    interfaces: list[InterfaceIn] = Field(min_length=1, max_length=64)
