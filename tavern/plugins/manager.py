"""PluginManager: finding, approving, loading and unloading plugins; commands, actions, model access.

Where plugins live: built-in ones in tavern/builtin_plugins/<id>/ (on without questions), the
user's in data/plugins/<id>/. A new plugin is off. Turning on a plugin that is not built in needs
`approve=True` (the UI shows what it says it does and the full-access warning first); the hash of
its files is remembered, and different files later (an update, a hand edit) switch it off again
until approved. A crash in `setup()` leaves the plugin off with the error shown beside it; the
rest of Tavern runs on (at startup it is also switched off, so the next start does not try again).
Replacing a user plugin keeps the old folder in data/plugins/.versions/<id>/ (the last
VERSIONS_KEEP); `rollback()` puts one back, still approved if its files are the ones approved then. `--no-plugins` starts with no server parts at all; `/?safe` in the browser
also skips their web parts.

Model access (the owner's rules, docs/adr/0009-plugins.md), checked by `gate_for()` before every
request a plugin makes: allowed at all (off by default), its own limits (per day / month in the
shown currency, requests per reply), and optionally a question to the user each time.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from tavern.core.llm import GateFn
from tavern.errors import AppError, Conflict, NotFound
from tavern.plugins import install
from tavern.plugins.api import PluginAPI
from tavern.plugins.manifest import (
    MANIFEST, PluginError, check_settings, files_hash, read_manifest, read_zip, with_defaults,
)
from tavern.util.ids import new_id

if TYPE_CHECKING:
    from tavern.context import AppContext

log = logging.getLogger(__name__)

ASK_TIMEOUT = 120.0
VERSIONS_KEEP = 3
VERSIONS_DIR = ".versions"
ACCESS_DEFAULTS: dict[str, Any] = {"models": False, "ask": False, "day": 0.0, "month": 0.0, "per_reply": 0}


class PluginRefused(Conflict):
    """A plugin's model request was refused: no permission, its limit, or the user said no."""

    code = "plugin_refused"


class PluginFailed(AppError):
    code = "plugin_failed"
    status = 500


@dataclass
class Found:
    id: str
    folder: Path
    builtin: bool
    manifest: dict[str, Any] | None
    error: str = ""


@dataclass
class Loaded:
    api: PluginAPI
    module_name: str = ""


@dataclass
class Ask:
    info: dict[str, Any]
    future: asyncio.Future[bool] = field(repr=False)


def check_access(raw: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PluginError("Доступ дополнения: нужен объект")
    out = {**ACCESS_DEFAULTS, **current}
    for key, value in raw.items():
        if key not in ACCESS_DEFAULTS:
            raise PluginError(f"Нет такой настройки доступа: {key!r}")
        if key in ("models", "ask"):
            if not isinstance(value, bool):
                raise PluginError(f"{key}: да или нет")
            out[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise PluginError(f"{key}: число, 0 или больше")
            out[key] = int(value) if key == "per_reply" else float(value)
    return out


class PluginManager:
    def __init__(self, ctx: AppContext, builtin_dir: Path, user_dir: Path, *, load_server: bool = True) -> None:
        self.ctx = ctx
        self.builtin_dir = builtin_dir
        self.user_dir = user_dir
        self.load_server = load_server
        self.loaded: dict[str, Loaded] = {}
        self.errors: dict[str, str] = {}
        self.commands: dict[str, tuple[str, Callable[..., Any], str]] = {}  # name -> (plugin, fn, description)
        self.actions: dict[tuple[str, str], Callable[..., Any]] = {}
        self.asks: dict[str, Ask] = {}
        self.transport: httpx.AsyncBaseTransport | None = None  # tests swap the network out

    # -- finding -----------------------------------------------------------------------------

    def discover(self) -> dict[str, Found]:
        found: dict[str, Found] = {}
        for root, builtin in ((self.builtin_dir, True), (self.user_dir, False)):
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if not folder.is_dir() or folder.name.startswith(".") or folder.name in found:
                    continue  # a user folder never shadows a built-in one
                if not (folder / MANIFEST).is_file():
                    continue
                try:
                    found[folder.name] = Found(folder.name, folder, builtin, read_manifest(folder))
                except PluginError as exc:
                    found[folder.name] = Found(folder.name, folder, builtin, None, exc.message)
        return found

    def _get(self, plugin_id: str) -> Found:
        item = self.discover().get(plugin_id)
        if item is None:
            raise NotFound(f"Дополнение {plugin_id!r} не найдено")
        if item.manifest is None:
            raise PluginError(f"Дополнение {plugin_id!r} повреждено: {item.error}")
        return item

    def summary(self, item: Found, row: dict[str, Any] | None = None) -> dict[str, Any]:
        from tavern.services import spending  # services import the context module
        from tavern.storage.repos.spending import today

        row = row if row is not None else (self.ctx.plugin_repo.get(item.id) or {})
        manifest = item.manifest or {"id": item.id, "name": item.id, "settings": [], "uses": []}
        current = files_hash(item.folder)
        changed = bool(not item.builtin and row.get("approved_hash") and row["approved_hash"] != current)
        loaded = item.id in self.loaded
        enabled = bool(row.get("enabled")) and not changed
        server = bool(manifest.get("server"))
        purpose = f"plugin:{item.id}"
        day = today()
        return {
            **manifest,
            "builtin": item.builtin,
            "enabled": enabled,
            "active": enabled and item.manifest is not None and (loaded or not server),
            "error": item.error or self.errors.get(item.id, ""),
            "changed": changed,
            "server_off": server and not self.load_server,
            "hash": current[:16],
            "source": row.get("source") or {"kind": "builtin" if item.builtin else "folder"},
            "values": with_defaults(manifest.get("settings", []), row.get("settings", {})),
            "access": {**ACCESS_DEFAULTS, **row.get("access", {})},
            "commands": sorted(n for n, (owner, _, _) in self.commands.items() if owner == item.id),
            "data_count": self.ctx.plugin_repo.data_count(item.id),
            "versions": 0 if item.builtin else len(self._version_ids(item.id)),
            "spent": {"day": self.ctx.spending.purpose(purpose, day=day),
                      "month": self.ctx.spending.purpose(purpose, month=day[:7])},
            "currency_rate": spending.rate(spending.get_settings(self.ctx)),
        }

    def list(self) -> list[dict[str, Any]]:
        rows = self.ctx.plugin_repo.all()
        return [self.summary(item, rows.get(item.id, {})) for item in self.discover().values()]

    def get(self, plugin_id: str) -> dict[str, Any]:
        item = self.discover().get(plugin_id)
        if item is None:
            raise NotFound(f"Дополнение {plugin_id!r} не найдено")
        return self.summary(item)

    def web_file(self, plugin_id: str, rel: str) -> Path:
        """A file of an active plugin's web part, for /plugins/{id}/{path}."""
        from tavern.plugins.manifest import WEB_EXTENSIONS, safe_relpath

        item = self.discover().get(plugin_id)
        if item is None or item.manifest is None or not self.get(plugin_id)["active"]:
            raise NotFound("Нет такого файла")
        rel = safe_relpath(rel)
        path = item.folder / rel
        if path.suffix.lower() not in WEB_EXTENSIONS or not path.is_file():
            raise NotFound("Нет такого файла")
        return path

    # -- on / off ----------------------------------------------------------------------------

    def start(self) -> None:
        """At startup: load every plugin that is on and whose files are the approved ones."""
        rows = self.ctx.plugin_repo.all()
        for item in self.discover().values():
            row = rows.get(item.id)
            if not row or not row["enabled"] or item.manifest is None:
                continue
            if not item.builtin and row["approved_hash"] != files_hash(item.folder):
                self.ctx.plugin_repo.save(item.id, enabled=False)
                self.errors[item.id] = "Файлы дополнения изменились после включения. Включите снова, чтобы подтвердить."
                continue
            if not self._load(item):
                self.ctx.plugin_repo.save(item.id, enabled=False)
                self.errors[item.id] = "Выключено само: " + self.errors.get(item.id, "упало при запуске")
                self.ctx.notices.emit("plugins")

    def enable(self, plugin_id: str, *, approve: bool = False) -> dict[str, Any]:
        item = self._get(plugin_id)
        if not item.builtin and not approve:
            raise Conflict("Дополнение получит полный доступ к Tavern. Нужно подтверждение.",
                           code="needs_approval", details=self.summary(item))
        self._unload(plugin_id)
        self.errors.pop(plugin_id, None)
        self.ctx.plugin_repo.save(plugin_id, enabled=True,
                                  approved_hash="" if item.builtin else files_hash(item.folder))
        if not self._load(item):
            self.ctx.plugin_repo.save(plugin_id, enabled=False)
        self.ctx.notices.emit("plugins")
        return self.get(plugin_id)

    def disable(self, plugin_id: str) -> dict[str, Any]:
        self._get(plugin_id)
        self._unload(plugin_id)
        self.errors.pop(plugin_id, None)
        self.ctx.plugin_repo.save(plugin_id, enabled=False)
        self.ctx.notices.emit("plugins")
        return self.get(plugin_id)

    def stop(self) -> None:
        for plugin_id in list(self.loaded):
            self._unload(plugin_id)

    def _load(self, item: Found) -> bool:
        assert item.manifest is not None
        if not self.load_server or not item.manifest["server"]:
            return True
        api = PluginAPI(self, item.manifest, item.folder)
        loaded = Loaded(api, f"tavern_plugin_{item.id.replace('-', '_')}")
        self.loaded[item.id] = loaded
        try:
            module = self._import(loaded.module_name, item.folder / item.manifest["server"])
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise PluginError(f"В {item.manifest['server']} нет функции setup(api)")
            result = setup(api)
            if inspect.isawaitable(result):
                getattr(result, "close", lambda: None)()
                raise PluginError("setup(api) должна быть обычной функцией (не async)")
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not take Tavern down
            log.exception("Plugin %s failed to start", item.id)
            self._unload(item.id)
            message = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
            self.errors[item.id] = f"Не запустилось: {message}"
            return False
        log.info("Plugin %s %s started", item.id, item.manifest["version"])
        return True

    @staticmethod
    def _import(name: str, path: Path) -> Any:
        # The server file acts as a package, so a plugin can split its code: `from .rules import x`.
        spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)])
        if spec is None or spec.loader is None:
            raise PluginError(f"Не загружается {path.name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def _unload(self, plugin_id: str) -> None:
        loaded = self.loaded.pop(plugin_id, None)
        self.ctx.events.off(plugin_id)
        self.ctx.generators.unregister_owner(plugin_id)
        for name in [n for n, (owner, _, _) in self.commands.items() if owner == plugin_id]:
            del self.commands[name]
        for key in [k for k in self.actions if k[0] == plugin_id]:
            del self.actions[key]
        if loaded is None:
            return
        for fn in reversed(loaded.api._unload):
            try:
                fn()
            except Exception:  # noqa: BLE001 - unloading goes on regardless
                log.exception("Plugin %s failed while unloading", plugin_id)
        for name in [n for n in sys.modules if n == loaded.module_name or n.startswith(loaded.module_name + ".")]:
            del sys.modules[name]

    # -- install / update / delete -------------------------------------------------------------

    def install_zip(self, blob: bytes, *, replace: bool = False, source: dict[str, Any] | None = None,
                    subdir: str = "") -> dict[str, Any]:
        manifest, files = read_zip(blob, subdir=subdir)
        plugin_id = manifest["id"]
        if (self.builtin_dir / plugin_id).is_dir():
            raise Conflict(f"«{plugin_id}» — имя встроенного дополнения", code="plugin_builtin")
        existing = self.discover().get(plugin_id)
        if existing is not None and not replace:
            old = existing.manifest or {}
            raise Conflict(f"Дополнение «{old.get('name', plugin_id)}» уже установлено. Заменить?",
                           code="plugin_exists", details={"id": plugin_id, "version": old.get("version", ""),
                                                          "new_version": manifest["version"]})
        if existing is not None:
            self.ctx.backups.before("plugin-update")
            self._save_version(existing)
        self._unload(plugin_id)
        self.errors.pop(plugin_id, None)
        install.write_folder(self.user_dir, plugin_id, files)
        self.ctx.plugin_repo.save(plugin_id, enabled=False, approved_hash="",
                                  source=source or {"kind": "file"})
        self.ctx.notices.emit("plugins")
        return self.get(plugin_id)

    # -- versions ------------------------------------------------------------------------------

    def _versions_root(self, plugin_id: str) -> Path:
        return self.user_dir / VERSIONS_DIR / plugin_id

    def _save_version(self, item: Found) -> None:
        """Keep the plugin's current folder and its approval before it is replaced."""
        if item.builtin:
            return
        row = self.ctx.plugin_repo.get(item.id) or {}
        root = self._versions_root(item.id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        shutil.copytree(item.folder, root / stamp, ignore=shutil.ignore_patterns("__pycache__"))
        meta = {"version": (item.manifest or {}).get("version", ""), "saved_at": datetime.now(timezone.utc).isoformat(),
                "approved_hash": row.get("approved_hash", ""), "enabled": bool(row.get("enabled")),
                "source": row.get("source") or {"kind": "folder"}}
        (root / f"{stamp}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        for old in self._version_ids(item.id)[VERSIONS_KEEP:]:
            shutil.rmtree(root / old, ignore_errors=True)
            (root / f"{old}.json").unlink(missing_ok=True)

    def _version_ids(self, plugin_id: str) -> list[str]:
        root = self._versions_root(plugin_id)
        if not root.is_dir():
            return []
        return sorted((p.name for p in root.iterdir() if p.is_dir() and (root / f"{p.name}.json").is_file()),
                      reverse=True)

    def versions(self, plugin_id: str) -> list[dict[str, Any]]:
        """Earlier versions of a user plugin, newest first."""
        root = self._versions_root(plugin_id)
        out = []
        for stamp in self._version_ids(plugin_id):
            meta = json.loads((root / f"{stamp}.json").read_text(encoding="utf-8"))
            out.append({"id": stamp, "version": meta.get("version", ""), "saved_at": meta.get("saved_at", ""),
                        "approved": bool(meta.get("approved_hash"))})
        return out

    def rollback(self, plugin_id: str, version_id: str = "") -> dict[str, Any]:
        """Put an earlier version back (the newest one by default). The current one is kept too."""
        item = self.discover().get(plugin_id)
        if item is None:
            raise NotFound(f"Дополнение {plugin_id!r} не найдено")
        ids = self._version_ids(plugin_id)
        if not ids:
            raise Conflict("Прошлых версий нет", code="no_versions")
        version_id = version_id or ids[0]
        if version_id not in ids:
            raise NotFound("Нет такой версии")
        root = self._versions_root(plugin_id)
        meta = json.loads((root / f"{version_id}.json").read_text(encoding="utf-8"))
        files = {p.relative_to(root / version_id).as_posix(): p.read_bytes()
                 for p in (root / version_id).rglob("*") if p.is_file()}
        self.ctx.backups.before("plugin-rollback")
        self._save_version(item)
        shutil.rmtree(root / version_id, ignore_errors=True)
        (root / f"{version_id}.json").unlink(missing_ok=True)
        self._unload(plugin_id)
        self.errors.pop(plugin_id, None)
        folder = install.write_folder(self.user_dir, plugin_id, files)
        approved = meta.get("approved_hash", "")
        still_approved = bool(approved) and approved == files_hash(folder)
        self.ctx.plugin_repo.save(plugin_id, approved_hash=approved if still_approved else "",
                                  enabled=bool(meta.get("enabled")) and still_approved,
                                  source=meta.get("source") or {"kind": "folder"})
        if meta.get("enabled") and still_approved:
            restored = self.discover()[plugin_id]
            if restored.manifest is None or not self._load(restored):
                self.ctx.plugin_repo.save(plugin_id, enabled=False)
        self.ctx.notices.emit("plugins")
        return self.get(plugin_id)

    async def install_github(self, url: str, *, replace: bool = False) -> dict[str, Any]:
        link = install.parse_github(url)
        found = await install.latest(link, self.transport)
        blob = await install.download(link, found["sha"], self.transport)
        return self.install_zip(blob, replace=replace, source=install.source_of(url, link, found),
                                subdir=link["subdir"])

    async def check_update(self, plugin_id: str) -> dict[str, Any]:
        source = self.get(plugin_id)["source"]
        if source.get("kind") != "github":
            raise Conflict("Обновления проверяются только у дополнений, поставленных по ссылке GitHub",
                           code="not_github")
        link = install.parse_github(source["url"])
        link["ref"] = source.get("ref") or link["ref"]
        found = await install.latest(link, self.transport)
        return {"current": source.get("sha", ""), "latest": found["sha"], "update": found["sha"] != source.get("sha")}

    async def update(self, plugin_id: str) -> dict[str, Any]:
        source = self.get(plugin_id)["source"]
        if source.get("kind") != "github":
            raise Conflict("Обновить можно только дополнение, поставленное по ссылке GitHub", code="not_github")
        link = install.parse_github(source["url"])
        link["ref"] = source.get("ref") or link["ref"]
        found = await install.latest(link, self.transport)
        blob = await install.download(link, found["sha"], self.transport)
        new_source = {**source, "sha": found["sha"], "ref": found["ref"]}
        return self.install_zip(blob, replace=True, source=new_source, subdir=link["subdir"])

    def delete(self, plugin_id: str, *, data: bool) -> None:
        item = self.discover().get(plugin_id)
        if item is None:
            raise NotFound(f"Дополнение {plugin_id!r} не найдено")
        if item.builtin:
            raise Conflict("Встроенное дополнение не удаляется, его можно только выключить", code="plugin_builtin")
        self._unload(plugin_id)
        self.errors.pop(plugin_id, None)
        shutil.rmtree(item.folder)
        shutil.rmtree(self._versions_root(plugin_id), ignore_errors=True)
        self.ctx.plugin_repo.delete(plugin_id, data=data)
        self.ctx.notices.emit("plugins")

    # -- settings ----------------------------------------------------------------------------

    def save_settings(self, plugin_id: str, raw: Any) -> dict[str, Any]:
        item = self._get(plugin_id)
        row = self.ctx.plugin_repo.get(plugin_id) or {}
        assert item.manifest is not None
        values = check_settings(item.manifest["settings"], raw, row.get("settings", {}))
        self.ctx.plugin_repo.save(plugin_id, settings=values)
        return self.get(plugin_id)

    def save_access(self, plugin_id: str, raw: Any) -> dict[str, Any]:
        self._get(plugin_id)
        row = self.ctx.plugin_repo.get(plugin_id) or {}
        self.ctx.plugin_repo.save(plugin_id, access=check_access(raw, row.get("access", {})))
        return self.get(plugin_id)

    # -- model access --------------------------------------------------------------------------

    def gate_for(self, plugin_id: str, *, chat_id: str | None = None) -> GateFn:
        """Checked before each model request of one client (one reply, one `api.llm()` client)."""
        made = 0

        async def gate() -> None:
            nonlocal made
            from tavern.services import spending
            from tavern.storage.repos.spending import today

            row = self.ctx.plugin_repo.get(plugin_id) or {}
            access = {**ACCESS_DEFAULTS, **row.get("access", {})}
            item = self.discover().get(plugin_id)
            name = (item.manifest or {}).get("name", plugin_id) if item else plugin_id
            if not access["models"]:
                raise PluginRefused(f"Дополнению «{name}» не разрешено обращаться к моделям. "
                                    "Разрешите в настройках → «Дополнения».", code="plugin_no_models")
            if access["per_reply"] and made >= access["per_reply"]:
                raise PluginRefused(f"Дополнение «{name}»: предел {access['per_reply']} запросов на один ответ",
                                    code="plugin_limit")
            settings = spending.get_settings(self.ctx)
            rate = spending.rate(settings)
            day = today()
            for period, label in (("day", "день"), ("month", "месяц")):
                limit = access[period]
                if not limit:
                    continue
                spent = self.ctx.spending.purpose(f"plugin:{plugin_id}", **({"day": day} if period == "day"
                                                                             else {"month": day[:7]}))
                if spent["cost"] * rate >= limit:
                    raise PluginRefused(f"Дополнение «{name}»: лимит расхода за {label} достигнут "
                                        f"({spent['cost'] * rate:.2f} из {limit:g} "
                                        f"{settings['currency']['code'] or '$'})", code="plugin_limit")
            if access["ask"] and not await self.ask(plugin_id, name, chat_id):
                raise PluginRefused(f"Вы не разрешили запрос дополнения «{name}»", code="plugin_denied")
            made += 1

        return gate

    async def ask(self, plugin_id: str, name: str, chat_id: str | None) -> bool:
        """Ask the open page(s) whether the plugin may make one model request. No page = no."""
        if not self.ctx.notices.listeners:
            return False
        ask_id = new_id()
        info = {"id": ask_id, "plugin": plugin_id, "name": name, "chat_id": chat_id}
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.asks[ask_id] = Ask(info, future)
        self.ctx.notices.emit("plugin_ask", ask=info)
        try:
            return await asyncio.wait_for(future, ASK_TIMEOUT)
        except asyncio.TimeoutError:
            return False
        finally:
            self.asks.pop(ask_id, None)
            self.ctx.notices.emit("plugin_asked", id=ask_id)

    def answer(self, ask_id: str, allow: bool) -> None:
        ask = self.asks.get(ask_id)
        if ask is None:
            raise NotFound("Этот вопрос уже закрыт")
        if not ask.future.done():
            ask.future.set_result(allow)

    # -- commands and actions -------------------------------------------------------------------

    def add_command(self, plugin_id: str, name: str, fn: Callable[..., Any], description: str) -> None:
        owner = self.commands.get(name, (plugin_id,))[0]
        if owner != plugin_id:
            raise PluginError(f"Команда /{name} уже есть у дополнения {owner!r}")
        self.commands[name] = (plugin_id, fn, description[:200])

    def command_list(self) -> list[dict[str, str]]:
        return [{"name": n, "plugin": p, "description": d} for n, (p, _, d) in sorted(self.commands.items())]

    async def run_command(self, chat_id: str, text: str) -> dict[str, Any]:
        """"/roll 2d6" -> the command's answer. Not a known command -> {"handled": False}."""
        if not text.startswith("/"):
            return {"handled": False}
        name, _, args = text[1:].partition(" ")
        entry = self.commands.get(name.strip().lower())
        if entry is None:
            return {"handled": False}
        plugin_id, fn, _ = entry
        self.ctx.chats.get_chat(chat_id)  # 404 for a wrong chat before the plugin sees it
        result = await self._call(plugin_id, f"/{name}", fn, chat_id, args.strip())
        return {"handled": True, "text": result if isinstance(result, str) else ""}

    async def call_action(self, plugin_id: str, name: str, payload: dict[str, Any]) -> Any:
        fn = self.actions.get((plugin_id, name))
        if fn is None:
            raise NotFound(f"У дополнения {plugin_id!r} нет действия {name!r} (оно включено?)")
        return await self._call(plugin_id, name, fn, payload)

    async def _call(self, plugin_id: str, what: str, fn: Callable[..., Any], *args: Any) -> Any:
        try:
            result = fn(*args)
            if inspect.isawaitable(result):
                result = await result
            return result
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - shown to the user, the plugin stays on
            log.exception("Plugin %s failed in %s", plugin_id, what)
            raise PluginFailed(f"Дополнение «{plugin_id}» ошиблось в {what}: {type(exc).__name__}: {exc}") from None
