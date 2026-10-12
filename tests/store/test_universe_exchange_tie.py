"""Universe rule 2: a `valid_from` tie between two exchanges of one class (#1574).

`universe._current_listings` kept the first row of a tie in frame order, and
`listings_as_of` orders by `(security_id, ticker, exchange, valid_from)`: a
class whose cover page names NYSE and a venue sorting earlier (CBOE_BZX) was
read on the earlier-sorting venue and excluded as `exchange:CBOE_BZX`, though
its NYSE listing was live. Rule 2 now breaks that tie toward a row on a venue
in `universe.exchanges`, whatever order the names sort in; with no such row,
the frame order still decides (unchanged).

Fixtures: NYSE + CBOE_BZX (must be a member), AbbVie's NYSE + NYSE_CHICAGO
shape (unchanged: NYSE), and CBOE_BZX + NYSE_CHICAGO (neither in
`universe.exchanges`: unchanged, `exchange:CBOE_BZX`).

The store is the small hand-built one below: one cover page per name carrying
the two listings under one acceptance, quarterly filings over 2020-2021 that
repeat it (each a shares fact), and synthetic bars — just enough for rules
1-8. It is separate from `test_cover_page_shapes.py`'s five-name fixture so
that fixture's row counts stay pinned.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.calendar import next_session, session_close
from tradepartner.config import Settings
from tradepartner.ingest import ingest_session
from tradepartner.store.db import open_for_write
from tradepartner.universe import _current_listings, universe_as_of

#: A class traded on a venue in `universe.exchanges` (NYSE) and one that
#: sorts before it (`CBOE_BZX`): frame order reads the second, so pre-#1574
#: rule 2 saw `exchange:CBOE_BZX` and excluded a live name.
BZX = "0000000011"
#: A control: one NYSE listing only, always a member.
GOOD = "0000000012"
#: AbbVie's shape: NYSE and `NYSE_CHICAGO` (`NYSE` sorts first, so the
#: pre-#1574 frame order already read NYSE; unchanged either way).
CHI = "0000000013"
#: Neither venue is in `universe.exchanges`: the tie keeps the first row.
TIE = "0000000014"

#: cik -> (name, ticker, the exchanges its one class's cover page names).
NAMES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    BZX: ("Bzx Tie Inc.", "BZX", ("NYSE", "CBOE_BZX")),
    GOOD: ("Good Listing Co.", "GOOD", ("NYSE",)),
    CHI: ("Chicago Tie Inc.", "CHI", ("NYSE", "NYSE_CHICAGO")),
    TIE: ("Neither Tie Co.", "TIE", ("CBOE_BZX", "NYSE_CHICAGO")),
}

COMMON = "Common Stock, $0.01 par value per share"
ACCEPTED = datetime(2019, 7, 30, 20, 30, tzinfo=UTC)
SNAPSHOT_AT = datetime(2026, 10, 4, 2, 16, 55, tzinfo=UTC)
RUN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
T_2021 = session_close(date(2021, 6, 30))


def _at(year: int, month: int, day: int, hour: int = 20) -> datetime:
    return datetime(year, month, day, hour, 30, tzinfo=UTC)


def _accession(cik: str, year: int, n: int) -> str:
    return f"{cik}-{year % 100:02d}-{n:06d}"


def _source() -> FixtureFilingSource:
    """One name per tie shape: a 2015 10-K (its first filing), the 2019 cover
    page naming its class's exchanges, quarterly 2020-2021 10-Qs that repeat
    it (each a shares fact), and the 2026-10-04 companies snapshot."""
    index: list[FilingIndexEntry] = []
    headers: list[FilingHeader] = []
    pages: list[CoverPage] = []
    facts: list[FactRecord] = []
    for cik, (name, ticker, exchanges) in NAMES.items():
        first = _accession(cik, 2015, 1)
        index.append(FilingIndexEntry(cik, name, "10-K", first, _at(2015, 2, 24)))
        headers.append(FilingHeader(cik, first, "10-K", 3570, _at(2015, 2, 24)))
        listings = tuple(CoverListing(COMMON, ticker, exchange) for exchange in exchanges)
        dated = [ACCEPTED] + [
            _at(year, month, 28) for year in (2020, 2021) for month in (1, 4, 7, 10)
        ]
        for n, at in enumerate(dated, start=2):
            accession = _accession(cik, at.year, n)
            index.append(FilingIndexEntry(cik, name, "10-Q", accession, at))
            headers.append(FilingHeader(cik, accession, "10-Q", 3570, at))
            pages.append(CoverPage(cik, accession, at, listings))
            if at.year >= 2020:
                facts.append(
                    FactRecord(
                        cik,
                        "EntityCommonStockSharesOutstanding",
                        at.date(),
                        "",
                        1_500_000_000,
                        accession,
                        at,
                    )
                )
    snapshot = [
        CompanySnapshotEntry(cik, name, ticker, "NYSE", SNAPSHOT_AT)
        for cik, (name, ticker, _) in NAMES.items()
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


def _store(settings: Settings) -> Settings:
    """Ingest the fixture and add synthetic bars: USD 150 on 10m shares a day,
    past every price, liquidity and size rule."""
    result = ingest_session(
        settings, prices=_NoPrices(), filings=_source(), source="edgar", clock=lambda: RUN
    )
    assert result.ok, result.runs
    rows: list[tuple[Any, ...]] = []
    session = date(2020, 1, 2)
    while session <= date(2021, 6, 30):
        close = session_close(session)
        for cik in NAMES:
            rows.append((cik, session, 150.0, 150.0, 150.0, 150.0, 10_000_000, close))
        session = next_session(session)
    with open_for_write(settings) as conn:
        conn.executemany(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
            "known_at, ingested_at, source, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'alpaca_sip', 'bar')",
            [(*row, RUN) for row in rows],
        )
    return settings


def _read(settings: Settings) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(settings.store.path, read_only=True)


def _current(settings: Settings, security_id: str) -> dict[str, Any]:
    """The listing rule 2 reads for `security_id` at `close(2021-06-30)`."""
    with _read(settings) as conn:
        return _current_listings(conn, T_2021, T_2021.date(), settings)[security_id]


def _members(settings: Settings) -> set[str]:
    with _read(settings) as conn:
        return set(universe_as_of(conn, T_2021, settings).members["security_id"].to_list())


def _excluded(settings: Settings) -> dict[str, tuple[int, str, str]]:
    with _read(settings) as conn:
        u = universe_as_of(conn, T_2021, settings)
    return {
        row["security_id"]: (row["rule"], row["rule_name"], row["reason"])
        for row in u.exclusions.iter_rows(named=True)
    }


def test_a_tie_goes_to_the_venue_in_universe_exchanges(settings: Settings) -> None:
    """`CBOE_BZX` sorts before `NYSE`, so the old frame order read the name on
    `CBOE_BZX` and rule 2 failed it as `exchange:CBOE_BZX`. The tie now goes
    to the NYSE row: the name is a member, from 2019, off a live listing."""
    _store(settings)
    listing = _current(settings, BZX)
    assert (listing["exchange"], listing["status"]) == ("NYSE", "listed")
    assert listing["valid_from"] == ACCEPTED.date()
    assert BZX in _members(settings)


def test_the_abbvie_shape_still_reads_nyse(settings: Settings) -> None:
    """`NYSE` sorts before `NYSE_CHICAGO`, so the frame order already read
    NYSE; the tie-break keeps it (a regression guard, not a fix)."""
    _store(settings)
    assert _current(settings, CHI)["exchange"] == "NYSE"
    assert CHI in _members(settings)


def test_a_tie_outside_universe_exchanges_is_unchanged(settings: Settings) -> None:
    """Neither `CBOE_BZX` nor `NYSE_CHICAGO` is in `universe.exchanges`, so
    the first row in frame order still wins: `CBOE_BZX`, and rule 2 excludes
    the name exactly as before."""
    _store(settings)
    assert _current(settings, TIE)["exchange"] == "CBOE_BZX"
    assert _excluded(settings) == {TIE: (2, "exchange", "exchange:CBOE_BZX")}


def test_the_universe_reads_the_expected_members(settings: Settings) -> None:
    """End to end: the two tied names with an NYSE row plus the single-listing
    control are members; the all-outside tie stays out."""
    _store(settings)
    assert _members(settings) == {BZX, GOOD, CHI}
