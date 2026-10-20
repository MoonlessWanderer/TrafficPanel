"""تبدیل واحد. حجم شبکه بر پایه ۱۰۰۰ و دیسک/حافظه بر پایه ۱۰۲۴ است."""

from __future__ import annotations


def _scale(value: float, base: int, units: list[str]) -> dict[str, float | str]:
    sign = -1 if value < 0 else 1
    amount = abs(float(value))
    index = 0
    while amount >= base and index < len(units) - 1:
        amount /= base
        index += 1
    scaled = sign * amount
    if index == 0:
        text = f"{int(round(scaled))} {units[index]}"
    else:
        text = f"{scaled:.2f} {units[index]}"
    return {"value": scaled, "unit": units[index], "text": text}


def format_bytes(value: float | None, *, binary: bool = False) -> dict | None:
    if value is None:
        return None
    if binary:
        return _scale(value, 1024, ["B", "KB", "MB", "GB", "TB", "PB"])
    return _scale(value, 1000, ["B", "KB", "MB", "GB", "TB", "PB"])


def format_bps(bytes_per_second: float | None) -> dict | None:
    """سرعت بر حسب بیت بر ثانیه و پله‌های دهدهی (Kbps، Mbps، Gbps)."""
    if bytes_per_second is None:
        return None
    return _scale(float(bytes_per_second) * 8, 1000, ["bps", "Kbps", "Mbps", "Gbps", "Tbps"])
