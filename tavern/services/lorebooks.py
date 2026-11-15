"""Lorebooks (stage 5): books of bubbles that belong to no character.

A book's bubbles are edited with the same functions as a character's tree (services/
character_tree.py, the book id is the owner) and found by the same engine (core/retrieval.py).
Here: the books themselves, where they are switched on, choosing their entries for a reply, and
SillyTavern world files.

Levels, all at once: global (every chat), a chat, a character (wherever it takes part, as a
character of the chat or as the user's own "это я"). A book switched on twice counts once.
Order of the pool (it decides who is dropped first when the budget is short): the chat's books,
then the members' books, then the global ones; inside a book, the bubbles' order numbers.

Settings (global, "lorebooks"): {budget_tokens: 2000, budget_percent: 25 (of the model's window, as in
SillyTavern; 0 = use budget_tokens; budget_tokens also when the window is not known), recursion: 2 (chain steps)}.
Entries are ranked SillyTavern's way (core/retrieval.py `by_order`): book, then the entry's «Order» highest first;
an «ignore budget» entry is never cut. Outlet entries (place "outlet") are never chosen.

Events:
    lorebooks.changed        lorebook_id      a book was created, renamed or deleted
    lorebooks.uses_changed   scope, target    the books of one place changed
    lorebook.blocks_changed  lorebook_id      (from character_tree) its bubbles changed
"""

from __future__ import annotations

from typing import Any

from tavern.context import AppContext
from tavern.core.prompt import substitute
from tavern.core.retrieval import PATH_SEPARATOR, Selection, select_blocks
from tavern.core.retrieval import from_st as is_from_st
from tavern.errors import AppError, NotFound
from tavern.formats import st_lorebook
from tavern.services import cast, character_tree, history

# SillyTavern's defaults (ST_PARITY section 5): scan the last 2 messages with names, recursion off, character books
# before global ones, no minimum activations, no group scoring, no absolute cap.
DEFAULT_SETTINGS: dict[str, Any] = {
    "budget_tokens": 2000, "budget_percent": 25, "budget_cap": 0, "recursion": 0, "scan_messages": 2,
    "include_names": True, "strategy": "character_first", "min_activations": 0, "min_depth_max": 0,
    "group_scoring": False,
}
SETTING_LIMITS = {"budget_tokens": (0, 1_000_000), "budget_percent": (0, 100), "budget_cap": (0, 1_000_000),
                  "recursion": (0, 10), "scan_messages": (0, 1000), "min_activations": (0, 100),
                  "min_depth_max": (0, 1000)}
SETTING_FLAGS = ("include_names", "group_scoring")
STRATEGIES = ("character_first", "global_first", "evenly")
# Tiers of the budget's ranking: the chat's books, the persona's, then characters / global by the strategy.
TIERS = {"character_first": (2, 3), "global_first": (3, 2), "evenly": (2, 2)}
SCOPES = ("global", "chat", "character")
MAX_NAME = 200
MAX_DESCRIPTION = 20_000
MAX_ENTRIES = 20_000


class LorebookError(AppError):
    status = 400
    code = "bad_lorebook"


# --- settings ---------------------------------------------------------------------------------


def clean_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LorebookError("lorebooks settings must be an object")
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if key in SETTING_FLAGS:
            if not isinstance(item, bool):
                raise LorebookError(f"lorebooks.{key} must be true or false")
            clean[key] = item
            continue
        if key == "strategy":
            if item not in STRATEGIES:
                raise LorebookError(f"lorebooks.strategy must be one of {', '.join(STRATEGIES)}")
            clean[key] = item
            continue
        if key not in SETTING_LIMITS:
            raise LorebookError(f"Unknown lorebooks setting {key!r}")
        low, high = SETTING_LIMITS[key]
        if isinstance(item, bool) or not isinstance(item, int) or not low <= item <= high:
            raise LorebookError(f"lorebooks.{key} must be {low}..{high}")
        clean[key] = item
    return clean


def lore_settings(ctx: AppContext) -> dict[str, Any]:
    stored = ctx.settings.get("lorebooks") or {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}


# --- books ------------------------------------------------------------------------------------


def _fields(fields: Any, *, creating: bool) -> dict[str, Any]:
    if not isinstance(fields, dict):
        raise LorebookError("A lorebook must be an object")
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key == "name":
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_NAME:
                raise LorebookError(f"name must be 1..{MAX_NAME} characters")
            clean[key] = value.strip()
        elif key == "description":
            if not isinstance(value, str) or len(value) > MAX_DESCRIPTION:
                raise LorebookError(f"description must be text up to {MAX_DESCRIPTION} characters")
            clean[key] = value
        else:
            raise LorebookError(f"Unknown lorebook field {key!r}")
    if creating and "name" not in clean:
        raise LorebookError("A lorebook needs a name")
    return clean


def list_books(ctx: AppContext) -> list[dict[str, Any]]:
    books = ctx.lorebooks.list()
    for book in books:
        places = ctx.lorebooks.places_of(book["id"])
        book["global"] = any(p["scope"] == "global" for p in places)
        book["chats"] = sum(1 for p in places if p["scope"] == "chat")
        book["characters"] = sum(1 for p in places if p["scope"] == "character")
    return books


async def create_book(ctx: AppContext, fields: Any) -> dict[str, Any]:
    book = ctx.lorebooks.create(_fields(fields, creating=True))
    await ctx.events.emit("lorebooks.changed", lorebook_id=book["id"])
    return book


async def update_book(ctx: AppContext, book_id: str, changes: Any) -> dict[str, Any]:
    old = ctx.lorebooks.get(book_id)
    clean = _fields(changes, creating=False)
    if not clean:
        return old
    book = ctx.lorebooks.update(book_id, clean)
    history.record(ctx, book_id, "Книга изменена", [{"t": "book", "data": {k: old[k] for k in clean}}])
    await ctx.events.emit("lorebooks.changed", lorebook_id=book_id)
    return book


async def delete_book(ctx: AppContext, book_id: str) -> None:
    ctx.lorebooks.delete(book_id)  # its bubbles, uses and history go with it
    await ctx.events.emit("lorebooks.changed", lorebook_id=book_id)


# --- where books are switched on --------------------------------------------------------------


def _check_place(ctx: AppContext, scope: str, target: str | None) -> str | None:
    if scope not in SCOPES:
        raise LorebookError(f"scope must be one of {', '.join(SCOPES)}")
    if scope == "global":
        return None
    if not target:
        raise LorebookError(f"A {scope} is needed")
    if scope == "chat":
        ctx.chats.get_chat(target)
    else:
        ctx.characters.get(target)
    return target


def uses(ctx: AppContext, scope: str, target: str | None = None) -> list[str]:
    return ctx.lorebooks.uses(scope, _check_place(ctx, scope, target))


async def set_uses(ctx: AppContext, scope: str, target: str | None, book_ids: Any) -> list[str]:
    """Switch on exactly these books (in this order) for one place."""
    target = _check_place(ctx, scope, target)
    if not isinstance(book_ids, list) or not all(isinstance(b, str) for b in book_ids):
        raise LorebookError("lorebook_ids must be a list of book ids")
    wanted = list(dict.fromkeys(book_ids))
    for book_id in wanted:
        if not ctx.lorebooks.exists(book_id):
            raise NotFound(f"Lorebook {book_id} not found")
    ctx.lorebooks.set_uses(scope, target, wanted)
    await ctx.events.emit("lorebooks.uses_changed", scope=scope, target=target)
    return wanted


def active_books(ctx: AppContext, chat_id: str, member_ids: list[str], me: str | None = None) -> list[str]:
    """Books working in a chat, each once: the chat's, the persona's (`me`, the user's own character), the other
    members' (in member order), global."""
    return list(book_tiers(ctx, chat_id, member_ids, me))


def book_tiers(ctx: AppContext, chat_id: str, member_ids: list[str], me: str | None = None,
               strategy: str = "character_first") -> dict[str, int]:
    """Book id -> tier of the budget's ranking, in pool order (SillyTavern's): the chat's books 0, the persona's
    1, then the characters' and the global ones by `strategy` (character first: 2 / 3, global first: 3 / 2,
    evenly: both 2, so only «Order» decides between them). A book on in two places counts in the first."""
    characters, global_ = TIERS.get(strategy, TIERS["character_first"])
    found: dict[str, int] = {}
    places = [(ctx.lorebooks.uses("chat", chat_id), 0)]
    if me is not None:
        places.append((ctx.lorebooks.uses("character", me), 1))
    places += [(ctx.lorebooks.uses("character", m), characters) for m in member_ids if m != me]
    places.append((ctx.lorebooks.uses("global"), global_))
    for books, tier in places:
        for book_id in books:
            found.setdefault(book_id, tier)
    return found


def _me(members: list[Any]) -> str | None:
    return next((m.character_id for m in members if getattr(m, "is_me", False)), None)


def scan_lines(history_: list[dict[str, Any]], *, names: bool, user: str = "") -> list[str]:
    """The visible messages as the lorebook scan reads them, oldest first: «Name: text» (SillyTavern's «include
    names», on by default), or the bare text."""
    lines = []
    for message in history_:
        if message.get("exclude_from_prompt"):
            continue
        name = (message.get("name") or (user if message.get("role") == "user" else "")).strip()
        lines.append(f"{name}: {message['content']}" if names and name else message["content"])
    return lines


def scan_sources(members: list[Any]) -> dict[str, str]:
    """Texts an entry may look in besides the messages (its rule `match`): the speaker's card fields and the
    persona's description."""
    primary = next((m for m in members if getattr(m, "primary", False)), None)
    me = next((m for m in members if getattr(m, "is_me", False)), None)
    card = primary.card if primary is not None else {}
    extensions = card.get("extensions") if isinstance(card.get("extensions"), dict) else {}
    depth_prompt = extensions.get("depth_prompt") if isinstance(extensions.get("depth_prompt"), dict) else {}
    found = {
        "persona": (me.card.get("description") if me is not None else "") or "",
        "description": card.get("description") or "",
        "personality": card.get("personality") or "",
        "scenario": card.get("scenario") or "",
        "creator_notes": card.get("creator_notes") or "",
        "depth_prompt": depth_prompt.get("prompt") or "",
    }
    return {k: v for k, v in found.items() if isinstance(v, str) and v.strip()}


def _with_key_macros(blocks: list[dict[str, Any]], *, char: str, user: str) -> list[dict[str, Any]]:
    """Macros in keywords ({{char}}, {{user}} …) are filled before the search, as SillyTavern does; the stored
    bubbles stay as written."""
    def fill(words: list[str]) -> list[str]:
        return [substitute(w, char=char, user=user) if "{{" in w or "<" in w else w for w in words]

    result = []
    for block in blocks:
        rules = block.get("rules") or {}
        words = list(block.get("keywords") or []) + list(rules.get("keys2") or [])
        if not any("{{" in w or "<" in w for w in words):
            result.append(block)
            continue
        changed = {**block, "keywords": fill(list(block.get("keywords") or []))}
        if rules.get("keys2"):
            changed["rules"] = {**rules, "keys2": fill(list(rules["keys2"]))}
        result.append(changed)
    return result


# --- choosing entries for a reply ---------------------------------------------------------------


async def meaning_for_reply(
    ctx: AppContext, chat: dict[str, Any], members: list[Any], history_: list[dict[str, Any]],
    *, wanted: bool = False,
) -> character_tree.Meaning:
    """Meaning for a whole reply: the members' cards and the chat's books together, ONE embedding request.
    Returns a `Meaning` (.scores by block id, .queries the query vectors, .error text for the page or None). Pass
    `.scores` to both select_for_chat functions. `wanted`: a plugin needs the query vectors even when no block
    has a vector (the filter event `meaning.wanted`, asked by the chat service)."""
    owners = [m.character_id for m in members] + active_books(ctx, chat["id"], [m.character_id for m in members])
    pool = ctx.blocks.for_owners(list(dict.fromkeys(owners)))
    every = [b for blocks in pool.values() for b in blocks]
    return await character_tree.meaning_search(
        ctx, every, character_tree.meaning_texts(character_tree.retrieval_settings(ctx), history_), wanted=wanted)


async def select_for_chat(
    ctx: AppContext, chat: dict[str, Any], members: list[Any], history_: list[dict[str, Any]],
    *, max_context: int = 0, seed_text: str = "", meaning: dict[str, float] | None = None,
    kind: str = "normal",
) -> Selection:
    """Entries of the chat's books for this reply. Call after character_tree.select_for_chat: it
    counts the reply (the chat's generation number) that both use. `seed_text`: the text of the
    characters' bubbles going with this reply, so chains can start from them too. `meaning`: scores made once
    for the reply by meaning_for_reply; without it they are made here. `kind`: the kind of generation (normal,
    continue, impersonate, swipe, regenerate, quiet) for entries with triggers."""
    lore = lore_settings(ctx)
    me = _me(members)
    tiers = book_tiers(ctx, chat["id"], [m.character_id for m in members], me, lore["strategy"])
    book_ids = list(tiers)
    if not book_ids:
        return Selection(found=[])
    pool = ctx.blocks.for_owners(book_ids)
    blocks = [b for book_id in book_ids for b in pool[book_id] if (b.get("rules") or {}).get("place") != "outlet"]
    if not blocks:
        return Selection(found=[])
    retrieval = character_tree.retrieval_settings(ctx)
    user = next((m.name for m in members if getattr(m, "is_me", False)), "") or cast.user_name(ctx, chat["id"])
    char = next((m.name for m in members if getattr(m, "primary", False)), "")
    messages = scan_lines(history_, names=bool(lore["include_names"]), user=user)
    depth = int(lore["scan_messages"])
    scan_text = "\n".join(messages[-depth:]) if depth > 0 else ""
    generation = int(ctx.chats.get_chat(chat["id"])["meta"].get("generation", 0))
    clock = len(history_)  # SillyTavern counts timers of its entries in chat messages
    budget = lore["budget_tokens"]
    if lore["budget_percent"] and max_context:
        budget = max_context * lore["budget_percent"] // 100
    if lore["budget_cap"]:
        budget = min(budget, lore["budget_cap"]) if budget else lore["budget_cap"]
    hits, missed = ctx.blocks.hits(chat["id"])
    if meaning is None:
        meaning = (await character_tree.meaning_search(
            ctx, blocks, character_tree.meaning_texts(retrieval, history_))).scores
    selection = select_blocks(
        _with_key_macros(blocks, char=char, user=user), owner_name="", scan_text=scan_text,
        scan_messages=messages, hits=hits, generation=generation, budget_tokens=budget,
        meaning=meaning,
        threshold=float(retrieval["threshold"]), missed=missed, links=ctx.blocks.links(book_ids),
        recursion=lore["recursion"], seed_text=seed_text, present={m.character_id for m in members},
        owner_names={book_id: ctx.lorebooks.get(book_id)["name"] for book_id in book_ids}, by_order=True,
        sources=scan_sources(members), kind=kind, clock=clock, group_scoring=bool(lore["group_scoring"]),
        tiers=tiers, min_found=int(lore["min_activations"]), scan_depth=depth,
        min_depth_max=int(lore["min_depth_max"]),
    )
    from_st = {b["id"] for b in blocks if is_from_st(b)}

    def record(ids: list[str], missed_: bool = False) -> None:
        # each bubble keeps one clock: SillyTavern's entries the message count, ours the reply number
        st_ids = [b for b in ids if b in from_st]
        own = [b for b in ids if b not in from_st]
        if st_ids:
            ctx.blocks.record_hits(chat["id"], st_ids, clock, missed=missed_)
        if own:
            ctx.blocks.record_hits(chat["id"], own, generation, missed=missed_)

    record(selection.fresh_hits)
    record(selection.misses, True)
    return selection


# --- SillyTavern world files ------------------------------------------------------------------


def _ids_by_name(ctx: AppContext):
    by_name = {c["name"].casefold(): c["id"] for c in ctx.characters.list()}

    def ids(names: list[str]) -> list[str]:
        return [by_name[n.casefold()] for n in names if n.casefold() in by_name]
    return ids


def _names_by_id(ctx: AppContext):
    by_id = {c["id"]: c["name"] for c in ctx.characters.list()}

    def names(ids: list[str]) -> list[str]:
        return [by_id[i] for i in ids if i in by_id]
    return names


async def import_book(ctx: AppContext, data: Any, name: str = "") -> dict[str, Any]:
    """A lorebook file: SillyTavern's world file, a card's embedded book, or our own export (it
    carries the whole tree, so nesting and links come back). Returns the new book."""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), (dict, list)):
        raise LorebookError("This is not a lorebook file (no entries)")
    inside = data.get("name").strip() if isinstance(data.get("name"), str) else ""
    title = inside or name.strip() or "Лорбук"
    meta = {k: v for k, v in data.items() if k not in ("entries", "name", "description", character_tree.TREE_KEY)}
    book = ctx.lorebooks.create({
        "name": title.strip()[:MAX_NAME] or "Лорбук",
        "description": data.get("description") if isinstance(data.get("description"), str) else "",
        "extra": meta,
    })
    tree = data.get(character_tree.TREE_KEY)
    if isinstance(tree, dict):
        character_tree.import_tree(ctx, book["id"], tree)
    else:
        if st_lorebook.is_world(data):
            entries = st_lorebook.entries_of(data)[:MAX_ENTRIES]
            ids = _ids_by_name(ctx)
            fields = [st_lorebook.block_from_entry(e, i, ids) for i, e in enumerate(entries)]
        else:
            fields = character_tree.blocks_from_book(data)[:MAX_ENTRIES]
        with ctx.db.transaction():
            for item in fields:
                ctx.blocks.create(book["id"], item)
    await ctx.events.emit("lorebooks.changed", lorebook_id=book["id"])
    character_tree.schedule_auto_vectors(ctx, book["id"])
    return ctx.lorebooks.get(book["id"])


def export_book(ctx: AppContext, book_id: str) -> dict[str, Any]:
    """A SillyTavern world file of the book (nesting flattened, the path in the comment), with our
    whole tree beside it for a lossless trip back into Tavern."""
    book = ctx.lorebooks.get(book_id)
    blocks = ctx.blocks.list(book_id)
    by_id = {b["id"]: b for b in blocks}
    paths = character_tree.block_paths(blocks, book["name"])
    names = _names_by_id(ctx)
    entries: dict[str, Any] = {}
    for uid, block in enumerate(sorted(blocks, key=lambda b: paths[b["id"]])):
        comment = paths[block["id"]].split(PATH_SEPARATOR, 1)[-1]
        enabled = block["mode"] != "off" and not character_tree.is_free(block, by_id)
        entries[str(uid)] = st_lorebook.entry_from_block(block, uid, comment, enabled, names)
    result: dict[str, Any] = {**book["extra"], "name": book["name"], "description": book["description"],
                              "entries": entries}
    tree = character_tree.tree_of(ctx, book_id)
    if tree is not None:
        result[character_tree.TREE_KEY] = tree
    return result


def attach_card_world(ctx: AppContext, character_id: str) -> str | None:
    """A SillyTavern card names its lorebook in extensions.world: if we have a book of that name,
    switch it on for the character. Returns the book id."""
    data = ctx.characters.get(character_id)["data"]
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    world = extensions.get("world")
    if not isinstance(world, str) or not world.strip():
        return None
    book = ctx.lorebooks.find_by_name(world.strip())
    if book is None:
        return None
    current = ctx.lorebooks.uses("character", character_id)
    if book["id"] not in current:
        ctx.lorebooks.set_uses("character", character_id, [*current, book["id"]])
    return book["id"]
