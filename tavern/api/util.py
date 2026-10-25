"""Helpers shared by route modules: request body parsing and field validation."""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request

from tavern.context import AppContext
from tavern.errors import AppError


class BadRequest(AppError):
    code = "bad_request"


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


async def json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BadRequest("Request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise BadRequest("Request body must be a JSON object")
    return body


def field(body: dict[str, Any], key: str, kind: type, *, default: Any = ..., allow_empty: bool = True) -> Any:
    """Read `key` from a JSON body, checking its type. Missing without default is an error."""
    if key not in body:
        if default is ...:
            raise BadRequest(f"Field {key!r} is required")
        return default
    value = body[key]
    # bool is a subclass of int: reject it where a number is expected.
    if not isinstance(value, kind) or (kind in (int, float) and isinstance(value, bool)):
        raise BadRequest(f"Field {key!r} must be of type {kind.__name__}")
    if kind is str and not allow_empty and not value.strip():
        raise BadRequest(f"Field {key!r} must not be empty")
    return value
