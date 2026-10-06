"""Tests for `parse_statement_facts` (plan T77, spec amendment 2026-10-03, #660).

Every parser clause of the spec's "Statement facts" acceptance group, on
constructed companyfacts payloads: comparatives on their own dates, a
misleading `fy`, the form filter on the submissions record's form, the
unit filter, a 10-K/A first carrier, a dimension raising, tag precedence,
and a same-tag conflict returned (never raised). The recorded fixtures
carry no statement tags until T78 re-records them, so they yield no rows.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.edgar import (
    StatementConflict,
    StatementFactsParse,
    parse_statement_facts,
)
from tradepartner.adapters.edgar_source import reduce_submissions
from tradepartner.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
CIK = "0000123456"
TAGS = Settings(_env_file=None).edgar.statement_tags
FORMS = Settings(_env_file=None).edgar.statement_forms
UNITS = Settings(_env_file=None).edgar.statement_units

K2020 = "0000123456-21-000001"  # FY2020 10-K
KA2020 = "0000123456-21-000009"  # FY2020 10-K/A
Q2022 = "0000123456-22-000020"  # Q2 2022 10-Q
K2022 = "0000123456-23-000001"  # FY2022 10-K
K2023 = "0000123456-24-000001"  # FY2023 10-K
KA2022 = "0000123456-23-000009"  # FY2022 10-K/A
EIGHT_K = "0000123456-21-000030"  # an 8-K the entry calls a 10-K
NO_RECORD = "0000123456-22-000099"  # no stamp record yet

# 2021 to 2023 have 364-day calendar years (period_days = end - start);
# 2020 is a leap year (365), so its column groups apart (see the leap test).
FY2023 = ("2023-01-01", "2023-12-31")
FY2022 = ("2022-01-01", "2022-12-31")
FY2021 = ("2021-01-01", "2021-12-31")
FY2020 = ("2020-01-01", "2020-12-31")
FY2019 = ("2019-01-01", "2019-12-31")


@dataclass(frozen=True)
class Stamp:
    """The submissions record the parser reads: form and acceptance."""

    form: str
    accepted_at: datetime | None


ACCEPTANCE: dict[str, Stamp] = {
    K2020: Stamp("10-K", datetime(2021, 2, 26, 21, 30, tzinfo=UTC)),
    KA2020: Stamp("10-K/A", datetime(2021, 4, 30, 20, 0, tzinfo=UTC)),
    Q2022: Stamp("10-Q", datetime(2022, 7, 29, 20, 0, tzinfo=UTC)),
    K2022: Stamp("10-K", datetime(2023, 2, 24, 21, 30, tzinfo=UTC)),
    K2023: Stamp("10-K", datetime(2024, 2, 23, 21, 30, tzinfo=UTC)),
    KA2022: Stamp("10-K/A", datetime(2023, 4, 28, 20, 0, tzinfo=UTC)),
    EIGHT_K: Stamp("8-K", datetime(2021, 2, 1, 21, 0, tzinfo=UTC)),
}


def entry(
    accn: str,
    period: tuple[str, str] | str,
    val: Any,
    *,
    fy: int = 2020,
    fp: str = "FY",
    form: str = "10-K",
    filed: str = "2021-02-26",
    frame: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One companyfacts entry; a tuple period is a duration, a str an instant."""
    row: dict[str, Any] = {}
    if isinstance(period, tuple):
        row["start"], row["end"] = period
    else:
        row["end"] = period
    row.update(val=val, accn=accn, fy=fy, fp=fp, form=form, filed=filed)
    if frame is not None:
        row["frame"] = frame
    row.update(extra)
    return row


def payload(
    facts: dict[str, list[dict[str, Any]]], *, taxonomy: str = "us-gaap", unit: str = "USD"
) -> dict[str, Any]:
    """A companyfacts payload carrying `facts` (local tag -> entries) in one unit."""
    return {
        "cik": int(CIK),
        "entityName": "Constructed Co",
        "facts": {
            taxonomy: {
                tag: {"label": tag, "description": "", "units": {unit: entries}}
                for tag, entries in facts.items()
            }
        },
    }


def parse(data: dict[str, Any], acceptance: dict[str, Stamp] | None = None) -> StatementFactsParse:
    return parse_statement_facts(
        data, TAGS, FORMS, UNITS, ACCEPTANCE if acceptance is None else acceptance
    )


def by_period(result: StatementFactsParse, fact_name: str) -> dict[date, Any]:
    return {r.period_end: r for r in result.records if r.fact_name == fact_name}


# --- periods and comparatives --------------------------------------------


def test_ten_k_with_two_comparative_years_yields_three_keys_on_their_own_dates() -> None:
    result = parse(
        payload(
            {
                "Revenues": [
                    entry(K2023, FY2023, 300, fy=2023, filed="2024-02-23", frame="CY2023"),
                    # comparatives carry the filing's fy, not their own
                    entry(K2023, FY2022, 200, fy=2023, filed="2024-02-23"),
                    entry(K2023, FY2021, 100, fy=2023, filed="2024-02-23"),
                ]
            }
        )
    )
    rows = by_period(result, "revenue")
    assert sorted(rows) == [date(2021, 12, 31), date(2022, 12, 31), date(2023, 12, 31)]
    assert {d: (r.value, r.comparative) for d, r in rows.items()} == {
        date(2023, 12, 31): (300.0, False),
        date(2022, 12, 31): (200.0, True),
        date(2021, 12, 31): (100.0, True),
    }
    current = rows[date(2023, 12, 31)]
    assert current.period_start == date(2023, 1, 1)
    assert current.period_days == 364
    assert current.cik == CIK
    assert current.xbrl_tag == "us-gaap:Revenues"
    assert current.unit == "USD"
    assert current.form == "10-K"
    assert current.accession == K2023
    assert current.accepted_at == ACCEPTANCE[K2023].accepted_at
    assert current.filed == date(2024, 2, 23)
    assert result.conflicts == ()
    assert (result.non_unit, result.malformed) == (0, 0)


def test_comparative_groups_on_exact_period_days_as_the_spec_reads() -> None:
    """The Periods rule compares within one `period_days`: a leap-year
    current column (365 days) and its prior year (364) are separate groups,
    so the prior year is not flagged. Pinned as written; the open question
    is on the PR."""
    result = parse(payload({"Revenues": [entry(K2020, FY2020, 300), entry(K2020, FY2019, 200)]}))
    flags = {r.period_end: (r.period_days, r.comparative) for r in result.records}
    assert flags == {date(2020, 12, 31): (365, False), date(2019, 12, 31): (364, False)}


def test_misleading_fy_and_no_frame_still_land_on_the_entry_dates() -> None:
    result = parse(payload({"Revenues": [entry(K2020, FY2020, 300, fy=2015, fp="Q1")]}))
    (row,) = result.records
    assert (row.period_start, row.period_end) == (date(2020, 1, 1), date(2020, 12, 31))
    assert row.comparative is False


def test_frame_never_splits_a_key() -> None:
    """Two identical entries for one period, one framed, collapse to one."""
    result = parse(
        payload(
            {"Revenues": [entry(K2020, FY2020, 300, frame="CY2020"), entry(K2020, FY2020, 300)]}
        )
    )
    assert len(result.records) == 1
    assert result.conflicts == ()


def test_comparative_is_per_fact_and_period_days() -> None:
    """A 10-Q's quarter and its year-to-date end on the same date: neither is
    a comparative; the prior year's quarter and half-year both are."""
    q2 = ("2022-04-01", "2022-06-30")
    h1 = ("2022-01-01", "2022-06-30")
    q2_prior = ("2021-04-01", "2021-06-30")
    h1_prior = ("2021-01-01", "2021-06-30")
    result = parse(
        payload(
            {
                "Revenues": [
                    entry(Q2022, q2, 90, fp="Q2", form="10-Q"),
                    entry(Q2022, h1, 170, fp="Q2", form="10-Q"),
                    entry(Q2022, q2_prior, 80, fp="Q2", form="10-Q"),
                    entry(Q2022, h1_prior, 150, fp="Q2", form="10-Q"),
                ]
            }
        )
    )
    flags = {(r.period_end, r.period_days): r.comparative for r in result.records}
    assert flags == {
        (date(2022, 6, 30), 90): False,
        (date(2022, 6, 30), 180): False,
        (date(2021, 6, 30), 90): True,
        (date(2021, 6, 30), 180): True,
    }


def test_instant_fact_has_no_start_and_zero_days() -> None:
    result = parse(
        payload({"Assets": [entry(K2020, "2020-12-31", 5000), entry(K2020, "2019-12-31", 4000)]})
    )
    rows = by_period(result, "total_assets")
    assert rows[date(2020, 12, 31)].period_start is None
    assert rows[date(2020, 12, 31)].period_days == 0
    assert rows[date(2020, 12, 31)].comparative is False
    assert rows[date(2019, 12, 31)].comparative is True


# --- form filter -------------------------------------------------------------


def test_form_comes_from_the_submissions_record_not_the_entry() -> None:
    result = parse(
        payload(
            {
                "Revenues": [
                    entry(EIGHT_K, FY2020, 999, form="10-K"),  # submissions says 8-K
                    entry(K2020, FY2019, 200, form="8-K"),  # submissions says 10-K
                ]
            }
        )
    )
    (row,) = result.records
    assert (row.accession, row.form) == (K2020, "10-K")
    assert (result.non_unit, result.malformed) == (0, 0)


def test_settled_unstampable_accession_keeps_its_form_filter_and_no_stamp() -> None:
    """A record with `accepted_at = None` (cached unstampable) still names the
    form, so the filter applies; the adapter settles it at read time."""
    acceptance = {
        K2020: Stamp("10-K", None),
        EIGHT_K: Stamp("8-K", None),
    }
    result = parse(
        payload({"Revenues": [entry(K2020, FY2020, 300), entry(EIGHT_K, FY2019, 1)]}), acceptance
    )
    (row,) = result.records
    assert (row.accession, row.form, row.accepted_at) == (K2020, "10-K", None)


def test_accession_with_no_stamp_record_is_emitted_unstamped() -> None:
    """Never dropped (the ingest's hold rule reads it); the form is unknown
    until submissions names it, so it is empty, never the entry's own."""
    result = parse(payload({"Revenues": [entry(NO_RECORD, FY2020, 300, filed="2021-02-20")]}))
    (row,) = result.records
    assert row.accepted_at is None
    assert row.form == ""
    assert row.filed == date(2021, 2, 20)


# --- unit filter ---------------------------------------------------------------


def test_non_read_unit_is_skipped_and_counted() -> None:
    result = parse(payload({"Revenues": [entry(K2020, FY2020, 300)]}, unit="EUR"))
    assert result.records == ()
    assert result.non_unit == 1


def test_non_read_unit_beside_a_read_one_is_not_counted() -> None:
    """Counted only when it is the filing's only unit for the tag."""
    data = payload({"Revenues": [entry(K2020, FY2020, 300)]})
    data["facts"]["us-gaap"]["Revenues"]["units"]["EUR"] = [entry(K2020, FY2020, 280)]
    result = parse(data)
    (row,) = result.records
    assert (row.unit, row.value) == ("USD", 300.0)
    assert result.non_unit == 0


def test_configured_units_are_read_in_order() -> None:
    data = payload({"Revenues": [entry(K2020, FY2020, 280)]}, unit="EUR")
    result = parse_statement_facts(data, TAGS, FORMS, ["USD", "EUR"], ACCEPTANCE)
    (row,) = result.records
    assert row.unit == "EUR"


# --- 10-K/A vintage ------------------------------------------------------------


def test_ten_k_a_first_carrier_keeps_its_form_verbatim() -> None:
    result = parse(payload({"Revenues": [entry(KA2020, FY2020, 310, form="10-K/A")]}))
    (row,) = result.records
    assert (row.form, row.comparative) == ("10-K/A", False)


# --- dimensions ----------------------------------------------------------------


@pytest.mark.parametrize("key", ["segment", "segments", "dimension", "dimensions"])
def test_entry_naming_a_dimension_raises(key: str) -> None:
    bad = entry(K2020, FY2020, 300, **{key: {"us-gaap:StatementBusinessSegmentsAxis": "X"}})
    with pytest.raises(ValueError, match="dimension"):
        parse(payload({"Revenues": [bad]}))


def test_dimension_raises_even_on_an_entry_the_filters_would_drop() -> None:
    bad = entry(EIGHT_K, FY2020, 300, segment={"axis": "member"})
    with pytest.raises(ValueError, match="dimension"):
        parse(payload({"Revenues": [bad]}, unit="EUR"))


# --- precedence ------------------------------------------------------------------


def test_first_listed_tag_wins_and_is_recorded() -> None:
    result = parse(
        payload(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [entry(K2020, FY2020, 290)],
                "Revenues": [entry(K2020, FY2020, 300)],
            }
        )
    )
    (row,) = result.records
    assert (row.xbrl_tag, row.value) == ("us-gaap:Revenues", 300.0)
    assert result.conflicts == ()


def test_later_tag_fills_a_period_the_first_does_not_carry() -> None:
    result = parse(
        payload(
            {
                "Revenues": [entry(K2020, FY2020, 300)],
                "SalesRevenueNet": [entry(K2020, FY2019, 200)],
            }
        )
    )
    tags = {r.period_end: r.xbrl_tag for r in result.records}
    assert tags == {
        date(2020, 12, 31): "us-gaap:Revenues",
        date(2019, 12, 31): "us-gaap:SalesRevenueNet",
    }


def test_tag_in_another_taxonomy_is_not_the_configured_one() -> None:
    result = parse(payload({"Revenues": [entry(K2020, FY2020, 300)]}, taxonomy="ifrs-full"))
    assert result.records == ()


def test_parser_never_derives_gross_profit() -> None:
    result = parse(
        payload(
            {
                "Revenues": [entry(K2020, FY2020, 300)],
                "CostOfRevenue": [entry(K2020, FY2020, 120)],
            }
        )
    )
    assert {r.fact_name for r in result.records} == {"revenue", "cost_of_revenue"}


# --- conflicts ---------------------------------------------------------------------


def test_same_tag_conflict_is_returned_withheld_not_raised() -> None:
    k22 = {"fy": 2022, "filed": "2023-02-24"}
    k23 = {"fy": 2023, "filed": "2024-02-23"}
    result = parse(
        payload(
            {
                "Revenues": [
                    entry(K2022, FY2022, 300, **k22),
                    entry(K2022, FY2022, 305, **k22),
                    entry(K2022, FY2021, 200, **k22),
                    entry(K2023, FY2023, 400, **k23),
                    entry(K2023, FY2022, 301, **k23),
                    entry(KA2022, FY2022, 302, fy=2022, form="10-K/A", filed="2023-04-28"),
                ]
            }
        )
    )
    assert result.conflicts == (
        StatementConflict(K2022, "revenue", date(2022, 1, 1), date(2022, 12, 31), (300.0, 305.0)),
    )
    assert result.conflicts[0].period_days == 364
    fy2022 = {r.accession: r for r in result.records if r.period_end == date(2022, 12, 31)}
    assert K2022 not in fy2022  # withheld from the conflicting filing
    assert (fy2022[K2023].value, fy2022[K2023].comparative) == (301.0, True)
    assert (fy2022[KA2022].value, fy2022[KA2022].comparative) == (302.0, False)
    # the conflicting filing's own withheld current column still makes its
    # clean prior-year column a comparative
    (prior,) = (r for r in result.records if r.accession == K2022)
    assert (prior.period_end, prior.comparative) == (date(2021, 12, 31), True)


def test_conflict_in_the_winning_tag_does_not_fall_back_to_the_next() -> None:
    result = parse(
        payload(
            {
                "Revenues": [entry(K2020, FY2020, 300), entry(K2020, FY2020, 305)],
                "SalesRevenueNet": [entry(K2020, FY2020, 299)],
            }
        )
    )
    assert result.records == ()
    assert len(result.conflicts) == 1


def test_identical_duplicates_collapse_to_one() -> None:
    result = parse(payload({"Revenues": [entry(K2020, FY2020, 300), entry(K2020, FY2020, 300.0)]}))
    assert len(result.records) == 1
    assert result.conflicts == ()


# --- malformed ---------------------------------------------------------------------


def test_period_ending_after_the_new_york_acceptance_date_is_malformed() -> None:
    """Accepted 2021-02-01 16:00 ET (21:00Z): an end on 02-01 is fine,
    on 02-02 it is after the acceptance date and withheld."""
    acceptance = {K2020: Stamp("10-K", datetime(2021, 2, 1, 21, 0, tzinfo=UTC))}
    result = parse(
        payload(
            {
                "Assets": [entry(K2020, "2021-02-01", 10), entry(K2020, "2021-02-02", 11)],
            }
        ),
        acceptance,
    )
    assert [r.period_end for r in result.records] == [date(2021, 2, 1)]
    assert result.malformed == 1


def test_new_york_date_not_utc_date_bounds_the_period() -> None:
    """Accepted 2021-02-02 03:00Z is still 2021-02-01 in New York."""
    acceptance = {K2020: Stamp("10-K", datetime(2021, 2, 2, 3, 0, tzinfo=UTC))}
    result = parse(payload({"Assets": [entry(K2020, "2021-02-02", 11)]}), acceptance)
    assert result.records == ()
    assert result.malformed == 1


@pytest.mark.parametrize(
    "bad",
    [
        entry(K2020, ("2020-12-31", "2020-01-01"), 300),  # starts after it ends
        entry(K2020, ("2020-12-31", "2020-12-31"), 300),  # zero-day duration
        entry(K2020, FY2020, math.nan),
        entry(K2020, FY2020, math.inf),
        entry(K2020, FY2020, "300"),
        entry(K2020, FY2020, True),
    ],
)
def test_malformed_entries_are_withheld_and_counted(bad: dict[str, Any]) -> None:
    result = parse(payload({"Revenues": [bad, entry(K2020, FY2019, 200)]}))
    assert [r.period_end for r in result.records] == [date(2019, 12, 31)]
    assert result.malformed == 1
    assert result.records[0].comparative is False  # a malformed entry sets no latest end


def test_missing_entry_field_raises() -> None:
    bad = entry(K2020, FY2020, 300)
    del bad["accn"]
    with pytest.raises(ValueError, match="malformed payload"):
        parse(payload({"Revenues": [bad]}))


def test_payload_without_the_taxonomy_yields_nothing() -> None:
    result = parse({"cik": int(CIK), "entityName": "X", "facts": {"dei": {}}})
    assert result == StatementFactsParse((), (), 0, 0)


# --- recorded fixtures -----------------------------------------------------------


@pytest.mark.parametrize("name", ["plain_issuer", "dual_class", "delisted_25nse"])
def test_recorded_fixtures_carry_no_statement_tags_yet(name: str) -> None:
    facts = json.loads((FIXTURES / f"company_facts_{name}.json").read_text())
    acceptance: dict[str, Any] = {}
    for path in sorted(FIXTURES.glob(f"submissions_{name}*.json")):
        records, _ = reduce_submissions(json.loads(path.read_text()))
        acceptance.update(records)
    assert acceptance
    result = parse_statement_facts(facts, TAGS, FORMS, UNITS, acceptance)
    assert result == StatementFactsParse((), (), 0, 0)
