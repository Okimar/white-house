"""Sandbox mode: run against a throwaway copy of the data directory.

Agents and plugin authors use `--sandbox` to experiment without touching real data. The copy
lives in the system temp dir and is deleted on exit unless `keep` is set.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path

from tavern.paths import DataPaths

log = logging.getLogger(__name__)

# Large or irrelevant for experiments; the sandbox starts without them.
SKIP_DIRS = frozenset({"backups", "logs"})
SKIP_FILES = frozenset({".lock", "tavern.sqlite3", "tavern.sqlite3-wal", "tavern.sqlite3-shm"})


def make_sandbox(source: DataPaths) -> DataPaths:
    root = Path(tempfile.mkdtemp(prefix="tavern-sandbox-"))
    sandbox = DataPaths(root)
    if source.root.exists():
        for item in source.root.iterdir():
            if item.name in SKIP_FILES or (item.is_dir() and item.name in SKIP_DIRS):
                continue
            if item.is_dir():
                shutil.copytree(item, root / item.name)
            else:
                shutil.copy2(item, root / item.name)
        if source.db.exists():
            # Backup API gives a consistent copy even if the real server is running.
            src = sqlite3.connect(f"file:{source.db.as_posix()}?mode=ro", uri=True)
            dst = sqlite3.connect(str(sandbox.db))
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
    sandbox.ensure()
    log.info("Sandbox data dir: %s", root)
    return sandbox


def remove_sandbox(sandbox: DataPaths) -> None:
    shutil.rmtree(sandbox.root, ignore_errors=True)
