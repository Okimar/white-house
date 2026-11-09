"""Chats, messages and generation runs (with the SSE stream)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, copy_before, ctx_of, field, json_body
from tavern.errors import NotFound
from tavern.services import chat as chat_service
from tavern.services import messages as message_service
from tavern.services import regex as regex_service
from tavern.services import speakers

PAGE_DEFAULT = 50
PAGE_MAX = 200
HEARTBEAT_SECONDS = 15.0


def _int_param(request: Request, name: str, default: int | None, *, low: int, high: int) -> int | None:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"Query parameter {name!r} must be an integer") from None
    return max(low, min(high, value))


def _page(request: Request, chat_id: str) -> dict[str, Any]:
    """Newest `limit` messages (or those older than `before_depth`) plus paging info."""
    ctx = ctx_of(request)
    limit = _int_param(request, "limit", PAGE_DEFAULT, low=1, high=PAGE_MAX)
    before = _int_param(request, "before_depth", None, low=0, high=10**9)
    messages = ctx.chats.active_path(chat_id, before_depth=before, limit=limit)
    first_depth = messages[0]["depth"] if messages else (before or 0)
    return {
        "messages": messages,
        "total": ctx.chats.path_length(chat_id),
        "has_more": first_depth > 0,
    }


async def list_chats(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    limit = _int_param(request, "limit", 200, low=1, high=1000)
    chats = ctx.chats.list_chats(request.query_params.get("character_id"), limit=limit)
    return JSONResponse({"chats": chats})


async def create_chat(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    body = await json_body(request)
    chat = chat_service.create_chat(
        ctx,
        field(body, "character_id", str, default=None),
        field(body, "title", str, default=None),
    )
    return JSONResponse({"chat": chat, **_page(request, chat["id"])}, status_code=201)


async def get_chat(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    chat = ctx.chats.get_chat(chat_id)
    run = ctx.runs.active(chat_id)
    return JSONResponse(
        {"chat": chat, **_page(request, chat_id), "run": run.summary() if run else None}
    )


async def get_history(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    return JSONResponse({"messages": ctx.chats.all_messages(chat_id)})


async def get_messages(request: Request) -> JSONResponse:
    ctx_of(request).chats.get_chat(request.path_params["id"])
    return JSONResponse(_page(request, request.path_params["id"]))


async def rename_chat(request: Request) -> JSONResponse:
    body = await json_body(request)
    title = field(body, "title", str, allow_empty=False).strip()
    return JSONResponse(ctx_of(request).chats.rename_chat(request.path_params["id"], title))


async def update_chat_meta(request: Request) -> JSONResponse:
    """Per-chat options: connection_id, generator, preset_id."""
    ctx = ctx_of(request)
    chat = ctx.chats.get_chat(request.path_params["id"])
    body = await json_body(request)
    meta = dict(chat["meta"])
    for key in ("connection_id", "generator", "preset_id"):
        if key in body:
            value = field(body, key, str)
            if value:
                meta[key] = value
            else:
                meta.pop(key, None)
    ctx.chats.update_meta(chat["id"], meta)
    return JSONResponse(ctx.chats.get_chat(chat["id"]))


async def update_speakers(request: Request) -> JSONResponse:
    """Who speaks: {order?, others?} (services/speakers.py)."""
    body = await json_body(request)
    settings = speakers.save_settings(ctx_of(request), request.path_params["id"], body)
    return JSONResponse({"speakers": settings})


async def delete_chat(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    ctx.chats.get_chat(chat_id)
    await copy_before(request, "delete-chat")
    ctx.runs.stop(chat_id)
    ctx.chats.delete_chat(chat_id)
    ctx.prompt_log.collect_garbage()  # texts only this chat's prompts used
    return JSONResponse({"ok": True})


# --- generation -----------------------------------------------------------------------------


async def _optional_body(request: Request) -> dict[str, Any]:
    """JSON object body, or {} when the request has no body at all."""
    return await json_body(request) if await request.body() else {}


async def send(request: Request) -> JSONResponse:
    """Body: {text, reply_role?, images?, speaker?}. reply_role (default assistant) is who the reply
    is saved as; images are picture names from the library (text may then be empty); speaker is the
    character who answers. With two or more characters and the order "manual" only the message is saved:
    {"run_id": null, "message": ...}."""
    body = await json_body(request)
    images = field(body, "images", list, default=[])
    if not all(isinstance(name, str) for name in images):
        raise BadRequest("'images' must be a list of picture names")
    run = chat_service.start_send(
        ctx_of(request),
        request.path_params["id"],
        field(body, "text", str, allow_empty=bool(images)),
        field(body, "reply_role", str, default="assistant"),
        images,
        field(body, "speaker", str, default="") or None,
    )
    if run is None:
        ctx = ctx_of(request)
        leaf = ctx.chats.get_chat(request.path_params["id"])["leaf_id"]
        return JSONResponse({"run_id": None, "message": ctx.chats.get_message(leaf)}, status_code=201)
    return JSONResponse({"run_id": run.id}, status_code=202)


async def add_message(request: Request) -> JSONResponse:
    """Save a message as user, assistant or system without generating anything."""
    body = await json_body(request)
    message = chat_service.add_message(
        ctx_of(request),
        request.path_params["id"],
        field(body, "text", str, allow_empty=False),
        field(body, "role", str),
    )
    return JSONResponse(message, status_code=201)


async def set_roles(request: Request) -> JSONResponse:
    """Body: {role, ids?: [...], numbers?: "1-5, 8", all?: bool}."""
    body = await json_body(request)
    ids = field(body, "ids", list, default=[])
    if not all(isinstance(i, str) for i in ids):
        raise BadRequest("Field 'ids' must be a list of strings")
    messages = chat_service.set_roles(
        ctx_of(request),
        request.path_params["id"],
        field(body, "role", str),
        ids=ids,
        numbers=field(body, "numbers", str, default=""),
        everything=field(body, "all", bool, default=False),
    )
    return JSONResponse({"messages": messages})


async def continue_chat(request: Request) -> JSONResponse:
    """Body (optional): {role?, speaker?}. The generated message is saved with this role (default
    assistant); speaker is the character who writes it ("ответь ты")."""
    body = await _optional_body(request)
    run = chat_service.start_continue(
        ctx_of(request), request.path_params["id"], field(body, "role", str, default="assistant"),
        field(body, "speaker", str, default="") or None,
    )
    return JSONResponse({"run_id": run.id}, status_code=202)


async def talk(request: Request) -> JSONResponse:
    """«Пусть поговорят». Body (optional): {count?}: how many replies (default the chat's talk_limit)."""
    body = await _optional_body(request)
    count = field(body, "count", int, default=0) or None
    run = chat_service.start_talk(ctx_of(request), request.path_params["id"], count)
    return JSONResponse({"run_id": run.id}, status_code=202)


async def impersonate(request: Request) -> JSONResponse:
    run = chat_service.start_impersonate(ctx_of(request), request.path_params["id"])
    return JSONResponse({"run_id": run.id}, status_code=202)


async def regenerate(request: Request) -> JSONResponse:
    run = chat_service.start_regenerate(ctx_of(request), request.path_params["id"])
    return JSONResponse({"run_id": run.id}, status_code=202)


async def stop(request: Request) -> JSONResponse:
    chat_id = request.path_params["id"]
    ctx_of(request).chats.get_chat(chat_id)
    return JSONResponse({"stopped": chat_service.stop(ctx_of(request), chat_id)})


def _sse(index: int, event: dict[str, Any]) -> bytes:
    head = f"id: {index}\n" if index >= 0 else ""
    return f"{head}data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()


async def run_events(request: Request) -> StreamingResponse:
    """Server-sent events of one run. Reconnecting with Last-Event-ID resumes after that event."""
    ctx = ctx_of(request)
    run = ctx.runs.get(request.path_params["run_id"])
    if run is None:
        raise NotFound("Run not found (it may have been evicted); reload the chat")
    last_id = request.headers.get("last-event-id") or request.query_params.get("after") or "-1"
    try:
        after = int(last_id)
    except ValueError:
        after = -1

    async def stream() -> AsyncIterator[bytes]:
        yield b"retry: 2000\n\n"
        async for index, event in run.subscribe(after, heartbeat=HEARTBEAT_SECONDS):
            yield _sse(index, event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- messages -------------------------------------------------------------------------------


async def edit_message(request: Request) -> JSONResponse:
    """Body: {content?, exclude_from_prompt?}: new text of the shown version; true hides the message
    from the model (it stays in the chat), false shows it to the model again."""
    ctx = ctx_of(request)
    message_id = request.path_params["id"]
    body = await json_body(request)
    message = ctx.chats.get_message(message_id)
    if ctx.runs.active(message["chat_id"]) is not None:
        raise BadRequest("Wait for the generation to finish before editing", code="busy")
    if "content" not in body and "exclude_from_prompt" not in body:
        raise BadRequest("Nothing to change: give content and/or exclude_from_prompt")
    text = original = None
    if "content" in body:
        text, original = regex_service.stored(ctx, message["chat_id"], field(body, "content", str),
                                              message["role"], editing=True)
        # a saved-text rule that runs on edit may have changed it: the text as typed is kept (`original`)
    exclude = field(body, "exclude_from_prompt", bool) if "exclude_from_prompt" in body else None
    return JSONResponse(await message_service.edit(ctx, message_id, text=text, original=original, exclude=exclude))


async def list_branches(request: Request) -> JSONResponse:
    branches = ctx_of(request).chats.child_messages(request.path_params["id"])
    return JSONResponse({"branches": branches})


async def select_branch(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    ctx.chats.get_chat(chat_id)
    if ctx.runs.active(chat_id) is not None:
        raise BadRequest("Wait for the generation to finish before switching branches", code="busy")
    body = await json_body(request)
    leaf_id = field(body, "leaf_id", str)
    message = ctx.chats.get_message(leaf_id)
    if message["chat_id"] != chat_id:
        raise BadRequest("Branch leaf belongs to another chat", code="wrong_chat")
    # Selecting an alternative shows its whole continuation, not just the message itself. `exact`
    # stops at that message: the next one starts a new branch there, the old one stays under ⑂.
    exact = field(body, "exact", bool, default=False)
    return JSONResponse({"chat": await message_service.select_branch(ctx, chat_id, leaf_id, exact=exact)})


async def search_chat(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    ctx.chats.get_chat(chat_id)
    query = request.query_params.get("q", "").strip()
    if not query:
        raise BadRequest("Search query must not be empty", code="empty_query")
    path = {message["id"] for message in ctx.chats.active_path(chat_id)}
    found = ctx.chats.search_messages(chat_id, query)
    return JSONResponse({"messages": [{**message, "on_path": message["id"] in path} for message in found]})


async def list_swipes(request: Request) -> JSONResponse:
    """All versions of a message, to compare them side by side."""
    return JSONResponse({"swipes": ctx_of(request).chats.list_swipes(request.path_params["id"])})


async def select_swipe(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    message_id = request.path_params["id"]
    body = await json_body(request)
    message = ctx.chats.get_message(message_id)
    if ctx.runs.active(message["chat_id"]) is not None:
        raise BadRequest("Wait for the generation to finish", code="busy")
    return JSONResponse(await message_service.select_swipe(ctx, message_id, field(body, "index", int)))


async def delete_message(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    message_id = request.path_params["id"]
    message = ctx.chats.get_message(message_id)
    if ctx.runs.active(message["chat_id"]) is not None:
        raise BadRequest("Wait for the generation to finish before deleting", code="busy")
    return JSONResponse({"chat": await message_service.delete(ctx, message_id)})


routes = [
    Route("/chats", list_chats, methods=["GET"]),
    Route("/chats", create_chat, methods=["POST"]),
    Route("/chats/{id}", get_chat, methods=["GET"]),
    Route("/chats/{id}", rename_chat, methods=["PUT"]),
    Route("/chats/{id}", delete_chat, methods=["DELETE"]),
    Route("/chats/{id}/meta", update_chat_meta, methods=["PUT"]),
    Route("/chats/{id}/speakers", update_speakers, methods=["PUT"]),
    Route("/chats/{id}/messages", get_messages, methods=["GET"]),
    Route("/chats/{id}/history", get_history, methods=["GET"]),
    Route("/chats/{id}/send", send, methods=["POST"]),
    Route("/chats/{id}/messages", add_message, methods=["POST"]),
    Route("/chats/{id}/roles", set_roles, methods=["PUT"]),
    Route("/chats/{id}/continue", continue_chat, methods=["POST"]),
    Route("/chats/{id}/talk", talk, methods=["POST"]),
    Route("/chats/{id}/impersonate", impersonate, methods=["POST"]),
    Route("/chats/{id}/regenerate", regenerate, methods=["POST"]),
    Route("/chats/{id}/stop", stop, methods=["POST"]),
    Route("/chats/{id}/search", search_chat, methods=["GET"]),
    Route("/chats/{id}/branch", select_branch, methods=["POST"]),
    Route("/messages/{id}/branches", list_branches, methods=["GET"]),
    Route("/runs/{run_id}/events", run_events, methods=["GET"]),
    Route("/messages/{id}", edit_message, methods=["PUT"]),
    Route("/messages/{id}", delete_message, methods=["DELETE"]),
    Route("/messages/{id}/swipe", select_swipe, methods=["PUT"]),
    Route("/messages/{id}/swipes", list_swipes, methods=["GET"]),
]
