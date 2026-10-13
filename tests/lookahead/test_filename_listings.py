"""Filename listing rows stay invisible until each filing's acceptance."""

from datetime import UTC, date, datetime

import duckdb

from lookahead.harness import PROBE_EPSILON, TruncatedStore
from tradepartner.adapters.filings import FilenameListing, FilingIndexEntry
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import configure_connection
from tradepartner.store.master import build_master, write_master
from tradepartner.store.schema import init_schema


def test_each_filename_acceptance_is_truncation_invariant() -> None:
    cik = "0000099999"
    first = datetime(2015, 3, 2, 20, 0, tzinfo=UTC)
    second = datetime(2016, 3, 1, 20, 0, tzinfo=UTC)
    source = FixtureFilingSource(
        index=[FilingIndexEntry(cik, "History", "10-K", "one", first)],
        filename_listings=[
            FilenameListing(
                cik, "one", "10-K", first, date(2015, 3, 2), "OLD", "NYSE", "Common Stock", "OLD"
            ),
            FilenameListing(
                cik, "two", "10-K", second, date(2016, 3, 1), "NEW", "NYSE", "Common Stock", "NEW"
            ),
        ],
    )
    settings = Settings(_env_file=None, edgar={"filename_listings": True})
    built = build_master(source, settings, ingested_at=datetime(2020, 1, 1, tzinfo=UTC))
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    write_master(conn, built)
    truncated = TruncatedStore(conn, tables=("securities", "listings"))
    try:
        for stamp, before, after in ((first, 0, 1), (second, 1, 2)):
            for t, count in ((stamp - PROBE_EPSILON, before), (stamp + PROBE_EPSILON, after)):
                full = listings_as_of(conn, t, security_ids=[cik])
                assert full.height == count
                assert full.equals(listings_as_of(truncated.at(t), t, security_ids=[cik]))
    finally:
        truncated.close()
        conn.close()
