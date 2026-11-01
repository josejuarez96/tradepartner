"""Revision-aware EDGAR filing-index storage."""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

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


def test_two_daily_files_in_one_batch_preserve_first_known_date() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    first = _entry(filed_on=date(2026, 4, 1))
    corrected = _entry(filed_on=date(2026, 3, 31), listed_on=date(2026, 4, 6))
    counts = filing_index.insert_listings(conn, [first, corrected], RUN + timedelta(days=4))
    assert counts == filing_index.InsertCounts(1, 1, 0, 0)
    rows = conn.execute("SELECT filed_on, known_at FROM filing_index ORDER BY known_at").fetchall()
    assert rows[0] == (first.filed_on, ACCEPTED)
    assert rows[1][0] == corrected.filed_on
    assert rows[1][1] > datetime(2026, 4, 6, tzinfo=UTC)
    between = datetime(2026, 4, 4, tzinfo=UTC)
    assert filing_index.by_cik(conn, "1", between)[0].filed_on == first.filed_on
    assert filing_index.first_known(conn, between)[0].filed_on == first.filed_on


def test_per_file_corrections_at_one_clock_are_order_independent() -> None:
    older = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2))
    newer = _entry(filed_on=date(2026, 4, 2), listed_on=date(2026, 4, 3))
    for order in ((older, newer), (newer, older)):
        conn = duckdb.connect(":memory:")
        schema.init_schema(conn)
        filing_index.insert_listings(conn, [_entry()], RUN)
        with filing_index.listing_run(conn, RUN + timedelta(days=1)) as add:
            for listing in order:
                add([listing])
            add([newer])
        assert filing_index.by_cik(conn, "1", RUN + timedelta(days=1))[0].filed_on == newer.filed_on
        assert conn.execute("SELECT count(*) FROM filing_index").fetchone()[0] == 2


def test_per_file_x_y_x_at_one_clock_matches_batch() -> None:
    x = _entry(listed_on=date(2026, 4, 1))
    y = _entry(filed_on=date(2026, 4, 2), listed_on=date(2026, 4, 2))
    x_again = _entry(listed_on=date(2026, 4, 2))
    rows = []
    for separate in (True, False):
        conn = duckdb.connect(":memory:")
        schema.init_schema(conn)
        filing_index.insert_listings(conn, [x], RUN)
        if separate:
            with filing_index.listing_run(conn, RUN + timedelta(days=1)) as add:
                for listing in (x, y, x_again):
                    add([listing])
                assert conn.execute("SELECT count(*) FROM filing_index").fetchone()[0] == 1
        else:
            filing_index.insert_listings(conn, [x, y, x_again], RUN + timedelta(days=1))
        rows.append(
            conn.execute("SELECT filed_on, known_at FROM filing_index ORDER BY known_at").fetchall()
        )
    assert rows[0] == rows[1] == [(x.filed_on, ACCEPTED)]


def test_filing_index_module_never_updates_or_deletes() -> None:
    module = Path(filing_index.__file__)
    tree = ast.parse(module.read_text(encoding="utf-8"))
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    assert not any(
        re.search(r"\b(?:UPDATE\s+|DELETE\s+FROM\s+)filing_index\b", value, re.I)
        for value in strings
    )


def test_new_accession_form_conflict_is_counted() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    counts = filing_index.insert_listings(
        conn, [_entry(), _entry(form="8-K", listed_on=date(2026, 4, 2))], RUN
    )
    assert counts == filing_index.InsertCounts(1, 0, 1, 0)
    assert filing_index.by_cik(conn, "1", RUN)[0].form == "10-K"


def test_insert_listings_rolls_back_entire_batch_on_error() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    future = replace(_entry(), cik="2", accession="0002", accepted_at=RUN + timedelta(days=1))
    with pytest.raises(ValueError, match="acceptance is after ingestion clock"):
        filing_index.insert_listings(conn, [_entry(), future], RUN)
    assert conn.execute("SELECT count(*) FROM filing_index").fetchone()[0] == 0


def test_revision_cannot_precede_its_listing_date() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    future = _entry(filed_on=date(2026, 4, 2), listed_on=date(2026, 4, 6))
    with pytest.raises(ValueError, match="listing is after ingestion clock"):
        filing_index.insert_listings(conn, [future], RUN + timedelta(days=1))
    assert conn.execute("SELECT count(*) FROM filing_index").fetchone()[0] == 1


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


def test_quarter_and_first_known_hide_future_rows() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    corrected = _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2))
    filing_index.insert_listings(conn, [corrected], RUN + timedelta(days=1))
    before_acceptance = ACCEPTED - timedelta(seconds=1)
    between = RUN + timedelta(hours=12)
    after = RUN + timedelta(days=2)
    assert filing_index.first_known(conn, before_acceptance) == []
    assert filing_index.by_quarter(conn, 2026, 1, before_acceptance) == []
    assert filing_index.by_quarter(conn, 2026, 2, before_acceptance) == []
    assert filing_index.by_quarter(conn, 2026, 1, between)[0].filed_on == date(2026, 3, 31)
    assert filing_index.by_quarter(conn, 2026, 2, between) == []
    assert filing_index.first_known(conn, between)[0].filed_on == date(2026, 3, 31)
    assert filing_index.by_quarter(conn, 2026, 1, after) == []
    assert filing_index.by_quarter(conn, 2026, 2, after)[0].filed_on == corrected.filed_on


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


def _listing(cik: str, position: int | None, *, listed_on: date = date(2026, 4, 1)) -> object:
    return filing_index.Listing(
        cik, "0001", "10-K", "Issuer", date(2026, 3, 31), ACCEPTED, listed_on, True, position
    )


def test_listing_order_ignores_physical_insert_order() -> None:
    """A rebuild that inserts the same rows in another order keeps the first row (#2813)."""
    listings = [_listing("1", 5), _listing("2", 2), _listing("3", 9)]
    firsts = []
    for order in (listings, list(reversed(listings))):
        conn = duckdb.connect(":memory:")
        schema.init_schema(conn)
        for listing in order:
            filing_index.insert_listings(conn, [listing], RUN)  # type: ignore[list-item]
        rows = filing_index.accession_listing_order(conn, "0001", RUN)
        firsts.append([r.cik for r in rows])
    assert firsts[0] == firsts[1] == ["2", "1", "3"]


def test_listing_order_sorts_listed_on_first_and_null_position_last() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    for listing in (
        _listing("1", None),
        _listing("2", 7),
        _listing("3", 99, listed_on=date(2026, 4, 2)),
    ):
        filing_index.insert_listings(conn, [listing], RUN)  # type: ignore[list-item]
    rows = filing_index.accession_listing_order(conn, "0001", RUN)
    assert [r.cik for r in rows] == ["2", "1", "3"]


def test_revision_keeps_the_first_listings_position() -> None:
    conn2 = duckdb.connect(":memory:")
    schema.init_schema(conn2)
    filing_index.insert_listings(conn2, [replace(_entry(), list_position=4)], RUN)
    corrected = replace(
        _entry(filed_on=date(2026, 4, 1), listed_on=date(2026, 4, 2)), list_position=0
    )
    assert filing_index.insert_listings(conn2, [corrected], RUN + timedelta(days=1)).revised == 1
    positions = conn2.execute("SELECT list_position FROM filing_index").fetchall()
    assert positions == [(4,), (4,)]


def test_position_migration_keeps_rows_with_null_position() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    filing_index.insert_listings(conn, [_entry()], RUN)
    old_rows = conn.execute(
        "SELECT cik, accession, form, company_name, filed_on, accepted_at, listed_on, "
        "known_at, ingested_at, source, provenance FROM filing_index"
    ).fetchall()
    for table in ("filing_index", "filing_index_pending"):
        conn.execute(f"DROP TABLE {table}")
    conn.execute(schema._CREATE_FILING_INDEX.replace("list_position INTEGER,", ""))
    conn.execute(schema._CREATE_FILING_INDEX_PENDING.replace("list_position INTEGER,", ""))
    conn.executemany(
        "INSERT INTO filing_index (cik, accession, form, company_name, filed_on, accepted_at, "
        "listed_on, known_at, ingested_at, source, provenance) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        old_rows,
    )
    conn.execute("DELETE FROM schema_version WHERE version = ?", [schema.CURRENT_SCHEMA_VERSION])
    schema.init_schema(conn)
    assert conn.execute("SELECT count(*), count(list_position) FROM filing_index").fetchone() == (
        1,
        0,
    )
    conn.execute("SELECT list_position FROM filing_index_pending")
    assert schema._PRE_LIST_POSITION_VERSION == 45
    [version] = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    assert version == schema.CURRENT_SCHEMA_VERSION


def test_position_truncation_invariance() -> None:
    full = duckdb.connect(":memory:")
    truncated = duckdb.connect(":memory:")
    for conn in (full, truncated):
        schema.init_schema(conn)
    early = replace(_entry(), list_position=3)
    other = replace(_entry(), cik="2", accepted_at=ACCEPTED + timedelta(days=2), list_position=1)
    filing_index.insert_listings(full, [early], RUN)
    filing_index.insert_listings(truncated, [early], RUN)
    filing_index.insert_listings(full, [other], RUN + timedelta(days=1))
    cutoff = ACCEPTED + timedelta(days=1)
    assert filing_index.accession_listing_order(
        full, "0001", cutoff
    ) == filing_index.accession_listing_order(truncated, "0001", cutoff)
