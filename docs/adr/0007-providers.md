# 0007. Providers: service catalog, several keys with proxies, model facts, Gemini's own protocol

Status: accepted (stage 6а, 2026-10-30)

## Context

The owner wants a long list of popular services plus "my own address", a proxy per key, the fullest
possible settings shown "like a console, but pretty", and an automatic check of what a model
accepts. They use Gemini through a program of their own. Later parts of stage 6 add Anthropic,
instruct templates, profiles with fallbacks, parallel requests, spending and structured output on
top of this.

## Decision

- **Kind vs service.** `kind` is the protocol (`openai`, `gemini`, plugins add more); `service` is
  the catalog entry the connection came from (`providers/catalog.py`). Most services speak the
  OpenAI protocol, so the catalog is mostly addresses. The address stays editable.
- **Settings table** (`providers/params.py`): every setting has a name, type, range, the kinds that
  take it, and whether it is sent at all (`send=False` = Tavern's own: context size, retries, key
  rotation). An absent setting is not sent. `extra_body` is merged last, as is. `chat.py` sends
  `request_params(kind, params)`; the preset's model settings still win.
- **Keys** live in the secret store as `connection:<id>:keys` = `[{id, label, key, proxy}]`. The old
  single `connection:<id>:api_key` is read as one key and removed on the next write. The browser
  sees labels, `has_key`, and a proxy's `scheme://host:port` only.
- **KeyedProvider** wraps one provider per key. Before any text arrives: `limit`/`auth` failures go
  to the next key (`rotate_keys`), `network`/`server` failures repeat the same key (`retries`,
  `retry_delay`). The key that worked is remembered per connection in memory. Messages have keys and
  proxy strings replaced by `***`. `ProviderError.reason` carries the cause so stage 6г can add
  fallbacks without parsing messages.
- **Proxy per key**: a key with a proxy gets its own `httpx.AsyncClient(proxy=...)`, closed with the
  provider. SOCKS needs `socksio` (pure Python, `py3-none-any`, pinned 1.0.0).
- **Model facts** (`model_info` table, migration 0010): `describe_models()` gives context, maximum
  answer, accepted settings and prices where the service says (OpenRouter-style `/models`, Gemini's
  `models` list). Where it does not, "Пробные запросы" sends one one-token request per setting; a
  400 means "not accepted". A service that silently ignores a setting cannot be told apart from one
  that accepts it; the window says so.
- **Gemini** natively (`providers/gemini.py`): key in the `x-goog-api-key` header, system messages
  into `systemInstruction`, same-role turns merged, thinking parts as `reasoning` stream events,
  safety filters off on request, blocked prompts and safety stops raise `reason="refused"`.

## Consequences

- Plugins keep the `(connection, api_key, http_client)` factory signature; their kinds get every
  sendable setting.
- `ctx.providers.create()` returns a `KeyedProvider`; kind-specific methods are reached via `inner()`.
- "reasoning" events are produced but not yet shown (stage 6б shows them folded above the reply).

## Addendum: stage 6б (2026-10-31)

- **Claude** natively (`providers/anthropic.py`): key in `x-api-key`, `max_tokens` always sent
  (4096 by default), thinking with a budget (at least 1024; temperature and top_p/top_k are then
  left out, as the API requires), `stop_reason: refusal` raises `reason="refused"`, 529 counts as
  a server failure, "credit balance" as a limit. No native JSON mode: the text is parsed instead.
- **Thinking**: `LLMClient.stream` passes "reasoning" events on (it used to treat them as the end
  of the stream). The default generator hands them to `job.think()`; the chat streams them as SSE
  `reasoning` and keeps them in the reply's `swipe_meta.reasoning` (no schema change: swipes
  already had `meta`; messages now return the active swipe's meta as `swipe_meta`).
- **Pictures**: `send` takes `images` (names in the picture library); they are kept in the user
  message's `swipe_meta.images`, read at prompt time (PNG/JPEG/GIF/WebP up to 10 MB, at most 8 per
  message; a missing one is skipped with a log line) and sent in each protocol's own form.
- **Web search** is one setting, `web_search`: Gemini `google_search` tool, Claude's web search
  tool, OpenRouter's `web` plugin, `web_search_options` for other OpenAI-style services.

## Addendum: stage 6в (2026-10-31)

- **Text mode**: an OpenAI-style connection with `api_mode = "text"` renders the conversation with an
  instruct template (`core/templates.py`) and posts it to `/completions`; the template's stop strings
  and the next user marker join the connection's own (all of them: local servers take more than 4).
- **Templates** have our own shape (markers per role, `bos`, `story_string` around the leading system
  text, `chat_start`, `system_as_user`, stop strings, a `models` regex for "auto"). Thirteen are
  built in and never stored; the user's own live in `instruct_templates` (migration 0011). "auto"
  picks the first template, own ones first, whose regex matches the model name; else ChatML.
- **SillyTavern** instruct and context files import (also merged into one template) and export.
  Its story string is a template over card fields; the preset already puts those into the system
  text, so only `{{system}}` and `{{#if system}}` survive import. `wrap` becomes explicit newlines.
- The registry resolves the template when it builds the provider (`template_resolver`, set by the
  context so own templates count); plugin factories see it as `connection["template"]`.

## Addendum: stage 6г (2026-10-31)

- **Profiles** (`services/profiles.py`) are snapshots of what is active: connection, its model, its
  settings and the preset, each part switchable on or off. Kept in the settings table
  (`connection_profiles`, `active_profile`): no schema change, no secrets.
- **Fallback steps** (`providers/fallback.py`) are the connection setting `fallbacks`: in order,
  each step names the failures it answers (`ProviderError.reason`, plus `empty`) and where to go
  (another connection, another model, one key = its proxy). Only before anything was passed on: a
  half-written reply is never replaced. `registry.create(..., fallbacks=False)` is used by checks
  of a single connection (model list, trial requests).
- **Refusal by text**: when a step answers `refused`, the reply is held back until it is longer than
  400 characters; a short reply containing a refusal phrase (`refusal_phrases`, or a built-in
  Russian/English list) counts as a refusal. Longer replies are never judged, so a story that
  merely mentions "I can't" is not thrown away.
- The answering step is kept with the reply as `swipe_meta.via` and shown as "↪ через …".

## Addendum: stage 6д (2026-11-01)

- **Parallel requests** (`services/parallel.py`, setting `parallel`): on send and regenerate, with
  the default generator, extra targets (a connection or "the active one", a model, a count) run
  beside the main request on the same prompt, each with its own connection's settings plus the
  preset's model settings, all charged to the run's one budget.
- Modes: `variants` streams the main reply and adds the others as swipes (the main one stays shown,
  each swipe's `via` names its source); `first` races them all and keeps only the first that writes
  text, cancelling the rest; `pick` is `variants` plus `done.pick`, and the chat opens a side-by-side
  choice. A failed extra never fails the reply: it is reported as a `variants_failed` event.
- `parallel.gather()` / `collect()` / `race()` are the same tools for plugins and agents.

## Addendum: stage 6е (2026-11-01)

- **Spending** (`services/spending.py`, table `spending`, migration 0012): one row per model request
  with tokens and the cost in dollars (NULL when no price is known). Recording is always on; the
  setting `spending` only decides where it is shown (under messages, per chat, per day, per month,
  tokens or not), the currency at the user's own rate, limits and own prices.
- `LLMClient(meter=...)` is the single hook: it is told about every finished call, and about a call
  stopped by the user or lost in a race (then the counts are estimated, `estimated`). Provider
  errors are not charged. The meter returns the cost, so `RunBudget.max_cost` now works too.
- Price: the user's own price for a model name wins; else the service's model list (`model_info`)
  of that connection, else of any connection listing that model. Unknown price = tokens only.
- Fallbacks are priced as the model that actually answered (`FallbackProvider.serving`); attempts it
  threw away (refusal, empty reply) are recorded too (`discarded`): the service billed them.
- Each reply version keeps its own sum in `swipe_meta.usage` (variants of parallel requests too).
- Limits are in the shown currency against today/this month (local calendar). `warn` sends a
  `spend_warning` run event; `stop` refuses send/continue/regenerate with 409 `spend_limit` before
  any message is created. Rows survive deleting a chat or connection: money spent stays spent.

## Addendum: stage 6ж (2026-11-01)

- **Structured output** is `LLMClient.generate_json(messages, schema=..., retries=1)` for plugins
  and agents; there is no UI. Native modes: OpenAI-compatible `response_format` (json_schema),
  Gemini `responseJsonSchema`, Claude a forced tool (`tool_choice`; a non-object root is wrapped as
  `{"value": ...}` and unwrapped; not with thinking or web search, which forbid forcing a tool).
- The request is always also said in words (appended to the last user turn), so text completion,
  small local models and proxies that drop the mode still understand it. A backend that rejects
  the mode (`request` error) is asked again without it, once.
- The answer is checked against the schema by `core/structured.py`, a pure-Python subset of JSON
  Schema (no `jsonschema` dependency: Termux). When it does not fit, the model sees its own answer
  and the list of problems and tries again, up to `retries` times; then `StructuredOutputError`
  (`reason="format"`, details: last text and problems). Every attempt is a paid request.
- The model check has one more trial, `structured`: "ok" (fits in the JSON mode), "words" (mode
  rejected, words worked) or "rejected".
