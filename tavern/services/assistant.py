"""The assistant over the chat (stage 8б, engine of stage 10а): settings, conversations, requests,
confirmations, undo and «Что я о вас знаю».

How a request is worked on lives in services/assistant_engine.py: small requests run a step loop
by a form, big work goes questions → plan → items → critic → report on a task kept with the
conversation. Every change goes to the journal with a snapshot of what was there before;
"Отменить" puts back that action and every later one.

Settings (key "assistant"): source ("" = the chat's model, "connection:<id>", "profile:<id>"),
max_steps (model calls per small request and per plan item), read_messages, day / month (own
spending limits in the shown currency, 0 = none), roles {planner, writer, critic, small: {source,
model}} ("" = as the assistant), critic (on for big work), critic_rounds (1..5), progress ("full" |
"minimal": what the window shows while it works), auto (true: the «Разрешить?» questions for its own actions
are not asked; the copy before risky actions, undo and the closed routes stay), web (default true: the internet
tools web_search / read_page / install_skill; search words and addresses go out to the sites), web_engine
("duckduckgo" | "searxng") and web_url (the address of the person's own SearXNG, may be on their network). Requests are spent as purpose "assistant"; the
global limits apply too. Runs go through ctx.runs under "assistant:<conversation id>" (events:
start, thinking, message, action, task, confirm, confirmed, done | stopped | error), so
/runs/{id}/events follows them.
"""

from __future__ import annotations

import logging
from typing import Any

from tavern.context import AppContext
from tavern.core.runs import Run
from tavern.errors import AppError, Conflict, NotFound
from tavern.providers.params import request_params
from tavern.services import assistant_app, assistant_files, profiles, spending, web_access
from tavern.services.chat import make_budget, resolve_connection
from tavern.storage import snapshot
from tavern.storage.repos.spending import today
from tavern.util.ids import now_iso

log = logging.getLogger(__name__)

KEY = "assistant"
PURPOSE = "assistant"
RUN_PREFIX = "assistant:"
MAX_TEXT = 20_000  # one message from the user
STEP_LIMIT_MARK = "предел на одну просьбу"  # the engine's note when a short request ran out of steps
MAX_FACT = 300  # one line of «Что я о вас знаю»
ROLES = ("planner", "writer", "critic", "small")
WORK_CALLS = 1000  # model calls one run may make at most (step limits and money limits stop it sooner)


# --- settings ---------------------------------------------------------------------------------


def defaults() -> dict[str, Any]:
    return {"source": "", "max_steps": 12, "day": 0.0, "month": 0.0, "read_messages": 50,
            "roles": {role: {"source": "", "model": ""} for role in ROLES},
            "critic": True, "critic_rounds": 2, "progress": "full", "auto": False,
            "web": True, "web_engine": "duckduckgo", "web_url": ""}


def _source(value: Any, label: str) -> str:
    if not isinstance(value, str) or not (value == "" or value.startswith(("connection:", "profile:"))):
        raise AppError(f"{label}: empty, connection:<id> or profile:<id>")
    return value


def _int(raw: dict[str, Any], key: str, low: int, high: int, message: str, fallback: int) -> int:
    value = raw.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise AppError(message)
    return value


def check(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AppError("Assistant settings must be an object")
    clean = defaults()
    clean["source"] = _source(raw.get("source", ""), "source")
    clean["max_steps"] = _int(raw, "max_steps", 1, 50, "max_steps must be 1..50", clean["max_steps"])
    clean["read_messages"] = _int(raw, "read_messages", 0, 100_000,
                                  "read_messages: how many messages of a chat it reads at once (0 = the whole chat)",
                                  clean["read_messages"])
    for period in ("day", "month"):
        value = raw.get(period, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise AppError(f"{period} must be a number from 0 (0 = no limit)")
        clean[period] = float(value)
    roles = raw.get("roles", {})
    if not isinstance(roles, dict):
        raise AppError("roles must be an object")
    for role in ROLES:
        spec = roles.get(role) or {}
        if not isinstance(spec, dict):
            raise AppError(f"roles.{role} must be an object")
        model = spec.get("model", "")
        if not isinstance(model, str) or len(model) > 200:
            raise AppError(f"roles.{role}.model must be a model name")
        clean["roles"][role] = {"source": _source(spec.get("source", ""), f"roles.{role}.source"),
                                "model": model.strip()}
    if not isinstance(raw.get("critic", True), bool):
        raise AppError("critic must be true or false")
    clean["critic"] = raw.get("critic", True)
    clean["critic_rounds"] = _int(raw, "critic_rounds", 1, 5, "critic_rounds must be 1..5", clean["critic_rounds"])
    if raw.get("progress", "full") not in ("full", "minimal"):
        raise AppError("progress must be full or minimal")
    clean["progress"] = raw.get("progress", "full")
    if not isinstance(raw.get("auto", False), bool):
        raise AppError("auto must be true or false")
    clean["auto"] = raw.get("auto", False)
    if not isinstance(raw.get("web", True), bool):
        raise AppError("web must be true or false")
    clean["web"] = raw.get("web", True)
    if raw.get("web_engine", "duckduckgo") not in ("duckduckgo", "searxng"):
        raise AppError("web_engine must be duckduckgo or searxng")
    clean["web_engine"] = raw.get("web_engine", "duckduckgo")
    clean["web_url"] = web_access.check_search_address(raw.get("web_url", ""))
    return clean


def get_settings(ctx: AppContext) -> dict[str, Any]:
    try:
        return check(ctx.settings.get(KEY) or {})
    except AppError:
        return defaults()


def save_settings(ctx: AppContext, raw: Any) -> dict[str, Any]:
    settings = check(raw)
    for source in [settings["source"], *(spec["source"] for spec in settings["roles"].values())]:
        kind, _, target_id = source.partition(":")
        if kind == "connection":
            ctx.connections.get(target_id)  # NotFound for a deleted one
        elif kind == "profile":
            _profile(ctx, target_id)
    ctx.settings.set(KEY, settings)
    return settings


def _profile(ctx: AppContext, profile_id: str) -> dict[str, Any]:
    for profile in profiles.list_profiles(ctx)["profiles"]:
        if profile["id"] == profile_id:
            return profile
    raise NotFound("The assistant's profile was deleted; pick another in its settings")


def target(ctx: AppContext, source: str, chat_id: str | None) -> tuple[dict, str, dict]:
    """(connection, model, request defaults) a source points to ("" = the chat's model)."""
    kind, _, target_id = source.partition(":")
    try:
        if kind == "connection":
            connection = ctx.connections.get(target_id)
            model, params = connection["model"], dict(connection["params"])
        elif kind == "profile":
            profile = _profile(ctx, target_id)
            connection = ctx.connections.get(profile["connection_id"])
            model = profile["model"] if profile["parts"].get("model") else connection["model"]
            params = dict(profile["params"] if profile["parts"].get("params") else connection["params"])
        else:
            chat = ctx.chats.get_chat(chat_id) if chat_id else {"meta": {}}
            connection = resolve_connection(ctx, chat)
            model, params = connection["model"], dict(connection["params"])
    except NotFound as exc:
        raise Conflict(f"{exc.message}. Выберите модель помощника в его настройках.", code="no_connection") from None
    params.pop("max_context_tokens", None)
    return connection, model, request_params(connection["kind"], params)


def spent(ctx: AppContext) -> dict[str, Any]:
    """What the assistant spent today and this month (dollars)."""
    day = today()
    return {"day": ctx.spending.purpose(PURPOSE, day=day), "month": ctx.spending.purpose(PURPOSE, month=day[:7])}


def gate(ctx: AppContext):
    async def check_limits() -> None:
        spending.guard(ctx)  # the global limits (raises when they say stop)
        own = get_settings(ctx)
        money = spending.get_settings(ctx)
        rate = spending.rate(money)
        sums = spent(ctx)
        for period, label in (("day", "день"), ("month", "месяц")):
            limit = own[period]
            if limit and sums[period]["cost"] * rate >= limit:
                raise Conflict(f"Помощник: лимит расхода за {label} достигнут "
                               f"({sums[period]['cost'] * rate:.2f} из {limit:g} "
                               f"{money['currency']['code'] or '$'}). Поднимите его в настройках помощника.",
                               code="assistant_limit")
    return check_limits


# --- conversations ----------------------------------------------------------------------------


def run_key(conversation_id: str) -> str:
    return RUN_PREFIX + conversation_id


def list_chats(ctx: AppContext) -> list[dict[str, Any]]:
    return [{k: v for k, v in c.items() if k != "task"} | {"phase": c["task"].get("phase", ""),
                                                           "imported": bool(c["task"].get("imported")),
                                                           "running": ctx.runs.active(run_key(c["id"])) is not None}
            for c in ctx.assistant_repo.chats()]


def create_chat(ctx: AppContext) -> dict[str, Any]:
    return ctx.assistant_repo.create_chat()


def delete_chat(ctx: AppContext, conversation_id: str) -> None:
    if ctx.runs.active(run_key(conversation_id)) is not None:
        raise Conflict("The assistant is working in this conversation; stop it first", code="busy")
    ctx.assistant_repo.delete_chat(conversation_id)


def rename_chat(ctx: AppContext, conversation_id: str, title: Any) -> dict[str, Any]:
    if not isinstance(title, str) or len(title) > 200:
        raise AppError("title must be text up to 200 characters")
    return ctx.assistant_repo.rename_chat(conversation_id, title.strip())


def _left_midway(task: dict[str, Any], messages: list[dict[str, Any]]) -> bool:
    """Did the last run end without finishing (the user's «Стоп», an error, a limit, a restart)?
    Then «Продолжить» goes on from there. Questions and a plan waiting for the user have their own buttons."""
    phase = task.get("phase")
    if phase == "work":
        return True
    if phase == "questions":  # answered, and the plan was not made: the questions are not waiting for answers
        return bool(task.get("answers"))
    if phase == "plan":
        return False
    last = messages[-1] if messages else None
    if last is None:
        return False
    data = last.get("data") or {}
    if last["role"] == "user":
        return True  # a request nobody answered
    if last["role"] == "tool":
        return "results" in data  # stopped between a step's actions and the next step
    return bool(data.get("error")) or STEP_LIMIT_MARK in (last.get("text") or "")


def get_chat(ctx: AppContext, conversation_id: str) -> dict[str, Any]:
    from tavern.services import assistant_engine as engine  # the engine imports this module

    chat = ctx.assistant_repo.chat(conversation_id)
    run = ctx.runs.active(run_key(conversation_id))
    messages = ctx.assistant_repo.messages(conversation_id)
    return {"chat": {k: v for k, v in chat.items() if k != "task"}, "task": chat["task"],
            "messages": messages, "resumable": run is None and not chat["task"].get("imported") and _left_midway(chat["task"], messages),
            "actions": ctx.assistant_repo.actions(conversation_id),
            "run_id": run.id if run else None, "ask": engine.pending_ask(conversation_id)}


def state(ctx: AppContext) -> dict[str, list[str]]:
    """For the 🪄 mark at the top of the page: conversations working now, and those waiting for the
    user (questions, a plan, «Разрешить?»)."""
    from tavern.services import assistant_engine as engine

    running, waiting = [], []
    for chat in ctx.assistant_repo.chats():
        task = chat["task"]
        # Work left midway by a restart or a failure waits too; one the user stopped does not nag.
        waits = task.get("phase") in ("questions", "plan") or (task.get("phase") == "work" and not task.get("stopped"))
        if engine.pending_ask(chat["id"]) or (waits and ctx.runs.active(run_key(chat["id"])) is None):
            waiting.append(chat["id"])
        elif ctx.runs.active(run_key(chat["id"])) is not None:
            running.append(chat["id"])
    return {"running": running, "waiting": waiting}


def answer(conversation_id: str, ask_id: str, allow: bool) -> None:
    from tavern.services import assistant_engine as engine

    engine.answer(conversation_id, ask_id, allow)


# --- requests ---------------------------------------------------------------------------------


def _answers_text(task: dict[str, Any], answers: dict[str, str], to_taste: set[str] = frozenset()) -> str:
    lines = [f"{q['text']} — " + (f"на ваш вкус (предложено: {answers[q['id']]})" if q["id"] in to_taste else answers[q["id"]])
             for q in task.get("questions", []) if q["id"] in answers]
    return "Ответы:\n" + "\n".join(lines)


def start(ctx: AppContext, conversation_id: str, text: Any = "", chat_id: Any = None, *,
          answers: Any = None, go: bool = False, attachments: Any = None) -> Run:
    """Save what the user sent and work on it in the background.

    `answers` ({question id: text}; missing ones take their default) answer the task's questions;
    `go` approves the plan («Давай») or continues work left midway. A message sent while the plan
    items are being done goes to the task's inbox and the running work takes it in: the running Run
    is returned. Sent to work left midway, it goes to the inbox too and the work goes on with it.
    `attachments` (services/assistant_files.py) are files and library pictures that go with the message
    as data; the text may then be empty.
    """
    from tavern.services import assistant_engine as engine

    repo = ctx.assistant_repo
    chat = repo.chat(conversation_id)
    task = chat["task"]
    key = run_key(conversation_id)
    chat_id = chat_id if isinstance(chat_id, str) and chat_id else None
    running = ctx.runs.active(key)
    if answers is not None:
        if task.get("phase") != "questions" or running is not None:
            raise Conflict("Вопросов сейчас нет", code="no_questions")
        if not isinstance(answers, dict):
            raise AppError("answers must be an object {question id: text}")
        chosen: dict[str, str] = {}
        left_to_taste: set[str] = set()  # not answered: the assistant's own choice, said so in the message
        for question in task["questions"]:
            value = answers.get(question["id"])
            if isinstance(value, str) and value.strip():
                chosen[question["id"]] = value.strip()[:1000]
            else:
                chosen[question["id"]] = question["options"][question.get("default", 0)]
                left_to_taste.add(question["id"])
        task["answers"] = chosen
        if "language" in chosen:  # the second round of questions has no language question
            task["language"] = chosen["language"]
        text, go = _answers_text(task, chosen, left_to_taste), True
    elif go:
        if running is not None:
            raise Conflict("Плана, который ждёт «Давай», сейчас нет", code="no_plan")
        phase = task.get("phase")
        if phase not in ("plan", "work") and not _left_midway(task, repo.messages(conversation_id)):
            raise Conflict("Плана, который ждёт «Давай», сейчас нет", code="no_plan")
        text = "Давай" if phase == "plan" else "Продолжай"
        if phase not in ("plan", "work", "questions"):
            go = False  # a short request that was interrupted: the model reads the history and goes on
    files = assistant_files.clean(ctx, attachments) if answers is None and not go else []
    if files and (not isinstance(text, str) or not text.strip()):
        text = "Посмотри приложенное: " + ", ".join(f["name"] for f in files)
    if not isinstance(text, str) or not text.strip():
        raise AppError("Write what the assistant should do", code="empty_message")
    if len(text) > MAX_TEXT:
        raise AppError(f"At most {MAX_TEXT} characters", code="too_long")
    text = text.strip()
    if running is not None:
        if task.get("phase") == "work":
            message = repo.add_message(conversation_id, "user", text, {"inbox": True, **_files_data(files)})
            running.emit("message", message=message)
            return running
        raise Conflict("The assistant is already working here", code="busy")
    resuming = task.get("phase") == "work"
    if answers is not None or (resuming and task.pop("stopped", None)):
        repo.set_task(conversation_id, task)
    data: dict[str, Any] = {"answers": True} if answers is not None else {"inbox": True} if resuming and not go else {}
    message = repo.add_message(conversation_id, "user", text, {**data, **_files_data(files)})
    work_text = assistant_files.text_for_work(message)
    if not chat["title"]:
        repo.rename_chat(conversation_id, " ".join(text.split())[:60])
    budget = make_budget(ctx)
    budget.max_steps = WORK_CALLS  # the step limits per request and per item stop a run long before

    async def body(run: Run) -> None:
        run.emit("start", run_id=run.id, message=message)
        ctx.notices.emit("assistant_state", chat_id=conversation_id, phase=task.get("phase", ""))
        await engine.run_request(ctx, conversation_id, chat_id, run, work_text, go=go)

    return ctx.runs.start(key, budget, body)


def _files_data(files: list[dict[str, Any]]) -> dict[str, Any]:
    return {"attachments": files} if files else {}


def stop(ctx: AppContext, conversation_id: str) -> bool:
    from tavern.services import assistant_engine as engine

    engine.refuse_all(conversation_id)
    return ctx.runs.stop(run_key(conversation_id))


def drop_task(ctx: AppContext, conversation_id: str) -> None:
    """«Отменить задачу»: forget its questions / plan; what was already made stays (undo is per action)."""
    if ctx.runs.active(run_key(conversation_id)) is not None:
        raise Conflict("Сначала остановите помощника", code="busy")
    task = ctx.assistant_repo.task(conversation_id)
    if task.get("phase") in ("questions", "plan", "work"):
        ctx.assistant_repo.set_task(conversation_id, {**task, "phase": "dropped"})
        ctx.assistant_repo.add_message(conversation_id, "assistant", "Задача отменена.", {"kind": "progress"})


# --- «Что я о вас знаю» -----------------------------------------------------------------------


def _fact_text(text: Any) -> str:
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_FACT:
        raise AppError(f"A line of memory: text up to {MAX_FACT} characters")
    return text.strip()


def memory(ctx: AppContext) -> list[dict[str, Any]]:
    return ctx.assistant_repo.memory()


def add_fact(ctx: AppContext, text: Any) -> dict[str, Any]:
    return ctx.assistant_repo.remember(_fact_text(text))


def edit_fact(ctx: AppContext, fact_id: int, text: Any) -> dict[str, Any]:
    return ctx.assistant_repo.edit_fact(fact_id, _fact_text(text))


def forget(ctx: AppContext, fact_id: int) -> None:
    ctx.assistant_repo.forget(fact_id)


# --- export and import of a conversation ------------------------------------------------------

EXPORT_FORMAT = "tavern-assistant-chat"
EXPORT_VERSION = 1
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_MESSAGES = 5000
MAX_IMPORT_ACTIONS = 5000
_HIDDEN = "[скрыто]"


def _scrub(value: Any) -> Any:
    """A copy without anything that looks like a key or a password (the assistant never sees them, but a
    file that travels must not rely on that)."""
    if isinstance(value, dict):
        return {k: (_HIDDEN if assistant_app.SECRET_WORDS.match(str(k)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _portable_data(data: dict[str, Any]) -> dict[str, Any]:
    """Message data for a file: pictures of the library are named, not carried (they stay on this device)."""
    data = _scrub(data)
    if isinstance(data.get("attachments"), list):
        data["attachments"] = [{k: v for k, v in a.items() if k != "asset"} | ({"gone": True} if a.get("kind") == "image" else {})
                               for a in data["attachments"] if isinstance(a, dict)]
    return data


def export_chat(ctx: AppContext, conversation_id: str) -> dict[str, Any]:
    """The conversation as one versioned JSON document: messages, the task and the journal lines (what was
    done, without the means to undo it: those rows belong to this installation)."""
    repo = ctx.assistant_repo
    chat = repo.chat(conversation_id)
    task = _scrub(chat["task"])
    return {
        "format": EXPORT_FORMAT, "version": EXPORT_VERSION, "exported_at": now_iso(),
        "title": chat["title"], "created_at": chat["created_at"], "updated_at": chat["updated_at"],
        "task": task,
        "messages": [{"role": m["role"], "text": m["text"], "data": _portable_data(m["data"]),
                      "created_at": m["created_at"]} for m in repo.messages(conversation_id)],
        "actions": [{"tool": a["tool"], "what": a["what"], "status": a["status"], "created_at": a["created_at"]}
                    for a in repo.actions(conversation_id)],
    }


def _bad_file(why: str) -> AppError:
    return AppError(f"Это не беседа с помощником или файл повреждён: {why}", code="bad_import")


def _str(value: Any, label: str, limit: int, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not empty and not value) or len(value) > limit:
        raise _bad_file(f"{label} — не текст или слишком длинный")
    return value


def _when(raw: dict[str, Any], key: str = "created_at") -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and 10 <= len(value) <= 40 else None


def import_chat(ctx: AppContext, payload: Any) -> dict[str, Any]:
    """A new conversation from an exported file. Everything is checked first; nothing is written when
    anything is wrong. The conversation is marked imported; its actions cannot be undone (the rows they
    changed are not this installation's); a task that was running is closed."""
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise _bad_file("нет пометки формата")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _bad_file("нет номера версии")
    if version > EXPORT_VERSION:
        raise AppError("Файл сделан более новой версией приложения: обновите приложение, чтобы его прочитать",
                       code="bad_import")
    title = _str(payload.get("title", ""), "название", 200)
    raw_messages, raw_actions = payload.get("messages"), payload.get("actions", [])
    if not isinstance(raw_messages, list) or not isinstance(raw_actions, list):
        raise _bad_file("сообщения и действия должны быть списками")
    if len(raw_messages) > MAX_IMPORT_MESSAGES or len(raw_actions) > MAX_IMPORT_ACTIONS:
        raise _bad_file("слишком много сообщений")
    task = payload.get("task", {})
    if not isinstance(task, dict):
        raise _bad_file("состояние задачи должно быть объектом")
    messages = []
    for raw in raw_messages:
        if not isinstance(raw, dict) or raw.get("role") not in ("user", "assistant", "tool"):
            raise _bad_file("у сообщения нет роли user, assistant или tool")
        data = raw.get("data", {})
        if not isinstance(data, dict):
            raise _bad_file("данные сообщения должны быть объектом")
        messages.append((raw["role"], _str(raw.get("text", ""), "текст сообщения", 200_000), data, _when(raw)))
    actions = []
    for raw in raw_actions:
        if not isinstance(raw, dict):
            raise _bad_file("действие должно быть объектом")
        actions.append((_str(raw.get("tool", ""), "имя действия", 100),
                        _str(raw.get("what", ""), "описание действия", 1000), _when(raw)))
    task = _scrub(task)
    if task.get("phase") in ("questions", "plan", "work"):
        task["phase"] = "dropped"  # nobody is working on it here
    task.pop("stopped", None)
    task["imported"] = True
    repo = ctx.assistant_repo
    made = repo.create_chat((title + " (импорт)").strip()[:200], _when(payload, "created_at"), _when(payload, "updated_at"))
    try:
        repo.set_task(made["id"], task)
        for role, text, data, when in messages:
            data = _scrub(data)
            if isinstance(data.get("attachments"), list):  # pictures of another device are not here
                data["attachments"] = [{k: v for k, v in a.items() if k != "asset"} | {"gone": True} if a.get("kind") == "image"
                                       else a for a in data["attachments"] if isinstance(a, dict)]
            repo.add_message(made["id"], role, text, data, created_at=when)
        for tool, what, when in actions:
            repo.add_action(made["id"], tool, {}, what, {}, "imported", created_at=when)
    except Exception:
        repo.delete_chat(made["id"])
        raise
    return repo.chat(made["id"])


# --- undo -------------------------------------------------------------------------------------


async def undo(ctx: AppContext, action_id: int) -> list[int]:
    """Put back this action and every later one of its conversation still in force. Returns their ids."""
    repo = ctx.assistant_repo
    action = repo.action(action_id)
    if ctx.runs.active(run_key(action["chat_id"])) is not None:
        raise Conflict("Wait until the assistant finishes (or stop it), then undo", code="busy")
    if assistant_app.busy_with_replies(ctx):
        raise Conflict("Сейчас пишется ответ в чате: отменить можно, когда он закончится", code="busy")
    if action["status"] != "done":
        raise Conflict("This action is not in force", code="not_done")
    items = repo.done_since(action["chat_id"], action_id)
    changed: set[str] = set()
    for item in items:  # newest first
        try:
            snapshot.restore(ctx.db, item["undo"])
            for use in item["undo"].get("uses", []):
                ids = [b for b in use["ids"] if ctx.lorebooks.exists(b)]
                ctx.lorebooks.set_uses(use["scope"], use["target"], ids)
            assistant_app.undo_themes(ctx, item["undo"].get("themes", []))
        except Exception as exc:  # noqa: BLE001 - say which one could not be put back
            log.exception("Undo of assistant action %s failed", item["id"])
            raise Conflict(f"Не удалось отменить «{item['what']}»: {exc}", code="undo_failed") from exc
        repo.set_status(item["id"], "undone")
        changed.update(item["undo"].get("changed", []))
    if ctx.db.fetchone("SELECT 1 FROM assistant_chats WHERE id = ?", (action["chat_id"],)) is not None:  # the journal outlives it
        repo.add_message(action["chat_id"], "tool", "", {"undone": [i["what"] for i in reversed(items)]})
    await ctx.events.emit("assistant.undone", action_ids=[i["id"] for i in items])
    ctx.notices.emit("assistant_changed", changed=sorted(changed))
    return [i["id"] for i in items]
