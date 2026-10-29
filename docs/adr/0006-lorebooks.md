# 0006. Lorebooks: books of bubbles with an owner, one engine, four levels

Status: accepted (stage 5, 2026-10-29)

## Context

The owner wants lorebooks (world info) with SillyTavern's full feature set, but looking and working
like the character's bubble map they already use: same colours, keywords, chance, links, order
numbers, undo. Decisions (2026-10-29): the same bubble map; four levels at once (global, chat,
character, the user's own "это я"); chains (an entry's text wakes other entries) with a depth
limit; all of ST's finer entry options, folded under «Ещё»; their own token budget (tokens or a
percent of the model's window); places in the prompt as in ST; meaning search by vectors; a
"Лорбуки" section in the left panel.

## Decision

1. **A bubble has an owner.** Migration 0009 rebuilds `character_blocks` with `character_id` or
   `lorebook_id` (exactly one) and a generated `owner_id`; links, history versions and steps are
   keyed by `owner_id` (history rows go with their owner through triggers, since the owner can be in
   either table). Every block function in `services/character_tree.py` takes an owner id, so the map,
   links, chance, order, vectors and "Отменить" work for books with no second copy of the code. The
   rebuild copies the dependent tables out first: dropping a table while foreign keys are on deletes
   the rows that point at it.
2. **One engine** (`core/retrieval.py`). New per-bubble `rules` (JSON): second keywords with
   and/or/not logic, whole words, case, own scan depth, pause after firing (cooldown), delay, groups
   (one wins: held > main > weighted draw), a character filter, no-wake / not-woken for chains, and
   `place`. They apply to character bubbles too. Lorebooks use `by_order`: entries rank by book order
   and order number, blue ones share the budget, so green ones with the largest numbers are dropped
   first; what was dropped is shown in "what the model saw".
3. **Levels**: `lorebook_uses` (global / chat / character), cascading with chats and characters. A
   reply's pool is the chat's books, then each member's (as character or as "это я"), then global
   ones; a book counts once. Chains start from the chat and from the characters' bubbles going with
   the reply (`seed_text`). Settings `lorebooks`: budget_tokens, budget_percent, recursion.
4. **Places**: the preset blocks `lore_before` / `lore_after` (already in every preset since 4а),
   next to the author's note (top/bottom, at its depth), around the example dialogues, or at a depth.
   Whatever finds no block in the preset goes just before the chat.
5. **SillyTavern files** (`formats/st_lorebook.py`): world files in and out; unknown entry fields
   ride along and go back; character names in filters are matched to ours. Our export adds the whole
   tree (`tavern_tree`) for a lossless trip back. A card that names its world in
   `extensions.world` gets that book switched on when it exists.
6. **Speed**: keywords are counted against a sorted word index of the text (binary search) instead
   of one regex pass per keyword; phrases and /regex/ keep their patterns (cached). 2000 entries
   with two chain steps: ~70 ms on a desktop.

## Consequences

- `owner_id` is a generated column: raw row restores (history) must not write it.
- Plugins listening to `character.blocks_changed` see book changes as `lorebook.blocks_changed`.
- The "title is a keyword" rule stays for bubbles, but entries imported from ST with a comment get
  `title_key: false` (an ST comment is a note, not a key).
