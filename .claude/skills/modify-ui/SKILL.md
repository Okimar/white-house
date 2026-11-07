---
name: modify-ui
description: How the Tavern browser UI is built and how to change it safely - Preact + htm without a build step, VirtualList, SSE run following, what to verify and how. Use before editing anything under web/.
---

# Modifying the UI

**Read `.claude/skills/mobile-ui/SKILL.md` first**: the app is used by touch on a phone and a tablet; its rules (touch drags, 44px, no jank, no blinking maps, settings order, test by numbers) apply to every change here.

## Rules

- No build step, no CDN, no npm at runtime. Plain ES modules served from `web/`.
- Libraries live in `web/vendor/` (see its README). Import `html`, hooks and `render` only from
  `web/vendor/preact-htm.mjs`. A new library needs an ADR in `docs/adr/` (size, license, why).
- Message text is rendered as text (`${text}`), never as HTML. Do not use `dangerouslySetInnerHTML`.
- The API key never reaches the browser. The UI only sees `has_key`.
- Must work on a phone: layout breaks at 720px, tap targets >= 32px, no hover-only controls. There the left panel's sections open as a sheet from small icons in the top bar (`data-section`).
- Spacing on a 4px grid, sizes from `--text-*`, words from `docs/UI_AUDIT.md`: «вы», «Помощник» is the window, the model's role is «Персонаж», «Копия» is only a backup.
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
| `web/components/Icon.js` | `Icon`, `IconButton` (a label is required): one set of line icons, `web/vendor/lucide-icons.mjs` (ADR 0014). No emoji as icons; plugins' own `icon` text stays |
| `web/components/Menu.js` | `openMenu(anchor, items)`, `MenuButton`: a drop-down at the end of body, so no list or dialog cuts it off |
| `web/components/Select.js` | `<${Select} value onChange aria-label>` + `<option>` / `<optgroup>` children: our own drop-down in place of the native `<select>` (never use `<select>`; a test checks). `onChange` gets `{target:{value}}`; list under the field or above, filter field over 10 options, keys, themed (`select-list` slot) |
| `web/components/Dialogs.js` | `await ask(text, {ok, danger, hint})`, `await askText(text, {value})`: use these, never `confirm()` / `prompt()`; `DialogHost` is in the shell |
| `web/components/State.js` | `useLoad`, `Pending` (a failed load shows why and «Повторить», never an endless «Загрузка…»), `EmptyNote` |
| `web/core/notify.js`, `web/core/keys.js` | `notify(text, "ok"/"info"/"error")`: only errors are red. `useEscape(close)`, `backdrop(close)` for every window: Esc closes the newest, a text selection dragged out of a window does not close it |
| `web/core/messageActions.js` | a message's buttons: settings `message_actions` ("menu" / "always") and `message_pinned` decide the panel and the "…" list |
| `web/core/theme.js` | `applyTheme` (stylesheet, base, effects script with error containment), `emit` events for theme scripts, `SAFE_MODE` (`/?safe`) |
| `web/components/roles.js` | the three roles (user/assistant/system) with icon and label, shared by Composer and Message |
| `web/components/VirtualList.js` | windowed list with variable heights, stick-to-bottom, scroll anchoring; `jumpTo={id}` holds that item at the top while the heights above it are measured (a search find, after `chat.reveal(message)` loaded its branch and pages) |
| `web/components/Message.js`, `Composer.js`, `Sidebar.js`, `Settings.js` | views; Settings has tabs. Composer: 📎 / paste adds library pictures to the message (`send(text, role, images)`); Message shows `swipe_meta.reasoning` folded (💭) and `swipe_meta.images` |
| `web/components/Profiles.js` | profiles block and "Несколько запросов сразу" (Settings → Подключения); `PickVariants` opens when a run ends with `done.pick` (state `chat.pick`, `GET /messages/{id}/swipes`); `variants_failed` events become an error toast; `ProfileSwitch` in the top bar (remounted when Settings closes), `FallbackSteps` in the connection editor; Message shows `swipe_meta.via` |
| `web/components/Skills.js` | Settings tab «Инструкции»: skills grouped built-in / own / plugins / programmer, switch each, built-in ones read-only with «Сделать копию» (a copy of a built-in replaces it for the assistant), own ones edited (name, title, when to use, audience, English text with ## Plan / ## Write / ## Check). The assistant's «📖» line opens it on that skill through localStorage `tavern.skill` and `onOpenSkills` |
| `web/components/Templates.js` | Settings tab "Шаблоны": instruct templates (built-in read-only, copy to edit), fields, live preview, import ours / ST instruct / ST context (optionally into the open template), export |
| `web/components/Connections.js` | Settings tab "Подключения": service picker, editor (address, keys with proxies and order, model facts, «Проверить» / «Пробные запросы», log, settings console with ✓ ✗ ? per setting) |
| `web/components/Themes.js`, `Gallery.js` | Settings tabs "Оформление" (themes, script approval, character themes, versions) and "Картинки" (image library, generation) |
| `web/themes/<id>/` | built-in themes; `sakura` is the reference for effects |
| `web/components/Members.js` | chat "👥" panel: members with depth / importance / "это я" / may the model write for me, chat preset; «Кто говорит» (any chat may have many characters: the order, what the model knows about the others), per character talkativeness / «молчит» / short description / 🗣 «ответь ты». `main.js` loads the members for per-reply avatars (`message.character_id`) and the «Ответит:» buttons (`chat.continue(role, speaker)`); `send` with 2+ characters in the order «manual» returns no run, only the message |
| `web/components/CharacterEditor.js` | create or edit a card; "мой персонаж по умолчанию" (setting `default_me`) |
| `web/components/Presets.js` | Settings tab "Промпт": presets, block list editor, model params, ST import/export |
| `web/components/CharacterTree.js` | "Дерево" tab of a character: bubble map (pan/pinch, drag to move or drop inside another, nested or line display, culling: only bubbles in view are rendered) and list; bubble panel (mode, keywords + "Подобрать", depth, role, sticky) |
| `web/components/CharacterTree.js` (cont.) | ports to pull 🔗 link lines, order number at a bubble's corner (typed in the panel), free bubbles, 🔗 links, chance colours, "⤢" spaces (one blurred layer behind), drop-to-nest switch; restyle via the `style-bubble-map` skill |
| `web/components/CharacterEditor.js` (cont.) | "↶ Отменить" (Ctrl+Z outside text fields) and "🕘 История" (versions, restore); closing ends a version |
| `web/components/Lorebooks.js` | lorebooks: `LorebookShelf` (left panel: list, create, import, download, delete; opens `LorebookEditor`: name, description, undo/history, the book's bubble map = `CharacterTree` with `character={id, name, book: true}`), `BookPicker` (books on for global / chat / character, used in Settings → "Лорбуки", 👥 and the card), `LorebookSettings` (budget, chain steps) |
| `web/components/CharacterTree.js` (cont.) | «Ещё» (`MoreRules`): second keywords, whole words, case, scan, pause, delay, groups, place, chains, character filter |
| `web/components/CharacterTree.js` (cont.) | list view `TreeList`: foldable branches (`tavern.treeOpen.<owner id>` in localStorage), fixed-height rows and only the rows in view are rendered — keep rows one line high (`ROW_H`). New bubbles open in the panel only with the setting `open_new_bubble` |
| `web/core/plugins.js` | web parts of plugins: `syncPlugins(list)` loads/unloads them, `usePluginUi()` (message buttons, message footer lines, composer buttons), `setPluginEnv` (toast, command, chat id, reload), `pluginNotice` (server → plugin). Contract for plugin authors: `.claude/skills/create-plugin` |
| `web/components/Assistant.js` | the 🪄 assistant window (opened from the sidebar head or the top-bar mark «🪄 работает / ждёт вас», `GET /assistant/state`, refreshed on the notice `assistant_state`): dragged by its header (position in localStorage), full width under 700px, folded with ▁; conversation list, timeline of messages and journal lines (`Отменить` undoes that action and later ones), «Разрешить / Нет»; big work as cards by `message.data.kind`: `questions` (options, «Своё…», «На твой вкус» → `POST …/answers`), `plan` (ticks items from the live `task`, «Давай» → `POST …/go`, «Отменить задачу»), `progress` / `step` / `critic` lines (setting «Ход работы»: full or minimal), `remembered` (🧠 with «Отменить»), `report`; typing while items are done goes into the plan; ⚙ model, roles, critic, limits, «Что я о вас знаю». It follows the run it started itself (a quick run may end before a reload would see it); a card may arrive before its `task` event. `main.js` reloads lists, the open chat and settings on the notice `assistant_changed`, fires `tavern:lorebooks` for the shelf, and opens a chat / character on `assistant_show` (through `assistantRef`: the event stream outlives renders). Whatever a person can do on the page the assistant reaches through the same HTTP route (`services/assistant_app.py`): a new button needs a route, not a new tool |
| `web/components/Plugins.js` | Settings tab "Дополнения": cards (switch, approval dialog with the full-access warning, settings fields, model access and spending, updates, delete), install from a file or a GitHub link; `PluginAsks` = «хочет обратиться к модели» window. `main.js` keeps one `EventSource("/api/events")` open (types in `core/notices.py`), hides `hidden` messages, sends "/команда" to `POST /plugins/command` when a plugin owns it; 👥 «Ответ пишет» picks `chat.meta.generator` |
| `web/components/Backups.js` | Settings tab «Копии» (`BackupsTab`: triggers, keep counts, own folder, list with download / restore / delete / «На Диск» and ☁, upload, `GoogleDrive`: guide, app pass, sign-in, copies on Drive, the doctor) and `BackupWarning` (top line from `GET /backups/status`). `main.js` refreshes it on the notice `backups` (also fires `tavern:backups`) and reloads the page on `restored`; a page opened with `?code&state` (back from Google) posts the address to `/backups/gdrive/finish`; `setShowSettings("backups")` opens Settings on that tab |
| `web/components/PromptView.js` | "👁" on a message: what the model saw, exact count, read-only versions with copy |
| `web/components/Notes.js` | author's notes: `NoteList` (one level, also in the "Промпт" tab), the chat "📝" window (Чат / Персонажи / Общие / По номерам, show all hidden notes or delete them), `NotesByNumbers` (picked messages with their kept notes, folded when many). `Message.js` draws kept notes beside the message (`.msg-row` grid: hidden left, visible right; under it below 1200px), folds visible ones after 3 later messages, and has the per-message 📝 toggle |

## Generation events (SSE)

`POST /api/chats/{id}/send` (and `/continue`, `/impersonate`, `/regenerate`) returns `{run_id}`.
`send` takes `reply_role` and `continue` takes `role`: who the generated message is saved as
(the composer's "Ответ от" menu, remembered per chat; `user` means the model writes for you).
Regenerate works on the last assistant or system message. Then
`followRun` then receives
`start` (message, user_message) -> `delta` (text) and `reasoning` (the model's thinking) -> `done | stopped | error`, plus `trace`
(progress of plugin generators, ignored by the chat view) and `ping` (filtered in api.js).
The run lives on the server, so reloading the page mid-reply reattaches: `GET /chats/{id}`
returns `run`, and `useChat` follows it again. Deltas are batched into one render per frame.
A *turn* of several replies (`POST /chats/{id}/talk`, or the order «все сразу») sends `turn_start`,
then per reply `start` / `delta {id}` / `reply_done` | `reply_error`, and ends with a terminal event
carrying `reload: true` (the newest page is reloaded: a round may reorder or join replies).
`useChat` keeps `streams` (id -> text) so several replies can stream at once.
`useChat` must only react to `start`, `delta`, the turn events above and the three terminal events; reacting to any
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

## Verify: in the browser on the fake model

Agents check in the browser with chrome-devtools on the FAKE model only (AGENTS.md rule 9): one agent at a
time (the selected page is shared), `--data-dir` on an empty temp folder, a connection to
`python -m tavern.devtools.fake_llm`, never the live model unless the owner asks. Follow a written checklist,
no improvising. Phone size is 375x667, DPR 2, mobile, touch (`emulate viewport 375x667x2,mobile,touch`); also
375x553 (browser bars) and 375x410 (keyboard open: the message box must stay visible). Read
`[innerWidth, innerHeight, devicePixelRatio, matchMedia('(pointer: coarse)').matches]` after every reload: it
must be 375, 667, 2, true. Judge by numbers, not by how it looks on a big monitor: buttons at least 44x44,
close buttons reachable without scrolling, text at least 14 px, no element wider than the viewport. Also run
`python -m pytest` (test_web_syntax parses every module) and add source-level tests where sensible. Anything
not seen on screen is reported as unverified. The list below is what to check:

```bash
python -m tavern.devtools.fake_llm --port 5001 &
python -m tavern --sandbox --port 8770 --no-browser
```

Open `http://127.0.0.1:8770/`, add a connection with base URL `http://127.0.0.1:5001/v1`, import
or create a character, then check at least: send, streaming with Stop, regenerate, swipes, edit,
delete, continue, branch list (⑂), switching and «Новая ветка отсюда», search with jump to a result (also one not loaded yet or on another branch), renaming a chat,
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

## Added in the 2026-11-07 batch

- New components: Changes, Groups, Regex, Search, Updates (web/components); web/core/layout.js, choice.js, regex.js.
- "Text is never HTML" has one exception: regex rules. The server cleans the rule's HTML into a tree (core/safe_html.py) and the page builds elements from that tree with no `innerHTML` (web/core/regex.js). Do not widen it.
- New localStorage keys: `tavern.menuMode`, `tavern.menuFolded`, `tavern.searchScope`, `tavern.settingsChoice`, `tavern.settingsView`. Wrap in try/catch like the others.
- `api.get(path, signal)` and `api.post(path, body, signal)` take an AbortSignal; cancel outdated searches.
- `Assistant` takes an `openMsg` prop (jump to a found message).
