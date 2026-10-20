"""ساخت برنامه FastAPI."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.settings import settings
from app.api.v1.routes import router as api_router
from app.core.errors import AppError
from app.db.database import db
from app.logging_setup import get_logger
from app.tasks.scheduler import scheduler_loop
from app.utils.responses import fail
from app.web.routes import router as web_router


@asynccontextmanager
async def lifespan(application: FastAPI):
    from app.bootstrap import bootstrap

    bootstrap()
    stop = asyncio.Event()
    task = None
    if application.state.enable_scheduler:
        task = asyncio.create_task(scheduler_loop(stop))
    try:
        yield
    finally:
        stop.set()
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if not db.is_closed():
            db.close()


def create_app(*, enable_scheduler: bool | None = None) -> FastAPI:
    application = FastAPI(
        title="VPS Monitor",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.env != "production" else None,
        redoc_url=None,
    )
    if enable_scheduler is None:
        enable_scheduler = settings.env != "test"
    application.state.enable_scheduler = enable_scheduler

    @application.middleware("http")
    async def security_and_db(request: Request, call_next):
        if settings.trusted_hosts:
            host = request.headers.get("host", "").split(":")[0]
            if host not in settings.trusted_hosts:
                return JSONResponse(fail("invalid_host", "This host is not allowed."), status_code=400)
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length:
                try:
                    size = int(length)
                except ValueError:
                    size = 0
                if size > settings.max_body_bytes:
                    return JSONResponse(fail("body_too_large", "The request body is too large."), status_code=413)
        if db.database:
            db.connect(reuse_if_open=True)
        try:
            response = await call_next(request)
        finally:
            if not db.is_closed():
                db.close()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; "
            "font-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @application.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return JSONResponse(fail(exc.code, exc.message, exc.details or None), status_code=exc.status_code)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = []
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", []) if part != "body")
            details.append({"field": location or "body", "message": "This value is invalid."})
        return JSONResponse(
            fail("validation_error", "The submitted values are invalid.", {"fields": details}),
            status_code=422,
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or request.url.path == "/health":
            message = "Not found." if exc.status_code == 404 else "The request could not be completed."
            return JSONResponse(fail("http_error", message), status_code=exc.status_code)
        if exc.status_code == 404:
            return RedirectResponse("/", status_code=302)
        return JSONResponse(fail("http_error", "The request could not be completed."), status_code=exc.status_code)

    @application.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        get_logger().exception("Unexpected error on %s", request.url.path)
        return JSONResponse(fail("server_error", "The request failed."), status_code=500)

    @application.get("/health")
    def health():
        from app.utils.time import isoformat, utcnow

        try:
            db.connect(reuse_if_open=True)
            db.execute_sql("SELECT 1")
        except Exception:
            return JSONResponse(
                {"ok": False, "error": {"code": "database", "message": "The database is unavailable."}},
                status_code=503,
            )
        return {
            "ok": True,
            "data": {"status": "up", "version": settings.app_version, "time": isoformat(utcnow())},
        }

    application.include_router(api_router)
    application.include_router(web_router)
    application.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
    return application
