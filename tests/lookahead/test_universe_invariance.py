"""Truncation invariance for `universe_as_of` (spec "Look-ahead"; plan T13).

`universe_as_of(T)` on the full fixture store equals `universe_as_of(T)` on
the store truncated to `known_at <= T`, at a probe just before and just
after every distinct `known_at` in the fixture. Members and exclusions are
compared whole, so a rule that read a row from after T (a later split, a
restated fact, a delisting filed later) shows up as a difference.
`top_n_by_cap=1` so the rule-8 cut runs at every probe (the fixture has
far fewer than 1,000 companies).

#1122 item 1 (#980.2): the fixture universe has no first-span-lead case
(#974: bars stored under a security_id before any listing of it is known),
so the probe loop above never walks that ordering. `tests/test_universe.py`
covers the mechanics with two hand-picked instants on its own synthetic
store; `test_first_span_lead_bars_invariant_under_truncation` below runs
the same shape through every truncation boundary instead, on its own
synthetic store (never the shared fixture, so it moves no momentum/H1
result built on it).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.schema import init_schema
from tradepartner.universe import universe_as_of


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_universe_as_of_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    settings = Settings(_env_file=None, universe={"top_n_by_cap": 1})
    members_seen = 0
    for t in probe_timestamps(fixture_store):
        full = universe_as_of(fixture_store, t, settings)
        cut = universe_as_of(truncated.at(t), t, settings)
        assert full.members.equals(cut.members), f"members disagree at T={t!r}"
        assert full.exclusions.equals(cut.exclusions), f"exclusions disagree at T={t!r}"
        members_seen += full.members.height
    # Not vacuous: the universe is non-empty at many probes.
    assert members_seen > 0


def _build_first_span_lead_universe_store() -> duckdb.DuckDBPyConnection:
    """A synthetic store for `test_first_span_lead_bars_invariant_under_
    truncation` (#1122 item 1, #980.2): a filing known in 2016, a handful
    of lead bars known (at each session's close) years before any listing
    of the security is known, a listing known in 2019, and a couple of
    bars known once it is listed -- the same shape as
    `tests/test_universe.py`'s `test_first_span_lead_bars_admit_no_name_
    before_its_listing_is_known`, but as a store the harness can walk at
    every truncation boundary rather than two hand-picked instants."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    sid = "SEC_FIRST_SPAN_LEAD_INV"
    filed = datetime(2016, 3, 1, 21, tzinfo=UTC)
    listed = datetime(2019, 7, 25, 21, tzinfo=UTC)
    filing_common = {"ingested_at": filed, "source": "edgar", "provenance": "filing"}
    insert_row(
        conn,
        "securities",
        {"security_id": sid, "cik": sid, "name": "Lead Co", "known_at": filed, **filing_common},
    )
    insert_row(
        conn,
        "classifications",
        {
            "security_id": sid,
            "sic": 7370,
            "security_type": "common",
            "rule": "common_default",
            "known_at": filed,
            **filing_common,
        },
    )
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": "LDLK",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": date(2019, 7, 24),
            "known_at": listed,
            "ingested_at": listed,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    bar = {"open": 200.0, "high": 200.0, "low": 200.0, "close": 200.0, "volume": 10**6}
    # Lead sessions (#974's shape): known, at each close, years before the
    # listing; plus two sessions once the name is listed.
    for session in (
        date(2018, 7, 2),
        date(2018, 7, 3),
        date(2018, 7, 5),
        date(2019, 7, 26),
        date(2019, 7, 29),
    ):
        known_at = session_close(session)
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": sid,
                "session": session,
                **bar,
                "known_at": known_at,
                "ingested_at": known_at,
                "source": "alpaca_sip",
                "provenance": "bar",
            },
        )
    return conn


def test_first_span_lead_bars_invariant_under_truncation() -> None:
    """#1122 item 1 (#980.2): lead bars known years before the listing
    (#974's shape, #35's rule-2 `not_listed` decision) must agree between
    the full store and the truncated one at every boundary, not only at
    two hand-picked instants -- a rule that read the listing's or a lead
    bar's row from after T would show up as a members/exclusions
    difference at some probe between the two."""
    conn = _build_first_span_lead_universe_store()
    settings = Settings(_env_file=None, universe={"top_n_by_cap": 1})
    try:
        truncated_store = TruncatedStore(conn)
        try:
            for t in probe_timestamps(conn):
                full = universe_as_of(conn, t, settings)
                cut = universe_as_of(truncated_store.at(t), t, settings)
                assert full.members.equals(cut.members), f"members disagree at T={t!r}"
                assert full.exclusions.equals(cut.exclusions), f"exclusions disagree at T={t!r}"
        finally:
            truncated_store.close()
    finally:
        conn.close()
