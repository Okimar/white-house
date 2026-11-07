"""«Изменения»: the list of what the assistant changed (all conversations), what exactly, and rollback.
Logic lives in services/changes.py."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, json_body
from tavern.services import changes


def _int(value: str | None, name: str, default: int = 0) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        raise BadRequest(f"{name} must be a number") from None


def _action_id(request: Request) -> int:
    try:
        return int(request.path_params["id"])
    except ValueError:
        raise BadRequest("Action id must be a number") from None


async def list_changes(request: Request) -> JSONResponse:
    q = request.query_params
    return JSONResponse(changes.list_changes(
        ctx_of(request), since=q.get("since", ""), until=q.get("until", ""), conversation=q.get("conversation", ""),
        before=_int(q.get("before"), "before"), limit=_int(q.get("limit"), "limit", changes.PAGE)))


async def details(request: Request) -> JSONResponse:
    return JSONResponse(changes.details(ctx_of(request), _action_id(request)))


async def plan(request: Request) -> JSONResponse:
    return JSONResponse(changes.plan(ctx_of(request), _action_id(request)))


async def rollback(request: Request) -> JSONResponse:
    """Body: {accepted?: true once the person has read the plan's warnings}."""
    body = await json_body(request)
    return JSONResponse(await changes.rollback(ctx_of(request), _action_id(request), body.get("accepted") is True))


routes = [
    Route("/changes", list_changes, methods=["GET"]),
    Route("/changes/actions/{id}", details, methods=["GET"]),
    Route("/changes/actions/{id}/plan", plan, methods=["GET"]),
    Route("/changes/actions/{id}/rollback", rollback, methods=["POST"]),
]
