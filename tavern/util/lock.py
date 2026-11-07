"""Exclusive lock on the data directory, so two processes never share one.

Uses OS file locks (released automatically if the process dies), not PID files.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import TracebackType

from tavern.errors import TavernError


class DataDirLocked(TavernError):
    pass


class DirLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 - held open for the lock's lifetime
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise DataDirLocked(
                f"Data directory {self.path.parent} is in use by another Tavern process. "
                "Stop it first (Ctrl+C in its window, or: pkill -f \"m tavern\"). "
                "Данные уже использует другая запущенная копия White House: остановите её "
                "(Ctrl+C в её окне или pkill -f \"m tavern\") и запустите снова."
            ) from exc
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> DirLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
