"""Revision-aware EDGAR filing-index storage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import duckdb

from tradepartner.store import filing_index, schema

ACCEPTED = datetime(2026, 3, 31, 20, tzinfo=UTC)
RUN = datetime(2026, 4, 3, 23, tzinfo=UTC)


def _entry(
    *,
    filed_on: date = date(2026, 3, 31),
    listed_on: date = date(2026, 4, 1),
    daily: bool = True,
    form: str = "10-K",
) -> filing_index.Listing:
    return filing_index.Listing("1", "0001", form, "Issuer", filed_on, ACCEPTED, listed_on, daily)


def test_revision_as_of_and_first_known() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    assert filing_index.insert_listings(conn, [_entry()], RUN) == filing_index.InsertCounts(
        1, 0, 0, 0
    )
    assert filing_index.insert_listings(conn, [_entry()], RUN) == filing_index.InsertCounts(
        0, 0, 0, 0
    )
    corrected = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2))
    assert filing_index.insert_listings(conn, [corrected], RUN + timedelta(days=1)).revised == 1
    assert conn.execute("SELECT count(*) FROM filing_index").fetchone()[0] == 2
    before = filing_index.by_cik(conn, "1", RUN)
    after = filing_index.by_cik(conn, "1", RUN + timedelta(days=1))
    assert len(before) == len(after) == 1
    assert before[0].filed_on == date(2026, 3, 31)
    assert after[0].filed_on == date(2026, 4, 1)
    assert filing_index.first_known(conn, RUN + timedelta(days=2))[0] == before[0]
    assert filing_index.by_quarter(conn, 2026, 1, RUN + timedelta(days=2)) == []
    assert filing_index.by_quarter(conn, 2026, 2, RUN + timedelta(days=2)) == after
    assert filing_index.by_form(conn, "10-K", RUN + timedelta(days=2)) == after


def test_stale_changed_and_one_revision_per_run() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    later = RUN + timedelta(days=1)
    quarterly = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2), daily=False)
    daily = _entry(filed_on=date(2026, 4, 2), listed_on=date(2026, 4, 2))
    assert filing_index.insert_listings(conn, [quarterly, daily], later).revised == 1
    assert filing_index.by_cik(conn, "1", later)[0].filed_on == daily.filed_on
    assert filing_index.insert_listings(conn, [_entry()], later + timedelta(days=1)).stale == 1
    assert (
        filing_index.insert_listings(conn, [_entry(form="8-K")], later + timedelta(days=1)).changed
        == 1
    )
    for changed in (
        replace(_entry(), company_name="Other Issuer"),
        replace(_entry(), accepted_at=ACCEPTED + timedelta(seconds=1)),
    ):
        assert filing_index.insert_listings(conn, [changed], later + timedelta(days=1)).changed == 1
    assert conn.execute("SELECT count(*) FROM filing_index").fetchone()[0] == 2


def test_additive_migration_keeps_prior_rows() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    conn.execute(
        "INSERT INTO ingestion_runs (run_id, source, started_at, status, mode) "
        "VALUES ('old', 'edgar', ?, 'ok', 'session')",
        [RUN],
    )
    for table in ("filing_index", "filing_index_pending", "edgar_filers", "edgar_index_files"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute("DELETE FROM schema_version WHERE version = ?", [schema.CURRENT_SCHEMA_VERSION])
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        [schema._PRE_FILING_INDEX_VERSION, RUN],
    )
    schema.init_schema(conn)
    assert conn.execute("SELECT run_id FROM ingestion_runs").fetchone()[0] == "old"
    assert (
        conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()[
            0
        ]
        == schema.CURRENT_SCHEMA_VERSION
    )


def test_truncation_and_trial_vintage() -> None:
    from tradepartner.store import registry

    full = duckdb.connect(":memory:")
    truncated = duckdb.connect(":memory:")
    for conn in (full, truncated):
        schema.init_schema(conn)
    first = _entry()
    correction = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2))
    filing_index.insert_listings(full, [first], RUN)
    filing_index.insert_listings(full, [correction], RUN + timedelta(days=1))
    cutoff = RUN + timedelta(hours=12)
    filing_index.insert_listings(truncated, [first], RUN)
    assert filing_index.by_cik(full, "1", cutoff) == filing_index.by_cik(truncated, "1", cutoff)
    assert registry.data_vintage(full, cutoff) == registry.data_vintage(truncated, cutoff)
    assert registry.data_vintage(full, RUN + timedelta(days=2)) is None
    assert filing_index.by_cik(full, "1", ACCEPTED - timedelta(seconds=1)) == []


def test_snapshot_id_detects_latest_writer_even_if_failed() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    for run_id, status, when in (
        ("first", "ok", RUN),
        ("second", "failed", RUN + timedelta(hours=1)),
    ):
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, started_at, finished_at, status, source, mode) "
            "VALUES (?, ?, ?, ?, 'edgar_index', 'session')",
            [run_id, when, when, status],
        )
    assert filing_index.snapshot_id(conn) == "second"


def test_quarterly_listing_date_uses_eastern_last_modified() -> None:
    assert filing_index.quarterly_listed_on(datetime(2026, 4, 3, 3, 30, tzinfo=UTC)) == date(
        2026, 4, 1
    )


def test_older_listing_is_counted_even_when_date_matches() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry(listed_on=date(2026, 4, 3))], RUN)
    older = _entry(listed_on=date(2026, 4, 2))
    assert filing_index.insert_listings(conn, [older], RUN + timedelta(days=1)).stale == 1


def test_invalid_later_listing_does_not_hide_valid_correction() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    valid = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2))
    invalid = replace(valid, form="8-K", listed_on=date(2026, 4, 3))
    counts = filing_index.insert_listings(conn, [valid, invalid], RUN + timedelta(days=1))
    assert counts.changed == counts.revised == 1
    assert filing_index.by_cik(conn, "1", RUN + timedelta(days=1))[0].filed_on == valid.filed_on


def test_later_repost_of_same_daily_file_wins_batch() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    first = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2))
    repost = replace(first, filed_on=date(2026, 4, 2))
    filing_index.insert_listings(conn, [first, repost], RUN + timedelta(days=1))
    assert filing_index.by_cik(conn, "1", RUN + timedelta(days=1))[0].filed_on == repost.filed_on


def test_first_known_can_filter_before_ranking() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    other = replace(_entry(), cik="2", accession="0002", form="8-K")
    filing_index.insert_listings(conn, [_entry(), other], RUN)
    assert len(filing_index.first_known(conn, RUN, form="10-K")) == 1
    assert len(filing_index.first_known(conn, RUN, cik="2")) == 1


def test_quarter_read_returns_only_latest_same_quarter_revision() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    correction = _entry(filed_on=date(2026, 3, 30), listed_on=date(2026, 4, 2))
    filing_index.insert_listings(conn, [correction], RUN + timedelta(days=1))
    rows = filing_index.by_quarter(conn, 2026, 1, RUN + timedelta(days=2))
    assert len(rows) == 1
    assert rows[0].filed_on == correction.filed_on
