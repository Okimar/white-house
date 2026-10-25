"""Saved API connections. API keys are not stored here: they live in secrets.json."""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso


def _dict(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model": row["model"],
        "params": json.loads(row["params"]),
        "created_at": row["created_at"],
    }


class ConnectionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, *, name: str, kind: str, base_url: str, model: str = "", params: dict | None = None) -> dict:
        conn_id = new_id()
        self.db.execute(
            "INSERT INTO connections (id, name, kind, base_url, model, params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conn_id, name, kind, base_url, model, json.dumps(params or {}), now_iso()),
        )
        return self.get(conn_id)

    def get(self, conn_id: str) -> dict[str, Any]:
        row = self.db.fetchone("SELECT * FROM connections WHERE id = ?", (conn_id,))
        if row is None:
            raise NotFound(f"Connection {conn_id} not found")
        return _dict(row)

    def list(self) -> list[dict[str, Any]]:
        return [_dict(r) for r in self.db.fetchall("SELECT * FROM connections ORDER BY name COLLATE NOCASE")]

    def update(self, conn_id: str, **fields: Any) -> dict[str, Any]:
        current = self.get(conn_id)
        merged = {**current, **{k: v for k, v in fields.items() if v is not None}}
        self.db.execute(
            "UPDATE connections SET name = ?, kind = ?, base_url = ?, model = ?, params = ? WHERE id = ?",
            (
                merged["name"],
                merged["kind"],
                merged["base_url"],
                merged["model"],
                json.dumps(merged["params"]),
                conn_id,
            ),
        )
        return self.get(conn_id)

    def delete(self, conn_id: str) -> None:
        self.get(conn_id)
        self.db.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
