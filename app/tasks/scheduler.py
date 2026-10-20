"""حلقه نگهداری، تجمیع و بررسی قطع اتصال."""

from __future__ import annotations

import asyncio

from app.core.settings import settings
from app.logging_setup import get_logger
from app.services.aggregates import run_maintenance


async def scheduler_loop(stop: asyncio.Event) -> None:
    logger = get_logger()
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_maintenance)
        except Exception:
            logger.exception("A periodic task failed and will run again next cycle.")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.task_interval_seconds)
        except asyncio.TimeoutError:
            continue
