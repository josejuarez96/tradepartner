"""The pure Form 4 provenance decisions (plan T348, #2755; data-foundation spec
amendment #2714 items 1 and 5): the pinned row digest, the manifest, the run
message order, the changed-zip recheck and the entry match."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradepartner.adapters import form4_archive as fa
from tradepartner.adapters.form4_archive import (
    ProvenanceError,
    RawInput,
    build_manifest,
    canonical_row,
    find_entry,
    manifest_entry,
    message_digest,
    parse_manifest,
    prior_xml_hashes,
    quarter_message,
    recheck_verdict,
    row_digest,
    written_rows,
    zip_changed,
)

ACCEPTED = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)
XML_SHA = "1" * 64
ZIP_SHA = "2" * 64

EVENT = {
    "accession": "0000320193-26-000123",
    "form": "4",
    "filed_date": date(2026, 9, 24),
    "subject_cik": "0000320193",
    "filer_cik": "0001214128",
    "accepted_at": ACCEPTED,
    "known_at": ACCEPTED,
    "ingested_at": ACCEPTED + timedelta(hours=1),
    "source": "sec_form4_xml",
    "provenance": "filing",
}
TRANSACTION = {
    "accession": "0000320193-26-000123",
    "filer_cik": "0001214128",
    "line_no": 0,
    "is_director": True,
    "price": Decimal("12.50"),
    "deemed_execution_date": None,
    "ingested_at": ACCEPTED,
}


def test_the_row_digest_fields_and_serialization_are_pinned() -> None:
    """The exact canonical text: every column but `ingested_at`, names sorted,
    compact JSON; UTC ISO datetimes, ISO dates, normalized plain decimals."""
    text = canonical_row(EVENT)
    assert text == (
        '{"accepted_at":"2026-09-24T21:00:00+00:00","accession":"0000320193-26-000123",'
        '"filed_date":"2026-09-24","filer_cik":"0001214128","form":"4",'
        '"known_at":"2026-09-24T21:00:00+00:00","provenance":"filing",'
        '"source":"sec_form4_xml","subject_cik":"0000320193"}'
    )
    assert row_digest(EVENT) == hashlib.sha256(text.encode("ascii")).hexdigest()
    assert canonical_row(TRANSACTION) == (
        '{"accession":"0000320193-26-000123","deemed_execution_date":null,'
        '"filer_cik":"0001214128","is_director":true,"line_no":0,"price":"12.5"}'
    )
    assert frozenset({"ingested_at"}) == fa.DIGEST_EXCLUDED


def test_the_row_digest_is_stable_across_store_round_trips() -> None:
    """A row read back from the store (another decimal scale, another zone, a later
    commit stamp) has the same digest; a changed fact does not."""
    eastern = timezone(timedelta(hours=-4))
    read_back = {
        **EVENT,
        "accepted_at": ACCEPTED.astimezone(eastern),
        "ingested_at": ACCEPTED + timedelta(days=9),
    }
    assert row_digest(read_back) == row_digest(EVENT)
    assert row_digest({**TRANSACTION, "price": Decimal("12.5000")}) == row_digest(TRANSACTION)
    assert row_digest({**TRANSACTION, "price": Decimal("12.51")}) != row_digest(TRANSACTION)
    assert row_digest({**EVENT, "known_at": ACCEPTED + timedelta(seconds=1)}) != row_digest(EVENT)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 9, 24, 21, tzinfo=UTC).replace(tzinfo=None), 1.5, b"bytes", Decimal("NaN")],
)
def test_the_row_digest_refuses_values_it_cannot_pin(value: object) -> None:
    with pytest.raises(ProvenanceError):
        row_digest({"accession": "a", "value": value})


def test_written_rows_carry_each_tables_unique_key_and_digest() -> None:
    rows = written_rows({"sec_form4_transactions": [TRANSACTION], "sec_filing_events": [EVENT]})
    assert rows == [
        {
            "table": "sec_filing_events",
            "key": ["0000320193-26-000123", "0000320193", "0001214128"],
            "digest": row_digest(EVENT),
        },
        {
            "table": "sec_form4_transactions",
            "key": ["0000320193-26-000123", "0001214128", 0],
            "digest": row_digest(TRANSACTION),
        },
    ]
    with pytest.raises(ProvenanceError):
        written_rows({"other_table": [EVENT]})


def _xml_entry(accession: str = EVENT["accession"], outcome: str = "added") -> dict[str, object]:
    raw = RawInput(XML_SHA, "https://www.sec.gov/x.xml", ACCEPTED, "sec_forms_parser=1")
    rows = {"sec_filing_events": [EVENT]} if outcome == "added" else {}
    return manifest_entry(
        accession=str(accession),
        row_source="sec_form4_xml",
        quarter="2026q3",
        raw=raw,
        outcome=outcome,
        rows=rows,
    )


def test_the_manifest_is_canonical_versioned_and_round_trips() -> None:
    zips = [{"quarter": "2026q3", "sha256": ZIP_SHA, "url": None, "fetched_at": None}]
    first = build_manifest(
        run_id="run-1", source="edgar", mode="backfill", entries=[_xml_entry()], zips=zips
    )
    again = build_manifest(
        run_id="run-1", source="edgar", mode="backfill", entries=[_xml_entry()], zips=zips
    )
    assert first == again
    parsed = parse_manifest(first)
    assert parsed["format"] == fa.MANIFEST_FORMAT and parsed["version"] == fa.MANIFEST_VERSION
    assert parsed["writer"] == {"run_id": "run-1", "source": "edgar", "mode": "backfill"}
    entry = parsed["entries"][0]
    assert entry["raw_sha256"] == XML_SHA and entry["fetched_at"] == "2026-09-24T21:00:00+00:00"
    assert entry["parser_version"] == "sec_forms_parser=1" and entry["outcome"] == "added"
    assert entry["rows"][0]["digest"] == row_digest(EVENT)
    with pytest.raises(ProvenanceError):
        parse_manifest(json.dumps({**parsed, "version": 99}).encode())
    with pytest.raises(ProvenanceError):
        build_manifest(run_id="r", source="alpaca", mode="backfill", entries=[], zips=[])


def test_only_an_added_entry_lists_written_rows() -> None:
    """Parsed input and rows actually written stay apart: an unchanged or refused
    re-read names its raw hash but no written row."""
    for outcome in ("unchanged", "changed_refused", "recheck_same", "not_written"):
        assert _xml_entry(outcome=outcome)["rows"] == []
    with pytest.raises(ProvenanceError):
        manifest_entry(
            accession="a",
            row_source="sec_form4_xml",
            quarter="2026q3",
            raw=RawInput(XML_SHA, None, None, "p"),
            outcome="unchanged",
            rows={"sec_filing_events": [EVENT]},
        )
    with pytest.raises(ProvenanceError):
        manifest_entry(
            accession="a",
            row_source="sec_form4_xml",
            quarter="2026q3",
            raw=RawInput(XML_SHA, None, None, "p"),
            outcome="guessed",
            rows={},
        )


def test_the_quarter_message_keeps_both_digests_inside_a_200_character_cap() -> None:
    message = quarter_message(
        checked=ZIP_SHA, provenance=XML_SHA, zip_sha256="3" * 64, rest="quarter=2026q3; x=1"
    )
    assert message.startswith(f"checked_sha256={ZIP_SHA}; provenance_sha256={XML_SHA}; ")
    assert message.index("zip_sha256=") > message.index("provenance_sha256=")
    cut = message[:200]
    assert message_digest(cut) == XML_SHA
    assert len(f"checked_sha256={ZIP_SHA}; provenance_sha256={XML_SHA}") == 163
    # With the archive off there is no provenance key; the order is otherwise the same.
    off = quarter_message(checked="none", provenance=None, zip_sha256=ZIP_SHA, rest="quarter=q")
    assert off == f"checked_sha256=none; zip_sha256={ZIP_SHA}; quarter=q"
    assert message_digest(off) is None


def test_message_digest_reads_only_a_whole_hash() -> None:
    assert message_digest(f"12 subjects; provenance_sha256={XML_SHA}; rest") == XML_SHA
    assert message_digest(f"provenance_sha256={XML_SHA[:40]}") is None
    assert message_digest(None) is None


@pytest.mark.parametrize(
    "checked, current, changed",
    [
        (None, ZIP_SHA, False),
        ("none", ZIP_SHA, False),
        (ZIP_SHA, ZIP_SHA, False),
        (ZIP_SHA, None, False),
        ("9" * 64, ZIP_SHA, True),
    ],
)
def test_only_a_recorded_then_differing_zip_hash_is_a_change(
    checked: str | None, current: str | None, changed: bool
) -> None:
    assert zip_changed(checked, current) is changed


def test_the_recheck_verdict_compares_the_held_xml_hash() -> None:
    assert recheck_verdict(XML_SHA, XML_SHA) == "same"
    assert recheck_verdict(XML_SHA, "4" * 64) == "changed"
    assert recheck_verdict(None, XML_SHA) == "unknown"


def test_prior_xml_hashes_take_the_entry_that_wrote_the_stored_row() -> None:
    """The stored row's writer run names the held hash: a later run's refused or
    unchanged re-read never replaces it."""
    writer = parse_manifest(
        build_manifest(
            run_id="first", source="edgar", mode="backfill", entries=[_xml_entry()], zips=[]
        )
    )
    later_entry = {**_xml_entry(outcome="changed_refused"), "raw_sha256": "5" * 64}
    later = parse_manifest(
        build_manifest(
            run_id="later", source="sec_forms", mode="backfill", entries=[later_entry], zips=[]
        )
    )
    accession = str(EVENT["accession"])
    assert prior_xml_hashes([later, writer], {accession}) == {accession: XML_SHA}
    assert prior_xml_hashes([later], {accession}) == {}


def _manifest(
    run_id: str, source: str, mode: str, *entries: dict[str, object]
) -> dict[str, object]:
    return parse_manifest(
        build_manifest(run_id=run_id, source=source, mode=mode, entries=list(entries), zips=[])
    )


def test_an_entry_is_matched_by_writer_source_mode_accession_key_and_digest() -> None:
    accession = str(EVENT["accession"])
    key = ["0000320193-26-000123", "0000320193", "0001214128"]
    criteria = {
        "row_source": "sec_form4_xml",
        "mode": "backfill",
        "accession": accession,
        "table": "sec_filing_events",
        "key": key,
        "digest": row_digest(EVENT),
    }
    manifests = [
        _manifest("first", "edgar", "backfill", _xml_entry()),
        _manifest("later", "sec_forms", "backfill", _xml_entry(outcome="unchanged")),
    ]
    run_id, entry = find_entry(manifests, **criteria)
    assert run_id == "first" and entry["raw_sha256"] == XML_SHA
    with pytest.raises(ProvenanceError, match="no manifest entry"):
        find_entry(manifests, **{**criteria, "mode": "daily"})
    with pytest.raises(ProvenanceError, match="no manifest entry"):
        find_entry(manifests, **{**criteria, "row_source": "sec_insider_bulk"})
    with pytest.raises(ProvenanceError, match="no manifest entry"):
        find_entry(manifests, **{**criteria, "digest": "0" * 64})
    with pytest.raises(ProvenanceError, match="more than one"):
        find_entry(
            [*manifests, _manifest("copy", "sec_forms", "backfill", _xml_entry())], **criteria
        )


def test_rechecked_against_names_only_inputs_read_under_that_zip_hash() -> None:
    zips = [{"quarter": "2026q3", "sha256": ZIP_SHA, "url": None, "fetched_at": None}]
    done = _xml_entry(outcome="recheck_same")
    failed = {**_xml_entry(accession="b", outcome="not_written")}
    manifest = parse_manifest(
        build_manifest(
            run_id="r", source="edgar", mode="backfill", entries=[done, failed], zips=zips
        )
    )
    assert fa.rechecked_against([manifest], "2026q3", ZIP_SHA) == {EVENT["accession"]}
    assert fa.rechecked_against([manifest], "2026q3", "9" * 64) == set()
    assert fa.rechecked_against([manifest], "2026q2", ZIP_SHA) == set()
