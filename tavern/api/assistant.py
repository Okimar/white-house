"""The assistant over the chat: its settings, conversations, requests (followed through
/runs/{id}/events), answers to its questions, «Давай» for its plan, confirmations, stop, undo and
«Что я о вас знаю». Logic lives in services/assistant.py and services/assistant_engine.py."""

from __future__ import annotations

import json
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body, read_body
from tavern.services import assistant, assistant_files, assistant_screenshots, profiles, spending


async def overview(request: Request) -> JSONResponse:
    """Settings, what it spent, the conversations and the models it may use."""
    ctx = ctx_of(request)
    sources = [{"id": "", "label": "Как в чате"}]
    sources += [{"id": f"connection:{c['id']}", "label": f"{c['name']} · {c['model']}" if c["model"] else c["name"]}
                for c in ctx.connections.list()]
    sources += [{"id": f"profile:{p['id']}", "label": f"Профиль: {p['name']}"}
                for p in profiles.list_profiles(ctx)["profiles"]]
    return JSONResponse({"settings": assistant.get_settings(ctx), "spent": assistant.spent(ctx),
                         "chats": assistant.list_chats(ctx), "sources": sources,
                         "money": spending.get_settings(ctx), "memory": assistant.memory(ctx)})


async def state(request: Request) -> JSONResponse:
    """Conversations working now and those waiting for the user (the 🪄 mark at the top)."""
    return JSONResponse(assistant.state(ctx_of(request)))


async def put_settings(request: Request) -> JSONResponse:
    return JSONResponse(assistant.save_settings(ctx_of(request), await json_body(request)))


async def create_chat(request: Request) -> JSONResponse:
    return JSONResponse(assistant.create_chat(ctx_of(request)), status_code=201)


async def get_chat(request: Request) -> JSONResponse:
    return JSONResponse(assistant.get_chat(ctx_of(request), request.path_params["id"]))


async def rename_chat(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(assistant.rename_chat(ctx_of(request), request.path_params["id"], body.get("title")))


async def delete_chat(request: Request) -> JSONResponse:
    assistant.delete_chat(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


async def send(request: Request) -> JSONResponse:
    """Body: {text, chat_id?: the chat open on the page, attachments?: from POST /assistant/files or library pictures}."""
    body = await json_body(request)
    run = assistant.start(ctx_of(request), request.path_params["id"], body.get("text"), body.get("chat_id"),
                          attachments=body.get("attachments"))
    return JSONResponse({"run_id": run.id}, status_code=202)


async def upload_file(request: Request) -> JSONResponse:
    """Body: the file's bytes, `?name=` its name. Returns the attachment to send with the next message
    (a picture goes into the image library, a text file is read in, anything else is only described)."""
    blob = await read_body(request, assistant_files.MAX_UPLOAD_BYTES)
    return JSONResponse(assistant_files.ingest(ctx_of(request), request.query_params.get("name", ""), blob),
                        status_code=201)


async def upload_screenshot(request: Request) -> JSONResponse:
    """Body: the picture (PNG or JPEG) the page made for a capture request the assistant started. The id is the
    one from the `assistant_capture` notice: random, single use, short lived. `?failed=reason` instead of a body
    tells that the page could not draw itself."""
    ident = request.path_params["id"]
    ctx = ctx_of(request)
    if request.query_params.get("failed"):
        assistant_screenshots.refuse(ctx, ident, request.query_params["failed"])
        return JSONResponse({"ok": True})
    assistant_screenshots.accept(ctx, ident, await read_body(request, assistant_screenshots.MAX_BYTES))
    return JSONResponse({"ok": True}, status_code=201)


async def export_chat(request: Request) -> Response:
    document = assistant.export_chat(ctx_of(request), request.path_params["id"])
    name = quote((document["title"] or "beseda")[:60].replace("/", "-") + ".tavern-assistant.json")
    return Response(json.dumps(document, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})


async def import_chat(request: Request) -> JSONResponse:
    """Body: a file made by the export. Makes a new conversation; the old one is not touched."""
    blob = await read_body(request, assistant.MAX_IMPORT_BYTES)
    try:
        payload = json.loads(blob.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise BadRequest("Это не беседа с помощником: файл не читается как JSON", code="bad_import") from None
    return JSONResponse(assistant.import_chat(ctx_of(request), payload), status_code=201)


async def answers(request: Request) -> JSONResponse:
    """Body: {answers: {question id: text} (missing = its default; {} = «на твой вкус»), chat_id?}."""
    body = await json_body(request)
    run = assistant.start(ctx_of(request), request.path_params["id"], chat_id=body.get("chat_id"),
                          answers=body.get("answers") if body.get("answers") is not None else {})
    return JSONResponse({"run_id": run.id}, status_code=202)


async def go(request: Request) -> JSONResponse:
    """«Давай»: start the plan. Body: {chat_id?}."""
    body = await json_body(request)
    run = assistant.start(ctx_of(request), request.path_params["id"], chat_id=body.get("chat_id"), go=True)
    return JSONResponse({"run_id": run.id}, status_code=202)


async def drop(request: Request) -> JSONResponse:
    assistant.drop_task(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


def _fact_id(request: Request) -> int:
    try:
        return int(request.path_params["id"])
    except ValueError:
        raise BadRequest("Memory line id must be a number") from None


async def list_memory(request: Request) -> JSONResponse:
    return JSONResponse({"memory": assistant.memory(ctx_of(request))})


async def add_memory(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(assistant.add_fact(ctx_of(request), body.get("text")), status_code=201)


async def edit_memory(request: Request) -> JSONResponse:
    body = await json_body(request)
    return JSONResponse(assistant.edit_fact(ctx_of(request), _fact_id(request), body.get("text")))


async def delete_memory(request: Request) -> JSONResponse:
    assistant.forget(ctx_of(request), _fact_id(request))
    return JSONResponse({"ok": True})


async def stop(request: Request) -> JSONResponse:
    return JSONResponse({"stopped": assistant.stop(ctx_of(request), request.path_params["id"])})


async def confirm(request: Request) -> JSONResponse:
    body = await json_body(request)
    allow = field(body, "allow", bool)
    ask_id = field(body, "ask_id", str, allow_empty=False)
    assistant.answer(request.path_params["id"], ask_id, allow)
    return JSONResponse({"ok": True})


async def undo(request: Request) -> JSONResponse:
    try:
        action_id = int(request.path_params["id"])
    except ValueError:
        raise BadRequest("Action id must be a number") from None
    return JSONResponse({"undone": await assistant.undo(ctx_of(request), action_id)})


routes = [
    Route("/assistant", overview, methods=["GET"]),
    Route("/assistant/state", state, methods=["GET"]),
    Route("/assistant/settings", put_settings, methods=["PUT"]),
    Route("/assistant/chats", create_chat, methods=["POST"]),
    Route("/assistant/chats/{id}", get_chat, methods=["GET"]),
    Route("/assistant/chats/{id}", rename_chat, methods=["PUT"]),
    Route("/assistant/chats/{id}", delete_chat, methods=["DELETE"]),
    Route("/assistant/files", upload_file, methods=["POST"]),
    Route("/assistant/screenshots/{id}", upload_screenshot, methods=["POST"]),
    Route("/assistant/import", import_chat, methods=["POST"]),
    Route("/assistant/chats/{id}/export", export_chat, methods=["GET"]),
    Route("/assistant/chats/{id}/send", send, methods=["POST"]),
    Route("/assistant/chats/{id}/answers", answers, methods=["POST"]),
    Route("/assistant/chats/{id}/go", go, methods=["POST"]),
    Route("/assistant/chats/{id}/drop", drop, methods=["POST"]),
    Route("/assistant/chats/{id}/stop", stop, methods=["POST"]),
    Route("/assistant/memory", list_memory, methods=["GET"]),
    Route("/assistant/memory", add_memory, methods=["POST"]),
    Route("/assistant/memory/{id}", edit_memory, methods=["PUT"]),
    Route("/assistant/memory/{id}", delete_memory, methods=["DELETE"]),
    Route("/assistant/chats/{id}/confirm", confirm, methods=["POST"]),
    Route("/assistant/actions/{id}/undo", undo, methods=["POST"]),
]
