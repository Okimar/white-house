"""Command line: `python -m tavern [command]`. With no command, starts the server."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from tavern import __version__
from tavern.errors import TavernError
from tavern.paths import DEFAULT_DATA_DIR, DataPaths

log = logging.getLogger("tavern")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tavern", description="Tavern roleplay chat server")
    parser.add_argument("--version", action="version", version=f"tavern {__version__}")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("TAVERN_DATA_DIR", DEFAULT_DATA_DIR)),
        help="data directory (default: ./data or $TAVERN_DATA_DIR)",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the server (default)")
    _add_serve_args(serve)

    backup = sub.add_parser("backup", help="copies of the whole data folder (without API keys)")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    create = backup_sub.add_parser("create", help="take a copy now")
    create.add_argument("--reason", default="manual")
    backup_sub.add_parser("list", help="list copies, newest first")
    restore = backup_sub.add_parser("restore", help="put a copy back (server stopped)")
    restore.add_argument("name", help="copy file name from `backup list`")

    doctor = sub.add_parser("doctor", help="check the data folder")
    doctor.add_argument("--fix", action="store_true", help="repair what can be repaired safely (after a copy)")

    token = sub.add_parser("token", help="network access token")
    token.add_argument("--rotate", action="store_true", help="generate a new token")

    sub.add_parser("check", help="verify the data directory and database, then exit")
    return parser


def _add_serve_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", help="bind address (0.0.0.0 = local network, token required)")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="run on a temporary copy of the data directory; real data is never modified",
    )
    parser.add_argument("--keep-sandbox", action="store_true", help="do not delete the sandbox")
    parser.add_argument("--no-plugins", action="store_true",
                        help="start without plugins (when one of them breaks the start)")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    # A bare `tavern --port 9000` means `tavern serve --port 9000`.
    head, tail = _split_global(argv)
    if not tail or (tail[0].startswith("-") and tail[0] not in {"-h", "--help", "--version"}):
        argv = [*head, "serve", *tail]
    args = parser.parse_args(argv)

    from tavern.logs import setup_logging

    setup_logging("info")
    try:
        if args.command == "backup":
            return cmd_backup(args)
        if args.command == "token":
            return cmd_token(args)
        if args.command == "check":
            return cmd_check(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        return cmd_serve(args)
    except TavernError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


def _split_global(argv: list[str]) -> tuple[list[str], list[str]]:
    """Separate `--data-dir X` (a global option) from serve options."""
    head: list[str] = []
    tail: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--data-dir" and i + 1 < len(argv):
            head += argv[i : i + 2]
            i += 2
            continue
        if arg.startswith("--data-dir="):
            head.append(arg)
        else:
            tail.append(arg)
        i += 1
    return head, tail


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from tavern.app import create_app
    from tavern.context import open_context
    from tavern.logs import setup_logging
    from tavern.sandbox import make_sandbox, remove_sandbox
    from tavern.util.lock import DirLock

    started = time.perf_counter()
    real = DataPaths(args.data_dir.resolve())
    paths = make_sandbox(real) if args.sandbox else real
    lock = DirLock(paths.lock)
    lock.acquire()
    ctx = None
    try:
        overrides = {"host": args.host, "port": args.port}
        if args.no_browser:
            overrides["open_browser"] = False
        ctx = open_context(paths, server_overrides=overrides, sandbox=args.sandbox,
                           load_plugins=not args.no_plugins)
        setup_logging(ctx.config.log_level, paths.logs / "tavern.log")
        busy_url = _already_running(ctx.config.server.host, ctx.config.server.port)
        if busy_url:
            print(f"White House уже запущен: {busy_url}\n"
                  "Откройте этот адрес в браузере (или другой порт: --port 8001).", file=sys.stderr)
            if ctx.config.server.open_browser:
                open_url(busy_url)
            return 0
        ctx.plugins.start()
        if not args.sandbox:  # a sandbox is thrown away: copies of it would only fill the real disk
            ctx.backups.start_scheduler()
        ctx.gdrive.start()  # a sandbox has its own sign-in (none until made there): safe to run
        app = create_app(ctx)
        server_cfg = ctx.config.server
        config = uvicorn.Config(
            app,
            host=server_cfg.host,
            port=server_cfg.port,
            loop="asyncio",  # uvloop needs a C build; asyncio is plenty for one user
            http="h11",
            ws="none",
            lifespan="off",
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=3,
        )
        server = uvicorn.Server(config)
        url = _local_url(server_cfg.host, server_cfg.port)
        threading.Thread(
            target=_announce,
            args=(server, url, ctx, started, server_cfg.open_browser),
            daemon=True,
        ).start()
        server.run()
        return 0 if server.started else 1
    finally:
        if ctx is not None:
            ctx.close()
        lock.release()
        if args.sandbox:
            if args.keep_sandbox:
                log.info("Sandbox kept at %s", paths.root)
            else:
                remove_sandbox(paths)


def _already_running(host: str, port: int) -> str | None:
    """The address if something already listens on it (most likely our own earlier start)."""
    import socket

    url = _local_url(host, port)
    probe = "127.0.0.1" if host in {"0.0.0.0", "", "localhost"} else "::1" if host == "::" else host
    try:
        with socket.create_connection((probe, port), timeout=0.5):
            return url
    except OSError:
        return None


def _local_url(host: str, port: int) -> str:
    shown = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    if ":" in shown:
        shown = f"[{shown}]"
    return f"http://{shown}:{port}/"


def _announce(server, url: str, ctx, started: float, open_browser: bool) -> None:
    deadline = time.monotonic() + 30
    while not server.started:
        if server.should_exit or time.monotonic() > deadline:
            return
        time.sleep(0.02)
    log.info("Tavern %s ready in %.2fs: %s", __version__, time.perf_counter() - started, url)
    if ctx.sandbox:
        log.warning("SANDBOX MODE: changes go to %s and are discarded on exit", ctx.paths.root)
    if ctx.config.remote_enabled:
        # The address with the token goes to the screen only: the log file keeps no secrets.
        log.warning("Remote access is ON. The address for other devices is shown on the screen. "
                    "Rotate the token with `python -m tavern token --rotate`.")
        print(f"Open {url}login?token={ctx.secrets.access_token()} on the other device.", file=sys.stderr, flush=True)
    if open_browser:
        open_url(url)


def open_url(url: str) -> None:
    """Open the UI. Termux has no desktop browser hook, so use termux-open-url there."""
    termux_open = shutil.which("termux-open-url")
    try:
        if termux_open:
            subprocess.Popen([termux_open, url])
        else:
            webbrowser.open(url)
    except OSError as exc:
        log.info("Could not open a browser (%s); open %s manually", exc, url)


def cmd_backup(args: argparse.Namespace) -> int:
    """Whole-data copies (stage 9). Listing and taking a copy work while the server runs."""
    from tavern.context import open_context
    from tavern.util.lock import DirLock

    paths = DataPaths(args.data_dir.resolve())
    if args.backup_command in ("list", "create"):
        ctx = open_context(paths, load_plugins=False)
        try:
            if args.backup_command == "list":
                items = ctx.backups.list()
                if not items:
                    print("No backups.")
                for item in items:
                    print(f"{item['name']}  {item['size'] / 1024:.0f} KiB")
            else:
                made = ctx.backups.create("manual", args.reason if args.reason != "manual" else "", force=True)
                assert made is not None
                print(made["name"])
                if made["warning"]:
                    log.warning("%s", made["warning"])
        finally:
            ctx.close()
        return 0
    with DirLock(paths.lock):  # restore only while the server is stopped (the page restores live)
        ctx = open_context(paths, load_plugins=False)
        try:
            done = ctx.backups.restore(args.name)
        finally:
            ctx.close()
    print(f"Restored {args.name}." + (f" Previous state saved as {done['safety']}." if done["safety"] else ""))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check the data folder; `--fix` repairs what has a safe repair (after a copy)."""
    from tavern.context import open_context
    from tavern.services import doctor
    from tavern.util.lock import DirLock

    paths = DataPaths(args.data_dir.resolve())
    with DirLock(paths.lock):
        ctx = open_context(paths, load_plugins=False)
        try:
            findings = doctor.full(ctx)
            later: list[str] = []
            if args.fix:
                done = doctor.fix(ctx, [f["id"] for f in findings if f["fix"]])
                findings, later = done["findings"], done["later"]
        finally:
            ctx.close()
    marks = {"error": "✗", "warn": "!", "info": "·"}
    for f in findings:
        print(f"{marks[f['level']]} {f['title']}" + (f" — {f['detail']}" if f["detail"] else ""))
    if not findings:
        print("✓ Всё в порядке")
    elif not args.fix and any(f["fix"] for f in findings):
        print("Исправить: tavern doctor --fix")
    if later:
        print("Пузыри без ключевых слов исправляет модель: «Настройки → Копии» → «Исправить» на странице")
    return 1 if any(f["level"] == "error" for f in findings) else 0


def cmd_token(args: argparse.Namespace) -> int:
    from tavern.secret_store import SecretStore

    paths = DataPaths(args.data_dir.resolve())
    paths.ensure()
    store = SecretStore(paths.secrets)
    token = store.rotate_access_token() if args.rotate else store.access_token()
    print(token)
    if args.rotate:
        print("Token rotated. Restart the server and log in again on remote devices.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Quick health check used by install scripts and agents."""
    from tavern.context import open_context
    from tavern.util.lock import DirLock

    paths = DataPaths(args.data_dir.resolve())
    with DirLock(paths.lock):
        ctx = open_context(paths)
        try:
            integrity = ctx.db.fetchone("PRAGMA integrity_check")[0]
            print(f"tavern {__version__}, python {sys.version.split()[0]}")
            print(f"data dir: {paths.root}")
            print(f"schema: v{ctx.db.schema_version}, integrity: {integrity}")
        finally:
            ctx.close()
    return 0 if integrity == "ok" else 1
