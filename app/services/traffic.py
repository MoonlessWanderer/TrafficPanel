"""تفاضل شمارنده‌های شبکه.

ترافیک بازه فقط اختلاف دو نمونه معتبر است. اگر شمارنده عقب برگردد، بازنشانی
تشخیص داده می‌شود و مقدار منفی ثبت نمی‌شود. فاصله بیش از آستانه، شکاف است و
صفر محسوب نمی‌شود.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CounterPoint:
    collected_at: datetime
    rx_bytes: int
    tx_bytes: int


@dataclass(frozen=True)
class TrafficDelta:
    duration_seconds: float
    rx_bytes: int | None
    tx_bytes: int | None
    rx_bps: float | None
    tx_bps: float | None
    reset_detected: bool
    valid: bool
    reason: str


def compute_traffic_delta(
    previous: CounterPoint,
    current: CounterPoint,
    *,
    max_gap_seconds: float,
    max_plausible_bps: float,
) -> TrafficDelta:
    duration = (current.collected_at - previous.collected_at).total_seconds()
    if duration <= 0:
        return TrafficDelta(duration, None, None, None, None, False, False, "non_monotonic_time")
    if duration > max_gap_seconds:
        return TrafficDelta(duration, None, None, None, None, False, False, "gap")

    reset = current.rx_bytes < previous.rx_bytes or current.tx_bytes < previous.tx_bytes
    rx = current.rx_bytes - previous.rx_bytes if current.rx_bytes >= previous.rx_bytes else current.rx_bytes
    tx = current.tx_bytes - previous.tx_bytes if current.tx_bytes >= previous.tx_bytes else current.tx_bytes
    rx_bps = rx / duration
    tx_bps = tx / duration
    if rx_bps > max_plausible_bps or tx_bps > max_plausible_bps:
        return TrafficDelta(duration, None, None, None, None, reset, False, "implausible")
    return TrafficDelta(
        duration_seconds=duration,
        rx_bytes=rx,
        tx_bytes=tx,
        rx_bps=rx_bps,
        tx_bps=tx_bps,
        reset_detected=reset,
        valid=True,
        reason="reset" if reset else "ok",
    )
