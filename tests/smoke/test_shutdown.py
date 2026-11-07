"""Ctrl+C with a page open must end the process at once (no "Waiting for connections to close").

Real server in a subprocess, a live event stream open, the interrupt sent the way a terminal does.
On Windows a console Ctrl+C cannot be aimed at one child, so Ctrl+Break to its own process group
stands in for it (uvicorn handles both the same way); elsewhere it is SIGINT.
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time

import httpx

from tavern.paths import REPO_ROOT, DataPaths
from tavern.util.lock import DirLock

BOUND_SECONDS = 1.5  # it used to take 3+ seconds (uvicorn's graceful timeout)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_interrupt_with_open_event_streams_exits_promptly_and_frees_the_data(tmp_path):
    port = _free_port()
    data = tmp_path / "data"
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "tavern", "--data-dir", str(data), "--port", str(port), "--no-browser"],
        cwd=REPO_ROOT, env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags,
    )
    base = f"http://127.0.0.1:{port}"
    opened = threading.Event()

    def hold_stream(path: str) -> None:
        try:
            with httpx.stream("GET", base + path, timeout=None, trust_env=False) as reply:
                for _ in reply.iter_raw():
                    opened.set()
        except httpx.HTTPError:
            pass  # the server went away: exactly what the test wants

    try:
        deadline = time.monotonic() + 20
        with httpx.Client(trust_env=False, timeout=1) as http:
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    if http.get(base + "/api/health").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.1)
        threads = [threading.Thread(target=hold_stream, args=("/api/events",), daemon=True) for _ in range(2)]
        for t in threads:
            t.start()
        assert opened.wait(5), "the event stream did not open"

        started = time.monotonic()
        proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        output, _ = proc.communicate(timeout=15)
        took = time.monotonic() - started
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    assert took < BOUND_SECONDS, f"stopping took {took:.1f}s\n{output}"
    assert "Waiting for connections to close" not in output
    # The data folder is free again: the lock was released and the database closed.
    lock = DirLock(DataPaths(data).lock)
    lock.acquire()
    lock.release()
