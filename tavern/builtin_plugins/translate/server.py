"""🌐 Перевод: messages translated by Google Translate (no model, no key).

It uses the free endpoint translate.googleapis.com (client=gtx), the same one browser extensions
use. It is unofficial: Google may refuse for a while after many requests, which is reported to the
user as is. Long texts go in pieces of up to CHUNK characters, split between paragraphs.
Translations are kept in the plugin's storage of the chat, key "<message id>:<version>", so they
survive a reload and go when the chat is deleted.
"""

from __future__ import annotations

from typing import Any

import httpx

from tavern.errors import AppError, NotFound

URL = "https://translate.googleapis.com/translate_a/single"
CHUNK = 4000
TIMEOUT = 20.0
transport: httpx.AsyncBaseTransport | None = None  # tests swap the network out


class TranslateFailed(AppError):
    code = "translate_failed"
    status = 502


def pieces(text: str, size: int = CHUNK) -> list[str]:
    """Split between paragraphs, then lines, then anywhere, keeping every piece within `size`."""
    out: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                out.append(current)
                current = ""
            out.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            out.append(current)
            current = ""
        current += line
    if current:
        out.append(current)
    return out


async def translate(text: str, to: str, source: str = "auto") -> str:
    if not text.strip():
        return ""
    parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            for piece in pieces(text):
                response = await client.post(URL, params={"client": "gtx", "sl": source, "tl": to, "dt": "t"},
                                             data={"q": piece})
                if response.status_code == 429 or response.status_code == 403:
                    raise TranslateFailed("Google Переводчик временно отказал (много запросов). Попробуйте позже.")
                if response.status_code >= 400:
                    raise TranslateFailed(f"Google Переводчик ответил ошибкой {response.status_code}")
                data = response.json()
                parts.append("".join(seg[0] for seg in data[0] if seg and isinstance(seg[0], str)))
    except httpx.HTTPError as exc:
        raise TranslateFailed(f"Нет связи с Google Переводчиком: {exc}") from None
    except (ValueError, TypeError, IndexError):
        raise TranslateFailed("Google Переводчик прислал непонятный ответ") from None
    return "".join(parts)


def setup(api: Any) -> None:
    def key(message: dict[str, Any]) -> str:
        return f"{message['id']}:{message['active_swipe']}"

    async def translate_message(chat_id: str, message: dict[str, Any]) -> dict[str, Any]:
        to = api.settings["to"]
        entry = {"text": await translate(message["content"], to), "to": to}
        api.storage(chat_id).set(key(message), entry)
        return {"key": key(message), **entry}

    async def act_translate(payload: dict[str, Any]) -> dict[str, Any]:
        """{message_id} -> the translation of the version shown now."""
        message = api.ctx.chats.get_message(str(payload.get("message_id", "")))
        return await translate_message(message["chat_id"], message)

    def act_list(payload: dict[str, Any]) -> dict[str, Any]:
        """{chat_id} -> every kept translation of the chat."""
        chat_id = str(payload.get("chat_id", ""))
        if not chat_id:
            raise NotFound("Нет чата")
        return api.storage(chat_id).all()

    def act_forget(payload: dict[str, Any]) -> None:
        api.storage(str(payload.get("chat_id", ""))).delete(str(payload.get("key", "")))

    async def on_received(message: dict[str, Any], chat: dict[str, Any], **_: Any) -> None:
        if api.settings["auto"] and message.get("role") == "assistant" and message.get("content"):
            try:
                await translate_message(chat["id"], message)
            except AppError as exc:
                api.log.warning("Auto translation failed: %s", exc)
                return
            # The page saw the reply finish before this: tell it the translation is ready now.
            api.notify("translated", chat_id=chat["id"])

    api.action("translate", act_translate)
    api.action("list", act_list)
    api.action("forget", act_forget)
    api.on("message.received", on_received)
