"""Plugin-owned tables: a plugin's `migrations/NNNN_name.sql` files, applied before its `setup(api)` runs.

Rules (ADR 0019 section 2.2-B), the same as the core's `storage/migrations`: numbers contiguous from 0001, one
transaction per file together with the stored `plugins.schema_version`, a shipped file is never edited. In
addition, everything a file creates (tables, indexes, triggers, views) must be named `p_<id>_...` (`-` in the id
becomes `_`), and nothing else in the database may change. The check compares `sqlite_master` before and after
each file inside the transaction: a stray name, or a changed core object, rolls the file back and the plugin
stays off with the error shown. Tables may reference `chats(id)` / `messages(id)` with `ON DELETE CASCADE`, so
deleting a chat or a message cleans them up. «Удалить вместе с данными» drops every `p_<id>_*` object.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from tavern.plugins.manifest import PluginError
from tavern.storage.db import Database, split_sql
from tavern.storage.migrations import Migration, load_migrations

log = logging.getLogger(__name__)

FOLDER_RE = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_-]+)*$")


def prefix(plugin_id: str) -> str:
    return "p_" + plugin_id.replace("-", "_") + "_"


def folder_migrations(folder: Path, relative: str) -> list[Migration]:
    """The plugin's migration files in order. An empty list when the plugin has none."""
    if not relative:
        return []
    directory = folder / relative
    if not directory.is_dir():
        raise PluginError(f"plugin.json называет папку миграций {relative!r}, но её нет")
    try:
        return load_migrations(directory)
    except RuntimeError as exc:
        raise PluginError(f"Миграции дополнения: {exc}") from None


def latest(folder: Path, relative: str) -> int:
    try:
        found = folder_migrations(folder, relative)
    except PluginError:
        return 0
    return found[-1].version if found else 0


def _objects(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    return {r["name"]: (r["type"], r["sql"] or "")
            for r in conn.execute("SELECT type, name, sql FROM sqlite_master")}


def _own(name: str, own: str) -> bool:
    return name.startswith(own) or name.startswith("sqlite_autoindex_" + own)


def apply(db: Database, plugin_id: str, migrations: list[Migration], current: int) -> list[int]:
    """Run the files above `current` (one transaction each). Returns the versions applied."""
    own = prefix(plugin_id)
    done: list[int] = []
    if migrations and current > migrations[-1].version:
        raise PluginError(f"Данные дополнения новее его файлов (версия схемы {current}, в папке "
                          f"{migrations[-1].version}). Поставьте свежую версию дополнения.")
    for migration in migrations:
        if migration.version <= current:
            continue
        log.info("Applying %s migration %04d_%s", plugin_id, migration.version, migration.name)
        try:
            with db.transaction() as conn:
                before = _objects(conn)
                for statement in split_sql(migration.sql):
                    conn.execute(statement)
                after = _objects(conn)
                for name, (kind, sql) in after.items():
                    if _own(name, own):
                        continue
                    if name not in before:
                        raise PluginError(
                            f"Миграция {migration.version:04d}_{migration.name} создаёт {kind} «{name}»: "
                            f"имена должны начинаться с «{own}»")
                    if before[name] != (kind, sql):
                        raise PluginError(
                            f"Миграция {migration.version:04d}_{migration.name} меняет чужой объект «{name}»")
                for name in before:
                    if name not in after and not _own(name, own):
                        raise PluginError(
                            f"Миграция {migration.version:04d}_{migration.name} удаляет чужой объект «{name}»")
                conn.execute("UPDATE plugins SET schema_version = ? WHERE id = ?", (migration.version, plugin_id))
        except sqlite3.Error as exc:
            raise PluginError(f"Миграция {migration.version:04d}_{migration.name} не прошла и откатилась: "
                              f"{exc}") from exc
        done.append(migration.version)
    return done


def drop_all(db: Database, plugin_id: str) -> int:
    """Drop every `p_<id>_*` object (the plugin is deleted with its data). Returns how many tables went."""
    own = prefix(plugin_id)
    rows = [r for r in db.fetchall("SELECT type, name FROM sqlite_master") if r["name"].startswith(own)]
    for kind in ("trigger", "view", "index"):
        for row in rows:
            if row["type"] == kind:
                db.execute(f'DROP {kind.upper()} IF EXISTS "{row["name"]}"')
    tables = [r["name"] for r in rows if r["type"] == "table"]
    for _ in range(len(tables) + 1):  # a table other tables point at goes after them
        left = []
        for name in tables:
            try:
                db.execute(f'DROP TABLE IF EXISTS "{name}"')
            except sqlite3.Error:
                left.append(name)
        tables = left
        if not tables:
            break
    return len(rows)
