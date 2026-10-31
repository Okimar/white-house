"""Provider kinds and construction from a saved connection.

Plugins register additional kinds with `registry.register(kind, factory)`.
A factory receives (connection dict, api_key, http_client) and returns a Provider.
`create()` wraps the providers of all the connection's keys in a `KeyedProvider`; a key with a
proxy gets an HTTP client of its own going through that proxy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from tavern.errors import AppError
from tavern.core import templates as T
from tavern.providers import keys as K
from tavern.providers.anthropic import AnthropicProvider
from tavern.providers.base import Provider
from tavern.providers.fallback import FallbackProvider
from tavern.providers.gemini import GeminiProvider
from tavern.providers.openai_compat import OpenAICompatProvider, make_client
from tavern.providers.params import tavern_setting
from tavern.secret_store import SecretStore

ProviderFactory = Callable[[dict[str, Any], str, "httpx.AsyncClient | None"], Provider]


class UnknownProviderKind(AppError):
    code = "unknown_provider"


def _openai(conn: dict[str, Any], api_key: str, client: httpx.AsyncClient | None) -> Provider:
    return OpenAICompatProvider(conn["base_url"], api_key, client=client, template=conn.get("template"))


def _gemini(conn: dict[str, Any], api_key: str, client: httpx.AsyncClient | None) -> Provider:
    return GeminiProvider(conn["base_url"], api_key, client=client)


def _anthropic(conn: dict[str, Any], api_key: str, client: httpx.AsyncClient | None) -> Provider:
    return AnthropicProvider(conn["base_url"], api_key, client=client)


def key_name(connection_id: str) -> str:
    """The single key of older versions (kept readable; new keys go to `keys.keys_name`)."""
    return K.legacy_key_name(connection_id)


class ProviderRegistry:
    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets
        self._factories: dict[str, ProviderFactory] = {"openai": _openai, "gemini": _gemini, "anthropic": _anthropic}
        # Tests inject an httpx client wired to a fake server; production leaves it None.
        self.http_client: httpx.AsyncClient | None = None
        self._key_index: dict[str, int] = {}  # connection id -> key that worked last
        # (template id, model) -> instruct template; set by the context so user templates count.
        self.template_resolver: Callable[[str, str], dict[str, Any]] | None = None
        # connection id -> saved connection, for fallback steps that go to another connection.
        self.connection_lookup: Callable[[str], dict[str, Any]] | None = None

    def register(self, kind: str, factory: ProviderFactory) -> None:
        self._factories[kind] = factory

    def unregister(self, kind: str) -> None:
        self._factories.pop(kind, None)

    @property
    def kinds(self) -> list[str]:
        return sorted(self._factories)

    def keys(self, connection_id: str) -> list[dict[str, str]]:
        return K.load_keys(self._secrets, connection_id)

    def create(
        self, connection: dict[str, Any], *, api_key: str | None = None, proxy: str = "",
        fallbacks: bool = True, only_key: str = "",
    ) -> Provider:
        """Provider for a saved connection (keys from secrets) or a draft with an explicit key.

        With `fallbacks` (default) the connection's fallback steps wrap it; checks of one
        connection (model list, trial requests) pass False so they test that connection alone.
        `only_key` limits it to one key (a fallback step naming a key)."""
        kind = connection["kind"]
        factory = self._factories.get(kind)
        if factory is None:
            raise UnknownProviderKind(
                f"Unknown provider kind {kind!r}. Available: {', '.join(self.kinds)}"
            )
        params = connection.get("params") or {}
        if kind == "openai" and params.get("api_mode") == "text":
            template_id = str(params.get("template") or "auto")
            model = str(connection.get("model") or "")
            template = (self.template_resolver(template_id, model) if self.template_resolver
                        else T.BUILTIN[T.detect(model, T.BUILTIN)])
            connection = {**connection, "template": template}
        if api_key is None:
            keys = self.keys(connection["id"])
            if only_key:
                keys = [k for k in keys if k["id"] == only_key] or keys
        else:
            keys = [{"id": "draft", "label": "", "key": api_key, "proxy": proxy}]

        def build(key: dict[str, str]) -> tuple[Provider, httpx.AsyncClient | None]:
            client = self.http_client
            own = None
            if client is None and key["proxy"]:
                client = own = make_client(connection["base_url"], key["proxy"])
            return factory(connection, key["key"], client), own

        provider: Provider = K.KeyedProvider(
            kind, keys, build,
            rotate=bool(tavern_setting(params, "rotate_keys")) and not only_key,
            retries=int(tavern_setting(params, "retries")),
            retry_delay=float(tavern_setting(params, "retry_delay")),
            remember=self._key_index, remember_as=connection.get("id") or "",
        )
        steps = params.get("fallbacks") or []
        if fallbacks and steps and api_key is None:
            provider = FallbackProvider(provider, steps, lambda step: self._fallback(connection, step),
                                        phrases=params.get("refusal_phrases") or None)
        return provider

    def _fallback(self, connection: dict[str, Any], step: dict[str, Any]) -> tuple[Provider, str, str]:
        target = connection
        if step["connection"] and step["connection"] != connection.get("id"):
            if self.connection_lookup is None:
                raise UnknownProviderKind("Fallback to another connection is not available here")
            target = self.connection_lookup(step["connection"])
        model = step["model"] or target.get("model") or ""
        target = {**target, "model": model}
        provider = self.create(target, fallbacks=False, only_key=step["key"])
        label = target.get("name", "")
        if model:
            label += f" · {model}"
        if step["key"]:
            key = next((k for k in self.keys(target["id"]) if k["id"] == step["key"]), None)
            if key is not None:
                label += f" · {key['label'] or 'ключ'}"
        return provider, model, label
