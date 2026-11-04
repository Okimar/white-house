"""Image library: list, upload, delete, generate with the picture connection.

The pictures themselves are served by `api/static.py` at /assets/{name}.
"""

from __future__ import annotations

import re

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body, read_body
from tavern.errors import NotFound
from tavern.services import spending
from tavern.storage.assets import MAX_IMAGE_BYTES

SIZE_RE = re.compile(r"^\d{2,4}x\d{2,4}$")


async def list_assets(request: Request) -> JSONResponse:
    return JSONResponse({"assets": ctx_of(request).assets.list()})


async def upload_asset(request: Request) -> JSONResponse:
    """Body: the picture's bytes. `?name=` suggests a file name; the type comes from the content."""
    blob = await read_body(request, MAX_IMAGE_BYTES)
    asset = ctx_of(request).assets.save(request.query_params.get("name", "image"), blob)
    return JSONResponse(asset, status_code=201)


async def delete_asset(request: Request) -> JSONResponse:
    ctx_of(request).assets.delete(request.path_params["name"])
    return JSONResponse({"ok": True})


async def generate_asset(request: Request) -> JSONResponse:
    """Body: {prompt, size?, name?}. Uses the connection chosen in settings as `image_connection`."""
    ctx = ctx_of(request)
    body = await json_body(request)
    prompt = field(body, "prompt", str, allow_empty=False).strip()
    size = field(body, "size", str, default="1024x1024")
    if not SIZE_RE.match(size):
        raise BadRequest("Size must look like 1024x1024")
    connection_id = ctx.settings.get("image_connection")
    if not connection_id:
        raise BadRequest("Choose a connection for pictures in Settings first", code="no_image_connection")
    try:
        connection = ctx.connections.get(connection_id)
    except NotFound:
        raise BadRequest("The connection for pictures was deleted. Choose another one in Settings",
                         code="no_image_connection") from None
    provider = ctx.providers.create(connection)
    try:
        blob = await provider.generate_image(prompt, model=connection.get("model", ""), size=size)
        spending.record_call(ctx, connection, "picture", model=connection.get("model", ""))
    finally:
        await provider.aclose()
    name = field(body, "name", str, default="") or prompt[:40]
    return JSONResponse(ctx.assets.save(name, blob), status_code=201)


routes = [
    Route("/assets", list_assets, methods=["GET"]),
    Route("/assets", upload_asset, methods=["POST"]),
    Route("/assets/generate", generate_asset, methods=["POST"]),
    Route("/assets/{name}", delete_asset, methods=["DELETE"]),
]
