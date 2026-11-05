"""A port held by another program (SillyTavern defaults to 8000 too) must not stop the start."""
from __future__ import annotations

import socket

import pytest

from tavern.cli import _free_port, _port_busy
from tavern.errors import TavernError


@pytest.fixture
def held_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        yield s.getsockname()[1]


def test_busy_port_detected(held_port: int) -> None:
    assert _port_busy("127.0.0.1", held_port)


def test_free_port_stays_as_asked() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert _free_port("127.0.0.1", port) == port


def test_busy_port_moves_up(held_port: int) -> None:
    assert _free_port("127.0.0.1", held_port) > held_port


def test_all_tried_ports_busy_is_a_clear_error(held_port: int) -> None:
    with pytest.raises(TavernError, match="заняты"):
        _free_port("127.0.0.1", held_port, tries=1)
