"""#2178: the scheduled writers out-wait a long reader, and a kill that cannot
get the store says plainly that nothing is engaged."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

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


def _connects_off_the_main_thread(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every store connection opened from any thread but the main one (#2200: a
    second-thread probe made engage's write fail at once in the same process)."""
    real = duckdb.connect
    strays: list[str] = []

    def connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        if threading.current_thread() is not threading.main_thread():
            strays.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", connect)
    return strays


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


@contextmanager
def _writer_holds(path: str, *, seconds: float) -> Iterator[None]:
    """A separate process keeps DuckDB's write lock after the kill starts."""
    code = (
        "import duckdb, time\n"
        f"c = duckdb.connect({path!r}, read_only=False)\n"
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


@pytest.mark.usefixtures("started")
def test_paper_kill_waits_for_another_process_writer_and_engages(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
    factory: _Factory,  # noqa: F811
) -> None:
    monkeypatch.setenv("STORE__LOCK_RETRY_SECONDS", "1")
    monkeypatch.setenv("STORE__WRITER_LOCK_RETRY_SECONDS", "5")
    before = _engaged_rows()
    with _writer_holds(_settings().store.path, seconds=2):
        out = _paper(clock, factory, "kill", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    assert _engaged_rows() == before + 1


@pytest.mark.usefixtures("started")
def test_paper_kill_writer_outlasts_wait_and_reports_not_engaged(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
    factory: _Factory,  # noqa: F811
) -> None:
    monkeypatch.setenv("STORE__LOCK_RETRY_SECONDS", "1")
    monkeypatch.setenv("STORE__WRITER_LOCK_RETRY_SECONDS", "2")
    before = _engaged_rows()
    with _writer_holds(_settings().store.path, seconds=10):
        out = _paper(clock, factory, "kill", "--reason", LONG_REASON)
    assert out.exit_code == cli.WRITE_FAILED_EXIT_CODE
    assert "kill switch NOT engaged yet: store busy" in out.output
    assert "kill switch NOT engaged: store busy" in out.output
    assert _engaged_rows() == before


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
    assert out.exit_code == cli.WRITE_FAILED_EXIT_CODE
    assert "kill switch NOT engaged yet: store busy" in out.output
    assert "waiting up to 1 s" in out.output
    assert "kill switch NOT engaged: store busy (held by " in out.output, out.output
    assert "retry, or stop the reader" in out.output
    assert _engaged_rows() == before


@pytest.mark.usefixtures("books")
def test_paper_kill_all_reports_a_wait_in_open_books_once(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
    books: object,  # noqa: F811
) -> None:
    """The listing's own first lock retry prints the waiting line; no other thread
    opens the store."""
    monkeypatch.setenv("STORE__LOCK_RETRY_SECONDS", "1")
    monkeypatch.setenv("STORE__WRITER_LOCK_RETRY_SECONDS", "10")
    strays = _connects_off_the_main_thread(monkeypatch)
    with _writer_holds(_settings().store.path, seconds=1.5):
        out = _paper(clock, books, "kill", "--all", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    assert out.output.count("kill switch NOT engaged yet: store busy (held by ") == 1
    assert "waiting up to 10 s" in out.output
    assert strays == []


@pytest.mark.usefixtures("started")
def test_paper_kill_reports_the_wait_from_the_stores_first_retry(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,  # noqa: F811
    factory: _Factory,  # noqa: F811
) -> None:
    """Another process's writer: one waiting line naming it, then engaged, and every
    store connection on the main thread."""
    monkeypatch.setenv("STORE__LOCK_RETRY_SECONDS", "1")
    monkeypatch.setenv("STORE__WRITER_LOCK_RETRY_SECONDS", "10")
    strays = _connects_off_the_main_thread(monkeypatch)
    before = _engaged_rows()
    with _writer_holds(_settings().store.path, seconds=1.5):
        out = _paper(clock, factory, "kill", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    assert out.output.count("kill switch NOT engaged yet: store busy (held by ") == 1
    assert ", pid " in out.output
    assert strays == []
    assert _engaged_rows() == before + 1


def test_a_same_process_lock_reads_as_store_busy() -> None:
    """`StoreSameProcessLockedError` folded into `WriteFailed` gets the plain wording."""
    assert cli._STORE_LOCKED_RE.search(
        "the kill switch row could not be written, so it is NOT engaged: "
        "StoreSameProcessLockedError: 'x' is already open in this process"
    )
    assert cli._STORE_LOCKED_RE.search("NOT engaged: StoreLockedError: held in /x (PID 1)")
    assert not cli._STORE_LOCKED_RE.search("NOT engaged: IOException: disk full")
    assert (
        cli._lock_holder("'x' is already open in this process in a different mode")
        == "another connection in this process"
    )
