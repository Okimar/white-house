"""API connections. Keys and proxies are write-only: responses only say whether they are set.

A connection has any number of keys, each with an optional proxy (see `providers/keys.py`).
The older single `api_key` field still works: it replaces all keys with one.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from tavern.api.util import BadRequest, ctx_of, field, json_body
from tavern.errors import AppError, NotFound
from tavern.providers import catalog as services
from tavern.providers import keys as K
from tavern.providers import params as P
from tavern.services import connections as conn_service
from tavern.services import parallel

log = logging.getLogger(__name__)


def _public(ctx: Any, conn: dict[str, Any]) -> dict[str, Any]:
    keys = ctx.providers.keys(conn["id"])
    return {**conn, "has_key": any(k["key"] for k in keys), "keys": K.public_keys(keys)}


def _params(body: dict[str, Any], default: Any) -> Any:
    raw = field(body, "params", dict, default=default)
    if raw is None:
        return None
    try:
        return P.validate(raw)
    except ValueError as exc:
        raise BadRequest(str(exc)) from None


def _key_fields(body: dict[str, Any], *, partial: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("label", "key", "proxy"):
        if name in body or not partial:
            out[name] = field(body, name, str, default="").strip()
    if "proxy" in out:
        try:
            K.check_proxy(out["proxy"])
        except ValueError as exc:
            raise BadRequest(str(exc)) from None
    return out


async def list_connections(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    return JSONResponse(
        {
            "connections": [_public(ctx, c) for c in ctx.connections.list()],
            "kinds": ctx.providers.kinds,
            "active": ctx.settings.get("active_connection"),
        }
    )


async def get_catalog(request: Request) -> JSONResponse:
    """Known services and every setting the window can show."""
    ctx = ctx_of(request)
    return JSONResponse({**services.catalog(ctx.providers.kinds), "settings": P.catalog()})


def _kind_and_url(ctx: Any, body: dict[str, Any]) -> tuple[str, str, str]:
    service = field(body, "service", str, default="")
    preset = services.BY_ID.get(service, {})
    kind = field(body, "kind", str, default=preset.get("kind", "openai"))
    if kind not in ctx.providers.kinds:
        raise BadRequest(f"Unknown provider kind {kind!r}. Available: {', '.join(ctx.providers.kinds)}")
    base_url = field(body, "base_url", str, default=preset.get("base_url", "")).strip()
    if not base_url:
        raise BadRequest("Field 'base_url' must not be empty")
    base_url, fixed = services.fix_address(base_url, kind)
    if fixed:
        log.info("Connection address corrected to %s", base_url)
    return service, kind, base_url


async def create_connection(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    body = await json_body(request)
    service, kind, base_url = _kind_and_url(ctx, body)
    raw_keys = field(body, "keys", list, default=[])
    keys = [K.new_key(**_key_fields(k, partial=False)) for k in raw_keys if isinstance(k, dict)]
    api_key = field(body, "api_key", str, default="")
    if api_key:
        keys.insert(0, K.new_key(key=api_key))
    conn = ctx.connections.create(
        name=field(body, "name", str, allow_empty=False),
        kind=kind,
        service=service,
        base_url=base_url,
        model=field(body, "model", str, default=""),
        params=_params(body, {}),
    )
    K.save_keys(ctx.secrets, conn["id"], keys)
    if ctx.settings.get("active_connection") is None:
        ctx.settings.set("active_connection", conn["id"])
    out = _public(ctx, conn)
    if base_url != str(body.get("base_url") or base_url).strip():
        out["address_fixed"] = base_url
    return JSONResponse(out, status_code=201)


async def update_connection(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    conn_id = request.path_params["id"]
    body = await json_body(request)
    kind = field(body, "kind", str, default=None)
    if kind is not None and kind not in ctx.providers.kinds:
        raise BadRequest(f"Unknown provider kind {kind!r}. Available: {', '.join(ctx.providers.kinds)}")
    new_url = field(body, "base_url", str, default=None, allow_empty=False)
    fixed_url = None
    if new_url is not None:
        kind_now = kind or ctx.connections.get(conn_id)["kind"]
        fixed_url, changed = services.fix_address(new_url, kind_now)
        if changed:
            log.info("Connection address corrected to %s", fixed_url)
        else:
            fixed_url = None
    conn = ctx.connections.update(
        conn_id,
        name=field(body, "name", str, default=None, allow_empty=False),
        kind=kind,
        service=field(body, "service", str, default=None),
        base_url=fixed_url or new_url,
        model=field(body, "model", str, default=None),
        params=_params(body, None),
    )
    if "api_key" in body:
        api_key = field(body, "api_key", str)
        K.save_keys(ctx.secrets, conn_id, [K.new_key(key=api_key)] if api_key else [])
    out = _public(ctx, conn)
    if fixed_url:
        out["address_fixed"] = fixed_url
    return JSONResponse(out)


async def delete_connection(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    conn_id = request.path_params["id"]
    ctx.connections.delete(conn_id)
    K.delete_keys(ctx.secrets, conn_id)
    if ctx.settings.get("active_connection") == conn_id:
        ctx.settings.delete("active_connection")
    return JSONResponse({"ok": True})


# Keys


async def add_key(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    keys = ctx.providers.keys(conn["id"])
    fields = _key_fields(await json_body(request), partial=False)
    if not fields["label"]:
        fields["label"] = f"Ключ {len(keys) + 1}"
    keys.append(K.new_key(**fields))
    K.save_keys(ctx.secrets, conn["id"], keys)
    return JSONResponse(_public(ctx, conn), status_code=201)


def _find_key(keys: list[dict[str, str]], key_id: str) -> dict[str, str]:
    for key in keys:
        if key["id"] == key_id:
            return key
    raise NotFound(f"Key {key_id} not found")


async def update_key(request: Request) -> JSONResponse:
    """Change a key's label, key or proxy. An empty `key` keeps the stored one; an empty `proxy`
    removes the proxy."""
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    keys = ctx.providers.keys(conn["id"])
    key = _find_key(keys, request.path_params["key_id"])
    fields = _key_fields(await json_body(request), partial=True)
    if not fields.get("key"):
        fields.pop("key", None)
    key.update(fields)
    K.save_keys(ctx.secrets, conn["id"], keys)
    return JSONResponse(_public(ctx, conn))


async def delete_key(request: Request) -> JSONResponse:
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    keys = ctx.providers.keys(conn["id"])
    gone = _find_key(keys, request.path_params["key_id"])
    K.save_keys(ctx.secrets, conn["id"], [k for k in keys if k is not gone])
    return JSONResponse(_public(ctx, conn))


async def order_keys(request: Request) -> JSONResponse:
    """The order in which keys are tried: {"ids": [...]} with every key id once."""
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    keys = {k["id"]: k for k in ctx.providers.keys(conn["id"])}
    ids = field(await json_body(request), "ids", list)
    if sorted(map(str, ids)) != sorted(keys):
        raise BadRequest("'ids' must list every key of the connection exactly once")
    K.save_keys(ctx.secrets, conn["id"], [keys[str(i)] for i in ids])
    return JSONResponse(_public(ctx, conn))


# Models


async def _models_of(provider: Any) -> JSONResponse:
    try:
        return JSONResponse({"models": await provider.list_models()})
    finally:
        await provider.aclose()


async def list_models(request: Request) -> JSONResponse:
    """Ask the backend which models it offers (also serves as a connection test)."""
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    if services.builtin_models(conn.get("service") or ""):
        return JSONResponse({"models": services.builtin_models(conn["service"])})
    return await _models_of(ctx.providers.create(conn, fallbacks=False))


async def probe_models(request: Request) -> JSONResponse:
    """Model list for settings not saved yet, so the form can offer models while it is filled in."""
    ctx = ctx_of(request)
    body = await json_body(request)
    service, kind, base_url = _kind_and_url(ctx, body)
    if services.builtin_models(service):
        return JSONResponse({"models": services.builtin_models(service)})
    proxy = field(body, "proxy", str, default="").strip()
    try:
        K.check_proxy(proxy)
    except ValueError as exc:
        raise BadRequest(str(exc)) from None
    draft = {"id": "", "kind": kind, "base_url": base_url, "params": {}}
    provider = ctx.providers.create(draft, api_key=field(body, "api_key", str, default=""), proxy=proxy)
    return await _models_of(provider)


async def model_info(request: Request) -> JSONResponse:
    """What is already known about the connection's models (no request to the service)."""
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    return JSONResponse({"models": ctx.connections.models(conn["id"]),
                         "probeable": conn_service.probeable(conn["kind"])})


async def inspect_models(request: Request) -> JSONResponse:
    """Ask the service about its models now and remember the answer."""
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    return JSONResponse({"models": await conn_service.inspect(ctx, conn),
                         "probeable": conn_service.probeable(conn["kind"])})


async def probe_settings(request: Request) -> JSONResponse:
    """Trial requests: which settings the model takes. Body: {"model"?, "names"?: [...]}."""
    ctx = ctx_of(request)
    conn = ctx.connections.get(request.path_params["id"])
    body = await json_body(request)
    model = field(body, "model", str, default=conn["model"]).strip()
    if not model:
        raise BadRequest("Choose a model first")
    names = field(body, "names", list, default=None)
    results = await conn_service.probe(ctx, conn, model, [str(n) for n in names] if names else None)
    return JSONResponse({"model": model, "probe": results})


async def get_parallel(request: Request) -> JSONResponse:
    return JSONResponse(parallel.get_settings(ctx_of(request)))


async def put_parallel(request: Request) -> JSONResponse:
    """Body: {enabled, mode: variants | first | pick, targets: [{connection, model, count}]}."""
    ctx = ctx_of(request)
    try:
        return JSONResponse(parallel.save_settings(ctx, await json_body(request)))
    except AppError as exc:
        if isinstance(exc, NotFound):
            raise
        raise BadRequest(exc.message) from None


routes = [
    Route("/parallel", get_parallel, methods=["GET"]),
    Route("/parallel", put_parallel, methods=["PUT"]),
    Route("/connections", list_connections, methods=["GET"]),
    Route("/connections", create_connection, methods=["POST"]),
    Route("/connections/catalog", get_catalog, methods=["GET"]),
    Route("/connections/models", probe_models, methods=["POST"]),
    Route("/connections/{id}", update_connection, methods=["PUT"]),
    Route("/connections/{id}", delete_connection, methods=["DELETE"]),
    Route("/connections/{id}/keys", add_key, methods=["POST"]),
    Route("/connections/{id}/keys/order", order_keys, methods=["PUT"]),
    Route("/connections/{id}/keys/{key_id}", update_key, methods=["PUT"]),
    Route("/connections/{id}/keys/{key_id}", delete_key, methods=["DELETE"]),
    Route("/connections/{id}/models", list_models, methods=["GET"]),
    Route("/connections/{id}/model-info", model_info, methods=["GET"]),
    Route("/connections/{id}/inspect", inspect_models, methods=["POST"]),
    Route("/connections/{id}/probe", probe_settings, methods=["POST"]),
]
