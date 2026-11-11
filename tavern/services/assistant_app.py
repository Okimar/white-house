"""Everything the page can do, for the assistant (stage 8б-2).

The owner's wish: the assistant can do whatever a person can press on the page. Instead of a tool per
button, `app_call` makes the same HTTP requests the page makes, in process, with:
- a catalog (`app_routes`): every route with its docstring or a hint below;
- closed routes: API keys, installing / enabling plugins, approving theme scripts, the assistant
  itself, updating the app, live streams; theme files and pictures go through their own tools (files, not rows);
- "Отменить" for any change: while the request runs, storage/changelog.Recorder writes down every
  row it changes. Changes wait until no reply is being written anywhere (the recorder sees the
  whole connection) — up to QUIET_WAIT seconds;
- deleting asks the user first (DELETE, and the routes that delete by numbers).

Also here: `read_chat` (the open chat by numbers, as many messages as the settings say, with
reasoning and flags), themes (a new theme from the current one, switched on at once; edit files of
the user's own themes), and `show` (open a chat or a character on the page).
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

import httpx

from tavern.errors import NotFound
from tavern.services import assistant_screenshots, assistant_tools
from tavern.services.assistant_tools import Env, Outcome, Tool, ToolError
from tavern.storage import snapshot
from tavern.storage.changelog import Recorder

QUIET_WAIT = 180.0  # seconds a change waits for replies being written elsewhere
MAX_THEME_TEXT = 200_000
THEME_TEXT_FILES = (".css", ".js", ".json", ".svg", ".txt", ".md")
SECRET_WORDS = re.compile(r"^(api_?key|key|keys|secret|token|password|proxy|proxies|access_token)$", re.I)

ALWAYS_CLOSED = re.compile(r"^/(assistant|events|runs/|plugins/asks|updates)")
CLOSED_WRITES = (
    (re.compile(r"^/connections/[^/]+/keys"), "API keys are closed to the assistant"),
    (re.compile(r"^/backups/gdrive"), "the Google Drive sign-in and app pass are closed to the assistant"),
    (re.compile(r"^/backups/[^/]+/restore$"),
     "restoring a copy replaces all the data: closed to the assistant, the user does it on the «Копии» tab"),
    (re.compile(r"^/plugins(?!/command$|/[^/]+/actions/)"), "installing and switching plugins is closed to the assistant"),
    (re.compile(r"^/themes"), "use the theme tools (make_theme, write_theme_file, set_theme)"),
    (re.compile(r"^/assets"), "pictures are files: the assistant cannot add or delete them"),
    (re.compile(r"^/regex/approve"),
     "allowing the rules that came with a card or preset is the user's own decision (they change what the model gets)"),
    (re.compile(r"^/regex/clear"), "удалить все общие правила может только человек"),
    (re.compile(r"^/skills"), "the assistant reads skills (read_skill) but never changes its own instructions"),
)
ASK_FIRST = re.compile(r"/(delete-by-numbers)$")
PLUGIN_ACTION = re.compile(r"^/plugins/([^/]+)/actions/([^/]+)$")
# POST routes that only look (they change no rows): they do not wait for replies being written.
READ_POSTS = ("/regex/try", "/regex/display")

# Routes without a docstring of their own: what they take. Everything else: read with GET first
# and send the same field names.
HINTS = {
    "GET /characters": "all characters (id, name)",
    "PUT /characters/{id}": "the whole card data (name, description, personality, scenario, first_mes, "
                            "mes_example, ...); update_character is simpler",
    "DELETE /characters/{id}": "delete a character with its bubbles",
    "GET /chats": "?character_id=&limit= — chats, newest first",
    "POST /chats": "{character_id?} — a new chat (its greeting included)",
    "GET /chats/{id}": "the chat: title, meta (connection_id, preset_id, generator), members, speakers",
    "PUT /chats/{id}": "{title} — rename",
    "DELETE /chats/{id}": "delete the chat",
    "GET /chats/{id}/messages": "?limit=&before_depth= — prefer read_chat",
    "GET /chats/{id}/history": "the chat's message tree (branches)",
    "GET /chats/{id}/search": "?q= — messages containing the words",
    "POST /chats/{id}/branch": "{leaf_id, exact?} — switch to the branch through that message; exact: stop at it, "
                               "so the next message starts a new branch there (the old one stays)",
    "GET /messages/{id}/branches": "the other continuations after this message",
    "PUT /messages/{id}/swipe": "{index} — show another version of the message",
    "DELETE /messages/{id}": "delete only this message (all its versions); the later ones stay and move up",
    "GET /messages/{id}/prompt": "what the model saw before writing this message (the 👁 window): every "
                                 "part of the prompt with where it came from (card, bubbles, lorebook, notes)",
    "POST /chats/{id}/impersonate": "the model writes the user's next message",
    "POST /chats/{id}/regenerate": "another version of the last reply",
    "POST /chats/{id}/stop": "stop the reply being written",
    "GET /chats/{id}/members": "the chat's characters: depth, importance, is_me, model_may_write, "
                               "talkativeness, muted, blurb",
    "POST /chats/{id}/members": "{character_id, ...the same fields} — add a character to the chat",
    "PUT /chats/{id}/members/{character_id}": "{depth?, importance?, is_me?, model_may_write?, talkativeness?, "
                                              "muted?, blurb?}",
    "DELETE /chats/{id}/members/{character_id}": "take a character out of the chat",
    "GET /settings": "the app settings",
    "PUT /settings": "{key: value, ...} — user_name, active_connection, active_preset, default_me, "
                     "notes_display, retrieval, lorebooks, ...",
    "GET /connections": "connections (no keys): name, base_url, model, params",
    "PUT /connections/{id}": "{name?, model?, params?} — keys are not here. params are ALL the model settings (temperature, top_p, max_tokens, reasoning_effort, max_context_tokens ...): they live only here, not in presets; send the whole object",
    "GET /presets": "prompt presets (the built-in one cannot be changed: copy it with POST /presets {name, from})",
    "GET /presets/{id}": "a preset: its blocks (order, enabled, text) and prompt texts (no model settings: those are on the connection)",
    "PUT /presets/{id}": "{name?, data?} — data as GET returns it",
    "DELETE /presets/{id}": "delete a preset",
    "GET /lorebooks/{id}": "a lorebook", "PUT /lorebooks/{id}": "{name?, description?, settings?}",
    "PUT /blocks/{id}": "change a bubble / lorebook entry (fields as GET .../blocks returns)",
    "DELETE /blocks/{id}": "delete a bubble with its sub-bubbles",
    "POST /notes": "{scope: global|character|chat, owner_id?, title, text, ...} — an author's note",
    "PUT /notes/{id}": "change an author's note (fields as GET /notes returns)",
    "DELETE /notes/{id}": "delete an author's note",
    "PUT /message-notes/{id}": "change a note kept beside a message",
    "DELETE /message-notes/{id}": "delete a note kept beside a message",
    "GET /profiles": "connection profiles", "DELETE /profiles/{id}": "delete a profile",
    "POST /profiles/{id}/apply": "switch to the profile",
    "GET /templates": "instruct templates", "GET /lorebooks": "lorebooks",
    "GET /spending/report": "spending by model, by day, plugins, the assistant",
}


# --- the page's requests, in process --------------------------------------------------------

_clients: dict[int, httpx.AsyncClient] = {}


def _client(env: Env) -> httpx.AsyncClient:
    """An HTTP client straight into this instance's routes (no network, no access middleware)."""
    client = _clients.get(id(env.ctx))
    if client is None:
        from starlette.applications import Starlette
        from starlette.routing import Mount

        from tavern.app import api_routes, handle_app_error, handle_unexpected  # app imports the services
        from tavern.errors import AppError

        app = Starlette(routes=[Mount("/api", routes=api_routes())],
                        exception_handlers={AppError: handle_app_error, Exception: handle_unexpected})
        app.state.ctx = env.ctx
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://tavern")
        _clients[id(env.ctx)] = client
    return client


def _routes() -> list[tuple[str, str, str]]:
    from tavern.app import api_routes

    found = []
    for route in api_routes():
        path = getattr(route, "path", "")
        if not path or path == "/{path:path}" or ALWAYS_CLOSED.match(path):
            continue
        for method in sorted(m for m in (route.methods or ()) if m != "HEAD"):
            if method != "GET" and any(rule.match(path) for rule, _ in CLOSED_WRITES):
                continue
            doc = (route.endpoint.__doc__ or "").strip().split("\n")[0]
            found.append((method, path, HINTS.get(f"{method} {path}", doc)))
    return found


def _secret_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        found = [k for k in value if SECRET_WORDS.match(str(k))]
        return found + [k for v in value.values() for k in _secret_keys(v)]
    if isinstance(value, list):
        return [k for v in value for k in _secret_keys(v)]
    return []


def _check(method: str, path: str, body: Any) -> None:
    if ALWAYS_CLOSED.match(path):
        raise ToolError(f"{path} is closed to the assistant")
    if method == "GET":
        return
    for rule, reason in CLOSED_WRITES:
        if rule.match(path):
            raise ToolError(reason)
    secrets = _secret_keys(body)
    if secrets:
        raise ToolError(f"Keys and passwords are closed to the assistant (fields {secrets})")
    if path == "/settings" and isinstance(body, dict) and "trusted_theme_scripts" in body:
        raise ToolError("Approving theme scripts is the user's own decision")


def busy_with_replies(ctx: Any) -> bool:
    return any(not key.startswith("assistant:") for key in ctx.runs.running())


async def wait_quiet(env: Env) -> None:
    """A change waits until no reply is written anywhere: the recorder would catch those rows too."""
    waited = 0.0
    while busy_with_replies(env.ctx):
        if waited >= QUIET_WAIT:
            raise ToolError("A reply is still being written; try again when it is done")
        await asyncio.sleep(0.2)
        waited += 0.2


def _request_args(args: dict[str, Any]) -> tuple[str, str, dict[str, str], Any]:
    method = str(args.get("method", "GET")).upper()
    if method not in ("GET", "POST", "PUT", "DELETE"):
        raise ToolError("method must be GET, POST, PUT or DELETE")
    path = args.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ToolError("path must start with / (e.g. /chats/abc/members)")
    path = path.removeprefix("/api").split("?", 1)[0] or "/"
    query = args.get("query") or {}
    if not isinstance(query, dict):
        raise ToolError("query must be an object of names and values")
    return method, path, {str(k): str(v) for k, v in query.items()}, args.get("body")


def _action_info(ctx: Any, method: str, path: str) -> dict[str, Any]:
    """What a plugin declared about the action this request calls (api.action: describe, reads, confirm)."""
    found = PLUGIN_ACTION.match(path) if method == "POST" else None
    if found is None or ctx.plugins is None:
        return {}
    return ctx.plugins.action_info.get((found.group(1), found.group(2)), {})


def _ask_app_call(env: Env, args: dict[str, Any]) -> str:
    method, path, _, _ = _request_args(args)
    if method == "DELETE" or ASK_FIRST.search(path):
        return str(args.get("what") or f"{method} {path}")
    confirm = _action_info(env.ctx, method, path).get("confirm")
    if confirm:  # a plugin's action that must be allowed by the user (e.g. making a mini-card)
        return str(args.get("what") or confirm)
    return ""


async def app_call(env: Env, args: dict[str, Any]) -> Outcome:
    method, path, query, body = _request_args(args)
    _check(method, path, body)
    client = _client(env)
    kwargs: dict[str, Any] = {"params": query}
    if body is not None and method != "GET":
        kwargs["json"] = body
    if method == "GET" or path in READ_POSTS or _action_info(env.ctx, method, path).get("reads"):
        response = await client.request(method, "/api" + path, **kwargs)
        return Outcome({"status": response.status_code, "data": _answer(response)})
    await wait_quiet(env)
    with Recorder(env.ctx.db) as rec:
        response = await client.request(method, "/api" + path, **kwargs)
    result = {"status": response.status_code, "data": _answer(response)}
    if not rec.changed:
        return Outcome(result)
    what = str(args.get("what") or f"{method} {path}")[:200]
    return Outcome(result, what, rec.snapshot(), ("all",))


def _answer(response: httpx.Response) -> Any:
    kind = response.headers.get("content-type", "")
    if "json" in kind:
        return response.json()
    if kind.startswith("text/") and len(response.content) < 200_000:
        return response.text
    return f"[{kind or 'binary'}, {len(response.content)} bytes]"


def _plugin_action_lines(ctx: Any) -> list[str]:
    """The actions of switched-on plugins that describe themselves (api.action(..., describe=)), one line each."""
    if ctx.plugins is None:
        return []
    out = []
    for (plugin_id, name), info in sorted(ctx.plugins.action_info.items()):
        if not info.get("describe"):
            continue
        marks = (" [reads only]" if info.get("reads") else "") + (" [asks the user first]" if info.get("confirm") else "")
        out.append(f"POST /plugins/{plugin_id}/actions/{name} — {info['describe']}{marks}")
    return out


async def app_routes(env: Env, args: dict[str, Any]) -> Outcome:
    words = str(args.get("filter") or "").lower().split()
    lines = [f"{m} {p} — {d}" if d else f"{m} {p}" for m, p, d in _routes()]
    lines += _plugin_action_lines(env.ctx)
    if words:
        lines = [line for line in lines if all(w in line.lower() for w in words)]
    return Outcome("\n".join(lines) or "nothing matches")


# --- reading a chat ---------------------------------------------------------------------------


def _chat_id(env: Env, args: dict[str, Any]) -> str:
    chat_id = args.get("chat_id") or env.chat_id
    if not isinstance(chat_id, str) or not chat_id:
        raise ToolError("No chat is open; give chat_id")
    env.ctx.chats.get_chat(chat_id)
    return chat_id


async def read_chat(env: Env, args: dict[str, Any]) -> Outcome:
    from tavern.services.assistant import get_settings  # it imports this module

    ctx = env.ctx
    chat_id = _chat_id(env, args)
    path = ctx.chats.active_path(chat_id)
    total = len(path)
    first, last = args.get("from_number"), args.get("to_number")
    if first is not None or last is not None:
        try:
            low, high = int(first or 1), int(last or total)
        except (TypeError, ValueError):
            raise ToolError("from_number and to_number are message numbers") from None
        chosen = [m for m in path if low <= m["depth"] + 1 <= high]
    else:
        count = args.get("last")
        count = get_settings(ctx)["read_messages"] if count is None else count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ToolError("last must be a number (0 = the whole chat)")
        chosen = path[-count:] if count else path
    lines = []
    for m in chosen:
        flags = [f for f, on in (("hidden from the model", m["exclude_from_prompt"]),
                                 ("service message", m["hidden"])) if on]
        meta = m["swipe_meta"]
        head = f"#{m['depth'] + 1} id={m['id']} {m['role']}" + (f" {m['name']}" if m["name"] else "")
        if m["swipe_count"] > 1:
            head += f" (version {m['active_swipe'] + 1} of {m['swipe_count']})"
        if flags:
            head += " [" + ", ".join(flags) + "]"
        if meta.get("via"):
            head += f" [answered via {meta['via']}]"
        lines.append(head + ":\n" + m["content"])
        if meta.get("reasoning"):
            lines.append(f"  (the model's reasoning for #{m['depth'] + 1}: {meta['reasoning']})")
    shown = f"messages {chosen[0]['depth'] + 1}–{chosen[-1]['depth'] + 1} of {total}" if chosen else f"0 of {total}"
    return Outcome(f"Chat {chat_id}, {shown}:\n\n" + "\n\n".join(lines))


# --- themes -----------------------------------------------------------------------------------


def _active_theme(env: Env) -> str:
    return env.ctx.settings.get("theme") or "default"


async def list_themes(env: Env, args: dict[str, Any]) -> Outcome:
    active = _active_theme(env)
    return Outcome([{"id": t["id"], "name": t["name"], "built_in": t["builtin"], "on": t["id"] == active,
                     "files": [f["path"] for f in env.ctx.themes.files(t["id"])]}
                    for t in env.ctx.themes.list()])


async def read_theme_file(env: Env, args: dict[str, Any]) -> Outcome:
    theme_id = str(args.get("theme_id") or _active_theme(env))
    path = env.ctx.themes.file(theme_id, str(args.get("path") or "theme.css"))
    if path.suffix not in THEME_TEXT_FILES:
        return Outcome(f"[{path.suffix} file, {path.stat().st_size} bytes]")
    return Outcome(path.read_text(encoding="utf-8", errors="replace")[:MAX_THEME_TEXT])


def _settings_snapshot(env: Env, key: str) -> dict[str, Any]:
    snap = snapshot.capture(env.ctx.db, "settings", {"key": key})
    if not snap["rows"]:
        snapshot.created(snap, "settings", {"key": key})
    return snap


async def make_theme(env: Env, args: dict[str, Any]) -> Outcome:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ToolError("A theme needs a name")
    source = str(args.get("from") or _active_theme(env))
    css = args.get("css")
    if css is not None and (not isinstance(css, str) or len(css) > MAX_THEME_TEXT):
        raise ToolError("css must be text (the whole theme.css)")
    undo = _settings_snapshot(env, "theme")
    theme = env.ctx.themes.create(name, source=source)
    undo["themes"] = [{"id": theme["id"], "made": True}]
    if css is not None:
        env.ctx.themes.write_file(theme["id"], "theme.css", css.encode("utf-8"))
    env.ctx.settings.set("theme", theme["id"])
    note = "on now" + ("; its effects script runs only after the user approves it in Темы" if theme.get("script")
                       else "")
    return Outcome({"id": theme["id"], "note": note}, f"Новая тема «{theme['name']}» включена", undo, ("theme",))


async def write_theme_file(env: Env, args: dict[str, Any]) -> Outcome:
    theme_id = str(args.get("theme_id") or _active_theme(env))
    rel = str(args.get("path") or "theme.css")
    content = args.get("content")
    if not isinstance(content, str) or len(content) > MAX_THEME_TEXT:
        raise ToolError("content must be the whole file as text")
    if not rel.endswith(THEME_TEXT_FILES):
        raise ToolError(f"Only text files: {', '.join(THEME_TEXT_FILES)}")
    if env.ctx.themes.get(theme_id)["builtin"]:
        raise ToolError("Built-in themes are not changed: make_theme makes your own copy")
    try:
        old = env.ctx.themes.file(theme_id, rel).read_bytes()
    except NotFound:
        old = None
    theme = env.ctx.themes.write_file(theme_id, rel, content.encode("utf-8"))
    undo = {**snapshot.empty(), "themes": [{"id": theme_id, "path": rel,
                                            "old": base64.b64encode(old).decode("ascii") if old is not None else None}]}
    note = "saved" + ("; a changed effects script runs only after the user approves it in Темы" if rel.endswith(".js")
                      else "")
    return Outcome({"note": note}, f"Тема «{theme['name']}»: изменён {rel}", undo, ("theme",))


async def set_theme(env: Env, args: dict[str, Any]) -> Outcome:
    theme = env.ctx.themes.get(str(args.get("theme_id") or ""))
    undo = _settings_snapshot(env, "theme")
    env.ctx.settings.set("theme", theme["id"])
    return Outcome({"ok": True}, f"Включена тема «{theme['name']}»", undo, ("theme",))


def undo_themes(ctx: Any, items: list[dict[str, Any]]) -> None:
    """The file side of undoing theme tools (the setting comes back with the rows)."""
    for item in items:
        if item.get("made"):
            try:
                ctx.themes.delete(item["id"])  # kept in the theme's history, restorable in Темы
            except NotFound:
                pass
        elif item.get("old") is None:
            try:
                ctx.themes.delete_file(item["id"], item["path"])
            except NotFound:
                pass
        else:
            ctx.themes.write_file(item["id"], item["path"], base64.b64decode(item["old"]))


# --- the page ---------------------------------------------------------------------------------


async def drive_guide(env: Env, args: dict[str, Any]) -> Outcome:
    from tavern.services.gdrive import guide_text

    return Outcome({"guide": guide_text(), "note": "give this text to the user word for word"})


async def show(env: Env, args: dict[str, Any]) -> Outcome:
    if args.get("chat_id"):
        env.ctx.chats.get_chat(str(args["chat_id"]))
        env.ctx.notices.emit("assistant_show", chat_id=str(args["chat_id"]))
    elif args.get("character_id"):
        env.ctx.characters.get(str(args["character_id"]))
        env.ctx.notices.emit("assistant_show", character_id=str(args["character_id"]))
    else:
        raise ToolError("Give chat_id or character_id")
    return Outcome({"ok": True, "note": "opened on the page"})


async def screenshot(env: Env, args: dict[str, Any]) -> Outcome:
    """Ask the open page for a picture of itself; the model sees it with the next results (ADR 0020)."""
    what = str(args.get("what") or "screen").lower()
    try:
        media, data = await assistant_screenshots.request(env.ctx, what)
    except assistant_screenshots.NoPage as exc:
        raise ToolError(str(exc)) from None
    return Outcome({"ok": True, "what": assistant_screenshots.WHAT[what], "bytes": len(data) * 3 // 4,
                    "note": "The picture comes to you as an image with the next message. Look at it and judge by it. "
                            "If you cannot see any picture, say so plainly and tell the user to pick a model that "
                            "reads pictures for the assistant (Помощник → модели); never describe what you did not see."},
                   images=((media, data),))


TOOLS = (
    Tool("screenshot", "Take a picture of the app as the person sees it, to check a theme or a screen by eye. "
         "what: screen (the current screen) | phone (375x667) | tablet (768x1280). Needs the app open in a browser; "
         "the person sees a note each time.", {"what": "screen | phone | tablet (default screen)"}, screenshot),
    Tool("app_routes", "The catalog of everything the page can do: METHOD /path — what it takes.",
         {"filter": "optional words, e.g. 'members' or 'notes'"}, app_routes),
    Tool("app_call", "Do what the page does: one request from the catalog. Changes can be undone by the "
         "user; deleting asks the user first.",
         {"method": "GET | POST | PUT | DELETE", "path": "/chats/<id>/members ...", "query": "optional object",
          "body": "optional JSON", "what": "for changes: one short line in the user's language, "
          "e.g. «Борис: глубина 2» (shown in the journal and in the question before deleting)"},
         app_call, _ask_app_call, ("method", "path")),
    Tool("read_chat", "Messages of a chat by number with role, name, flags and the model's reasoning. "
         "Default: the open chat, the last N from the settings.",
         {"chat_id": "optional", "last": "how many from the end (0 = all)",
          "from_number": "optional first number", "to_number": "optional last number"}, read_chat),
    Tool("list_themes", "Themes: id, name, built in, which is on, files.", {}, list_themes),
    Tool("read_theme_file", "A theme file as text (default: theme.css of the theme that is on).",
         {"theme_id": "optional", "path": "theme.css | theme.json | script.js ..."}, read_theme_file),
    Tool("make_theme", "Make a new theme from the one that is on (or `from`) and switch it on. CSS variables: "
         "--bg --panel --fg --muted --line --accent --accent-fg --danger --user --bot --radius --shadow, "
         "places: [data-slot=...] (message, composer, sidebar, topbar, ...).",
         {"name": "theme name", "css": "optional: the whole new theme.css", "from": "optional theme id"},
         make_theme, required=("name",)),
    Tool("write_theme_file", "Replace a text file of the user's own theme (built-in ones: make_theme first).",
         {"theme_id": "optional, default the one that is on", "path": "theme.css ...", "content": "whole file"},
         write_theme_file, required=("content",)),
    Tool("set_theme", "Switch to another theme.", {"theme_id": "theme id"}, set_theme, required=("theme_id",)),
    Tool("drive_guide", "How to connect copies on Google Drive (the app pass, Client ID, Google Cloud). "
         "Give its text to the user as is.", {}, drive_guide),
    Tool("show", "Open a chat or a character's card on the page so the user sees the result.",
         {"chat_id": "or", "character_id": "one of the two"}, show),
)

assistant_tools.TOOLS.update({t.name: t for t in TOOLS})  # the step loop sees one list
