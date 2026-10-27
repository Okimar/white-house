# AGENTS.md

Guide for AI agents (and humans) working on this repository. Read this first, then the skill that
matches your task.

## What this is

Tavern: a roleplay chat frontend with SillyTavern's feature set, written from scratch (no
SillyTavern code: it is AGPL). Compatible with SillyTavern data formats. Python backend
(Starlette), browser UI without a build step. Must run on Termux (Android).

Plan and stage status: [docs/ROADMAP.md](docs/ROADMAP.md). Current stage: **0, 1, 2 and 3 done, 4 next**.

## Commands

| Task | Command |
|---|---|
| Install (Linux/macOS/Termux) | `bash install.sh` |
| Run | `./start.sh` or `start.bat` or `python -m tavern` |
| Run on a throwaway copy of the data | `python -m tavern --sandbox` |
| All tests | `python -m pytest` |
| Fast tests only | `python -m pytest tests/unit` |
| Health check of a data dir | `python -m tavern check` |
| Snapshots | `python -m tavern backup create|list|restore NAME` |
| Fake LLM for manual testing | `python -m tavern.devtools.fake_llm --port 5001` |

Use the virtualenv interpreter: `.venv/bin/python` (Windows: `.venv\Scripts\python.exe`).

## Map

```
tavern/                 Python package (backend)
  cli.py                entry point, subcommands; `serve` is the default
  app.py                Starlette app factory, API routes, error handlers
  context.py            AppContext: config + db + secrets for one running instance
  config.py             data/config.toml -> frozen dataclasses, strict validation
  security.py           access control middleware (loopback / token / Host / Origin)
  secret_store.py       data/secrets.json: access token, API keys (never sent to the browser)
  sandbox.py            --sandbox: temp copy of the data dir
  errors.py             TavernError (user-facing) / AppError (HTTP JSON errors)
  logs.py               stderr + rotating data/logs/tavern.log
  paths.py              every filesystem path is derived here
  storage/db.py         SQLite wrapper, transactions, migration runner
  storage/migrations/   NNNN_name.sql, applied in order, one transaction each
  storage/backup.py     snapshots via SQLite backup API, restore, prune
  util/                 atomic file writes, data-dir lock
  devtools/fake_llm.py  scriptable OpenAI-compatible fake server for tests
  api/                  HTTP routes (chats + SSE, characters + settings, connections); thin, no logic
  services/chat.py      use cases: create chat, send, continue, impersonate, regenerate, stop
  core/                 events (EventBus), budget (RunBudget), prompt, llm (LLMClient), generation
                        (Generator strategies), runs (background Run + SSE replay)
  providers/            Provider interface, openai_compat, registry (kinds, API keys from secrets)
  formats/cards.py      character card V1/V2/V3 parsing and PNG metadata import/export
  formats/jsonl.py      versioned JSONL chat tree and swipe import/export
  storage/repos/        SQL access per entity (characters, chats/messages/swipes, connections, settings)
web/                    browser UI: Preact + htm vendored in web/vendor, plain ES modules, no bundler
  main.js               app shell; core/api.js (fetch + SSE), core/chat.js (chat state hook)
  components/           VirtualList, Message, Composer, Sidebar, Settings, roles (role icons/labels)
  styles.css            light/dark via prefers-color-scheme, mobile layout under 720px
tests/unit/             pure logic, no I/O beyond tmp_path
tests/integration/      HTTP API through httpx.ASGITransport, CLI commands
tests/smoke/            starts the real server in a subprocess
docs/                   ROADMAP.md, adr/ (architecture decisions)
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
   secrets never reach the browser or logs. Do not weaken `security.py` without an ADR.
7. **Errors:** raise `TavernError` subclasses for expected failures (message shown to the user),
   `AppError` subclasses in request handlers (become `{"error": {code, message}}`). Do not
   swallow exceptions silently.
8. **Style:** type hints, `from __future__ import annotations`, module docstring stating the
   module's role, comments only where the *why* is not obvious. Python 3.11 compatible.

## Skills

| Skill | When |
|---|---|
| [write-tests](.claude/skills/write-tests/SKILL.md) | adding or fixing any test |
| [debugging](.claude/skills/debugging/SKILL.md) | something fails at runtime or in tests |
| [core-changes](.claude/skills/core-changes/SKILL.md) | schema, config, security, storage, startup changes |
| [modify-ui](.claude/skills/modify-ui/SKILL.md) | changing anything under `web/` |
| [add-provider](.claude/skills/add-provider/SKILL.md) | adding an LLM provider kind (core or plugin) |

Planned with their stages: `create-plugin` and `agentic-plugin` (stage 8).

## Done checklist

- [ ] `python -m pytest` passes
- [ ] New behaviour has tests; fixed bugs have a regression test
- [ ] AGENTS.md map and the relevant skill updated if structure or workflow changed
- [ ] `docs/ROADMAP.md` stage status updated
