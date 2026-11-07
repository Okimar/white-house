"""Themes: list, create, edit files, delete, share as one file, version history.

The theme files themselves are served by `api/static.py` at /themes/{id}/{path}.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, ctx_of, field, json_body, read_body
from tavern.services import look
from tavern.storage.themes import MAX_FILE_BYTES, MAX_THEME_BYTES, safe_relpath


async def list_themes(request: Request) -> JSONResponse:
    """Installed themes, plus deleted user themes that can be restored from their history."""
    themes = ctx_of(request).themes
    return JSONResponse({"themes": themes.list(), "deleted": themes.deleted()})


async def get_theme(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    theme_id = request.path_params["id"]
    return JSONResponse({**ctx.themes.get(theme_id), "files": ctx.themes.files(theme_id)})


async def create_theme(request: Request) -> JSONResponse:
    """Body: {name, id?, from?}. `from` copies an existing theme (built-in or user)."""
    body = await json_body(request)
    theme = ctx_of(request).themes.create(
        field(body, "name", str, allow_empty=False),
        theme_id=field(body, "id", str, default=None),
        source=field(body, "from", str, default=None),
    )
    return JSONResponse(theme, status_code=201)


async def delete_theme(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    theme_id = request.path_params["id"]
    ctx.themes.delete(theme_id)
    # Nothing may keep pointing at a theme that is gone: fall back to the default.
    if ctx.settings.get("theme") == theme_id:
        ctx.settings.delete("theme")
    per_character = ctx.settings.get("character_themes") or {}
    if theme_id in per_character.values():
        ctx.settings.set("character_themes", {k: v for k, v in per_character.items() if v != theme_id})
    return JSONResponse({"ok": True})


async def export_theme(request: Request) -> Response:
    name, blob = ctx_of(request).themes.export(request.path_params["id"])
    return Response(blob, media_type="application/zip", headers={"Content-Disposition": attachment(name)})


async def import_theme(request: Request) -> JSONResponse:
    """Body: the theme file. `?replace=1` replaces an installed theme with the same id (409 otherwise)."""
    blob = await read_body(request, MAX_THEME_BYTES)
    replace = request.query_params.get("replace") in ("1", "true")
    return JSONResponse(ctx_of(request).themes.import_zip(blob, replace=replace), status_code=201)


async def list_versions(request: Request) -> JSONResponse:
    return JSONResponse({"versions": ctx_of(request).themes.versions(request.path_params["id"])})


async def restore_version(request: Request) -> JSONResponse:
    body = await json_body(request)
    theme = ctx_of(request).themes.restore(request.path_params["id"], field(body, "version", str, allow_empty=False))
    return JSONResponse(theme)


async def put_file(request: Request) -> JSONResponse:
    """Body: raw file content. Used by agents and the future in-app assistant to edit a theme."""
    data = await read_body(request, MAX_FILE_BYTES)
    theme = ctx_of(request).themes.write_file(request.path_params["id"], request.path_params["path"], data)
    return JSONResponse(theme)


async def put_colors(request: Request) -> JSONResponse:
    """Body: {colors: {bg: "#aabbcc", ...}}: the main colours of a user theme ({} removes them)."""
    body = await json_body(request)
    colors = field(body, "colors", dict)
    return JSONResponse(ctx_of(request).themes.set_colors(request.path_params["id"], colors))


async def delete_file(request: Request) -> JSONResponse:
    ctx_of(request).themes.delete_file(request.path_params["id"], request.path_params["path"])
    return JSONResponse({"ok": True})


async def add_asset(request: Request) -> JSONResponse:
    """Copy a picture from the image library into a theme. Body: {asset, path?}."""
    ctx = ctx_of(request)
    body = await json_body(request)
    name = field(body, "asset", str, allow_empty=False)
    source = ctx.assets.path(name)
    rel = safe_relpath(field(body, "path", str, default=f"assets/{name}"))
    if rel.rsplit(".", 1)[-1].lower() != source.suffix.lstrip(".").lower():
        raise BadRequest("The theme file must keep the picture's extension")
    theme = ctx.themes.write_file(request.path_params["id"], rel, source.read_bytes())
    return JSONResponse({**theme, "path": rel})


async def look_status(request: Request) -> JSONResponse:
    """Safe mode on or off and what the last update switched off (services/look.py)."""
    run_safe = bool(getattr(request.app.state, "safe_run", False))
    return JSONResponse(look.status(ctx_of(request), run_safe=run_safe))


async def look_restore(request: Request) -> JSONResponse:
    """Switch the look that an update parked back on."""
    return JSONResponse(look.restore(ctx_of(request)))


async def look_dismiss(request: Request) -> JSONResponse:
    look.dismiss(ctx_of(request))
    return JSONResponse({"ok": True})


async def look_safe(request: Request) -> JSONResponse:
    """Body: {on}. Persistent safe mode, the same switch as `python -m tavern safe-mode`."""
    body = await json_body(request)
    look.set_safe_mode(ctx_of(request), field(body, "on", bool))
    return await look_status(request)


routes = [
    Route("/look", look_status, methods=["GET"]),
    Route("/look/restore", look_restore, methods=["POST"]),
    Route("/look/dismiss", look_dismiss, methods=["POST"]),
    Route("/look/safe", look_safe, methods=["POST"]),
    Route("/themes", list_themes, methods=["GET"]),
    Route("/themes", create_theme, methods=["POST"]),
    Route("/themes/import", import_theme, methods=["POST"]),
    Route("/themes/{id}", get_theme, methods=["GET"]),
    Route("/themes/{id}", delete_theme, methods=["DELETE"]),
    Route("/themes/{id}/export", export_theme, methods=["GET"]),
    Route("/themes/{id}/versions", list_versions, methods=["GET"]),
    Route("/themes/{id}/restore", restore_version, methods=["POST"]),
    Route("/themes/{id}/colors", put_colors, methods=["PUT"]),
    Route("/themes/{id}/assets", add_asset, methods=["POST"]),
    Route("/themes/{id}/files/{path:path}", put_file, methods=["PUT"]),
    Route("/themes/{id}/files/{path:path}", delete_file, methods=["DELETE"]),
]
