"""Regex rules (stage 11): rules of three scopes, and the four places they work. See docs/adr/0017-regex-rules.md.

Which rules a chat has, in this order: global, then the chat's preset, then every character in the chat
(each scope in the order the person set). A rule works only if it is on and allowed (rules that came with
an imported card or preset wait for «Разрешить»).

Four places, one pipeline each (all run in the worker process of core/regex_runner.py, never here):
- display: what the page shows. Result {text, changed, nodes}: `nodes` is a cleaned HTML tree when a
  rule with «показывать как разметку» changed the text (core/safe_html.py). Cached by rules and text.
- prompt: what the model gets. `prompt_texts()` rewrites the history (and lorebook entries) before
  core/assembly.py builds the prompt, so «Что видела модель» shows exactly the rewritten text. Runs on the
  message text AFTER `<thinking>` was split out of it and BEFORE the speaker's name and the old
  thinking are put back, so a rule never sees the thinking frame.
- stored: once when a message is saved (and again on edit, for rules marked so). The text before the rules is
  kept in the version's `regex_before`, and `restore_original()` puts it back.
- try-out: any rule on any text, nothing saved.

A rule that runs too long is skipped (the worker is stopped), remembered as slow until it is edited, and
announced to every open page by the notice `regex_skipped`.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import threading
import weakref
from collections import OrderedDict
from typing import Any

from tavern.context import AppContext
from tavern.core.regex import RegexError, applies, build_spec, normalize_rule
from tavern.core.regex_runner import RUNNER, Outcome
from tavern.core.safe_html import sanitize
from tavern.errors import AppError, NotFound
from tavern.formats import st_regex
from tavern.services import cast
from tavern.storage.repos.regex import SCOPES, RegexRepo

log = logging.getLogger(__name__)

MAX_RULES = 2000
MAX_TEXT = 400_000
CACHE_ENTRIES = 3000
PLACE_OF_ROLE = {"user": "user", "assistant": "assistant", "reasoning": "reasoning"}
DEFAULT_CHAR = "Assistant"

_cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
_cache_lock = threading.Lock()  # display() runs in worker threads
_slow: dict[str, str] = {}  # rule id -> fingerprint of the rule that ran too long (until it is edited)


class BadRule(AppError):
    code = "bad_regex"


def repo(ctx: AppContext) -> RegexRepo:
    return RegexRepo(ctx.db)


# --- rules: lists, edits ---------------------------------------------------------------------


def _compiled(find: str, replace: str, macros: str, trim: tuple[str, ...], char: str, user: str):
    """(spec, groups, fingerprint) and None, or None and the Russian error. Cached: the same rule is used again
    and again; the names matter only to a rule that has a `{{macro}}` in it."""
    if "{{" not in find and "{{" not in replace:
        char = user = ""
    return _compiled_cached(find, replace, macros, trim, char, user)


@functools.lru_cache(maxsize=8192)
def _compiled_cached(find: str, replace: str, macros: str, trim: tuple[str, ...], char: str, user: str):
    try:
        spec, pattern = build_spec({"find": find, "replace": replace, "macros": macros, "trim": list(trim)},
                                   char, user)
    except RegexError as exc:
        return None, exc.message
    return (spec, pattern.groups, _fingerprint(spec)), None


def _fingerprint(spec: dict[str, Any]) -> str:
    return hashlib.blake2b(json.dumps(spec, sort_keys=True).encode(), digest_size=8).hexdigest()


def _owner_check(ctx: AppContext, scope: str, owner_id: str) -> str:
    if scope not in SCOPES:
        raise BadRule("Область правил: global (для всех), character (для персонажа) или preset (для пресета)")
    if scope == "global":
        return ""
    if scope == "character":
        ctx.characters.get(owner_id)
    else:
        from tavern.services import presets  # noqa: PLC0415 - presets imports this module for import / export

        if owner_id == presets.BUILTIN_ID:
            raise BadRule("К встроенному пресету правила не привязать: сделайте его копию")
        ctx.presets.get(owner_id)
    return owner_id


def describe(rule: dict[str, Any]) -> dict[str, Any]:
    """A rule for the page: with the pattern's error text (if it cannot be used) and the slow mark."""
    done, error = _compiled(rule["find"], rule["replace"], rule["macros"], tuple(rule["trim"]), "Имя", "Вы")
    slow = _slow.get(rule["id"])
    out = dict(rule)
    out["error"] = error or ""
    out["slow"] = bool(done and slow and slow == done[2])
    return out


def list_rules(ctx: AppContext, scope: str, owner_id: str = "") -> list[dict[str, Any]]:
    owner_id = _owner_check(ctx, scope, owner_id)
    return [describe(r) for r in _rules_of(ctx, _memo(ctx), scope, owner_id)]


def overview(ctx: AppContext) -> dict[str, Any]:
    """Everything the «Регекс» tab shows: the global list, and every character / preset that has rules."""
    store = repo(ctx)
    memo = _memo(ctx)
    names = {c["id"]: c["name"] for c in ctx.characters.list()}
    preset_names = {p["id"]: p["name"] for p in ctx.presets.list()}

    def owners(scope: str, known: dict[str, str]) -> list[dict[str, Any]]:
        out = []
        for owner_id in store.owners(scope):
            if owner_id in known:
                rules = [describe(r) for r in _rules_of(ctx, memo, scope, owner_id)]
                out.append({"id": owner_id, "name": known[owner_id], "rules": rules,
                            "pending": sum(not r["approved"] for r in rules)})
        return sorted(out, key=lambda o: o["name"].lower())

    return {"global": [describe(r) for r in _rules_of(ctx, memo, "global", "")],
            "characters": owners("character", names), "presets": owners("preset", preset_names)}


def get_rule(ctx: AppContext, rule_id: str) -> dict[str, Any]:
    return describe(repo(ctx).get(rule_id))


def create_rule(ctx: AppContext, scope: str, owner_id: str, raw: Any) -> dict[str, Any]:
    owner_id = _owner_check(ctx, scope, owner_id)
    if sum(repo(ctx).count(s, o) for s, o in {("global", ""), (scope, owner_id)}) >= MAX_RULES:
        raise BadRule(f"Правил слишком много (не больше {MAX_RULES})")
    rule = normalize_rule(raw if isinstance(raw, dict) else {})
    return describe(repo(ctx).create(scope, owner_id, rule, approved=True))


def update_rule(ctx: AppContext, rule_id: str, raw: Any) -> dict[str, Any]:
    store = repo(ctx)
    base = store.get(rule_id)
    rule = normalize_rule(raw if isinstance(raw, dict) else {}, base=base)
    _slow.pop(rule_id, None)
    return describe(store.update(rule_id, rule))


def delete_rule(ctx: AppContext, rule_id: str) -> None:
    repo(ctx).delete(rule_id)
    _slow.pop(rule_id, None)


def copy_rule(ctx: AppContext, rule_id: str, scope: str | None = None, owner_id: str = "") -> dict[str, Any]:
    """A copy of a rule, in the same list or in another (a character's, a preset's, the global one)."""
    store = repo(ctx)
    source = store.get(rule_id)
    scope = scope or source["scope"]
    owner_id = _owner_check(ctx, scope, owner_id if scope != source["scope"] or owner_id else source["owner_id"])
    rule = {**source, "name": (source["name"] + " (копия)")[:120], "extra": {k: v for k, v in source["extra"].items()
                                                                          if k != "id"}}
    return describe(store.create(scope, owner_id, rule, approved=True))


def reorder(ctx: AppContext, scope: str, owner_id: str, ids: Any) -> list[dict[str, Any]]:
    owner_id = _owner_check(ctx, scope, owner_id)
    store = repo(ctx)
    have = [r["id"] for r in store.list(scope, owner_id)]
    if not isinstance(ids, list) or sorted(ids) != sorted(have):
        raise BadRule("Порядок должен содержать каждое правило списка по одному разу")
    store.set_order(scope, owner_id, ids)
    return list_rules(ctx, scope, owner_id)


def approve(ctx: AppContext, scope: str, owner_id: str) -> int:
    """Allow the rules that came with a card or a preset. They change what the model receives, so
    the person says yes once."""
    return repo(ctx).approve(scope, _owner_check(ctx, scope, owner_id))


# --- which rules work in a chat --------------------------------------------------------------


def _owners(ctx: AppContext, chat: dict[str, Any]) -> list[tuple[str, str]]:
    """The lists whose rules work in a chat, in the order they run."""
    from tavern.services import presets  # noqa: PLC0415

    lists = [("global", "")]
    preset_id = presets.resolve(ctx, chat)["id"]
    if preset_id != presets.BUILTIN_ID:
        lists.append(("preset", preset_id))
    seen: set[str] = set()
    members = [m["character_id"] for m in ctx.members.list(chat["id"])]
    for character_id in [*members, chat.get("character_id")]:
        if character_id and character_id not in seen:
            seen.add(character_id)
            lists.append(("character", character_id))
    return lists


class _Memo:
    """Rules read from the database and rule sets built from them, kept while the table does not change
    (RegexRepo.stamp()). A chat of hundreds of messages asks for them on every view and every send."""

    def __init__(self, stamp: tuple) -> None:
        self.stamp = stamp
        self.lists: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.sets: dict[tuple, _Set] = {}


_memos: "weakref.WeakKeyDictionary[Any, _Memo]" = weakref.WeakKeyDictionary()
_memo_lock = threading.Lock()


def _memo(ctx: AppContext) -> _Memo:
    stamp = repo(ctx).stamp()
    with _memo_lock:
        memo = _memos.get(ctx.db)
        if memo is None or memo.stamp != stamp:
            memo = _memos[ctx.db] = _Memo(stamp)
        return memo


def _rules_of(ctx: AppContext, memo: _Memo, scope: str, owner_id: str) -> list[dict[str, Any]]:
    got = memo.lists.get((scope, owner_id))
    if got is None:
        got = memo.lists[(scope, owner_id)] = repo(ctx).list(scope, owner_id)
    return got


def chat_rules(ctx: AppContext, chat: dict[str, Any]) -> list[dict[str, Any]]:
    memo = _memo(ctx)
    rules: list[dict[str, Any]] = []
    for scope, owner_id in _owners(ctx, chat):
        rules += _rules_of(ctx, memo, scope, owner_id)
    return rules


def rule_set(ctx: AppContext, chat: dict[str, Any], char: str, user: str, *, stored_only: bool = False) -> "_Set":
    """The built rules of a chat (see `_Set`); the same object while nothing they depend on has changed."""
    memo = _memo(ctx)
    owners = _owners(ctx, chat)
    key = (tuple(owners), char, user, stored_only, frozenset(_slow.items()))
    with _memo_lock:
        found = memo.sets.get(key)
    if found is not None:
        return found
    rules: list[dict[str, Any]] = []
    for scope, owner_id in owners:
        rules += _rules_of(ctx, memo, scope, owner_id)
    if stored_only:
        rules = [r for r in rules if r["when"] == "stored"]
    built = _Set(rules, char, user)
    with _memo_lock:
        if len(memo.sets) >= 32:
            memo.sets.clear()
        memo.sets[key] = built
    return built


def chat_names(ctx: AppContext, chat: dict[str, Any]) -> tuple[str, str]:
    char = DEFAULT_CHAR
    if chat.get("character_id"):
        try:
            char = ctx.characters.get(chat["character_id"])["name"]
        except NotFound:
            pass
    return char, cast.user_name(ctx, chat["id"])


class _Set:
    """The rules of a chat, built into specs once for one request."""

    def __init__(self, rules: list[dict[str, Any]], char: str, user: str) -> None:
        self.rules: list[dict[str, Any]] = []
        self.specs: list[dict[str, Any]] = []
        self.finger: list[str] = []   # what the display cache goes by: the spec and how its result is shown
        self.prints: list[str] = []   # the spec alone (a rule that ran too long is remembered by it)
        self.broken: list[dict[str, Any]] = []
        self._bases: dict[tuple, list[tuple[int, int | None, int | None]]] = {}
        self._picked: dict[tuple, list[int]] = {}
        for rule in rules:
            if not rule["enabled"] or not rule["approved"]:
                continue
            done, error = _compiled(rule["find"], rule["replace"], rule["macros"], tuple(rule["trim"]), char, user)
            if error or done is None:
                self.broken.append({"id": rule["id"], "name": rule["name"], "why": error or ""})
                continue
            fingerprint = done[2]
            if _slow.get(rule["id"]) == fingerprint:
                continue  # it ran too long before; skipped until it is edited
            self.rules.append(rule)
            self.specs.append(done[0])
            self.prints.append(fingerprint)
            self.finger.append(fingerprint + ("h" if rule["render_html"] else ""))

    def pick(self, place: str | None, depth: int, kind: str, *, editing: bool = False) -> list[int]:
        """Indexes of the rules that work on a text of this place at this depth. The list is shared: do not change it."""
        if place is None:
            return []
        base = self._bases.get((place, kind, editing))
        if base is None:  # the rules for this place and purpose, whatever the depth: (index, min, max)
            base = self._bases[(place, kind, editing)] = [
                (i, rule["min_depth"], rule["max_depth"]) for i, rule in enumerate(self.rules)
                if applies({**rule, "min_depth": None, "max_depth": None}, place, 0, kind)
                and (not editing or rule["run_on_edit"])]
        if place == "lore" or all(low is None and high is None for _, low, high in base):
            key = (place, kind, editing, None)
        else:
            key = (place, kind, editing, depth)
        got = self._picked.get(key)
        if got is None:
            got = self._picked[key] = [i for i, low, high in base
                                       if (low is None or depth >= low) and (high is None or depth <= high)
                                       or place == "lore"]
        return got


def _report(rules: _Set, outcome: Outcome) -> list[dict[str, Any]]:
    """Rules the worker skipped: remember them as slow until edited; the NEW ones are returned."""
    fresh: list[dict[str, Any]] = []
    for index, why in outcome.skipped.items():
        if index >= len(rules.rules):
            continue
        rule = rules.rules[index]
        if _slow.get(rule["id"]) != rules.prints[index]:
            _slow[rule["id"]] = rules.prints[index]
            fresh.append({"id": rule["id"], "name": rule["name"] or rule["find"][:40],
                          "why": "работает слишком долго" if why == "slow" else "не выполнилось"})
    return fresh


def announce(ctx: AppContext, skipped: list[dict[str, Any]]) -> None:
    """Tell every open page which rules were skipped. Call it on the event loop (notices are not thread-safe)."""
    if skipped:
        ctx.notices.emit("regex_skipped", rules=skipped)
        ctx.notices.emit("regex")


def _digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()


def _remember(key: tuple, value: dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)


def _recall(key: tuple) -> dict[str, Any] | None:
    with _cache_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


# --- display ---------------------------------------------------------------------------------


def display_summary(ctx: AppContext, chat_id: str) -> dict[str, Any]:
    chat = ctx.chats.get_chat(chat_id)
    char, user = chat_names(ctx, chat)
    rules = rule_set(ctx, chat, char, user)
    shown = [i for i, r in enumerate(rules.rules) if r["when"] in ("display", "both")]
    parts = [f"{rules.finger[i]}{rules.rules[i]['places']}{rules.rules[i]['min_depth']}{rules.rules[i]['max_depth']}"
             for i in shown]
    stamp = hashlib.blake2b("|".join(parts).encode(), digest_size=6).hexdigest()
    sensitive = any(rules.rules[i]["min_depth"] is not None or rules.rules[i]["max_depth"] is not None for i in shown)
    return {"display": len(shown), "stamp": stamp, "depth_sensitive": sensitive, "broken": rules.broken}


def display(ctx: AppContext, chat_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """items: [{id, text, role, depth, stream?}] -> {results: {id: {text, changed, nodes}}, skipped: [...]}.
    Blocks while the worker runs (a few milliseconds): call it from a thread."""
    chat = ctx.chats.get_chat(chat_id)
    char, user = chat_names(ctx, chat)
    rules = rule_set(ctx, chat, char, user)
    results: dict[str, dict[str, Any]] = {}
    jobs: list[tuple[str, dict[str, Any], list[int], tuple]] = []
    for item in items[:600]:
        text = item["text"][:MAX_TEXT]
        picked = rules.pick(PLACE_OF_ROLE.get(item.get("role")), int(item.get("depth") or 0), "display")
        if not picked:
            results[item["id"]] = {"text": text, "changed": False, "nodes": None}
            continue
        key = (tuple(rules.finger[i] for i in picked), _digest(text), bool(item.get("stream")))
        cached = _recall(key)
        if cached is not None:
            results[item["id"]] = cached
            continue
        jobs.append((item["id"], {"text": text, "rules": picked, "stream": bool(item.get("stream"))}, picked, key))
    skipped: list[dict[str, Any]] = []
    if jobs:
        outcome = RUNNER.run(rules.specs, [job[1] for job in jobs])
        skipped = _report(rules, outcome)
        for (item_id, payload, picked, key), out, hits in zip(jobs, outcome.outs, outcome.hits):
            html = any(rules.rules[i]["render_html"] for i in hits)
            result = {"text": out, "changed": out != payload["text"],
                      "nodes": sanitize(out, stream=payload["stream"]) if html else None}
            results[item_id] = result
            if not outcome.skipped:  # a result made without a skipped rule must not be remembered
                _remember(key, result)
    return {"results": results, "skipped": skipped}


# --- prompt ----------------------------------------------------------------------------------


def prompt_texts(ctx: AppContext, chat: dict[str, Any], history: list[dict[str, Any]], lore: list[Any],
                 char: str, user: str
                 ) -> tuple[dict[str, str], dict[str, str], dict[str, str], list[dict[str, Any]]]:
    """The model's view of the chat: {message id: text} for messages a rule changed, {lore block id: text}
    for lorebook entries, {message id: reasoning} for old reasoning a rule changed (ST placement 6), and the
    rules skipped for the first time (give them to `announce`). Blocks while the worker runs: call it from a
    thread."""
    rules = rule_set(ctx, chat, char, user)
    if not any(r["when"] in ("prompt", "both") for r in rules.rules):
        return {}, {}, {}, []
    items: list[dict[str, Any]] = []
    owners: list[tuple[str, str]] = []
    last = len(history) - 1
    for index, message in enumerate(history):
        picked = rules.pick(PLACE_OF_ROLE.get(message["role"]), last - index, "prompt")
        if picked and message["content"]:
            items.append({"text": message["content"], "rules": picked})
            owners.append(("m", message["id"]))
        thought = ((message.get("swipe_meta") or {}).get("reasoning") or "") if message["role"] == "assistant" else ""
        picked = rules.pick("reasoning", last - index, "prompt") if isinstance(thought, str) and thought else []
        if picked:
            items.append({"text": thought, "rules": picked})
            owners.append(("t", message["id"]))
    for found in lore:
        block = found.block
        picked = rules.pick("lore", 0, "prompt")
        if picked and block["content"]:
            items.append({"text": block["content"], "rules": picked})
            owners.append(("l", block["id"]))
    if not items:
        return {}, {}, {}, []
    outcome = RUNNER.run(rules.specs, items)
    skipped = _report(rules, outcome)
    messages: dict[str, str] = {}
    lore_text: dict[str, str] = {}
    thoughts: dict[str, str] = {}
    target = {"m": messages, "l": lore_text, "t": thoughts}
    for (kind, owner_id), item, out in zip(owners, items, outcome.outs):
        if out != item["text"]:
            target[kind][owner_id] = out
    return messages, lore_text, thoughts, skipped


# --- stored ----------------------------------------------------------------------------------


def stored(ctx: AppContext, chat_id: str, text: str, role: str, *, editing: bool = False) -> tuple[str, str | None]:
    """The text to save, and the text before the rules when they changed it (else None)."""
    place = PLACE_OF_ROLE.get(role)
    if place is None or not text:
        return text, None
    chat = ctx.chats.get_chat(chat_id)
    char, user = chat_names(ctx, chat)
    rules = rule_set(ctx, chat, char, user, stored_only=True)
    picked = rules.pick(place, 0, "stored", editing=editing)
    if not picked:
        return text, None
    outcome = RUNNER.run(rules.specs, [{"text": text, "rules": picked}])
    announce(ctx, _report(rules, outcome))
    out = outcome.outs[0]
    if not out.strip():  # a rule must not turn a message into nothing
        return text, None
    return (out, text) if out != text else (text, None)


def restore_original(ctx: AppContext, message_id: str) -> dict[str, Any]:
    """«Вернуть текст как был»: put back the text a stored rule changed. The rules do not run again."""
    message = ctx.chats.get_message(message_id)
    meta = message.get("swipe_meta") or {}
    before = meta.get("regex_before")
    if not isinstance(before, str):
        raise BadRule("У этого сообщения нет сохранённого прежнего текста")
    ctx.chats.set_content(message_id, before)
    ctx.chats.update_swipe_meta(message_id, message["active_swipe"], {"regex_before": None})
    return ctx.chats.get_message(message_id)


# --- try-out ---------------------------------------------------------------------------------


def try_out(ctx: AppContext, raw: Any, text: Any, chat_id: str | None = None) -> dict[str, Any]:
    """Run one rule (not saved) on a text: what it finds and what comes out. Never raises for a bad pattern."""
    if not isinstance(raw, dict) or not isinstance(text, str):
        raise BadRule("Нужны правило и текст для пробы")
    text = text[:MAX_TEXT]
    char, user = "Персонаж", "Вы"
    if chat_id:
        try:
            char, user = chat_names(ctx, ctx.chats.get_chat(chat_id))
        except NotFound:
            pass
    try:
        rule = normalize_rule(raw)
        spec, pattern = build_spec(rule, char, user)
    except RegexError as exc:
        return {"ok": False, "error": exc.message, "matches": [], "result": text, "nodes": None}
    outcome = RUNNER.run([spec], [{"text": text, "rules": [0]}], find=True)
    if outcome.skipped:
        why = outcome.skipped.get(0, "")
        error = ("Шаблон работает слишком долго на этом тексте и был остановлен. Скорее всего, в нём вложенные "
                 "повторы вроде «(a+)+»: перепишите так, чтобы одно и то же нельзя было разбить разными способами"
                 if why == "slow" else f"Шаблон не выполнился: {why}")
        return {"ok": False, "error": error, "matches": [], "result": text, "nodes": None}
    result = outcome.outs[0]
    nodes = sanitize(result) if rule["render_html"] and result != text else None
    return {"ok": True, "error": "", "matches": outcome.matches, "result": result, "nodes": nodes,
            "groups": pattern.groups, "names": list(pattern.names)}


# --- import and export -----------------------------------------------------------------------


def import_rules(ctx: AppContext, obj: Any, scope: str, owner_id: str = "", *, approved: bool | None = None
                 ) -> dict[str, Any]:
    """Scripts from a file (ST or ours) into a list. A script whose pattern cannot be used is kept switched
    off, so nothing is lost, and named in `problems`. Global ones are on at once (you chose them); a
    character's or preset's wait for «Разрешить»."""
    owner_id = _owner_check(ctx, scope, owner_id)
    allowed = (scope == "global") if approved is None else approved
    scripts = st_regex.scripts_from_file(obj)
    fresh, problems = [], []
    for fields in scripts[:MAX_RULES]:
        try:
            rule = normalize_rule(fields)
        except RegexError as exc:
            rule = normalize_rule({**fields, "find": "x", "enabled": False})
            rule["find"], rule["enabled"] = str(fields.get("find", ""))[:4000], False
            problems.append({"name": fields.get("name", ""), "why": exc.message})
        fresh.append(rule)
    if len(scripts) > MAX_RULES:
        problems.append({"name": "", "why": f"В файле {len(scripts)} правил, взято первые {MAX_RULES}"})
    made = [describe(r) for r in repo(ctx).create_many(scope, owner_id, fresh, approved=allowed)]
    return {"imported": made, "problems": problems}


def export_rules(ctx: AppContext, scope: str, owner_id: str = "", ids: list[str] | None = None) -> Any:
    """One script as a SillyTavern object, several as an array."""
    rules = repo(ctx).list(scope, _owner_check(ctx, scope, owner_id))
    if ids:
        rules = [r for r in rules if r["id"] in ids]
    scripts = [st_regex.script_to_st(r) for r in rules]
    return scripts[0] if len(scripts) == 1 else scripts


def take_card_rules(ctx: AppContext, character: dict[str, Any]) -> int:
    """A card was imported: move the scripts it carries (`extensions.regex_scripts`) into the rules of that
    character, switched on only after «Разрешить». Returns how many."""
    data = character["data"]
    extensions = data.get("extensions")
    scripts = extensions.get("regex_scripts") if isinstance(extensions, dict) else None
    if not isinstance(scripts, list) or not scripts:
        return 0
    try:
        count = len(import_rules(ctx, scripts, "character", character["id"], approved=False)["imported"])
    except RegexError:
        count = 0
    rest = {k: v for k, v in extensions.items() if k != "regex_scripts"}
    ctx.characters.update(character["id"], {**data, "extensions": rest})
    return count


def card_with_rules(ctx: AppContext, character_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """The card data for export, with the character's rules back in `extensions.regex_scripts`."""
    rules = repo(ctx).list("character", character_id)
    if not rules:
        return data
    extensions = dict(data.get("extensions") or {})
    extensions["regex_scripts"] = [st_regex.script_to_st(r) for r in rules]
    return {**data, "extensions": extensions}


def preset_scripts(ctx: AppContext, preset_id: str) -> list[dict[str, Any]]:
    return [st_regex.script_to_st(r) for r in repo(ctx).list("preset", preset_id)]


# --- the doctor -------------------------------------------------------------------------------


def problems(ctx: AppContext) -> list[dict[str, Any]]:
    """Rules whose pattern cannot be used, or that have no owner any more (for services/doctor.py)."""
    out = []
    characters = {c["id"] for c in ctx.characters.list()}
    presets = {p["id"] for p in ctx.presets.list()}
    for rule in repo(ctx).all():
        orphan = (rule["scope"] == "character" and rule["owner_id"] not in characters) or \
                 (rule["scope"] == "preset" and rule["owner_id"] not in presets)
        error = describe(rule)["error"]
        if orphan or error:
            out.append({"id": rule["id"], "name": rule["name"], "orphan": orphan, "error": error})
    return out
