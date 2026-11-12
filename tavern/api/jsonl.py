"""JSONL chat export and import routes."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, copy_before, ctx_of
from tavern.errors import AppError, NotFound
from tavern.formats.jsonl import export_chat_jsonl, import_chat_jsonl
from tavern.formats.st_chat import is_st_chat, parse_st_chat
from tavern.services import cast
from tavern.services import notes as note_service
from tavern.storage.repos.notes import NOTE_FIELDS


def _export_rows(ctx: Any, chat_id: str) -> str:
    chat = ctx.chats.get_chat(chat_id)
    messages = ctx.chats.all_messages(chat_id)
    versions = [ctx.chats.list_swipes(message["id"]) for message in messages]
    return export_chat_jsonl(chat, messages, [[v["content"] for v in row] for row in versions],
                             chat_notes=ctx.notes.list("chat", chat_id),
                             message_notes=ctx.notes.attached(chat_id),
                             swipe_metas=[[v["meta"] for v in row] for row in versions],
                             members=ctx.members.list(chat_id, include_empty=True),
                             plugins=ctx.plugins.export_records(chat_id))


async def export_chat(request: Request) -> PlainTextResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    title = ctx.chats.get_chat(chat_id)["title"]
    safe = re.sub(r'[\\/"\r\n\x00-\x1f\x7f:*?<>|]+', "_", title).strip(" ._") or "chat"
    return PlainTextResponse(
        _export_rows(ctx, chat_id),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": attachment(safe[:120] + ".jsonl")},
    )


def _file_title(request: Request) -> str:
    """The uploaded file's name (header `X-Filename`, URI-encoded) as a chat title: SillyTavern's
    `Name_2026_11_04@03h21m31s689ms.jsonl` -> `Name 04.11.2026`."""
    name = unquote(request.headers.get("x-filename", "")).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = re.sub(r"\.(jsonl|json|txt)$", "", name, flags=re.IGNORECASE).split("@", 1)[0]
    match = re.match(r"^(.*?)_(\d{4})_(\d{2})_(\d{2})$", stem)
    if match:
        name, year, month, day = match.groups()
        return f"{name.replace('_', ' ').strip()} {day}.{month}.{year}"[:120]
    return stem.replace("_", " ").strip()[:120]


def _read_file(body: bytes) -> tuple[dict[str, Any], bool]:
    """Our own file or a SillyTavern one; a clear Russian message when neither can be read."""
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BadRequest("Файл чата не в кодировке UTF-8: это не файл чата White House или SillyTavern",
                         code="invalid_jsonl") from exc
    if not text.strip():
        raise BadRequest("Файл чата пустой", code="invalid_jsonl")
    if is_st_chat(text):
        try:
            return parse_st_chat(text), True
        except ValueError as exc:
            raise BadRequest(f"Не удалось прочитать чат SillyTavern: {exc}", code="invalid_jsonl") from exc
    try:
        return import_chat_jsonl(text), False
    except ValueError as exc:
        raise BadRequest("Не удалось прочитать файл чата: это не чат White House и не чат SillyTavern "
                         f"(.jsonl). Подробности: {exc}", code="invalid_jsonl") from exc


async def import_chat(request: Request) -> JSONResponse:
    await copy_before(request, "import-chat")
    imported, from_st = _read_file(await request.body())
    ctx = ctx_of(request)
    source = imported["chat"]
    slot_names: list[str] = []
    if from_st:
        # Never linked by name (a character here with the same name is another one): every author of the file
        # becomes an empty place under its name; «Заменить» in the members window links a real character.
        character_id = None
        user = str(imported.get("user_name") or "").strip().casefold()
        for item in imported["messages"]:
            name = str(item.get("name") or "").strip()
            item["character_id"] = None
            if (item["role"] == "assistant" and name and name.casefold() != user
                    and name.casefold() not in {n.casefold() for n in slot_names}):
                slot_names.append(name)
        header = str(imported.get("character_name") or "").strip()
        if header and header.casefold() not in {n.casefold() for n in slot_names}:
            slot_names.append(header)
        title = _file_title(request) or imported["character_name"] or "Чат из SillyTavern"
        source = {**source, "title": title}
    character_id = source.get("character_id") if not from_st else character_id
    if character_id is not None:
        try:
            ctx.characters.get(character_id)
        except NotFound:
            character_id = None
    chat = ctx.chats.create_chat(
        character_id=character_id,
        title=str(source.get("title") or "Imported chat"),
        meta=source.get("meta") if isinstance(source.get("meta"), dict) else {},
    )
    empty = cast.restore_members(ctx, chat["id"], imported["members"], character_id, slot_names)
    id_map: dict[str, str] = {}
    known = {character["id"] for character in ctx.characters.list()}
    # Stable by depth: the same order as the export, so swipe rows still line up with messages.
    messages = sorted(imported["messages"], key=lambda item: item.get("depth", 0))
    swipe_rows, meta_rows = imported["swipes"], imported["swipe_metas"]
    for index, item in enumerate(messages):
        parent_id = id_map.get(item.get("parent_id"))
        author = item.get("character_id")
        created = ctx.chats.add_message(
            chat["id"], role=item.get("role", "system"), content=item.get("content", ""),
            parent_id=parent_id, name=item.get("name", ""), hidden=bool(item.get("hidden")),
            character_id=author if author in known else None,
            exclude_from_prompt=bool(item.get("exclude_from_prompt")), owner=item.get("owner", ""),
            extra=item.get("extra") if isinstance(item.get("extra"), dict) else {},
            set_leaf=False,
        )
        id_map[item["id"]] = created["id"]
        values = swipe_rows[index] if index < len(swipe_rows) else [created["content"]]
        metas = meta_rows[index] if index < len(meta_rows) else []
        if values:
            ctx.chats.set_content(created["id"], values[0], swipe_idx=0)
            if metas and metas[0]:
                ctx.chats.update_swipe_meta(created["id"], 0, metas[0])
            for number, text in enumerate(values[1:], 1):
                ctx.chats.add_swipe(created["id"], text, metas[number] if number < len(metas) else None)
            active = item.get("active_swipe", 0)
            if isinstance(active, int) and 0 <= active < len(values):
                ctx.chats.set_active_swipe(created["id"], active)
    for note in imported["chat_notes"]:
        try:
            ctx.notes.create("chat", chat["id"], note_service.clean_note(
                {k: note[k] for k in NOTE_FIELDS if k in note}))
        except AppError:
            continue  # a note this version cannot read is skipped, not the whole chat
    for note in imported["message_notes"]:
        message_id = id_map.get(note["message_id"])
        kind = note.get("kind", "visible")
        if message_id is None or kind not in note_service.KINDS:
            continue
        ctx.notes.attach(chat["id"], message_id, kind=kind, text=note["text"],
                         role=note["role"] if note.get("role") in note_service.ROLES else "system",
                         title=str(note.get("title") or ""), note_id=note.get("note_id"))
    leaf = id_map.get(source.get("leaf_id"))
    ctx.chats.set_leaf(chat["id"], leaf)
    # what plugins keep with a chat (ADR 0019 point E): after the messages exist, with the old -> new id map
    ctx.plugins.import_records(chat["id"], imported["plugins"], id_map)
    return JSONResponse({"chat": ctx.chats.get_chat(chat["id"]), "empty_members": empty}, status_code=201)


routes = [
    Route("/chats/{id}/export.jsonl", export_chat, methods=["GET"]),
    Route("/chats/import.jsonl", import_chat, methods=["POST"]),
]
