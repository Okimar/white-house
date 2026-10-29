"""SillyTavern World Info (lorebook) files <-> our bubbles.

Written from the file format SillyTavern saves (observed files), not from its code. A world file
is {"entries": {"0": {...}, "1": {...}}, ...}.

Entry fields we understand (anything else rides along in `extra.st_entry` and goes back on export):
    key, keysecondary, selective, selectiveLogic (0 and_any, 1 not_all, 2 not_any, 3 and_all),
    comment, content, constant, disable, order, displayIndex, uid, position (0 before the card,
    1 after it, 2/3 top/bottom of the author's note, 4 at a depth, 5/6 top/bottom of the
    examples), depth, role (0 system, 1 user, 2 assistant), probability, useProbability, sticky,
    cooldown, delay, scanDepth, caseSensitive, matchWholeWords, group, groupOverride, groupWeight,
    excludeRecursion, preventRecursion, characterFilter {names, isExclude}.
"""

from __future__ import annotations

from typing import Any, Callable

LOGIC_FROM_ST = {0: "and_any", 1: "not_all", 2: "not_any", 3: "and_all"}
LOGIC_TO_ST = {v: k for k, v in LOGIC_FROM_ST.items()}
PLACE_FROM_ST = {1: "card_after", 2: "an_top", 3: "an_bottom", 5: "examples_top", 6: "examples_bottom"}
PLACE_TO_ST = {v: k for k, v in PLACE_FROM_ST.items()}
AT_DEPTH = 4
ROLE_FROM_ST = {0: "system", 1: "user", 2: "assistant"}
ROLE_TO_ST = {v: k for k, v in ROLE_FROM_ST.items()}
MAX_TITLE = 200
MAX_KEYWORDS = 100


def _int(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return None
    return value


def _strings(value: Any) -> list[str]:
    return [v.strip() for v in value if isinstance(v, str) and v.strip()] if isinstance(value, list) else []


def is_world(data: Any) -> bool:
    return isinstance(data, dict) and isinstance(data.get("entries"), dict)


def entries_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    """World entries in the order SillyTavern shows them (displayIndex, then uid)."""
    raw = data.get("entries")
    items = [e for e in (raw.values() if isinstance(raw, dict) else []) if isinstance(e, dict)]
    return sorted(items, key=lambda e: (_int(e.get("displayIndex"), 0, 10**9) or 0,
                                        _int(e.get("uid"), 0, 10**9) or 0))


def block_from_entry(entry: dict[str, Any], index: int,
                     character_ids: Callable[[list[str]], list[str]] | None = None) -> dict[str, Any]:
    """One world entry as block fields. `character_ids` turns character names into our ids (for
    the character filter); names it does not know stay only in the kept entry."""
    keys = _strings(entry.get("key"))[:MAX_KEYWORDS]
    comment = entry.get("comment") if isinstance(entry.get("comment"), str) else ""
    title = (comment.strip() or (keys[0] if keys else "") or f"Запись {index + 1}")[:MAX_TITLE]
    mode = "off" if entry.get("disable") else ("always" if entry.get("constant") else "search")
    rules: dict[str, Any] = {"title_key": not comment.strip()}  # a note about the entry is not a key
    second = _strings(entry.get("keysecondary"))[:MAX_KEYWORDS]
    if second and entry.get("selective", True):
        rules["keys2"] = second
        rules["logic"] = LOGIC_FROM_ST.get(entry.get("selectiveLogic"), "and_any")
    position = entry.get("position")
    depth = None
    if position == AT_DEPTH:
        depth = _int(entry.get("depth"), 0, 1000)
        depth = 4 if depth is None else depth
    elif position in PLACE_FROM_ST:
        rules["place"] = PLACE_FROM_ST[position]
    for ours, theirs, low, high in (("scan", "scanDepth", 1, 1000), ("cooldown", "cooldown", 1, 1000),
                                    ("delay", "delay", 1, 100_000)):
        value = _int(entry.get(theirs), low, high)
        if value is not None:
            rules[ours] = value
    if entry.get("caseSensitive") is True:
        rules["case"] = True
    if entry.get("matchWholeWords") is True:
        rules["whole"] = True
    groups = [g.strip()[:100] for g in str(entry.get("group") or "").split(",") if g.strip()][:20]
    if groups:
        rules["groups"] = groups
        if entry.get("groupOverride"):
            rules["main"] = True
        weight = _int(entry.get("groupWeight"), 0, 1000)
        if weight is not None and weight != 100:
            rules["weight"] = weight
    if entry.get("excludeRecursion"):
        rules["not_woken"] = True
    if entry.get("preventRecursion"):
        rules["no_wake"] = True
    wanted = entry.get("characterFilter") if isinstance(entry.get("characterFilter"), dict) else {}
    names = _strings(wanted.get("names"))
    if names and character_ids is not None:
        ids = character_ids(names)
        if ids:
            rules["filter"] = {"mode": "except" if wanted.get("isExclude") else "only", "ids": ids}
    chance = _int(entry.get("probability"), 0, 100)
    use_chance = entry.get("useProbability", True) and chance is not None
    return {
        "title": title,
        "content": entry.get("content") if isinstance(entry.get("content"), str) else "",
        "mode": mode,
        "keywords": keys,
        "depth": depth,
        "role": ROLE_FROM_ST.get(entry.get("role"), "system"),
        "sticky": _int(entry.get("sticky"), 0, 1000) or 0,
        "chance": chance if use_chance and mode == "search" else 100,
        "position": index,
        "rules": rules,
        "extra": {"st_entry": entry},
    }


def entry_from_block(block: dict[str, Any], uid: int, comment: str, enabled: bool,
                     character_names: Callable[[list[str]], list[str]] | None = None) -> dict[str, Any]:
    """A block as a world entry, on top of the entry it came from (unknown fields kept)."""
    rules = block.get("rules") or {}
    entry = dict(block.get("extra", {}).get("st_entry") or {})
    position = AT_DEPTH if block["depth"] is not None else PLACE_TO_ST.get(rules.get("place"), 0)
    keys = list(block["keywords"])
    if rules.get("title_key", True) and block["title"] not in keys:
        keys = [block["title"], *keys]
    old_filter = entry.get("characterFilter") if isinstance(entry.get("characterFilter"), dict) else {}
    wanted = rules.get("filter")
    if wanted:
        names = character_names(wanted["ids"]) if character_names else []
        character_filter = {**old_filter, "isExclude": wanted["mode"] == "except", "names": names,
                            "tags": old_filter.get("tags", [])}
    else:  # names we could not match on import stay as they came
        character_filter = {"isExclude": False, "names": [], "tags": [], **old_filter}
    entry.update({
        "uid": uid,
        "displayIndex": uid,
        "key": keys,
        "keysecondary": list(rules.get("keys2") or []),
        "selective": bool(rules.get("keys2")),
        "selectiveLogic": LOGIC_TO_ST.get(rules.get("logic", "and_any"), 0),
        "comment": comment,
        "content": block["content"],
        "constant": block["mode"] == "always",
        "disable": not enabled,
        "order": entry.get("order", 100),
        "position": position,
        "depth": block["depth"] if block["depth"] is not None else entry.get("depth", 4),
        "role": ROLE_TO_ST.get(block["role"], 0),
        "probability": block["chance"],
        "useProbability": block["chance"] < 100,
        "sticky": block["sticky"],
        "cooldown": rules.get("cooldown", 0),
        "delay": rules.get("delay", 0),
        "scanDepth": rules.get("scan"),
        "caseSensitive": True if rules.get("case") else None,
        "matchWholeWords": True if rules.get("whole") else None,
        "group": ",".join(rules.get("groups") or []),
        "groupOverride": bool(rules.get("main")),
        "groupWeight": rules.get("weight", 100),
        "excludeRecursion": bool(rules.get("not_woken")),
        "preventRecursion": bool(rules.get("no_wake")),
        "characterFilter": character_filter,
    })
    return entry
