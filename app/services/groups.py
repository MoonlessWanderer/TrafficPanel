"""گروه‌ها. هر نود هنگام ساخت به یک گروه وصل می‌شود."""

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
    live_members = (
        Node.select()
        .join(NodeGroupMembership)
        .where((NodeGroupMembership.group == group) & (Node.deleted_at.is_null(True)))
        .count()
    )
    if live_members:
        raise AppError("group_has_nodes", "Delete the nodes in this group first.", 409)
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


def get_group(group_id: int) -> NodeGroup:
    return _group(group_id)
