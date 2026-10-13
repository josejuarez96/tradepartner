"""Filename listing rows stay invisible until each filing's acceptance."""

import json
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from lookahead.harness import PROBE_EPSILON, TruncatedStore
from tradepartner.adapters.alpaca_prices import ListingResolver, registrant_evidence
from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilenameListing,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings
from tradepartner.ingest import _filename_counts
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.delistings import build_delistings, derive_listing_ends
from tradepartner.store.master import build_master, write_master
from tradepartner.store.schema import init_schema
from tradepartner.universe import universe_as_of

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "universe" / "filename_cases.json"


def test_filename_cases_regenerate_identically(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "make_fixture_universe.py"
    subprocess.run([sys.executable, str(script), str(tmp_path)], check=True)
    assert (tmp_path / CASES.name).read_bytes() == CASES.read_bytes()


def _case_source(case: dict[str, Any]) -> FixtureFilingSource:
    cik = case["cik"]
    index = []
    readings = []
    for n, item in enumerate(case["readings"]):
        accession = "form25" if item["form"] in {"25", "25-NSE"} else f"annual-{n}"
        at = datetime.fromisoformat(item["accepted_at"])
        index.append(FilingIndexEntry(cik, case["case"], item["form"], accession, at))
        readings.append(
            FilenameListing(
                cik,
                accession,
                item["form"],
                at,
                at.date(),
                item["ticker"],
                item["exchange"],
                None,
                item["dei_symbol"],
            )
        )
    pages = []
    if cover := case.get("cover"):
        at = datetime.fromisoformat(cover["accepted_at"])
        pages.append(
            CoverPage(
                cik,
                "cover",
                at,
                (CoverListing("Common Stock", cover["ticker"], cover["exchange"]),),
            )
        )
    forms25 = []
    if form25 := case.get("form25"):
        at = datetime.fromisoformat(form25["accepted_at"])
        if not any(item.form in {"25", "25-NSE"} for item in index):
            index.append(FilingIndexEntry(cik, case["case"], "25-NSE", "form25", at))
        forms25.append(
            DelistingFiling(
                cik,
                "25-NSE",
                "Common Stock",
                form25["exchange"],
                "form25",
                at,
                date.fromisoformat(form25["effective_on"]),
            )
        )
    return FixtureFilingSource(
        index=index,
        cover_pages=pages,
        delistings=forms25,
        filename_listings=readings,
    )


def test_generated_filename_cases_cover_verdicts_and_form25_matching() -> None:
    cases = json.loads(CASES.read_text())
    settings = Settings(_env_file=None, edgar={"filename_listings": True})
    at = datetime(2020, 1, 1, tzinfo=UTC)
    results = {}
    counts = {}
    for case in cases:
        source = _case_source(case)
        master = build_master(source, settings, ingested_at=at)
        delistings = build_delistings(source.delistings(), master, ingested_at=at)
        results[case["case"]] = (master, delistings)
        counts[case["case"]] = _filename_counts(master, delistings, source)
    survivor = results["survivor_three_10ks"][0]
    assert [r["valid_from"] for r in survivor.listings if r["provenance"] == "filing_name"] == [
        date(2015, 3, 2),
        date(2016, 3, 1),
        date(2017, 3, 1),
    ]
    nasdaq, ended = results["nasdaq_delister"]
    assert nasdaq.filename_counts["filename_confirmed"] == 1
    assert nasdaq.filename_counts.get("filename_form25_rows", 0) == 0
    assert len(ended.delistings) == 1
    fallback, ended = results["nasdaq_fallback"]
    assert fallback.filename_counts["filename_form25_rows"] == 1
    assert len(ended.delistings) == 1
    assert results["nyse_single_reading"][0].filename_counts["filename_single_reading"] == 1
    assert len(results["transfer"][1].delistings) == 1
    assert len(results["unmatched_exchange"][1].unmatched) == 1
    assert "filename_transfers_delisted: 1" in counts["transfer"]
    assert "filename_form25_unmatched: 1" in counts["unmatched_exchange"]


def test_survivor_is_unlisted_before_first_acceptance_then_a_member() -> None:
    survivor = next(
        case for case in json.loads(CASES.read_text()) if case["case"] == "survivor_three_10ks"
    )
    base = _case_source(survivor)
    cik = survivor["cik"]
    early = datetime(2014, 6, 2, 20, 0, tzinfo=UTC)
    source = FixtureFilingSource(
        index=[FilingIndexEntry(cik, "Survivor", "S-1", "early", early), *base._index],
        cover_pages=base._cover_pages,
        filename_listings=base._filename_listings,
    )
    settings = Settings(
        _env_file=None,
        edgar={"filename_listings": True},
        universe={"min_history_months": 1, "liquidity_rule_enabled": False},
    )
    built = build_master(source, settings, ingested_at=datetime(2020, 1, 1, tzinfo=UTC))
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    write_master(conn, built)
    insert_row(
        conn,
        "classifications",
        {
            "security_id": cik,
            "sic": 3571,
            "security_type": "common",
            "rule": "common_default",
            "known_at": early,
            "ingested_at": early,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    try:
        before = universe_as_of(conn, session_close(date(2015, 2, 27)), settings)
        reason = before.exclusions.filter(before.exclusions["security_id"] == cik)["reason"].item()
        assert reason == "not_listed"
        for day in all_sessions():
            if date(2017, 1, 3) <= day <= date(2017, 3, 3):
                stamp = session_close(day)
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": cik,
                        "session": day,
                        "open": 10.0,
                        "high": 10.0,
                        "low": 10.0,
                        "close": 10.0,
                        "volume": 1_000_000,
                        "known_at": stamp,
                        "ingested_at": stamp,
                        "source": "alpaca",
                        "provenance": "bar",
                    },
                )
        fact_at = datetime(2017, 2, 3, 20, 0, tzinfo=UTC)
        insert_row(
            conn,
            "facts",
            {
                "security_id": cik,
                "fact_name": "shares_outstanding",
                "as_of_date": date(2017, 2, 1),
                "class_member": "",
                "value": 1_000_000,
                "filing_accession": "shares",
                "known_at": fact_at,
                "ingested_at": fact_at,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        after = universe_as_of(conn, session_close(date(2017, 3, 3)), settings)
        assert cik in after.members["security_id"].to_list()
        truncated = TruncatedStore(conn)
        try:
            for reading in survivor["readings"]:
                stamp = datetime.fromisoformat(reading["accepted_at"])
                for t in (stamp - PROBE_EPSILON, stamp + PROBE_EPSILON):
                    full = universe_as_of(conn, t, settings)
                    cut = universe_as_of(truncated.at(t), t, settings)
                    assert full.members.equals(cut.members)
                    assert full.exclusions.equals(cut.exclusions)
        finally:
            truncated.close()
    finally:
        conn.close()


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


def test_nasdaq_delister_span_ends_before_successor_uses_ticker() -> None:
    case = next(case for case in json.loads(CASES.read_text()) if case["case"] == "nasdaq_delister")
    source = _case_source(case)
    settings = Settings(_env_file=None, edgar={"filename_listings": True})
    at = datetime(2020, 1, 1, tzinfo=UTC)
    old = build_master(source, settings, ingested_at=at)
    delistings = build_delistings(source.delistings(), old, ingested_at=at)
    successor_cik = "0000099997"
    next_at = datetime(2019, 3, 1, 20, 30, tzinfo=UTC)
    successor = build_master(
        FixtureFilingSource(
            index=[FilingIndexEntry(successor_cik, "Successor", "10-K", "next", next_at)],
            cover_pages=[
                CoverPage(
                    successor_cik,
                    "next",
                    next_at,
                    (CoverListing("Common Stock", "NDEL", "NASDAQ"),),
                )
            ],
        ),
        settings,
        ingested_at=at,
    )
    listings = [*old.listings, *successor.listings]
    bar_days = [date(2018, 6, 8), date(2019, 3, 4)]
    ends = derive_listing_ends(
        pl.DataFrame(listings),
        pl.DataFrame(list(delistings.delistings)),
        pl.DataFrame({"security_id": [case["cik"], successor_cik], "session": bar_days}),
        transfer_window_sessions=settings.master.transfer_window_sessions,
    )
    old_end = ends.filter(pl.col("security_id") == case["cik"])["end_session"].item()
    assert old_end <= date(2018, 6, 11)
    evidence = registrant_evidence(
        [],
        ends.to_dicts(),
        as_of=date(2019, 3, 5),
        quiet_after_days=180,
        transfer_window_sessions=settings.master.transfer_window_sessions,
    )
    resolver = ListingResolver(listings, evidence)
    assert resolver.resolve("NDEL", date(2018, 6, 8)) == case["cik"]
    assert resolver.resolve("NDEL", date(2019, 3, 4)) == successor_cik
