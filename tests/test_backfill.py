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
from test_ingest import (
    ACME,
    DUAL,
    DUAL_B,
    NEWCO,
    NOT_COMMON,
    NOW,
    OTC_B,
    SPY,
    STAT,
    _at,
    _fact,
    _filings,
    _loose,
    _with_newco,
    _with_stat,
)

from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    action_first_seen_known_at,
    bar_known_at,
)
from tradepartner.backfill import (
    BACKFILL,
    FILLED,
    HALTED,
    HOLES,
    NO_HOLE,
    NOT_ALPACA,
    UNASSIGNED,
    UNKNOWN,
    NamedSecurity,
    backfill,
    fill_holes,
    month_windows,
)
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


def test_backfill_edgar_chunk_calls_record_failures_after_a_committed_ok(
    settings: Settings,
) -> None:
    """T11h: `backfill`'s edgar chunk passes `after_commit` exactly as
    `ingest_session` does (both read from `tests.test_ingest`'s
    `_RecordFailures`, imported here so the wiring is exercised, not
    re-implemented)."""
    from test_ingest import _RecordFailures

    calls: list[str] = []
    filings = _filings(cls=lambda **kw: _RecordFailures(calls, **kw))
    result = _backfill(settings, _History(), filings=filings, source="edgar")
    assert result.runs[0].status == OK
    assert calls == ["called"]


def test_backfill_check_failures_raising_fails_the_edgar_chunk(settings: Settings) -> None:
    from tradepartner.adapters.fixture_filings import FixtureFilingSource

    class Unhealthy(FixtureFilingSource):
        def check_failures(self) -> None:
            raise RuntimeError("too many failures")

    result = _backfill(settings, _History(), filings=_filings(cls=Unhealthy), source="edgar")
    assert result.runs[0].status == FAILED
    assert "too many failures" in result.runs[0].message


def test_backfill_input_validation_fails_the_edgar_chunk(
    settings: Settings, tmp_path: Path
) -> None:
    """#578: the backfill runs the same `_prefetch` gate (the crashes it
    exists for were backfills: #599, #609)."""
    from test_ingest import _CRASHES, _validating

    filings = _validating(tmp_path, _CRASHES)
    result = _backfill(settings, _History(), filings=filings, source="edgar")
    assert result.runs[0].status == FAILED
    assert "3 input(s) failed to parse" in result.runs[0].message
    assert _read(settings, "SELECT count(*) FROM securities") == [(0,)]


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


def _re_dated_across_months() -> list[CorporateAction]:
    # One Alpaca id moved from May 20 to June 3: each month's window holds one revision.
    return [
        CorporateAction(
            ACME,
            ActionType.SPLIT,
            ex,
            2.0,
            action_first_seen_known_at(ex),
            "alpaca",
            source_action_id="a1",
        )
        for ex in (date(2019, 5, 20), date(2019, 6, 3))
    ]


@dataclass
class _Widened(_History):
    """`_History` that runs `on_widened` (or raises) on a request spanning two months."""

    on_widened: Callable[[], None] | None = None
    fail_widened: bool = False

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        if start.replace(day=1) != end.replace(day=1):
            if self.fail_widened:
                raise RuntimeError("actions endpoint down on the widened window")
            if self.on_widened is not None:
                self.on_widened()
        return super().corporate_actions(security_ids, start, end)


def test_a_re_dated_action_is_one_event_and_widened_outside_the_lock(
    settings: Settings,
) -> None:
    seen: list[int] = []
    prices = _Widened(
        actions=_re_dated_across_months(),
        on_widened=lambda: seen.append(_write_from_another_process(settings.store.path)),
    )
    assert _backfill(settings, prices, clock=_ticking(NOW, timedelta(seconds=1))).ok
    assert seen == [0]  # the widened re-query ran with the write lock free
    live = _read(
        settings,
        "SELECT ex_date, source_action_id FROM corporate_actions ORDER BY known_at DESC LIMIT 1",
    )
    assert live == [(date(2019, 6, 3), "a1")]
    assert _read(settings, "SELECT count(*) FROM corporate_actions")[0][0] == 2


def test_a_failed_widened_re_query_fails_only_its_month(settings: Settings) -> None:
    prices = _Widened(actions=_re_dated_across_months(), fail_widened=True)
    result = _backfill(settings, prices, clock=_ticking(NOW, timedelta(seconds=1)))
    assert [run.status for run in result.runs if run.source == "alpaca"] == [OK, OK, FAILED]
    assert _read(settings, "SELECT ex_date FROM corporate_actions") == [(date(2019, 5, 20),)]
    assert _read(
        settings, "SELECT count(*) FROM prices_daily WHERE session >= DATE '2019-06-01'"
    ) == [(0,)]


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


MAY = _sessions(date(2019, 5, 1), date(2019, 5, 31))


@pytest.mark.parametrize(("missing", "status"), [(DUAL_B, OK), (ACME, STALE)])
def test_an_otc_common_name_is_not_counted_in_a_months_staleness(
    settings: Settings, missing: str, status: str
) -> None:
    # #784: OTC is not one of `universe.exchanges`; a NYSE name still counts.
    prices = _History(gaps={(missing, s) for s in MAY})
    result = _backfill(settings, prices, filings=_filings(dual_listings=OTC_B))
    assert result.runs[-1].status == status, result.runs[-1].message


def test_a_month_fetches_no_notes_preferreds_or_otc_listings(
    settings: Settings,
) -> None:
    # #794: a note, a preferred and an OTC listing are never fetched; a
    # common NYSE name and a benchmark are.
    prices = _History()
    filings = _filings(acme_extra=NOT_COMMON, dual_listings=OTC_B)
    result = _backfill(settings, prices, filings=filings)
    assert result.runs[-1].status == OK, result.runs[-1].message
    for fetched in prices.fetched.values():
        assert {ACME, SPY} <= fetched
        assert DUAL_B not in fetched
        assert not any(sid.startswith(f"{ACME}:") for sid in fetched)  # preferred, note


def test_a_snapshot_static_only_name_with_no_rows_in_a_month_is_reported_not_counted(
    settings: Settings,
) -> None:
    prices = _History(gaps={(STAT, s) for s in MAY})
    result = _backfill(settings, prices, filings=_with_stat())
    assert result.ok, result.runs[-1].message
    may = next(r for r in result.runs if r.chunk_cursor.endswith("through=2019-05-31"))
    assert "0 of 4 listed names without a bar" in may.message
    assert f"1 snapshot-only names with no rows (not counted): {STAT}" in may.message


def test_a_name_with_a_filing_based_span_in_the_month_still_counts(settings: Settings) -> None:
    prices = _History(gaps={(STAT, s) for s in MAY})
    result = _backfill(settings, prices, filings=_with_stat(cover=True))
    assert result.runs[-1].status == STALE and STAT in result.runs[-1].message
    assert "snapshot-only" not in result.runs[-1].message


def test_a_snapshot_static_only_name_with_some_rows_follows_the_existing_rule(
    settings: Settings,
) -> None:
    # Bars on some May sessions: not missing, so neither counted nor reported.
    prices = _History(gaps={(STAT, s) for s in MAY[1:]})
    result = _backfill(settings, prices, filings=_with_stat())
    assert result.ok, result.runs[-1].message
    may = next(r for r in result.runs if r.chunk_cursor.endswith("through=2019-05-31"))
    assert "0 of 5 listed names without a bar" in may.message
    assert "snapshot-only" not in may.message


def test_a_benchmark_with_no_rows_in_a_month_still_counts(settings: Settings) -> None:
    tuned = settings.model_copy(
        update={"ingest": settings.ingest.model_copy(update={"reference_symbol": "ACME"})}
    )
    result = _backfill(tuned, _History(gaps={(SPY, s) for s in MAY}))
    assert result.runs[-1].status == STALE and SPY in result.runs[-1].message
    assert "snapshot-only" not in result.runs[-1].message


APRIL_END = datetime(2019, 5, 1, 2, 0, tzinfo=UTC)  # expected session 2019-04-30
APRIL = _sessions(SINCE, date(2019, 4, 30))


def test_a_name_dark_since_before_the_month_is_reported_not_counted(settings: Settings) -> None:
    # #784 (dark names): ACME has no bar in April (committed under a looser
    # share), so its May and June misses are reported, not counted.
    early = _filings(fetched_at=_at(2019, 4, 1))
    dark = {(ACME, s) for s in _sessions(SINCE, date(2019, 6, 30))}
    first = _backfill(_loose(settings), _History(gaps=dark), filings=early, now=APRIL_END)
    assert first.ok, first.runs[-1].message
    result = _backfill(settings, _History(gaps=dark), filings=early)
    assert result.ok, result.runs[-1].message
    may = next(r for r in result.runs if r.chunk_cursor.endswith("through=2019-05-31"))
    assert "0 of 3 listed names without a bar" in may.message
    assert f"1 names with no bar in the previous chunk (not counted): {ACME}" in may.message


def test_too_many_dark_names_make_the_month_stale(settings: Settings) -> None:
    # #796 (i a): ACME dark since April is 1 of 4 listed names in May.
    early = _filings(fetched_at=_at(2019, 4, 1))
    dark = {(ACME, s) for s in _sessions(SINCE, date(2019, 6, 30))}
    assert _backfill(_loose(settings), _History(gaps=dark), filings=early, now=APRIL_END).ok
    tuned = settings.model_copy(
        update={"ingest": settings.ingest.model_copy(update={"max_dark_share": 0.2})}
    )
    result = _backfill(tuned, _History(gaps=dark), filings=early)
    assert (result.runs[-1].status, result.runs[-1].chunk_cursor) == (
        STALE,
        "since=2019-04-10;through=2019-05-31",
    )
    message = result.runs[-1].message
    assert "1 of 4 listed names (25.0%, over 20.0%) are dark or snapshot-only" in message
    assert ACME in message


def test_a_name_with_a_bar_last_month_and_none_now_counts(settings: Settings) -> None:
    result = _backfill(settings, _History(gaps={(ACME, s) for s in MAY}))
    assert (result.runs[-1].status, result.runs[-1].chunk_cursor) == (
        STALE,
        "since=2019-04-10;through=2019-05-31",
    )
    assert ACME in result.runs[-1].message


def test_a_name_first_listed_in_the_month_with_no_bar_counts(settings: Settings) -> None:
    filings = _with_newco(_at(2019, 5, 1))
    result = _backfill(settings, _History(gaps={(NEWCO, s) for s in MAY}), filings=filings)
    assert result.runs[-1].status == STALE and NEWCO in result.runs[-1].message


def test_backfill_run_messages_are_redacted(tmp_path: Path) -> None:
    secret = "sk-sentinel-backfill"
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        alpaca_api_key=secret,
    )

    class Leaky(_History):
        def corporate_actions(
            self, security_ids: Sequence[str], start: date, end: date
        ) -> list[CorporateAction]:
            raise RuntimeError(f"401 for key {secret}")

    result = _backfill(settings, Leaky())
    stored = _read(settings, "SELECT message FROM ingestion_runs WHERE source = 'alpaca'")
    assert result.runs[-1].status == FAILED
    assert all(secret not in m for (m,) in stored) and "[redacted]" in stored[0][0]


# --- #573: a failed run row names where the error was raised --------------


def test_backfill_failed_run_message_names_the_raising_file_line_and_function(
    settings: Settings,
) -> None:
    class Boom(_History):
        def corporate_actions(
            self, security_ids: Sequence[str], start: date, end: date
        ) -> list[CorporateAction]:
            raise RuntimeError("actions endpoint down")

    result = _backfill(settings, Boom())
    assert result.runs[-1].status == FAILED
    message = result.runs[-1].message
    assert "RuntimeError: actions endpoint down" in message
    assert " | at: " in message
    where = message.split(" | at: ", 1)[1]
    assert "test_backfill.py" in where
    assert " in corporate_actions" in where


def test_backfill_failed_run_message_has_no_local_or_argument_values(
    settings: Settings,
) -> None:
    def _inner(argument: str) -> None:
        local_secret = "sk-local-backfill-5c1a"
        assert local_secret  # kept "in scope" for the frame, never read back
        raise ValueError("boom")

    class Boom(_History):
        def corporate_actions(
            self, security_ids: Sequence[str], start: date, end: date
        ) -> list[CorporateAction]:
            _inner("sk-argument-backfill-9d4e")

    result = _backfill(settings, Boom())
    message = result.runs[-1].message
    assert result.runs[-1].status == FAILED
    assert "ValueError: boom" in message
    assert "sk-local-backfill-5c1a" not in message
    assert "sk-argument-backfill-9d4e" not in message
    assert "_inner" in message


def test_backfill_failed_run_message_includes_the_chained_causes_frame(
    settings: Settings,
) -> None:
    def _root_cause() -> None:
        raise KeyError("cik")

    class Boom(_History):
        def corporate_actions(
            self, security_ids: Sequence[str], start: date, end: date
        ) -> list[CorporateAction]:
            try:
                _root_cause()
            except KeyError as exc:
                raise RuntimeError("wrapped") from exc

    result = _backfill(settings, Boom())
    message = result.runs[-1].message
    assert result.runs[-1].status == FAILED
    where = message.split(" | at: ", 1)[1]
    assert "_root_cause" in where
    assert "corporate_actions" in where


def test_backfill_failed_run_message_where_is_length_bounded(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        ingest={"max_where_chars": 60},
    )

    def _deep(n: int) -> None:
        if n == 0:
            raise RuntimeError("deep failure")
        _deep(n - 1)

    class Boom(_History):
        def corporate_actions(
            self, security_ids: Sequence[str], start: date, end: date
        ) -> list[CorporateAction]:
            _deep(20)

    result = _backfill(settings, Boom())
    message = result.runs[-1].message
    assert result.runs[-1].status == FAILED
    assert "RuntimeError: deep failure" in message
    assert len(message) <= 60


def test_the_edgar_commit_never_reaches_the_source() -> None:
    from tradepartner.ingest import _prefetch, _Recorded

    recorded = _Recorded(_filings())
    _prefetch(recorded, Settings(_env_file=None), dry_run=False)
    with pytest.raises(RuntimeError, match="after the fetch pass"):
        recorded.facts(ACME, ["SomethingNew"])


# --- #735: the resolver's exclusions are counted on the run row ---------------


@dataclass
class _Resolving(_History):
    def resolution_summary(self) -> str:
        return "resolver left out 3 placeholder-ticker listings"


def test_the_resolution_summary_is_on_every_backfill_price_run(settings: Settings) -> None:
    result = _backfill(settings, _Resolving())
    alpaca = [run for run in result.runs if run.source == "alpaca"]
    assert alpaca and all("resolver left out 3" in run.message for run in alpaca)


def test_the_resolution_summary_is_on_the_daily_price_run(settings: Settings) -> None:
    result = ingest_session(settings, prices=_Resolving(), filings=_filings(), clock=lambda: NOW)
    assert result.ok, result.runs[-1].message
    assert "resolver left out 3" in result.runs[-1].message


# --- refetching holes (#831) --------------------------------------------------

LATER = NOW + timedelta(days=2)
MAY_WINDOW = (date(2019, 5, 1), date(2019, 5, 31))
JUNE_WINDOW = (date(2019, 6, 1), date(2019, 6, 28))


def _drop_bars(settings: Settings, sid: str, window: tuple[date, date]) -> None:
    """Make a hole: the store loses `sid`'s bars in `window` (as a stale
    listing end once kept the backfill from fetching them)."""
    with duckdb.connect(settings.store.path) as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session BETWEEN ? AND ?",
            [sid, *window],
        )


def _holes(result: Any) -> list[tuple[str, tuple[date, date]]]:
    return [(hole.security_id, hole.window) for hole in result.holes]


def test_a_dry_run_lists_live_months_with_no_stored_bar_and_changes_nothing(
    settings: Settings,
) -> None:
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    before = _read(settings, "SELECT count(*) FROM prices_daily")
    runs = _read(settings, "SELECT count(*) FROM ingestion_runs")
    prices = _History()
    found = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, MAY_WINDOW)]
    assert found.holes[0].ticker == "ACME"
    assert found.runs == () and found.exit_code == 0
    assert prices.calls == []
    assert _read(settings, "SELECT count(*) FROM prices_daily") == before
    assert _read(settings, "SELECT count(*) FROM ingestion_runs") == runs


def test_only_months_the_backfill_committed_are_searched(settings: Settings) -> None:
    # A backfill halted after April: May and June are the resume's job.
    prices = _History(gaps={(SPY, date(2019, 5, 15))})
    assert not _backfill(settings, prices).ok
    _drop_bars(settings, ACME, (date(2019, 4, 10), date(2019, 4, 30)))
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, (date(2019, 4, 10), date(2019, 4, 30)))]
    other = fill_holes(
        settings, prices=_History(), since=date(2019, 4, 11), clock=lambda: LATER, dry_run=True
    )
    assert other.holes == ()  # no committed month for that --since


def test_filling_fetches_only_the_holes_and_the_reference_and_records_its_own_run(
    settings: Settings,
) -> None:
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    prices = _History()
    result = fill_holes(
        settings, prices=prices, since=SINCE, clock=_ticking(LATER, timedelta(minutes=1))
    )
    assert result.exit_code == 0, result.runs
    assert [r.status for r in result.runs] == [FILLED]
    # Staleness counts the stored bars of the names not fetched (DUAL, DUAL_B).
    assert prices.fetched == {date(2019, 5, 1): {ACME, SPY}}
    assert [c[1:] for c in prices.calls if c[0] == "actions"] == [MAY_WINDOW]
    rows = _read(
        settings,
        f"SELECT session, known_at, ingested_at FROM prices_daily WHERE security_id = '{ACME}' "
        "AND session BETWEEN DATE '2019-05-01' AND DATE '2019-05-31' ORDER BY session",
    )
    assert [r[0] for r in rows] == _sessions(*MAY_WINDOW)
    # First-seen bars take the timing rule's stamp; the fetch is `ingested_at`.
    assert all(known == bar_known_at(s) and ingested > LATER for s, known, ingested in rows)
    holes_runs = _read(
        settings,
        f"SELECT status, mode, chunk_cursor FROM ingestion_runs WHERE mode = '{HOLES}'",
    )
    assert holes_runs == [(FILLED, HOLES, "holes;since=2019-04-10;through=2019-05-31")]
    # Never an `ok` row: a hole fill is not a fresh ingest.
    assert _read(
        settings, f"SELECT count(*) FROM ingestion_runs WHERE mode = '{HOLES}' AND status = 'ok'"
    ) == [(0,)]


def test_a_filled_store_has_no_holes_and_the_backfill_still_resumes_to_nothing(
    settings: Settings,
) -> None:
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    assert fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER).exit_code == 0
    listed = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert listed.holes == ()
    prices = _History()
    again = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER)
    assert again.runs == () and prices.calls == []
    resumed = _History()
    assert _backfill(settings, resumed, now=LATER, source="alpaca").ok
    assert [c for c in resumed.calls if c[0] == "bars"] == []


def test_a_month_without_holes_is_not_fetched(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    prices = _History()
    result = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER)
    assert [r.chunk_cursor for r in result.runs] == ["holes;since=2019-04-10;through=2019-06-28"]
    assert set(prices.fetched) == {date(2019, 6, 1)}


def test_a_hole_fill_with_a_reference_gap_is_stale_and_writes_no_bar(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    prices = _History(gaps={(SPY, date(2019, 5, 15))})
    result = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER)
    assert [r.status for r in result.runs] == [STALE] and result.exit_code == 1
    assert _read(
        settings,
        f"SELECT count(*) FROM prices_daily WHERE security_id = '{ACME}' "
        "AND session BETWEEN DATE '2019-05-01' AND DATE '2019-05-31'",
    ) == [(0,)]
    assert _read(settings, f"SELECT status FROM ingestion_runs WHERE mode = '{HOLES}'") == [
        (STALE,)
    ]


def test_a_hole_the_source_still_cannot_fill_is_stale_by_the_existing_share(
    settings: Settings,
) -> None:
    # One of four counted names (25%) has no bar in May even after the fetch.
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    prices = _History(gaps={(ACME, s) for s in _sessions(*MAY_WINDOW)})
    result = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER)
    assert [r.status for r in result.runs] == [STALE]
    assert ACME in result.runs[0].message


def test_holes_are_read_as_of_the_clock(settings: Settings) -> None:
    # NEWCO is first listed by a cover page accepted on 2019-06-27: before
    # that is known, its missing June bars are no hole (no look-ahead).
    accepted = _at(2019, 6, 27)
    assert _backfill(settings, _History(), filings=_with_newco(accepted)).ok
    _drop_bars(settings, NEWCO, JUNE_WINDOW)
    early = fill_holes(
        settings,
        prices=_History(),
        since=SINCE,
        clock=lambda: accepted - timedelta(hours=1),
        dry_run=True,
    )
    assert early.holes == ()
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(NEWCO, JUNE_WINDOW)]


def test_a_bar_known_after_the_clock_does_not_close_a_hole(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    with duckdb.connect(settings.store.path) as conn:
        conn.execute(
            "UPDATE prices_daily SET known_at = $1, ingested_at = $1 WHERE security_id = $2 "
            "AND session BETWEEN $3 AND $4",
            [LATER + timedelta(days=1), ACME, *MAY_WINDOW],
        )
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, MAY_WINDOW)]


def test_hole_lines_group_months_and_mark_those_between_stored_bars(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    _drop_bars(settings, DUAL, MAY_WINDOW)
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert found.summary().startswith("3 holes (security, month) over 2 securities")
    assert found.lines() == [
        f"  {ACME} ACME: 2019-05 (1 months, 1 between its stored bars)",
        f"  {DUAL} DUA: 2019-05..2019-06 (2 months, 0 between its stored bars)",
    ]


def test_a_listing_that_now_runs_on_makes_a_hole_that_the_fill_refetches(
    settings: Settings,
) -> None:
    # End to end: a Form 25 ended ACME in May, so June was never fetched.
    # A later EDGAR run learns that ACME moved to NASDAQ instead (a cover
    # page accepted before the Form 25 took effect): ACME now runs on
    # through June, which the store has no bar for.
    form_25 = DelistingFiling(
        ACME, "25", "Common Stock", "NYSE", f"{ACME}-19-000025", _at(2019, 5, 6), date(2019, 5, 16)
    )
    gone = {(ACME, s) for s in _sessions(date(2019, 5, 17), date(2019, 6, 30))}
    assert _backfill(settings, _History(gaps=gone), filings=_filings(delistings=[form_25])).ok
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
    assert _backfill(settings, _History(), filings=filings, now=LATER, source="edgar").ok
    clock = LATER + timedelta(hours=1)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: clock, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]
    prices = _History()
    result = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: clock)
    assert [r.status for r in result.runs] == [FILLED]
    assert prices.fetched == {date(2019, 6, 1): {ACME, SPY}}
    assert _read(
        settings,
        f"SELECT count(*) FROM prices_daily WHERE security_id = '{ACME}' "
        "AND session BETWEEN DATE '2019-06-01' AND DATE '2019-06-28'",
    ) == [(len(_sessions(*JUNE_WINDOW)),)]


# --- holes the resolver cannot assign, and named securities (#876) -----------

SPAC = "0000000005"  # SIC 6770: its unit and warrant class ids are typed spac
SPAC_UNITS = f"{SPAC}:units"
SPAC_WARRANTS = f"{SPAC}:redeemable-warrants"
TWIN, TWIN_2 = "0000000006", "0000000007"  # both list TWIN on one day: ambiguous
ODD = "0000000008"  # a common class under "ODD1", not an Alpaca symbol
UNASSIGNABLE = (SPAC_UNITS, SPAC_WARRANTS, TWIN, TWIN_2, ODD)


def _with_unassignable() -> Any:
    """`_filings` plus ids the fetch set holds but no fetched bar can land
    on: a SPAC's units and warrants (non-equity rows the resolver drops),
    two companies listing one ticker on one day (ambiguous), and a common
    class whose ticker has no Alpaca symbol."""
    units = (
        "Units, each consisting of one share of Class A Common Stock and one-half of one warrant"
    )
    companies = [
        (
            SPAC,
            6770,
            5,
            (
                CoverListing(units, "SPCU", "NASDAQ"),
                CoverListing("Class A Common Stock", "SPC", "NASDAQ"),
                CoverListing("Redeemable Warrants", "SPCW", "NASDAQ"),
            ),
        ),
        (TWIN, 3571, 7, (CoverListing("Common Stock", "TWIN", "NYSE"),)),
        (TWIN_2, 3571, 7, (CoverListing("Common Stock", "TWIN", "NYSE"),)),
        (ODD, 3571, 8, (CoverListing("Common Stock", "ODD1", "NYSE"),)),
    ]
    index, headers, covers, facts = [], [], [], []
    for cik, sic, day, listings in companies:
        accession = f"{cik}-19-000001"
        accepted = _at(2019, 3, day)
        index.append(FilingIndexEntry(cik, f"Co {cik}", "10-K", accession, accepted))
        headers.append(FilingHeader(cik, accession, "10-K", sic, accepted))
        covers.append(CoverPage(cik, accession, accepted, listings))
        facts.append(_fact(cik, "", 1_000_000, accession, accepted))
    return _filings(
        extra_index=index, extra_headers=headers, extra_covers=covers, extra_facts=facts
    )


def _unassignable_holes(settings: Settings, ids: Sequence[str] = UNASSIGNABLE) -> None:
    """A backfill with the unassignable ids (the stub source has bars for
    every id), then ACME and each of `ids` lose May."""
    assert _backfill(settings, _History(), filings=_with_unassignable()).ok
    for sid in (ACME, *ids):
        _drop_bars(settings, sid, MAY_WINDOW)


def test_a_dry_run_lists_only_holes_the_resolver_can_assign_and_counts_the_rest(
    settings: Settings,
) -> None:
    _unassignable_holes(settings)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, MAY_WINDOW)]
    assert dict(found.dropped) == {UNASSIGNED: 4, NOT_ALPACA: 1}
    assert found.summary().endswith(
        f"; 5 holes the resolver cannot assign, not fetched (1 {NOT_ALPACA}, 4 {UNASSIGNED})"
    )
    assert found.named == ()


def test_a_fill_fetches_no_hole_the_resolver_cannot_assign(settings: Settings) -> None:
    # ODD stays without a bar: 1 of 7 counted names is under the loosened
    # share, so May fills (a counted name the fill skips still counts).
    loose = _loose(settings)
    _unassignable_holes(loose, (SPAC_UNITS, SPAC_WARRANTS, ODD))
    prices = _History()
    result = fill_holes(loose, prices=prices, since=SINCE, clock=lambda: LATER)
    assert [r.status for r in result.runs] == [FILLED], result.runs
    assert prices.fetched == {date(2019, 5, 1): {ACME, SPY}}
    assert result.runs[0].message.startswith(
        "holes of 1 names with no stored bar; 3 holes the resolver cannot assign, not fetched"
    )
    assert dict(result.dropped) == {UNASSIGNED: 2, NOT_ALPACA: 1}


def test_named_securities_limit_the_holes_and_each_unfetched_one_is_listed(
    settings: Settings,
) -> None:
    _unassignable_holes(settings, (TWIN,))
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    named = [ACME, TWIN, DUAL_B, "0000009999"]
    found = fill_holes(
        settings,
        prices=_History(),
        since=SINCE,
        clock=lambda: LATER,
        dry_run=True,
        securities=named,
    )
    assert _holes(found) == [(ACME, MAY_WINDOW)]  # not DUAL's June: not named
    expected = (
        NamedSecurity(DUAL_B, NO_HOLE),
        NamedSecurity(TWIN, f"1 holes not fetched (1 {UNASSIGNED})"),
        NamedSecurity("0000009999", UNKNOWN),
    )
    assert found.named == expected
    assert dict(found.dropped) == {UNASSIGNED: 1}
    assert found.lines()[-3:] == [
        f"  {DUAL_B}: not fetched, {NO_HOLE}",
        f"  {TWIN}: not fetched, 1 holes not fetched (1 {UNASSIGNED})",
        f"  0000009999: not fetched, {UNKNOWN}",
    ]
    prices = _History()
    result = fill_holes(
        _loose(settings), prices=prices, since=SINCE, clock=lambda: LATER, securities=named
    )
    assert [r.status for r in result.runs] == [FILLED], result.runs
    assert prices.fetched == {date(2019, 5, 1): {ACME, SPY}}
    assert result.named == expected
    assert [r[2] for r in _alpaca_runs(settings) if r[1] == HOLES] == [
        "holes;since=2019-04-10;through=2019-05-31"
    ]
    # No committed month for that --since: a known id has no hole, not "unknown".
    other = fill_holes(
        settings,
        prices=_History(),
        since=date(2019, 4, 11),
        clock=lambda: LATER,
        dry_run=True,
        securities=[ACME],
    )
    assert other.holes == () and other.named == (NamedSecurity(ACME, NO_HOLE),)


def test_a_named_security_in_a_month_that_halts_is_listed_as_not_filled(
    settings: Settings,
) -> None:
    _unassignable_holes(settings, ())
    prices = _History(gaps={(SPY, date(2019, 5, 15))})
    result = fill_holes(
        settings, prices=prices, since=SINCE, clock=lambda: LATER, securities=[ACME]
    )
    assert [r.status for r in result.runs] == [STALE]
    assert result.named == (NamedSecurity(ACME, HALTED),)


def test_named_securities_must_be_ids(settings: Settings) -> None:
    with pytest.raises(TypeError):
        fill_holes(settings, prices=_History(), since=SINCE, securities=ACME)
    with pytest.raises(ValueError):
        fill_holes(settings, prices=_History(), since=SINCE, securities=[])
