"""PluginAPI: what a plugin's server part receives in `setup(api)`.

    def setup(api):
        api.command("roll", roll, "бросить кубики: /roll 2d6")
        api.on("message.received", on_reply)
        api.action("translate", translate)      # the plugin's web part calls it: t.call("translate", {...})

Everything registered here is removed when the plugin is switched off. A plugin runs inside Tavern
and may import any of it (`tavern.services.cast`, ...); this object adds what has to be tracked
per plugin: event handlers, commands, actions, reply writers, model access, its own storage.
Model calls (`api.llm`) obey the plugin's access settings: allowed at all, its own limits, asking
the user first (see manager.py), and count as the plugin's on the «Расход» tab.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tavern.core.budget import RunBudget
from tavern.core.generation import Generator
from tavern.core.llm import LLMClient
from tavern.errors import Conflict, NotFound
from tavern.plugins.manifest import PluginError, secret_name, with_defaults

if TYPE_CHECKING:
    from tavern.context import AppContext
    from tavern.plugins.manager import PluginManager

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class PluginStorage:
    """The plugin's own data in one chat (or app-wide, chat_id ''). Values are anything JSON."""

    def __init__(self, ctx: AppContext, plugin_id: str, chat_id: str) -> None:
        self._repo = ctx.plugin_repo
        self._id = plugin_id
        self._chat = chat_id

    def get(self, key: str, default: Any = None) -> Any:
        return self._repo.data_get(self._id, self._chat, key, default)

    def set(self, key: str, value: Any) -> None:
        self._repo.data_set(self._id, self._chat, key, value)

    def delete(self, key: str) -> None:
        self._repo.data_delete(self._id, self._chat, key)

    def all(self) -> dict[str, Any]:
        return self._repo.data_all(self._id, self._chat)


class PluginAPI:
    def __init__(self, manager: PluginManager, manifest: dict[str, Any], folder: Path) -> None:
        self._manager = manager
        self.ctx: AppContext = manager.ctx
        self.id: str = manifest["id"]
        self.name: str = manifest["name"]
        self.folder = folder
        self.manifest = manifest
        self.log = logging.getLogger(f"tavern.plugin.{self.id}")
        self._unload: list[Callable[[], Any]] = []

    @property
    def settings(self) -> dict[str, Any]:
        row = self.ctx.plugin_repo.get(self.id) or {}
        return with_defaults(self.manifest["settings"], row.get("settings", {}))

    def secret(self, key: str) -> str:
        """A "secret" setting (a key, a password): "" when not set. Never pass it to the page or a log."""
        return str(self.ctx.secrets.get(secret_name(self.id, key), "") or "")

    def on(self, event: str, fn: Callable[..., Any], *, priority: int = 100) -> None:
        """Events of core/events.py: "message.received", "chat.members_changed", "prompt.built" ..."""
        self.ctx.events.on(event, fn, priority=priority, owner=self.id)

    def add_generator(self, generator: Generator, name: str = "") -> None:
        """A way of writing replies, chosen per chat in 👥 «Ответ пишет». Its model calls are this plugin's."""
        owner = self.ctx.generators.owner_of(generator.id)
        if generator.id in self.ctx.generators.ids and owner != self.id:
            raise PluginError(f"Способ ответа {generator.id!r} уже занят")
        self.ctx.generators.register(generator, owner=self.id, name=name or self.name)

    def command(self, name: str, fn: Callable[..., Any], description: str = "") -> None:
        """A chat command: "/name args". `fn(chat_id, args)` returns a short text for the user (or None)."""
        self._manager.add_command(self.id, self._name(name), fn, description)

    def action(self, name: str, fn: Callable[..., Any]) -> None:
        """Called by the plugin's web part: `t.call(name, payload)` -> `fn(payload)` -> JSON."""
        self._manager.actions[(self.id, self._name(name))] = fn

    def notify(self, name: str, **data: Any) -> None:
        """Tell this plugin's web part on every open page: `t.onNotice(name, fn)` gets `data`."""
        self.ctx.notices.emit("plugin", plugin=self.id, name=name, data=data)

    def on_unload(self, fn: Callable[[], Any]) -> None:
        self._unload.append(fn)

    def storage(self, chat_id: str = "") -> PluginStorage:
        return PluginStorage(self.ctx, self.id, chat_id)

    def job(self, key: str, work: Callable[[Any], Awaitable[None]], *, delay: float = 2.0) -> None:
        """A background job of this plugin (core/background.py): one per `key`, `work(job)` runs after `delay`
        seconds, each step through `await job.step(factory)` gives way to the user's requests. Scheduling the same
        key again only asks for one more pass. Stopped when the plugin is switched off."""
        self.ctx.jobs.schedule(self.id, key, work, delay=delay)

    def job_status(self, key: str) -> dict[str, Any]:
        """{running, queued, paused, done, total, note, error} of this plugin's job `key`."""
        return self.ctx.jobs.status(self.id, key)

    async def embed(self, texts: list[str], *, chat_id: str | None = None) -> list[list[float]]:
        """Vectors for `texts` on the global embedding connection (Settings → «Промпт»), one request. Counted as
        this plugin's spending and held to its day / month limits (not to the «may use models» permission: the
        connection is the user's own choice for vectors). Raises `no_embedding_connection` when none is chosen,
        ProviderError for a failing service (a 429 has `upstream_status` 429: the job's `rate_wait` sleeps)."""
        from tavern.services import embeddings

        self._manager.check_money(self.id)
        vectors, _ = await embeddings.embed(self.ctx, texts, purpose=f"plugin:{self.id}", chat_id=chat_id)
        return vectors

    def vectors_model(self) -> str:
        """The embedding model now chosen ('' when none): stored vectors are keyed by it."""
        from tavern.services import embeddings

        connection = embeddings.embedding_connection(self.ctx)
        return connection["model"] if connection else ""

    def llm(self, *, connection_id: str = "", model: str = "", chat_id: str | None = None,
            budget: RunBudget | None = None) -> LLMClient:
        """A model client on the given connection (default: the chat's, else the active one)."""
        connection = self._connection(connection_id, chat_id)
        from tavern.services import spending  # services import the context module

        return LLMClient(
            self.ctx.providers.create(connection), model=model or connection.get("model", ""),
            budget=budget or RunBudget(max_steps=0),
            meter=spending.meter(self.ctx, connection, chat_id=chat_id, purpose=f"plugin:{self.id}"),
            gate=self._manager.gate_for(self.id, chat_id=chat_id))

    def add_message(self, chat_id: str, text: str, *, role: str = "system", name: str = "",
                    hidden: bool = False, to_model: bool = True, extra: dict[str, Any] | None = None,
                    ) -> dict[str, Any]:
        """A message at the end of the chat. hidden: not shown in the chat; to_model=False: never
        sent to the model (a service note for the plugin itself)."""
        chat = self.ctx.chats.get_chat(chat_id)
        return self.ctx.chats.add_message(
            chat_id, role=role, content=text, parent_id=chat.get("leaf_id"), name=name or self.name,
            hidden=hidden, exclude_from_prompt=not to_model, owner=self.id, extra=extra)

    def _connection(self, connection_id: str, chat_id: str | None) -> dict[str, Any]:
        if not connection_id and chat_id:
            connection_id = self.ctx.chats.get_chat(chat_id)["meta"].get("connection_id") or ""
        connection_id = connection_id or self.ctx.settings.get("active_connection") or ""
        if not connection_id:
            raise Conflict("Нет подключения к модели. Добавьте его в настройках.", code="no_connection")
        try:
            return self.ctx.connections.get(connection_id)
        except NotFound:
            raise Conflict("Подключение не найдено", code="no_connection") from None

    @staticmethod
    def _name(name: str) -> str:
        if not NAME_RE.match(name):
            raise PluginError(f"Имя {name!r}: латинские буквы, цифры, «-» и «_», с буквы")
        return name
