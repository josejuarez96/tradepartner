# ruff: noqa: F811
"""A `paper run` with no ok ingest covering S-1 pings the heartbeat /fail (#2460).

A stale ingest engages no kill switch, so `test_cli_paper_heartbeat_halted.py`
(which reads the switch) does not cover it: the run itself must end non-zero
and the ping must say so, with nothing submitted. Built on
`tests/test_cli_paper.py`'s fixtures; the fixture store has no 2026 bars and no
`ingestion_runs` row, so a session-day run is the "no ok ingest for S-1" case
with nothing patched.
"""

from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest
from test_cli_paper import (  # noqa: F401  (fixtures)
    HEARTBEAT_URL,
    _Clock,
    _count,
    _Factory,
    _heartbeat_calls,
    _paper,
    _settings,
    books,
    clock,
    factory,
    live,
    started,
)

from tradepartner.calendar import previous_session, session_close


def _submits(factory: _Factory) -> list[object]:
    return [c for c in factory.fake.calls if c.method == "submit"]


@pytest.mark.usefixtures("started")
def test_run_with_no_ok_ingest_pings_fail_exits_non_zero_and_submits_nothing(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    with duckdb.connect(_settings().store.path, read_only=True) as conn:
        (ingests,) = conn.execute("SELECT count(*) FROM ingestion_runs").fetchone()  # type: ignore[misc]
    assert ingests == 0, "the premise: no ingest at all"
    out = _paper(clock, factory, "run")
    assert out.exit_code != 0, out.output
    assert "StaleDataError: no ok daily price ingest" in out.output, out.output
    assert calls == [HEARTBEAT_URL + "/fail"], calls
    assert _submits(factory) == []
    assert _count("orders") == 0


@pytest.mark.usefixtures("started")
def test_an_ok_ingest_that_finished_before_s_minus_1_closed_is_still_stale(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    """The scheduled ingest for S-1 never ran: an older ok row does not count."""
    calls = _heartbeat_calls(monkeypatch)
    finished = session_close(previous_session(clock.now.date())) - timedelta(days=3)
    with duckdb.connect(_settings().store.path) as conn:
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, started_at, finished_at, status, source, mode) "
            "VALUES ('old-ok', ?, ?, 'ok', 'alpaca', 'session')",
            [finished - timedelta(minutes=5), finished],
        )
    out = _paper(clock, factory, "run")
    assert out.exit_code != 0, out.output
    assert "StaleDataError" in out.output, out.output
    assert calls == [HEARTBEAT_URL + "/fail"], calls
    assert _submits(factory) == []
    assert _count("orders") == 0
