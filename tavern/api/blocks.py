"""The block tree under a character card: list, create, change, delete, keywords, vectors."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, field, json_body
from tavern.services import character_tree


async def list_blocks(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    character_id = request.path_params["id"]
    blocks = character_tree.list_blocks(ctx, character_id)
    connection = character_tree.embedding_connection(ctx)
    vectorized: list[str] = []
    if connection is not None:
        vectorized = list(ctx.blocks.vectors([b["id"] for b in blocks], connection["model"]))
    return JSONResponse({"blocks": blocks, "vectorized": vectorized,
                         "links": [list(pair) for pair in ctx.blocks.links([character_id])],
                         "retrieval": character_tree.retrieval_settings(ctx)})


async def create_block(request: Request) -> JSONResponse:
    body = await json_body(request)
    block = await character_tree.create_block(ctx_of(request), request.path_params["id"], body)
    return JSONResponse(block, status_code=201)


async def save_layout(request: Request) -> JSONResponse:
    """Body: {items: [{id, x, y}, ...]} — positions on the bubble map after a drag."""
    body = await json_body(request)
    saved = await character_tree.save_layout(ctx_of(request), request.path_params["id"], body.get("items"))
    return JSONResponse({"saved": saved})


async def update_block(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(await character_tree.update_block(ctx_of(request), request.path_params["id"], body))


async def delete_block(request: Request) -> JSONResponse:
    await character_tree.delete_block(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


async def suggest_keywords(request: Request) -> JSONResponse:
    """Body (optional): {avoid: [...]}: words suggested before, so a second press gives new ones."""
    body = await json_body(request) if await request.body() else {}
    avoid = field(body, "avoid", list, default=[])
    words = await character_tree.suggest_keywords(ctx_of(request), request.path_params["id"],
                                                  [w for w in avoid if isinstance(w, str)])
    return JSONResponse({"keywords": words})


async def set_order(request: Request) -> JSONResponse:
    """Body: {place: 1..}: the bubble's number among its neighbours."""
    body = await json_body(request)
    await character_tree.set_order(ctx_of(request), request.path_params["id"], body.get("place"))
    return JSONResponse({"ok": True})


async def vectorize(request: Request) -> JSONResponse:
    """Body (optional): {character_ids: [...]}; default: the character in the path."""
    ctx = ctx_of(request)
    body = await json_body(request) if await request.body() else {}
    ids = field(body, "character_ids", list, default=[request.path_params["id"]])
    made = await character_tree.vectorize(ctx, [str(i) for i in ids])
    return JSONResponse({"made": made})


async def check_vectors(request: Request) -> JSONResponse:
    """Settings → Промпт «Проверить векторы»: one tiny request through the chosen connection."""
    return JSONResponse(await character_tree.check_vectors(ctx_of(request)))


async def add_link(request: Request) -> JSONResponse:
    """Body: {a, b}: two bubbles of this character to link."""
    body = await json_body(request)
    links = await character_tree.link_blocks(ctx_of(request), request.path_params["id"],
                                             field(body, "a", str), field(body, "b", str))
    return JSONResponse({"links": [list(pair) for pair in links]}, status_code=201)


async def remove_link(request: Request) -> JSONResponse:
    """?a=...&b=...: the two ends of the link to remove."""
    params = request.query_params
    links = await character_tree.unlink_blocks(ctx_of(request), request.path_params["id"],
                                               params.get("a", ""), params.get("b", ""))
    return JSONResponse({"links": [list(pair) for pair in links]})


async def set_chance(request: Request) -> JSONResponse:
    """Body: {chance: 0..100, under?: block id}: every bubble, or every sub-bubble of `under`."""
    body = await json_body(request)
    changed = await character_tree.set_chance(ctx_of(request), request.path_params["id"], body.get("chance"),
                                              field(body, "under", str, default=None))
    return JSONResponse({"changed": changed})


routes = [
    Route("/characters/{id}/blocks", list_blocks, methods=["GET"]),
    Route("/characters/{id}/blocks", create_block, methods=["POST"]),
    Route("/vectors/test", check_vectors, methods=["POST"]),
    Route("/characters/{id}/vectorize", vectorize, methods=["POST"]),
    Route("/characters/{id}/blocks/layout", save_layout, methods=["PUT"]),
    Route("/characters/{id}/blocks/chance", set_chance, methods=["PUT"]),
    Route("/characters/{id}/links", add_link, methods=["POST"]),
    Route("/characters/{id}/links", remove_link, methods=["DELETE"]),
    Route("/blocks/{id}", update_block, methods=["PUT"]),
    Route("/blocks/{id}", delete_block, methods=["DELETE"]),
    Route("/blocks/{id}/suggest-keywords", suggest_keywords, methods=["POST"]),
    Route("/blocks/{id}/order", set_order, methods=["PUT"]),
]
