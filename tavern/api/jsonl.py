"""JSONL chat export and import routes."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of
from tavern.errors import NotFound
from tavern.formats.jsonl import export_chat_jsonl, import_chat_jsonl


def _export_rows(ctx: Any, chat_id: str) -> str:
    chat = ctx.chats.get_chat(chat_id)
    messages = ctx.chats.all_messages(chat_id)
    swipes = [ctx.chats.get_swipes(message["id"]) for message in messages]
    return export_chat_jsonl(chat, messages, swipes)


async def export_chat(request: Request) -> PlainTextResponse:
    chat_id = request.path_params["id"]
    return PlainTextResponse(
        _export_rows(ctx_of(request), chat_id),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{chat_id}.jsonl"'},
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
    leaf = id_map.get(source.get("leaf_id"))
    ctx.chats.set_leaf(chat["id"], leaf)
    return JSONResponse({"chat": ctx.chats.get_chat(chat["id"])}, status_code=201)


routes = [
    Route("/chats/{id}/export.jsonl", export_chat, methods=["GET"]),
    Route("/chats/import.jsonl", import_chat, methods=["POST"]),
]
