"""Updating the app from the page (ADR 0016).

The app is installed by `get.sh` (a git clone), so an update is: fetch, then a fast-forward merge of the
branch's upstream. Nothing else is ever done to the code: no merge commits, no resets of the owner's work,
no passing of user input to git (the commands are fixed lists, run without a shell, and the page sends none).

`check` looks (a fetch, nothing changed) and says in plain Russian whether there is a newer version and what
changed, or why an update is not possible. `apply` refuses on the same reasons, makes a whole-data copy,
fast-forwards, installs requirements when `requirements.txt` changed, tries to import the new code and puts
the old commit back if any step fails. Then the process leaves with RESTART_CODE; `start.sh` / `start.bat`
start it again on the same address (they set TAVERN_SUPERVISED). Started any other way, the app just stops and
the page says so.

Remote addresses are never shown or logged: git's own messages are scrubbed of anything like `user:key@`.
"""

from __future__ import annotations

import logging
import os
import re
import secrets as _secrets
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tavern import __version__
from tavern.context import AppContext
from tavern.errors import Conflict
from tavern.paths import REPO_ROOT

log = logging.getLogger(__name__)

RESTART_CODE = 75  # the exit code that asks start.sh / start.bat to start the app again
BOOT_ID = _secrets.token_hex(8)  # changes with every start: the page waits for a different one after an update
FETCH_TIMEOUT = 90
GIT_TIMEOUT = 30
PIP_TIMEOUT = 900
MAX_CHANGES = 60

_lock = threading.Lock()
_exit: dict[str, Any] = {"hook": None, "requested": False}

HOW_TO_UPDATE_BY_HAND = ("Обновите вручную: скачайте свежую версию и распакуйте поверх старой (папка data с вашими "
                         "данными остаётся), либо установите заново командой из описания на GitHub (get.sh), "
                         "она обновляет установку и не трогает данные.")


# --- the process leaving -----------------------------------------------------------------------


def install_exit_hook(hook: Callable[[], None] | None) -> None:
    """The server (cli.py) gives the way to stop it."""
    _exit["hook"] = hook
    _exit["requested"] = False


def exit_requested() -> bool:
    return bool(_exit["requested"])


def supervised() -> bool:
    return os.environ.get("TAVERN_SUPERVISED") == "1"


def request_exit() -> bool:
    """Stop the server (after an update). False when nothing can be stopped (tests, embedded runs)."""
    hook = _exit["hook"]
    if hook is None:
        return False
    _exit["requested"] = True
    hook()
    return True


# --- git ---------------------------------------------------------------------------------------

_URL_AUTH = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")


def scrub(text: str) -> str:
    """Drop `user:token@` from any address in a message before it is shown or logged."""
    return _URL_AUTH.sub(r"\1", text or "")


class _Git:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.exe = shutil.which("git")
        self.env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "LC_ALL": "C",
                    "GIT_ASKPASS": "", "SSH_ASKPASS": ""}

    def run(self, *args: str, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess[str]:
        assert self.exe
        return subprocess.run([self.exe, *args], cwd=self.root, env=self.env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)

    def out(self, *args: str) -> str:
        done = self.run(*args)
        return done.stdout.strip() if done.returncode == 0 else ""


def _is_git_install(root: Path) -> bool:
    return (root / ".git").exists()


def _info(git: _Git, ref: str = "HEAD") -> dict[str, str]:
    line = git.out("log", "-1", "--format=%h%x09%cs%x09%s", ref)
    sha, _, rest = line.partition("\t")
    date, _, subject = rest.partition("\t")
    return {"commit": sha, "date": date, "subject": subject}


def info(root: Path | None = None) -> dict[str, Any]:
    """What is installed, without any network: the page shows it before «Проверить»."""
    root = root or REPO_ROOT
    out: dict[str, Any] = {"version": __version__, "boot": BOOT_ID, "git": False, "supervised": supervised(),
                           "commit": "", "date": "", "subject": ""}
    if _is_git_install(root) and shutil.which("git"):
        git = _Git(root)
        out["git"] = True
        try:
            out |= _info(git)
        except (OSError, subprocess.SubprocessError):
            out["git"] = False
    return out


def _busy(ctx: AppContext) -> bool:
    return bool(ctx.runs.running())


def check(ctx: AppContext, root: Path | None = None) -> dict[str, Any]:
    """Look for a newer version (one fetch) and say whether and why an update can or cannot be done."""
    root = root or REPO_ROOT
    result: dict[str, Any] = {"state": "error", "current": info(root), "behind": 0, "ahead": 0, "changes": [],
                              "can_update": False, "reason": "", "code": "", "how": ""}

    def stop(state: str, code: str, reason: str, how: str = "") -> dict[str, Any]:
        return result | {"state": state, "code": code, "reason": reason, "how": how}

    if not _is_git_install(root):
        return stop("unsupported", "not_git", "Это не установка через git (похоже на скачанный архив), "
                    "поэтому обновить одной кнопкой нельзя.", HOW_TO_UPDATE_BY_HAND)
    git = _Git(root)
    if git.exe is None:
        return stop("unsupported", "no_git", "На этом устройстве не найдена программа git, без неё кнопка не работает.",
                    "Установите git (в Termux: pkg install git) или обновите вручную. " + HOW_TO_UPDATE_BY_HAND)
    try:
        branch = git.out("rev-parse", "--abbrev-ref", "HEAD")
        if not branch or branch == "HEAD":
            return stop("blocked", "detached", "Установка стоит не на ветке (открыта отдельная версия), "
                        "автоматически обновлять её нельзя.", HOW_TO_UPDATE_BY_HAND)
        if not git.out("rev-parse", "--abbrev-ref", "@{u}"):
            return stop("blocked", "no_upstream", "У установки нет ветки на сервере, с которой сверяться.",
                        HOW_TO_UPDATE_BY_HAND)
        fetched = git.run("fetch", "--quiet", timeout=FETCH_TIMEOUT)
        if fetched.returncode != 0:
            log.info("Update check: fetch failed: %s", scrub(fetched.stderr).strip()[-300:])
            return stop("error", "no_network", "Не удалось связаться с сервером обновлений. Проверьте интернет и "
                        "попробуйте ещё раз.")
        head = git.out("rev-parse", "HEAD")
        upstream = git.out("rev-parse", "@{u}")
        result["current"] = info(root)
        if head == upstream:
            return result | {"state": "up_to_date"}
        fast = git.run("merge-base", "--is-ancestor", "HEAD", "@{u}").returncode == 0
        if not fast:
            ahead_only = git.run("merge-base", "--is-ancestor", "@{u}", "HEAD").returncode == 0
            if ahead_only:
                result["ahead"] = int(git.out("rev-list", "--count", "@{u}..HEAD") or 0)
                return result | {"state": "up_to_date"}
            return stop("blocked", "diverged", "Ваша копия и сервер разошлись: в ней есть свои изменения кода, "
                        "которых нет на сервере, и наоборот. Автоматически объединять их опасно.",
                        "Нужна ручная работа с git, либо поставьте чистую копию через get.sh в другую папку и перенесите "
                        "папку data.")
        lines = git.out("log", f"--max-count={MAX_CHANGES}", "--format=%h%x09%s", "HEAD..@{u}").splitlines()
        result["changes"] = [{"commit": a, "subject": b} for a, _, b in (line.partition("\t") for line in lines)]
        result["behind"] = int(git.out("rev-list", "--count", "HEAD..@{u}") or len(lines))
        result["latest"] = _info(git, "@{u}")
        result["state"] = "available"
        blocked = _blocker(ctx, git)
        if blocked:
            result |= {"code": blocked[0], "reason": blocked[1], "state": "blocked"}
        else:
            result["can_update"] = True
        return result
    except subprocess.TimeoutExpired:
        return stop("error", "timeout", "Git не ответил вовремя. Попробуйте ещё раз.")
    except OSError as exc:
        return stop("error", "git_failed", f"Не удалось запустить git: {scrub(str(exc))}")


def _blocker(ctx: AppContext, git: _Git) -> tuple[str, str] | None:
    if git.out("status", "--porcelain", "--untracked-files=no"):
        return ("dirty", "В файлах программы есть ваши правки (изменённые файлы). Обновление затёрло бы их, "
                "поэтому оно не выполняется. Верните файлы к исходному виду или отложите правки, и повторите.")
    if _busy(ctx):
        return ("busy", "Сейчас идёт работа: пишется ответ или работает помощник. Дождитесь конца или остановите её.")
    return None


# --- the update --------------------------------------------------------------------------------


def _requirements_installed_marker() -> Path | None:
    marker = Path(sys.prefix) / "requirements.installed"
    return marker if (Path(sys.prefix) / "pyvenv.cfg").exists() else None


def _install_requirements(root: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--only-binary=:all:",
         "-r", "requirements.txt"], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=PIP_TIMEOUT, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        log.warning("Update: pip failed: %s", scrub(done.stderr).strip()[-500:])
        raise RuntimeError("Не удалось установить нужные библиотеки (проверьте интернет)")
    marker = _requirements_installed_marker()
    if marker is not None:
        try:
            shutil.copyfile(root / "requirements.txt", marker)
        except OSError:
            pass


def _imports(root: Path) -> bool:
    """Does the new code at least load? A fresh interpreter in the project folder (not this process)."""
    done = subprocess.run([sys.executable, "-c", "import tavern.app, tavern.cli"], cwd=root, capture_output=True,
                          text=True, timeout=120, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        log.warning("Update: the new code does not load: %s", scrub(done.stderr).strip()[-500:])
    return done.returncode == 0


def apply(ctx: AppContext, root: Path | None = None) -> dict[str, Any]:
    """Update to the newest version. Raises Conflict (plain reason) and changes nothing when it cannot."""
    root = root or REPO_ROOT
    if not _lock.acquire(blocking=False):
        raise Conflict("Обновление уже идёт.", code="busy")
    try:
        found = check(ctx, root)
        if found["state"] == "up_to_date":
            raise Conflict("Установлена последняя версия, обновлять нечего.", code="up_to_date")
        if not found["can_update"]:
            raise Conflict((found["reason"] + (" " + found["how"] if found["how"] else "")).strip(),
                           code=found["code"] or "cannot_update")
        git = _Git(root)
        old = git.out("rev-parse", "HEAD")
        try:
            copy = ctx.backups.create("risky", "перед обновлением программы", force=True)
        except Exception as exc:  # noqa: BLE001 - no copy, no update
            log.warning("Update: no copy made: %s", exc)
            raise Conflict("Не удалось сделать копию данных перед обновлением, поэтому ничего не изменено. "
                           "Проверьте место на диске.", code="no_copy") from exc
        merged = git.run("merge", "--ff-only", "--quiet", "@{u}", timeout=120)
        if merged.returncode != 0:
            log.warning("Update: fast-forward refused: %s", scrub(merged.stderr).strip()[-500:])
            raise Conflict("Git не смог обновить файлы, ничего не изменено.", code="merge_failed")
        new = git.out("rev-parse", "HEAD")
        try:
            changed = git.out("diff", "--name-only", old, new, "--", "requirements.txt")
            if changed:
                _install_requirements(root)
            if not _imports(root):
                raise RuntimeError("Новая версия не запускается")
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            git.run("reset", "--hard", "--quiet", old)  # the tree was clean and the move a fast-forward: safe
            raise Conflict(f"{exc}. Программа возвращена к прежней версии, ваши данные не тронуты.",
                           code="update_failed") from exc
        log.info("Updated %s -> %s", old[:8], new[:8])
        return {"updated": True, "from": _info(git, old), "to": _info(git, new), "copy": (copy or {}).get("name", ""),
                "changes": found["changes"], "restart": "auto" if supervised() else "manual"}
    finally:
        _lock.release()
