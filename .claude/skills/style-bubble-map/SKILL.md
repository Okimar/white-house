---
name: style-bubble-map
description: How to restyle the character bubble map (the "Дерево" tab of a character) with CSS - every class and state of bubbles, lines, link arcs, chance colours, opened spaces, ports and the side panel, the tokens a theme sets, and the rules that keep it smooth with thousands of bubbles on a phone. Use when the user asks to make the bubble space prettier, change bubble shapes, colours, lines or animations, or when a theme should reach the bubble map.
---

# Styling the bubble map

The bubble map is a custom menu, and it must look good: calm, readable, with clear depth. The owner
judges it by eye; agents check in the browser on the fake model (AGENTS.md rule 9), in the light and dark
themes, at phone size 375x667, and with a big tree (1500 bubbles must stay at ~60 fps: measure frame times
with the CPU throttled 4x), and the owner has the final look.

Code: `web/components/CharacterTree.js` (logic, do not restyle from there), `web/styles.css`
(base look). A theme restyles it from its own `theme.css` (see `make-theme`): override the
tokens first, classes second. Never change the JS to get a look; never rename a class or slot
listed here without updating this file and `make-theme`.

## Anatomy

```
.tree [data-slot=character-tree]
  .tree-toolbar            tabs Карта/Список, + Пузырь, legend, "Шанс всем"
  .tree-hint               status line (.done = good news; plain = "pick the other bubble")
  .tree-crumbs [data-slot=tree-crumbs]   path of opened spaces; .crumb, .crumb.current
  .tree-body
    .tree-map              the viewport (pan/zoom); .in-space when a bubble is opened
      .tree-behind         the space underneath an opened bubble: one frozen, blurred layer
      .tree-world          the camera layer (one transform for pan/zoom)
        .tree-group        the frame of a bubble shown "внутри пузыря" (display nested)
        svg.tree-lines     all lines, a few <path>s (see Lines)
        .bubble            one bubble (see Bubble)
    .tree-list             the list view
    .tree-panel [data-slot=tree-panel]   the editor of the picked bubble
      .tree-more [data-slot=tree-more]   «Ещё»: finer options (a <details>, folded)
```

## Bubble

`.bubble` is absolutely placed by `transform: translate(x, y)` (168×46 px; the size lives in the
JS constants W/H, so keep the box size — change the look inside it).

| Class | Meaning |
|---|---|
| `.root` | the card itself (or, in an opened space, `.space-root`: the opened bubble) |
| `.mode-always` / `.mode-search` / `.mode-off` | blue (always sent) / green (by topic) / grey (off); sets `--mode-color` |
| `.selected` | picked, its panel is open |
| `.dragging` | being dragged (follows the pointer every frame) |
| `.target` | a drop or a pulled line will land here |
| `.inactive` | free bubble or under one: joined to nothing, not sent — must look pale, not broken |
| `.linking` | waiting for the other end of a 🔗 link |
| `.depth-1` `.depth-2` `.depth-3` | folded bubble with that many levels inside: copies behind it "into the depth" |

Inside: `.mode-dot` (the ring colour), `.bubble-title`, `.bubble-chance` ("40%"), `.bubble-vec` (≈),
`.bubble-count` (number of sub-bubbles), `.bubble-order` (small number at the top right corner:
the place among neighbours; absent on free bubbles), `.bubble-port.in` (left) and
`.bubble-port.out` (right): the handles a 🔗 link line is pulled from (the card has none). Ports
are shown on hover, on the picked bubble and always on touch screens; keep them at least 14 px
(20 px on touch) — they are what you grab.

## Lines

`svg.tree-lines` holds one `<path>` per kind, so thousands of lines cost a few DOM nodes:

| Path class | What |
|---|---|
| `.line-c100` | normal parent → child line (green bubble with chance 100%, or blue/grey) |
| `.line-c75` `.line-c50` `.line-c25` `.line-c0` | to a green bubble whose chance is ≥75 / ≥50 / ≥25 / <25 % |
| `.line-off` | inside a free (inactive) chain |
| `.tree-refs` | link arcs between bubbles of different groups — faint, dotted, bowing out "behind"; must never be confused with a parent line |
| `.tree-wire` | the line being pulled from a port right now |

The legend uses `.swatch.line-*` and `.swatch.tree-refs`: keep it matching the lines.

## Tokens a theme sets

`--mode-always --mode-search --mode-off` (ring colours), `--chance-75 --chance-50 --chance-25
--chance-0` (line colours by chance), plus the general `--dialog-bg --line --muted --accent
--shadow --radius-sm --panel`. Prefer changing tokens: every state above follows them.

## Rules that keep it smooth

- Animate only `opacity`, `box-shadow`, `color`, `border-color` on bubbles. Never animate or
  transition `transform` on `.bubble` (lines are redrawn instantly; a gliding bubble would leave
  its line behind), never `filter` / `backdrop-filter` on bubbles or lines.
- `contain: layout` on `.bubble` stays (not `paint`: the ports stick out).
- No per-bubble pseudo-element tricks with blur; depth is plain offset `box-shadow`. Do not use
  `var(--shadow)` inside a list of shadows: a theme may set it to `none`, which kills the rule.
- `.tree-behind` is the only blurred thing and it never moves. Do not add more blurred layers;
  do not show more than one space behind.
- Hover effects must not be the only way to see something (phones have no hover).
- Test: a character with 1500 bubbles (create them through the API in a sandbox) — opening the
  map well under 200 ms, panning and dragging at ~16 ms per frame.

## Checklist

- [ ] Light, dark, sakura themes and `/?safe` all look right; text contrast is readable
- [ ] Blue / green / grey, selected, dragging, target, inactive, linking, depth-1..3 all distinct
- [ ] Chance colours ordered and the legend matches; link arcs clearly "behind"
- [ ] Opened space: the background reads as further away, the path above is clear
- [ ] Phone width: ports big enough, panel slides from the bottom, nothing overflows
- [ ] 1500 bubbles: no frame drops when panning or dragging

## Added in the 2026-11-07 batch

- `.tree-map.far`: zoomed far out, bubbles drop ports, numbers, shadows and transitions. Do not restore them in a theme.
- Bubbles outside the view are culled lazily; do not rely on every bubble being in the DOM.
- Group maps use the classes `group-map`, `group-frame`, `group-label`, `group-item` (web/styles.css).
