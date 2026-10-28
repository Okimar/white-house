"""Author's notes (global, a character's, a chat's) and notes on messages. Logic: services/notes.py."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body
from tavern.services import notes as note_service

NOTE_KEYS = ("title", "text", "enabled", "depth", "role", "interval", "mode", "position")


def _fields(body: dict) -> dict:
    unknown = set(body) - set(NOTE_KEYS) - {"scope", "owner_id"}
    if unknown:
        raise BadRequest(f"Unknown fields: {', '.join(sorted(unknown))}")
    return {k: body[k] for k in NOTE_KEYS if k in body}


async def list_notes(request: Request) -> JSONResponse:
    """?scope=global | ?scope=character&owner_id=... | ?scope=chat&owner_id=..."""
    params = request.query_params
    notes = note_service.list_notes(ctx_of(request), params.get("scope", "global"), params.get("owner_id"))
    return JSONResponse({"notes": notes})


async def chat_notes(request: Request) -> JSONResponse:
    """Everything the notes window of a chat shows: the chat's, each member's, the global ones."""
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    ctx.chats.get_chat(chat_id)
    return JSONResponse({
        "chat": ctx.notes.list("chat", chat_id),
        "characters": [
            {"character_id": m["character_id"], "name": m["name"], "is_me": m["is_me"],
             "notes": ctx.notes.list("character", m["character_id"])}
            for m in ctx.members.list(chat_id)
        ],
        "global": ctx.notes.list("global"),
    })


async def create_note(request: Request) -> JSONResponse:
    body = await json_body(request)
    note = await note_service.create_note(
        ctx_of(request), field(body, "scope", str), field(body, "owner_id", str, default=None), _fields(body)
    )
    return JSONResponse(note, status_code=201)


async def update_note(request: Request) -> JSONResponse:
    body = await json_body(request)
    if "scope" in body or "owner_id" in body:
        raise BadRequest("A note cannot move to another level")
    return JSONResponse(await note_service.update_note(ctx_of(request), request.path_params["id"], _fields(body)))


async def delete_note(request: Request) -> JSONResponse:
    await note_service.delete_note(ctx_of(request), request.path_params["id"])
    return JSONResponse({"deleted": True})


# --- notes on messages ------------------------------------------------------------------------


async def list_attached(request: Request) -> JSONResponse:
    """?kinds=hidden,visible (default both)."""
    kinds = tuple(k for k in request.query_params.get("kinds", "hidden,visible").split(",") if k)
    return JSONResponse({"notes": note_service.attached(ctx_of(request), request.path_params["id"], kinds)})


async def add_attached(request: Request) -> JSONResponse:
    body = await json_body(request)
    note = await note_service.add_to_message(
        ctx_of(request), request.path_params["id"], field(body, "message_id", str),
        field(body, "text", str), field(body, "role", str, default="system"), field(body, "title", str, default=""),
        field(body, "kind", str, default="visible"),
    )
    return JSONResponse(note, status_code=201)


async def edit_attached(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(await note_service.edit_attached(ctx_of(request), request.path_params["id"], body))


async def delete_attached(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    note = ctx.notes.get_attached(request.path_params["id"])
    removed = await note_service.delete_attached(ctx, [note["id"]], note["chat_id"])
    return JSONResponse({"removed": removed})


async def by_numbers(request: Request) -> JSONResponse:
    body = await json_body(request)
    picked = note_service.by_numbers(ctx_of(request), request.path_params["id"], field(body, "numbers", str))
    return JSONResponse({"items": picked})


async def delete_by_numbers(request: Request) -> JSONResponse:
    body = await json_body(request)
    removed = await note_service.delete_by_numbers(
        ctx_of(request), request.path_params["id"], field(body, "numbers", str)
    )
    return JSONResponse({"removed": removed})


async def add_by_numbers(request: Request) -> JSONResponse:
    body = await json_body(request)
    made = await note_service.add_by_numbers(
        ctx_of(request), request.path_params["id"], field(body, "numbers", str), field(body, "text", str),
        field(body, "role", str, default="system"), field(body, "title", str, default=""),
        field(body, "kind", str, default="visible"),
    )
    return JSONResponse({"notes": made})


async def kind_by_numbers(request: Request) -> JSONResponse:
    body = await json_body(request)
    changed = await note_service.kind_by_numbers(
        ctx_of(request), request.path_params["id"], field(body, "numbers", str), field(body, "kind", str)
    )
    return JSONResponse({"changed": changed})


async def clear_kind(request: Request) -> JSONResponse:
    """Remove every kept note of one kind (?kind=hidden, the default) from the chat."""
    removed = await note_service.clear_kind(ctx_of(request), request.path_params["id"],
                                            request.query_params.get("kind", "hidden"))
    return JSONResponse({"removed": removed})


routes = [
    Route("/notes", list_notes, methods=["GET"]),
    Route("/notes", create_note, methods=["POST"]),
    Route("/notes/{id}", update_note, methods=["PUT"]),
    Route("/notes/{id}", delete_note, methods=["DELETE"]),
    Route("/chats/{id}/notes", chat_notes, methods=["GET"]),
    Route("/chats/{id}/message-notes", list_attached, methods=["GET"]),
    Route("/chats/{id}/message-notes", add_attached, methods=["POST"]),
    Route("/chats/{id}/message-notes/by-numbers", by_numbers, methods=["POST"]),
    Route("/chats/{id}/message-notes/delete-by-numbers", delete_by_numbers, methods=["POST"]),
    Route("/chats/{id}/message-notes/add-by-numbers", add_by_numbers, methods=["POST"]),
    Route("/chats/{id}/message-notes/kind-by-numbers", kind_by_numbers, methods=["POST"]),
    Route("/chats/{id}/message-notes", clear_kind, methods=["DELETE"]),
    Route("/message-notes/{id}", edit_attached, methods=["PUT"]),
    Route("/message-notes/{id}", delete_attached, methods=["DELETE"]),
]
