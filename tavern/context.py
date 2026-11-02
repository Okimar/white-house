"""AppContext: the services one running instance owns. Built once at startup, passed explicitly.

Routes reach it through `request.app.state.ctx`. Tests build it with `open_context(tmp_path)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from tavern.config import Config, load_config, with_server_overrides
from tavern.core.events import EventBus
from tavern.core.generation import GeneratorRegistry
from tavern.core.notices import Notices
from tavern.core.runs import RunManager
from tavern.paths import BUILTIN_PLUGINS_DIR, BUILTIN_THEMES_DIR, DataPaths
from tavern.plugins.manager import PluginManager
from tavern.providers.registry import ProviderRegistry
from tavern.secret_store import SecretStore
from tavern.services.backups import BackupService
from tavern.services.gdrive import DriveService
from tavern.storage.assets import AssetStore
from tavern.storage.backup import create_backup, prune_backups
from tavern.storage.db import Database
from tavern.storage.repos.characters import CharacterRepo
from tavern.storage.repos.chats import ChatRepo
from tavern.storage.repos.connections import ConnectionRepo
from tavern.storage.repos.skills import SkillRepo
from tavern.storage.repos.templates import TemplateRepo
from tavern.storage.prompt_log import PromptLog
from tavern.storage.repos.assistant import AssistantRepo
from tavern.storage.repos.blocks import BlockRepo
from tavern.storage.repos.members import MemberRepo
from tavern.storage.repos.notes import NoteRepo
from tavern.storage.repos.history import HistoryRepo
from tavern.storage.repos.lorebooks import LorebookRepo
from tavern.storage.repos.plugins import PluginRepo
from tavern.storage.repos.presets import PresetRepo
from tavern.storage.repos.settings import SettingsRepo
from tavern.storage.repos.spending import SpendingRepo
from tavern.storage.themes import ThemeStore

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    paths: DataPaths
    config: Config
    db: Database
    secrets: SecretStore
    events: EventBus
    providers: ProviderRegistry
    generators: GeneratorRegistry
    runs: RunManager
    characters: CharacterRepo
    chats: ChatRepo
    connections: ConnectionRepo
    templates: TemplateRepo
    skills: SkillRepo
    settings: SettingsRepo
    themes: ThemeStore
    assets: AssetStore
    members: MemberRepo
    blocks: BlockRepo
    presets: PresetRepo
    prompt_log: PromptLog
    notes: NoteRepo
    history: HistoryRepo
    lorebooks: LorebookRepo
    spending: SpendingRepo
    plugin_repo: PluginRepo
    assistant_repo: AssistantRepo
    notices: Notices
    plugins: PluginManager = None  # type: ignore[assignment]  # set right after construction
    backups: BackupService = None  # type: ignore[assignment]  # set right after construction
    gdrive: DriveService = None  # type: ignore[assignment]  # set right after construction
    sandbox: bool = False

    def close(self) -> None:
        if self.backups is not None:
            self.backups.stop_scheduler()
        if self.gdrive is not None:
            self.gdrive.stop()
        if self.plugins is not None:
            self.plugins.stop()
        self.db.close()


def open_context(
    paths: DataPaths,
    *,
    server_overrides: dict[str, Any] | None = None,
    sandbox: bool = False,
    load_plugins: bool = True,
) -> AppContext:
    """Create the data dir if needed, load config, open and migrate the database.

    An existing database is snapshotted before any migration runs. Plugins are found but not
    started: the caller runs `ctx.plugins.start()` (the server does; tests do when they need it).
    `load_plugins=False` (`--no-plugins`) never runs their server parts.
    """
    paths.ensure()
    config = load_config(paths.config)
    if server_overrides:
        config = with_server_overrides(config, **server_overrides)
    secrets = SecretStore(paths.secrets)
    db = Database(paths.db)

    def backup_before_migration(current: int, target: int) -> None:
        create_backup(paths.db, paths.backups, f"pre-migrate-v{current}-to-v{target}")
        prune_backups(paths.backups, config.backups.keep)

    try:
        db.migrate(before_upgrade=backup_before_migration)
    except BaseException:
        db.close()
        raise
    ctx = AppContext(
        paths=paths,
        config=config,
        db=db,
        secrets=secrets,
        events=EventBus(),
        providers=ProviderRegistry(secrets),
        generators=GeneratorRegistry(),
        runs=RunManager(),
        characters=CharacterRepo(db),
        chats=ChatRepo(db),
        connections=ConnectionRepo(db),
        templates=TemplateRepo(db),
        skills=SkillRepo(db),
        settings=SettingsRepo(db),
        themes=ThemeStore(BUILTIN_THEMES_DIR, paths.themes),
        assets=AssetStore(paths.assets),
        members=MemberRepo(db),
        blocks=BlockRepo(db),
        presets=PresetRepo(db),
        prompt_log=PromptLog(db),
        notes=NoteRepo(db),
        history=HistoryRepo(db),
        lorebooks=LorebookRepo(db),
        spending=SpendingRepo(db),
        plugin_repo=PluginRepo(db),
        assistant_repo=AssistantRepo(db),
        notices=Notices(),
        sandbox=sandbox,
    )
    ctx.plugins = PluginManager(ctx, BUILTIN_PLUGINS_DIR, paths.plugins, load_server=load_plugins)
    ctx.backups = BackupService(ctx)
    ctx.gdrive = DriveService(ctx)

    def resolve_template(template_id: str, model: str) -> dict:
        from tavern.services.templates import resolve  # services import the context module

        return resolve(ctx, template_id, model)

    ctx.providers.template_resolver = resolve_template
    ctx.providers.connection_lookup = ctx.connections.get
    return ctx
