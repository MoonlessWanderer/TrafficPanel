"""نقطه ورود پنل. راه‌اندازی تنظیمات، پایگاه‌داده و Uvicorn."""

from __future__ import annotations

import sys


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except Exception:
            pass


def main() -> None:
    _configure_stdio()
    try:
        from app.core.settings import SettingsError, settings

        settings.reload()
        settings.ensure_directories()
        settings.ensure_secret()
        settings.validate_runtime()
        from app.bootstrap import bootstrap

        bootstrap()
        from app.factory import create_app

        application = create_app()
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except Exception as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    import uvicorn

    from app.logging_setup import get_logger

    logger = get_logger()
    logger.info(
        "Panel ready on %s:%s. Database: %s",
        settings.host,
        settings.port,
        settings.database_path,
    )
    ssl_kwargs: dict[str, str] = {}
    if settings.ssl_certfile and settings.ssl_keyfile:
        ssl_kwargs["ssl_certfile"] = settings.ssl_certfile
        ssl_kwargs["ssl_keyfile"] = settings.ssl_keyfile
    uvicorn.run(
        application,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
        workers=1,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()
