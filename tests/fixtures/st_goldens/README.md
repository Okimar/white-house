# SillyTavern golden requests

What SillyTavern sends to a Chat Completion model for small synthetic inputs, so this app can be compared
with it (`python -m tavern.devtools.compare_requests`). Data only: no SillyTavern code was read for this
or copied here.

Each case folder `C0` … `C11` holds:

| File | What |
|---|---|
| `card.json` | the character «Ава», ST's JSON export (V3 card) |
| `lorebook.json` | the world `JB` as ST stored it for this case |
| `preset.json` | the chat-completion preset (see the case's `notes.md`) |
| `chat.json` | the chat, written by hand: `[{role, name, text}]`, last message = the one sent |
| `notes.md` | what was set for this case |
| `request.json` | the body ST sent to the model (from `tavern.devtools.capture`) |
| `st-console.log` | the same request as ST printed it in its console (log level 0), for a cross-check |

## How they were made (2026-11-13)

- SillyTavern **1.18.0** (`SillyTavern/package.json`), Node v24.20.0.
- ST started with its **own config copy and an empty data root** (the owner's install, `config.yaml` and data
  were not used):
  `node server.js --configPath <scratch>/st-config.yaml --dataRoot <scratch>/st-golden-data --port 8123 --listen false --browserLaunchEnabled false --whitelist false`
  where `st-config.yaml` is a copy of `SillyTavern/default/config.yaml` with `extensions.enabled: false`,
  `extensions.autoUpdate: false`, `browserLaunch.enabled: false` (`logging.minLogLevel` is already 0 there).
  Extensions are off because the install has third-party UI extensions in its global folder; with them off
  the request is ST core only.
- Model = `python -m tavern.devtools.capture --port 5003 --out <scratch>/captures` (fixed fake reply,
  streamed; secrets stripped).
- In ST (browser): API Chat Completion → source Custom (OpenAI-compatible), URL `http://127.0.0.1:5003/v1`,
  model `gemini-3.8-flash-high`, Connect. Preset `Default`. Persona: default `User`.
- Character «Ава» created (description + greeting, nothing else; see `card.json`). New chat: the greeting.
- Sent user message 1; the fake reply was edited to «Конечно! Follow me, the map room is upstairs.».
- World `JB` created and switched on globally with `/world JB`.
- For every case: write `JB`'s entries as in the case's `lorebook.json` (and the preset change if any), send
  user message 2 «Что там на карте? What is drawn near the river?», keep the captured request, delete the
  user message 2 and its reply, so each case starts from the same three messages.
- Entries hold only the fields in `lorebook.json` (`uid, comment, constant, key, content, position, depth, role,
  order`); ST fills the rest with its defaults. Position codes: 0 = before character, 1 = after character,
  4 = at depth; role 0 system, 1 user, 2 assistant. Order defaults to 100 where not given.
- C9 / C10: ST wrote the saved presets as `{}`, so their `preset.json` is `Default` plus the case's change made
  by hand (C9: `squash_system_messages: true`; C10: a prompt `golden_inchat`, content `PRESET-INJECT`,
  role system, `injection_position: 1` (in-chat), depth 1, order 100, enabled in `prompt_order` 100001).
  The requests were recorded with those changes live in ST. C10 has squash off.

To re-record: same steps; generation, World Info and preset settings not mentioned stay at ST defaults.

## What the requests show

Baseline C0 (7 messages): `system` main prompt, `system` description, `system` «[Start a new Chat]»,
`assistant` greeting, `user` msg 1, `assistant` reply, `user` msg 2. Params: temperature 1, max_tokens 300,
stream true, presence/frequency penalty 0, top_p 1.

| Case | Where `JB-TEXT` landed |
|---|---|
| C1 @D depth 0 system | separate `system` message after the last user message (end of the request) |
| C2 @D depth 1 system | separate `system` message between the last assistant reply and the last user message |
| C3 @D depth 1 user | separate `user` message at the same place; not merged with the following user message |
| C4 @D depth 1 assistant | separate `assistant` message right after the assistant reply; not merged with it |
| C5 @D depth 2 system | `system` message between user msg 1 and the assistant reply |
| C6 two @D depth 1 system | ONE `system` message `JB-TEXT-B50\nJB-TEXT-A100`: same depth+role joined with `\n`, order 50 before order 100 |
| C7 before char | own `system` message between main prompt and description |
| C8 after char | own `system` message after description, before «[Start a new Chat]» |
| C9 squash ON | main prompt + description squashed into one `system` message (`\n`); «[Start a new Chat]» stays its own `system` message; JB at depth 1 stays its own message (no system neighbour) |
| C10 preset in-chat d1 + JB d1 | ONE `system` message `PRESET-INJECT\nJB-TEXT` at depth 1: preset prompt first, then lore |
| C11 keyword `river` | triggered by user msg 2; landed like C2 |

Lore at a depth is sent bare: no book or entry title around the text.
