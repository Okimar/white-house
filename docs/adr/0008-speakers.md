# 0008. Who speaks in a chat, and what the model knows about the others

Status: accepted (stage 7а, 2026-10-31). Extends 0003 (chat members with depth and importance).

## Context

Since stage 4а a chat can have any number of characters (members with depth and importance), but
every reply was written by the chat's main character. For the owner several characters in a chat
is the normal state, not a special "group chat" mode: there is no switch between the two, the same
settings exist in every chat and simply matter once there are two or more characters. The owner wants every way of choosing the
next speaker as a per-chat setting, a "ответь ты" for any character, and a choice of what the model
sees about the characters it is not writing for: only the speaker, plus names, plus short
descriptions, or every full card. Stage 7б adds several replies per turn (characters talking among
themselves, everyone at once, a narrator), and stage 8 agents playing each character; both build on
"one reply = one speaker" decided here.

## Decision

- **A reply knows its author**: `messages.character_id` (migration 0013, `ON DELETE SET NULL`). Old
  replies are matched to members by name once, at upgrade; code that meets a reply without it
  (imports) still matches by name (`group.author`). The name stays stored with the message, so a
  deleted character keeps its name in the chat.
- **Settings** live in `chats.meta["speakers"]` = `{order, others}` (`services/speakers.py`), changed by
  `PUT /api/chats/{id}/speakers`. Member settings are columns of `chat_members`: `talkativeness`
  (0..100, default 50), `muted`, `blurb` (≤ 500 characters).
- **No mode**: the rules apply to every chat. With one character (not "me") it answers and the
  prompt is as before; with two or more (`speakers.several`) replies go with names and the model is
  told whom it writes for. The 👥 window always shows «Кто говорит», the chat always the «Ответит:»
  buttons.
- **Order**: `natural` (the first member named in the last message, by the first word of the name
  with any ending, `core/retrieval.keyword_pattern`; a character naming itself does not answer
  itself; nobody named: each of the others is willing with the chance of their talkativeness, one
  willing is drawn, none willing: the most talkative), `turns` (list order after the last speaker;
  muted ones are skipped), `model` (a `generate_json` request with the names as an enum, stage 6ж;
  charged as `purpose="speaker"` to the reply's spending; any failure falls back to natural and is
  traced), `manual` (sending only saves the message: the API answers `{"run_id": null, message}`).
  "Продолжить" without a speaker uses the order (manual acts as natural). Regenerate keeps the
  reply's author.
- **"Ответь ты"**: `speaker` on `/send` and `/continue`; works in every order and for a muted
  member. A chat where every character is muted answers 409 `nobody_to_answer` before anything is
  written.
- **Prompt**: the speaker is the primary member (`{{char}}`, card overrides). `others`: `none` /
  `names` / `blurbs` send only the speaker's and "me"'s cards (and only their bubbles, lorebook
  links and character notes), plus an "Also in the scene:" entry in the character block for names
  or blurbs; `full` sends every unmuted card as in 4а. Muted members are always out. With two or more
  characters, other characters' replies go as `Name: text` (so the model does not take them as its own), and
  the preset text `speaker_reply` (default "[Write the next reply only as {{char}}. ...]", imported
  from ST's `group_nudge_prompt`) is added last unless the model writes for the user. The default
  `others` is `none`: the owner's first choice.

## Stage 7б: several replies in one turn

- **One reply writer, many uses**: `chat._writer` writes one reply inside a run. `final=True` is the
  old single-reply run (start / delta / done | stopped | error). Otherwise it is part of a *turn*
  (`chat._start_turn`): events `turn_start`, then per reply `start` / `delta {id}` / `reply_done` |
  `reply_error`, then `done | stopped | error` with `reload: true` (the client reloads the newest page).
- **«Пусть поговорят»** (`POST /chats/{id}/talk {count?}`): replies by the chat's order, one after
  another, until `talk_limit` (setting, 1..50, default 4) or the count asked, Stop, an error or the
  spending limit (checked before every reply; the stop setting ends the turn with an error).
- **Order "all"** (everyone at once): sending, «Продолжить» and every step of «пусть поговорят»
  make a *round*: one empty reply per character that may speak, chained in list order, written at
  the same time; each sees the chat up to the round (`skip_ids`), not the others' replies. Then
  `_arrange` puts them in order: `together` = `finished` (who finished first is higher), `list`
  (list order) or `one` (joined into the first: `Name: text` blocks, name "A, B", character_id empty,
  `extra.together` so the prompt does not prefix it again, usage summed). Empty replies are
  dropped; `chats.rechain` re-links the chain in one transaction, so Stop in the middle leaves a
  whole chain with the partial texts.
- **Narrator** (`narrator.enabled`, its own name, connection and model; empty = the chat's): after
  a round one more request (`speakers.narrator_prompt`: the cast, the chat before the round, the
  round's replies, the preset text `narrator`) writes the scene as a system message under the
  narrator's name; its prompt goes to "what the model saw", its cost to spending as `narrator`.
- Parallel requests of 6д (variants) apply to single replies only.

## Consequences

- **Characters brought in by code**: plugins and agents (stage 8) add a character to the scene with
  `cast.add_member` (the same checks and the `chat.members_changed` event as the UI; the UI reloads
  the members after every reply) and hand it the turn with `speaker=`, or pick with
  `speakers.choose` / `speakers.ask_model`; a stage 8 game master can take the narrator's place; a "round" of several replies (7б) is a sequence or a gather of such replies.
- Chats from before this stage that had two characters change behaviour: they now show only the
  speaker's card until "others" is set to full cards.
- ST group files (members, order, talkativeness) are imported with the ST folder in stage 11.
