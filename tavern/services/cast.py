"""Who takes part in a chat: add, change and remove members, find "me".

The UI, agents and the on-top assistant (stage 8) all change members through these functions, so
the same checks apply everywhere. Every change fires the action event

    chat.members_changed   chat_id, members

The chat's main character (`chats.character_id`, used to name replies and for {{char}}) is kept
pointing at a member that is not "me": the first such member if the old one left.

Empty places (`chat_member_slots`): a character of an imported chat that has no card here. The place keeps the
original name and the member settings, never speaks and is not in the prompt; «Заменить» (`replace_member`) turns
it, or any member, into the chosen character and re-points the messages it wrote. Only the members window asks
for them (`ctx.members.list(chat_id, include_empty=True)`); every other reader gets real members only.
"""

from __future__ import annotations

from typing import Any

from tavern.context import AppContext
from tavern.core.assembly import Member
from tavern.errors import AppError, NotFound
from tavern.services import speakers

DEFAULT_USER_NAME = "User"
MAX_BLURB = 500


def _check(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "depth":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                      or not 0 <= value <= 1000):
                raise AppError("Depth must be empty (start of the prompt) or 0..1000", code="bad_member")
        elif key == "importance":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
                raise AppError("Importance must be 1..10", code="bad_member")
        elif key == "talkativeness":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise AppError("Talkativeness must be 0..100", code="bad_member")
        elif key == "blurb":
            if not isinstance(value, str) or len(value) > MAX_BLURB:
                raise AppError(f"The short description is at most {MAX_BLURB} characters", code="bad_member")
            value = value.strip()
        elif key in ("is_me", "model_may_write", "muted"):
            if not isinstance(value, bool):
                raise AppError(f"{key} must be true or false", code="bad_member")
        elif key == "position":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AppError("Position must be a non-negative number", code="bad_member")
        else:
            raise AppError(f"Unknown member field {key!r}", code="bad_member")
        clean[key] = value
    return clean


def _fix_primary(ctx: AppContext, chat_id: str) -> None:
    chat = ctx.chats.get_chat(chat_id)
    members = ctx.members.list(chat_id)
    candidates = [m["character_id"] for m in members if not m["is_me"]]
    if chat["character_id"] not in candidates:
        ctx.chats.set_character(chat_id, candidates[0] if candidates else None)


async def _changed(ctx: AppContext, chat_id: str) -> list[dict[str, Any]]:
    _fix_primary(ctx, chat_id)
    members = ctx.members.list(chat_id)
    await ctx.events.emit("chat.members_changed", chat_id=chat_id, members=members)
    return ctx.members.list(chat_id, include_empty=True)


def add_member_now(ctx: AppContext, chat_id: str, character_id: str, **options: Any) -> dict[str, Any]:
    """Synchronous add without the event, for chat creation."""
    ctx.chats.get_chat(chat_id)
    ctx.characters.get(character_id)
    member = ctx.members.add(chat_id, character_id, **_check(options))
    _fix_primary(ctx, chat_id)
    return member


def add_empty_now(ctx: AppContext, chat_id: str, name: str, **options: Any) -> dict[str, Any]:
    """An empty place with the original name (no card linked), without the event, for chat import."""
    ctx.chats.get_chat(chat_id)
    name = name.strip()
    if not name:
        raise AppError("An empty place needs a name", code="bad_member")
    options.pop("is_me", None)
    return ctx.members.add_slot(chat_id, name, **_check({k: v for k, v in options.items() if k != "position"}))


def restore_members(
    ctx: AppContext, chat_id: str, rows: list[dict[str, Any]], main: str | None, names: list[str] = (),
) -> list[str]:
    """The cast of an imported chat: the file's members this install has. A file without any of
    them (older format, other install) gets what a new chat gets: `main` and the default "me".
    A member this install does not have becomes an empty place under its original name, and so does
    each of `names` (authors of a SillyTavern chat: never linked to a character by name). Returns the
    names of the empty places, for the warning."""
    empty: list[str] = []
    for row in rows:
        options = {k: row[k] for k in ("depth", "importance", "is_me", "model_may_write", "talkativeness",
                                       "muted", "blurb") if k in row}
        try:
            if not row.get("character_id"):
                raise NotFound("no character in the file's row")
            add_member_now(ctx, chat_id, row["character_id"], **options)
        except NotFound:  # that character is not here: its place stays, named as in the file
            name = str(row.get("name") or "").strip()
            if name and not row.get("is_me"):
                try:
                    add_empty_now(ctx, chat_id, name, **options)
                    empty.append(name)
                except AppError:
                    pass
        except AppError:  # a field this version cannot read
            continue
    for name in names:
        if name.strip() and not any(name.strip().casefold() == e.casefold() for e in empty):
            add_empty_now(ctx, chat_id, name)
            empty.append(name.strip())
    current = ctx.members.list(chat_id)
    if not any(not m["is_me"] for m in current) and main:
        add_member_now(ctx, chat_id, main)
    if not rows:
        default_me = ctx.settings.get("default_me")
        if default_me and default_me != main:
            try:
                add_member_now(ctx, chat_id, default_me, is_me=True)
            except NotFound:
                pass  # the default character was deleted: the chat simply has no "me"
    return empty


async def add_member(ctx: AppContext, chat_id: str, character_id: str, **options: Any) -> list[dict[str, Any]]:
    add_member_now(ctx, chat_id, character_id, **options)
    return await _changed(ctx, chat_id)


async def update_member(
    ctx: AppContext, chat_id: str, character_id: str, changes: dict[str, Any]
) -> list[dict[str, Any]]:
    ctx.chats.get_chat(chat_id)
    try:
        ctx.members.update(chat_id, character_id, _check(changes))
    except ValueError as exc:  # an empty place cannot be "me"
        raise AppError("Пустое место нельзя назначить «этим я»: сначала замените его персонажем",
                       code="bad_member") from exc
    return await _changed(ctx, chat_id)


async def replace_member(ctx: AppContext, chat_id: str, key: str, character_id: str) -> list[dict[str, Any]]:
    """«Заменить»: the chosen character takes the place of a member or an empty place in this chat. Its
    settings stay, the messages it wrote now belong to the new character. `key` is the old character's id
    or the id of the empty place."""
    chat = ctx.chats.get_chat(chat_id)
    ctx.characters.get(character_id)
    old = ctx.members.get(chat_id, key)
    ctx.members.replace(chat_id, key, character_id)
    if old["character_id"] and chat["character_id"] == old["character_id"]:
        ctx.chats.set_character(chat_id, character_id)
    return await _changed(ctx, chat_id)


async def remove_member(ctx: AppContext, chat_id: str, character_id: str) -> list[dict[str, Any]]:
    ctx.chats.get_chat(chat_id)
    ctx.members.remove(chat_id, character_id)
    return await _changed(ctx, chat_id)


def me(ctx: AppContext, chat_id: str) -> dict[str, Any] | None:
    return next((m for m in ctx.members.list(chat_id) if m["is_me"]), None)


def user_name(ctx: AppContext, chat_id: str | None = None) -> str:
    """{{user}}: the chat's "me" character, else the name from settings."""
    if chat_id is not None:
        mine = me(ctx, chat_id)
        if mine is not None:
            return mine["name"]
    return ctx.settings.get("user_name") or DEFAULT_USER_NAME


def prompt_members(ctx: AppContext, chat: dict[str, Any], speaker: str | None = None) -> list[Member]:
    """Members with their cards, for prompt assembly. `speaker` writes this reply: its card is the
    primary one ({{char}}); default the chat's main character. With two or more characters muted
    ones stay out, and unless the chat shows the others in full only the speaker and "me" go in
    with their cards (services/speakers.py)."""
    speaker = speaker or chat["character_id"]
    rows = ctx.members.list(chat["id"])
    if speakers.several(rows):
        full = speakers.get_settings(chat)["others"] == "full"
        rows = [m for m in rows if m["is_me"] or m["character_id"] == speaker or (full and not m["muted"])]
    result = []
    for member in rows:
        try:
            card = ctx.characters.get(member["character_id"])["data"]
        except NotFound:
            continue
        result.append(Member(
            character_id=member["character_id"], name=member["name"], card=card,
            depth=member["depth"], importance=member["importance"], is_me=member["is_me"],
            model_may_write=member["model_may_write"],
            primary=member["character_id"] == speaker,
        ))
    return result
