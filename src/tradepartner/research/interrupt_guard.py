"""Keep an interrupted study's failed close alive through another Ctrl-C."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable


def run_with_sigint_ignored[T](action: Callable[[], T]) -> T:
    """Run a close with SIGINT ignored on the main thread, restoring its handler."""
    if threading.current_thread() is not threading.main_thread():
        return action()
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return action()
    finally:
        signal.signal(signal.SIGINT, previous if previous is not None else signal.SIG_DFL)
