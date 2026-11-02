"""Snapshots in data/backups: database-only snapshots and whole-data archives.

Database snapshots (`tavern-*.sqlite3`) are taken before migrations. Archives (`tavern-*.zip`,
stage 9) hold the database plus pictures, themes and plugins, never secrets.json: an archive can be
handed to someone. Both read the database through the SQLite online backup API, so they are
consistent even while the app writes. Restoring an archive into a running app is
services/backups.py; this module only writes, reads and checks files.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import zipfile
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


# -- whole-data archives (stage 9) --------------------------------------------------------------

ARCHIVE_FORMAT = 1
ARCHIVE_KINDS = ("start", "daily", "hourly", "risky", "manual", "upload")
ARCHIVE_DIRS = ("assets", "themes", "plugins")  # data folders an archive carries besides the database
ARCHIVE_DB = "tavern.sqlite3"
ARCHIVE_MANIFEST = "manifest.json"
_SKIP_DIRS = {".versions", "__pycache__"}  # undo history and caches: rebuilt, not worth the space
_ARCHIVE_RE = re.compile(
    r"^tavern-(\d{8}-\d{6}-\d{6})-(" + "|".join(ARCHIVE_KINDS) + r")(?:-([a-z0-9-]{1,40}))?\.zip$"
)
# Pictures and zips do not shrink; storing them as is keeps big libraries fast to copy.
_STORED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".zip", ".mp4", ".webm", ".woff2"}


@dataclass(frozen=True)
class ArchiveInfo:
    name: str
    path: Path
    size: int
    created_at: datetime
    kind: str
    reason: str


def archive_name(kind: str, reason: str = "") -> str:
    if kind not in ARCHIVE_KINDS:
        raise StorageError(f"Unknown backup kind: {kind!r}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    slug = _reason_slug(reason)
    return f"tavern-{stamp}-{kind}-{slug}.zip" if slug else f"tavern-{stamp}-{kind}.zip"


def parse_archive_name(name: str) -> tuple[datetime, str, str] | None:
    match = _ARCHIVE_RE.match(name)
    if match is None:
        return None
    stamp = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S-%f").replace(tzinfo=timezone.utc)
    return stamp, match.group(2), match.group(3) or ""


def list_archives(folder: Path) -> list[ArchiveInfo]:
    """Newest first. Unfinished (`.part`) and foreign files are skipped."""
    if not folder.is_dir():
        return []
    items = []
    for path in folder.iterdir():
        parsed = parse_archive_name(path.name)
        if parsed is None or not path.is_file():
            continue
        items.append(ArchiveInfo(path.name, path, path.stat().st_size, *parsed))
    items.sort(key=lambda a: a.name[7:29], reverse=True)
    return items


def snapshot_db(db_path: Path, target: Path) -> None:
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def data_files(root: Path) -> list[tuple[str, Path]]:
    """(name in the archive, file) for every carried data file, sorted."""
    out = []
    for top in ARCHIVE_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith((".new-", ".old-")))
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                out.append((path.relative_to(root).as_posix(), path))
    return out


def fingerprint(root: Path, db_snapshot: Path) -> str:
    """What the data looks like: equal fingerprints mean nothing worth a new copy changed.

    The database part hashes the snapshot without its 100-byte header (change counters move
    without any change of content); files count by name, size and modification time.
    """
    digest = hashlib.sha1()
    with db_snapshot.open("rb") as fh:
        fh.seek(100)
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    for rel, path in data_files(root):
        stat = path.stat()
        digest.update(f"\0{rel}\0{stat.st_size}\0{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def write_archive(root: Path, db_snapshot: Path, target: Path, manifest: dict) -> None:
    """Write the archive next to `target` as `.part`, then rename: no torn archive is ever listed."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr(ARCHIVE_MANIFEST, json.dumps({"format": ARCHIVE_FORMAT, **manifest}, ensure_ascii=False))
            zf.write(db_snapshot, ARCHIVE_DB)
            for rel, path in data_files(root):
                stored = path.suffix.lower() in _STORED_SUFFIXES
                zf.write(path, rel, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    log.info("Backup archive written: %s", target)


def read_archive_manifest(path: Path) -> dict:
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read(ARCHIVE_MANIFEST).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"{path.name}: это не копия Tavern или файл повреждён") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != ARCHIVE_FORMAT:
        raise StorageError(f"{path.name}: незнакомый формат копии")
    return manifest


def extract_archive(path: Path, into: Path) -> dict:
    """Unpack and check an archive into an empty folder. Returns its manifest.

    Refuses names that would land outside `into`, an archive without a database, and a database
    that fails SQLite's integrity check.
    """
    manifest = read_archive_manifest(path)
    into.mkdir(parents=True, exist_ok=True)
    base = into.resolve()
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise StorageError(f"{path.name}: повреждён файл {bad} внутри копии")
            for info in zf.infolist():
                name = info.filename
                if info.is_dir() or name == ARCHIVE_MANIFEST:
                    continue
                if name != ARCHIVE_DB and name.split("/", 1)[0] not in ARCHIVE_DIRS:
                    continue
                target = (into / name).resolve()
                if base not in target.parents:
                    raise StorageError(f"{path.name}: недопустимое имя внутри копии: {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
    except (zipfile.BadZipFile, OSError) as exc:
        raise StorageError(f"{path.name}: не читается ({exc})") from exc
    db = into / ARCHIVE_DB
    if not db.is_file():
        raise StorageError(f"{path.name}: в копии нет базы")
    check_db(db)
    return manifest


def check_db(db: Path) -> None:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise StorageError(f"База в копии повреждена: {exc}") from exc
    finally:
        conn.close()
    if result != "ok":
        raise StorageError(f"База в копии не прошла проверку: {result}")
