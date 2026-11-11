# 0019. Memory++: chapters, summaries, character development and threads, as a built-in plugin

Status: accepted (2026-11-09). Stage M0 (architecture) 2026-11-09; M1 and M2 done 2026-11-09, M3, M4 and M5 done 2026-11-10, M6 and the combined M7 + M8 done 2026-11-11 (see «M1 done», «M2 done»,
«M3 done», «M4 done», «M5 done», «M6 done» and «M7 + M8 done» in section 7). Builds on 0004 (character tree, vectors), 0006
(lorebooks), 0009 (plugins), 0010 / 0013 (assistant), 0011 (tree of thoughts: an agentic built-in plugin),
and the foreground rule being built now (`tavern/core/foreground.py`).

Stages M1 to M6 are implemented so far. Sections 2 and 3 name the code to add; section 7 splits it into stages
M1–M8; section 8 lists risks and the owner's answers to the open questions (all decided 2026-11-09).

## 1. Context

Long roleplay chats outgrow the model's window. Today the history goes whole and is cut from the oldest end
when it does not fit (`core/assembly.py: assemble`, `drop_oldest`), and the `{{summary}}` macro is an empty
placeholder (`assembly.py`, `values["summary"] = ""`). The owner wants the best roleplay memory possible, built
as a **built-in plugin** that is switched on in «Дополнения», like `builtin_plugins/thoughts`.

The owner's decisions (2026-11-09), binding, in short:

- **Two levels (RAG).** The old part of a chat (later also a document) is cut into **chapters** by meaning. A
  chapter has a **summary** (about 1k tokens). Under it are **pieces** of the full text (200–400 tokens), each
  with a vector and keywords. The one meaning request per reply (`lorebooks.meaning_for_reply` →
  `character_tree.meaning_search`) scores both levels: when the summary matches, the summary goes; when a piece
  matches more strongly, that piece (± neighbours) goes. «Всю главу» sends the full chapter text instead.
  Keywords weigh as much as vectors (names and numbers are the details that get lost).
- **History first.** The chat's history still goes word for word while it fits the context, chapters or not.
  Memory is used **only for what no longer fits**: a chapter (or a piece) can be found only when all its
  messages were left out of the prompt, so nothing is sent twice. «Сюжет до сих пор» (300–500 tokens) goes
  whenever anything was left out.
- **Live tail**: the newest messages always go word for word: 35 % of the context, but never fewer than 10
  messages (both editable). Chapters are made in the background after replies, never during one.
- **Memory is per chat**: a switch «Память в этом чате», and a global «Включать в новых чатах».
- **Chat vectors** (the former stage В): every message except each speaker's latest (each character's last, the
  user's last); only the message text, never prompts, notes or thinking. The vectors belong to the chat and
  never go into export files.
- **Cutting into chapters.** A scene is one place, continuous time, the same active participants, one goal or
  conflict. A border is any change of these, or an explicit marker (`***`, «Глава», «Тем временем», «На
  следующий день»). Not a border: a shift of mood or topic inside one conversation, a short flashback, someone
  dropping in for one line, OOC. Default (no model): a score for every gap, only gaps before a user message.
  Optional small model (off by default): JSON `{"finished": false}` / `{"finished": true, "ends_at": 47,
  "reason": "..."}`. The user can move, split and merge borders by hand.
- **Summaries** need a (small) model: the chat's language, past tense, no meta. A failed summary leaves the
  chapter raw with a mark and is retried later.
- **Edits.** A changed or deleted message inside a summarized chapter marks it stale. Re-summarize by button, or
  automatically (checkbox, off). A hand-edited summary gets a lock. Chapter edits can be undone, a deleted
  chapter restored.
- **Branches and swipes.** A chapter belongs to its messages' path: chapters before a fork are shared, those of
  another branch are silent. Changing the swipe of an old message counts as an edit.
- **In the prompt**: found chapters in story order, marked «Ранее в истории: …», sticky for N replies, placed
  late (after the stable part, so provider prompt caching survives), with their own token budget apart from
  lorebooks, and all of it visible in «Что видела модель».
- **Group chats**: a member gets only the chapters they were in, plus «Сюжет до сих пор». A member added later
  does not get earlier chapters word for word. Per member in the chat: «Помнит всё» (every chapter, for a
  narrator-like character; there is no special narrator in memory) and «Не наслаивать» (no «Развитие» layer for
  that member).
- **Export**: summaries, chapter borders and open threads go into the chat file; vectors never.
- **Old and imported chats** are chaptered by a button, in the background, with progress, resumable after rate
  limits, with the price shown first; spending is recorded.
- Hidden messages (`exclude_from_prompt`) and thinking never go into chapters.
- **«Развитие персонажа»**: the card never changes. Per character, per chat and branch, a 300–600 token layer
  (relationships, what happened, new traits), updated by a small model when a chapter closes, compressing
  itself, sent right after the card and winning over it as "now", versioned with undo, editable by hand with a
  lock, and carried to another chat by a button. Also for the user's own persona.
- **Lorebooks**: no new entities; an event adds a «Сейчас: …» line to an existing bubble. The line lives in the
  chat and branch and is added to the bubble's text only when that bubble is sent; the lorebook itself never
  changes. A button «Записать в лорбук насовсем» writes it in for good.
- **«Слово и нити»** (off, needs a small model): promises, vows and principles, claims about oneself or the
  world, debts and agreements, secrets told (who knows what), quests (own toggle, off). A record holds who, to
  whom, a quote with a link to the message, and a status (в силе / выполнено / нарушено / снято). It goes only
  into that character's replies: vows always, the rest when relevant. Also for the user's persona.
- **Side characters without a card**: tracked as **mini-cards** (flat, short, never a bubble tree), usable in
  every chat; creating them is off by default. A button «Сделать настоящим персонажем» turns one into a card.
- **The assistant** (🪄) sees and edits every layer and can press «Векторизовать». It creates mini-cards only
  after asking the user.
- **Background work never runs beside the user's requests**: a request starts, background work stops at once
  (the request in flight is cancelled), progress is kept, and it resumes afterwards. Setting «Прерывать фоновую
  работу при запросе», default on.
- Every heavy part is **off by default**. The basic form works with no chat model at all (chapters by script,
  chat vectors and keywords, full-text pieces). Summaries, development and threads need a model.
- Termux: pure Python, no compiled dependencies. Phone UI: 44 px targets, no native `<select>`.

## 2. Plugin and core: where the line goes

### 2.1 The plugin

`tavern/builtin_plugins/memory/` (id `memory`, name «🧠 Память++»):

```
plugin.json        settings (section 6.3), uses: ["models", "ui", "storage"]
server.py          setup(api): registers everything below, nothing else
store.py           SQL over the plugin's own tables (section 3)
pieces.py          cutting messages into pieces, keywords (pure)
borders.py         gap scores, TextTiling, chapter closing rules (pure); the optional model judge
search.py          scoring pieces and summaries with the reply's query vectors and keywords (pure)
inject.py          the prompt contribution: tail, plot, chapters, development, threads, mini-cards
work.py            the background job per chat: vectorize → borders → summaries → plot → development →
                   threads; each step through the foreground gate
prompts.py         texts for the small-model tasks (Russian-aware, JSON forms for core/structured.py)
actions.py         the web part's and the assistant's actions (list / edit / split / merge / lock / undo ...)
transfer.py        export / import of its data with chat files; «перенести развитие в другой чат»
migrations/        0001_memory.sql ... (section 3), applied by the new plugin schema API (2.2-B)
skills/memory.md   a skill so the assistant knows how to work with chapters (plugins' skills already load:
                   services/skills.py)
web.js, style.css  the «Память» window, chapter dividers in the chat, progress chip
```

Everything that decides *what* memory is (pieces, borders, scoring, summaries, layers, threads) lives here. The
core gets only general extension points that any plugin can use; memory is their first user, as `spelling` was
for `t.composerHelper`.

### 2.2 Extension points: what exists, what is missing

Legend: **exists** = usable as is; **extend** = small change to something that exists; **new** = to add.

**A. Background jobs that yield to the user (new, on top of `core/foreground.py`).**
The foreground rule exists in the working tree (`core/foreground.py: Foreground`, `enter` / `leave` / `busy`,
`run_background(factory)`, `wait_idle`). `RunManager.start` enters it for every run (`core/runs.py`), so every
chat reply and every assistant request is covered. `ctx.foreground` is a property on `AppContext`. The setting
is `background_yield` (default on), and `character_tree._embed_and_save` already sends its batches through
`run_background`. What is missing is a *job* a plugin can own: one per key, progress kept, visible to the page,
removed when the plugin is switched off. Today `character_tree` keeps its own `_jobs` / `_auto_tasks` dicts by
hand. Add `tavern/core/background.py`:

```python
class Jobs:                                   # ctx.jobs, one per AppContext
    def schedule(self, owner: str, key: str, work: Callable[[JobContext], Awaitable[None]],
                 *, delay: float = 2.0) -> None
        # one job per (owner, key); scheduling again while it waits or runs only sets "run once more"
    def status(self, owner: str, key: str) -> dict   # {running, paused (=foreground.active), done, total,
                                                     #  note, error}
    def cancel(self, owner: str, key: str | None = None) -> int

class JobContext:
    async def step(self, factory: Callable[[], Awaitable[T]]) -> T   # = ctx.foreground.run_background
    def progress(self, done: int, total: int, note: str = "") -> None # emits notice "job"
    async def rate_wait(self, seconds: float) -> None                 # 429 sleep, counted toward a cap
    stopped: bool
```

Plugin API: `api.job(key, work, delay=2.0)` and `api.job_status(key)`. Jobs are cancelled in `_unload`
(`manager.py`). Rule for job authors, the same as `run_background`: a step must be safe to redo; keep a result
only after the step returns. Memory's job is **todo-driven** (each pass asks the tables "what is next?"), so a
restart, a crash or a cancelled step loses nothing. `character_tree.start_vectorize` / `schedule_auto_vectors`
may move onto `Jobs` later (not part of memory).

**B. Plugin-owned tables (new).** `plugin_data` (`migrations/0014_plugins.sql`, `PluginStorage` in
`plugins/api.py`) is a JSON key-value store per chat. That is fine for settings and small state, but not for
thousands of pieces with vectors, indexes, foreign keys to `messages`, or cascades. Add:

- Core migration **0024_plugin_schema.sql**: `ALTER TABLE plugins ADD COLUMN schema_version INTEGER NOT NULL
  DEFAULT 0`.
- `plugin.json` key `"migrations": "migrations"`: a folder of `NNNN_name.sql`, the same rules as the core
  (`storage/migrations/__init__.py`: contiguous, one transaction each, never edited once shipped).
- Applied by `PluginManager._load` before `setup(api)`: pending files run in one transaction each, then
  `schema_version` is stored. Every name a file creates (tables, indexes, triggers) must start with
  `p_<id>_`. The manager compares `sqlite_master` before and after; a stray name rolls the file back and
  leaves the plugin off with the error shown.
- Tables may `REFERENCES chats(id) / messages(id) ON DELETE CASCADE`. Deleting a chat or a message then
  cleans them up, and `storage/snapshot.py` (which follows foreign keys) brings them back on the assistant's
  undo.
- «Удалить вместе с данными» (`manager.delete(data=True)`) drops the `p_<id>_*` objects.
- A database snapshot is already taken before core migrations (`storage/backup.py`). Plugin migrations take one
  too, through `services/backups.before("plugin-migration")`.
- `storage/changelog.py` watches every table not skipped, so plugin tables are watched automatically and
  assistant edits become undoable. Vector tables must not be watched: extend `SKIP_TABLES` with a rule that
  skips names ending in `_vectors` (core `block_embeddings` is already skipped by name).
- `services/doctor.py` gets one check: `schema_version` matches the folder.

**C. Chat events (new).** Today only `message.received`, `chat.before_generate`, `prompt.built`,
`generation.failed`, `chat.members_changed` and the block events exist (`core/generation.py` docstring,
`services/cast.py`, `services/character_tree.py: _changed`). Edits, deletes, swipe changes and branch switches
happen in `api/chats.py` (`edit_message`, `delete_message`, `select_swipe`, `select_branch`, `set_roles`) and
call the repo directly, with no event. Add a thin `services/messages.py` that the routes call, and that emits:

| event (action) | payload | fired by |
|---|---|---|
| `message.added` | message, chat_id | `chat.add_message`, the user's message in `start_send` (via a helper, not by editing the reply body) |
| `message.edited` | message, chat_id, before (text) | edit_message (content) |
| `message.excluded` | message, chat_id, excluded | edit_message (exclude_from_prompt) |
| `message.deleted` | message (row before), chat_id, children (moved up) | delete_message, deletes by numbers |
| `message.swipe_selected` | message, chat_id, before_idx | select_swipe, regenerate (new swipe) |
| `chat.branch_selected` | chat_id, leaf_id, before_leaf | select_branch |
| `chat.imported` | chat_id, id_map | api/jsonl.py import_chat (see E) |

The assistant's `app_call` goes through the same routes (`services/assistant_app.py`), so it fires them too.
Memory needs the events only to *wake* its job and mark chapters stale quickly. Correctness never depends on
them, because staleness is also checked by content hash (section 3.2).

**D. Prompt contribution with placement, budget and the archive (extend + new).**
Already there: `chat.before_generate` (action, gets the `GenerationJob` with `job.prompt_input` filled, before
any generator runs, so the default writer, `thoughts` and parallel requests all see the same input), and
`prompt_input.lore_rewritten` (text override per lorebook block id, used by regex rules). `prompt.built` is
too late: it filters finished `ChatMessage`s, so it has no budget, no place and no archive entry. Add to
`core/assembly.py`:

```python
@dataclass
class Extra:                       # a plugin's piece of the prompt
    text: str
    source: str                    # archive key, "plugin:memory:chapter:<id>" (versions viewable as today)
    label: str                     # shown in «Что видела модель»: «Ранее в истории: Глава 3. В таверне»
    place: str = "memory"          # "memory" | "after_card" | "card_before" | "card_after" | "depth" | "an_top"
    owner: str = ""                # character id: for "after_card" (that member's card), and for trimming
    depth: int = 0                 # for place "depth"
    role: str = "system"
    kind: str = "memory"           # Entry.kind, for the viewer's grouping
    drop_rank: int = 50            # trimmed under the context limit before cards (lower goes first)
    covers: tuple[str, str] | None = None   # first, last message id: sent ONLY if all of them were left out
    if_dropped: bool = False       # sent only if any history message was left out («Сюжет до сих пор»)

PromptInput.extras: list[Extra]               # filled by plugins in chat.before_generate
PromptInput.extras_dropped: list[str]         # labels a plugin left out (its own budget): shown as dropped
PromptInput.tail_from: str | None             # message id: the protected live tail starts here
PromptInput.memory_reserve: int               # tokens kept for conditional extras when history overflows
```

- A new preset block kind **`memory`** (`core/presets.py: KINDS`) marks where `place="memory"` entries go. The
  default preset gets it just before `history`. Presets without it put memory entries just before the first
  sent history message, which is the same spot.
- `after_card` entries go right after their member's card (inside `place_blocks`, `assembly.py`), so
  «Развитие» follows the card and is cut together with that member when the context is short.
- **History first, memory for the rest** (owner, Q2). `assemble` builds as today with the whole history. If it
  does not fit, the oldest messages are left out until the total plus `memory_reserve` fits, never past
  `tail_from` (only the old `drop_oldest(1)` last resort may go further). Then the conditional extras are
  added in the plugin's order while they fit the reserve: an extra with `covers` only when every message in
  that range was left out, one with `if_dropped` only when anything was. When everything fits, no conditional
  extra is sent and the prompt is exactly today's. The prompt record shows «Оставлено за окном: n сообщений»
  and which memory entries replaced them.
- `{{summary}}` gets the «Сюжет до сих пор» text when a plugin sets `PromptInput.values["summary"]` (a new
  dict merged into `values` in `assemble`).
- Order inside one place: the order of `extras`, so the plugin sets story order itself.
- Trimming: in `assemble`, after `drop_oldest` reaches `tail_from`, unconditional extras are dropped by
  `drop_rank` before member cards.
- What was really sent is in `job.prompt` (its entries' sources), so a plugin learns which conditional extras
  went (for sticky hits) in its `prompt.built` handler.

**E. Chat file export and import hooks (new).** `formats/jsonl.py` has a fixed `RECORD_TYPES` and refuses
unknown types. `api/jsonl.py: import_chat` builds `id_map` (old message id → new) but nobody else sees it. Add:

- Record type `"plugin"`: `{"plugin": "memory", "version": 1, "data": {...}}`. `import_chat_jsonl` returns
  `plugins: [...]`.
- `api.on_export(fn(chat_id) -> dict | None)` and `api.on_import(fn(chat_id, data, id_map))`, called by
  `api/jsonl.py`. The import runs after messages exist and before `chat.imported` fires. A plugin that is off
  at import time gets its data kept in `plugin_data` under `import:<plugin>` and is offered it when switched on.
- Older Tavern builds would refuse a file that has a `plugin` line. Bump the header to `version: 2` and say so
  in the change log (risk R7).

**F. One meaning request per reply, shared (extend).** `lorebooks.meaning_for_reply` returns only block scores,
and `character_tree.meaning_search` throws the query vectors away. Worse, it makes **no request** when no
block has a fresh vector, which is the normal case for a chat without lorebooks. Change:

```python
@dataclass
class Meaning:                      # services/character_tree.py
    scores: dict[str, float]        # block id -> best cosine (as now)
    queries: list[array]            # the query vectors, one per meaning text
    texts: list[str]
    model: str
    error: str | None

meaning_for_reply(...) -> Meaning                        # callers in services/chat.py use .scores / .error
GenerationJob.meaning: Meaning | None                    # set by the chat service, read in chat.before_generate
```

The request is made when blocks have fresh vectors **or** a plugin asks for one. That is a filter event
`meaning.wanted` (`value=False, chat=chat`), answered `True` by memory when the chat has vectorized pieces.
The two call sites are in `services/chat.py` (`_writer` body, near `meaning_for_reply`), which another agent is
editing now. This change waits until that work is merged (M1, one agent).

**G. Embeddings for plugins (new).** `api.llm` gives chat models only. Add
`api.embed(texts, *, chat_id) -> list[list[float]]` on the global embedding connection
(`character_tree.embedding_connection`). It is metered as `plugin:<id>` through `spending.record_call`,
checks the plugin's day and month limits (the money part of `manager.gate_for`, split out), and raises
`AppError("no_embedding_connection")` when none is chosen. `api.vectors_model()` gives the current model name,
so stored vectors are keyed by model like `block_embeddings`. Batch sizes and 429 handling come from
`character_tree._batches` / `_embed_and_save`, moved to a small shared `services/embeddings.py`.

**H. Model access in the background (extend).** `gate_for` (`plugins/manager.py`) checks permission,
per-reply count, money limits and "ask each time". "Ask each time" makes no sense for unattended work, and
`ask()` answers *no* when no page is open. Add `api.llm(..., background=True)`. With "ask" on, such a client
asks once per job, not per request, and with no page open the job pauses with the note «ждёт разрешения»
instead of failing. Per-role connections and models come from settings of type `connection` + `text`, which
already exist (`manifest.FIELD_TYPES`).

**I. «Что видела модель» (exists, small extend).** Any `Entry.source` string is archived with versions
(`storage/prompt_log.py: record`, `versions(source)`), so `plugin:memory:...` sources work as they are. The
viewer (`web/components/PromptView.js`) needs only a label group for `kind == "memory"` and the
`dropped.memory` / `extras_dropped` lines.

**J. Assistant tools (exists, extend).** The assistant can already call any plugin action through `app_call`:
`POST /plugins/{id}/actions/{name}` is open (`assistant_app.py`, the `ALWAYS_CLOSED` / `/plugins` rules). Its
writes to `p_memory_*` tables are journaled by `changelog.Recorder` (thanks to B), so undo works. What is
missing is discovery and safety marks:
`api.action(name, fn, *, describe: str = "", reads: bool = False, confirm: str = "")`. `app_routes` lists
plugin actions with `describe`; `reads=True` skips the journal; `confirm` makes the assistant ask, as DELETE
does. The plugin's `skills/memory.md` teaches the workflow.

**K. Web part: places for a real window and marks in the chat (new).** `t.panel` shows a read-only text tree
(`web/components/PluginTree.js`), and `t.messageFooter` gives one text line. Memory needs an editable list,
text areas, toggles and confirm dialogs. Add to `web/core/plugins.js`:

- `t.window({icon, title, render(ui)})`: a top-bar or menu button opening a draggable window (a full-height
  sheet on phones, the same frame as the assistant window). `render` gets
  `ui = {html, useState, useEffect, Select, Icon, confirm, toast}` from the page's own vendored Preact + htm,
  `components/Select.js` and `components/Dialogs.js`. The plugin draws its own content with the app's parts and
  the 44 px rules. (Web parts already run with full page rights; this only hands them the shared parts so they
  do not bring their own.)
- `t.messageDivider(message => {label, title, state, onClick} | null)`: a thin line *above* a message
  (chapter borders). Plain text only.
- `t.messageMenu({label, onClick(message), show(message)})`: an item in a message's «⋯» menu («Новая глава
  отсюда»). `t.messageButton` exists, but the owner's phone has no room for more buttons on each message.
- `t.chip({text, title, onClick} | null)` via `t.status(fn)`: a small chip in the chat's top line for job
  progress («Память: 12/40 · пауза»).

**L. Settings UI (exists).** The plugin's settings form in Settings → «Дополнения» comes from `plugin.json`
(bool, number, text multiline, choice, connection, list, action). That is enough for section 6.3. Per-chat
settings live in the «Память» window (K) and are stored in the plugin's tables.

**M. Spending (exists).** Model requests through `api.llm` are metered as `plugin:memory`. Vectors go through
`api.embed` (G). The bulk price estimate uses `spending.price_for(ctx, connection_id, model)` and
`core/prompt.estimate_tokens`.

**N. Lorebook «Сейчас:» lines (exists).** `PromptInput.lore_rewritten[block_id]` is the per-reply text of a
lorebook entry (regex rules write it, `assembly.found_entry` reads it). Memory appends its line there, on top
of what a regex rule wrote, only for entries already chosen for this reply. No new core API is needed; see
decision Q5: the line lives in the chat and branch, and «Записать в лорбук насовсем» calls
`character_tree.update_block` (a normal history step with undo).

## 3. Data model (plugin migrations `builtin_plugins/memory/migrations/`)

Every table is `STRICT`, prefixed `p_memory_`, and cascades from `chats` or `messages`. Ids come from
`util/ids.new_id`.

### 3.1 Branches without copying

A chat is a tree (`messages.parent_id`, `chats.leaf_id`, `ChatRepo.active_path`), and the active path is a
single chain from the root to the leaf. So:

- A **chapter** is stored by its first and last message ids. It belongs to the current path when **its last
  message is on the path** (all its ancestors then are too) and its first message is an ancestor of the last.
  Chapters before a fork are shared by every branch. A chapter of another branch is silent with no extra rule.
- Things that change over time (the plot, development, thread status, «Сейчас:» lines) are **versions
  anchored at a message** (`anchor_id`, normally the last message of the chapter that produced them). The
  value on a path is the newest version whose anchor is on the path. Undo and branch switches then cost
  nothing, and a branch keeps its own development.

`ChatRepo.active_path` already returns the ids. A per-reply `set(ids)` gives O(1) membership.

### 3.2 Tables

```sql
-- 0001_memory.sql
CREATE TABLE p_memory_chats (          -- per-chat switches and state
    chat_id      TEXT PRIMARY KEY REFERENCES chats(id) ON DELETE CASCADE,
    enabled      INTEGER NOT NULL DEFAULT 0,      -- «Память в этом чате»; new chats take «Включать в новых чатах»
    settings     TEXT NOT NULL DEFAULT '{}',      -- per-chat overrides: tail_percent, tail_min...
    updated_at   TEXT NOT NULL
) STRICT;

CREATE TABLE p_memory_pieces (         -- full text, cut along message boundaries (long messages split)
    id           TEXT PRIMARY KEY,
    chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    first_id     TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    last_id      TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    part         INTEGER NOT NULL DEFAULT 0,      -- n-th part of a long single message
    text_hash    TEXT NOT NULL,                   -- of the text as cut: a change makes the piece stale
    tokens       INTEGER NOT NULL,
    keywords     TEXT NOT NULL DEFAULT '[]',      -- stems (core/retrieval.stem), names, numbers
    created_at   TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_pieces_chat ON p_memory_pieces(chat_id, last_id);

CREATE TABLE p_memory_vectors (        -- never exported, never journaled (changelog skips *_vectors)
    item_id      TEXT NOT NULL,                   -- a piece id or a summary id ("s:" + chapter id)
    chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    model        TEXT NOT NULL,
    text_hash    TEXT NOT NULL,
    vector       BLOB NOT NULL,                   -- array('f'), normalized, as block_embeddings
    PRIMARY KEY (item_id, model)
) STRICT;

CREATE TABLE p_memory_chapters (
    id           TEXT PRIMARY KEY,
    chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    first_id     TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    last_id      TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    title        TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT 'raw',     -- raw | summarized | stale | failed | deleted
    content_hash TEXT NOT NULL,                   -- hash of (id, active swipe, text) of its messages
    participants TEXT NOT NULL DEFAULT '[]',      -- character ids in the scene (incl. the user's persona)
    whole        INTEGER NOT NULL DEFAULT 0,      -- «Всю главу»: send full text when found
    border_by    TEXT NOT NULL DEFAULT 'script',  -- script | model | hand
    reason       TEXT NOT NULL DEFAULT '',        -- why the border is here (shown in the window)
    tries        INTEGER NOT NULL DEFAULT 0,      -- failed summary attempts (backoff)
    created_at   TEXT NOT NULL, updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_chapters_chat ON p_memory_chapters(chat_id, last_id);

CREATE TABLE p_memory_texts (          -- every model- or hand-written text, versioned
    id           TEXT PRIMARY KEY,
    chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,      -- summary | plot | development | now_line
    target       TEXT NOT NULL,      -- chapter id | '' (plot) | character id | lorebook block id
    anchor_id    TEXT REFERENCES messages(id) ON DELETE SET NULL,  -- branch membership (3.1)
    text         TEXT NOT NULL,
    by           TEXT NOT NULL,      -- auto | hand | assistant | import
    locked       INTEGER NOT NULL DEFAULT 0,      -- hand edit: auto never replaces it
    current      INTEGER NOT NULL DEFAULT 1,      -- 0 = an older version kept for undo
    created_at   TEXT NOT NULL
) STRICT;
CREATE INDEX p_memory_texts_target ON p_memory_texts(chat_id, kind, target, created_at);

CREATE TABLE p_memory_hits (           -- sticky: when a chapter last went into a reply
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    chapter_id TEXT NOT NULL REFERENCES p_memory_chapters(id) ON DELETE CASCADE,
    hit_gen INTEGER NOT NULL, what TEXT NOT NULL,  -- summary | pieces:<ids> | whole
    PRIMARY KEY (chat_id, chapter_id)
) STRICT;

CREATE TABLE p_memory_members (        -- per member of a chat (Q4)
    chat_id      TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    character_id TEXT NOT NULL,
    remembers_all INTEGER NOT NULL DEFAULT 0,     -- «Помнит всё»: every chapter, not only their own
    no_layer     INTEGER NOT NULL DEFAULT 0,      -- «Не наслаивать»: no «Развитие» for this member
    PRIMARY KEY (chat_id, character_id)
) STRICT;

-- 0002_threads.sql (M5)
CREATE TABLE p_memory_threads (
    id         TEXT PRIMARY KEY,
    chat_id    TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,      -- promise | vow | claim | debt | secret | quest
    who        TEXT NOT NULL,      -- character id (or the persona's)
    to_whom    TEXT NOT NULL DEFAULT '',          -- character id, '' = everyone / the world
    knows      TEXT NOT NULL DEFAULT '[]',        -- secrets: who knows
    quote      TEXT NOT NULL,
    message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    keywords   TEXT NOT NULL DEFAULT '[]',
    by         TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE p_memory_thread_states (  -- status changes are anchored, so branches differ (3.1)
    thread_id  TEXT NOT NULL REFERENCES p_memory_threads(id) ON DELETE CASCADE,
    anchor_id  TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,      -- active | kept | broken | dropped
    note       TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, anchor_id)
) STRICT;

-- 0003_faces.sql (M6): side characters, app-wide
CREATE TABLE p_memory_faces (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]',
    card TEXT NOT NULL,            -- flat text, about 100-200 tokens; never a bubble tree
    favourite INTEGER NOT NULL DEFAULT 0, by TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
) STRICT;
CREATE TABLE p_memory_face_chats (     -- where a face appeared and what it is "now" in that chat
    face_id TEXT NOT NULL REFERENCES p_memory_faces(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    first_message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
    PRIMARY KEY (face_id, chat_id)
) STRICT;
```

Notes:

- **Staleness by hash.** A chapter's `content_hash` covers its messages in path order: id, active swipe
  index, and text. On read (and when an event wakes the job), a mismatch makes it `stale`. A deleted first or
  last message cascades the chapter away. That is why "restore a deleted chapter" works through the version
  rows: before a cascade, a `message.deleted` handler moves the chapter's ends to the neighbours and marks it
  `stale`, so the chapter survives. If even that fails, the `texts` rows keep its summary.
- **Undo.** Chapter row changes made by hand are kept as `p_memory_texts`-style snapshots in
  `plugin_data` (`undo:<chat>`, the last 50 steps), and the window's «↶» reverses them. The assistant's changes
  are undone by its own journal (2.2-B, J).
- **Pieces and the open part.** Pieces are made as messages arrive, never crossing a chapter border. A piece
  that straddles a new border is cut in two and both are re-embedded; it is small. A piece containing the
  latest message of any speaker is not embedded yet (owner's rule): that message may still be regenerated or
  edited.
- **Excluded from pieces and chapter text:** `exclude_from_prompt`, `hidden`, `owner != ''` (plugin service
  messages), and thinking (`swipe_meta.reasoning` is never read). The text is the active swipe's content.
- **Export** (2.2-E) carries chapters (borders as message ids, title, state, whole, participants, reason),
  current and locked `texts` (summaries, plot, development, now-lines, with anchors), threads with states, and
  the faces that appear in this chat. It never carries vectors, pieces (rebuilt by script), hits or undo. On
  import, message ids go through `id_map` and pieces and vectors are rebuilt by the job.

## 4. Flows

### 4.1 Per reply (inside the user's run, no model call of its own)

In `chat.before_generate(job)`, only when memory is on for the chat:

1. **Tail.** Walk `job.history` back (skipping excluded messages), adding `estimate_tokens` until
   `tail_percent` × `job.max_context_tokens` (default 35 %) and at least `tail_min` messages (default 10); set
   `prompt_input.tail_from`. With no window known, the whole history goes as today and only the unconditional
   parts (development, vows, threads, faces) are added. Set `memory_reserve` to the memory budget (step 6).
   The plugin does not decide which old messages fit: the assembler does (2.2-D). The plugin offers
   candidates, each with `covers`: every chapter on the path that is not `deleted` and ends before the tail,
   and pieces of old messages that are in no chapter yet (the job lags, e.g. right after an import), so those
   can still be found.
2. **Query.** Use `job.meaning.queries` (2.2-F: the one embedding request already made for bubbles and
   lorebooks). No new request. The keyword query is the stems of the same last `meaning_messages` texts
   (`character_tree.meaning_texts`) plus the names of chat members.
3. **Score** (pure, `search.py`), over the candidate chapters (only those whose messages end up left out are
   sent):
   - vector part of an item: `max(cos(q, v) for q in queries)`, mapped to 0..1 above the same per-service
     threshold the bubbles use (`retrieval_settings(ctx)["threshold"]`);
   - keyword part: the sum of IDF weights of matching stems (IDF over this chat's pieces; numbers and
     capitalised names get ×2), mapped to 0..1;
   - item score = `max(vec, kw)`, plus 0.25 × the smaller one, so "as much as vectors" means either alone can
     find an item, and both together rank higher;
   - per chapter: `S` = summary score, `P` = the best piece score. When `P > S + margin` (0.1), the pieces go
     (best piece ± 1 neighbour inside the chapter); otherwise the summary. A chapter with no summary yet (raw,
     failed, or summaries off) offers only pieces; with `whole = 1`, the full chapter text goes when found.
   - The cost is pure-Python dot products over normalized `array('f')` vectors (risk R1).
4. **Sticky.** A chapter found in reply `g` stays until `g + sticky` (default 3) in the same form
   (`p_memory_hits`, the chat's `meta.generation` counter that `character_tree.select_for_chat` already
   increments), so the prompt does not flicker between replies.
5. **Group filter** (Q4). When the chat has two or more characters (`speakers.several`), a chapter goes only if
   the speaking member (`member.primary`) is in its `participants`, unless that member has «Помнит всё»
   (`p_memory_members`). «Сюжет до сих пор» goes to everyone. A member added later is not in the earlier
   chapters' participants, so they do not get them word for word. A narrator is an ordinary character here:
   give it «Помнит всё». The user's persona sees everything.
6. **Budget.** Own budget `memory_tokens` (default 1500) or `memory_percent`, apart from lorebooks, passed as
   `memory_reserve`. Candidates go to the assembler ranked: «Сюжет до сих пор» first, then chapters by score;
   the assembler takes those whose messages were left out while the reserve lasts and names the rest as
   dropped.
7. **Emit extras** (2.2-D):
   - `place="memory"`: «Сюжет до сих пор» (`plugin:memory:plot`, `if_dropped`), then candidate chapters
     **in story order**, each as «Ранее в истории: Глава N. <title>» + summary, piece or whole text
     (`plugin:memory:chapter:<id>` / `...:piece:<id>`, `covers` = its messages); then relevant threads (non-vow)
     and mini-cards of side characters named in the scan text (unconditional).
   - `place="after_card", owner=<character id>` for each member in the prompt without «Не наслаивать»:
     «Сейчас (развитие): …» (`plugin:memory:dev:<character id>`), and that character's vows always («Клятвы и
     принципы …»).
   - `lore_rewritten[block_id] += "\nСейчас: …"` for lorebook entries already chosen (2.2-N); the lorebook
     row is never changed.
   - `values["summary"] = plot` for presets that use `{{summary}}`.

Everything is read from the database in one pass. With memory off for the chat, the handler returns at once.

### 4.2 Background (after a reply, and after edits): `work.py`

`message.received`, `message.*` events and `chat.branch_selected` call `api.job(f"chat:{chat_id}", work)`.
The job waits for `delay`, then loops. Each step is the smallest unit of work, wrapped in `job.step(...)`, so
a user request cancels it at once and it is redone later (2.2-A). The job always works on the *current*
active path.

1. **Check.** Recompute `content_hash` of chapters on the path and mark mismatches `stale`. Re-cut pieces
   whose `text_hash` changed. No model, no network.
2. **Vectorize** pieces (and summaries) without a current vector, except pieces holding a speaker's latest
   message. Batches as in `character_tree._batches`, one batch per step, 429s waited out with `rate_wait`, and
   the job paused with a note after `MAX_WAIT`. Needs an embedding connection, otherwise the step is skipped
   (keywords still work).
3. **Borders** on the open part (after the last chapter), script by default (`borders.py`):
   - gaps scored only **before a user message**;
   - strong: an explicit marker at the start of a message (`***`, `---`, «Глава», «Часть», «Тем временем»,
     «Позже», «На следующий день», «Утром», «Через …»); time words at the start of a message;
   - medium: cast change (chat members' names and lorebook people/places mentioned in the window before vs
     after, Jaccard < 0.3); a cohesion valley (TextTiling over `retrieval.stem` words, window of 4 messages,
     depth score); vector drift (1 − cos of the mean piece vectors of the windows before and after), only when
     those vectors exist;
   - weak: a real-time pause (`created_at` gap > 6 h);
   - not a border: a gap whose message is OOC (`((…))`, `OOC:`), or a one-message cameo (a name appears in
     one message only).
   - Close a chapter when the open part has more than `N` messages (default 40, setting) and the best gap
     scores above the threshold; a forced cut at `3N` at the best gap. Several chapters can close in one pass
     (bulk). With «Границы определяет модель» on, the script proposes candidate gaps, and the small model gets
     the open part with message numbers (`LLMClient.generate_json`, schema
     `{"finished": bool, "ends_at": int?, "reason": str?}`, core/structured.py) and has the last word.
   - Participants = authors of the chapter's messages (`messages.character_id`) + the persona, plus the
     model's list when a model is used. Muted members who were present but silent are not participants unless
     they have «Помнит всё».
4. **Summaries** (needs a model, setting on): chapters `raw`, `failed` (backoff by `tries`), or `stale` when
   «Пересказывать устаревшие сами» is on, and never `locked`. One request per chapter. Input: the chapter
   text with speaker names; the previous chapter's summary as context. Output JSON
   `{"title": str, "summary": str}`; the summary in the language of most of the chapter's text (Q11), past
   tense, no meta, about
   `summary_tokens`. Saved as a new `texts` version (the old one becomes `current = 0`).
5. **«Сюжет до сих пор»** (setting on): after each new summary, old plot + new summary → a new plot of
   300–500 tokens, anchored at the chapter's last message.
6. **Development** (setting on): for each participant of the closed chapter (persona included) without «Не
   наслаивать»: old layer +
   the chapter summary (or text) + the card's short description → a new layer ≤ `dev_tokens`, compressing
   itself, anchored. Same request (JSON field `now_lines: [{block_id, line}]`): «Сейчас:» lines for lorebook
   entries the chapter mentions (ids from the chat's books with keywords that hit). Skipped when locked.
7. **Threads** (setting «Слово и нити» on): one request per chapter with the chapter text and the active
   threads list: JSON `{"new": [...], "changes": [{"id", "status", "message_no"}]}`. Quests only when that
   toggle is on.
8. **Faces** (setting on): side characters named in the chapter that are neither members nor lorebook
   entries; a new or updated mini-card (flat, ≤ 200 tokens).

Each model step is one request, saved only after it returns (safe to redo). A failure marks that item and
moves on; it never blocks the next chapter's vectors or borders. Spending goes to `plugin:memory`.

**Bulk** («Разобрать весь чат»): the same job with no `N`-message wait, steps 1–3 first for the whole chat,
then 4–8. Before starting, the window shows an estimate: message tokens × (1 + summaries' share) per role
model, through `spending.price_for`, plus vectors. «Начать» starts it; progress shows as «Память: главы 12/40 ·
пересказы 3/12 · пауза: ждёт ответа». Being todo-driven, it resumes after a restart or a rate limit by itself.

## 5. Prompt placement, budget, order (summary)

```
[preset: main, persona, cast (card → «Развитие» → vows), examples, lore_before/after (with «Сейчас:»)]
[memory block]  «Сюжет до сих пор» → «Ранее в истории: Глава 2 …» → «… Глава 7 …» → threads → faces
                (plot and chapters only for what was left out of the history)
[history: as much as fits, word for word; the live tail always]
[author's notes at their depth, behaviour blocks, prefill]
```

- The stable part (system, cards, examples) comes first and changes only when a chapter closes (development).
  The memory block changes only when a sticky window ends, a new chapter is found, or the oldest kept message
  moves out of the window. History grows at the end. Providers' prefix caching keeps the stable part.
  Development stays right after the card (Q8), so it breaks the cache once per chapter close.
- Own budget, apart from lorebooks (`lorebooks.lore_settings`). Under a short context the assembler drops
  unconditional extras by `drop_rank` (faces, then threads) before member cards. The tail is never dropped
  except by the old `drop_oldest(1)` last resort.
- Story order inside the memory block; score order only decides what fits.
- «Что видела модель» shows each piece with its label and versions, plus «Оставлено за окном: n сообщений»
  and «Не влезли: Глава 3, Глава 5».

## 6. Interface sketch (Russian labels)

### 6.1 The «Память» window (`t.window`, a full-height sheet on 375×667)

Opened from the top bar «🧠» (or the chat menu «Память»). Tabs as a row of 44 px chips: «Главы», «Развитие»,
«Слово и нити», «Лица». The top line: «Память в этом чате [вкл]», the progress chip, and «Разобрать весь чат…».
Under it a folded «Участники» list: each member of the chat with two toggles, «Помнит всё» and «Не наслаивать»
(44 px rows).

- **Главы**: the card «Сюжет до сих пор» (text, «✎», «🔒», «↶») on top, then chapters in story order. Each row:
  «Глава 3 · №41–№78 · В таверне» and a state chip («пересказ», «без пересказа», «устарела», «ошибка», «🔒»,
  «удалена»). Tapping a row opens the chapter sheet: the summary as a text area («Сохранить» sets the lock),
  «Пересказать заново», «Вся глава в ответ» (toggle), «Показать в чате», «Разделить здесь…» (a list of the
  chapter's messages, choose one), «Объединить со следующей», «Удалить» / «Вернуть», «Версии» (list, «Вернуть
  эту»), and why the border is here («граница: «На следующий день»»). Undo «↶» at the top of the tab.
- **Развитие**: one card per character of the chat (and «Я»): text, «✎», «🔒», «Версии», «Перенести в другой
  чат…» (chat picker, `components/Select.js`). Below, «Строки «Сейчас:»»: per lorebook bubble its line in this
  branch, «✎», «Удалить», «Записать в лорбук насовсем» (asks first).
- **Слово и нити**: grouped by who; each record shows its kind, the quote, «→ к сообщению №47», and the status
  as a 4-option choice (Select); «+ Добавить» by hand.
- **Лица**: mini-cards: name, aliases, text, «★ Любимое» (usable in every chat), «Сделать настоящим
  персонажем» (creates a card with this name and text, then opens it), «Удалить».

### 6.2 Marks in the chat

- A chapter border is a thin divider above the first message of a chapter (`t.messageDivider`): «Глава 3 · В
  таверне». Tapping it opens the chapter sheet. A stale chapter's divider has a dot.
- The message «⋯» menu (`t.messageMenu`) gets «Новая глава отсюда» and «Граница главы — после этого». Each is
  one tap, then «Отменить» in a toast.
- A progress chip in the chat's top line while the job runs or pauses (`t.status`).

### 6.3 Settings (plugin form in Settings → «Дополнения» → «🧠 Память++»)

In the mobile-ui order (most used first, details folded):
- «Включать память в новых чатах» off; «Живой хвост, % окна» 35; «Живой хвост, не меньше сообщений» 10;
  «Бюджет памяти, токенов» 1500 (M4: «не меньше», with «Бюджет памяти, % окна модели» 15); «Держать найденную
  главу, ответов» 3.
- (Per chat, in the window: «Память в этом чате»; per member «Помнит всё», «Не наслаивать».)
- «Сообщений в главе (примерно)» 40; «Границы глав определяет модель» off.
- «Пересказы глав (нужна модель)» off; «Пересказывать устаревшие сами» off; «Сюжет до сих пор» off.
- «Развитие персонажей» off; «Строки «Сейчас:» в лорбуках» off.
- «Слово и нити» off; «Квесты» off; «Второстепенные лица (мини-карточки)» off.
- «Модель для памяти»: connection + model (one small model); a list «Своя модель для…» (role: границы /
  пересказы / развитие / нити / лица → connection, model).
- Elsewhere and already present: «Прерывать фоновую работу при запросе» (core `background_yield`) and the
  embedding connection (Settings → «Промпт»).

## 7. Stages

Each stage ends with `python -m pytest` green, tests for the new behaviour, a written browser checklist on the
fake model (`tavern.devtools.fake_llm`, a temp `--data-dir`, 375×667 and 768×1280), and AGENTS.md map plus
ROADMAP updated. Vectors in tests and browser checks use the fake server's `/v1/embeddings`
(`devtools/fake_llm.py: _embeddings`).

**M1: chat vectors and the plugin API they need.** One agent, because it touches core shared files (`chat.py`
`_writer`, `assembly.py`, `manager.py`), and only after the foreground work is merged.
- Core: `core/background.py` (A); plugin migrations + core migration `0024_plugin_schema.sql` (B);
  `services/messages.py` + events and the route switch in `api/chats.py` (C); `Extra` (`covers`, `if_dropped`) /
  `tail_from` / `memory_reserve` / `values` / preset kind `memory` in `core/assembly.py`, `core/presets.py` (D); `Meaning` +
  `meaning.wanted` + `GenerationJob.meaning` in `character_tree.py`, `lorebooks.py`, `chat.py`,
  `generation.py` (F); `services/embeddings.py`, `api.embed` (G); `changelog.SKIP` rule; `create-plugin`
  skill updated; the ADR 0009 consequences line.
- Plugin: skeleton `builtin_plugins/memory` (plugin.json, migrations 0001 with chats / pieces / vectors),
  `pieces.py`, the job's steps 1–2, the per-chat switch, piece search in `inject.py` (pieces only, no chapters
  yet: found pieces of messages left out of the window go as «Ранее в истории (отрывок)»).
- Tests: unit `test_background_jobs.py` (preempt, redo, progress, cancel on unload); `test_plugin_schema.py`
  (prefix check, rollback, version, drop with data); `test_memory_pieces.py` (cut rules, excluded / hidden /
  thinking never in, latest-of-each-speaker not embedded); integration: events fire from routes (and through
  the assistant's `app_call`); one meaning request per reply with memory on and no lorebooks (fake embeddings
  call count = 1); the extras appear in the prompt record with their source; when the whole history fits the
  prompt is byte-for-byte today's; a conditional extra goes only when all its messages were left out; the tail
  (35 %, at least 10 messages) is never cut before older messages.
- Browser: switch the plugin on; send 10 messages; the chip shows vectorizing, then done; sending during
  vectorizing pauses it (chip «пауза») and the reply is not delayed; «Что видела модель» shows a found piece.

**M1 done (2026-11-09).** Built: `core/background.py` (`ctx.jobs`, `api.job`, `api.job_status`); plugin tables
(core migration `0024_plugin_schema.sql`, `plugin.json` key `migrations`, `plugins/schema.py`: prefix check and
sqlite_master comparison per file, `schema_version`, drop with data, a plugin deleted without its data remembers
its version, `.sql` is a plugin file type and part of the approved hash, never served to the browser, doctor check
`plugin-schema:<id>`, a copy via `backups.before("plugin-migration")` before a migration on existing data);
`services/messages.py` and the events `message.added / edited / excluded / deleted / swipe_selected`,
`chat.branch_selected` (the routes of `api/chats.py` and so the assistant's `app_call` go through it; `message.added`
and the swipe of a regenerate are fired from the chat service with `messages.announce`), plus `chat.created`
(not in the ADR table: the global «Включать в новых чатах» needs it); `Extra` / `tail_from` / `memory_reserve` /
`values` / `extras_dropped` in `core/assembly.py` and the preset block kind `memory` (in the default preset right
before the history; not exported to SillyTavern presets); `Meaning` (scores, query vectors, texts, model, error) and
`meaning_search(..., wanted=)`, the filter `meaning.wanted`, `GenerationJob.meaning`; `services/embeddings.py`
(embedding connection, batching, one metered request) and `api.embed`, `api.vectors_model`, with
`PluginManager.check_money` split out of the gate; the changelog and snapshot skip tables ending in `_vectors`
(`storage/changelog.py`, `storage/snapshot.py`); the plugin `builtin_plugins/memory` (pieces, store, search, inject,
work, server, web.js, migration 0001 with chats / pieces / vectors).
Choices where the ADR left room: (1) one piece = one message or a part of a long one (no merging of short
messages), so `first_id = last_id`; (2) migration 0001 holds only the M1 tables, chapters etc. come in 0002 and
later (section 3.2 shows them as one 0001: a shipped file is never edited); (3) a per-chat switch is a composer
button 🧠 of the plugin (the «Память» window and `t.window` belong to M2); (4) sticky hits, the group filter and
neighbour pieces (± 1) are not in M1; (5) with no window known (`max_context_tokens` 0) the plugin adds nothing;
(6) a conditional extra always goes to the memory spot; (7) speed: `math.sumprod` where Python has it, query
vectors averaged past 3 M multiply-adds, only keyword hits and the newest pieces get a vector score past that
(5000 pieces x 3 queries x 512 numbers: about 180 ms on the development desktop, not measured on the tablet);
(8) ADR points H and J (background model access, action descriptions) are not part of M1.

**M2: chapters.** One agent (plugin files plus web/core/plugins.js for K).
- `borders.py` (scores, TextTiling, rules), job step 3, the chapters table in use as candidates with
  `covers`, staleness by hash, branch membership, the deleted-message handler.
  `t.window`, `t.messageDivider`, `t.messageMenu`, `t.status` (K). The window: the «Главы» tab with split /
  merge / move / delete / restore / undo, and the dividers.
- Tests: pure border tests on hand-made Russian dialogues (markers, time words, cast change, OOC and cameo are
  not borders, forced cut at 3N); a chapter never goes beside its own messages; a chapter of another branch
  is silent and a shared one is not; editing / deleting / swiping inside a chapter marks it stale; old messages
  not yet in a chapter are still found by their pieces.
- Browser: an 80-message fake chat; dividers appear; split and merge from the menu at 375×667 (44 px, the sheet
  closes without scrolling); switch a branch and see the dividers change.

**M2 done (2026-11-09).** Built: `borders.py` (gap scores only before a user message: marker 1.0, time words / «Тем
временем» 0.8, cast change 0.3, cohesion valley up to 0.35, vector drift up to 0.3, pause over 6 h 0.15; OOC scores 0;
threshold 0.5; a chapter closes when the open part is longer than N (setting, default 40) and a gap of [N/4 .. 3N]
reaches the threshold, the earliest of near-equal gaps wins; forced at 3N), the optional model judge
(`border_judge`, off, own connection / model settings, `generate_json`, scene definition in the prompt, the script's
candidates as hints, a failed or refused request falls back to the script with a note, asked again only after 4 new
messages); `chapters.py` (membership by last / first message on the path, staleness by hash, repair on a deleted
message, split / merge / move / delete / restore / accept / title / whole, undo of the last 50 in plugin storage);
migration 0002_chapters.sql; job step 3 (`work.close_chapter`: one chapter per step through `job.step`, never beside a
reply); chapters as search candidates (`inject.py`: a found chapter brings its best piece with one neighbour each
side as one entry «Ранее в истории: Глава N» with `covers` = their range; `whole` brings the full text; pieces of
messages in no chapter are still found one by one); actions `chapters`, `split`, `merge`, `move_border`, `remove`,
`restore`, `accept`, `edit`, `undo`; web API `t.window`, `t.messageDivider`, `t.messageMenu`, `t.status` and the
`job` event; the «Память» window (full height on a phone) with «Главы», dividers, «Новая глава отсюда», the chip.
Choices where the ADR left room: (1) `first_id` / `last_id` of a chapter have no foreign key to messages, because the
event «message deleted» fires after the row is gone and a cascade would remove the chapter before the repair;
(2) `stale` also goes back to `raw` by itself when the text is the same again, «Принять изменения» adopts the new
text; summarized / failed states come with M3; (3) a deleted chapter keeps its range (not re-cut) and can be restored;
(4) the cast uses members, lorebook entry titles and capitalised names inside sentences; (5) a marker in the reply
after the user's message counts for the gap before that message; (6) split / move in the window take the message
NUMBER the chat shows; (7) the 🧠 composer button now opens the window, where the per-chat switch lives; (8) the
browser was not used (owner's decision of 2026-11-09): nothing here is seen on screen.

**M3: summaries, two-level search, «Сюжет до сих пор».** One agent.
- Job steps 4–5, `prompts.py`, summary vectors, the scoring in `search.py` (summary vs piece, `whole`), sticky
  hits, group filter with «Помнит всё» (`p_memory_members`, the «Участники» list), budget, «Что видела модель»
  labels (`PromptView.js` group for `memory`), locks and versions in the window, the optional model border
  judge.
- Tests: scoring (a piece beats the summary only by the margin; keywords alone find a name; story order in the
  output; budget drop order); sticky holds for N replies; the group filter (own chapters + plot; «Помнит
  всё»; a member added later gets no earlier chapter); a failed summary leaves the chapter
  raw and is retried with backoff; locked text is never replaced; with the fake model, JSON forms through
  `generate_json`.
- Browser: summaries appear in the window; edit one → lock; «Пересказать заново» while sending a message →
  the job pauses; the prompt view shows «Сюжет до сих пор» and «Ранее в истории».

**M3 done (2026-11-10).** Review of M1 + M2 first; found and fixed (each with a regression test):
(1) `inject.py` cut its offers to the memory budget by score BEFORE the window was known: recent messages still in the
window share the query's words, took the whole budget, and the old messages really left out were never offered (memory
was nearly silent exactly when needed). Now every offer goes to the assembler ranked, and the assembler takes those
left out while `memory_reserve` lasts; new `Extra.order` (placed in story order, considered in list order) and
`Extra.group` (alternatives: a chapter's summary or else its pieces; a group none of which fits is named once as
dropped). (2) `pieces.keywords` looked back over the whole text for every word (quadratic), and the job cut every
message of the branch again on every pass, on the event loop: a linear look-back, the cut memoized per message text,
and the job's cut done in a thread. (3) With the preset's memory block switched off, conditional extras still kept
their reserve (old messages pushed out for nothing) and were named as sent.
Built: job steps 4–5 (`summaries.py`, `prompts.py`): one request per chapter through `job.step` (cancelled at once by a
user request, redone after), JSON `{"title", "summary"}` via `generate_json`, the language of most of the chapter
(R4: the Cyrillic share is checked, one more try with the reason, then «ошибка»), past tense, no meta, about
`summary_tokens` (1000); the previous summary as context; a failed chapter keeps its mark (`error`, `tries`), backoff
2 min doubling up to 6 h, and the plugin wakes the job by a timer. «Сюжет до сих пор»: the old plot + the new summaries
(or from scratch in batches when a middle summary changed), 300–500 tokens, a version anchored at the last summarized
chapter (the newest version anchored on the branch wins: per branch), sent `if_dropped` and set as `{{summary}}`.
Summaries get vectors (`s:<chapter id>`) in step 2. Two-level search: a chapter's summary and pieces are scored in
the same pass with the reply's one meaning request; pieces go only when P > S + 0.1, else the summary (covers the whole
chapter), its pieces as the alternative; «Вся глава» sends the full text. Sticky: a found chapter (as really sent,
learned in `prompt.built`) is kept for `sticky` (3) replies in the same form; a reply that only keeps it does not
renew it. Group filter: in a chat with several characters a chapter goes only to a speaker in its participants or with
«Помнит всё»; the plot goes to everyone; the user's own turn sees everything. Model access (point H):
`api.llm(background=True, on_wait=)`: «спрашивать каждый раз» asks once per job run, no per-reply limit, and with no
page open the request waits («ждёт разрешения»); a refusal or no access leaves chapters raw and the job note says why.
Settings (all off by default): «Пересказы глав», «Пересказывать устаревшие сами», «Сюжет до сих пор», length of a
summary, «Держать найденную главу, ответов» (3), «Модель для памяти» (one small model) and own models for summaries,
the plot and borders (role → memory model → the chat's). The window: «Сюжет до сих пор» card, a summary per chapter
(edit = lock, lock toggle, «Пересказать заново», versions with «Вернуть эту»; all in the one «↶»), state chips,
«Участники» with «Помнит всё» / «Не наслаивать»; «Что видела модель» shows «Память вместо оставленного за окном» and
«Не влезло в бюджет памяти». Migration 0003_summaries.sql: `p_memory_texts` (columns `made_by`, `is_current`,
`made_from` instead of the sketch's `by` / `current`), `p_memory_hits`, `p_memory_members`, chapter columns `error`,
`failed_at`, a trigger that drops a summary's vector with its chapter.
Choices where the ADR left room: (1) a stale summary still goes when found (it is mostly right) until redone; (2) a
locked summary is not redone by the button either (the window asks to take the lock off first); (3) the memory's own
reply counter for sticky (in `p_memory_chats.settings`), not `meta.generation`; (4) loose pieces (in no chapter) are
not group-filtered; (5) a denied «may it?» is asked again on the next job run (each reply wakes the job); (6) with the
default budget (1500) one ~1000-token summary plus the plot fit: raise «Бюджет памяти» for more; (7) a sticky chapter
ranks after the ones found now; (8) a hand edit of the plot is anchored where the branch's plot was. Not seen on
screen (no browser, owner's rule 9).

**M4: development and «Сейчас:» lines.** One agent.
- Job step 6, `after_card` extras, versions anchored per branch, hand edit and lock, «Перенести в другой чат»,
  the persona's layer, «Не наслаивать», `lore_rewritten` lines and «Записать в лорбук насовсем».
- Tests: the layer goes right after the card and is cut with its member; another branch keeps its own
  version; undo / versions; a now-line only on an entry chosen for this reply and stacked after a regex
  rewrite; the card and the lorebook rows never change (row hashes before / after) until «Записать в лорбук
  насовсем», which makes one undoable history step; «Не наслаивать» means no layer and no request.
- Browser: the «Развитие» tab, edit, lock, transfer to another chat.

**M4 done (2026-11-10).** Built: `layers.py` and migration `0004_layers.sql` (`p_memory_layer_runs`: which closed
chapter the development step worked on, with its failure and tries; an index on `p_memory_texts(chat_id, kind,
anchor_id)`). Layers and lines are `p_memory_texts` versions (kind `development`, target = character id, the persona's
character id or `user` in a chat without a «me» member; kind `now_line`, target = lorebook bubble id) anchored at the
chapter's last message, so the newest version anchored on the branch is the branch's (3.1). Job step 6 (`layers.step`,
after the summaries and the plot): the next closed chapter in STORY ORDER without a run (with «Пересказы глав» on it
waits for its summary; a chapter whose summary failed goes by its text) gets ONE request through `job.step` (cancelled at
once by a user request, redone after): the scene (summary, else the shortened text), each participant without «Не
наслаивать» and without a lock (name, the card's description, the old layer), and the chat's lorebook bubbles the
chapter mentions by keywords (`retrieval.keyword_hits`, at most 12, a locked line left out) with their old lines; JSON
`{"layers": [{"who", "text"}], "now_lines": [{"entry", "line"}]}` (codes C1 / E1, names understood too), present tense,
the scene's language (one retry with the reason, then the run fails with a backoff, skipped after 3 failures). Self-
compression: the old layer goes in and the prompt asks to merge and compress; a layer longer than 1.3 × «Длина
развития» (450) gets one more request «Shorten…» inside the same step, and is cut at a sentence end if still too long.
Nobody to layer and no bubble mentioned: the run is marked done with no request. In the prompt (`layers.contribute`,
whatever the window): each prompt member's layer as `Extra(place="after_card", owner=member, drop_rank=60)` labelled
«Развитие: <name>», text «[<name> сейчас — развитие по ходу истории; где расходится с карточкой, верно это]»; the persona's
after the persona's card, or in the memory spot when the chat has no «me» member; «Сейчас: …» appended to
`lore_rewritten[block]` (on top of a regex rewrite) only for bubbles chosen for this reply. The window's «Развитие» tab:
a card per character and «Я» (text, «Сохранить» = a new locked version, «🔒», «Версии» / «Вернуть эту», «Перенести в
другой чат…» with the app's `Select`, «Не наслаивать»), then the branch's «Сейчас:» lines («Сохранить» = lock,
«Удалить» = an empty version, «Записать в лорбук насовсем» after `ask`); hand edits share the window's one «↶». «Записать
в лорбук насовсем»: `backups.before("memory-now-line")`, then `character_tree.update_block` (one lorebook history step,
undone there), then an empty version so the line is not appended twice. Settings (off): «Развитие персонажей», «Строки
«Сейчас:» для лорбуков», «Длина развития, токенов» (450), own connection / model for development (role → «Модель для
памяти» → the chat's). Budget (M3 note 6): new setting «Бюджет памяти, % окна модели» (15); the reserve is
max(«Бюджет памяти, не меньше токенов» (1500), 15 % of the window): 4 800 tokens for a 32k window (the plot and 3–4
summaries), the floor for small windows; existing chats keep their number as the floor.
Choices where the ADR left room: (1) one request per chapter for all its participants and the lines together (the ADR
read as per participant; one is cheaper and keeps lines and layers consistent); (2) strict story order: a failing chapter
holds the later ones until its backoff (a layer is built on the previous one), at most 3 tries; (3) a chapter changed
later (stale, split, merged) is not layered again unless it is a new chapter row; switching the settings on later
processes all closed chapters of the branch, oldest first; (4) the layer is sent to every reply where that member's
card is in the prompt, not only when it speaks; settings off = nothing made and nothing sent, hand-written layers
included; (5) hand edits, versions and lock work like the plot (a new locked version at the branch's anchor; the lock
toggle is not in «↶»); (6) transfer copies the branch's layer into the other chat at its newest message (`made_by
transfer`, not locked); the persona's layer goes to the other chat's persona; the other chat must have messages and the
character; memory there is not switched on by it (the toast says so); (7) one «Сейчас:» line per bubble per branch, a
newer one replaces it (the model sees the old one); «Удалить» is an empty version (undoable); (8) «Записать в лорбук
насовсем» is not in the window's «↶» (the lorebook's own history undoes it); (9) the development step uses the
`development` role's model. Not built (M5+): threads, mini-cards, export of layers and lines, assistant tools, bulk.
Not seen on screen (no browser, owner's rule 9).

**M5: «Слово и нити».** One agent. Migration 0002, job step 7, vows always in that character's replies, the
rest by relevance (keywords + vectors of the quote), status per branch, the tab. Tests: a vow goes only to its
owner's reply; a status change on one branch only; the persona's promises reach characters («ты же обещал»);
quests only with their toggle.

**M5 done (2026-11-10).** Built: `threads.py` and migration `0005_threads.sql` (the sketch's «0002_threads»: shipped
files are never renumbered): `p_memory_threads` (kind, who, to whom, knows, the exact quote, the message, an anchor = the
quoted message, so a record made before a fork is shared; the chapter it came from, importance 1-3, keywords, made_by,
locked, deleted), `p_memory_thread_states` (status + who knows, anchored at a message: the newest on the branch wins, none
= «в силе»; so a status is per branch), `p_memory_thread_runs` (which closed chapter the step read, failures, tries) and
a trigger dropping a record's vector with it. No foreign keys on the message ids (as with chapters): a deleted message
takes the record off the branches, and the assistant's undo of that delete brings it back. Job step 7 (`threads.step`,
after the development step; setting «Слово и нити» off = nothing): the next closed chapter in story order without a run
(it does not wait for summaries: exact quotes need the text) gets ONE request through `job.step` (cancelled at once by a
user request, redone after): the scene with the chat's message numbers (the user's messages under the persona's name),
the people with codes (the chat's characters, the persona, anyone else who wrote in it), and the branch's records still
open before the chapter's end (active ones, and secrets not «снято»; at most 40, most important first). JSON
`{"new": [{kind, who, to, quote, message_no, importance, knows}], "changes": [{id, status, message_no, note, told}]}`.
A new record is kept only when its quote is really in the named message or another message of the scene (case, «ё»,
quote marks and spaces aside; else it is dropped as invented), not a repeat of a record of the branch (deleted ones
included), and of a known kind (quests only with «Квесты», off). A change makes a status row anchored at the named
message of the chapter (else its last); `told` adds listeners to a secret; a locked record is never changed. A failure
is retried after the summaries' backoff, at most 3 times. The quotes get vectors in step 2 (`t:<id>`, as summaries do).
In the prompt (`threads.contribute`, whatever the window; nothing with the setting off, hand-written records included):
only for the one who speaks now (the primary member; the persona on the user's own turn). Their vows and principles in
force ALWAYS, right after their card (`Extra(place="after_card")`, «Клятвы и принципы: <name>», drop rank 58; the persona
without a «me» member: the memory spot). Other records only when relevant: scored by `search.score` with the stems of
the last `meaning_messages` texts (member names left out: they are in nearly every record) and the reply's ONE meaning
request (no request of its own), with the chat's pieces as the IDF corpus (`search.score(idf=)`: a few records are no
corpus), at most 8, in story order, as one memory-spot entry «Слово и нити: <name>» (drop rank 45) with kind, who → to
whom, status (and its note), «№N» and the quote. Who may get a record: their own; one given to them; the persona's given
to them or to everyone (so a character can say «ты же обещал»); a secret only to those who know it; «Помнит всё»: every
record (by relevance). «снято» never goes. The «Слово и нити» tab: records grouped by who (the chat's characters, then
«Я»), a status chip each, a filter by status (в силе / все / выполнено / нарушено / снято / удалённые) and kind, the
record sheet (quote, kind, to whom, importance, who knows a secret: «Сохранить» locks; the status on this branch;
«🔒»; «Удалить» / «Вернуть»; the status history; «→ к сообщению №N» closes the window and shows the message through
the new plugin web call `t.showMessage({id, depth})`), «+ Добавить» by hand (with an optional message number); hand edits
share the window's one «↶». Settings (off): «Слово и нити (нужна модель)», «Квесты в «Слове и нитях»», own connection /
model (role `threads` → «Модель для памяти» → the chat's). Fixed M4's known gap of M3: the plot's hand edit in a chat
with no plot was anchored at the newest message and went missing while that message was regenerated or continued; it
is now anchored before it (`chapters.fresh_anchor`, moved there from layers.py), with a regression test.
Choices where the ADR left room: (1) one request per chapter for all kinds and the status changes together; (2) a
record whose quote is not found in the scene is dropped (small models paraphrase: fewer records, none invented);
(3) every hand change locks the record, a hand status too (the model never sets it back); the lock toggle itself is not
in «↶» (as in M4); (4) a hand status is anchored before the newest message, on this branch only; (5) «Удалить» is for
the whole record (all branches sharing it), not per branch; «снято» is the per-branch way to retire one; (6) kept and
broken records still go when relevant (with their status), only «снято» never; vows go always only while «в силе»;
(7) a character's records to everyone go only to that character (only the persona's reach everyone); (8) «Помнит всё»
also opens secrets (the narrator-like member knows everything); (9) records are per chat, never carried to other chats;
(10) a chapter changed later is not read again unless it is a new chapter row (as M4). Not built (M6+): mini-cards,
export of records, assistant tools, bulk. Not seen on screen (no browser, owner's rule 9).

**M6: mini-cards.** One agent. Migration 0003, job step 8, the faces found by name / alias in the scan text,
«★» reuse in every chat, «Сделать настоящим персонажем», the tab. Tests: never a bubble tree; off by default;
a favourite face shows up in another chat only when named; the button makes a card and keeps the face linked;
the assistant's create-face action has `confirm`.

**M6 done (2026-11-11).** Built: `faces.py` and migration `0006_faces.sql` (the sketch's «0003_faces»: shipped files are
never renumbered): `p_memory_faces` (APP-WIDE: name, aliases, the flat card, ★ favourite, made_by, locked, deleted,
merged_into, character_id of the real character made from it, the chat it came from), `p_memory_face_versions` (every
state of name / aliases / card), `p_memory_face_chats` (where a face appeared, its first message, `in_use` = «Использовать
в этом чате»), `p_memory_face_runs` (which closed chapter the step read), a trigger dropping a face's vectors with it.
Job step 8 (`faces.step`, setting «Второстепенные лица — мини-карточки», off) runs BEFORE steps 6 and 7 in each pass, so
a side character found in a chapter is already a person of that chapter's development and threads requests: the next
closed chapter in story order gets ONE request through `job.step` (cancelled at once by a user request, redone after)
with the numbered scene, the main characters (members, the persona; lorebook entries are excluded by stem) and the
known faces the scene names (linked to the chat or favourites, codes F1.., a locked one marked «[locked]»). JSON
`{"faces": [{face, name, aliases, card}]}`; one more try when a card is in the wrong language. Detection rule: the model
proposes, the script confirms: a NEW face is kept only when its name is not a main character's (all words of the name
by stem) and it RECURS: named (name or alias, any ending) in two messages of the chapter, or in one and in an earlier
message of the branch; a one-message cameo is dropped. A known face (by code, or by the same name / alias) gets the new
card and aliases as an automatic version, never when locked or deleted (a deleted face stays known, so it is not made
again); every face the chapter names is linked to the chat. Cards are cut to 200 tokens. A failure is retried after the
backoff, at most 3 times. Cards of the chat's faces get vectors `f:<face>@<chat>` in step 2. In the prompt
(`faces.contribute`, whatever the window, nothing with the setting off): faces linked and used in the chat, or
favourites not switched off there, without one made real whose character is a member of the chat; relevant when named
in the last `meaning_messages` texts (name or alias, any ending), or, for faces linked to the chat, found by the reply's
ONE meaning request (no request of its own); a favourite goes to another chat ONLY when named. At most 4 faces within
600 tokens as one memory-spot entry «Лица: …» (source `plugin:memory:faces`, drop rank 40: dropped before threads),
each «- Имя (также: …): card» and, with «Развитие» on, «Сейчас: …» = that face's development layer of this branch cut to
120 tokens. Development and «Слово и нити» of a side character reuse M4 / M5 unchanged: the face is a person with target
`face:<id>` in their requests when the chapter names it (`layers.plan`, `threads.people_of`), in the «Развитие» tab
(«Лицо · …», hand edit, versions, «Не наслаивать», transfer to chats it is linked to) and in the «Слово и нити» people;
its records reach a member by the M5 rules (given to them, secrets by who knows). The «Лица» tab: this chat's faces or
«Все лица» with a search (name, alias, card); a face sheet: name, other names, the card («Сохранить» = a new locked
version: rename and aliases too), «Версии» / «Вернуть эту», «★ Любимое», «Использовать в этом чате», «🔒», «Объединить
с…» (asks; the face's name and aliases become aliases of the other, its links, development texts, records, who-knows
lists and member flags move; undoable), «Сделать настоящим персонажем» (asks; a card through the normal character
service, name + the card as the description + «Также: …», no bubbles; the face keeps `character_id`; then the page
opens it through the new plugin web call `t.openCharacter(id)`), «Открыть персонажа», «Удалить» / «Вернуть»; hand
edits share the window's one «↶» (the chat's undo log, though faces are app-wide).
Owner's request during M6 (2026-11-11): ONE model for every memory task. The per-role «Своя модель для …» settings
(borders, summaries, plot, development, threads) are gone; `Clients.which` reads only «Модель для памяти» (connection ""
= the chat's, model "" = that connection's own); older stored per-role values are dropped by the settings check. New
general plugin setting type `model` with `connection_key` (`plugins/manifest.py`, `web/components/Plugins.js`
`ModelField`): the form shows the connection's own model as the value and offers that service's model list (the list
«Проверить» uses), so one connection serves any of its models.
Choices where the ADR left room: (1) one request per closed chapter for all its side characters, new and known; (2)
«recurring» = two messages of the chapter, or one plus an earlier message (not «two chapters»: a long scene with the
innkeeper is enough); (3) a face is app-wide but a non-favourite one is known only in the chats it is linked to, so the
same name in another chat makes a separate face (merge joins them); (4) a favourite is linked to a chat when a chapter
there names it; «Использовать в этом чате» off keeps it out even as a favourite; (5) a made-real face stays a mini-card
in chats where its character is not a member; (6) lock, ★ and «Использовать в этом чате» toggles are not in «↶» (as
M4 / M5 locks); make-real is not either (the card is deleted as any card); (7) the merged face row stays (marked
merged, hidden) only for undo; (8) a face's own records «to everyone» reach nobody (M5 choice 7); (9) faces are not group-
filtered by speaker. Not built (M7+): the assistant's create-face action with `confirm`, export of faces with chats,
bulk. Not seen on screen (no browser, owner's rule 9).

**M7: the assistant, export, bulk.** Can be two agents in parallel: (a) export / import (E) +
`transfer.py`, (b) assistant (J) + `skills/memory.md` + bulk with the estimate. They share no files if (a)
owns `api/jsonl.py`, `formats/jsonl.py`, and (b) owns `services/assistant_app.py` and the plugin's
`actions.py` / `work.py`.
- Tests: export → import round trip keeps chapters, summaries, development, threads with remapped ids, and no
  vectors in the file; an old Tavern file still imports; the assistant can list / edit / undo a summary via
  `app_call` and press «Векторизовать»; the bulk estimate equals the recorded spending within the estimate
  error; bulk resumes after a forced 429 on the fake server.
- Browser: «Разобрать весь чат…» shows the price first, then progress; the assistant edits a chapter summary
  and «Отменить» puts it back.

**M7 + M8 done (2026-11-11), one combined stage (owner's decision).** No browser (owner's rule 9): the interface is written
by the mobile-ui rules and not seen on screen.

*M7, export / import (point E).* `formats/jsonl.py` knows the record type `plugin` (`{"plugin", "version", "data"}`); the
header says `version: 2` only when a file has such a record (a file with none is byte-for-byte the old format, so older
builds still read it). Core: `api.on_export(fn(chat_id) -> dict | None)` and `api.on_import(fn(chat_id, data, id_map))`
(`plugins/api.py`, `plugins/manager.py: export_records / import_records`), called by `api/jsonl.py` after the messages
exist. A plugin that is off or not installed when a file comes keeps its record in `plugin_data` under `import:<id>` and is
handed it right after it starts. `transfer.py` of the plugin: chapters (borders), summaries with their lock, every version
of the plot, development layers, «Сейчас:» lines, member flags, «Слово и нити» records with their status rows, the faces
linked to the chat (with versions), and which chapters the per-chapter steps already did. NEVER vectors, pieces or sticky
hits (the new chat is woken and makes them). Every message id goes through `id_map`; chapters, texts, records get new ids,
the plot's `made_from` is remapped to the new summary ids, and a chapter's `content_hash` is recomputed for the new message
ids (else every imported chapter would be stale). A row whose message is not in the file is skipped. A face already in the
app under the same id and name is just linked; otherwise a new card is made (with its versions). A file of a newer memory
format is ignored, the chat still imports.

*M7, the assistant (point J).* `api.action(name, fn, *, describe, reads, confirm)`; `PluginManager.action_info`.
`assistant_app.app_routes` lists the actions that have a `describe` (`POST /plugins/<id>/actions/<name> — line [reads only]
[asks the user first]`); `app_call` skips the journal and the wait for replies for `reads`, and `_ask_app_call` returns the
`confirm` question (the model's own `what` wins). `catalog.py` holds the line for each memory action (chapters, summaries,
plot, development, «Сейчас:», threads, faces, `vectorize`, `resummarize`, whole-chat run, documents); an action without a
line (e.g. the window's own `undo`) stays hidden. New `face_add` (confirm; refuses a person the chat already knows). `skills/memory.md`
(plugin skill, English) teaches the workflow, the price rule and the confirmations. Changes go through the assistant's
journal as before (`p_memory_*` tables are watched, `_vectors` tables are not), so «Отменить» puts a text back.

*M7, «Разобрать весь чат» (`bulk.py`).* It is the chat's ordinary job (it already goes chapter by chapter and gives way to the
user) plus: `bulk_estimate` (requests per step, tokens, vector tokens, money from `spending.price_for` of the memory model and
of the vector connection; an unknown price is said plainly, never a number), `bulk_start` (needs `confirmed: true`: the page
asks, the assistant has `confirm`; it switches memory on, remembers the agreed estimate, wakes the job), `bulk_state`
(progress per step, the real spending since the start; the mark ends by itself when nothing is left), `bulk_stop`. A job
that gave up on rate limits (`JobGaveUp`) is woken again after `RATE_RETRY` while the mark is on; a restart wakes unfinished
runs and documents `RESUME_DELAY` after the plugin starts. Estimate = a guess from the chat's own sizes plus fixed prompt
overheads per step (`bulk.OVERHEAD`).

*M7, R2.* `Foreground.run_background(factory, on_cancel)`: a step cancelled `quiet_after` (3) times in a row waits
`quiet_seconds` (20) after the user's work last started or ended before it is made again (a reply in that time moves the
window); the job's note shows «отменено n раз: жду тишины» (`JobContext.step`).

*Plugin settings (general).* A field's `help` is shown FOLDED («Что это?», `<details class="plugin-help">`) in every plugin's
form. The model-access permission came first on the card: «Разрешить дополнению пользоваться моделями (это тратит деньги)»
with folded help; while it is off the plugin's `connection` / `model` settings are hidden (`visibleSettings`) with a short
line, when on come «Спрашивать меня перед каждым запросом», the limits and (always) the «Потрачено» line. When a `model`
setting's `connection_key` setting changes without the model in the same save, the saved model is reset to its default
(`manifest.check_settings`): a model of another service is never kept. New setting: `docs_percent` (10).

*M8, documents (`docs.py`, migration 0007).* A .txt / .md / .docx (pure Python: `word/document.xml`, heading styles
become `#`; cp1251 accepted) is cut at once, no model: chapters by headings («#», «Глава N», «Chapter N», Roman numerals;
a section under ~150 tokens joins the next, one over ~6000 is cut again), else by the chat's border cutter over paragraphs
(`borders.score_gaps` / `choose`, so «***» and «На следующий день» are borders); each chapter into pieces (`pieces.cut`),
keywords as for messages. Tables `p_memory_docs`, `_doc_texts`, `_doc_chapters`, `_doc_pieces`, `_doc_summaries`, `_doc_chats`
(which chats may find it), `_doc_vectors` (app-wide documents, so not the chat vectors table; never exported or
journaled). Before any model work `doc_estimate` gives the price; `doc_start` needs `confirmed: true`; the job
`doc:<id>` makes vectors (batches, 429 waited out) and a summary per chapter (own prompt «memory of a long text», the same
checks and retries as chat summaries), every step through `job.step`. Searching (`docs.contribute`, called from
`inject.contribute`): only with memory on in the chat and the document attached; the two levels (summary, or a piece with one
neighbour each side when it beats the summary by the margin; «Вся глава» sends the chapter up to 3000 tokens), keywords alone
work before the run; own budget `docs_percent` of the window (at least 300 tokens), at most 4 entries «Из документа «…»,
глава N», each its own unconditional memory-spot entry (drop rank 30, before the chat's own memory). `meaning.wanted` is
also answered yes when an attached document has vectors. «Документы» tab: add (price dialog, yes starts), attach to / detach
from this chat, «Разобрать…», «Остановить», chapters with «Вся глава», delete.

Choices where the ADR left room: (1) bulk reuses the chat's job instead of a second job, so it follows the same switches; the
estimate names the steps that are off; (2) vectors cost is estimated from piece tokens, summaries are counted per chapter;
(3) documents are not in chat files and their vectors are one set per document, not per chat; (4) a document without vectors
is searched by words only; (5) `doc_add` for the assistant takes plain `text`, files come from the page; (6) the sticky rule
does not apply to documents; (7) no per-member filter for documents; (8) not seen on screen.

## 8. Risks and decisions

### Risks

- **R1. Speed on the phone.** Scoring is pure-Python cosine. 5000 pieces × 3 query vectors × 1024 numbers is
  about 15 M multiply-adds per reply: around 0.5 s on a desktop, several seconds on Termux. Mitigations, in
  order: store normalized vectors (dot = cosine); average the query vectors into one; cache each chat's
  vectors in memory as `array('f')` while it is open; ask Voyage / Cohere for 512-number vectors
  (`output_dimension`) for chat vectors. Measure in M1 with a 5000-piece fixture; the budget is 300 ms on the
  tablet.
- **R2. Cancelled requests may still be billed.** Cutting a summary request in flight when the user sends a
  message is the owner's rule, but the provider may charge for what it already read. Many short replies in a
  row could cancel the same summary repeatedly. Mitigation: show «отменено n раз» in the job note, and give a
  step that was cancelled 3 times a 20 s quiet window after a reply.
- **R3. Script borders on Russian roleplay** are heuristic: a stem list, not morphology
  (`retrieval.stem`). Hand fixes (split / merge / move) are the safety net, and the thresholds must be tuned on
  the owner's real chats (read-only, on a copy).
- **R4. Summary quality with small models** (meta talk, English, invented details). Mitigation: a strict JSON
  form, a check that the text is in the chat's script (Cyrillic share), one retry, and otherwise «ошибка».
- **R5. Prompt caching.** The development layer sits right after the card, so every chapter close breaks the
  cache from that point. Accepted by the owner (Q8).
- **R6. Plugin migrations** add a new failure mode at startup. A bad file must leave only that plugin off
  (B), never stop Tavern; covered by tests.
- **R7. Chat file compatibility.** A file with a `plugin` record cannot be read by older Tavern builds.
- **R8. Many shared files in M1** (`chat.py`, `assembly.py`, `character_tree.py`, `manager.py`) are being
  changed by another agent now. M1 must start from the merged tree.
- **R9. Muted members as a workaround.** The owner adds characters and mutes them so their cards stay in the
  context. A proper «присутствует молча» feature may be worth doing later; not designed here.

### Decided 2026-11-09 (the owner's answers; folded into the sections above)

- **Q1.** Memory is per chat: «Память в этом чате», plus a global «Включать в новых чатах».
- **Q2.** Old messages go word for word while they fit, chapters or not; memory covers only what no longer
  fits (conditional extras, 2.2-D).
- **Q3.** Live tail: 35 % of the context and never fewer than 10 messages, both editable.
- **Q4.** No special narrator in memory. Per member: «Помнит всё» and «Не наслаивать». By default a member gets
  only the chapters they were in, plus «Сюжет до сих пор»; a member added later gets no earlier chapters word
  for word. (The muted-member workaround: R9.)
- **Q5.** «Сейчас:» lines live in the chat and branch and are added only when the bubble is sent; the lorebook
  never changes; «Записать в лорбук насовсем».
- **Q6.** Chat vectors need an embedding connection and the plugin's money limits, not the «может обращаться
  к моделям» permission.
- **Q7.** With «спрашивать каждый раз», background work asks once per job.
- **Q8.** «Развитие» goes right after the card.
- **Q9.** «Сделать настоящим персонажем» on a mini-card; the assistant creates mini-cards only after asking.
- **Q10.** Documents: a separate stage M8 after M7.
- **Q11.** Summaries in the language of most of the chapter's text.
- **Q12.** The plugin is called «🧠 Память++».

## Consequences

- The core gains general plugin points: jobs that yield to the user, plugin tables, chat events, prompt extras
  with places and budget, export / import hooks, a shared meaning request, embeddings, background model access,
  plugin action descriptions for the assistant, and window / divider / menu / status places in the page.
  `thoughts`, `translate` and future plugins can use them.
- `{{summary}}` finally has a value.
- Chat files gain a `plugin` record and header version 2.
- The ADR 0009 rule holds: everything a plugin registers carries its owner and goes away when it is switched
  off. Its tables stay until the user deletes the plugin with its data.
