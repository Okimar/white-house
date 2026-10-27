"""Characters and settings."""

from __future__ import annotations

import base64
import binascii
import re
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body
from tavern.formats.cards import card_to_png, decode_png_card, encode_card, parse_card


async def list_characters(request: Request) -> JSONResponse:
    return JSONResponse({"characters": ctx_of(request).characters.list()})


async def get_character(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).characters.get(request.path_params["id"]))


async def create_character(request: Request) -> JSONResponse:
    """Body: a character card (V1 flat, V2 or V3 JSON) or {"name": ...}."""
    spec, data = parse_card(await json_body(request))
    return JSONResponse(ctx_of(request).characters.create(data, spec=spec), status_code=201)


async def import_character_png(request: Request) -> JSONResponse:
    blob = await request.body()
    if len(blob) > 10 * 1024 * 1024:
        raise BadRequest("PNG card is too large", code="card_too_large")
    spec, data = decode_png_card(blob)
    avatar = "data:image/png;base64," + base64.b64encode(blob).decode("ascii")
    return JSONResponse(ctx_of(request).characters.create(data, spec=spec, avatar=avatar), status_code=201)


async def export_character_json(request: Request) -> JSONResponse:
    character = ctx_of(request).characters.get(request.path_params["id"])
    return JSONResponse(encode_card(character["data"], spec=character["spec"]))


async def export_character_png(request: Request) -> Response:
    character = ctx_of(request).characters.get(request.path_params["id"])
    safe_name = re.sub(r'[\\"\r\n\x00-\x1f\x7f]+', "_", character["name"]).strip(" .") or "character"
    blob = card_to_png(character["data"], spec=character["spec"])
    avatar = character.get("avatar") or ""
    if avatar.startswith("data:image/png;base64,"):
        try:
            original = base64.b64decode(avatar.split(",", 1)[1], validate=True)
            if original.startswith(b"\x89PNG\r\n\x1a\n"):
                blob = original
        except (ValueError, binascii.Error):
            pass
    return Response(blob, media_type="image/png", headers={"Content-Disposition": _attachment(safe_name + ".png")})


def _attachment(filename: str) -> str:
    """Content-Disposition for any name: headers are latin-1, so non-ASCII goes in filename* (RFC 6266)."""
    fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


async def update_character(request: Request) -> JSONResponse:
    _, data = parse_card(await json_body(request))
    return JSONResponse(ctx_of(request).characters.update(request.path_params["id"], data))


async def delete_character(request: Request) -> JSONResponse:
    ctx_of(request).characters.delete(request.path_params["id"])
    return JSONResponse({"ok": True})


# Settings the UI may change. Anything else is rejected so typos do not silently persist.
SETTING_TYPES = {
    "user_name": str,
    "active_connection": str,
    "generator": str,
    "run_budget": dict,
    "theme": str,
}


async def get_settings(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).settings.all())


async def put_settings(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    body = await json_body(request)
    for key, value in body.items():
        if key not in SETTING_TYPES:
            raise BadRequest(f"Unknown setting {key!r}")
        field(body, key, SETTING_TYPES[key])
        if value in ("", None):
            ctx.settings.delete(key)
        else:
            ctx.settings.set(key, value)
    return JSONResponse(ctx.settings.all())


routes = [
    Route("/characters", list_characters, methods=["GET"]),
    Route("/characters", create_character, methods=["POST"]),
    Route("/characters/import/png", import_character_png, methods=["POST"]),
    Route("/characters/{id}", get_character, methods=["GET"]),
    Route("/characters/{id}/export.json", export_character_json, methods=["GET"]),
    Route("/characters/{id}/export.png", export_character_png, methods=["GET"]),
    Route("/characters/{id}", update_character, methods=["PUT"]),
    Route("/characters/{id}", delete_character, methods=["DELETE"]),
    Route("/settings", get_settings, methods=["GET"]),
    Route("/settings", put_settings, methods=["PUT"]),
]
