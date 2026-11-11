"""🧠 Память++ (stages M1-M6). Everything is off until the plugin is switched on AND the chat's memory is.

Registers: chat events that wake the chat's background job (`work.py`), `chat.created` (the global «Включать
память в новых чатах»), the filter `meaning.wanted` and the `chat.before_generate` handler (`inject.py`), and the
actions the web part calls (chapters; M3: summaries, the plot, members' flags), and `prompt.built` (which chapters
really went: the sticky rule); M4: the «Развитие» tab's actions (layers, hand edit with a lock, versions, transfer to
another chat, «Сейчас:» lines with «Записать в лорбук насовсем»: layers.py); M5: the «Слово и нити» tab's actions (records with
their status per branch, hand edit with a lock, status, delete / restore, add by hand: threads.py); M6: the «Лица» tab's
actions (mini-cards of side characters: list and search, hand edit with a lock, versions, ★, use in this chat, delete /
restore, merge, «Сделать настоящим персонажем»: faces.py). A failed summary asks for a later pass after its backoff (a timer per chat, dropped
when the plugin is switched off). Nothing happens in a chat whose switch is off: no tables written, no job, no request.
ADR 0019 holds the whole design; this file only wires it.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any

from tavern.core.background import JobGaveUp
from tavern.errors import AppError
from tavern.services import cast

from . import actions, bulk, catalog, chapters, docs, faces, inject, layers, pieces, store, threads, transfer, work

WAKE_DELAY = 2.0  # seconds a chat's job waits after an event, so edits that follow each other are worked together
RATE_RETRY = 120.0  # seconds after which a bulk run or a document that gave up on rate limits is woken again
RESUME_DELAY = 5.0  # seconds after start when unfinished bulk runs and documents are woken (a restart loses nothing)
WAKE_EVENTS = ("message.received", "message.added", "message.edited", "message.excluded", "message.deleted",
               "message.swipe_selected", "chat.branch_selected")


def setup(api: Any) -> None:
    db = api.ctx.db
    cache = store.VectorCache()
    docs_cache = docs.DocVectorCache()
    pending: dict[str, Any] = {}  # reply id -> chapters offered to it (inject.record_hits)
    timers: dict[str, asyncio.TimerHandle] = {}  # chat id -> the next pass after a failed summary's backoff

    def reg(name: str, fn: Any) -> None:
        """api.action with what the assistant is told (catalog.py): describe, reads, confirm."""
        describe, reads, confirm = catalog.ACTIONS.get(name, ("", False, ""))
        api.action(name, fn, describe=describe, reads=reads, confirm=confirm)

    def stop_timers() -> None:
        for handle in timers.values():
            handle.cancel()
        timers.clear()

    api.on_unload(cache.clear)
    api.on_unload(docs_cache.clear)
    api.on_unload(stop_timers)

    def wake(chat_id: str | None, delay: float | None = None) -> None:
        """Start or re-run the chat's job. Nothing at all while the chat's memory is off."""
        if not chat_id or not store.is_enabled(db, chat_id):
            return

        async def job(ctx: Any) -> None:
            try:
                retry = await work.run(api, chat_id, ctx, cache)
            except JobGaveUp:
                if bulk.is_on(api, chat_id):  # a whole-chat run waits out the rate limit and goes on (progress is kept)
                    later(chat_id, RATE_RETRY)
                raise
            if retry is not None:
                later(chat_id, retry)

        api.job(f"chat:{chat_id}", job, delay=WAKE_DELAY if delay is None else delay)

    def later(chat_id: str, seconds: float) -> None:
        old = timers.pop(chat_id, None)
        if old is not None:
            old.cancel()

        def fire() -> None:
            timers.pop(chat_id, None)
            wake(chat_id, 0.1)

        timers[chat_id] = asyncio.get_running_loop().call_later(max(1.0, seconds), fire)

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
        await inject.contribute(api, cache, job, pending, docs_cache)

    api.on("chat.before_generate", before_generate, priority=50)

    def built(messages: Any, job: Any = None, **_: Any) -> Any:
        if job is not None and pending:
            inject.record_hits(api, job, pending)
        return messages

    api.on("prompt.built", built)

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
        sums = store.current_summaries(db, chat_id)
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
                        "preview": " ".join((first.get("content") or "").split())[:100],
                        "summary": (sums.get(p.row["id"]) or {}).get("text", ""),
                        "summary_by": (sums.get(p.row["id"]) or {}).get("by", ""),
                        "locked": bool((sums.get(p.row["id"]) or {}).get("locked")),
                        "error": p.row.get("error") or "", "tries": p.row.get("tries") or 0})
        open_part = [m for m in path[chapters.open_start(rows):] if pieces.eligible(m)]
        return {"chapters": out, "open": {"count": len(open_part), "first_id": open_part[0]["id"] if open_part else ""},
                "size": int(api.settings["chapter_size"]), "undo": chapters.undo_label(api.storage(chat_id)),
                "enabled": store.is_enabled(db, chat_id), "job": api.job_status(f"chat:{chat_id}"),
                "plot": plot_view(chat_id, path), "summaries_on": bool(api.settings.get("summaries")),
                "plot_on": bool(api.settings.get("plot")), "members": members_of(chat_id)}

    def plot_view(chat_id: str, path: list[dict[str, Any]]) -> dict[str, Any] | None:
        plot = store.plot_for_path(db, chat_id, {m["id"] for m in path})
        return None if plot is None else {"id": plot["id"], "text": plot["text"], "locked": plot["locked"],
                                          "by": plot["by"]}

    def members_of(chat_id: str) -> list[dict[str, Any]]:
        """The chat's characters with their memory flags (the user's persona is left out: it sees everything)."""
        flags = store.member_flags(db, chat_id)
        out = []
        for m in api.ctx.members.list(chat_id):
            if m.get("is_me"):
                continue
            mine = flags.get(m["character_id"], {})
            out.append({"character_id": m["character_id"], "name": m["name"],
                        "remembers_all": bool(mine.get("remembers_all")), "no_layer": bool(mine.get("no_layer"))})
        return out

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

    reg("chapters", view)
    def _message(payload: dict[str, Any], path: list[dict[str, Any]]) -> str:
        """A message of the open branch by `message_id`, or by its number in the chat (`number`, what the page shows)."""
        if isinstance(payload.get("message_id"), str) and payload["message_id"]:
            return payload["message_id"]
        number = payload.get("number")
        found = next((m["id"] for m in path if isinstance(number, int) and m["depth"] + 1 == number), None)
        if found is None:
            raise AppError("Нет сообщения с таким номером на этой ветке", code="memory_chapter", status=409)
        return found

    reg("split", hand(lambda c, path, p, st, me: chapters.split(db, st, c, path, _message(p, path), me)))
    reg("merge", hand(lambda c, path, p, st, me: chapters.merge(db, st, c, path, _text(p, "chapter_id"), me)))
    reg("move_border", hand(lambda c, path, p, st, me: chapters.move_border(
        db, st, c, path, _text(p, "chapter_id"), _message(p, path), me)))
    reg("remove", hand(lambda c, path, p, st, me: chapters.remove(db, st, c, path, _text(p, "chapter_id"))))
    reg("restore", hand(lambda c, path, p, st, me: chapters.restore(db, st, c, path, _text(p, "chapter_id"), me)))
    reg("accept", hand(lambda c, path, p, st, me: chapters.accept(db, st, c, path, _text(p, "chapter_id"), me)))
    reg("edit", hand(lambda c, path, p, st, me: chapters.edit(
        db, st, c, path, _text(p, "chapter_id"), title=p.get("title"),
        whole=p.get("whole") if "whole" in p else None)))
    reg("undo", hand(lambda c, path, p, st, me: chapters.undo(db, st)))

    # M3: summaries, the plot, members (actions.py)
    def text_hand(fn: Any) -> Any:
        return hand(lambda c, path, p, st, me: fn(db, c, path, p, st))

    for name in ("summary_edit", "summary_lock", "summary_restore", "plot_edit", "plot_lock", "plot_restore"):
        reg(name, text_hand(getattr(actions, name)))

    def on_chat(payload: dict[str, Any]) -> str:
        chat_id = _chat_id(payload)
        if not store.is_enabled(db, chat_id):
            raise AppError("Память в этом чате выключена", code="memory_off")
        return chat_id

    def resummarize(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = on_chat(payload)
        actions.ask_resummarize(db, chat_id, api.ctx.chats.active_path(chat_id), _text(payload, "chapter_id"),
                                api.storage(chat_id))
        wake(chat_id, delay=0.2)
        return view(payload)

    def plot_rebuild(payload: dict[str, Any]) -> dict[str, Any]:
        """«Пересобрать сюжет» from the summaries (with «Сюжет до сих пор» off too; never over a lock)."""
        chat_id = on_chat(payload)
        api.storage(chat_id).set("plot_force", True)
        wake(chat_id, delay=0.2)
        return view(payload)

    def member_set(payload: dict[str, Any]) -> dict[str, Any]:
        """«Помнит всё» / «Не наслаивать» of one member of the chat."""
        chat_id = on_chat(payload)
        character_id = _text(payload, "character_id")
        if not any(t["target"] == character_id for t in layers.targets(api.ctx, chat_id, faces=True)):  # persona, faces
            raise AppError("Такого участника в чате нет", code="not_found", status=404)
        values = {k: bool(payload[k]) for k in ("remembers_all", "no_layer") if k in payload}
        store.set_member(db, chat_id, character_id, **values)
        return {"members": members_of(chat_id)}

    reg("resummarize", resummarize)
    reg("plot_rebuild", plot_rebuild)
    reg("member_set", member_set)
    reg("summary_versions", lambda p: {"versions": actions.summary_versions(
        db, _chat_id(p), _text(p, "chapter_id"))})
    reg("plot_versions", lambda p: {"versions": actions.plot_versions(
        db, _chat_id(p), api.ctx.chats.active_path(_chat_id(p)))})
    reg("state", state)
    reg("set_enabled", set_enabled)
    reg("vectorize", vectorize)

    # M4: «Развитие» layers and «Сейчас:» lines (layers.py)
    def layer_hand(fn: Any) -> Any:
        """A hand edit of a layer or a line: memory must be on in the chat; tell the pages; give the new tab view."""
        def run(payload: dict[str, Any]) -> dict[str, Any]:
            chat_id = on_chat(payload)
            storage = api.storage(chat_id)
            fn(api, chat_id, api.ctx.chats.active_path(chat_id), payload, storage)
            api.notify("layers", chat_id=chat_id)
            return layers.view(api, chat_id, storage)
        return run

    for name in ("layer_edit", "layer_lock", "layer_restore", "now_edit", "now_delete"):
        reg(name, layer_hand(getattr(layers, name)))

    def layers_view(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        return layers.view(api, chat_id, api.storage(chat_id))

    def layer_transfer(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = on_chat(payload)
        done = layers.transfer(api, chat_id, payload)
        return {"done": done, **layers.view(api, chat_id, api.storage(chat_id))}

    async def now_write(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = on_chat(payload)
        written = await layers.now_write(api, chat_id, api.ctx.chats.active_path(chat_id), payload)
        api.notify("layers", chat_id=chat_id)
        return {"written": written, **layers.view(api, chat_id, api.storage(chat_id))}

    reg("layers", layers_view)
    reg("layer_versions", lambda p: {"versions": layers.layer_versions(api, _chat_id(p), p)})
    reg("layer_chats", lambda p: {"chats": layers.transfer_chats(api, _chat_id(p), p)})
    reg("layer_transfer", layer_transfer)
    reg("now_write", now_write)

    # M5: «Слово и нити» (threads.py)
    def thread_hand(fn: Any) -> Any:
        """A hand edit of a record: memory must be on in the chat; tell the pages; give the new tab view."""
        def run(payload: dict[str, Any]) -> dict[str, Any]:
            chat_id = on_chat(payload)
            storage = api.storage(chat_id)
            fn(api, chat_id, api.ctx.chats.active_path(chat_id), payload, storage)
            api.notify("threads", chat_id=chat_id)
            return threads.view(api, chat_id, storage)
        return run

    for name in ("thread_edit", "thread_status", "thread_lock", "thread_delete", "thread_restore", "thread_add"):
        reg(name, thread_hand(getattr(threads, name)))

    def threads_view(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        return threads.view(api, chat_id, api.storage(chat_id))

    reg("threads", threads_view)

    # M6: «Лица», mini-cards of side characters (faces.py). Faces are app-wide; a hand edit goes into the undo log of the
    # chat whose window made it.
    def face_hand(fn: Any) -> Any:
        """A hand edit of a face: memory must be on in the chat; tell the pages; give the new tab view."""
        def run(payload: dict[str, Any]) -> dict[str, Any]:
            chat_id = on_chat(payload)
            storage = api.storage(chat_id)
            fn(api, chat_id, payload, storage)
            api.notify("faces", chat_id=chat_id)
            return faces.view(api, chat_id, storage, payload)
        return run

    for name in ("face_edit", "face_lock", "face_favourite", "face_use", "face_restore", "face_delete",
                 "face_undelete", "face_merge"):
        reg(name, face_hand(getattr(faces, name)))

    def faces_view(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        return faces.view(api, chat_id, api.storage(chat_id), payload)

    def face_make_real(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = on_chat(payload)
        made = faces.face_make_real(api, chat_id, payload)
        api.notify("faces", chat_id=chat_id)
        return {"made": made, **faces.view(api, chat_id, api.storage(chat_id), payload)}

    reg("faces", faces_view)
    reg("face_versions", lambda p: {"versions": faces.face_versions(api, p)})
    reg("face_make_real", face_make_real)

    def face_add(payload: dict[str, Any]) -> dict[str, Any]:
        """A new mini-card (the assistant asks the user first: catalog.py `confirm`)."""
        chat_id = on_chat(payload)
        made = faces.face_add(api, chat_id, payload, api.storage(chat_id))
        api.notify("faces", chat_id=chat_id)
        return {"made": made, **faces.view(api, chat_id, api.storage(chat_id), payload)}

    reg("face_add", face_add)

    # M7: memory in the chat file (transfer.py)
    api.on_export(lambda chat_id: transfer.export(api, chat_id))

    def on_import(chat_id: str, data: dict[str, Any], id_map: dict[str, str]) -> None:
        if transfer.import_data(api, chat_id, data, id_map):
            api.notify("chapters", chat_id=chat_id)
            wake(chat_id, delay=1.0)  # the vectors are not in the file: the job makes them

    api.on_import(on_import)

    # M7: «Разобрать весь чат» (bulk.py)
    def confirmed(payload: dict[str, Any]) -> None:
        if payload.get("confirmed") is not True:
            raise AppError("Сначала покажите цену и спросите, продолжать ли", code="memory_confirm", status=409)

    def bulk_estimate(payload: dict[str, Any]) -> dict[str, Any]:
        return bulk.estimate(api, _chat_id(payload))

    def bulk_start(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        confirmed(payload)
        if not api.ctx.chats.active_path(chat_id):
            raise AppError("В чате ещё нет сообщений", code="memory_empty", status=409)
        found = bulk.start(api, chat_id)
        wake(chat_id, delay=0.2)
        return {"estimate": found, **bulk.state(api, chat_id)}

    def bulk_stop(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload)
        bulk.stop(api, chat_id)
        api.ctx.jobs.cancel(api.id, f"chat:{chat_id}")
        return bulk.state(api, chat_id)

    reg("bulk_estimate", bulk_estimate)
    reg("bulk_start", bulk_start)
    reg("bulk_stop", bulk_stop)
    reg("bulk_state", lambda p: bulk.state(api, _chat_id(p)))

    # M8: documents (docs.py)
    def wake_doc(doc_id: str, delay: float = 0.2) -> None:
        async def job(ctx: Any) -> None:
            try:
                await docs.run(api, doc_id, ctx, work.Clients(api, None, ctx))
            except JobGaveUp:
                later_doc(doc_id, RATE_RETRY)
                raise

        api.job(f"doc:{doc_id}", job, delay=delay)

    def later_doc(doc_id: str, seconds: float) -> None:
        old = timers.pop("doc:" + doc_id, None)
        if old is not None:
            old.cancel()

        def fire() -> None:
            timers.pop("doc:" + doc_id, None)
            wake_doc(doc_id, 0.1)

        timers["doc:" + doc_id] = asyncio.get_running_loop().call_later(max(1.0, seconds), fire)

    def doc_item(doc: dict[str, Any], chat_id: str = "") -> dict[str, Any]:
        rows = docs.chapter_rows(db, doc["id"])
        mine = {d["id"] for d in docs.attached(db, chat_id)} if chat_id else set()
        return {"id": doc["id"], "title": doc["title"], "source_name": doc["source_name"], "state": doc["state"],
                "started": doc["started"], "with_summaries": doc["with_summaries"], "error": doc["error"],
                "tokens": doc["tokens"], "chapters": len(rows),
                "summarized": len(docs.summaries_of(db, doc["id"])), "attached": doc["id"] in mine,
                "job": api.job_status(f"doc:{doc['id']}")}

    def docs_view(payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = _chat_id(payload) if payload.get("chat_id") else ""
        return {"docs": [doc_item(d, chat_id) for d in docs.all_docs(db)], "percent": api.settings.get("docs_percent")}

    def doc_view(payload: dict[str, Any]) -> dict[str, Any]:
        doc = docs.get(db, _text(payload, "doc_id"))
        sums = docs.summaries_of(db, doc["id"])
        return {"doc": doc_item(doc), "chapters": [
            {"id": c["id"], "number": c["number"], "title": c["title"], "tokens": c["tokens"], "whole": c["whole"],
             "cut_by": c["cut_by"], "error": c["error"], "summary": (sums.get(c["id"]) or {}).get("text", "")}
            for c in docs.chapter_rows(db, doc["id"])]}

    async def doc_add(payload: dict[str, Any]) -> dict[str, Any]:
        """The page sends the file as base64 (`data`), the assistant may send `text` (a .txt). Cut and stored, no money."""
        name = str(payload.get("name") or "document.txt")
        if isinstance(payload.get("data"), str):
            try:
                raw = base64.b64decode(payload["data"], validate=False)
            except (binascii.Error, ValueError):
                raise AppError("Файл не прочитался", code="bad_request") from None
        elif isinstance(payload.get("text"), str):
            raw = payload["text"].encode("utf-8")
        else:
            raise AppError("Нужен файл (data) или текст (text)", code="bad_request")
        doc = await docs.add_from_bytes(api, name, raw, str(payload.get("title") or ""))
        if payload.get("chat_id"):
            docs.attach(db, _chat_id(payload), doc["id"])
        api.notify("docs", doc_id=doc["id"])
        return {"doc": doc_item(doc, payload.get("chat_id") or ""), "estimate": docs.estimate(api, doc["id"])}

    def doc_estimate(payload: dict[str, Any]) -> dict[str, Any]:
        doc = docs.get(db, _text(payload, "doc_id"))
        return docs.estimate(api, doc["id"], payload.get("with_summaries") is not False)

    def doc_start(payload: dict[str, Any]) -> dict[str, Any]:
        doc = docs.get(db, _text(payload, "doc_id"))
        confirmed(payload)
        with_summaries = payload.get("with_summaries") is not False
        db.execute(f"UPDATE {docs.DOCS} SET started = 1, with_summaries = ? WHERE id = ?", (int(with_summaries), doc["id"]))
        for c in docs.chapter_rows(db, doc["id"]):
            db.execute(f"UPDATE {docs.DOC_CHAPTERS} SET tries = 0, error = '' WHERE id = ?", (c["id"],))
        wake_doc(doc["id"])
        return doc_item(docs.get(db, doc["id"]))

    def doc_stop(payload: dict[str, Any]) -> dict[str, Any]:
        doc = docs.get(db, _text(payload, "doc_id"))
        db.execute(f"UPDATE {docs.DOCS} SET started = 0 WHERE id = ?", (doc["id"],))
        api.ctx.jobs.cancel(api.id, f"doc:{doc['id']}")
        return doc_item(docs.get(db, doc["id"]))

    def doc_link(attach: bool) -> Any:
        def run(payload: dict[str, Any]) -> dict[str, Any]:
            chat_id = _chat_id(payload)
            (docs.attach if attach else docs.detach)(db, chat_id, _text(payload, "doc_id"))
            return docs_view({"chat_id": chat_id})
        return run

    def doc_delete(payload: dict[str, Any]) -> dict[str, Any]:
        doc_id = _text(payload, "doc_id")
        api.ctx.jobs.cancel(api.id, f"doc:{doc_id}")
        docs.delete(db, doc_id)
        docs_cache.clear()
        return {"deleted": doc_id}

    def doc_chapter(payload: dict[str, Any]) -> dict[str, Any]:
        """«Вся глава» on or off, or a new title, for one chapter of a document."""
        row = db.fetchone(f"SELECT id, doc_id FROM {docs.DOC_CHAPTERS} WHERE id = ?", (_text(payload, "chapter_id"),))
        if row is None:
            raise AppError("Такой главы нет", code="not_found", status=404)
        if "whole" in payload:
            db.execute(f"UPDATE {docs.DOC_CHAPTERS} SET whole = ? WHERE id = ?", (int(bool(payload["whole"])), row["id"]))
        if isinstance(payload.get("title"), str):
            db.execute(f"UPDATE {docs.DOC_CHAPTERS} SET title = ? WHERE id = ?", (payload["title"].strip()[:120], row["id"]))
        return doc_view({"doc_id": row["doc_id"]})

    reg("docs", docs_view)
    reg("doc_view", doc_view)
    reg("doc_add", doc_add)
    reg("doc_estimate", doc_estimate)
    reg("doc_start", doc_start)
    reg("doc_stop", doc_stop)
    reg("doc_attach", doc_link(True))
    reg("doc_detach", doc_link(False))
    reg("doc_delete", doc_delete)
    reg("doc_chapter", doc_chapter)

    def resume() -> None:
        """After a start: whole-chat runs and documents that were working are woken (a restart loses nothing)."""
        timers.pop("resume", None)
        for row in db.fetchall("SELECT chat_id FROM plugin_data WHERE plugin_id = ? AND key = ?", (api.id, bulk.KEY)):
            if bulk.is_on(api, row["chat_id"]):
                wake(row["chat_id"], delay=0.2)
        for doc in docs.all_docs(db):
            if doc["started"] and doc["state"] in ("working", "failed", "cut"):
                wake_doc(doc["id"])

    try:
        timers["resume"] = asyncio.get_running_loop().call_later(RESUME_DELAY, resume)
    except RuntimeError:
        pass  # no event loop yet (a sync start): nothing to resume
