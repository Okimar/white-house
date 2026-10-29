"""Blocks ("bubbles") of a character's tree or of a lorebook, their vectors and per-chat hit times.

A block has one owner: a character (`character_id`) or a lorebook (`lorebook_id`); `owner_id` is
whichever is set, and every method here works by owner.

Block dict shape:
    id, owner_id, character_id, lorebook_id, parent_id, position, title, content,
    mode ('always'|'search'|'off'), keywords [str], depth (None = next to the card), role, sticky,
    layout {}, extra {}, rules {} (finer options, see services/character_tree.py), detached,
    chance, every, created_at, updated_at
"""

from __future__ import annotations

import json
from array import array
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

BLOCK_FIELDS = ("parent_id", "position", "title", "content", "mode", "keywords", "depth", "role",
                "sticky", "layout", "extra", "detached", "chance", "every", "rules")
JSON_FIELDS = ("keywords", "layout", "extra", "rules")


def _block(row: Any) -> dict[str, Any]:
    block = dict(row)
    for key in JSON_FIELDS:
        block[key] = json.loads(block[key])
    block["detached"] = bool(block["detached"])
    return block


class BlockRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM character_blocks WHERE owner_id = ? ORDER BY position, created_at",
            (owner_id,),
        )
        return [_block(r) for r in rows]

    def for_owners(self, owner_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {oid: [] for oid in owner_ids}
        if not owner_ids:
            return result
        marks = ",".join("?" * len(owner_ids))
        rows = self.db.fetchall(
            f"SELECT * FROM character_blocks WHERE owner_id IN ({marks}) ORDER BY position, created_at",
            owner_ids,
        )
        for row in rows:
            result[row["owner_id"]].append(_block(row))
        return result

    def get(self, block_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM character_blocks WHERE id = ?", (block_id,))
        if row is None:
            raise NotFound(f"Block {block_id} not found")
        return _block(row)

    def create(self, owner_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        block_id, now = new_id(), now_iso()
        values = {k: fields[k] for k in BLOCK_FIELDS if k in fields}
        if "position" not in values:
            row = self.db.fetchone(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM character_blocks "
                "WHERE owner_id = ? AND parent_id IS ?",
                (owner_id, values.get("parent_id")),
            )
            values["position"] = row[0]
        for key in JSON_FIELDS:
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        book = self.db.fetchone("SELECT 1 FROM lorebooks WHERE id = ?", (owner_id,)) is not None
        columns = ["id", "lorebook_id" if book else "character_id", *values, "created_at", "updated_at"]
        self.db.execute(
            f"INSERT INTO character_blocks ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            (block_id, owner_id, *values.values(), now, now),
        )
        return self.get(block_id)

    def update(self, block_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.get(block_id)
        values = {k: changes[k] for k in BLOCK_FIELDS if k in changes}
        if values:
            for key in JSON_FIELDS:
                if key in values:
                    values[key] = json.dumps(values[key], ensure_ascii=False)
            sets = ", ".join(f"{k} = ?" for k in values)
            self.db.execute(
                f"UPDATE character_blocks SET {sets}, updated_at = ? WHERE id = ?",
                (*values.values(), now_iso(), block_id),
            )
        return self.get(block_id)

    def delete(self, block_id: str) -> None:
        self.get(block_id)
        self.db.execute("DELETE FROM character_blocks WHERE id = ?", (block_id,))

    def delete_all(self, owner_id: str) -> None:
        self.db.execute("DELETE FROM character_blocks WHERE owner_id = ?", (owner_id,))

    # --- meaning vectors ---------------------------------------------------------------------

    def vectors(self, block_ids: list[str], model: str) -> dict[str, tuple[str, array]]:
        """block id -> (text hash, vector) for one embedding model."""
        result: dict[str, tuple[str, array]] = {}
        for start in range(0, len(block_ids), 500):
            chunk = block_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self.db.fetchall(
                f"SELECT block_id, text_hash, vector FROM block_embeddings "
                f"WHERE model = ? AND block_id IN ({marks})",
                (model, *chunk),
            ):
                vector = array("f")
                vector.frombytes(row["vector"])
                result[row["block_id"]] = (row["text_hash"], vector)
        return result

    def save_vector(self, block_id: str, model: str, text_hash: str, vector: list[float]) -> None:
        self.db.execute(
            "INSERT INTO block_embeddings (block_id, model, text_hash, vector) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(block_id, model) DO UPDATE SET text_hash = excluded.text_hash, "
            "vector = excluded.vector",
            (block_id, model, text_hash, array("f", vector).tobytes()),
        )

    # --- hits per chat -----------------------------------------------------------------------

    def hits(self, chat_id: str) -> tuple[dict[str, int], set[str]]:
        """(block id -> generation it was last found at, ids whose chance failed that time)."""
        rows = self.db.fetchall(
            "SELECT block_id, hit_gen, missed FROM chat_block_hits WHERE chat_id = ?", (chat_id,)
        )
        return {r["block_id"]: r["hit_gen"] for r in rows}, {r["block_id"] for r in rows if r["missed"]}

    def record_hits(self, chat_id: str, block_ids: list[str], generation: int, *, missed: bool = False) -> None:
        with self.db.transaction() as conn:
            for block_id in block_ids:
                conn.execute(
                    "INSERT INTO chat_block_hits (chat_id, block_id, hit_gen, missed) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(chat_id, block_id) DO UPDATE SET hit_gen = excluded.hit_gen, "
                    "missed = excluded.missed",
                    (chat_id, block_id, generation, int(missed)),
                )

    # --- links between bubbles ---------------------------------------------------------------

    def links(self, owner_ids: list[str]) -> list[tuple[str, str]]:
        if not owner_ids:
            return []
        marks = ",".join("?" * len(owner_ids))
        rows = self.db.fetchall(
            f"SELECT a, b FROM block_links WHERE owner_id IN ({marks}) ORDER BY a, b", owner_ids
        )
        return [(r["a"], r["b"]) for r in rows]

    def link(self, owner_id: str, a: str, b: str) -> None:
        a, b = sorted((a, b))
        self.db.execute(
            "INSERT OR IGNORE INTO block_links (owner_id, a, b) VALUES (?, ?, ?)", (owner_id, a, b)
        )

    def unlink(self, a: str, b: str) -> int:
        a, b = sorted((a, b))
        return self.db.execute("DELETE FROM block_links WHERE a = ? AND b = ?", (a, b)).rowcount

    def set_chance(self, block_ids: list[str], chance: int) -> int:
        changed = 0
        with self.db.transaction() as conn:
            for start in range(0, len(block_ids), 500):
                chunk = block_ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                changed += conn.execute(
                    f"UPDATE character_blocks SET chance = ?, updated_at = ? WHERE id IN ({marks})",
                    (chance, now_iso(), *chunk),
                ).rowcount
        return changed
