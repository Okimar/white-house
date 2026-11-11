"""«Развитие персонажа» and «Сейчас:» lines for lorebook bubbles (ADR 0019 stage M4).

The card NEVER changes and the lorebook NEVER changes. What changes is kept here, per chat and per branch:

  * a development layer per character of the chat (and the user's persona): 300-600 tokens of «how this character is
    NOW» (relationships, what happened, new traits). It goes into the prompt RIGHT AFTER that character's card
    (`Extra(place="after_card")`, so it is cut together with that member when the context is short) and wins over the
    card as "now". A member with «Не наслаивать» has no layer and no request is made for them. The persona's layer goes
    after the persona's card; a chat without a «me» member keeps it under the target 'user' and sends it in the
    memory spot.
  * a «Сейчас: …» line per lorebook bubble: appended to the bubble's text ONLY when that bubble was chosen for this
    reply, through `prompt_input.lore_rewritten` (on top of what a regex rule wrote). «Записать в лорбук насовсем»
    writes it into the bubble for good (a copy of the data first, then a normal history step of the lorebook).

Both are versions in `p_memory_texts` anchored at a message (the last message of the chapter they were made after):
the value on a branch is the newest version anchored on it, so another branch keeps its own development and undo or
a branch switch costs nothing. A hand edit is a new LOCKED version: automatic work never replaces it.

The job step (`step`, called by work.py after the summaries and the plot): the next closed chapter of the branch, in
story order, that has no development run yet (with «Пересказы глав» on it waits for the chapter's summary) gets ONE
request through `job.step` (a user's request cancels it at once and it is redone): the chapter's summary (or text),
and for each participant without «Не наслаивать» and without a lock their card's short description and old layer;
and the lorebook bubbles of the chat that the chapter mentions by keywords, with their old lines. M6: the side
characters («Лица») the chapter names are people of the request too (target 'face:<id>'); their layer goes with their
mini-card (faces.contribute), not after a card. The answer is JSON
(`prompts.LAYER_SCHEMA`). A layer longer than asked is compressed by one more request inside the same step (the
self-compression), and cut at a sentence end if it is still too long. A failed request marks the chapter's run and
is retried after a backoff; after MAX_TRIES the chapter is skipped, so one bad chapter never stops the layers.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from tavern.core import retrieval
from tavern.core.assembly import Extra
from tavern.core.llm import StructuredOutputError
from tavern.core.prompt import estimate_tokens
from tavern.errors import AppError, NotFound
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ProviderError
from tavern.services import cast, character_tree, embeddings, lorebooks

from . import chapters, prompts, store, summaries

DEV, NOW = "development", "now_line"
DEV_DROP_RANK = 60  # unconditional extras are dropped by rank under a short context; the layer goes after the others
COMPRESS_OVER = 1.3  # a layer longer than this x the asked length is compressed once more
MAX_TRIES = 3  # failed development runs of one chapter before it is skipped
MAX_ENTRIES = 12  # lorebook bubbles offered for «Сейчас:» lines per chapter at most
SCENE_TOKENS = 4000  # a chapter without a summary is shortened to about this much
MAX_TEXT = 8000


class LayerError(AppError):
    code = "memory_layer"
    status = 409


def development_on(settings: dict[str, Any]) -> bool:
    return bool(settings.get("development"))


def lines_on(settings: dict[str, Any]) -> bool:
    return bool(settings.get("now_lines"))


def dev_tokens(settings: dict[str, Any]) -> int:
    return max(100, int(settings.get("dev_tokens") or 450))


# -- who has a layer -------------------------------------------------------------------------------


def persona_target(ctx: Any, chat_id: str) -> str:
    me = cast.me(ctx, chat_id)
    return me["character_id"] if me else "user"


def targets(ctx: Any, chat_id: str, *, faces: bool = False) -> list[dict[str, Any]]:
    """Who of the chat may have a layer: its characters, then the user's persona («Я»); with `faces`, then the side
    characters used in the chat (M6: target 'face:<id>')."""
    out = [{"target": m["character_id"], "name": m["name"], "is_me": False, "is_face": False}
           for m in ctx.members.list(chat_id) if not m["is_me"]]
    me = cast.me(ctx, chat_id)
    out.append({"target": me["character_id"] if me else "user", "name": cast.user_name(ctx, chat_id), "is_me": True,
                "is_face": False})
    if faces:
        from . import faces as side  # faces.py imports this module

        out.extend(side.people(ctx, chat_id))
    return out


def _card(ctx: Any, target: str) -> tuple[str, str]:
    """(name, the card's short description) of a character; ('', '') when there is no such card."""
    try:
        character = ctx.characters.get(target)
    except NotFound:
        return "", ""
    data = character.get("data") or {}
    return character["name"], " ".join(str(data.get("description") or "").split())


# -- the prompt (inside the reply, no request) -----------------------------------------------------


def contribute(api: Any, job: Any, history: list[dict[str, Any]]) -> None:
    """Development layers after the cards and «Сейчас:» lines on the chosen lorebook bubbles. Unconditional: they go
    whatever the window (the history-first rule is for what replaces left-out messages)."""
    settings = api.settings
    prompt = job.prompt_input
    db = api.ctx.db
    chat_id = job.chat["id"]
    path_ids = {m["id"] for m in history}
    if development_on(settings):
        layers = store.anchored_for_path(db, chat_id, DEV, path_ids)
        flags = store.member_flags(db, chat_id)
        if layers:
            sent_persona = False
            for member in prompt.members:
                row = layers.get(member.character_id)
                if row is None or not row["text"].strip() or flags.get(member.character_id, {}).get("no_layer"):
                    continue
                sent_persona = sent_persona or member.is_me
                prompt.extras.append(layer_extra(member.name, member.character_id, row["text"], "after_card"))
            row = layers.get("user")
            if not sent_persona and row is not None and row["text"].strip() and \
                    not any(m.is_me for m in prompt.members) and not flags.get("user", {}).get("no_layer"):
                prompt.extras.append(layer_extra(prompt.user_name, "", row["text"], "memory", source="user"))
    if lines_on(settings) and prompt.lore:
        lines = store.anchored_for_path(db, chat_id, NOW, path_ids)
        add_now_lines(prompt, lines)


def layer_extra(name: str, owner: str, text: str, place: str, *, source: str = "") -> Extra:
    return Extra(text=f"[{name} сейчас — развитие по ходу истории; где расходится с карточкой, верно это]\n{text.strip()}",
                 source=f"plugin:memory:dev:{source or owner}", label=f"Развитие: {name}", place=place, owner=owner,
                 drop_rank=DEV_DROP_RANK)


def add_now_lines(prompt: Any, lines: dict[str, dict[str, Any]]) -> int:
    """Append each chosen lorebook bubble's «Сейчас:» line to its text for this reply only (after a regex rewrite, if
    any). The lorebook row is never touched. Returns how many lines went."""
    count = 0
    for found in prompt.lore:
        block_id = found.block["id"]
        row = lines.get(block_id)
        if row is None or not row["text"].strip():
            continue
        base = prompt.lore_rewritten.get(block_id, found.block["content"])
        prompt.lore_rewritten[block_id] = f"{base.rstrip()}\nСейчас: {row['text'].strip()}"
        count += 1
    return count


# -- the job step ----------------------------------------------------------------------------------


def next_chapter(rows: list[chapters.Placed], sums: dict[str, dict[str, Any]], runs: dict[str, dict[str, Any]], *,
                 summaries_on: bool, now: Any) -> tuple[chapters.Placed | None, float | None]:
    """(the next chapter for the development step, or None; seconds until a failed one may be retried). Strictly in
    story order: a layer is built on the one before it. With summaries on, a chapter waits for its summary (unless
    summarizing it failed: then its text is used)."""
    for p in rows:
        if p.row["state"] == "deleted":
            continue
        run = runs.get(p.row["id"])
        if run is not None and run["state"] == "done":
            continue
        if run is not None and run["state"] == "failed":
            if run["tries"] >= MAX_TRIES:
                continue
            left = summaries.retry_in(run, now)
            if left > 0:
                return None, left
        if summaries_on and p.row["id"] not in sums and p.row["state"] != "failed":
            return None, None  # its summary comes first
        return p, None
    return None, None


def scene_text(path: list[dict[str, Any]], p: chapters.Placed, sums: dict[str, dict[str, Any]]) -> str:
    summary = sums.get(p.row["id"])
    if summary is not None and summary["text"].strip():
        return summary["text"].strip()
    lines = prompts.chapter_lines(chapters.eligible_in(path, p.lo, p.hi))
    return prompts.shorten("\n".join(lines), SCENE_TOKENS)


def chapter_entries(ctx: Any, chat_id: str, text: str, old: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Lorebook bubbles of the chat's books that the chapter's text mentions by their keywords (most hits first);
    a bubble whose line is locked is left out."""
    members = [m["character_id"] for m in ctx.members.list(chat_id)]
    book_ids = lorebooks.active_books(ctx, chat_id, members)
    if not book_ids:
        return []
    found: list[tuple[int, dict[str, Any]]] = []
    for blocks in ctx.blocks.for_owners(book_ids).values():
        for block in blocks:
            if block.get("mode") == "off" or (old.get(block["id"]) or {}).get("locked"):
                continue
            hits = retrieval.keyword_hits(block, text)
            if hits:
                found.append((hits, block))
    found.sort(key=lambda item: -item[0])
    return [block for _, block in found[:MAX_ENTRIES]]


def plan(api: Any, chat_id: str, path: list[dict[str, Any]], p: chapters.Placed,
         sums: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """(scene, people, entries) of one chapter's request. Empty people and entries: nothing to ask."""
    ctx = api.ctx
    db = ctx.db
    settings = api.settings
    path_ids = {m["id"] for m in path}
    scene = scene_text(path, p, sums)
    people: list[dict[str, Any]] = []
    if development_on(settings):
        flags = store.member_flags(db, chat_id)
        layers = store.anchored_for_path(db, chat_id, DEV, path_ids)
        persona = persona_target(ctx, chat_id)
        for target in p.row["participants"]:
            if target == "user":
                target = persona
            if flags.get(target, {}).get("no_layer") or (layers.get(target) or {}).get("locked"):
                continue
            if target == "user":
                name, card = cast.user_name(ctx, chat_id), ""
            else:
                name, card = _card(ctx, target)
            if not name or any(x["target"] == target for x in people):
                continue
            people.append({"code": f"C{len(people) + 1}", "target": target, "name": name, "card": card,
                           "old": (layers.get(target) or {}).get("text", "")})
        from . import faces as side  # M6: the side characters the chapter names are people of the request too

        text = "\n".join(m.get("content") or "" for m in chapters.eligible_in(path, p.lo, p.hi))
        for face in side.named_in(ctx, chat_id, text):
            who = side.target(face["id"])
            if flags.get(who, {}).get("no_layer") or (layers.get(who) or {}).get("locked"):
                continue
            people.append({"code": f"C{len(people) + 1}", "target": who, "name": face["name"], "card": face["card"],
                           "old": (layers.get(who) or {}).get("text", "")})
    entries: list[dict[str, Any]] = []
    if lines_on(settings):
        old = store.anchored_for_path(db, chat_id, NOW, path_ids)
        source = scene + "\n" + "\n".join(m.get("content") or "" for m in chapters.eligible_in(path, p.lo, p.hi))
        for i, block in enumerate(chapter_entries(ctx, chat_id, source, old), 1):
            entries.append({"code": f"E{i}", "block_id": block["id"], "title": block.get("title") or "",
                            "text": block.get("content") or "", "old": (old.get(block["id"]) or {}).get("text", "")})
    return scene, people, entries


async def make(llm: Any, scene: str, people: list[dict[str, Any]], entries: list[dict[str, Any]],
               target: int) -> tuple[dict[str, str], dict[str, str]]:
    """The request (one more with a note when the language is wrong), the self-compression of a too long layer.
    Returns ({target: layer text}, {block id: line}). Raises summaries.Refused / ProviderError / ..."""
    note = ""
    for _ in range(2):
        messages, source = prompts.layer_messages(scene, people, entries, target, note)
        value = await llm.generate_json(messages, schema=prompts.LAYER_SCHEMA)
        layers = _pick(people, value.get("layers") or [], "who", "text")
        lines = _pick(entries, value.get("now_lines") or [], "entry", "line")
        note = next((prompts.wrong_language(t, source) for t in [*layers.values(), *lines.values()]
                     if prompts.wrong_language(t, source)), "")
        if not note:
            break
    else:
        raise summaries.Refused(f"Модель дважды ответила неподходяще: {note}")
    out_layers: dict[str, str] = {}
    for person in people:
        text = layers.get(person["code"], "").strip()
        if not text:
            continue
        if estimate_tokens(text) > target * COMPRESS_OVER:
            value = await llm.generate_json(prompts.compress_messages(person["name"], text, target),
                                            schema=prompts.COMPRESS_SCHEMA)
            shorter = str(value.get("text") or "").strip()
            if shorter and not prompts.wrong_language(shorter, source):
                text = shorter
            text = prompts.cut_to(text, int(target * COMPRESS_OVER))
        out_layers[person["target"]] = text[:MAX_TEXT]
    out_lines: dict[str, str] = {}
    for entry in entries:
        line = lines.get(entry["code"], "").strip()
        if line.casefold().startswith("сейчас:"):
            line = line[len("сейчас:"):].strip()
        if line:
            out_lines[entry["block_id"]] = line[:prompts.NOW_LINE_MAX_CHARS]
    return out_layers, out_lines


def _pick(items: list[dict[str, Any]], answer: list[Any], key: str, field: str) -> dict[str, str]:
    """The answer's texts by our codes; a model that wrote the name or title instead of the code is understood too."""
    codes = {i["code"].casefold(): i["code"] for i in items}
    for i in items:
        codes.setdefault((i.get("name") or i.get("title") or "").casefold(), i["code"])
    out: dict[str, str] = {}
    for item in answer:
        if not isinstance(item, dict):
            continue
        code = codes.get(str(item.get(key) or "").strip().casefold())
        if code and isinstance(item.get(field), str):
            out[code] = item[field]
    return out


async def step(api: Any, chat_id: str, path: list[dict[str, Any]], job: Any, clients: Any,
               ) -> tuple[str, float | None, str]:
    """Step 6 of the job. Returns (what happened, seconds until a retry, a note) with the words of
    `work.summary_step`: done / idle / blocked / rate."""
    settings = api.settings
    if not (development_on(settings) or lines_on(settings)):
        return "idle", None, ""
    ctx = api.ctx
    db = ctx.db
    rows = chapters.placed(store.chapter_rows(db, chat_id), path)
    sums = store.current_summaries(db, chat_id)
    runs = store.layer_runs(db, chat_id)
    chosen, wait = next_chapter(rows, sums, runs, summaries_on=bool(settings.get("summaries")), now=datetime.now(timezone.utc))
    if chosen is None:
        return "idle", wait, ""
    scene, people, entries = plan(api, chat_id, path, chosen, sums)
    if not people and not entries:
        store.put_layer_run(db, chat_id, chosen.row["id"], "done")  # nobody to layer, nothing mentioned: no request
        return "done", None, ""
    live = [p for p in rows if p.row["state"] != "deleted"]
    job.progress(sum(1 for p in live if (runs.get(p.row["id"]) or {}).get("state") == "done"), len(live), "развитие")
    target = dev_tokens(settings)
    try:
        client = clients.get("development")
        layers, lines = await job.step(lambda: make(client, scene, people, entries, target))
    except PluginRefused as exc:
        return "blocked", None, exc.message
    except ProviderError as exc:  # before AppError: it is one
        if exc.upstream_status == 429:
            await job.rate_wait(embeddings.RATE_WAIT)
            return "rate", None, ""
        return _failed(db, chat_id, chosen, exc, runs)
    except AppError as exc:
        if exc.code == "no_connection":
            return "blocked", None, "нет подключения к модели для развития"
        return _failed(db, chat_id, chosen, exc, runs)
    except StructuredOutputError as exc:
        return _failed(db, chat_id, chosen, exc, runs)
    anchor = chosen.row["last_id"]
    old = {p["target"]: p["old"] for p in people}
    for who, text in layers.items():
        if text != old.get(who):
            store.put_anchored(db, chat_id, DEV, who, text, anchor, by="auto", made_from=chosen.row["id"])
    old_lines = {e["block_id"]: e["old"] for e in entries}
    for block_id, line in lines.items():
        if line != old_lines.get(block_id):
            store.put_anchored(db, chat_id, NOW, block_id, line, anchor, by="auto", made_from=chosen.row["id"])
    store.put_layer_run(db, chat_id, chosen.row["id"], "done")
    api.notify("layers", chat_id=chat_id)
    return "done", None, ""


def _failed(db: Any, chat_id: str, chosen: chapters.Placed, exc: Exception,
            runs: dict[str, dict[str, Any]]) -> tuple[str, float | None, str]:
    store.put_layer_run(db, chat_id, chosen.row["id"], "failed", summaries.failure_text(exc))
    tries = int((runs.get(chosen.row["id"]) or {}).get("tries") or 0) + 1
    return "done", summaries.retry_delay(tries) if tries < MAX_TRIES else None, ""


# -- the «Развитие» tab ------------------------------------------------------------------------------


def view(api: Any, chat_id: str, storage: Any) -> dict[str, Any]:
    """The layers and the «Сейчас:» lines of the open branch."""
    ctx = api.ctx
    db = ctx.db
    path_ids = {m["id"] for m in ctx.chats.active_path(chat_id)}
    flags = store.member_flags(db, chat_id)
    layers = store.anchored_for_path(db, chat_id, DEV, path_ids)
    members = []
    for who in targets(ctx, chat_id, faces=True):
        row = layers.get(who["target"])
        members.append({**who, "no_layer": bool(flags.get(who["target"], {}).get("no_layer")),
                        "text": row["text"] if row else "", "by": row["by"] if row else "",
                        "locked": bool(row and row["locked"]), "id": row["id"] if row else ""})
    lines = []
    for block_id, row in store.anchored_for_path(db, chat_id, NOW, path_ids).items():
        if not row["text"].strip():
            continue
        try:
            block = ctx.blocks.get(block_id)
            book = ctx.lorebooks.get(block["owner_id"])["name"] if ctx.lorebooks.exists(block["owner_id"]) else ""
        except NotFound:
            continue  # the bubble was deleted from its lorebook
        lines.append({"block_id": block_id, "title": block.get("title") or "", "book": book, "line": row["text"],
                      "locked": row["locked"], "by": row["by"], "id": row["id"]})
    lines.sort(key=lambda x: (x["book"], x["title"]))
    return {"development_on": development_on(api.settings), "now_on": lines_on(api.settings), "members": members,
            "now_lines": lines, "undo": chapters.undo_label(storage), "dev_tokens": dev_tokens(api.settings)}


def _need(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AppError(f"Нужен {key}", code="bad_request")
    return value


def _body(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AppError("Пустой текст", code="bad_request")
    return text.strip()[:MAX_TEXT]


def _target(ctx: Any, chat_id: str, payload: dict[str, Any]) -> str:
    target = _need(payload, "target")
    if not any(t["target"] == target for t in targets(ctx, chat_id, faces=True)):
        raise AppError("Такого участника в чате нет", code="not_found", status=404)
    return target


def _anchor(current: dict[str, Any] | None, path: list[dict[str, Any]]) -> str:
    """Where a hand-made version is anchored: where the branch's version was, else `fresh_anchor`."""
    if current is not None and current["anchor_id"]:
        return current["anchor_id"]
    return fresh_anchor(path)


fresh_anchor = chapters.fresh_anchor  # kept here too: M4 code and tests call it by this name


def layer_edit(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """«Сохранить» a layer by hand: a new LOCKED version of this branch."""
    db = api.ctx.db
    target = _target(api.ctx, chat_id, payload)
    text = _body(payload)
    versions = store.anchored_versions(db, chat_id, DEV, target, {m["id"] for m in path})
    made = store.put_anchored(db, chat_id, DEV, target, text, _anchor(versions[0] if versions else None, path),
                              by="hand", locked=True)
    chapters.log_text(storage, "Изменить развитие", made)


def layer_lock(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    db = api.ctx.db
    target = _target(api.ctx, chat_id, payload)
    versions = store.anchored_versions(db, chat_id, DEV, target, {m["id"] for m in path})
    if not versions:
        raise LayerError("Развития ещё нет")
    store.set_text_lock(db, versions[0]["id"], bool(payload.get("locked")))


def layer_restore(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """«Вернуть эту версию»: a copy of it becomes the branch's newest version, locked."""
    db = api.ctx.db
    target = _target(api.ctx, chat_id, payload)
    row = next((r for r in store.anchored_versions(db, chat_id, DEV, target, {m["id"] for m in path})
                if r["id"] == payload.get("text_id")), None)
    if row is None:
        raise LayerError("Такой версии развития нет на этой ветке")
    made = store.put_anchored(db, chat_id, DEV, target, row["text"], row["anchor_id"], by="hand", locked=True,
                              made_from=row["made_from"])
    chapters.log_text(storage, "Вернуть версию развития", made)


def layer_versions(api: Any, chat_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    target = _target(api.ctx, chat_id, payload)
    path_ids = {m["id"] for m in api.ctx.chats.active_path(chat_id)}
    return [{k: v[k] for k in ("id", "text", "by", "locked", "created_at")}
            for v in store.anchored_versions(api.ctx.db, chat_id, DEV, target, path_ids)]


def transfer_chats(api: Any, chat_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Chats the layer can go to: those where this character takes part (any chat for the persona)."""
    ctx = api.ctx
    target = _target(ctx, chat_id, payload)
    persona = target == persona_target(ctx, chat_id)
    if persona:
        ids = [c["id"] for c in ctx.chats.list_chats(limit=500)]
    elif target.startswith(store.FACE_PREFIX):  # a side character: the chats it is linked to
        ids = [link["chat_id"] for link in store.face_links(ctx.db, face_id=target[len(store.FACE_PREFIX):])]
    else:
        ids = [r["chat_id"] for r in ctx.db.fetchall(
            "SELECT DISTINCT cm.chat_id FROM chat_members cm JOIN chats c ON c.id = cm.chat_id "
            "WHERE cm.character_id = ? ORDER BY c.updated_at DESC", (target,))]
    out = []
    for other in ids:
        if other == chat_id:
            continue
        chat = ctx.chats.get_chat(other)
        out.append({"chat_id": other, "title": chat["title"] or "Без названия", "has_messages": bool(chat["leaf_id"]),
                    "memory": store.is_enabled(ctx.db, other)})
    return out


def transfer(api: Any, chat_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """«Перенести развитие в другой чат»: the branch's layer becomes a version in the other chat, anchored near its
    newest message (`fresh_anchor`: it is that chat's current layer on its open branch). The persona's layer goes to
    the other chat's persona."""
    ctx = api.ctx
    db = ctx.db
    target = _target(ctx, chat_id, payload)
    to_chat = _need(payload, "to_chat_id")
    if to_chat == chat_id:
        raise LayerError("Это тот же чат")
    other = ctx.chats.get_chat(to_chat)
    path_ids = {m["id"] for m in ctx.chats.active_path(chat_id)}
    versions = store.anchored_versions(db, chat_id, DEV, target, path_ids)
    if not versions or not versions[0]["text"].strip():
        raise LayerError("Переносить нечего: развития ещё нет")
    other_path = ctx.chats.active_path(to_chat) if other["leaf_id"] else []
    if not other_path:
        raise LayerError("В том чате ещё нет сообщений")
    if target == persona_target(ctx, chat_id):
        to_target = persona_target(ctx, to_chat)
    elif any(m["character_id"] == target for m in ctx.members.list(to_chat)):
        to_target = target
    elif target.startswith(store.FACE_PREFIX) and any(
            link["chat_id"] == to_chat for link in store.face_links(db, face_id=target[len(store.FACE_PREFIX):])):
        to_target = target
    else:
        raise LayerError("Этого персонажа нет в том чате")
    store.put_anchored(db, to_chat, DEV, to_target, versions[0]["text"], fresh_anchor(other_path), by="transfer",
                       made_from=f"chat:{chat_id}")
    api.notify("layers", chat_id=to_chat)
    return {"to_chat_id": to_chat, "title": other["title"], "memory": store.is_enabled(db, to_chat)}


def _line(db: Any, chat_id: str, path: list[dict[str, Any]], block_id: str) -> dict[str, Any] | None:
    versions = store.anchored_versions(db, chat_id, NOW, block_id, {m["id"] for m in path})
    return versions[0] if versions else None


def now_edit(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """A «Сейчас:» line by hand (new or changed): locked."""
    db = api.ctx.db
    block_id = _need(payload, "block_id")
    try:
        api.ctx.blocks.get(block_id)
    except NotFound:
        raise LayerError("Такого пузыря в лорбуке нет") from None
    text = _body(payload)[:prompts.NOW_LINE_MAX_CHARS]
    if text.casefold().startswith("сейчас:"):
        text = text[len("сейчас:"):].strip()
    made = store.put_anchored(db, chat_id, NOW, block_id, text, _anchor(_line(db, chat_id, path, block_id), path),
                              by="hand", locked=True)
    chapters.log_text(storage, "Изменить строку «Сейчас:»", made)


def now_delete(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """«Удалить» a line on this branch (an empty version: «↶» brings it back)."""
    db = api.ctx.db
    block_id = _need(payload, "block_id")
    current = _line(db, chat_id, path, block_id)
    if current is None or not current["text"].strip():
        raise LayerError("Такой строки нет")
    made = store.put_anchored(db, chat_id, NOW, block_id, "", _anchor(current, path), by="hand")
    chapters.log_text(storage, "Удалить строку «Сейчас:»", made)


async def now_write(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    """«Записать в лорбук насовсем»: a copy of the data first (a risky action), then the line is appended to the
    bubble's text as one normal history step of the lorebook (undone there), and it leaves the chat's lines."""
    ctx = api.ctx
    block_id = _need(payload, "block_id")
    current = _line(ctx.db, chat_id, path, block_id)
    if current is None or not current["text"].strip():
        raise LayerError("Такой строки нет")
    try:
        block = ctx.blocks.get(block_id)
    except NotFound:
        raise LayerError("Такого пузыря в лорбуке нет") from None
    if not ctx.lorebooks.exists(block["owner_id"]):
        raise LayerError("Это не пузырь лорбука")
    await asyncio.to_thread(ctx.backups.before, "memory-now-line")
    content = f"{(block.get('content') or '').rstrip()}\nСейчас: {current['text'].strip()}".lstrip("\n")
    updated = await character_tree.update_block(ctx, block_id, {"content": content})
    store.put_anchored(ctx.db, chat_id, NOW, block_id, "", _anchor(current, path), by="lorebook")
    return {"block_id": block_id, "content": updated["content"]}
