# 0020. Screenshots for the assistant

Status: accepted (2026-11-09). Builds on 0010 (assistant), 0013 (engine), 0018 (skills, theme scripts), 0002 (themes).

## Why

The assistant made and edited themes without ever seeing the result; its skills said «you cannot look at the screen».
Now it can take a picture of the app and judge by it. Image generation is not part of this.

## Decision

- **Library:** `modern-screenshot@4.7.0` (MIT, `dist/index.mjs`, 54 KB, one ES module, no dependencies), vendored as
  `web/vendor/modern-screenshot.mjs` with `LICENSE.modern-screenshot` and a line in `web/vendor/README.md`. It draws the
  DOM through an SVG `foreignObject` onto a canvas, offline, no build step. It runs under the page CSP unchanged: the
  SVG goes into an `<img>` as a `data:` address (`img-src data:`), fonts and pictures are fetched from the same origin
  (`connect-src 'self'`), no `eval`, and no worker is started (`workerUrl` is not given). It is loaded with a dynamic
  `import()` only when a capture is asked for.
- **Flow:** the tool `screenshot {what: screen|phone|tablet}` (`services/assistant_app.py`) calls
  `services/assistant_screenshots.request`: a random single-use id, the notice `assistant_capture {id, what, long_side,
  max_bytes}` to the open pages (`core/notices.py`), then it waits 20 s. `web/core/capture.js` draws, downsizes to 1280 px
  on the long side, encodes PNG (JPEG with falling quality if over 2 MB) and POSTs it to
  `POST /api/assistant/screenshots/{id}`. No page open: the tool says «Откройте приложение в браузере…»; a timeout or a
  page that failed (`?failed=reason`) gives a clear tool error too.
- **Phone and tablet:** `capture.js` loads the same page, `/?capture=1`, in a hidden same-origin frame of that size
  (offscreen, `aria-hidden`) and draws the frame, so a tablet user also gets the phone look. The frame ignores capture
  notices (`window.top !== window`).
- **The one CSP change:** the page is sent with `X-Frame-Options: DENY` and `frame-ancestors 'none'`. For the single
  address `/?capture=1` the server sends `SAMEORIGIN` and `frame-ancestors 'self'` instead (`app.py`, `with_headers`).
  Other origins still cannot frame anything; every other address is unchanged. Tests pin both.
- **To the model:** `Outcome.images` → `Env.pictures` → the next «Results» message carries them as image parts (the step
  loop, the writer's items and the critic). The model's capability is not known to the app; the tool result tells the model
  to say plainly if it sees no picture, and to ask for a model that reads pictures. The picture is never saved in the
  conversation, the journal or the logs: only the words of the result are.

## Privacy and security

- A capture happens only when the assistant's own tool asks while a page is open; each one shows the toast
  «🪄 Помощник сделал снимок экрана».
- `input[type=password]` elements are left out of every picture (API keys and app passwords are such fields; the browser
  gets no stored key values anyway).
- The upload route accepts only ids the server made, still waiting, once; they expire with the tool's wait. Content is
  checked by its bytes (PNG or JPEG only), at most 2 MB. The route is under `/assistant`, which `app_call` refuses, so
  the assistant cannot upload a picture to itself.

## Consequences

- Skills `unslop` and `theme-scripts` now tell the assistant to take a screenshot after changing a theme, at phone size too.
  The import notes no longer list screenshots among the things it cannot do.
- A screenshot is one still frame: it does not show motion, and an effects script shows only after the person allows it.
- Not seen on screen at the time of writing: the drawing by the library, the hidden frame, the toast.
