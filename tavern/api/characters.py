"""Characters and settings."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body
from tavern.formats.cards import parse_card


async def list_characters(request: Request) -> JSONResponse:
    return JSONResponse({"characters": ctx_of(request).characters.list()})


async def get_character(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).characters.get(request.path_params["id"]))


async def create_character(request: Request) -> JSONResponse:
    """Body: a character card (V1 flat, V2 or V3 JSON) or {"name": ...}."""
    spec, data = parse_card(await json_body(request))
    return JSONResponse(ctx_of(request).characters.create(data, spec=spec), status_code=201)


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
    Route("/characters/{id}", get_character, methods=["GET"]),
    Route("/characters/{id}", update_character, methods=["PUT"]),
    Route("/characters/{id}", delete_character, methods=["DELETE"]),
    Route("/settings", get_settings, methods=["GET"]),
    Route("/settings", put_settings, methods=["PUT"]),
]
