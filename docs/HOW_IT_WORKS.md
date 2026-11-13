# How it works: the runtime pipelines

For agents and programmers who change this app. It describes what happens at runtime, in order, with
`file:function` references, so you need not re-read the code to find your way. The map of files is in
[AGENTS.md](../AGENTS.md); decisions are in [docs/adr/](adr/).

Written against the working tree of 2026-11-13, which includes the SillyTavern parity work
([docs/ST_PARITY.md](ST_PARITY.md): what we match, what we deliberately do not). When behaviour changes, update this
file, [GUIDE.md](GUIDE.md) (the owner's user guide, Russian) and ST_PARITY.md.

To see how the request we send differs from another program's (SillyTavern, a raw API call), use the two devtools:
`python -m tavern.devtools.capture --port 5003 --out captures` is a recording stand-in for an LLM server (OpenAI,
Gemini and Claude shaped; `--forward URL` passes the request on to the real service). Point each program at it and
every generation request is saved as one JSON file. `python -m tavern.devtools.compare_requests A.json B.json` then
prints what differs (messages as role + text with invisible characters made visible, then parameters; exit code 1
when they differ). Tests: `tests/unit/test_capture.py`, `tests/unit/test_compare_requests.py`.

Conventions: paths are relative to the repo root. `ctx` is the `AppContext`. "Entry" is
`core/assembly.py:Entry` and "bubble" or "block" is a row of `character_blocks` (a character's tree or a
lorebook's entries).

---

## 1. Overview

**Process.** `python -m tavern` → `tavern/cli.py` (`serve` by default) → `tavern/app.py` builds a Starlette app.
API routes are mounted under `/api` (`app.py`, `Mount("/api", routes=api_routes())`), and static `web/` is served
without a build step. There is one process and one asyncio loop. Regex matching runs in a separate worker process
(`core/regex_runner.py`).

**AppContext** (`tavern/context.py:open_context`) is built once and reached by routes through
`request.app.state.ctx`. It holds:
- `paths`, `config` (data/config.toml), `secrets` (data/secrets.json: keys and the token, never sent to the browser)
- `db`: SQLite (`storage/db.py`). Migrations run at open, with a snapshot taken first.
- repositories: `characters, chats, connections, templates, skills, settings, members, blocks, presets, notes,
  history, lorebooks, spending, plugin_repo, assistant_repo`, plus `prompt_log`, `assets`, `themes`
- `events` (`core/events.py:EventBus`), `providers` (`providers/registry.py:ProviderRegistry`), `generators`
  (`core/generation.py:GeneratorRegistry`), `runs` (`core/runs.py:RunManager`, owns `foreground`), `notices`
  (app-wide page events, `GET /api/events`), and after construction `plugins`, `backups`, `gdrive`, `jobs`.

**Storage.** Chats are trees of messages. Each message has swipes (versions), and every swipe has a `swipe_meta`
dict (reasoning, usage, via, images, regex_before, plugin data). A chat's `leaf_id` is the active branch, and
`ctx.chats.active_path(chat_id)` returns it oldest-first. Each message's `depth` is its 0-based position on the
path. `chats.meta` is a JSON dict: `preset_id`, `connection_id`, `generator`, `speakers`, and the reply counter
`generation`. Settings are rows in the `settings` table (`ctx.settings.get(key)`).

**Events** (`core/events.py`). There are two kinds of hook:
- **actions**: `await events.emit(name, **payload)`. Return values are ignored.
- **filters**: `await events.filter(name, value, **payload)`. A handler returns the new value; returning None keeps
  the value as it was.

Handler errors are logged and skipped. Lower `priority` runs first. `off(owner)` removes everything a plugin
registered. The events a reply fires are listed under `core/generation.py`. Other events: `message.added|edited|
excluded|deleted|swipe_selected`, `chat.created`, `chat.branch_selected`, `chat.members_changed`,
`character.blocks_changed`, `lorebook.blocks_changed`, `lorebooks.changed|uses_changed`, `notes.changed`,
`message_notes.changed`, `presets.changed`.

**Runs/SSE** (`core/runs.py`). `RunManager.start(chat_id, budget, body)` allows one run per chat
(`services/chat.py:_ensure_idle` raises `busy` otherwise). It marks the app busy (`foreground.enter`), so
background model work waits (`core/foreground.py`). `Run.emit(type, **data)` appends to `run.events`.
`GET /api/runs/{run_id}/events` (`api/chats.py:run_events`) replays every event from `Last-Event-ID`/`?after`, then
follows live. A run outlives the HTTP request: a sleeping phone tab still gets the reply saved. Event types:
`start, delta, reasoning, trace, spend_warning, meaning_failed, variants_failed, turn_start, reply_done,
reply_error, done, stopped, error`.

**Plugin hooks** (ADR 0009, `plugins/api.py:PluginAPI`):
- `api.on(event, fn)` registers on any event above.
- `api.add_generator` replaces the reply writer (`chat.meta.generator` or the setting `generator`).
- `api.llm()` gives a gated, metered client.
- `api.embed` and `api.job` handle vectors and background jobs.
- `api.action` and `api.command` add calls for the page and slash commands.
- `api.on_export` and `api.on_import` carry data in the chat file.
- `Extra`s are added to `job.prompt_input.extras` in `chat.before_generate` (section 7).

```
page ──POST /api/chats/{id}/send──▶ api/chats.py:send ──▶ services/chat.py:start_send
                                                         │ validate, regex «stored» on user text, save user msg,
                                                         │ pick speaker, add empty reply, spending.guard
                                                         ▼
                                         RunManager.start(body)  ◀── page GET /api/runs/{id}/events (SSE)
                                                         │
     _writer.body: LLMClient(provider=registry.create(conn)) ─ speakers.ask_model? ─ cast.prompt_members
       ─ lorebooks.meaning_for_reply (ONE embedding call) ─ character_tree.select_for_chat ─ lorebooks.select_for_chat
       ─ regex.prompt_texts (worker process) ─ PromptInput ─ events «chat.before_generate» (plugins add Extras)
       ─ generator.generate(job) ─▶ job.build_messages ─▶ core/assembly.py:assemble ─▶ filter «prompt.built»
       ─▶ LLMClient.stream ─▶ FallbackProvider ─▶ KeyedProvider ─▶ openai_compat | gemini | anthropic
       ◀── text / reasoning events (ThinkSplitter) ── run.emit("delta"|"reasoning")
       ─▶ settle: regex «stored» → chats.set_content; swipe_meta; prompt_log.record; notes.record_placed
       ─▶ run.emit("done"); events «message.received»
```

---

## 2. A chat reply end to end

**In:** `POST /api/chats/{id}/send {text, reply_role?, images?, speaker?}`. Sibling routes in `api/chats.py`:
`/continue`, `/impersonate`, `/regenerate`, `/talk`, `/stop`.

### 2.1 Before the run (sync, `services/chat.py`)

1. `start_send`: strip the text, check the images (`ctx.assets.get`) and the role, `_ensure_idle`.
2. `regex_service.stored(ctx, chat_id, text, "user")` applies «stored» rules to the user's text. When they change
   it, the original is kept in `swipe_meta.regex_before`.
3. Order "manual" with 2+ characters and no `speaker`: only the user message is saved and the route returns
   `run_id: null`.
4. `resolve_connection`: `chat.meta.connection_id`, else the setting `active_connection`. Without one it raises
   `no_connection`.
5. Order "all" with 2+ who may speak: `_start_turn` (section 7.3).
6. `_pick` decides who speaks: an explicit `speaker` (checked by `speakers.require`), else `speakers.choose`, else
   for order "model" `ask=True` (the speaker is decided inside the run).
7. `spending.guard` raises `spend_limit` (on_limit=stop) or returns warning text.
8. Save the user message, emit `message.added`, add an empty reply (`_author` gives `character_id` and name), then
   `_launch` → `_writer` → `ctx.runs.start`.
- `start_continue` does the same without a user turn. `role="user"` is impersonation.
- `start_regenerate` adds an empty swipe (`chats.add_swipe`) to the last assistant or system message
  (`new_swipe=True`).

### 2.2 Inside the run: `services/chat.py:_writer.body`

1. `provider = ctx.providers.create(connection)` (section 6.3).
   `llm = LLMClient(provider, model, budget=run.budget, trace=run.trace, defaults=request_params(kind, params),
   meter=spending.meter(...))`. The connection's `max_context_tokens` is popped from the params and becomes the
   budget. The preset's `params` are never used (model settings live on the connection).
2. `history` is the active path without the reply itself and without `skip_ids`.
3. When `ask_speaker` is set, `speakers.ask_model` makes one `generate_json` request
   (`{"name": enum}`, purpose `speaker`). If it fails, `speakers.choose` decides. Then `chats.set_speaker`.
4. `character` is the reply's `character_id`, else the chat's main character.
   `members = cast.prompt_members(ctx, chat, speaker)` returns `assembly.Member`s. With 2+ characters it keeps only
   the speaker and «me», unless `speakers.others == "full"` (then every member that is not muted).
5. `events.filter("meaning.wanted", False, chat=chat)`: a plugin asks for query vectors.
6. `lorebooks.meaning_for_reply` makes **one** embedding request for the members' cards and the active books
   (section 4.3). It returns `Meaning(scores, queries, texts, model, error)`.
7. `character_tree.select_for_chat` picks the bubbles of each member and **increments
   `chat.meta.generation`**. The result goes to `member.blocks`.
8. `lorebooks.select_for_chat(..., max_context, seed_text=<chosen bubble texts>, meaning)` returns a `Selection`
   (found, dropped).
9. `regex_service.prompt_texts` runs in a thread (the worker process). It returns the rewritten texts by message
   id, by lore block id, and for old reasoning, plus the rules that were skipped (announced to every open page).
10. `PromptInput(preset, members, history=notes.with_attached(...), notes=notes.for_reply(...), lore, lore_dropped,
    rewritten…, several, scene=speakers.scene(...), impersonate=reply.role=="user", max_context_tokens,
    load_image)`.
11. `GenerationJob(...)`, with `job.params["split_thinking"]=True`. `run.emit("start")`, plus `spend_warning` and
    `meaning_failed` when there is one.
12. `events.emit("chat.before_generate", job=job)`: plugins add `Extra`s, `values`, `tail_from`,
    `memory_reserve`, `lore_rewritten` (section 7).
13. The generator is `ctx.generators.get(chat.meta.generator or setting generator)`. When a plugin owns it,
    `job.llm` is forked with the plugin's gate (`ctx.plugins.gate_for`) and the meter purpose `plugin:<id>`.
14. Generation:
    - Normal: `DefaultGenerator.generate` → `job.build_messages()`, which runs `assemble()` and then
      `events.filter("prompt.built", messages, job=job)`, then `llm.stream(messages, **job.params)`. Text events
      become `run.emit("delta")`, reasoning goes to `job.think`, which emits `reasoning`.
    - Parallel (setting `parallel.enabled`, default generator only, allowed for send/regenerate):
      `parallel.extra_targets` returns targets.
      - mode `variants|pick`: `_main_with_variants` streams the main reply while `parallel.collect` runs the
        others. Non-empty results become extra swipes (`_add_variants`), and the main one stays shown.
      - mode `first`: `parallel.race`. The first stream to write text wins and the others are cancelled.
15. Finish:
    - `finish(text)` → `settle`: `regex_service.stored(text, role)` → `chats.set_content(reply, swipe_idx)`, plus
      `regex_before`.
    - An empty reply is discarded (the message or the swipe is removed) and reported as the error `empty_reply`.
    - `swipe_meta` gets `job.meta` (plugin data, `reasoning_tag`), `reasoning`, `via` (`via_line`: the parallel
      label or the fallback label from `provider.used`) and `usage` (from the `spending.Tally`).
    - `archive(job)`: `ctx.prompt_log.record(chat_id, message_id, swipe_idx, entries, meta)`. Entries are the
      `Prompt.entries` dicts. If `prompt.built` changed the messages, what was really sent is archived instead,
      labelled «после плагинов». Then `notes.record_placed`: fired hidden/visible notes are attached to the message
      they followed.
    - `run.emit("done", message, budget, pick)` and then `events.emit("message.received")`.
    - On stop (CancelledError) the partial text is saved and `stopped` is emitted.
    - On an exception the partial text is saved, then `error` and the event `generation.failed`.
    - `provider.aclose()` always runs.

### 2.3 Streaming and thinking

`core/llm.py:LLMClient.stream` → `_begin` (gate, then `budget.begin_step`) → `_request` builds a
`GenerationRequest`:
- the fields are `model, max_tokens, temperature, top_p, stop, response_format`; the other params go to `extra`;
- `split_thinking` is removed before sending.

Provider events are `text | reasoning | done`. With `split_thinking`, `core/thinking.py:ThinkSplitter.feed` turns
`<thinking>`/`<think>` written inside the text into `reasoning` events. Tags may be split across chunks.
- An unclosed tag with no reply text: the raw text is emitted as the reply, so the reply is never empty.
- On `done`, usage is charged (`_charge`). When the service reported no usage, tokens are estimated at 4
  characters each. The meter records the call and returns its cost in dollars, which the run budget counts.
- On cancel the partial request is still billed.

With `split_thinking`, `LLMClient.stream` also yields one `reasoning_tag` event (text `thinking` or `think`) the first
time the splitter sees an opening tag (`ThinkSplitter.tag`). The writers keep it as `job.meta["reasoning_tag"]`
(`core/generation.py:DefaultGenerator`, `services/chat.py:_writer`, `_main_with_variants`, `_narrate`), so it lands in
`swipe_meta.reasoning_tag`, and past thinking goes back in the same tag (3.3).

### 2.4 Provider request bodies

`GenerationRequest` → wire format. `ChatMessage(role, content, images)`.

| Kind | File / function | What becomes what |
|---|---|---|
| openai (chat) | `providers/openai_compat.py:OpenAICompatProvider.build_body` | `messages` as is, roles kept; images as `image_url` data parts (`_message`); `stop[:4]`; `stream_options.include_usage`; `seed=-1` dropped; `web_search` → OpenRouter `plugins:[{id:web}]` or `web_search_options`; other params go to the top level; `extra_body` merged last. Reasoning is read from `delta.reasoning_content`/`reasoning`. |
| openai (text mode) | same, with `self.template` | param `api_mode="text"` → `registry.create` resolves a template (`params.template`, "auto" = `core/templates.py:detect` by model name). `templates.render` makes one prompt: leading system messages go through `story_string`, roles are wrapped in prefixes/suffixes, and the text ends with an open assistant prefix (or inside the last assistant message: continue/prefill). Template stops plus the user prefix are added to `stop`. POST `/completions`. |
| gemini | `providers/gemini.py:GeminiProvider.build_body`, `_contents` | **Leading** system messages are joined with `\n\n` into `systemInstruction`. Later system messages become **user** turns. Neighbouring same-role turns are merged; assistant→`model`; an empty conversation gets `"."`. `generationConfig` (`maxOutputTokens, temperature, topP, topK, stopSequences[:5], seed, penalties`). `thinkingConfig` from `thinking_budget`/`reasoning_effort`/`include_thoughts`. JSON → `responseMimeType`/`responseJsonSchema`. `web_search` → `google_search` tool. `safety_off` → all categories OFF. Unknown params are dropped. |
| anthropic | `providers/anthropic.py:AnthropicProvider.build_body`, `_messages` | Leading system → `system`; later system → user turn. Same-role turns merged, and a leading `"."` user turn is added when needed. `max_tokens` defaults to 4096. `thinking_budget` → `thinking` (no temperature, top_p or top_k then). `stop_sequences`. JSON is a forced tool `answer`. `web_search` tool. A trailing assistant message works as a native prefill. |

Plugins may register other kinds (`registry.register`). `params.request_params(kind, params)` filters the
connection's settings to the ones that kind takes. `send=False` settings (`max_context_tokens, api_mode,
template, fallbacks, refusal_phrases, rotate_keys, retries, retry_delay`) never reach the wire.

### 2.5 Spending, archive

- `services/spending.py:meter` records one row per request in `spending`: connection, model, purpose
  (`chat|speaker|narrator|plugin:<id>|vectors`), tokens and cost. The user's own price wins; an unknown price is
  recorded as tokens only. Requests a fallback discarded are billed from `provider.discarded`.
- `Tally` adds up one version → `swipe_meta.usage`.
- `guard` runs before every reply.
- Archive: `storage/prompt_log.py:PromptLog.record` stores each text once (hash, zlib) and records only deltas
  against the previous record. It is read by `GET /api/messages/{id}/prompt` (`api/prompts.py`): «Что видела
  модель». Source keys: `preset:<id>:<block>`, `character:<id>:card|examples|system_prompt|…`, `block:<id>`,
  `message:<id>`, `note:<id>`, `message_note:<id>`, `prompt:<key>`, `plugin:memory:…`.

### 2.6 Fallbacks, keys, parallel, profiles

- **Keys** (`providers/keys.py:KeyedProvider`): one provider per key, each key with its own proxy client.
  - Before any text: `network|server` → retry the same key `retries` times after `retry_delay`; `limit|auth` → the
    next key (`rotate_keys`); otherwise stop.
  - The last key that worked is remembered per connection.
- **Fallbacks** (`providers/fallback.py:FallbackProvider`) wrap the keyed provider when `params.fallbacks` is set.
  - Each step is `{on:[reasons], connection, model, key}` and is tried when the failure reason (or `empty`) is in
    `on`, before anything was streamed.
  - Refusal phrases: while a step answers "refused", text is held back up to 400 characters and matched against
    them.
  - `provider.used` is the label of the step that answered, shown in `via`. `serving` and `discarded` feed
    spending.
- **Parallel** (`services/parallel.py`): setting `parallel = {enabled, mode: variants|first|pick, targets}`. Each
  target is a `providers.create` plus that connection's own `request_params`. `gather()` is the same machinery for
  plugins.
- **Profiles** (`services/profiles.py:apply`) are not runtime per request. They write `active_connection`, the
  connection's `model`/`params`, and `active_preset`.

---

## 3. Prompt assembly (`core/assembly.py:assemble`)

**In:** `PromptInput`, which holds:
- `preset` (normalized data), `members[Member]`, `history` (with `notes` attached), `notes[NoteInput]`,
  `lore[FoundBlock]`;
- `rewritten`, `lore_rewritten`, `thoughts_rewritten`;
- `extras[Extra]`, `tail_from`, `memory_reserve`, `values`;
- `several`, `scene`, `impersonate`, `max_context_tokens`.

**Out:** `Prompt(entries, messages[ChatMessage], tokens, dropped, placed)`.

ST-parity points inside assembly (details in the steps below): texts at one depth are ordered and joined
SillyTavern's way (3.4, `_Injection.order/cat/sub`, `CAT_*`); system texts are squashed only when the preset's
`squash_system` is on (3.5); lore at a depth or from an ST file is sent bare (3.2); outlet entries are never sent.

### 3.1 Preset blocks (`core/presets.py`)

A block is `{id, kind, name, enabled, role, text, position: top|depth, depth, group, when?, history?}`.

Kinds:
- `main`, `post_history`: text. With `prefer_card_prompts`, the card's own `system_prompt` /
  `post_history_instructions` replaces it, and `{{original}}` in the card text means the preset text
  (`card_with_override`).
- `persona`: the «me» member's card (`card_text`), prefixed `"{user} (played by the user):"`, plus its bubbles.
- `cast`: one entry per other member, by `importance` ascending, with `### Name` headers when there are several.
  Each card is followed by its bubbles and its `after_card` extras. Then `scene` ("Also in the scene:").
- `examples`: `mes_example` without `<START>` lines, "Example dialogue of X:". Lore `examples_top|bottom` goes
  around it.
- `lore_before` / `lore_after`: lore places `card_before` / `card_after`.
- `history`: exactly one per preset. Options `{mode: all|last, count, max_tokens, thoughts}`. Blocks before it
  go into `before`, after it into `after`.
- `text`: free text. With `when` it is a **behaviour block**, skipped in the flow and sent in the tail (3.5).
- `thinking` («Размышление», at most one): sent as `thinking.frame(text)` ("think inside `<thinking>`…"),
  skipped when `impersonate`.
- `memory`: the slot for plugin memory. No block means "just before the chat"; a disabled block means memory is
  off.

Rules that apply to every block:
- **position `depth`**: the block becomes an injection at `depth` (3.4) instead of going into before/after.
- **Member depth** (`Member.depth`, set in the members window): that member's card and bubbles go in as an
  injection at that depth.
- **groups «один из»**: `group` is only validated (`normalize_preset`: at most one enabled block per group). At
  runtime a disabled block is simply skipped.
- **Card text** (`card_text`): when the preset's `prompts.personality_format`/`scenario_format` are set (ST
  imports), they are used. Otherwise "Description:\n…", "Personality:", "Scenario:" sections.

### 3.2 Bubbles and lore placement

`found_entry` builds an Entry from a found block. A lore entry uses `lore_rewritten` when there is one.
- **Bare or with a path:** an entry at a depth, or one that came from an ST file (`retrieval.from_st`: it has
  `extra.st_entry`), is sent as its content only. Any other gets `"[path]\ncontent"` (path like `Book › Title`).
- **Role:** the block's `role` counts only at a depth. Lore (`kind == "lore"`) anywhere else is `system`. (The
  importer also forces `system` outside a depth, `formats/st_lorebook.py:block_from_entry`.)
- A block with `depth` set (not None) becomes an injection at that depth (`cat=CAT_LORE`, `sub=retrieval.block_order`).
- A block with `rules.place` (and no depth) goes to `placed_at[place]`, where `place` is one of
  `card_before, card_after, an_top, an_bottom, examples_top, examples_bottom`. Lore with no place goes to
  `card_before`.
- **`place == "outlet"`** (ST position 7, reached in ST only through a macro): never sent. `assemble` skips it for
  lore and drops `placed_at["outlet"]`; `services/lorebooks.py:select_for_chat` does not even choose it.
- A character bubble without a place goes next to its card (`place_blocks`).
- `an_top` and `an_bottom` become injections at the depth of the first fired note (default 4) and take that note's
  role (`note_role`, default system); `sub=-1` / `+1` puts them above / below the note's own text in one message.
- Places the preset has no block for go into `before`, just before the chat.
- A member whose card block is not in the preset still gets its bubbles, just before the chat.

### 3.3 History and budget

1. `history`: messages with `exclude_from_prompt` are dropped. Mode `last` keeps the last `count`.
2. `message_entry`:
   - the text is `rewritten[id]`, else `content`;
   - with `several`, another character's assistant line is prefixed `"Name: "` (unless `extra.together`);
   - the last `thoughts` assistant messages with `swipe_meta.reasoning` get `thinking.wrap(compact(reasoning),
     text, tag)` (`<thinking>…</thinking>` in front, with lines repeated from earlier thinking dropped). `tag` is
     `swipe_meta.reasoning_tag` (`<think>` when the model wrote that), else `thinking.SEND_TAG`. A preset imported
     from ST gets `history.thoughts = 1`, as ST sends the last reply's reasoning back;
   - `thoughts_rewritten` replaces the reasoning text;
   - images from `swipe_meta.images` are loaded through `load_image`;
   - attached notes go to `entry.follow`.
3. `history.max_tokens` cap: the newest messages are kept while they fit.
4. **Budget**, only when `max_context_tokens > 0`. Cost = `estimate_tokens` = len/4. Steps, in order:
   1. Drop the oldest messages, keeping `MIN_HISTORY=6` and the live tail from `tail_from`, against
      `max_context − memory_reserve` (the reserve counts only when conditional extras exist).
   2. Drop unconditional extras by `drop_rank`, lowest first.
   3. Drop member cards (owner entries) from the highest `importance` number down. The primary member and «me» are
      never dropped.
   4. Drop old messages down to 1.
   5. Add the conditional extras that fit (section 7.1).

   `Prompt.dropped = {messages: n, members: [...], lore: lore_dropped, extras?, memory?}`.

### 3.4 Injections at depth

Sources of injections: depth blocks, member depth, bubbles/lore with depth, notes, `an_top|an_bottom`, and
`Extra(place="depth")`.
- An injection at depth `d` goes at `index = max(0, len(messages) − d)` of the **sent** history, after trimming.
  `d=0` means after the last message. Messages hidden from the model and messages dropped by budget do not count.
- Several at one index are sorted in the final loop of `assemble` by `(order, ROLE_RANK[role], cat, sub)`:
  - `order` is the preset block's `order` (ST `injection_order`, default `DEFAULT_DEPTH_ORDER` 100); lore and
    bubbles use the default; lower first;
  - `ROLE_RANK` is assistant, user, system, so the system text stands nearest the chat message that follows;
  - `cat` inside one message: `CAT_PRESET`, `CAT_EXTRA` (a plugin's text), `CAT_NOTE`, `CAT_CARD`, `CAT_LORE`;
  - `sub` inside a kind: the entry's «Order» (`retrieval.block_order`, lower first), or above / below the note.

  Texts with the same `(order, role)` become **one message**: each after the first gets `Entry.join = True`, and
  the sender joins them with one line break.
- A fired note records `placed {note_id, mode, message_id (the message before it)}`. A saved copy already attached
  there (when regenerating) is not sent twice.

### 3.5 Tail, final list, roles

`entries = before + chat_part + after + tail`, where:
- `chat_part` is the injections, each message, and the message's notes;
- `tail` is the behaviour blocks (`core/presets.py:WHEN`, `when_texts`, enabled with text), in this order:
  - `no_write_for_me`: «me» has `model_may_write=False`, and this is not impersonation;
  - `speaker_reply`: `several`, and not impersonation;
  - `impersonate`: role user by default;
  - otherwise `prompts.prefill`, as an **assistant** Entry. Prefill is never sent with impersonate.
- `narrator` is used only by `_narrate` (7.3).

Other rules for the final list:
- When there is no user entry at all, `START_TURN="[Start]"` (user) is inserted before the chat.
- Messages (the loop over `entries` that builds `sent`):
  - an entry with `join` (a depth group, 3.4) is appended to the previous message of the same role with `\n`;
  - adjacent `system` entries that are not chat messages (`kind != "message"`) are merged with `\n` only when the
    preset's `squash_system` is on (`core/presets.py`, default true; an ST preset takes `squash_system_messages`,
    false when the file does not say; `to_st` writes it back);
  - everything else becomes one `ChatMessage` each, with roles as given.
- The roles that reach the provider: blocks have their `role`; history messages keep their stored role (`user`,
  `assistant`, `system`). The provider then folds non-leading system messages into user turns (Gemini, Anthropic)
  or keeps them (OpenAI).
- Impersonation: `IMPERSONATE_INSTRUCTION` in `services/chat.py` is used only by the legacy `core/prompt.py:
  build_prompt`. With a preset, the `impersonate` behaviour block does that job.

### 3.6 Macros (`core/prompt.py:substitute`)

`assemble.sub()` runs on **every** Entry text: blocks, cards, bubbles, lore, messages, notes, extras, behaviour
blocks and prefill. It runs in prompt order, so `{{setvar}}` → `{{getvar}}` works across blocks.

| Macro | Value |
|---|---|
| `{{char}}` / `{{user}}` | the primary member's name (default "Assistant") / the «me» member's name or the setting |
| `{{description}} {{personality}} {{scenario}}` | the primary card's fields |
| `{{mesExamples}}` | the primary card's examples, without `<START>` |
| `{{persona}}` | the «me» card's description |
| `{{summary}}` | "" unless a plugin sets `values["summary"]` (memory: «Сюжет до сих пор») |
| any `{{name}}` in `PromptInput.values` | the plugin's value. Names are matched case-insensitively; unknown macros stay as written. |
| `{{setvar::n::v}}` / `{{getvar::n}}` | per-prompt variables |
| `{{random::a::b}}` / `{{random:a,b}}` | random choice |
| `{{roll:NdM}}` / `{{roll::dM}}` | dice |

Other places that substitute:
- greetings at chat creation (`services/chat.py:create_chat`, char/user only);
- the narrator instruction;
- regex rule patterns with `macros` raw/escaped (`core/regex.py:build_spec`).

---

## 4. Retrieval: card bubbles and lorebooks

The engine is `core/retrieval.py:select_blocks`, pure logic. The callers are
`services/character_tree.py:select_for_chat` (per member) and `services/lorebooks.py:select_for_chat` (one pool).

### 4.1 Block fields (`storage/repos/blocks.py`)

- `mode` is `always` (blue), `search` (green) or `off`.
- `keywords`, `title` (a keyword unless `rules.title_key=false`), `depth` (None = next to the card), `role`.
- `sticky` (default 3), `chance`, `every`, `detached` (a free bubble, never sent), `parent_id`, `position`.
- `rules` is validated in `character_tree.py:_rules`: `keys2`, `logic`, `whole`, `case`, `scan`, `cooldown`,
  `delay`, `groups`, `main`, `weight`, `filter`, `no_wake`, `not_woken`, `place`.

### 4.2 Activation, in order (`select_blocks`)

1. Walk the tree. `off` or `detached` blocks and everything under them are skipped. The path is
   `Owner › … › Title`. Blocks under a green parent are "layered".
2. **Blue**: not layered, `(generation−1) % (every+1) == 0`, and `allowed` (`delay` ≤ generation; `filter`
   only/except by character ids present). Character trees take them for free. Lorebooks (`by_order`) `take()`
   them, which uses budget.
3. **Green candidates** (`candidates_in`), scanned over the last `scan_messages` (setting `retrieval.scan_messages`,
   default 3, or the block's own `rules.scan`):
   - **keyword**: `keyword_hits` counts title and keywords; the second keywords apply `logic`
     (and_any|and_all|not_any|not_all);
   - **meaning**: score ≥ `threshold` (default 0.55, or the vector service's own default);
   - **sticky**: found within the last `sticky` replies.

   `cooldown` blocks are skipped while cooling. A miss is held back while its hold lasts. Ranking: keyword hits
   (more first), then meaning, then sticky. Lorebooks (`by_order`) rank by tree order instead (book, then
   position).
4. `one_per_group`: of found blocks sharing a `rules.groups` name only one stays: one still held, else `main`,
   else a weighted draw.
5. `take()` adds a block and its blue sub-bubbles (`subtree`; green children wait for their own words) when they
   fit `budget_tokens`; otherwise the title goes to `dropped`. A fresh green block rolls `chance`; a failed roll is
   remembered as a miss.
6. **Chains**: `recursion` steps. The text of the blocks taken (except `no_wake`) plus `seed_text` is scanned for
   more green blocks (except `not_woken`), with keywords only. Lorebooks use the setting `lorebooks.recursion`
   (default 2) and seed it with the chosen character bubbles. Character trees use 0.
7. **Links** (`ctx.blocks.links`): one step to the linked bubble and its blue subtree.
8. Hits: `fresh_hits` and `misses` are written with `ctx.blocks.record_hits(chat, ids, generation)` into
   `chat_block_hits`.

**Russian endings**: `stem()` cuts a longest ending from `RU_ENDINGS` (English: `ing|es|ed|s`) when ≥3 letters
remain. `keyword_pattern` matches `stem\w*` per word. `/regex/flags` keywords are compiled as written.
`whole`/`case` change this. One-word keywords are counted by binary search over sorted words (`_Words`).

**Generation counter**: `chat.meta.generation` is incremented once per reply by
`character_tree.select_for_chat`. Lorebooks read the same number.

### 4.3 Meaning (vectors)

- `lorebooks.meaning_for_reply` → `character_tree.meaning_search(ctx, every_block, meaning_texts(settings,
  history), wanted)`.
- The texts are the last `meaning_messages` (default 3) messages of the chosen kinds (user / characters), each its
  own input, each cut to 4000 characters.
- One `provider.embed` call for all texts. A block's score is its best cosine over the texts.
- Only green blocks with a **current** vector are scored (`_fresh_vectors`: the hash of path+content must match).
  Bubbles are never embedded here; vectorizing is `character_tree.vectorize` / `schedule_auto_vectors`.
- No request is made when there is no embedding connection, search is off, or nothing is vectorized, unless
  `wanted` is set.
- On failure the error text reaches the page (`meaning_failed`) and the reply goes on by keywords.

### 4.4 Lorebook pool, budget, positions (`services/lorebooks.py:select_for_chat`)

- `active_books` = the chat's books, then each member's books (as a character or as «me»), then the global books,
  deduplicated (`lorebook_uses`). This order is the drop order: later books are cut first.
- Budget = `lorebooks.budget_tokens` (2000). When `budget_percent > 0` and the window is known, it is that percent
  of `max_context`. The default percent is 25 (ST's), so the window decides when it is known and `budget_tokens`
  is the fallback.
- Ranking (`core/retrieval.py:select_blocks`, `by_order`): by book (pool order), then `block_order` highest first
  (`rules.order`, else the imported `st_entry.order` / `insertion_order`, else 100), then the place in the tree.
  Constant and found entries are ranked together against the budget (`take`), so a constant with a low order can be
  cut before a keyword entry with a high one. `rules.no_budget` (`retrieval.ignores_budget`, ST «ignore budget»)
  costs nothing and is never cut.
- Outlet entries (`place == "outlet"`) are filtered out of the pool before selection.
- Character bubbles have their own budget, `retrieval.budget_tokens` (2000) **per member**, and blue ones are free.
- Positions in the prompt: see 3.2.

### 4.5 SillyTavern import mapping

**World file**: `services/lorebooks.py:import_book` → `formats/st_lorebook.py:entries_of` (sorted by
`displayIndex`, uid) → `block_from_entry`. The entry index becomes `position`.

| ST field | Ours |
|---|---|
| `key` / `comment` | `keywords` / `title` (`title_key=false` when there is a comment) |
| `constant`, `disable` | mode always / off, else search |
| `keysecondary` + `selective` + `selectiveLogic` (0 and_any, 1 not_all, 2 not_any, 3 and_all) | `rules.keys2`, `logic` |
| `position` 0 / 1 / 2 / 3 / 5 / 6 / 7 | card_before (default) / card_after / an_top / an_bottom / examples_top / examples_bottom / outlet |
| `position` 4 + `depth` | `depth` (default 4) |
| `role` 0/1/2 | system/user/assistant |
| `scanDepth, cooldown, delay, caseSensitive, matchWholeWords` | `scan, cooldown, delay, case, whole` |
| `group` (comma list), `groupOverride`, `groupWeight` | `groups, main, weight` |
| `excludeRecursion` / `preventRecursion` | `not_woken` / `no_wake` |
| `characterFilter {names, isExclude}` | `filter` (names → ids by character name) |
| `probability`+`useProbability` | `chance` (green only) |
| `sticky` | `sticky` (default 0 here) |
| everything | kept in `extra.st_entry`, written back by `entry_from_block` |

ST `order` → `rules.order` and `ignoreBudget` → `rules.no_budget` (written back by `entry_from_block`); `position`
7 → place `outlet`, kept and never sent. Roles outside position 4 are imported as `system`.

**A card's embedded `character_book`** uses a different function: `character_tree.py:blocks_from_book`. Sticky
defaults to 3 there, and `extensions.position==4` → depth (a missing or broken depth means ST's default 4, not
«next to the card»).
- `take_book` / `card_with_book` move a card's book in and out.
- `lorebooks.attach_card_world` switches on a book named in `extensions.world`.
- Export: `export_book` writes ST entries plus our whole tree (`tavern_tree`) for a lossless round trip.

---

## 5. Regex rules (ADR 0017)

- **Rule** (`core/regex.py:normalize_rule`): `{name, enabled, approved, find (/js/flags), replace, trim[], places:
  user|assistant|reasoning|lore, when: display|prompt|both|stored, min_depth, max_depth, run_on_edit, macros:
  none|raw|escaped, render_html, extra}`.
- JS patterns are translated to Python `re` with the JS meaning kept (`translate`). A pattern that cannot be
  translated is refused with a Russian message.
- **Which rules** (`services/regex.py:_owners`): global, then the chat's preset (unless built-in), then each
  member character and the chat's character. Within each scope, the user's order. Only `enabled and approved`
  rules work. Rules imported with a card or preset wait for «Разрешить» (`approve`); `waiting()` lets the page say
  so.
- **`applies(rule, place, depth, kind)`**: `when` must match (`both` = display+prompt). Depth 0 = the newest
  message, and the min/max depth is checked for everything except lore.

The four places:

| Place | Where | When |
|---|---|---|
| stored | `services/regex.py:stored`, from `chat.start_send`, `add_message`, `_writer.settle`, edits (`run_on_edit`) | once, when the text is saved. The original goes to `swipe_meta.regex_before`; `restore_original` undoes it. A result that is empty is ignored. |
| prompt | `prompt_texts`, from `_writer` before `PromptInput` | rewrites history message texts (by message id), old reasoning, and the content of chosen lore entries. The depth is the index from the end of the history, counted over the messages that are really sent (`prompt_texts` first drops those with `exclude_from_prompt`). Assembly then adds the name, thinking and macros around the rewritten text. |
| display | `display(ctx, chat_id, items)` from the page's request | page view only. `render_html` → a cleaned node tree (`core/safe_html.py`). Cached. With the setting `regex_live`, stored rules are also previewed on text being streamed. |
| try-out | `try_out` | any rule on any text, nothing saved |

- **Separate process**: `core/regex_runner.py:RUNNER.run(specs, items)` runs a worker (`core/regex_worker.py`)
  over a pipe.
- A rule that sends no word within 2 s (`TAVERN_REGEX_RULE_SECONDS`), or a job longer than 10 s, kills the worker.
  The rule is skipped and remembered as slow until edited (`_slow`), and the notice `regex_skipped` goes to the
  pages.
- Call it from a thread (`asyncio.to_thread`).
- **ST import**: `formats/st_regex.py:script_from_st`.
  - `placement` 1/2/5/6 → user/assistant/lore/reasoning.
  - `markdownOnly`+`promptOnly`: both → both, md → display, prompt → prompt, neither → stored.
  - `substituteRegex` 0/1/2 → none/raw/escaped.
  - Rules are found on cards (`data.extensions.regex_scripts`) and on presets (`extensions.regex_scripts`).
  - SillyTavern's own `settings.json` is read too: `st_regex.settings_scripts` takes its global scripts from
    `extension_settings.regex` (`scripts_from_file`); `regex.import_rules` is the entry point. A file with none is
    refused with a Russian message.
- **Waiting rules:** `services/regex.py:waiting` counts rules of this chat's owners that are on but not approved; the
  display summary returns `pending` and `pending_lists`, and the chat's top line shows «Регекс ждёт: N».

---

## 6. Presets, connections, params, keys, catalog

### 6.1 Presets

- `services/presets.py:resolve(ctx, chat)` returns `chat.meta.preset_id`, else the setting `active_preset`, else
  the built-in `default` (`core/presets.py:DEFAULT_PRESET`). A deleted preset falls back. The data is
  re-normalized on read (`normalize_preset`).
- `normalize_preset` checks:
  - unique ids, exactly one history block, at most one thinking block, one enabled block per group, one block per
    behaviour;
  - old `prompts.{no_write_for_me,speaker_reply,impersonate,narrator}` texts become behaviour blocks;
  - `prompts` keeps only `prefill, personality_format, scenario_format`;
  - `params`/`max_context_tokens` are validated but **unused**: model settings live on the connection.
    `move_model_settings` moves old ones once at startup, and an import returns `model_params` so the page can ask.
- A block's optional `order` (ST `injection_order`, default 100, only for `position: depth`) and the preset's
  `squash_system` are validated here (`normalize_block`, `normalize_preset`); how they act is in 3.4 and 3.5.

### 6.2 ST Chat Completion preset (`formats/st_preset.py:from_st`)

- The order comes from `prompt_order`: the entry with `character_id == 100001` (global), else the first, else the
  `prompts` list order.
- Markers: `main→main`, `jailbreak→post_history`, `chatHistory→history`, `dialogueExamples→examples`,
  `charDescription|charPersonality|scenario→one cast`, `personaDescription→persona`,
  `worldInfoBefore/After→lore_before/after`.
- Unknown markers are skipped. Non-marker prompts become `text` blocks.
- `injection_position==1` → `position: depth` with `injection_depth`. `role` is kept.
- Params: `temperature, top_p, top_k, frequency/presence_penalty, openai_max_tokens→max_tokens`. "Off" values are
  dropped, and so is `reasoning_effort` "auto". `openai_max_context` → `max_context_tokens`. All of these are only
  handed back as `model_params`.
- Texts: `impersonation_prompt→impersonate`, `group_nudge_prompt→speaker_reply` (both become behaviour blocks),
  `assistant_prefill→prefill`, `personality_format`, `scenario_format`.
- Unused keys are kept in `st` for export (`to_st`).
- `injection_order` → the block's `order`; `squash_system_messages` → `squash_system` (false when absent; it is
  not copied into `st`). The history block of an import gets `history.thoughts = 1`. `to_st` writes all three back.

### 6.3 Connections and providers

- A connection is `{id, name, kind, base_url, model, params, service}` (`storage/repos/connections.py`).
- `providers/registry.py:ProviderRegistry.create(connection)`:
  1. Pick the factory by `kind` (openai, gemini, anthropic, or a plugin's).
  2. Text mode: attach the template.
  3. Load the keys from secrets (`connection:<id>:keys`).
  4. Wrap them in `KeyedProvider`, then `FallbackProvider` when there are steps.
- **Params** (`providers/params.py:PARAMS`): every setting has an id, type, range, the kinds that take it and
  `send`. Sampling, repetition, length, thinking, tools, safety, `extra_body`; Tavern's own settings have
  `send=False`.
- **Catalog** (`providers/catalog.py:SERVICES`): known services with a ready base_url and kind, and the default
  vector threshold (`service_threshold`).
- `services/connections.py` handles the model lists and trial requests per setting.
- Instruct templates: `services/templates.py:resolve` (own and built-in), `core/templates.py:from_st/to_st`.

---

## 7. Memory++, notes, multi-character turns

### 7.1 Memory++ in the prompt (ADR 0019, `builtin_plugins/memory/`)

The plugin is off by default and on per chat (`store.is_enabled`). Its server part registers in `server.py`:
- `meaning.wanted` → `inject.wants_meaning`;
- `chat.before_generate` (priority 50) → `inject.contribute`;
- `prompt.built` → `inject.record_hits` (which chapters really went, for the sticky rule).

It makes no model call while a reply is built. It reuses `job.meaning` (the reply's one embedding request).

`contribute` adds, in this order:

| What | How it enters `PromptInput` | Where it lands |
|---|---|---|
| «Сюжет до сих пор» | `values["summary"]` (always), and when the window is known also `Extra(if_dropped=True)` | `{{summary}}`, and the memory slot when anything was left out |
| «Развитие» per member (M4, `layers.contribute`) | `Extra(place="after_card", owner, drop_rank=60)`; the user's own development without a «me» member goes to `place="memory"` | right after that member's card, dropped together with the member |
| «Сейчас:» lines (M4) | appended to `prompt.lore_rewritten[block_id]` | inside that lore entry's text |
| Vows (M5, `threads.contribute`) | `Extra(place="after_card" or "memory", drop_rank=58)` | after the speaker's card |
| Threads the speaker may know (M5) | `Extra(drop_rank=45)` | memory slot |
| Faces (M6, `faces.contribute`) | `Extra(drop_rank=40)` | memory slot |
| Documents (M8, `docs.contribute`) | `Extra(drop_rank=30)` | memory slot |
| Chapters / pieces (M2/M3) | `Extra(covers=(first,last), order, group)`, ranked, at most 40; `tail_from`, `memory_reserve = memory_percent (15%) of the window, ≥ memory_tokens` | memory slot, **only** when every covered message was cut, and in story order |

- Conditional extras (`covers` or `if_dropped`) are considered in the plugin's ranking and placed by `order`.
- `group` makes alternatives: a chapter's summary, else its pieces.
- With `max_context_tokens == 0` only the unconditional parts and `{{summary}}` are sent.
- Unconditional extras with `place="memory"` go to the slot whatever the window; under a short context they are
  dropped by `drop_rank`.

### 7.2 Author's notes (`services/notes.py`, `core/assembly.py`)

- `notes.for_reply`: enabled global, then character, then chat notes with text where `fires(interval,
  reply_number)`. Here `reply_number = 1 + non-user messages in the history` and the test is
  `number % (interval+1) == 0`. Each becomes `NoteInput{depth (default 4), role, mode}`.
- In assembly, each fired note is an injection at its depth. Its `placed` record goes to `record_placed` after the
  reply is saved:
  - `hidden` and `visible` notes are attached to the message before them (`message_notes`);
  - `once` notes are not kept.
- Attached notes come back through `notes.with_attached` as `message.notes`. They follow their message as
  `Entry.follow` and are dropped with it.
- Lore `an_top`/`an_bottom` sits at the first note's depth.

### 7.3 Multi-character turns (`services/speakers.py`, `services/chat.py`)

- Settings are in `chat.meta.speakers`: `order` (natural|turns|model|manual|all), `others`
  (none|names|blurbs|full), `talk_limit`, `together`, `narrator`.
- `speakers.choose`:
  - **turns**: the next member after the last speaker in list order (muted ones skipped).
  - **natural**: a name in the last message (`named`: the capitalised first letter plus case endings, not written
    by its own speaker), else a draw by `talkativeness`, excluding whoever wrote last.
- What the model knows:
  - `prompt_members` decides whose cards go in;
  - `speakers.scene` gives the names or blurbs of the others ("Also in the scene");
  - `several` → other characters' lines get a `Name: ` prefix, and the `speaker_reply` behaviour block is sent.
- **Turns** (`_start_turn`) emit `turn_start` and then loop until `replies` are written or nobody may speak. Each
  step is one of:
  - `_one`: one `_writer(final=False)`;
  - `_round` (order all): one empty reply per member who may speak, all written at once with `skip_ids` (each sees
    the chat up to the round). Then `_arrange` by `together`:
    - `finished`: in finishing order;
    - `list`: in list order;
    - `one`: joined into one message with `extra.together`.

    Then optionally `_narrate`: one system message by the narrator's connection and model.
    `speakers.narrator_prompt` uses the `narrator` behaviour block, not the preset assembly.
- `spending.guard` runs between steps.
- `done|stopped|error` carry `reload=True`.
- «Пусть поговорят» is `start_talk` → `_start_turn(replies=count)`.

---

## 8. Assistant engine and plugins (short)

- **Assistant** (ADR 0010, 0013, 0018, 0020): `services/assistant.py` (settings, conversations, limits, undo) and
  `services/assistant_engine.py`.
  - Small requests run a step loop of `generate_json` forms `{say, actions}`. Big work is a task: questions →
    plan → items (writer, task pinned) → critic (read-only) → report.
  - Each role can have its own model.
  - Tools are `services/assistant_tools.py`, the same services the UI uses. `services/assistant_app.py:app_call`
    runs the page's own API in process.
  - Undo: `storage/changelog.py` (TEMP triggers) and `storage/snapshot.py`.
  - Skills (10б): `services/skills.py`, `tavern/skills/*.md`.
- **Plugins** (ADR 0009):
  - `plugins/manager.py` finds plugins, checks approval by hash, and loads `setup(api)`.
  - Model access goes through `gate_for(owner)` (permission, own limits, «may it?» questions).
  - Plugin tables are `p_<id>_*` with their own migrations (`plugins/schema.py`).
  - Background jobs use `ctx.jobs` (`core/background.py`) and yield to user runs (`core/foreground.py`).
  - Web parts are run by `web/core/plugins.js`.
  - Reply writers: `api.add_generator` (example: `builtin_plugins/thoughts`, ADR 0011).

---

## 9. Where to look when…

| Symptom | Look at |
|---|---|
| A block or card is missing in «Что видела модель» | `core/assembly.py:assemble` (block `enabled`, kind, `prefer_card_prompts`); `services/cast.py:prompt_members` (others ≠ full drops cards); `Prompt.dropped` (budget) |
| A text lands at the wrong depth | `assemble` injection index `len(messages)-depth` over **sent** messages; the member's `depth`; the bubble's `depth`; the order at one depth is `(order, role, cat, sub)` (3.4) |
| Two system texts are merged or separate | the final loop of `assemble` (merging); then the provider: `gemini._contents`, `anthropic._messages` |
| A lorebook entry does not fire | `core/retrieval.py:select_blocks` (keyword_hits, stem, `rules`, cooldown, delay, filter, chance miss in `chat_block_hits`); `lorebooks.active_books`; the budget (`Selection.dropped` → `dropped.lore`) |
| Meaning search never works | `character_tree.meaning_search` (embedding connection, `_fresh_vectors` stale hash, `meaning_on`), the `meaning_failed` event |
| A bubble fires every time / never stops | `sticky`, `hits` in `storage/repos/blocks.py`; `chat.meta.generation` |
| The wrong character answers | `services/speakers.py:choose`, `named`, `name_pattern`; `chat._pick` |
| Wrong `{{macro}}` | `core/prompt.py:substitute`; the `values` built in `assemble` |
| Regex not applied | `services/regex.py:_owners` (scope), `approved`, `core/regex.py:applies` (when/places/depth); `_slow` (skipped as slow) |
| The model's thinking shows in the reply | `core/thinking.py:ThinkSplitter`; `split_thinking` set in `_writer`; the provider's reasoning fields |
| A request field is wrong or missing | `providers/params.py:request_params` (kinds, send); `*.build_body`; `extra_body` |
| Retries / next key / fallback | `providers/keys.py:KeyedProvider._failed`; `providers/fallback.py:FallbackProvider.stream` |
| Text-completion output looks garbled | `core/templates.py:render`, `detect`; `registry.create` (api_mode) |
| An empty reply disappears | `chat._writer.settle` (an empty one is discarded → `empty_reply`) |
| Spending is wrong | `services/spending.py:meter`, `price_for`, `Tally`; `LLMClient._charge` (estimates) |
| A note is not sent / sent twice | `services/notes.py:for_reply`, `fires`; `assemble` `saved_sources` check; `record_placed` |
| Memory is not in the prompt | `builtin_plugins/memory/inject.py:contribute` (enabled, window known, `tail`); the `assemble` conditional extras (`covers` all cut, `memory_reserve`), the memory block disabled |
| A run hangs or reports «busy» | `core/runs.py:RunManager`; `chat._ensure_idle` |
| An imported ST preset looks wrong | `formats/st_preset.py:from_st` (`_order`, MARKER_KINDS) |
| An imported ST world looks wrong | `formats/st_lorebook.py:block_from_entry`; card books: `character_tree.blocks_from_book` |
| A plugin changed the prompt | the `prompt.built` filter; the archive then shows «после плагинов» (`chat._writer.archive`) |
