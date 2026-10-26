"""ASGI application factory."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from tavern import __version__
from tavern.api import chats as chats_api
from tavern.api import characters as characters_api
from tavern.api import connections as connections_api
from tavern.api import jsonl as jsonl_api
from tavern.context import AppContext
from tavern.errors import AppError, NotFound
from tavern.paths import WEB_DIR
from tavern.security import AccessControl

log = logging.getLogger(__name__)

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


async def health(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse(
        {
            "status": "ok",
            "version": __version__,
            "schema_version": ctx.db.schema_version,
            "sandbox": ctx.sandbox,
        }
    )


async def api_not_found(request: Request) -> JSONResponse:
    raise NotFound(f"No API route {request.method} {request.url.path}")


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(exc.to_dict(), status_code=exc.status)


async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error in %s %s", request.method, request.url.path)
    return JSONResponse(
        {"error": {"code": "internal", "message": "Internal server error, see data/logs"}},
        status_code=500,
    )


def api_routes() -> list[Route]:
    return [
        Route("/health", health, methods=["GET"]),
        *characters_api.routes,
        *connections_api.routes,
        *jsonl_api.routes,
        *chats_api.routes,
        # Must stay last: unknown /api paths get JSON 404 instead of falling through to static files.
        Route("/{path:path}", api_not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
    ]


def create_app(ctx: AppContext) -> Callable[..., Any]:
    app = Starlette(
        routes=[
            Mount("/api", routes=api_routes()),
            Mount("/", app=StaticFiles(directory=WEB_DIR, html=True), name="web"),
        ],
        exception_handlers={AppError: handle_app_error, Exception: handle_unexpected},
    )
    app.state.ctx = ctx
    guarded = AccessControl(
        app,
        token_getter=ctx.secrets.access_token,
        remote_enabled=ctx.config.remote_enabled,
        trust_no_client=ctx.config.server.remote_access,
    )
    return with_headers(guarded, SECURITY_HEADERS)


def with_headers(app: Callable[..., Any], extra: list[tuple[bytes, bytes]]) -> Callable[..., Any]:
    async def wrapped(scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await app(scope, receive, send_with_headers)

    return wrapped
