---
name: mobile-ui
description: Rules every change under web/ must follow - this app is used on a phone (iPhone 8, 375x667) and an Android tablet (768x1280), by touch, not on a PC. Touch drags, 44px targets, bubble and chip sizes, no layout jank, no blinking when a map moves, settings order, window close buttons, and how to test by numbers. Read before any change under web/.
---

# Mobile UI (White House)

Owner's words: most complaints are about the interface. The app is used on a phone and a tablet, with a finger. A feature that only works with a mouse is not done. Read this, then `modify-ui`; for looks also `interface-details`, `unslop-ui`, `ui-a11y` (adapted public skills, credits inside).

## 1. Touch first

- Anything a mouse can drag, a finger can drag. Use **pointer events** (`pointerdown / pointermove / pointerup / pointercancel`), not mouse events; call `setPointerCapture(e.pointerId)` (see `CharacterTree.js`, `Groups.js`). Set `touch-action` on the draggable (`none` on a map or handle, `pan-y` on a vertical list row that also scrolls) or the browser scrolls instead of dragging.
- In a scrolling list a drag must not steal the scroll: start a drag on a **long press** (about 350 ms, cancel if the finger moves more than 8 px before that, haptic `navigator.vibrate?.(10)` if present) or from a visible drag handle with `touch-action: none`. Show the picked item (lift, shadow) and auto-scroll near the edges.
- No hover-only actions. Hover may add polish, never reveal the only way to do something. No tooltips as the only hint (use a visible hint line). Right-click and double-click always have a tap equivalent (menu button, long press).
- Targets at least **44x44 CSS px** (`--tap` is 44px under `(pointer: coarse)`; use `min-height: var(--tap)`; enlarge small icons with padding or a `::after` hit area). Gap between neighbours at least 4 px so nothing is hit by mistake.
- Text at least **14 px** (`--text-*`); inputs 16 px so iOS does not zoom the page on focus. Nothing wider than the viewport; no sideways page scroll.
- Gestures need a button too (pinch = zoom buttons, swipe = a menu item).

## 2. Sizes

- Bubbles, chips, tags and buttons size to their text: `width: max-content` with `min-width` and `max-width` (ellipsis after the max, full text in a title or on open). A one-letter name gives a small bubble, not a fixed wide one. Compute the hit area from the box, not from a constant.
- Rows in a list share one height (the virtual lists depend on it; see `ROW_H`).

## 3. No layout jank

Opening or closing a window, tab, panel or menu must not make the page jump, flash a bar at the top or reset a scroll.
- Never re-mount the whole app or a parent to show a child (no changing `key` of the shell, no early `return null` that removes the layout). Keep the tree stable and toggle a child.
- Reserve space for what appears later (loading line, toast, banner): fixed `min-height`, or overlay it with `position: fixed`. Do not push the content.
- Keep scroll positions: do not replace a scroll container's content with a spinner; keep the old content under a thin overlay.
- Animate only `transform` and `opacity` (`interface-details`). Never width, height, top, left, margin. Use `100dvh`, not `100vh`; the phone's address bar and keyboard change the height.
- A window opening must not shift the page behind it (`overflow` on `body` changes the scrollbar width: use `scrollbar-gutter: stable` or `overscroll-behavior: contain` on the window).

## 4. Maps and long lists must not blink

- While the camera moves (pan, zoom, pinch) **do not mount or unmount visible items**. Cull with a margin (render about one screen beyond the edge) and update the visible set only when the camera stops or moves farther than the margin.
- Items entering the margin fade in (`opacity` over 120-180 ms); never pop. Remove with a short fade, after they are well outside.
- Keep stable keys. Move the camera with one `transform` on a wrapper (`will-change: transform` while moving only), never by changing each item's `left/top`.
- Heavy effects (blur, many shadows, big SVG filters) switch off while moving.

## 5. Settings and order

- No scattered mini text fields when a prompt preset, author's notes or lorebooks already do the job. Before adding a setting, check those three; a setting that only appends text to the prompt belongs there.
- Every control has a hint in plain words (what it does, what happens when on), under the control, visible on a phone (not a tooltip).
- A tab with little in it is merged into a neighbour or finished; no tab with one switch.
- Destructive actions use `ask(text, {danger: true})` and offer the same «Не спрашивать больше» choice everywhere. Today only bubble deletion has it (Settings.js); the aim is one shared option in `Dialogs.js` (`ask`) and one place in Settings to turn it back on. A new destructive action must use that, not a private switch. Where undo exists, prefer undo to asking.
- Words come from `docs/UI_AUDIT.md`.

## 6. Windows and menus

- Every window has a **✕ always visible at the top right** (sticky header: `position: sticky; top: 0`), reachable without scrolling, 44x44. Esc and the browser Back close the newest window (`useEscape`, `backdrop` in `core/keys.js`). On a phone a window fills the width and its content scrolls under the header.
- A drop-down never goes off screen: use `Menu.js` (`openMenu`, `MenuButton`) and `Select.js`; never `<select>`, `confirm`, `prompt`, `alert`.

## 7. App specifics

- Keep every existing `data-slot` name and CSS variable (contract with user themes, see `make-theme`); new visible places get a new `data-slot`. Colours and sizes come from variables in `web/styles.css`, never literals.
- Preact + htm from `web/vendor/preact-htm.mjs`, no build step, no CDN. Text is rendered as text; no `innerHTML` / `dangerouslySetInnerHTML` (the one exception is documented in `modify-ui`).
- `localStorage` access in try/catch. Back-compatible: a saved layout from an older version must still open.

## 8. Test plan (browser, fake model only, rule 9 in AGENTS.md)

Write the checklist first (one line per control you touched, with the number you expect), then follow it, no side quests.
1. Run on an empty temp `--data-dir`, connection to `python -m tavern.devtools.fake_llm --port 5001`; one agent at a time uses the browser.
2. Emulate: phone `375x667x2,mobile,touch`; also 375x553 (browser bars), 375x410 (keyboard open); tablet `768x1280x2,mobile,touch`. After **every reload** read `[innerWidth, innerHeight, devicePixelRatio, matchMedia('(pointer: coarse)').matches]` and stop if it is not what you set (375/667/2/true; 768/1280).
3. Judge by numbers from `evaluate_script`: `getBoundingClientRect()` of each control >= 44x44; `getComputedStyle().fontSize` >= 14; `document.documentElement.scrollWidth <= innerWidth`; close button `top >= 0` and visible without scrolling; no console errors.
4. Jank check: record `scrollY` and the top bar's rect before and after opening and closing the window or tab; they must be equal. For maps, count how many item nodes are added or removed during a pan (`MutationObserver` on the map; expected: none while moving).
5. Touch drag: the `drag` tool is a mouse. For touch dispatch pointer events from `evaluate_script`: `pointerdown` (pointerType "touch", isPrimary true, pointerId 1, clientX/Y), wait the long-press time, several `pointermove` steps, `pointerup`, all dispatched on the element (they bubble; capture is honoured). Check the result (order changed, item moved). Real finger behaviour (scroll vs drag, inertia) is not proven by this: report as unverified for the owner's device.
6. Screenshot at each size; report anything not seen on screen as unverified.
7. `python -m pytest tests/unit -o addopts="" -q` before finishing.
