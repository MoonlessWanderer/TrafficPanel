"""Replace this process with a new copy of the same command."""

from __future__ import annotations

import os
import sys
import threading
import time


def restart_process() -> None:
    python = sys.executable
    os.execl(python, python, *sys.argv)


def schedule_restart(delay: float = 0.6) -> None:
    def _run() -> None:
        time.sleep(delay)
        from app.db.database import db

        if not db.is_closed():
            db.close()
        restart_process()

    threading.Thread(target=_run, daemon=False).start()
