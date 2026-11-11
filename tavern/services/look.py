"""The look of the interface across updates, and safe mode (ADR 0015).

An update can change the menu, and a theme written for the old menu then breaks it. So the app
remembers a stamp of its own interface files (`look_stamp`). When the stamp differs at startup,
everything that is not built in and shapes the look is switched off once: the chosen user theme, the
characters' own user themes, and the stylesheets of plugins that are not shipped with the app (the
plugins themselves keep working). The built-in default look and built-in plugins' styles stay. Nothing is deleted: what was
switched off is kept in `look_parked` and one action brings it back.

Safe mode is a persistent switch (`safe_mode`, set by `python -m tavern safe-mode on|off`) or a single
run (`python -m tavern --safe`) or `/?safe` in the address. The page then loads no theme and no
plugin parts. All of it lives in the settings table, so copies and restores keep the state and the
stamp consistent.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from tavern import __version__
from tavern.paths import WEB_DIR
from tavern.util.ids import now_iso

log = logging.getLogger(__name__)

KEY_STAMP = "look_stamp"
KEY_PARKED = "look_parked"
KEY_SAFE = "safe_mode"
_SKIP_DIRS = {"__pycache__", ".git", "node_modules"}


def interface_stamp(web_dir: Path = WEB_DIR) -> str:
    """Version plus a hash of the page's own files (not of git: it must also be right after a zip
    update, and a Python-only commit does not change the look)."""
    digest = hashlib.sha256(__version__.encode())
    for path in sorted(web_dir.rglob("*")):
        if not path.is_file() or _SKIP_DIRS & set(path.relative_to(web_dir).parts):
            continue
        digest.update(path.relative_to(web_dir).as_posix().encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"?")
    return f"{__version__}+{digest.hexdigest()[:16]}"


def _builtin_ids(ctx) -> set[str]:
    return {t["id"] for t in ctx.themes.list() if t["builtin"]}


def _custom_look(ctx) -> dict[str, Any]:
    """What the settings point at that is not built in."""
    builtin = _builtin_ids(ctx)
    found: dict[str, Any] = {}
    theme = ctx.settings.get("theme")
    if isinstance(theme, str) and theme and theme not in builtin:
        found["theme"] = theme
    per_character = ctx.settings.get("character_themes") or {}
    custom = {k: v for k, v in per_character.items() if v not in builtin}
    if custom:
        found["character_themes"] = custom
    return found


def _user_plugin_styles(ctx) -> list[str]:
    """Ids of plugins that are not built in and bring a stylesheet."""
    try:
        found = ctx.plugins.discover().values()
    except Exception:  # noqa: BLE001 - a broken plugin folder must not stop the start
        log.exception("Could not list plugins to park their styles")
        return []
    return sorted(i.id for i in found if not i.builtin and i.manifest and i.manifest.get("style"))


def css_off(ctx) -> set[str]:
    """Plugins whose own stylesheet an update switched off (until «Вернуть прежний вид»)."""
    saved = parked(ctx)
    ids = saved.get("plugin_css") if saved else None
    return {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()


def check_upgrade(ctx, stamp: str | None = None) -> dict[str, Any] | None:
    """Run at startup. Returns what was switched off (None when nothing was).

    No stored stamp counts as "an unknown older version": data from before this feature is exactly
    what an update can break. A fresh data folder has nothing custom, so nothing happens there.
    """
    stamp = stamp or interface_stamp()
    old = ctx.settings.get(KEY_STAMP)
    if old == stamp:
        return None
    result = None
    custom = _custom_look(ctx)
    styles = _user_plugin_styles(ctx)
    if styles:
        custom["plugin_css"] = styles
    if custom:
        per_character = ctx.settings.get("character_themes") or {}
        earlier = {k: v for k, v in (parked(ctx) or {}).items() if k in ("theme", "character_themes", "plugin_css")}
        merged = {**earlier, **custom}  # a look parked by an earlier update is not lost by this one
        if earlier.get("character_themes") and custom.get("character_themes"):
            merged["character_themes"] = {**earlier["character_themes"], **custom["character_themes"]}
        if earlier.get("plugin_css") and custom.get("plugin_css"):
            merged["plugin_css"] = sorted({*earlier["plugin_css"], *custom["plugin_css"]})
        result = {"at": now_iso(), "from_stamp": old, "to_stamp": stamp, "seen": False, **merged}
        if "theme" in custom:
            ctx.settings.delete("theme")
        if "character_themes" in custom:
            kept = {k: v for k, v in per_character.items() if k not in custom["character_themes"]}
            if kept:
                ctx.settings.set("character_themes", kept)
            else:
                ctx.settings.delete("character_themes")
        ctx.settings.set(KEY_PARKED, result)
        log.warning("Interface changed since the last run: the custom look was switched off "
                    "(kept, can be switched back on in the page)")
    ctx.settings.set(KEY_STAMP, stamp)
    return result


def parked(ctx) -> dict[str, Any] | None:
    value = ctx.settings.get(KEY_PARKED)
    return value if isinstance(value, dict) else None


def settle(ctx) -> dict[str, Any] | None:
    """Forget the parked parts that are on again, so the notice never says «switched off» about a look that
    shows. A theme comes back on not only through «Вернуть прежний вид»: picked by hand in «Оформление», by the
    assistant, by an undo or a restored copy. Without this the notice stayed (a later update that does not
    touch the page files parks nothing new) and claimed the standard look while the old theme was on."""
    saved = parked(ctx)
    if saved is None:
        return None
    left = dict(saved)
    theme = saved.get("theme")
    if theme and ctx.settings.get("theme") == theme:
        left.pop("theme")
    chosen = ctx.settings.get("character_themes") or {}
    per_character = saved.get("character_themes") or {}
    still = {k: v for k, v in per_character.items() if chosen.get(k) != v}
    if per_character and still != per_character:
        if still:
            left["character_themes"] = still
        else:
            left.pop("character_themes")
    if not (left.get("theme") or left.get("character_themes") or left.get("plugin_css")):
        ctx.settings.delete(KEY_PARKED)
        return None
    if left != saved:
        ctx.settings.set(KEY_PARKED, left)
    return left


def restore(ctx) -> dict[str, Any]:
    """Switch the parked look back on. Themes that no longer exist are skipped."""
    saved = parked(ctx)
    if saved is None:
        return {"restored": False}
    known = {t["id"] for t in ctx.themes.list() if not t.get("error")}
    if saved.get("theme") in known:
        ctx.settings.set("theme", saved["theme"])
    back = {k: v for k, v in (saved.get("character_themes") or {}).items() if v in known}
    if back:
        ctx.settings.set("character_themes", {**(ctx.settings.get("character_themes") or {}), **back})
    ctx.settings.delete(KEY_PARKED)
    return {"restored": True}


def dismiss(ctx) -> None:
    """The owner has read the notice; the parked look stays available in the themes tab."""
    saved = parked(ctx)
    if saved is not None:
        ctx.settings.set(KEY_PARKED, {**saved, "seen": True})


def set_safe_mode(ctx, on: bool) -> None:
    if on:
        ctx.settings.set(KEY_SAFE, True)
    else:
        ctx.settings.delete(KEY_SAFE)


def status(ctx, *, run_safe: bool = False) -> dict[str, Any]:
    saved = settle(ctx)
    persistent = bool(ctx.settings.get(KEY_SAFE))
    names = {t["id"]: t["name"] for t in ctx.themes.list()}
    current = ctx.settings.get("theme") or "default"
    return {
        "safe_mode": persistent or run_safe,
        "safe_persistent": persistent,
        "safe_run": run_safe,
        "parked": None if saved is None else {
            "at": saved.get("at"),
            "seen": bool(saved.get("seen")),
            "theme": saved.get("theme"),
            "theme_name": names.get(saved.get("theme"), saved.get("theme")),
            "characters": len(saved.get("character_themes") or {}),
            "plugin_css": len(css_off(ctx)),
            # what is on instead: the notice says «стандартный вид» only when it is true
            "now_theme": current,
            "now_theme_name": names.get(current, current),
        },
    }
