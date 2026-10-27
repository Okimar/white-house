"""API connections. The API key is write-only: responses only say whether one is set."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body
from tavern.providers.registry import key_name


def _public(ctx: Any, conn: dict[str, Any]) -> dict[str, Any]:
    return {**conn, "has_key": ctx.secrets.has(key_name(conn["id"]))}


async def list_connections(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse(
        {
            "connections": [_public(ctx, c) for c in ctx.connections.list()],
            "kinds": ctx.providers.kinds,
            "active": ctx.settings.get("active_connection"),
        }
    )


async def create_connection(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    body = await json_body(request)
    kind = field(body, "kind", str, default="openai")
    if kind not in ctx.providers.kinds:
        raise BadRequest(f"Unknown provider kind {kind!r}. Available: {', '.join(ctx.providers.kinds)}")
    conn = ctx.connections.create(
        name=field(body, "name", str, allow_empty=False),
        kind=kind,
        base_url=field(body, "base_url", str, allow_empty=False).strip(),
        model=field(body, "model", str, default=""),
        params=field(body, "params", dict, default={}),
    )
    api_key = field(body, "api_key", str, default="")
    if api_key:
        ctx.secrets.set(key_name(conn["id"]), api_key)
    if ctx.settings.get("active_connection") is None:
        ctx.settings.set("active_connection", conn["id"])
    return JSONResponse(_public(ctx, conn), status_code=201)


async def update_connection(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    conn_id = request.path_params["id"]
    body = await json_body(request)
    conn = ctx.connections.update(
        conn_id,
        name=field(body, "name", str, default=None, allow_empty=False),
        kind=field(body, "kind", str, default=None),
        base_url=field(body, "base_url", str, default=None, allow_empty=False),
        model=field(body, "model", str, default=None),
        params=field(body, "params", dict, default=None),
    )
    if "api_key" in body:
        api_key = field(body, "api_key", str)
        if api_key:
            ctx.secrets.set(key_name(conn_id), api_key)
        else:
            ctx.secrets.delete(key_name(conn_id))
    return JSONResponse(_public(ctx, conn))


async def delete_connection(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    conn_id = request.path_params["id"]
    ctx.connections.delete(conn_id)
    ctx.secrets.delete(key_name(conn_id))
    if ctx.settings.get("active_connection") == conn_id:
        ctx.settings.delete("active_connection")
    return JSONResponse({"ok": True})


async def _models_of(provider: Any) -> JSONResponse:
    try:
        return JSONResponse({"models": await provider.list_models()})
    finally:
        await provider.aclose()


async def list_models(request: Request) -> JSONResponse:
    """Ask the backend which models it offers (also serves as a connection test)."""
    ctx = ctx_of(request)
    return await _models_of(ctx.providers.create(ctx.connections.get(request.path_params["id"])))


async def probe_models(request: Request) -> JSONResponse:
    """Model list for settings not saved yet, so the form can offer models while it is filled in."""
    ctx = ctx_of(request)
    body = await json_body(request)
    kind = field(body, "kind", str, default="openai")
    if kind not in ctx.providers.kinds:
        raise BadRequest(f"Unknown provider kind {kind!r}. Available: {', '.join(ctx.providers.kinds)}")
    draft = {
        "id": "",
        "kind": kind,
        "base_url": field(body, "base_url", str, allow_empty=False).strip(),
        "params": {},
    }
    return await _models_of(ctx.providers.create(draft, api_key=field(body, "api_key", str, default="")))


routes = [
    Route("/connections", list_connections, methods=["GET"]),
    Route("/connections", create_connection, methods=["POST"]),
    Route("/connections/models", probe_models, methods=["POST"]),
    Route("/connections/{id}", update_connection, methods=["PUT"]),
    Route("/connections/{id}", delete_connection, methods=["DELETE"]),
    Route("/connections/{id}/models", list_models, methods=["GET"]),
]
