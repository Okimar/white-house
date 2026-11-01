"""Plugins: list, turn on (with approval) and off, settings, model access, install from a file or a
GitHub link, updates, delete; chat commands, the plugins' own actions, the "may it?" questions and
the app-wide event stream (`GET /events`). Logic lives in plugins/manager.py.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body, read_body
from tavern.plugins.manifest import MAX_PLUGIN_BYTES

HEARTBEAT_SECONDS = 15.0


def _flag(request: Request, name: str) -> bool:
    return request.query_params.get(name, "") in ("1", "true", "yes")


async def list_plugins(request: Request) -> JSONResponse:
    plugins = ctx_of(request).plugins
    return JSONResponse({
        "plugins": plugins.list(),
        "commands": plugins.command_list(),
        "generators": ctx_of(request).generators.describe(),
        "server_off": not plugins.load_server,
        "asks": [a.info for a in plugins.asks.values()],
    })


async def get_plugin(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).plugins.get(request.path_params["id"]))


async def enable(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(ctx_of(request).plugins.enable(
        request.path_params["id"], approve=field(body, "approve", bool, default=False)))


async def disable(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).plugins.disable(request.path_params["id"]))


async def save_settings(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).plugins.save_settings(request.path_params["id"], await json_body(request)))


async def save_access(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).plugins.save_access(request.path_params["id"], await json_body(request)))


async def delete(request: Request) -> Response:
    ctx_of(request).plugins.delete(request.path_params["id"], data=_flag(request, "data"))
    return Response(status_code=204)


async def install_file(request: Request) -> JSONResponse:
    blob = await read_body(request, MAX_PLUGIN_BYTES)
    if not blob:
        raise BadRequest("Пустой файл")
    plugin = ctx_of(request).plugins.install_zip(blob, replace=_flag(request, "replace"))
    return JSONResponse(plugin, status_code=201)


async def install_url(request: Request) -> JSONResponse:
    body = await json_body(request)
    plugin = await ctx_of(request).plugins.install_github(
        field(body, "url", str, allow_empty=False), replace=field(body, "replace", bool, default=False))
    return JSONResponse(plugin, status_code=201)


async def check_update(request: Request) -> JSONResponse:
    return JSONResponse(await ctx_of(request).plugins.check_update(request.path_params["id"]))


async def update(request: Request) -> JSONResponse:
    return JSONResponse(await ctx_of(request).plugins.update(request.path_params["id"]))


async def action(request: Request) -> JSONResponse:
    """The plugin's web part calls its server part: body = payload, answer = {"result": ...}."""
    result = await ctx_of(request).plugins.call_action(
        request.path_params["id"], request.path_params["name"], await json_body(request))
    return JSONResponse({"result": result})


async def command(request: Request) -> JSONResponse:
    """Body {chat_id, text}: "/roll 2d6". {"handled": false} when no plugin knows the command."""
    body = await json_body(request)
    return JSONResponse(await ctx_of(request).plugins.run_command(
        field(body, "chat_id", str, allow_empty=False), field(body, "text", str, allow_empty=False)))


async def list_asks(request: Request) -> JSONResponse:
    return JSONResponse({"asks": [a.info for a in ctx_of(request).plugins.asks.values()]})


async def answer_ask(request: Request) -> Response:
    body = await json_body(request)
    ctx_of(request).plugins.answer(request.path_params["ask_id"], field(body, "allow", bool))
    return Response(status_code=204)


async def events(request: Request) -> StreamingResponse:
    """App-wide notices (core/notices.py) for as long as the page is open."""
    notices = ctx_of(request).notices

    async def stream():  # type: ignore[no-untyped-def]
        yield b": open\n\n"
        async for event in notices.subscribe(heartbeat=HEARTBEAT_SECONDS):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


routes = [
    Route("/events", events, methods=["GET"]),
    Route("/plugins", list_plugins, methods=["GET"]),
    Route("/plugins/install", install_file, methods=["POST"]),
    Route("/plugins/install-url", install_url, methods=["POST"]),
    Route("/plugins/command", command, methods=["POST"]),
    Route("/plugins/asks", list_asks, methods=["GET"]),
    Route("/plugins/asks/{ask_id}", answer_ask, methods=["POST"]),
    Route("/plugins/{id}", get_plugin, methods=["GET"]),
    Route("/plugins/{id}", delete, methods=["DELETE"]),
    Route("/plugins/{id}/enable", enable, methods=["POST"]),
    Route("/plugins/{id}/disable", disable, methods=["POST"]),
    Route("/plugins/{id}/settings", save_settings, methods=["PUT"]),
    Route("/plugins/{id}/access", save_access, methods=["PUT"]),
    Route("/plugins/{id}/update", check_update, methods=["GET"]),
    Route("/plugins/{id}/update", update, methods=["POST"]),
    Route("/plugins/{id}/actions/{name}", action, methods=["POST"]),
]
