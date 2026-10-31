---
name: make-theme
description: How to build or change a Tavern theme from a text request - theme folder anatomy, CSS variables and named slots, scene/overlay layers, effects script API and events, generating and placing pictures, paper-doll animated characters, performance budget for phones, safety, sharing. Use whenever the user asks to restyle the interface ("make it pretty", "add an animated character", "sakura background").
---

# Making a theme

A theme restyles Tavern without touching app code. The user asks in words ("make the chat look
like a night garden with a girl who reacts to replies"); you build a theme folder. The app must
keep working whatever the theme does, and it must stay smooth on a cheap phone.

## Where themes live

| Kind | Folder | Editable |
|---|---|---|
| Built-in | `web/themes/<id>/` (default, light, dark, sakura) | No. Change these only when the task is about the app itself |
| User | `data/themes/<id>/` | Yes. Every user request goes here |

To start from an existing look: `POST /api/themes {"name": "...", "from": "sakura"}` (or the
"Копия" button). Edit files directly on disk, or with `PUT /api/themes/<id>/files/<path>` (raw
body). The browser revalidates theme files on every load, so a page reload shows the edit. Hard
reload if a script was edited. **Never touch the real `data/` while developing the app**; for a
user's own theme request, their `data/themes/` is the target (sandbox first if unsure).

```
data/themes/night-garden/
  theme.json        manifest (required)
  theme.css         styles (loaded after web/styles.css)
  script.js         effects script (optional, needs the user's approval)
  preview.png       card picture in Settings (optional, any image type)
  assets/...        pictures, fonts, sounds (png jpg webp gif svg woff2 woff ttf otf mp3 ogg wav)
```

`theme.json`:

```json
{"format": "tavern-theme", "format_version": 1, "id": "night-garden", "name": "Ночной сад",
 "author": "", "version": "1", "description": "", "license": "", "base": "dark",
 "script": "script.js", "preview": "preview.png"}
```

`base` is `auto`, `light` or `dark`: which built-in colour set sits underneath. Allowed files are
listed in `tavern/formats/media.py`; no HTML, nothing hidden, paths at most 6 levels deep, 10 MB
per file, 30 MB per theme.

## Styling: variables first, slots second

1. Override variables in `:root { ... }` (a plain `:root` always beats the base; see the
   specificity note at the top of `web/styles.css`):

| Variable | Meaning |
|---|---|
| `--bg --panel --fg --muted --line --accent --accent-fg --danger` | core colours |
| `--user --bot` | message colours (fallbacks for the two below) |
| `--app-bg --sidebar-bg --topbar-bg --composer-bg --dialog-bg --overlay-bg` | surfaces, may be gradients or `url(...)` |
| `--msg-user-bg --msg-bot-bg --msg-border --msg-shadow` | message bubbles |
| `--font --font-size --line-height` | type |
| `--radius --radius-sm --shadow` | shape |
| `--sidebar-width --chat-width --avatar-size --tap` | sizes (`--tap` = minimum touch target) |
| `--mode-always --mode-search --mode-off` | character-tree bubble rings: blue / green / grey |
| `--chance-75 --chance-50 --chance-25 --chance-0` | bubble map line colours by the chance of a green bubble |

2. Then style named places with `[data-slot="..."]`. These names are a stable contract:

| Slot | Element | Extra attributes |
|---|---|---|
| `app` | whole app | `data-chat-open` |
| `sidebar` | left panel | |
| `chat` | chat column | `data-running` (model is writing) |
| `scene` | layer behind the chat | |
| `overlay` | layer above the chat | |
| `topbar`, `title` | header and chat title | |
| `message` | one message | `data-role` (user/assistant/system), `data-streaming` |
| `message-header`, `message-avatar`, `message-name`, `message-body` | parts of a message | |
| `message-thinking`, `message-images` | the model's folded thinking (`details.thinking`), pictures sent with a message | |
| `chat-actions` | Continue / roles row | |
| `composer`, `composer-input`, `send-button`, `stop-button` | input area | |
| `attach-button`, `composer-images` | 📎 and the pictures waiting to be sent | |
| `template-editor`, `template-preview` | Settings → Шаблоны | |
| `parallel`, `pick-variants` | parallel requests settings, the side-by-side choice of versions | |
| `profile-switch`, `profiles`, `fallback-steps`, `message-via` | profile picker in the top bar, profiles and fallback steps in Settings, "↪ через …" on a reply | |
| `conn-list`, `conn-new`, `conn-editor`, `conn-keys`, `conn-log`, `conn-console`, `model-facts` | Settings → Подключения (the console: `.param.yes/.no/.unknown`, `.param-mark`) | |
| `empty`, `dialog`, `toast` | empty screen, settings dialog, error popup | |
| `members-button`, `member` | chat 👥 button, one member in the members panel | |
| `prompt-part`, `preset-block` | a part in "what the model saw", a block in the preset editor | |
| `character-tree`, `tree-panel`, `tree-crumbs` | the bubble map of a character, the bubble editor panel, the path of opened spaces — details in the `style-bubble-map` skill | |
| `character-history` | the versions list of a character or a lorebook ("🕘 История") | |
| `tree-more` | «Ещё» in the bubble panel: the finer options, folded | |
| `lorebooks`, `book-head`, `book-picker`, `lorebook-settings` | the "Лорбуки" part of the left panel, the name/description row of a book window, the list of books switched on for a place (⚙, 👥, card), Settings → "Лорбуки" | |
| `notes-more`, `attached-note` | the "ещё N" chip beside a message, one note in the notes drop-down | |
| `notes-button`, `note-list`, `note` | chat 📝 button, a list of author's notes, one note in it | |
| `message-row` | a message with its notes (grid: hidden notes, message, visible notes) | |
| `message-notes-hidden`, `message-notes-visible` | the column of hidden notes (left) and visible notes (right); under the message on narrow screens | |
| `message-note` | one note beside a message; `data-kind="hidden"` or `"visible"` | |
| `numbered-message` | one message in the "notes by numbers" window | |

Inside theme CSS, relative URLs resolve to the theme folder: `url(assets/bg.webp)`.
Library pictures are at `/assets/<name>`, but copy them into the theme
(`POST /api/themes/<id>/assets {"asset": "<name>"}`) so the theme file stays self-contained.

## Layers and the effects script

`scene` (behind) and `overlay` (above) are empty, full-size, and ignore taps. Static decoration
needs no script: use `[data-slot="scene"]::before/::after`. For elements, animation reacting to
the chat, or interactivity, write `script.js`:

```js
export default function setup(t) {
  // t.id, t.scene, t.overlay, t.url("assets/x.svg"), t.reducedMotion, t.phone, t.signal
  const el = document.createElement("div");
  t.scene.append(el);
  t.on("generation", ({ running }) => el.classList.toggle("talking", running));
  t.on("message", ({ role, text }) => {});   // a reply finished
  t.on("send", ({ text }) => {});            // the user sent a message
  t.on("chat", ({ chatId, characterId, characterName }) => {}); // chat opened (replayed on load)
  window.addEventListener("resize", onResize, { signal: t.signal }); // auto-removed
  return () => el.remove();                  // undo everything you added
}
```

Rules: put every listener on `t.signal` or remove it in the returned cleanup; add elements only
to `t.scene`/`t.overlay` (they are emptied on theme switch); no network calls (the page CSP
allows only this server anyway); no reading chats unless the effect needs it. A thrown error
switches the script off and shows the user a message; the theme's CSS stays.

The user must approve a user theme's script once per content (its sha256). Every edit of
`script.js` asks again; mention that when you hand the theme over. Built-in scripts run freely.

## Animated character from parts (paper doll)

The sakura theme is the reference (`web/themes/sakura/`). Technique:
- Draw or generate every part as a **full-size transparent layer of the same canvas**
  (e.g. 200×260): `hair-back`, `body`, `head`, `eyes`, `eyes-happy`, `mouth`, `mouth-open`,
  `blush`. Stacking them with `position:absolute; inset:0` reassembles the character with no
  coordinates to manage, and any part can be swapped for a generated PNG of the same size.
- Motion lives on wrapper divs (`.bob`, `.tilt`, `.look`, `.eyes`), one animation per element,
  with `transform-origin` in canvas percentages (neck ≈ 50% 62%, eye line ≈ 50% 45%).
- States are classes the script toggles: `is-talking` (mouth-open flickers while
  `generation.running`), `react-happy` on tap, `react-nod` on a finished reply.
- Wide screens: put the rig in `t.overlay` beside the chat column with `pointer-events: auto`.
  Phones: put it faded in `t.scene` so it never covers text.

## Pictures

- Generate: `POST /api/assets/generate {"prompt", "size": "1024x1024", "name"}` uses the
  connection chosen as "Подключение для картинок" (Settings → Картинки); it fails with
  `no_image_connection` if none is set. Then copy into the theme.
- Without a generator: draw SVG by hand (small, sharp at any size), or write prompts for the user
  to run in their image tool and ask them to drop files into `data/themes/<id>/assets/`.
  For parts ask for: same canvas size for all parts, transparent background, character centred,
  each part on its own image. Gemini chat often returns no transparency: ask for a flat
  pure-green background you can key out, or use SVG for parts.
- Prefer SVG or WebP; keep each picture under ~300 KB for phones.

## Performance budget (the user's first requirement: no lag)

- Animate only `transform` and `opacity`. Never animate width/height/top/left/box-shadow/filter.
- No `backdrop-filter` or large `filter: blur()` on anything that scrolls or covers the chat:
  this is the main cause of lag on phones. Use semi-transparent solid backgrounds instead.
- At most ~15 moving elements on desktop, ~6 on phones (`t.phone`); zero with
  `t.reducedMotion` and in `@media (prefers-reduced-motion: reduce)`.
- No `requestAnimationFrame` loops running all the time; react to events, and throttle pointer
  tracking to one update per frame.
- `will-change: transform` only on the few elements that move constantly.
- Test at 390 px width and with the CPU throttled ×4 in DevTools before handing over.

## Safety

- Safe mode: `/?safe` opens the app without any theme. Tell the user about it.
- A theme cannot load anything from other sites (Content-Security-Policy), cannot contain HTML,
  and its pictures are served sandboxed.
- Replacing or deleting a user theme keeps the old version (`.versions/`, last 5): the
  "Версии" button or `POST /api/themes/<id>/restore {"version"}` brings it back.
- Never weaken these to make a theme work.

## Sharing

`GET /api/themes/<id>/export` gives one `.tavern-theme` file (a zip of the folder);
"Установить тему из файла" imports it. Installing a theme with an existing id asks before
replacing; a theme carrying a built-in id installs as `<id>-custom`.

## Done

1. Theme loads with no console errors; switching to another theme and back leaves nothing behind.
2. Checked at desktop width and at 390 px; text is never covered; nothing lags.
3. Checked with "reduce motion" (DevTools → Rendering) and in `/?safe`.
4. `python -m pytest` still passes (theme-related tests: `tests/unit/test_themes.py`).
