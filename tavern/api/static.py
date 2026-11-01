"""Serving files from user folders: theme files at /themes/{id}/{path}, pictures at /assets/{name}.

These URLs are outside /api so a theme's CSS can use relative paths (`url(assets/bg.png)`).
Files are revalidated on every load (`no-cache` + ETag), so an edited theme shows up on reload.
Anything that is not CSS or JS gets a sandboxing CSP: an SVG opened directly cannot run script.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.routing import Route

from tavern.api.util import ctx_of
from tavern.formats.media import ACTIVE_EXTENSIONS, media_type

INERT_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"


def serve(path: Path) -> FileResponse:
    headers = {"Cache-Control": "no-cache"}
    if path.suffix.lower() not in ACTIVE_EXTENSIONS:
        headers["Content-Security-Policy"] = INERT_CSP
    return FileResponse(path, media_type=media_type(path.suffix), headers=headers)


async def theme_file(request: Request) -> FileResponse:
    return serve(ctx_of(request).themes.file(request.path_params["id"], request.path_params["path"]))


async def asset_file(request: Request) -> FileResponse:
    return serve(ctx_of(request).assets.path(request.path_params["name"]))


async def plugin_file(request: Request) -> FileResponse:
    """The web part of a plugin that is on (never its Python files)."""
    return serve(ctx_of(request).plugins.web_file(request.path_params["id"], request.path_params["path"]))


routes = [
    Route("/themes/{id}/{path:path}", theme_file, methods=["GET", "HEAD"]),
    Route("/plugins/{id}/{path:path}", plugin_file, methods=["GET", "HEAD"]),
    Route("/assets/{name}", asset_file, methods=["GET", "HEAD"]),
]
