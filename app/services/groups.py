"""گروه‌ها و عضویت چندگانه نودها."""

from __future__ import annotations

from peewee import IntegrityError

from app.core.errors import AppError
from app.db.database import db
from app.models import Node, NodeGroup, NodeGroupMembership
from app.schemas.groups import GroupCreate, GroupUpdate
from app.services.audit import write_audit
from app.services.present import group_payload
from app.utils.jsonbox import dump_json
from app.utils.time import utcnow


def _group(group_id: int) -> NodeGroup:
    group = NodeGroup.get_or_none(NodeGroup.id == group_id)
    if group is None:
        raise AppError("not_found", "Group not found.", 404)
    return group


def _nodes(node_ids: list[str]) -> list[Node]:
    unique = list(dict.fromkeys(node_ids))
    nodes = list(Node.select().where(Node.node_id.in_(unique)))
    found = {node.node_id for node in nodes}
    missing = [node_id for node_id in unique if node_id not in found]
    if missing:
        raise AppError("not_found", "Some nodes were not found.", 404, details={"node_ids": missing})
    return nodes


def create_group(data: GroupCreate, *, actor_id: str, ip_address: str | None) -> dict:
    now = utcnow()
    try:
        group = NodeGroup.create(
            name=data.name,
            description=data.description,
            color=data.color,
            tags=dump_json(data.tags),
            created_at=now,
            updated_at=now,
        )
    except IntegrityError as exc:
        raise AppError("duplicate_name", "A group with this name already exists.", 409) from exc
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="group_created",
        entity_type="group",
        entity_id=str(group.id),
        ip_address=ip_address,
        details={"name": group.name},
    )
    return group_payload(group)


def update_group(group_id: int, data: GroupUpdate, *, actor_id: str, ip_address: str | None) -> dict:
    group = _group(group_id)
    if data.name is not None:
        group.name = data.name
    if data.description is not None:
        group.description = data.description
    if data.color is not None:
        group.color = data.color
    if data.tags is not None:
        group.tags = dump_json(data.tags)
    group.updated_at = utcnow()
    try:
        group.save()
    except IntegrityError as exc:
        raise AppError("duplicate_name", "A group with this name already exists.", 409) from exc
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="group_updated",
        entity_type="group",
        entity_id=str(group.id),
        ip_address=ip_address,
        details={"name": group.name},
    )
    return group_payload(group)


def delete_group(group_id: int, *, actor_id: str, ip_address: str | None) -> None:
    group = _group(group_id)
    name = group.name
    identity = str(group.id)
    with db.atomic():
        group.delete_instance(recursive=True)
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="group_deleted",
        entity_type="group",
        entity_id=identity,
        ip_address=ip_address,
        details={"name": name},
    )


def add_members(group_id: int, node_ids: list[str], *, actor_id: str, ip_address: str | None) -> dict:
    group = _group(group_id)
    nodes = _nodes(node_ids)
    now = utcnow()
    added: list[str] = []
    with db.atomic():
        for node in nodes:
            try:
                NodeGroupMembership.create(node=node, group=group, created_at=now)
                added.append(node.node_id)
            except IntegrityError:
                continue
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="group_members_added",
        entity_type="group",
        entity_id=str(group.id),
        ip_address=ip_address,
        details={"node_ids": added},
    )
    payload = group_payload(group)
    payload["added"] = added
    return payload


def remove_member(group_id: int, node_id: str, *, actor_id: str, ip_address: str | None) -> dict:
    group = _group(group_id)
    node = Node.get_or_none(Node.node_id == node_id)
    if node is None:
        raise AppError("not_found", "Node not found.", 404)
    deleted = (
        NodeGroupMembership.delete()
        .where((NodeGroupMembership.group == group) & (NodeGroupMembership.node == node))
        .execute()
    )
    if not deleted:
        raise AppError("not_member", "This node is not in the group.", 404)
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="group_member_removed",
        entity_type="group",
        entity_id=str(group.id),
        ip_address=ip_address,
        details={"node_id": node_id},
    )
    return group_payload(group)


def transfer_members(
    group_id: int,
    node_ids: list[str],
    target_group_id: int,
    *,
    actor_id: str,
    ip_address: str | None,
) -> dict:
    if group_id == target_group_id:
        raise AppError("same_group", "The source and destination groups are the same.", 422)
    source = _group(group_id)
    target = _group(target_group_id)
    nodes = _nodes(node_ids)
    now = utcnow()
    moved: list[str] = []
    with db.atomic():
        for node in nodes:
            removed = (
                NodeGroupMembership.delete()
                .where((NodeGroupMembership.group == source) & (NodeGroupMembership.node == node))
                .execute()
            )
            if not removed:
                continue
            try:
                NodeGroupMembership.create(node=node, group=target, created_at=now)
            except IntegrityError:
                pass
            moved.append(node.node_id)
    if not moved:
        raise AppError("not_member", "None of the nodes belong to the source group.", 404)
    write_audit(
        actor_type="user",
        actor_id=actor_id,
        action="group_members_transferred",
        entity_type="group",
        entity_id=str(source.id),
        ip_address=ip_address,
        details={"node_ids": moved, "target_group_id": target.id},
    )
    return {"moved": moved, "source": group_payload(source), "target": group_payload(target)}


def get_group(group_id: int) -> NodeGroup:
    return _group(group_id)
