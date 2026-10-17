"""The e2e harness hands out ports that an outgoing connection can never take first.

A port reserved by binding to 0 comes from the OS ephemeral range; the harness closes it and the engine
only binds it much later, after the backend has opened outgoing connections, whose local ports come
from that same range. CI saw `EADDRINUSE` on such a port.
"""

import socket

from tests.e2e import harness


def test_ports_come_from_below_the_ephemeral_range():
    low, _high = harness.ephemeral_range()
    for _ in range(50):
        port = harness.free_port()
        assert 1024 < port < low, (port, low)


def test_ports_are_never_handed_out_twice_in_a_run():
    ports = [harness.free_port() for _ in range(200)]
    assert len(set(ports)) == len(ports)


def test_handed_out_ports_are_bindable():
    port = harness.free_port()
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))


def test_busy_ports_are_skipped(monkeypatch):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        picks = iter([taken, taken, 23456])
        monkeypatch.setattr(harness, "_candidate", lambda low: next(picks))
        monkeypatch.setattr(harness, "_handed_out", set())
        monkeypatch.setattr(harness, "ephemeral_range", lambda: (65000, 65535))
        assert harness.free_port() == 23456
