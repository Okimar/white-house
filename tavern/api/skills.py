"""The «Инструкции» tab: skills the assistant reads (built-in, own, from plugins) and the programmer's
guides. Logic lives in services/skills.py."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, json_body
from tavern.services import skills


async def list_skills(request: Request) -> JSONResponse:
    return JSONResponse({"skills": skills.listing(ctx_of(request))})


async def get_skill(request: Request) -> JSONResponse:
    return JSONResponse(skills.get(ctx_of(request), request.path_params["id"]))


async def create_skill(request: Request) -> JSONResponse:
    """Body: a new skill {name, title?, description?, body?, audience?}, or {from: id} for a copy."""
    return JSONResponse(skills.create(ctx_of(request), await json_body(request)), status_code=201)


async def update_skill(request: Request) -> JSONResponse:
    """Own skills: any field. Built-in, plugin and programmer ones: only {enabled}."""
    return JSONResponse(skills.update(ctx_of(request), request.path_params["id"], await json_body(request)))


async def delete_skill(request: Request) -> JSONResponse:
    skills.delete(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


routes = [
    Route("/skills", list_skills, methods=["GET"]),
    Route("/skills", create_skill, methods=["POST"]),
    Route("/skills/{id}", get_skill, methods=["GET"]),
    Route("/skills/{id}", update_skill, methods=["PUT"]),
    Route("/skills/{id}", delete_skill, methods=["DELETE"]),
]
