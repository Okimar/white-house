# 0009. Plugins: what they are, how they are switched on, what they may spend

Status: accepted (stage 8а, 2026-10-31). Builds on 0001 (stack), 0002 (themes: approval of scripts),
0007 (providers, spending).

## Context

Stage 8 brings plugins, an assistant over the chat (8б) and an agentic "tree of thoughts" (8в).
Plugins come first, because the other two are built from the same parts. Since stage 1 the core
had places for them: a replaceable reply writer (`Generator`), events with an owner, service
messages (`hidden`, `exclude_from_prompt`, `owner`), `trace` events, `RunBudget`, structured output.

The owner's decisions (2026-10-31):
- A plugin is a folder; it can be put in `data/plugins/`, installed from a file, or from a GitHub
  link with "check for updates" (like SillyTavern).
- A new plugin is **off**. Turning on one that is not built in shows what it says about itself and
  a warning that it gets full access, keys included. Built-ins are turned on without questions.
- Model use, after the owner asked "why should they spend my limits?": **only with permission**
  (off by default), **the plugin's own limits** (per day / month, requests per reply), **its own
  line on the «Расход» tab**, and **"ask me each time"** as an option. All of it in the settings.
- Examples: dice, and translation through **Google Translate, not a model**.

## Decision

- **Layout.** `plugin.json` (id = folder name, name, version, author, description, `server` Python
  file with `setup(api)`, `web` ES module with `export default setup(t)`, `style` CSS, `uses`
  (what it claims to do, shown before enabling), `settings` fields: bool / number / text / choice).
  Built-ins in `tavern/builtin_plugins/`, user ones in `data/plugins/`; a user folder never shadows
  a built-in id. Allowed file types are listed in `plugins/manifest.py`; the browser is never served
  the Python part.
- **Approval by hash.** Turning on stores the sha256 of all files (names and contents). Different
  files later (an update, a hand edit) switch the plugin off at the next start until approved
  again. An update or a re-install also switches it off.
- **Loading.** The server part is imported in-process as a package (relative imports work);
  `setup` must be a plain function. Anything it raises leaves the plugin off with the error beside
  it. Switching off removes every handler, command, action and reply writer it registered
  (`owner` = plugin id), runs its `on_unload`, and drops its modules. `--no-plugins` starts with no
  server parts; `/?safe` also skips web parts.
- **What a plugin gets** (`PluginAPI`): events (`on`), a reply writer chosen per chat in 👥
  (`add_generator`), chat commands (`command` → "/name args"), actions its web part calls
  (`action` → `POST /api/plugins/{id}/actions/{name}`), its own storage per chat
  (`plugin_data`, deleted with the chat, optionally with the plugin), service messages
  (`add_message`), model clients (`llm`), and `notify` to its web part on every open page.
  The web part gets places in the chat: message buttons, lines under a message, buttons beside
  the message box, plus the theme events (`chat`, `generation`, …).
- **Model access** is a `gate` on `LLMClient`, awaited before every request of a plugin's client
  (and of a reply written by a plugin's generator): permission, then the plugin's per-reply count,
  then its day / month spending (rows with `purpose = "plugin:<id>"`, in the shown currency), then
  the question to the user. The question goes over a new app-wide event stream (`GET /api/events`,
  `core/notices.py`); with no page open, or no answer in 2 minutes, the answer is no. A refused
  request raises `PluginRefused` (409) and the run ends with that message.
- **Spending** keeps one table: a plugin's requests are rows with its purpose; the «Расход» report
  and each plugin card show its sums.
- **Install from GitHub** downloads the archive of an exact commit from codeload.github.com (no git
  needed on Termux); the commit and branch are remembered for "check for updates". The plugin may
  sit in a folder of the repository (`/tree/<branch>/<folder>`).

## Honest limit

The model-access rules hold for every plugin that uses the API it is given. A malicious plugin's
server part runs with Tavern's rights and could call a provider on its own or read `secrets.json`;
there is no reliable sandbox for in-process Python, least of all on Termux. The defence against such
a plugin is the warning before enabling, the hash approval, and installing only from trusted
places. This is stated in the warning the user reads.

## Consequences

- Plugin authors: `.claude/skills/create-plugin/SKILL.md`. The assistant (8б) and the agentic
  example (8в) are built as plugins on this API.
- New core events or services become available to plugins without changes here; anything a
  plugin registers must carry its owner so switching off can remove it.
- Stage M1 of ADR 0019 (2026-11-09) adds general points on top of this API, all of them owned by the plugin and removed
  when it is switched off: background jobs (`api.job`, `api.job_status`; core/background.py), tables of its own
  (`plugin.json` "migrations", names `p_<id>_*`; plugins/schema.py), chat events from services/messages.py, pieces of
  the prompt (`Extra`; core/assembly.py), the reply's shared meaning request (`job.meaning`, filter `meaning.wanted`),
  embeddings (`api.embed`, `api.vectors_model`; held to the plugin's money limits, not to the «may use models»
  permission). `.sql` files are plugin files (approved with the hash, never served to the browser).
