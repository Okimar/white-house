# ADR 0015: The look across updates, safe mode from the console, quick exit

Status: accepted

## Context

Three owner complaints. (1) Ctrl+C on Termux printed "Waiting for connections to close" and hung: an open
page holds `GET /api/events` and run streams, and uvicorn waits for every connection. (2) After an update the
menu changed, a theme written for the old menu broke it, and the page could not be used normally. (3) Safe
mode existed only as `/?safe` in the address, which is hard to type when the page is already broken.

## Decision

**Quick exit.** `util/streams.StreamReaper` wraps the app: it remembers requests answered as
`text/event-stream`; the server (a small `uvicorn.Server` subclass in `cli.py`) calls `close_all()` first in
`shutdown()`, which cancels them and finishes each response cleanly. A page reconnecting during shutdown is
ended at once. The process then leaves `server.run()` normally, so `ctx.close()` (database) and the data
lock release run as before. The graceful timeout stays as a backstop (2 s).

**Look across updates.** `services/look.py` keeps `look_stamp` in settings: the app version plus a SHA-256 of
the page's own files (`web/`), not git (zip updates must work too, and a Python-only commit does not change
the look). At startup, when the stamp differs, every non-built-in thing that shapes the look is switched off
once: the chosen user theme and the characters' own user themes (effect scripts and CSS belong to themes).
Built-in choices stay. The old choices are kept in `look_parked`; nothing is deleted. The page shows a
notice with "Вернуть прежний вид" (with a warning that it may break the menu) and the Themes tab offers the
same action. No stored stamp counts as an older version (data from before this feature is what an update
can break); a fresh data folder has nothing custom, so nothing happens. A sandbox follows the same rule on
its copy. Settings (not a file) hold the state so copies and restores keep stamp and parked look consistent.
Not covered: user plugins' `style.css` (switching plugins off changes behaviour; safe mode covers them).

**Safe mode.** `python -m tavern safe-mode on|off|status` writes the `safe_mode` setting (no data lock, works
with the server running); `python -m tavern --safe` is for one run; `/?safe` stays. `GET /api/look` tells the
page; `core/theme.js` reads it before anything else and draws a bar (plain DOM, inline styles, independent of
the app) saying that safe mode is on and how to leave it.

## Consequences

- Each interface change makes the first start after an update switch the owner's custom theme off once.
  That is the intended price of "updates break nothing".
- The stamp reads all of `web/` (about 0.7 MB) at startup.
- `core/theme.js` now awaits one request at load (`/api/look`); if it fails the address alone decides.
- (2026-11-11, owner's bug) The parked record only tells what is *still* off: `look.settle` drops the parts that
  are on again (a theme picked by hand, by the assistant, by an undo or a copy), and `/api/look` names the theme
  that is on instead, so the notice never says «стандартный вид» while another look shows. The page asks again
  when the theme choice changes. A theme script's setup is watched: nodes it adds to `<head>`/`<body>` and
  classes it adds to `<html>`/`<body>` are removed when the theme is switched off, even if its undo forgets them.
