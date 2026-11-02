"""SIGINT handling while closing an interrupted research run."""

from __future__ import annotations

import signal
from concurrent.futures import ThreadPoolExecutor

import pytest

from tradepartner.research.interrupt_guard import run_with_sigint_ignored


def test_restores_previous_handler_if_close_raises() -> None:
    previous = signal.getsignal(signal.SIGINT)

    def handler(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handler)
    try:

        def fail() -> None:
            assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
            raise RuntimeError("close failed")

        with pytest.raises(RuntimeError, match="close failed"):
            run_with_sigint_ignored(fail)
        assert signal.getsignal(signal.SIGINT) is handler
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def test_none_previous_handler_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(signal, "getsignal", lambda signum: None)
    monkeypatch.setattr(signal, "signal", lambda signum, handler: calls.append(handler))
    assert run_with_sigint_ignored(lambda: 42) == 42
    assert calls == [signal.SIG_IGN, signal.SIG_DFL]


def test_worker_thread_runs_action_without_changing_handler() -> None:
    previous = signal.getsignal(signal.SIGINT)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(run_with_sigint_ignored, lambda: "closed").result() == "closed"
    assert signal.getsignal(signal.SIGINT) == previous
