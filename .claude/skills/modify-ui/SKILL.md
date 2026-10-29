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
- The look comes from CSS variables in `web/styles.css`; themes override them and style
  `data-slot` places. Give new visible elements a `data-slot` name and use variables, not
  hard-coded colours. Never rename or remove an existing slot or variable without updating
  `.claude/skills/make-theme/SKILL.md` (it is a contract with user themes). Restyling requests
  from the user are theme work: see the `make-theme` skill, not this one.
- The page runs under a strict CSP (`app.py`): no inline `<script>`, no remote URLs, no `eval`.

## Layout

| File | Role |
|---|---|
| `web/main.js` | app shell: lists, selected chat, dialogs, toast |
| `web/core/api.js` | `api.get/post/put/del` (throws `ApiError`), `followRun(runId, onEvent)` |
| `web/core/chat.js` | `useChat(chatId, onError)`: messages, paging, send(text, replyRole)/continue(role)/regenerate/stop, edit/swipe/remove, branches/selectBranch, search, setRoles (ids / numbers / all) |
| `web/core/theme.js` | `applyTheme` (stylesheet, base, effects script with error containment), `emit` events for theme scripts, `SAFE_MODE` (`/?safe`) |
| `web/components/roles.js` | the three roles (user/assistant/system) with icon and label, shared by Composer and Message |
| `web/components/VirtualList.js` | windowed list with variable heights, stick-to-bottom, scroll anchoring |
| `web/components/Message.js`, `Composer.js`, `Sidebar.js`, `Settings.js` | views; Settings has tabs |
| `web/components/Themes.js`, `Gallery.js` | Settings tabs "Оформление" (themes, script approval, character themes, versions) and "Картинки" (image library, generation) |
| `web/themes/<id>/` | built-in themes; `sakura` is the reference for effects |
| `web/components/Members.js` | chat "👥" panel: members with depth / importance / "это я" / may the model write for me, chat preset |
| `web/components/CharacterEditor.js` | create or edit a card; "мой персонаж по умолчанию" (setting `default_me`) |
| `web/components/Presets.js` | Settings tab "Промпт": presets, block list editor, model params, ST import/export |
| `web/components/CharacterTree.js` | "Дерево" tab of a character: bubble map (pan/pinch, drag to move or drop inside another, nested or line display, culling: only bubbles in view are rendered) and list; bubble panel (mode, keywords + "Подобрать", depth, role, sticky) |
| `web/components/CharacterTree.js` (cont.) | ports to pull 🔗 link lines, order number at a bubble's corner (typed in the panel), free bubbles, 🔗 links, chance colours, "⤢" spaces (one blurred layer behind), drop-to-nest switch; restyle via the `style-bubble-map` skill |
| `web/components/CharacterEditor.js` (cont.) | "↶ Отменить" (Ctrl+Z outside text fields) and "🕘 История" (versions, restore); closing ends a version |
| `web/components/Lorebooks.js` | lorebooks: `LorebookShelf` (left panel: list, create, import, download, delete; opens `LorebookEditor`: name, description, undo/history, the book's bubble map = `CharacterTree` with `character={id, name, book: true}`), `BookPicker` (books on for global / chat / character, used in Settings → "Лорбуки", 👥 and the card), `LorebookSettings` (budget, chain steps) |
| `web/components/CharacterTree.js` (cont.) | «Ещё» (`MoreRules`): second keywords, whole words, case, scan, pause, delay, groups, place, chains, character filter |
| `web/components/CharacterTree.js` (cont.) | list view `TreeList`: foldable branches (`tavern.treeOpen.<owner id>` in localStorage), fixed-height rows and only the rows in view are rendered — keep rows one line high (`ROW_H`). New bubbles open in the panel only with the setting `open_new_bubble` |
| `web/components/PromptView.js` | "👁" on a message: what the model saw, exact count, read-only versions with copy |
| `web/components/Notes.js` | author's notes: `NoteList` (one level, also in the "Промпт" tab), the chat "📝" window (Чат / Персонажи / Общие / По номерам, show all hidden notes or delete them), `NotesByNumbers` (picked messages with their kept notes, folded when many). `Message.js` draws kept notes beside the message (`.msg-row` grid: hidden left, visible right; under it below 1200px), folds visible ones after 3 later messages, and has the per-message 📝 toggle |

## Generation events (SSE)

`POST /api/chats/{id}/send` (and `/continue`, `/impersonate`, `/regenerate`) returns `{run_id}`.
`send` takes `reply_role` and `continue` takes `role`: who the generated message is saved as
(the composer's "Ответ от" menu, remembered per chat; `user` means the model writes for you).
Regenerate works on the last assistant or system message. Then
`followRun` then receives
`start` (message, user_message) -> `delta` (text) -> `done | stopped | error`, plus `trace`
(progress of plugin generators, ignored by the chat view) and `ping` (filtered in api.js).
The run lives on the server, so reloading the page mid-reply reattaches: `GET /chats/{id}`
returns `run`, and `useChat` follows it again. Deltas are batched into one render per frame.
`useChat` must only react to `start`, `delta` and the three terminal events; reacting to any
other type (e.g. `trace`) as if it ended the run breaks streaming.

Actions that change the active branch on the server (`POST /chats/{id}/branch`, deleting a
message) are followed by a reload of the newest page inside `useChat`; never reload the browser
page for them. The server answers `busy` to edits, swipes, deletes and branch switches during a run.

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
delete, continue, branch list (⑂) and switching, search with jump to a result,
the composer "Ответ от" menu (system: the reply arrives as ⚙; user: the model writes for you), the role icon
menu on a message, bulk role change (checkboxes, numbers like `1-5, 8`, "Всем"),
reload during a reply, a long chat (scroll to the top loads older pages without jumping),
narrow window (<720px), dark theme, members panel (add, depth, importance, "это я", the
"may write" switch), the 👁 prompt view (versions after editing a card, exact count), presets
(copy, edit blocks, import an ST file, choose per chat), the sakura theme (mascot talks while a reply streams, reacts
to a tap), switching themes back and forth, `/?safe`, and an empty browser console.
Use `--data-dir` with an empty temp folder (or switch the active connection to the fake server
after `--sandbox`): manual checks must never call the owner's real model.

If a system HTTP proxy is configured, bypass it for 127.0.0.1 (it answers an empty 503).
Browser state: `localStorage["tavern.chat"]` (last opened chat), `tavern.replyRole.<chat>`, `tavern.showHiddenNotes` (show all hidden notes), `tavern.treeView`, `tavern.treeNest` (drop on a bubble nests it) and
`tavern.theme` (last theme's look, to avoid a flash before settings load). Everything else is
on the server. Hard-reload (ignore cache) after editing modules; the browser caches ES modules.

## Done

Run `python -m pytest` (backend must stay green), update the table above if files moved.
