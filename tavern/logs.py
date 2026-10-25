"""Logging setup: stderr plus a rotating file in data/logs/tavern.log."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


_OWNED = "_tavern_handler"


def setup_logging(level: str = "info", log_file: Path | None = None) -> None:
    """Idempotent: replaces only the handlers it installed earlier, never foreign ones."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _OWNED, False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level.upper())

    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        setattr(handler, _OWNED, True)
        root.addHandler(handler)

    # Request logs from uvicorn are noise at info level; errors still come through.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
