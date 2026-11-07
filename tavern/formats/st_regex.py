"""SillyTavern regex scripts <-> Tavern rules (stage 11).

Written from the shape of exported script files and the public SillyTavern documentation, not from its
code. A script is a JSON object:

    {"id": "...", "scriptName": "...", "findRegex": "/pattern/flags", "replaceString": "...",
     "trimStrings": ["..."], "placement": [1, 2, 5], "disabled": false, "markdownOnly": true,
     "promptOnly": false, "runOnEdit": true, "substituteRegex": 0, "minDepth": null, "maxDepth": null}

placement: 1 = your messages, 2 = the model's replies, 3 = slash commands, 5 = world info entries,
6 = reasoning (4 is an old value). markdownOnly = "only what is shown", promptOnly = "only what is sent to
the model", both = both, neither = the saved text itself. substituteRegex: 0 = macros are not put into the
pattern, 1 = put in as they are, 2 = put in escaped.

A file holds one script or an array of them; a card carries them in `data.extensions.regex_scripts`, a
preset in `extensions.regex_scripts`. Fields we do not use (and placements we cannot serve) stay in the
rule's `extra`, so an export gives them back. Our own export adds a `tavern` object with the few settings
SillyTavern has no place for (HTML output); SillyTavern ignores it.
"""

from __future__ import annotations

from typing import Any

from tavern.core.regex import MACROS, RegexError

PLACEMENT = {1: "user", 2: "assistant", 5: "lore", 6: "reasoning"}
PLACEMENT_NUMBER = {name: number for number, name in PLACEMENT.items()}
USED = {"scriptName", "findRegex", "replaceString", "trimStrings", "placement", "disabled", "markdownOnly",
        "promptOnly", "runOnEdit", "substituteRegex", "minDepth", "maxDepth", "tavern"}


def _depth(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def script_from_st(script: Any) -> dict[str, Any]:
    """One SillyTavern script -> the fields of a rule (not yet validated; see core/regex.normalize_rule)."""
    if not isinstance(script, dict) or not isinstance(script.get("findRegex"), str):
        raise RegexError("Это не регекс-скрипт: нет поля findRegex")
    placement = [p for p in script.get("placement") or [] if isinstance(p, int)]
    markdown, prompt = bool(script.get("markdownOnly")), bool(script.get("promptOnly"))
    when = "both" if markdown and prompt else "display" if markdown else "prompt" if prompt else "stored"
    macros = script.get("substituteRegex")
    extra = {k: v for k, v in script.items() if k not in USED}
    unused = [p for p in placement if p not in PLACEMENT]
    if unused:
        extra["placement_unused"] = unused
    rule: dict[str, Any] = {
        "name": str(script.get("scriptName") or "Без названия"),
        "enabled": not script.get("disabled", False),
        "find": script["findRegex"],
        "replace": script.get("replaceString") if isinstance(script.get("replaceString"), str) else "",
        "trim": [t for t in script.get("trimStrings") or [] if isinstance(t, str)],
        "places": [PLACEMENT[p] for p in placement if p in PLACEMENT],
        "when": when,
        "min_depth": _depth(script.get("minDepth")),
        "max_depth": _depth(script.get("maxDepth")),
        "run_on_edit": bool(script.get("runOnEdit", False)),
        "macros": MACROS[macros] if isinstance(macros, int) and 0 <= macros < len(MACROS) else "none",
        "extra": extra,
    }
    native = script.get("tavern")
    if isinstance(native, dict):  # our own export: the settings SillyTavern has no place for
        for key in ("render_html", "macros", "when", "places"):
            if key in native:
                rule[key] = native[key]
    return rule


def scripts_from_file(obj: Any) -> list[dict[str, Any]]:
    """The contents of a file: one script, a list, or {"scripts": [...]} / {"regex_scripts": [...]}."""
    if isinstance(obj, dict):
        for key in ("scripts", "regex_scripts"):
            if isinstance(obj.get(key), list):
                obj = obj[key]
                break
    items = obj if isinstance(obj, list) else [obj]
    if not items:
        raise RegexError("В файле нет ни одного скрипта")
    return [script_from_st(item) for item in items]


def script_to_st(rule: dict[str, Any]) -> dict[str, Any]:
    """A rule as a SillyTavern script, with what we kept of its unknown fields."""
    extra = dict(rule.get("extra") or {})
    unused = extra.pop("placement_unused", [])
    when = rule.get("when", "display")
    out = dict(extra)
    out.update({
        "id": extra.get("id") or rule.get("id", ""),
        "scriptName": rule.get("name", ""),
        "findRegex": rule.get("find", ""),
        "replaceString": rule.get("replace", ""),
        "trimStrings": list(rule.get("trim") or []),
        "placement": [PLACEMENT_NUMBER[p] for p in rule.get("places", []) if p in PLACEMENT_NUMBER] + list(unused),
        "disabled": not rule.get("enabled", True),
        "markdownOnly": when in ("display", "both"),
        "promptOnly": when in ("prompt", "both"),
        "runOnEdit": bool(rule.get("run_on_edit", False)),
        "substituteRegex": MACROS.index(rule.get("macros", "none")),
        "minDepth": rule.get("min_depth"),
        "maxDepth": rule.get("max_depth"),
        "tavern": {"render_html": bool(rule.get("render_html", False)), "macros": rule.get("macros", "none"),
                   "when": when, "places": list(rule.get("places", []))},
    })
    return out
