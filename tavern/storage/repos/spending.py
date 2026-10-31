"""Spending records: one row per model request (tokens, cost in dollars) and sums over them."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import now_iso

_SUMS = ("COUNT(*) AS requests, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
         "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, COALESCE(SUM(cost), 0) AS cost, "
         "COALESCE(SUM(cost IS NULL), 0) AS unpriced")


def today() -> str:
    """The local calendar day: limits reset at the user's midnight, not UTC's."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _sums(row: Any) -> dict[str, Any]:
    return {"requests": row["requests"], "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"], "cost": round(row["cost"], 6),
            "unpriced": row["unpriced"]}


class SpendingRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self, *, chat_id: str | None, message_id: str | None, connection_id: str, connection_name: str,
        model: str, purpose: str, prompt_tokens: int, completion_tokens: int, cost: float | None,
        estimated: bool, day: str | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO spending (at, day, chat_id, message_id, connection_id, connection_name, model, purpose, "
            "prompt_tokens, completion_tokens, cost, estimated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), day or today(), chat_id, message_id, connection_id, connection_name, model, purpose,
             prompt_tokens, completion_tokens, cost, int(estimated)),
        )

    def day(self, day: str) -> dict[str, Any]:
        return _sums(self.db.fetchone(f"SELECT {_SUMS} FROM spending WHERE day = ?", (day,)))

    def month(self, month: str) -> dict[str, Any]:
        """`month` is YYYY-MM."""
        return _sums(self.db.fetchone(f"SELECT {_SUMS} FROM spending WHERE day LIKE ?", (month + "-%",)))

    def chat(self, chat_id: str) -> dict[str, Any]:
        return _sums(self.db.fetchone(f"SELECT {_SUMS} FROM spending WHERE chat_id = ?", (chat_id,)))

    def message(self, message_id: str) -> dict[str, Any]:
        """Everything spent on a message: every version, fallback attempt and extra request."""
        return _sums(self.db.fetchone(f"SELECT {_SUMS} FROM spending WHERE message_id = ?", (message_id,)))

    def by_model(self, since_day: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            f"SELECT connection_id, MAX(connection_name) AS connection_name, model, MAX(day) AS last_day, {_SUMS} "
            "FROM spending WHERE day >= ? GROUP BY connection_id, model ORDER BY cost DESC, requests DESC",
            (since_day,))
        return [{"connection_id": r["connection_id"], "connection_name": r["connection_name"], "model": r["model"],
                 "last_day": r["last_day"], **_sums(r)} for r in rows]

    def by_day(self, since_day: str) -> list[dict[str, Any]]:
        rows = self.db.fetchall(
            f"SELECT day, {_SUMS} FROM spending WHERE day >= ? GROUP BY day ORDER BY day DESC", (since_day,))
        return [{"day": r["day"], **_sums(r)} for r in rows]
