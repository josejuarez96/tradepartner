"""The C2 done-test (#1682, T199): a `snapshot_static` row is no evidence.

Spec amendment 2026-10-14 (#1682), acceptance line "C2 (T184h's evidence)":
on a fixture in AEP's shape -- a `snapshot_static` NASDAQ row with
`valid_from` 1994 known at a 2026 snapshot fetch; a 2019 10-K whose instance
prefix is `aep` and whose 12(b) text names NYSE; a 2021 10-K naming NASDAQ --
reads before the fetch see no listing before the 2019 acceptance, NYSE from
it, and NASDAQ only from the 2021 acceptance, never earlier; the static
row's `known_at`, `valid_from` and exchange stay byte-identical; and a
look-ahead test fails if any writer path uses a `snapshot_static` row's
exchange or `valid_from` as evidence for a row known before that row's
`known_at`.

The real AEP (CIK 0000004904) is the case: its static row says NASDAQ since
1994, its 2018 10-K says NYSE, and an 8-K of 2020-09-15 moved it to NASDAQ
from 2020-10-01. Per the spec, NASDAQ starts at the **FY2020 10-K's
acceptance in 2021**, not at the 8-K: the 8-K Item 3.01 reading is **not
built** ("what it does not cover"), so the honest store reads NYSE until
then. This test asserts exactly that, and that the static NASDAQ value wrote
nothing.

A second, **pre-rename** security joins the fixture so the guard has teeth:
its 2019 10-K prefix is `HTZ` while today's static row says `HRI`. A
confirmation that (wrongly) treated the static ticker as a reading would
refuse the `HTZ` prefix; the tests below fail if that ever happens.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from lookahead.harness import PROBE_EPSILON
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    FilenameListing,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import configure_connection
from tradepartner.store.master import build_master, write_master
from tradepartner.store.schema import init_schema

#: American Electric Power: the C2 case's CIK.
CIK = "0000004904"
#: A pre-rename security: today's static ticker differs from its 2019
#: filing-name prefix (`HRI` vs `HTZ`), the spec's Herc shape.
PRERENAME_CIK = "0000004907"
#: The earliest issuer filing: the class's `known_at`, so the static span's
#: `valid_from` is this filing's session (1994-05-16).
EARLIEST = datetime(1994, 5, 16, 20, 0, tzinfo=UTC)
#: The FY2018 10-K accepted 2019-02-25; its instance prefix is `aep` and its
#: Section 12(b) text names NYSE.
FY2018_10K = datetime(2019, 2, 25, 20, 0, tzinfo=UTC)
#: The 8-K of 2020-09-15 that announced the move to NASDAQ from 2020-10-01;
#: its reading is not built, so it writes no listing.
MOVE_8K = datetime(2020, 9, 15, 20, 0, tzinfo=UTC)
#: A read after the move's 2020-10-01 effective day, before the FY2020 10-K.
POST_MOVE = datetime(2020, 11, 2, 21, 0, tzinfo=UTC)
#: The FY2020 10-K accepted 2021-02-24, naming NASDAQ.
FY2020_10K = datetime(2021, 2, 24, 20, 0, tzinfo=UTC)
#: The pre-rename security's FY2018 10-K accepted 2019-03-04, prefix `HTZ`.
PRE_RENAME_10K = datetime(2019, 3, 4, 20, 0, tzinfo=UTC)
#: The companies-snapshot fetch the static rows are known at.
FETCH = datetime(2026, 10, 3, 22, 16, 55, tzinfo=UTC)
INGESTED_AT = datetime(2027, 1, 2, 12, 0, tzinfo=UTC)

_ROW = tuple[str, str, date, str, datetime]
_STATIC = tuple[str, str, str, date, datetime]
_SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"


def _source() -> FixtureFilingSource:
    """AEP's shape plus a pre-rename prefix under a static current ticker.

    No cover pages, so each snapshot entry lands as a static row and every
    listing fact comes from a filing's own files.
    """
    return FixtureFilingSource(
        index=[
            FilingIndexEntry(CIK, "American Electric Power", "10-K", "aep-1994", EARLIEST),
            FilingIndexEntry(CIK, "American Electric Power", "10-K", "aep-2019", FY2018_10K),
            FilingIndexEntry(CIK, "American Electric Power", "8-K", "aep-8k-2020", MOVE_8K),
            FilingIndexEntry(CIK, "American Electric Power", "10-K", "aep-2021", FY2020_10K),
            FilingIndexEntry(PRERENAME_CIK, "Herc Holdings", "10-K", "hri-1994", EARLIEST),
            FilingIndexEntry(PRERENAME_CIK, "Herc Holdings", "10-K", "htz-2019", PRE_RENAME_10K),
        ],
        snapshot=[
            CompanySnapshotEntry(CIK, "American Electric Power", "AEP", "NASDAQ", FETCH),
            CompanySnapshotEntry(PRERENAME_CIK, "Herc Holdings", "HRI", "NASDAQ", FETCH),
        ],
        filename_listings=[
            FilenameListing(
                CIK,
                "aep-2019",
                "10-K",
                FY2018_10K,
                date(2019, 2, 25),
                "aep",
                "NYSE",
                "Common Stock",
                None,
            ),
            FilenameListing(
                CIK,
                "aep-2021",
                "10-K",
                FY2020_10K,
                date(2021, 2, 24),
                "aep",
                "NASDAQ",
                "Common Stock",
                "AEP",
            ),
            FilenameListing(
                PRERENAME_CIK,
                "htz-2019",
                "10-K",
                PRE_RENAME_10K,
                date(2019, 3, 4),
                "HTZ",
                "NYSE",
                "Common Stock",
                None,
            ),
        ],
    )


def _settings(*, filename_listings: bool = True) -> Settings:
    return Settings(_env_file=None, edgar={"filename_listings": filename_listings})


def _store(
    source: FixtureFilingSource, *, filename_listings: bool = True
) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    write_master(
        conn,
        build_master(
            source, _settings(filename_listings=filename_listings), ingested_at=INGESTED_AT
        ),
    )
    return conn


def _rows(conn: duckdb.DuckDBPyConnection, t: datetime, security_id: str) -> list[_ROW]:
    return sorted(
        (r["ticker"], r["exchange"], r["valid_from"], r["provenance"], r["known_at"])
        for r in listings_as_of(conn, t, [security_id]).iter_rows(named=True)
    )


def _static(conn: duckdb.DuckDBPyConnection, t: datetime) -> list[_STATIC]:
    return sorted(
        (r["security_id"], r["ticker"], r["exchange"], r["valid_from"], r["known_at"])
        for r in listings_as_of(conn, t).iter_rows(named=True)
        if r["provenance"] == "snapshot_static"
    )


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = _store(_source())
    try:
        yield connection
    finally:
        connection.close()


def test_aep_reads_nyse_then_nasdaq_never_the_static_venue(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    # Before the 2019 10-K's acceptance no read sees a listing: the static
    # NASDAQ row is known only at its 2026 fetch.
    assert _rows(conn, datetime(1995, 1, 3, 21, 0, tzinfo=UTC), CIK) == []
    assert _rows(conn, FY2018_10K - PROBE_EPSILON, CIK) == []

    nyse: list[_ROW] = [("AEP", "NYSE", date(2019, 2, 25), "filing_name", FY2018_10K)]
    assert _rows(conn, FY2018_10K + PROBE_EPSILON, CIK) == nyse

    # The 8-K exists but its Item 3.01 reading is not built: the store still
    # reads NYSE after the move's effective day, until the FY2020 10-K.
    assert _rows(conn, POST_MOVE, CIK) == nyse

    # Never NASDAQ before the FY2020 10-K's acceptance: the static row's
    # exchange is not evidence, so nothing reads NASDAQ on these sessions.
    for t in (
        FY2018_10K + PROBE_EPSILON,
        MOVE_8K + PROBE_EPSILON,
        POST_MOVE,
        FY2020_10K - PROBE_EPSILON,
    ):
        assert not any(row[1] == "NASDAQ" for row in _rows(conn, t, CIK))

    nasdaq: _ROW = ("AEP", "NASDAQ", date(2021, 2, 24), "filing_name", FY2020_10K)
    assert _rows(conn, FY2020_10K + PROBE_EPSILON, CIK) == sorted([nyse[0], nasdaq])


def test_the_static_rows_are_byte_identical_with_the_switch_off(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The filing-name release re-stamps nothing: the static rows it leaves
    behind equal a build with `edgar.filename_listings` off."""
    off = _store(_source(), filename_listings=False)
    try:
        assert _static(conn, FETCH) == _static(off, FETCH)
    finally:
        off.close()
    # And the values are the fixture's honest static ones (the premise).
    assert _static(conn, FETCH) == sorted(
        [
            (CIK, "AEP", "NASDAQ", date(1994, 5, 16), FETCH),
            (PRERENAME_CIK, "HRI", "NASDAQ", date(1994, 5, 16), FETCH),
        ]
    )


def test_a_static_row_cannot_refuse_a_pre_rename_prefix(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """The `HTZ` prefix differs from today's static `HRI`, so a confirmation
    that treated the static ticker as a reading would refuse it (#1682, C2:
    a `snapshot_static` row is not a reading)."""
    assert _rows(conn, PRE_RENAME_10K + PROBE_EPSILON, PRERENAME_CIK) == [
        ("HTZ", "NYSE", date(2019, 3, 4), "filing_name", PRE_RENAME_10K)
    ]
    assert not any(
        row[1] == "HRI" for row in _rows(conn, PRE_RENAME_10K + PROBE_EPSILON, PRERENAME_CIK)
    )


def test_no_writer_path_uses_the_static_row_before_its_known_at() -> None:
    """A store built from a source truncated to each record's own timestamp
    must agree at that `t` with the store built from the full source.

    This is the look-ahead teeth: if any writer path read a static row's
    exchange or `valid_from` to write or refuse a row known before the
    fetch, the full store would show a row the truncated source cannot know
    (for the pre-rename security, a refused old prefix), and the frames
    would differ.
    """
    source = _source()
    full = _store(source)
    try:
        # Premise (the test is not vacuous): the static rows are current
        # venues dated 1994, known only at the fetch.
        assert _static(full, FETCH) == sorted(
            [
                (CIK, "AEP", "NASDAQ", date(1994, 5, 16), FETCH),
                (PRERENAME_CIK, "HRI", "NASDAQ", date(1994, 5, 16), FETCH),
            ]
        )
        for stamp in source.known_ats():
            for t in (stamp - PROBE_EPSILON, stamp + PROBE_EPSILON):
                if t < EARLIEST:
                    continue
                truncated = _store(source.known_by(t))
                try:
                    full_rows = listings_as_of(full, t)
                    if t < FETCH:  # before its fetch no read sees a static row
                        assert all(
                            row["provenance"] != "snapshot_static"
                            for row in full_rows.iter_rows(named=True)
                        )
                    assert full_rows.equals(listings_as_of(truncated, t))
                finally:
                    truncated.close()
    finally:
        full.close()


def test_the_confirmation_rule_cannot_see_snapshot_rows() -> None:
    """`confirm_filename_listings` judges filing readings only (spec: a
    `snapshot_static` row "is **not** a reading"). Guard: no identifier in
    its module names a snapshot, so the rule cannot refer to one by name."""
    tree = ast.parse((_SRC / "store" / "filename_listings.py").read_text("utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not [name for name in names if "snapshot" in name.lower()]
