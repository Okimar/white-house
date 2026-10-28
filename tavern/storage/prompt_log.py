"""Archive of what the model saw before each reply, kept small by storing every text only once.

A prompt is a list of entries (role, text, source, label, msg). Each distinct text is a "piece":
stored once, compressed, keyed by its hash, and never changed. A record keeps only the entry list
with piece hashes. Consecutive replies share most of their prompt (same system blocks, same
history plus one or two messages), so a record reuses the first `keep` entries of the chat's
previous record (`base_id`) and stores only the rest. Chains are cut every MAX_CHAIN records so
reading one never walks far.

Because pieces are immutable, editing a card or a message later adds a new piece and leaves old
records exactly as they were. `prompt_piece_sources` remembers which versions each source had,
for the read-only version viewer.
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections import OrderedDict
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import now_iso

MAX_CHAIN = 32
_CACHE_SIZE = 64

# role, hash, source, label, msg (index of the sent message), kind (block kind or "message")
Row = tuple[str, str, str, str, int, str]


def piece_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _pack(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _unpack(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


class PromptLog:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._rows_cache: OrderedDict[int, list[Row]] = OrderedDict()

    # --- writing -----------------------------------------------------------------------------

    def record(
        self,
        chat_id: str,
        message_id: str,
        swipe_idx: int,
        entries: list[dict[str, Any]],
        meta: dict[str, Any] | None = None,
    ) -> int:
        """Store one prompt. `entries`: dicts with role, text, source, label, msg."""
        now = now_iso()
        rows: list[Row] = []
        with self.db.transaction() as conn:
            for entry in entries:
                text = entry["text"]
                digest = piece_hash(text)
                conn.execute(
                    "INSERT OR IGNORE INTO prompt_pieces (hash, content, size) VALUES (?, ?, ?)",
                    (digest, zlib.compress(text.encode("utf-8")), len(text)),
                )
                if entry.get("source"):
                    conn.execute(
                        "INSERT OR IGNORE INTO prompt_piece_sources (source, hash, first_seen) "
                        "VALUES (?, ?, ?)",
                        (entry["source"], digest, now),
                    )
                rows.append((entry["role"], digest, entry.get("source", ""),
                             entry.get("label", ""), int(entry.get("msg", 0)), entry.get("kind", "")))

            base = conn.execute(
                "SELECT id, chain FROM prompt_records WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                (chat_id,),
            ).fetchone()
            base_id, keep, chain = None, 0, 0
            if base is not None and base["chain"] + 1 < MAX_CHAIN:
                base_rows = self._rows(base["id"])
                while keep < min(len(base_rows), len(rows)) and base_rows[keep] == rows[keep]:
                    keep += 1
                if keep:
                    base_id, chain = base["id"], base["chain"] + 1
            cursor = conn.execute(
                "INSERT INTO prompt_records (chat_id, message_id, swipe_idx, base_id, keep, chain, "
                "entries, meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, message_id, swipe_idx, base_id, keep, chain,
                 _pack([list(r) for r in rows[keep:]]), json.dumps(meta or {}, ensure_ascii=False), now),
            )
            record_id = int(cursor.lastrowid)
        self._remember(record_id, rows)
        return record_id

    # --- reading -----------------------------------------------------------------------------

    def _remember(self, record_id: int, rows: list[Row]) -> None:
        self._rows_cache[record_id] = rows
        self._rows_cache.move_to_end(record_id)
        while len(self._rows_cache) > _CACHE_SIZE:
            self._rows_cache.popitem(last=False)

    def _rows(self, record_id: int) -> list[Row]:
        cached = self._rows_cache.get(record_id)
        if cached is not None:
            return cached
        row = self.db.fetchone(
            "SELECT base_id, keep, entries FROM prompt_records WHERE id = ?", (record_id,)
        )
        if row is None:
            return []
        own = [tuple(r) for r in _unpack(row["entries"])]
        prefix = self._rows(row["base_id"])[: row["keep"]] if row["base_id"] is not None else []
        rows: list[Row] = [*prefix, *own]  # type: ignore[list-item]
        self._remember(record_id, rows)
        return rows

    def find(self, message_id: str, swipe_idx: int) -> int | None:
        """Newest record of this reply version (a regenerated swipe index may repeat)."""
        row = self.db.fetchone(
            "SELECT id FROM prompt_records WHERE message_id = ? AND swipe_idx = ? "
            "ORDER BY id DESC LIMIT 1",
            (message_id, swipe_idx),
        )
        return None if row is None else int(row["id"])

    def get(self, record_id: int) -> dict[str, Any]:
        """The prompt as it was sent: entries with their texts, plus the record's meta."""
        row = self.db.fetchone("SELECT * FROM prompt_records WHERE id = ?", (record_id,))
        if row is None:
            return {}
        rows = self._rows(record_id)
        texts = self.pieces({r[1] for r in rows})
        counts = self.version_counts({r[2] for r in rows if r[2]})
        return {
            "id": record_id,
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "swipe_idx": row["swipe_idx"],
            "created_at": row["created_at"],
            "meta": json.loads(row["meta"]),
            "entries": [
                {
                    "role": role, "hash": digest, "source": source, "label": label, "msg": msg,
                    "kind": kind, "text": texts.get(digest, ""), "versions": counts.get(source, 0),
                }
                for role, digest, source, label, msg, kind in rows
            ],
        }

    def pieces(self, hashes: set[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        items = list(hashes)
        for start in range(0, len(items), 500):
            chunk = items[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self.db.fetchall(
                f"SELECT hash, content FROM prompt_pieces WHERE hash IN ({marks})", chunk
            ):
                result[row["hash"]] = zlib.decompress(row["content"]).decode("utf-8")
        return result

    def version_counts(self, sources: set[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        items = list(sources)
        for start in range(0, len(items), 500):
            chunk = items[start : start + 500]
            marks = ",".join("?" * len(chunk))
            for row in self.db.fetchall(
                f"SELECT source, COUNT(*) AS n FROM prompt_piece_sources WHERE source IN ({marks}) "
                "GROUP BY source",
                chunk,
            ):
                result[row["source"]] = int(row["n"])
        return result

    def versions(self, source: str) -> list[dict[str, Any]]:
        """Every text this source was ever sent with, oldest first. Read-only by design."""
        rows = self.db.fetchall(
            "SELECT s.hash, s.first_seen, p.size, p.content FROM prompt_piece_sources s "
            "JOIN prompt_pieces p ON p.hash = s.hash WHERE s.source = ? ORDER BY s.first_seen, s.rowid",
            (source,),
        )
        return [
            {"hash": r["hash"], "first_seen": r["first_seen"], "size": r["size"],
             "text": zlib.decompress(r["content"]).decode("utf-8")}
            for r in rows
        ]

    # --- cleanup -----------------------------------------------------------------------------

    def collect_garbage(self) -> int:
        """Delete pieces no record refers to any more (after chats were deleted). Returns count."""
        used: set[str] = set()
        for row in self.db.fetchall("SELECT entries FROM prompt_records"):
            used.update(r[1] for r in _unpack(row["entries"]))
        stored = [r["hash"] for r in self.db.fetchall("SELECT hash FROM prompt_pieces")]
        unused = [h for h in stored if h not in used]
        with self.db.transaction() as conn:
            for start in range(0, len(unused), 500):
                chunk = unused[start : start + 500]
                conn.execute(
                    f"DELETE FROM prompt_pieces WHERE hash IN ({','.join('?' * len(chunk))})", chunk
                )
        self._rows_cache.clear()
        return len(unused)
