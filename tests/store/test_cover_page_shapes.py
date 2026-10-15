"""Cover-page listings under the exchange axis, end to end (#1541, plan T178).

A fixture store built from five names' cover-page shapes (IBM, AbbVie,
Abbott, 3M and P&G, the synthetic iXBRL documents under
`tests/fixtures/edgar/cover_exchange_axis/`), parsed by `parse_cover_page`
with `edgar.exchange_axis_listings` off and on, ingested through the EDGAR
chunk, given synthetic bars and shares over 2020 to 2021, and read with
`universe_as_of` (the spec amendment's "Master and universe" acceptance).
The four names whose common is tagged only by exchange have no `filing`
listing with the switch off, only the companies snapshot's (known
2026-10-04), so every earlier read sees them `not_listed`; P&G's first class
read is its note. With the switch on, each common is a member at
`close(2021-06-30)` from a `filing` row known at its 2019 acceptance, and
none is listed before it (no look-ahead).

"The residual" case: a store built with the merge-base rules through
2022-10-19 (P&G's note on the primary id) and re-ingested with the new rules
still reads the note on the primary id at `close(2023-06-30)`; the stored
note row is underived and `master-retract` withdraws it at its run, never
back-dated (#859; spec open question 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.edgar import parse_cover_page, parse_fsn
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverPage,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.calendar import next_session, previous_session, session_close
from tradepartner.config import Settings
from tradepartner.ingest import ingest_session
from tradepartner.retract import master_retract
from tradepartner.store import registry
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import open_for_write
from tradepartner.store.schema import init_schema
from tradepartner.universe import _current_listings, universe_as_of

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "cover_exchange_axis"

IBM, ABBV, ABT, MMM, PG = "0000051143", "0001551152", "0000001800", "0000066740", "0000080424"


def _at(year: int, month: int, day: int, hour: int = 20) -> datetime:
    return datetime(year, month, day, hour, 30, tzinfo=UTC)


#: CIK -> (name, SIC, the 2019 cover document, its acceptance).
NAMES: dict[str, tuple[str, int, str, datetime]] = {
    IBM: ("International Business Machines", 3570, "ibm_10q_2019q2.htm", _at(2019, 7, 30)),
    ABBV: ("AbbVie Inc.", 2834, "abbvie_10q_2019q2.htm", _at(2019, 8, 2)),
    ABT: ("Abbott Laboratories", 2834, "abbott_10q_2019q2.htm", _at(2019, 8, 2, 16)),
    MMM: ("3M Co", 3841, "mmm_10q_2019q2.htm", _at(2019, 7, 26)),
    PG: ("Procter & Gamble Co", 2840, "pg_10k_2019.htm", _at(2019, 8, 6)),
}
EARLIEST = min(accepted for *_, accepted in NAMES.values())
SNAPSHOT_AT = datetime(2026, 10, 4, 2, 16, 55, tzinfo=UTC)
TICKERS = {IBM: "IBM", ABBV: "ABBV", ABT: "ABT", MMM: "MMM", PG: "PG"}
RUN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
T_2021 = session_close(date(2021, 6, 30))
#: The close of the last session before the earliest 2019 acceptance.
T_BEFORE = session_close(previous_session(EARLIEST.date()))


def _accession(cik: str, year: int, n: int) -> str:
    return f"{cik}-{year % 100:02d}-{n:06d}"


def _cover(document: str, cik: str, accession: str, accepted_at: datetime, on: bool) -> CoverPage:
    parsed = parse_cover_page(
        (FIXTURES / document).read_bytes(),
        accession=accession,
        accepted_at=accepted_at,
        exchange_axis=on,
    )
    assert parsed.cover.cik == cik
    return CoverPage(cik, accession, accepted_at, parsed.cover.listings)


def _shares(cik: str, accepted_at: datetime, accession: str) -> FactRecord:
    """A synthetic undimensioned cover count (2020-2021), 1.5bn shares."""
    return FactRecord(
        cik,
        "EntityCommonStockSharesOutstanding",
        accepted_at.date(),
        "",
        1_500_000_000,
        accession,
        accepted_at,
    )


def _source(
    on: bool,
    *,
    names: Sequence[str] = tuple(NAMES),
    pg_pages: Sequence[tuple[str, datetime]] | None = None,
    until: datetime | None = None,
) -> FixtureFilingSource:
    """The five names: a 2015 10-K (the security's first filing), the 2019
    cover page parsed with the switch `on` or off, quarterly 10-Qs over 2020
    to 2021 that repeat it (each carrying a synthetic shares fact), and the
    companies snapshot of 2026-10-04. `pg_pages` replaces P&G's cover pages
    with (document, acceptance) pairs; `until` keeps only what was accepted
    by then."""
    index: list[FilingIndexEntry] = []
    headers: list[FilingHeader] = []
    pages: list[CoverPage] = []
    facts: list[FactRecord] = []
    for cik in names:
        name, sic, document, accepted = NAMES[cik]
        first = _accession(cik, 2015, 1)
        index.append(FilingIndexEntry(cik, name, "10-K", first, _at(2015, 2, 24)))
        headers.append(FilingHeader(cik, first, "10-K", sic, _at(2015, 2, 24)))
        dated = [(document, accepted)] + [
            (document, _at(year, month, 28)) for year in (2020, 2021) for month in (1, 4, 7, 10)
        ]
        if cik == PG and pg_pages is not None:
            dated = list(pg_pages)
        for n, (doc, at) in enumerate(dated, start=2):
            if until is not None and at > until:
                continue
            accession = _accession(cik, at.year, n)
            form = "10-K" if doc.startswith("pg_10k") else "10-Q"
            index.append(FilingIndexEntry(cik, name, form, accession, at))
            headers.append(FilingHeader(cik, accession, form, sic, at))
            pages.append(_cover(doc, cik, accession, at, on))
            if at.year >= 2020:
                facts.append(_shares(cik, at, accession))
    snapshot = [
        CompanySnapshotEntry(cik, NAMES[cik][0], TICKERS[cik], "NYSE", SNAPSHOT_AT)
        for cik in names
        if until is None or until >= SNAPSHOT_AT
    ]
    return FixtureFilingSource(
        index=index, headers=headers, cover_pages=pages, facts=facts, snapshot=snapshot
    )


class _NoPrices(PriceSource):
    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return []

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        return []


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _ingest(settings: Settings, source: FixtureFilingSource, at: datetime) -> None:
    result = ingest_session(
        settings, prices=_NoPrices(), filings=source, source="edgar", clock=lambda: at
    )
    assert result.ok, result.runs


def _bars(settings: Settings, security_ids: Sequence[str], start: date, end: date) -> None:
    """Synthetic bars for `security_ids`, each known at its session's close:
    USD 150 on 10m shares a day, past every price, liquidity and size rule."""
    rows: list[tuple[Any, ...]] = []
    session = start
    while session <= end:
        close = session_close(session)
        for sid in security_ids:
            rows.append((sid, session, 150.0, 150.0, 150.0, 150.0, 10_000_000, close))
        session = next_session(session)
    with open_for_write(settings) as conn:
        conn.executemany(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
            "known_at, ingested_at, source, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'alpaca_sip', 'bar')",
            [(*row, RUN) for row in rows],
        )


def _read(settings: Settings) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(settings.store.path, read_only=True)


def _store(settings: Settings, on: bool) -> Settings:
    _ingest(settings, _source(on), RUN)
    _bars(settings, list(NAMES), date(2020, 1, 2), date(2021, 6, 30))
    return settings


def _excluded(settings: Settings, t: datetime) -> dict[str, tuple[int, str, str]]:
    with _read(settings) as conn:
        u = universe_as_of(conn, t, settings)
    return {
        row["security_id"]: (row["rule"], row["rule_name"], row["reason"])
        for row in u.exclusions.iter_rows(named=True)
    }


def _members(settings: Settings, t: datetime) -> set[str]:
    with _read(settings) as conn:
        return set(universe_as_of(conn, t, settings).members["security_id"].to_list())


def _filing_listings(settings: Settings, t: datetime) -> list[tuple[Any, ...]]:
    with _read(settings) as conn:
        rows = listings_as_of(conn, t).filter(pl.col("provenance") == "filing")
    keys = ("security_id", "ticker", "exchange", "class_title", "valid_from", "known_at")
    return sorted(tuple(row[k] for k in keys) for row in rows.iter_rows(named=True))


def _current(settings: Settings, security_id: str, t: datetime) -> dict[str, Any]:
    """The listing `universe_as_of` reads for `security_id` at `t` (rule 2)."""
    with _read(settings) as conn:
        return _current_listings(conn, t, t.date(), settings)[security_id]


# --- master and universe -------------------------------------------------------


def test_with_the_switch_off_the_names_stay_out_as_on_the_merge_base(
    settings: Settings,
) -> None:
    """Today's rules, pinned: the four exchange-tagged commons have only the
    snapshot listing (known 2026-10-04), so at `close(2021-06-30)` they are
    rule 2 `not_listed`; P&G's primary id carries its undimensioned note, so
    it fails rule 1 as `debt`."""
    _store(settings, on=False)
    assert _members(settings, T_2021) == set()
    assert _excluded(settings, T_2021) == {
        IBM: (2, "exchange", "not_listed"),
        ABBV: (2, "exchange", "not_listed"),
        ABT: (2, "exchange", "not_listed"),
        MMM: (2, "exchange", "not_listed"),
        PG: (1, "security_type", "debt"),
    }
    assert [row[:3] for row in _filing_listings(settings, RUN)] == [(PG, "PG25", "NYSE")]


def test_with_the_switch_on_the_five_commons_are_members_from_their_filing_rows(
    settings: Settings,
) -> None:
    _store(settings, on=True)
    assert _members(settings, T_2021) == set(NAMES)
    for cik, (*_, accepted) in NAMES.items():
        listing = _current(settings, cik, T_2021)
        assert listing["provenance"] == "filing"
        assert listing["known_at"] == accepted  # the 2019 acceptance, not the run
        assert listing["ticker"] == TICKERS[cik]  # P&G's primary id carries PG
    # the notes are their own classes, out by rule 1
    assert _excluded(settings, T_2021) == {
        f"{MMM}:1-500pct-notes-due-2026": (1, "security_type", "debt"),
        f"{PG}:1-375pct-notes-due-2025": (1, "security_type", "debt"),
    }


def test_with_the_switch_on_no_name_is_listed_before_its_filing_is_known(
    settings: Settings,
) -> None:
    """No look-ahead: a `filing` row is known at its acceptance, so at the
    close before the earliest one all five are `not_listed`, and each name's
    listing is read from its own acceptance instant, never a moment before."""
    _store(settings, on=True)
    assert _excluded(settings, T_BEFORE) == dict.fromkeys(NAMES, (2, "exchange", "not_listed"))
    for cik, (*_, accepted) in NAMES.items():
        with _read(settings) as conn:
            just_before = accepted - timedelta(microseconds=1)
            assert cik not in set(listings_as_of(conn, just_before)["security_id"].to_list())
            assert cik in set(listings_as_of(conn, accepted)["security_id"].to_list())


def test_the_listing_rows_read_at_t_are_those_a_store_built_at_t_holds(
    settings: Settings, tmp_path: Path
) -> None:
    """Truncation invariance for the new rows: the `filing` listings read at
    `close(2021-06-30)` from the full store equal those of a store built
    only from what was known by then."""
    _ingest(settings, _source(True), RUN)
    truncated = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "truncated.duckdb"), "lock_retry_seconds": 1},
    )
    _ingest(truncated, _source(True).known_by(T_2021), RUN)
    assert _filing_listings(settings, T_2021) == _filing_listings(truncated, T_2021)
    assert len(_filing_listings(settings, T_2021)) == 8  # 5 commons, ABBV's second, 2 notes


# --- the residual (P&G's note on the primary id) ------------------------------

#: P&G's covers: the common tagged only by exchange from 2019, a note
#: (undimensioned) listed beside it from 2022-10-19.
PG_PAGES = (
    ("pg_10k_2019_common_only.htm", _at(2019, 8, 6)),
    ("pg_10k_2019_common_only.htm", _at(2021, 8, 6)),
    ("pg_10k_2019.htm", _at(2022, 10, 19)),
    ("pg_10k_2019.htm", _at(2023, 4, 19)),
)
FIRST_RUN = datetime(2022, 10, 20, 12, 0, tzinfo=UTC)
RETRACT_AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
T_2023 = session_close(date(2023, 6, 30))
NOTE_KEY = (PG, "PG25", "NYSE", date(2022, 10, 19))


def test_the_residual_pg_reads_as_its_note_until_the_retraction(settings: Settings) -> None:
    """Built with today's rules through 2022-10-19, P&G's primary id carries
    the note; re-ingested with the switch on, the common is derived on the
    primary id from 2019 (known at that acceptance) and the note on its
    class id, but the stored note row on the primary id stays until
    `master-retract` withdraws it, stamped at its run (#859). So at
    `close(2023-06-30)` the primary id still reads as the note (the latest
    `valid_from`): the documented residual, spec open question 4."""
    _ingest(settings, _source(False, names=[PG], pg_pages=PG_PAGES, until=FIRST_RUN), FIRST_RUN)
    assert _current(settings, PG, T_2023)["ticker"] == "PG25"

    source = _source(True, names=[PG], pg_pages=PG_PAGES)
    _ingest(settings, source, RUN)
    assert _current(settings, PG, T_2023)["ticker"] == "PG25"
    common = _current(settings, PG, session_close(date(2021, 6, 30)))
    assert (common["ticker"], common["known_at"]) == ("PG", PG_PAGES[0][1])

    with open_for_write(settings) as conn:
        init_schema(conn)
        registry.open_data_release(conn, conn, name="t178", backup_path="b.duckdb", reason="#1541")
    dry = master_retract(settings, filings=source, clock=lambda: RETRACT_AT)
    assert [(u.table, u.key) for u in dry.found] == [("listings", NOTE_KEY)]
    master_retract(
        settings,
        filings=source,
        clock=lambda: RETRACT_AT,
        dry_run=False,
        expect=(dry.rows, dry.digest),
        release="t178",
    )
    # never back-dated: every read before the run still sees the note
    assert _current(settings, PG, T_2023)["ticker"] == "PG25"
    assert _current(settings, PG, session_close(date(2026, 10, 2)))["ticker"] == "PG25"
    after = _current(settings, PG, session_close(date(2026, 10, 6)))
    assert after["ticker"] == "PG"
    with _read(settings) as conn:
        assert conn.execute(
            "SELECT known_at, retracted FROM listings WHERE security_id = ? AND ticker = ? "
            "ORDER BY known_at",
            [PG, "PG25"],
        ).fetchall() == [(_at(2022, 10, 19), False), (RETRACT_AT, True)]


# --- T193 (#1682, #1675): NiSource's real FSN rows, end to end ------------------------

NI = "0001111711"
NI_ACCESSION = "0001111711-21-000018"
NI_ACCEPTED = datetime(2021, 5, 5, 17, 8, 52, tzinfo=UTC)
FSN_SPLIT = FIXTURES.parent / "fsn_split" / "nisource_2021q2"


def _ni_source(on: bool) -> FixtureFilingSource:
    """NiSource with one cover page, its real 2021-05-05 10-Q as FSN holds it
    (Common Stock `NI`, Series A Corporate Units `NIMC`, the Series B
    depositary shares), parsed with the switch `on` or off; the companies
    snapshot names `NI`."""

    def rows(name: str) -> list[dict[str, str]]:
        lines = (FSN_SPLIT / name).read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        return [dict(zip(header, line.split("\t"), strict=False)) for line in lines[1:]]

    parsed = parse_fsn(
        rows("sub.tsv"),
        rows("num.tsv"),
        rows("txt.tsv"),
        rows("dim.tsv"),
        exchange_axis=True,
        split_contexts=on,
    )
    [record] = parsed.records
    first = _accession(NI, 2015, 1)
    return FixtureFilingSource(
        index=[
            FilingIndexEntry(NI, "NiSource Inc.", "10-K", first, _at(2015, 2, 18)),
            FilingIndexEntry(NI, "NiSource Inc.", "10-Q", NI_ACCESSION, NI_ACCEPTED),
        ],
        headers=[
            FilingHeader(NI, first, "10-K", 4931, _at(2015, 2, 18)),
            FilingHeader(NI, NI_ACCESSION, "10-Q", 4931, NI_ACCEPTED),
        ],
        cover_pages=[CoverPage(NI, NI_ACCESSION, NI_ACCEPTED, record.listings)],
        facts=[],
        snapshot=[CompanySnapshotEntry(NI, "NiSource Inc.", "NI", "NYSE", SNAPSHOT_AT)],
    )


def test_nisource_common_holds_the_primary_id_and_its_units_their_own(
    settings: Settings,
) -> None:
    """#1675's done-when, end to end: with the switch on, the common `NI` is
    the primary id's filing listing from its 2021-05-05 acceptance, and
    `NIMC` sits on its own class id, never on the common's."""
    _ingest(settings, _ni_source(True), RUN)
    rows = _filing_listings(settings, RUN)
    on_primary = [row for row in rows if row[0] == NI]
    assert [(r[1], r[2], r[5]) for r in on_primary] == [("NI", "NYSE", NI_ACCEPTED)]
    [units] = [row for row in rows if row[1] == "NIMC"]
    assert units[0] != NI and units[0].startswith(f"{NI}:")
    assert units[3] == "Series A Corporate Units"


def test_nisource_with_the_switch_off_has_no_filing_listing(settings: Settings) -> None:
    _ingest(settings, _ni_source(False), RUN)
    assert _filing_listings(settings, RUN) == []
