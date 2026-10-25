"""Secrets (access token, provider API keys) in `data/secrets.json`, file mode 0600.

Secret values never go to the browser and never into logs. API routes may only report
whether a key is set.
"""

from __future__ import annotations

import json
import secrets
import threading
from pathlib import Path
from typing import Any

from tavern.errors import StorageError
from tavern.util.fs import atomic_write_text

ACCESS_TOKEN_KEY = "access_token"


class SecretStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StorageError(f"{self._path}: corrupted secrets file: {exc}") from exc
        if not isinstance(data, dict):
            raise StorageError(f"{self._path}: expected a JSON object")
        return data

    def _save(self) -> None:
        atomic_write_text(self._path, json.dumps(self._data, indent=2), mode=0o600)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def has(self, key: str) -> bool:
        with self._lock:
            return bool(self._data.get(key))

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def delete(self, key: str) -> None:
        with self._lock:
            if self._data.pop(key, None) is not None:
                self._save()

    def access_token(self) -> str:
        """Token for network access. Generated on first use."""
        with self._lock:
            token = self._data.get(ACCESS_TOKEN_KEY)
            if not token:
                token = secrets.token_urlsafe(32)
                self._data[ACCESS_TOKEN_KEY] = token
                self._save()
            return token

    def rotate_access_token(self) -> str:
        with self._lock:
            token = secrets.token_urlsafe(32)
            self._data[ACCESS_TOKEN_KEY] = token
            self._save()
            return token
