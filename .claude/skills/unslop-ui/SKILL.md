---
name: unslop-ui
description: Redline pass for a screen that looks vibecoded or AI-made - spacing on a 4px grid, one tinted neutral ramp, one icon set, type scale, details, quiet motion. Trimmed for this app (plain CSS variables, phone first). Use when asked to polish, clean up or fix how an interface looks, or when a UI shows emoji labels, gradients, glows, uneven spacing, bouncing motion.
license: CC-BY-4.0
metadata:
  author: Damir Sokolovsky (https://x.com/heydamir)
  source: https://heydamir.design/unslop
---

# Unslop (trimmed for White House)

Credit: **unslop** was created by Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), who started the Unslopping series on X. It is licensed under CC BY 4.0, so keep this credit in any copy or adaptation. Source text: `~/.claude/skills/unslop` on the owner's machine or https://heydamir.design/unslop.

**Trimmed here:** the landing-page hero and scene rules (6, 8), the image-generation and story-video parts, the node scripts, the copy rules (section 4: our words come from `docs/UI_AUDIT.md`, Russian). Kept (Tailwind names turned into plain CSS): sections 1, 2, 3, 5, 7, 9 as written, plus the "Do not" list. Where it says "dashboard" or "screen" read "window, panel or tab of this app".

How to use it here: read `mobile-ui` first (touch, sizes, jank). Then audit the screen, list every sign with where it lives, fix one layer at a time across the whole screen in the order below, render again, compare with the list. Colors and sizes come from the CSS variables in `web/styles.css` and `--text-*`; themes override them, so never hard-code. Light slop: fix only what the audit found and keep the existing look. No slop: say so and stop.

## 1. Spacing

- Everything sits on a 4px grid. Margins, paddings, gaps and fixed heights are multiples of 4, so 28 or 104 are fine even though they are not in the token list. A 2px step is fine for nudges inside small controls, and an odd value that exactly centers something stays.
- Same thing, same size. All rows in a list share one height and one gap, all cards in a row share one padding.
- Space shows grouping. Space inside a group is smaller than space between groups, so a list reads as one block and sections read as separate ones.
- Things line up. Icons sit in one column, labels start on one line, numbers align right in one column. Nothing is nudged off the line with a stray translate or odd margin.
- Padding follows density. Compact panels and sidebars around 16, cards 20 to 24, page edges 24 to 32.
- Navigation is the densest part of an app. Rows are 32 to 36 tall with 2 to 4 between them, icons in one column, labels on one line. A section label sits closer to its own items than to the section above it, like 24 above and 8 below. Measure the sidebar after the pass, it is where spacing breaks most often.
- Remove one-off values that break the rhythm, like `mt-[13px]` or `py-[11px]`, and replace them with steps from the scale.

## 2. Color

- Build from one neutral ramp, tinted toward the world from the direction. Pick graphite, warm or ink from the tokens, or tint your own the same way. Background, raised surfaces, borders and text all come from that ramp.
- In apps and dashboards the backgrounds, surfaces, borders and text stay neutral, the graphite ramp or something just as grey. People look at them for hours and the content has to carry the color. A screen whose background reads as orange, brown, blue or green has gone too far. The world shows in the accent, the charts and the moments, not in the surfaces. Landing pages can tint their surfaces further toward the world.
- Three text roles, primary, secondary and muted. Not ten slightly different lavenders.
- In the chrome, one accent, used rarely. It marks the primary action, the selection or the one number that matters.
- The moments from the direction are the exception. Inside them color can be rich, several hues in a chart, a rendered object with real materials, an image with its own light, as long as it stays inside the world and each color means something.
- If the brand has a real color, it becomes the accent and leaves the surfaces. If there is no real brand color, take the accent from the world.
- Accent with text on it reaches 4.5 to 1 contrast. If it does not, darken it or put dark text on it. Accent used as text or ink on a light surface needs the same contrast, so use a darker shade of it there.
- On dark themes the primary button can be an off white from the ramp with dark text, and the accent can live in a smaller place like the selection or a key figure. This often looks more deliberate than a colored button.
- Semantic colors only carry meaning, like green for a good change and red for an error, and they are toned down to sit inside the palette.
- Remove hue gradients on surfaces, gradient text, colored glows and colored shadows, radial color washes and blobs behind content, and neon borders.
- Borders are quiet hairlines, the text color of the ramp at 6 to 10% opacity.
- On dark themes depth comes from surface steps and hairlines, not from shadows. A window or object floating over an image may keep a soft neutral shadow. On light themes shadows are neutral, soft and small.
- A gradient is allowed only as a small tone shift inside one neutral, like an active row going from #2b2b2b to #1f1f1f. Never from one hue to another.

## 3. Icons

- Every emoji used as an icon or as decoration in a label becomes a line icon from one set. Use the set the project already has, otherwise lucide.
- One size and one stroke everywhere, 16 or 18px with a 1.5 stroke.
- Icons take the color of their text role, usually secondary or muted. They never get their own colors.
- The icon box has the same width in every row and the gap to the label is fixed, 8 or 10, so labels align.
- Drop icons that add nothing, like an emoji in every card title or before every section label. Categories that need color get a small status dot.

## 5. Type

- Choose type for the character in the direction, from the families in the tokens. Keep the project's font when it is part of a real brand. A default like Inter that nobody chose may be swapped when another family fits the character better. One family for the interface, plus at most one partner from any row of the tokens, a mono for figures and meta or a display face for large headlines.
- A few sizes from a scale and two weights, 400 and 500, with 600 at most for rare emphasis. No 700 to 900 headings, no gradient text.
- Hierarchy comes from size and color, not from weight.
- Sentence case everywhere. Labels like `NAVIGATION` or `PAID THIS MONTH` with wide tracking become `Navigation` and `Paid this month`.
- Small text never gets negative letter spacing. Readability on small text means bigger, a bit bolder or slightly looser. Tight tracking is only for large display text.
- Numbers use `tabular-nums` and one format across the screen. If one figure is `$48,210`, the next is not `$124,860.40`.
- No italic for emphasis.

## 7. Details

- Nested radius. An inner radius equals the outer radius minus the padding between them, never less than 4. Controls 8 to 10, cards 12 to 16, panels 14 to 20. Not everything is a pill.
- Counts and meta become quiet text, often mono and muted, instead of glowing pills.
- Keyboard shortcuts become small keycaps.
- Charts sit on a muted grid with muted mono labels. No rainbow bars inside one series, no glowing bars, no uppercase day labels. If the chart is placeholder noise, replace it with data that tells something real.
- Charts have a hierarchy, like text. One main chart is clearly the biggest thing on the screen, drawn with care, like smooth lines or a mirrored comparison. Anything else that shows data is smaller and different in kind, like a ranked list with thin bars or a short table, never a second chart of the same size.
- One accent per chart. The thing that matters, like the current month, the line you are tracking or the plan that grew, gets the accent, and everything else sits in neutral steps of the ramp. Several hues only when the chart really has to tell series apart, and even then one leads and the rest are quiet.
- Stat cards are numbers. A label, a big figure and a change with what it compares to. No sparklines, tallies, mini bars or icons added to fill them, they read as decoration. A card can hold a real looking object when it is the very thing its number counts, like a paid receipt next to paid revenue or bank cards next to spend. Use as many as make sense for that dashboard, as long as each one reads on its own and none of them is a mini chart.
- Vary the sizes. Cards of equal width side by side, each with the same kind of content, read as a template. Give the grid a rhythm, like three or four compact stats in a row above one wide chart, or a wide chart next to a narrow list.
- Pick the chart type for the data. A trend is a line, a comparison is bars, a share is one stacked bar or a list with bars, a single goal is a progress bar.
- The active state is a small surface step with a hairline highlight, not a gradient with a glow.
- Hover and focus states exist and stay inside the palette. A visible focus ring for keyboard users.
- The logo is the product name in the main family, plus a simple mark if there is one. No gradient wordmark and no sparkle tile.
- The signature detail from the direction appears where it belongs and nowhere else.

## 9. Motion

- Remove motion that loops for no reason, like `animate-pulse` or `animate-ping` on things that are not live, shimmer on headings, breathing glows, floating elements and parallax.
- Remove `transition-all`, bouncy springs, `hover:scale` on cards and anything that moves more than a few pixels on hover.
- Keep motion that explains something. A hover is a quick color or surface change, a press is a tiny scale down, a menu fades and rises a few pixels from where it opens, a changed number tweens.
- Transition color, background, border, opacity and transform, and name them, (for example `transition-property: transform, opacity`), instead of a catch all. Never animate width, height, top, left, margins or large blurs, they stutter on weak devices.
- One easing curve and a few durations across the screen, from the tokens. Short for feedback, a little longer for things that enter.
- At most one entrance on load, a short fade and rise for the main content with a small stagger. Not every card flying in from a different side.

## Do not

- Do not rename the product, even when the name has AI in it. Keep a real logo as it is. When the logo itself is slop, like an emoji or a sparkle in a gradient tile, redraw it flat: the wordmark alone in the new type, or a simple glyph from the product's idea in one solid color. Never a glossy or gradient ball, a glow or a gradient tile, those read as AI at first sight.
- Do not change what the product is. Keep its features, content and the order of sections unless the user asks for more. Placeholder content that means nothing may be replaced with content that does.
- Do not replace removed decoration with new decoration, and do not swap one trend for another like glass, grain, noise, neon or bento grids.
- Do not invent features, claims or data.
- Do not fall back to the same look for every product. The direction decides the palette tint, type and visual, not habit.

## Checklist

- [ ] Every spacing value is on the 4px scale and equal things have equal sizes
- [ ] Icons, labels and numbers each sit in one aligned column
- [ ] Three text roles, one accent with enough contrast
- [ ] No hue gradients, gradient text, glows, colored shadows
- [ ] No emoji used as an icon (our line-icon set only; plugins' own `icon` text stays)
- [ ] Weights 400 to 600, no uppercase tracked labels, no italic emphasis, no negative tracking on small text
- [ ] Nested radii add up
- [ ] No looping or bouncy motion, one easing curve, no animated layout properties
