"""مدیریت هویت نود و کلید اختصاصی."""

from __future__ import annotations

from peewee import IntegrityError

from app.core.errors import AppError
from app.core.security import hash_password, key_prefix, new_node_id, new_node_key
from app.db.database import db
from app.models import Alert, Node, NodeCredential, NodeGroup, NodeGroupMembership
from app.schemas.nodes import NodeCreate, NodeUpdate
from app.services.alerts import resolve_alert_row
from app.services.audit import write_audit
from app.services.present import active_credential, node_payload
from app.utils.jsonbox import dump_json
from app.utils.time import utcnow


def _get_node(node_id: str) -> Node:
    node = Node.get_or_none(Node.node_id == node_id)
    if node is None:
        raise AppError("not_found", "Node not found.", 404)
    return node


def _require_active(node: Node) -> None:
    if node.deleted_at is not None:
        raise AppError("node_removed", "This node was removed.", 409)


def create_node(data: NodeCreate, *, actor_id: str, ip_address: str | None) -> dict:
    now = utcnow()
    plaintext = new_node_key()
    with db.atomic():
        node = Node.create(
            node_id=new_node_id(),
            name=data.name,
            description=data.description,
            tags=dump_json(data.tags),
            is_enabled=True,
            created_at=now,
            updated_at=now,
        )
        NodeCredential.create(
            node=node,
            key_prefix=key_prefix(plaintext),
            key_hash=hash_password(plaintext),
            is_active=True,
            created_at=now,
        )
        if data.group_ids:
            groups = list(NodeGroup.select().where(NodeGroup.id.in_(data.group_ids)))
            if len(groups) != len(set(data.group_ids)):
                raise AppError("not_found", "One of the selected groups was not found.", 404)
            for group in groups:
                NodeGroupMembership.create(node=node, group=group, created_at=now)
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="node_created",
        entity_type="node",
        entity_id=node.node_id,
        ip_address=ip_address,
        details={"name": node.name},
    )
    payload = node_payload(node)
    payload["node_key"] = plaintext
    payload["key_notice"] = "This key is shown only once."
    return payload


def update_node(node_id: str, data: NodeUpdate, *, actor_id: str, ip_address: str | None) -> dict:
    node = _get_node(node_id)
    _require_active(node)
    if data.name is not None:
        node.name = data.name
    if data.description is not None:
        node.description = data.description
    if data.tags is not None:
        node.tags = dump_json(data.tags)
    node.updated_at = utcnow()
    node.save()
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="node_updated",
        entity_type="node",
        entity_id=node.node_id,
        ip_address=ip_address,
        details={"name": node.name},
    )
    return node_payload(node)


def set_enabled(node_id: str, enabled: bool, *, actor_id: str, ip_address: str | None) -> dict:
    node = _get_node(node_id)
    _require_active(node)
    node.is_enabled = enabled
    node.updated_at = utcnow()
    node.save()
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="node_enabled" if enabled else "node_disabled",
        entity_type="node",
        entity_id=node.node_id,
        ip_address=ip_address,
    )
    return node_payload(node)


def delete_node(node_id: str, *, actor_id: str, ip_address: str | None) -> None:
    node = _get_node(node_id)
    _require_active(node)
    identity = node.node_id
    name = node.name
    now = utcnow()
    with db.atomic():
        NodeCredential.update(is_active=False, revoked_at=now).where(
            (NodeCredential.node == node) & (NodeCredential.is_active == True)  # noqa: E712
        ).execute()
        node.deleted_at = now
        node.is_enabled = False
        node.updated_at = now
        node.save()
        for alert in Alert.select().where((Alert.node == node) & (Alert.status == "open")):
            resolve_alert_row(alert, "The node was removed.")
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="node_deleted",
        entity_type="node",
        entity_id=identity,
        ip_address=ip_address,
        details={"name": name},
    )


def rotate_key(node_id: str, *, actor_id: str, ip_address: str | None) -> dict:
    node = _get_node(node_id)
    _require_active(node)
    plaintext = new_node_key()
    now = utcnow()
    with db.atomic():
        NodeCredential.update(is_active=False, revoked_at=now).where(
            (NodeCredential.node == node) & (NodeCredential.is_active == True)  # noqa: E712
        ).execute()
        NodeCredential.create(
            node=node,
            key_prefix=key_prefix(plaintext),
            key_hash=hash_password(plaintext),
            is_active=True,
            created_at=now,
        )
        node.updated_at = now
        node.save()
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="node_key_rotated",
        entity_type="node",
        entity_id=node.node_id,
        ip_address=ip_address,
    )
    payload = node_payload(node)
    payload["node_key"] = plaintext
    payload["key_notice"] = "The previous key was revoked. The new key is shown only once."
    return payload


def revoke_key(node_id: str, *, actor_id: str, ip_address: str | None) -> dict:
    node = _get_node(node_id)
    _require_active(node)
    credential = active_credential(node)
    if credential is None:
        raise AppError("no_active_key", "This node has no active key.", 409)
    now = utcnow()
    credential.is_active = False
    credential.revoked_at = now
    credential.save()
    node.updated_at = now
    node.save()
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="node_key_revoked",
        entity_type="node",
        entity_id=node.node_id,
        ip_address=ip_address,
    )
    return node_payload(node)


def get_node(node_id: str) -> Node:
    node = _get_node(node_id)
    if node.deleted_at is not None:
        raise AppError("not_found", "Node not found.", 404)
    return node
