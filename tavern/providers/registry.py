"""Provider kinds and construction from a saved connection.

Plugins register additional kinds with `registry.register(kind, factory)`.
A factory receives (connection dict, api_key, http_client) and returns a Provider.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from tavern.errors import AppError
from tavern.providers.base import Provider
from tavern.providers.openai_compat import OpenAICompatProvider
from tavern.secret_store import SecretStore

ProviderFactory = Callable[[dict[str, Any], str, "httpx.AsyncClient | None"], Provider]


class UnknownProviderKind(AppError):
    code = "unknown_provider"


def _openai(conn: dict[str, Any], api_key: str, client: httpx.AsyncClient | None) -> Provider:
    return OpenAICompatProvider(conn["base_url"], api_key, client=client)


def key_name(connection_id: str) -> str:
    return f"connection:{connection_id}:api_key"


class ProviderRegistry:
    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets
        self._factories: dict[str, ProviderFactory] = {"openai": _openai}
        # Tests inject an httpx client wired to a fake server; production leaves it None.
        self.http_client: httpx.AsyncClient | None = None

    def register(self, kind: str, factory: ProviderFactory) -> None:
        self._factories[kind] = factory

    def unregister(self, kind: str) -> None:
        self._factories.pop(kind, None)

    @property
    def kinds(self) -> list[str]:
        return sorted(self._factories)

    def create(self, connection: dict[str, Any], *, api_key: str | None = None) -> Provider:
        """Provider for a saved connection (key from secrets) or a draft with an explicit key."""
        factory = self._factories.get(connection["kind"])
        if factory is None:
            raise UnknownProviderKind(
                f"Unknown provider kind {connection['kind']!r}. Available: {', '.join(self.kinds)}"
            )
        if api_key is None:
            api_key = self._secrets.get(key_name(connection["id"]), "") or ""
        return factory(connection, api_key, self.http_client)
