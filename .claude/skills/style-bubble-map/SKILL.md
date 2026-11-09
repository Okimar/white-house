---
name: style-bubble-map
description: How to restyle the character bubble map (the "Дерево" tab of a character, and a lorebook's map) - the map is drawn on a canvas from the theme's CSS tokens; what each token does, what stays CSS (frame, background, toolbar, side panel), the old DOM map kept behind ?treedebug=dom, and the rules that keep it smooth with thousands of bubbles on a phone. Use when the user asks to make the bubble space prettier, change colours or lines, or when a theme should reach the bubble map.
---

# Styling the bubble map

Since 2026-11-08 the map is drawn on ONE `<canvas>` (`web/components/TreeCanvas.js`), not as DOM bubbles: the DOM
map blanked out whole rectangles on weak mobile GPUs and lagged when bubbles entered the page. CharacterTree.js
owns the camera, pointers and every API call; TreeCanvas.js only builds a model (`buildModel`), answers
hit tests (`hitTest`) and paints (`createPainter`). `?treedebug=dom` (kept in localStorage, `?treedebug=off`
clears it) brings back the old DOM map for comparison; `?treedebug=hud` shows zoom, drawn bubbles and fps.

Judge changes by eye in the light, dark and sakura themes at 375x667 and 768x1280, and with a big tree
(3000 bubbles through the API in a sandbox; frames must stay ~16 ms with the CPU throttled 4x). Agents check on the
fake model (AGENTS.md rule 9).

## What a theme can change

The painter reads CSS custom properties through a hidden probe element (so `var()` and `color-mix()` work) at start
and again when the theme, the colour settings or a stylesheet changes. Set tokens in `theme.css` (see `make-theme`):

| Token | Used for |
|---|---|
| `--mode-always` `--mode-search` `--mode-off` | bubble ring and dot (blue / green / grey), nested-group frame, folded copies |
| `--chance-75` `--chance-50` `--chance-25` `--chance-0` | line colours by the chance of the green bubble a line leads to |
| `--accent` | the card (root bubble) ring, selected ring, link arcs, the wire being pulled |
| `--muted` | plain lines (100%, blue, grey), numbers on bubbles |
| `--line` | the box around the sub-bubble count |
| `--dialog-bg` | bubble fill |
| `--fg` | titles |
| `--mode-search` | the ring of a bubble a drop or wire will land on |
| font | `font-family` and `font-size` computed on `.tree-map` (inherited from the page) |

Not tokens (fixed in TreeCanvas.js): sizes (168x46, ring 2 px, radius 23), shadows (none), dash patterns, alpha of
inactive (.4) / off (.55) / refs (.5) bubbles and lines. Changing them means editing TreeCanvas.js.

## What stays CSS

`.tree-map` (dotted background, `.in-space` inset frame, cursor), `.tree-canvas` (fills the map), the toolbar, legend
(`.swatch.line-*`, `.swatch.tree-refs`: keep them matching the lines), `.tree-crumbs`, `.tree-hint`, and the side
panel `.tree-panel` with `.tree-more` (all `data-slot`s below). Restyle those with plain CSS as before.

## Anatomy

```
.tree [data-slot=character-tree]
  .tree-toolbar            tabs Карта/Список, + Пузырь, legend, "Шанс всем"
  .tree-hint               status line (.done = good news; plain = "pick the other bubble")
  .tree-crumbs [data-slot=tree-crumbs]   path of opened spaces; .crumb, .crumb.current
  .tree-body
    .tree-map              the viewport (pan/zoom); .in-space when a bubble is opened; .canvas
      canvas.tree-canvas   role=application: arrows move, + / - zoom; everything on the map is painted here
    .tree-list             the list view (the accessible way to every bubble)
    .tree-panel [data-slot=tree-panel]   the editor of the picked bubble
      .tree-more [data-slot=tree-more]   «Ещё»: finer options (a <details>, folded)
```

## What is drawn

Bubble states: card (accent ring, avatar), `mode` ring (blue / green / grey, grey at 55% opacity), selected (accent
ring), dragging (75%), target (3-4 px green ring), inactive (free bubble or under one: 40%, dashed ring), linking
(accent ring), folded with 1..3 levels inside (offset copies behind). Inside: dot, title with ellipsis, chance
(green < 100%), `≈` (vectorized), sub-bubble count box (`+` when folded), order number on the top right corner. Ports
(circles on the left/right edge, 14 px, 20 px on touch) are drawn on the picked / hovered / target bubble, always on
touch, and never below zoom 0.45. Lines: one stroke per chance band (`c100 c75 c50 c25 c0`, `off` = dashed, faint);
link arcs (dotted accent, bow out sideways); nested-group frames (dashed rounded box, 7% tint); the wire.
Opened space: the space underneath is painted once into its own canvas (no text) and copied at 22% under it.
Zoom levels: a text is drawn only when it is at least 6 CSS px on screen (`TEXT_PX`), each by its own size, so with
the 16 px font titles (and the dot) come at 0.375, `≈` at 0.42, the sub-bubble count with its box at 0.5, the chance
at 0.54, the order number with its box at 0.58; ports from 0.45 (the hit tests use the same limit); below 0.2 plain
boxes (`fillRect` / `strokeRect`). A title always leaves room for every mark, so its ellipsis never changes with zoom.

## Rules that keep it smooth

- A frame is painted only when something changed (camera, data, selection, drag, hover, theme) and only the part
  in view (culling through the model's grid of cells when that is fewer than all bubbles). Never loop forever; call
  `requestDraw()`. Nothing during a pan, pinch or drag changes Preact state: the painter reads refs (`getLive`).
- Nothing is measured, laid out or allocated per bubble per frame: the text layout (`layoutOf`, fitted title and the
  places of the marks) and the Path2D shapes (`shapesOf`, `boxesOf`) live on the model's item, made again only when
  the tree, the position or the font changes (`gen`). Layouts are warmed in idle time after a change, so the first
  zoom to a readable size does not measure hundreds of titles in one frame. Text is measured on a separate canvas.
- Plain bubbles are drawn grouped by look (mode + free): fill, stroke, font and alpha are set a handful of times per
  frame, never per bubble. Picked, hovered, folded, target, dragged bubbles and the card go through `drawBubble`.
- Each bubble, dot, port and mark box stays its own rounded rect (GPUs draw those analytically and batch them);
  never merge hundreds of them into one path (a weak GPU falls back to a slow general path renderer for that).
  Lines and link arcs are the opposite: one path per colour band, culled by their real extent (arcs bow far out).
- Dashes (free rings, `off` lines, link arcs, group frames) are left out while the map moves and below 0.45: dashing
  a curve is done on the CPU every frame.
- The space underneath an opened bubble is painted once into its own canvas and copied; it is painted again only
  when the tree, the theme, the size or its camera changes.
- Backing store = CSS size x min(devicePixelRatio, 2). The canvas is resized only by the ResizeObserver, and the new
  frame is drawn in the same callback, so an empty canvas is never shown.
- No shadows or filters on the canvas; state is told by rings, alpha and dashes. No per-bubble `save()/restore()`.
- Hit tests use a grid of cells in world space; the DOM is never asked what is under a finger.
- Hover effects must not be the only way to see something (phones have no hover).
- `tests/unit/test_tree_canvas.py` runs the painter in Node against a recording fake canvas and checks these rules
  (no measuring and few font switches per frame, one clear and no resize per frame, texts by size, the space
  underneath painted once, hit tests, arcs culled by their curve). Keep it passing when you change the painter.
- Debug flags: `?treedebug=hud` (zoom, drawn bubbles, fps), `?treedebug=nolines` (no lines or arcs), `?treedebug=dom`.

## Checklist

- [ ] Light, dark, sakura themes and `/?safe` all look right; text contrast is readable
- [ ] Blue / green / grey, selected, dragging, target, inactive, linking, folded 1..3 all distinct
- [ ] Chance colours ordered and the legend matches; link arcs clearly "behind"
- [ ] Opened space reads as further away, the path above is clear
- [ ] Phone width: ports big enough, panel slides from the bottom, nothing overflows; `innerWidth` stays 375
- [ ] Canvas backing size = css size x min(devicePixelRatio, 2) (sharp text, no 3x stores on phones)
- [ ] 3000 bubbles: no frame drops when panning, zooming or dragging

## Old DOM map (`?treedebug=dom`)

Still in CharacterTree.js for comparison: `.tree-world`, `.tree-behind`, `.tree-group`, `svg.tree-lines`, `.bubble`
and its states/classes (`.root .mode-* .selected .dragging .target .inactive .linking .depth-1..3`, `.bubble-*`
parts, `.line-*` paths, `.tree-refs`, `.tree-wire`), `.tree-map.far / .lite / .moving`. Themes may still style them;
they no longer affect the default map. Group maps (`Groups.js`) use `group-map`, `group-frame`, `group-label`,
`group-item` with DOM, unchanged.
