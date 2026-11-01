"""#2291 (T287): no look-ahead in the snapshot rename.

The nightly master build reads the store's snapshot rows back as the fetches
that wrote them (`stored_snapshot_fetches`). A read at T must see only rows
known by T, so a build at T from a store truncated at T agrees with one from
the full store, and the rename row is invisible before its own fetch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import pytest

from lookahead.harness import PROBE_EPSILON, TruncatedStore, probe_timestamps
from tradepartner.adapters.filings import CompanySnapshotEntry, FilingIndexEntry
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.master import (
    MasterBuild,
    build_master,
    record_snapshot_sightings,
    stored_snapshot_fetches,
    stored_snapshot_history,
)

CIK = "0000000097"
F1 = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
F2 = datetime(2026, 10, 25, 18, 47, tzinfo=UTC)
F3 = datetime(2026, 10, 27, 23, 0, tzinfo=UTC)
AT = datetime(2026, 10, 28, 2, 0, tzinfo=UTC)
FILED = datetime(2010, 3, 1, 20, 30, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)


def _entry(ticker: str, fetched_at: datetime) -> CompanySnapshotEntry:
    return CompanySnapshotEntry(CIK, "QUIET INC", ticker, "OTC", fetched_at)


def _build(
    current: CompanySnapshotEntry, earlier: tuple[CompanySnapshotEntry, ...], at: datetime
) -> MasterBuild:
    first = FilingIndexEntry(CIK, "Quiet Inc", "10-K", f"{CIK}-10-000001", FILED)
    source = FixtureFilingSource(index=[first], snapshot=[current])
    return build_master(source, _settings(), ingested_at=at, earlier_fetches=earlier)


@pytest.fixture
def full() -> duckdb.DuckDBPyConnection:
    """The store after QTA, then two fetches naming QTB."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for row in _build(_entry("QTB", F3), (_entry("QTA", F1), _entry("QTB", F2)), AT).listings:
        insert_row(conn, "listings", row)
    record_snapshot_sightings(conn, (_entry("QTB", F2), _entry("QTB", F3)), AT)
    return conn


def test_stored_fetches_are_truncation_invariant(full: duckdb.DuckDBPyConnection) -> None:
    cut = TruncatedStore(full)
    try:
        for t in probe_timestamps(full, ["listings", "snapshot_sightings"]):
            assert stored_snapshot_fetches(full, t) == stored_snapshot_fetches(cut.at(t), t)
            assert stored_snapshot_history(full, t) == stored_snapshot_history(cut.at(t), t)
    finally:
        cut.close()


def test_no_fetch_known_after_t(full: duckdb.DuckDBPyConnection) -> None:
    before = stored_snapshot_fetches(full, F2 - PROBE_EPSILON)
    assert [(e.ticker, e.fetched_at) for e in before] == [("QTA", F1)]
    assert all(e.fetched_at <= F2 - PROBE_EPSILON for e in before)


def test_the_rename_row_is_invisible_before_its_fetch(full: duckdb.DuckDBPyConnection) -> None:
    early = listings_as_of(full, F2 - PROBE_EPSILON, [CIK])
    assert early["ticker"].to_list() == ["QTA"]
    assert listings_as_of(full, F2, [CIK])["ticker"].to_list() == ["QTA"]
    late = listings_as_of(full, F3, [CIK]).sort("valid_from")
    assert late.select("ticker", "valid_from").rows() == [
        ("QTA", date(2010, 3, 1)),
        ("QTB", date(2026, 10, 26)),
    ]


def test_a_build_from_a_truncated_store_agrees(full: duckdb.DuckDBPyConnection) -> None:
    cut = TruncatedStore(full)
    try:
        for t in (F2 - PROBE_EPSILON, F2 + PROBE_EPSILON, F3):
            current = _entry("QTB", t)
            built = _build(current, stored_snapshot_history(full, t), t)
            truncated = _build(current, stored_snapshot_history(cut.at(t), t), t)
            assert built.listings == truncated.listings
            assert all(row["known_at"] <= t for row in built.listings)
    finally:
        cut.close()
