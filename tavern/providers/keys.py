"""Several API keys per connection, each with its own proxy, and what to do when one fails.

Keys live in the secret store under `connection:<id>:keys` as a list of
{"id", "label", "key", "proxy"}; an older single key under `connection:<id>:api_key` reads as one
entry. The browser only ever sees `public_keys()`: labels, whether a key is set, and the proxy
address without its login and password.

`KeyedProvider` wraps the real provider of each key. Before any text arrived it may:
- repeat the same key after a pause when the service is down or unreachable (`retries`),
- go on to the next key when the current one hit its limit or was not accepted (`rotate_keys`).
The key that last worked is remembered, so the next request starts from it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import anyio
import httpx

from tavern.providers.base import GenerationRequest, ModelInfo, Provider, ProviderError, StreamEvent
from tavern.util.ids import new_id

T = TypeVar("T")
RETRY_REASONS = {"network", "server"}
ROTATE_REASONS = {"limit", "auth"}


def keys_name(connection_id: str) -> str:
    return f"connection:{connection_id}:keys"


def legacy_key_name(connection_id: str) -> str:
    return f"connection:{connection_id}:api_key"


def load_keys(secrets: Any, connection_id: str) -> list[dict[str, str]]:
    stored = secrets.get(keys_name(connection_id))
    if isinstance(stored, list):
        return [_clean(k) for k in stored if isinstance(k, dict)]
    legacy = secrets.get(legacy_key_name(connection_id))
    return [{"id": "main", "label": "", "key": str(legacy), "proxy": ""}] if legacy else []


def save_keys(secrets: Any, connection_id: str, keys: list[dict[str, str]]) -> None:
    secrets.delete(legacy_key_name(connection_id))
    if keys:
        secrets.set(keys_name(connection_id), [_clean(k) for k in keys])
    else:
        secrets.delete(keys_name(connection_id))


def delete_keys(secrets: Any, connection_id: str) -> None:
    secrets.delete(legacy_key_name(connection_id))
    secrets.delete(keys_name(connection_id))


def new_key(label: str = "", key: str = "", proxy: str = "") -> dict[str, str]:
    return {"id": new_id(), "label": label, "key": key, "proxy": proxy}


def _clean(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(raw.get("id") or new_id()),
        "label": str(raw.get("label") or ""),
        "key": str(raw.get("key") or ""),
        "proxy": str(raw.get("proxy") or ""),
    }


def public_keys(keys: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {"id": k["id"], "label": k["label"], "has_key": bool(k["key"]), "has_proxy": bool(k["proxy"]),
         "proxy": proxy_address(k["proxy"])}
        for k in keys
    ]


def proxy_address(proxy: str) -> str:
    """scheme://host:port of a proxy, without login and password."""
    if not proxy:
        return ""
    try:
        url = httpx.URL(proxy)
    except (httpx.InvalidURL, TypeError, ValueError):
        return "?"
    port = f":{url.port}" if url.port else ""
    return f"{url.scheme}://{url.host}{port}"


def check_proxy(proxy: str) -> str:
    """The proxy address as given, or an explanation of what is wrong with it."""
    if not proxy:
        return ""
    try:
        url = httpx.URL(proxy)
    except (httpx.InvalidURL, TypeError, ValueError):
        raise ValueError("Proxy address is not a valid URL") from None
    if url.scheme not in ("http", "https", "socks5", "socks5h") or not url.host:
        raise ValueError("Proxy address must look like http://host:port or socks5://host:port")
    return proxy


class KeyedProvider(Provider):
    """One connection with all its keys. Behaves as a single provider to the rest of Tavern."""

    def __init__(
        self,
        kind: str,
        keys: list[dict[str, str]],
        build: Callable[[dict[str, str]], tuple[Provider, httpx.AsyncClient | None]],
        *,
        rotate: bool = True,
        retries: int = 0,
        retry_delay: float = 0.0,
        remember: dict[str, int] | None = None,
        remember_as: str = "",
    ) -> None:
        self.kind = kind
        self._keys = keys or [{"id": "none", "label": "", "key": "", "proxy": ""}]
        self._build = build
        self._made: dict[int, Provider] = {}
        self._clients: list[httpx.AsyncClient] = []
        self.rotate = rotate
        self.retries = max(0, retries)
        self.retry_delay = max(0.0, retry_delay)
        self._remember = remember if remember is not None else {}
        self._remember_as = remember_as
        self.used_key = ""  # id of the key the last successful call went through

    def _provider(self, index: int) -> Provider:
        if index not in self._made:
            provider, client = self._build(self._keys[index])
            self._made[index] = provider
            if client is not None:
                self._clients.append(client)
        return self._made[index]

    def _order(self) -> list[int]:
        start = self._remember.get(self._remember_as, 0) % len(self._keys)
        order = [(start + i) % len(self._keys) for i in range(len(self._keys))]
        return order if self.rotate else order[:1]

    def _scrub(self, exc: ProviderError, index: int) -> ProviderError:
        """Keys and proxy logins must never reach a message or a log line."""
        secrets = [s for s in (self._keys[index]["key"], self._keys[index]["proxy"]) if len(s) >= 4]
        message = exc.message
        for secret in secrets:
            message = message.replace(secret, "***")
        label = self._keys[index]["label"] or f"#{index + 1}"
        if len(self._keys) > 1:
            message = f"[{label}] {message}"
        if message == exc.message:
            return exc
        clean = ProviderError(message, reason=exc.reason, upstream_status=exc.upstream_status, details=exc.details)
        clean.__cause__ = exc.__cause__
        return clean

    def _succeeded(self, index: int) -> None:
        self._remember[self._remember_as] = index
        self.used_key = self._keys[index]["id"]

    async def _failed(self, exc: ProviderError, attempt: int) -> str:
        """After a failure before any text: "retry" the same key, try the "next" one, or "stop"."""
        if exc.reason in RETRY_REASONS and attempt < self.retries:
            await anyio.sleep(self.retry_delay)
            return "retry"
        return "next" if exc.reason in ROTATE_REASONS else "stop"

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        last: ProviderError | None = None
        for index in self._order():
            for attempt in range(self.retries + 1):
                wrote = False
                try:
                    async for event in self._provider(index).stream(request):
                        if event.kind != "done":
                            wrote = True
                        yield event
                except ProviderError as exc:
                    last = self._scrub(exc, index)
                    if wrote:
                        raise last
                    verdict = await self._failed(exc, attempt)
                    if verdict == "retry":
                        continue
                    if verdict == "stop":
                        raise last
                    break
                self._succeeded(index)
                return
        assert last is not None
        raise last

    async def _call(self, fn: Callable[[Provider], Awaitable[T]]) -> T:
        last: ProviderError | None = None
        for index in self._order():
            for attempt in range(self.retries + 1):
                try:
                    result = await fn(self._provider(index))
                except ProviderError as exc:
                    last = self._scrub(exc, index)
                    verdict = await self._failed(exc, attempt)
                    if verdict == "retry":
                        continue
                    if verdict == "stop":
                        raise last
                    break
                self._succeeded(index)
                return result
        assert last is not None
        raise last

    async def list_models(self) -> list[str]:
        return await self._call(lambda p: p.list_models())

    async def describe_models(self) -> list[ModelInfo]:
        return await self._call(lambda p: p.describe_models())

    async def generate_image(self, prompt: str, *, model: str = "", size: str = "1024x1024") -> bytes:
        return await self._call(lambda p: p.generate_image(prompt, model=model, size=size))

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        return await self._call(lambda p: p.embed(texts, model=model))

    def inner(self, index: int = 0) -> Provider:
        """The provider of one key (tests and plugins that need kind-specific methods)."""
        return self._provider(index)

    async def aclose(self) -> None:
        for provider in self._made.values():
            await provider.aclose()
        for client in self._clients:
            await client.aclose()
        self._made.clear()
        self._clients.clear()
