"""Command line: `python -m tavern [command]`. With no command, starts the server."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
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

    safe = sub.add_parser("safe-mode", help="safe mode: the page starts without themes, styles and plugin parts")
    safe.add_argument("state", choices=["on", "off", "status"],
                      help="on / off stay until changed (a running server notices on the next page load)")

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
    parser.add_argument("--safe", action="store_true",
                        help="safe mode for this run only: no themes, custom styles or plugin parts in the page")
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
        if args.command == "safe-mode":
            return cmd_safe_mode(args)
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
    from tavern.config import with_server_overrides
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
        # The data dir lock above already refused a second copy of ourselves on this data, so whoever
        # holds the port is someone else (SillyTavern also defaults to 8000): step aside, don't fail.
        wanted = ctx.config.server.port
        port = _free_port(ctx.config.server.host, wanted)
        if port != wanted:
            print(f"Порт {wanted} занят другой программой, White House запускается на порту {port}.",
                  file=sys.stderr, flush=True)
            # Through the config, because the Google Drive sign-in builds its address from it.
            ctx.config = with_server_overrides(ctx.config, port=port)
        ctx.plugins.start()
        from tavern.services import look
        from tavern.services import presets as preset_service

        preset_service.migrate_behaviour_blocks(ctx)  # texts of older presets become blocks (once)

        if look.check_upgrade(ctx):  # the interface changed: the custom look is off, the page says so
            print("Интерфейс обновился: свои темы и стили отключены (ничего не удалено). "
                  "Вернуть можно на странице.", file=sys.stderr, flush=True)
        if not args.sandbox:  # a sandbox is thrown away: copies of it would only fill the real disk
            ctx.backups.start_scheduler()
        ctx.gdrive.start()  # a sandbox has its own sign-in (none until made there): safe to run
        app = create_app(ctx, safe_mode=args.safe)
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
            timeout_graceful_shutdown=2,
        )
        server = _make_server(config, app.reaper)
        from tavern.services import updates

        # «Обновить» on the page ends the run this way; start.sh / start.bat then start it again (ADR 0016).
        updates.install_exit_hook(lambda: setattr(server, "should_exit", True))
        url = _local_url(server_cfg.host, server_cfg.port)
        # After an update the script starts us again: the page is already open and reconnects by itself.
        reopen = server_cfg.open_browser and os.environ.get("TAVERN_RESTARTED") != "1"
        threading.Thread(
            target=_announce,
            args=(server, url, ctx, started, reopen),
            daemon=True,
        ).start()
        server.run()
        if updates.exit_requested() and updates.supervised():
            return updates.RESTART_CODE  # the database and the data lock are released in `finally` below
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


def _make_server(config, reaper):
    """uvicorn's server that first ends the live event streams of open pages, so Ctrl+C exits at once
    instead of waiting for them ("Waiting for connections to close")."""
    import uvicorn

    class QuickExitServer(uvicorn.Server):
        async def shutdown(self, sockets=None) -> None:  # type: ignore[no-untyped-def]
            await reaper.close_all()
            await super().shutdown(sockets)

    return QuickExitServer(config)


def _port_busy(host: str, port: int) -> bool:
    """Does anything listen there? Connecting, not binding: a bind test also fails for a few
    minutes after our own restart (TIME_WAIT), and that port is really free."""
    probe = "127.0.0.1" if host in {"0.0.0.0", "", "localhost"} else "::1" if host == "::" else host
    try:
        with socket.create_connection((probe, port), timeout=0.5):
            return True
    except OSError:
        return False


def _free_port(host: str, port: int, tries: int = 50) -> int:
    """The wanted port, or the nearest free one above it."""
    for candidate in range(port, min(port + tries, 65536)):
        if not _port_busy(host, candidate):
            return candidate
    raise TavernError(f"Порты {port}-{port + tries - 1} заняты. Укажите другой: --port НОМЕР")


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


def cmd_safe_mode(args: argparse.Namespace) -> int:
    """Safe mode on / off / status. Works with the server stopped or running (no data lock taken):
    a running server hands the state to the page on its next load."""
    from tavern.context import open_context
    from tavern.services import look

    ctx = open_context(DataPaths(args.data_dir.resolve()), load_plugins=False)
    try:
        if args.state != "status":
            look.set_safe_mode(ctx, args.state == "on")
        on = bool(ctx.settings.get(look.KEY_SAFE))
    finally:
        ctx.close()
    if on:
        print("Безопасный режим ВКЛЮЧЁН: страница открывается без тем, своих стилей и скриптов.\n"
              "Выключить: python -m tavern safe-mode off (или кнопка на странице).")
    else:
        print("Безопасный режим выключен.\nВключить: python -m tavern safe-mode on "
              "(на один запуск: python -m tavern --safe).")
    return 0


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
