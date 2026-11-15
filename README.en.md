# White House

An AI roleplay chat frontend for people who play on their phone. It installs in Termux with one line and opens your SillyTavern cards, lorebooks and presets.

The interface is in Russian only for now. [Русский](README.md)

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)

## Install in Termux

```bash
curl -fsSL https://raw.githubusercontent.com/Okimar/white-house/main/get.sh | bash
```

The script installs `git` and `python`, downloads the app and creates a start command:

```bash
white-house
```

Then open <http://127.0.0.1:8000/> in your browser. To update, run the same line again. Your data stays where it is.

> With `pkg install termux-api` and the Termux:API app, the browser opens on its own.

<details>
<summary>Windows, Linux, macOS</summary>

Windows (needs [Python 3.11+](https://www.python.org/downloads/)):

```bat
git clone https://github.com/Okimar/white-house.git
cd white-house
start.bat
```

Linux and macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/Okimar/white-house/main/get.sh | bash
```

The first run installs everything it needs.
</details>

## How it differs from SillyTavern

### Installing on a phone

The server is written in Python and the page has no build step, so you don't need Node.js or `npm install`. Every dependency is a ready-made pure Python package, about a megabyte in total, so your phone has nothing to compile.

### Long chats

The page keeps only the messages near the screen and loads older ones in parts as you scroll up. A chat with thousands of messages isn't loaded in full when you open it.

### Several characters in any chat

There is no separate group mode: you can add a character to any chat. The next speaker is picked by names in the text, talkativeness, turn order, the model or you. You can let the characters talk among themselves, have everyone answer at once, or bring in a narrator.

### An assistant inside the app

Ask it to build a lorebook or a character, change the theme or read a chat. It also works with weak models, and you can undo anything it changes.

### Also included

- Characters and lorebooks are trees of blocks. A block fires always, by keywords (with Russian word endings handled), by meaning, or in a chain from another block. Any edit to a card can be rolled back.
- One connection can hold several keys, each with its own proxy. If a model fails, a fallback continues the chat.
- Gemini and Claude connect through their own protocols, which gives you thinking, pictures and web search.
- Spending is counted in tokens and money per message, chat, day and month, with limits you can set.
- Tree of thoughts: before a reply, several thinkers discuss the scene, and you can see it under the message.
- Copies of the whole data folder run on a schedule and before risky actions, including to Google Drive through your own OAuth client.
- Themes install as a folder or a single `.tavern-theme` file, and plugins from a file or GitHub. Each plugin needs your approval.

## Compatibility

It opens PNG and JSON cards (V1–V3), SillyTavern worlds, presets and instruct templates.

## Not there yet

If you need any of these, stay on SillyTavern for now:

- importing SillyTavern chats or a whole SillyTavern folder;
- regex scripts and summarization;
- SillyTavern extensions, which don't run here;
- an English interface.

## First steps

The button names below are the Russian ones you will see in the app.

1. Press «Добавить подключение» (add connection), choose a service, enter your key and model.
2. In «Персонажи» (characters), create a character or upload a card.
3. Press the character and choose «Новый чат» (new chat).

## Access from another device

By default the server listens only on this device. For local network access, set `host = "0.0.0.0"` in `data/config.toml` or start with `--host 0.0.0.0`. A link like `http://IP:8000/login?token=...` appears at startup: open it once on the other device. To change the token: `python -m tavern token --rotate`.

## Data and rollback

Everything lives in `data/`.

```bash
python -m tavern backup create        # snapshot now
python -m tavern backup list
python -m tavern backup restore NAME  # the server must be stopped
python -m tavern doctor --fix         # check and repair
python -m tavern --sandbox            # try it on a temporary copy of the data
```

## For developers and AI agents

Starlette and SQLite in pure Python, with a Preact interface and no build step. Start with [AGENTS.md](AGENTS.md); status: [docs/STATUS.md](docs/STATUS.md), plan: [docs/ROADMAP.md](docs/ROADMAP.md). Tests: `python -m pytest`.

Written from scratch, with no SillyTavern code, from public data formats.

## License

[AGPL-3.0](LICENSE)
