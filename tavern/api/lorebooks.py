"""Lorebooks: the books, where they are switched on, ST files. A book's bubbles and its history
use the same handlers as a character's (api/blocks.py, api/characters.py): the book id is the owner.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api import blocks as blocks_api
from tavern.api import characters as characters_api
from tavern.api.util import BadRequest, attachment, copy_before, ctx_of, field, json_body, read_body
from tavern.services import lorebooks

MAX_FILE = 20 * 1024 * 1024


async def list_books(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse({"lorebooks": lorebooks.list_books(ctx), "settings": lorebooks.lore_settings(ctx)})


async def create_book(request: Request) -> JSONResponse:
    """Body: {name, description?}."""
    return JSONResponse(await lorebooks.create_book(ctx_of(request), await json_body(request)), status_code=201)


async def import_book(request: Request) -> JSONResponse:
    """Body: the file's bytes (SillyTavern world .json, a card's book, or our export). `?name=` is
    the file name, used when the file has no name inside."""
    blob = await read_body(request, MAX_FILE)
    await copy_before(request, "import-lorebook")
    try:
        data = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BadRequest("The lorebook file is not valid JSON", code="bad_lorebook") from None
    name = request.query_params.get("name", "").rsplit(".", 1)[0][:200]
    return JSONResponse(await lorebooks.import_book(ctx_of(request), data, name), status_code=201)


async def get_book(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    book = ctx.lorebooks.get(request.path_params["id"])
    return JSONResponse({**book, "places": ctx.lorebooks.places_of(book["id"])})


async def update_book(request: Request) -> JSONResponse:
    return JSONResponse(await lorebooks.update_book(ctx_of(request), request.path_params["id"],
                                                    await json_body(request)))


async def delete_book(request: Request) -> JSONResponse:
    ctx_of(request).lorebooks.get(request.path_params["id"])
    await copy_before(request, "delete-lorebook")
    await lorebooks.delete_book(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


async def export_book(request: Request) -> Response:
    ctx = ctx_of(request)
    data = lorebooks.export_book(ctx, request.path_params["id"])
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": attachment(f"{data['name']}.json")})


async def get_uses(request: Request) -> JSONResponse:
    """?scope=global|chat|character&target=<chat or character id>"""
    params = request.query_params
    ids = lorebooks.uses(ctx_of(request), params.get("scope", ""), params.get("target") or None)
    return JSONResponse({"lorebook_ids": ids})


async def put_uses(request: Request) -> JSONResponse:
    """Body: {scope, target?, lorebook_ids: [...]}: exactly these books, in this order."""
    body = await json_body(request)
    ids = await lorebooks.set_uses(ctx_of(request), field(body, "scope", str),
                                   field(body, "target", str, default=None), body.get("lorebook_ids"))
    return JSONResponse({"lorebook_ids": ids})


routes = [
    Route("/lorebooks", list_books, methods=["GET"]),
    Route("/lorebooks", create_book, methods=["POST"]),
    Route("/lorebooks/import", import_book, methods=["POST"]),
    Route("/lorebook-uses", get_uses, methods=["GET"]),
    Route("/lorebook-uses", put_uses, methods=["PUT"]),
    Route("/lorebooks/{id}", get_book, methods=["GET"]),
    Route("/lorebooks/{id}", update_book, methods=["PUT"]),
    Route("/lorebooks/{id}", delete_book, methods=["DELETE"]),
    Route("/lorebooks/{id}/export.json", export_book, methods=["GET"]),
    # the book's bubbles and history: the same handlers as a character's
    Route("/lorebooks/{id}/blocks", blocks_api.list_blocks, methods=["GET"]),
    Route("/lorebooks/{id}/blocks", blocks_api.create_block, methods=["POST"]),
    Route("/lorebooks/{id}/vectorize", blocks_api.vectorize, methods=["POST"]),
    Route("/lorebooks/{id}/blocks/layout", blocks_api.save_layout, methods=["PUT"]),
    Route("/lorebooks/{id}/blocks/chance", blocks_api.set_chance, methods=["PUT"]),
    Route("/lorebooks/{id}/links", blocks_api.add_link, methods=["POST"]),
    Route("/lorebooks/{id}/links", blocks_api.remove_link, methods=["DELETE"]),
    Route("/lorebooks/{id}/history", characters_api.character_history, methods=["GET"]),
    Route("/lorebooks/{id}/history/close", characters_api.close_history, methods=["POST"]),
    Route("/lorebooks/{id}/history/{version}/restore", characters_api.restore_version, methods=["POST"]),
    Route("/lorebooks/{id}/undo", characters_api.undo_character, methods=["POST"]),
]
