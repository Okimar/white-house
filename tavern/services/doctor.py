"""The doctor (stage 9): checks the data folder and fixes what it safely can.

Each finding: {id, level: error | warn | info, title, detail, fix: bool}. `quick()` runs at
startup (cheap checks only; the page shows them as a warning), `full()` from the «Копии» tab and
`tavern doctor`. `fix(ids)` takes a copy first and repairs only what has a safe repair:
leftover temporary files, rows that point at deleted rows, a broken active theme, no fresh copy.
A damaged database is never "fixed" in place: the answer is a copy.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from tavern.services.backups import BackupError, leftovers

if TYPE_CHECKING:
    from tavern.context import AppContext

log = logging.getLogger(__name__)

LOW_SPACE = 300 * 1024 * 1024
VERY_LOW_SPACE = 50 * 1024 * 1024
STALE_COPY_DAYS = 2
_ASSET_URL = re.compile(r"/assets/([^\s\"')<>?#]+)")


def _finding(id: str, level: str, title: str, detail: str = "", fix: bool = False) -> dict[str, Any]:
    return {"id": id, "level": level, "title": title, "detail": detail, "fix": fix}


def _size(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} МБ"


def quick(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    result = ctx.db.fetchone("PRAGMA quick_check")[0]
    if result != "ok":
        out.append(_finding("db", "error", "База повреждена",
                            f"{result}. Восстановите последнюю копию на вкладке «Копии»."))
    out += _space(ctx) + _folder(ctx) + _plugins(ctx)
    return out


def full(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    result = ctx.db.fetchone("PRAGMA integrity_check")[0]
    if result != "ok":
        out.append(_finding("db", "error", "База повреждена",
                            f"{result}. Восстановите последнюю копию на вкладке «Копии»."))
    broken = ctx.db.fetchall("PRAGMA foreign_key_check")
    if broken:
        tables = sorted({r[0] for r in broken})
        out.append(_finding("links", "warn", f"Строк со ссылкой на удалённое: {len(broken)}",
                            "Таблицы: " + ", ".join(tables) + ". «Исправить» удалит эти строки.", fix=True))
    out += _assets(ctx) + _themes(ctx) + _plugins(ctx) + _space(ctx) + _folder(ctx) + _copies(ctx)
    junk = leftovers(ctx.paths.root, ctx.backups.folders())
    if junk:
        out.append(_finding("leftovers", "info", f"Остатки прерванных операций: {len(junk)}",
                            ", ".join(p.name for p in junk[:5]), fix=True))
    return out


def _space(ctx: AppContext) -> list[dict[str, Any]]:
    free = shutil.disk_usage(ctx.paths.root).free
    if free < VERY_LOW_SPACE:
        return [_finding("space", "error", "Почти нет места на диске", f"Свободно {_size(free)}.")]
    if free < LOW_SPACE:
        return [_finding("space", "warn", "Мало места на диске", f"Свободно {_size(free)}.")]
    return []


def _folder(ctx: AppContext) -> list[dict[str, Any]]:
    problem = ctx.backups.own_dir_problem()
    return [_finding("folder", "warn", "Своя папка для копий недоступна",
                     problem + ". Копии пока кладутся в папку данных.")] if problem else []


def _plugins(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    for item in ctx.plugins.list():
        if item["changed"]:
            out.append(_finding(f"plugin:{item['id']}", "warn", f"Дополнение «{item['name']}» изменено",
                                "Его файлы поменялись после одобрения, поэтому оно выключено. "
                                "Включите его снова, если изменения ваши."))
        elif item["error"]:
            out.append(_finding(f"plugin:{item['id']}", "warn", f"Дополнение «{item['name']}» не работает",
                                item["error"]))
    return out


def _themes(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    active = ctx.settings.get("theme") or "default"
    for theme in ctx.themes.list():
        if theme.get("error"):
            on = theme["id"] == active
            out.append(_finding(f"theme:{theme['id']}", "warn", f"Тема «{theme['id']}» сломана",
                                theme["error"] + (". Она включена: «Исправить» вернёт обычную тему." if on else ""),
                                fix=on))
    return out


def _text_values(ctx: AppContext) -> list[str]:
    values = []
    tables = [r[0] for r in ctx.db.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        columns = [r[1] for r in ctx.db.fetchall(f'PRAGMA table_info("{table}")') if "TEXT" in (r[2] or "").upper()]
        for column in columns:
            values += [r[0] for r in ctx.db.fetchall(
                f'SELECT "{column}" FROM "{table}" WHERE "{column}" LIKE \'%/assets/%\'')]
    return values


def _assets(ctx: AppContext) -> list[dict[str, Any]]:
    wanted = {unquote(m) for text in _text_values(ctx) for m in _ASSET_URL.findall(text)}
    missing = sorted(name for name in wanted if not (ctx.paths.assets / name).is_file())
    if not missing:
        return []
    return [_finding("assets", "warn", f"Потеряно картинок: {len(missing)}",
                     "На них ссылаются, но файлов нет: " + ", ".join(missing[:5]) + ("…" if len(missing) > 5 else ""))]


def _copies(ctx: AppContext) -> list[dict[str, Any]]:
    settings = ctx.backups.settings()
    out = []
    if ctx.backups.last_error:
        out.append(_finding("copy_error", "warn", "Последняя копия не удалась", ctx.backups.last_error, fix=True))
    if not any(settings[k] for k in ("on_start", "daily", "hourly")):
        return out
    newest = next(iter(ctx.backups.archives()), None)
    if newest is None:
        out.append(_finding("copy_age", "warn", "Копий ещё нет", "«Исправить» сделает копию сейчас.", fix=True))
    elif (datetime.now(timezone.utc) - newest.created_at).days >= STALE_COPY_DAYS:
        days = (datetime.now(timezone.utc) - newest.created_at).days
        out.append(_finding("copy_age", "warn", f"Последней копии {days} дн.",
                            "«Исправить» сделает копию сейчас.", fix=True))
    return out


def fix(ctx: AppContext, ids: list[str]) -> dict[str, Any]:
    """Repair the chosen findings that have a safe repair. A copy is taken first."""
    found = {f["id"]: f for f in full(ctx) if f["fix"]}
    chosen = [i for i in ids if i in found]
    if not chosen:
        return {"fixed": [], "findings": full(ctx)}
    try:
        ctx.backups.create("risky", "doctor-fix", force=True)
        copied = True
    except BackupError as exc:
        if set(chosen) - {"leftovers", "copy_error", "copy_age"}:
            raise BackupError(f"Не исправляю без копии. {exc.message}") from exc
        copied = False
    fixed = []
    for finding_id in chosen:
        if finding_id == "leftovers":
            for path in leftovers(ctx.paths.root, ctx.backups.folders()):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        elif finding_id == "links":
            with ctx.db.transaction() as conn:
                for table, rowid, _parent, _fk in conn.execute("PRAGMA foreign_key_check").fetchall():
                    conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
        elif finding_id.startswith("theme:"):
            ctx.settings.delete("theme")
        elif finding_id in ("copy_age", "copy_error") and not copied:
            continue  # the copy taken above is the repair
        fixed.append(finding_id)
        log.info("Doctor fixed %s", finding_id)
    ctx.notices.emit("backups")
    return {"fixed": fixed, "findings": full(ctx)}
