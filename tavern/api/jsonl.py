"""JSONL chat export and import routes."""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, ctx_of
from tavern.errors import AppError, NotFound
from tavern.formats.jsonl import export_chat_jsonl, import_chat_jsonl
from tavern.services import notes as note_service
from tavern.storage.repos.notes import NOTE_FIELDS


def _export_rows(ctx: Any, chat_id: str) -> str:
    chat = ctx.chats.get_chat(chat_id)
    messages = ctx.chats.all_messages(chat_id)
    swipes = [ctx.chats.get_swipes(message["id"]) for message in messages]
    return export_chat_jsonl(chat, messages, swipes, chat_notes=ctx.notes.list("chat", chat_id),
                             message_notes=ctx.notes.attached(chat_id))


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


async def import_chat(request: Request) -> JSONResponse:
    try:
        imported = import_chat_jsonl((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BadRequest(str(exc), code="invalid_jsonl") from exc
    ctx = ctx_of(request)
    source = imported["chat"]
    character_id = source.get("character_id")
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
    id_map: dict[str, str] = {}
    messages = sorted(imported["messages"], key=lambda item: item.get("depth", 0))
    swipe_rows = imported["swipes"]
    for index, item in enumerate(messages):
        parent_id = id_map.get(item.get("parent_id"))
        created = ctx.chats.add_message(
            chat["id"], role=item.get("role", "system"), content=item.get("content", ""),
            parent_id=parent_id, name=item.get("name", ""), hidden=bool(item.get("hidden")),
            exclude_from_prompt=bool(item.get("exclude_from_prompt")), owner=item.get("owner", ""),
            extra=item.get("extra") if isinstance(item.get("extra"), dict) else {},
            set_leaf=False,
        )
        id_map[item["id"]] = created["id"]
        values = swipe_rows[index] if index < len(swipe_rows) else [created["content"]]
        if values:
            ctx.chats.set_content(created["id"], values[0], swipe_idx=0)
            for text in values[1:]:
                ctx.chats.add_swipe(created["id"], text)
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
    return JSONResponse({"chat": ctx.chats.get_chat(chat["id"])}, status_code=201)


routes = [
    Route("/chats/{id}/export.jsonl", export_chat, methods=["GET"]),
    Route("/chats/import.jsonl", import_chat, methods=["POST"]),
]
