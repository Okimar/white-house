"""🧠 Память++ (stage M1: chat vectors). Everything is off until the plugin is switched on AND the chat's memory is.

Registers: chat events that wake the chat's background job (`work.py`), `chat.created` (the global «Включать
память в новых чатах»), the filter `meaning.wanted` and the `chat.before_generate` handler (`inject.py`), and the
actions the web part calls. Nothing happens in a chat whose switch is off: no tables written, no job, no request.
ADR 0019 holds the whole design; this file only wires it.
"""

from __future__ import annotations

from typing import Any

from tavern.errors import AppError
from tavern.services import cast

from . import chapters, inject, pieces, store, work

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
            await work.run(api, chat_id, ctx, cache)

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

    def on_deleted(chat_id: str = "", message: dict[str, Any] | None = None, children: list[str] | None = None,
                   **_: Any) -> None:
        """The deleted first or last message of a chapter: the chapter's end moves to the neighbour."""
        if chat_id and message and store.is_enabled(db, chat_id):
            chapters.on_message_deleted(db, chat_id, message, children or [],
                                        lambda mid: _parent(mid))
            api.notify("chapters", chat_id=chat_id)

    def _parent(message_id: str) -> str | None:
        try:
            return api.ctx.chats.get_message(message_id)["parent_id"]
        except Exception:  # noqa: BLE001 - a missing message ends the walk
            return None

    api.on("message.deleted", on_deleted, priority=10)
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
                "job": api.job_status(f"chat:{chat_id}"), "new_chats": bool(api.settings.get("new_chats")),
                "chapters": sum(1 for c in store.chapter_rows(db, chat_id) if c["state"] != "deleted")}

    def view(payload: dict[str, Any]) -> dict[str, Any]:
        """The chapters of the open branch for the window and the dividers, plus the part without chapters."""
        chat_id = _chat_id(payload)
        path = api.ctx.chats.active_path(chat_id)
        chapters.refresh(db, chat_id, path)
        rows = chapters.placed(store.chapter_rows(db, chat_id), path)
        out, number = [], 0
        for p in rows:
            span = chapters.eligible_in(path, p.lo, p.hi)
            if p.row["state"] != "deleted":
                number += 1
            first = span[0] if span else path[p.lo]
            out.append({"id": p.row["id"], "number": number if p.row["state"] != "deleted" else None,
                        "first_id": p.row["first_id"], "last_id": p.row["last_id"],
                        "first_no": chapters.number_of(path[p.lo]), "last_no": chapters.number_of(path[p.hi]),
                        "count": len(span), "state": p.row["state"], "title": p.row["title"],
                        "reason": p.row["reason"], "by": p.row["border_by"], "whole": p.row["whole"],
                        "preview": " ".join((first.get("content") or "").split())[:100]})
        open_part = [m for m in path[chapters.open_start(rows):] if pieces.eligible(m)]
        return {"chapters": out, "open": {"count": len(open_part), "first_id": open_part[0]["id"] if open_part else ""},
                "size": int(api.settings["chapter_size"]), "undo": chapters.undo_label(api.storage(chat_id)),
                "enabled": store.is_enabled(db, chat_id), "job": api.job_status(f"chat:{chat_id}")}

    def hand(op: Any) -> Any:
        """A hand edit of chapters: refuse in a chat with memory off, run it, tell the pages, give the new view."""
        def run(payload: dict[str, Any]) -> dict[str, Any]:
            chat_id = _chat_id(payload)
            if not store.is_enabled(db, chat_id):
                raise AppError("Память в этом чате выключена", code="memory_off")
            path = api.ctx.chats.active_path(chat_id)
            chapters.refresh(db, chat_id, path)
            me = cast.me(api.ctx, chat_id)
            op(chat_id, path, payload, api.storage(chat_id), me["character_id"] if me else "")
            api.notify("chapters", chat_id=chat_id)
            return view(payload)
        return run

    def _text(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise AppError(f"Нужен {key}", code="bad_request")
        return value

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

    api.action("chapters", view)
    def _message(payload: dict[str, Any], path: list[dict[str, Any]]) -> str:
        """A message of the open branch by `message_id`, or by its number in the chat (`number`, what the page shows)."""
        if isinstance(payload.get("message_id"), str) and payload["message_id"]:
            return payload["message_id"]
        number = payload.get("number")
        found = next((m["id"] for m in path if isinstance(number, int) and m["depth"] + 1 == number), None)
        if found is None:
            raise AppError("Нет сообщения с таким номером на этой ветке", code="memory_chapter", status=409)
        return found

    api.action("split", hand(lambda c, path, p, st, me: chapters.split(db, st, c, path, _message(p, path), me)))
    api.action("merge", hand(lambda c, path, p, st, me: chapters.merge(db, st, c, path, _text(p, "chapter_id"), me)))
    api.action("move_border", hand(lambda c, path, p, st, me: chapters.move_border(
        db, st, c, path, _text(p, "chapter_id"), _message(p, path), me)))
    api.action("remove", hand(lambda c, path, p, st, me: chapters.remove(db, st, c, path, _text(p, "chapter_id"))))
    api.action("restore", hand(lambda c, path, p, st, me: chapters.restore(db, st, c, path, _text(p, "chapter_id"), me)))
    api.action("accept", hand(lambda c, path, p, st, me: chapters.accept(db, st, c, path, _text(p, "chapter_id"), me)))
    api.action("edit", hand(lambda c, path, p, st, me: chapters.edit(
        db, st, c, path, _text(p, "chapter_id"), title=p.get("title"),
        whole=p.get("whole") if "whole" in p else None)))
    api.action("undo", hand(lambda c, path, p, st, me: chapters.undo(db, st)))
    api.action("state", state)
    api.action("set_enabled", set_enabled)
    api.action("vectorize", vectorize)
