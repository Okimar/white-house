# C8

One constant entry `JB-TEXT`: position «after character definitions» (↓Char, position 1).

`preset.json` = ST's shipped `Default` chat-completion preset, unchanged.

Common to every case: ST 1.18.0, empty data root, UI extensions off (config `extensions.enabled: false`).
API: Chat Completion -> Custom (OpenAI-compatible), URL http://127.0.0.1:5003/v1, model `gemini-3.8-flash-high`,
streaming on (ST default). User persona: default `User`, no description. Character «Ава» from `card.json`.
Lorebook `JB` (`lorebook.json`, read back from ST after saving) switched on globally (`/world JB`); global World
Info settings untouched (ST defaults). The entries hold only the fields listed (ST filled the rest with its own
defaults at run time); the recorded request was made with exactly this file. The chat in `chat.json` (the first assistant message is the greeting;
the second assistant message was the fake reply edited to the text shown). The last user message is the one sent;
`request.json` is the body ST sent for it.
