# 0003. Prompt engine: presets, chat members, archive of what the model saw

Status: accepted (stage 4а, 2026-10-28)

## Context

The owner wants a SillyTavern-like prompt manager, ST preset compatibility without slowing the
app, a "what the model saw" button on every reply, and no separate "persona" entity: the user's
own character is an ordinary character marked "me" in a chat. A chat may have any number of
characters, each with a depth (where its card goes) and an importance (what is dropped first).
Agents and the on-top assistant (stage 8) must be able to change all of this.

## Decision

1. **Presets are JSON** (`core/presets.py`): an ordered list of blocks (`main`, `persona`,
   `cast`, `examples`, `lore_before`, `lore_after`, `history`, `post_history`, `text`) plus
   model parameters. One built-in read-only preset; user presets live in the `presets` table.
   A chat uses its own preset, else the general one, else the built-in.
2. **ST presets are converted once** at import/export (`formats/st_preset.py`). Unused ST keys
   are kept in `st` and written back, so a round trip loses nothing. Text Completion presets
   are refused until stage 6.
3. **Chat members** (`chat_members`, migration 0003). `chats.character_id` stays as the main
   character (names replies, `{{char}}`) and is kept pointing at a member that is not "me".
   All changes go through `services/cast.py` and fire `chat.members_changed`.
4. **Assembly** (`core/assembly.py`) returns both the messages sent and the entries they were
   made from (source, label, kind). Budget order: oldest messages down to 6, then member cards by
   importance (never the main character or "me"), then messages down to the newest one.
5. **Archive** (`storage/prompt_log.py`): every distinct text is stored once (zlib, keyed by
   hash) and never changed; a record per reply version stores only piece hashes, as a delta on
   the chat's previous record (chains cut every 32). Editing a card or message later adds a new
   piece, so old records stay exact. `prompt_piece_sources` lists the versions of each source
   for a read-only viewer. Deleting a chat collects pieces nobody uses.
6. The old `build_prompt` stays for plugins that call it; `GenerationJob.build_messages` uses the
   assembler when the service passes a `PromptInput`.

## Consequences

- Storing the archive for every reply costs roughly the size of the chat itself plus small
  records, instead of growing with the square of the chat length.
- Exact token counts need one extra request (`max_tokens: 1`, read `usage.prompt_tokens`), so
  they are only made on demand.
- Lorebook blocks exist but stay empty until stage 5; the memory/compression design (roadmap,
  open question) will plug into the `history` block and per-recipient presets.
