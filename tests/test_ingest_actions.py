"""Tests for writing corporate actions by identity (#181, on #108's store side).

`ingest._add_actions` is exercised on an in-memory store with `_Replay`, a
`PriceSource` that, like the fixture adapter, returns every record it holds
whose own `ex_date` is in the requested range. Each scenario is a sequence of
runs, each at its own `ingested_at`; the reads are `live_actions_as_of`, so
"applied twice" is two live events for one real action.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import load_universe_fixtures

from tradepartner.adapters.fixture_prices import FixturePriceSource
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    action_first_seen_known_at,
)
from tradepartner.backfill import month_windows
from tradepartner.ingest import _add_actions, _replay_actions, _replay_plan
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.schema import init_schema

UNIVERSE_DIR = Path(__file__).resolve().parent / "fixtures" / "universe"
SID = "SEC_A"
MAY = (date(2019, 5, 1), date(2019, 5, 31))
JUNE = (date(2019, 6, 1), date(2019, 6, 28))
T0 = datetime(2019, 6, 29, 2, 0, tzinfo=UTC)
T1, T2, T3 = T0 + timedelta(days=1), T0 + timedelta(days=2), T0 + timedelta(days=3)


@dataclass
class _Replay(PriceSource):
    """Every held action whose own ex-date is in range, as the fixture adapter
    replays them; `calls` records each actions request."""

    actions: list[CorporateAction] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], date, date]] = field(default_factory=list)

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return []

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        self.calls.append((tuple(security_ids), start, end))
        return sorted(
            (
                a
                for a in self.actions
                if a.security_id in security_ids and start <= a.ex_date <= end
            ),
            key=lambda a: (a.security_id, a.action_type.value, a.ex_date, a.known_at),
        )


def _action(
    ex: date,
    *,
    kind: ActionType = ActionType.SPLIT,
    value: float = 2.0,
    source_id: str | None = None,
    known_at: datetime | None = None,
    cancelled: bool = False,
) -> CorporateAction:
    return CorporateAction(
        SID,
        kind,
        ex,
        value,
        known_at or action_first_seen_known_at(ex),
        "alpaca",
        source_action_id=source_id,
        cancelled=cancelled,
    )


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    store = duckdb.connect(":memory:")
    init_schema(store)
    store.execute("SET TimeZone='UTC'")
    return store


def _run(
    conn: duckdb.DuckDBPyConnection,
    source: _Replay,
    window: tuple[date, date],
    at: datetime,
) -> int:
    actions, covered = _replay(conn, source, source.corporate_actions([SID], *window), window)
    return _add_actions(conn, actions, window, ingested_at=at, covered=covered)


def _replay(
    conn: duckdb.DuckDBPyConnection,
    source: PriceSource,
    actions: list[CorporateAction],
    window: tuple[date, date],
) -> tuple[list[CorporateAction], tuple[date, date]]:
    return _replay_actions(source, actions, window, _replay_plan(conn, actions, window))


def _live(conn: duckdb.DuckDBPyConnection, t: datetime) -> list[tuple[date, str, float]]:
    frame = live_actions_as_of(conn, t)
    return [
        (row["ex_date"], row["source_action_id"], row["ratio_or_amount"])
        for row in frame.iter_rows(named=True)
    ]


def _rows(conn: duckdb.DuckDBPyConnection) -> list[tuple[object, ...]]:
    return conn.execute(
        "SELECT ex_date, source_action_id, cancelled, known_at FROM corporate_actions "
        "ORDER BY known_at, source_action_id, ex_date"
    ).fetchall()


# --- identity by source id ----------------------------------------------------


def test_a_re_dated_action_with_an_id_is_one_event(conn: duckdb.DuckDBPyConnection) -> None:
    old, new = date(2019, 6, 14), date(2019, 6, 21)
    assert _run(conn, _Replay([_action(old, source_id="a1")]), JUNE, T0) == 1
    assert _run(conn, _Replay([_action(new, source_id="a1")]), JUNE, T1) == 1
    assert _live(conn, T0) == [(old, "a1", 2.0)]
    assert _live(conn, T1) == [(new, "a1", 2.0)]
    assert _rows(conn)[-1] == (new, "a1", False, T1)


def test_a_re_date_out_of_the_window_is_a_revision_not_a_first_seen_row(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # A source that serves only the current record: in June it has moved the
    # May action to June 3. The stored May row is found by its id.
    old, new = date(2019, 5, 20), date(2019, 6, 3)
    _run(conn, _Replay([_action(old, source_id="a1")]), MAY, T0)
    assert _run(conn, _Replay([_action(new, source_id="a1")]), JUNE, T1) == 1
    assert _rows(conn) == [
        (old, "a1", False, action_first_seen_known_at(old)),
        (new, "a1", False, T1),  # at ingest, never the June proxy (look-ahead)
    ]
    assert _live(conn, T1) == [(new, "a1", 2.0)]


def test_replaying_the_old_ex_date_does_not_revert_the_re_date(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # A replay source filters each revision on its own ex-date: a June window
    # returns only the June 20 revision after the event moved to May 30.
    first = _action(date(2019, 6, 20), source_id="a1")
    moved = _action(date(2019, 5, 30), source_id="a1", known_at=T1)
    _run(conn, _Replay([first]), JUNE, T0)
    source = _Replay([first, moved])
    assert _run(conn, source, MAY, T1) == 1
    source.calls.clear()
    for at in (T2, T3):  # and on re-run
        assert _run(conn, source, JUNE, at) == 0
    assert _live(conn, T3) == [(date(2019, 5, 30), "a1", 2.0)]
    # The replay window was widened to take in the stored May 30 ex-date.
    assert (("SEC_A",), date(2019, 5, 30), JUNE[1]) in source.calls


def test_a_source_cancel_withdraws_the_event(conn: duckdb.DuckDBPyConnection) -> None:
    ex = date(2019, 6, 14)
    _run(conn, _Replay([_action(ex, source_id="a1")]), JUNE, T0)
    cancel = _action(ex, source_id="a1", cancelled=True)
    assert _run(conn, _Replay([cancel]), JUNE, T1) == 1
    assert _run(conn, _Replay([cancel]), JUNE, T2) == 0
    assert _live(conn, T0) == [(ex, "a1", 2.0)]
    assert _live(conn, T1) == []


def test_a_cancel_of_an_event_never_stored_adds_nothing(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    cancel = _action(date(2019, 6, 14), source_id="a1", cancelled=True)
    assert _run(conn, _Replay([cancel]), JUNE, T0) == 0
    assert _rows(conn) == []


# --- id-less re-dates and a key gaining an id ---------------------------------


def test_an_id_less_re_date_is_a_cancel_and_a_replacement_at_ingest(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    old, new = date(2019, 6, 14), date(2019, 6, 21)
    _run(conn, _Replay([_action(old)]), JUNE, T0)
    assert _run(conn, _Replay([_action(new)]), JUNE, T1) == 2
    assert _rows(conn)[1:] == [(old, "", True, T1), (new, "", False, T1)]
    assert _live(conn, T1 - timedelta(microseconds=1)) == [(old, "", 2.0)]
    assert _live(conn, T1) == [(new, "", 2.0)]
    assert _run(conn, _Replay([_action(new)]), JUNE, T2) == 0


def test_an_id_less_key_that_gains_an_id_is_one_event(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    ex = date(2019, 6, 14)
    _run(conn, _Replay([_action(ex)]), JUNE, T0)
    assert _run(conn, _Replay([_action(ex, source_id="a1")]), JUNE, T1) == 2
    assert _rows(conn)[1:] == [(ex, "", True, T1), (ex, "a1", False, T1)]
    assert _live(conn, T1) == [(ex, "a1", 2.0)]
    assert _run(conn, _Replay([_action(ex, source_id="a1")]), JUNE, T2) == 0


def test_an_absent_action_is_not_cancelled(conn: duckdb.DuckDBPyConnection) -> None:
    # Absence is not evidence of withdrawal: only a replacement pairs with it.
    ex = date(2019, 6, 14)
    _run(conn, _Replay([_action(ex, kind=ActionType.DIVIDEND, value=0.1)]), JUNE, T0)
    assert _run(conn, _Replay([]), JUNE, T1) == 0
    assert _live(conn, T1) == [(ex, "", 0.1)]


def test_ambiguous_id_less_changes_are_not_paired(conn: duckdb.DuckDBPyConnection) -> None:
    # Two stored keys gone and two new ones for the same security and type:
    # no pairing is certain, so nothing is cancelled and the new keys are
    # first seen on their own stamps.
    gone = [date(2019, 6, 3), date(2019, 6, 4)]
    new = [date(2019, 6, 20), date(2019, 6, 21)]
    _run(conn, _Replay([_action(d) for d in gone]), JUNE, T0)
    assert _run(conn, _Replay([_action(d) for d in new]), JUNE, T1) == 2
    stamps = [row[3] for row in _rows(conn)[2:]]
    assert stamps == [action_first_seen_known_at(d) for d in new]
    assert not any(row[2] for row in _rows(conn))


def test_a_new_action_of_another_type_is_not_a_replacement(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    split, dividend = date(2019, 6, 14), date(2019, 6, 21)
    _run(conn, _Replay([_action(split)]), JUNE, T0)
    new = _action(dividend, kind=ActionType.DIVIDEND, value=0.1)
    assert _run(conn, _Replay([new]), JUNE, T1) == 1
    assert _live(conn, T1) == [(dividend, "", 0.1), (split, "", 2.0)]


def test_a_rerun_of_every_revision_adds_nothing(conn: duckdb.DuckDBPyConnection) -> None:
    ex = date(2019, 6, 14)
    base = _action(ex, source_id="a1")
    _run(conn, _Replay([base]), JUNE, T0)
    source = _Replay([base, replace(base, ratio_or_amount=3.0, known_at=T1)])
    assert _run(conn, source, JUNE, T1) == 1
    assert _run(conn, source, JUNE, T2) == 0
    assert _live(conn, T2) == [(ex, "a1", 3.0)]


def test_two_ids_on_one_id_less_key_cancel_it_once(conn: duckdb.DuckDBPyConnection) -> None:
    # A regular and a special dividend on one ex-date, first stored without ids.
    ex = date(2019, 6, 14)
    div = ActionType.DIVIDEND
    _run(conn, _Replay([_action(ex, kind=div, value=0.1)]), JUNE, T0)
    both = [
        _action(ex, kind=div, value=0.1, source_id="a1"),
        _action(ex, kind=div, value=0.5, source_id="a2"),
    ]
    assert _run(conn, _Replay(both), JUNE, T1) == 3
    assert _rows(conn)[1:] == [(ex, "", True, T1), (ex, "a1", False, T1), (ex, "a2", False, T1)]
    assert _live(conn, T1) == [(ex, "a1", 0.1), (ex, "a2", 0.5)]
    assert _run(conn, _Replay(both), JUNE, T2) == 0


def test_an_id_less_key_that_gains_an_id_and_a_new_ex_date_is_one_event(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    old, new = date(2019, 6, 14), date(2019, 6, 21)
    _run(conn, _Replay([_action(old)]), JUNE, T0)
    assert _run(conn, _Replay([_action(new, source_id="a1")]), JUNE, T1) == 2
    assert _rows(conn)[1:] == [(old, "", True, T1), (new, "a1", False, T1)]
    assert _live(conn, T1) == [(new, "a1", 2.0)]


def test_an_id_less_re_date_across_months_is_not_paired(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # A documented limit: an id-less key is only looked for inside the window,
    # so a re-date out of it leaves both keys live. Alpaca gives ids.
    old, new = date(2019, 5, 30), date(2019, 6, 3)
    _run(conn, _Replay([_action(old)]), MAY, T0)
    assert _run(conn, _Replay([_action(new)]), JUNE, T1) == 1
    assert _live(conn, T1) == [(old, "", 2.0), (new, "", 2.0)]


def test_a_store_changed_since_the_replay_was_planned_fails_the_write(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # The replay was planned over June only; meanwhile a1 was stored in May.
    _run(conn, _Replay([_action(date(2019, 5, 20), source_id="a1")]), MAY, T0)
    june = [_action(date(2019, 6, 3), source_id="a1")]
    with pytest.raises(ValueError, match="re-run"):
        _add_actions(conn, june, JUNE, ingested_at=T1, covered=JUNE)
    assert len(_rows(conn)) == 1


def test_a_replayed_revision_not_yet_known_is_not_applied(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    ex = date(2019, 6, 14)
    base = _action(ex, source_id="a1")
    _run(conn, _Replay([base]), JUNE, T0)
    source = _Replay([base, replace(base, ratio_or_amount=3.0, known_at=T2)])
    assert _run(conn, source, JUNE, T1) == 0
    assert _run(conn, source, JUNE, T2) == 1
    assert _rows(conn)[-1][3] == T2


# --- the fixture universe's own cases ----------------------------------------


def test_replaying_the_fixture_month_by_month_leaves_its_live_events(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # SEC_SPLIT_REDATED (id re-date), SEC_SPLIT_REDATED_NOID (id-less re-date
    # with its cancel) and SEC_DIV_CANCELLED, plus every other action: ingest
    # of the replay, one window per month, ends with the recording's events.
    source = FixturePriceSource(UNIVERSE_DIR)
    ids = [row[0] for row in _fixture_ids()]
    at = datetime(2026, 1, 1, tzinfo=UTC)
    for first, last in month_windows(date(2017, 1, 1), date(2020, 6, 30)):
        at += timedelta(seconds=1)
        actions, covered = _replay(
            conn, source, source.corporate_actions(ids, first, last), (first, last)
        )
        _add_actions(conn, actions, (first, last), ingested_at=at, covered=covered)
    recording = duckdb.connect(":memory:")
    init_schema(recording)
    load_universe_fixtures(recording, UNIVERSE_DIR)
    columns = ["security_id", "action_type", "ex_date", "ratio_or_amount", "source_action_id"]
    want = live_actions_as_of(recording, at).select(columns)
    got = live_actions_as_of(conn, at).select(columns)
    assert got.equals(want)
    assert {"SEC_SPLIT_REDATED", "SEC_SPLIT_REDATED_NOID"} <= set(want["security_id"])
    assert "SEC_DIV_CANCELLED" not in set(want["security_id"])
    # And again: nothing new.
    for first, last in month_windows(date(2017, 1, 1), date(2020, 6, 30)):
        actions, covered = _replay(
            conn, source, source.corporate_actions(ids, first, last), (first, last)
        )
        assert _add_actions(conn, actions, (first, last), ingested_at=at, covered=covered) == 0


def _fixture_ids() -> list[tuple[str]]:
    with (UNIVERSE_DIR / "securities.csv").open() as handle:
        return [(line.split(",")[0],) for line in handle.readlines()[1:] if line.strip()]
