"""Spoken pieces kept on disk so that hearing a message again costs nothing.

A piece is found by a hash of (service, model, voice, the text of the piece), so editing a message
or changing the voice simply makes a new piece, and the old one ages out. The folder is a cache:
it is under data/cache, never backed up, and may be deleted by hand at any time. When the total goes
over the limit the least recently used pieces go first.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path

EXTENSIONS = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/ogg": "ogg", "audio/aac": "aac", "audio/flac": "flac"}
MIMES = {ext: mime for mime, ext in EXTENSIONS.items()}


def make_key(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8") + b"\0")
    return digest.hexdigest()[:40]


class AudioCache:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def get(self, key: str) -> tuple[bytes, str] | None:
        for ext, mime in MIMES.items():
            path = self.folder / f"{key}.{ext}"
            try:
                data = path.read_bytes()
            except OSError:
                continue
            with contextlib.suppress(OSError):
                os.utime(path)  # used just now: it is the last to go
            return data, mime
        return None

    def put(self, key: str, data: bytes, mime: str, limit_bytes: int) -> None:
        """Keep a piece; a failure to write is not an error (the audio is played all the same)."""
        if limit_bytes <= 0 or len(data) > limit_bytes:
            return
        ext = EXTENSIONS.get(mime, "mp3")
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            temp = self.folder / f"{key}.tmp"
            temp.write_bytes(data)
            temp.replace(self.folder / f"{key}.{ext}")
        except OSError:
            return
        self.trim(limit_bytes)

    def files(self) -> list[Path]:
        try:
            return [p for p in self.folder.iterdir() if p.is_file() and p.suffix[1:] in MIMES]
        except OSError:
            return []

    def size(self) -> int:
        total = 0
        for path in self.files():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
        return total

    def trim(self, limit_bytes: int) -> None:
        rows = []
        for path in self.files():
            with contextlib.suppress(OSError):
                stat = path.stat()
                rows.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in rows)
        for _, size, path in sorted(rows, key=lambda r: r[0]):
            if total <= limit_bytes:
                break
            with contextlib.suppress(OSError):
                path.unlink()
                total -= size

    def clear(self) -> int:
        """Delete every piece; returns the bytes freed."""
        freed = 0
        for path in self.files():
            with contextlib.suppress(OSError):
                freed += path.stat().st_size
                path.unlink()
        return freed
