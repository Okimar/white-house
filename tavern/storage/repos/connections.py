"""Saved API connections and what is known about their models. API keys live in secrets.json."""

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
        "service": row["service"],
        "base_url": row["base_url"],
        "model": row["model"],
        "params": json.loads(row["params"]),
        "created_at": row["created_at"],
    }


class ConnectionRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self, *, name: str, kind: str, base_url: str, model: str = "", params: dict | None = None,
        service: str = "",
    ) -> dict:
        conn_id = new_id()
        self.db.execute(
            "INSERT INTO connections (id, name, kind, service, base_url, model, params, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conn_id, name, kind, service, base_url, model, json.dumps(params or {}), now_iso()),
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
            "UPDATE connections SET name = ?, kind = ?, service = ?, base_url = ?, model = ?, params = ? "
            "WHERE id = ?",
            (
                merged["name"],
                merged["kind"],
                merged["service"],
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

    # What we know about models

    def models(self, conn_id: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            "SELECT * FROM model_info WHERE connection_id = ? ORDER BY model", (conn_id,)
        )
        return [_model(r) for r in rows]

    def model_infos(self, model: str) -> list[dict[str, Any]]:
        """What every connection's list says about `model` (prices for spending)."""
        rows = self.db.fetchall("SELECT * FROM model_info WHERE model = ?", (model,))
        return [{**_model(r), "connection_id": r["connection_id"]} for r in rows]

    def replace_models(self, conn_id: str, infos: list[dict[str, Any]]) -> None:
        """The service's fresh list. Trial results of models still listed are kept."""
        now = now_iso()
        with self.db.transaction():
            probes = {
                r["model"]: r["probe"]
                for r in self.db.fetchall("SELECT model, probe FROM model_info WHERE connection_id = ?", (conn_id,))
            }
            self.db.execute("DELETE FROM model_info WHERE connection_id = ?", (conn_id,))
            for info in infos:
                self.db.execute(
                    "INSERT OR REPLACE INTO model_info (connection_id, model, info, probe, checked_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (conn_id, info["id"], json.dumps(info), probes.get(info["id"], "{}"), now),
                )

    def set_probe(self, conn_id: str, model: str, results: dict[str, str]) -> dict[str, Any]:
        row = self.db.fetchone(
            "SELECT probe FROM model_info WHERE connection_id = ? AND model = ?", (conn_id, model)
        )
        merged = {**(json.loads(row["probe"]) if row else {}), **results}
        if row is None:
            self.db.execute(
                "INSERT INTO model_info (connection_id, model, info, probe, checked_at) VALUES (?, ?, ?, ?, ?)",
                (conn_id, model, json.dumps({"id": model}), json.dumps(merged), now_iso()),
            )
        else:
            self.db.execute(
                "UPDATE model_info SET probe = ?, checked_at = ? WHERE connection_id = ? AND model = ?",
                (json.dumps(merged), now_iso(), conn_id, model),
            )
        return merged


def _model(row: Any) -> dict[str, Any]:
    return {**json.loads(row["info"]), "id": row["model"], "probe": json.loads(row["probe"]),
            "checked_at": row["checked_at"]}
