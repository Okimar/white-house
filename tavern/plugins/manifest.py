"""plugin.json and the plugin's files: what is allowed, the hash the user approves, zip reading.

A plugin is a folder:
    plugin.json   {"id", "name", "version", "author", "description", "homepage",
                   "server": "server.py",   Python file with `def setup(api)` (optional)
                   "web": "web.js",         ES module, `export default function setup(t)` (optional)
                   "style": "style.css",    added to the page while the plugin is on (optional)
                   "migrations": "migrations",   folder of NNNN_name.sql files: the plugin's own tables, every name
                                            starting with p_<id>_ (plugins/schema.py); applied before setup(api)
                   "uses": ["commands", "models", ...],   what it says it does, shown before enabling
                   "settings": [{"key", "label", "type": bool|number|text|choice|connection|model|list,
                                 "default", "choices": [[value, label]], "min", "max", "help",
                                 "multiline": true  text only: a box of several lines
                                 "choices_from": "action"  choice only: the options (a list of [value, label])
                                                    come from the plugin's action of that name
                                 "action": "name"  type "action": a button that calls the plugin's action
                                                    and shows its {"message"} (e.g. «Проверить связь»)
                                 "fields": [...]   list only: the fields of one row (bool/number/text/choice)
                                 "connection_key": "key"  model only: the "connection" setting whose models
                                                    the field offers
                                }]}
"connection" is the id of one of the user's connections ("" = as in the chat); "model" is a model name of the
connection chosen in its `connection_key` setting ("" = that connection's own model; the page shows that model as
the value and offers the service's model list, so one connection serves any of its models); "list" is a list of rows,
each an object with the row fields (e.g. the thinkers of the tree of thoughts). "secret" is a key or
password: written once, kept in data/secrets.json, never returned to the browser (plugins read it with
`api.secret(key)`).
The hash covers every file (names and contents), so any change asks for approval again.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from tavern.errors import AppError

MANIFEST = "plugin.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
USES = ("commands", "messages", "storage", "models", "generator", "events", "ui", "network", "microphone", "secrets")
FIELD_TYPES = ("bool", "number", "text", "choice", "connection", "model", "list", "secret", "action")
ROW_TYPES = ("bool", "number", "text", "choice")
MAX_ROWS = 100
EXTENSIONS = frozenset({
    ".py", ".js", ".mjs", ".css", ".json", ".md", ".txt", ".toml", ".sql",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".woff", ".woff2", ".ttf",
})
# Served to the browser at /plugins/{id}/...: never the Python part.
WEB_EXTENSIONS = EXTENSIONS - {".py", ".toml", ".md", ".txt", ".sql"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PLUGIN_BYTES = 20 * 1024 * 1024
MAX_FILES = 500
MAX_DEPTH = 8
JUNK = frozenset({"__pycache__", "__MACOSX", ".git", ".github", "node_modules"})


class PluginError(AppError):
    code = "plugin_invalid"


def safe_relpath(rel: str) -> str:
    """A path inside a plugin ('server.py', 'web/icons/die.svg'). Rejects traversal and odd files."""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if (not rel or len(rel) > 240 or len(parts) > MAX_DEPTH or ":" in rel
            or any(p in ("", ".", "..") or p.startswith(".") for p in parts)):
        raise PluginError(f"Недопустимый путь файла в дополнении: {rel!r}")
    suffix = Path(parts[-1]).suffix.lower()
    if suffix not in EXTENSIONS:
        raise PluginError(f"Такой файл не может быть в дополнении: {parts[-1]!r}")
    return rel


def _junk(rel: str) -> bool:
    """OS and tool leftovers (.DS_Store, __pycache__, .git). Traversal ("..") is not junk: it fails."""
    parts = rel.replace("\\", "/").split("/")
    return any(p in JUNK or (p.startswith(".") and p not in (".", "..")) for p in parts) or rel.endswith(".pyc")


def plugin_files(folder: Path) -> dict[str, Path]:
    """Relative path -> file, for every file that belongs to the plugin (junk skipped)."""
    files: dict[str, Path] = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if _junk(rel):
            continue
        try:
            files[safe_relpath(rel)] = path
        except PluginError:
            continue
    return files


def files_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for rel, path in plugin_files(folder).items():
        digest.update(rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def secret_name(plugin_id: str, key: str) -> str:
    """Where a plugin's "secret" setting lives in data/secrets.json."""
    return f"plugin.{plugin_id}.{key}"


def _text(raw: dict[str, Any], key: str, limit: int) -> str:
    value = raw.get(key, "")
    return value.strip()[:limit] if isinstance(value, str) else ""


def _field(raw: Any, types: tuple[str, ...] = FIELD_TYPES) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("key"), str) or not raw["key"]:
        raise PluginError("plugin.json: у каждой настройки нужен key")
    kind = raw.get("type", "text")
    if kind not in types:
        raise PluginError(f"plugin.json: тип настройки {kind!r} неизвестен ({', '.join(types)})")
    out: dict[str, Any] = {"key": raw["key"][:64], "label": _text(raw, "label", 200) or raw["key"],
                           "type": kind, "help": _text(raw, "help", 500)}
    if kind == "action":
        if not isinstance(raw.get("action"), str) or not raw["action"]:
            raise PluginError(f"plugin.json: у кнопки {out['key']!r} нет action")
        out["action"] = raw["action"][:64]
    if kind == "choice" and isinstance(raw.get("choices_from"), str) and raw["choices_from"]:
        out["choices_from"] = raw["choices_from"][:64]
        out["choices"] = []
    elif kind == "choice":
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise PluginError(f"plugin.json: у настройки {out['key']!r} нет choices")
        out["choices"] = [[str(c[0]), str(c[1])] if isinstance(c, list) and len(c) == 2 else [str(c), str(c)]
                          for c in choices]
    if kind == "model" and isinstance(raw.get("connection_key"), str) and raw["connection_key"]:
        out["connection_key"] = raw["connection_key"][:64]
    if kind == "text" and raw.get("multiline") is True:
        out["multiline"] = True  # shown as a box of several lines
    if kind == "list":
        rows = raw.get("fields")
        if not isinstance(rows, list) or not rows:
            raise PluginError(f"plugin.json: у списка {out['key']!r} нет fields")
        out["fields"] = [_field(f, ROW_TYPES) for f in rows]
    if kind == "number":
        for bound in ("min", "max"):
            if isinstance(raw.get(bound), (int, float)) and not isinstance(raw.get(bound), bool):
                out[bound] = raw[bound]
    default = raw.get("default")
    try:
        out["default"] = coerce(out, default) if default is not None else _empty(kind, out)
    except PluginError:
        out["default"] = _empty(kind, out)
    return out


def _empty(kind: str, field: dict[str, Any]) -> Any:
    if kind == "list":
        return []
    if kind == "choice":
        return field["choices"][0][0] if field.get("choices") else ""
    return {"bool": False, "number": 0, "text": "", "connection": "", "model": "", "secret": "", "action": ""}.get(kind, "")


def coerce(field: dict[str, Any], value: Any) -> Any:
    kind = field["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise PluginError(f"{field['label']}: да или нет")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PluginError(f"{field['label']}: нужно число")
        if "min" in field and value < field["min"] or "max" in field and value > field["max"]:
            raise PluginError(f"{field['label']}: от {field.get('min', '…')} до {field.get('max', '…')}")
        return value
    if kind == "list":
        if not isinstance(value, list) or len(value) > MAX_ROWS:
            raise PluginError(f"{field['label']}: нужен список (до {MAX_ROWS} строк)")
        rows = []
        for row in value:
            if not isinstance(row, dict):
                raise PluginError(f"{field['label']}: каждая строка — объект")
            rows.append({f["key"]: coerce(f, row[f["key"]]) if f["key"] in row else f["default"]
                         for f in field["fields"]})
        return rows
    if not isinstance(value, str):
        raise PluginError(f"{field['label']}: нужен текст")
    if kind == "action":
        raise PluginError(f"{field['label']}: это кнопка, у неё нет значения")
    if kind == "choice" and not field.get("choices_from") and value not in [c[0] for c in field["choices"]]:
        raise PluginError(f"{field['label']}: нет такого варианта")
    return value[:5000]


def check_settings(fields: list[dict[str, Any]], raw: Any, current: dict[str, Any]) -> dict[str, Any]:
    """Saved settings after applying `raw` (a partial object) to `current`."""
    if not isinstance(raw, dict):
        raise PluginError("Настройки дополнения: нужен объект")
    known = {f["key"]: f for f in fields}
    out = {k: v for k, v in current.items() if k in known}
    for key, value in raw.items():
        if key not in known:
            raise PluginError(f"У дополнения нет настройки {key!r}")
        out[key] = coerce(known[key], value)
    return out


def with_defaults(fields: list[dict[str, Any]], saved: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: saved.get(f["key"], f["default"]) for f in fields}


def normalize(raw: Any, *, folder_id: str | None = None) -> dict[str, Any]:
    """A clean manifest. `folder_id`: the folder name, which must match the id (when given)."""
    if not isinstance(raw, dict):
        raise PluginError("plugin.json должен быть объектом")
    plugin_id = raw.get("id")
    if not isinstance(plugin_id, str) or not ID_RE.match(plugin_id):
        raise PluginError("plugin.json: id — латинские буквы, цифры, «-» и «_» (до 48 знаков)")
    if folder_id is not None and plugin_id != folder_id:
        raise PluginError(f"plugin.json: id {plugin_id!r} не совпадает с папкой {folder_id!r}")
    out: dict[str, Any] = {
        "id": plugin_id,
        "name": _text(raw, "name", 80) or plugin_id,
        "version": _text(raw, "version", 40) or "0",
        "author": _text(raw, "author", 120),
        "description": _text(raw, "description", 2000),
        "homepage": _text(raw, "homepage", 300),
    }
    for key, suffixes in (("server", (".py",)), ("web", (".js", ".mjs")), ("style", (".css",))):
        value = raw.get(key)
        if value in (None, ""):
            out[key] = ""
            continue
        if not isinstance(value, str) or not value.lower().endswith(suffixes):
            raise PluginError(f"plugin.json: {key} — путь к файлу {'/'.join(suffixes)}")
        out[key] = safe_relpath(value)
    migrations = raw.get("migrations", "")
    if migrations in (None, ""):
        out["migrations"] = ""
    elif isinstance(migrations, str) and len(migrations) <= 100 and re.fullmatch(r"[a-z0-9_-]+(/[a-z0-9_-]+)*", migrations):
        out["migrations"] = migrations
    else:
        raise PluginError("plugin.json: migrations — имя папки (латинские буквы, цифры, «-» и «_»)")
    uses = raw.get("uses", [])
    out["uses"] = [u for u in USES if isinstance(uses, list) and u in uses]
    fields = raw.get("settings", [])
    if not isinstance(fields, list):
        raise PluginError("plugin.json: settings — список")
    out["settings"] = [_field(f) for f in fields[:50]]
    if len({f["key"] for f in out["settings"]}) != len(out["settings"]):
        raise PluginError("plugin.json: ключи настроек повторяются")
    connections = {f["key"] for f in out["settings"] if f["type"] == "connection"}
    for f in out["settings"]:
        if f.get("connection_key") and f["connection_key"] not in connections:
            raise PluginError(f"plugin.json: у настройки {f['key']!r} connection_key не указывает на настройку-подключение")
    return out


def read_manifest(folder: Path) -> dict[str, Any]:
    try:
        raw = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except OSError as exc:
        raise PluginError(f"Не читается plugin.json: {exc}") from None
    except ValueError as exc:
        raise PluginError(f"plugin.json — не JSON: {exc}") from None
    manifest = normalize(raw, folder_id=folder.name)
    for key in ("server", "web", "style"):
        if manifest[key] and not (folder / manifest[key]).is_file():
            raise PluginError(f"plugin.json называет {key} {manifest[key]!r}, но файла нет")
    if manifest["migrations"] and not (folder / manifest["migrations"]).is_dir():
        raise PluginError(f"plugin.json называет папку миграций {manifest['migrations']!r}, но её нет")
    return manifest


def read_zip(blob: bytes, *, subdir: str = "") -> tuple[dict[str, Any], dict[str, bytes]]:
    """(manifest, relative path -> bytes) from a zip. The plugin may sit in a folder of the zip (a
    GitHub archive: "repo-sha/plugin.json"); `subdir` picks one inside it ("plugins/dice")."""
    if len(blob) > MAX_PLUGIN_BYTES:
        raise PluginError("Файл дополнения слишком большой (до 20 МБ)", code="plugin_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise PluginError("Это не дополнение: ожидался zip-архив") from None
    with archive:
        entries = [i for i in archive.infolist() if not i.is_dir() and not _junk(i.filename)]
        names = [i.filename.replace("\\", "/") for i in entries]
        manifests = sorted((n for n in names if n == MANIFEST or n.endswith("/" + MANIFEST)),
                           key=lambda n: n.count("/"))
        subdir = subdir.strip("/")
        if subdir:
            manifests = [n for n in manifests if n[: -len(MANIFEST)].rstrip("/").endswith(subdir)]
        if not manifests:
            raise PluginError("В архиве нет plugin.json" + (f" в папке {subdir!r}" if subdir else ""))
        prefix = manifests[0][: -len(MANIFEST)]
        chosen = [(i, n[len(prefix):]) for i, n in zip(entries, names) if n.startswith(prefix)]
        if len(chosen) > MAX_FILES:
            raise PluginError(f"В дополнении слишком много файлов (до {MAX_FILES})")
        files: dict[str, bytes] = {}
        total = 0
        for info, rel in chosen:
            try:
                rel = safe_relpath(rel)
            except PluginError:
                if rel.lower().endswith((".py", ".js", ".mjs", ".css", ".json")):
                    raise
                continue  # a licence picture or a build file: not part of the plugin
            with archive.open(info) as fh:  # sizes in the zip directory can lie: read with a cap
                data = fh.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise PluginError(f"Файл {rel!r} слишком большой (до 5 МБ)", code="plugin_too_large")
            total += len(data)
            if total > MAX_PLUGIN_BYTES:
                raise PluginError("Дополнение слишком большое (до 20 МБ)", code="plugin_too_large")
            files[rel] = data
    try:
        raw = json.loads(files[MANIFEST])
    except ValueError as exc:
        raise PluginError(f"plugin.json — не JSON: {exc}") from None
    manifest = normalize(raw)
    for key in ("server", "web", "style"):
        if manifest[key] and manifest[key] not in files:
            raise PluginError(f"plugin.json называет {key} {manifest[key]!r}, но файла нет")
    return manifest, files
