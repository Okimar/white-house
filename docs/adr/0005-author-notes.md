# 0005. Author's notes: three levels, hidden marks and notes saved in the history

Status: accepted (stage 4в, 2026-10-28)

## Context

The owner wants notes that steer the story: global, per character and per chat, each sent at a
depth, as a role, every N replies. Some must be seen by the model only once ("hidden"); others
must stay in the history where they were placed. The user must be able to see where hidden notes
went, clear those marks, and edit saved notes in bulk by message numbers ("1-52 68-72"). Agents
must be able to do all of it.

## Update after the owner's review (migration 0006)

Three kinds instead of two: `once` (sent only in the request it fired in, nothing kept; the old
"hidden" with its marks), `hidden` (kept in the history, out of sight in the chat) and `visible`
(kept and shown beside its message, folded after 3 later messages). Marks are gone. The interval
is now the number of replies skipped between sendings, 0 = every reply. A note is attached to a
message at most once (`UNIQUE (message_id, note_id)`), whatever its kind. Points below are the
original decision; where they say "saved" read "hidden or visible".

## Decision

1. **Storage** (migration 0005): `author_notes` (scope global/character/chat with real foreign
   keys to characters and chats, so notes go away with their owner; depth, role, interval, mode,
   enabled) and `message_notes` (a note after a message: kind `saved` = part of the history,
   `mark` = where a hidden note went, never sent). `note_id` is kept without a foreign key, so a
   saved note outlives the note it came from. `(message_id, note_id, kind)` is unique.
2. **Firing** (`services/notes.py`): a note fires when the reply number (1 + replies already on
   the branch) divides by its interval. The assembler places fired notes at their depth and says
   after which message each landed (`Prompt.placed`). Saved notes attached to history messages
   travel with their message, so budget trimming drops them together.
3. **After the reply is saved**, placements are recorded: a saved note is attached, a hidden one
   leaves a mark. A regenerated reply fires again at the same place; the unique key turns that
   into an update, and the assembler does not send a saved note twice at a place where it is
   already attached.
4. **Edits by numbers** reuse the role editor's number parser (1-based positions on the active
   branch). "One note for all" replaces the picked messages' saved notes.
5. Our chat file (JSONL) carries `chat_note` and `message_note` records. SillyTavern's author's
   note comes with SillyTavern chat import, which does not exist yet.

## Consequences

- A saved note with interval 1 adds one note to the history per reply; that is the point of
  "saved", and the user can remove them by numbers.
- A note deeper than the whole chat goes before the first message and cannot be attached
  anywhere, so as a saved note it is sent but not kept.
- Every change fires `notes.changed` or `message_notes.changed` for plugins and the assistant;
  automatic placements after a reply do not fire an event (the reply's own events cover them).
