"""Character history: versions, steps (what things were before a change), raw block rows.

Step dict shape: id, character_id, version_id, at, what, undone, before (list of ops, see
services/history.py). Version dict shape: id, character_id, started_at, closed_at.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

from tavern.storage.db import Database


def _pack(ops: list[dict[str, Any]]) -> bytes:
    return zlib.compress(json.dumps(ops, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _unpack(blob: bytes) -> list[dict[str, Any]]:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


class HistoryRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # --- versions and steps ----------------------------------------------------------------------

    def open_version(self, character_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM character_versions WHERE character_id = ? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
            (character_id,),
        )
        return dict(row) if row else None

    def new_version(self, character_id: str, at: str) -> int:
        return self.db.execute(
            "INSERT INTO character_versions (character_id, started_at) VALUES (?, ?)", (character_id, at)
        ).lastrowid

    def close_version(self, version_id: int, at: str) -> None:
        self.db.execute("UPDATE character_versions SET closed_at = ? WHERE id = ?", (at, version_id))

    def add_step(self, character_id: str, version_id: int, at: str, what: str, ops: list[dict[str, Any]]) -> int:
        return self.db.execute(
            "INSERT INTO character_steps (character_id, version_id, at, what, before) VALUES (?, ?, ?, ?, ?)",
            (character_id, version_id, at, what, _pack(ops)),
        ).lastrowid

    def drop_undone(self, character_id: str) -> None:
        self.db.execute("DELETE FROM character_steps WHERE character_id = ? AND undone = 1", (character_id,))
        self.db.execute(
            "DELETE FROM character_versions WHERE character_id = ? AND closed_at IS NOT NULL "
            "AND id NOT IN (SELECT version_id FROM character_steps)", (character_id,),
        )

    def last_step(self, character_id: str, *, undone: bool = False) -> dict[str, Any] | None:
        row = self.db.fetchone(
            "SELECT * FROM character_steps WHERE character_id = ? AND undone = ? ORDER BY id DESC LIMIT 1",
            (character_id, int(undone)),
        )
        return self._step(row) if row else None

    def steps_after(self, character_id: str, step_id: int) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM character_steps WHERE character_id = ? AND undone = 0 AND id > ? ORDER BY id DESC",
            (character_id, step_id),
        )
        return [self._step(r) for r in rows]

    def mark_undone(self, step_id: int) -> None:
        self.db.execute("UPDATE character_steps SET undone = 1 WHERE id = ?", (step_id,))

    def versions(self, character_id: str) -> list[dict[str, Any]]:
        versions = [dict(r) for r in self.db.fetchall(
            "SELECT * FROM character_versions WHERE character_id = ? ORDER BY id DESC", (character_id,)
        )]
        steps: dict[int, list[dict[str, Any]]] = {}
        for row in self.db.fetchall(
            "SELECT id, version_id, at, what, undone FROM character_steps WHERE character_id = ? ORDER BY id DESC",
            (character_id,),
        ):
            steps.setdefault(row["version_id"], []).append(
                {"id": row["id"], "at": row["at"], "what": row["what"], "undone": bool(row["undone"])}
            )
        for version in versions:
            version["steps"] = steps.get(version["id"], [])
        return [v for v in versions if v["steps"]]

    def version_last_step(self, version_id: int) -> int | None:
        row = self.db.fetchone("SELECT MAX(id) AS m FROM character_steps WHERE version_id = ?", (version_id,))
        return row["m"] if row else None

    def size(self, character_id: str) -> int:
        row = self.db.fetchone(
            "SELECT COALESCE(SUM(LENGTH(before)), 0) AS n FROM character_steps WHERE character_id = ?", (character_id,)
        )
        return int(row["n"])

    def _step(self, row: Any) -> dict[str, Any]:
        step = dict(row)
        step["before"] = _unpack(step["before"])
        step["undone"] = bool(step["undone"])
        return step

    # --- raw rows, to put things back exactly ------------------------------------------------------

    def block_row(self, block_id: str) -> dict[str, Any] | None:
        row = self.db.fetchone("SELECT * FROM character_blocks WHERE id = ?", (block_id,))
        return dict(row) if row else None

    def put_block_row(self, block_id: str, row: dict[str, Any] | None) -> None:
        """Make the block exactly `row` again; None = it did not exist."""
        if row is None:
            self.db.execute("DELETE FROM character_blocks WHERE id = ?", (block_id,))
            return
        columns = [c for c in row if c != "id"]
        if self.block_row(block_id) is None:
            names = ["id", *columns]
            self.db.execute(
                f"INSERT INTO character_blocks ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                [block_id, *(row[c] for c in columns)],
            )
        else:
            sets = ", ".join(f"{c} = ?" for c in columns)
            self.db.execute(f"UPDATE character_blocks SET {sets} WHERE id = ?", [*(row[c] for c in columns), block_id])
