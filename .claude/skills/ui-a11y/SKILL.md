---
name: ui-a11y
description: Accessibility patterns for the White House UI (Preact + htm, plain ES modules) - semantic elements, labels, ARIA only where needed, keyboard and focus in windows and lists, live regions, reduced motion. Adapted from the frontend-a11y skill. Use when building or reviewing forms, windows, menus, tabs or icon buttons under web/.
license: MIT
metadata:
  author: Everything Claude Code (ECC) community, 2.2.3 - MIT, Copyright (c) 2026 Affaan Mustafa
---

# UI accessibility (adapted for White House)

Credit and license: adapted from the `frontend-a11y` skill of ECC (https://github.com/affaan-m/everything-claude-code, MIT, Copyright (c) 2026 Affaan Mustafa). MIT permits the adaptation as long as this notice stays.

**Trimmed here:** everything React / Next.js / TypeScript (`htmlFor`, `useId` forms, `focus-trap-react`, the long example components) and the code-review-tool talk. Rewritten for `html` template strings from `web/vendor/preact-htm.mjs` (the attribute is `for`, `class`, not `htmlFor`, `className`). Read `mobile-ui` first.

## What the app already gives you (use it, do not rebuild)

- Windows: `useEscape(close)` and `backdrop(close)` from `web/core/keys.js` (Esc and the browser's Back close the newest window).
- Drop-down lists: `Select.js` (keys, filter, roles) and `Menu.js` (`openMenu`, `MenuButton`). Never `<select>`, `confirm()` or `prompt()`; use `ask` / `askText` from `Dialogs.js`.
- Icon buttons: `IconButton` in `Icon.js` requires a label. Use it.
- Errors and empty lists: `Pending`, `EmptyNote` in `State.js`.

## Rules

1. Use the native element: `<button type="button">`, `<a href>`, `<input>`, `<label>`. A clickable `<div>` or `<span>` is wrong; if it truly cannot be a button, give it `role`, `tabIndex=0` and an Enter / Space handler.
2. Every field has a visible `<label>` (wrapping it, or `for` + `id`). A placeholder is not a label. A plain-words hint under the field goes in a node linked with `aria-describedby`.
3. Icon-only buttons have `aria-label` (Russian, what it does). Decorative icons have `aria-hidden="true"`.
4. ARIA only where native semantics fall short; wrong ARIA is worse than none. State attributes that matter: `aria-expanded` on a button that opens something, `aria-pressed` on toggles, `aria-selected` on tabs / options, `aria-current` on the open item. Never `aria-hidden` on something focusable. No positive `tabIndex`.
5. A window: `role="dialog"`, `aria-modal="true"`, an accessible name (`aria-label` or `aria-labelledby` the title), focus moves into it on open and returns to the opener on close.
6. Errors and results that appear without a page change go to a live region: `role="status"` (polite) for ok / info, `role="alert"` for errors. `notify()` already does it for toasts; use it instead of inventing another toast.
7. Contrast 4.5:1 for text, 3:1 for large text and control borders, in every built-in theme (light, dark, default, sakura). Colour is never the only signal (add an icon or word).
8. Reduced motion: wrap non-essential animation in `@media (prefers-reduced-motion: no-preference)` or switch it off in `@media (prefers-reduced-motion: reduce)`.
9. Focus is always visible (`:focus-visible` outline from a CSS variable). Do not remove outlines.
10. Text must reflow at 200% zoom without sideways scroll.

## Quick patterns

```js
// labelled field with a hint
html`<label>Пауза между ответами
  <input type="number" value=${v} onInput=${on} aria-describedby="pause-hint" />
</label>
<p id="pause-hint" class="hint">Сколько секунд ждать перед следующим ответом.</p>`

// toggle button
html`<button type="button" aria-pressed=${on} onClick=${toggle}>Показывать скрытые</button>`

// opener of a drop-down
html`<button type="button" aria-expanded=${open} aria-haspopup="menu" onClick=${openIt}>…</button>`
```

## Checklist

- [ ] No `onClick` on `div` / `span` without role, tabIndex and keys
- [ ] Every input labelled; hints linked
- [ ] Icon buttons labelled
- [ ] Window: Esc and Back close it, focus enters and returns
- [ ] Errors reach a live region
- [ ] Contrast checked in light and dark
- [ ] Animation respects reduced motion
