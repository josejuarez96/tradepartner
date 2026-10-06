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
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from edgar_transport import EdgarRouter, edgar_settings

from tradepartner.adapters.edgar import (
    StatementConflict,
    StatementFactsParse,
    parse_statement_facts,
)
from tradepartner.adapters.edgar_source import (
    COVER_VERSION,
    STATEMENT_VERSION,
    EdgarFilingSource,
    SubmissionRecord,
    reduce_submissions,
)
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


def test_non_read_unit_is_skipped_and_counted_once_per_filing_and_fact() -> None:
    """A 10-K carrying revenue and two comparatives only in EUR counts once."""
    result = parse(
        payload(
            {
                "Revenues": [
                    entry(K2023, FY2023, 3),
                    entry(K2023, FY2022, 2),
                    entry(K2023, FY2021, 1),
                ]
            },
            unit="EUR",
        )
    )
    assert result.records == ()
    assert result.non_unit == 1


def test_non_read_unit_counts_per_fact_across_tags() -> None:
    """Two EUR-only revenue tags in one filing count once; a USD fallback tag
    for the fact means the fact is read, so nothing is counted."""
    eur_only = payload({"Revenues": [entry(K2020, FY2020, 300)]}, unit="EUR")
    eur_only["facts"]["us-gaap"]["SalesRevenueNet"] = {
        "units": {"EUR": [entry(K2020, FY2020, 300)]}
    }
    assert parse(eur_only).non_unit == 1
    with_usd = payload({"Revenues": [entry(K2020, FY2020, 300)]}, unit="EUR")
    with_usd["facts"]["us-gaap"]["SalesRevenueNet"] = {
        "units": {"USD": [entry(K2020, FY2020, 290)]}
    }
    result = parse(with_usd)
    (row,) = result.records
    assert (row.xbrl_tag, row.unit) == ("us-gaap:SalesRevenueNet", "USD")
    assert result.non_unit == 0


def test_non_read_unit_beside_a_read_one_is_not_counted() -> None:
    """Counted only when the filing carries the fact in no read unit."""
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
        entry(K2022, ("2022-12-31", "2022-01-01"), 300),  # starts after it ends
        entry(K2022, ("2022-12-31", "2022-12-31"), 300),  # zero-day duration
        entry(K2022, FY2022, math.nan),
        entry(K2022, FY2022, math.inf),
        entry(K2022, FY2022, "300"),
        entry(K2022, FY2022, True),
        # 364 days like FY2021, ending after K2022's acceptance (2023-02-24)
        entry(K2022, ("2022-03-01", "2023-02-28"), 300),
    ],
)
def test_malformed_entries_are_withheld_and_counted(bad: dict[str, Any]) -> None:
    result = parse(payload({"Revenues": [bad, entry(K2022, FY2021, 200)]}))
    assert [r.period_end for r in result.records] == [date(2021, 12, 31)]
    assert result.records[0].period_days == 364
    assert result.malformed == 1
    assert result.records[0].comparative is False  # a malformed entry sets no latest end


def test_clean_same_length_column_does_flag_the_prior_one() -> None:
    """Control for the malformed test: the same pair, well formed, flags FY2021."""
    result = parse(payload({"Revenues": [entry(K2022, FY2022, 300), entry(K2022, FY2021, 200)]}))
    assert {r.period_end: r.comparative for r in result.records} == {
        date(2022, 12, 31): False,
        date(2021, 12, 31): True,
    }


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


# --- the adapter: `EdgarFilingSource.statement_facts` (plan T77a) -----------------
#
# `filing_index()` and FSN are not run here (their own tests' job): the
# source's `_filing_index_ran` and `_fsn_ready` flags are set and its
# per-CIK stamps written with `_save_stamps`, the shortcut
# `test_edgar_source_cik.py` takes. Every request goes through an
# `EdgarRouter`, which fails the test on an unrouted URL; the autouse
# socket fixture backs it up.

API = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SHARES = "EntityCommonStockSharesOutstanding"
LATE = "0000123456-22-000098"  # no stamp record at first; a late 10-Q stamp
LATE_ACCEPTED = datetime(2022, 8, 1, 20, 0, tzinfo=UTC)  # before K2022: same cache key


def _settings(tmp_path: Path, **edgar: Any) -> Settings:
    return edgar_settings(tmp_path / "cache", statement_facts_enabled=True, **edgar)


def _adapter(settings: Settings, router: EdgarRouter, **kwargs: Any) -> EdgarFilingSource:
    source = EdgarFilingSource(settings, client=router.client(), **kwargs)
    source._filing_index_ran = True
    source._fsn_ready = True  # no FSN period; the co-registrant test sets its accessions
    source._fsn_loaded_periods = ("2099_01",)  # a lag window no filing here falls inside
    return source


def _stamps(
    source: EdgarFilingSource, cik: str, stamps: dict[str, tuple[str, datetime | None]]
) -> None:
    source._save_stamps(
        cik, {a: SubmissionRecord(a, form, "doc.htm", True, at) for a, (form, at) in stamps.items()}
    )


def _company(cik: str, units: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
    """A companyfacts payload for `cik`: local tag -> unit -> entries, plus a
    `dei` share entry under `K2022`, so the payload holds the latest filing."""
    return {
        "cik": int(cik),
        "facts": {
            "dei": {SHARES: {"units": {"shares": [entry(K2022, "2023-01-31", 10)]}}},
            "us-gaap": {tag: {"units": by_unit} for tag, by_unit in units.items()},
        },
    }


def _router(**payloads: dict[str, Any]) -> EdgarRouter:
    router = EdgarRouter()
    for cik, body in payloads.items():
        router.add(API.format(cik=cik.removeprefix("c")), body)
    return router


def _cache_dir(settings: Settings) -> Path:
    return Path(settings.edgar.cache_dir) / "statement_facts" / f"v{STATEMENT_VERSION}"


#: CIK's stamps for the adapter tests: one 10-K, the latest cover-form filing.
STAMPS: dict[str, tuple[str, datetime | None]] = {
    K2022: ("10-K", datetime(2023, 2, 24, 21, 30, tzinfo=UTC)),
}


def test_switch_off_makes_no_call_no_request_and_no_cache(tmp_path: Path) -> None:
    settings = edgar_settings(tmp_path / "cache")
    assert settings.edgar.statement_facts_enabled is False  # the default
    shares = _company(CIK, {})
    router = _router(**{f"c{CIK}": shares})
    source = _adapter(settings, router)
    _stamps(source, CIK, STAMPS)
    assert source.statement_facts(CIK) == []
    assert source.requests == 0
    source.facts(CIK, [SHARES])  # the shares path's payload read fills no statement cache
    assert source.requests == 1
    assert not _cache_dir(settings).exists()


def _conflicted() -> dict[str, Any]:
    return _company(
        CIK,
        {
            "Revenues": {
                "USD": [
                    entry(K2022, FY2022, 300, filed="2023-02-24"),
                    entry(K2022, FY2022, 301, filed="2023-02-24"),  # a same-tag conflict
                    entry(LATE, FY2021, 200, filed="2022-08-01"),
                ]
            },
            "Assets": {"USD": [entry(K2022, "2022-12-31", 900, filed="2023-02-24")]},
        },
    )


def test_second_call_in_a_run_reads_the_cache_file_with_no_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router(**{f"c{CIK}": _conflicted()})
    source = _adapter(settings, router)
    _stamps(source, CIK, STAMPS)
    first = source.statement_facts(CIK)
    assert router.urls == [API.format(cik=CIK)]  # the per-CIK API, below the bulk threshold
    conflict = StatementConflict(K2022, "revenue", date(2022, 1, 1), date(2022, 12, 31), (300, 301))
    assert source.statement_conflict_keys == [conflict]
    counts = (source.statement_conflicts, source.statement_unstampable)
    assert counts == (1, 0)
    # The unstamped accession's entry arrives with no stamp and its `filed`.
    [late] = [r for r in first if r.accession == LATE]
    assert (late.accepted_at, late.form, late.filed) == (None, "", date(2022, 8, 1))
    [assets] = [r for r in first if r.fact_name == "total_assets"]
    assert assets.accepted_at == STAMPS[K2022][1] and assets.form == "10-K"

    source.statement_conflict_keys.clear()  # the write phase's read restores the list
    assert source.statement_facts(CIK) == first
    assert source.requests == 1
    assert source.statement_conflict_keys == [conflict]
    assert (source.statement_conflicts, source.statement_unstampable) == counts
    source.statement_facts(CIK)
    assert source.statement_conflict_keys == [conflict]  # listed once

    # The second call is the file, never an in-memory memo of the records.
    (_cache_dir(settings) / f"{CIK}.json").unlink()
    with pytest.raises(RuntimeError, match="filled this run"):
        source.statement_facts(CIK)
    assert source.requests == 1


def test_a_late_stamp_applies_on_the_next_read_with_no_request(tmp_path: Path) -> None:
    """The stamp is a read-time lookup: once `LATE` has a 10-Q record its
    entry is stamped, and once `EIGHT_K` turns out to be an 8-K its entries
    and its conflict leave the output (#1037); a stamped entry ending after
    its acceptance date is dropped, as the parser would."""
    body = _company(
        CIK,
        {
            "Revenues": {
                "USD": [
                    entry(LATE, FY2021, 200, filed="2022-08-01"),
                    entry(LATE, ("2022-01-01", "2022-09-30"), 250, filed="2022-08-01"),
                    entry(EIGHT_K, FY2020, 1, filed="2021-02-01"),
                    entry(EIGHT_K, FY2020, 2, filed="2021-02-01"),
                    entry(EIGHT_K, FY2019, 3, filed="2021-02-01"),
                ]
            }
        },
    )
    settings = _settings(tmp_path)
    source = _adapter(settings, _router(**{f"c{CIK}": body}))
    _stamps(source, CIK, STAMPS)
    before = source.statement_facts(CIK)
    assert {(r.accession, r.accepted_at) for r in before} == {(LATE, None), (EIGHT_K, None)}
    assert len(before) == 3 and source.statement_conflicts == 1

    _stamps(source, CIK, {**STAMPS, LATE: ("10-Q", LATE_ACCEPTED), EIGHT_K: ("8-K", None)})
    [after] = source.statement_facts(CIK)
    assert source.requests == 1
    assert (after.accession, after.form, after.accepted_at) == (LATE, "10-Q", LATE_ACCEPTED)
    assert after.period_end == date(2021, 12, 31)  # 2022-09-30 ends after acceptance
    assert after.comparative is False  # the dropped entry sets no latest end

    fresh = _adapter(settings, EdgarRouter())  # the next run: a cache hit
    assert fresh.statement_facts(CIK) == [after]
    assert fresh.statement_conflict_keys == [] and fresh.statement_conflicts == 0
    assert fresh.statement_unstampable == 0  # an 8-K is filtered, never unstampable


ONE, NONE_TAGGED, UNSTAMPED = "0000000001", "0000000002", "0000000003"


def _first_load_payloads() -> dict[str, dict[str, Any]]:
    k = {ONE: "0000000001-23-000001", NONE_TAGGED: "0000000002-23-000001"}
    k[UNSTAMPED] = "0000000003-23-000001"
    no_record = "0000000003-22-000050"

    def company(cik: str, units: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
        body = _company(cik, units)
        body["facts"]["dei"][SHARES]["units"]["shares"][0]["accn"] = k[cik]
        return body

    return {
        ONE: company(
            ONE,
            {
                "Revenues": {"USD": [entry(k[ONE], FY2022, 1), entry(k[ONE], FY2022, 2)]},
                "GrossProfit": {"EUR": [entry(k[ONE], FY2022, 3)]},
                "CostOfRevenue": {"USD": [entry(k[ONE], ("2022-12-31", "2022-12-31"), 4)]},
                "Assets": {"USD": [entry(k[ONE], "2022-12-31", 5)]},
            },
        ),
        NONE_TAGGED: company(
            NONE_TAGGED, {"InventoryNet": {"USD": [entry(k[NONE_TAGGED], FY2022, 6)]}}
        ),
        UNSTAMPED: company(UNSTAMPED, {"Revenues": {"USD": [entry(no_record, FY2021, 7)]}}),
    }


def test_a_cross_run_cache_hit_restores_conflicts_and_unstampable_only(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    payloads = _first_load_payloads()
    router = _router(**{f"c{cik}": body for cik, body in payloads.items()})
    first = _adapter(settings, router)
    accepted = datetime(2023, 2, 24, 21, 30, tzinfo=UTC)
    for cik in payloads:
        _stamps(first, cik, {f"{cik}-23-000001": ("10-K", accepted)})
        first.statement_facts(cik)
    assert first.requests == 3
    counts = ("conflicts", "non_usd", "malformed", "none", "unstampable")
    assert {c: getattr(first, f"statement_{c}") for c in counts} == {
        "conflicts": 1,
        "non_usd": 1,
        "malformed": 1,
        "none": 1,
        "unstampable": 0,  # no stamp record yet: held for the ingest, not the adapter
    }
    no_record = "0000000003-22-000050"
    _stamps(  # the stamps file settles the third's accession as unstampable
        first,
        UNSTAMPED,
        {f"{UNSTAMPED}-23-000001": ("10-K", accepted), no_record: ("10-Q", None)},
    )

    second = _adapter(settings, EdgarRouter())  # any companyfacts request fails the test
    served = {cik: second.statement_facts(cik) for cik in payloads}
    assert second.requests == 0
    assert {c: getattr(second, f"statement_{c}") for c in counts} == {
        "conflicts": 1,
        "non_usd": 0,
        "malformed": 0,
        "none": 0,
        "unstampable": 1,
    }
    assert served[UNSTAMPED] == []
    assert [r.fact_name for r in served[ONE]] == ["total_assets"]


def test_facts_and_statement_facts_share_one_payload_read(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router(**{f"c{CIK}": _conflicted()})
    source = _adapter(settings, router)
    _stamps(source, CIK, STAMPS)
    source.facts(CIK, [SHARES])
    records = source.statement_facts(CIK)
    assert router.urls == [API.format(cik=CIK)]
    assert records and source.statement_conflicts == 1  # counted on the first answer


def test_a_trailing_payload_is_cached_under_the_accession_it_reached(tmp_path: Path) -> None:
    """The API payload trails the latest 10-K (holds only `Q2022`): served
    and cached for this run's second call, keyed by `Q2022`, so the next
    run asks again."""
    body = {
        "cik": int(CIK),
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [entry(Q2022, FY2021, 5)]}}}},
    }
    settings = _settings(tmp_path)
    stamps = {**STAMPS, Q2022: ("10-Q", datetime(2022, 7, 29, 20, 0, tzinfo=UTC))}
    source = _adapter(settings, _router(**{f"c{CIK}": body}))
    _stamps(source, CIK, stamps)
    [record] = source.statement_facts(CIK)
    assert record.accession == Q2022
    assert source.statement_facts(CIK) == [record] and source.requests == 1
    cached = json.loads((_cache_dir(settings) / f"{CIK}.json").read_bytes())
    assert cached["key"].startswith(f"{Q2022}|")
    again = _adapter(settings, _router(**{f"c{CIK}": body}))
    again.statement_facts(CIK)
    assert again.requests == 1


def test_many_conflicting_accessions_never_fail_the_chunk(tmp_path: Path) -> None:
    settings = _settings(tmp_path, min_failed_filings=1)
    accessions = [f"0000123456-2{n}-000001" for n in range(3)]
    revenue = [entry(a, FY2020, v) for a in accessions for v in (1, 2)]
    source = _adapter(
        settings, _router(**{f"c{CIK}": _company(CIK, {"Revenues": {"USD": revenue}})})
    )
    accepted = datetime(2023, 2, 24, 21, 30, tzinfo=UTC)
    _stamps(source, CIK, {**STAMPS, **{a: ("10-K", accepted) for a in accessions}})
    source.statement_facts(CIK)
    assert source.statement_conflicts == 3 > settings.edgar.min_failed_filings
    source.check_failures()  # does not raise
    source.record_failures()
    failed = Path(settings.edgar.cache_dir) / "failed_filings.json"
    assert not failed.exists() or json.loads(failed.read_text()).get("entries") == {}
    assert source.failed_filings == 0


def test_co_registrant_accessions_contribute_nothing(tmp_path: Path) -> None:
    """An accession FSN extracted under another CIK (absent from this CIK's
    FSN cache), and one whose cached cover page names another entity."""
    settings = _settings(tmp_path)
    body = _company(
        CIK,
        {"Revenues": {"USD": [entry(K2022, FY2022, 1), entry(Q2022, FY2021, 2)]}},
    )
    source = _adapter(settings, _router(**{f"c{CIK}": body}))
    stamps = {**STAMPS, Q2022: ("10-Q", datetime(2022, 7, 29, 20, 0, tzinfo=UTC))}
    _stamps(source, CIK, stamps)
    source._fsn_extracted_accessions = frozenset({K2022})  # FSN holds it under another CIK
    cover = source._cover_cache_path(Q2022)
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_text(
        json.dumps(
            {
                "version": COVER_VERSION,
                "accession": Q2022,
                "cik": CIK,
                "entity_cik": "0000999999",
                "listings": [],
                "facts": [],
            }
        )
    )
    assert source.statement_facts(CIK) == []
    assert source.statement_conflicts == 0


# --- bulk reuse (`reuse_cached`, `--bulk-from-cache`) -----------------------------


def _bulk_zip(settings: Settings, members: dict[str, bytes]) -> Path:
    path = Path(settings.edgar.cache_dir) / "bulk" / "companyfacts.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def test_reuse_cached_serves_from_the_cached_zip_with_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    _bulk_zip(settings, {f"CIK{CIK}.json": json.dumps(_conflicted()).encode()})
    source = _adapter(settings, EdgarRouter(), reuse_cached=True)
    monkeypatch.setattr(source, "_stale_fact_caches", lambda: 0)
    _stamps(source, CIK, STAMPS)
    records = source.statement_facts(CIK)
    assert records and source.requests == 0  # neither the zip nor the per-CIK API


def test_reuse_cached_raises_on_a_zip_that_does_not_open(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = _bulk_zip(settings, {})
    path.write_bytes(b"not a zip")
    source = _adapter(settings, EdgarRouter(), reuse_cached=True)
    _stamps(source, CIK, STAMPS)
    with pytest.raises(zipfile.BadZipFile):
        source.statement_facts(CIK)
    assert source.requests == 0


@pytest.mark.parametrize("enabled", [False, True])
def test_stale_statement_caches_count_only_while_the_switch_is_on(
    tmp_path: Path, enabled: bool
) -> None:
    """A CIK whose facts cache is current but whose statement cache is
    absent is stale for the bulk decision only with the switch on."""
    settings = edgar_settings(tmp_path / "cache", statement_facts_enabled=enabled)
    source = _adapter(settings, _router(**{f"c{CIK}": _conflicted()}))
    _stamps(source, CIK, STAMPS)
    if enabled:  # fill the facts cache alone, as a run before the switch did
        off = _adapter(edgar_settings(tmp_path / "cache"), _router(**{f"c{CIK}": _conflicted()}))
        off.facts(CIK, [SHARES])
    else:
        source.facts(CIK, [SHARES])
    assert source._stale_fact_caches() == (1 if enabled else 0)
