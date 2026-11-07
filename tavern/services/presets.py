"""Prompt presets: the built-in default, the user's own, which one a chat uses, ST import/export.

A chat uses `chat.meta["preset_id"]`, else the setting `active_preset`, else the built-in
default. A preset that was deleted silently falls back, so a chat never stops working.
Changes fire `presets.changed` (action, preset_id) for plugins and agents.
"""

from __future__ import annotations

from typing import Any

from tavern.context import AppContext
from tavern.core.presets import default_preset, normalize_preset
from tavern.errors import AppError, NotFound
from tavern.formats import st_preset

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


async def create(ctx: AppContext, name: Any, data: Any = None) -> dict[str, Any]:
    preset = ctx.presets.create(_name(name), normalize_preset(data if data is not None else default_preset()))
    await ctx.events.emit("presets.changed", preset_id=preset["id"])
    return preset


async def update(ctx: AppContext, preset_id: str, name: Any, data: Any) -> dict[str, Any]:
    if preset_id == BUILTIN_ID:
        raise AppError("The built-in preset cannot be changed; make a copy", code="builtin_preset")
    preset = ctx.presets.update(preset_id, _name(name), normalize_preset(data))
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


async def import_st(ctx: AppContext, obj: Any, filename: str = "") -> dict[str, Any]:
    name, data = st_preset.from_st(obj, _stem(filename))
    data, scripts = _split_rules(data)
    return await _with_rules(ctx, await create(ctx, name, data), scripts)


async def import_any(ctx: AppContext, obj: Any, filename: str = "") -> dict[str, Any]:
    """Our own preset file (everything kept) or a SillyTavern one (converted)."""
    if isinstance(obj, dict) and NATIVE_KEY in obj:
        if not isinstance(obj[NATIVE_KEY], int) or obj[NATIVE_KEY] > NATIVE_VERSION:
            raise AppError("This preset was made by a newer Tavern; update the app", code="bad_preset")
        preset = await create(ctx, obj.get("name") or _stem(filename) or "Пресет", obj.get("data"))
        return await _with_rules(ctx, preset, obj.get("regex_scripts"))
    return await import_st(ctx, obj, filename)


def export_native(ctx: AppContext, preset_id: str) -> tuple[str, dict[str, Any]]:
    """Our full format: every block option and setting survives."""
    preset = get(ctx, preset_id)
    from tavern.services import regex as regex_service  # noqa: PLC0415

    out = {NATIVE_KEY: NATIVE_VERSION, "name": preset["name"], "data": normalize_preset(preset["data"])}
    scripts = regex_service.preset_scripts(ctx, preset_id)
    if scripts:
        out["regex_scripts"] = scripts
    return preset["name"], out


def export_st(ctx: AppContext, preset_id: str) -> tuple[str, dict[str, Any]]:
    """SillyTavern format: what ST has no place for (e.g. history limits) is left out."""
    preset = get(ctx, preset_id)
    from tavern.services import regex as regex_service  # noqa: PLC0415

    out = st_preset.to_st(normalize_preset(preset["data"]))
    scripts = regex_service.preset_scripts(ctx, preset_id)
    if scripts:
        out["extensions"] = {**(out.get("extensions") or {}), "regex_scripts": scripts}
    return preset["name"], out
