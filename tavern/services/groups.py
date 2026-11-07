"""Groups for characters, lorebooks and chats: create, rename, delete, move items.

Deleting a group never deletes its items: they become «Без группы». Deleting an item (a character,
a book, a chat) clears its place by a database trigger (migration 0020), so nothing here has to
remember to. Fires `groups.changed` with kind.
"""

from __future__ import annotations

from typing import Any

from tavern.context import AppContext
from tavern.errors import AppError, NotFound
from tavern.storage.repos.groups import KINDS, GroupRepo

MAX_NAME = 120
MAX_ITEMS = 5000


class BadGroup(AppError):
    code = "bad_group"


def _kind(kind: Any) -> str:
    if kind not in KINDS:
        raise BadGroup(f"kind must be one of {', '.join(KINDS)}")
    return kind


def _name(value: Any) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not name:
        raise BadGroup("У группы должно быть название")
    return name[:MAX_NAME]


def overview(ctx: AppContext, kind: Any) -> dict[str, Any]:
    """{groups: [{id, name, position, count}], members: {item_id: group_id}}."""
    repo, kind = GroupRepo(ctx.db), _kind(kind)
    return {"kind": kind, "groups": repo.list(kind), "members": repo.members(kind)}


async def create(ctx: AppContext, kind: Any, name: Any) -> dict[str, Any]:
    group = GroupRepo(ctx.db).create(_kind(kind), _name(name))
    await ctx.events.emit("groups.changed", kind=group["kind"])
    return group


async def rename(ctx: AppContext, group_id: str, name: Any) -> dict[str, Any]:
    group = GroupRepo(ctx.db).rename(group_id, _name(name))
    await ctx.events.emit("groups.changed", kind=group["kind"])
    return group


async def delete(ctx: AppContext, group_id: str) -> None:
    repo = GroupRepo(ctx.db)
    group = repo.get(group_id)
    repo.delete(group_id)
    await ctx.events.emit("groups.changed", kind=group["kind"])


async def assign(ctx: AppContext, kind: Any, item_ids: Any, group_id: Any) -> dict[str, Any]:
    """Put items into a group, or free them (group_id null). Unknown items are refused."""
    repo, kind = GroupRepo(ctx.db), _kind(kind)
    if not isinstance(item_ids, list) or not item_ids or len(item_ids) > MAX_ITEMS \
            or not all(isinstance(i, str) for i in item_ids):
        raise BadGroup("item_ids must be a list of ids")
    if group_id is not None:
        if not isinstance(group_id, str):
            raise BadGroup("group_id must be an id or null")
        if repo.get(group_id)["kind"] != kind:
            raise NotFound(f"Group {group_id} is not a group of {kind}")
    missing = [i for i in item_ids if not repo.item_exists(kind, i)]
    if missing:
        raise NotFound(f"Not found: {', '.join(missing[:5])}")
    repo.assign(kind, item_ids, group_id)
    await ctx.events.emit("groups.changed", kind=kind)
    return overview(ctx, kind)
