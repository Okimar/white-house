---
name: modify-ui
description: How the Tavern browser UI is built and how to change it safely - Preact + htm without a build step, VirtualList, SSE run following, manual browser checks. Use before editing anything under web/.
---

# Modifying the UI

## Rules

- No build step, no CDN, no npm at runtime. Plain ES modules served from `web/`.
- Libraries live in `web/vendor/` (see its README). Import `html`, hooks and `render` only from
  `web/vendor/preact-htm.mjs`. A new library needs an ADR in `docs/adr/` (size, license, why).
- Message text is rendered as text (`${text}`), never as HTML. Do not use `dangerouslySetInnerHTML`.
- The API key never reaches the browser. The UI only sees `has_key`.
- Must work on a phone: layout breaks at 720px, tap targets >= 32px, no hover-only controls.
- Light and dark themes come from CSS variables in `web/styles.css` (`prefers-color-scheme`).

## Layout

| File | Role |
|---|---|
| `web/main.js` | app shell: lists, selected chat, dialogs, toast |
| `web/core/api.js` | `api.get/post/put/del` (throws `ApiError`), `followRun(runId, onEvent)` |
| `web/core/chat.js` | `useChat(chatId, onError)`: messages, paging, send/regenerate/stop/edit/swipe/remove |
| `web/components/VirtualList.js` | windowed list with variable heights, stick-to-bottom, scroll anchoring |
| `web/components/Message.js`, `Composer.js`, `Sidebar.js`, `Settings.js` | views |

## Generation events (SSE)

`POST /api/chats/{id}/send` returns `{run_id}`; `followRun` then receives
`start` (message, user_message) -> `delta` (text) -> `done | stopped | error`, plus `trace`
(progress of plugin generators, ignored by the chat view) and `ping` (filtered in api.js).
The run lives on the server, so reloading the page mid-reply reattaches: `GET /chats/{id}`
returns `run`, and `useChat` follows it again. Deltas are batched into one render per frame.
`useChat` must only react to `start`, `delta` and the three terminal events; reacting to any
other type (e.g. `trace`) as if it ended the run breaks streaming.

## VirtualList pitfalls

- Item identity is `item.id`; heights are cached per id and reset with `resetKey` (chat id).
- The scroll anchor is captured at the end of each commit and on user scroll. If you change
  when it is captured, re-check prepending older messages: the first visible message must keep
  its position on screen.
- A height-changing child must be inside `.vitem` so the ResizeObserver sees it.
- The scroll container needs a bounded height: `.main` has `min-height: 0` for that.

## Verify in a real browser (the test suite cannot see the UI)

```bash
python -m tavern.devtools.fake_llm --port 5001 &
python -m tavern --sandbox --port 8770 --no-browser
```

Open `http://127.0.0.1:8770/`, add a connection with base URL `http://127.0.0.1:5001/v1`, import
or create a character, then check at least: send, streaming with Stop, regenerate, swipes, edit,
delete, reload during a reply, a long chat (scroll to the top loads older pages without jumping),
narrow window (<720px), dark theme, and an empty browser console.

If a system HTTP proxy is configured, bypass it for 127.0.0.1 (it answers an empty 503).
Browser state: only `localStorage["tavern.chat"]` (last opened chat). Everything else is on the
server. Hard-reload (ignore cache) after editing modules; the browser caches ES modules.

## Done

Run `python -m pytest` (backend must stay green), update the table above if files moved.
