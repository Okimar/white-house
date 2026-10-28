"""History of a character and "Отменить": every change of the card or its bubbles is a step.

A step keeps what the changed things were *before* it, as ops:
    {"t": "card", "data": {...}}                 the whole card before the change
    {"t": "block", "id": ..., "row": {...}|None}  a bubble's raw row before (None: it did not exist)
    {"t": "link", "a": ..., "b": ..., "had": bool} whether the link existed before
Undo puts those back, newest step first, as far back as the user likes. A new change after undo
drops the undone steps. Steps are grouped into versions: one closes when the character is closed
in the editor or after VERSION_GAP of quiet, and "restore" undoes every step made after it.
Positions on the map are not history (they change all the time and matter little).

The setting `character_history` (default on) turns recording off.
Events: character.history_changed  character_id
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tavern.context import AppContext
from tavern.errors import AppError, NotFound
from tavern.util.ids import now_iso

VERSION_GAP = timedelta(minutes=10)


def enabled(ctx: AppContext) -> bool:
    return ctx.settings.get("character_history") is not False


def block_before(ctx: AppContext, block_id: str) -> dict[str, Any]:
    return {"t": "block", "id": block_id, "row": ctx.history.block_row(block_id)}


def link_before(ctx: AppContext, a: str, b: str) -> dict[str, Any]:
    a, b = sorted((a, b))
    had = ctx.db.fetchone("SELECT 1 FROM block_links WHERE a = ? AND b = ?", (a, b)) is not None
    return {"t": "link", "a": a, "b": b, "had": had}


def _parse(at: str) -> datetime:
    return datetime.fromisoformat(at.replace("Z", "+00:00"))


def record(ctx: AppContext, character_id: str, what: str, ops: list[dict[str, Any]]) -> None:
    """Remember one change (call it with the "before" ops gathered before changing anything)."""
    if not enabled(ctx) or not ops:
        return
    now = now_iso()
    ctx.history.drop_undone(character_id)
    version = ctx.history.open_version(character_id)
    last = ctx.history.last_step(character_id)
    if version is not None and last is not None and last["version_id"] == version["id"] \
            and _parse(now) - _parse(last["at"]) > VERSION_GAP:
        ctx.history.close_version(version["id"], last["at"])
        version = None
    version_id = version["id"] if version else ctx.history.new_version(character_id, now)
    ctx.history.add_step(character_id, version_id, now, what[:200], ops)


def close(ctx: AppContext, character_id: str) -> None:
    """The character was closed: what was changed so far is one version."""
    version = ctx.history.open_version(character_id)
    if version is not None:
        ctx.history.close_version(version["id"], now_iso())


def _apply(ctx: AppContext, character_id: str, ops: list[dict[str, Any]]) -> None:
    with ctx.db.transaction():
        for op in ops:
            if op["t"] == "card":
                ctx.characters.update(character_id, op["data"])
            elif op["t"] == "block":
                ctx.history.put_block_row(op["id"], op["row"])
            elif op["t"] == "link":
                if op["had"]:
                    if ctx.history.block_row(op["a"]) and ctx.history.block_row(op["b"]):
                        ctx.blocks.link(character_id, op["a"], op["b"])
                else:
                    ctx.blocks.unlink(op["a"], op["b"])


async def _changed(ctx: AppContext, character_id: str) -> None:
    await ctx.events.emit("character.blocks_changed", character_id=character_id)
    await ctx.events.emit("character.history_changed", character_id=character_id)


async def undo(ctx: AppContext, character_id: str) -> dict[str, Any] | None:
    """Take back the newest step. Returns {id, what} of it, or None when there is nothing left."""
    ctx.characters.get(character_id)
    step = ctx.history.last_step(character_id)
    if step is None:
        return None
    _apply(ctx, character_id, step["before"])
    ctx.history.mark_undone(step["id"])
    await _changed(ctx, character_id)
    return {"id": step["id"], "what": step["what"]}


async def restore(ctx: AppContext, character_id: str, version_id: int) -> int:
    """Back to how the character was at the end of a version: undo every later step."""
    ctx.characters.get(character_id)
    versions = {v["id"]: v for v in ctx.history.versions(character_id)}
    if version_id not in versions:
        raise NotFound(f"Version {version_id} not found")
    last = ctx.history.version_last_step(version_id)
    if last is None:
        raise AppError("That version has no steps", code="bad_version")
    steps = ctx.history.steps_after(character_id, last)
    for step in steps:  # newest first
        _apply(ctx, character_id, step["before"])
        ctx.history.mark_undone(step["id"])
    if steps:
        await _changed(ctx, character_id)
    return len(steps)


def versions(ctx: AppContext, character_id: str) -> dict[str, Any]:
    ctx.characters.get(character_id)
    return {"versions": ctx.history.versions(character_id), "bytes": ctx.history.size(character_id),
            "enabled": enabled(ctx)}
