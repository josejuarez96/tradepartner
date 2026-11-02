"""Tests for `store.retraction` and schema version 11 (#859): a retraction
is a revision of a master key that withdraws it from its own `known_at` on,
never before; the migration keeps every stored row live."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.ingest import _add_rows
from tradepartner.store import schema
from tradepartner.store.asof import listings_as_of
from tradepartner.store.benchmarks import benchmark_candidates, ticker_holders
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.master import MasterBuild, securities_as_of
from tradepartner.store.retraction import (
    Underived,
    carry_forward_underived,
    cik_of,
    record_underived,
    retraction,
    split_kept,
    stored_underived,
    underived,
    underived_as_of,
    write_retractions,
)

KNOWN = datetime(2020, 8, 10, 20, 30, tzinfo=UTC)
FIXED = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 6, 2, 0, tzinfo=UTC)
SID = "0001647088@2020-08-10"


def _common(source: str = "edgar", known: datetime = KNOWN) -> dict[str, Any]:
    return {"known_at": known, "ingested_at": known, "source": source, "provenance": "filing"}


def _security(sid: str = SID, **extra: Any) -> dict[str, Any]:
    return {"security_id": sid, "cik": "0001647088", "name": "WillScot", "benchmark": False} | (
        _common(**extra)
    )


def _listing(sid: str = SID, ticker: str = "WSC", **extra: Any) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NASDAQ",
        "class_title": "Common Stock",
        "valid_from": date(2020, 8, 10),
    } | _common(**extra)


def _build(
    securities: tuple[dict[str, Any], ...],
    listings: tuple[dict[str, Any], ...],
    *,
    filename: bool = False,
) -> Any:
    return MasterBuild(securities, listings, (), (), filename_enabled=filename)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    return c


# --- underived (pure) ---------------------------------------------------------------


def test_underived_is_every_stored_edgar_key_the_build_does_not_derive() -> None:
    kept = _listing("0001647088")
    found = underived(
        _build((_security("0001647088"),), (kept | {"class_title": "Common shares"},)),
        [_security("0001647088"), _security(), _security("BENCH:SPY", source="config")],
        [kept, _listing(), _listing("BENCH:SPY", "SPY", source="config")],
    )
    # A reworded title is a revision ingest writes, not an underived key; a
    # config-seeded benchmark is never the builder's to judge.
    assert [(u.table, u.key) for u in found] == [
        ("securities", (SID,)),
        ("listings", (SID, "WSC", "NASDAQ", date(2020, 8, 10))),
    ]


def test_underived_includes_filing_name_but_keeps_snapshot_out() -> None:
    filename = _listing("0001647088", "OLD") | {"provenance": "filing_name"}
    snapshot = _listing("0001647088", "NEW") | {"provenance": "snapshot_static"}
    found = underived(_build((), (), filename=True), [], [filename, snapshot])
    assert [(item.table, item.row["ticker"]) for item in found] == [("listings", "OLD")]


def test_filing_name_rows_are_never_judged_while_the_switch_is_off() -> None:
    # #1662: `edgar.filename_listings` off reads no file names; the stored
    # rows are not "no longer derived", so nothing is proposed for them.
    filename = _listing("0001647088", "OLD") | {"provenance": "filing_name"}
    assert underived(_build((), ()), [], [filename]) == ()
    assert len(underived(_build((), (), filename=True), [], [filename])) == 1


def test_master_retract_sees_stored_filename_row(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    security = _security("0001647088")
    filename = _listing("0001647088", "OLD") | {"provenance": "filing_name"}
    insert_row(conn, "securities", security)
    insert_row(conn, "listings", filename)
    found = stored_underived(conn, _build((security,), (), filename=True), FIXED)
    assert [(item.table, item.row["ticker"]) for item in found] == [("listings", "OLD")]


def test_a_successor_relisted_on_snapshot_evidence_is_never_judged() -> None:
    """Quant audit of #872, pass 2: a `<cik>@<date>` successor relisted from
    a companies snapshot has a `filing` securities row, yet exists only while
    the snapshot names it; its `snapshot` listing marks it."""
    successor = "0000900005@2026-06-16"
    snapshot_listing = _listing(successor, "NEWCO") | {"provenance": "snapshot"}
    later_cover = _listing(successor, "NEWC2") | {"valid_from": date(2026, 9, 1)}
    found = underived(
        _build((), ()),
        [_security(successor), _security()],
        [snapshot_listing, later_cover, _listing()],
    )
    assert {u.row["security_id"] for u in found} == {SID}


def test_a_retraction_is_stamped_at_the_run_and_never_back_dated() -> None:
    row = retraction(_listing(), FIXED)
    assert (row["retracted"], row["known_at"], row["ingested_at"]) == (True, FIXED, FIXED)
    assert row["valid_from"] == date(2020, 8, 10)
    with pytest.raises(ValueError, match="back-dated"):
        retraction(_listing(), KNOWN)


# --- as-of reads (no look-ahead) ------------------------------------------------------


def test_reads_before_the_retraction_still_see_the_row(conn: duckdb.DuckDBPyConnection) -> None:
    insert_row(conn, "securities", _security())
    insert_row(conn, "listings", _listing())
    found = underived(_build((), ()), [_security()], [_listing()])
    assert write_retractions(conn, found, FIXED) == 2
    for t in (KNOWN, datetime(2026, 10, 5, 1, 59, tzinfo=UTC)):
        assert listings_as_of(conn, t)["security_id"].to_list() == [SID]
        assert securities_as_of(conn, t)["security_id"].to_list() == [SID]
    for t in (FIXED, LATER):
        assert listings_as_of(conn, t).is_empty()
        assert securities_as_of(conn, t).is_empty()
    assert "retracted" not in listings_as_of(conn, KNOWN).columns
    assert "retracted" not in securities_as_of(conn, KNOWN).columns


def test_a_key_derived_again_is_written_back_live(conn: duckdb.DuckDBPyConnection) -> None:
    insert_row(conn, "listings", _listing())
    write_retractions(conn, underived(_build((), ()), [], [_listing()]), FIXED)
    assert _add_rows(conn, "listings", [_listing()], ingested_at=LATER, current=False) == 1
    assert listings_as_of(conn, FIXED).is_empty()
    assert listings_as_of(conn, LATER)["known_at"].to_list() == [LATER]
    # A re-run over the same build adds nothing.
    assert _add_rows(conn, "listings", [_listing()], ingested_at=LATER, current=False) == 0


def test_an_ingest_never_rewrites_a_retraction_it_does_not_derive(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    insert_row(conn, "listings", _listing())
    write_retractions(conn, underived(_build((), ()), [], [_listing()]), FIXED)
    other = _listing("0001647088")
    assert _add_rows(conn, "listings", [other], ingested_at=LATER, current=False) == 1
    assert listings_as_of(conn, LATER)["security_id"].to_list() == ["0001647088"]


def test_benchmark_identity_ignores_a_retracted_row(conn: duckdb.DuckDBPyConnection) -> None:
    bench = _security("BENCH:SPY", source="config") | {"benchmark": True}
    insert_row(conn, "securities", bench)
    insert_row(conn, "listings", _listing("BENCH:SPY", "SPY", source="config"))
    insert_row(conn, "listings", _listing("0000000009", "SPY"))
    start = date(2019, 1, 2)
    assert ticker_holders(conn, "SPY", start=start, through=None, exclude="BENCH:SPY") == [
        "0000000009"
    ]
    write_retractions(conn, underived(_build((), ()), [], [_listing("0000000009", "SPY")]), FIXED)
    assert ticker_holders(conn, "SPY", start=start, through=None, exclude="BENCH:SPY") == []
    assert benchmark_candidates(conn, "SPY") == ["BENCH:SPY"]
    insert_row(conn, "securities", retraction(bench, FIXED))
    assert benchmark_candidates(conn, "SPY") == []


# --- version 11 migration -------------------------------------------------------------


def _version_10_store(path: Path) -> None:
    """A store as version 10 left it: `_TABLE_DDL`'s version-4 master tables
    (no `retracted`), no `master_underived`, rows in both master tables."""
    with duckdb.connect(str(path)) as c:
        configure_connection(c)
        for ddl in (
            schema._TABLE_DDL
            + schema._REGISTRY_TABLE_DDL
            + schema._JOURNAL_TABLE_DDL
            + schema._STATEMENT_FACTS_TABLE_DDL
        ):
            c.execute(ddl)
        c.execute("INSERT INTO schema_version VALUES (10, ?)", [KNOWN])
        for table, row in (
            ("securities", _security()),
            ("securities", _security("0001647088")),
            ("listings", _listing()),
            ("listings", _listing("0001647088")),
        ):
            names = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            c.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(row.values()))


def test_the_migration_keeps_every_master_row_live(tmp_path: Path) -> None:
    path = tmp_path / "v10.duckdb"
    _version_10_store(path)
    with duckdb.connect(str(path)) as c:
        before = {
            t: c.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
            for t in schema.RETRACTABLE_TABLES
        }
        schema.init_schema(c)
        for table in schema.RETRACTABLE_TABLES:
            after = c.execute(f"SELECT * EXCLUDE (retracted) FROM {table} ORDER BY rowid")
            assert after.fetchall() == before[table]
            assert c.execute(f"SELECT DISTINCT retracted FROM {table}").fetchall() == [(False,)]
        assert c.execute("SELECT version FROM schema_version ORDER BY 1").fetchall() == [
            (v,) for v in range(10, schema.CURRENT_SCHEMA_VERSION + 1)
        ]
        assert c.execute("SELECT count(*) FROM master_underived").fetchone() == (0,)
        migrated = _ddl(c)
    fresh = duckdb.connect(":memory:")
    schema.init_schema(fresh)
    assert migrated == _ddl(fresh)


def _ddl(c: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return c.execute(
        "SELECT table_name, sql FROM duckdb_tables() "
        "WHERE table_name IN ('securities', 'listings', 'master_underived') ORDER BY 1"
    ).fetchall()


def test_a_read_only_version_10_store_reads_every_master_row_as_live(tmp_path: Path) -> None:
    """A read-only connection never migrates (`paper start` reads the master
    before its write connection does): no column, no retraction."""
    path = tmp_path / "v10.duckdb"
    _version_10_store(path)
    with duckdb.connect(str(path), read_only=True) as c:
        schema.init_schema(c)  # a version check only: passes, migrates nothing
        assert not schema.has_retracted(c, "listings")
        assert set(listings_as_of(c, LATER)["security_id"]) == {SID, "0001647088"}
        assert set(securities_as_of(c, LATER)["security_id"]) == {SID, "0001647088"}
        assert ticker_holders(c, "WSC", start=date(2020, 1, 2), through=None) == [
            "0001647088",
            SID,
        ]
        assert benchmark_candidates(c, "WSC") == []


def test_split_kept_partitions_by_security_id_and_keeps_order() -> None:
    """#922: rows of a keep-listed security, in either table, are kept."""
    known = datetime(2020, 1, 2, tzinfo=UTC)
    found = (
        Underived("securities", {"security_id": "0000000001@2020-01-02", "known_at": known}),
        Underived("securities", {"security_id": "0000000002@2020-01-02", "known_at": known}),
        Underived("listings", {"security_id": "0000000001@2020-01-02", "known_at": known}),
    )
    keep = frozenset({"0000000001@2020-01-02"})
    proposed, kept = split_kept(found, keep, frozenset())
    assert proposed == (found[1],)
    assert kept == (found[0], found[2])
    assert split_kept(found, frozenset(), frozenset()) == (found, ())
    # Derived again: the keep no longer applies (quant audit of #923).
    assert split_kept(found, keep, keep) == (found, ())


# --- carry-forward of a touched-only run (#2629, T321) ---------------------------------

CIK_A, CIK_B, CIK_C = "0000000001", "0000000002", "0000000003"
RUN_START = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)


def _stored_pair(cik: str) -> tuple[dict[str, Any], dict[str, Any]]:
    security = _security(cik) | {"cik": cik}
    return security, _listing(cik, ticker="T" + cik[-1])


def _record_run(c: duckdb.DuckDBPyConnection, run_id: str, started: datetime, found: Any) -> None:
    insert_row(
        c,
        "ingestion_runs",
        {
            "run_id": run_id,
            "started_at": started,
            "finished_at": started + timedelta(minutes=5),
            "status": "ok",
            "source": "edgar",
            "mode": "session",
            "rows_added": 0,
            "chunk_cursor": None,
            "message": None,
        },
    )
    record_underived(c, run_id, started + timedelta(minutes=5), found)


def _set(frame: Any) -> set[tuple[Any, ...]]:
    return {
        (r["table"], r["security_id"], r["ticker"], r["exchange"], r["valid_from"], r["known_at"])
        for r in frame.iter_rows(named=True)
    }


def test_a_touched_only_run_reads_the_same_underived_set_as_a_full_one(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    for cik in (CIK_A, CIK_B, CIK_C):
        security, listing = _stored_pair(cik)
        insert_row(conn, "securities", security)
        insert_row(conn, "listings", listing)
    # Night 1 (full): the build derives nothing, so all three CIKs are underived.
    empty = _build((), ())
    stored = stored_underived(conn, empty, FIXED)
    assert len(stored) == 6
    _record_run(conn, "full-1", RUN_START, stored)
    previous = underived_as_of(conn, RUN_START + timedelta(hours=1))

    # Night 2: only CIK_A is touched, and its rows are derived again.
    security_a, listing_a = _stored_pair(CIK_A)
    build_a = _build((security_a,), (listing_a,))
    touched = frozenset({CIK_A})
    recomputed = underived(
        build_a,
        [r for r in securities_as_of(conn, LATER).iter_rows(named=True) if r["cik"] in touched],
        [
            r
            for r in listings_as_of(conn, LATER).iter_rows(named=True)
            if cik_of(r["security_id"]) in touched
        ],
    )
    carried = carry_forward_underived(recomputed, previous.iter_rows(named=True), touched)
    day2 = RUN_START + timedelta(days=1)
    _record_run(conn, "touched-2", day2, carried)

    # The full run of night 2 judges every CIK against the same build.
    full = underived(
        build_a,
        securities_as_of(conn, LATER).iter_rows(named=True),
        listings_as_of(conn, LATER).iter_rows(named=True),
    )
    _record_run(conn, "full-2", day2 + timedelta(hours=1), full)
    at = day2 + timedelta(hours=2)
    assert _set(underived_as_of(conn, day2 + timedelta(minutes=30))) == _set(
        underived_as_of(conn, at)
    )
    assert {row[1] for row in _set(underived_as_of(conn, at))} == {CIK_B, CIK_C}


def test_carry_forward_drops_touched_and_skipped_ciks_and_prefers_recomputed() -> None:
    old = [
        {"table": "securities", "security_id": CIK_A, "ticker": None, "exchange": None,
         "valid_from": None, "known_at": KNOWN},
        {"table": "securities", "security_id": CIK_B, "ticker": None, "exchange": None,
         "valid_from": None, "known_at": KNOWN},
        {"table": "securities", "security_id": CIK_C, "ticker": None, "exchange": None,
         "valid_from": None, "known_at": KNOWN},
    ]  # fmt: skip
    fresh = Underived("securities", {"security_id": CIK_A, "known_at": LATER})
    out = carry_forward_underived([fresh], old, frozenset({CIK_A}), frozenset({CIK_C}))
    assert [u.key for u in out] == [(CIK_A,), (CIK_B,)]
    assert out[0].row["known_at"] == LATER
    assert out[1].row["known_at"] == KNOWN
    # A pure function: nothing but a new tuple comes back.
    assert carry_forward_underived([], [], frozenset()) == ()


def test_carry_forward_orders_securities_before_listings_by_key() -> None:
    listing = {"table": "listings", "security_id": CIK_A, "ticker": "A", "exchange": "NYSE",
               "valid_from": date(2020, 1, 1), "known_at": KNOWN}  # fmt: skip
    security = {"table": "securities", "security_id": CIK_B, "ticker": None, "exchange": None,
                "valid_from": None, "known_at": KNOWN}  # fmt: skip
    out = carry_forward_underived([], [listing, security], frozenset())
    assert [u.table for u in out] == ["securities", "listings"]
