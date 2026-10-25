# ADR 0001: Technology stack

Status: accepted (stage 0)

## Context

Requirements: fast start, low memory, smooth UI on long chats, Python backend, one-command start,
works on Termux (Android) without compiling native code, easy for AI agents to modify.

## Decision

| Layer | Choice | Rejected and why |
|---|---|---|
| HTTP server | Starlette + uvicorn (plain, `loop="asyncio"`, `http="h11"`) | FastAPI: pulls pydantic (Rust core). `uvicorn[standard]`: uvloop/httptools need C builds |
| Outgoing HTTP | httpx | aiohttp: C extensions |
| Validation | own dataclasses and checks | pydantic: Rust |
| JSON | stdlib `json` | orjson: Rust |
| Storage | SQLite (stdlib), WAL, `STRICT` tables | JSON files like SillyTavern: no atomicity, slow pagination of long chats |
| Config | TOML via stdlib `tomllib` | YAML: extra dependency |
| PNG cards | own chunk parser (`struct` + `zlib`) | Pillow: C build on Termux |
| Tokens | estimate + provider tokenize endpoints | tiktoken: Rust |
| Frontend | ES modules, Preact + htm + signals vendored in `web/vendor`, no bundler | React/Vite: needs Node; slower edit loop for agents |
| Tests | pytest + anyio plugin, `httpx.ASGITransport` | Starlette TestClient: deprecated with httpx in Starlette 1.x |

All runtime wheels are `py3-none-any` (checked with
`pip install --dry-run --only-binary=:all: -r requirements.txt`).

## Consequences

- Install on Termux is `pkg install python` plus pip with no compiler.
- Measured on stage 0 (Windows, Python 3.15): ready in 0.09 s, about 37 MB working set.
- Token counts are approximate unless the provider offers a tokenize endpoint.
- Plugins needing heavy native libraries must declare them as optional and degrade gracefully.
