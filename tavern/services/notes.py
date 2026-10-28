"""Author's notes: edit them, decide which fire for a reply, and work with notes on messages.

Levels: global (every chat), a character's (every chat the character is in), a chat's. A note
fires every reply, or skips `interval` replies between sendings ("через N ответов", 0 = every
reply), and goes `depth` messages from the end, as `role`. Three kinds (`mode`):
  - once: the model sees it only in the request it fired in; nothing is kept;
  - hidden: kept in the history after the message it followed; the chat shows it only on request;
  - visible: kept in the history and shown next to its message (statuses and other things the
    user tracks).

Kept notes can also be written, changed and deleted by message numbers ("1-52 68-72").

The UI, agents and the on-top assistant (stage 8) all go through these functions. Every change
fires an action event:

    notes.changed           scope, owner_id
    message_notes.changed   chat_id
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from tavern.context import AppContext
from tavern.core.assembly import NoteInput
from tavern.errors import AppError, NotFound

SCOPES = ("global", "character", "chat")
ROLES = ("system", "user", "assistant")
MODES = ("once", "hidden", "visible")
KINDS = ("hidden", "visible")  # notes kept in the history
MAX_TITLE = 200
MAX_TEXT = 100_000


def _int(value: Any, low: int, high: int, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AppError(message, code="bad_note")
    return value


def _text(value: Any, *, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise AppError("Note text is too long" if limit == MAX_TEXT else "Note title is too long",
                       code="bad_note")
    return value


def _role(value: Any) -> str:
    if value not in ROLES:
        raise AppError(f"Role must be one of: {', '.join(ROLES)}", code="bad_note")
    return value


def _kind(value: Any) -> str:
    if value not in KINDS:
        raise AppError("A note on a message is 'hidden' or 'visible'", code="bad_note")
    return value


def clean_note(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "title":
            clean[key] = _text(value, limit=MAX_TITLE).strip()
        elif key == "text":
            clean[key] = _text(value)
        elif key == "enabled":
            if not isinstance(value, bool):
                raise AppError("enabled must be true or false", code="bad_note")
            clean[key] = value
        elif key == "depth":
            clean[key] = _int(value, 0, 1000, "Depth must be 0..1000")
        elif key == "interval":
            clean[key] = _int(value, 0, 1000, "Replies to skip must be 0..1000 (0 = every reply)")
        elif key == "position":
            clean[key] = _int(value, 0, 1_000_000, "Position must be a non-negative number")
        elif key == "role":
            clean[key] = _role(value)
        elif key == "mode":
            if value not in MODES:
                raise AppError("Mode must be 'once', 'hidden' or 'visible'", code="bad_note")
            clean[key] = value
        else:
            raise AppError(f"Unknown note field {key!r}", code="bad_note")
    return clean


def _owner(ctx: AppContext, scope: str, owner_id: str | None) -> str | None:
    if scope not in SCOPES:
        raise AppError(f"Scope must be one of: {', '.join(SCOPES)}", code="bad_note")
    if scope == "global":
        return None
    if not owner_id:
        raise AppError(f"A {scope} note needs the {scope} id", code="bad_note")
    if scope == "character":
        ctx.characters.get(owner_id)
    else:
        ctx.chats.get_chat(owner_id)
    return owner_id


def _owner_of(note: dict[str, Any]) -> str | None:
    return note["character_id"] or note["chat_id"]


# --- notes ------------------------------------------------------------------------------------


def list_notes(ctx: AppContext, scope: str, owner_id: str | None = None) -> list[dict[str, Any]]:
    return ctx.notes.list(scope, _owner(ctx, scope, owner_id))


async def create_note(ctx: AppContext, scope: str, owner_id: str | None,
                      fields: dict[str, Any]) -> dict[str, Any]:
    owner = _owner(ctx, scope, owner_id)
    note = ctx.notes.create(scope, owner, clean_note(fields))
    await ctx.events.emit("notes.changed", scope=scope, owner_id=owner)
    return note


async def update_note(ctx: AppContext, note_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    note = ctx.notes.update(note_id, clean_note(changes))
    await ctx.events.emit("notes.changed", scope=note["scope"], owner_id=_owner_of(note))
    return note


async def delete_note(ctx: AppContext, note_id: str) -> None:
    note = ctx.notes.get(note_id)
    ctx.notes.delete(note_id)
    await ctx.events.emit("notes.changed", scope=note["scope"], owner_id=_owner_of(note))


# --- a reply ----------------------------------------------------------------------------------


def reply_number(history: list[dict[str, Any]]) -> int:
    """Which reply of the chat is being written: 1 + replies already in the history."""
    return 1 + sum(1 for m in history if m["role"] != "user")


def fires(interval: int, number: int) -> bool:
    """Skip `interval` replies between sendings: 0 = every reply, 1 = every other one."""
    return number % (interval + 1) == 0


def for_reply(ctx: AppContext, chat_id: str, character_ids: list[str],
              history: list[dict[str, Any]]) -> list[NoteInput]:
    """Notes that fire for the next reply, global first, then characters', then the chat's."""
    number = reply_number(history)
    return [
        NoteInput(id=n["id"], text=n["text"], role=n["role"], depth=n["depth"], mode=n["mode"],
                  title=n["title"])
        for n in ctx.notes.for_chat(chat_id, character_ids)
        if n["enabled"] and n["text"].strip() and fires(n["interval"], number)
    ]


def with_attached(ctx: AppContext, chat_id: str, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """History messages with their kept notes under "notes" (what the prompt assembler reads)."""
    by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in ctx.notes.attached(chat_id):
        by_message[note["message_id"]].append(note)
    return [{**m, "notes": by_message.get(m["id"], [])} for m in history]


def record_placed(ctx: AppContext, chat_id: str, placed: list[dict[str, Any]]) -> None:
    """After a reply is saved: keep its hidden and visible notes after the message they followed."""
    for item in placed:
        if item["mode"] == "once" or item["message_id"] is None:
            continue  # not kept, or went before the first message where there is nothing to follow
        ctx.notes.attach(chat_id, item["message_id"], kind=item["mode"], text=item["text"],
                         role=item["role"], title=item["title"], note_id=item["note_id"])


# --- notes on messages ------------------------------------------------------------------------


def attached(ctx: AppContext, chat_id: str, kinds: tuple[str, ...] = KINDS) -> list[dict[str, Any]]:
    ctx.chats.get_chat(chat_id)
    for kind in kinds:
        _kind(kind)
    return ctx.notes.attached(chat_id, kinds)


def by_numbers(ctx: AppContext, chat_id: str, numbers: str) -> list[dict[str, Any]]:
    """Messages of the active branch picked by numbers, each with its kept notes:
    [{number, message, notes: [...]}]."""
    from tavern.services.chat import parse_numbers  # chat imports this module

    if not numbers.strip():
        raise AppError("No messages selected", code="nothing_selected")
    wanted = parse_numbers(numbers, ctx.chats.path_length(chat_id))
    kept: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for note in ctx.notes.attached(chat_id):
        kept[note["message_id"]].append(note)
    return [
        {"number": m["depth"] + 1, "message": m, "notes": kept.get(m["id"], [])}
        for m in ctx.chats.active_path(chat_id) if m["depth"] + 1 in wanted
    ]


def _message_in_chat(ctx: AppContext, chat_id: str, message_id: str) -> None:
    if ctx.chats.get_message(message_id)["chat_id"] != chat_id:
        raise NotFound(f"Message {message_id} is not in chat {chat_id}")


async def _changed(ctx: AppContext, chat_id: str) -> None:
    await ctx.events.emit("message_notes.changed", chat_id=chat_id)


async def add_to_message(ctx: AppContext, chat_id: str, message_id: str, text: str,
                         role: str = "system", title: str = "", kind: str = "visible") -> dict[str, Any]:
    """Write a note by hand after a message."""
    _message_in_chat(ctx, chat_id, message_id)
    note = ctx.notes.attach(chat_id, message_id, kind=_kind(kind), text=_text(text), role=_role(role),
                            title=_text(title, limit=MAX_TITLE).strip())
    await _changed(ctx, chat_id)
    return note


async def edit_attached(ctx: AppContext, attached_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "text":
            clean[key] = _text(value)
        elif key == "role":
            clean[key] = _role(value)
        elif key == "title":
            clean[key] = _text(value, limit=MAX_TITLE).strip()
        elif key == "kind":
            clean[key] = _kind(value)
        else:
            raise AppError(f"Unknown note field {key!r}", code="bad_note")
    note = ctx.notes.update_attached(attached_id, clean)
    await _changed(ctx, note["chat_id"])
    return note


async def delete_attached(ctx: AppContext, attached_ids: list[str], chat_id: str) -> int:
    ids = [i for i in attached_ids if ctx.notes.get_attached(i)["chat_id"] == chat_id]
    removed = ctx.notes.detach(ids)
    await _changed(ctx, chat_id)
    return removed


async def delete_by_numbers(ctx: AppContext, chat_id: str, numbers: str) -> int:
    """Remove the kept notes of the picked messages."""
    picked = by_numbers(ctx, chat_id, numbers)
    removed = ctx.notes.detach_from(chat_id, [p["message"]["id"] for p in picked])
    await _changed(ctx, chat_id)
    return removed


async def add_by_numbers(ctx: AppContext, chat_id: str, numbers: str, text: str,
                         role: str = "system", title: str = "", kind: str = "visible") -> list[dict[str, Any]]:
    """One note for every picked message, added next to the notes they already have."""
    text, role, kind = _text(text), _role(role), _kind(kind)
    title = _text(title, limit=MAX_TITLE).strip()
    if not text.strip():
        raise AppError("The note is empty", code="bad_note")
    picked = by_numbers(ctx, chat_id, numbers)
    made = [ctx.notes.attach(chat_id, p["message"]["id"], kind=kind, text=text, role=role, title=title)
            for p in picked]
    await _changed(ctx, chat_id)
    return made


async def kind_by_numbers(ctx: AppContext, chat_id: str, numbers: str, kind: str) -> int:
    """Make every note of the picked messages visible or hidden."""
    kind = _kind(kind)
    picked = by_numbers(ctx, chat_id, numbers)
    changed = ctx.notes.set_kind(chat_id, [p["message"]["id"] for p in picked], kind)
    await _changed(ctx, chat_id)
    return changed


async def clear_kind(ctx: AppContext, chat_id: str, kind: str) -> int:
    """Remove every kept note of one kind from the chat ("очистить чат от невидимых")."""
    ctx.chats.get_chat(chat_id)
    removed = ctx.notes.clear(chat_id, _kind(kind))
    await _changed(ctx, chat_id)
    return removed
