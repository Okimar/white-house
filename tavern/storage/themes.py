"""Themes: folders with `theme.json`, `theme.css`, an optional effects script and pictures.

Built-in themes ship in `web/themes` and are read-only; user themes live in `data/themes`.
A theme travels as one zip file (`.tavern-theme`). Replacing, restoring or deleting a user theme
first moves its folder to `data/themes/.versions/<id>/<stamp>`, so every change can be undone.

Everything here must survive a hand-edited or broken theme folder: listing never raises because
of one bad theme, it reports the theme with an `error` instead.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tavern.errors import AppError, Conflict, NotFound
from tavern.formats.media import IMAGE_EXTENSIONS, MEDIA_TYPES
from tavern.util.fs import atomic_write_bytes, atomic_write_text

log = logging.getLogger(__name__)

THEME_FILE_SUFFIX = ".tavern-theme"
MANIFEST = "theme.json"
STYLESHEET = "theme.css"
FORMAT = "tavern-theme"
FORMAT_VERSION = 1
BASES = ("auto", "light", "dark")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
VERSIONS_DIR = ".versions"
KEEP_VERSIONS = 5
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_THEME_BYTES = 30 * 1024 * 1024
MAX_FILES = 300
MAX_DEPTH = 6
# Windows refuses these as file names whatever the extension.
RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)
TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


class ThemeError(AppError):
    code = "bad_theme"


class ThemeExists(Conflict):
    code = "theme_exists"


def safe_relpath(rel: str) -> str:
    """Validate a path inside a theme ('assets/bg.png'). Rejects traversal, hidden and unknown files."""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if (
        not rel
        or len(rel) > 200
        or len(parts) > MAX_DEPTH
        or ":" in rel
        or any(p in ("", ".", "..") or p.startswith(".") for p in parts)
    ):
        raise ThemeError(f"Invalid file path {rel!r}")
    name = parts[-1]
    stem, dot, suffix = name.rpartition(".")
    if not dot or f".{suffix.lower()}" not in MEDIA_TYPES:
        raise ThemeError(f"File type not allowed in a theme: {name!r}")
    if stem.split(".")[0].lower() in RESERVED_NAMES:
        raise ThemeError(f"Reserved file name {name!r}")
    return rel


def slugify(name: str) -> str:
    text = "".join(TRANSLIT.get(ch, ch) for ch in name.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    return slug or "theme"


def normalize_manifest(raw: Any, *, theme_id: str) -> dict[str, Any]:
    """Known fields with types and lengths checked; unknown fields are dropped."""
    if not isinstance(raw, dict):
        raise ThemeError("theme.json must be a JSON object")
    if raw.get("format", FORMAT) != FORMAT:
        raise ThemeError("Not a Tavern theme (theme.json has a different format)")
    version = raw.get("format_version", FORMAT_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ThemeError("theme.json: format_version must be an integer")
    if version > FORMAT_VERSION:
        raise ThemeError("This theme needs a newer version of Tavern")

    def text(key: str, limit: int) -> str:
        value = raw.get(key, "")
        return value.strip()[:limit] if isinstance(value, str) else ""

    def optional_path(key: str, allowed: frozenset[str] | None) -> str | None:
        value = raw.get(key)
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise ThemeError(f"theme.json: {key} must be a file path")
        rel = safe_relpath(value)
        if allowed is not None and "." + rel.rsplit(".", 1)[-1].lower() not in allowed:
            raise ThemeError(f"theme.json: {key} has the wrong file type")
        return rel

    base = raw.get("base", "auto")
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": theme_id,
        "name": text("name", 80) or theme_id,
        "author": text("author", 80),
        "version": text("version", 20),
        "description": text("description", 500),
        "license": text("license", 80),
        "base": base if base in BASES else "auto",
        "script": optional_path("script", frozenset({".js"})),
        "preview": optional_path("preview", IMAGE_EXTENSIONS),
    }


SCRIPT_SUFFIXES = (".js", ".mjs")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _theme_files(folder: Path) -> dict[str, Path]:
    """Relative path -> file for every servable file of a theme folder (hidden files skipped)."""
    files: dict[str, Path] = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        try:
            files[safe_relpath(rel)] = path
        except ThemeError:
            continue
    return files


def _scripts_sha256(folder: Path, script: str) -> str | None:
    """What approving a theme's script covers: the script and every other script it could load. With
    just the one file this is that file's own hash, so earlier approvals stay valid."""
    main = _sha256(folder / script)
    others = [(rel, path) for rel, path in _theme_files(folder).items()
              if path.suffix.lower() in SCRIPT_SUFFIXES and rel != script]
    if main is None or not others:
        return main
    combined = hashlib.sha256(main.encode())
    for rel, path in others:
        combined.update(f"\0{rel}\0{_sha256(path)}".encode())
    return combined.hexdigest()


@dataclass
class ThemeStore:
    builtin_dir: Path
    user_dir: Path

    # -- reading ---------------------------------------------------------------------------

    def _folder(self, theme_id: str) -> tuple[Path, bool]:
        if not ID_RE.match(theme_id):
            raise NotFound(f"Theme {theme_id!r} not found")
        # Built-in first: a user folder with a built-in id (made by hand) never shadows it.
        builtin = self.builtin_dir / theme_id
        if (builtin / MANIFEST).is_file():
            return builtin, True
        user = self.user_dir / theme_id
        if (user / MANIFEST).is_file():
            return user, False
        raise NotFound(f"Theme {theme_id!r} not found")

    def _summary(self, folder: Path, builtin: bool) -> dict[str, Any]:
        theme_id = folder.name
        try:
            manifest = normalize_manifest(
                json.loads((folder / MANIFEST).read_text(encoding="utf-8")), theme_id=theme_id
            )
        except (OSError, ValueError, ThemeError) as exc:
            message = exc.message if isinstance(exc, ThemeError) else str(exc)
            return {"id": theme_id, "name": theme_id, "builtin": builtin, "error": message}
        script = manifest["script"]
        return {
            **manifest,
            "builtin": builtin,
            "has_css": (folder / STYLESHEET).is_file(),
            # The browser runs a script only if the user approved exactly this content.
            "script_sha256": _scripts_sha256(folder, script) if script else None,
            "versions": len(self._version_dirs(theme_id)) if not builtin else 0,
        }

    def list(self) -> list[dict[str, Any]]:
        themes: dict[str, dict[str, Any]] = {}
        for root, builtin in ((self.builtin_dir, True), (self.user_dir, False)):
            if not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                if folder.is_dir() and ID_RE.match(folder.name) and (folder / MANIFEST).is_file():
                    if builtin or folder.name not in themes:
                        themes[folder.name] = self._summary(folder, builtin)
        by_name = sorted(themes.values(), key=lambda t: (t["id"] != "default", t["name"].lower()))
        return [t for t in by_name if t["builtin"]] + [t for t in by_name if not t["builtin"]]

    def get(self, theme_id: str) -> dict[str, Any]:
        folder, builtin = self._folder(theme_id)
        return self._summary(folder, builtin)

    def file(self, theme_id: str, rel: str) -> Path:
        """A file of a theme for serving. Raises NotFound for anything outside the theme folder."""
        folder, _ = self._folder(theme_id)
        try:
            rel = safe_relpath(rel)
        except ThemeError:
            raise NotFound("No such theme file") from None
        path = (folder / rel).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            raise NotFound("No such theme file")
        return path

    def files(self, theme_id: str) -> list[dict[str, Any]]:
        folder, _ = self._folder(theme_id)
        return [{"path": rel, "size": p.stat().st_size} for rel, p in _theme_files(folder).items()]

    # -- writing ---------------------------------------------------------------------------

    def _user_folder(self, theme_id: str) -> Path:
        folder, builtin = self._folder(theme_id)
        if builtin:
            raise ThemeError("Built-in themes cannot be changed. Make a copy first", code="builtin_theme")
        return folder

    def _free_id(self, wanted: str) -> str:
        taken = {t["id"] for t in self.list()}
        candidate, n = wanted, 2
        while candidate in taken or (self.user_dir / candidate).exists():
            suffix = f"-{n}"
            candidate, n = wanted[: 64 - len(suffix)] + suffix, n + 1
        return candidate

    def _builtin_ids(self) -> set[str]:
        if not self.builtin_dir.is_dir():
            return set()
        return {p.name for p in self.builtin_dir.iterdir() if (p / MANIFEST).is_file()}

    def create(self, name: str, *, theme_id: str | None = None, source: str | None = None) -> dict[str, Any]:
        """New user theme, empty or copied from another theme (built-in or user)."""
        name = name.strip()[:80]
        if not name:
            raise ThemeError("Theme name must not be empty")
        if theme_id is not None and not ID_RE.match(theme_id):
            raise ThemeError("Theme id may contain only a-z, 0-9, '-' and '_'")
        new_id = self._free_id(theme_id or slugify(name))
        files: dict[str, bytes] = {}
        manifest: dict[str, Any] = {"name": name}
        if source:
            folder, _ = self._folder(source)
            files = {rel: p.read_bytes() for rel, p in _theme_files(folder).items()}
            manifest = {**json.loads(files.pop(MANIFEST, b"{}") or b"{}"), "name": name}
        manifest = normalize_manifest(manifest, theme_id=new_id)
        files.setdefault(STYLESHEET, b"/* Theme styles. See .claude/skills/make-theme */\n")
        self._write_folder(new_id, manifest, files)
        return self.get(new_id)

    def write_file(self, theme_id: str, rel: str, data: bytes) -> dict[str, Any]:
        folder = self._user_folder(theme_id)
        rel = safe_relpath(rel)
        if len(data) > MAX_FILE_BYTES:
            raise ThemeError("File is too large (max 10 MB)", code="file_too_large")
        if rel == MANIFEST:
            try:
                manifest = normalize_manifest(json.loads(data), theme_id=theme_id)
            except ValueError as exc:
                raise ThemeError(f"theme.json is not valid JSON: {exc}") from None
            atomic_write_text(folder / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
        else:
            existing = sum(p.stat().st_size for r, p in _theme_files(folder).items() if r != rel)
            if existing + len(data) > MAX_THEME_BYTES:
                raise ThemeError("Theme is too large (max 30 MB)", code="theme_too_large")
            atomic_write_bytes(folder / rel, data)
        return self.get(theme_id)

    def delete_file(self, theme_id: str, rel: str) -> None:
        folder = self._user_folder(theme_id)
        rel = safe_relpath(rel)
        if rel == MANIFEST:
            raise ThemeError("theme.json cannot be deleted")
        path = folder / rel
        if not path.is_file():
            raise NotFound("No such theme file")
        path.unlink()

    def delete(self, theme_id: str) -> None:
        """Moves the theme to its version history; `restore` brings it back."""
        self._archive(self._user_folder(theme_id))

    # -- sharing ---------------------------------------------------------------------------

    def export(self, theme_id: str) -> tuple[str, bytes]:
        """(file name, zip bytes) of the theme."""
        folder, _ = self._folder(theme_id)
        summary = self._summary(folder, False)
        if "error" in summary:
            raise ThemeError(f"Theme is broken and cannot be exported: {summary['error']}")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for rel, path in _theme_files(folder).items():
                archive.write(path, rel)
        return f"{theme_id}{THEME_FILE_SUFFIX}", buffer.getvalue()

    def import_zip(self, blob: bytes, *, replace: bool = False) -> dict[str, Any]:
        """Install a theme file. An existing user theme with the same id is replaced only with
        `replace=True` (the old one goes to the version history). A theme carrying a built-in id
        is installed as '<id>-custom', so importing it again replaces that copy."""
        manifest, files = read_theme_zip(blob)
        theme_id = manifest["id"]
        if theme_id in self._builtin_ids():
            theme_id = f"{theme_id[:56]}-custom"
            manifest = {**manifest, "id": theme_id}
        existing = self.user_dir / theme_id
        if (existing / MANIFEST).is_file():
            if not replace:
                raise ThemeExists(
                    f"Theme {theme_id!r} is already installed",
                    details={"existing": self.get(theme_id), "incoming": manifest},
                )
            self._archive(existing)
        elif existing.exists():
            self._archive(existing)
        self._write_folder(theme_id, manifest, files)
        return self.get(theme_id)

    # -- history ---------------------------------------------------------------------------

    def _version_dirs(self, theme_id: str) -> list[Path]:
        root = self.user_dir / VERSIONS_DIR / theme_id
        if not root.is_dir():
            return []
        return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)

    def deleted(self) -> list[dict[str, Any]]:
        """Deleted user themes that can still be restored: {id, name, versions}."""
        root = self.user_dir / VERSIONS_DIR
        if not root.is_dir():
            return []
        result = []
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not ID_RE.match(folder.name) or (self.user_dir / folder.name).exists():
                continue
            history = self._version_dirs(folder.name)
            if history:
                latest = self._summary(history[0], False)
                result.append({"id": folder.name, "name": latest.get("name", folder.name), "versions": len(history)})
        return result

    def versions(self, theme_id: str) -> list[dict[str, Any]]:
        if not ID_RE.match(theme_id):
            raise NotFound(f"Theme {theme_id!r} not found")
        result = []
        for folder in self._version_dirs(theme_id):
            summary = self._summary(folder, False)
            result.append({
                "version": folder.name,
                "saved_at": datetime.strptime(folder.name, "%Y%m%dT%H%M%S%f")
                .replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
                "name": summary.get("name", theme_id),
                "theme_version": summary.get("version", ""),
            })
        return result

    def restore(self, theme_id: str, version: str) -> dict[str, Any]:
        """Bring back an archived version; the current one (if any) is archived first."""
        if not ID_RE.match(theme_id):
            raise NotFound(f"Theme {theme_id!r} not found")
        source = next((p for p in self._version_dirs(theme_id) if p.name == version), None)
        if source is None:
            raise NotFound(f"Version {version!r} of theme {theme_id!r} not found")
        current = self.user_dir / theme_id
        if current.exists():
            self._archive(current, keep=source)
        shutil.move(str(source), str(current))
        return self.get(theme_id)

    # -- internals -------------------------------------------------------------------------

    def _archive(self, folder: Path, *, keep: Path | None = None) -> None:
        target = self.user_dir / VERSIONS_DIR / folder.name / _stamp()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(folder), str(target))
        for old in self._version_dirs(folder.name)[KEEP_VERSIONS:]:
            if old != keep:
                shutil.rmtree(old, ignore_errors=True)

    def _write_folder(self, theme_id: str, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
        """Write into a temp folder, then rename: a crash never leaves half a theme in place."""
        self.user_dir.mkdir(parents=True, exist_ok=True)
        staging = self.user_dir / f".staging-{theme_id}-{_stamp()}"
        try:
            for rel, data in files.items():
                if rel == MANIFEST:
                    continue
                path = staging / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            staging.mkdir(parents=True, exist_ok=True)
            (staging / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            staging.rename(self.user_dir / theme_id)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def read_theme_zip(blob: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Validate a theme file and return (manifest, relative path -> bytes)."""
    if len(blob) > MAX_THEME_BYTES:
        raise ThemeError("Theme file is too large (max 30 MB)", code="theme_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise ThemeError("Not a theme file (expected a .tavern-theme / zip archive)") from None
    with archive:
        # Skip OS junk (.DS_Store, __MACOSX); traversal like "../x" is not junk and fails below.
        entries = [
            info for info in archive.infolist()
            if not info.is_dir()
            and not any(
                (p.startswith(".") and p not in (".", "..")) or p == "__MACOSX"
                for p in info.filename.replace("\\", "/").split("/")
            )
        ]
        if len(entries) > MAX_FILES:
            raise ThemeError(f"Theme has too many files (max {MAX_FILES})")
        names = [info.filename.replace("\\", "/") for info in entries]
        prefix = ""
        # Accept a zip of the theme folder itself ("my-theme/theme.json").
        if MANIFEST not in names and names:
            top = names[0].split("/")[0] + "/"
            if all(n.startswith(top) for n in names) and top + MANIFEST in names:
                prefix = top
        files: dict[str, bytes] = {}
        total = 0
        for info, name in zip(entries, names):
            rel = safe_relpath(name[len(prefix):])
            # Sizes in the zip directory can lie (zip bombs): limit what is actually read.
            with archive.open(info) as fh:
                data = fh.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise ThemeError(f"File {rel!r} is too large (max 10 MB)", code="file_too_large")
            total += len(data)
            if total > MAX_THEME_BYTES:
                raise ThemeError("Theme is too large when unpacked (max 30 MB)", code="theme_too_large")
            files[rel] = data
    if MANIFEST not in files:
        raise ThemeError("Theme file has no theme.json")
    try:
        raw = json.loads(files[MANIFEST])
    except ValueError as exc:
        raise ThemeError(f"theme.json is not valid JSON: {exc}") from None
    wanted = raw.get("id") if isinstance(raw, dict) else None
    theme_id = wanted if isinstance(wanted, str) and ID_RE.match(wanted) else slugify(str(raw.get("name", "")) if isinstance(raw, dict) else "")
    manifest = normalize_manifest(raw, theme_id=theme_id)
    for key in ("script", "preview"):
        if manifest[key] and manifest[key] not in files:
            raise ThemeError(f"theme.json names {key} {manifest[key]!r}, but the file is missing")
    return manifest, files
