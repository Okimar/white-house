"""Screenshots for the assistant (ADR 0020): it asks an open page to capture itself.

Flow: the tool `screenshot` calls `request()`; that makes a random single-use id, sends the notice
`assistant_capture {id, what}` to every open page (core/notices.py) and waits. The page draws itself
(web/core/capture.js) and POSTs the picture to `/api/assistant/screenshots/{id}` (`accept()`); the tool then
returns the picture to the model as an image part. Nothing is written to disk or to the database, and the
picture never goes into the conversation's rows: only the words «снимок сделан» do.

Only ids the server made, still waiting, are accepted, once. The route is under `/assistant`, which `app_call`
refuses, so the assistant cannot upload a picture to itself.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

from tavern.context import AppContext
from tavern.errors import AppError, NotFound
from tavern.formats.media import sniff_image

WHAT = {"screen": "the screen as the person sees it", "phone": "the app at phone size (375x667)",
        "tablet": "the app at tablet size (768x1280)"}
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 20.0  # seconds the tool waits for the page
LONG_SIDE = 1280  # the page downsizes to this
MEDIA = {".png": "image/png", ".jpg": "image/jpeg"}


class NoPage(Exception):
    """No page is open, or it did not answer in time: the message is for the assistant to tell the user."""


class BadScreenshot(AppError):
    code = "bad_screenshot"


@dataclass
class _Pending:
    what: str
    future: asyncio.Future[tuple[str, str]] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


_registries: weakref.WeakKeyDictionary[Any, dict[str, _Pending]] = weakref.WeakKeyDictionary()


def _pending(ctx: AppContext) -> dict[str, _Pending]:
    return _registries.setdefault(ctx.notices, {})  # one registry per running app, no change to the context


async def request(ctx: AppContext, what: str, *, timeout: float | None = None) -> tuple[str, str]:
    """Ask an open page for a picture; returns (media type, base64). Raises NoPage."""
    if what not in WHAT:
        raise NoPage(f"`what` must be one of: {', '.join(WHAT)}")
    if not ctx.notices.listeners:
        raise NoPage("Откройте приложение в браузере, чтобы я мог сделать снимок (сейчас ни одна страница не открыта).")
    pending = _Pending(what)
    ident = secrets.token_urlsafe(16)
    registry = _pending(ctx)
    registry[ident] = pending
    ctx.notices.emit("assistant_capture", id=ident, what=what, long_side=LONG_SIDE, max_bytes=MAX_BYTES)
    try:
        return await asyncio.wait_for(asyncio.shield(pending.future), timeout or TIMEOUT)
    except asyncio.TimeoutError:
        raise NoPage("Страница не ответила на запрос снимка за %d с (возможно, вкладка свёрнута или закрыта). "
                     "Попросите открыть приложение и повторите." % (timeout or TIMEOUT)) from None
    finally:
        registry.pop(ident, None)  # expired or used: the id never works again


def accept(ctx: AppContext, ident: str, blob: bytes) -> None:
    """The page's answer. Unknown, used or expired id: NotFound. Not a PNG / JPEG or too big: BadScreenshot."""
    pending = _pending(ctx).get(ident)
    if pending is None or pending.future.done():
        raise NotFound("Нет такого запроса снимка (он выполнен или истёк)")
    if len(blob) > MAX_BYTES:
        raise BadScreenshot(f"Снимок больше {MAX_BYTES // (1024 * 1024)} МБ", code="too_large", status=413)
    kind = sniff_image(blob)
    if kind not in MEDIA:
        raise BadScreenshot("Снимок должен быть PNG или JPEG")
    pending.future.set_result((MEDIA[kind], base64.b64encode(blob).decode("ascii")))


def refuse(ctx: AppContext, ident: str, why: str) -> None:
    """The page could not draw itself: the tool gets the reason instead of waiting for the timeout."""
    pending = _pending(ctx).get(ident)
    if pending is not None and not pending.future.done():
        pending.future.set_exception(NoPage(f"Страница не смогла сделать снимок: {why[:200]}"))
