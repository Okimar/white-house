"""Updating the app from the page (ADR 0016): what is installed, look for a newer version, update.
Logic lives in services/updates.py; git and pip are slow, so they run off the event loop. After a successful
update the process is asked to leave a moment later, once this answer has been sent."""

from __future__ import annotations

import asyncio

import anyio
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of
from tavern.services import updates


async def get_info(request: Request) -> JSONResponse:
    return JSONResponse(await anyio.to_thread.run_sync(updates.info))


async def check(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse(await anyio.to_thread.run_sync(lambda: updates.check(ctx)))


async def _leave() -> None:
    await asyncio.sleep(1.0)  # let the answer reach the page first
    updates.request_exit()


async def apply(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    done = await anyio.to_thread.run_sync(lambda: updates.apply(ctx))
    return JSONResponse(done, background=BackgroundTask(_leave))


routes = [
    Route("/updates", get_info, methods=["GET"]),
    Route("/updates/check", check, methods=["POST"]),
    Route("/updates/apply", apply, methods=["POST"]),
]
