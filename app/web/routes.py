"""صفحه‌های HTML. داده از API خوانده می‌شود."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.settings import settings
from app.core.auth import resolve_session

router = APIRouter()
templates = Jinja2Templates(directory=str(settings.templates_dir))


def _render(request: Request, name: str, page: str, **extra):
    resolved = resolve_session(request)
    if resolved is None:
        return RedirectResponse("/login", status_code=302)
    user, _session = resolved
    context = {
        "page": page,
        "username": user.username,
        "timezone": settings.timezone,
        "app_name": settings.app_name,
        **extra,
    }
    return templates.TemplateResponse(request=request, name=name, context=context)


@router.get("/login")
def login_page(request: Request):
    if resolve_session(request) is not None:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"page": "login", "timezone": settings.timezone, "app_name": settings.app_name},
    )


@router.get("/")
def dashboard_page(request: Request):
    return _render(request, "dashboard.html", "dashboard")


@router.get("/nodes")
def nodes_page(request: Request):
    return _render(request, "nodes.html", "nodes")


@router.get("/nodes/{node_id}")
def node_page(node_id: str, request: Request):
    return _render(request, "node_detail.html", "node", node_id=node_id)


@router.get("/groups")
def groups_page(request: Request):
    return _render(request, "groups.html", "groups")


@router.get("/groups/{group_id}")
def group_page(group_id: int, request: Request):
    return _render(request, "group_detail.html", "group", group_id=group_id)


@router.get("/compare")
def compare_page(request: Request):
    return _render(request, "compare.html", "compare")


@router.get("/alerts")
def alerts_page(request: Request):
    return _render(request, "alerts.html", "alerts")


@router.get("/settings")
def settings_page(request: Request):
    return _render(request, "settings.html", "settings")
