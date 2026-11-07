"""What the assistant can do (stage 8б): tools over the same services the interface uses.

A tool gets checked arguments and returns an Outcome: `result` goes back to the model as data;
a change also has `what` (the journal line the user sees) and `undo` (storage/snapshot.py rows,
plus "uses": lorebook places to set back). Tools with `confirm` ask the user first (deleting).

Secrets have no tool. Text read from cards and books reaches the model marked as data
(services/assistant.py), never as instructions.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tavern.context import AppContext
from tavern.core.retrieval import select_blocks
from tavern.errors import AppError
from tavern.formats.cards import TEXT_FIELDS, parse_card
from tavern.services import character_tree, history, lorebooks
from tavern.storage import snapshot

MAX_FIELD = 20_000  # characters of one card field / bubble sent back to the model
PREVIEW = 160

CARD_FIELDS = (*TEXT_FIELDS, "alternate_greetings", "tags")
BLOCK_FIELDS = ("title", "content", "mode", "keywords", "parent_id", "depth", "role", "chance", "detached")


class ToolError(AppError):
    """A tool was called wrongly; the text goes back to the model so it can correct itself."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="assistant_tool")


@dataclass
class Env:
    ctx: AppContext
    chat_id: str | None = None  # the chat open on the page, if any
    # Links made by this request, per owner (the engine keeps it on the task, so it holds for the whole
    # work), and what the user wrote: only the user can ask for more links than the usual limit.
    link_counts: dict[str, int] = field(default_factory=dict)
    asked_for: str = ""
    # Asks the person «Разрешить?» in the middle of a tool (install_skill shows what it downloaded first);
    # the engine sets it, "auto" mode answers yes. None: the tool runs without a person (tests of the tool alone).
    ask: Callable[[str], Awaitable[bool]] | None = None


@dataclass
class Outcome:
    result: Any
    what: str = ""  # empty: nothing changed, nothing to journal
    undo: dict[str, Any] = field(default_factory=snapshot.empty)
    changed: tuple[str, ...] = ()  # what the page should reload: characters, lorebooks, blocks


@dataclass
class Tool:
    name: str
    description: str
    args: dict[str, str]
    run: Callable[[Env, dict[str, Any]], Awaitable[Outcome]]
    confirm: Callable[[Env, dict[str, Any]], str] | None = None
    required: tuple[str, ...] = ()


# --- helpers ----------------------------------------------------------------------------------


def _text(args: dict[str, Any], key: str, *, required: bool = True) -> str:
    value = args.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(f"Argument {key!r} must be non-empty text")
    return value


def _dict(args: dict[str, Any], key: str) -> dict[str, Any]:
    value = args.get(key)
    if not isinstance(value, dict) or not value:
        raise ToolError(f"Argument {key!r} must be an object with the fields to change")
    return value


def _cut(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD:
        return value[:MAX_FIELD] + f"… [cut, {len(value)} characters in all]"
    return value


def _preview(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW else text[:PREVIEW] + "…"


def _card_changes(changes: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in changes if k not in (*CARD_FIELDS, "name")]
    if unknown:
        raise ToolError(f"Unknown card fields {unknown}; known: name, {', '.join(CARD_FIELDS)}")
    return changes


def _character(env: Env, character_id: Any) -> dict[str, Any]:
    if not isinstance(character_id, str):
        raise ToolError("Argument 'id' must be a character id")
    return env.ctx.characters.get(character_id)


def _book(env: Env, book_id: Any) -> dict[str, Any]:
    if not isinstance(book_id, str) or not env.ctx.lorebooks.exists(book_id):
        raise ToolError(f"No lorebook with id {book_id!r}")
    return env.ctx.lorebooks.get(book_id)


def _block_view(block: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    view = {"id": block["id"], "title": block["title"], "mode": block["mode"], "keywords": block["keywords"],
            "parent_id": block["parent_id"], "free": block["detached"]}
    if full:
        view.update(content=_cut(block["content"]), depth=block["depth"], role=block["role"],
                    chance=block["chance"])
    else:
        view["preview"] = _preview(block["content"])
    return view


def _owner_name(env: Env, owner_id: str) -> str:
    return f"«{character_tree.owner_of(env.ctx, owner_id)['name']}»"


# --- characters -------------------------------------------------------------------------------


async def list_characters(env: Env, args: dict[str, Any]) -> Outcome:
    return Outcome([{"id": c["id"], "name": c["name"]} for c in env.ctx.characters.list()])


async def read_character(env: Env, args: dict[str, Any]) -> Outcome:
    character = _character(env, args.get("id"))
    data = character["data"]
    card = {key: _cut(data.get(key, "")) for key in CARD_FIELDS}
    books = [env.ctx.lorebooks.get(b)["name"] for b in env.ctx.lorebooks.uses("character", character["id"])]
    return Outcome({"id": character["id"], "name": character["name"], **card,
                    "bubbles": len(env.ctx.blocks.list(character["id"])), "lorebooks": books})


async def create_character(env: Env, args: dict[str, Any]) -> Outcome:
    _card_changes({k: v for k, v in args.items() if k != "name"})
    spec, data = parse_card(dict(args))
    character = env.ctx.characters.create(data, spec=spec)
    undo = snapshot.created(snapshot.empty(), "characters", {"id": character["id"]})
    return Outcome({"id": character["id"], "name": character["name"]},
                   f"Создан персонаж «{character['name']}»", undo, ("characters",))


FIELD_NAMES = {"name": "имя", "description": "описание", "personality": "характер", "scenario": "сценарий",
               "first_mes": "приветствие", "mes_example": "примеры диалога", "alternate_greetings": "приветствия",
               "tags": "метки", "system_prompt": "системный промпт", "creator_notes": "заметки автора"}


async def update_character(env: Env, args: dict[str, Any]) -> Outcome:
    character = _character(env, args.get("id"))
    changes = _card_changes(_dict(args, "changes"))
    _, data = parse_card({**character["data"], **changes})
    undo = snapshot.capture(env.ctx.db, "characters", {"id": character["id"]})
    env.ctx.characters.update(character["id"], data)
    history.record(env.ctx, character["id"], "Помощник: карточка изменена",
                   [{"t": "card", "data": character["data"]}])
    fields = ", ".join(FIELD_NAMES.get(k, k) for k in changes)
    return Outcome({"ok": True}, f"Персонаж «{character['name']}»: изменено {fields}", undo, ("characters",))


def _ask_delete_character(env: Env, args: dict[str, Any]) -> str:
    return f"Удалить персонажа «{_character(env, args.get('id'))['name']}» вместе с его пузырями?"


async def delete_character(env: Env, args: dict[str, Any]) -> Outcome:
    character = _character(env, args.get("id"))
    undo = snapshot.capture(env.ctx.db, "characters", {"id": character["id"]}, cascade=True)
    env.ctx.characters.delete(character["id"])
    return Outcome({"ok": True}, f"Удалён персонаж «{character['name']}»", undo, ("characters", "blocks"))


# --- bubbles (of a character or a lorebook) --------------------------------------------------


LIST_PAGE = 40  # entries one list_bubbles answer carries: a longer list is paged, never cut silently


def _int_arg(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError(f"Argument {key!r} must be a whole number")
    return max(low, min(high, value))


async def list_bubbles(env: Env, args: dict[str, Any]) -> Outcome:
    owner_id = _text(args, "owner_id")
    blocks = character_tree.list_blocks(env.ctx, owner_id)
    offset = _int_arg(args, "offset", 0, 0, len(blocks))
    limit = _int_arg(args, "limit", LIST_PAGE, 1, 100)
    if len(blocks) <= limit and offset == 0:
        return Outcome([_block_view(b) for b in blocks])
    page = blocks[offset:offset + limit]
    out: dict[str, Any] = {"total": len(blocks),
                           "showing": f"{offset + 1}-{offset + len(page)}" if page else "none",
                           "entries": [_block_view(b) for b in page]}
    if offset + limit < len(blocks):
        out["more"] = (f"{len(blocks) - offset - limit} more entries are NOT shown. Call list_bubbles again with "
                       f"offset={offset + limit}. For a job over every entry, use begin_sweep.")
    return Outcome(out)


async def read_bubble(env: Env, args: dict[str, Any]) -> Outcome:
    block = env.ctx.blocks.get(_text(args, "id"))
    view = _block_view(block, full=True)
    view["linked_to"] = _linked(env, block["owner_id"], block["id"])
    return Outcome(view)


def _linked(env: Env, owner_id: str, block_id: str) -> list[dict[str, str]]:
    titles = {b["id"]: b["title"] for b in env.ctx.blocks.list(owner_id)}
    out = []
    for a, b in env.ctx.blocks.links([owner_id]):
        if block_id in (a, b):
            other = b if a == block_id else a
            out.append({"id": other, "title": titles.get(other, "")})
    return out


def _block_fields(fields: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in fields if k not in BLOCK_FIELDS]
    if unknown:
        raise ToolError(f"Unknown bubble fields {unknown}; known: {', '.join(BLOCK_FIELDS)}")
    return fields


async def create_bubble(env: Env, args: dict[str, Any]) -> Outcome:
    owner_id = _text(args, "owner_id")
    fields = _block_fields({k: v for k, v in args.items() if k != "owner_id"})
    block = await character_tree.create_block(env.ctx, owner_id, fields)
    undo = snapshot.created(snapshot.empty(), "character_blocks", {"id": block["id"]})
    return Outcome({"id": block["id"]}, f"{_owner_name(env, owner_id)}: новый пузырь «{block['title']}»",
                   undo, ("blocks",))


async def update_bubble(env: Env, args: dict[str, Any]) -> Outcome:
    block = env.ctx.blocks.get(_text(args, "id"))
    changes = _block_fields(_dict(args, "changes"))
    undo = snapshot.capture(env.ctx.db, "character_blocks", {"id": block["id"]})
    await character_tree.update_block(env.ctx, block["id"], changes)
    return Outcome({"ok": True}, f"{_owner_name(env, block['owner_id'])}: пузырь «{block['title']}» изменён",
                   undo, ("blocks",))


def _ask_delete_bubble(env: Env, args: dict[str, Any]) -> str:
    block = env.ctx.blocks.get(_text(args, "id"))
    return f"Удалить пузырь «{block['title']}» ({_owner_name(env, block['owner_id'])}) с вложенными?"


async def delete_bubble(env: Env, args: dict[str, Any]) -> Outcome:
    block = env.ctx.blocks.get(_text(args, "id"))
    undo = snapshot.capture(env.ctx.db, "character_blocks", {"id": block["id"]}, cascade=True)
    await character_tree.delete_block(env.ctx, block["id"])
    return Outcome({"ok": True}, f"{_owner_name(env, block['owner_id'])}: удалён пузырь «{block['title']}»",
                   undo, ("blocks",))


# --- links between bubbles --------------------------------------------------------------------
#
# A link makes the search bring the partner whenever one of the two is found, so every link adds
# text to many replies. A model that is allowed to link "whatever is related" links everything.
# So the limit lives here, in code, not only in the skill.

MIN_LINKS = 3  # even a tiny book may have a few
LINKS_PER_ENTRY = 0.12  # about one link per eight entries: an average entry then has 0.25 links
MAX_PER_ENTRY = 2  # no entry becomes a hub that drags half the book along
MANY_RE = re.compile(r"как можно больше|по максимуму|максимум связ|все возможные связ|много связ|"
                     r"as many (?:links|connections)|many links|lots of links|every possible link|"
                     r"link everything|свяжи всё|свяжи все", re.IGNORECASE)
NUMBER_RE = re.compile(r"(\d+)\s*(?:новых\s+)?(?:связ\w*|ссыл\w*|links?|connections?)", re.IGNORECASE)


def link_limits(entries: int, asked_for: str = "") -> tuple[int, int]:
    """(links one request may make in a book, links one entry may have). Sized to the book: about one link
    per eight entries (100 entries: 12 links), at least 3; two per entry. Only the user's own words raise
    it: a number ("20 связей") or "as many as possible" (four times the usual, at least 12)."""
    base = max(MIN_LINKS, math.ceil(entries * LINKS_PER_ENTRY))
    match = NUMBER_RE.search(asked_for or "")
    if match:
        return max(1, int(match.group(1))), MAX_PER_ENTRY + 2
    if MANY_RE.search(asked_for or ""):
        return max(12, base * 4), MAX_PER_ENTRY + 2
    return base, MAX_PER_ENTRY


def _pair(env: Env, args: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    owner_id = _text(args, "owner_id")
    a, b = (env.ctx.blocks.get(_text(args, k)) for k in ("a", "b"))
    return owner_id, a, b


async def list_links(env: Env, args: dict[str, Any]) -> Outcome:
    owner_id = _text(args, "owner_id")
    character_tree.owner_of(env.ctx, owner_id)
    titles = {b["id"]: b["title"] for b in env.ctx.blocks.list(owner_id)}
    pairs = env.ctx.blocks.links([owner_id])
    cap, per = link_limits(len(titles), env.asked_for)
    return Outcome({"links": [{"a": a, "b": b, "titles": [titles.get(a, ""), titles.get(b, "")]} for a, b in pairs],
                    "this_request_may_still_make": max(0, cap - env.link_counts.get(owner_id, 0)),
                    "limit_per_request": cap, "limit_per_entry": per})


async def link_bubbles(env: Env, args: dict[str, Any]) -> Outcome:
    owner_id, a, b = _pair(env, args)
    reason = _text(args, "reason").strip()
    if len(reason) < 12:
        raise ToolError("Argument 'reason' must say in a sentence why one of these entries cannot be understood "
                        "without the other")
    character_tree.owner_of(env.ctx, owner_id)
    if a["id"] == b["id"]:
        raise ToolError("An entry cannot link to itself")
    if {a["owner_id"], b["owner_id"]} != {owner_id}:
        raise ToolError("Both entries must belong to the owner given in owner_id")
    if a.get("parent_id") == b["id"] or b.get("parent_id") == a["id"]:
        # Owner's rule: a parent and its direct child already travel together, a link adds nothing, even if asked.
        raise ToolError(f"«{a['title']}» and «{b['title']}» are parent and child in the tree, they already go "
                        "together. Never link an entry with its direct parent. Choose another pair, or stop "
                        "linking and say so in the final answer.")
    pairs = env.ctx.blocks.links([owner_id])
    if tuple(sorted((a["id"], b["id"]))) in pairs:
        return Outcome({"ok": True, "note": "already linked"})
    cap, per = link_limits(len(env.ctx.blocks.list(owner_id)), env.asked_for)
    made = env.link_counts.get(owner_id, 0)
    if made >= cap:
        raise ToolError(
            f"Link limit reached: this request already made {made} of {cap} links in this book. Do not make "
            "more and do not try another way. A link is only for entries that cannot be understood without each "
            "other. In your final answer tell the user you stopped at the limit and that more can be made if they "
            "ask for more.")
    for block in (a, b):
        degree = sum(1 for pair in pairs if block["id"] in pair)
        if degree >= per:
            raise ToolError(f"«{block['title']}» already has {degree} links (the most one entry may have is {per}). "
                            "Choose another pair.")
    await character_tree.link_blocks(env.ctx, owner_id, a["id"], b["id"])
    env.link_counts[owner_id] = made + 1
    x, y = sorted((a["id"], b["id"]))
    undo = snapshot.created(snapshot.empty(), "block_links", {"a": x, "b": y})
    return Outcome({"ok": True, "links_made_in_this_request": made + 1, "limit": cap},
                   f"{_owner_name(env, owner_id)}: связь «{a['title']}» ↔ «{b['title']}» — {_preview(reason)}",
                   undo, ("blocks",))


async def unlink_bubbles(env: Env, args: dict[str, Any]) -> Outcome:
    owner_id, a, b = _pair(env, args)
    x, y = sorted((a["id"], b["id"]))
    if (x, y) not in env.ctx.blocks.links([owner_id]):
        return Outcome({"ok": True, "note": "they were not linked"})
    undo = snapshot.capture(env.ctx.db, "block_links", {"a": x, "b": y})
    await character_tree.unlink_blocks(env.ctx, owner_id, a["id"], b["id"])
    return Outcome({"ok": True}, f"{_owner_name(env, owner_id)}: связь «{a['title']}» ↔ «{b['title']}» убрана",
                   undo, ("blocks",))


class _Lucky:
    """Every chance passes: a trial phrase shows what can come, not one roll of the dice."""

    def random(self) -> float:
        return 0.0


async def try_phrase(env: Env, args: dict[str, Any]) -> Outcome:
    """The bubbles one chat message would bring, by the same search as a real reply; nothing is kept."""
    owner_id = _text(args, "owner_id")
    text = _text(args, "text")
    owner = character_tree.owner_of(env.ctx, owner_id)
    blocks = env.ctx.blocks.for_owners([owner_id]).get(owner_id, [])
    is_book = env.ctx.lorebooks.exists(owner_id)
    settings = character_tree.retrieval_settings(env.ctx)
    lore = lorebooks.lore_settings(env.ctx)
    selection = select_blocks(
        blocks, owner_name=owner["name"], scan_text=text, scan_messages=[text],
        budget_tokens=lore["budget_tokens"] if is_book else int(settings["budget_tokens"]),
        meaning=await character_tree.meaning_scores(env.ctx, blocks, text), threshold=float(settings["threshold"]),
        links=env.ctx.blocks.links([owner_id]), rng=_Lucky(), recursion=lore["recursion"] if is_book else 0,
        owner_names={owner_id: owner["name"]} if is_book else None, by_order=is_book,
    )
    found = {f.block["id"] for f in selection.found}
    asleep = [b["title"] for b in blocks if b["mode"] == "search" and b["id"] not in found]
    return Outcome({"text": text,
                    "found": [{"title": f.block["title"], "path": f.path, "why": f.reason} for f in selection.found],
                    "not_found": asleep[:60], "over_budget": selection.dropped})


# --- lorebooks --------------------------------------------------------------------------------


async def list_lorebooks(env: Env, args: dict[str, Any]) -> Outcome:
    here = set(env.ctx.lorebooks.uses("chat", env.chat_id)) if env.chat_id else set()
    return Outcome([{"id": b["id"], "name": b["name"], "description": _preview(b["description"]),
                     "entries": b["blocks"], "everywhere": b["global"], "in_open_chat": b["id"] in here}
                    for b in lorebooks.list_books(env.ctx)])


async def create_lorebook(env: Env, args: dict[str, Any]) -> Outcome:
    fields = {"name": _text(args, "name"), "description": _text(args, "description", required=False)}
    book = await lorebooks.create_book(env.ctx, fields)
    undo = snapshot.created(snapshot.empty(), "lorebooks", {"id": book["id"]})
    return Outcome({"id": book["id"]}, f"Создан лорбук «{book['name']}»", undo, ("lorebooks",))


async def update_lorebook(env: Env, args: dict[str, Any]) -> Outcome:
    book = _book(env, args.get("id"))
    changes = _dict(args, "changes")
    undo = snapshot.capture(env.ctx.db, "lorebooks", {"id": book["id"]})
    await lorebooks.update_book(env.ctx, book["id"], changes)
    return Outcome({"ok": True}, f"Лорбук «{book['name']}» изменён", undo, ("lorebooks",))


def _ask_delete_lorebook(env: Env, args: dict[str, Any]) -> str:
    return f"Удалить лорбук «{_book(env, args.get('id'))['name']}» со всеми записями?"


async def delete_lorebook(env: Env, args: dict[str, Any]) -> Outcome:
    book = _book(env, args.get("id"))
    undo = snapshot.capture(env.ctx.db, "lorebooks", {"id": book["id"]}, cascade=True)
    await lorebooks.delete_book(env.ctx, book["id"])
    return Outcome({"ok": True}, f"Удалён лорбук «{book['name']}»", undo, ("lorebooks", "blocks"))


async def switch_lorebook(env: Env, args: dict[str, Any]) -> Outcome:
    book = _book(env, args.get("id"))
    where = args.get("where")
    on = args.get("on", True)
    if not isinstance(on, bool):
        raise ToolError("Argument 'on' must be true or false")
    if where == "chat":
        if not env.chat_id:
            raise ToolError("No chat is open")
        target, label = env.chat_id, "в этом чате"
    elif where == "character":
        character = _character(env, args.get("character_id"))
        target, label = character["id"], f"у персонажа «{character['name']}»"
    elif where == "everywhere":
        target, label = None, "везде"
    else:
        raise ToolError("Argument 'where' must be chat, character or everywhere")
    scope = {"chat": "chat", "character": "character", "everywhere": "global"}[where]
    before = lorebooks.uses(env.ctx, scope, target)
    after = [b for b in before if b != book["id"]] + ([book["id"]] if on else [])
    if after == before:
        return Outcome({"ok": True, "note": "already so"})
    await lorebooks.set_uses(env.ctx, scope, target, after)
    undo = {**snapshot.empty(), "uses": [{"scope": scope, "target": target, "ids": before}]}
    return Outcome({"ok": True}, f"Лорбук «{book['name']}» {'включён' if on else 'выключен'} {label}",
                   undo, ("lorebooks",))


# --- the list the model sees ------------------------------------------------------------------

CARD_ARGS = ("description, personality, scenario, first_mes (greeting), mes_example, alternate_greetings (list), "
             "tags (list), system_prompt, post_history_instructions, creator_notes")

TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("list_characters", "All characters: id and name.", {}, list_characters),
    Tool("read_character", "A character's card fields, bubble count and lorebooks.", {"id": "character id"},
         read_character, required=("id",)),
    Tool("create_character", "Make a new character.",
         {"name": "name", "...": f"any card fields: {CARD_ARGS}"}, create_character, required=("name",)),
    Tool("update_character", "Change card fields of a character (only those given).",
         {"id": "character id", "changes": f"object of fields to set: name, {CARD_ARGS}"},
         update_character, required=("id", "changes")),
    Tool("delete_character", "Delete a character (the user is asked first).", {"id": "character id"},
         delete_character, _ask_delete_character, ("id",)),
    Tool("list_bubbles", "Bubbles (the knowledge tree) of a character, or the entries of a lorebook. A long list "
         f"comes in pages of {LIST_PAGE}: the answer says how many are not shown and the offset for the next page.",
         {"owner_id": "character id or lorebook id", "offset": "optional, start of the page",
          "limit": "optional, page size (at most 100)"}, list_bubbles, required=("owner_id",)),
    Tool("read_bubble", "One bubble in full, with the entries it is linked to.", {"id": "bubble id"}, read_bubble,
         required=("id",)),
    Tool("list_links", "The links between the bubbles of a character or the entries of a lorebook, and how many "
         "more links this request may still make.", {"owner_id": "character id or lorebook id"}, list_links,
         required=("owner_id",)),
    Tool("link_bubbles", "Link two bubbles of ONE owner (a character, or one lorebook): when one is found by the "
         "search the other goes to the model too. Only for a pair where one cannot be understood without the "
         "other. Tavern limits the number of links per request, sized to the book.",
         {"owner_id": "character id or lorebook id that both belong to", "a": "bubble id", "b": "bubble id",
          "reason": "one sentence: why one cannot be understood without the other"},
         link_bubbles, required=("owner_id", "a", "b", "reason")),
    Tool("unlink_bubbles", "Remove the link between two bubbles.",
         {"owner_id": "character id or lorebook id", "a": "bubble id", "b": "bubble id"}, unlink_bubbles,
         required=("owner_id", "a", "b")),
    Tool("create_bubble", "Add a bubble to a character, or an entry to a lorebook.",
         {"owner_id": "character id or lorebook id", "title": "short title", "content": "the text",
          "mode": "always (always sent; under a search bubble: sent whenever that one is) | search (sent when its own keywords appear, default; a parent never brings it) | off",
          "keywords": "list of words that wake it", "parent_id": "optional parent bubble id"},
         create_bubble, required=("owner_id", "title")),
    Tool("update_bubble", "Change a bubble.",
         {"id": "bubble id", "changes": "object: title, content, mode, keywords, parent_id, depth, role, chance"},
         update_bubble, required=("id", "changes")),
    Tool("delete_bubble", "Delete a bubble with its sub-bubbles (the user is asked first).", {"id": "bubble id"},
         delete_bubble, _ask_delete_bubble, ("id",)),
    Tool("try_phrase", "Test the keywords: which bubbles of a character or entries of a lorebook one chat "
         "message would bring (the same search as a real reply, every chance passing; nothing is kept). "
         "\"not_found\" lists the keyword bubbles that stayed asleep.",
         {"owner_id": "character id or lorebook id", "text": "a message as a player would write it"},
         try_phrase, required=("owner_id", "text")),
    Tool("list_lorebooks", "All lorebooks: id, name, entry count, where they are on.", {}, list_lorebooks),
    Tool("create_lorebook", "Make an empty lorebook; then add entries with create_bubble.",
         {"name": "name", "description": "optional"}, create_lorebook, required=("name",)),
    Tool("update_lorebook", "Rename a lorebook or change its description.",
         {"id": "lorebook id", "changes": "object: name, description"}, update_lorebook, required=("id", "changes")),
    Tool("delete_lorebook", "Delete a lorebook with its entries (the user is asked first).",
         {"id": "lorebook id"}, delete_lorebook, _ask_delete_lorebook, ("id",)),
    Tool("switch_lorebook", "Turn a lorebook on or off in the open chat, for a character, or everywhere.",
         {"id": "lorebook id", "where": "chat | character | everywhere", "character_id": "when where=character",
          "on": "true (default) or false"}, switch_lorebook, required=("id", "where")),
)}


def describe() -> str:
    """The tool list as the model reads it."""
    lines = []
    for tool in TOOLS.values():
        args = "; ".join(f"{k}: {v}" for k, v in tool.args.items()) or "no arguments"
        lines.append(f"- {tool.name}: {tool.description} Args: {args}")
    return "\n".join(lines)


def get(name: Any) -> Tool:
    if not isinstance(name, str) or name not in TOOLS:
        raise ToolError(f"Unknown tool {name!r}. Tools: {', '.join(TOOLS)}")
    return TOOLS[name]
