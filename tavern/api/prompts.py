"""Chat members, prompt presets (with SillyTavern import/export) and "what the model saw"."""

from __future__ import annotations

import json
import re
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, ctx_of, field, json_body, read_body
from tavern.errors import AppError, NotFound
from tavern.providers.base import ChatMessage, GenerationRequest
from tavern.services import cast
from tavern.services import presets as preset_service
from tavern.services.chat import resolve_connection

MEMBER_KEYS = ("depth", "importance", "is_me", "model_may_write", "position", "talkativeness", "muted", "blurb")
MAX_PRESET_FILE = 5 * 1024 * 1024


# --- members --------------------------------------------------------------------------------


async def list_members(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    chat_id = request.path_params["id"]
    ctx.chats.get_chat(chat_id)
    # with the empty places (`empty: true`, no character_id): the members window shows and replaces them
    return JSONResponse({"members": ctx.members.list(chat_id, include_empty=True)})


async def add_member(request: Request) -> JSONResponse:
    body = await json_body(request)
    options = {k: body[k] for k in MEMBER_KEYS if k in body and k != "position"}
    members = await cast.add_member(
        ctx_of(request), request.path_params["id"], field(body, "character_id", str), **options
    )
    return JSONResponse({"members": members}, status_code=201)


async def update_member(request: Request) -> JSONResponse:
    body = await json_body(request)
    unknown = set(body) - set(MEMBER_KEYS)
    if unknown:
        raise BadRequest(f"Unknown fields: {', '.join(sorted(unknown))}")
    members = await cast.update_member(
        ctx_of(request), request.path_params["id"], request.path_params["character_id"], body
    )
    return JSONResponse({"members": members})


async def replace_member(request: Request) -> JSONResponse:
    body = await json_body(request)
    members = await cast.replace_member(
        ctx_of(request), request.path_params["id"], request.path_params["character_id"],
        field(body, "character_id", str),
    )
    return JSONResponse({"members": members})


async def remove_member(request: Request) -> JSONResponse:
    members = await cast.remove_member(
        ctx_of(request), request.path_params["id"], request.path_params["character_id"]
    )
    return JSONResponse({"members": members})


# --- presets --------------------------------------------------------------------------------


async def list_presets(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse({"presets": preset_service.list_presets(ctx),
                         "active": ctx.settings.get("active_preset") or preset_service.BUILTIN_ID})


async def get_preset(request: Request) -> JSONResponse:
    return JSONResponse(preset_service.get(ctx_of(request), request.path_params["id"]))


async def create_preset(request: Request) -> JSONResponse:
    """Body: {name, data?} or {name, from: preset id} to copy one (the built-in included)."""
    ctx = ctx_of(request)
    body = await json_body(request)
    data = body.get("data")
    source = field(body, "from", str, default=None)
    if source:
        data = preset_service.get(ctx, source)["data"]
    preset = await preset_service.create(ctx, body.get("name"), data)
    return JSONResponse(preset, status_code=201)


async def update_preset(request: Request) -> JSONResponse:
    body = await json_body(request)
    preset = await preset_service.update(
        ctx_of(request), request.path_params["id"], body.get("name"), body.get("data")
    )
    return JSONResponse(preset)


async def delete_preset(request: Request) -> JSONResponse:
    await preset_service.delete(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


async def import_st_preset(request: Request) -> JSONResponse:
    """Body: the SillyTavern preset file as is. ?name= is the file name (becomes the preset name)."""
    blob = await read_body(request, MAX_PRESET_FILE)
    try:
        obj = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadRequest("The preset file is not valid JSON", code="bad_preset") from None
    preset = await preset_service.import_st(ctx_of(request), obj, request.query_params.get("name", ""))
    return JSONResponse(preset, status_code=201)


async def import_preset(request: Request) -> JSONResponse:
    """Body: a preset file, ours or SillyTavern's (detected). ?name= is the file name."""
    blob = await read_body(request, MAX_PRESET_FILE)
    try:
        obj = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadRequest("The preset file is not valid JSON", code="bad_preset") from None
    preset = await preset_service.import_any(ctx_of(request), obj, request.query_params.get("name", ""))
    return JSONResponse(preset, status_code=201)


def _download(name: str, obj: dict[str, Any], suffix: str) -> Response:
    safe = re.sub(r'[\\/"\r\n\x00-\x1f\x7f:*?<>|]+', "_", name).strip(" .") or "preset"
    return Response(
        json.dumps(obj, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": attachment(safe + suffix)},
    )


async def export_st_preset(request: Request) -> Response:
    name, obj = preset_service.export_st(ctx_of(request), request.path_params["id"])
    return _download(name, obj, ".json")


async def export_preset(request: Request) -> Response:
    """Our full format (?format=tavern, default) or SillyTavern's (?format=st)."""
    if request.query_params.get("format") == "st":
        return await export_st_preset(request)
    name, obj = preset_service.export_native(ctx_of(request), request.path_params["id"])
    return _download(name, obj, ".tavern-preset.json")


# --- what the model saw ---------------------------------------------------------------------


def _record_for(request: Request) -> dict[str, Any]:
    ctx = ctx_of(request)
    message = ctx.chats.get_message(request.path_params["id"])
    raw = request.query_params.get("swipe")
    try:
        swipe = message["active_swipe"] if raw is None else int(raw)
    except ValueError:
        raise BadRequest("swipe must be a number") from None
    record_id = ctx.prompt_log.find(message["id"], swipe)
    if record_id is None:
        raise NotFound("No saved prompt for this message (it was written by hand, imported, or "
                       "generated before this feature)", code="no_prompt")
    return ctx.prompt_log.get(record_id)


async def message_prompt(request: Request) -> JSONResponse:
    return JSONResponse(_record_for(request))


async def count_prompt_tokens(request: Request) -> JSONResponse:
    """Exact prompt size as the model counts it: a 1-token request that reports usage."""
    ctx = ctx_of(request)
    record = _record_for(request)
    connection = None
    connection_id = record["meta"].get("connection_id")
    if connection_id:
        try:
            connection = ctx.connections.get(connection_id)
        except NotFound:
            connection = None
    if connection is None:
        connection = resolve_connection(ctx, ctx.chats.get_chat(record["chat_id"]))
    messages: list[ChatMessage] = []
    for entry in record["entries"]:
        if entry["msg"] < len(messages):
            last = messages[entry["msg"]]
            messages[entry["msg"]] = ChatMessage(last.role, f"{last.content}\n\n{entry['text']}")
        else:
            messages.append(ChatMessage(entry["role"], entry["text"]))
    provider = ctx.providers.create(connection)
    try:
        result = await provider.generate(GenerationRequest(messages=messages, model=connection["model"],
                                                           max_tokens=1))
    finally:
        await provider.aclose()
    if not result.usage.prompt_tokens:
        raise AppError("The API did not report a token count for this request", code="no_count",
                       status=502)
    return JSONResponse({"tokens": result.usage.prompt_tokens, "model": connection["model"]})


async def source_versions(request: Request) -> JSONResponse:
    """Every text one source was sent with (read-only history of a block, card or message)."""
    source = request.query_params.get("source", "").strip()
    if not source:
        raise BadRequest("Query parameter 'source' is required")
    return JSONResponse({"source": source, "versions": ctx_of(request).prompt_log.versions(source)})


routes = [
    Route("/chats/{id}/members", list_members, methods=["GET"]),
    Route("/chats/{id}/members", add_member, methods=["POST"]),
    Route("/chats/{id}/members/{character_id}", update_member, methods=["PUT"]),
    Route("/chats/{id}/members/{character_id}", remove_member, methods=["DELETE"]),
    Route("/chats/{id}/members/{character_id}/replace", replace_member, methods=["POST"]),
    Route("/presets", list_presets, methods=["GET"]),
    Route("/presets", create_preset, methods=["POST"]),
    Route("/presets/import/st", import_st_preset, methods=["POST"]),
    Route("/presets/import", import_preset, methods=["POST"]),
    Route("/presets/{id}", get_preset, methods=["GET"]),
    Route("/presets/{id}", update_preset, methods=["PUT"]),
    Route("/presets/{id}", delete_preset, methods=["DELETE"]),
    Route("/presets/{id}/export/st", export_st_preset, methods=["GET"]),
    Route("/presets/{id}/export", export_preset, methods=["GET"]),
    Route("/messages/{id}/prompt", message_prompt, methods=["GET"]),
    Route("/messages/{id}/prompt/count", count_prompt_tokens, methods=["POST"]),
    Route("/prompt/versions", source_versions, methods=["GET"]),
]
