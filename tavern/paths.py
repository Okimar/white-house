"""Filesystem layout. Every path the app touches is derived from here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
DEFAULT_DATA_DIR = REPO_ROOT / "data"


@dataclass(frozen=True)
class DataPaths:
    """Layout of a data directory. Nothing outside `root` is ever written."""

    root: Path

    @property
    def db(self) -> Path:
        return self.root / "tavern.sqlite3"

    @property
    def config(self) -> Path:
        return self.root / "config.toml"

    @property
    def secrets(self) -> Path:
        return self.root / "secrets.json"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def lock(self) -> Path:
        return self.root / ".lock"

    def ensure(self) -> None:
        for directory in (self.root, self.backups, self.logs):
            directory.mkdir(parents=True, exist_ok=True)
