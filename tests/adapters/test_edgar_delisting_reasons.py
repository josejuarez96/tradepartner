"""Reason evidence for Form 25 delistings (plan T327, #2670): the Form 25 provision
and text and the issuer's 8-K Items 1.03/2.01/3.01 into `delisting_reasons`
revisions at the evidence's acceptance.

Synthetic evidence only; nothing here reads the real store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.edgar import (
    Form25Evidence,
    eightk_item_codes,
    normalise_rule_provision,
    parse_form25_evidence,
)
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.delisting_reasons import (
    EightK,
    Episode,
    ReasonBuild,
    build_delisting_reasons,
    build_delisting_reasons_from_store,
    classify_notice_text,
    delisting_reasons_as_of,
    write_delisting_reasons,
)

CIK = "0000123456"
FILED = datetime(2024, 3, 5, 20, 0, tzinfo=UTC)  # 15:00 EST, before the close
INGESTED = datetime(2024, 6, 1, tzinfo=UTC)
EPISODE = Episode("S1", CIK, "25", "Common Stock", "NASDAQ", FILED)


def _evidence(
    provision: str | None, text: str | None = None, *, accepted: datetime = FILED
) -> Form25Evidence:
    return Form25Evidence(
        cik=CIK,
        form="25",
        class_title="Common Stock",
        exchange="NASDAQ",
        accession="0000123456-24-000025",
        accepted_at=accepted,
        provision=provision,
        notice_text=text,
    )


def _eightk(items: str, accepted: datetime, accession: str = "0000123456-24-000008") -> EightK:
    return EightK(CIK, "8-K", accession, items, accepted)


def _build(
    evidence: list[Form25Evidence], eightks: list[EightK], episodes: list[Episode] | None = None
) -> ReasonBuild:
    return build_delisting_reasons(
        episodes if episodes is not None else [EPISODE], evidence, eightks, ingested_at=INGESTED
    )


def _latest(build: ReasonBuild, t: datetime) -> tuple[str, str] | None:
    """The reason and source an as-of read at `t` returns for the one episode."""
    known = [r for r in build.rows if r["known_at"] <= t]  # type: ignore[operator]
    if not known:
        return None
    row = max(known, key=lambda r: r["known_at"])  # type: ignore[type-var,return-value]
    return str(row["reason"]), str(row["reason_source"])


# --- parsers ------------------------------------------------------------------


def test_parse_form25_evidence_reads_the_provision() -> None:
    xml = (
        f"<notificationOfRemoval><issuer><cik>{CIK}</cik></issuer>"
        "<descriptionClassSecurity>Common Stock</descriptionClassSecurity>"
        "<exchange><entityName>Nasdaq Stock Market LLC</entityName></exchange>"
        "<ruleProvision>17 CFR 240.12d2-2(b)</ruleProvision></notificationOfRemoval>"
    )
    evidence = parse_form25_evidence(
        xml, form="25", accession="0000123456-24-000025", accepted_at=FILED
    )
    assert evidence.provision == "12d2-2(b)"
    assert (evidence.cik, evidence.exchange, evidence.accepted_at) == (CIK, "NASDAQ", FILED)


def test_parse_form25_evidence_blank_provision_is_none() -> None:
    xml = (
        f"<notificationOfRemoval><issuer><cik>{CIK}</cik></issuer>"
        "<descriptionClassSecurity>Common Stock</descriptionClassSecurity>"
        "<exchange><entityName>New York Stock Exchange</entityName></exchange>"
        "</notificationOfRemoval>"
    )
    evidence = parse_form25_evidence(xml, form="25", accession="a-24-1", accepted_at=FILED)
    assert evidence.provision is None


def test_normalise_provision_and_item_codes() -> None:
    assert normalise_rule_provision("Rule 12d2-2 (a)(3)") == "12d2-2(a)(3)"
    assert normalise_rule_provision("  ") is None
    assert eightk_item_codes("1.03, 9.01") == {"1.03", "9.01"}
    assert "2.01" not in eightk_item_codes("12.01")  # a whole code, never a suffix


def test_notice_text_names_every_class_its_cue_words_allow() -> None:
    assert classify_notice_text("Pursuant to the Agreement and Plan of Merger") == {
        "merger_acquisition"
    }
    assert classify_notice_text("filed Chapter 11; failed to satisfy continued listing") == {
        "bankruptcy",
        "performance_failure",
    }
    assert classify_notice_text(None) == frozenset()


# --- precedence, ambiguity, conflict, transfer, unmatched ---------------------


def test_provision_b_alone_is_ambiguous_and_stays_unknown() -> None:
    build = _build([_evidence("12d2-2(b)")], [])
    assert build.rows == ()
    assert build.n_unknown == 1


def test_precedence_8k_item_over_text_over_provision() -> None:
    eightk_at = FILED - timedelta(days=20)
    # provision (b) allows failure or bankruptcy; 1.03 settles bankruptcy
    build = _build([_evidence("12d2-2(b)")], [_eightk("1.03", eightk_at)])
    assert [(r["reason"], r["reason_source"]) for r in build.rows] == [
        ("bankruptcy", "eightk_item"),
    ]
    # text naming a continued-listing failure settles (b) as a failure
    text_build = _build([_evidence("12d2-2(b)", "failure to comply with continued listing")], [])
    assert [(r["reason"], r["reason_source"]) for r in text_build.rows] == [
        ("performance_failure", "form25_text")
    ]
    # the item wins the source when everything agrees
    both = _build([_evidence("12d2-2(b)", "filed Chapter 11")], [_eightk("1.03", eightk_at)])
    assert both.rows[-1]["reason_source"] == "eightk_item"
    assert both.rows[-1]["reason"] == "bankruptcy"


def test_revisions_are_written_at_each_evidence_acceptance() -> None:
    eightk_at = FILED - timedelta(days=20)
    build = _build([_evidence("12d2-2(b)")], [_eightk("1.03", eightk_at)])
    row = build.rows[0]
    assert row["known_at"] == row["reason_known_at"] == eightk_at
    assert row["reason_evidence_form"] == "8-K"
    assert row["reason_evidence_accession"] == "0000123456-24-000008"


def test_conflict_is_reported_and_reverts_the_reason_to_unknown() -> None:
    eightk_at = FILED - timedelta(days=20)
    # 1.03 says bankruptcy; a voluntary-withdrawal provision then contradicts it
    build = _build([_evidence("12d2-2(c)")], [_eightk("1.03", eightk_at)])
    assert [r["reason"] for r in build.rows] == ["bankruptcy", "unknown"]
    assert build.rows[1]["reason_source"] == "unknown"
    assert build.rows[1]["reason_evidence_accession"] is None
    assert len(build.conflicts) == 1
    assert build.conflicts[0].known_at == FILED
    assert _latest(build, eightk_at + timedelta(days=1)) == ("bankruptcy", "eightk_item")
    assert _latest(build, FILED) == ("unknown", "unknown")


def test_transfer_or_withdrawal_provision_is_voluntary_other() -> None:
    build = _build([_evidence("12d2-2(c)")], [])
    assert [(r["reason"], r["reason_source"]) for r in build.rows] == [
        ("voluntary_other", "form25_provision")
    ]
    assert build.rows[0]["delisting_form"] == "25"  # the episode key is untouched


def test_unmatched_items_are_counted_and_never_used() -> None:
    far_before = FILED - timedelta(days=500)
    later = FILED + timedelta(days=200)
    build = _build(
        [_evidence("12d2-2(a)(4)")],  # voluntary or bankruptcy
        [_eightk("1.03", far_before, "a-1"), _eightk("3.01", later, "a-2")],
    )
    assert build.rows == ()  # (a)(4) is ambiguous and the 1.03 is out of window
    assert build.coverage.unmatched["1.03"] == 1
    assert build.coverage.unmatched["3.01"] == 1
    assert build.coverage.matched["1.03"] == 0


def test_bare_3_01_notice_establishes_nothing() -> None:
    build = _build([], [_eightk("3.01", FILED - timedelta(days=5))])
    assert build.rows == ()
    assert build.coverage.matched["3.01"] == 1


def test_2_01_alone_does_not_match_but_with_a_merger_provision_it_does() -> None:
    at = FILED + timedelta(days=3)
    alone = _build([], [_eightk("2.01", at)])
    assert alone.rows == ()
    assert alone.coverage.unmatched["2.01"] == 1

    corroborated = _build([_evidence("12d2-2(a)(3)")], [_eightk("2.01", at)])
    assert [(r["reason"], r["reason_source"]) for r in corroborated.rows] == [
        ("merger_acquisition", "eightk_item")
    ]
    assert corroborated.rows[0]["known_at"] == at
    assert corroborated.coverage.matched["2.01"] == 1


def test_an_8k_of_an_issuer_with_no_episode_is_not_counted() -> None:
    other = EightK("0000999999", "8-K", "x-24-1", "1.03", FILED)
    build = _build([_evidence("12d2-2(c)")], [other])
    assert build.coverage.matched["1.03"] == 0
    assert build.coverage.unmatched["1.03"] == 0


def test_evidence_after_ingested_at_is_refused() -> None:
    with pytest.raises(ValueError, match="is after ingested_at"):
        build_delisting_reasons(
            [EPISODE],
            [_evidence("12d2-2(c)")],
            [],
            ingested_at=FILED - timedelta(seconds=1),
        )


# --- as-of visibility ----------------------------------------------------------


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    insert_row(
        conn,
        "delistings",
        {
            "security_id": "S1",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": FILED,
            "effective_on": (FILED + timedelta(days=10)).date(),
            "known_at": FILED,
            "ingested_at": FILED,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    return conn


def test_after_close_acceptance_is_invisible_at_that_close() -> None:
    close = datetime(2024, 3, 6, 21, 0, tzinfo=UTC)  # 16:00 EST
    after_close = close + timedelta(hours=1)  # an 8-K accepted 17:00 EST
    next_close = close + timedelta(days=1)
    conn = _store()
    build = _build([_evidence("12d2-2(b)")], [_eightk("1.03", after_close)])
    write_delisting_reasons(conn, build)
    assert delisting_reasons_as_of(conn, close).is_empty()
    seen = delisting_reasons_as_of(conn, next_close)
    assert seen["reason"].to_list() == ["bankruptcy"]
    assert seen["known_at"].to_list() == [after_close]


def test_reasons_do_not_precede_their_episode() -> None:
    """An 8-K accepted months before the Form 25 is stamped at its own acceptance
    but the read keeps it hidden until the episode itself is known."""
    conn = _store()
    early = FILED - timedelta(days=30)
    write_delisting_reasons(conn, _build([_evidence("12d2-2(b)")], [_eightk("1.03", early)]))
    assert delisting_reasons_as_of(conn, FILED - timedelta(days=1)).is_empty()
    assert delisting_reasons_as_of(conn, FILED)["reason"].to_list() == ["bankruptcy"]


def test_no_look_ahead_truncation_invariance() -> None:
    """The reason known at every instant T equals the reason built from only the
    evidence accepted by T."""
    t1 = FILED - timedelta(days=40)
    t2 = FILED + timedelta(days=2)
    evidence = [_evidence("12d2-2(a)(3)")]
    eightks = [_eightk("1.03", t1, "a-1"), _eightk("2.01", t2, "a-2")]
    full = _build(evidence, eightks)
    probes = [
        t1 - timedelta(seconds=1),
        t1,
        FILED - timedelta(microseconds=1),
        FILED,
        t2 - timedelta(microseconds=1),
        t2,
        t2 + timedelta(days=30),
    ]
    for t in probes:
        cut_evidence = [e for e in evidence if e.accepted_at <= t]
        cut_eightks = [e for e in eightks if e.accepted_at <= t]
        truncated = _build(cut_evidence, cut_eightks)
        assert _latest(full, t) == _latest(truncated, t), t


# --- the store edge -------------------------------------------------------------


def _row(**values: Any) -> dict[str, Any]:
    return {"ingested_at": INGESTED, "source": "edgar", "provenance": "filing", **values}


def test_build_from_store_reads_episodes_and_filing_events() -> None:
    conn = _store()
    old = datetime(2020, 1, 1, tzinfo=UTC)
    insert_row(
        conn,
        "securities",
        _row(security_id="S1", cik=CIK, name="Acme", known_at=old),
    )
    accepted = FILED - timedelta(days=10)
    insert_row(
        conn,
        "filing_events",
        _row(
            cik=CIK,
            accession="0000123456-24-000008",
            form="8-K",
            items="1.03,9.01",
            accepted_at=accepted,
            known_at=accepted,
        ),
    )
    build = build_delisting_reasons_from_store(conn, [_evidence("12d2-2(b)")], ingested_at=INGESTED)
    assert [(r["security_id"], r["reason"]) for r in build.rows] == [("S1", "bankruptcy")]
    assert build.coverage.matched["1.03"] == 1


def test_source_fetches_once_and_serves_the_provision_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from edgar_transport import edgar_settings

    from tradepartner.adapters import edgar_raw
    from tradepartner.adapters.edgar_source import EdgarFilingSource

    xml = (
        f"<notificationOfRemoval><issuer><cik>{CIK}</cik></issuer>"
        "<descriptionClassSecurity>Common Stock</descriptionClassSecurity>"
        "<exchange><entityName>Nasdaq Stock Market LLC</entityName></exchange>"
        "<ruleProvision>17 CFR 240.12d2-2(a)(3)</ruleProvision></notificationOfRemoval>"
    )
    calls: list[str] = []

    def fake_download(cik: str, accession: str, document: str, **_: Any) -> Path:
        calls.append(document)
        path = tmp_path / "dl.xml"
        path.write_text(xml, encoding="utf-8")
        return path

    monkeypatch.setattr(edgar_raw, "download_filing_file", fake_download)
    source = EdgarFilingSource(edgar_settings(tmp_path), clock=lambda: INGESTED)
    args = (CIK, "0000123456-24-000025", "25", "xslF25/primary_doc.xml", FILED)
    first = source.form25_evidence(*args)
    second = source.form25_evidence(*args)
    assert first is not None and second is not None
    assert first.provision == second.provision == "12d2-2(a)(3)"
    assert calls == ["primary_doc.xml"]  # the root copy, once
    assert second.accepted_at == FILED
