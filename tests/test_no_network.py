"""Confirms the autouse `_no_network` fixture in tests/conftest.py actually
blocks outbound network access (T4 review fix), not just the specific
`socket.socket.connect` call it patches directly: `socket.create_connection`
wraps `connect` internally, and `connect_ex` is a separate code path some
libraries use instead of `connect`.
"""

from __future__ import annotations

import socket

import pytest


def test_create_connection_is_blocked() -> None:
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_connect_ex_is_blocked() -> None:
    """`connect_ex` never raises by contract; it returns a nonzero errno
    instead. The no-network fixture honors that contract rather than
    raising, so a caller that branches on the return value still sees
    the connection refused."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        result = sock.connect_ex(("127.0.0.1", 9))
        assert result != 0
    finally:
        sock.close()
