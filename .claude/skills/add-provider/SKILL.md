---
name: add-provider
description: How to add an LLM provider kind to Tavern (core or plugin) - the Provider interface, registry, streaming, usage, JSON output, tests against the fake server. Use when adding Anthropic, Gemini, a local backend or any new API.
---

# Adding a provider

A provider turns a `GenerationRequest` into a stream of `StreamEvent`s. Read
`tavern/providers/base.py` (interface) and `tavern/providers/openai_compat.py` (reference
implementation) first.

## Contract

- `stream(request)` yields `StreamEvent(kind="text", text=...)` deltas, then one
  `StreamEvent(kind="done", usage=Usage(...), finish_reason=...)`.
- `list_models()` returns model ids. Override `describe_models()` when the backend tells more
  (context, maximum answer, accepted settings as names from `providers/params.py`, prices per
  million tokens): the settings window marks settings ✓/✗ from it.
- Map the settings of `providers/params.py` that your kind takes (add the kind to their `kinds`);
  `request.extra` holds them under those names, plus `extra_body` to merge last.
- Thinking text goes out as `StreamEvent("reasoning", ...)`, never mixed into "text" (it is saved as
  the reply's `swipe_meta.reasoning`).
- `ChatMessage.images` holds pictures as (media type, base64): put them into your wire format.
- The `web_search` setting turns on the backend's own search tool where it has one.
- Honour cancellation: never swallow `asyncio.CancelledError`. Stop is implemented by cancelling the
  task, so a provider must not wrap the stream in a bare `except`.
- Report `Usage` whenever the backend gives it; the run budget (`max_tokens`, `max_cost`) and spending
  (`services/spending.py`: what each reply cost) depend on it. Without it the counts are a rough guess
  marked «≈». Prices come from `describe_models()` (`ModelInfo.price_in/out`, dollars per million).
- `request.response_format` (`{"type": "json_object"}` or `{"type": "json_schema", ...}`): map it to
  the backend's native mode (OpenAI `response_format`, Gemini `responseJsonSchema`, Claude a forced
  tool, see `anthropic.py`). If the backend has none, ignore it: `LLMClient.generate_json` always says
  the request in words too, checks the answer against the schema (`core/structured.py`) and asks
  again with the problems. A backend that rejects the mode with a `request` error is asked once more
  without it. Test it against the fake (`fake.queue('{...}')`, `fake.rejected_params`).
- Errors: raise `ProviderError` with a message safe to show to the user and a `reason`
  (`limit`, `auth`, `refused`, `network`, `server`, `request`; `reason_for_status()` maps HTTP codes):
  key rotation and retries in `providers/keys.py` decide by it. Never include the API key
  or the full request in a message or a log line.
- Use pure-Python `httpx` only. Accept an injected `httpx.AsyncClient` (tests pass one wired to the
  fake server); create and close your own when none is given.

## Register

Core kinds are added to `ProviderRegistry._factories` in `tavern/providers/registry.py`.
A plugin calls `registry.register("kind", factory)`. The factory signature is
`(connection: dict, api_key: str, http_client | None) -> Provider`. The API key comes from the secret
store (`connection:<id>:keys`, one entry per key); keys are write-only through the API.

The kind then appears in `GET /api/connections` (`kinds`) and can be chosen when creating a connection.
Add ready addresses to `providers/catalog.py` if it is a public service. `registry.create()` wraps
your provider per key in `KeyedProvider` (several keys, a proxy each: a key with a proxy gives your
factory an `http_client` going through it).
Connection `params` (temperature, top_p, max_tokens, stop, max_context_tokens) are passed by
`services/chat.py`; provider-specific options go into `request.extra`.

## Test (no network, no keys)

1. Extend `tavern/devtools/fake_llm.py` or write a small Starlette fake of the backend's wire format
   next to your tests; assert on the request body it received (model, messages, stream flag, format).
2. Unit tests in `tests/unit/`: SSE/NDJSON parsing, usage extraction, error mapping (401, 429, 500,
   malformed chunk, connection refused), cancellation mid-stream.
3. One integration test through `/api/chats/{id}/send` with a connection of the new kind.
4. A real-socket test (`live_llm` fixture style) is needed whenever behaviour depends on incremental
   delivery; `httpx.ASGITransport` buffers whole responses.

## Checklist

- [ ] deltas are yielded as they arrive (not buffered until the end)
- [ ] `done` event always sent, with usage if available
- [ ] errors keep secrets out of messages and logs
- [ ] new dependency? Pure-Python wheel, pinned, ADR in `docs/adr/` (see AGENTS.md rule 2)
- [ ] `python -m pytest` passes
