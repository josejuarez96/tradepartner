"""Tests for the in-window restamp pre-check query (plan task T166f).

A fixture journal store in memory: one open window started Friday 2026-10-09
(EDT, close 20:00Z), a closed one, and `mark`, `rebalance` and `catch_up`
runs, a plan, a report and a standalone reconciliation of the open window.
No network.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import pytest

from tradepartner.calendar import session_close
from tradepartner.store import schema
from tradepartner.store.db import insert_row
from tradepartner.store.restamp_precheck import (
    CANDIDATE_TABLES,
    candidate_rows,
    compute_precheck,
    crossed_instants,
    journal_high_water,
    read_instant_sources,
    read_instants,
    window_first_session,
)

STARTED = datetime(2026, 10, 9, 18, tzinfo=UTC)  # Friday 14:00 New York
MARK = date(2026, 10, 12)  # Monday, a session
REBALANCE = date(2026, 10, 30)
CATCH_UP = date(2026, 11, 2)  # EST: close 21:00Z
RECONCILE_AT = datetime(2026, 10, 17, 15, tzinfo=UTC)  # Saturday: session 10-19


def _close(day: date) -> datetime:
    return session_close(day)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _stamp(at: datetime) -> dict[str, datetime]:
    return {"known_at": at, "ingested_at": at}


def _window(conn: duckdb.DuckDBPyConnection, window_id: int, started_at: datetime) -> None:
    insert_row(
        conn,
        "paper_windows",
        {
            "window_id": window_id,
            "hypothesis_id": 1,
            "first_rebalance_session": REBALANCE,
            "account_id": "paper",
            "starting_cash": 100_000.0,
            "starting_equity": 100_000.0,
            "code_version": "test",
            "started_at": started_at,
            "frozen_json": "{}",
            "frozen_sha256": "0" * 64,
            "book_id": "main",
            **_stamp(started_at),
        },
    )


def _run(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    session: date | None,
    kind: str | None,
    *,
    window_id: int = 1,
    at: datetime | None = None,
) -> None:
    when = at or (session_close(session) + timedelta(hours=1) if session else STARTED)
    insert_row(
        conn,
        "paper_runs",
        {
            "run_id": run_id,
            "window_id": window_id,
            "session": session,
            "kind": kind,
            "started_at": when,
            "invoked_by": "scheduler",
            "code_version": "test",
            **_stamp(when),
        },
    )


def _plan(conn: duckdb.DuckDBPyConnection, run_id: int, trial_id: int, cutoff: datetime) -> None:
    insert_row(
        conn,
        "trials",
        {
            "trial_id": trial_id,
            "hypothesis_id": 1,
            "kind": "tracking",
            "started_at": cutoff + timedelta(hours=1),
            "start_session": date(2020, 1, 2),
            "end_session": REBALANCE,
            "data_cutoff": cutoff,
            "code_version": "test",
            "synthetic": False,
            "run_by": "test",
        },
    )
    insert_row(
        conn,
        "paper_plans",
        {
            "run_id": run_id,
            "plan_trial_id": trial_id,
            "rebalance_session": REBALANCE,
            "n_universe": 1,
            "n_targets": 1,
            "n_orders_below_min_at_live_capital": 0,
            **_stamp(cutoff + timedelta(hours=1)),
        },
    )


def _report(conn: duckdb.DuckDBPyConnection, through: date) -> None:
    at = session_close(through) + timedelta(hours=2)
    insert_row(
        conn,
        "paper_reports",
        {"window_id": 1, "trial_id": 99, "through_session": through, "run_at": at, **_stamp(at)},
    )


def _reconcile(conn: duckdb.DuckDBPyConnection, rec_id: int, at: datetime) -> None:
    insert_row(
        conn,
        "reconciliations",
        {
            "reconciliation_id": rec_id,
            "window_id": 1,
            "run_id": None,
            "at": at,
            "status": "ok",
            "book_id": "main",
            **_stamp(at),
        },
    )


def _filing(conn: duckdb.DuckDBPyConnection, n: int, at: datetime) -> None:
    row: dict[str, Any] = {
        "cik": "0000320193",
        "accession": f"0000320193-26-{n:06d}",
        "form": "8-K",
        "items": "2.02",
        "accepted_at": at,
        "known_at": at,
        "ingested_at": at + timedelta(days=1),
        "source": "edgar",
        "provenance": "filing",
    }
    insert_row(conn, "filing_events", row)


def _journal(conn: duckdb.DuckDBPyConnection) -> None:
    """One open window with every instant kind, and a closed window."""
    _window(conn, 1, STARTED)
    _run(conn, 1, MARK, "mark")
    _run(conn, 2, REBALANCE, "rebalance")
    _run(conn, 3, CATCH_UP, "catch_up")
    _run(conn, 4, None, None, at=datetime(2026, 10, 10, 14, tzinfo=UTC))  # no_session
    _plan(conn, 2, 7, _close(REBALANCE))
    _report(conn, REBALANCE)
    _reconcile(conn, 1, RECONCILE_AT)
    # A closed window and its run add no instant.
    _window(conn, 2, datetime(2026, 9, 1, 14, tzinfo=UTC))
    _run(conn, 5, date(2026, 9, 2), "mark", window_id=2)
    insert_row(
        conn,
        "paper_window_stops",
        {"window_id": 2, "at": datetime(2026, 9, 3, tzinfo=UTC), "state": "closed"}
        | _stamp(datetime(2026, 9, 3, tzinfo=UTC)),
    )


def test_no_open_window_passes_with_no_instants(conn: duckdb.DuckDBPyConnection) -> None:
    _filing(conn, 1, datetime(2026, 10, 12, 21, tzinfo=UTC))
    result = compute_precheck(conn, release="r1", made_at=STARTED)
    assert window_first_session(conn) is None
    assert read_instants(conn) == []
    assert result.passed
    assert result.instants == ()
    assert all(c.rows == 0 for c in result.candidates.values())


def test_window_first_session_is_the_session_on_or_after_started_at(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _window(conn, 1, datetime(2026, 10, 10, 15, tzinfo=UTC))  # Saturday
    assert window_first_session(conn) == date(2026, 10, 12)


def test_open_window_lists_every_read_instant(conn: duckdb.DuckDBPyConnection) -> None:
    _journal(conn)
    expected = {
        STARTED,  # the window's started_at
        _close(date(2026, 10, 9)),  # mark close(S-1)
        _close(MARK),  # mark close(S)
        _close(date(2026, 10, 29)),  # rebalance close(S-1)
        _close(REBALANCE),  # rebalance close(S), plan data_cutoff, report, catch_up S-1
        _close(CATCH_UP),  # catch_up close(S)
        _close(date(2026, 10, 16)),  # standalone reconciliation close(S-1)
    }
    assert read_instants(conn) == sorted(expected)
    sources = {i.source for i in read_instant_sources(conn)}
    assert {
        "window 1 started_at",
        "run 1 close(S-1)",
        "run 1 close(S)",
        "run 2 close(S-1)",
        "run 3 close(S)",
        "plan 2 data_cutoff",
        f"report window 1 close({REBALANCE.isoformat()})",
        "reconciliation 1 close(S-1)",
    } <= sources
    assert not any(s.startswith("run 5") for s in sources)


def test_plan_data_cutoff_is_its_own_instant(conn: duckdb.DuckDBPyConnection) -> None:
    _window(conn, 1, STARTED)
    _run(conn, 1, REBALANCE, "rebalance")
    cutoff = _close(REBALANCE) - timedelta(minutes=7)  # distinct from close(S)
    _plan(conn, 1, 7, cutoff)
    assert cutoff in read_instants(conn)


def test_high_water_mark_moves_when_a_run_row_is_added(conn: duckdb.DuckDBPyConnection) -> None:
    _journal(conn)
    before = journal_high_water(conn)
    assert before is not None
    later = before + timedelta(hours=3)
    _run(conn, 9, date(2026, 11, 3), "mark", at=later)
    assert journal_high_water(conn) == later


def test_a_candidate_row_within_reach_of_an_instant_fails(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _journal(conn)
    _filing(conn, 1, _close(MARK) + timedelta(hours=1))
    result = compute_precheck(conn, release="r1", made_at=STARTED)
    assert not result.passed
    (hit,) = result.in_reach
    assert (hit.table, hit.instant, hit.rows) == ("filing_events", _close(MARK), 1)
    assert result.candidates["filing_events"].in_reach == 1


def test_a_candidate_row_exactly_five_hours_after_an_instant_fails(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _journal(conn)
    _filing(conn, 1, _close(MARK) + timedelta(hours=5))
    assert not compute_precheck(conn, release="r1", made_at=STARTED).passed


def test_candidate_rows_outside_reach_pass(conn: duckdb.DuckDBPyConnection) -> None:
    _journal(conn)
    _filing(conn, 1, _close(MARK) + timedelta(hours=5, seconds=1))
    _filing(conn, 2, _close(MARK))  # at the instant itself: read as known, unmoved
    _filing(conn, 3, datetime(2026, 10, 8, 20, 30, tzinfo=UTC))  # before the window
    result = compute_precheck(conn, release="r1", made_at=STARTED)
    assert result.passed
    assert result.candidates["filing_events"].rows == 2


def test_refusal_candidates_are_counted(conn: duckdb.DuckDBPyConnection) -> None:
    _window(conn, 1, STARTED)
    _filing(conn, 1, datetime(2026, 10, 13, 15, tzinfo=UTC))  # 11:00 New York: no
    _filing(conn, 2, datetime(2026, 10, 13, 6, tzinfo=UTC))  # 02:00 New York: midnight
    _filing(conn, 3, datetime(2026, 10, 13, 22, tzinfo=UTC))  # 18:00 New York: close
    _filing(conn, 4, datetime(2026, 10, 8, 22, tzinfo=UTC))  # before the window
    counts, failing = candidate_rows(conn, window_first_session(conn), [])
    assert set(counts) == set(CANDIDATE_TABLES)
    assert counts["filing_events"].rows == 3
    assert counts["filing_events"].refusal_candidates == 2
    assert failing == []


def test_crossed_instants_is_new_le_t_lt_old() -> None:
    t = _close(MARK)
    assert crossed_instants(t + timedelta(hours=1), t, [t]) == [t]
    assert crossed_instants(t, t - timedelta(hours=1), [t]) == []
    assert crossed_instants(t + timedelta(hours=2), t + timedelta(hours=1), [t]) == []


def test_the_file_names_the_release_and_the_window_start(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _journal(conn)
    made = datetime(2026, 11, 3, 13, tzinfo=UTC)
    data = compute_precheck(conn, release="restamp-1382a2", made_at=made).to_json()
    assert data["release"] == "restamp-1382a2"
    assert data["window_first_session"] == "2026-10-09"
    assert data["made_at"] == made.isoformat()
    assert data["pass"] is True
    assert data["instants"] == [t.isoformat() for t in read_instants(conn)]
    assert data["journal_high_water"] is not None
    assert data["open_windows"] == [
        {
            "window_id": 1,
            "book_id": "main",
            "first_rebalance_session": REBALANCE.isoformat(),
            "started_at": STARTED.isoformat(),
        }
    ]
    assert "store_max_ingested_at" in data
