"""Search across all chats: thin routes over services/search.py (plain, by the model, stop, info)."""

from __future__ import annotations

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, json_body
from tavern.services import search

FILTER_KEYS = ("character", "since", "until", "group", "chat")


def _filters(source) -> dict:
    return {k: source.get(k) for k in FILTER_KEYS if source.get(k)}


async def plain(request: Request) -> JSONResponse:
    """?scope=chats|assistant&q=...&offset=&limit=&character=&since=&until=&group=(id|none)&chat="""
    ctx, q = ctx_of(request), request.query_params
    result = await anyio.to_thread.run_sync(lambda: search.search(
        ctx, q.get("scope", "chats"), q.get("q"), _filters(q), q.get("offset", 0), q.get("limit", search.PAGE)))
    return JSONResponse(result)


async def info(request: Request) -> JSONResponse:
    return JSONResponse(search.info(ctx_of(request)))


async def ask(request: Request) -> JSONResponse:
    """Body: {scope, question, token?, character?, since?, until?, group?, chat?}."""
    body = await json_body(request)
    token = body.get("token") if isinstance(body.get("token"), str) else ""
    return JSONResponse(await search.ask(ctx_of(request), body.get("scope", "chats"), body.get("question"),
                                         _filters(body), token))


async def stop(request: Request) -> JSONResponse:
    body = await json_body(request)
    token = body.get("token")
    return JSONResponse({"stopped": search.stop(token) if isinstance(token, str) else False})


routes = [
    Route("/search", plain, methods=["GET"]),
    Route("/search/info", info, methods=["GET"]),
    Route("/search/ask", ask, methods=["POST"]),
    Route("/search/stop", stop, methods=["POST"]),
]
