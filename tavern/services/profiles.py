"""Connection profiles: one click switches the connection, its model, its settings and the preset.

A profile is a snapshot taken from what is active now. Each part can be left out (`parts`): a
profile with only "model" changes the model of whatever connection is active, and so on.
Profiles live in the settings table (`connection_profiles`, plus `active_profile`): they hold no
secrets, keys stay with their connections.
"""

from __future__ import annotations

from typing import Any

from tavern.context import AppContext
from tavern.errors import AppError, Conflict, NotFound
from tavern.util.ids import new_id

KEY = "connection_profiles"
PARTS = ("connection", "model", "params", "preset")


def _all(ctx: AppContext) -> list[dict[str, Any]]:
    value = ctx.settings.get(KEY) or []
    return value if isinstance(value, list) else []


def _save(ctx: AppContext, profiles: list[dict[str, Any]]) -> None:
    ctx.settings.set(KEY, profiles)


def _find(profiles: list[dict[str, Any]], profile_id: str) -> dict[str, Any]:
    for profile in profiles:
        if profile.get("id") == profile_id:
            return profile
    raise NotFound(f"Profile {profile_id} not found")


def _parts(raw: Any) -> dict[str, bool]:
    if raw is None:
        return {p: True for p in PARTS}
    if not isinstance(raw, dict):
        raise AppError("'parts' must be an object")
    parts = {p: bool(raw.get(p, False)) for p in PARTS}
    if not any(parts.values()):
        raise AppError("A profile must switch at least one thing")
    return parts


def _snapshot(ctx: AppContext) -> dict[str, Any]:
    """What is active now."""
    connection_id = ctx.settings.get("active_connection")
    if not connection_id:
        raise Conflict("No API connection selected. Add one in Settings.", code="no_connection")
    connection = ctx.connections.get(connection_id)
    return {"connection_id": connection_id, "connection_name": connection["name"],
            "model": connection["model"], "params": connection["params"],
            "preset_id": ctx.settings.get("active_preset") or ""}


def list_profiles(ctx: AppContext) -> dict[str, Any]:
    return {"profiles": _all(ctx), "active": ctx.settings.get("active_profile")}


def create(ctx: AppContext, name: str, parts: Any = None) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise AppError("A profile needs a name")
    profile = {"id": new_id(), "name": name[:100], "parts": _parts(parts), **_snapshot(ctx)}
    _save(ctx, [*_all(ctx), profile])
    ctx.settings.set("active_profile", profile["id"])
    return profile


def update(ctx: AppContext, profile_id: str, *, name: str | None = None, parts: Any = None,
           recapture: bool = False) -> dict[str, Any]:
    """Rename, change the parts, or take a fresh snapshot of what is active now."""
    profiles = _all(ctx)
    profile = _find(profiles, profile_id)
    if name is not None:
        if not name.strip():
            raise AppError("A profile needs a name")
        profile["name"] = name.strip()[:100]
    if parts is not None:
        profile["parts"] = _parts(parts)
    if recapture:
        profile.update(_snapshot(ctx))
    _save(ctx, profiles)
    return profile


def delete(ctx: AppContext, profile_id: str) -> None:
    profiles = _all(ctx)
    gone = _find(profiles, profile_id)
    _save(ctx, [p for p in profiles if p is not gone])
    if ctx.settings.get("active_profile") == profile_id:
        ctx.settings.delete("active_profile")


def apply(ctx: AppContext, profile_id: str) -> dict[str, Any]:
    """Switch to the profile's parts. Returns the connection that is active afterwards."""
    profile = _find(_all(ctx), profile_id)
    parts = profile["parts"]
    if parts["connection"]:
        try:
            ctx.connections.get(profile["connection_id"])
        except NotFound:
            raise Conflict(f"The profile's connection «{profile.get('connection_name', '')}» was deleted",
                           code="no_connection") from None
        ctx.settings.set("active_connection", profile["connection_id"])
    connection_id = ctx.settings.get("active_connection")
    if not connection_id:
        raise Conflict("No API connection selected. Add one in Settings.", code="no_connection")
    changes: dict[str, Any] = {}
    if parts["model"]:
        changes["model"] = profile["model"]
    if parts["params"]:
        changes["params"] = profile["params"]
    connection = ctx.connections.update(connection_id, **changes) if changes else ctx.connections.get(connection_id)
    if parts["preset"]:
        if profile["preset_id"]:
            ctx.settings.set("active_preset", profile["preset_id"])
        else:
            ctx.settings.delete("active_preset")
    ctx.settings.set("active_profile", profile_id)
    return connection
