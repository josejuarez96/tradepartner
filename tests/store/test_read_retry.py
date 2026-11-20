"""`open_read_only(retry=True)` waits out another process's writer lock (#1757).

DuckDB refuses a read-only open while another *process* holds the writer, so
the holder here is a subprocess on the fixture-store copy. In-process locks are
a different error and never retried.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tradepartner.config import Settings
from tradepartner.store.db import StoreLockedError, open_for_write, open_read_only

_HOLDER = (
    "import duckdb, sys, time\n"
    "conn = duckdb.connect(sys.argv[1])\n"
    "print('held', flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


@contextmanager
def writer_held(path: str, seconds: float) -> Iterator[None]:
    """Another process holds the store's writer lock for up to `seconds`; the
    holder is stopped when the block ends, so no test waits out the rest."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, path, str(seconds)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=30)


class _FakeTime:
    """A clock that only moves when `sleep` is called, and records each sleep:
    the lock conflict stays real, only the waiting is simulated (#3661)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _settings(path: Path, retry_seconds: int) -> Settings:
    return Settings(_env_file=None, store={"path": str(path), "lock_retry_seconds": retry_seconds})


def test_a_plain_read_only_open_still_fails_at_once(fixture_store_path: Path) -> None:
    settings = _settings(fixture_store_path, 30)
    fake = _FakeTime()
    with (
        writer_held(str(fixture_store_path), 1.0),
        pytest.raises(StoreLockedError),
        open_read_only(settings, clock=fake.clock, sleep=fake.sleep),
    ):
        pass
    assert fake.sleeps == []


def test_a_retrying_open_waits_for_the_writer_to_let_go(fixture_store_path: Path) -> None:
    settings = _settings(fixture_store_path, 20)
    with writer_held(str(fixture_store_path), 1.0), open_read_only(settings, retry=True) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_a_retrying_open_gives_up_after_lock_retry_seconds(fixture_store_path: Path) -> None:
    """The backoff doubles from the initial delay up to the cap, and the last
    sleep is cut to what is left: the open never sleeps past the deadline."""
    settings = _settings(fixture_store_path, 1)
    fake = _FakeTime()
    with (
        writer_held(str(fixture_store_path), 3.0),
        pytest.raises(StoreLockedError, match="within 1s"),
        open_read_only(settings, retry=True, clock=fake.clock, sleep=fake.sleep),
    ):
        pass
    store = settings.store
    assert fake.sleeps[0] == store.lock_retry_initial_delay_seconds
    assert all(
        later == pytest.approx(min(2 * earlier, store.lock_retry_max_delay_seconds))
        for earlier, later in zip(fake.sleeps, fake.sleeps[1:-1], strict=False)
    )
    assert sum(fake.sleeps) == pytest.approx(store.lock_retry_seconds)


def test_the_default_clock_is_the_real_one(fixture_store_path: Path) -> None:
    """With no `clock` or `sleep` argument both opens work on a free store."""
    settings = _settings(fixture_store_path, 20)
    with open_read_only(settings) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    with open_for_write(settings) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)


def test_on_wait_fires_once_on_the_first_retry_of_either_open(fixture_store_path: Path) -> None:
    """#2200: the caller hears of a wait from the open itself, once, with the holder."""
    settings = _settings(fixture_store_path, 20)
    for opener in (
        lambda heard: open_read_only(settings, retry=True, on_wait=heard.append),
        lambda heard: open_for_write(settings, on_wait=heard.append),
    ):
        heard: list[str] = []
        with writer_held(str(fixture_store_path), 1.0), opener(heard):
            pass
        assert len(heard) == 1, heard
        assert "PID" in heard[0]


def test_on_wait_never_fires_when_the_store_is_free(fixture_store_path: Path) -> None:
    settings = _settings(fixture_store_path, 20)
    heard: list[str] = []
    with open_read_only(settings, retry=True, on_wait=heard.append):
        pass
    with open_for_write(settings, on_wait=heard.append):
        pass
    assert heard == []


def test_on_wait_never_fires_without_retry(fixture_store_path: Path) -> None:
    settings = _settings(fixture_store_path, 20)
    heard: list[str] = []
    with (
        writer_held(str(fixture_store_path), 1.0),
        pytest.raises(StoreLockedError),
        open_read_only(settings, on_wait=heard.append),
    ):
        pass
    assert heard == []
