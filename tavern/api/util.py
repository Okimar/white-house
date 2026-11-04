"""Helpers shared by route modules: request body parsing and field validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anyio
from starlette.requests import Request

from tavern.context import AppContext
from tavern.errors import AppError


class BadRequest(AppError):
    code = "bad_request"


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


async def copy_before(request: Request, reason: str) -> None:
    """A backup before a risky action (stage 9), off the event loop. Never blocks the action."""
    await anyio.to_thread.run_sync(ctx_of(request).backups.before, reason)


async def json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise BadRequest("Request body must be valid JSON") from None
    if not isinstance(body, dict):
        raise BadRequest("Request body must be a JSON object")
    return body


async def read_body(request: Request, limit: int) -> bytes:
    """Raw request body, refusing more than `limit` bytes without reading it all into memory."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise BadRequest(f"Upload is too large (max {limit // (1024 * 1024)} MB)", code="too_large", status=413)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BadRequest(f"Upload is too large (max {limit // (1024 * 1024)} MB)", code="too_large", status=413)
        chunks.append(chunk)
    return b"".join(chunks)


async def save_body(request: Request, path: Path, limit: int) -> int:
    """The request body into a file, piece by piece: a large one must not fill a phone's memory.
    Returns its size."""
    message = f"Upload is too large (max {limit // (1024 * 1024)} MB)"
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise BadRequest(message, code="too_large", status=413)
    size = 0
    async with await anyio.open_file(path, "wb") as out:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise BadRequest(message, code="too_large", status=413)
            await out.write(chunk)
    return size


def attachment(filename: str) -> str:
    """Content-Disposition for any name: headers are latin-1, so non-ASCII goes in filename* (RFC 6266)."""
    fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def field(body: dict[str, Any], key: str, kind: type, *, default: Any = ..., allow_empty: bool = True) -> Any:
    """Read `key` from a JSON body, checking its type. Missing without default is an error."""
    if key not in body:
        if default is ...:
            raise BadRequest(f"Field {key!r} is required")
        return default
    value = body[key]
    # JSON null for an optional field means "not given" (browsers send {"character_id": null}).
    if value is None and default is None:
        return None
    # bool is a subclass of int: reject it where a number is expected.
    if not isinstance(value, kind) or (kind in (int, float) and isinstance(value, bool)):
        raise BadRequest(f"Field {key!r} must be of type {kind.__name__}")
    if kind is str and not allow_empty and not value.strip():
        raise BadRequest(f"Field {key!r} must not be empty")
    return value
