"""What a connection's models can do: the service's own list, and tiny trial requests.

"Проверить" asks the service for its model list (free) and keeps what it says: context size,
accepted settings, prices. Where the list says nothing about settings, "Пробные запросы" sends one
tiny request per setting (a reply of one token) and records whether the service took it.
A service that silently ignores an unknown setting looks the same as one that accepts it; only
outright refusals are detectable, and the window says so.
One more trial, STRUCTURED, asks for a tiny JSON answer in the JSON mode: "ok" = the answer fit,
"words" = the mode was rejected but the request in words worked, "rejected" = neither gave JSON.
"""

from __future__ import annotations

from typing import Any

import anyio

from tavern.context import AppContext
from tavern.core import structured
from tavern.core.llm import extract_json
from tavern.providers import params as P
from tavern.providers.base import ChatMessage, GenerationRequest, Provider, ProviderError

PROBE_PARALLEL = 3
# Values sent when trying a setting: harmless, and different from "not sent".
PROBE_VALUES: dict[str, Any] = {
    "temperature": 0.7, "top_p": 0.9, "top_k": 40, "min_p": 0.05, "top_a": 0.1, "typical_p": 0.9, "tfs": 0.9,
    "repetition_penalty": 1.05, "frequency_penalty": 0.1, "presence_penalty": 0.1, "stop": ["\n###"],
    "seed": 42, "logit_bias": {}, "reasoning_effort": "low", "thinking_budget": 128,
    "include_thoughts": True, "safety_off": True,
}
PROBE_MESSAGES = [ChatMessage("user", "Say OK.")]
STRUCTURED = "structured"  # not a setting: "does it answer in a given JSON shape"
STRUCTURED_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
                     "additionalProperties": False}


def probeable(kind: str) -> list[str]:
    """Settings of this kind worth a trial request (the ones that go to the backend)."""
    return [p["id"] for p in P.PARAMS
            if p.get("send", True) and kind in p["kinds"] and p["id"] in PROBE_VALUES]


async def inspect(ctx: AppContext, connection: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask the service about its models and remember the answer."""
    provider = ctx.providers.create(connection, fallbacks=False)
    try:
        infos = await provider.describe_models()
    finally:
        await provider.aclose()
    ctx.connections.replace_models(connection["id"], [i.as_dict() for i in infos])
    return ctx.connections.models(connection["id"])


async def probe(
    ctx: AppContext, connection: dict[str, Any], model: str, names: list[str] | None = None
) -> dict[str, str]:
    """Try each setting with a one-token reply. Result per setting: "ok", "rejected" or "unknown"
    (the trial failed for another reason, e.g. the limit ran out). Raises ProviderError when even
    a request with no settings fails: then the connection or model itself is broken."""
    wanted = [n for n in (names or probeable(connection["kind"])) if n in PROBE_VALUES]
    provider = ctx.providers.create(connection, fallbacks=False)
    try:
        await _try(provider, model, {})
        results: dict[str, str] = {}
        limiter = anyio.Semaphore(PROBE_PARALLEL)

        async def one(name: str) -> None:
            async with limiter:
                try:
                    await _try(provider, model, {name: PROBE_VALUES[name]})
                except ProviderError as exc:
                    results[name] = "rejected" if exc.reason == "request" else "unknown"
                else:
                    results[name] = "ok"

        async with anyio.create_task_group() as tg:
            for name in wanted:
                tg.start_soon(one, name)
        if names is None or STRUCTURED in names:
            results[STRUCTURED] = await _try_structured(provider, model)
    finally:
        await provider.aclose()
    return ctx.connections.set_probe(connection["id"], model, results)


async def _try_structured(provider: Provider, model: str) -> str:
    messages = [ChatMessage("user", "Is water wet? " + structured.instruction(STRUCTURED_SCHEMA))]
    fmt = {"type": "json_schema", "name": "response", "schema": STRUCTURED_SCHEMA}
    answer = "ok"
    try:
        try:
            result = await provider.generate(GenerationRequest(messages, model, max_tokens=40, response_format=fmt))
        except ProviderError as exc:
            if exc.reason != "request":
                raise
            answer = "words"
            result = await provider.generate(GenerationRequest(messages, model, max_tokens=40))
        return answer if not structured.problems(extract_json(result.text), STRUCTURED_SCHEMA) else "rejected"
    except ProviderError as exc:
        return "rejected" if exc.reason in ("request", "") else "unknown"


async def _try(provider: Provider, model: str, settings: dict[str, Any]) -> None:
    extra = dict(settings)
    request = GenerationRequest(
        messages=PROBE_MESSAGES, model=model, max_tokens=extra.pop("max_tokens", 1),
        temperature=extra.pop("temperature", None), top_p=extra.pop("top_p", None),
        stop=extra.pop("stop", []), extra=extra,
    )
    await provider.generate(request)
