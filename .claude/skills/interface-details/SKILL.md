---
name: interface-details
description: Small design-engineering details that make an interface feel polished - concentric radius, optical alignment, shadows and borders, text wrapping, tabular numbers, enter/exit motion, transition scope, hit areas. Adapted for this app (touch first, plain CSS). Use when a control, list or window feels off, cramped, flat or jumpy, and when adding states to a component.
license: MIT
metadata:
  author: linus707 (salvaged community PR #1659) via Everything Claude Code (ECC) 2.2.3, MIT, Copyright (c) 2026 Affaan Mustafa
---

# Interface details (adapted for White House)

Credit and license: text from the `make-interfaces-feel-better` skill of ECC (https://github.com/affaan-m/everything-claude-code, MIT, Copyright (c) 2026 Affaan Mustafa), itself salvaged from community PR #1659 by `linus707`. MIT permits the copy as long as this notice stays.

**Changed here:** hit areas raised to this app's 44x44 minimum (touch first; see `mobile-ui`); `scale(0.96)` press only on non-list controls; macOS font smoothing and the image outline example dropped (our CSS variables and themes own colors; do not hard-code `rgba` outlines); the review-table output and the "When to Use" list dropped. Read `mobile-ui` first.

## Core Principles

### Concentric Radius

For nearby nested rounded surfaces:

```text
outer radius = inner radius + padding
```

If padding is large, treat layers as separate surfaces instead of forcing the
math. The point is optical coherence, not formula worship.

### Optical Alignment

Geometric centering is not always visual centering. Icon buttons, play
triangles, arrows, stars, and asymmetric icons often need a small offset. Fix the
SVG when possible; otherwise adjust with a pixel-level margin or padding change.

### Shadows And Borders

Use borders for separation and focus rings. Use layered shadows when a card,
button, dropdown, or popover needs depth. Shadows should be transparent and
subtle enough to work across backgrounds.

### Text Wrapping

- Use `text-wrap: balance` on headings and short titles.
- Use `text-wrap: pretty` on short-to-medium body text, captions, descriptions,
  and list items.
- Avoid both on long prose, code, and preformatted content.
- Use `font-variant-numeric: tabular-nums` for counters, timers, prices, tables,
  and other updating numbers.

### Motion

Use CSS transitions for interactive state changes because they can retarget
when the user changes intent mid-motion. Reserve keyframes for staged
one-shot entrances or loading sequences.

Good motion defaults:

- Enter: combine opacity, small `translateY`, and optionally blur.
- Exit: shorter and quieter than enter, usually 150ms.
- Press: `scale(0.96)` for tactile buttons, with a way to disable it when the
  movement distracts.
- Icon swaps: cross-fade with opacity, scale, and blur instead of instant
  visibility toggles.

### Transition Scope

Never use `transition: all`. Specify the changed properties:

```css
.button {
  transition-property: transform, background-color, box-shadow;
  transition-duration: 150ms;
  transition-timing-function: ease-out;
}
```

Use `will-change` only for first-frame stutter on compositor-friendly
properties such as `transform`, `opacity`, and `filter`. Never use
`will-change: all`.

### Hit Areas

Interactive controls have at least a 44x44 CSS px hit area on touch (the app's rule)
Expand with a pseudo-element when the visible icon
is smaller, but do not let expanded hit areas overlap.

## Checklist

- Nested rounded elements are optically coherent.
- Icons are visually centered.
- Headings and short text avoid awkward wrapping; dynamic numbers use `tabular-nums`.
- Enter and exit animations are split, subtle, interruptible, `transform` and `opacity` only.
- `transition: all` and `will-change: all` are absent.
- Every control has a 44x44 hit area on touch, without overlap.
- Colors and shadows come from CSS variables, not literals.
