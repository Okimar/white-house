"""Instruct templates: list (built-in and own), edit own, import/export (ours or SillyTavern), preview."""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, ctx_of, field, json_body
from tavern.core import templates as T
from tavern.providers.base import ChatMessage
from tavern.services import templates as template_service

SAMPLE = [
    ChatMessage("system", "Ты — Аня, добрая хозяйка таверны."),
    ChatMessage("user", "Привет! Что сегодня на ужин?"),
    ChatMessage("assistant", "Тушёное мясо и свежий хлеб."),
    ChatMessage("user", "Звучит отлично."),
]


def _checked(raw: Any) -> dict[str, Any]:
    try:
        return T.validate(raw)
    except ValueError as exc:
        raise BadRequest(str(exc)) from None


def _own(ctx: Any, template_id: str) -> dict[str, Any]:
    if template_id in T.BUILTIN:
        raise BadRequest("Встроенный шаблон не меняется: сделайте копию")
    return ctx.templates.get(template_id)


async def list_templates(request: Request) -> JSONResponse:
    return JSONResponse({"templates": list(template_service.all_templates(ctx_of(request)).values())})


async def create_template(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).templates.create(_checked(await json_body(request))), status_code=201)


async def update_template(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    template_id = request.path_params["id"]
    _own(ctx, template_id)
    return JSONResponse(ctx.templates.update(template_id, _checked(await json_body(request))))


async def delete_template(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    template_id = request.path_params["id"]
    _own(ctx, template_id)
    ctx.templates.delete(template_id)
    return JSONResponse({"ok": True})


async def import_template(request: Request) -> JSONResponse:
    """Body: our template, an ST instruct file, an ST context file, or {"instruct", "context"}.
    `?into=<id>` merges an ST file into one of your templates instead of making a new one."""
    ctx = ctx_of(request)
    raw = await json_body(request)
    into = request.query_params.get("into")
    try:
        if isinstance(raw.get("instruct"), dict) or isinstance(raw.get("context"), dict):
            data = T.from_st(raw["instruct"]) if isinstance(raw.get("instruct"), dict) else None
            if isinstance(raw.get("context"), dict):
                data = T.from_st(raw["context"], data)
        elif T.is_st(raw):
            data = T.from_st(raw, _own(ctx, into) if into else None)
        else:
            data = T.validate(raw)
    except ValueError as exc:
        raise BadRequest(str(exc)) from None
    if into:
        return JSONResponse(ctx.templates.update(into, data))
    return JSONResponse(ctx.templates.create(data), status_code=201)


async def export_template(request: Request) -> Response:
    """?format=tavern (default) | st-instruct | st-context."""
    ctx = ctx_of(request)
    template = template_service.all_templates(ctx).get(request.path_params["id"])
    if template is None:
        template = ctx.templates.get(request.path_params["id"])  # raises NotFound
    data = {k: template[k] for k in T.blank()}
    fmt = request.query_params.get("format", "tavern")
    if fmt == "st-instruct":
        data, suffix = T.to_st(data)["instruct"], " (instruct)"
    elif fmt == "st-context":
        data, suffix = T.to_st(data)["context"], " (context)"
    elif fmt == "tavern":
        suffix = ".tavern-template"
    else:
        raise BadRequest("format: tavern, st-instruct or st-context")
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": attachment(f"{template['name']}{suffix}.json")})


async def preview(request: Request) -> JSONResponse:
    """What a short sample conversation looks like through a template (saved or being edited)."""
    ctx = ctx_of(request)
    body = await json_body(request)
    if "template" in body:
        template = _checked(field(body, "template", dict))
    else:
        template = template_service.resolve(ctx, field(body, "id", str, default="auto"),
                                            field(body, "model", str, default=""))
    text, stops = T.render(template, SAMPLE)
    return JSONResponse({"text": text, "stop": stops, "name": template["name"]})


routes = [
    Route("/templates", list_templates, methods=["GET"]),
    Route("/templates", create_template, methods=["POST"]),
    Route("/templates/import", import_template, methods=["POST"]),
    Route("/templates/preview", preview, methods=["POST"]),
    Route("/templates/{id}", update_template, methods=["PUT"]),
    Route("/templates/{id}", delete_template, methods=["DELETE"]),
    Route("/templates/{id}/export", export_template, methods=["GET"]),
]
