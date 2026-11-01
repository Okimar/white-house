# 0011. Tree of thoughts: an agentic reply writer built as a plugin

Status: accepted (stage 8в, 2026-11-01). Builds on 0009 (plugins, model access) and 0008 (speakers).

## Context

The goal set at stage 1: several agents think for the characters, the world and the plot, then the
reply is written. The owner's decisions (2026-11-01):
- **Who thinks:** every character of the scene for themselves, plus the user's own list of thinkers
  (name + task, each can be switched off or removed); «Мир» and «Сюжет» by default.
- **Showing:** folded under the reply, in a side panel, or both, chosen in the settings; live while
  they think.
- **Model:** the chat's by default; the thinkers may get their own connection and model, the reply is
  always written by the chat's model.
- **Rounds:** one by default, up to three; from the second each thinker sees what the others thought.
- **Model access:** like any plugin, the user ticks «may use models» by hand; without it the reply
  ends with the usual refusal message.

## Decision

- A built-in plugin `tavern/builtin_plugins/thoughts` with a reply writer `thoughts`, chosen per chat
  in 👥 «Ответ пишет». It is off until switched on, like every built-in.
- A round runs every thinker at once (`asyncio.gather`) over the standard prompt
  (`job.build_messages()`: cards, lore, notes, history) plus a closing user turn with its task. The
  reply is the standard prompt plus a closing system note with the last round's thoughts.
- Thinkers' clients are forks of `job.llm`, so the reply's budget, «Стоп» and the plugin's access
  gate (its per-reply count included) cover them; with an own connection the requests are metered
  against that connection with the plugin's purpose.
- One thinker failing is marked in the tree and the reply goes on; a refusal of access, a run limit,
  or every thinker failing ends the reply with that message.
- Core additions any plugin can use:
  - `job.meta`: kept with this version of the reply (`swipe_meta`), core keys win. Thoughts live in
    `swipe_meta.thoughts` and are never put into a later prompt.
  - `job.message_id`: the reply being written (for trace events and meters).
  - `trace` events of a run reach plugins' web parts as the page event `"trace"` (with `chatId`).
  - Web places `t.messageTree(fn)` (a folded tree under a message) and `t.panel({...})` (a side panel
    with a top-bar button). Trees are plain data, never HTML.
  - Plugin setting types `list` (rows of small fields) and `connection`; `multiline` for text.

## Consequences

- Each reply costs (thinkers × rounds + 1) requests; the plugin's per-reply limit must allow that.
- Thinkers on another connection are not in the message's usage line (they are on «Расход»).
- Writing agentic plugins: `.claude/skills/agentic-plugin/SKILL.md`.
