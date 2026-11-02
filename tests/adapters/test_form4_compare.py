"""T349 (#2714): rule 5 per-field source comparison, pure."""

from __future__ import annotations

from collections import Counter
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


# --- T353 (#2900): the recorded read-only baseline on #2706's 11 refused quarters ---------
# Recorded 2026-11-02 by an offline replay (no SEC request, no fact write): the pre-run
# store backup bak-20261031-0441 for the pending set, bak-20261102-1729 for the stored XML
# rows, the cached index and bulk zips, and the 100 XMLs fetched for #2706. Per quarter:
# (XML-routed pending accessions, sample accessions with a cached XML, stored-XML accessions
# compared against the bulk zip, of those the multi-owner ones).
BASELINE = {
    "2016q1": (2692, 0, 1471, 120),
    "2017q4": (2066, 19, 892, 43),
    "2018q4": (1996, 0, 986, 71),
    "2019q1": (1987, 19, 1078, 53),
    "2019q3": (1836, 20, 934, 59),
    "2019q4": (1922, 0, 941, 58),
    "2020q1": (2262, 0, 1142, 70),
    "2020q3": (2064, 17, 1009, 48),
    "2021q3": (2332, 0, 788, 11),
    "2022q1": (2245, 0, 1013, 22),
    "2024q2": (2256, 17, 2141, 1135),
}
# The disagreeing stored-XML accessions, one recorded row difference per field:
# (quarter, accession, filer_cik, line_no, field, bulk value, XML value).
DEEMED = "deemed_execution_date"
BASELINE_DISAGREEMENTS = [
    ("2019q1", "0001491519-19-000001", "0001491519", 0, DEEMED, "2018-12-24", None),
    ("2019q3", "0001763113-19-000007", "0001763113", 0, DEEMED, "2019-09-16", None),
    ("2024q2", "0000895345-24-000172", "0001465837", 0, "is_director", True, False),
    ("2024q2", "0000912282-24-000379", "0001390411", 0, "is_director", False, True),
    ("2024q2", "0000912282-24-000379", "0001390411", 0, "is_officer", False, True),
    ("2024q2", "0000950172-24-000192", "0001965769", 0, "is_other", True, False),
    *(
        ("2024q2", f"0001437749-24-{n}", "0001056823", 0, "is_ten_percent_owner", False, True)
        for n in (
            "010228", "010519", "010783", "011029", "011180", "011338",
            "011481", "011602", "011739", "011866", "011987", "012146",
        )
    ),
]  # fmt: skip
BASELINE_ROW_COUNTS = {  # disagreeing rows per field over all 11 quarters, diagnosis only
    "is_ten_percent_owner": 144, "is_director": 4, "is_other": 2, "is_officer": 2,
    "deemed_execution_date": 4,
}  # fmt: skip


def _recorded(value: object, name: str) -> object:
    return date.fromisoformat(value) if name == "deemed_execution_date" and value else value  # type: ignore[arg-type]


def baseline_tally(quarters: tuple[str, ...]) -> RunTally:
    """Replay the recorded stored-XML population of `quarters` through the real comparison."""
    t = RunTally()
    bad: dict[str, list[tuple[str, object, object]]] = {}
    for q, acc, _cik, _line, name, bulk_v, xml_v in BASELINE_DISAGREEMENTS:
        if q in quarters:
            bad.setdefault(acc, []).append((name, _recorded(bulk_v, name), _recorded(xml_v, name)))
    for acc, diffs in bad.items():
        b = replace(row(), **{n: bv for n, bv, _ in diffs})  # type: ignore[arg-type]
        x = replace(row(), **{n: xv for n, _, xv in diffs})  # type: ignore[arg-type]
        found = compare_rows([b], [x], decl())
        assert {d.field for d in found} == {n for n, _, _ in diffs}
        t.add(acc, found)
    for q in quarters:
        recorded_bad = len({a for qq, a, *_ in BASELINE_DISAGREEMENTS if qq == q})
        for i in range(BASELINE[q][2] - recorded_bad):
            t.add(f"{q}-{i}", compare_rows([row()], [row()], decl()))
    return t


def test_recorded_baseline_numerator_and_denominator() -> None:
    every = tuple(BASELINE)
    total = baseline_tally(every)
    assert (total.disagreeing, total.compared) == (17, 12_395)
    assert total.by_field == Counter(
        {"is_ten_percent_owner": 12, "is_director": 2, "is_officer": 1, "is_other": 1,
         "deemed_execution_date": 2}
    )  # fmt: skip
    worst = baseline_tally(("2024q2",))
    assert (worst.disagreeing, worst.compared) == (15, 2_141)
    assert worst.share() == Decimal(15) / Decimal(2_141)  # 0.70%, the highest quarter
    single_owner = sum(v[2] - v[3] for v in BASELINE.values())
    assert single_owner == 12_395 - 1_690  # 2 of 10,705 single-owner accessions disagree
    assert {q for q, *_ in BASELINE_DISAGREEMENTS if q != "2024q2"} == {"2019q1", "2019q3"}


def test_recorded_baseline_against_candidate_thresholds() -> None:
    every, worst = baseline_tally(tuple(BASELINE)), baseline_tally(("2024q2",))
    assert every.outcome(Decimal("0.01")) is Outcome.BELOW
    assert worst.outcome(Decimal("0.01")) is Outcome.BELOW  # 0.70% under 1%
    assert worst.outcome(Decimal("0.005")) is Outcome.AT_OR_ABOVE  # a 0.5% line would fail it
    assert baseline_tally(("2019q1",)).outcome(Decimal("0.01")) is Outcome.BELOW
    assert every.outcome(None) is Outcome.REPORT_ONLY


def test_recorded_baseline_adds_no_request_and_fits_the_budget() -> None:
    from tradepartner.adapters.form4_route import RequestCounts, plan_requests

    sample = EventsConfig().form4_bulk_order_sample
    budget = EventsConfig().sec_forms_max_requests
    fetched = {q: v[0] + sample for q, v in BASELINE.items()}  # today's XML fetches
    assert sum(fetched.values()) == 23_878
    # Rule 5 compares accessions the route already fetches: it adds no request. Each fetch is
    # 1 request (primary document is XML) or 2 (a filing-index lookup first).
    for q, n in fetched.items():
        assert not plan_requests(RequestCounts(0, 0, n, 0), budget=budget).refused, q
        assert not plan_requests(RequestCounts(0, 0, 2 * n, 0), budget=budget).refused, q
    assert not plan_requests(RequestCounts(0, 0, 23_878, 0), budget=budget).refused
    assert plan_requests(RequestCounts(0, 0, 2 * 23_878, 0), budget=budget).refused
    assert (
        budget - 2 * max(fetched.values()) == 34_576
    )  # the busiest quarter, 2016q1, leaves this headroom


def test_recorded_baseline_sample_population_has_no_disagreement() -> None:
    sampled = sum(v[1] for v in BASELINE.values())
    t = RunTally()
    for i in range(sampled):
        t.add(f"s{i}", compare_rows([row()], [row()], decl()))
    assert (t.disagreeing, t.compared) == (0, 92)
