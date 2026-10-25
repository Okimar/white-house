"""Starts the real server in a subprocess, the way users run it. Slowest test; keep it single."""

import os
import socket
import subprocess
import sys
import time

import httpx

from tavern.paths import REPO_ROOT


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_starts_answers_and_stops(tmp_path):
    port = free_port()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "tavern", "--data-dir", str(tmp_path / "data"),
         "--port", str(port), "--no-browser"],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        body = None
        # trust_env=False: a system HTTP proxy (HTTP_PROXY / Windows settings) must not sit between
        # the test and localhost; it answers an empty 503 while the server is still starting.
        with httpx.Client(trust_env=False, timeout=1) as http:
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    reply = http.get(f"http://127.0.0.1:{port}/api/health")
                    if reply.status_code == 200:
                        body = reply.json()
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
        assert body is not None, "server did not come up"
        assert body["status"] == "ok"

        # A second instance on the same data dir must refuse to start.
        second = subprocess.run(
            [sys.executable, "-m", "tavern", "--data-dir", str(tmp_path / "data"),
             "--port", str(free_port()), "--no-browser"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=20,
        )
        assert second.returncode == 1
        assert "in use by another Tavern process" in second.stderr
    finally:
        proc.terminate()
        output, _ = proc.communicate(timeout=10)
    assert "ready in" in output
