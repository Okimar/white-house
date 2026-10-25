"""SQLite access.

One connection per process, guarded by a lock. Calls are synchronous and fast; async code that
runs heavy queries should wrap them in `anyio.to_thread.run_sync`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tavern.errors import StorageError
from tavern.storage.migrations import Migration, load_migrations

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = path
        self._lock = threading.RLock()
        # isolation_level=None: no implicit transactions; `transaction()` issues BEGIN itself.
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._configure()

    def _configure(self) -> None:
        c = self._conn
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            mode = c.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if mode.lower() != "wal":
                # Some filesystems (Android shared storage, network drives) lack WAL support.
                log.warning("SQLite WAL unavailable (journal_mode=%s); using it as is", mode)
        c.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def fetchone(self, sql: str, params: Any = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Any = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any exception. Not reentrant."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    @property
    def schema_version(self) -> int:
        return int(self.fetchone("PRAGMA user_version")[0])

    def migrate(
        self,
        migrations: list[Migration] | None = None,
        *,
        before_upgrade: Callable[[int, int], None] | None = None,
    ) -> list[int]:
        """Apply pending migrations. Returns the versions applied.

        `before_upgrade(current, target)` runs once before the first migration on a database
        that already has a schema; the app uses it to take a backup.
        """
        migrations = load_migrations() if migrations is None else migrations
        latest = migrations[-1].version if migrations else 0
        current = self.schema_version
        if current > latest:
            raise StorageError(
                f"Database schema v{current} is newer than this app supports (v{latest}). "
                "Update Tavern or restore an older backup."
            )
        pending = [m for m in migrations if m.version > current]
        if not pending:
            return []
        if current > 0 and before_upgrade is not None:
            before_upgrade(current, latest)
        applied: list[int] = []
        for migration in pending:
            log.info("Applying migration %04d_%s", migration.version, migration.name)
            try:
                with self.transaction() as conn:
                    for statement in split_sql(migration.sql):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {migration.version:d}")
            except sqlite3.Error as exc:
                raise StorageError(
                    f"Migration {migration.version:04d}_{migration.name} failed and was rolled "
                    f"back: {exc}"
                ) from exc
            applied.append(migration.version)
        return applied


def split_sql(script: str) -> list[str]:
    """Split a script into complete statements (sqlite3.execute runs only one at a time)."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if not _is_only_comments(buffer):
        raise StorageError(f"Incomplete SQL statement at end of migration: {buffer.strip()[:80]}")
    return statements


def _is_only_comments(sql: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in sql.splitlines())
