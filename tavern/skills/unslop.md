---
name: unslop
title: Убрать «шаблонный» вид интерфейса
description: Making the interface of Tavern look designed instead of generic - "make it prettier / cleaner / more beautiful", fixing glowing gradients, uneven spacing, shouting colours, too many effects. Works through a theme (CSS variables and slots); says plainly what a theme cannot reach.
audience: assistant
---
Adapted for Tavern's assistant from the "unslop" skill by Damir Sokolovsky (https://heydamir.design/unslop),
licensed CC BY 4.0. This is a shortened rewrite: only what you can really do here is kept. Credit stays.

THE IDEA. A generic interface fails the same way everywhere: every surface glows, colours are everywhere,
spacing is whatever it happened to be, every effect moves. The cure is not more decoration. The chrome (panels,
buttons, borders) gets quiet; the richness goes into one or two places the person looks at for hours: here, the
chat itself and the characters' messages. A theme that could belong to any app is the next template, so every
theme starts from this product: a roleplay chat, read for a long time, often at night, often on a phone.

WHAT YOU CAN TOUCH. Only a THEME of your own: theme.css (CSS variables and [data-slot="..."] places), theme.json,
small text files such as an .svg you draw by hand, and an effects script (see the skill theme-scripts). You make
it with make_theme (a copy of the theme that is on, switched on at once) and write_theme_file. Built-in themes
are never changed. Everything is undoable.

WHAT YOU CANNOT DO, and must say so in the plan and the report, naming the part, without a workaround:
- Make pictures or "hero images" (no image tool). A hand-drawn SVG file or a CSS gradient is a different thing;
  offer it only as that, never as the picture that was asked for.
- Judge by feeling alone. You CAN look: the `screenshot` tool shows the app (what: screen, phone, tablet). Take one
  after every theme change, at phone size too, and judge by it. It needs the app open in a browser; if the tool says
  it is not, tell the person to open it. If you cannot see the picture (the model reads no pictures), say so and ask
  for a model that does. Effects scripts only run after the person allows them, so a screenshot may not show them.
  Never write "it now looks great" without a screenshot you looked at.
- Change the app's own words on buttons and menus, its icons, the layout of its screens, or add a font file:
  those are in the program's code or are binary files. You may change text that is data (names and descriptions of
  characters, chats, presets, lorebooks) when the person asks. Fonts: only those already on the device (a system
  stack such as system-ui, "Segoe UI", Roboto, Georgia for reading text).
- Run tools, measure contrast with a program, or test on a phone. You compute by hand (below) and say it was by hand.

## Plan
Ask 2 to 4 questions about taste only, each with ready options taken from THIS product, never about technique:
the mood (candlelit and warm / cold and quiet / paper and ink / neon city at night), light or dark, how much
colour (almost none / one accent / two), how much motion (none / a little / lively). Offer 2 or 3 unusual
twists, not the stock ones. Never ask the person to send a screenshot: take one yourself
(`screenshot`) and read the current theme (list_themes, read_theme_file).

Before the items, write the direction in five short lines and put them into "decisions" so the writer sees them:
- Product: a roleplay chat, read at length, on phones and desktops.
- Character: two or three concrete words ("calm, bookish, a little warm"). Not allowed: clean, modern, sleek,
  minimal, premium.
- World: the place or material it belongs to (a tavern table, a ship's log, a night road). It sets the tint of the
  neutrals and the accent.
- Moments: one or two places that get richness (the message bubbles, the speaker's name, the empty chat screen).
- Signature: one small detail used everywhere the same way (a thin accent rule on the left of the bot's messages,
  a rounded-corner shape, a number style).
If the request asks for something from the CANNOT list, add a decision "CANNOT: ..." for that part and plan no item
for it.

Items, in this order (each is one write_theme_file or make_theme step): 1. make_theme from the theme in use and
set the colour variables; 2. spacing and size variables; 3. type; 4. shape, borders, shadows; 5. motion budget
and removal of looping effects; 6. the moments (messages) with [data-slot="message"] rules; 7. check by reading the
file back and counting against the rules below.

## Write
Read the current theme.css first (read_theme_file) and the theme.json. Keep what is good; change only what the
rules below name. Write the whole theme.css in one make_theme (css field) or one write_theme_file, not a hundred
tiny edits. Touch screens first: nothing may depend on hover; hover styles go inside @media (hover: hover), and
every hover colour also exists for :active and :focus-visible. Targets for fingers: set --tap to at least 40px.

1. SPACING. One 4px grid: every padding, gap and size is a multiple of 4 (a 2px nudge is fine inside tiny
controls). The same thing has the same size everywhere. Space inside a group is smaller than space between groups.
Variables to use: --tap, --avatar-size, --sidebar-width (240-280px), --chat-width (680-760px for reading; a line
of more than ~75 characters tires the eye), --radius, --radius-sm. Do not invent one-off values.

2. COLOUR. Build the surfaces from ONE neutral ramp tinted slightly toward the World (saturation very low, so a
surface never reads as a colour): --bg (page), --panel (raised), --line (hairline), --fg, --muted. Dark example:
#141415 page, #19191b panel, text #ededed, muted #a1a1a6; light example: #fafafa, #ffffff, text #171717, muted
#525252. Three text roles only: --fg, a secondary (--text2), --muted for captions. ONE accent (--accent), used
rarely: the send button, the selection, the one thing that matters. Text on the accent needs contrast of at least
4.5 to 1: compute it by hand (relative luminance L = 0.2126 R + 0.7152 G + 0.0722 B after the sRGB curve; ratio =
(Lbright + 0.05) / (Ldark + 0.05)) or pick dark text on a light accent; say it was computed by hand. Remove hue-to-hue
gradients on surfaces, gradient text, coloured glows and coloured shadows, neon borders and radial colour washes.
A gradient may only be a small tone shift inside one neutral. Borders are hairlines: the text colour at 6-10%
opacity. Colour of the two sides of the conversation (--user, --bot) differ by a few percent of lightness, not by
hue shouting at each other. Semantic colours (--danger, --success, --warning) stay muted.

3. TYPE. Reading text is the product: --font-reading with a comfortable --text-reading (16-18px on phones, line
height 1.5-1.65). The interface font may be a system sans; a serif such as Georgia suits the message text of a
bookish theme. At most two weights (400 and 500). Sentence case. No negative letter-spacing on small text, no
uppercase-with-wide-tracking labels (do not add text-transform: uppercase). Numbers with font-variant-numeric:
tabular-nums where they line up (counters, spending).

4. SHAPE AND DEPTH. Nested radii: an inner radius = outer radius minus the padding between them (never below 4).
Controls 8-10px, message bubbles 12-16px, panels 14-20px. Not everything is a pill. On dark themes depth comes from
surface steps and hairlines, not shadows; on light themes shadows are neutral, soft and small. --shadow: none or
one soft neutral value.

5. MOTION BUDGET (phones first). Remove infinite pulses, shimmer, floating, breathing glows and parallax. Allowed:
a quick colour or opacity change on press (120-180ms, one easing such as ease-out), a menu fading in. Animate only
transform and opacity, never width, height, top, left, box-shadow or filter; name the properties in transition
(never transition: all). No backdrop-filter or big blur on anything that scrolls. Wrap every animation in
@media (prefers-reduced-motion: no-preference). At most ~6 moving things on a phone.

6. MOMENTS. Give the richness to the messages: [data-slot="message"][data-role="assistant"] and
[data-role="user"] get the --msg-* variables, a calm border, and the Signature; the speaker name
([data-slot="message-name"]) and the avatar ([data-slot="message-avatar"]) get the character. The empty chat
([data-slot="empty"]) may carry a hand-drawn SVG or a gentle gradient from the World. Everything else stays quiet.

7. COPY. Only what is data: if a chat title, preset name or character description reads like marketing ("Amazing!
Supercharged!"), say so and offer a plainer line; change it only when asked. Do not touch hard-coded labels.

Then tell the person plainly what you changed, which parts of the request you could not do (see the list above), that
you could not see the result, how to undo (the journal's «Отменить», or choosing the old theme in Настройки → Темы) and
the safe mode /?safe if anything looks broken.

## Check
- The theme.css uses variables and [data-slot] places only; no `!important`, no rules on app classes that a program update can rename.
- No hue-to-hue gradients, gradient text, coloured glows, neon borders; one accent; surfaces from one tinted neutral ramp.
- Every spacing and size value is a multiple of 4 (or a 2px nudge); --tap is at least 40px; reading text is 16px or larger on phones.
- Text on the accent has a contrast of at least 4.5 to 1 and the report says it was computed by hand.
- Nothing needs hover: hover styles are inside @media (hover: hover) and also exist for :active / :focus-visible.
- No looping animation, no transition: all, no animated width/height/top/left/box-shadow/filter, no backdrop-filter on scrolling parts; animations sit inside prefers-reduced-motion: no-preference.
- A screenshot at phone size (what: phone) was taken after the last change and looked at: text is readable, nothing is cut or overlaps, buttons are big enough.
- The report names every part that was asked for and could not be done (pictures, fonts, the program's own labels, a screenshot that could not be taken) and says what the screenshots showed.
- The new theme is a copy (the built-in one is unchanged) and is switched on; the person is told how to go back.
