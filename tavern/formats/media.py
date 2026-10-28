"""File types Tavern stores in user folders (themes, image library) and serves back to the browser.

Only these extensions are accepted anywhere: no HTML (a page from a theme would run with full
access to the app), no executables. Pictures are recognised by their bytes, not their name.
"""

from __future__ import annotations

MEDIA_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"})
# Code the browser executes or applies; everything else is served as an inert, sandboxed file.
ACTIVE_EXTENSIONS = frozenset({".css", ".js"})


def media_type(suffix: str) -> str:
    return MEDIA_TYPES.get(suffix.lower(), "application/octet-stream")


def sniff_image(blob: bytes) -> str | None:
    """Extension of a picture judged by its content, or None if it is not a supported picture."""
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    head = blob[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith((b"<?xml", b"<svg", b"<!--")) and b"<svg" in blob[:4096]:
        return ".svg"
    return None
