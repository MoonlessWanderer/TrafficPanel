"""تبدیل مدل‌ها به پاسخ API."""

from __future__ import annotations

from datetime import timedelta

from app.core.settings import settings
from app.models import Node, NodeCredential, NodeGroup
from app.utils.jsonbox import load_json
from app.utils.time import isoformat, utcnow
from app.utils.units import format_bps, format_bytes


def node_status(node: Node, now=None) -> str:
    if node.deleted_at is not None:
        return "removed"
    if not node.is_enabled:
        return "disabled"
    moment = now or utcnow()
    if node.last_seen_at is None:
        return "offline"
    age = (moment - node.last_seen_at).total_seconds()
    if age <= settings.offline_after_seconds:
        return "online"
    return "offline"


def node_is_stale(node: Node, now=None) -> bool:
    if node_status(node, now) != "online" or node.last_seen_at is None:
        return False
    moment = now or utcnow()
    age = (moment - node.last_seen_at).total_seconds()
    return age > settings.stale_after_seconds


def active_credential(node: Node) -> NodeCredential | None:
    return (
        NodeCredential.select()
        .where((NodeCredential.node == node) & (NodeCredential.is_active == True))  # noqa: E712
        .first()
    )


def percent(used: int | None, total: int | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return round(used * 100.0 / total, 2)


def node_payload(node: Node, *, include_groups: bool = True) -> dict:
    credential = active_credential(node)
    payload = {
        "node_id": node.node_id,
        "name": node.name,
        "description": node.description,
        "tags": load_json(node.tags, []),
        "is_enabled": node.is_enabled,
        "status": node_status(node),
        "stale": node_is_stale(node),
        "agent_version": node.agent_version,
        "os_name": node.os_name,
        "kernel_version": node.kernel_version,
        "last_seen_at": isoformat(node.last_seen_at),
        "key_active": credential is not None,
        "key_prefix": credential.key_prefix if credential else None,
        "cpu_percent": node.last_cpu_percent,
        "ram": {
            "total_bytes": node.last_ram_total_bytes,
            "used_bytes": node.last_ram_used_bytes,
            "free_bytes": node.last_ram_free_bytes,
            "percent": percent(node.last_ram_used_bytes, node.last_ram_total_bytes),
            "total_text": format_bytes(node.last_ram_total_bytes, binary=True),
            "used_text": format_bytes(node.last_ram_used_bytes, binary=True),
        },
        "disk": {
            "total_bytes": node.last_disk_total_bytes,
            "used_bytes": node.last_disk_used_bytes,
            "free_bytes": node.last_disk_free_bytes,
            "percent": percent(node.last_disk_used_bytes, node.last_disk_total_bytes),
            "total_text": format_bytes(node.last_disk_total_bytes, binary=True),
            "used_text": format_bytes(node.last_disk_used_bytes, binary=True),
            "free_text": format_bytes(node.last_disk_free_bytes, binary=True),
        },
        "load": {"m1": node.last_load_1, "m5": node.last_load_5, "m15": node.last_load_15},
        "uptime_seconds": node.last_uptime_seconds,
        "speed": {
            "rx_bps": node.last_rx_bps,
            "tx_bps": node.last_tx_bps,
            "rx_text": format_bps(node.last_rx_bps),
            "tx_text": format_bps(node.last_tx_bps),
        },
        "created_at": isoformat(node.created_at),
        "updated_at": isoformat(node.updated_at),
    }
    if include_groups:
        from app.models import NodeGroupMembership

        memberships = (
            NodeGroup.select()
            .join(NodeGroupMembership)
            .where(NodeGroupMembership.node == node)
            .order_by(NodeGroup.name)
        )
        payload["groups"] = [
            {"id": group.id, "name": group.name, "color": group.color} for group in memberships
        ]
    return payload


def group_payload(group: NodeGroup, *, with_counts: bool = True) -> dict:
    payload = {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "color": group.color,
        "tags": load_json(group.tags, []),
        "created_at": isoformat(group.created_at),
        "updated_at": isoformat(group.updated_at),
    }
    if with_counts:
        from app.models import NodeGroupMembership

        members = list(
            Node.select()
            .join(NodeGroupMembership)
            .where((NodeGroupMembership.group == group) & (Node.deleted_at.is_null(True)))
        )
        payload["members"] = len(members)
        payload["online"] = sum(1 for node in members if node_status(node) == "online")
        payload["offline"] = sum(1 for node in members if node_status(node) == "offline")
        payload["disabled"] = sum(1 for node in members if node_status(node) == "disabled")
    return payload


def status_cutoff():
    return utcnow() - timedelta(seconds=settings.offline_after_seconds)
