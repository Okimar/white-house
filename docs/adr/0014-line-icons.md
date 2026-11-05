# ADR 0014: line icons instead of emoji

Date: 2026-11-05. Status: accepted.

## Problem

The interface used about 85 lines of emoji and text glyphs as icons. Colour emoji sat next to one-colour symbols, looked different on every phone, and some were nearly invisible in the dark theme.

## Decision

One set of line icons: Lucide (`lucide-static@0.460.0`, ISC licence). `web/vendor/lucide-icons.mjs` holds only the icons the app uses, as data (about 60, 10 KB). `web/components/Icon.js` draws them as inline SVG in the colour of the text. No font, no network and no build step.

Plugins may still give any text as their button icon: that is part of their contract.

## Consequences

- A new icon is added by hand from the package's `icons/NAME.svg`; a name not in the file draws nothing.
- Icon-only buttons need a Russian label (`IconButton`), which also gives them an accessible name.
- Server text no longer carries emoji for the assistant's skill line. Messages written by plugins (the dice) keep theirs.
