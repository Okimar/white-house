"""Schema migrations: files named `NNNN_description.sql`, applied in order.

Rules (see .claude/skills/core-changes):
- Never edit a migration that has been committed. Add a new one.
- Numbers are contiguous, starting at 0001.
- A migration runs inside one transaction together with the `user_version` bump.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent
_NAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in directory.iterdir():
        match = _NAME_RE.match(path.name)
        if match is None:
            continue
        migrations.append(
            Migration(int(match.group(1)), match.group(2), path.read_text(encoding="utf-8"))
        )
    migrations.sort(key=lambda m: m.version)
    for expected, migration in enumerate(migrations, start=1):
        if migration.version != expected:
            raise RuntimeError(
                f"Migrations must be numbered contiguously: expected {expected:04d}, "
                f"found {migration.version:04d}_{migration.name}"
            )
    return migrations
