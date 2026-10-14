"""Cover-page listings under the exchange axis, end to end (#1541, plan T178).

A fixture store built from five names' cover-page shapes (IBM, AbbVie,
Abbott, 3M and P&G, the synthetic iXBRL documents under
`tests/fixtures/edgar/cover_exchange_axis/`), parsed by `parse_cover_page`
with `edgar.exchange_axis_listings` off and on, ingested through the EDGAR
chunk, given synthetic bars and shares over 2020 to 2021, and read with
`universe_as_of` (the spec amendment's "Master and universe" acceptance).
The four names whose common is tagged only by exchange have no `filing`
listing with the switch off, only the companies snapshot's (known
2026-10-04), so every earlier read sees them `not_listed`; P&G's note has
its own class id. With the switch on, each common is a member at
`close(2021-06-30)` from a `filing` row known at its 2019 acceptance, and
none is listed before it (no look-ahead).

P&G's lone note page through 2022-10-19 leaves the primary unlisted. A
re-ingest with exchange-axis cover parsing adds the common to the primary,
and the note remains on its own class id; `master-retract` has no stale
PG25 row on the primary to withdraw (#1680).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.edgar import parse_cover_page
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
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import open_for_write
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


def test_with_the_switch_off_the_names_stay_out(
    settings: Settings,
) -> None:
    """Today's rules, pinned: the four exchange-tagged commons have only the
    snapshot listing (known 2026-10-04), so at `close(2021-06-30)` they are
    rule 2 `not_listed`; P&G's note is a separate debt class."""
    _store(settings, on=False)
    assert _members(settings, T_2021) == set()
    assert _excluded(settings, T_2021) == {
        IBM: (2, "exchange", "not_listed"),
        ABBV: (2, "exchange", "not_listed"),
        ABT: (2, "exchange", "not_listed"),
        MMM: (2, "exchange", "not_listed"),
        PG: (2, "exchange", "not_listed"),
        f"{PG}:1-375pct-notes-due-2025": (1, "security_type", "debt"),
    }
    assert [row[:3] for row in _filing_listings(settings, RUN)] == [
        (f"{PG}:1-375pct-notes-due-2025", "PG25", "NYSE")
    ]


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


# --- P&G's note stays off the primary id --------------------------------------

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


def test_pg_never_reads_as_its_note_on_the_primary(settings: Settings) -> None:
    """A lone note on the first parsed page never creates a stale PG25 row
    on P&G's primary. A later exchange-axis common arrives at its own
    acceptance, and no retraction of a primary PG25 row is needed."""
    _ingest(settings, _source(False, names=[PG], pg_pages=PG_PAGES, until=FIRST_RUN), FIRST_RUN)
    with _read(settings) as conn:
        assert PG not in _current_listings(conn, T_2023, T_2023.date(), settings)
    assert _current(settings, f"{PG}:1-375pct-notes-due-2025", T_2023)["ticker"] == "PG25"

    source = _source(True, names=[PG], pg_pages=PG_PAGES)
    _ingest(settings, source, RUN)
    assert _current(settings, PG, T_2023)["ticker"] == "PG"
    common = _current(settings, PG, session_close(date(2021, 6, 30)))
    assert (common["ticker"], common["known_at"]) == ("PG", PG_PAGES[0][1])

    dry = master_retract(settings, filings=source, clock=lambda: RETRACT_AT)
    assert not any(
        u.table == "listings" and u.key[0] == PG and u.key[1] == "PG25" for u in dry.found
    )
    assert _current(settings, PG, session_close(date(2026, 10, 2)))["ticker"] == "PG"
    with _read(settings) as conn:
        assert (
            conn.execute(
                "SELECT known_at, retracted FROM listings WHERE security_id = ? AND ticker = ? "
                "ORDER BY known_at",
                [PG, "PG25"],
            ).fetchall()
            == []
        )
