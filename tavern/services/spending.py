"""Spending: what every model request cost, where it is shown, and the daily/monthly limit.

Every request made through an `LLMClient` that carries a `meter()` is recorded (storage/repos/
spending.py): tokens as the service reported them (or a rough guess when it did not, or the reply
was stopped), and the cost in dollars when a price is known. The user's own price for a model wins
over the one the service's model list gave (model_info, stage 6а). Unknown price = tokens only.

The setting `spending` (settings table):
    {"show": {"message": false, "chat": false, "day": false, "month": false},  where it is shown
     "tokens": true,                         show token counts beside money
     "currency": {"code": "", "rate": 0},    "" = dollars; else shown as code at rate per dollar
     "limits": {"day": 0, "month": 0},       in the shown currency, 0 = none
     "on_limit": "warn" | "stop",
     "prices": {"<model>": {"in": 3.0, "out": 15.0}}}   own prices, dollars per million tokens
Recording never stops: the checkboxes only decide what is shown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from tavern.context import AppContext
from tavern.core.llm import MeterFn
from tavern.errors import AppError, Conflict, NotFound
from tavern.providers.base import Provider, Usage
from tavern.storage.repos.spending import today

log = logging.getLogger(__name__)

KEY = "spending"
PLACES = ("message", "chat", "day", "month")
ON_LIMIT = ("warn", "stop")
REPORT_DAYS = 31


def defaults() -> dict[str, Any]:
    return {"show": dict.fromkeys(PLACES, False), "tokens": True, "currency": {"code": "", "rate": 0.0},
            "limits": {"day": 0.0, "month": 0.0}, "on_limit": "warn", "prices": {}}


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise AppError(f"{name}: a number, 0 or more")
    return float(value)


def check(raw: Any) -> dict[str, Any]:
    """A full, clean settings object from a partial one (missing parts get defaults)."""
    if not isinstance(raw, dict):
        raise AppError("Spending settings must be an object")
    out = defaults()
    show = raw.get("show", {})
    if not isinstance(show, dict) or any(k not in PLACES for k in show):
        raise AppError(f"show: places are {', '.join(PLACES)}")
    out["show"].update({k: bool(v) for k, v in show.items()})
    out["tokens"] = bool(raw.get("tokens", True))
    currency = raw.get("currency", {})
    if not isinstance(currency, dict):
        raise AppError("currency must be an object")
    code = currency.get("code", "")
    if not isinstance(code, str) or len(code.strip()) > 8:
        raise AppError("currency.code: up to 8 characters")
    out["currency"] = {"code": code.strip(), "rate": _number(currency.get("rate", 0), "currency.rate")}
    if out["currency"]["code"] and not out["currency"]["rate"]:
        raise AppError("currency.rate: how much of it one dollar is")
    limits = raw.get("limits", {})
    if not isinstance(limits, dict):
        raise AppError("limits must be an object")
    out["limits"] = {k: _number(limits.get(k, 0), f"limits.{k}") for k in ("day", "month")}
    on_limit = raw.get("on_limit", "warn")
    if on_limit not in ON_LIMIT:
        raise AppError(f"on_limit: one of {', '.join(ON_LIMIT)}")
    out["on_limit"] = on_limit
    prices = raw.get("prices", {})
    if not isinstance(prices, dict):
        raise AppError("prices must be an object")
    for model, price in prices.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(price, dict):
            raise AppError("prices: {model: {in, out}}")
        out["prices"][model.strip()] = {k: _number(price.get(k, 0), f"price of {model}") for k in ("in", "out")}
    return out


def get_settings(ctx: AppContext) -> dict[str, Any]:
    raw = ctx.settings.get(KEY)
    try:
        return check(raw) if raw else defaults()
    except AppError:
        log.warning("Spending settings are broken; using defaults")
        return defaults()


def save_settings(ctx: AppContext, raw: Any) -> dict[str, Any]:
    settings = check(raw)
    ctx.settings.set(KEY, settings)
    return settings


# Prices


def _priced(info: dict[str, Any] | None) -> bool:
    return info is not None and (info.get("price_in") is not None or info.get("price_out") is not None)


def price_for(ctx: AppContext, connection_id: str, model: str,
              settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """{"in", "out" (dollars per million, None = unknown), "source": "own" | "service" | ""}.
    The service's price comes from this connection's model list, else any connection listing it."""
    own = (settings or get_settings(ctx))["prices"].get(model)
    if own is not None:
        return {**own, "source": "own"}
    infos = ctx.connections.model_infos(model)
    found = next((i for i in infos if i["connection_id"] == connection_id and _priced(i)), None)
    found = found or next((i for i in infos if _priced(i)), None)
    if found is None:
        return {"in": None, "out": None, "source": ""}
    return {"in": found.get("price_in"), "out": found.get("price_out"), "source": "service"}


def cost_of(price: dict[str, Any], usage: Usage) -> float | None:
    if price["in"] is None and price["out"] is None:
        return None
    return (usage.prompt_tokens * (price["in"] or 0) + usage.completion_tokens * (price["out"] or 0)) / 1_000_000


# Recording


@dataclass
class Tally:
    """What one reply version cost: the sum of its requests (saved in the swipe's meta)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    unpriced: bool = False
    estimated: bool = False
    requests: int = 0

    def add(self, usage: Usage, cost: float | None, estimated: bool) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.requests += 1
        self.estimated = self.estimated or estimated
        if cost is None:
            self.unpriced = True
        else:
            self.cost += cost

    def as_meta(self) -> dict[str, Any]:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "cost": None if self.unpriced and not self.cost else round(self.cost, 8),
                "unpriced": self.unpriced, "estimated": self.estimated, "requests": self.requests}


def meter(
    ctx: AppContext, connection: dict[str, Any], *, chat_id: str | None = None, message_id: str | None = None,
    purpose: str = "chat", tally: Tally | None = None,
) -> MeterFn:
    """A meter for LLMClient: records each request against `connection` (or the fallback that
    actually answered) and adds it to `tally`. Never raises: a broken record must not cost a reply."""

    def record(model: str, usage: Usage, estimated: bool, provider: Provider) -> float | None:
        discarded = getattr(provider, "discarded", None)
        while discarded:  # a fallback threw these attempts away; they were paid for all the same
            serving, spent = discarded.pop(0)
            one(serving, model, spent, False)
        return one(getattr(provider, "serving", ("", "")), model, usage, estimated)

    def one(serving: tuple[str, str], model: str, usage: Usage, estimated: bool) -> float | None:
        target = connection
        served_connection, served_model = serving
        if served_connection and served_connection != connection.get("id"):
            try:
                target = ctx.connections.get(served_connection)
            except NotFound:
                pass
        model = served_model or model or target.get("model", "")
        cost = None
        try:
            cost = cost_of(price_for(ctx, target.get("id", ""), model), usage)
            ctx.spending.record(
                chat_id=chat_id, message_id=message_id, connection_id=target.get("id", ""),
                connection_name=target.get("name", ""), model=model, purpose=purpose,
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                cost=cost, estimated=estimated)
        except Exception:  # noqa: BLE001 - see the docstring
            log.exception("Could not record spending of a request to %s", model)
        if tally is not None:
            tally.add(usage, cost, estimated)
        return cost

    return record


def record_call(
    ctx: AppContext, connection: dict[str, Any], purpose: str, *, model: str = "", tokens: int = 0,
    usage: Usage | None = None,
) -> None:
    """A paid request that does not go through an LLMClient: vectors, a picture, a trial request.
    `tokens`: a rough guess when the service reports nothing (a picture has none: counted, price unknown)."""
    spent = usage if usage is not None and usage.total else Usage(prompt_tokens=tokens)
    meter(ctx, connection, purpose=purpose)(model, spent, not (usage and usage.total), None)


# Limits and summaries


def rate(settings: dict[str, Any]) -> float:
    """Shown currency per dollar (1 when money is shown in dollars). Plugin limits use it too."""
    return settings["currency"]["rate"] if settings["currency"]["code"] else 1.0


_rate = rate


def limit_state(ctx: AppContext, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Spent today and this month (dollars) and which limit, if any, is reached ("day" / "month" / "")."""
    settings = settings or get_settings(ctx)
    day = today()
    spent = {"day": ctx.spending.day(day), "month": ctx.spending.month(day[:7])}
    over = ""
    for period in ("month", "day"):
        limit = settings["limits"][period]
        if limit and spent[period]["cost"] * _rate(settings) >= limit:
            over = period
    return {"day": spent["day"], "month": spent["month"], "over": over}


def _limit_text(settings: dict[str, Any], state: dict[str, Any]) -> str:
    period = state["over"]
    code = settings["currency"]["code"] or "$"
    limit = settings["limits"][period]
    spent = state[period]["cost"] * _rate(settings)
    return (f"Лимит расхода за {'день' if period == 'day' else 'месяц'} достигнут: "
            f"{spent:.2f} из {limit:g} {code}")


def guard(ctx: AppContext) -> str:
    """Before a new reply. Raises when a limit is reached and the setting says stop; returns a
    warning text ("" = fine) when it says warn."""
    settings = get_settings(ctx)
    if not any(settings["limits"].values()):
        return ""
    state = limit_state(ctx, settings)
    if not state["over"]:
        return ""
    text = _limit_text(settings, state)
    if settings["on_limit"] == "stop":
        raise Conflict(text + ". Поднимите лимит в настройках или подождите.", code="spend_limit")
    return text


def summary(ctx: AppContext, chat_id: str = "") -> dict[str, Any]:
    """Everything the UI shows: the settings, today, this month, the chat, the limit state."""
    settings = get_settings(ctx)
    state = limit_state(ctx, settings)
    return {"settings": settings, "day": state["day"], "month": state["month"], "over": state["over"],
            "chat": ctx.spending.chat(chat_id) if chat_id else None}


def report(ctx: AppContext) -> dict[str, Any]:
    """The last month by model (with the price used now) and by day."""
    settings = get_settings(ctx)
    since = (date.fromisoformat(today()) - timedelta(days=REPORT_DAYS - 1)).isoformat()
    models = ctx.spending.by_model(since)
    for row in models:
        row["price"] = price_for(ctx, row["connection_id"], row["model"], settings)
    # Plugins' requests, one line each (the owner asked to see what every plugin spends).
    found = ctx.plugins.discover()
    plugins = ctx.spending.by_purpose("plugin:", since)
    for row in plugins:
        plugin_id = row["purpose"].removeprefix("plugin:")
        item = found.get(plugin_id)
        row["plugin"] = plugin_id
        row["name"] = (item.manifest or {}).get("name", plugin_id) if item else plugin_id
    assistant = ctx.spending.by_purpose("assistant", since)  # the assistant over the chat, its own line
    return {"since": since, "models": models, "days": ctx.spending.by_day(since), "plugins": plugins,
            "assistant": assistant[0] if assistant else None}
