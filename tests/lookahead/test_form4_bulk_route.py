"""Bulk Form 4 rows enter research only at the issuer's acceptance time."""

from __future__ import annotations

import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from tradepartner.adapters.edgar_insider_bulk import read_insider_bulk
from tradepartner.adapters.filings import SecFormFiling
from tradepartner.adapters.form4_bulk_route import run_form4_bulk_route
from tradepartner.adapters.form4_route import Quarter
from tradepartner.config import EventsConfig, Settings
from tradepartner.ingest import write_sec_form_accession
from tradepartner.store import asof, schema


def test_bulk_row_after_close_matches_store_truncated_at_close(tmp_path: Path) -> None:
    directory = Path(__file__).parents[1] / "fixtures/edgar/sec_forms/insider_bulk_2026q1"
    archive = tmp_path / "quarter.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name in ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS"):
            handle.write(directory / f"{name}.tsv", f"{name}.tsv")
    bulk = read_insider_bulk(archive)
    full = duckdb.connect(":memory:")
    truncated = duckdb.connect(":memory:")
    for conn in (full, truncated):
        schema.init_schema(conn)
    close = datetime(2026, 3, 31, 20, tzinfo=UTC)
    accepted = datetime(2026, 3, 31, 21, tzinfo=UTC)
    earlier = datetime(2026, 3, 31, 18, tzinfo=UTC)
    early_accession = "0001577526-26-000036"
    early_facts = bulk.accessions[early_accession]
    early_filing = SecFormFiling(
        early_accession,
        "4",
        earlier,
        date(2026, 3, 31),
        (early_facts.issuer_cik,),
        early_facts.filer_ciks,
        early_facts.transactions,
    )
    for conn in (full, truncated):
        write_sec_form_accession(conn, early_filing, ingested_at=earlier, source="sec_insider_bulk")
    accession = "0001493152-26-014171"
    run_form4_bulk_route(
        [(accession, "0000873860", "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: None,
        write=lambda filing: write_sec_form_accession(
            full, filing, ingested_at=accepted, source="sec_form4_xml"
        ),
        write_bulk=lambda filing, _quarter: write_sec_form_accession(
            full, filing, ingested_at=accepted, source="sec_insider_bulk"
        ),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    settings = Settings(_env_file=None, events=EventsConfig(sec_forms_read_enabled=True))
    at_close = asof.sec_form4_transactions_as_of(full, close, settings=settings)
    assert not at_close.is_empty()
    assert at_close.equals(asof.sec_form4_transactions_as_of(truncated, close, settings=settings))
    assert len(asof.sec_form4_transactions_as_of(full, accepted, settings=settings)) > len(at_close)
