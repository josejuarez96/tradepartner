"""The identity answer key: loader, matcher and the two seeds (#3275, T394)."""

from __future__ import annotations

import re
from datetime import date

import pytest

from tradepartner.store.identity_answer_key import (
    AnswerKeyError,
    Observed,
    Span,
    load_answer_key,
    match,
    parse_key,
)

_CITE = '{ kind = "accession", ref = "0001193125-20-163490", date = 2020-06-08 }'


def _key(expected_cite: str | None = _CITE, extra: str = "") -> str:
    cite = f"cite = {expected_cite}\n" if expected_cite is not None else ""
    return (
        '[[history]]\nid = "h"\n'
        "[[history.expected]]\n"
        'kind = "holds"\ncik = "0000006769"\nticker = "APA"\nfirst = 2020-06-09\n'
        f"{cite}{extra}"
    )


def test_a_cited_fact_loads() -> None:
    (h,) = parse_key(_key(), date(2026, 1, 1))
    assert h.expected[0].citation.ref == "0001193125-20-163490"


def test_the_loader_refuses_an_uncited_fact() -> None:
    with pytest.raises(AnswerKeyError, match="no citation"):
        parse_key(_key(None), date(2026, 1, 1))


def test_the_loader_refuses_a_fact_dated_after_the_check() -> None:
    with pytest.raises(AnswerKeyError, match="after the check date"):
        parse_key(_key(), date(2020, 6, 7))
    assert parse_key(_key(), date(2020, 6, 8))  # the check day itself is fine


@pytest.mark.parametrize(
    ("cite", "why"),
    [
        ('{ kind = "hearsay", ref = "https://x.test/a", date = 2020-01-01 }', "kind"),
        ('{ kind = "accession", ref = "", date = 2020-01-01 }', "no reference"),
        ('{ kind = "accession", ref = "src/a.py", date = 2020-01-01 }', "accession"),
        ('{ kind = "press_release", ref = "src/a.py", date = 2020-01-01 }', "https URL"),
        ('{ kind = "press_release", ref = "https://x.test/a" }', "no date"),
    ],
)
def test_the_loader_refuses_a_citation_that_is_not_an_outside_source(cite: str, why: str) -> None:
    with pytest.raises(AnswerKeyError, match=why):
        parse_key(_key(cite), date(2026, 1, 1))


def test_the_loader_refuses_a_bad_cik_and_an_empty_history() -> None:
    with pytest.raises(AnswerKeyError, match="CIK"):
        parse_key(_key().replace("0000006769", "6769"), date(2026, 1, 1))
    with pytest.raises(AnswerKeyError, match="at least one expected"):
        parse_key('[[history]]\nid = "h"\n', date(2026, 1, 1))


def test_the_seeds_load_with_sources_outside_this_repo() -> None:
    key = load_answer_key(date(2026, 11, 11))
    assert [h.id for h in key] == ["apa-apache-holdco-2021", "mar-nyse-chicago-2020"]
    facts = [
        c
        for h in key
        for c in [*(i.citation for i in h.inputs), *(b.citation for b in h.bars)]
        + [e.citation for e in h.expected]
    ]
    assert facts
    for c in facts:
        assert c.kind != "accession" or re.fullmatch(r"\d{10}-\d{2}-\d{6}", c.ref)
        assert c.kind == "accession" or c.ref.startswith("https://")
        assert "tradepartner" not in c.ref
    apa = key[0]
    assert {i.citation.ref for i in apa.inputs} == {
        "0001193125-20-163490",
        "0001193125-20-163501",
        "0001193125-21-063695",
    }


def _true_apa() -> Observed:
    d = date
    return Observed(
        holds=(
            Span("0000006769", "APA", d(2013, 1, 1), d(2021, 2, 28)),
            Span("0001841666", "APA", d(2021, 3, 1), None),
        ),
        bars=(
            Span("0000006769", "APA", d(2020, 6, 9), d(2020, 12, 31)),
            Span("0000006769", "APA", d(2021, 1, 1), d(2021, 2, 26)),
            Span("0001841666", "APA", d(2021, 3, 1), None),
        ),
        line_ends={
            ("0000006769", "NYSE"): d(2020, 6, 8),
            ("0000006769", "NYSE Chicago"): d(2020, 6, 8),
            ("0000006769", "Nasdaq"): None,
        },
    )


def test_the_truth_matches_and_the_apa_hole_is_named_with_its_citation() -> None:
    apa = load_answer_key(date(2026, 11, 11))[0]
    assert match(apa, _true_apa()) == []
    # The #3264 shape: the resolver ends APA's span at 2020-06-17 on the Nasdaq line too.
    wrong = Observed(
        holds=(Span("0000006769", "APA", date(2013, 1, 1), date(2020, 6, 17)),),
        bars=(Span("0000006769", "APA", date(2020, 6, 9), date(2020, 6, 17)),),
        line_ends={**_true_apa().line_ends, ("0000006769", "Nasdaq"): date(2020, 6, 17)},
    )
    misses = match(apa, wrong)
    texts = [str(m) for m in misses]
    assert any("Nasdaq ended 2020-06-17, expected no end" in t for t in texts)
    assert any("holds: 0000006769 APA 2020-06-09..2021-02-28 not covered" in t for t in texts)
    assert any("bars: 0001841666 APA" in t for t in texts)
    assert all("[accession 0001193125-" in t for t in texts)


def test_a_gap_in_the_bars_is_a_miss_and_overlaps_are_joined() -> None:
    apa = load_answer_key(date(2026, 11, 11))[0]
    gap = _true_apa()
    gapped = Observed(
        holds=gap.holds,
        bars=(
            Span("0000006769", "APA", date(2020, 6, 9), date(2020, 6, 30)),
            Span("0000006769", "APA", date(2020, 6, 25), date(2020, 7, 10)),  # overlap: joined
            Span("0000006769", "APA", date(2020, 8, 3), date(2021, 2, 26)),  # July missing
            Span("0001841666", "APA", date(2021, 3, 1), None),
        ),
        line_ends=gap.line_ends,
    )
    misses = match(apa, gapped)
    assert [m.expected.kind for m in misses] == ["bars"]


def test_an_unknown_line_is_a_miss() -> None:
    mar = load_answer_key(date(2026, 11, 11))[1]
    observed = Observed(
        holds=(Span("0001048286", "MAR", date(2020, 9, 21), None),),
        bars=(Span("0001048286", "MAR", date(2020, 9, 21), None),),
        line_ends={("0001048286", "NYSE Chicago"): date(2020, 9, 20)},
    )
    (miss,) = match(mar, observed)
    assert "Nasdaq unknown" in miss.why
