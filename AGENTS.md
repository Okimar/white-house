# AGENTS.md

Guide for AI agents (and humans) working on this repository. Read this first, then the skill that
matches your task.

## What this is

White House (formerly Tavern; the package is still `tavern`): a roleplay chat frontend with SillyTavern's feature set, written from scratch (no
SillyTavern code: it is AGPL). Compatible with SillyTavern data formats. Python backend
(Starlette), browser UI without a build step. Must run on Termux (Android).

Plan and stage status: [docs/ROADMAP.md](docs/ROADMAP.md). Current stage: **0–5 done; 6а (connections, keys with proxies, Gemini) 6б (Claude, thinking, pictures, web search) 6в (instruct templates, text mode) 6г (profiles, fallbacks) 6д (parallel requests), 6е (spending) and 6ж (structured output) done; 7а (several characters in any chat: who speaks, what the model knows about the others) and 7б (turns of several replies: «пусть поговорят», everyone at once, narrator) done; 8а (plugins: approval, install from a file / GitHub, model access with their own limits, dice and Google translation) done; 8б (assistant over the chat: window, conversations, steps by form with any model, everything the page can do through one generic tool with undo of any change, themes, reading chats and what the model saw) done; 8в (tree of thoughts: thinkers for every character + own list, rounds, live tree under the reply or in a side panel) done; 9а (whole-data copies on a schedule and before risky actions, live restore, plugin versions, the doctor) and 9б (copies on Google Drive: own OAuth client, sign-in, auto upload with resume, restore from Drive) done; 10а (the assistant's engine for weak models: questions, plan, items with the task pinned, a checklist critic, a model per role, live test stand) and 10б (skills inside the app: «Инструкции» tab, parts per role, checks for the critic) and 10в (layers in bubbles: a found bubble brings only its «Всегда» sub-bubbles; the doctor gives bare «По теме» sub-bubbles keywords by the model) and 10г (the «Лорбуки» skill: core of the world, «Если … →» lines, banned clichés, names; try_phrase tests keywords like a reply) done; 10д (the «Персонажи» skill: the core in the description, bubbles per person/place/item, greetings; a second round of questions for skills with «## More questions») done; 10е-1 (preset groups «один из», the «Размышление» block, `<thinking>` cut out of replies, show / send / repeats dropped) done; then a batch from the owner's list of 2026-11-06 (look kept across updates and safe mode, update from the page, change log, groups, search across chats, regex rules, assistant files / internet / skill writing, spelling plugin, menu layout) written and covered by tests but almost none of it seen on screen: [docs/STATUS.md](docs/STATUS.md), section 2026-11-07**. An inventory of what really works, with what it found and fixed: [docs/STATUS.md](docs/STATUS.md) (2026-11-04). The interface was then brought to order: [docs/UI_AUDIT.md](docs/UI_AUDIT.md) (2026-11-05).

## How a session starts

1. Read this file, [docs/STATUS.md](docs/STATUS.md) and the section of [docs/ROADMAP.md](docs/ROADMAP.md) for the task.
2. **Write a plan first** and show it to the owner (Russian). No edits before that.
3. **Decide about sub-agents** and say it in the plan: what they would do and why, or why not. Read-only sweeps and separate files are fine; shared files stay with one agent.
4. Only then work. Commit only when the owner asks.

## Commands

| Task | Command |
|---|---|
| Install (Linux/macOS/Termux) | `bash install.sh` |
| Run | `./start.sh` or `start.bat` or `python -m tavern` |
| Run on a throwaway copy of the data | `python -m tavern --sandbox` |
| All tests | `python -m pytest` |
| Fast tests only | `python -m pytest tests/unit` |
| Health check of a data dir | `python -m tavern check` |
| Copies of the whole data folder | `python -m tavern backup create|list|restore NAME` |
| Check / repair the data folder | `python -m tavern doctor [--fix]` |
| Fake LLM for manual testing | `python -m tavern.devtools.fake_llm --port 5001` |
| Live run of an assistant scenario (real model, sandbox copy, report in `.bench/`) | `python -m tavern.devtools.assistant_bench lorebook [--roles planner=MODEL,...]` |
| Fake Google Drive for manual testing | `python -m tavern.devtools.fake_gdrive --port 5002`, then run with `TAVERN_GDRIVE_URL=http://127.0.0.1:5002` |

Use the virtualenv interpreter: `.venv/bin/python` (Windows: `.venv\Scripts\python.exe`).

## Map

```
tavern/                 Python package (backend)
  cli.py                entry point, subcommands; `serve` is the default (`--no-plugins`)
  app.py                Starlette app factory, API routes, error handlers, page CSP
  context.py            AppContext: config + db + secrets for one running instance
  config.py             data/config.toml -> frozen dataclasses, strict validation
  security.py           access control middleware (loopback / token / Host / Origin)
  secret_store.py       data/secrets.json: access token, API keys (never sent to the browser)
  sandbox.py            --sandbox: temp copy of the data dir
  errors.py             TavernError (user-facing) / AppError (HTTP JSON errors)
  logs.py               stderr + rotating data/logs/tavern.log
  paths.py              every filesystem path is derived here (the own backup folder is the one exception)
  storage/db.py         SQLite wrapper, transactions, migration runner
  storage/migrations/   NNNN_name.sql, applied in order, one transaction each
  storage/backup.py     database snapshots (before migrations) and whole-data archives (zip: db + assets,
                        themes, plugins; never secrets), fingerprint, safe extract
  services/backups.py   copies: settings, kinds and keep counts, own folder, scheduler thread, before(reason)
                        for risky routes (api/util.copy_before), live restore with rollback; ADR 0012
  services/gdrive.py    copies on Google Drive (9б): the user's own OAuth client, sign-in (PKCE, loopback
                        redirect or a pasted address), worker sending every new copy (resumable, retried),
                        list / download / restore; secrets under `gdrive`, state in data/gdrive.json
  services/doctor.py    checks (quick at startup, full) and safe fixes after a copy; fix_all adds the
                        model's fix for «По теме» sub-bubbles without keywords (10в); api/backups.py
  storage/themes.py     theme folders (built-in web/themes, user data/themes): validate, import/export
                        as one .tavern-theme zip, version history (.versions/) for undo
  storage/assets.py     image library in data/assets (type decided by content, never overwrites)
  storage/prompt_log.py "what the model saw": each text stored once (hash, zlib), records are deltas,
                        old versions stay exact; read-only version history per source
  util/                 atomic file writes, data-dir lock
  devtools/fake_llm.py  scriptable OpenAI-compatible fake server for tests
  devtools/assistant_bench.py  live test stand: scenarios in devtools/bench/*.json run against the real model in a
                        sandbox copy; report.md with the plan, critic, timings, spending per role and every made book
  devtools/fake_gdrive.py  fake Google sign-in + Drive (tests: transport(); manual: --port 5002 and
                        TAVERN_GDRIVE_URL=http://127.0.0.1:5002)
  api/                  HTTP routes (chats + SSE, characters + settings, connections, themes,
                        assets); static.py serves /themes/{id}/{path} and /assets/{name}; thin, no logic
  services/chat.py      use cases: create chat, send, continue, impersonate, regenerate, stop
  services/cast.py      chat members (depth, importance, "me", may the model write for me); used by
                        UI, agents and the assistant alike; fires chat.members_changed
  services/speakers.py  who speaks next in a chat (many characters is the normal state, not a mode):
                        by name / talkativeness, in turns, the model decides, manual; muted
                        members; what the model knows about the others; messages.character_id =
                        who wrote a reply. Agents add characters via cast.add_member + speaker=.
                        Turns of several replies live in services/chat.py (_start_turn, _round,
                        _narrate): «пусть поговорят», everyone at once, the narrator
  services/presets.py   prompt presets: built-in default, user presets, per-chat choice, import/export
                        (our full .tavern-preset.json or SillyTavern)
  services/character_tree.py  the block tree of an owner (a card or a lorebook): edit (fires
                        character.blocks_changed / lorebook.blocks_changed), finer `rules` checked,
                        keyword suggestions, meaning vectors, choosing blocks for a reply,
                        ST character_book <-> blocks on import/export
  services/lorebooks.py lorebooks: books, where they are on (global / chat / character), choosing
                        entries for a reply (pool, budget, chains), ST world files in and out
  storage/repos/lorebooks.py  lorebooks, lorebook_uses
  formats/st_lorebook.py  SillyTavern world entry <-> bubble fields (rules, places, filters)
  services/notes.py     author's notes (global / character / chat): which fire for a reply; kinds once
                        (not kept), hidden (kept, out of sight), visible (kept, beside the message);
                        edits by message numbers; fires notes.changed and message_notes.changed
  storage/repos/notes.py  author_notes, message_notes
  services/history.py   character history: every change of a card or its bubbles is a step (what it
                        was before, packed), unlimited undo, versions; storage/repos/history.py
  core/retrieval.py     which blocks go: blue always (or every N replies), green by keywords (Russian
                        endings) / meaning / sticky with a chance, links between bubbles, free chains
                        never, groups with paths, token budget; finer rules (second keywords, whole
                        words, pause, delay, one-per-group, filter), chains, lorebook pools by order
  storage/repos/blocks.py  character_blocks, block_embeddings, chat_block_hits
  core/presets.py       preset JSON shape and validation (block kinds, model params, groups «один из»,
                        behaviour blocks = kind text + `when`: no_write_for_me, speaker_reply, impersonate, narrator,
                        sent only while that behaviour is on; older presets' `prompts` texts migrate into them;
                        the «Размышление» block, how many past thinkings the history sends)
  core/thinking.py      `<thinking>`/`<think>` written in a reply: split out while streaming (LLMClient.stream
                        split_thinking=True, set for chat replies) into the reply's reasoning; old thinking
                        sent back without repeated lines; the frame for the «Размышление» block
  core/assembly.py      builds the prompt from a preset + members + history, with budget trimming;
                        keeps every part (entry) for the archive
  core/                 events (EventBus), budget (RunBudget), prompt, llm (LLMClient), generation
                        (Generator strategies), runs (background Run + SSE replay), notices (app-wide
                        events for every open page: GET /api/events)
  plugins/              stage 8а: manifest (plugin.json, files, approval hash, zips), install (folder
                        writes, GitHub), api (PluginAPI given to setup(api)), manager (find, approve,
                        load/unload, commands, actions, the model-access gate, «may it?» questions);
                        api/plugins.py; storage/repos/plugins.py (plugins, plugin_data); ADR 0009
  builtin_plugins/      dice (/roll), translate (Google Translate, no model), thoughts (tree of thoughts:
                        a reply writer; thinkers in parallel, rounds, thoughts in swipe_meta; ADR 0011)
  services/assistant.py the assistant over the chat (stage 8б): settings (roles, critic, progress), conversations,
                        requests (answers, «Давай», messages into the running plan), own spending limits, undo,
                        «Что я о вас знаю» (assistant_memory);
  services/assistant_engine.py  how a request is worked on (10а): small ones by the step loop (JSON form via
                        generate_json, any model), big work as a task on the conversation: questions → plan →
                        items (writer, task pinned in every call) → critic (read-only, no conversation) → report;
                        a model per role (planner, writer, critic, small); ADR 0013;
                        services/assistant_tools.py (tools over the same services as the UI; try_phrase runs the real
                        bubble search on a test message, nothing kept);
                        services/assistant_app.py (app_call: the page's own requests in process, closed
                        routes, read_chat, theme tools, show); storage/changelog.py (rows a request
                        changed, via TEMP triggers, for undo);
                        storage/snapshot.py (rows + what goes with them on delete, put back);
                        storage/repos/assistant.py; api/assistant.py; ADR 0010
  providers/            Provider interface, openai_compat, gemini and anthropic (own protocols), registry
                        (kinds), keys (several keys per connection, a proxy each, next key / retries),
                        params (every setting: type, range, kinds, sent or Tavern's own), catalog
                        (known services with ready addresses), fallback (steps when a connection fails
                        before writing: other model / connection / key, refusal phrases, empty reply)
  services/look.py      the look kept across updates and safe mode (themes, styles, plugin parts); `safe-mode on|off`
                        command and `--safe` (one run); ADR 0015
  util/streams.py       small helpers for byte streams
  services/updates.py   update from the page («Обновить»: GitHub, copy first); api/updates.py, web/components/Updates.js;
                        the app ends with exit code 75 and start.sh / start.bat (TAVERN_SUPERVISED=1) start it
                        again; ADR 0016
  services/changes.py   change log of what the user and the assistant changed; api/changes.py, web/components/Changes.js
  services/groups.py    groups in the left panel; storage/repos/groups.py (migration 0020), api/groups.py,
                        web/components/Groups.js
  services/search.py    search across all chats; api/search.py, web/components/Search.js
  core/regex.py         regex rules (stage 11): JS patterns translated to Python re, applied to text / display /
                        prompt; core/regex_worker.py + core/regex_runner.py run them in a separate process with a
                        time limit; core/safe_html.py cleans the HTML a rule makes; services/regex.py,
                        storage/repos/regex.py (migration 0022), api/regex.py, formats/st_regex.py (SillyTavern
                        regex files), web/components/Regex.js, web/core/regex.js; ADR 0017
  services/assistant_files.py   files given to the assistant (pictures only in short requests; binary files only described)
  services/web_access.py        the assistant's internet tools (search, read a page); proxies are ignored
  services/assistant_extras.py  more assistant tools (skills it writes through its own tools); ADR 0018
  web/components/Select.js  own drop-down instead of the native <select> (themed, filter, keys); never add a native <select>
  web/core/layout.js, choice.js  menu layout (top / side, folded) and the «choice» setting control
  builtin_plugins/spelling  spelling strip above the chat message box; first user of t.composerHelper / t.composer
  skills/*.md           built-in skills now: characters, lorebooks, lorebook-work, regex, unslop, theme-scripts
  services/skills.py    skills (10б): instructions the assistant reads for a kind of work; built-in files in
                        tavern/skills/*.md (English, header name/title/description/audience, parts ## Plan /
                        ## Write / ## Check for planner / writer / critic), the user's own and copies (table
                        `skills`, storage/repos/skills.py; a copy of a built-in replaces it), plugins' skills/*.md,
                        the programmer's .claude/skills (read-only, audience programmer unless marked both);
                        api/skills.py; the engine picks one (small role) and shows «📖 Инструкция: …»
  services/profiles.py  connection profiles: snapshot of connection, model, settings, preset; apply
  services/parallel.py  several requests at once: settings, extra targets, collect/gather (also for
                        plugins and agents), race (first writer wins)
  services/spending.py  spending: a meter for LLMClient records every request (tokens, dollars by the
                        service's or own price), sums per message/chat/day/month, limits (warn/stop),
                        currency at own rate; storage/repos/spending.py (table `spending`)
  services/connections.py  what models can do: the service's model list, trial requests per setting
  core/structured.py    structured output: JSON Schema check (pure Python subset), the request in
                        words, the retry note; used by LLMClient.generate_json
  core/templates.py     instruct templates: conversation -> one text for text completion (built-ins,
                        auto by model name, SillyTavern instruct/context files both ways);
                        services/templates.py (own + built-in, resolve), storage/repos/templates.py
  formats/cards.py      character card V1/V2/V3 parsing and PNG metadata import/export
  formats/jsonl.py      versioned JSONL chat tree, swipes with what each carried, members (older files still read)
  formats/media.py      allowed file types for themes/pictures, image type sniffing
  formats/st_preset.py  SillyTavern Chat Completion preset <-> Tavern preset (converted once)
  storage/repos/        SQL access per entity (characters, chats/messages/swipes, connections, settings)
web/                    browser UI: Preact + htm vendored in web/vendor, plain ES modules, no bundler
  main.js               app shell; core/api.js (fetch + SSE), core/chat.js (chat state hook),
                        core/theme.js (applies a theme, runs its effects script, safe mode /?safe)
  components/           Icon, Menu, Dialogs, State (shared parts: see the modify-ui skill),
                        VirtualList, Message, Composer, Sidebar, Settings (tabs), Themes, Gallery,
                        Connections (services, keys, model check, settings console), Templates,
                        Profiles (profiles, top-bar switch, fallback steps, parallel requests,
                        side-by-side choice of variants), Spending (top-bar line, «Расход» tab;
                        core/spending.js formats money and tokens),
                        Members (chat members + chat preset), CharacterEditor, CharacterTree,
                        Presets, PromptView, Notes (📝 window, notes by numbers),
                        Lorebooks (left-panel shelf, book window, BookPicker, settings tab),
                        roles (role icons/labels), Plugins (Settings tab, «may it?» window), Assistant (🪄 window);
                        core/plugins.js runs plugins' web parts (buttons, lines and trees under messages,
                        side panels: components/PluginTree.js)
  styles.css            base look: CSS variables + data-slot places themes restyle; phone/tablet layout
  themes/               built-in themes (default, light, dark, sakura = example with effects)
tests/unit/             pure logic, no I/O beyond tmp_path (test_web_syntax parses every web/ module with Node)
tests/integration/      HTTP API through httpx.ASGITransport, CLI commands
tests/smoke/            starts the real server in a subprocess
docs/                   ROADMAP.md, STATUS.md, adr/ (decisions 0001 to 0018), parked/ (stopped drafts, not collected by pytest: voice-plugin)
.claude/skills/         task guides for agents (see below)
data/                   user data, git-ignored. Never commit, never delete
```

## Rules

1. **No SillyTavern code.** Do not read, copy or translate its source. Implement formats from
   public specs (character card V2/V3 spec) and observed behaviour.
2. **Pure-Python dependencies only.** Anything needing Rust or a C compiler breaks Termux
   (`pydantic`, `orjson`, `uvloop`, `tiktoken`, `Pillow` are out). New dependency: pin the exact
   version in `requirements.txt`, check `pip install --only-binary=:all:` gives a `py3-none-any`
   wheel, and record why in `docs/adr/`.
3. **Never touch real user data while developing.** Tests use `tmp_path`; manual runs use
   `--sandbox` or `--data-dir` pointing at a temp dir.
4. **Schema changes go through a new migration file.** Never edit a committed migration.
   See `.claude/skills/core-changes`.
5. **Run `python -m pytest` before you finish.** All tests must pass. Add tests with every
   change; bugs get a regression test first.
6. **Security defaults stay on.** Loopback-only by default; remote access requires the token;
   secrets never reach the browser or logs. Do not weaken `security.py`, the page CSP in `app.py`
   or the theme-script approval without an ADR (see `docs/adr/0002-themes.md`).
7. **Errors:** raise `TavernError` subclasses for expected failures (message shown to the user),
   `AppError` subclasses in request handlers (become `{"error": {code, message}}`). Do not
   swallow exceptions silently.
8. **Style:** type hints, `from __future__ import annotations`, module docstring stating the
   module's role, comments only where the *why* is not obvious. Python 3.11 compatible.
9. **Agents check in the browser on the FAKE model.** Owner's decision (2026-11-07: «Возвращаем тесты
   в браузере, без них тяжко»), replacing the one of 2026-11-06. Use chrome-devtools against a run on an
   empty temp `--data-dir` with a connection to the fake LLM (`python -m tavern.devtools.fake_llm`), plus
   `python -m pytest`. One agent at a time uses the browser, because the selected page is shared. Never the
   live model unless the owner asks for it in that task. Follow a written checklist item by item, no
   improvising and no side quests; anything not seen on screen is reported as unverified. Phone checks use
   375x667, DPR 2, mobile, touch (an iPhone 8; also 375x553 with browser bars and 375x410 with the keyboard
   open), and judge by numbers: buttons at least 44x44 CSS px, close buttons reachable without scrolling,
   text at least 14 px, nothing wider than the viewport. Check `innerWidth` is 375 after every reload.

## Skills

| Skill | When |
|---|---|
| [write-tests](.claude/skills/write-tests/SKILL.md) | adding or fixing any test |
| [debugging](.claude/skills/debugging/SKILL.md) | something fails at runtime or in tests |
| [core-changes](.claude/skills/core-changes/SKILL.md) | schema, config, security, storage, startup changes |
| [mobile-ui](.claude/skills/mobile-ui/SKILL.md) | **before any change under `web/`**: touch first, 44px, no jank, no blinking maps, settings order, test by numbers on 375x667 and 768x1280 |
| [modify-ui](.claude/skills/modify-ui/SKILL.md) | changing anything under `web/` (read mobile-ui first) |
| [interface-details](.claude/skills/interface-details/SKILL.md) | polish details: radius, shadows, motion, hit areas (adapted public skill, MIT) |
| [unslop-ui](.claude/skills/unslop-ui/SKILL.md) | a screen looks AI-made or uneven: redline pass (adapted public skill, CC BY 4.0) |
| [ui-a11y](.claude/skills/ui-a11y/SKILL.md) | labels, ARIA, focus, keys, contrast, reduced motion (adapted public skill, MIT) |
| [add-provider](.claude/skills/add-provider/SKILL.md) | adding an LLM provider kind (core or plugin) |
| [make-theme](.claude/skills/make-theme/SKILL.md) | restyling the interface: themes, pictures, animations, effects scripts |
| [style-bubble-map](.claude/skills/style-bubble-map/SKILL.md) | restyling the character bubble map: classes, states, lines, tokens, speed rules |
| [create-plugin](.claude/skills/create-plugin/SKILL.md) | writing or changing a plugin (server part, web part, tests, sharing) |
| [agentic-plugin](.claude/skills/agentic-plugin/SKILL.md) | a reply writer that thinks: several model calls, live tree, data kept with the reply |


## Done checklist

- [ ] `python -m pytest` passes
- [ ] New behaviour has tests; fixed bugs have a regression test
- [ ] AGENTS.md map and the relevant skill updated if structure or workflow changed
- [ ] `docs/ROADMAP.md` stage status updated
