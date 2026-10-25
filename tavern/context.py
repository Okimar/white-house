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
from tavern.core.runs import RunManager
from tavern.paths import DataPaths
from tavern.providers.registry import ProviderRegistry
from tavern.secret_store import SecretStore
from tavern.storage.backup import create_backup, prune_backups
from tavern.storage.db import Database
from tavern.storage.repos.characters import CharacterRepo
from tavern.storage.repos.chats import ChatRepo
from tavern.storage.repos.connections import ConnectionRepo
from tavern.storage.repos.settings import SettingsRepo

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
    settings: SettingsRepo
    sandbox: bool = False

    def close(self) -> None:
        self.db.close()


def open_context(
    paths: DataPaths,
    *,
    server_overrides: dict[str, Any] | None = None,
    sandbox: bool = False,
) -> AppContext:
    """Create the data dir if needed, load config, open and migrate the database.

    An existing database is snapshotted before any migration runs.
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
    return AppContext(
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
        settings=SettingsRepo(db),
        sandbox=sandbox,
    )
