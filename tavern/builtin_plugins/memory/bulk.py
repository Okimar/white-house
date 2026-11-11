"""«Разобрать весь чат» (ADR 0019 stage M7): go through a whole old or imported chat in the background, and tell the PRICE
first.

The work itself is the chat's ordinary job (`work.py`: pieces, vectors, chapters, and with their switches summaries,
the plot, development, threads, faces), which already goes chapter by chapter, gives way to the user at once and
redoes the cut step. What «Разобрать весь чат» adds:
  * `estimate`: how many requests, how many tokens and about how much money, from the chat's own messages and the
    prices the app knows (services/spending.py `price_for`), BEFORE anything is spent. It is an estimate: the real
    sum is shown next to it while the work goes (`state`).
  * `start`: only with the user's «yes» (the page asks; the assistant asks too, the action has `confirm`). It switches
    memory on in the chat, remembers the start, and wakes the job. A job that gave up on rate limits is woken again
    later by the plugin (server.py) while this mark stays; a restart picks it up too.
  * `state`: progress per step, the real spending since the start; the mark goes off by itself when nothing is left.
Which steps run is decided by the settings as they are now (the estimate names them): a switch that is off costs nothing.
"""

from __future__ import annotations

from typing import Any

from tavern.core.prompt import estimate_tokens
from tavern.errors import AppError
from tavern.services import embeddings, spending
from tavern.util.ids import now_iso

from . import chapters, faces, layers, pieces, store, threads

KEY = "bulk"  # plugin storage of the chat: {"on": bool, "started": iso, "estimate": {...}}
OVERHEAD = {"summary": 450, "dev": 900, "threads": 1000, "faces": 900}  # prompt tokens a request adds to the text
OUT = {"threads": 500, "faces": 350, "plot": 500}  # answer tokens of the steps that have no setting of their own
PLOT_EVERY = 4  # the plot folds this many new summaries per request
STEP_NAMES = {"summary": "пересказы глав", "plot": "«Сюжет до сих пор»", "dev": "развитие персонажей",
              "threads": "«Слово и нити»", "faces": "лица"}


def money_text(ctx: Any, dollars: float) -> str:
    """«≈ 0,35 $» in the currency the user chose for the spending lines (dollars by default)."""
    currency = spending.get_settings(ctx)["currency"]
    value = dollars * currency["rate"] if currency["code"] else dollars
    unit = currency["code"] or "$"
    return f"{value:.2f} {unit}".replace(".", ",")


def llm_target(api: Any, chat_id: str | None) -> tuple[str, str, dict[str, Any]]:
    """(connection id, model, price) of «Модель для памяти»; ('', '', no price) when there is no connection."""
    settings = api.settings
    try:
        connection = api._connection(str(settings.get("memory_connection") or ""), chat_id)
    except AppError:
        return "", "", {"in": None, "out": None, "source": ""}
    model = str(settings.get("memory_model_name") or "").strip() or connection.get("model", "")
    return connection["id"], model, spending.price_for(api.ctx, connection["id"], model)


def embed_target(api: Any) -> tuple[str, dict[str, Any]]:
    """(model, price) of the vector connection ('' when none)."""
    connection = embeddings.embedding_connection(api.ctx)
    if connection is None:
        return "", {"in": None, "out": None, "source": ""}
    return connection["model"], spending.price_for(api.ctx, connection["id"], connection["model"])


def price(api: Any, chat_id: str | None, requests: list[tuple[str, int, int, int]], embed_tokens: int) -> dict[str, Any]:
    """The money for `requests` [(step, count, tokens in each, tokens out each)] and `embed_tokens` of vectors.
    `cost` is None when the model's price is not known (the text then says so: never a made-up number)."""
    _, model, p = llm_target(api, chat_id)
    emodel, ep = embed_target(api)
    tin = sum(n * i for _, n, i, _ in requests)
    tout = sum(n * o for _, n, _, o in requests)
    count = sum(n for _, n, _, _ in requests)
    cost: float | None = 0.0
    unknown = []
    if count:
        if p["in"] is None and p["out"] is None:
            cost = None
            unknown.append(model or "модель памяти")
        else:
            cost += (tin * (p["in"] or 0) + tout * (p["out"] or 0)) / 1_000_000
    if embed_tokens and emodel:
        if ep["in"] is None:
            cost = None
            unknown.append(emodel)
        elif cost is not None:
            cost += embed_tokens * ep["in"] / 1_000_000
    if cost is None:
        text = (f"цена не известна для: {', '.join(unknown)}. Задайте её в «Расход» → цены, и здесь будет сумма")
    else:
        text = "около " + money_text(api.ctx, cost) if cost >= 0.005 else "меньше цента"
    return {"requests": count, "tokens_in": tin, "tokens_out": tout, "embed_tokens": embed_tokens,
            "cost": cost, "price_text": text, "model": model, "embed_model": emodel}


def _chapter_tokens(path: list[dict[str, Any]], p: chapters.Placed) -> int:
    return sum(estimate_tokens(m["content"]) for m in chapters.eligible_in(path, p.lo, p.hi))


def _steps_on(api: Any) -> dict[str, bool]:
    s = api.settings
    return {"summary": bool(s.get("summaries")), "plot": bool(s.get("plot")), "dev": layers.development_on(s),
            "threads": threads.threads_on(s), "faces": faces.faces_on(s)}


def progress(api: Any, chat_id: str, path: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """step -> (chapters done, chapters of the branch) for each switched-on step."""
    db = api.ctx.db
    rows = [p for p in chapters.placed(store.chapter_rows(db, chat_id), path) if p.row["state"] != "deleted"]
    ids = [p.row["id"] for p in rows]
    on = _steps_on(api)
    out: dict[str, tuple[int, int]] = {}
    sums = store.current_summaries(db, chat_id)
    if on["summary"]:
        out["summary"] = (sum(1 for i in ids if i in sums), len(ids))
    for name, table, flag in (("dev", store.LAYER_RUNS, "dev"), ("threads", store.THREAD_RUNS, "threads"),
                              ("faces", store.FACE_RUNS, "faces")):
        if on[flag]:
            runs = store.runs(db, table, chat_id)
            out[name] = (sum(1 for i in ids if runs.get(i, {}).get("state") == "done"), len(ids))
    return out


def estimate(api: Any, chat_id: str) -> dict[str, Any]:
    """What «Разобрать весь чат» would ask of the model and the vector service, and about what it costs."""
    ctx = api.ctx
    db = ctx.db
    settings = api.settings
    path = ctx.chats.active_path(chat_id)
    size = max(4, int(settings["chapter_size"]))
    rows = chapters.placed(store.chapter_rows(db, chat_id), path)
    live = [p for p in rows if p.row["state"] != "deleted"]
    open_part = [m for m in path[chapters.open_start(rows):] if pieces.eligible(m)]
    new_n = len(open_part) // size if len(open_part) > size else 0
    per_message = (sum(estimate_tokens(m["content"]) for m in open_part) / len(open_part)) if open_part else 0.0
    sizes = {p.row["id"]: _chapter_tokens(path, p) for p in live}
    new_each = int(per_message * size)
    on = _steps_on(api)
    sums = store.current_summaries(db, chat_id)
    pending: dict[str, list[int]] = {}
    pending["summary"] = [sizes[i] for i in sizes if i not in sums] + [new_each] * new_n
    for name, table in (("dev", store.LAYER_RUNS), ("threads", store.THREAD_RUNS), ("faces", store.FACE_RUNS)):
        done = {i for i, r in store.runs(db, table, chat_id).items() if r["state"] == "done"}
        pending[name] = [sizes[i] for i in sizes if i not in done] + [new_each] * new_n
    requests: list[tuple[str, int, int, int]] = []
    steps: list[dict[str, Any]] = []

    def add(name: str, chapters_: list[int], overhead: int, out: int) -> None:
        if chapters_:
            mean = sum(chapters_) // len(chapters_)
            requests.append((name, len(chapters_), min(mean, 16000) + overhead, out))
            steps.append({"step": name, "name": STEP_NAMES[name], "count": len(chapters_)})

    if on["summary"]:
        add("summary", pending["summary"], OVERHEAD["summary"], int(settings["summary_tokens"]))
    if on["plot"]:
        count = max(1, -(-len(pending["summary"]) // PLOT_EVERY)) if pending["summary"] else 0
        if count:
            requests.append(("plot", count, 500 + int(settings["summary_tokens"]) * PLOT_EVERY, OUT["plot"]))
            steps.append({"step": "plot", "name": STEP_NAMES["plot"], "count": count})
    if on["dev"]:
        add("dev", pending["dev"], OVERHEAD["dev"], int(settings["dev_tokens"]))
    if on["threads"]:
        add("threads", pending["threads"], OVERHEAD["threads"], OUT["threads"])
    if on["faces"]:
        add("faces", pending["faces"], OVERHEAD["faces"], OUT["faces"])
    embed_tokens = _vector_tokens(api, chat_id, path)
    money = price(api, chat_id, requests, embed_tokens)
    return {**money, "messages": sum(1 for m in path if pieces.eligible(m)), "chapters_now": len(live),
            "chapters_new": new_n, "steps": steps, "size": size,
            "off": [STEP_NAMES[k] for k, v in on.items() if not v]}


def _vector_tokens(api: Any, chat_id: str, path: list[dict[str, Any]]) -> int:
    """Tokens of the pieces that still need a vector (nothing without a vector connection)."""
    model = api.vectors_model()
    if not model:
        return 0
    db = api.ctx.db
    have = {(p["message_id"], p["part"]): p for p in store.pieces(db, chat_id)}
    stored = store.current_vector_ids(db, chat_id, model)
    latest = pieces.latest_ids(path)
    total = 0
    for spec in pieces.specs([m for m in path if m["id"] not in latest]):
        row = have.get((spec.message_id, spec.part))
        if row is None or stored.get(row["id"]) != spec.hash:
            total += spec.tokens
    return total


def spent_since(api: Any, chat_id: str, since: str) -> dict[str, Any]:
    row = api.ctx.db.fetchone(
        "SELECT COUNT(*) AS n, COALESCE(SUM(cost), 0) AS cost, COALESCE(SUM(prompt_tokens + completion_tokens), 0) "
        "AS tokens FROM spending WHERE purpose = 'plugin:memory' AND chat_id = ? AND at >= ?", (chat_id, since))
    return {"requests": int(row["n"]), "cost": float(row["cost"]), "tokens": int(row["tokens"])}


def is_on(api: Any, chat_id: str) -> bool:
    return bool((api.storage(chat_id).get(KEY) or {}).get("on"))


def start(api: Any, chat_id: str) -> dict[str, Any]:
    """Remember that the user agreed to the price, and switch memory on in the chat. The caller wakes the job."""
    found = estimate(api, chat_id)
    store.set_enabled(api.ctx.db, chat_id, True)
    api.storage(chat_id).set(KEY, {"on": True, "started": now_iso(), "estimate": {
        "requests": found["requests"], "price_text": found["price_text"], "cost": found["cost"]}})
    return found


def stop(api: Any, chat_id: str) -> None:
    mark = api.storage(chat_id).get(KEY) or {}
    api.storage(chat_id).set(KEY, {**mark, "on": False, "stopped": now_iso()})


def state(api: Any, chat_id: str) -> dict[str, Any]:
    """Progress of «Разобрать весь чат»: on / finished, per step, the real spending since the start."""
    mark = api.storage(chat_id).get(KEY) or {}
    path = api.ctx.chats.active_path(chat_id)
    done = progress(api, chat_id, path)
    job = api.job_status(f"chat:{chat_id}")
    size = max(4, int(api.settings["chapter_size"]))
    rows = chapters.placed(store.chapter_rows(api.ctx.db, chat_id), path)
    open_n = len([m for m in path[chapters.open_start(rows):] if pieces.eligible(m)])
    left = sum(total - got for got, total in done.values()) + (1 if open_n > size else 0)
    working = bool(job["running"] or job["queued"])
    if mark.get("on") and not working and left == 0 and store.is_enabled(api.ctx.db, chat_id):
        mark = {**mark, "on": False, "finished": now_iso()}
        api.storage(chat_id).set(KEY, mark)
    out: dict[str, Any] = {"on": bool(mark.get("on")), "finished": bool(mark.get("finished")),
                           "started": mark.get("started", ""), "estimate": (mark.get("estimate") or {}).get("price_text", ""),
                           "steps": [{"step": k, "name": STEP_NAMES[k], "done": g, "total": t} for k, (g, t) in done.items()],
                           "left": left, "job": job}
    if mark.get("started"):
        spent = spent_since(api, chat_id, mark["started"])
        out["spent"] = {**spent, "text": money_text(api.ctx, spent["cost"])}
    return out
