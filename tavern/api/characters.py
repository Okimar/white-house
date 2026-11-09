"""Characters and settings."""

from __future__ import annotations

import base64
import binascii
import re

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tavern.api.util import BadRequest, attachment, copy_before, ctx_of, field, json_body
from tavern.errors import NotFound
from tavern.formats.cards import card_to_png, decode_png_card, encode_card, parse_card
from tavern.formats.media import media_type, sniff_image
from tavern.services import character_tree, history, lorebooks
from tavern.services import regex as regex_service


async def list_characters(request: Request) -> JSONResponse:
    return JSONResponse({"characters": ctx_of(request).characters.list()})


async def get_character(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).characters.get(request.path_params["id"]))


async def create_character(request: Request) -> JSONResponse:
    """Body: a character card (V1 flat, V2 or V3 JSON) or {"name": ...}."""
    ctx = ctx_of(request)
    spec, data = parse_card(await json_body(request))
    character = ctx.characters.create(data, spec=spec)
    character_tree.take_book(ctx, character["id"])  # embedded lorebook entries become blocks
    regex_service.take_card_rules(ctx, character)  # scripts the card carries wait for «Разрешить»
    lorebooks.attach_card_world(ctx, character["id"])  # its named ST lorebook, if we have it
    return JSONResponse(ctx.characters.get(character["id"]), status_code=201)


async def import_character_png(request: Request) -> JSONResponse:
    blob = await request.body()
    await copy_before(request, "import-character")
    if len(blob) > 10 * 1024 * 1024:
        raise BadRequest("PNG card is too large", code="card_too_large")
    spec, data = decode_png_card(blob)
    avatar = "data:image/png;base64," + base64.b64encode(blob).decode("ascii")
    ctx = ctx_of(request)
    character = ctx.characters.create(data, spec=spec, avatar=avatar)
    character_tree.take_book(ctx, character["id"])
    regex_service.take_card_rules(ctx, character)
    lorebooks.attach_card_world(ctx, character["id"])
    return JSONResponse(ctx.characters.get(character["id"]), status_code=201)


async def export_character_json(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    character = ctx.characters.get(request.path_params["id"])
    data = regex_service.card_with_rules(ctx, character["id"], character_tree.card_with_book(ctx, character))
    return JSONResponse(encode_card(data, spec=character["spec"]))


async def export_character_png(request: Request) -> Response:
    ctx = ctx_of(request)
    character = ctx.characters.get(request.path_params["id"])
    safe_name = re.sub(r'[\\"\r\n\x00-\x1f\x7f]+', "_", character["name"]).strip(" .") or "character"
    data = regex_service.card_with_rules(ctx, character["id"], character_tree.card_with_book(ctx, character))
    blob = card_to_png(data, spec=character["spec"])
    avatar = character.get("avatar") or ""
    if avatar.startswith("data:image/png;base64,"):
        try:
            original = base64.b64decode(avatar.split(",", 1)[1], validate=True)
            if original.startswith(b"\x89PNG\r\n\x1a\n"):
                # keep the original picture but write the current card (with its blocks) into it
                blob = card_to_png(data, spec=character["spec"], image=original)
        except (ValueError, binascii.Error):
            pass
    return Response(blob, media_type="image/png", headers={"Content-Disposition": attachment(safe_name + ".png")})


async def character_avatar(request: Request) -> Response:
    """The avatar as an image file, so the chat can show it without loading the whole card."""
    avatar = ctx_of(request).characters.get(request.path_params["id"]).get("avatar") or ""
    blob = b""
    if avatar.startswith("data:") and ";base64," in avatar:
        try:
            blob = base64.b64decode(avatar.split(",", 1)[1], validate=True)
        except (ValueError, binascii.Error):
            blob = b""
    suffix = sniff_image(blob)
    if suffix is None or suffix == ".svg":  # an SVG avatar could carry script; never serve it inline
        raise NotFound("Character has no avatar")
    return Response(blob, media_type=media_type(suffix), headers={"Cache-Control": "no-cache"})


async def update_character(request: Request) -> JSONResponse:
    _, data = parse_card(await json_body(request))
    ctx = ctx_of(request)
    character_id = request.path_params["id"]
    old = ctx.characters.get(character_id)["data"]
    updated = ctx.characters.update(character_id, data)
    if updated["data"] != old:
        history.record(ctx, character_id, "Карточка изменена", [{"t": "card", "data": old}])
    return JSONResponse(updated)


async def character_history(request: Request) -> JSONResponse:
    return JSONResponse(history.versions(ctx_of(request), request.path_params["id"]))


async def undo_character(request: Request) -> JSONResponse:
    """Take back the newest change of the character ("Отменить")."""
    return JSONResponse({"undone": await history.undo(ctx_of(request), request.path_params["id"])})


async def close_history(request: Request) -> JSONResponse:
    """The editor was closed: the changes so far become one version."""
    history.close(ctx_of(request), request.path_params["id"])
    return JSONResponse({"ok": True})


async def restore_version(request: Request) -> JSONResponse:
    try:
        version_id = int(request.path_params["version"])
    except ValueError:
        raise BadRequest("Version must be a number") from None
    return JSONResponse({"undone": await history.restore(ctx_of(request), request.path_params["id"], version_id)})


async def delete_character(request: Request) -> JSONResponse:
    ctx_of(request).characters.get(request.path_params["id"])
    await copy_before(request, "delete-character")
    ctx_of(request).characters.delete(request.path_params["id"])
    return JSONResponse({"ok": True})


# Settings the UI may change. Anything else is rejected so typos do not silently persist.
SETTING_TYPES = {
    "user_name": str,
    "active_connection": str,
    "generator": str,
    "run_budget": dict,
    "theme": str,
    "character_themes": dict,  # character id -> theme id
    "trusted_theme_scripts": dict,  # theme id -> sha256 of the effects script the user allowed
    "image_connection": str,  # connection used to generate pictures
    "active_preset": str,  # prompt preset for chats without their own
    "default_me": str,  # character id that joins every new chat as the user's own character
    "retrieval": dict,  # finding character blocks: scan_messages, budget_tokens, threshold
    "embedding_connection": str,  # connection used for meaning vectors
    "auto_vectors": bool,  # vectorize changed green bubbles by itself (needs embedding_connection); default off
    "background_yield": bool,  # background work (vectorization) stops while a reply runs, then resumes; default on
    "notes_display": str,  # notes beside a message: "few" (2 + "ещё N", default) or "all"
    "thinking_display": str,  # a reply's «Размышление»: "folded" (default), "open" or "hidden"
    "message_actions": str,  # a message's buttons: "menu" (a "…" list, default) or "always" (all on its panel)
    "message_pinned": list,  # ids of the buttons kept on the panel in any mode (web/core/messageActions.js)
    "notes_shown": int,  # how many of a message's notes the column shows before «Все заметки» (1..20, default 2)
    "skip_confirms": dict,  # delete questions turned off: key -> true (web/components/Dialogs.js SKIP_ASKS)
    "service_texts_moved": list,  # presets whose own texts became blocks; the page shows it once, then clears it
    "skip_delete_confirm": bool,  # delete bubbles without asking "are you sure?"
    "open_new_bubble": bool,  # "+ Пузырь" / "+ Подпузырь" opens the new bubble in the panel; default off
    "character_history": bool,  # keep the history of character changes ("Отменить", versions); default on
    "lorebooks": dict,  # lorebook budget_tokens / budget_percent / recursion (services/lorebooks.py)
}
STRING_MAPS = frozenset({"character_themes", "trusted_theme_scripts"})
SETTING_CHOICES = {
    "notes_display": ("few", "all"),
    "thinking_display": ("folded", "open", "hidden"),
    "message_actions": ("menu", "always"),
}
MESSAGE_BUTTONS = frozenset({"edit", "regenerate", "branches", "prompt", "exclude", "notes", "delete"})


async def get_settings(request: Request) -> JSONResponse:
    return JSONResponse(ctx_of(request).settings.all())


async def put_settings(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    body = await json_body(request)
    for key, value in body.items():
        if key not in SETTING_TYPES:
            raise BadRequest(f"Unknown setting {key!r}")
        field(body, key, SETTING_TYPES[key])
        if key in STRING_MAPS and not all(
            isinstance(k, str) and isinstance(v, str) and len(k) <= 100 and len(v) <= 100
            for k, v in value.items()
        ):
            raise BadRequest(f"Setting {key!r} must map short strings to short strings")
        if key in SETTING_CHOICES and value not in ("", None, *SETTING_CHOICES[key]):
            raise BadRequest(f"Setting {key!r} must be one of: {', '.join(SETTING_CHOICES[key])}")
        if key == "notes_shown" and not 1 <= value <= 20:
            raise BadRequest(f"Setting {key!r} must be from 1 to 20")
        if key == "skip_confirms" and not all(
            isinstance(k, str) and len(k) <= 40 and v is True for k, v in value.items()
        ):
            raise BadRequest(f"Setting {key!r} maps names of questions to true")
        if key == "service_texts_moved" and not all(isinstance(v, str) for v in value):
            raise BadRequest(f"Setting {key!r} is a list of names")
        if key == "message_pinned" and not all(isinstance(v, str) and v in MESSAGE_BUTTONS for v in value):
            raise BadRequest(f"Setting {key!r} takes these buttons: {', '.join(sorted(MESSAGE_BUTTONS))}")
        if key == "retrieval" and value:
            value = character_tree.clean_retrieval(value)
        if key == "lorebooks" and value:
            value = lorebooks.clean_settings(value)
        if value in ("", None):
            ctx.settings.delete(key)
        else:
            ctx.settings.set(key, value)
    return JSONResponse(ctx.settings.all())


routes = [
    Route("/characters", list_characters, methods=["GET"]),
    Route("/characters", create_character, methods=["POST"]),
    Route("/characters/import/png", import_character_png, methods=["POST"]),
    Route("/characters/{id}", get_character, methods=["GET"]),
    Route("/characters/{id}/export.json", export_character_json, methods=["GET"]),
    Route("/characters/{id}/export.png", export_character_png, methods=["GET"]),
    Route("/characters/{id}/avatar", character_avatar, methods=["GET"]),
    Route("/characters/{id}", update_character, methods=["PUT"]),
    Route("/characters/{id}", delete_character, methods=["DELETE"]),
    Route("/characters/{id}/history", character_history, methods=["GET"]),
    Route("/characters/{id}/history/close", close_history, methods=["POST"]),
    Route("/characters/{id}/history/{version}/restore", restore_version, methods=["POST"]),
    Route("/characters/{id}/undo", undo_character, methods=["POST"]),
    Route("/settings", get_settings, methods=["GET"]),
    Route("/settings", put_settings, methods=["PUT"]),
]
