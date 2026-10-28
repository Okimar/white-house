"""Image library: pictures the user uploaded or generated, stored as files in `data/assets`.

The file type is decided by the picture's bytes, never by the name the browser sent. Names are
reduced to letters, digits, '-' and '_' and made unique, so a picture is never overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tavern.errors import AppError, NotFound
from tavern.formats.media import IMAGE_EXTENSIONS, media_type, sniff_image
from tavern.util.fs import atomic_write_bytes

MAX_IMAGE_BYTES = 15 * 1024 * 1024
NAME_RE = re.compile(r"^[\w-]{1,80}\.(png|jpg|jpeg|webp|gif|svg)$")


class AssetError(AppError):
    code = "bad_asset"


def _clean_stem(name: str) -> str:
    stem = Path(name.replace("\\", "/").split("/")[-1]).stem
    stem = re.sub(r"[^\w-]+", "-", stem).strip("-_")[:60]
    return stem or "image"


@dataclass
class AssetStore:
    root: Path

    def _info(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "name": path.name,
            "url": "/assets/" + quote(path.name),
            "size": stat.st_size,
            "type": media_type(path.suffix),
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        }

    def list(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        items = [
            p for p in self.root.iterdir()
            if p.is_file() and NAME_RE.match(p.name) and p.suffix.lower() in IMAGE_EXTENSIONS
        ]
        items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [self._info(p) for p in items]

    def path(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise NotFound(f"Picture {name!r} not found")
        path = self.root / name
        if not path.is_file():
            raise NotFound(f"Picture {name!r} not found")
        return path

    def get(self, name: str) -> dict[str, Any]:
        return self._info(self.path(name))

    def save(self, name: str, blob: bytes) -> dict[str, Any]:
        if not blob:
            raise AssetError("The picture is empty")
        if len(blob) > MAX_IMAGE_BYTES:
            raise AssetError("The picture is too large (max 15 MB)", code="asset_too_large")
        suffix = sniff_image(blob)
        if suffix is None:
            raise AssetError("Not a supported picture (PNG, JPEG, WebP, GIF or SVG)", code="not_an_image")
        stem = _clean_stem(name)
        candidate, n = f"{stem}{suffix}", 2
        while (self.root / candidate).exists():
            candidate, n = f"{stem}-{n}{suffix}", n + 1
        atomic_write_bytes(self.root / candidate, blob)
        return self.get(candidate)

    def delete(self, name: str) -> None:
        self.path(name).unlink()
