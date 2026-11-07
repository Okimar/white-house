"""The «Инструкции» tab: skills the assistant reads (built-in, own, from plugins) and the programmer's
guides. Logic lives in services/skills.py."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import ctx_of, json_body
from tavern.errors import AppError
from tavern.services import skills


async def list_skills(request: Request) -> JSONResponse:
    return JSONResponse({"skills": skills.listing(ctx_of(request))})


async def get_skill(request: Request) -> JSONResponse:
    return JSONResponse(skills.get(ctx_of(request), request.path_params["id"]))


async def create_skill(request: Request) -> JSONResponse:
    """Body: a new skill {name, title?, description?, body?, audience?}, or {from: id} for a copy."""
    return JSONResponse(skills.create(ctx_of(request), await json_body(request)), status_code=201)


async def import_skill(request: Request) -> JSONResponse:
    """Body: {url} (a link to a SKILL.md, a GitHub file page works too) or {text, filename?} (a file the person
    picked). Gives {skill, notes}: the saved skill and what could not be carried over. Text only: nothing runs."""
    ctx = ctx_of(request)
    body = await json_body(request)
    if isinstance(body.get("url"), str) and body["url"].strip():
        return JSONResponse(await skills.import_url(ctx, body["url"]), status_code=201)
    if isinstance(body.get("text"), str):
        name = str(body.get("filename") or "")[:200]
        hint = name.rsplit("/", 1)[-1].removesuffix(".md").removesuffix(".MD")
        if hint.lower() == "skill":
            hint = ""
        return JSONResponse(skills.import_text(ctx, body["text"], origin=f"file:{name or 'SKILL.md'}", hint=hint),
                            status_code=201)
    raise AppError("Нужна ссылка (url) или текст файла (text)")


async def update_skill(request: Request) -> JSONResponse:
    """Own skills: any field. Built-in, plugin and programmer ones: only {enabled}."""
    return JSONResponse(skills.update(ctx_of(request), request.path_params["id"], await json_body(request)))


async def delete_skill(request: Request) -> JSONResponse:
    skills.delete(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


routes = [
    Route("/skills", list_skills, methods=["GET"]),
    Route("/skills", create_skill, methods=["POST"]),
    Route("/skills/import", import_skill, methods=["POST"]),
    Route("/skills/{id}", get_skill, methods=["GET"]),
    Route("/skills/{id}", update_skill, methods=["PUT"]),
    Route("/skills/{id}", delete_skill, methods=["DELETE"]),
]
