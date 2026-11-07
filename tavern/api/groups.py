"""Groups of characters, lorebooks and chats: thin routes over services/groups.py."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, json_body
from tavern.services import groups


async def list_groups(request: Request) -> JSONResponse:
    """Groups of one kind (?kind=character|lorebook|chat) and which item sits in which group."""
    return JSONResponse(groups.overview(ctx_of(request), request.query_params.get("kind")))


async def create_group(request: Request) -> JSONResponse:
    """Body: {kind, name}."""
    body = await json_body(request)
    return JSONResponse(await groups.create(ctx_of(request), body.get("kind"), body.get("name")), status_code=201)


async def assign_items(request: Request) -> JSONResponse:
    """Body: {kind, item_ids: [...], group_id: id | null}. null frees the items («Без группы»)."""
    body = await json_body(request)
    return JSONResponse(await groups.assign(ctx_of(request), body.get("kind"), body.get("item_ids"),
                                            body.get("group_id")))


async def rename_group(request: Request) -> JSONResponse:
    """Body: {name}."""
    body = await json_body(request)
    return JSONResponse(await groups.rename(ctx_of(request), request.path_params["id"], body.get("name")))


async def delete_group(request: Request) -> JSONResponse:
    """Delete a group; its items stay and go back to «Без группы»."""
    await groups.delete(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


routes = [
    Route("/groups", list_groups, methods=["GET"]),
    Route("/groups", create_group, methods=["POST"]),
    Route("/groups/assign", assign_items, methods=["POST"]),
    Route("/groups/{id}", rename_group, methods=["PUT"]),
    Route("/groups/{id}", delete_group, methods=["DELETE"]),
]
