"""Ending live event streams when the server stops.

An open page keeps `GET /api/events` and run streams (server-sent events) open for as long as it
lives. uvicorn waits for every connection before it exits, so Ctrl+C printed "Waiting for
connections to close" and then sat until its timeout. `StreamReaper` remembers the requests that
answered with `text/event-stream` and, when the server shuts down, ends them cleanly (the
response is finished, not torn), so the process exits at once and closes the database.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class StreamReaper:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self.stopping = False

    def wrap(self, app: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await app(scope, receive, send)
                return
            task = asyncio.current_task()
            state = {"stream": False, "started": False, "finished": False}

            async def watching_send(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    state["started"] = True
                    if any(k.lower() == b"content-type" and v.startswith(b"text/event-stream")
                           for k, v in message.get("headers", [])):
                        state["stream"] = True
                        if task is not None:
                            self._tasks.add(task)
                elif message["type"] == "http.response.body" and not message.get("more_body"):
                    state["finished"] = True
                await send(message)
                if state["stream"] and self.stopping and message["type"] == "http.response.start":
                    raise asyncio.CancelledError  # a page reconnecting while we stop

            try:
                await app(scope, receive, watching_send)
            except asyncio.CancelledError:
                if not (state["stream"] and self.stopping):
                    raise
                # Cancelled by us: finish the response properly so uvicorn does not log a torn one.
                if not state["finished"]:
                    try:
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                    except Exception:  # the client is already gone
                        pass
            finally:
                if task is not None:
                    self._tasks.discard(task)

        return wrapped

    async def close_all(self, wait: float = 1.0) -> int:
        """Cancel every live stream and give them a moment to finish. Returns how many were open."""
        self.stopping = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=wait)
        return len(tasks)
