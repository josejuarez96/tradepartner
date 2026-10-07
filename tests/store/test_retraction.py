"""Tests for `store.retraction` and schema version 11 (#859): a retraction
is a revision of a master key that withdraws it from its own `known_at` on,
never before; the migration keeps every stored row live."""

from __future__ import annotations

from datetime import UTC, date, datetime
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
    retraction,
    split_kept,
    underived,
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


def _build(securities: tuple[dict[str, Any], ...], listings: tuple[dict[str, Any], ...]) -> Any:
    return MasterBuild(securities, listings, (), ())


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
            (10,),
            (11,),
            (12,),
            (13,),
            (14,),
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
