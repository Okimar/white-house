"""The tree of blocks ("bubbles") of an owner: a character card or a lorebook (stage 5). Edit it,
find blocks for a chat, ST lorebook mapping.

The UI, agents and the on-top assistant (stage 8) all go through these functions. Every change
fires the action event

    character.blocks_changed   character_id      (a character's tree)
    lorebook.blocks_changed    lorebook_id       (a lorebook's bubbles)

Settings used (global, Settings → "Промпт"):
    retrieval            {scan_messages: 3, budget_tokens: 2000, threshold: 0.55}
    embedding_connection connection id for meaning vectors; unset = keywords only
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from tavern.context import AppContext
from tavern.core.llm import LLMClient
from tavern.core.retrieval import LOGICS, PATH_SEPARATOR, FoundBlock, cosine, select_blocks, stem
from tavern.errors import AppError, NotFound
from tavern.services import history, spending
from tavern.providers.base import ChatMessage, ProviderError

log = logging.getLogger(__name__)

MODES = ("always", "search", "off")
ROLES = ("system", "user", "assistant")
DISPLAYS = ("line", "nested")
DEFAULT_RETRIEVAL = {"scan_messages": 3, "budget_tokens": 2000, "threshold": 0.55}
MAX_TITLE = 200
MAX_CONTENT = 200_000
MAX_KEYWORDS = 100
KEYWORD_PROMPT = (
    "List search keywords for the text below: words and short phrases a person would use when "
    "this topic comes up in a story chat. Include synonyms and both Russian and English forms "
    "when they fit. Answer with a JSON array of 5 to 12 strings and nothing else.\n\n"
    "Title: {title}\n\nText:\n{content}"
)
KEYWORD_AVOID = "\n\nThese are already taken, do not repeat them or their other word forms: {words}"


def retrieval_settings(ctx: AppContext) -> dict[str, Any]:
    stored = ctx.settings.get("retrieval") or {}
    return {**DEFAULT_RETRIEVAL, **{k: v for k, v in stored.items() if k in DEFAULT_RETRIEVAL}}


def _clean(changes: dict[str, Any], *, creating: bool) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "title":
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_TITLE:
                raise AppError("A block needs a title (up to 200 characters)", code="bad_block")
            clean[key] = value.strip()
        elif key == "content":
            if not isinstance(value, str) or len(value) > MAX_CONTENT:
                raise AppError("Block text is too long", code="bad_block")
            clean[key] = value
        elif key == "mode":
            if value not in MODES:
                raise AppError("mode must be always, search or off", code="bad_block")
            clean[key] = value
        elif key == "role":
            if value not in ROLES:
                raise AppError("role must be system, user or assistant", code="bad_block")
            clean[key] = value
        elif key == "keywords":
            if not isinstance(value, list) or len(value) > MAX_KEYWORDS or not all(isinstance(k, str) for k in value):
                raise AppError("keywords must be a list of words", code="bad_block")
            seen: list[str] = []
            for keyword in (k.strip() for k in value):
                if keyword and keyword.lower() not in (s.lower() for s in seen):
                    seen.append(keyword[:100])
            clean[key] = seen
        elif key == "depth":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000):
                raise AppError("depth must be empty (next to the card) or 0..1000", code="bad_block")
            clean[key] = value
        elif key in ("sticky", "position"):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000:
                raise AppError(f"{key} must be a non-negative number", code="bad_block")
            clean[key] = value
        elif key == "layout":
            if not isinstance(value, dict):
                raise AppError("layout must be an object", code="bad_block")
            layout = {}
            for coord in ("x", "y"):
                if isinstance(value.get(coord), (int, float)) and not isinstance(value.get(coord), bool):
                    layout[coord] = max(-1e6, min(1e6, float(value[coord])))
            if value.get("display") in DISPLAYS:
                layout["display"] = value["display"]
            if isinstance(value.get("collapsed"), bool):
                layout["collapsed"] = value["collapsed"]
            clean[key] = layout
        elif key == "parent_id":
            if value is not None and not isinstance(value, str):
                raise AppError("parent_id must be a block id or empty", code="bad_block")
            clean[key] = value
        elif key == "detached":
            if not isinstance(value, bool):
                raise AppError("detached must be true or false", code="bad_block")
            clean[key] = value
        elif key == "chance":
            clean[key] = _chance(value)
        elif key == "every":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1000:
                raise AppError("every must be 0..1000 replies to skip", code="bad_block")
            clean[key] = value
        elif key == "rules":
            clean[key] = _rules(value)
        else:
            raise AppError(f"Unknown block field {key!r}", code="bad_block")
    if creating and "title" not in clean:
        raise AppError("A block needs a title", code="bad_block")
    return clean


PLACES = ("card_before", "card_after", "an_top", "an_bottom", "examples_top", "examples_bottom")
RULE_FLAGS = ("title_key", "whole", "case", "main", "no_wake", "not_woken")
RULE_NUMBERS = {"scan": (1, 1000), "cooldown": (0, 1000), "delay": (0, 100_000), "weight": (0, 1000)}


def _words(value: Any, name: str, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(w, str) for w in value):
        raise AppError(f"{name} must be a list of at most {limit} words", code="bad_block")
    seen: list[str] = []
    for word in (w.strip()[:100] for w in value):
        if word and word.lower() not in (s.lower() for s in seen):
            seen.append(word)
    return seen


def _rules(value: Any) -> dict[str, Any]:
    """The finer options of a bubble (core/retrieval.py lists them). None or empty = default."""
    if not isinstance(value, dict):
        raise AppError("rules must be an object", code="bad_block")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if item is None or item == [] or item == "":
            continue
        if key in RULE_FLAGS:
            if not isinstance(item, bool):
                raise AppError(f"rules.{key} must be true or false", code="bad_block")
            clean[key] = item
        elif key in RULE_NUMBERS:
            low, high = RULE_NUMBERS[key]
            if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
                raise AppError(f"rules.{key} must be {low}..{high}", code="bad_block")
            clean[key] = item
        elif key == "keys2":
            clean[key] = _words(item, "rules.keys2", MAX_KEYWORDS)
        elif key == "groups":
            clean[key] = _words(item, "rules.groups", 20)
        elif key == "logic":
            if item not in LOGICS:
                raise AppError(f"rules.logic must be one of {', '.join(LOGICS)}", code="bad_block")
            clean[key] = item
        elif key == "place":
            if item not in PLACES:
                raise AppError(f"rules.place must be one of {', '.join(PLACES)}", code="bad_block")
            clean[key] = item
        elif key == "filter":
            if not isinstance(item, dict) or item.get("mode", "only") not in ("only", "except"):
                raise AppError("rules.filter must be {mode: only|except, ids: [...]}", code="bad_block")
            people = _words(item.get("ids") or [], "rules.filter.ids", 200)
            if people:
                clean[key] = {"mode": item.get("mode", "only"), "ids": people}
        else:
            raise AppError(f"Unknown rule {key!r}", code="bad_block")
    return clean


def _chance(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise AppError("chance must be 0..100 percent", code="bad_block")
    return value


def _check_parent(ctx: AppContext, owner_id: str, block_id: str | None, parent_id: str | None) -> None:
    if parent_id is None:
        return
    parent = ctx.blocks.get(parent_id)
    if parent["owner_id"] != owner_id:
        raise AppError("The parent block belongs to another character", code="bad_block")
    current: str | None = parent_id
    seen = set()
    while current is not None and current not in seen:  # moving under itself would make a loop
        if current == block_id:
            raise AppError("A block cannot go inside itself", code="bad_block")
        seen.add(current)
        current = ctx.blocks.get(current)["parent_id"]


def owner_of(ctx: AppContext, owner_id: str) -> dict[str, Any]:
    """{kind: 'character'|'lorebook', id, name} of whoever owns a tree; NotFound if neither."""
    if ctx.lorebooks.exists(owner_id):
        return {"kind": "lorebook", "id": owner_id, "name": ctx.lorebooks.get(owner_id)["name"]}
    return {"kind": "character", "id": owner_id, "name": ctx.characters.get(owner_id)["name"]}


async def _changed(ctx: AppContext, owner_id: str) -> None:
    if ctx.lorebooks.exists(owner_id):
        await ctx.events.emit("lorebook.blocks_changed", lorebook_id=owner_id)
    else:
        await ctx.events.emit("character.blocks_changed", character_id=owner_id)


def list_blocks(ctx: AppContext, owner_id: str) -> list[dict[str, Any]]:
    owner_of(ctx, owner_id)
    return ctx.blocks.list(owner_id)


async def create_block(ctx: AppContext, owner_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    owner_of(ctx, owner_id)
    clean = _clean(fields, creating=True)
    _check_parent(ctx, owner_id, None, clean.get("parent_id"))
    block = ctx.blocks.create(owner_id, clean)
    history.record(ctx, owner_id, f"Новый пузырь «{block['title']}»",
                   [{"t": "block", "id": block["id"], "row": None}])
    await _changed(ctx, owner_id)
    return block


async def update_block(ctx: AppContext, block_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    block = ctx.blocks.get(block_id)
    clean = _clean(changes, creating=False)
    if "parent_id" in clean:
        _check_parent(ctx, block["owner_id"], block_id, clean["parent_id"])
        if clean["parent_id"] is not None:
            clean["detached"] = False  # joined to a bubble: active again
    if "layout" in clean:
        clean["layout"] = {**block["layout"], **clean["layout"]}
    before = history.block_before(ctx, block_id)
    updated = ctx.blocks.update(block_id, clean)
    # Where a bubble sits (on the map, among its neighbours) is not worth a history step.
    if set(clean) - {"layout", "position"}:
        history.record(ctx, block["owner_id"], f"«{block['title']}»: изменён", [before])
    await _changed(ctx, block["owner_id"])
    return updated


async def set_order(ctx: AppContext, block_id: str, place: Any) -> None:
    """Put a bubble at number `place` (from 1) among its neighbours: the bubbles with the same parent.
    Numbers never repeat: the others shift. The model reads neighbours in this order. Free bubbles
    have no number (they are not sent) and stay after the rest."""
    if isinstance(place, bool) or not isinstance(place, int) or place < 1:
        raise AppError("place must be a number from 1", code="bad_block")
    block = ctx.blocks.get(block_id)
    if block["detached"]:
        raise AppError("A free bubble has no order number: join it first", code="bad_block")
    row = [b for b in ctx.blocks.list(block["owner_id"]) if b["parent_id"] == block["parent_id"]]
    numbered = [b["id"] for b in row if not b["detached"] and b["id"] != block_id]
    numbered.insert(min(place, len(numbered) + 1) - 1, block_id)
    order = numbered + [b["id"] for b in row if b["detached"]]
    with ctx.db.transaction() as conn:
        for index, item in enumerate(order):
            conn.execute("UPDATE character_blocks SET position = ? WHERE id = ? AND position != ?",
                         (index, item, index))
    await _changed(ctx, block["owner_id"])


async def save_layout(ctx: AppContext, owner_id: str, items: Any) -> int:
    """Bubble positions after a drag on the map: [{id, x, y}], all of one character, one event."""
    owner_of(ctx, owner_id)
    if not isinstance(items, list) or len(items) > 10_000:
        raise AppError("items must be a list of {id, x, y}", code="bad_block")
    owned = {b["id"]: b for b in ctx.blocks.list(owner_id)}
    changes = []
    for item in items:
        if not isinstance(item, dict) or item.get("id") not in owned:
            raise AppError("Unknown block in layout", code="bad_block")
        old = owned[item["id"]]["layout"]
        if item.get("auto") is True:  # forget the position: the map places it by the tree again
            changes.append((item["id"], {k: v for k, v in old.items() if k not in ("x", "y")}))
            continue
        layout = _clean({"layout": {"x": item.get("x"), "y": item.get("y")}}, creating=False)["layout"]
        changes.append((item["id"], {**old, **layout}))
    with ctx.db.transaction() as conn:
        for block_id, layout in changes:
            conn.execute("UPDATE character_blocks SET layout = ? WHERE id = ?", (json.dumps(layout), block_id))
    if changes:
        await _changed(ctx, owner_id)
    return len(changes)


async def delete_block(ctx: AppContext, block_id: str) -> None:
    block = ctx.blocks.get(block_id)
    # Parent first, so undo can put the rows back in order; then the links that go with them.
    children: dict[str | None, list[str]] = {}
    for item in ctx.blocks.list(block["owner_id"]):
        children.setdefault(item["parent_id"], []).append(item["id"])
    order, queue = [], [block_id]
    while queue:
        current = queue.pop(0)
        order.append(current)
        queue.extend(children.get(current, []))
    gone = set(order)
    links = [pair for pair in ctx.blocks.links([block["owner_id"]]) if gone & set(pair)]
    before = [history.block_before(ctx, i) for i in order] + [history.link_before(ctx, a, b) for a, b in links]
    ctx.blocks.delete(block_id)  # its sub-blocks go with it
    history.record(ctx, block["owner_id"], f"Удалён «{block['title']}»", before)
    await _changed(ctx, block["owner_id"])


async def link_blocks(ctx: AppContext, owner_id: str, a: str, b: str) -> list[tuple[str, str]]:
    """Link two bubbles of one character: when one goes to the model, so does the other."""
    owner_of(ctx, owner_id)
    if a == b:
        raise AppError("A bubble cannot link to itself", code="bad_block")
    for block_id in (a, b):
        if ctx.blocks.get(block_id)["owner_id"] != owner_id:
            raise AppError("Both bubbles must belong to this character", code="bad_block")
    before = history.link_before(ctx, a, b)
    ctx.blocks.link(owner_id, a, b)
    history.record(ctx, owner_id, "Ссылка между пузырями", [before])
    await _changed(ctx, owner_id)
    return ctx.blocks.links([owner_id])


async def unlink_blocks(ctx: AppContext, owner_id: str, a: str, b: str) -> list[tuple[str, str]]:
    owner_of(ctx, owner_id)
    before = history.link_before(ctx, a, b)
    ctx.blocks.unlink(a, b)
    history.record(ctx, owner_id, "Ссылка убрана", [before])
    await _changed(ctx, owner_id)
    return ctx.blocks.links([owner_id])


async def set_chance(ctx: AppContext, owner_id: str, chance: Any, under: str | None = None) -> int:
    """One chance for every bubble of the character, or for every sub-bubble of `under` only."""
    owner_of(ctx, owner_id)
    chance = _chance(chance)
    blocks = ctx.blocks.list(owner_id)
    if under is None:
        ids = [b["id"] for b in blocks]
    else:
        if ctx.blocks.get(under)["owner_id"] != owner_id:
            raise AppError("That bubble belongs to another character", code="bad_block")
        children: dict[str, list[str]] = {}
        for block in blocks:
            children.setdefault(block["parent_id"], []).append(block["id"])
        ids, stack = [], list(children.get(under, []))
        while stack:
            current = stack.pop()
            ids.append(current)
            stack.extend(children.get(current, []))
    before = [history.block_before(ctx, i) for i in ids]
    changed = ctx.blocks.set_chance(ids, chance)
    history.record(ctx, owner_id, f"Шанс {chance}% у {len(ids)} пузырей", before)
    await _changed(ctx, owner_id)
    return changed


# --- keywords by the model --------------------------------------------------------------------


def _word_key(word: str) -> tuple[str, ...]:
    """"Косу" and "коса" are one keyword: matching ignores endings, so repeats are found the same way."""
    return tuple(stem(w) for w in re.findall(r"\w+", word.lower()))


async def suggest_keywords(ctx: AppContext, block_id: str, avoid: list[str] | None = None) -> list[str]:
    """Ask the chat model for keywords. Returns only new ones: never the title, the block's keywords
    or `avoid` (what was suggested before), in any form. The caller decides what to keep."""
    from tavern.services.chat import resolve_connection  # chat imports this module

    block = ctx.blocks.get(block_id)
    known = [block["title"], *block["keywords"], *(avoid or [])][:MAX_KEYWORDS * 2]
    connection = resolve_connection(ctx, {"meta": {}})
    provider = ctx.providers.create(connection)
    try:
        llm = LLMClient(provider, model=connection["model"],
                        meter=spending.meter(ctx, connection, purpose="keywords"))
        prompt = KEYWORD_PROMPT.format(title=block["title"], content=block["content"][:6000])
        prompt += KEYWORD_AVOID.format(words=", ".join(known)[:3000])
        value = await llm.generate_json([ChatMessage("user", prompt)])
    finally:
        await provider.aclose()
    if not isinstance(value, list):
        raise ProviderError("The model did not return a list of keywords")
    seen = {_word_key(w) for w in known}
    words = []
    for item in value:
        word = str(item).strip() if isinstance(item, (str, int, float)) else ""
        key = _word_key(word)
        if word and key and key not in seen:
            seen.add(key)
            words.append(word)
    return words[:MAX_KEYWORDS]


# --- meaning vectors --------------------------------------------------------------------------


def block_paths(blocks: list[dict[str, Any]], owner: str) -> dict[str, str]:
    by_id = {b["id"]: b for b in blocks}
    result: dict[str, str] = {}

    def path(block: dict[str, Any]) -> str:
        if block["id"] not in result:
            parent = by_id.get(block["parent_id"])
            result[block["id"]] = f"{path(parent) if parent else owner}{PATH_SEPARATOR}{block['title']}"
        return result[block["id"]]

    for block in blocks:
        path(block)
    return result


def _embed_text(path: str, content: str) -> str:
    """Only the block's own words go into its vector: never system prompts or other data."""
    return f"{path}\n{content}".strip()


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def embedding_connection(ctx: AppContext) -> dict[str, Any] | None:
    connection_id = ctx.settings.get("embedding_connection")
    if not connection_id:
        return None
    try:
        return ctx.connections.get(connection_id)
    except NotFound:
        return None


async def vectorize(ctx: AppContext, owner_ids: list[str]) -> int:
    """Make vectors for green blocks that have none or changed since. Returns how many were made."""
    connection = embedding_connection(ctx)
    if connection is None:
        raise AppError("Choose a connection for meaning search in Settings → Промпт", code="no_embedding_connection")
    model = connection["model"]
    todo: list[tuple[str, str, str]] = []  # block id, text, hash
    for owner_id in owner_ids:
        owner = owner_of(ctx, owner_id)
        blocks = [b for b in ctx.blocks.list(owner_id) if b["mode"] == "search" and b["content"].strip()]
        paths = block_paths(ctx.blocks.list(owner_id), owner["name"])
        stored = ctx.blocks.vectors([b["id"] for b in blocks], model)
        for block in blocks:
            text = _embed_text(paths[block["id"]], block["content"])
            digest = _hash(text)
            if stored.get(block["id"], ("",))[0] != digest:
                todo.append((block["id"], text, digest))
    if not todo:
        return 0
    provider = ctx.providers.create(connection)
    try:
        for start in range(0, len(todo), 64):
            batch = todo[start : start + 64]
            vectors = await provider.embed([t for _, t, _ in batch], model=model)
            for (block_id, _, digest), vector in zip(batch, vectors):
                ctx.blocks.save_vector(block_id, model, digest, vector)
    finally:
        await provider.aclose()
    return len(todo)


async def meaning_scores(ctx: AppContext, blocks: list[dict[str, Any]], query: str) -> dict[str, float]:
    """Similarity of the recent chat to each vectorized green block. Empty without a connection."""
    connection = embedding_connection(ctx)
    if connection is None or not query.strip():
        return {}
    stored = ctx.blocks.vectors([b["id"] for b in blocks if b["mode"] == "search"], connection["model"])
    if not stored:
        return {}
    provider = ctx.providers.create(connection)
    try:
        [query_vector] = await provider.embed([query[-4000:]], model=connection["model"])
    except ProviderError:
        log.warning("Meaning search skipped: the embedding connection failed", exc_info=True)
        return {}
    finally:
        await provider.aclose()
    return {block_id: cosine(query_vector, vector) for block_id, (_, vector) in stored.items()}


# --- choosing blocks for a reply --------------------------------------------------------------


async def select_for_chat(
    ctx: AppContext, chat: dict[str, Any], members: list[Any], history: list[dict[str, Any]]
) -> dict[str, list[FoundBlock]]:
    """character id -> blocks to send with this reply. Records fresh hits for "stays N replies"."""
    settings = retrieval_settings(ctx)
    visible = [m for m in history if not m.get("exclude_from_prompt")]
    scan_text = "\n".join(m["content"] for m in visible[-int(settings["scan_messages"]):])
    meta = dict(ctx.chats.get_chat(chat["id"])["meta"])
    generation = int(meta.get("generation", 0)) + 1
    meta["generation"] = generation
    ctx.chats.update_meta(chat["id"], meta)

    hits, missed = ctx.blocks.hits(chat["id"])
    all_blocks = ctx.blocks.for_owners([m.character_id for m in members])
    links = ctx.blocks.links([m.character_id for m in members])
    result: dict[str, list[FoundBlock]] = {}
    fresh: list[str] = []
    misses: list[str] = []
    for member in members:
        blocks = all_blocks.get(member.character_id, [])
        if not blocks:
            continue
        scores = await meaning_scores(ctx, blocks, scan_text)
        selection = select_blocks(
            blocks, owner_name=member.name, scan_text=scan_text, hits=hits, generation=generation,
            budget_tokens=int(settings["budget_tokens"]), meaning=scores, threshold=float(settings["threshold"]),
            missed=missed, links=links, scan_messages=[m["content"] for m in visible],
            present={m.character_id for m in members},
        )
        result[member.character_id] = selection.found
        fresh.extend(selection.fresh_hits)
        misses.extend(selection.misses)
    if fresh:
        ctx.blocks.record_hits(chat["id"], fresh, generation)
    if misses:
        ctx.blocks.record_hits(chat["id"], misses, generation, missed=True)
    return result


# --- SillyTavern embedded lorebook <-> blocks ---------------------------------------------------

ST_ROLES = {0: "system", 1: "user", 2: "assistant"}
AT_DEPTH = 4  # ST entry position "at depth"


def blocks_from_book(book: Any) -> list[dict[str, Any]]:
    """Entries of a card's character_book as block fields (blue = constant, green = keys/vector)."""
    if not isinstance(book, dict) or not isinstance(book.get("entries"), list):
        return []
    result = []
    for index, entry in enumerate(book["entries"]):
        if not isinstance(entry, dict):
            continue
        ext = entry.get("extensions") if isinstance(entry.get("extensions"), dict) else {}
        keys = [k for k in [*(entry.get("keys") or []), *(entry.get("secondary_keys") or [])] if isinstance(k, str)]
        title = (entry.get("comment") or entry.get("name") or (keys[0] if keys else "") or f"Запись {index + 1}")
        mode = "off" if entry.get("enabled") is False else ("always" if entry.get("constant") else "search")
        depth = ext.get("depth") if ext.get("position") == AT_DEPTH and isinstance(ext.get("depth"), int) else None
        sticky = ext.get("sticky") if isinstance(ext.get("sticky"), int) and ext.get("sticky") > 0 else 3
        chance = ext.get("probability")
        use_chance = ext.get("useProbability", True) and isinstance(chance, int) and 0 <= chance <= 100
        result.append({
            "title": str(title).strip()[:MAX_TITLE] or f"Запись {index + 1}",
            "content": entry.get("content") if isinstance(entry.get("content"), str) else "",
            "mode": mode,
            "keywords": keys[:MAX_KEYWORDS],
            "depth": depth if depth is None or 0 <= depth <= 1000 else None,
            "role": ST_ROLES.get(ext.get("role"), "system"),
            "sticky": min(sticky, 1000),
            "chance": chance if use_chance and mode == "search" else 100,
            "position": index,
            "extra": {"st_entry": entry},
        })
    return result


def is_free(block: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    """A free bubble, or one under a free bubble: not sent, so exported switched off."""
    seen: set[str] = set()
    current: dict[str, Any] | None = block
    while current is not None and current["id"] not in seen:
        if current.get("detached"):
            return True
        seen.add(current["id"])
        current = by_id.get(current["parent_id"])
    return False


def book_from_blocks(blocks: list[dict[str, Any]], owner: str, book_meta: dict[str, Any]) -> dict[str, Any] | None:
    """A character_book for ST export. Nested blocks are flattened; the path goes in the comment."""
    if not blocks:
        return book_meta or None
    paths = block_paths(blocks, owner)
    by_id = {b["id"]: b for b in blocks}
    ordered = sorted(blocks, key=lambda b: paths[b["id"]])
    entries = []
    for uid, block in enumerate(ordered):
        original = dict(block["extra"].get("st_entry") or {})
        ext = dict(original.get("extensions") or {})
        if block["depth"] is not None:
            ext["position"], ext["depth"] = AT_DEPTH, block["depth"]
        ext["role"] = {v: k for k, v in ST_ROLES.items()}[block["role"]]
        ext["sticky"] = block["sticky"]
        ext["probability"], ext["useProbability"] = block["chance"], block["chance"] < 100
        parent_path = paths[block["id"]] if block["parent_id"] in by_id else block["title"]
        original.update({
            "id": original.get("id", uid),
            "keys": block["keywords"] or [block["title"]],
            "secondary_keys": [],
            "comment": parent_path.split(PATH_SEPARATOR, 1)[-1],
            "content": block["content"],
            "constant": block["mode"] == "always",
            "selective": False,
            "enabled": block["mode"] != "off" and not is_free(block, by_id),
            "insertion_order": original.get("insertion_order", 100),
            "extensions": ext,
        })
        entries.append(original)
    return {**{k: v for k, v in (book_meta or {}).items() if k != "entries"}, "entries": entries}


# Our full tree travels inside the card as an extension SillyTavern ignores, next to the flat
# lorebook it reads: nesting, links, chance, free bubbles and map places survive a round trip.
TREE_KEY = "tavern_tree"
TREE_FIELDS = ("title", "content", "mode", "keywords", "depth", "role", "sticky", "chance", "every",
               "detached", "position", "layout", "rules")


def tree_of(ctx: AppContext, owner_id: str) -> dict[str, Any] | None:
    blocks = ctx.blocks.list(owner_id)
    if not blocks:
        return None
    return {
        "version": 1,
        "blocks": [{"id": b["id"], "parent_id": b["parent_id"], **{k: b[k] for k in TREE_FIELDS}} for b in blocks],
        "links": [list(pair) for pair in ctx.blocks.links([owner_id])],
    }


def import_tree(ctx: AppContext, owner_id: str, tree: Any) -> int:
    """Blocks and links from a card's tavern_tree; new ids, parents first. Bad entries are skipped."""
    if not isinstance(tree, dict) or not isinstance(tree.get("blocks"), list):
        return 0
    items = [b for b in tree["blocks"] if isinstance(b, dict) and isinstance(b.get("id"), str)]
    known = {b["id"] for b in items}
    new_ids: dict[str, str] = {}
    waiting = list(items)
    while waiting:
        ready = [b for b in waiting if b.get("parent_id") not in known or b["parent_id"] in new_ids]
        if not ready:  # a loop of parents: hang the rest at the top
            ready = waiting
        for item in ready:
            waiting.remove(item)
            try:
                fields = _clean({k: item[k] for k in TREE_FIELDS if k in item}, creating=True)
            except AppError:
                continue
            fields["parent_id"] = new_ids.get(item.get("parent_id"))
            new_ids[item["id"]] = ctx.blocks.create(owner_id, fields)["id"]
    for pair in tree.get("links") or []:
        if isinstance(pair, list) and len(pair) == 2 and all(p in new_ids for p in pair) and pair[0] != pair[1]:
            ctx.blocks.link(owner_id, new_ids[pair[0]], new_ids[pair[1]])
    return len(new_ids)


def take_book(ctx: AppContext, owner_id: str) -> int:
    """After an import: move the card's tree (ours, or else its embedded lorebook entries) into
    blocks. Returns how many."""
    character = ctx.characters.get(owner_id)
    data = dict(character["data"])
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    if TREE_KEY in extensions:
        made = import_tree(ctx, owner_id, extensions[TREE_KEY])
        data["extensions"] = {k: v for k, v in extensions.items() if k != TREE_KEY}
        if isinstance(data.get("character_book"), dict):
            data["character_book"] = {k: v for k, v in data["character_book"].items() if k != "entries"}
        ctx.characters.update(owner_id, data)
        return made
    fields = blocks_from_book(data.get("character_book"))
    if not fields:
        return 0
    for item in fields:
        ctx.blocks.create(owner_id, item)
    book = data["character_book"]
    data["character_book"] = {k: v for k, v in book.items() if k != "entries"}  # meta stays for export
    ctx.characters.update(owner_id, data)
    return len(fields)


def card_with_book(ctx: AppContext, character: dict[str, Any]) -> dict[str, Any]:
    """Card data for export with the blocks written back as its embedded lorebook."""
    data = dict(character["data"])
    book = book_from_blocks(ctx.blocks.list(character["id"]), character["name"], data.get("character_book") or {})
    if book is None:
        data.pop("character_book", None)
    else:
        data["character_book"] = book
    tree = tree_of(ctx, character["id"])
    if tree is not None:
        extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
        data["extensions"] = {**extensions, TREE_KEY: tree}
    return data
