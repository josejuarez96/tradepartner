"""Bulk Form 4 rows enter research only at the issuer's acceptance time."""

from __future__ import annotations

import hashlib
import zipfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import duckdb
import httpx
import pytest

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


def test_a_later_multi_owner_association_is_invisible_before_its_acceptance(
    tmp_path: Path,
) -> None:
    """T350 (#2711): a multi-owner accession routed through XML adds its owner rows
    only from its acceptance time, and a store truncated before it matches."""
    directory = Path(__file__).parents[1] / "fixtures/edgar/sec_forms/insider_bulk_2026q1"
    archive = tmp_path / "quarter.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for name in ("SUBMISSION", "REPORTINGOWNER", "NONDERIV_TRANS"):
            handle.write(directory / f"{name}.tsv", f"{name}.tsv")
    bulk = read_insider_bulk(archive)
    multi = "0001213900-26-037435"
    facts = bulk.accessions[multi]
    assert len(set(facts.filer_ciks)) == 2
    close = datetime(2026, 3, 31, 20, tzinfo=UTC)
    accepted = datetime(2026, 3, 31, 21, tzinfo=UTC)
    full = duckdb.connect(":memory:")
    truncated = duckdb.connect(":memory:")
    for conn in (full, truncated):
        schema.init_schema(conn)
    run_form4_bulk_route(
        [(multi, "0002025396", "4", date(2026, 3, 31))],
        quarters={Quarter(2026, 1): bulk},
        stamp=lambda *_: accepted,
        xml=lambda *_: SecFormFiling(
            multi,
            "4",
            accepted,
            date(2026, 3, 31),
            (facts.issuer_cik,),
            facts.filer_ciks,
            facts.transactions,
        ),
        write=lambda filing: write_sec_form_accession(
            full, filing, ingested_at=accepted, source="sec_form4_xml"
        ),
        write_bulk=lambda *_: pytest.fail("multi-owner accession written from bulk"),
        now=accepted,
        price_below=1,
        zero_price_codes=("P", "S"),
        order_sample_size=0,
        budget=10,
    )
    settings = Settings(_env_file=None, events=EventsConfig(sec_forms_read_enabled=True))
    assert asof.sec_form4_transactions_as_of(full, close, settings=settings).is_empty()
    assert asof.sec_form4_transactions_as_of(full, close, settings=settings).equals(
        asof.sec_form4_transactions_as_of(truncated, close, settings=settings)
    )
    later = asof.sec_form4_transactions_as_of(full, accepted, settings=settings)
    assert set(later["filer_cik"]) == set(facts.filer_ciks)


def test_a_later_xml_fetch_is_invisible_in_a_store_truncated_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T348 (#2714): with the raw archive on, a later run's newly fetched accession
    adds nothing to an as-of read before its acceptance, matches a store truncated
    before that run, and never becomes an entry of the earlier run's manifest."""
    from pydantic import SecretStr

    from tradepartner import diskfloor
    from tradepartner import ingest as ingest_module
    from tradepartner.adapters import edgar_raw
    from tradepartner.adapters.edgar import UnstampedFiling, parse_form4
    from tradepartner.adapters.edgar_insider_bulk import InsiderBulk
    from tradepartner.adapters.edgar_source import EdgarFilingSource, SubmissionRecord
    from tradepartner.adapters.form4_archive import message_digest, parse_manifest
    from tradepartner.adapters.form4_bulk_route import insider_zip_path
    from tradepartner.ingest import SourceRun

    xml = Path(__file__).parents[1] / "fixtures/edgar/sec_forms/form4_purchase_gift_indirect.xml"
    root = tmp_path / "archive"
    root.mkdir()
    settings = Settings(
        _env_file=None,
        tradepartner_raw_archive=SecretStr(str(root)),
        edgar={"cache_dir": str(tmp_path / "edgar")},
        events=EventsConfig(sec_forms_enabled=True, sec_forms_read_enabled=True),
    )
    issuer = parse_form4(xml.read_text()).issuer_cik
    early, late = "0000320193-26-000123", "0000320193-26-000124"
    filed = {early: date(2026, 9, 24), late: date(2026, 9, 30)}
    accepted = {a: datetime(d.year, d.month, d.day, 21, tzinfo=UTC) for a, d in filed.items()}
    bodies = {early: xml.read_bytes(), late: xml.read_bytes() + b"<!-- later -->"}
    records = {
        a: SubmissionRecord(a, "4", "form4.xml", False, accepted[a], filing_date=filed[a])
        for a in filed
    }
    quarter = Quarter(2026, 3)
    zip_path = insider_zip_path(settings, quarter)
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"insider zip")
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _path: 10**15)
    monkeypatch.setattr(
        ingest_module,
        "load_bulk_quarters",
        lambda *_a, **_k: (
            {quarter: InsiderBulk({})},
            {quarter: hashlib.sha256(zip_path.read_bytes()).hexdigest()},
            Counter(),
            frozenset(),
        ),
    )

    def run(conn: duckdb.DuckDBPyConnection, now: datetime, run_id: str) -> str:
        source = EdgarFilingSource(settings, client=httpx.Client(), clock=lambda: now)
        listed = [a for a in filed if accepted[a] <= now]
        rows = [((2026, 3), UnstampedFiling(issuer, "Issuer", "4", a, filed[a])) for a in listed]
        monkeypatch.setattr(source, "_rows", lambda *_: iter(rows))
        monkeypatch.setattr(source, "_load_stamps", lambda *_: {})
        monkeypatch.setattr(
            source, "_fetch_submissions", lambda _cik, _w: SimpleNamespace(records=records)
        )
        monkeypatch.setattr(source, "_sec_stamped", lambda _cik, accession, _f: records[accession])
        monkeypatch.setattr(
            edgar_raw,
            "fetch_filing_xml",
            lambda _cik, accession, _name, **_k: edgar_raw.FetchedXml(
                f"https://www.sec.gov/{accession}.xml",
                bodies[accession],
                bodies[accession].decode(),
                now,
            ),
        )
        provenance = ingest_module._Form4Provenance.start(
            settings, run_id=run_id, writer="sec_forms"
        )
        added, message = ingest_module._write_form4_bulk(
            conn, settings, source, frozenset({issuer}), now, provenance=provenance
        )
        clause = ingest_module._provenance_clause(provenance)
        run_row = SourceRun("sec_forms", "ok", added, "x", f"1 subjects{clause}; {message}")
        ingest_module.write_run(conn, run_id, now, now, run_row, mode="backfill")
        return clause

    full = duckdb.connect(":memory:")
    truncated = duckdb.connect(":memory:")
    for conn in (full, truncated):
        schema.init_schema(conn)
    first_at = datetime(2026, 9, 29, tzinfo=UTC)
    first = run(full, first_at, "first")
    assert run(truncated, first_at, "first") == first
    run(full, datetime(2026, 10, 2, tzinfo=UTC), "second")
    at_first = asof.sec_form4_transactions_as_of(full, first_at, settings=settings)
    assert not at_first.is_empty() and set(at_first["accession"]) == {early}
    assert at_first.equals(
        asof.sec_form4_transactions_as_of(truncated, first_at, settings=settings)
    )
    later = asof.sec_form4_transactions_as_of(
        full, datetime(2026, 10, 2, tzinfo=UTC), settings=settings
    )
    assert late in set(later["accession"])
    manifest = parse_manifest((root / "manifests" / str(message_digest(first))).read_bytes())
    assert {entry["accession"] for entry in manifest["entries"]} == {early}
