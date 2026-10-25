"""Exception hierarchy.

`TavernError` subclasses describe failures the user can act on; their message is shown as is.
`AppError` subclasses map to HTTP responses of the shape {"error": {"code", "message", "details"}}.
"""

from __future__ import annotations

from typing import Any


class TavernError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(TavernError):
    """Invalid config.toml or command-line options."""


class StorageError(TavernError):
    """Database or data directory problem (schema, migrations, backups)."""


class AppError(TavernError):
    """An error returned to the HTTP client."""

    status: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return {"error": body}


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"
