"""Several requests at once: the same API several times, or different APIs side by side.

The setting `parallel` (settings table) says what goes along with the main request:
    {"enabled": false, "mode": "variants" | "first" | "pick",
     "targets": [{"connection": "<id>" or "" (the active one), "model": "" (its own), "count": 1}]}
- variants: the main reply streams as usual; the others become more versions (swipes) of it.
- first: everything starts at once; the first that starts writing wins, the rest are stopped.
- pick: like variants, and the chat then shows all versions side by side to choose one.

`gather()` is the same machinery for code that is not a chat (plugins, agents): run several
(provider, model) calls on the same messages at once and get every result or its error.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from tavern.context import AppContext
from tavern.core.llm import LLMClient, MeterFn
from tavern.errors import AppError, NotFound
from tavern.providers.base import ChatMessage, Provider, StreamEvent
from tavern.providers.params import request_params

log = logging.getLogger(__name__)

KEY = "parallel"
MODES = ("variants", "first", "pick")
MAX_EXTRA = 8  # requests on top of the main one


@dataclass
class Target:
    """One request: a provider (closed by whoever made it), the model, how it is shown."""

    provider: Provider
    model: str
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    connection: dict[str, Any] | None = None  # whose settings and prices these are
    meter: MeterFn | None = None  # spending of this target; None = the parent client's


@dataclass
class Result:
    label: str
    text: str = ""
    reasoning: str = ""
    error: str = ""
    usage: dict[str, Any] | None = None  # what it cost (spending.Tally.as_meta), when metered


def get_settings(ctx: AppContext) -> dict[str, Any]:
    raw = ctx.settings.get(KEY)
    try:
        return check(raw) if raw else check({})
    except AppError:
        return check({})


def check(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AppError("Settings of parallel requests must be an object")
    mode = raw.get("mode", "variants")
    if mode not in MODES:
        raise AppError(f"mode: one of {', '.join(MODES)}")
    targets = raw.get("targets", [])
    if not isinstance(targets, list):
        raise AppError("targets must be a list")
    clean = []
    for item in targets:
        if not isinstance(item, dict):
            raise AppError("Each target must be an object")
        count = item.get("count", 1)
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_EXTRA:
            raise AppError(f"count: from 1 to {MAX_EXTRA}")
        connection, model = item.get("connection", ""), item.get("model", "")
        if not isinstance(connection, str) or not isinstance(model, str):
            raise AppError("connection and model must be strings")
        clean.append({"connection": connection.strip(), "model": model.strip(), "count": count})
    if sum(t["count"] for t in clean) > MAX_EXTRA:
        raise AppError(f"At most {MAX_EXTRA} extra requests at once")
    return {"enabled": bool(raw.get("enabled", False)), "mode": mode, "targets": clean}


def save_settings(ctx: AppContext, raw: Any) -> dict[str, Any]:
    settings = check(raw)
    for target in settings["targets"]:
        if target["connection"]:
            ctx.connections.get(target["connection"])  # NotFound for a deleted one
    ctx.settings.set(KEY, settings)
    return settings


def extra_targets(
    ctx: AppContext, main: dict[str, Any], settings: dict[str, Any], *, overrides: dict[str, Any] | None = None
) -> list[Target]:
    """The extra requests for one reply, each with its own connection's settings (plus `overrides`,
    the preset's model settings). A target whose connection was deleted is skipped."""
    out: list[Target] = []
    for item in settings["targets"]:
        try:
            connection = ctx.connections.get(item["connection"]) if item["connection"] else main
        except NotFound:
            log.warning("Parallel target %s no longer exists; skipped", item["connection"])
            continue
        model = item["model"] or connection["model"]
        label = f"{connection['name']} · {model}" if model else connection["name"]
        for n in range(item["count"]):
            provider = ctx.providers.create({**connection, "model": model})
            out.append(Target(provider, model, label + (f" #{n + 1}" if item["count"] > 1 else ""),
                              {**request_params(connection["kind"], connection["params"]), **(overrides or {})},
                              {**connection, "model": model}))
    return out


def client(llm: LLMClient, target: Target) -> LLMClient:
    """A client for the target with its own settings, sharing `llm`'s budget and trace."""
    other = llm.fork(provider=target.provider, model=target.model, meter=target.meter)
    other.defaults = dict(target.params)  # not the main connection's: kinds take different settings
    return other


async def collect(llm: LLMClient, target: Target, messages: list[ChatMessage], params: dict[str, Any]) -> Result:
    """One whole reply from a target; an error becomes part of the result, never raised."""
    other = client(llm, target)
    text: list[str] = []
    thoughts: list[str] = []
    try:
        async for event in other.stream(messages, **params):
            if event.kind == "text":
                text.append(event.text)
            elif event.kind == "reasoning":
                thoughts.append(event.text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one failed variant must not sink the others
        log.info("Parallel request to %s failed: %s", target.label, exc)
        return Result(target.label, error=str(getattr(exc, "message", exc)))
    return Result(target.label, "".join(text), "".join(thoughts))


async def gather(llm: LLMClient, targets: list[Target], messages: list[ChatMessage],
                 **params: Any) -> list[Result]:
    """Every target on the same messages at once, sharing `llm`'s budget. Order is kept."""
    return list(await asyncio.gather(*(collect(llm, t, messages, params) for t in targets)))


async def race(
    streams: list[tuple[str, AsyncIterator[StreamEvent]]],
) -> AsyncIterator[tuple[str, StreamEvent]]:
    """Run several streams; the first to produce text wins and only its events come out.
    The losers are cancelled. Raises the last error when every stream fails without text."""
    queue: asyncio.Queue[tuple[int, StreamEvent | BaseException | None]] = asyncio.Queue()

    async def pump(index: int, stream: AsyncIterator[StreamEvent]) -> None:
        try:
            async for event in stream:
                await queue.put((index, event))
            await queue.put((index, None))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - handed to the consumer
            await queue.put((index, exc))

    tasks = [asyncio.create_task(pump(i, s)) for i, (_, s) in enumerate(streams)]
    winner: int | None = None
    alive = set(range(len(streams)))
    failure: BaseException | None = None
    pending_reasoning: dict[int, list[StreamEvent]] = {}
    try:
        while alive:
            index, item = await queue.get()
            if winner is not None and index != winner:
                continue
            if item is None or isinstance(item, BaseException):
                alive.discard(index)
                if isinstance(item, BaseException):
                    failure = item
                if index == winner:
                    if isinstance(item, BaseException):
                        raise item
                    return
                continue
            if winner is None:
                if item.kind != "text":
                    pending_reasoning.setdefault(index, []).append(item)
                    continue
                winner = index
                for i, task in enumerate(tasks):
                    if i != winner:
                        task.cancel()
                for early in pending_reasoning.get(index, []):
                    yield streams[index][0], early
            yield streams[index][0], item
        if failure is not None:
            raise failure
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
