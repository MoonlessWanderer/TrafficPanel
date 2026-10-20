"""محدودیت نرخ در حافظه، مناسب اجرای تک‌فرایندی SQLite."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.errors import AppError


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise AppError(
                    "rate_limited",
                    "Too many requests. Try again in a moment.",
                    429,
                )
            bucket.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
