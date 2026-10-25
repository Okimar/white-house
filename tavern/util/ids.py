"""Identifiers and timestamps."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    """UTC, millisecond precision, same shape as the SQL defaults ('2026-10-26T10:00:00.123Z')."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
