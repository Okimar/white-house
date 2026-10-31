"""Connection profiles: list, save what is active as a profile, change, delete, apply."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, field, json_body
from tavern.services import profiles


async def list_profiles(request: Request) -> JSONResponse:
    return JSONResponse(profiles.list_profiles(ctx_of(request)))


async def create_profile(request: Request) -> JSONResponse:
    """Body: {name, parts?: {connection, model, params, preset}} — a snapshot of what is active now."""
    body = await json_body(request)
    profile = profiles.create(ctx_of(request), field(body, "name", str, allow_empty=False),
                              field(body, "parts", dict, default=None))
    return JSONResponse(profile, status_code=201)


async def update_profile(request: Request) -> JSONResponse:
    """Body: {name?, parts?, recapture?: true = take what is active now}."""
    body = await json_body(request)
    profile = profiles.update(
        ctx_of(request), request.path_params["id"],
        name=field(body, "name", str, default=None),
        parts=field(body, "parts", dict, default=None),
        recapture=field(body, "recapture", bool, default=False),
    )
    return JSONResponse(profile)


async def delete_profile(request: Request) -> JSONResponse:
    profiles.delete(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


async def apply_profile(request: Request) -> JSONResponse:
    connection = profiles.apply(ctx_of(request), request.path_params["id"])
    return JSONResponse({"connection": connection, **profiles.list_profiles(ctx_of(request))})


routes = [
    Route("/profiles", list_profiles, methods=["GET"]),
    Route("/profiles", create_profile, methods=["POST"]),
    Route("/profiles/{id}", update_profile, methods=["PUT"]),
    Route("/profiles/{id}", delete_profile, methods=["DELETE"]),
    Route("/profiles/{id}/apply", apply_profile, methods=["POST"]),
]
