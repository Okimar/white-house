"""Access control as a raw ASGI middleware.

Policy:
- Loopback-only mode (default): only clients on this machine are served, and the Host header must
  be a loopback name. The Host check blocks DNS-rebinding attacks from web pages.
- Remote mode (`server.host` not loopback, or `server.remote_access`): non-loopback clients must
  present the access token. With `remote_access` every client needs it, because a tunnel or proxy
  makes remote requests look local.
- In all modes, state-changing requests with a foreign Origin header are rejected (CSRF).

Token transport: `Authorization: Bearer <token>` or the `tavern_token` cookie, which
`GET /login?token=...` sets. Exempt paths: /login and /api/health.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

ASGIApp = Callable[..., Any]

COOKIE_NAME = "tavern_token"
PUBLIC_PATHS = frozenset({"/login", "/api/health"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def is_loopback_ip(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def host_header_name(value: str) -> str:
    """'example.com:8000' -> 'example.com', '[::1]:8000' -> '::1'."""
    value = value.strip().lower()
    if value.startswith("["):
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def tokens_match(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


class AccessControl:
    def __init__(
        self,
        app: ASGIApp,
        *,
        token_getter: Callable[[], str],
        remote_enabled: bool,
        trust_no_client: bool,
    ) -> None:
        """`trust_no_client`: require the token even from loopback clients (remote_access mode)."""
        self.app = app
        self.token_getter = token_getter
        self.remote_enabled = remote_enabled
        self.trust_no_client = trust_no_client

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        client = scope.get("client")
        client_is_local = is_loopback_ip(client[0] if client else None)
        host = host_header_name(headers.get("host", ""))

        if not self.remote_enabled:
            if not client_is_local:
                await _deny(send, 403, "remote_disabled", "Remote access is disabled")
                return
            if host not in LOOPBACK_HOSTNAMES:
                await _deny(send, 403, "bad_host", "Host header is not a loopback address")
                return

        origin = headers.get("origin")
        if method not in SAFE_METHODS and origin is not None and not _same_origin(origin, headers):
            await _deny(send, 403, "bad_origin", "Cross-origin request rejected")
            return

        if path == "/login" and method == "GET":
            await self._login(scope, send)
            return

        needs_token = self.remote_enabled and (self.trust_no_client or not client_is_local)
        if needs_token and path not in PUBLIC_PATHS and not self._has_valid_token(headers):
            await _deny(send, 401, "unauthorized", "Access token required. Open /login?token=...")
            return

        await self.app(scope, receive, send)

    def _has_valid_token(self, headers: dict[str, str]) -> bool:
        expected = self.token_getter()
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and tokens_match(auth[7:].strip(), expected):
            return True
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
        except Exception:  # malformed cookie header: treat as absent
            return False
        morsel = cookie.get(COOKIE_NAME)
        return morsel is not None and tokens_match(morsel.value, expected)

    async def _login(self, scope: dict, send: Callable) -> None:
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        supplied = (query.get("token") or [""])[0]
        if not tokens_match(supplied, self.token_getter()):
            await _deny(send, 401, "unauthorized", "Invalid token")
            return
        secure = "; Secure" if scope.get("scheme") == "https" else ""
        cookie = (
            f"{COOKIE_NAME}={supplied}; Path=/; HttpOnly; SameSite=Strict; "
            f"Max-Age=31536000{secure}"
        )
        await send(
            {
                "type": "http.response.start",
                "status": 303,
                "headers": [
                    (b"location", b"/"),
                    (b"set-cookie", cookie.encode("latin-1")),
                    (b"cache-control", b"no-store"),
                    (b"content-length", b"0"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})


def _headers(scope: dict) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _same_origin(origin: str, headers: dict[str, str]) -> bool:
    if origin == "null":
        return False
    parsed = urlsplit(origin)
    return parsed.netloc.lower() == headers.get("host", "").strip().lower()


async def _deny(send: Callable, status: int, code: str, message: str) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
