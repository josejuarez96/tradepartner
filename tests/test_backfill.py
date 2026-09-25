"""Tests for backfill and resume (spec req 9; plan T17).

A real DuckDB file under `tmp_path`, the synthetic filings of
`test_ingest`, and `_History`: a stub `PriceSource` with a bar for every
requested id and XNYS session, whose gaps, failures and mid-fetch hooks
each test states.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_ingest import ACME, DUAL, DUAL_B, NOW, SPY, _at, _filings

from tradepartner.adapters.filings import CoverListing, CoverPage, DelistingFiling
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    action_first_seen_known_at,
    bar_known_at,
)
from tradepartner.backfill import BACKFILL, backfill, month_windows
from tradepartner.calendar import is_session
from tradepartner.config import Settings
from tradepartner.ingest import FAILED, LOCKED, OK, STALE, ingest_session

SINCE = date(2019, 4, 10)
IDS = (ACME, DUAL, DUAL_B, SPY)


def _sessions(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=n) for n in range((end - start).days + 1))
    return [day for day in days if is_session(day)]


@dataclass
class _History(PriceSource):
    """A bar at `close` for every requested id and session; `gaps` are
    (id, session) pairs with no bar; `fail_in` names the month (first day)
    whose `corporate_actions` raises; `during` runs inside `bars` for that
    month."""

    close: float = 20.0
    gaps: set[tuple[str, date]] = field(default_factory=set)
    fail_in: date | None = None
    during: dict[date, Callable[[], None]] = field(default_factory=dict)
    actions: list[CorporateAction] = field(default_factory=list)
    calls: list[tuple[str, date, date]] = field(default_factory=list)
    fetched: dict[date, set[str]] = field(default_factory=dict)

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        self.calls.append(("bars", start, end))
        self.fetched[start.replace(day=1)] = set(security_ids)
        if start.replace(day=1) in self.during:
            self.during[start.replace(day=1)]()
        c = self.close
        return [
            Bar(sid, s, c, c + 1, c - 1, c, 1_000_000, bar_known_at(s), "alpaca")
            for sid in sorted(security_ids)
            for s in _sessions(start, end)
            if (sid, s) not in self.gaps
        ]

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        self.calls.append(("actions", start, end))
        if self.fail_in == start.replace(day=1):
            raise RuntimeError("actions endpoint down")
        return [
            a for a in self.actions if a.security_id in security_ids and start <= a.ex_date <= end
        ]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _backfill(
    settings: Settings,
    prices: PriceSource,
    *,
    since: date = SINCE,
    now: datetime = NOW,
    filings: Any = None,
    clock: Callable[[], datetime] | None = None,
    **kwargs: Any,
) -> Any:
    return backfill(
        settings,
        prices=prices,
        filings=filings if filings is not None else _filings(),
        since=since,
        clock=clock if clock is not None else (lambda: now),
        **kwargs,
    )


def _ticking(start: datetime, step: timedelta) -> Callable[[], datetime]:
    """A clock that moves `step` forward on every call."""
    ticks = iter(start + step * n for n in range(1_000_000))
    return lambda: next(ticks)


def _read(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        return conn.execute(sql).fetchall()


def _alpaca_runs(settings: Settings) -> list[tuple[Any, ...]]:
    return _read(
        settings,
        "SELECT status, mode, chunk_cursor FROM ingestion_runs "
        "WHERE source = 'alpaca' ORDER BY started_at, chunk_cursor",
    )


# --- month windows ----------------------------------------------------------


def test_month_windows_cut_at_calendar_months_and_skip_empty_ones() -> None:
    assert month_windows(date(2019, 4, 10), date(2019, 6, 28)) == [
        (date(2019, 4, 10), date(2019, 4, 30)),
        (date(2019, 5, 1), date(2019, 5, 31)),
        (date(2019, 6, 1), date(2019, 6, 28)),
    ]
    # 2019-06-29/30 is a weekend: no session, no window.
    assert month_windows(date(2019, 6, 29), date(2019, 6, 30)) == []
    assert month_windows(date(2019, 7, 1), date(2019, 6, 28)) == []


# --- monthly chunks per source ----------------------------------------------


def test_backfill_writes_one_chunk_per_month_and_every_session(settings: Settings) -> None:
    prices = _History()
    result = _backfill(settings, prices)
    assert result.ok
    assert [(r.source, r.status) for r in result.runs] == [("edgar", OK)] + [("alpaca", OK)] * 3
    assert [c for c in prices.calls if c[0] == "bars"] == [
        ("bars", date(2019, 4, 10), date(2019, 4, 30)),
        ("bars", date(2019, 5, 1), date(2019, 5, 31)),
        ("bars", date(2019, 6, 1), date(2019, 6, 28)),
    ]
    assert _alpaca_runs(settings) == [
        (OK, BACKFILL, "since=2019-04-10;through=2019-04-30"),
        (OK, BACKFILL, "since=2019-04-10;through=2019-05-31"),
        (OK, BACKFILL, "since=2019-04-10;through=2019-06-28"),
    ]
    expected = len(_sessions(SINCE, date(2019, 6, 28))) * len(IDS)
    assert _read(settings, "SELECT count(*) FROM prices_daily") == [(expected,)]
    assert _read(settings, "SELECT DISTINCT mode FROM ingestion_runs") == [(BACKFILL,)]


def test_edgar_is_one_full_history_chunk(settings: Settings) -> None:
    result = _backfill(settings, _History(), source="edgar")
    assert [(r.source, r.status, r.chunk_cursor) for r in result.runs] == [
        ("edgar", OK, "since=2019-04-10")
    ]
    # Filings from 2018, before --since, are in the master (spec open question 2).
    assert _read(settings, "SELECT count(*) FROM securities WHERE known_at < '2019-04-10'")[0][0]


def test_actions_are_fetched_per_month_window(settings: Settings) -> None:
    ex = date(2019, 5, 15)
    div = CorporateAction(
        ACME, ActionType.DIVIDEND, ex, 0.1, action_first_seen_known_at(ex), "alpaca"
    )
    prices = _History(actions=[div])
    _backfill(settings, prices)
    assert [c[1:] for c in prices.calls if c[0] == "actions"][1] == (
        date(2019, 5, 1),
        date(2019, 5, 31),
    )
    assert _read(settings, "SELECT ex_date FROM corporate_actions") == [(ex,)]


# --- failures and resume ----------------------------------------------------


def test_mid_chunk_failure_leaves_earlier_chunks_and_nothing_from_that_one(
    settings: Settings,
) -> None:
    prices = _History(fail_in=date(2019, 5, 1))
    result = _backfill(settings, prices)
    assert [(r.source, r.status) for r in result.runs] == [
        ("edgar", OK),
        ("alpaca", OK),
        ("alpaca", FAILED),
    ]
    assert "actions endpoint down" in result.runs[-1].message
    assert _read(settings, "SELECT max(session) FROM prices_daily") == [(date(2019, 4, 30),)]
    assert [c for c in prices.calls if c[1] == date(2019, 6, 1)] == []  # halted
    assert _alpaca_runs(settings) == [
        (OK, BACKFILL, "since=2019-04-10;through=2019-04-30"),
        (FAILED, BACKFILL, "since=2019-04-10;through=2019-05-31"),
    ]


def test_resume_starts_after_the_last_committed_chunk(settings: Settings) -> None:
    _backfill(settings, _History(fail_in=date(2019, 5, 1)))
    later = NOW + timedelta(hours=1)
    prices = _History()
    result = _backfill(settings, prices, now=later, source="alpaca")
    assert result.ok
    assert [c[1:] for c in prices.calls if c[0] == "bars"] == [
        (date(2019, 5, 1), date(2019, 5, 31)),
        (date(2019, 6, 1), date(2019, 6, 28)),
    ]
    expected = len(_sessions(SINCE, date(2019, 6, 28))) * len(IDS)
    assert _read(settings, "SELECT count(*) FROM prices_daily") == [(expected,)]


def test_a_finished_backfill_resumes_to_nothing_and_a_new_since_starts_over(
    settings: Settings,
) -> None:
    _backfill(settings, _History())
    again = _History()
    assert _backfill(settings, again, now=NOW + timedelta(hours=1), source="alpaca").runs == ()
    assert again.calls == []
    earlier = _History()
    result = _backfill(
        settings, earlier, since=date(2019, 6, 3), now=NOW + timedelta(hours=2), source="alpaca"
    )
    assert [r.chunk_cursor for r in result.runs] == ["since=2019-06-03;through=2019-06-28"]
    assert [r.rows_added for r in result.runs] == [0]  # already stored, unchanged


def test_resume_picks_up_sessions_completed_since_the_last_run(settings: Settings) -> None:
    _backfill(settings, _History())
    later = datetime(2019, 7, 3, 2, 0, tzinfo=UTC)  # after 2019-07-01 and 07-02 closed
    prices = _History()
    result = _backfill(settings, prices, now=later, source="alpaca")
    assert [c[1:] for c in prices.calls if c[0] == "bars"] == [(date(2019, 7, 1), date(2019, 7, 2))]
    assert result.runs[-1].rows_added == 2 * len(IDS)


def test_a_reference_symbol_gap_in_a_month_is_stale(settings: Settings) -> None:
    prices = _History(gaps={(SPY, date(2019, 5, 14))})
    result = _backfill(settings, prices)
    assert (result.runs[-1].status, result.runs[-1].chunk_cursor) == (
        STALE,
        "since=2019-04-10;through=2019-05-31",
    )
    assert "2019-05-14" in result.runs[-1].message
    assert _read(settings, "SELECT max(session) FROM prices_daily") == [(date(2019, 4, 30),)]


def test_prices_without_a_master_fail_rather_than_fetch_nothing(settings: Settings) -> None:
    ingest_session(
        settings, prices=_History(), filings=_filings(), source="edgar", clock=lambda: NOW
    )
    with duckdb.connect(settings.store.path) as conn:
        conn.execute("DELETE FROM listings")
    result = _backfill(settings, _History(), source="alpaca")
    assert result.runs[-1].status == FAILED and "SPY" in result.runs[-1].message


def test_a_backfill_then_a_daily_run_adds_nothing(settings: Settings) -> None:
    _backfill(settings, _History())
    daily = ingest_session(
        settings,
        prices=_History(),
        filings=_filings(),
        source="alpaca",
        clock=lambda: NOW + timedelta(hours=1),
    )
    assert daily.ok and daily.runs[-1].rows_added == 0


# --- the lock is released between chunks and while fetching ----------------


def _write_from_another_process(path: str) -> int:
    code = textwrap.dedent(f"""
        import duckdb
        duckdb.connect(database={path!r}, read_only=False).close()
        """)
    return subprocess.run([sys.executable, "-c", code], check=False).returncode


def test_the_lock_is_free_while_a_month_is_fetched(settings: Settings) -> None:
    seen: list[int] = []

    def other_writer() -> None:
        seen.append(_write_from_another_process(settings.store.path))

    prices = _History(during={date(2019, 5, 1): other_writer})
    assert _backfill(settings, prices).ok
    assert seen == [0]


def test_a_locked_store_halts_the_chunk_with_status_locked(settings: Settings) -> None:
    _backfill(settings, _History(), source="edgar")
    code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={settings.store.path!r}, read_only=False)
        print("HELD", flush=True)
        time.sleep(30)
        """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "HELD"
        result = _backfill(settings, _History(), source="alpaca")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert [r.status for r in result.runs] == [LOCKED]


def test_a_writer_that_finishes_within_the_retry_window_is_waited_for(settings: Settings) -> None:
    _backfill(settings, _History(), source="edgar")
    patient = settings.model_copy(
        update={"store": settings.store.model_copy(update={"lock_retry_seconds": 10})}
    )
    code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={settings.store.path!r}, read_only=False)
        print("HELD", flush=True)
        time.sleep(0.5)
        conn.close()
        """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "HELD"
        assert _backfill(patient, _History(), source="alpaca").ok
    finally:
        proc.wait(timeout=10)


# --- quant-auditor on #171 ---------------------------------------------------


def test_names_are_read_after_the_edgar_chunk_even_with_a_fresh_snapshot(
    settings: Settings,
) -> None:
    # The snapshot (and so SPY's listing) is fetched after the run began.
    ticks = iter([NOW] + [NOW + timedelta(minutes=30)] * 1000)
    filings = _filings(fetched_at=NOW + timedelta(minutes=10))
    result = _backfill(settings, _History(), filings=filings, clock=lambda: next(ticks))
    assert result.ok, result.runs[-1].message


def test_the_daily_run_also_reads_after_the_edgar_chunk(settings: Settings) -> None:
    ticks = iter([NOW] + [NOW + timedelta(minutes=30)] * 1000)
    result = ingest_session(
        settings,
        prices=_History(),
        filings=_filings(fetched_at=NOW + timedelta(minutes=10)),
        clock=lambda: next(ticks),
    )
    assert result.ok, result.runs[-1].message


def test_revisions_are_stamped_when_each_month_was_fetched(settings: Settings) -> None:
    _backfill(settings, _History(close=20.0))
    start = NOW + timedelta(days=1)
    _backfill(
        settings,
        _History(close=21.0),
        since=date(2019, 4, 11),
        clock=_ticking(start, timedelta(minutes=1)),
        source="alpaca",
    )
    stamps = _read(
        settings,
        "SELECT date_trunc('month', session), min(known_at), max(known_at) "
        "FROM prices_daily WHERE close = 21.0 GROUP BY 1 ORDER BY 1",
    )
    assert len(stamps) == 3
    # One stamp per month, strictly later month by month, never the run start.
    assert all(lo == hi and lo > start for _, lo, hi in stamps)
    assert stamps[0][1] < stamps[1][1] < stamps[2][1]


def test_a_failure_inside_the_commit_rolls_the_month_back(settings: Settings) -> None:
    # An action stamped after the run's clock is refused by the writer after
    # May's bars are inserted: the transaction must drop those bars too.
    ex = date(2019, 5, 15)
    future = CorporateAction(ACME, ActionType.DIVIDEND, ex, 0.1, NOW + timedelta(days=1), "alpaca")
    result = _backfill(settings, _History(actions=[future]))
    assert result.runs[-1].status == FAILED and "not knowable" in result.runs[-1].message
    assert _read(settings, "SELECT max(session) FROM prices_daily") == [(date(2019, 4, 30),)]


def test_a_delisted_name_is_fetched_through_its_effective_month_only(settings: Settings) -> None:
    form_25 = DelistingFiling(
        ACME, "25", "Common Stock", "NYSE", f"{ACME}-19-000025", _at(2019, 5, 6), date(2019, 5, 16)
    )
    prices = _History(gaps={(ACME, s) for s in _sessions(date(2019, 5, 17), date(2019, 6, 30))})
    result = _backfill(settings, prices, filings=_filings(delistings=[form_25]))
    assert result.ok, result.runs[-1].message
    assert [ACME in prices.fetched[m] for m in sorted(prices.fetched)] == [True, True, False]


def test_a_transferred_name_is_fetched_under_one_security_id(settings: Settings) -> None:
    form_25 = DelistingFiling(
        ACME, "25", "Common Stock", "NYSE", f"{ACME}-19-000025", _at(2019, 5, 6), date(2019, 5, 16)
    )
    moved = CoverPage(
        ACME,
        f"{ACME}-19-000030",
        _at(2019, 5, 8),
        (CoverListing("Common Stock", "ACME", "NASDAQ"),),
    )
    filings = _filings(delistings=[form_25])
    filings._cover_pages = sorted(
        [*filings._cover_pages, moved], key=lambda e: (e.accepted_at, e.accession)
    )
    prices = _History()
    assert _backfill(settings, prices, filings=filings).ok
    assert all(ACME in ids for ids in prices.fetched.values())


def test_a_month_with_too_many_names_missing_is_stale(settings: Settings) -> None:
    # One of four listed names (25%) returns nothing for May: over 5%.
    prices = _History(gaps={(ACME, s) for s in _sessions(date(2019, 5, 1), date(2019, 5, 31))})
    result = _backfill(settings, prices)
    assert result.runs[-1].status == STALE and ACME in result.runs[-1].message
    assert _read(settings, "SELECT max(session) FROM prices_daily") == [(date(2019, 4, 30),)]


def test_a_name_delisted_after_the_month_still_counts_toward_its_staleness(
    settings: Settings,
) -> None:
    # quant-auditor re-check on #171: ACME trades all of May and is delisted
    # in June; a May that returns no ACME bars must be stale, not ok.
    form_25 = DelistingFiling(
        ACME, "25", "Common Stock", "NYSE", f"{ACME}-19-000025", _at(2019, 6, 10), date(2019, 6, 20)
    )
    prices = _History(gaps={(ACME, s) for s in _sessions(date(2019, 5, 1), date(2019, 5, 31))})
    result = _backfill(settings, prices, filings=_filings(delistings=[form_25]))
    assert (result.runs[-1].status, result.runs[-1].chunk_cursor) == (
        STALE,
        "since=2019-04-10;through=2019-05-31",
    )
    assert ACME in result.runs[-1].message
