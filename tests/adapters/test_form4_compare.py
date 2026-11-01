"""T349 (#2714): rule 5 per-field source comparison, pure."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import date
from decimal import Decimal

import pytest

from tradepartner.adapters.edgar import Form4Transaction
from tradepartner.adapters.form4_compare import (
    KEY_FIELD,
    Declaration,
    FieldRule,
    Outcome,
    RunTally,
    compare_rows,
    declaration_from,
)
from tradepartner.config_data import FORM4_COMPARE_FIELDS, EventsConfig


def row(**kw: object) -> Form4Transaction:
    base = Form4Transaction(
        "1", "2", 0, True, False, False, False, date(2026, 1, 2), None, "P", "A",
        Decimal("100"), Decimal("10.00"), Decimal("500"), "D", "Common  Stock", None,
    )  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def decl(**kw: object) -> Declaration:
    return declaration_from(EventsConfig(**kw))  # type: ignore[arg-type]


def test_compared_fields_are_the_transaction_fields() -> None:
    assert {f.name for f in fields(Form4Transaction)} == set(FORM4_COMPARE_FIELDS)


def test_identical_rows_agree_and_title_whitespace_is_collapsed() -> None:
    assert compare_rows([row()], [row(security_title="Common Stock")], decl()) == ()


def test_price_boundary_at_two_decimals_default() -> None:
    d = decl()
    assert compare_rows([row(price=Decimal("10.004"))], [row(price=Decimal("10.00"))], d) == ()
    out = compare_rows([row(price=Decimal("10.005"))], [row(price=Decimal("10.00"))], d)
    assert [x.field for x in out] == ["price"]
    assert out[0].trusted == "xml"


def test_tolerance_boundary_inclusive() -> None:
    d = decl(form4_compare_tolerance={"price": "0.01"})
    assert compare_rows([row(price=Decimal("10.01"))], [row(price=Decimal("10.00"))], d) == ()
    out = compare_rows([row(price=Decimal("10.02"))], [row(price=Decimal("10.00"))], d)
    assert [x.field for x in out] == ["price"]


def test_none_against_value_disagrees_and_none_pair_agrees() -> None:
    d = decl()
    assert compare_rows([row(price=None)], [row(price=None)], d) == ()
    assert compare_rows([row(price=None)], [row(price=Decimal(0))], d)


def test_trusted_source_is_declared_per_field() -> None:
    d = decl(form4_compare_trusted={"shares": "bulk"})
    out = compare_rows([row(shares=Decimal(1))], [row(shares=Decimal(2))], d)
    assert [(x.field, x.trusted) for x in out] == [("shares", "bulk")]


def test_exact_fields_take_no_tolerance() -> None:
    with pytest.raises(ValueError):
        EventsConfig(form4_compare_tolerance={"line_no": "1"})
    with pytest.raises(ValueError):
        Declaration({n: FieldRule(tolerance=Decimal(1)) for n in FORM4_COMPARE_FIELDS})
    out = compare_rows([row(transaction_code="P")], [row(transaction_code="S")], decl())
    assert [x.field for x in out] == ["transaction_code"]


def test_owner_identity_and_order_differences_cannot_hide_behind_tolerance() -> None:
    d = decl(form4_compare_tolerance={"price": "1000", "shares": "1000"})
    a, b = row(line_no=0), row(line_no=1, filer_cik="3")
    swapped = [replace(a, line_no=1), replace(b, line_no=0)]
    out = compare_rows([a, b], swapped, d)
    assert [x.field for x in out] == [KEY_FIELD]
    assert compare_rows([row()], [row(filer_cik="9")], d)[0].field == KEY_FIELD
    assert compare_rows([row()], [row(issuer_cik="9")], d)[0].field == "issuer_cik"
    assert compare_rows([row(), row()], [row(), row()], d)[0].field == KEY_FIELD


def test_compare_does_not_mutate_its_inputs() -> None:
    bulk, xml = [row(price=Decimal("9.99"))], [row()]
    compare_rows(bulk, xml, decl())
    assert bulk == [row(price=Decimal("9.99"))] and xml == [row()]


def test_threshold_key_defaults_unset_and_validates() -> None:
    assert EventsConfig().form4_compare_threshold is None
    for bad in (0, "1.5", "-0.1"):
        with pytest.raises(ValueError):
            EventsConfig(form4_compare_threshold=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        EventsConfig(form4_compare_trusted={"nope": "xml"})


def tally(compared: int, bad: int) -> RunTally:
    t = RunTally()
    for i in range(compared):
        t.add(
            f"a{i}", compare_rows([row()], [row(shares=Decimal(1)) if i < bad else row()], decl())
        )
    return t


def test_empty_denominator_is_unchecked_even_with_a_threshold() -> None:
    t = RunTally()
    assert t.share() is None
    assert t.outcome(Decimal("0.1")) is Outcome.UNCHECKED
    assert t.outcome(None) is Outcome.UNCHECKED


def test_unset_threshold_reports_only() -> None:
    t = tally(4, 4)
    assert t.outcome(None) is Outcome.REPORT_ONLY
    assert "form4_compare_disagree=4; form4_compare_compared=4" in t.message(None)
    assert "form4_compare_threshold=unset" in t.message(None)


def test_below_and_at_threshold() -> None:
    limit = Decimal("0.25")
    assert tally(4, 0).outcome(limit) is Outcome.BELOW
    assert tally(5, 1).outcome(limit) is Outcome.BELOW  # 0.2
    assert tally(4, 1).outcome(limit) is Outcome.AT_OR_ABOVE  # exactly 0.25
    assert tally(4, 2).outcome(limit) is Outcome.AT_OR_ABOVE


def test_denominator_is_accessions_not_field_disagreements() -> None:
    t = RunTally()
    t.add("x", compare_rows([row()], [row(shares=Decimal(1), price=Decimal(1))], decl()))
    t.add("y", compare_rows([row()], [row()], decl()))
    assert (t.disagreeing, t.compared) == (1, 2)
    assert t.by_field == {"shares": 1, "price": 1}


def test_a_repeated_accession_counts_once_in_the_share() -> None:
    t = RunTally()
    bad = compare_rows([row()], [row(shares=Decimal(1))], decl())
    t.add("a", bad)
    t.add("a", bad)
    t.add("b", compare_rows([row()], [row()], decl()))
    assert (t.disagreeing, t.compared) == (1, 2)


def test_default_declaration_agrees_exactly_when_rows_agree_does() -> None:
    from tradepartner.adapters.form4_route import rows_agree

    cases = [
        ([row()], [row()]),
        ([row(price=Decimal("10.004"))], [row()]),
        ([row(price=Decimal("10.005"))], [row()]),
        ([row(filer_cik="9")], [row()]),
        ([row(line_no=1)], [row()]),
    ]
    for bulk, xml in cases:
        assert (compare_rows(bulk, xml, decl()) == ()) == rows_agree(bulk, xml)
