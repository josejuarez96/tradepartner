"""Wait for DNS to resolve before a scheduled ingest starts its sources (#3551).

DNS only: no HTTP, no credentials. Only host names are ever put in a message.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable, Sequence
from typing import Any

HTTPS_PORT = 443


def wait_for_hosts(
    hosts: Sequence[str],
    *,
    max_wait_seconds: float,
    initial_delay_seconds: float,
    resolve: Callable[..., Any] = socket.getaddrinfo,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """Wait, with doubling backoff, until every host resolves; None on success.

    Returns a message naming the unresolved hosts and the time waited when the
    budget runs out. A budget of 0 makes no resolver call (the wait is off).
    """
    if max_wait_seconds <= 0 or not hosts:
        return None
    start = clock()
    delay = initial_delay_seconds
    pending = list(hosts)
    while True:
        pending = [h for h in pending if not _resolves(h, resolve)]
        if not pending:
            return None
        remaining = max_wait_seconds - (clock() - start)
        if remaining <= 0:
            break
        pause = min(delay, remaining)
        sleep(pause)
        delay *= 2
    waited = clock() - start
    return f"network not ready after {waited:.0f}s: cannot resolve {', '.join(pending)}"


def _resolves(host: str, resolve: Callable[..., Any]) -> bool:
    try:
        resolve(host, HTTPS_PORT)
    except OSError:
        return False
    return True
