"""#2178: the scheduled writers out-wait a long reader, and a kill that cannot
get the store says plainly that nothing is engaged."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
import pytest
from test_cli_paper import (  # noqa: F401  (fixtures)
    LONG_REASON,
    _Clock,
    _Factory,
    _paper,
    _settings,
    books,
    clock,
    factory,
    live,
    started,
)

from tradepartner import cli
from tradepartner.config import StoreConfig


def _engaged_rows() -> int:
    with duckdb.connect(_settings().store.path, read_only=True) as conn:
        found = conn.execute("SELECT count(*) FROM kill_switch WHERE state = 'engaged'").fetchone()
    assert found is not None
    return int(found[0])


@contextmanager
def _reader_holds(path: str, *, seconds: float) -> Iterator[None]:
    """Another OS process holds a read-only connection for `seconds`."""
    code = (
        "import duckdb, time\n"
        f"c = duckdb.connect({path!r}, read_only=True)\n"
        "print('held', flush=True)\n"
        f"time.sleep({seconds})\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        yield
    finally:
        proc.kill()
        proc.wait()


def test_writer_wait_defaults_above_the_reader_wait() -> None:
    cfg = StoreConfig()
    assert cfg.lock_retry_seconds == 60
    assert cfg.writer_lock_retry_seconds == 300


def test_writer_settings_swaps_only_the_wait() -> None:
    s = _settings()
    w = cli._writer_settings(s)
    assert w.store.lock_retry_seconds == s.store.writer_lock_retry_seconds
    assert s.store.lock_retry_seconds != w.store.lock_retry_seconds
    assert w.store.path == s.store.path


@pytest.mark.usefixtures("started")
def test_paper_kill_out_waits_a_reader_that_outlasts_the_default_wait(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
    factory: _Factory,  # noqa: F811
) -> None:
    monkeypatch.setenv("STORE__LOCK_RETRY_SECONDS", "1")
    monkeypatch.setenv("STORE__WRITER_LOCK_RETRY_SECONDS", "60")
    before = _engaged_rows()
    with _reader_holds(_settings().store.path, seconds=4):
        out = _paper(clock, factory, "kill", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    assert _engaged_rows() == before + 1


@pytest.mark.usefixtures("started")
def test_paper_kill_that_cannot_get_the_store_says_not_engaged(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
    factory: _Factory,  # noqa: F811
) -> None:
    monkeypatch.setenv("STORE__WRITER_LOCK_RETRY_SECONDS", "1")
    before = _engaged_rows()
    with _reader_holds(_settings().store.path, seconds=30):
        out = _paper(clock, factory, "kill", "--reason", LONG_REASON)
    assert out.exit_code != 0
    assert "kill switch NOT engaged: store busy (held by " in out.output, out.output
    assert "retry, or stop the reader" in out.output
    assert _engaged_rows() == before
