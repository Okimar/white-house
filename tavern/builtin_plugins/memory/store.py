"""SQL over the memory plugin's own tables (migrations/0001_memory.sql). Nothing else touches them.

`db` is the app's `Database` (`api.ctx.db`). Vectors are stored normalized as `array('f')` bytes (a dot product is
then the cosine) next to the hash of the text they were made from and the model's name: a vector counts only when
both still match its piece. `VectorCache` keeps a chat's vectors in memory between replies, so scoring does not
read thousands of BLOBs every time (ADR 0019 risk R1).
"""

from __future__ import annotations

import json
from array import array
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

from .pieces import Spec

PIECES, VECTORS, CHATS = "p_memory_pieces", "p_memory_vectors", "p_memory_chats"


# -- per-chat switch -------------------------------------------------------------------------------


def chat_row(db: Database, chat_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT chat_id, enabled, settings FROM {CHATS} WHERE chat_id = ?", (chat_id,))
    return None if row is None else {"chat_id": row["chat_id"], "enabled": bool(row["enabled"]),
                                     "settings": json.loads(row["settings"])}


def is_enabled(db: Database, chat_id: str) -> bool:
    row = db.fetchone(f"SELECT enabled FROM {CHATS} WHERE chat_id = ?", (chat_id,))
    return bool(row and row["enabled"])


def set_enabled(db: Database, chat_id: str, enabled: bool) -> None:
    db.execute(
        f"INSERT INTO {CHATS} (chat_id, enabled, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
        (chat_id, int(enabled), now_iso()))


# -- pieces ----------------------------------------------------------------------------------------


def pieces(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every piece row of the chat (all branches): id, message id, part, hash, tokens, keywords."""
    rows = db.fetchall(
        f"SELECT id, first_id, part, text_hash, tokens, keywords FROM {PIECES} WHERE chat_id = ?", (chat_id,))
    return [{"id": r["id"], "message_id": r["first_id"], "part": r["part"], "hash": r["text_hash"],
             "tokens": r["tokens"], "keywords": json.loads(r["keywords"])} for r in rows]


def sync(db: Database, chat_id: str, wanted: list[Spec], path_ids: set[str]) -> dict[str, int]:
    """Make the pieces of the messages on `path_ids` equal `wanted`. Pieces of messages on other branches stay.
    Returns {added, changed, removed}. A changed piece keeps its id; its old vector goes stale by hash."""
    have = {(p["message_id"], p["part"]): p for p in pieces(db, chat_id) if p["message_id"] in path_ids}
    want = {(s.message_id, s.part): s for s in wanted}
    added = changed = removed = 0
    now = now_iso()
    with db.transaction() as conn:
        for key, piece in have.items():
            if key not in want:
                conn.execute(f"DELETE FROM {PIECES} WHERE id = ?", (piece["id"],))
                removed += 1
        for key, spec in want.items():
            piece = have.get(key)
            keywords = json.dumps(list(spec.keywords), ensure_ascii=False)
            if piece is None:
                conn.execute(
                    f"INSERT INTO {PIECES} (id, chat_id, first_id, last_id, part, text_hash, tokens, keywords, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), chat_id, spec.message_id, spec.message_id, spec.part, spec.hash, spec.tokens,
                     keywords, now))
                added += 1
            elif piece["hash"] != spec.hash:
                conn.execute(f"UPDATE {PIECES} SET text_hash = ?, tokens = ?, keywords = ? WHERE id = ?",
                             (spec.hash, spec.tokens, keywords, piece["id"]))
                changed += 1
    return {"added": added, "changed": changed, "removed": removed}


# -- vectors ---------------------------------------------------------------------------------------


def normalized(vector: list[float]) -> array:
    norm = sum(x * x for x in vector) ** 0.5
    return array("f", [x / norm for x in vector] if norm else vector)


def current_vector_ids(db: Database, chat_id: str, model: str) -> dict[str, str]:
    """item id -> text hash of the stored vector, for one model (no BLOBs read)."""
    rows = db.fetchall(f"SELECT item_id, text_hash FROM {VECTORS} WHERE chat_id = ? AND model = ?",
                       (chat_id, model))
    return {r["item_id"]: r["text_hash"] for r in rows}


def save_vectors(db: Database, chat_id: str, model: str, items: list[tuple[str, str, list[float]]]) -> None:
    """items: (piece id, text hash, vector). One transaction: a batch is kept whole or not at all."""
    with db.transaction() as conn:
        for item_id, digest, vector in items:
            conn.execute(
                f"INSERT INTO {VECTORS} (item_id, chat_id, model, text_hash, vector) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, model) DO UPDATE SET text_hash = excluded.text_hash, "
                "vector = excluded.vector, chat_id = excluded.chat_id",
                (item_id, chat_id, model, digest, normalized(vector).tobytes()))


def vector_count(db: Database, chat_id: str, model: str) -> int:
    row = db.fetchone(f"SELECT COUNT(*) AS n FROM {VECTORS} WHERE chat_id = ? AND model = ?", (chat_id, model))
    return int(row["n"])


class VectorCache:
    """A chat's vectors in memory: item id -> (text hash, array('f')). Reloaded when the table changed
    (count and the newest rowid differ)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[tuple[int, int], dict[str, tuple[str, array]]]] = {}

    def get(self, db: Database, chat_id: str, model: str) -> dict[str, tuple[str, array]]:
        stamp = db.fetchone(f"SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS top FROM {VECTORS} "
                            "WHERE chat_id = ? AND model = ?", (chat_id, model))
        key = (int(stamp["n"]), int(stamp["top"]))
        cached = self._data.get((chat_id, model))
        if cached is not None and cached[0] == key:
            return cached[1]
        loaded: dict[str, tuple[str, array]] = {}
        for row in db.fetchall(f"SELECT item_id, text_hash, vector FROM {VECTORS} WHERE chat_id = ? AND model = ?",
                               (chat_id, model)):
            vector = array("f")
            vector.frombytes(row["vector"])
            loaded[row["item_id"]] = (row["text_hash"], vector)
        self._data[(chat_id, model)] = (key, loaded)
        return loaded

    def clear(self) -> None:
        self._data.clear()


# -- chapters (migrations/0002_chapters.sql) -------------------------------------------------------

CHAPTERS = "p_memory_chapters"
CHAPTER_FIELDS = ("id", "chat_id", "first_id", "last_id", "title", "state", "content_hash", "participants", "whole",
                  "border_by", "reason", "tries", "created_at", "updated_at")


def _chapter(row: Any) -> dict[str, Any]:
    out = {key: row[key] for key in CHAPTER_FIELDS}
    out["participants"] = json.loads(out["participants"])
    out["whole"] = bool(out["whole"])
    return out


def chapter_rows(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every chapter row of the chat, all branches (which of them belong to the open branch: chapters.placed)."""
    rows = db.fetchall(f"SELECT {', '.join(CHAPTER_FIELDS)} FROM {CHAPTERS} WHERE chat_id = ? "
                       "ORDER BY created_at, id", (chat_id,))
    return [_chapter(r) for r in rows]


def get_chapter(db: Database, chapter_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {', '.join(CHAPTER_FIELDS)} FROM {CHAPTERS} WHERE id = ?", (chapter_id,))
    return None if row is None else _chapter(row)


def put_chapters(db: Database, rows: list[dict[str, Any]]) -> None:
    """Insert or replace whole chapter rows (a new chapter, an edit, an undo): one transaction."""
    with db.transaction() as conn:
        for row in rows:
            values = [json.dumps(row[k], ensure_ascii=False) if k == "participants" else
                      (int(row[k]) if k == "whole" else row[k]) for k in CHAPTER_FIELDS]
            conn.execute(
                f"INSERT OR REPLACE INTO {CHAPTERS} ({', '.join(CHAPTER_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(CHAPTER_FIELDS))})", values)


def delete_chapters(db: Database, ids: list[str]) -> None:
    with db.transaction() as conn:
        for chapter_id in ids:
            conn.execute(f"DELETE FROM {CHAPTERS} WHERE id = ?", (chapter_id,))


def new_chapter(chat_id: str, first_id: str, last_id: str, content_hash: str, participants: list[str], *,
                by: str = "script", reason: str = "", title: str = "") -> dict[str, Any]:
    now = now_iso()
    return {"id": new_id(), "chat_id": chat_id, "first_id": first_id, "last_id": last_id, "title": title,
            "state": "raw", "content_hash": content_hash, "participants": participants, "whole": False,
            "border_by": by, "reason": reason, "tries": 0, "created_at": now, "updated_at": now}


def existing_messages(db: Database, ids: list[str]) -> set[str]:
    """Which of these message ids are still in the database."""
    if not ids:
        return set()
    marks = ", ".join("?" * len(ids))
    return {r["id"] for r in db.fetchall(f"SELECT id FROM messages WHERE id IN ({marks})", tuple(ids))}
