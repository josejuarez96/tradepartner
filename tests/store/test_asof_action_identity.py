"""Re-dated and cancelled corporate actions in the as-of reads (#108).

An action's identity is `(security_id, source_action_id)` when the source
gives an id, else `(security_id, action_type, ex_date)`. A re-dated action
(same id, new ex-date) is a revision of the same event, so the old ex-date
stops applying from the revision's `known_at` on and the event never
applies twice. A `cancelled` revision removes the event from that instant
on. Each scenario runs on a schema-only store so its numbers are exact.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of, dropped_dividends_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.universe import _split_factors

SID = "SEC_X"
#: 2021-01-04 .. 2021-01-15 are all XNYS sessions (no holiday in that span).
SESSIONS = [date(2021, 1, d) for d in (4, 5, 6, 7, 8, 11, 12, 13, 14, 15)]


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for session in SESSIONS:
        known_at = datetime(session.year, session.month, session.day, 21, 0, tzinfo=UTC)
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": SID,
                "session": session,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
                "known_at": known_at,
                "ingested_at": known_at,
                "source": "test",
                "provenance": "bar",
            },
        )
    try:
        yield conn
    finally:
        conn.close()


def _action(
    conn: duckdb.DuckDBPyConnection,
    action_type: str,
    ex_date: date,
    ratio_or_amount: float,
    *,
    known_at: datetime,
    source_action_id: str = "",
    cancelled: bool = False,
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": SID,
            "action_type": action_type,
            "ex_date": ex_date,
            "ratio_or_amount": ratio_or_amount,
            "source_action_id": source_action_id,
            "cancelled": cancelled,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "action",
        },
    )


def _closes(df: pl.DataFrame) -> dict[date, float]:
    return {row["session"]: row["close"] for row in df.iter_rows(named=True)}


def _t(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2021, 1, day, hour, minute, tzinfo=UTC)


def _close(session: date) -> datetime:
    """21:00 UTC on `session`, the XNYS close in January."""
    return datetime(session.year, session.month, session.day, 21, 0, tzinfo=UTC)


class TestRedatedAction:
    def test_redated_split_applies_once_not_twice(self, store: duckdb.DuckDBPyConnection) -> None:
        # First seen at the proxy for ex 2021-01-11; the source then moves
        # the same event (same id) to 2021-01-13.
        _action(store, "split", date(2021, 1, 11), 2.0, known_at=_t(8, 21), source_action_id="A1")
        _action(store, "split", date(2021, 1, 13), 2.0, known_at=_t(12, 22), source_action_id="A1")

        closes = _closes(adjusted_prices_as_of(store, _t(15, 22), [SID]))
        assert closes[date(2021, 1, 8)] == pytest.approx(50.0)  # halved once, not quartered
        assert closes[date(2021, 1, 12)] == pytest.approx(50.0)  # before the new ex-date
        assert closes[date(2021, 1, 13)] == pytest.approx(100.0)  # on the new ex-date: raw

    def test_old_ex_date_applies_until_the_redate_is_known(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        _action(store, "split", date(2021, 1, 11), 2.0, known_at=_t(8, 21), source_action_id="A1")
        _action(store, "split", date(2021, 1, 13), 2.0, known_at=_t(12, 22), source_action_id="A1")

        # After the old ex-date, before the re-date is known: what was
        # knowable then is the old ex-date.
        closes = _closes(adjusted_prices_as_of(store, _t(12, 21, 30), [SID]))
        assert closes[date(2021, 1, 8)] == pytest.approx(50.0)
        assert closes[date(2021, 1, 11)] == pytest.approx(100.0)

    def test_redate_into_the_future_unapplies_the_old_ex_date(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # The latest revision's ex-date is after T, so nothing applies at T,
        # even though an older revision's ex-date is already past.
        _action(store, "split", date(2021, 1, 11), 2.0, known_at=_t(8, 21), source_action_id="A1")
        _action(store, "split", date(2021, 2, 1), 2.0, known_at=_t(12, 22), source_action_id="A1")

        closes = _closes(adjusted_prices_as_of(store, _t(15, 22), [SID]))
        assert set(closes.values()) == {100.0}

    def test_distinct_ids_on_one_ex_date_are_two_events(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # A regular and a special dividend on the same ex-date, each with
        # its own source id, both apply.
        _action(
            store, "dividend", date(2021, 1, 11), 1.0, known_at=_t(8, 21), source_action_id="D1"
        )
        _action(
            store, "dividend", date(2021, 1, 11), 4.0, known_at=_t(8, 21), source_action_id="D2"
        )

        closes = _closes(adjusted_prices_as_of(store, _t(15, 22), [SID], include_dividends=True))
        assert closes[date(2021, 1, 8)] == pytest.approx(100.0 * 0.99 * 0.96)

    def test_rows_without_an_id_keep_the_ex_date_key(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # No id: two ex-dates are two events (the pre-#108 behaviour, which
        # is why an id-less re-date needs a cancel row for the old key).
        _action(store, "split", date(2021, 1, 11), 2.0, known_at=_t(8, 21))
        _action(store, "split", date(2021, 1, 13), 2.0, known_at=_t(12, 21))

        closes = _closes(adjusted_prices_as_of(store, _t(15, 22), [SID]))
        assert closes[date(2021, 1, 8)] == pytest.approx(25.0)


class TestCancelledAction:
    def test_cancel_removes_the_event_from_its_known_at_on(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        _action(
            store, "dividend", date(2021, 1, 11), 2.0, known_at=_t(8, 21), source_action_id="D1"
        )
        _action(
            store,
            "dividend",
            date(2021, 1, 11),
            2.0,
            known_at=_t(13, 22),
            source_action_id="D1",
            cancelled=True,
        )

        before = _closes(adjusted_prices_as_of(store, _t(13, 21), [SID], include_dividends=True))
        assert before[date(2021, 1, 8)] == pytest.approx(98.0)
        after = _closes(adjusted_prices_as_of(store, _t(13, 22), [SID], include_dividends=True))
        assert after[date(2021, 1, 8)] == pytest.approx(100.0)

    def test_cancel_without_an_id_retires_the_ex_date_key(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # An id-less re-date: cancel the old key, first-see the new one.
        _action(store, "split", date(2021, 1, 11), 2.0, known_at=_t(8, 21))
        _action(store, "split", date(2021, 1, 11), 2.0, known_at=_t(12, 21), cancelled=True)
        _action(store, "split", date(2021, 1, 13), 2.0, known_at=_t(12, 21))

        closes = _closes(adjusted_prices_as_of(store, _t(15, 22), [SID]))
        assert closes[date(2021, 1, 8)] == pytest.approx(50.0)
        assert closes[date(2021, 1, 12)] == pytest.approx(50.0)

    def test_idless_redate_to_an_earlier_date_applies_once_and_not_early(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # Audit finding 1 on PR #111: known for ex 2021-01-13, moved on
        # 2021-01-15 to 2021-01-07. The replacement row is stamped at the
        # ingest, so before it nothing knew of 01-07.
        _action(store, "split", date(2021, 1, 13), 2.0, known_at=_t(12, 21))
        _action(store, "split", date(2021, 1, 13), 2.0, known_at=_t(15, 22), cancelled=True)
        _action(store, "split", date(2021, 1, 7), 2.0, known_at=_t(15, 22))

        early = _closes(adjusted_prices_as_of(store, _t(8, 22), [SID]))
        assert early[date(2021, 1, 6)] == pytest.approx(100.0)
        between = _closes(adjusted_prices_as_of(store, _t(14, 22), [SID]))
        assert between[date(2021, 1, 12)] == pytest.approx(50.0)
        assert between[date(2021, 1, 6)] == pytest.approx(50.0)
        after = _closes(adjusted_prices_as_of(store, _t(15, 22), [SID]))
        assert after[date(2021, 1, 6)] == pytest.approx(50.0)
        assert after[date(2021, 1, 7)] == pytest.approx(100.0)
        assert after[date(2021, 1, 12)] == pytest.approx(100.0)

    def test_cancelled_dividend_is_not_reported_as_dropped(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        # No prior bar before this ex-date, so while live it is a drop; once
        # cancelled it is simply gone.
        _action(store, "dividend", date(2021, 1, 4), 2.0, known_at=_t(1, 21))
        assert dropped_dividends_as_of(store, _t(5, 22), [SID]).height == 1
        _action(store, "dividend", date(2021, 1, 4), 2.0, known_at=_t(5, 23), cancelled=True)
        assert dropped_dividends_as_of(store, _t(6, 22), [SID]).height == 0


class TestFixtureCases:
    """The req 13 fixture cases for #108 (`tests/fixtures/universe/README.md`)."""

    @staticmethod
    def _ratio(conn: duckdb.DuckDBPyConnection, sid: str, t: datetime, session: date) -> float:
        raw = _closes(prices_as_of(conn, t, [sid]))[session]
        adjusted = _closes(adjusted_prices_as_of(conn, t, [sid], include_dividends=True))
        return adjusted[session] / raw

    def test_redated_split_applies_once_at_each_probe(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "SEC_SPLIT_REDATED"
        # Old ex-date 2019-06-10 passed, re-date (known 2019-06-12 21:00) not yet known.
        before = datetime(2019, 6, 12, 20, 59, 59, tzinfo=UTC)
        assert self._ratio(fixture_store, sid, before, date(2019, 6, 7)) == pytest.approx(0.5)
        assert self._ratio(fixture_store, sid, before, date(2019, 6, 10)) == pytest.approx(1.0)
        # Re-date known, new ex-date 2019-06-17 not yet effective: nothing applies.
        after_redate = datetime(2019, 6, 12, 21, 0, tzinfo=UTC)
        assert self._ratio(fixture_store, sid, after_redate, date(2019, 6, 7)) == pytest.approx(1.0)
        # Both ex-dates past: halved once, and only before the new ex-date.
        later = datetime(2019, 7, 1, 21, 0, tzinfo=UTC)
        assert self._ratio(fixture_store, sid, later, date(2019, 6, 7)) == pytest.approx(0.5)
        assert self._ratio(fixture_store, sid, later, date(2019, 6, 14)) == pytest.approx(0.5)
        assert self._ratio(fixture_store, sid, later, date(2019, 6, 17)) == pytest.approx(1.0)

    def test_cancelled_dividend_stops_applying_at_the_cancel(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "SEC_DIV_CANCELLED"
        cancel = datetime(2019, 9, 20, 21, 0, tzinfo=UTC)
        before = cancel - timedelta(microseconds=1)
        prior_close = _closes(prices_as_of(fixture_store, before, [sid]))[date(2019, 9, 13)]
        assert self._ratio(fixture_store, sid, before, date(2019, 9, 13)) == pytest.approx(
            1 - 0.40 / prior_close
        )
        assert self._ratio(fixture_store, sid, cancel, date(2019, 9, 13)) == pytest.approx(1.0)

    def test_idless_redate_to_an_earlier_date_is_not_seen_early(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "SEC_SPLIT_REDATED_NOID"
        # First known for ex 2019-10-21; at 2019-10-18 22:00 re-dated to the
        # already-past 2019-10-14 (cancel + replacement in one ingest).
        redate = datetime(2019, 10, 18, 22, 0, tzinfo=UTC)
        before = redate - timedelta(microseconds=1)
        assert self._ratio(fixture_store, sid, before, date(2019, 10, 11)) == pytest.approx(1.0)
        assert self._ratio(fixture_store, sid, redate, date(2019, 10, 11)) == pytest.approx(0.5)
        assert self._ratio(fixture_store, sid, redate, date(2019, 10, 14)) == pytest.approx(1.0)
        later = datetime(2019, 11, 1, 21, 0, tzinfo=UTC)
        assert self._ratio(fixture_store, sid, later, date(2019, 10, 11)) == pytest.approx(0.5)
        assert self._ratio(fixture_store, sid, later, date(2019, 10, 18)) == pytest.approx(1.0)


class TestSplitFactors:
    """`universe._split_factors` feeds rule 8's share adjustment and the
    survivorship gap's size share; it reads actions by identity too
    (quant-auditor round 2 on #111, finding 1)."""

    def test_split_redated_by_id_counts_once_at_its_new_ex_date(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        _action(
            store, "split", SESSIONS[3], 2.0, known_at=_close(SESSIONS[1]), source_action_id="A1"
        )
        _action(
            store, "split", SESSIONS[4], 2.0, known_at=_close(SESSIONS[2]), source_action_id="A1"
        )
        factors = _split_factors(store, _close(SESSIONS[6]), [SID], SESSIONS[6])
        assert factors == {SID: [(SESSIONS[4], 2.0)]}

    def test_cancelled_split_is_not_counted(self, store: duckdb.DuckDBPyConnection) -> None:
        _action(store, "split", SESSIONS[3], 2.0, known_at=_close(SESSIONS[0]))
        _action(store, "split", SESSIONS[3], 2.0, known_at=_close(SESSIONS[1]), cancelled=True)
        assert _split_factors(store, _close(SESSIONS[6]), [SID], SESSIONS[6]) == {}

    def test_split_counts_before_its_cancel_is_known(
        self, store: duckdb.DuckDBPyConnection
    ) -> None:
        _action(store, "split", SESSIONS[3], 2.0, known_at=_close(SESSIONS[0]))
        _action(store, "split", SESSIONS[3], 2.0, known_at=_close(SESSIONS[7]), cancelled=True)
        factors = _split_factors(store, _close(SESSIONS[6]), [SID], SESSIONS[6])
        assert factors == {SID: [(SESSIONS[3], 2.0)]}
