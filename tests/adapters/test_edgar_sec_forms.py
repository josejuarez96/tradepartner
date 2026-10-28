"""SEC ownership event forms, pure parsers (plan T305, #2435): the SGML header's
`SUBJECT COMPANY` / `FILED BY` blocks of an unstructured SC 13D, the XML of a
`SCHEDULE 13D` and the non-derivative rows of a Form 4 or 4/A. No store, no network:
the payloads are `tests/fixtures/edgar/sec_forms/`, written to the SEC's published
element names (the recorded real payloads arrive with the fetch layer, T307)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tradepartner.adapters import edgar
from tradepartner.adapters.edgar import (
    SEC_FORMS_PARSER_VERSION,
    parse_form4,
    parse_schedule_13d_xml,
    parse_sgml_13d_parties,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "sec_forms"
ISSUER = "0000777777"
JANE = "0000888888"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- 13D parties -----------------------------------------------------------------


def test_unstructured_13d_header_names_target_and_each_filer() -> None:
    parties = parse_sgml_13d_parties(
        _text("sgml_header_sc13d_unstructured.txt"), "0000950123-09-012345"
    )
    assert parties.subject_ciks == ("0000111111",)
    assert parties.filer_ciks == ("0000222222", "0000333333")


def test_header_text_after_the_header_is_never_read() -> None:
    parties = parse_sgml_13d_parties(
        _text("sgml_header_sc13d_unstructured.txt"), "0000950123-09-012345"
    )
    assert "0000999999" not in parties.subject_ciks + parties.filer_ciks


def test_header_naming_another_accession_is_refused() -> None:
    with pytest.raises(ValueError, match="names"):
        parse_sgml_13d_parties(_text("sgml_header_sc13d_unstructured.txt"), "0000950123-09-999999")


def test_truncated_header_is_refused() -> None:
    text = _text("sgml_header_sc13d_unstructured.txt").partition("</SEC-HEADER>")[0]
    with pytest.raises(ValueError, match="truncated"):
        parse_sgml_13d_parties(text, "0000950123-09-012345")


@pytest.mark.parametrize("block", ["SUBJECT COMPANY", "FILED BY"])
def test_header_without_a_party_block_is_identity_missing(block: str) -> None:
    text = _text("sgml_header_sc13d_unstructured.txt").replace(f"{block}:", "OTHER BLOCK:")
    with pytest.raises(ValueError, match="no CENTRAL INDEX KEY"):
        parse_sgml_13d_parties(text, "0000950123-09-012345")


def test_structured_schedule_13d_names_issuer_and_unique_reporting_persons() -> None:
    parties = parse_schedule_13d_xml(_text("schedule13d_structured.xml"))
    assert parties.subject_ciks == ("0000555555",)
    # The submitting filer is not a reporting person unless listed; a duplicate is one.
    assert parties.filer_ciks == ("0000444444", "0000666666")


def test_structured_schedule_13d_without_issuer_cik_is_identity_missing() -> None:
    xml = _text("schedule13d_structured.xml").replace("issuerCIK", "issuerOther")
    with pytest.raises(ValueError, match="issuerCIK"):
        parse_schedule_13d_xml(xml)


def test_structured_schedule_13d_without_reporting_person_is_identity_missing() -> None:
    xml = _text("schedule13d_structured.xml").replace("reportingPersonCIK", "reportingPersonX")
    with pytest.raises(ValueError, match="reportingPersonCIK"):
        parse_schedule_13d_xml(xml)


def test_malformed_xml_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        parse_schedule_13d_xml("<edgarSubmission><oops>")
    with pytest.raises(ValueError):
        parse_form4("not xml")


# --- Form 4 ------------------------------------------------------------------------


def test_purchase_gift_and_indirect_rows_carry_every_field() -> None:
    parse = parse_form4(_text("form4_purchase_gift_indirect.xml"))
    assert parse.issuer_cik == ISSUER
    assert parse.skipped == 0
    purchase, gift, indirect = parse.transactions
    assert purchase.issuer_cik == ISSUER
    assert purchase.filer_cik == JANE
    assert (purchase.is_director, purchase.is_officer) == (True, True)
    assert (purchase.is_ten_percent_owner, purchase.is_other) == (False, False)
    assert purchase.line_no == 0
    assert purchase.transaction_date == date(2025, 3, 3)
    assert purchase.deemed_execution_date is None
    assert purchase.transaction_code == "P"
    assert purchase.acquired_disposed == "A"
    assert purchase.shares == Decimal("1000")
    assert purchase.price == Decimal("12.34")
    assert purchase.shares_owned_after == Decimal("5000")
    assert purchase.ownership_form == "D"
    assert purchase.security_title == "Common Stock"
    assert purchase.date_of_original_submission is None
    # A gift has a footnote where the price would be: null, never zero.
    assert gift.transaction_code == "G"
    assert gift.acquired_disposed == "D"
    assert gift.price is None
    assert gift.line_no == 1
    assert indirect.ownership_form == "I"
    assert indirect.line_no == 2


def test_joint_filing_keeps_a_row_per_owner_and_transaction_with_own_flags() -> None:
    parse = parse_form4(_text("form4_joint_with_holding.xml"))
    assert parse.filer_ciks == (JANE, "0000999990")
    by_owner = {t.filer_cik: t for t in parse.transactions}
    assert len(parse.transactions) == 2
    assert by_owner[JANE].is_director is True
    assert by_owner[JANE].is_ten_percent_owner is False
    assert by_owner["0000999990"].is_ten_percent_owner is True
    assert by_owner["0000999990"].is_director is False
    assert {t.transaction_code for t in parse.transactions} == {"S"}


def test_duplicate_reporting_owner_cik_is_identity_ambiguous() -> None:
    xml = _text("form4_joint_with_holding.xml").replace("0000999990", JANE)
    with pytest.raises(ValueError, match="more than once"):
        parse_form4(xml)


def test_holding_row_does_not_shift_line_no() -> None:
    """The holding sits before the one transaction in the document: its line_no is 0."""
    parse = parse_form4(_text("form4_joint_with_holding.xml"))
    assert [t.line_no for t in parse.transactions] == [0, 0]
    assert all(t.shares == Decimal("50") for t in parse.transactions)


def test_rows_missing_code_date_ad_or_shares_are_skipped_and_counted() -> None:
    parse = parse_form4(_text("form4_amendment_with_bad_rows.xml"))
    assert parse.skipped == 4
    assert [t.line_no for t in parse.transactions] == [0, 5]  # skips still take a position
    first, last = parse.transactions
    assert first.price is None
    assert first.shares_owned_after is None  # an empty value is null, never zero
    assert last.shares_owned_after == Decimal("11")
    assert first.date_of_original_submission == date(2025, 3, 4)
    assert last.date_of_original_submission == date(2025, 3, 4)


def test_present_but_unparseable_price_skips_the_row() -> None:
    xml = _text("form4_purchase_gift_indirect.xml").replace(
        "<value>12.34</value></transactionPricePerShare>",
        "<value>twelve</value></transactionPricePerShare>",
        1,
    )
    parse = parse_form4(xml)
    assert parse.skipped == 1
    assert [t.line_no for t in parse.transactions] == [1, 2]


def test_deemed_execution_date_is_read_when_present() -> None:
    xml = _text("form4_purchase_gift_indirect.xml").replace(
        "<transactionCoding>",
        "<deemedExecutionDate><value>2025-03-02</value></deemedExecutionDate><transactionCoding>",
        1,
    )
    assert parse_form4(xml).transactions[0].deemed_execution_date == date(2025, 3, 2)


def test_filer_typed_date_with_a_timezone_suffix_is_a_plain_date() -> None:
    xml = _text("form4_purchase_gift_indirect.xml").replace(
        "<value>2025-03-03</value>", "<value>2025-03-03-05:00</value>", 1
    )
    assert parse_form4(xml).transactions[0].transaction_date == date(2025, 3, 3)


def test_transaction_code_is_verbatim() -> None:
    xml = _text("form4_purchase_gift_indirect.xml").replace(
        "<transactionCode>P</transactionCode>", "<transactionCode>M</transactionCode>", 1
    )
    assert parse_form4(xml).transactions[0].transaction_code == "M"


def test_derivative_table_rows_are_not_read() -> None:
    xml = _text("form4_purchase_gift_indirect.xml").replace(
        "</ownershipDocument>",
        "<derivativeTable><derivativeTransaction><securityTitle><value>Option</value>"
        "</securityTitle></derivativeTransaction></derivativeTable></ownershipDocument>",
    )
    assert len(parse_form4(xml).transactions) == 3


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("<issuerCik>0000777777</issuerCik>", "", "issuerCik"),
        ("<rptOwnerCik>0000888888</rptOwnerCik>", "", "rptOwnerCik"),
        ("<reportingOwner>", "<x>", "reportingOwner"),
    ],
)
def test_missing_issuer_or_owner_identity_is_a_value_error(old: str, new: str, match: str) -> None:
    xml = _text("form4_purchase_gift_indirect.xml").replace(old, new, 1)
    if new == "<x>":
        xml = xml.replace("</reportingOwner>", "</x>")
    with pytest.raises(ValueError, match=match):
        parse_form4(xml)


def test_xml_entities_are_not_expanded_into_the_document() -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;&a;">]>'
        "<ownershipDocument><issuer><issuerCik>&b;</issuerCik></issuer></ownershipDocument>"
    )
    with pytest.raises(ValueError):
        parse_form4(bomb)


def test_parser_version_is_its_own_integer_constant() -> None:
    assert isinstance(SEC_FORMS_PARSER_VERSION, int)
    assert SEC_FORMS_PARSER_VERSION >= 1
    assert not hasattr(edgar, "SEC_FORMS_CACHE_VERSION")
