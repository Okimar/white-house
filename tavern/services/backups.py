"""Backups of the whole data folder (stage 9): when they are taken, how many are kept, where they go,
and putting one back into the running app.

What a copy holds and how it is written is storage/backup.py. Here:
- settings (`settings` key "backups"): which triggers are on (start, daily at an hour, every N
  hours, before risky actions), how many copies of each kind to keep, an own folder;
- `create()`: a copy unless nothing changed since the last one (same fingerprint); manual and
  uploaded copies are never pruned;
- `before(reason)`: the copy before a risky action (delete, import, plugin update, doctor fix);
  a failure is logged and shown, the action goes on;
- a scheduler thread for the timed kinds;
- `restore()`: without restarting. The current state is copied first; any failure puts
  everything back as it was. The backup settings themselves survive a restore (they describe
  this machine, not the data).

Copies on Google Drive (stage 9б) are services/gdrive.py: every new copy wakes its sender.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tavern import __version__
from tavern.errors import AppError, Conflict, NotFound, StorageError
from tavern.storage.backup import (
    ARCHIVE_DB, ARCHIVE_DIRS, ArchiveInfo, archive_name, check_db, extract_archive, fingerprint,
    list_archives, list_backups, parse_archive_name, read_archive_manifest, snapshot_db, write_archive,
)

if TYPE_CHECKING:
    from tavern.context import AppContext

log = logging.getLogger(__name__)

SETTINGS_KEY = "backups"
DEFAULTS: dict[str, Any] = {
    "on_start": True,
    "daily": True,
    "daily_hour": 4,
    "hourly": True,
    "every_hours": 6,
    "risky": True,
    "keep": {"start": 5, "daily": 7, "hourly": 6, "risky": 10},
    "dir": "",
}
TICK_SECONDS = 60.0
VERSIONS_DIR = ".versions"  # past versions of plugins and themes (storage/backup.py leaves them out of copies)
TMP_DIR = ".backup-tmp"
RESTORE_NEW = "new"
RESTORE_OLD = "old"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024


class BackupError(AppError):
    """A copy could not be made, read or put back (message shown to the user)."""

    code = "backup_failed"


def check_settings(raw: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BackupError("Настройки копий: нужен объект")
    out = {**current, "keep": dict(current["keep"])}
    for key, value in raw.items():
        if key not in DEFAULTS:
            raise BackupError(f"Нет такой настройки копий: {key!r}")
        if key in ("on_start", "daily", "hourly", "risky"):
            if not isinstance(value, bool):
                raise BackupError(f"{key}: да или нет")
            out[key] = value
        elif key == "daily_hour":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
                raise BackupError("Час ежедневной копии: от 0 до 23")
            out[key] = value
        elif key == "every_hours":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 168:
                raise BackupError("Копия каждые N часов: от 1 до 168")
            out[key] = value
        elif key == "keep":
            if not isinstance(value, dict):
                raise BackupError("Сколько хранить: нужен объект")
            for kind, count in value.items():
                if kind not in DEFAULTS["keep"]:
                    raise BackupError(f"Нет такого вида копий: {kind!r}")
                if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
                    raise BackupError("Сколько хранить: от 1 до 1000")
                out["keep"][kind] = count
        elif key == "dir":
            if not isinstance(value, str) or len(value) > 1000:
                raise BackupError("Папка для копий: строка")
            value = value.strip()
            if value and not Path(value).expanduser().is_absolute():
                raise BackupError("Папка для копий: нужен полный путь (например, D:\\Google Drive\\Tavern)")
            out[key] = value
    return out


class BackupService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._lock = threading.RLock()  # one copy or restore at a time
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_fp: str | None = None
        self._tried: dict[str, float] = {}  # kind -> monotonic time of the last attempt
        self._daily_done = ""  # local date of the last daily attempt
        self.last_ok = ""
        self.last_error = ""
        self.warnings: list[dict[str, Any]] = []  # quick doctor findings at startup

    # -- settings and folders ----------------------------------------------------------------

    def settings(self) -> dict[str, Any]:
        saved = self.ctx.settings.get(SETTINGS_KEY) or {}
        return {**DEFAULTS, **saved, "keep": {**DEFAULTS["keep"], **saved.get("keep", {})}}

    def save_settings(self, raw: Any) -> dict[str, Any]:
        value = check_settings(raw, self.settings())
        self.ctx.settings.set(SETTINGS_KEY, value)
        self.ctx.notices.emit("backups")
        return value

    def own_dir(self) -> Path | None:
        if self.ctx.sandbox:
            return None  # a sandbox copy must never write into the real own folder
        configured = self.settings()["dir"]
        return Path(configured).expanduser() if configured else None

    def own_dir_problem(self) -> str:
        own = self.own_dir()
        if own is None:
            return ""
        try:
            own.mkdir(parents=True, exist_ok=True)
            probe = own / ".tavern-write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            return f"Своя папка для копий недоступна ({own}): {exc.strerror or exc}"
        return ""

    def target_dir(self) -> tuple[Path, str]:
        """Where the next copy goes, and a warning when the own folder had to be skipped."""
        problem = self.own_dir_problem()
        own = self.own_dir()
        if own is not None and not problem:
            return own, ""
        return self.ctx.paths.backups, problem

    def folders(self) -> list[Path]:
        out = [self.ctx.paths.backups]
        own = self.own_dir()
        if own is not None and own.is_dir() and own.resolve() != self.ctx.paths.backups.resolve():
            out.append(own)
        return out

    # -- listing -------------------------------------------------------------------------------

    def archives(self) -> list[ArchiveInfo]:
        items = [a for folder in self.folders() for a in list_archives(folder)]
        items.sort(key=lambda a: a.name[7:29], reverse=True)
        return items

    def list(self) -> list[dict[str, Any]]:
        own = self.own_dir()
        on_drive = set(self.ctx.gdrive.public()["uploaded"]) if self.ctx.gdrive is not None else set()
        out = [{
            "name": a.name, "size": a.size, "created_at": a.created_at.isoformat(), "kind": a.kind,
            "reason": a.reason, "where": "own" if own is not None and a.path.parent == own else "data",
            "drive": a.name in on_drive,
        } for a in self.archives()]
        # Database-only snapshots taken before a schema upgrade can be put back too.
        out += [{"name": b.name, "size": b.size, "created_at": b.created_at.isoformat(), "kind": "migrate",
                 "reason": b.name[30:-8], "where": "data", "drive": False} for b in list_backups(self.ctx.paths.backups)]
        out.sort(key=lambda b: b["created_at"], reverse=True)
        return out

    def find(self, name: str) -> Path:
        for folder in self.folders():
            known = parse_archive_name(name) is not None or name in {b.name for b in list_backups(folder)}
            if known and (folder / name).is_file():
                return folder / name
        raise NotFound("Нет такой копии")

    def delete(self, name: str) -> None:
        self.find(name).unlink()
        self._last_fp = None  # "nothing changed since the last copy" spoke of a copy that may be gone
        self.ctx.notices.emit("backups")

    def refresh_warnings(self) -> None:
        """The startup warnings again, once something may have fixed them. Without any there is nothing
        to take back, and a phone is spared the database check."""
        if not self.warnings:
            return
        from tavern.services import doctor

        fresh = doctor.quick(self.ctx)
        if fresh != self.warnings:
            self.warnings = fresh
            self.ctx.notices.emit("backups")

    def status(self) -> dict[str, Any]:
        newest = next(iter(self.archives()), None)
        target, problem = self.target_dir()
        return {
            "settings": self.settings(),
            "folder": str(target),
            "folder_problem": problem,
            "newest": newest.created_at.isoformat() if newest else "",
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "warnings": self.warnings,
            "gdrive": self.ctx.gdrive.public() if self.ctx.gdrive is not None else None,
        }

    # -- making copies -----------------------------------------------------------------------

    def tmp_dir(self) -> Path:
        """A fresh work folder inside the data folder (same disk: moving out of it is a rename)."""
        folder = self.ctx.paths.root / TMP_DIR
        folder.mkdir(exist_ok=True)
        return Path(tempfile.mkdtemp(dir=folder))

    def _newest_fingerprint(self) -> str | None:
        if self._last_fp is not None:
            return self._last_fp
        for info in self.archives():
            try:
                self._last_fp = read_archive_manifest(info.path).get("fingerprint") or None
            except StorageError:
                continue
            return self._last_fp
        return None

    def create(self, kind: str, reason: str = "", *, force: bool = False) -> dict[str, Any] | None:
        """Take a copy. Returns None when nothing changed since the last copy (unless `force`)."""
        with self._lock:
            work = self.tmp_dir()
            try:
                db_copy = work / ARCHIVE_DB
                snapshot_db(self.ctx.paths.db, db_copy)
                fp = fingerprint(self.ctx.paths.root, db_copy)
                if not force and fp == self._newest_fingerprint():
                    log.info("Backup %s skipped: nothing changed", kind)
                    return None
                folder, problem = self.target_dir()
                name = archive_name(kind, reason)
                write_archive(self.ctx.paths.root, db_copy, folder / name, {
                    "kind": kind, "reason": reason, "created_at": datetime.now(timezone.utc).isoformat(),
                    "app_version": __version__, "schema": self.ctx.db.schema_version, "fingerprint": fp,
                })
            except (OSError, sqlite3.Error, StorageError) as exc:
                self.last_error = f"Копия не сделана: {exc}"
                log.error("Backup %s failed: %s", kind, exc)
                self.ctx.notices.emit("backups")
                raise BackupError(self.last_error) from exc
            finally:
                shutil.rmtree(work, ignore_errors=True)
            self._last_fp = fp
            self.last_ok = datetime.now(timezone.utc).isoformat()
            self.last_error = problem
            self.prune()
        self.ctx.notices.emit("backups")
        if self.ctx.gdrive is not None:
            self.ctx.gdrive.wake()
        return {"name": name, "folder": str(folder), "warning": problem}

    def before(self, reason: str) -> None:
        """The copy before a risky action. Never blocks the action: a failure is shown instead."""
        if not self.settings()["risky"]:
            return
        try:
            self.create("risky", reason)
        except BackupError:
            pass  # already logged and kept in last_error for the page

    def prune(self) -> list[str]:
        keep = self.settings()["keep"]
        seen: dict[str, int] = {}
        removed = []
        for info in self.archives():
            if info.kind not in keep:
                continue  # manual and uploaded copies stay until deleted by hand
            seen[info.kind] = seen.get(info.kind, 0) + 1
            if seen[info.kind] > keep[info.kind]:
                info.path.unlink(missing_ok=True)
                removed.append(info.name)
        return removed

    def upload_file(self, probe: Path) -> dict[str, Any]:
        """Keep a copy file the user brought (from another device, from the cloud)."""
        name = self.keep(probe)
        if self.ctx.gdrive is not None:
            self.ctx.gdrive.wake()
        return {"name": name}

    def keep(self, probe: Path) -> str:
        """Move a checked copy file into the data folder as an `upload` copy. Returns its name."""
        try:
            manifest = read_archive_manifest(probe)
        except StorageError as exc:
            raise BackupError(str(exc)) from exc
        name = archive_name("upload", str(manifest.get("kind", "")))
        self.ctx.paths.backups.mkdir(parents=True, exist_ok=True)
        os.replace(probe, self.ctx.paths.backups / name)
        self.ctx.notices.emit("backups")
        return name

    # -- schedule ------------------------------------------------------------------------------

    def tick(self, now: datetime | None = None, mono: float | None = None) -> list[str]:
        """Take the timed copies that are due. Returns the kinds attempted."""
        now = now or datetime.now()
        mono = time.monotonic() if mono is None else mono
        settings = self.settings()
        due = []
        today = now.date().isoformat()
        if settings["daily"] and now.hour >= settings["daily_hour"] and self._daily_done != today:
            if not any(a.kind == "daily" and a.created_at.astimezone().date().isoformat() == today
                       for a in self.archives()):
                due.append("daily")
            self._daily_done = today
        if settings["hourly"]:
            period = settings["every_hours"] * 3600
            if "hourly" not in self._tried:
                newest = next((a for a in self.archives() if a.kind == "hourly"), None)
                age = (datetime.now(timezone.utc) - newest.created_at).total_seconds() if newest else period
                self._tried["hourly"] = mono - age
            if mono - self._tried["hourly"] >= period:
                due.append("hourly")
        for kind in due:
            self._tried[kind] = mono
            try:
                self.create(kind)
            except BackupError:
                pass
        return due

    def start_scheduler(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tavern-backups", daemon=True)
        self._thread.start()

    def stop_scheduler(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        from tavern.services import doctor  # doctor reads backup state

        try:
            self.warnings = doctor.quick(self.ctx)
            if self.warnings:
                self.ctx.notices.emit("backups")
            if self.settings()["on_start"]:
                self.create("start")
        except Exception:  # noqa: BLE001 - the scheduler must outlive one bad start
            log.exception("Startup backup failed")
        while not self._stop.wait(TICK_SECONDS):
            try:
                self.tick()
                self.refresh_warnings()
            except Exception:  # noqa: BLE001
                log.exception("Scheduled backup failed")

    # -- restore -------------------------------------------------------------------------------

    def restore(self, name: str) -> dict[str, Any]:
        """Put a copy back into the running app. Everything is as before if any step fails."""
        if self.ctx.runs.running():
            raise Conflict("Сейчас пишется ответ или работает помощник. Остановите или дождитесь конца, потом "
                           "восстанавливайте.",
                           code="busy")
        source = self.find(name)
        db_only = source.suffix == ".sqlite3"
        with self._lock:
            work = self.tmp_dir()
            try:
                if db_only:
                    check_db(source)
                    shutil.copyfile(source, work / ARCHIVE_DB)
                    manifest: dict[str, Any] = {}
                else:
                    manifest = extract_archive(source, work / RESTORE_NEW)
                    os.replace(work / RESTORE_NEW / ARCHIVE_DB, work / ARCHIVE_DB)
                if int(manifest.get("schema", 0)) > self.ctx.db.schema_version:
                    raise BackupError("Копия сделана более новой версией Tavern. Сначала обновите программу.")
                safety = self.create("risky", "pre-restore", force=True)
                self._swap(work, folders=not db_only)
            except StorageError as exc:
                raise BackupError(str(exc)) from exc
            finally:
                shutil.rmtree(work, ignore_errors=True)
            self._last_fp = None
            self.refresh_warnings()
        log.info("Restored from %s", name)
        self.ctx.notices.emit("restored", name=name)
        return {"restored": name, "safety": safety["name"] if safety else ""}

    def _swap(self, work: Path, *, folders: bool) -> None:
        ctx = self.ctx
        keep_settings = ctx.settings.get(SETTINGS_KEY)
        current_db = work / "current.sqlite3"
        snapshot_db(ctx.paths.db, current_db)
        old = work / RESTORE_OLD
        old.mkdir()
        moved: list[str] = []
        ctx.plugins.stop()
        try:
            if folders:
                for top in ARCHIVE_DIRS:
                    live = ctx.paths.root / top
                    if live.exists():
                        live.rename(old / top)
                    moved.append(top)
                    new = work / RESTORE_NEW / top
                    if new.is_dir():
                        new.rename(live)
                    else:
                        live.mkdir()
            self._load_db(work / ARCHIVE_DB)
            ctx.db.migrate()
            if keep_settings is not None:
                ctx.settings.set(SETTINGS_KEY, keep_settings)
            else:
                ctx.settings.delete(SETTINGS_KEY)
        except BaseException:
            log.exception("Restore failed, putting everything back")
            for top in moved:
                live = ctx.paths.root / top
                if live.exists():
                    shutil.rmtree(live, ignore_errors=True)
                if (old / top).exists():
                    (old / top).rename(live)
                else:
                    live.mkdir(exist_ok=True)
            self._load_db(current_db)
            raise
        else:
            self._keep_versions(old, moved)
        finally:
            ctx.plugins.errors.clear()
            ctx.plugins.start()

    def _keep_versions(self, old: Path, tops: list[str]) -> None:
        """Past versions of plugins and themes are not in a copy: they stay from before the restore,
        or «Вернуть прошлую версию» would lose them. Not worth failing a finished restore over."""
        for top in tops:
            kept, live = old / top / VERSIONS_DIR, self.ctx.paths.root / top / VERSIONS_DIR
            try:
                if kept.is_dir() and not live.exists():
                    kept.rename(live)
            except OSError as exc:
                log.warning("Could not keep past versions in %s: %s", top, exc)

    def _load_db(self, source: Path) -> None:
        db = self.ctx.db
        src = sqlite3.connect(str(source))
        try:
            with db._lock:  # noqa: SLF001 - restoring replaces the live connection's content
                src.backup(db.connection)
        finally:
            src.close()


def leftovers(root: Path, backups: list[Path]) -> list[Path]:
    """Files and folders an interrupted copy, restore or plugin install leaves behind."""
    out = list((root / TMP_DIR).iterdir()) if (root / TMP_DIR).is_dir() else []
    plugins = root / "plugins"
    if plugins.is_dir():
        out += [p for p in plugins.iterdir() if p.name.startswith((".new-", ".old-"))]
    for folder in backups:
        if folder.is_dir():
            out += [p for p in folder.iterdir() if p.name.endswith(".zip.part")]
    return out
