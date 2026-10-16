"""T191 (#1682 lever B): the reviewed delete of never-true master rows.

A fixture shaped as #1682 found Verizon's primary: the common's listing, the
2.375% note's listing on the same id (and the #859 retraction stamped at its
run), and the note's 25-NSE as a `delistings` row on the primary. After the
reviewed delete:

- the common's listing is never ended, at any `t`;
- the next derive of the note's Form 25 against the master lands it in
  `unmatched` (#1163's title match never reads a note as plain common);
- no read at a `t` before a deleted row's `known_at` changes, and a row whose
  evidence was accepted after its `known_at` is refused, so the delete never
  applies knowledge learned later.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner import repair
from tradepartner.adapters.filings import DelistingFiling, FilingHeader
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.delistings import (
    DELISTED,
    build_delistings,
    delistings_as_of,
    listing_ends_as_of,
)
from tradepartner.store.master import MasterBuild
from tradepartner.store.schema import init_schema

VZ = "0000732712"
NOTE = "2.375% Notes due 2022"
COMMON_KNOWN = datetime(2019, 8, 8, 17, 4, 24, tzinfo=UTC)
NOTE_KNOWN = datetime(2019, 10, 30, 16, 18, 19, tzinfo=UTC)
FORM25_AT = datetime(2020, 6, 15, 20, 43, 22, tzinfo=UTC)
TENQ, FORM25 = "0000732712-19-000063", "0000876661-20-000505"
INGESTED = datetime(2026, 10, 4, 2, 45, tzinfo=UTC)
RETRACTED_AT = datetime(2026, 10, 13, 18, 51, tzinfo=UTC)
RELEASE = "restate-test"

#: Month-end reads from before the common's first row to after the retraction.
PROBES = [
    session_close(date(2019, 7, 31)),
    *(
        datetime(year, month, 28, 21, tzinfo=UTC)
        for year in range(2019, 2027)
        for month in range(1, 13)
        if date(2019, 8, 1) <= date(year, month, 1) <= date(2026, 10, 1)
    ),
    datetime(2026, 10, 14, 21, tzinfo=UTC),
]


def _listing(ticker: str, title: str, known: datetime, **extra: Any) -> dict[str, Any]:
    return {
        "security_id": VZ,
        "ticker": ticker,
        "exchange": "NYSE",
        "class_title": title,
        "valid_from": extra.pop("valid_from", known.date()),
        "known_at": known,
        "ingested_at": extra.pop("ingested_at", INGESTED),
        "source": "edgar",
        "provenance": "filing",
        **extra,
    }


@pytest.fixture
def store(settings: Settings) -> Settings:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "securities",
            {
                "security_id": VZ,
                "cik": VZ,
                "name": "VERIZON COMMUNICATIONS INC",
                "known_at": datetime(2015, 2, 23, tzinfo=UTC),
                "ingested_at": INGESTED,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for row in (
            _listing("VZ", "Common Stock, par value $0.10", COMMON_KNOWN),
            _listing("VZ22A", NOTE, NOTE_KNOWN),
            _listing(
                "VZ22A",
                NOTE,
                RETRACTED_AT,
                valid_from=NOTE_KNOWN.date(),
                ingested_at=RETRACTED_AT,
                retracted=True,
            ),
        ):
            insert_row(conn, "listings", row)
        insert_row(
            conn,
            "delistings",
            {
                "security_id": VZ,
                "form": "25-NSE",
                "class_title": NOTE,
                "exchange": "NYSE",
                "filed_at": FORM25_AT,
                "effective_on": date(2020, 6, 25),
                "known_at": FORM25_AT,
                "ingested_at": INGESTED,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        registry.open_data_release(conn, conn, name=RELEASE, backup_path="b.duckdb", reason="t")
    return settings


def _manifest() -> list[repair.MasterRowKey]:
    note: dict[str, Any] = {
        "table": "listings",
        "security_id": VZ,
        "exchange": "NYSE",
        "class_title": NOTE,
        "ticker": "VZ22A",
        "valid_from": NOTE_KNOWN.date(),
        "accession": TENQ,
    }
    return [
        repair.MasterRowKey(**note, known_at=NOTE_KNOWN),
        repair.MasterRowKey(**note, known_at=RETRACTED_AT),
        repair.MasterRowKey(
            table="delistings",
            security_id=VZ,
            exchange="NYSE",
            class_title=NOTE,
            form="25-NSE",
            filed_at=FORM25_AT,
            known_at=FORM25_AT,
            accession=FORM25,
        ),
    ]


def _headers(late: timedelta = timedelta(0)) -> repair.HeaderLookup:
    stamps = {TENQ: NOTE_KNOWN + late, FORM25: FORM25_AT}

    def lookup(cik: str, accession: str) -> FilingHeader:
        return FilingHeader(cik, accession, "10-Q", 4813, stamps[accession])

    return lookup


def _delete(settings: Settings, late: timedelta = timedelta(0)) -> None:
    with open_for_write(settings) as conn:
        repair.delete_master_rows(
            conn,
            _manifest(),
            RELEASE,
            dry_run=False,
            headers=_headers(late),
            clock=lambda: datetime(2026, 10, 15, tzinfo=UTC),
        )


def _reads(settings: Settings) -> dict[datetime, tuple[pl.DataFrame, pl.DataFrame]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return {t: (listings_as_of(conn, t), delistings_as_of(conn, t)) for t in PROBES}


def _ends(settings: Settings, t: datetime) -> pl.DataFrame:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return listing_ends_as_of(conn, t, settings, [VZ]).sort("valid_from")


def test_after_the_delete_the_commons_listing_is_never_ended(store: Settings) -> None:
    # The defect: from the 25-NSE until the retraction is known, the
    # primary's latest listing is the note, and the 25-NSE ends it.
    defect = _ends(store, datetime(2021, 1, 28, 21, tzinfo=UTC))
    assert defect["ticker"].to_list()[-1] == "VZ22A" and defect["status"][-1] == DELISTED
    _delete(store)
    for t in PROBES:
        ends = _ends(store, t)
        if t < COMMON_KNOWN:
            assert ends.is_empty()
            continue
        assert ends["ticker"].to_list() == ["VZ"], t
        assert ends["status"].to_list() != [DELISTED] and ends["end_session"].to_list() == [None]


def _master(settings: Settings) -> MasterBuild:
    """The master an EDGAR derive would read for the class match: the
    stored rows, as `build_master` yields them (no retraction rows)."""
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        securities = conn.execute("SELECT * FROM securities").pl().to_dicts()
        listings = conn.execute("SELECT * FROM listings WHERE NOT retracted").pl().to_dicts()
    return MasterBuild(
        securities=tuple(securities),
        listings=tuple(listings),
        unmatched_snapshot=(),
        missing_benchmarks=(),
    )


def test_the_next_derive_lands_the_notes_form_25_in_unmatched(store: Settings) -> None:
    filing = DelistingFiling(VZ, "25-NSE", NOTE, "NYSE", FORM25, FORM25_AT, None)
    ingested = datetime(2026, 10, 15, tzinfo=UTC)
    before = build_delistings([filing], _master(store), ingested_at=ingested)
    assert [row["security_id"] for row in before.delistings] == [VZ]  # the defect's row
    _delete(store)
    after = build_delistings([filing], _master(store), ingested_at=ingested)
    assert after.delistings == () and after.unmatched == (filing,)


def test_no_read_before_a_deleted_rows_known_at_changes(store: Settings) -> None:
    before = _reads(store)
    _delete(store)
    after = _reads(store)
    for t in PROBES:
        (listings, delistings), (listings_after, delistings_after) = before[t], after[t]
        if t < NOTE_KNOWN:
            assert listings_after.equals(listings) and delistings_after.equals(delistings), t
        assert "VZ22A" not in listings_after["ticker"].to_list(), t
        assert delistings_after.is_empty(), t


def test_evidence_accepted_after_the_row_is_never_applied(store: Settings) -> None:
    before = _reads(store)
    with pytest.raises(repair.RepairRefused, match="after the row's known_at"):
        _delete(store, late=timedelta(seconds=1))
    after = _reads(store)
    assert all(after[t][0].equals(before[t][0]) for t in PROBES)
    assert all(after[t][1].equals(before[t][1]) for t in PROBES)
