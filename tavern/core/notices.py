"""Notices: app-wide events for every open page, outside any one generation run.

A run's events (core/runs.py) end with the run; these do not. The browser keeps one stream open
(`GET /api/events`) and reacts: a plugin asks for permission to call a model, a plugin was switched
off after a crash, and so on. Nothing is replayed: what a late page needs it reads with a normal
GET (pending questions: `GET /api/plugins/asks`).

Types used by the core:
  plugin_ask    {ask}        a plugin wants to call a model and the user asked to be asked
  plugin_asked  {id}         that question was answered or expired (close the dialog)
  plugins       {}           plugins changed (enabled, crashed): reload the list
  plugin        {plugin, name, data}   a plugin's server part to its web part (PluginAPI.notify)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any


class Notices:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict[str, Any]]] = set()

    def emit(self, type: str, **data: Any) -> None:
        event = {"type": type, **data}
        for queue in self._queues:
            queue.put_nowait(event)

    async def subscribe(self, heartbeat: float = 0.0) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._queues.add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), heartbeat) if heartbeat else await queue.get()
                except asyncio.TimeoutError:
                    yield {"type": "ping"}
        finally:
            self._queues.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._queues)
