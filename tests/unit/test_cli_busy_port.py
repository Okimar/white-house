"""A second start on a busy port says so instead of crashing with a bind error."""
from __future__ import annotations

import socket

from tavern.cli import _already_running


def test_busy_port_detected() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert _already_running("127.0.0.1", port) == f"http://127.0.0.1:{port}/"


def test_free_port_is_none() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert _already_running("127.0.0.1", port) is None
