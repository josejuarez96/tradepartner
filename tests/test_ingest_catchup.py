"""Tests for the daily ingest's catch-up of missed sessions (#1572, #1550).

The property: a successful daily alpaca run leaves `prices_daily` with every
XNYS session from the store's first stored bar through the run's expected
session, or it fails loudly and names the missing sessions and the
`--backfill --since` command. A store seeded with the sessions each test
states, the filings of `test_ingest`, the run's fetch calls, what the store
holds afterwards and the run row are what it is about. The trading calendar,
never the weekdays, names the sessions.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb
import pytest
from test_ingest import (
    ACME,
    DUAL,
    DUAL_B,
    IDS,
    NOW,
    SESSION,
    SPY,
    _delisting,
    _filings,
    _Prices,
    _run,
    _seed_prices,
    _sessions,
)

from tradepartner.adapters.prices import Bar, bar_known_at
from tradepartner.calendar import next_session, previous_session
from tradepartner.config import Settings
from tradepartner.ingest import OK, STALE, IngestResult

# The run's expected session and the sessions before it, by the XNYS calendar:
# 2019-06-28 is a Friday, so 06-22 and 06-23 (a Saturday and a Sunday) are not
# among the sessions below.
D = SESSION  # 2019-06-28
D1 = previous_session(D)  # 2019-06-27, Thursday
D2 = previous_session(D1)  # 2019-06-26, the day the #1550 run failed
D3 = previous_session(D2)  # 2019-06-25
D6 = previous_session(previous_session(previous_session(D3)))  # 2019-06-20
D7 = previous_session(D6)  # 2019-06-19
MAY_31 = date(2019, 5, 31)  # a Friday in the month before the run's


@pytest.fixture
def read(settings: Settings) -> Callable[[str], list[tuple[Any, ...]]]:
    def query(sql: str) -> list[tuple[Any, ...]]:
        with duckdb.connect(settings.store.path, read_only=True) as conn:
            conn.execute("SET TimeZone='UTC'")
            return conn.execute(sql).fetchall()

    return query


def _store(settings: Settings, sessions: list[date]) -> None:
    """A store holding a bar of every fetched name at exactly `sessions`, over
    the master a daily alpaca run needs ("run edgar or all first")."""
    assert _run(settings, source="edgar").ok
    _seed_prices(settings, sessions, IDS, NOW)


def _alpaca(settings: Settings, prices: _Prices | None = None) -> IngestResult:
    """The daily alpaca run for `D`, its fetch pass and its run row."""
    return _run(settings, prices if prices is not None else _Prices(), source="alpaca")


def _sessions_stored(read: Callable[[str], list[tuple[Any, ...]]]) -> set[date]:
    return {row[0] for row in read("SELECT DISTINCT session FROM prices_daily")}


def test_a_missed_session_is_caught_up_by_the_run_for_the_next_one(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #1572, from #1550: sessions D-3 and D-1 stored and D-2 missing is the
    # hole a failed run leaves behind (2026-10-08, the SIP 15-minute cap). The
    # run for D ends with D-2 and D stored and status ok, D-2's bar known at
    # its own close rather than at this run (no look-ahead).
    _store(settings, [D3, D1])
    prices = _Prices()
    result = _alpaca(settings, prices)
    run = result.runs[-1]
    assert run.status == OK and result.exit_code == 0, run.message
    assert _sessions_stored(read) == {D3, D2, D1, D}
    assert ("bars", tuple(sorted(IDS)), D2, D2) in prices.calls
    assert ("bars", tuple(sorted(IDS)), D, D) in prices.calls
    assert f"catch-up: 1 session ({D2.isoformat()})" in run.message
    caught = read(f"SELECT known_at, ingested_at FROM prices_daily WHERE session = DATE '{D2}'")
    assert caught and all(known == bar_known_at(D2) for known, _ in caught)
    assert {ingested for _, ingested in caught} == {NOW}


def test_a_gap_over_the_bound_fails_and_names_the_sessions(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #1572: a store more than alpaca.max_catchup_sessions (5) sessions behind
    # is a lapse this path does not paper over: the run fails, names the
    # missing sessions and the backfill command, and stores no bar of its own.
    missed = _sessions(next_session(D7), previous_session(D))  # six, over the bound of 5
    _store(settings, [D7])
    run = _alpaca(settings).runs[-1]
    assert run.status == STALE
    for day in missed:
        assert day.isoformat() in run.message
    assert f"--backfill --since {missed[0].isoformat()}" in run.message
    assert read(f"SELECT count(*) FROM prices_daily WHERE session = DATE '{D}'") == [(0,)]


def test_a_full_run_fetches_no_extra_sessions(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #1572: a store complete through the previous session has nothing to
    # catch up, so the run asks the source for its own session alone and its
    # message says so. 06-22 and 06-23 are a Saturday and a Sunday, so the
    # store's span over them is complete without bars for either.
    _store(settings, _sessions(D6, D1))
    prices = _Prices()
    result = _alpaca(settings, prices)
    run = result.runs[-1]
    assert run.status == OK, run.message
    bars = [call for call in prices.calls if call[0] == "bars"]
    assert bars == [("bars", tuple(sorted(IDS)), D, D)]
    assert ("actions", tuple(sorted(IDS)), D.replace(day=1), D) in prices.calls
    assert "catch-up: none" in run.message
    assert _sessions_stored(read) == set(_sessions(D6, D))


def test_a_gap_at_the_bound_is_caught_up_not_failed(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #1572: the bound is how many sessions a daily run may catch up, so a
    # store missing exactly that many of them is caught up by the run.
    missed = _sessions(next_session(D6), previous_session(D))  # five, at the bound of 5
    assert len(missed) == 5
    _store(settings, [D6])
    run = _alpaca(settings).runs[-1]
    assert run.status == OK, run.message
    assert f"catch-up: {len(missed)} sessions" in run.message
    assert _sessions_stored(read) == set(_sessions(D6, D))


def test_a_caught_up_session_brings_its_month_of_actions(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #1572: a caught-up session in an earlier month asks for that month of
    # corporate actions as the backfill of that month would, so a split with
    # an ex-date in it adjusts the bars the run stores for it.
    hole = [day for day in _sessions(date(2019, 5, 30), date(2019, 6, 27)) if day != MAY_31]
    _store(settings, hole)
    prices = _Prices()
    run = _alpaca(settings, prices).runs[-1]
    assert run.status == OK, run.message
    assert ("bars", tuple(sorted(IDS)), MAY_31, MAY_31) in prices.calls
    assert ("actions", tuple(sorted(IDS)), date(2019, 5, 1), D) in prices.calls
    assert _sessions_stored(read) == {*hole, MAY_31, D}


@dataclass
class _ThinOn(_Prices):
    """`_Prices` returning, for each session in `thin`, bars of `kept` only."""

    thin: dict[date, frozenset[str]] = field(default_factory=dict)

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return [
            bar
            for bar in super().bars(security_ids, start, end)
            if bar.session not in self.thin or bar.security_id in self.thin[bar.session]
        ]


@pytest.mark.parametrize(
    "kept",
    [frozenset(), frozenset({SPY}), frozenset({ACME, DUAL, DUAL_B})],
    ids=["no-bar", "reference-only", "no-reference"],
)
def test_a_caught_up_session_that_comes_back_thin_fails_the_run(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]], kept: frozenset[str]
) -> None:
    # #1572: a missed session the fetch fills with no bar, only the reference's
    # bar, or every bar but the reference's would count as stored and never be
    # fetched again, so the run fails naming it and the backfill command, and
    # stores no bar.
    _store(settings, [D3, D1])
    run = _alpaca(settings, _ThinOn(thin={D2: kept})).runs[-1]
    assert run.status == STALE, run.message
    assert D2.isoformat() in run.message
    assert f"--backfill --since {D2.isoformat()}" in run.message
    assert _sessions_stored(read) == {D3, D1}


def test_a_name_delisted_inside_the_gap_gets_its_missed_bars(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #1572: ACME is delisted effective D-1, so the run for D would not fetch it,
    # but its D-2 bar (one of its last) is part of the hole and is caught up.
    filings = _filings(delistings=[_delisting(D1)])
    assert _run(settings, source="edgar", filings=filings).ok
    _seed_prices(settings, [D3, D1], IDS, NOW)
    prices = _Prices()
    run = _run(settings, prices, source="alpaca", filings=filings).runs[-1]
    assert run.status == OK, run.message
    assert ("bars", tuple(sorted(IDS)), D2, D2) in prices.calls
    assert ("bars", tuple(sorted(set(IDS) - {ACME})), D, D) in prices.calls
    stored = read(f"SELECT session FROM prices_daily WHERE security_id = '{ACME}'")
    assert {row[0] for row in stored} == {D3, D2, D1}
