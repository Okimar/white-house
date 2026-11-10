"""«Слово и нити» (ADR 0019 stage M5): what the characters gave their word about, per chat and per branch.

A record (`p_memory_threads`): its kind (promise, vow / principle, claim about oneself or the world, debt / agreement,
secret, quest), who gave the word (a character id; the user's persona: its character id, or 'user' in a chat without
a «me» member), to whom ('' = everyone / the world), the EXACT quote and the message it is in, an importance (1-3), and
for a secret who knows it. It belongs to a branch when its anchor message (the quoted one) is on it. Its status
(в силе / выполнено / нарушено / снято) and who knows a secret are rows anchored at a message
(`p_memory_thread_states`): the newest one on the branch wins, none = в силе, so another branch keeps its own.

The job step (`step`, called by work.py after the development step): the next closed chapter of the branch in story
order without a run gets ONE request through `job.step` (a user's request cancels it at once and it is redone): the
scene with message numbers, the people with codes, and the records still open on the branch. The answer
(`prompts.THREAD_SCHEMA`) gives new records (a quote that is not really in the scene's messages is dropped: the model
must not invent words) and status changes of the open ones (a locked record is never changed). Quests only with their
own toggle. A failure is retried after a backoff, at most MAX_TRIES times.

In the prompt (`contribute`, whatever the window; nothing at all with «Слово и нити» off): only for the one who speaks
now. Their own vows and principles still in force ALWAYS go, right after their card («Клятвы и принципы»). Other
records go when they are relevant to the reply: keywords of the last messages and the reply's ONE meaning request
(the quotes have vectors made by the job: `vector_items`), scored by `search.score` with the chat's pieces as the IDF
corpus. Which records a speaker may get: their own, those given to them, the persona's given to them or to everyone
(so a character can say «ты же обещал»); a secret only to those who know it. A member with «Помнит всё» gets every
record (by relevance). A record with status «снято» never goes.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from tavern.core.assembly import Extra
from tavern.core.llm import StructuredOutputError
from tavern.core.prompt import estimate_tokens
from tavern.errors import AppError, NotFound
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ProviderError
from tavern.services import cast, character_tree, embeddings
from tavern.util.ids import now_iso

from . import chapters, layers, pieces, prompts, search, store, summaries

KINDS = prompts.THREAD_KINDS
STATUSES = ("active", "kept", "broken", "dropped")
KIND_RU = {"promise": "обещание", "vow": "клятва / принцип", "claim": "сказано о себе / мире",
           "debt": "долг / уговор", "secret": "тайна", "quest": "задание"}
STATUS_RU = {"active": "в силе", "kept": "выполнено", "broken": "нарушено", "dropped": "снято"}
STATUS_WORDS = {"fulfilled": "kept", "done": "kept", "complete": "kept", "completed": "kept", "kept": "kept",
                "broken": "broken", "violated": "broken", "dropped": "dropped", "released": "dropped",
                "cancelled": "dropped", "canceled": "dropped", "moot": "dropped", "active": "active", "open": "active"}
MAX_TRIES = 3
MAX_OPEN = 40  # open records shown to the model per request at most
MAX_RELEVANT = 8  # other records per reply at most
MAX_VOWS = 10
MAX_QUOTE = 400
MIN_QUOTE = 3
THREADS_DROP_RANK = 45  # under a short context these go before the development layer (60) and before the vows
VOWS_DROP_RANK = 58
IMPORTANCE_WEIGHT = 0.05  # ties of relevance go to the more important record


class ThreadError(AppError):
    code = "memory_thread"
    status = 409


def threads_on(settings: dict[str, Any]) -> bool:
    return bool(settings.get("threads"))


def quests_on(settings: dict[str, Any]) -> bool:
    return bool(settings.get("quests"))


def persona_ids(ctx: Any, chat_id: str) -> set[str]:
    """The user's persona as records may name it: 'user', and its character id when the chat has a «me» member."""
    return {"user", layers.persona_target(ctx, chat_id)}


_QUOTES = re.compile(r"[«»\"“”„'‘’]")


def norm(text: str) -> str:
    """For comparing a quote with a message: case, ё, quote marks and spaces do not matter."""
    return " ".join(_QUOTES.sub("", text).casefold().replace("ё", "е").split())


def on_branch(db: Any, chat_id: str, path_ids: set[str], *, quests: bool = True,
              deleted: bool = False) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    """(record, its status row on the branch or None = в силе) for the records of the branch, oldest first."""
    states = store.states_for_path(db, chat_id, path_ids)
    out = []
    for row in store.thread_rows(db, chat_id):
        if row["anchor_id"] not in path_ids or (row["deleted"] and not deleted):
            continue
        if row["kind"] == "quest" and not quests:
            continue
        found = states.get(row["id"])
        out.append((row, found[0] if found else None))
    return out


def status_of(state: dict[str, Any] | None) -> str:
    return state["status"] if state else "active"


def knows_of(row: dict[str, Any], state: dict[str, Any] | None) -> list[str]:
    return list(state["knows"]) if state else list(row["knows"])


def may_know(row: dict[str, Any], knows: list[str], speakers: set[str], persona: set[str], *,
             remembers_all: bool = False) -> bool:
    """May the one speaking now (`speakers`: their ids) get this record? Their own; one given to them; the persona's
    given to them or to everyone; a secret only to those who know it. «Помнит всё»: every record."""
    if remembers_all or row["who"] in speakers:
        return True
    if row["kind"] == "secret":
        return bool(speakers & set(knows))
    return row["to_whom"] in speakers or (row["who"] in persona and row["to_whom"] == "")


def names(ctx: Any, chat_id: str) -> dict[str, str]:
    """target -> name for the people of the chat (the persona under 'user' and its character id)."""
    out = {m["character_id"]: m["name"] for m in ctx.members.list(chat_id)}
    user = cast.user_name(ctx, chat_id)
    for target in persona_ids(ctx, chat_id):
        out[target] = user
    return out


def name_of(ctx: Any, known: dict[str, str], target: str) -> str:
    if not target:
        return ""
    if target not in known:
        try:
            known[target] = ctx.characters.get(target)["name"]
        except NotFound:
            known[target] = "?"
    return known[target]


# -- the prompt (inside the reply, no request) -----------------------------------------------------


def line(row: dict[str, Any], status: str, knows: list[str], note: str, who: str, to: str, number: int | None) -> str:
    head = f"{KIND_RU[row['kind']]} · {who}" + (f" → {to}" if to else "")
    tail = STATUS_RU[status] + (f" ({note})" if note and status != "active" else "")
    where = f", №{number}" if number else ""
    known = f" Знают: {', '.join(knows)}." if row["kind"] == "secret" and knows else ""
    return f"- {head} ({tail}{where}): «{row['quote']}»{known}"


def contribute(api: Any, cache: store.VectorCache, job: Any, history: list[dict[str, Any]], scan: list[str]) -> None:
    """The speaker's vows after their card and their relevant records in the memory spot (see the docstring)."""
    settings = api.settings
    if not threads_on(settings):
        return
    ctx = api.ctx
    db = ctx.db
    chat_id = job.chat["id"]
    prompt = job.prompt_input
    index = {m["id"]: i for i, m in enumerate(history)}
    items = on_branch(db, chat_id, set(index), quests=quests_on(settings))
    if not items:
        return
    persona = persona_ids(ctx, chat_id)
    me = next((m for m in prompt.members if m.is_me), None)
    if prompt.impersonate:
        speakers, owner, name = persona, (me.character_id if me else ""), prompt.user_name
    else:
        others = [m for m in prompt.members if not m.is_me]
        primary = next((m for m in prompt.members if m.primary), others[0] if others else None)  # as assembly does
        if primary is None:
            return
        speakers = persona if primary.is_me else {primary.character_id}
        owner, name = primary.character_id, primary.name
    flags = store.member_flags(db, chat_id)
    remembers_all = any(flags.get(s, {}).get("remembers_all") for s in speakers)
    vows: list[tuple[dict[str, Any], str, list[str], str]] = []
    others: list[tuple[dict[str, Any], str, list[str], str]] = []
    for row, state in items:
        status, knows = status_of(state), knows_of(row, state)
        if status == "dropped" or not may_know(row, knows, speakers, persona, remembers_all=remembers_all):
            continue
        item = (row, status, knows, state["note"] if state else "")
        if row["kind"] == "vow" and row["who"] in speakers and status == "active":
            vows.append(item)
        else:
            others.append(item)
    known = names(ctx, chat_id)
    number = {m["id"]: int(m["depth"]) + 1 for m in history}
    if vows:
        vows.sort(key=lambda v: (-v[0]["importance"], index[v[0]["anchor_id"]]))
        body = "\n".join(f"- «{row['quote']}» (№{number[row['message_id']]})" if row["message_id"] in number
                         else f"- «{row['quote']}»" for row, *_ in vows[:MAX_VOWS])
        prompt.extras.append(Extra(
            text=f"[Клятвы и принципы {name} — в силе, пока не нарушены; {name} держится их]\n{body}",
            source=f"plugin:memory:vows:{owner or 'user'}", label=f"Клятвы и принципы: {name}",
            place="after_card" if owner else "memory", owner=owner, drop_rank=VOWS_DROP_RANK))
    chosen = relevant(api, cache, job, history, index, others, scan)
    if chosen:
        chosen.sort(key=lambda v: index[v[0]["anchor_id"]])  # story order
        body = "\n".join(line(row, status, [name_of(ctx, known, k) for k in knows], note,
                              name_of(ctx, known, row["who"]), name_of(ctx, known, row["to_whom"]),
                              number.get(row["message_id"] or ""))
                         for row, status, knows, note in chosen)
        prompt.extras.append(Extra(
            text=f"[Слово и нити — что было обещано, сказано и доверено; что из этого знает {name}, статус на сейчас]\n"
                 f"{body}",
            source=f"plugin:memory:threads:{owner or 'user'}", label=f"Слово и нити: {name}",
            drop_rank=THREADS_DROP_RANK))


def relevant(api: Any, cache: store.VectorCache, job: Any, history: list[dict[str, Any]], index: dict[str, int],
             items: list[tuple[dict[str, Any], str, list[str], str]], scan: list[str],
             ) -> list[tuple[dict[str, Any], str, list[str], str]]:
    """The records relevant to this reply: scored like pieces, by the stems of the last messages and the reply's ONE
    meaning request (no request of its own). The chat's pieces are the IDF corpus (a word every message has weighs
    nearly nothing)."""
    if not items:
        return []
    ctx = api.ctx
    db = ctx.db
    chat_id = job.chat["id"]
    candidates = [search.Candidate("t:" + row["id"], row["anchor_id"], index[row["anchor_id"]],
                                   pieces.text_hash(row["quote"]), estimate_tokens(row["quote"]), tuple(row["keywords"]))
                  for row, *_ in items]
    corpus = [search.Candidate(p["id"], p["message_id"], 0, p["hash"], 0, tuple(p["keywords"]))
              for p in store.pieces(db, chat_id)]
    model = api.vectors_model()
    vectors = cache.get(db, chat_id, model) if model else {}
    meaning = getattr(job, "meaning", None)
    queries = []
    if meaning is not None and meaning.queries and not meaning.error and meaning.model == model:
        queries = [search.unit(q) for q in meaning.queries]
    threshold = float(character_tree.retrieval_settings(ctx)["threshold"])
    scored = search.score(candidates, vectors, queries, search.query_stems(scan), threshold,
                          idf=search.idf_table(candidates + corpus))
    by_id = {"t:" + row["id"]: (row, status, knows, note) for row, status, knows, note in items}
    ranked = sorted(scored, key=lambda s: -(s.score + IMPORTANCE_WEIGHT * by_id[s.candidate.piece_id][0]["importance"]))
    return [by_id[s.candidate.piece_id] for s in ranked[:MAX_RELEVANT]]


def vector_items(api: Any, chat_id: str, path: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """The quotes of the branch's records as (vector id «t:<id>», text, hash): the job vectorizes them in step 2, so
    a reply finds them by meaning with its one request."""
    if not threads_on(api.settings):
        return []
    ids = {m["id"] for m in path}
    return [("t:" + r["id"], r["quote"], pieces.text_hash(r["quote"])) for r in store.thread_rows(api.ctx.db, chat_id)
            if not r["deleted"] and r["anchor_id"] in ids]


# -- the job step ----------------------------------------------------------------------------------


def people_of(ctx: Any, chat_id: str, p: chapters.Placed) -> list[dict[str, str]]:
    """The people of a chapter's request with codes: the chat's characters and the persona, plus anyone else who wrote
    in the chapter."""
    persona = layers.persona_target(ctx, chat_id)
    targets = [(t["target"], t["name"]) for t in layers.targets(ctx, chat_id)]
    known = names(ctx, chat_id)
    for target in p.row["participants"]:
        target = persona if target == "user" else target
        if all(target != t for t, _ in targets):
            targets.append((target, name_of(ctx, known, target)))
    return [{"code": f"C{i}", "target": t, "name": n} for i, (t, n) in enumerate(targets, 1)]


def plan(api: Any, chat_id: str, path: list[dict[str, Any]], p: chapters.Placed) -> dict[str, Any]:
    """What one chapter's request needs: the numbered scene, the people, the records still open before its end."""
    ctx = api.ctx
    index = chapters.index_of(path)
    messages = chapters.eligible_in(path, p.lo, p.hi)
    people = people_of(ctx, chat_id, p)
    by_target = {x["target"]: x["name"] for x in people}
    known = names(ctx, chat_id)
    open_rows = []
    for row, state in on_branch(ctx.db, chat_id, {m["id"] for m in path}, quests=quests_on(api.settings)):
        status = status_of(state)
        if index[row["anchor_id"]] > p.hi or status == "dropped" or (status != "active" and row["kind"] != "secret"):
            continue
        open_rows.append((row, status))
    open_rows.sort(key=lambda x: (-x[0]["importance"], -index[x[0]["anchor_id"]]))
    open_threads = [{"code": f"T{i}", "id": row["id"], "kind": row["kind"], "status": status, "quote": row["quote"],
                     "who": by_target.get(row["who"]) or name_of(ctx, known, row["who"]),
                     "to": by_target.get(row["to_whom"]) or name_of(ctx, known, row["to_whom"])}
                    for i, (row, status) in enumerate(open_rows[:MAX_OPEN], 1)]
    return {"messages": messages, "lines": prompts.thread_lines(messages, cast.user_name(ctx, chat_id)),
            "people": people, "open": open_threads, "quests": quests_on(api.settings)}


async def ask(llm: Any, request: dict[str, Any]) -> dict[str, Any]:
    """The one request of a chapter. Raises ProviderError / StructuredOutputError / AppError."""
    messages = prompts.thread_messages(request["lines"], request["people"], request["open"], quests=request["quests"])
    return await llm.generate_json(messages, schema=prompts.THREAD_SCHEMA)


def _resolver(people: list[dict[str, str]]) -> dict[str, str]:
    """code or name (any case) -> target."""
    out: dict[str, str] = {}
    for person in people:
        out.setdefault(person["code"].casefold(), person["target"])
        out.setdefault(person["name"].casefold(), person["target"])
    return out


def _number(value: Any) -> int | None:
    try:
        return int(str(value).strip().lstrip("№#").strip())
    except (TypeError, ValueError):
        return None


def find_message(quote: str, preferred: dict[str, Any] | None, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The message the quote is really in: the one the model named, else any message of the scene; None when the words
    are in none of them (an invented quote)."""
    wanted = norm(quote)
    if not wanted:
        return None
    for message in ([preferred] if preferred else []) + messages:
        if wanted in norm(message.get("content") or ""):
            return message
    return None


def apply(db: Any, chat_id: str, p: chapters.Placed, request: dict[str, Any], value: dict[str, Any], *,
          existing: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, int]:
    """Keep a chapter's answer: new records (exact quotes only, no repeats of a record of the branch) and status
    changes of the open records (never of a locked one). Returns {new, changed}."""
    who_of = _resolver(request["people"])
    messages = request["messages"]
    by_number = {chapters.number_of(m): m for m in messages}
    seen = {norm(row["quote"]) for row, _ in existing}
    allowed = {k for k in KINDS if k != "quest" or request["quests"]}
    made = changed = 0
    for item in value.get("new") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().casefold()
        who = who_of.get(str(item.get("who") or "").strip().casefold())
        quote = _QUOTES.sub("", str(item.get("quote") or "")).strip()[:MAX_QUOTE]
        if kind not in allowed or not who or len(quote) < MIN_QUOTE or norm(quote) in seen:
            continue
        message = find_message(quote, by_number.get(_number(item.get("message_no")) or -1), messages)
        if message is None:
            continue  # not really said in this scene
        to = who_of.get(str(item.get("to") or "").strip().casefold(), "")
        to = "" if to == who else to
        knows: list[str] = []
        if kind == "secret":
            for target in [who, to, *[who_of.get(str(k).strip().casefold(), "") for k in item.get("knows") or []]]:
                if target and target not in knows:
                    knows.append(target)
        importance = min(3, max(1, _number(item.get("importance")) or 2))
        store.put_thread(db, store.new_thread(chat_id, kind, who, quote, message["id"], to_whom=to, knows=knows,
                                              message_id=message["id"], chapter_id=p.row["id"], importance=importance,
                                              keywords=pieces.keywords(quote)))
        seen.add(norm(quote))
        made += 1
    rows = {row["id"]: (row, state) for row, state in existing}
    codes = {t["code"].casefold(): t["id"] for t in request["open"]}
    for item in value.get("changes") or []:
        if not isinstance(item, dict):
            continue
        found = rows.get(codes.get(str(item.get("id") or "").strip().casefold(), ""))
        if found is None or found[0]["locked"]:
            continue
        row, state = found
        status = STATUS_WORDS.get(str(item.get("status") or "").strip().casefold())
        told = [who_of.get(str(k).strip().casefold(), "") for k in item.get("told") or []]
        knows = knows_of(row, state)
        if row["kind"] == "secret":
            knows += [t for t in told if t and t not in knows]
        status = status or status_of(state)
        if status == status_of(state) and knows == knows_of(row, state):
            continue
        message = by_number.get(_number(item.get("message_no")) or -1) or messages[-1]
        store.put_state(db, chat_id, row["id"], message["id"], status, knows, by="auto",
                        note=str(item.get("note") or "").strip(), message_id=message["id"])
        changed += 1
    return {"new": made, "changed": changed}


async def step(api: Any, chat_id: str, path: list[dict[str, Any]], job: Any, clients: Any,
               ) -> tuple[str, float | None, str]:
    """Step 7 of the job. Returns (what happened, seconds until a retry, a note) with the words of
    `work.summary_step`: done / idle / blocked / rate."""
    settings = api.settings
    if not threads_on(settings):
        return "idle", None, ""
    db = api.ctx.db
    rows = chapters.placed(store.chapter_rows(db, chat_id), path)
    runs = store.runs(db, store.THREAD_RUNS, chat_id)
    chosen, wait = layers.next_chapter(rows, {}, runs, summaries_on=False, now=datetime.now(timezone.utc))
    if chosen is None:
        return "idle", wait, ""
    request = plan(api, chat_id, path, chosen)
    if not request["messages"]:
        store.put_run(db, store.THREAD_RUNS, chat_id, chosen.row["id"], "done")
        return "done", None, ""
    live = [p for p in rows if p.row["state"] != "deleted"]
    job.progress(sum(1 for p in live if (runs.get(p.row["id"]) or {}).get("state") == "done"), len(live),
                 "слово и нити")
    try:
        client = clients.get("threads")
        value = await job.step(lambda: ask(client, request))
    except PluginRefused as exc:
        return "blocked", None, exc.message
    except ProviderError as exc:  # before AppError: it is one
        if exc.upstream_status == 429:
            await job.rate_wait(embeddings.RATE_WAIT)
            return "rate", None, ""
        return _failed(db, chat_id, chosen, exc, runs)
    except AppError as exc:
        if exc.code == "no_connection":
            return "blocked", None, "нет подключения к модели для «Слова и нитей»"
        return _failed(db, chat_id, chosen, exc, runs)
    except StructuredOutputError as exc:
        return _failed(db, chat_id, chosen, exc, runs)
    # read again after the request: a hand edit may have come meanwhile (a locked record stays as it is)
    existing = on_branch(db, chat_id, {m["id"] for m in path}, deleted=True)
    apply(db, chat_id, chosen, request, value, existing=existing)
    store.put_run(db, store.THREAD_RUNS, chat_id, chosen.row["id"], "done")
    api.notify("threads", chat_id=chat_id)
    return "done", None, ""


def _failed(db: Any, chat_id: str, chosen: chapters.Placed, exc: Exception,
            runs: dict[str, dict[str, Any]]) -> tuple[str, float | None, str]:
    store.put_run(db, store.THREAD_RUNS, chat_id, chosen.row["id"], "failed", summaries.failure_text(exc))
    tries = int((runs.get(chosen.row["id"]) or {}).get("tries") or 0) + 1
    return "done", summaries.retry_delay(tries) if tries < MAX_TRIES else None, ""


# -- the «Слово и нити» tab ----------------------------------------------------------------------------


def view(api: Any, chat_id: str, storage: Any) -> dict[str, Any]:
    """The records of the open branch grouped by who gave the word (the chat's people first), with their status on
    this branch and its history."""
    ctx = api.ctx
    path = ctx.chats.active_path(chat_id)
    number = {m["id"]: int(m["depth"]) + 1 for m in path}
    known = names(ctx, chat_id)
    people = [{"target": t["target"], "name": t["name"], "is_me": t["is_me"]} for t in layers.targets(ctx, chat_id)]
    persona = persona_ids(ctx, chat_id)
    me_target = layers.persona_target(ctx, chat_id)
    states = store.states_for_path(ctx.db, chat_id, set(number))
    groups: dict[str, dict[str, Any]] = {}
    for row, state in on_branch(ctx.db, chat_id, set(number), deleted=True):
        who = me_target if row["who"] in persona else row["who"]
        group = groups.setdefault(who, {"who": who, "name": name_of(ctx, known, row["who"]), "is_me": who == me_target,
                                        "items": []})
        status = status_of(state)
        group["items"].append({
            "id": row["id"], "kind": row["kind"], "kind_label": KIND_RU.get(row["kind"], row["kind"]),
            "who": row["who"], "to_whom": row["to_whom"], "to_name": name_of(ctx, known, row["to_whom"]),
            "knows": knows_of(row, state), "knows_names": [name_of(ctx, known, k) for k in knows_of(row, state)],
            "quote": row["quote"], "message_id": row["message_id"] or "",
            "message_no": number.get(row["message_id"] or ""), "status": status, "status_label": STATUS_RU[status],
            "note": state["note"] if state else "", "importance": row["importance"], "locked": row["locked"],
            "deleted": row["deleted"], "by": row["made_by"],
            "history": [{"status": s["status"], "status_label": STATUS_RU.get(s["status"], s["status"]),
                         "note": s["note"], "by": s["by"], "created_at": s["created_at"],
                         "message_no": number.get(s["message_id"] or "")} for s in states.get(row["id"], [])]})
    order = {p["target"]: i for i, p in enumerate(people)}
    listed = sorted(groups.values(), key=lambda g: (order.get(g["who"], len(order)), g["name"]))
    return {"on": threads_on(api.settings), "quests_on": quests_on(api.settings), "groups": listed, "people": people,
            "kinds": [[k, KIND_RU[k]] for k in KINDS if k != "quest" or quests_on(api.settings)],
            "statuses": [[s, STATUS_RU[s]] for s in STATUSES], "undo": chapters.undo_label(storage)}


def _need(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AppError(f"Нужен {key}", code="bad_request")
    return value


def _record(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    row = store.get_thread(api.ctx.db, _need(payload, "thread_id"))
    if row is None or row["chat_id"] != chat_id or row["anchor_id"] not in {m["id"] for m in path}:
        raise ThreadError("Такой записи нет на этой ветке")
    return row


def _person(api: Any, chat_id: str, value: Any, *, empty: bool = False) -> str:
    """A target of the chat's people (the persona too); '' when `empty` allows «всем / миру»."""
    if empty and value in (None, ""):
        return ""
    targets = {t["target"] for t in layers.targets(api.ctx, chat_id)} | persona_ids(api.ctx, chat_id)
    if not isinstance(value, str) or value not in targets:
        raise ThreadError("Такого участника в чате нет")
    return value


def _quote(payload: dict[str, Any]) -> str:
    text = payload.get("quote")
    if not isinstance(text, str) or len(text.strip()) < MIN_QUOTE:
        raise AppError("Пустая цитата", code="bad_request")
    return " ".join(text.split())[:MAX_QUOTE]


def _fields(api: Any, chat_id: str, payload: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """The edited fields of a record (those given), checked."""
    out = dict(row)
    if "kind" in payload:
        if payload["kind"] not in KINDS:
            raise ThreadError("Нет такого вида записи")
        out["kind"] = payload["kind"]
    if "quote" in payload:
        out["quote"] = _quote(payload)
        out["keywords"] = pieces.keywords(out["quote"])
    if "who" in payload:
        out["who"] = _person(api, chat_id, payload["who"])
    if "to_whom" in payload:
        out["to_whom"] = _person(api, chat_id, payload["to_whom"], empty=True)
    if "importance" in payload:
        if payload["importance"] not in (1, 2, 3):
            raise ThreadError("Важность: 1, 2 или 3")
        out["importance"] = payload["importance"]
    if "knows" in payload:
        if not isinstance(payload["knows"], list):
            raise AppError("knows: список", code="bad_request")
        out["knows"] = [_person(api, chat_id, k) for k in payload["knows"]]
    return out


def thread_edit(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """A record changed by hand (quote, kind, who, to whom, importance, who knows): locked."""
    row = _record(api, chat_id, path, payload)
    new = {**_fields(api, chat_id, payload, row), "locked": True, "updated_at": now_iso()}
    store.put_thread(api.ctx.db, new)
    chapters.log_thread(storage, "Изменить запись", before=row)


def thread_status(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """A status set by hand on this branch (a new status row anchored before the newest message), the record locked so
    the model does not change it back."""
    db = api.ctx.db
    row = _record(api, chat_id, path, payload)
    status = payload.get("status")
    if status not in STATUSES:
        raise ThreadError("Нет такого статуса")
    states = store.states_for_path(db, chat_id, {m["id"] for m in path}).get(row["id"])
    state = states[0] if states else None
    note = payload.get("note") if isinstance(payload.get("note"), str) else ""
    made = store.put_state(db, chat_id, row["id"], chapters.fresh_anchor(path), status, knows_of(row, state),
                           by="hand", note=note.strip())
    store.put_thread(db, {**row, "locked": True, "updated_at": now_iso()})
    chapters.log_thread(storage, "Изменить статус", before=row, made_state=made)


def thread_lock(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    row = _record(api, chat_id, path, payload)
    store.put_thread(api.ctx.db, {**row, "locked": bool(payload.get("locked")), "updated_at": now_iso()})


def thread_delete(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """«Удалить»: the record leaves the prompt and the model's list (it stays in the tab and can be brought back)."""
    row = _record(api, chat_id, path, payload)
    if row["deleted"]:
        raise ThreadError("Запись уже удалена")
    store.put_thread(api.ctx.db, {**row, "deleted": True, "updated_at": now_iso()})
    chapters.log_thread(storage, "Удалить запись", before=row)


def thread_restore(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    row = _record(api, chat_id, path, payload)
    if not row["deleted"]:
        raise ThreadError("Эта запись не удалена")
    store.put_thread(api.ctx.db, {**row, "deleted": False, "updated_at": now_iso()})
    chapters.log_thread(storage, "Вернуть запись", before=row)


def thread_add(api: Any, chat_id: str, path: list[dict[str, Any]], payload: dict[str, Any], storage: Any) -> None:
    """«+ Добавить» by hand: kind, who, quote, to whom, importance; `number` = the message it is from (optional; else
    it is anchored before the newest message). Locked."""
    kind = payload.get("kind")
    if kind not in KINDS:
        raise ThreadError("Нет такого вида записи")
    who = _person(api, chat_id, payload.get("who"))
    to = _person(api, chat_id, payload.get("to_whom"), empty=True)
    quote = _quote(payload)
    number = payload.get("number")
    message = None
    if number not in (None, ""):
        message = next((m for m in path if isinstance(number, int) and chapters.number_of(m) == number), None)
        if message is None:
            raise ThreadError("Нет сообщения с таким номером на этой ветке")
    importance = payload.get("importance", 2)
    if importance not in (1, 2, 3):
        raise ThreadError("Важность: 1, 2 или 3")
    knows = [who] + ([to] if to and to != who else []) if kind == "secret" else []
    row = store.new_thread(chat_id, kind, who, quote, message["id"] if message else chapters.fresh_anchor(path),
                           to_whom="" if to == who else to, knows=knows,
                           message_id=message["id"] if message else None, importance=importance,
                           keywords=pieces.keywords(quote), by="hand", locked=True)
    store.put_thread(api.ctx.db, row)
    chapters.log_thread(storage, "Добавить запись", created=row["id"])
