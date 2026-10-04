"""Implausible shares facts fall back to the last accepted fact (#845; ADR 0006
rule 7 amendment 2026-10-04).

A filer scale error in `EntityCommonStockSharesOutstanding` (x1,000 or
x1,000,000) made AJG, SKY, VCEL and CCRN rank among the largest companies.
At T, each shares fact known at T is compared with the security's last
accepted earlier fact, moved by the splits known at T between the two
dates; outside `[1 / universe.max_shares_ratio, universe.max_shares_ratio]`
it is rejected unless `universe.accepted_shares_facts` names it, and rules 7
and 8 use the last accepted fact. Only facts and splits known at T are read.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest
from lookahead.harness import TruncatedStore

from tradepartner.config import Settings
from tradepartner.gap import survivorship_gap
from tradepartner.health import health_report
from tradepartner.store.db import insert_row
from tradepartner.universe import (
    Universe,
    latest_shares_as_of,
    shares_as_of,
    universe_as_of,
)

SID = "SEC_SPLIT_BETWEEN"  # 5M as of 2018-10-29; 3-for-1 split ex 2019-01-11
STALE = "SEC_FACTS_STALE"  # 2M as of 2019-02-01; stale at T_STALE
T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)
T_STALE = datetime(2020, 4, 6, 20, 0, tzinfo=UTC)
#: SID's fact moved by its 3-for-1 split: the plausible count after 2019-01-11.
MOVED = 15_000_000


def _settings(**universe: Any) -> Settings:
    return Settings(_env_file=None, universe=universe)


def _fact(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    as_of: date,
    value: float,
    known: datetime,
    class_member: str = "",
) -> None:
    insert_row(
        conn,
        "facts",
        {
            "security_id": security_id,
            "fact_name": "shares_outstanding",
            "as_of_date": as_of,
            "class_member": class_member,
            "value": value,
            "filing_accession": f"0000000000-{as_of:%y%m%d}-{class_member or 0}",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _split(conn: duckdb.DuckDBPyConnection, ex_date: date, ratio: float, known: datetime) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": SID,
            "action_type": "split",
            "ex_date": ex_date,
            "ratio_or_amount": ratio,
            "announced_at": None,
            "source_action_id": "",
            "cancelled": False,
            "known_at": known,
            "ingested_at": known,
            "source": "alpaca",
            "provenance": "action",
        },
    )


def _known(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)


def _member(u: Universe, security_id: str) -> dict[str, Any]:
    (row,) = u.members.filter(u.members["security_id"] == security_id).iter_rows(named=True)
    return row


def _excluded(u: Universe) -> dict[str, tuple[int, str]]:
    return {r["security_id"]: (r["rule"], r["reason"]) for r in u.exclusions.iter_rows(named=True)}


SPIKE = date(2019, 2, 28)


def test_a_scale_spike_falls_back_to_the_last_accepted_fact(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, SID, SPIKE, MOVED * 1000, _known(date(2019, 3, 1)))
    u = universe_as_of(fixture_store, T_LATE, _settings())
    row = _member(u, SID)
    assert row["shares"] == pytest.approx(MOVED)
    assert u.shares_fallbacks.rows(named=True) == [
        {
            "security_id": SID,
            "as_of_date": SPIKE,
            "value": MOVED * 1000,
            "used_as_of": date(2018, 10, 29),
            "used_value": 5_000_000,
            "ratio": pytest.approx(1000),
        }
    ]


def test_a_downward_scale_error_is_rejected_too(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, SID, SPIKE, MOVED / 1000, _known(date(2019, 3, 1)))
    assert _member(universe_as_of(fixture_store, T_LATE, _settings()), SID)[
        "shares"
    ] == pytest.approx(MOVED)


def test_consecutive_spikes_compare_with_the_last_accepted_fact(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # AJG 2020: two bad quarters in a row; the second is in line with the
    # first but not with the last accepted fact.
    _fact(fixture_store, SID, SPIKE, MOVED * 1e6, _known(date(2019, 3, 1)))
    _fact(fixture_store, SID, date(2019, 5, 31), MOVED * 1.01e6, _known(date(2019, 6, 3)))
    pick = shares_as_of(fixture_store, T_LATE, [SID], _settings())
    assert pick.shares[SID] == (date(2018, 10, 29), 5_000_000)
    assert pick.outliers["as_of_date"].to_list() == [SPIKE, date(2019, 5, 31)]
    assert not pick.outliers["accepted"].any()
    # The next fact in line with the last accepted one is accepted again.
    _fact(fixture_store, SID, date(2019, 6, 14), MOVED * 1.02, _known(date(2019, 6, 17)))
    later = shares_as_of(fixture_store, T_LATE, [SID], _settings())
    assert later.shares[SID] == (date(2019, 6, 14), MOVED * 1.02)
    assert later.fallbacks.is_empty()


def test_an_in_line_fact_after_a_split_is_accepted(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # 5M -> 15.3M across the 3-for-1 split: x1.02 once moved, not x3.06 raw.
    _fact(fixture_store, SID, SPIKE, MOVED * 1.02, _known(date(2019, 3, 1)))
    pick = shares_as_of(fixture_store, T_LATE, [SID], _settings(max_shares_ratio=2))
    assert pick.shares[SID] == (SPIKE, MOVED * 1.02)
    assert pick.outliers.is_empty()


def test_a_split_explains_a_move_only_from_when_it_is_known(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # A 200-for-1 split ex 2019-02-15 known only on 2019-05-01; the fact
    # after it is known on 2019-03-01. Between the two it is out of line.
    _split(fixture_store, date(2019, 2, 15), 200, _known(date(2019, 5, 1)))
    _fact(fixture_store, SID, SPIKE, MOVED * 200, _known(date(2019, 3, 1)))
    before = shares_as_of(fixture_store, _known(date(2019, 4, 30)), [SID], _settings())
    after = shares_as_of(fixture_store, T_LATE, [SID], _settings())
    assert before.shares[SID] == (date(2018, 10, 29), 5_000_000)
    assert after.shares[SID] == (SPIKE, MOVED * 200)


def test_an_owner_accepted_fact_is_used_and_becomes_the_baseline(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, SID, SPIKE, MOVED * 500, _known(date(2019, 3, 1)))
    _fact(fixture_store, SID, date(2019, 5, 31), MOVED * 501, _known(date(2019, 6, 3)))
    settings = _settings(accepted_shares_facts=[f"{SID}@{SPIKE.isoformat()}"])
    pick = shares_as_of(fixture_store, T_LATE, [SID], settings)
    assert pick.shares[SID] == (date(2019, 5, 31), MOVED * 501)
    assert pick.outliers.select("as_of_date", "accepted").rows() == [(SPIKE, True)]
    assert pick.fallbacks.is_empty()


def test_a_fallback_past_the_age_limit_is_stale_shares(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Without the check the fresh 2e9 fact would admit STALE; its last
    # accepted fact (2019-02-01) is 430 days old at T_STALE.
    _fact(fixture_store, STALE, date(2020, 3, 31), 2e9, _known(date(2020, 4, 2)))
    u = universe_as_of(fixture_store, T_STALE, _settings())
    assert _excluded(u)[STALE] == (7, "stale_shares")
    assert STALE in u.shares_fallbacks["security_id"].to_list()
    accepted = _settings(accepted_shares_facts=[f"{STALE}@2020-03-31"])
    assert STALE in universe_as_of(fixture_store, T_STALE, accepted).members["security_id"]


def test_the_first_fact_of_a_security_passes(fixture_store: duckdb.DuckDBPyConnection) -> None:
    pick = shares_as_of(fixture_store, T_LATE, None, _settings(max_shares_ratio=1.0001))
    assert pick.outliers.is_empty()  # every fixture security has one fact


def test_a_non_positive_fact_is_never_a_baseline(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # quant-auditor on #849: a zero first fact must not make every later
    # correct fact an outlier.
    new = "SEC_ZERO_FIRST"
    _fact(fixture_store, new, date(2019, 1, 31), 0, _known(date(2019, 2, 1)))
    first = shares_as_of(fixture_store, _known(date(2019, 2, 1)), [new], _settings())
    assert new not in first.shares
    _fact(fixture_store, new, SPIKE, 8_000_000, _known(date(2019, 3, 1)))
    _fact(fixture_store, new, date(2019, 5, 31), 8_100_000, _known(date(2019, 6, 3)))
    _fact(fixture_store, new, date(2019, 6, 14), -1, _known(date(2019, 6, 17)))
    settings = _settings(accepted_shares_facts=[f"{new}@2019-06-14"])
    pick = shares_as_of(fixture_store, T_LATE, [new], settings)
    assert pick.shares[new] == (date(2019, 5, 31), 8_100_000)
    assert pick.outliers.select("as_of_date", "accepted").rows() == [
        (date(2019, 1, 31), False),
        (date(2019, 6, 14), False),
    ]
    assert pick.fallbacks["used_as_of"].to_list() == [date(2019, 5, 31)]


def test_the_ambiguous_latest_date_is_still_ambiguous(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, SID, SPIKE, MOVED, _known(date(2019, 3, 1)), "ClassA")
    _fact(fixture_store, SID, SPIKE, MOVED, _known(date(2019, 3, 1)), "ClassB")
    shares, ambiguous = latest_shares_as_of(fixture_store, T_LATE, [SID], _settings())
    assert ambiguous == {SID}
    assert SID not in shares


# --- No look-ahead ------------------------------------------------------------


@pytest.fixture
def _later_rows(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """A spike known before T_LATE, then a correction and another spike known
    only after it: neither may change the result at T_LATE."""
    _fact(fixture_store, SID, SPIKE, MOVED * 1000, _known(date(2019, 3, 1)))
    _fact(fixture_store, SID, date(2019, 6, 28), MOVED * 1.01, _known(date(2019, 7, 15)))
    _fact(fixture_store, SID, date(2019, 9, 30), MOVED * 1000, _known(date(2019, 10, 15)))
    # A 2-for-1 split ex 2019-08-15 known only on 2019-08-20: it moves the
    # baseline for the 2019-09-30 fact from then on, never before.
    _split(fixture_store, date(2019, 8, 15), 2, _known(date(2019, 8, 20)))


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


@pytest.mark.usefixtures("_later_rows")
def test_later_filings_never_change_the_pick_at_t(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    settings = _settings()
    probes = [
        _known(date(2019, 2, 28)),
        _known(date(2019, 3, 1)),
        T_LATE,
        _known(date(2019, 7, 15)),
        _known(date(2019, 8, 19)),
        _known(date(2019, 8, 20)),
        _known(date(2019, 10, 15)),
    ]
    for t in probes:
        full = shares_as_of(fixture_store, t, None, settings)
        cut = shares_as_of(truncated.at(t), t, None, settings)
        assert full.shares == cut.shares, t
        assert full.outliers.equals(cut.outliers), t
        assert full.fallbacks.equals(cut.fallbacks), t
        u_full = universe_as_of(fixture_store, t, settings)
        u_cut = universe_as_of(truncated.at(t), t, settings)
        assert u_full.members.equals(u_cut.members), t
        assert u_full.shares_fallbacks.equals(u_cut.shares_fallbacks), t
    # At T_LATE the correction is not yet known: the fallback is used.
    assert shares_as_of(fixture_store, T_LATE, [SID], settings).shares[SID][0] == date(2018, 10, 29)
    # Once known, the correction is accepted, then the later spike falls back to it.
    end = shares_as_of(fixture_store, _known(date(2019, 10, 15)), [SID], settings)
    assert end.shares[SID] == (date(2019, 6, 28), MOVED * 1.01)


# --- Health and gap -------------------------------------------------------------


def test_health_lists_every_out_of_line_fact_and_marks_the_accepted(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, SID, SPIKE, MOVED * 1000, _known(date(2019, 3, 1)))
    _fact(fixture_store, STALE, date(2019, 4, 30), 2e9, _known(date(2019, 5, 2)))
    report = health_report(fixture_store, T_LATE, _settings())
    assert report.shares_outliers.pending.select("security_id", "as_of_date").rows() == [
        (STALE, date(2019, 4, 30)),
        (SID, SPIKE),
    ]
    accepted = health_report(
        fixture_store, T_LATE, _settings(accepted_shares_facts=[f"{SID}@{SPIKE.isoformat()}"])
    )
    assert accepted.shares_outliers.frame.height == 2
    assert accepted.shares_outliers.pending["security_id"].to_list() == [STALE]
    cut = health_report(fixture_store, T_LATE, _settings(), jumps_before=date(2019, 4, 1))
    assert cut.shares_outliers.frame["security_id"].to_list() == [SID]


def test_the_gap_sizes_a_name_with_the_same_pick(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    before = survivorship_gap(fixture_store, T_LATE, _settings())
    _fact(fixture_store, SID, SPIKE, MOVED * 1e6, _known(date(2019, 3, 1)))
    after = survivorship_gap(fixture_store, T_LATE, _settings())
    assert SID in after.listed
    assert after.listed_value == pytest.approx(before.listed_value)
    assert after.size_share == pytest.approx(before.size_share)
