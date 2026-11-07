"""Regex rules: thin routes over services/regex.py (stage 11). Everything that runs a pattern goes to a thread,
because the worker process is waited for."""

from __future__ import annotations

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, ctx_of, field, json_body
from tavern.services import regex


def _scope(request: Request) -> tuple[str, str]:
    return request.query_params.get("scope", "global"), request.query_params.get("owner", "")


def _changed(request: Request) -> None:
    ctx_of(request).notices.emit("regex")


async def overview(request: Request) -> JSONResponse:
    """Every rule: {global: [...], characters: [{id, name, rules, pending}], presets: [...]}."""
    return JSONResponse(regex.overview(ctx_of(request)))


async def list_rules(request: Request) -> JSONResponse:
    """?scope=global|character|preset&owner=<character or preset id>: the rules of one list, in order."""
    scope, owner = _scope(request)
    return JSONResponse({"rules": regex.list_rules(ctx_of(request), scope, owner)})


async def create_rule(request: Request) -> JSONResponse:
    """Body: {scope, owner?, name, find, replace, trim?, places?, when?, min_depth?, max_depth?, run_on_edit?,
    macros?, render_html?}. `find` is `/pattern/flags` (JavaScript syntax) or a plain pattern."""
    body = await json_body(request)
    rule = regex.create_rule(ctx_of(request), body.get("scope", "global"), body.get("owner", ""), body)
    _changed(request)
    return JSONResponse(rule, status_code=201)


async def get_rule(request: Request) -> JSONResponse:
    return JSONResponse(regex.get_rule(ctx_of(request), request.path_params["id"]))


async def update_rule(request: Request) -> JSONResponse:
    """Body: any of the rule's fields; the others stay."""
    rule = regex.update_rule(ctx_of(request), request.path_params["id"], await json_body(request))
    _changed(request)
    return JSONResponse(rule)


async def delete_rule(request: Request) -> JSONResponse:
    regex.delete_rule(ctx_of(request), request.path_params["id"])
    _changed(request)
    return JSONResponse({"ok": True})


async def copy_rule(request: Request) -> JSONResponse:
    """Body (optional): {scope, owner}: copy into another list; by default next to the original."""
    raw = await request.body()
    body = await json_body(request) if raw.strip() else {}
    rule = regex.copy_rule(ctx_of(request), request.path_params["id"], body.get("scope"), body.get("owner", ""))
    _changed(request)
    return JSONResponse(rule, status_code=201)


async def reorder(request: Request) -> JSONResponse:
    """Body: {scope, owner?, ids: [...]}: the new order of one list (every rule once)."""
    body = await json_body(request)
    rules = regex.reorder(ctx_of(request), body.get("scope", "global"), body.get("owner", ""), body.get("ids"))
    _changed(request)
    return JSONResponse({"rules": rules})


async def approve(request: Request) -> JSONResponse:
    """Body: {scope, owner}: allow the rules that came with this card or preset."""
    body = await json_body(request)
    count = regex.approve(ctx_of(request), body.get("scope", ""), body.get("owner", ""))
    _changed(request)
    return JSONResponse({"approved": count})


async def try_out(request: Request) -> JSONResponse:
    """Body: {rule: {find, replace, ...}, text, chat_id?}: what the rule finds in the text and what comes out,
    or the error of the pattern. Nothing is saved."""
    body = await json_body(request)
    ctx = ctx_of(request)
    result = await anyio.to_thread.run_sync(regex.try_out, ctx, body.get("rule"), body.get("text"), body.get("chat_id"))
    return JSONResponse(result)


async def summary(request: Request) -> JSONResponse:
    """?chat_id=: how many screen rules work in this chat and a stamp that changes when they do."""
    chat_id = request.query_params.get("chat_id", "")
    return JSONResponse(await anyio.to_thread.run_sync(regex.display_summary, ctx_of(request), chat_id))


async def display(request: Request) -> JSONResponse:
    """Body: {chat_id, items: [{id, text, role, depth, stream?}]}: how each text looks on the screen.
    depth = how many messages from the end (0 = the newest). stream = the reply is still being written."""
    body = await json_body(request)
    items = body.get("items")
    if not isinstance(items, list) or not all(
            isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("text"), str) for i in items):
        raise BadRequest("items: список объектов {id, text, role, depth}")
    ctx = ctx_of(request)
    result = await anyio.to_thread.run_sync(regex.display, ctx, field(body, "chat_id", str), items)
    regex.announce(ctx, result["skipped"])
    return JSONResponse(result)


async def import_rules(request: Request) -> JSONResponse:
    """Body: {scope, owner?, scripts: <a SillyTavern script, an array of them, or our export>}."""
    body = await json_body(request)
    result = regex.import_rules(ctx_of(request), body.get("scripts"), body.get("scope", "global"), body.get("owner", ""))
    _changed(request)
    return JSONResponse(result, status_code=201)


async def export_rules(request: Request) -> JSONResponse:
    """?scope=&owner=&ids=a,b: one script (an object) or several (an array), in the SillyTavern shape."""
    scope, owner = _scope(request)
    ids = [i for i in request.query_params.get("ids", "").split(",") if i]
    data = regex.export_rules(ctx_of(request), scope, owner, ids or None)
    return JSONResponse(data, headers={"Content-Disposition": attachment("regex-scripts.json")})


async def restore_original(request: Request) -> JSONResponse:
    """The text of a message before a saved-text rule changed it, put back."""
    ctx = ctx_of(request)
    message = regex.restore_original(ctx, request.path_params["id"])
    return JSONResponse(message)


routes = [
    Route("/regex", overview, methods=["GET"]),
    Route("/regex/rules", list_rules, methods=["GET"]),
    Route("/regex/rules", create_rule, methods=["POST"]),
    Route("/regex/rules/{id}", get_rule, methods=["GET"]),
    Route("/regex/rules/{id}", update_rule, methods=["PUT"]),
    Route("/regex/rules/{id}", delete_rule, methods=["DELETE"]),
    Route("/regex/rules/{id}/copy", copy_rule, methods=["POST"]),
    Route("/regex/order", reorder, methods=["PUT"]),
    Route("/regex/approve", approve, methods=["POST"]),
    Route("/regex/try", try_out, methods=["POST"]),
    Route("/regex/summary", summary, methods=["GET"]),
    Route("/regex/display", display, methods=["POST"]),
    Route("/regex/import", import_rules, methods=["POST"]),
    Route("/regex/export", export_rules, methods=["GET"]),
    Route("/regex/messages/{id}/original", restore_original, methods=["POST"]),
]
