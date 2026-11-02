"""The pure owner-attribution check on a multi-owner Form 4 XML filing (T350, #2711)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from tradepartner.adapters.edgar import parse_form4
from tradepartner.adapters.filings import SecFormFiling
from tradepartner.adapters.form4_owner_route import (
    AMBIGUOUS,
    OWNER_SET_MISMATCH,
    owner_associations,
    refusal,
)

# The #2705 / #2711 case: 0001437749-24-011338 style joint filing with two owners.
_JOINT = Path(__file__).parents[1] / "fixtures/edgar/sec_forms/form4_joint_with_holding.xml"


def _filing() -> SecFormFiling:
    parsed = parse_form4(_JOINT.read_text())
    return SecFormFiling(
        "0001437749-24-011338",
        "4",
        datetime(2024, 5, 1, tzinfo=UTC),
        date(2024, 5, 1),
        (parsed.issuer_cik,),
        parsed.filer_ciks,
        parsed.transactions,
    )


def test_fixture_is_multi_owner_with_every_owner_on_every_line() -> None:
    filing = _filing()
    assert len(filing.filer_ciks) > 1
    assert set(owner_associations(filing).values()) == {frozenset(filing.filer_ciks)}
    assert refusal(filing, filing.filer_ciks) is None


def test_roles_come_from_xml_not_from_a_swapped_bulk_record() -> None:
    filing = _filing()
    roles = {t.filer_cik: (t.is_director, t.is_ten_percent_owner) for t in filing.transactions}
    assert set(roles) == set(filing.filer_ciks)
    assert refusal(filing, reversed(filing.filer_ciks)) is None  # order is not attribution


def test_missing_or_extra_association_is_ambiguous() -> None:
    filing = _filing()
    assert refusal(replace(filing, transactions=filing.transactions[1:])) == AMBIGUOUS
    one = filing.filer_ciks[0]
    only_one_owner = tuple(t for t in filing.transactions if t.filer_cik == one)
    assert refusal(replace(filing, transactions=only_one_owner)) == AMBIGUOUS


def test_repeated_pair_and_repeated_owner_are_ambiguous() -> None:
    filing = _filing()
    twice = replace(filing, transactions=(*filing.transactions, filing.transactions[0]))
    assert refusal(twice) == AMBIGUOUS
    dup = replace(filing, filer_ciks=(*filing.filer_ciks, filing.filer_ciks[0]))
    assert refusal(dup) == AMBIGUOUS


def test_owner_set_differing_from_bulk_is_refused() -> None:
    filing = _filing()
    assert refusal(filing, filing.filer_ciks[:1]) == OWNER_SET_MISMATCH


def test_single_owner_is_not_checked() -> None:
    filing = _filing()
    one = filing.filer_ciks[0]
    single = replace(
        filing,
        filer_ciks=(one,),
        transactions=tuple(t for t in filing.transactions if t.filer_cik == one),
    )
    assert refusal(single, (one,)) is None
