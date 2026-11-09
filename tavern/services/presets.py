"""Prompt presets: the built-in default, the user's own, which one a chat uses, ST import/export.

A chat uses `chat.meta["preset_id"]`, else the setting `active_preset`, else the built-in
default. A preset that was deleted silently falls back, so a chat never stops working.
Changes fire `presets.changed` (action, preset_id) for plugins and agents.

Model settings (temperature, context size ...) live only in the connection. A preset file may still
carry `params` / `max_context_tokens` (older presets, imports): they are read but never used, and
saving a preset drops them. `move_model_settings` moves the old ones once; an import hands them back
as `model_params` so the page can ask whether to put them into the active connection.
"""

from __future__ import annotations

import logging
from typing import Any

from tavern.context import AppContext
from tavern.core.presets import default_preset, normalize_preset
from tavern.errors import AppError, NotFound
from tavern.formats import st_preset
from tavern.providers import params as conn_params

log = logging.getLogger(__name__)

BUILTIN_ID = "default"
BUILTIN_NAME = "Стандартный"


def builtin() -> dict[str, Any]:
    return {"id": BUILTIN_ID, "name": BUILTIN_NAME, "builtin": True, "data": default_preset()}


def list_presets(ctx: AppContext) -> list[dict[str, Any]]:
    stored = [{**p, "builtin": False} for p in ctx.presets.list()]
    return [{"id": BUILTIN_ID, "name": BUILTIN_NAME, "builtin": True}, *stored]


def get(ctx: AppContext, preset_id: str) -> dict[str, Any]:
    if preset_id == BUILTIN_ID:
        return builtin()
    return {**ctx.presets.get(preset_id), "builtin": False}


def resolve(ctx: AppContext, chat: dict[str, Any]) -> dict[str, Any]:
    """The preset this chat generates with."""
    for preset_id in (chat["meta"].get("preset_id"), ctx.settings.get("active_preset")):
        if preset_id:
            try:
                preset = get(ctx, preset_id)
            except NotFound:
                continue
            preset["data"] = normalize_preset(preset["data"])  # older stored shapes stay readable
            return preset
    return builtin()


def _name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise AppError("A preset needs a name", code="bad_preset")
    return name.strip()[:100]


def model_settings(data: dict[str, Any]) -> dict[str, Any]:
    """The model settings an (older) preset carries: its params and its context size when set."""
    out = dict(data.get("params") or {})
    if data.get("max_context_tokens"):
        out["max_context_tokens"] = data["max_context_tokens"]
    return out


def _without_model_settings(data: dict[str, Any]) -> dict[str, Any]:
    data = normalize_preset(data)
    return {**data, "params": {}, "max_context_tokens": 0}


async def create(ctx: AppContext, name: Any, data: Any = None) -> dict[str, Any]:
    preset = ctx.presets.create(
        _name(name), _without_model_settings(data if data is not None else default_preset()))
    await ctx.events.emit("presets.changed", preset_id=preset["id"])
    return preset


async def update(ctx: AppContext, preset_id: str, name: Any, data: Any) -> dict[str, Any]:
    if preset_id == BUILTIN_ID:
        raise AppError("The built-in preset cannot be changed; make a copy", code="builtin_preset")
    preset = ctx.presets.update(preset_id, _name(name), _without_model_settings(data))
    await ctx.events.emit("presets.changed", preset_id=preset_id)
    return preset


async def delete(ctx: AppContext, preset_id: str) -> None:
    if preset_id == BUILTIN_ID:
        raise AppError("The built-in preset cannot be deleted", code="builtin_preset")
    ctx.presets.delete(preset_id)
    if ctx.settings.get("active_preset") == preset_id:
        ctx.settings.delete("active_preset")
    await ctx.events.emit("presets.changed", preset_id=preset_id)


NATIVE_KEY = "tavern_preset"  # marks our own export format (version number as value)
NATIVE_VERSION = 1


def _stem(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for suffix in (".tavern-preset.json", ".json"):
        if stem.lower().endswith(suffix):
            return stem[: -len(suffix)]
    return stem


async def _with_rules(ctx: AppContext, preset: dict[str, Any], scripts: Any) -> dict[str, Any]:
    """Regex scripts that came with an imported preset become its rules, waiting for «Разрешить» (stage 11)."""
    from tavern.services import regex as regex_service  # noqa: PLC0415

    if scripts:
        regex_service.import_rules(ctx, scripts, "preset", preset["id"], approved=False)
    return preset


def _split_rules(data: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """The preset's data without `extensions.regex_scripts`, and those scripts."""
    st = data.get("st") or {}
    extensions = st.get("extensions")
    scripts = extensions.get("regex_scripts") if isinstance(extensions, dict) else None
    if not scripts:
        return data, None
    rest = {k: v for k, v in extensions.items() if k != "regex_scripts"}
    st = {k: v for k, v in st.items() if k != "extensions"}
    if rest:
        st["extensions"] = rest
    return {**data, "st": st}, scripts


def _with_model_params(ctx: AppContext, preset: dict[str, Any], found: dict[str, Any]) -> dict[str, Any]:
    """The import answer: the preset plus the model settings it came with (not applied) and the
    active connection they would go to, for the page's question."""
    if not found:
        return preset
    out = {**preset, "model_params": found}
    conn_id = ctx.settings.get("active_connection")
    if conn_id:
        try:
            out["model_params_target"] = {"id": conn_id, "name": ctx.connections.get(conn_id)["name"]}
        except NotFound:
            pass
    return out


async def import_st(ctx: AppContext, obj: Any, filename: str = "") -> dict[str, Any]:
    name, data = st_preset.from_st(obj, _stem(filename))
    data, scripts = _split_rules(data)
    found = model_settings(data)
    preset = await _with_rules(ctx, await create(ctx, name, data), scripts)
    return _with_model_params(ctx, preset, found)


async def import_any(ctx: AppContext, obj: Any, filename: str = "") -> dict[str, Any]:
    """Our own preset file (everything kept) or a SillyTavern one (converted)."""
    if isinstance(obj, dict) and NATIVE_KEY in obj:
        if not isinstance(obj[NATIVE_KEY], int) or obj[NATIVE_KEY] > NATIVE_VERSION:
            raise AppError("This preset was made by a newer Tavern; update the app", code="bad_preset")
        data = normalize_preset(obj.get("data") if obj.get("data") is not None else default_preset())
        preset = await create(ctx, obj.get("name") or _stem(filename) or "Пресет", data)
        preset = await _with_rules(ctx, preset, obj.get("regex_scripts"))
        return _with_model_params(ctx, preset, model_settings(data))
    return await import_st(ctx, obj, filename)


def export_native(ctx: AppContext, preset_id: str) -> tuple[str, dict[str, Any]]:
    """Our full format: every block option and setting survives."""
    preset = get(ctx, preset_id)
    from tavern.services import regex as regex_service  # noqa: PLC0415

    out = {NATIVE_KEY: NATIVE_VERSION, "name": preset["name"],
           "data": {**normalize_preset(preset["data"]), "params": {}, "max_context_tokens": 0}}
    scripts = regex_service.preset_scripts(ctx, preset_id)
    if scripts:
        out["regex_scripts"] = scripts
    return preset["name"], out


def export_st(ctx: AppContext, preset_id: str) -> tuple[str, dict[str, Any]]:
    """SillyTavern format: what ST has no place for (e.g. history limits) is left out."""
    preset = get(ctx, preset_id)
    from tavern.services import regex as regex_service  # noqa: PLC0415

    data = normalize_preset(preset["data"])
    # ST keeps model settings in the preset: they come from the active connection, ours has none.
    params: dict[str, Any] = {}
    conn_id = ctx.settings.get("active_connection")
    if conn_id:
        try:
            params = dict(ctx.connections.get(conn_id)["params"])
        except NotFound:
            pass
    context = params.pop("max_context_tokens", 0)
    data = {**data, "params": params, "max_context_tokens": int(context or 0)}
    out = st_preset.to_st(data)
    scripts = regex_service.preset_scripts(ctx, preset_id)
    if scripts:
        out["extensions"] = {**(out.get("extensions") or {}), "regex_scripts": scripts}
    return preset["name"], out


MOVED_SETTING = "service_texts_moved"  # names of presets whose own wording was moved into blocks (page tells once)


def migrate_behaviour_blocks(ctx: AppContext) -> list[str]:
    """Stored presets from before the behaviour blocks kept «не писать за меня», «рассказчик», the
    speaker line and «написать за меня» as text fields in `prompts`. Each becomes a block (own wording
    kept; an empty field becomes a switched-off block) and the preset is saved. Returns the names of
    presets where a person's own wording was moved; the page tells them once (setting MOVED_SETTING)."""
    from tavern.core.presets import DEFAULT_PROMPTS_OLD, WHEN  # noqa: PLC0415

    moved: list[str] = []
    for row in ctx.presets.list():
        preset = ctx.presets.get(row["id"])
        data = preset["data"]
        old = data.get("prompts") if isinstance(data.get("prompts"), dict) else {}
        present = {b.get("when") for b in data.get("blocks", []) if isinstance(b, dict)}
        if all(key in present for key in WHEN) and not any(key in old for key in WHEN):
            continue
        custom = any(key not in present and isinstance(old.get(key), str) and old[key] != DEFAULT_PROMPTS_OLD[key]
                     for key in WHEN)
        ctx.presets.update(preset["id"], preset["name"], normalize_preset(data))
        if custom:
            moved.append(preset["name"])
    if moved:
        known = ctx.settings.get(MOVED_SETTING) or []
        ctx.settings.set(MOVED_SETTING, [*known, *[n for n in moved if n not in known]])
    return moved


MOVED_PARAMS = "preset_params_moved"


def move_model_settings(ctx: AppContext) -> dict[str, Any]:
    """Once: the model settings of the preset in effect everywhere go into the active connection (the
    preset used to win over it, so replies stay as they were). A whole-data copy is taken first. Only
    the keys the preset set are written. Other presets that carry settings are only logged. Returns
    what was moved ({} when nothing)."""
    if ctx.settings.get(MOVED_PARAMS):
        return {}
    active = ctx.settings.get("active_preset")
    moved: dict[str, Any] = {}
    others: list[str] = []
    for row in ctx.presets.list():
        found = model_settings(normalize_preset(ctx.presets.get(row["id"])["data"]))
        if not found:
            continue
        if row["id"] == active:
            moved = found
        else:
            others.append(row["name"])
    if others:
        log.info("Presets with their own model settings (not moved, no longer used): %s", ", ".join(others))
    if not moved:
        ctx.settings.set(MOVED_PARAMS, True)
        return {}
    conn_id = ctx.settings.get("active_connection")
    try:
        connection = ctx.connections.get(conn_id) if conn_id else None
    except NotFound:
        connection = None
    if connection is None:
        log.info("Model settings of the preset wait for an active connection to move into")
        return {}
    ctx.backups.before("Перенос настроек модели из пресета в подключение")
    try:
        merged = conn_params.validate({**connection["params"], **moved})
    except ValueError:
        log.warning("Preset model settings were not moved: they do not fit the connection", exc_info=True)
        ctx.settings.set(MOVED_PARAMS, True)
        return {}
    ctx.connections.update(connection["id"], params=merged)
    ctx.settings.set(MOVED_PARAMS, True)
    log.info("Model settings of the preset moved into connection %s: %s", connection["name"], ", ".join(moved))
    return moved
