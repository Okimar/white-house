# 0004. Character tree: blocks under a card, found by keywords and meaning

Status: accepted (stage 4б, 2026-10-28)

## Context

The owner wants a character to be anything from a plain 1500-token card to a large tree of
blocks ("Appearance › Hair" in great detail). The model always gets the card; a detailed block
goes only when its topic comes up. Meaning search must use only the blocks' own text, never
system prompts or lorebook noise. Agents must be able to edit the tree. The editor is a map of
bubbles that stays smooth with thousands of them, on a phone.

## Decision

1. **Storage** (migration 0004): `character_blocks` (tree by `parent_id`; mode always/search/off;
   keywords; depth; role; sticky; map layout; `extra` keeps an imported ST entry), vectors in
   `block_embeddings` keyed by model and a hash of the text, and `chat_block_hits` for "stays N
   replies". The card in `characters.data` stays the root, so card formats are unchanged.
2. **Selection** (`core/retrieval.py`, pure): blue always; green by keywords and title with
   Russian endings ignored (`/regex/` allowed), by meaning over a threshold, or sticky. A found
   block brings its blue sub-bubbles (layers, below); each block carries its path. Green blocks share a token budget:
   keyword hits first, then meaning, then sticky. The service runs it before assembly; the
   assembler puts blocks next to the member's card or at their own depth, and the archive
   records them as `block:<id>` (versions viewable).
3. **Vectors** use a separate connection (`/embeddings`, OpenAI-compatible). The vector text is
   the block's path and content only. Without that connection, keywords alone work.
4. **SillyTavern cards**: on import, `character_book` entries become blocks (constant → blue,
   keys or vectorized → green, disabled → grey, depth/role/sticky from extensions); the book's own
   fields stay in the card. On export the entries are rebuilt from the blocks, keeping unknown
   entry fields. PNG export now writes the current card into the original picture (it used to
   return the imported file unchanged, losing later edits).
5. **Map editor**: DOM bubbles on one transformed layer, culled to the viewport, lines as one SVG
   path, pointer moves applied once per frame. Checked at 1500 blocks: about 80 ms to open,
   60 fps while panning and zooming.

## Consequences

- Retrieval settings are global (Settings → "Промпт"), not per character, by the owner's choice.
- Keyword stemming is a short ending list, not a full morphology; it can over-match short stems.
- Cosine similarity runs in pure Python; fine for thousands of blocks, to revisit if trees grow
  far beyond that.

## Update after the owner's review (migrations 0007, 0008)

- **Free bubbles** (`detached`): a bubble can hang joined to nothing; it and its chain are not
  sent; "✂ Отцепить" sets one free. A line pulled from a port makes a 🔗 link (it used to rehang
  the bubble, which duplicated drag-to-nest and surprised the owner).
- **Order number**: a bubble's place among its neighbours (`position`, renumbered 0..n-1 by
  `PUT /blocks/{id}/order {place}`) is shown as a small number at its corner and typed in the
  panel; numbers never repeat, free bubbles have none. Replaces the ▲▼ buttons.
- **Chance** (green) and **every** (blue). A failed roll is stored in `chat_block_hits.missed` and
  holds for the bubble's sticky replies, so regenerating cannot force a detail in.
- **Links** (`block_links`, a < b): one step, both ways; the pulled bubble brings its blue sub-bubbles,
  never its parents ("тянуть минимум"), and rolls its own chance.
- **Spaces**: opening a bubble shows its subtree with the previous space behind as one frozen,
  blurred layer; only two layers are ever drawn, so depth costs nothing.
- **History** (`character_versions`, `character_steps`, services/history.py): a step stores the
  raw rows / card / link state *before* a change, zlib-packed JSON; undo writes them back. Map
  positions are not history. Versions close on editor close or after 10 quiet minutes.
- **Full tree in the card**: export adds `data.extensions.tavern_tree` (blocks with parents,
  links); import prefers it over the flat `character_book`, so nothing is lost between Tavern
  instances while SillyTavern keeps working with the flat book.

## Layers (stage 10в)

Before, a found green bubble brought its whole subtree, so a deep tree cost as much as a flat
entry and the detail inside could not wait for its topic. Now:

- a found bubble brings only its blue («Всегда») sub-bubbles, and theirs while they stay blue;
- a green («По теме») sub-bubble waits for its own keywords or meaning, found or not found
  regardless of its parent; a deep bubble gets its context through 🔗 links, which the
  assistant draws itself;
- a blue bubble under a green one goes only with it (blue means "always, when the parent goes");
  blue at the top, or under blue only, goes every reply as before.

The rule is the same for everyone at once, old trees included. Green sub-bubbles without keywords
can now be found only by title or meaning, so the doctor lists them per character / lorebook
(`character_tree.bare_layers`). Its fix never switches them to blue: after the usual copy the chat
model writes each its keywords and, when the title is too general, a clear title (each a history
step), then vectors are made if meaning search is set up (`fill_layers`, `doctor.fix_all`). The
command line leaves this fix to the page, since it needs the model.
