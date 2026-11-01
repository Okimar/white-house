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
| `api.llm(connection_id=, model=, chat_id=, budget=)` | an `LLMClient` (`generate`, `stream`, `generate_json(schema=)`) |
| `api.add_generator(gen, name)` | a reply writer; the user picks it per chat in 👥 «Ответ пишет». `gen.id`, `async def generate(job)` yielding text; reuse `job.build_messages()`, `job.llm`, `job.run.trace(kind, ...)`, `job.think(text)`, `job.meta` (kept with the version), `job.message_id`; see the `agentic-plugin` skill |
| `api.notify(name, **data)` | to the web part on every open page: `t.onNotice(name, fn)` |
| `api.on_unload(fn)`, `api.log`, `api.ctx`, `api.folder` | cleanup, logger, the whole app (services may be imported directly: `tavern.services.cast` …) |

**Model access is the user's.** Every request through `api.llm()` or `job.llm` of the plugin's
generator passes the gate: off by default (`PluginRefused`, code `plugin_no_models`), the plugin's
limits per day / month / reply (`plugin_limit`), optionally a question to the user
(`plugin_denied`). Do not work around it; handle the refusal (it is an `AppError` with a message
for the user). Spending is recorded as `purpose = "plugin:<id>"`.

Errors for the user: raise `tavern.errors.AppError` subclasses with a Russian message. Any other
exception in a command or action becomes «Дополнение … ошиблось в …» (500) and the plugin stays on.

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
Also `t.settings`, `t.chatId()`, `t.toast(text)`, `t.reloadChat()`, `t.url(path)`, `t.signal`.
The page CSP allows scripts and requests only to this server: go to the internet from the server
part (an action), not from the browser. Message objects: `id`, `role`, `content`, `active_swipe`,
`name`, `character_id`, `swipe_meta`, `extra`. Restyle through `style.css` and the `data-plugin`
attribute on your buttons and lines (slots in the make-theme skill).

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
