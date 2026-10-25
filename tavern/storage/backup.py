"""Database snapshots in data/backups.

Snapshots use the SQLite online backup API, so they are consistent even while the app writes.
Restoring requires the server to be stopped (the CLI holds the data-dir lock).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tavern.errors import StorageError

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^tavern-\d{8}-\d{6}-\d{6}(?:-[a-z0-9-]{1,40})?\.sqlite3$")


@dataclass(frozen=True)
class BackupInfo:
    name: str
    path: Path
    size: int
    created_at: datetime


def _reason_slug(reason: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-")
    return slug[:40]


def create_backup(db_path: Path, backups_dir: Path, reason: str = "manual") -> Path:
    if not db_path.exists():
        raise StorageError(f"No database to back up at {db_path}")
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    slug = _reason_slug(reason)
    target = backups_dir / (f"tavern-{stamp}-{slug}.sqlite3" if slug else f"tavern-{stamp}.sqlite3")
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    log.info("Backup created: %s", target.name)
    return target


def list_backups(backups_dir: Path) -> list[BackupInfo]:
    """Newest first."""
    if not backups_dir.exists():
        return []
    items = []
    for path in backups_dir.iterdir():
        if not _NAME_RE.match(path.name):
            continue
        stat = path.stat()
        stamp = datetime.strptime(path.name[7:29], "%Y%m%d-%H%M%S-%f").replace(tzinfo=timezone.utc)
        items.append(BackupInfo(path.name, path, stat.st_size, stamp))
    items.sort(key=lambda b: b.name, reverse=True)
    return items


def prune_backups(backups_dir: Path, keep: int) -> list[str]:
    """Delete the oldest snapshots beyond `keep`. Returns removed names."""
    removed = []
    for info in list_backups(backups_dir)[keep:]:
        info.path.unlink(missing_ok=True)
        removed.append(info.name)
    return removed


def resolve_backup(backups_dir: Path, name: str) -> Path:
    """Map a user-supplied backup name to a path, rejecting anything that is not a snapshot."""
    if not _NAME_RE.match(name):
        raise StorageError(f"Not a backup name: {name!r}")
    path = backups_dir / name
    if not path.is_file():
        raise StorageError(f"Backup not found: {name}")
    return path


def restore_backup(db_path: Path, backups_dir: Path, name: str) -> Path:
    """Replace the database with a snapshot. The current state is snapshotted first.

    Returns the path of the safety snapshot taken before restoring.
    """
    source = resolve_backup(backups_dir, name)
    check = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise StorageError(f"Backup {name} is not a valid database: {exc}") from exc
    finally:
        check.close()
    if result != "ok":
        raise StorageError(f"Backup {name} failed integrity check: {result}")

    safety = create_backup(db_path, backups_dir, "pre-restore") if db_path.exists() else None
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(db_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    log.info("Restored database from %s", name)
    return safety
