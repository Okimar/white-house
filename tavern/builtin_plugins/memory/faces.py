"""«Лица»: mini-cards of side characters (ADR 0019 stage M6).

A side character is someone without a card who keeps coming back: an innkeeper, a guard, a merchant. Their mini-card
(`p_memory_faces`) is FLAT text of about FACE_TOKENS tokens (never a bubble tree), APP-WIDE: it is linked to every chat
it appeared in (`p_memory_face_chats`), and a favourite one (★) is used in every chat where it is named.

Off by default (setting «Второстепенные лица»); needs a small model. The job step (`step`, called by work.py after the
plot and BEFORE the development and threads steps, so a side character found in a chapter is already a known person to
those requests): the next closed chapter of the branch in story order gets ONE request through `job.step` (a user's
request cancels it at once and it is redone): the scene, the main characters (members, the persona, lorebook entries:
never a mini-card), and the known side characters the scene names (linked to this chat, or favourites), whose cards
are updated. The detection rule: the model proposes, the script confirms. A new face is kept only when its name is not
a main character's and it RECURS: its name (or an alias) is in at least two messages of the chapter, or in one and in
an earlier message of the branch (a one-message cameo is not a face). A locked or deleted face is never changed by the
job (a deleted one is still known, so it is not made again). A failure is retried after a backoff, at most MAX_TRIES.

In the prompt (`contribute`, whatever the window; nothing with the setting off): the faces of the chat that are
relevant to this reply: named (by name or alias) in the last `meaning_messages` texts, or found by the reply's ONE
meaning request (a card has a vector per chat it is used in: `vector_items`). Which faces may go: linked to the chat
and «Использовать в этом чате» on, or a favourite not switched off here (a favourite goes to another chat ONLY when it
is named). A face made real is not sent where its character is a member. At most MAX_PROMPT_FACES within
PROMPT_TOKENS, as one memory-spot entry «Лица» (dropped first under a short context: FACES_DROP_RANK), each card with
its «Сейчас:» development line of this branch (M4 layers of target 'face:<id>', when «Развитие» is on).

Development and «Слово и нити» of a side character reuse M4 and M5 as they are: the face is a person with target
'face:<id>' in those requests when the chapter names it, and in the windows' lists of people.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable

from tavern.core.assembly import Extra
from tavern.core.llm import StructuredOutputError
from tavern.core.prompt import estimate_tokens
from tavern.core.retrieval import stem
from tavern.errors import AppError, NotFound
from tavern.formats.cards import parse_card
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ProviderError
from tavern.services import cast, character_tree, embeddings
from tavern.util.ids import now_iso

from . import chapters, layers, pieces, prompts, search, store, summaries

FACE_TOKENS = 200  # a mini-card at most (ADR: flat, about 100-200 tokens)
MAX_TRIES = 3
MAX_KNOWN = 12  # known faces offered for an update per request at most
MAX_PROMPT_FACES = 4  # faces per reply at most
PROMPT_TOKENS = 600  # the «Лица» entry at most (at least one face always fits)
NOW_TOKENS = 120  # a face's «Сейчас:» line in the prompt, cut to this
FACES_DROP_RANK = 40  # under a short context the faces go first (ADR 5: faces, then threads)
MAX_NAME = 60
MAX_ALIASES = 8
MAX_CARD = 4000


class FaceError(AppError):
    code = "memory_face"
    status = 409


def faces_on(settings: dict[str, Any]) -> bool:
    return bool(settings.get("faces"))


def target(face_id: str) -> str:
    return store.FACE_PREFIX + face_id


def face_of(value: str) -> str:
    """The face id of a target 'face:<id>' ('' for any other target)."""
    return value[len(store.FACE_PREFIX):] if value.startswith(store.FACE_PREFIX) else ""


# -- names -----------------------------------------------------------------------------------------


@lru_cache(maxsize=8192)
def word_stems(text: str) -> frozenset[str]:
    """Stems of the words of a text (endings do not matter: «Борина» is «Борин»)."""
    return frozenset(stem(w.lower()) for w in pieces._WORD.findall(text) if len(w) >= 2)


@lru_cache(maxsize=4096)
def name_stems(name: str) -> tuple[str, ...]:
    return tuple(stem(w.lower()) for w in pieces._WORD.findall(name) if len(w) >= 2)


def names_of(face: dict[str, Any]) -> list[str]:
    return [face["name"], *face["aliases"]]


def mentioned(names: list[str], stems: frozenset[str]) -> bool:
    """Is one of the names in a text (all the words of that name, by stem)?"""
    for name in names:
        wanted = name_stems(name)
        if wanted and all(s in stems for s in wanted):
            return True
    return False


def same_person(face: dict[str, Any], name: str) -> bool:
    wanted = name_stems(name)
    return bool(wanted) and any(name_stems(n) == wanted for n in names_of(face))


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip(" .,;:«»\"'")


def _aliases(value: Any, name: str) -> list[str]:
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        alias = _clean(item, MAX_NAME)
        if alias and alias.casefold() != name.casefold() and all(alias.casefold() != a.casefold() for a in out):
            out.append(alias)
    return out[:MAX_ALIASES]


# -- which faces a chat has ------------------------------------------------------------------------


def chat_faces(ctx: Any, chat_id: str, *, in_use: bool = True) -> list[dict[str, Any]]:
    """The faces linked to the chat (only those used in it, unless `in_use` is False), not deleted, without those made
    real whose character is a member of the chat (the real card is there)."""
    links = {link["face_id"]: link for link in store.face_links(ctx.db, chat_id=chat_id)}
    members = {m["character_id"] for m in ctx.members.list(chat_id)}
    return [f for f in store.face_rows(ctx.db) if f["id"] in links and not f["deleted"]
            and (links[f["id"]]["in_use"] or not in_use) and not (f["character_id"] and f["character_id"] in members)]


def people(ctx: Any, chat_id: str) -> list[dict[str, Any]]:
    """The chat's faces as people of the windows' lists (development, «Слово и нити»)."""
    return [{"target": target(f["id"]), "name": f["name"], "is_me": False, "is_face": True}
            for f in chat_faces(ctx, chat_id)]


def named_in(ctx: Any, chat_id: str, text: str) -> list[dict[str, Any]]:
    """The chat's faces (used in it) that a text names: the side characters of a chapter for the development and
    threads requests."""
    stems = word_stems(text)
    return [f for f in chat_faces(ctx, chat_id) if mentioned(names_of(f), stems)]


def name_of(db: Any, value: str) -> str:
    face = store.get_face(db, face_of(value)) if face_of(value) else None
    return face["name"] if face else ""


# -- the prompt (inside the reply, no request) -----------------------------------------------------


def vector_id(face_id: str, chat_id: str) -> str:
    return f"f:{face_id}@{chat_id}"


def vector_items(api: Any, chat_id: str) -> list[tuple[str, str, str]]:
    """The cards of the chat's faces as (vector id «f:<face>@<chat>», text, hash): the job vectorizes them in step 2,
    so a reply finds a face by meaning with its one request."""
    if not faces_on(api.settings):
        return []
    return [(vector_id(f["id"], chat_id), f["card"], pieces.text_hash(f["card"]))
            for f in chat_faces(api.ctx, chat_id) if f["card"].strip()]


def eligible(ctx: Any, chat_id: str, members: set[str]) -> tuple[list[dict[str, Any]], set[str]]:
    """(faces that may go into this chat's replies, ids of those linked and used here). A favourite goes everywhere
    it is not switched off; one made real not where its character is a member."""
    links = {link["face_id"]: link for link in store.face_links(ctx.db, chat_id=chat_id)}
    used = {fid for fid, link in links.items() if link["in_use"]}
    out = [f for f in store.face_rows(ctx.db) if not f["deleted"] and f["card"].strip()
           and (f["id"] in used or (f["favourite"] and f["id"] not in links))
           and not (f["character_id"] and f["character_id"] in members)]
    return out, used


def contribute(api: Any, cache: store.VectorCache, job: Any, history: list[dict[str, Any]], scan: list[str]) -> None:
    """The relevant faces as one entry «Лица» in the memory spot (see the docstring)."""
    settings = api.settings
    if not faces_on(settings):
        return
    ctx = api.ctx
    db = ctx.db
    chat_id = job.chat["id"]
    prompt = job.prompt_input
    members = {m["character_id"] for m in ctx.members.list(chat_id)}
    faces, used = eligible(ctx, chat_id, members)
    if not faces:
        return
    stems = word_stems("\n".join(scan))
    named = [f for f in faces if mentioned(names_of(f), stems)]
    found: list[tuple[float, dict[str, Any]]] = []
    model = api.vectors_model()
    meaning = getattr(job, "meaning", None)
    if model and meaning is not None and meaning.queries and not meaning.error and meaning.model == model:
        queries = [search.unit(q) for q in meaning.queries]
        vectors = cache.get(db, chat_id, model)
        threshold = float(character_tree.retrieval_settings(ctx)["threshold"])
        for face in faces:
            stored = vectors.get(vector_id(face["id"], chat_id))
            if face in named or face["id"] not in used or stored is None:
                continue  # a favourite from elsewhere goes only when named (ADR 0019 M6)
            if stored[0] != pieces.text_hash(face["card"]) or len(stored[1]) != len(queries[0]):
                continue
            score = search.vector_part(max(search._dot(q, stored[1]) for q in queries), threshold)
            if score >= search.MIN_SCORE:
                found.append((score, face))
    found.sort(key=lambda x: -x[0])
    chosen = (named + [f for _, f in found])[:MAX_PROMPT_FACES]
    if not chosen:
        return
    now: dict[str, dict[str, Any]] = {}
    if layers.development_on(settings):
        now = store.anchored_for_path(db, chat_id, layers.DEV, {m["id"] for m in history})
    lines: list[str] = []
    used_tokens = 0
    for face in chosen:
        line = f"- {face['name']}" + (f" (также: {', '.join(face['aliases'])})" if face["aliases"] else "") + \
               f": {prompts.cut_to(face['card'].strip(), FACE_TOKENS)}"
        layer = (now.get(target(face["id"])) or {}).get("text", "").strip()
        if layer:
            line += f"\n  Сейчас: {prompts.cut_to(layer, NOW_TOKENS)}"
        cost = estimate_tokens(line)
        if lines and used_tokens + cost > PROMPT_TOKENS:
            break
        lines.append(line)
        used_tokens += cost
    shown = [f["name"] for f in chosen[:len(lines)]]
    prompt.extras.append(Extra(
        text="[Второстепенные лица — кто они такие; это не главные герои]\n" + "\n".join(lines),
        source="plugin:memory:faces", label="Лица: " + ", ".join(shown), drop_rank=FACES_DROP_RANK))


# -- the job step ----------------------------------------------------------------------------------


def known_here(db: Any, chat_id: str) -> list[dict[str, Any]]:
    """The faces this chat already knows: linked to it (in use or not, deleted ones too: they are not made again),
    and the favourites."""
    linked = {link["face_id"] for link in store.face_links(db, chat_id=chat_id)}
    return [f for f in store.face_rows(db) if f["id"] in linked or f["favourite"]]


def plan(api: Any, chat_id: str, path: list[dict[str, Any]], p: chapters.Placed,
         main_stems: frozenset[str]) -> dict[str, Any]:
    """What one chapter's request needs: the scene, the main characters, the known faces it names."""
    ctx = api.ctx
    messages = chapters.eligible_in(path, p.lo, p.hi)
    user = cast.user_name(ctx, chat_id)
    main = [m["name"] for m in ctx.members.list(chat_id) if not m["is_me"]] + [user]
    stems = word_stems("\n".join(m.get("content") or "" for m in messages))
    known = known_here(ctx.db, chat_id)
    named = [f for f in known if mentioned(names_of(f), stems)]
    offered = [{"code": f"F{i}", "id": f["id"], "name": f["name"], "aliases": f["aliases"], "card": f["card"],
                "locked": f["locked"]} for i, f in enumerate([f for f in named if not f["deleted"]][:MAX_KNOWN], 1)]
    return {"messages": messages, "lines": prompts.thread_lines(messages, user), "main": main, "known": known,
            "named": named, "offered": offered, "exclude": main_stems | frozenset(name_stems(user)), "lo": p.lo}


async def ask(llm: Any, request: dict[str, Any]) -> list[dict[str, Any]]:
    """The one request of a chapter (one more with a note when a card is in the wrong language). Raises
    summaries.Refused / ProviderError / StructuredOutputError / AppError."""
    note = ""
    for _ in range(2):
        messages, source = prompts.face_messages(request["lines"], request["main"], request["offered"], FACE_TOKENS,
                                                 note)
        value = await llm.generate_json(messages, schema=prompts.FACE_SCHEMA)
        items = [i for i in value.get("faces") or [] if isinstance(i, dict)]
        note = next((prompts.wrong_language(str(i.get("card") or ""), source) for i in items
                     if prompts.wrong_language(str(i.get("card") or ""), source)), "")
        if not note:
            return items
    raise summaries.Refused(f"Модель дважды ответила неподходяще: {note}")


def _first_mention(messages: list[dict[str, Any]], names: list[str]) -> dict[str, Any] | None:
    return next((m for m in messages if mentioned(names, word_stems(m.get("content") or ""))), None)


def recurs(names: list[str], messages: list[dict[str, Any]], earlier: Callable[[], frozenset[str]]) -> bool:
    """Does a new side character really come back: named in two messages of the chapter, or in one and before it?"""
    count = sum(1 for m in messages if mentioned(names, word_stems(m.get("content") or "")))
    return count >= 2 or (count == 1 and mentioned(names, earlier()))


def apply(db: Any, chat_id: str, path: list[dict[str, Any]], request: dict[str, Any],
          items: list[dict[str, Any]]) -> dict[str, int]:
    """Keep a chapter's answer: new faces (not a main character, recurring) and updated cards of the known ones
    (never a locked or deleted one); every face the chapter names is linked to the chat. Returns {new, updated}."""
    messages = request["messages"]
    codes = {f["code"].casefold(): f["id"] for f in request["offered"]}
    known = list(request["known"])
    memo: list[frozenset[str]] = []

    def earlier() -> frozenset[str]:
        if not memo:
            text = "\n".join(m.get("content") or "" for m in path[:request["lo"]] if pieces.eligible(m))
            memo.append(word_stems(text))
        return memo[0]

    made = updated = 0
    touched: set[str] = set()
    for item in items:
        name = _clean(item.get("name"), MAX_NAME)
        card = " ".join(str(item.get("card") or "").split())
        if not name or not card:
            continue
        card = prompts.cut_to(card, FACE_TOKENS)
        aliases = _aliases(item.get("aliases"), name)
        face_id = codes.get(str(item.get("face") or "").strip().casefold(), "")
        face = next((f for f in known if f["id"] == face_id), None) or \
            next((f for f in known if same_person(f, name) or any(same_person(f, a) for a in aliases)), None)
        if face is not None:
            if face["deleted"] or face["locked"] or face["id"] in touched:
                continue
            more = [a for a in [*aliases, name] if all(a.casefold() != x.casefold() for x in names_of(face))]
            new = {**face, "aliases": (face["aliases"] + more)[:MAX_ALIASES], "card": card, "updated_at": now_iso()}
            if new["card"] != face["card"] or new["aliases"] != face["aliases"]:
                store.put_face(db, new, version_by="auto")
                updated += 1
            touched.add(face["id"])
            continue
        names = [name, *aliases]
        stems = name_stems(name)
        if not stems or all(s in request["exclude"] for s in stems) or not recurs(names, messages, earlier):
            continue  # a main character, or a cameo in one message
        face = store.new_face(name, card, aliases=aliases, origin=chat_id)
        store.put_face(db, face, version_by="auto")
        first = _first_mention(messages, names)
        store.link_face(db, face["id"], chat_id, first["id"] if first else None)
        known.append(face)
        touched.add(face["id"])
        made += 1
    for face in request["named"]:
        if not face["deleted"]:
            first = _first_mention(messages, names_of(face))
            store.link_face(db, face["id"], chat_id, first["id"] if first else None)
    return {"new": made, "updated": updated}


async def step(api: Any, chat_id: str, path: list[dict[str, Any]], job: Any, clients: Any,
               main_stems: Callable[[], frozenset[str]]) -> tuple[str, float | None, str]:
    """Step 8 of the job (run before 6 and 7). Returns (what happened, seconds until a retry, a note) with the words
    of `work.summary_step`: done / idle / blocked / rate."""
    if not faces_on(api.settings):
        return "idle", None, ""
    db = api.ctx.db
    rows = chapters.placed(store.chapter_rows(db, chat_id), path)
    runs = store.face_runs(db, chat_id)
    chosen, wait = layers.next_chapter(rows, {}, runs, summaries_on=False, now=datetime.now(timezone.utc))
    if chosen is None:
        return "idle", wait, ""
    request = plan(api, chat_id, path, chosen, main_stems())
    if not request["messages"]:
        store.put_face_run(db, chat_id, chosen.row["id"], "done")
        return "done", None, ""
    live = [p for p in rows if p.row["state"] != "deleted"]
    job.progress(sum(1 for p in live if (runs.get(p.row["id"]) or {}).get("state") == "done"), len(live), "лица")
    try:
        client = clients.get("faces")
        items = await job.step(lambda: ask(client, request))
    except PluginRefused as exc:
        return "blocked", None, exc.message
    except ProviderError as exc:  # before AppError: it is one
        if exc.upstream_status == 429:
            await job.rate_wait(embeddings.RATE_WAIT)
            return "rate", None, ""
        return _failed(db, chat_id, chosen, exc, runs)
    except AppError as exc:
        if exc.code == "no_connection":
            return "blocked", None, "нет подключения к модели для лиц"
        return _failed(db, chat_id, chosen, exc, runs)
    except StructuredOutputError as exc:
        return _failed(db, chat_id, chosen, exc, runs)
    # read again after the request: a hand edit (a lock, a delete) may have come meanwhile
    request["known"] = known_here(db, chat_id)
    request["named"] = [f for f in request["known"] if f["id"] in {x["id"] for x in request["named"]}]
    apply(db, chat_id, path, request, items)
    store.put_face_run(db, chat_id, chosen.row["id"], "done")
    api.notify("faces", chat_id=chat_id)
    return "done", None, ""


def _failed(db: Any, chat_id: str, chosen: chapters.Placed, exc: Exception,
            runs: dict[str, dict[str, Any]]) -> tuple[str, float | None, str]:
    store.put_face_run(db, chat_id, chosen.row["id"], "failed", summaries.failure_text(exc))
    tries = int((runs.get(chosen.row["id"]) or {}).get("tries") or 0) + 1
    return "done", summaries.retry_delay(tries) if tries < MAX_TRIES else None, ""


# -- the «Лица» tab --------------------------------------------------------------------------------


def view(api: Any, chat_id: str, storage: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """The faces of this chat (linked, and the favourites), or with `all` every face of the app; `query` filters by
    name, alias and card text."""
    payload = payload or {}
    ctx = api.ctx
    db = ctx.db
    every = bool(payload.get("all"))
    query = str(payload.get("query") or "").strip().casefold()
    links = {link["face_id"]: link for link in store.face_links(db, chat_id=chat_id)}
    counts = {r["face_id"]: int(r["n"]) for r in db.fetchall(
        f"SELECT face_id, COUNT(*) AS n FROM {store.FACE_CHATS} GROUP BY face_id")}
    number = {m["id"]: int(m["depth"]) + 1 for m in ctx.chats.active_path(chat_id)}
    rows = store.face_rows(db)
    shown = [f for f in rows if every or f["id"] in links or f["favourite"]]
    if query:
        shown = [f for f in shown if query in " ".join([*names_of(f), f["card"]]).casefold()]
    out = []
    for f in shown:
        link = links.get(f["id"])
        character = ""
        if f["character_id"]:
            try:
                character = ctx.characters.get(f["character_id"])["name"]
            except NotFound:
                character = ""
        out.append({"id": f["id"], "name": f["name"], "aliases": f["aliases"], "card": f["card"],
                    "favourite": f["favourite"], "locked": f["locked"], "deleted": f["deleted"], "by": f["made_by"],
                    "character_id": f["character_id"] if character else "", "character_name": character,
                    "linked": link is not None,
                    "in_use": bool(link["in_use"]) if link is not None else f["favourite"],
                    "chats": counts.get(f["id"], 0),
                    "first_no": number.get((link or {}).get("first_message_id") or "")})
    out.sort(key=lambda x: (not x["linked"], not x["favourite"], x["deleted"], x["name"].casefold()))
    return {"on": faces_on(api.settings), "faces": out, "all": every, "total": len(rows),
            "undo": chapters.undo_label(storage), "tokens": FACE_TOKENS}


def _face(api: Any, payload: dict[str, Any], key: str = "face_id") -> dict[str, Any]:
    value = payload.get(key)
    face = store.get_face(api.ctx.db, value) if isinstance(value, str) and value else None
    if face is None or face["merged_into"]:
        raise FaceError("Такого лица нет")
    return face


def _fields(payload: dict[str, Any], face: dict[str, Any]) -> dict[str, Any]:
    """The edited name, aliases and card (those given), checked."""
    out = dict(face)
    if "name" in payload:
        name = _clean(payload["name"], MAX_NAME)
        if not name:
            raise AppError("Пустое имя", code="bad_request")
        out["name"] = name
    if "aliases" in payload:
        if not isinstance(payload["aliases"], list):
            raise AppError("aliases: список", code="bad_request")
        out["aliases"] = _aliases(payload["aliases"], out["name"])
    if "card" in payload:
        card = str(payload["card"] or "").strip()
        if not card:
            raise AppError("Пустая мини-карточка", code="bad_request")
        out["card"] = card[:MAX_CARD]
    return out


def face_edit(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    """Name, aliases or card changed by hand (rename too): a new version, locked."""
    face = _face(api, payload)
    new = {**_fields(payload, face), "locked": True, "updated_at": now_iso()}
    version = store.put_face(api.ctx.db, new, version_by="hand")
    chapters.log_face(storage, "Изменить лицо", before=face, version=version)


def face_lock(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    face = _face(api, payload)
    store.put_face(api.ctx.db, {**face, "locked": bool(payload.get("locked")), "updated_at": now_iso()})


def face_favourite(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    """«★ Любимое»: used in every chat where it is named."""
    face = _face(api, payload)
    store.put_face(api.ctx.db, {**face, "favourite": bool(payload.get("favourite")), "updated_at": now_iso()})


def face_use(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    """«Использовать в этом чате» on or off (a link with in_use = 0 keeps a favourite out of this chat too)."""
    face = _face(api, payload)
    store.link_face(api.ctx.db, face["id"], chat_id, None, in_use=bool(payload.get("in_use")))


def face_restore(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    """«Вернуть эту версию»: its name, aliases and card become the face's again, as a new locked version."""
    face = _face(api, payload)
    version = next((v for v in store.face_versions(api.ctx.db, face["id"]) if v["id"] == payload.get("version_id")),
                   None)
    if version is None:
        raise FaceError("Такой версии нет")
    new = {**face, "name": version["name"], "aliases": version["aliases"], "card": version["card"], "locked": True,
           "updated_at": now_iso()}
    made = store.put_face(api.ctx.db, new, version_by="hand")
    chapters.log_face(storage, "Вернуть версию лица", before=face, version=made)


def face_delete(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    """«Удалить»: out of every prompt and every chat's list (it stays under «Все лица» and can be brought back)."""
    face = _face(api, payload)
    if face["deleted"]:
        raise FaceError("Лицо уже удалено")
    store.put_face(api.ctx.db, {**face, "deleted": True, "updated_at": now_iso()})
    chapters.log_face(storage, "Удалить лицо", before=face)


def face_undelete(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    face = _face(api, payload)
    if not face["deleted"]:
        raise FaceError("Это лицо не удалено")
    store.put_face(api.ctx.db, {**face, "deleted": False, "updated_at": now_iso()})
    chapters.log_face(storage, "Вернуть лицо", before=face)


def face_merge(api: Any, chat_id: str, payload: dict[str, Any], storage: Any) -> None:
    """«Объединить»: two mini-cards of the same person. `face_id` goes into `into_id`: its name and aliases become
    aliases, its links, development and records move; the card of `into_id` stays (edit it after if needed)."""
    face = _face(api, payload)
    into = _face(api, payload, "into_id")
    if face["id"] == into["id"]:
        raise FaceError("Это одно и то же лицо")
    step = store.merge_faces(api.ctx.db, face["id"], into["id"])
    chapters.log_face(storage, f"Объединить «{face['name']}» с «{into['name']}»", merge=step)


def face_versions(api: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return store.face_versions(api.ctx.db, _face(api, payload)["id"])


def face_make_real(api: Any, chat_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """«Сделать настоящим персонажем»: a character card with the face's name and its text as the description, made by
    the normal character service (no bubbles: a mini-card is flat). The face stays, linked to that character."""
    ctx = api.ctx
    face = _face(api, payload)
    if face["character_id"]:
        try:
            ctx.characters.get(face["character_id"])
            raise FaceError("Из этого лица уже сделан персонаж")
        except NotFound:
            pass  # that character was deleted: a new one may be made
    description = face["card"]
    if face["aliases"]:
        description += f"\n\nТакже: {', '.join(face['aliases'])}."
    spec, data = parse_card({"name": face["name"], "description": description})
    character = ctx.characters.create(data, spec=spec)
    store.put_face(ctx.db, {**face, "character_id": character["id"], "updated_at": now_iso()})
    return {"character_id": character["id"], "name": character["name"]}
