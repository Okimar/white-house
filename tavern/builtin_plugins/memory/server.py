"""🧠 Память++ (stage M1: chat vectors). Everything is off until the plugin is switched on AND the chat's memory is.

Registers: chat events that wake the chat's background job (`work.py`), `chat.created` (the global «Включать
память в новых чатах»), the filter `meaning.wanted` and the `chat.before_generate` handler (`inject.py`), and the
actions the web part calls. Nothing happens in a chat whose switch is off: no tables written, no job, no request.
ADR 0019 holds the whole design; this file only wires it.
"""

from __future__ import annotations

from typing import Any

from tavern.errors import AppError

from . import inject, store, work

WAKE_DELAY = 2.0  # seconds a chat's job waits after an event, so edits that follow each other are worked together
WAKE_EVENTS = ("message.received", "message.added", "message.edited", "message.excluded", "message.deleted",
               "message.swipe_selected", "chat.branch_selected")


def setup(api: Any) -> None:
    db = api.ctx.db
    cache = store.VectorCache()
    api.on_unload(cache.clear)

    def wake(chat_id: str | None, delay: float | None = None) -> None:
        """Start or re-run the chat's job. Nothing at all while the chat's memory is off."""
        if not chat_id or not store.is_enabled(db, chat_id):
            return

        async def job(ctx: Any) -> None:
            await work.run(api, chat_id, ctx)

        api.job(f"chat:{chat_id}", job, delay=WAKE_DELAY if delay is None else delay)

    def on_event(**payload: Any) -> None:
        chat_id = payload.get("chat_id") or (payload.get("chat") or {}).get("id")
        wake(chat_id)

    for event in WAKE_EVENTS:
        api.on(event, on_event)

    def on_created(chat_id: str, **_: Any) -> None:
        if api.settings.get("new_chats"):
            store.set_enabled(db, chat_id, True)
            wake(chat_id)

    api.on("chat.created", on_created)
    api.on("meaning.wanted", lambda value=False, chat=None, **_: inject.wants_meaning(api, value, chat))

    async def before_generate(job: Any) -> None:
        await inject.contribute(api, cache, job)

    api.on("chat.before_generate", before_generate, priority=50)

    def state(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        model = api.vectors_model()
        rows = store.pieces(db, chat_id)
        have = store.current_vector_ids(db, chat_id, model) if model else {}
        return {"enabled": store.is_enabled(db, chat_id), "connected": bool(model), "pieces": len(rows),
                "vectors": sum(1 for p in rows if have.get(p["id"]) == p["hash"]),
                "job": api.job_status(f"chat:{chat_id}"), "new_chats": bool(api.settings.get("new_chats"))}

    def set_enabled(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        store.set_enabled(db, chat_id, bool(payload.get("enabled")))
        if payload.get("enabled"):
            wake(chat_id, delay=0.2)
        else:
            api.ctx.jobs.cancel(api.id, f"chat:{chat_id}")
        return state(payload)

    def vectorize(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        wake(chat_id, delay=0.2)
        return state(payload)

    def _chat_id(payload: dict[str, Any]) -> str:
        chat_id = payload.get("chat_id")
        if not isinstance(chat_id, str) or not chat_id:
            raise AppError("Нужен chat_id", code="bad_request")
        api.ctx.chats.get_chat(chat_id)  # NotFound for a wrong chat
        return chat_id

    api.action("state", state)
    api.action("set_enabled", set_enabled)
    api.action("vectorize", vectorize)
