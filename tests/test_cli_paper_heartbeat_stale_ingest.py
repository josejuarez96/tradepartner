# ruff: noqa: F811
"""A `paper run` with no ok ingest covering S-1 pings the heartbeat /fail (#2460),
and the next permitted run after S-1 is made valid recovers (#2602, INT-03A).

A stale ingest engages no kill switch, so `test_cli_paper_heartbeat_halted.py`
(which reads the switch) does not cover it: the run itself must end non-zero
and the ping must say so, with nothing submitted. Built on
`tests/test_cli_paper.py`'s fixtures; the fixture store has no 2026 bars and no
`ingestion_runs` row, so a session-day run is the "no ok ingest for S-1" case
with nothing patched.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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

from tradepartner.calendar import next_session, previous_session, session_close


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


def _ok_ingest(run_id: str, finished: datetime) -> None:
    """An `ok` daily price ingest row (the source and mode the run's check reads)."""
    with duckdb.connect(_settings().store.path) as conn:
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, started_at, finished_at, status, source, mode) "
            "VALUES (?, ?, ?, 'ok', 'alpaca', 'session')",
            [run_id, finished - timedelta(minutes=5), finished],
        )


def _reference_bar(session_day: date, known: datetime) -> None:
    """The reference symbol's (SPY's) bar for `session_day`, known at `known`."""
    with duckdb.connect(_settings().store.path) as conn:
        conn.execute(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
            "known_at, ingested_at, source, provenance) "
            "VALUES ('SEC_SPY', ?, 100, 101, 99, 100, 1000, ?, ?, 'alpaca', 'bar')",
            [session_day, known, known + timedelta(minutes=10)],
        )


def _results() -> list[tuple[int, str, str | None]]:
    with duckdb.connect(_settings().store.path, read_only=True) as conn:
        rows = conn.execute(
            "SELECT run_id, status, fault_type FROM paper_run_results ORDER BY run_id"
        ).fetchall()
    return [(int(r[0]), str(r[1]), r[2]) for r in rows]


#: The next permitted run after the refusal: later the same session (outside its
#: submit window), or the next session's scheduled 09:00 ET run (inside it).
LATER_TODAY = "later_today"
NEXT_SESSION = "next_session"


@pytest.mark.parametrize("when", [LATER_TODAY, NEXT_SESSION])
@pytest.mark.usefixtures("started")
def test_once_s_minus_1_is_covered_the_next_permitted_run_succeeds_and_the_refusal_stays(
    when: str, monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    """INT-03A recovery (#2602): S-1 is made valid through the real freshness
    contract (an `ok` ingest row finished after S-1's close and the reference
    symbol's S-1 bar known by then), not by patching `_staleness`. The next run
    succeeds, sends nothing twice, and the refused run is still `stale`."""
    calls = _heartbeat_calls(monkeypatch)
    refused = _paper(clock, factory, "run")
    assert refused.exit_code != 0 and "StaleDataError" in refused.output, refused.output
    assert _results() == [(1, "stale", "StaleDataError")]

    if when == LATER_TODAY:
        clock.now += timedelta(minutes=30)
    else:
        clock.now = datetime.combine(next_session(clock.now.date()), clock.now.time(), UTC)
        clock.now -= timedelta(hours=1)  # 09:00 ET: inside the submit window
    s_minus_1 = previous_session(clock.now.date())
    close = session_close(s_minus_1)
    finished = close + timedelta(minutes=30)
    assert finished < clock.now

    # A green row alone is not coverage: the reference symbol's S-1 bar is missing.
    _ok_ingest("s1-ok", finished)
    green_only = _paper(clock, factory, "run")
    assert green_only.exit_code != 0, green_only.output
    assert "does not cover" in green_only.output, green_only.output
    assert _results() == [(1, "stale", "StaleDataError"), (2, "stale", "StaleDataError")]

    _reference_bar(s_minus_1, close)
    clock.now += timedelta(minutes=1)
    recovered = _paper(clock, factory, "run")
    assert recovered.exit_code == 0, recovered.output
    assert "paper run: ok (run 3" in recovered.output, recovered.output
    outside = "outside the submit window" in recovered.output
    assert outside == (when == LATER_TODAY), recovered.output
    assert calls == [HEARTBEAT_URL + "/fail"] * 2 + [HEARTBEAT_URL], calls

    # The refusals stay recorded as refused (not closed `crashed`, not rewritten),
    # the switch was never engaged, and nothing was sent at all, let alone twice.
    assert _results() == [
        (1, "stale", "StaleDataError"),
        (2, "stale", "StaleDataError"),
        (3, "ok", None),
    ]
    assert _count("kill_switch") == 0
    assert _submits(factory) == []
    assert _count("orders") == 0
