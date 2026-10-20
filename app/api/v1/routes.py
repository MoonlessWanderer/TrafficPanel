"""مسیرهای /api/v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from app.core.settings import settings
from app.core.auth import client_ip, require_node, require_user
from app.core.errors import AppError
from app.core.rate_limit import rate_limiter
from app.models import Node, NodeGroup
from app.schemas.auth import LoginIn
from app.schemas.groups import GroupCreate, GroupUpdate, MembershipIn, TransferIn
from app.schemas.ingest import HeartbeatIn, MetricsIn
from app.schemas.nodes import NodeCreate, NodeUpdate
from app.schemas.settings import SettingsUpdate
from app.core.restart import schedule_restart
from app.services import auth_service, groups, nodes
from app.services.audit import write_audit
from app.services.alerts import resolve_alert_row
from app.services.filters import parse_filters
from app.services.ingest import ingest_heartbeat, ingest_metrics, note_ingest_error
from app.services.present import group_payload, node_payload
from app.services.runtime_settings import snapshot, update_values
from app.services.stats import (
    compare_groups,
    compare_nodes,
    dashboard,
    export_traffic_csv,
    group_detail_stats,
    list_alerts,
    list_nodes,
    node_detail_stats,
    node_samples,
    resource_chart,
    traffic_chart,
)
from app.utils.responses import ok
from app.utils.time import isoformat, utcnow

router = APIRouter(prefix="/api/v1")


def _cookies(response: JSONResponse, token: str, csrf: str) -> None:
    common = {
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "max_age": settings.session_ttl_seconds,
        "path": "/",
    }
    response.set_cookie(settings.session_cookie_name, token, httponly=True, **common)
    response.set_cookie(settings.csrf_cookie_name, csrf, httponly=False, **common)


@router.get("/health")
def health() -> dict:
    from app.db.database import db

    db.execute_sql("SELECT 1")
    return ok({"status": "up", "version": settings.app_version, "time": isoformat(utcnow())})


@router.post("/auth/login")
def login(payload: LoginIn, request: Request):
    rate_limiter.check(
        f"login:{client_ip(request) or 'unknown'}",
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )
    user, token, csrf = auth_service.login(
        payload.username,
        payload.password,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse(
        ok({"user": auth_service.user_payload(user), "csrf_token": csrf, "expires_in": settings.session_ttl_seconds})
    )
    _cookies(response, token, csrf)
    return response


@router.post("/auth/logout")
def logout(request: Request, user=Depends(require_user)):
    auth_service.logout(request.state.session, ip_address=client_ip(request))
    response = JSONResponse(ok({"username": user.username}))
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response


@router.get("/auth/me")
def me(user=Depends(require_user)):
    return ok(auth_service.user_payload(user))


@router.get("/nodes")
def get_nodes(request: Request, _user=Depends(require_user)):
    result = list_nodes(parse_filters(request))
    return ok(result["items"], result["meta"])


@router.post("/nodes", status_code=201)
def post_node(payload: NodeCreate, request: Request, user=Depends(require_user)):
    return ok(nodes.create_node(payload, actor_id=str(user.id), ip_address=client_ip(request)))


@router.get("/nodes/{node_id}")
def get_node(node_id: str, _user=Depends(require_user)):
    return ok(node_payload(nodes.get_node(node_id)))


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, payload: NodeUpdate, request: Request, user=Depends(require_user)):
    return ok(nodes.update_node(node_id, payload, actor_id=str(user.id), ip_address=client_ip(request)))


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str, request: Request, user=Depends(require_user)):
    nodes.delete_node(node_id, actor_id=str(user.id), ip_address=client_ip(request))
    return ok({"deleted": True})


@router.post("/nodes/{node_id}/enable")
def enable_node(node_id: str, request: Request, user=Depends(require_user)):
    return ok(nodes.set_enabled(node_id, True, actor_id=str(user.id), ip_address=client_ip(request)))


@router.post("/nodes/{node_id}/disable")
def disable_node(node_id: str, request: Request, user=Depends(require_user)):
    return ok(nodes.set_enabled(node_id, False, actor_id=str(user.id), ip_address=client_ip(request)))


@router.post("/nodes/{node_id}/rotate-key")
def rotate_key(node_id: str, request: Request, user=Depends(require_user)):
    return ok(nodes.rotate_key(node_id, actor_id=str(user.id), ip_address=client_ip(request)))


@router.post("/nodes/{node_id}/revoke-key")
def revoke_key(node_id: str, request: Request, user=Depends(require_user)):
    return ok(nodes.revoke_key(node_id, actor_id=str(user.id), ip_address=client_ip(request)))


@router.get("/nodes/{node_id}/stats")
def get_node_stats(node_id: str, request: Request, _user=Depends(require_user)):
    return ok(node_detail_stats(nodes.get_node(node_id), parse_filters(request)))


@router.get("/nodes/{node_id}/samples")
def get_node_samples(node_id: str, request: Request, _user=Depends(require_user)):
    result = node_samples(nodes.get_node(node_id), parse_filters(request))
    return ok(result["items"], result["meta"])


@router.get("/groups")
def get_groups(_user=Depends(require_user)):
    return ok([group_payload(group) for group in NodeGroup.select().order_by(NodeGroup.name)])


@router.post("/groups", status_code=201)
def post_group(payload: GroupCreate, request: Request, user=Depends(require_user)):
    return ok(groups.create_group(payload, actor_id=str(user.id), ip_address=client_ip(request)))


@router.get("/groups/{group_id}")
def get_group(group_id: int, _user=Depends(require_user)):
    return ok(group_payload(groups.get_group(group_id)))


@router.patch("/groups/{group_id}")
def patch_group(group_id: int, payload: GroupUpdate, request: Request, user=Depends(require_user)):
    return ok(groups.update_group(group_id, payload, actor_id=str(user.id), ip_address=client_ip(request)))


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, request: Request, user=Depends(require_user)):
    groups.delete_group(group_id, actor_id=str(user.id), ip_address=client_ip(request))
    return ok({"deleted": True})


@router.post("/groups/{group_id}/members")
def post_members(group_id: int, payload: MembershipIn, request: Request, user=Depends(require_user)):
    return ok(groups.add_members(group_id, payload.node_ids, actor_id=str(user.id), ip_address=client_ip(request)))


@router.delete("/groups/{group_id}/members/{node_id}")
def delete_member(group_id: int, node_id: str, request: Request, user=Depends(require_user)):
    return ok(groups.remove_member(group_id, node_id, actor_id=str(user.id), ip_address=client_ip(request)))


@router.post("/groups/{group_id}/transfer")
def transfer(group_id: int, payload: TransferIn, request: Request, user=Depends(require_user)):
    return ok(
        groups.transfer_members(
            group_id,
            payload.node_ids,
            payload.target_group_id,
            actor_id=str(user.id),
            ip_address=client_ip(request),
        )
    )


@router.get("/groups/{group_id}/stats")
def get_group_stats(group_id: int, request: Request, _user=Depends(require_user)):
    return ok(group_detail_stats(groups.get_group(group_id), parse_filters(request)))


@router.post("/ingest/heartbeat")
def heartbeat(payload: HeartbeatIn, request: Request, node: Node = Depends(require_node)):
    try:
        return ok(ingest_heartbeat(node, payload, request.state.node_nonce))
    except AppError as exc:
        if exc.status_code >= 400 and exc.code not in {"replay"}:
            note_ingest_error(node, "The node report could not be stored.")
        raise


@router.post("/ingest/metrics")
def metrics(payload: MetricsIn, request: Request, node: Node = Depends(require_node)):
    try:
        return ok(ingest_metrics(node, payload, request.state.node_nonce))
    except AppError as exc:
        if exc.code not in {"replay"}:
            note_ingest_error(node, "The node report could not be stored.")
        raise


@router.get("/dashboard/summary")
def summary(request: Request, _user=Depends(require_user)):
    return ok(dashboard(parse_filters(request)))


@router.get("/charts/traffic")
def charts_traffic(request: Request, mode: str = Query(default="total"), _user=Depends(require_user)):
    if mode not in {"total", "nodes", "groups"}:
        raise AppError("invalid_filter", "Unknown chart mode.", 422)
    return ok(traffic_chart(parse_filters(request), mode))


@router.get("/charts/resources")
def charts_resources(request: Request, _user=Depends(require_user)):
    return ok(resource_chart(parse_filters(request)))


@router.get("/compare/nodes")
def get_compare_nodes(request: Request, _user=Depends(require_user)):
    return ok(compare_nodes(parse_filters(request)))


@router.get("/compare/groups")
def get_compare_groups(request: Request, _user=Depends(require_user)):
    return ok(compare_groups(parse_filters(request)))


@router.get("/alerts")
def get_alerts(request: Request, alert_status: str | None = None, _user=Depends(require_user)):
    if alert_status is not None and alert_status not in {"open", "resolved"}:
        raise AppError("invalid_filter", "Unknown alert status.", 422)
    result = list_alerts(parse_filters(request), alert_status)
    return ok(result["items"], result["meta"])


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, request: Request, user=Depends(require_user)):
    from app.models import Alert

    alert = Alert.get_or_none(Alert.id == alert_id)
    if alert is None:
        raise AppError("not_found", "Alert not found.", 404)
    resolve_alert_row(alert, "Closed by an admin.", actor_type="user", actor_id=str(user.id))
    return ok({"id": alert.id, "status": alert.status})


@router.get("/settings")
def get_settings(_user=Depends(require_user)):
    return ok(snapshot())


@router.put("/settings")
def put_settings(payload: SettingsUpdate, request: Request, user=Depends(require_user)):
    data = update_values(payload.values, actor_id=str(user.id), ip_address=client_ip(request))
    return ok(data)


@router.post("/system/restart")
def restart_panel(request: Request, user=Depends(require_user)):
    write_audit(
        actor_type="user",
        actor_id=str(user.id),
        action="panel_restarted",
        entity_type="settings",
        ip_address=client_ip(request),
    )
    schedule_restart()
    return ok({"message": "The panel is restarting."})


@router.get("/export/traffic.csv")
def export_csv(request: Request, _user=Depends(require_user)):
    content = export_traffic_csv(parse_filters(request))
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=traffic.csv"},
    )
