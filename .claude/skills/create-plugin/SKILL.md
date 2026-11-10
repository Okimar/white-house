---
name: create-plugin
description: How to write a Tavern plugin - plugin.json, the server part (setup(api): commands, actions, events, storage, service messages, model calls under the user's access rules, reply writers), the web part (buttons on messages and the composer, lines under messages, events), tests and installing. Use when asked to make a plugin or an extension for Tavern, or to change a built-in one.
---

# Creating a plugin

Decisions behind this: `docs/adr/0009-plugins.md`. Examples: `tavern/builtin_plugins/dice`
(command, message, composer button), `tavern/builtin_plugins/thoughts` (reply writer, trees,
panel, list settings) and `tavern/builtin_plugins/translate` (actions, storage,
events, message button and footer, notify, network with a swappable `transport` for tests).

## Folder

```
data/plugins/<id>/          (built-in ones: tavern/builtin_plugins/<id>/)
  plugin.json
  server.py                 optional: def setup(api)  — the file acts as a package (from .x import y)
  web.js                    optional: export default function setup(t)
  style.css                 optional: added to the page while the plugin is on
```

`plugin.json`:
```json
{"id": "dice", "name": "🎲 Кубики", "version": "1.0.0", "author": "...", "description": "...",
 "server": "server.py", "web": "web.js",
 "uses": ["commands", "messages", "storage", "models", "generator", "events", "ui", "network"],
 "migrations": "migrations",
 "settings": [{"key": "to", "label": "Язык", "type": "choice", "default": "ru", "choices": [["ru", "Русский"]]},
              {"key": "auto", "label": "Сразу", "type": "bool", "default": false}]}
```
`id` = folder name (`a-z0-9_-`). `uses` is what the user sees before enabling: list honestly what
the plugin does. Setting types: bool, number (min/max), text (`"multiline": true` for a box), choice,
connection (a user's connection, "" = as in the chat), list (rows of `fields`). Files allowed: see
`EXTENSIONS` in `tavern/plugins/manifest.py`.

## Server part: `setup(api)` (tavern/plugins/api.py)

Plain function, not async; register things and return. Anything raised leaves the plugin off with
the error shown. Everything registered is removed when the plugin is switched off.

| Call | What |
|---|---|
| `api.settings` | current values of the plugin.json settings |
| `api.command(name, fn, description)` | "/name args" in the chat → `fn(chat_id, args)` → short text (or None) |
| `api.action(name, fn)` | the web part calls `t.call(name, payload)` → `fn(payload)` (sync or async) → JSON |
| `api.on(event, fn, priority=)` | core events: `message.received` (message, chat), `chat.members_changed`, `prompt.built` (filter: messages, job), `chat.before_generate` (job), `generation.failed` … |
| `api.storage(chat_id="")` | `.get/.set/.delete/.all` — JSON values, per chat (gone with the chat) or app-wide |
| `api.add_message(chat_id, text, role="system", name=, hidden=False, to_model=True, extra=)` | a message at the end of the chat, owner = the plugin |
| `api.llm(connection_id=, model=, chat_id=, budget=, background=, on_wait=)` | an `LLMClient` (`generate`, `stream`, `generate_json(schema=)`); `background=True` for a job: «спрашивать каждый раз» asks once per client, no per-reply limit, with no page open it waits (`on_wait(True/False)`) |
| `api.add_generator(gen, name)` | a reply writer; the user picks it per chat in 👥 «Ответ пишет». `gen.id`, `async def generate(job)` yielding text; reuse `job.build_messages()`, `job.llm`, `job.run.trace(kind, ...)`, `job.think(text)`, `job.meta` (kept with the version), `job.message_id`; see the `agentic-plugin` skill |
| `api.notify(name, **data)` | to the web part on every open page: `t.onNotice(name, fn)` |
| `api.job(key, work, delay=2.0)` | a background job: `async def work(job)`; each small step through `await job.step(factory)` (a user's request cancels it at once and it is redone later: a step must be safe to redo), `job.progress(done, total, note)`, `await job.rate_wait(seconds)` after a 429 (capped), `job.stopped`. One job per key: scheduling again only asks for one more pass. Cancelled when the plugin is switched off. Make the job todo-driven (each pass asks the tables "what is next?") |
| `api.job_status(key)` | `{running, queued, paused, done, total, note, error}` |
| `api.embed(texts, chat_id=)` | vectors on the global embedding connection (one request), metered as the plugin, held to its money limits (not to «may use models»); `AppError` `no_embedding_connection` when none is chosen; a 429 is a `ProviderError` with `upstream_status` 429 |
| `api.vectors_model()` | the embedding model now chosen ('' if none): key stored vectors by it |
| `api.on_unload(fn)`, `api.log`, `api.ctx`, `api.folder` | cleanup, logger, the whole app (services may be imported directly: `tavern.services.cast` …) |

**Model access is the user's.** Every request through `api.llm()` or `job.llm` of the plugin's
generator passes the gate: off by default (`PluginRefused`, code `plugin_no_models`), the plugin's
limits per day / month / reply (`plugin_limit`), optionally a question to the user
(`plugin_denied`). Do not work around it; handle the refusal (it is an `AppError` with a message
for the user). Spending is recorded as `purpose = "plugin:<id>"`.

Errors for the user: raise `tavern.errors.AppError` subclasses with a Russian message. Any other
exception in a command or action becomes «Дополнение … ошиблось в …» (500) and the plugin stays on.

## Events a plugin can hear (stage M1)

Actions (`api.on(name, fn)`; `fn(**payload)`): `message.received` (message, chat), `message.added` (message, chat_id),
`message.edited` (message, chat_id, before), `message.excluded` (message, chat_id, excluded), `message.deleted`
(message, chat_id, children), `message.swipe_selected` (message, chat_id, before_idx), `chat.branch_selected`
(chat_id, leaf_id, before_leaf), `chat.created` (chat_id), `chat.before_generate` (job). They fire from the page's
routes, from the assistant's `app_call` and from the chat service. Use them to WAKE work; never rely on them for
correctness (check content by hash too). Filter: `meaning.wanted` (`value=False, chat=`): return True to make the
reply's ONE embedding request happen even for a chat without lorebooks.

## Tables of its own (stage M1)

`plugin.json` `"migrations": "migrations"`: a folder of `NNNN_name.sql` (contiguous from 0001, one transaction each,
never edit a shipped file, no BEGIN/COMMIT). Applied before `setup(api)`. EVERY table, index, trigger and view a file
creates must be named `p_<id>_...` (`-` in the id becomes `_`), and nothing else in the database may change; a stray
name rolls the file back and leaves only that plugin off with the error. Tables may `REFERENCES chats(id) / messages(id)
ON DELETE CASCADE`. Name vector tables `p_<id>_vectors`: they are never journaled for the assistant's undo and never
captured in snapshots. Use `api.ctx.db` (`fetchone / fetchall / transaction`). «Удалить вместе с данными» drops the
`p_<id>_*` objects; the doctor warns when `schema_version` is not the folder's. `.sql` files are plugin files: part of
the approval hash, never served to the browser. Example: `tavern/builtin_plugins/memory`.

## Pieces of the prompt (stage M1)

In `chat.before_generate` add `core.assembly.Extra`s to `job.prompt_input.extras`: `text`, `source` (archive key,
`plugin:<id>:...`), `label`, `place` ("memory" | "after_card" | "card_before" | "card_after" | "depth" | "an_top"),
`owner` (character id), `drop_rank`. History first: an Extra with `covers=(first_id, last_id)` or `if_dropped=True` is
sent only when those history messages were really left out of the window, and only within `prompt_input.memory_reserve`
tokens. Conditional extras are CONSIDERED in list order (rank them: the best first gets the reserve) and PLACED
by `order` (story position) when given; extras with the same `group` are alternatives (only the first that qualifies
and fits goes; a group none of which fits is named once as dropped). Set `prompt_input.tail_from` (the live tail is not cut before older messages), `prompt_input.values["summary"]`
for `{{summary}}`. `job.meaning` is the reply's meaning request (`.queries` vectors, `.texts`, `.model`, `.error`).

## Web part: `setup(t)` (web/core/plugins.js)

```js
export default function setup(t) {
  t.messageButton({ icon: "🌐", title: (m) => "Перевести", onClick: async (m) => { ... } });
  t.messageFooter((m) => (text for under the message) || null);   // plain text, never HTML
  t.composerButton({ icon: "🎲", title: "…", onClick: () => t.command("/roll 2d6") });
  t.messageTree((m) => tree || null);   // folded tree under a message: {title, open, items: [{title, text, state, children}]}
  const panel = t.panel({ icon: "🌳", title: "…", render: () => tree || null });   // side panel; panel.open()
  t.on("trace", (e) => ...);            // progress of its reply writer: {kind, chatId, ...}
  t.on("chat", (d) => ...);          // {chatId, characterId, characterName}; also "generation" {running}, "message", "send"
  t.onNotice("translated", (data) => ...);
  const result = await t.call("translate", { message_id: m.id });
  t.refresh();                        // redraw plugin places after your state changed
  return () => { /* undo anything outside the places above */ };
}
```
Also `t.settings`, `t.chatId()`, `t.toast(text)`, `t.reloadChat()`, `t.url(path)`, `t.signal`, and
`t.showMessage({id, depth})` (M5): bring a message of the open branch into view and mark it for a moment, as a
search find does (`depth` = the number the chat shows − 1; close your window first on a phone).
The page CSP allows scripts and requests only to this server: go to the internet from the server
part (an action), not from the browser. Message objects: `id`, `role`, `content`, `active_swipe`,
`name`, `character_id`, `swipe_meta`, `extra`. Restyle through `style.css` and the `data-plugin`
attribute on your buttons and lines (slots in the make-theme skill).

## Skills for the assistant

A plugin can teach the 🪄 assistant a kind of work: put English Markdown files in `skills/` of the
plugin folder, each with a header (`name`, `title`, `description` = when to use it, `audience:
assistant`) and optional parts `## Plan`, `## Write`, `## Check` (lines `- …` there are extra checks
for the critic). They are on while the plugin is active and show in Settings → «Инструкции» under
«Из дополнений». See `tavern/services/skills.py` for the format.

## Tests

- Pure logic: load the module by path (see `load_builtin` in `tests/unit/test_plugins.py`).
- Through the API: write the folder into `ctx.paths.plugins`, then
  `POST /api/plugins/<id>/enable {"approve": true}` (`write_plugin` in
  `tests/integration/test_plugins_api.py`). Network: give the module a `transport` attribute and set
  `httpx.MockTransport` in the test; models: the `fake_llm` fixture plus
  `PUT /api/plugins/<id>/access {"models": true}`.
- Browser: `python -m tavern --sandbox` (switch the active connection to the fake LLM first),
  ⚙ → «Дополнения», turn it on, try it; `/?safe` must show the app without it.

## Sharing

Zip the folder (or the folder inside) → «Установить из файла»; or push to GitHub → «Установить по
ссылке» (`https://github.com/me/repo`, `/tree/<branch>/<folder>` for a plugin inside a repository).
Every update needs the user's approval again.

## Windows, dividers, menu items, chips (stage M2)

```js
const win = t.window({ icon: "🧠", title: "Память", button: false, render: (ui) => ui.html`...` });  // win.open() / win.close()
t.messageDivider((m) => ({ label, title, state, onClick }) || null);   // a thin line ABOVE a message, plain text
t.messageMenu({ label, icon, show: (m) => bool, onClick: (m) => ... }); // an item in a message's «⋯» menu
t.status(() => ({ text, title, state: "wait" | "error" | "", onClick }) || null);   // a chip in the top bar
t.on("job", (e) => ...);   // {owner, key, running, queued, paused, done, total, note, error} of background jobs
```
`render(ui)` runs as a component (hooks work): `ui = {html, useState, useEffect, useRef, Select, Icon, IconButton, ask,
askText, toast, close}` are the page's own parts, so do not bring your own. The window is a full-height sheet on a
phone (✕ always visible); buttons 44px, text 14px+, no native `<select>`. Call `t.refresh()` after your state changed.
Example: `tavern/builtin_plugins/memory/web.js`.

## Composer helper (2026-11-07)

`t.composerHelper({onText(text, caret), view()})` draws a strip above the chat message box; `t.composer.text()/.caret()/.replace(edits, caret?)` read and edit the box. Only the chat composer is bridged, not the assistant's box. Example: tavern/builtin_plugins/spelling.
