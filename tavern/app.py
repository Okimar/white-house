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
from tavern.api import assets as assets_api
from tavern.api import backups as backups_api
from tavern.api import chats as chats_api
from tavern.api import characters as characters_api
from tavern.api import connections as connections_api
from tavern.api import skills as skills_api
from tavern.api import templates as templates_api
from tavern.api import profiles as profiles_api
from tavern.api import spending as spending_api
from tavern.api import jsonl as jsonl_api
from tavern.api import blocks as blocks_api
from tavern.api import lorebooks as lorebooks_api
from tavern.api import notes as notes_api
from tavern.api import plugins as plugins_api
from tavern.api import assistant as assistant_api
from tavern.api import prompts as prompts_api
from tavern.api import static as static_api
from tavern.api import themes as themes_api
from tavern.context import AppContext
from tavern.errors import AppError, NotFound
from tavern.paths import WEB_DIR
from tavern.security import AccessControl

log = logging.getLogger(__name__)

# The page may load code, styles, pictures and fonts only from this server. A theme (possibly
# downloaded from someone else) therefore cannot send chats anywhere or pull in remote scripts.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' data: blob:; font-src 'self' data:; "
    "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
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
        *templates_api.routes,
        *skills_api.routes,
        *profiles_api.routes,
        *spending_api.routes,
        *assistant_api.routes,
        *themes_api.routes,
        *assets_api.routes,
        *jsonl_api.routes,
        *prompts_api.routes,
        *blocks_api.routes,
        *lorebooks_api.routes,
        *notes_api.routes,
        *plugins_api.routes,
        *backups_api.routes,
        *chats_api.routes,
        # Must stay last: unknown /api paths get JSON 404 instead of falling through to static files.
        Route("/{path:path}", api_not_found, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
    ]


def create_app(ctx: AppContext) -> Callable[..., Any]:
    app = Starlette(
        routes=[
            Mount("/api", routes=api_routes()),
            *static_api.routes,
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
                headers = [*message.get("headers", []), *extra]
                # A route may set a stricter policy of its own (sandboxed theme files); keep it.
                if not any(name.lower() == b"content-security-policy" for name, _ in headers):
                    headers.append((b"content-security-policy", CONTENT_SECURITY_POLICY.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await app(scope, receive, send_with_headers)

    return wrapped
