"""Tests for `universe_as_of` (ADR 0006 rules 1-8; plan T13).

Probe times are the fixture README's documented `probe T` values.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner import universe as universe_module
from tradepartner.calendar import last_completed_session, sessions_in_month_window
from tradepartner.config import Settings
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import insert_row
from tradepartner.universe import MISSING_DATA_REASONS, RULES, Universe, universe_as_of

UNIVERSE_DIR = Path(__file__).resolve().parent / "fixtures" / "universe"

T_DUAL = datetime(2018, 12, 17, 21, 0, tzinfo=UTC)
T_SPLIT_FUTURE = datetime(2018, 11, 23, 18, 0, tzinfo=UTC)
T_SPLIT_BETWEEN = datetime(2019, 1, 28, 21, 0, tzinfo=UTC)
T_STALE = datetime(2020, 4, 6, 20, 0, tzinfo=UTC)
T_TRANSFER = datetime(2018, 10, 25, 20, 0, tzinfo=UTC)
T_WINDOW_DELIST = datetime(2019, 6, 24, 20, 0, tzinfo=UTC)
T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)
RUN_INGEST = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _settings(**universe: Any) -> Settings:
    return Settings(_env_file=None, universe=universe)


def _raw_close(security_id: str, session: date) -> float:
    with (UNIVERSE_DIR / "prices_daily.csv").open(newline="") as fh:
        rows = [
            r
            for r in csv.DictReader(fh)
            if r["security_id"] == security_id and r["session"] == session.isoformat()
        ]
    first_seen = min(rows, key=lambda r: r["known_at"])
    return float(first_seen["close"])


def _members(u: Universe) -> set[str]:
    return set(u.members["security_id"].to_list())


def _excluded(u: Universe) -> dict[str, tuple[int, str, str]]:
    return {
        row["security_id"]: (row["rule"], row["rule_name"], row["reason"])
        for row in u.exclusions.iter_rows(named=True)
    }


def _member(u: Universe, security_id: str) -> dict[str, Any]:
    (row,) = u.members.filter(u.members["security_id"] == security_id).iter_rows(named=True)
    return row


def test_rules_are_the_adr_rules_in_order() -> None:
    assert RULES == (
        "security_type",
        "exchange",
        "sector",
        "price",
        "liquidity",
        "history",
        "shares",
        "size",
    )


def test_a_bare_date_is_refused(fixture_store: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TypeError):
        universe_as_of(fixture_store, date(2019, 1, 28), _settings())  # type: ignore[arg-type]


def test_every_known_security_is_a_member_or_excluded_once(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_LATE, _settings())
    members, excluded = _members(u), set(u.exclusions["security_id"].to_list())
    assert not members & excluded
    assert u.exclusions.height == len(excluded)
    sql = "SELECT DISTINCT security_id FROM securities WHERE known_at <= ?"
    known = {r[0] for r in fixture_store.execute(sql, [T_LATE]).fetchall()}
    assert members | excluded == known


def test_first_failing_rule_is_reported(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # SEC_UNCLASSIFIABLE also has under 12 months of bars at T_LATE (bars
    # from 2019-03), yet rule 1 comes first.
    excluded = _excluded(universe_as_of(fixture_store, T_LATE, _settings()))
    assert excluded["SEC_UNCLASSIFIABLE"] == (1, "security_type", "unclassifiable")
    assert excluded["SEC_SPY"][:2] == (1, "security_type")
    assert excluded["SEC_MTUM"][:2] == (1, "security_type")


def test_dual_class_cap_is_summed_and_both_common_classes_admitted(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_DUAL, _settings())
    assert {"SEC_DUAL_A", "SEC_DUAL_B"} <= _members(u)
    assert _excluded(u)["SEC_DUAL_PFD"][:2] == (1, "security_type")
    session = date(2018, 12, 17)
    expected = 10_000_000 * _raw_close("SEC_DUAL_A", session) + 4_000_000 * _raw_close(
        "SEC_DUAL_B", session
    )
    a, b = _member(u, "SEC_DUAL_A"), _member(u, "SEC_DUAL_B")
    assert a["company_cap"] == b["company_cap"] == pytest.approx(expected)
    assert a["company_rank"] == b["company_rank"]


def test_split_with_ex_date_on_or_before_t_adjusts_shares(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Shares fact 5M as of 2018-10-29, 3-for-1 split ex 2019-01-11 <= T.
    row = _member(universe_as_of(fixture_store, T_SPLIT_BETWEEN, _settings()), "SEC_SPLIT_BETWEEN")
    assert row["shares"] == pytest.approx(15_000_000)
    close = _raw_close("SEC_SPLIT_BETWEEN", date(2019, 1, 28))
    assert row["close"] == pytest.approx(close)
    assert row["market_cap"] == pytest.approx(15_000_000 * close)


def test_split_known_before_t_with_ex_date_after_t_is_ignored(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # 4-for-1 split known 2018-11-15, ex 2019-02-14 > T: raw close, raw shares.
    row = _member(universe_as_of(fixture_store, T_SPLIT_FUTURE, _settings()), "SEC_SPLIT_FUTURE")
    close = _raw_close("SEC_SPLIT_FUTURE", date(2018, 11, 23))
    assert row["shares"] == pytest.approx(7_000_000)
    assert row["close"] == pytest.approx(close)
    assert row["market_cap"] == pytest.approx(7_000_000 * close)


def test_stale_shares_fact_fails_rule_7(fixture_store: duckdb.DuckDBPyConnection) -> None:
    excluded = _excluded(universe_as_of(fixture_store, T_STALE, _settings()))
    assert excluded["SEC_FACTS_STALE"] == (7, "shares", "stale_shares")


def test_a_known_form_25_removes_the_listing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # Between the Form 25's known_at and the new listing's: delisted at T.
    excluded = _excluded(universe_as_of(fixture_store, T_TRANSFER, _settings()))
    assert excluded["SEC_TRANSFER"] == (2, "exchange", "delisted")


def test_delisted_name_present_on_its_last_session_absent_after(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert "SEC_WINDOW_DELIST" in _members(
        universe_as_of(fixture_store, T_WINDOW_DELIST, _settings())
    )
    later = datetime(2019, 12, 31, 21, 0, tzinfo=UTC)
    assert _excluded(universe_as_of(fixture_store, later, _settings()))["SEC_WINDOW_DELIST"][0] == 2


def _insert_utility_sic(fixture_store: duckdb.DuckDBPyConnection) -> None:
    known = datetime(2018, 1, 2, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "classifications",
        {
            "security_id": "SEC_SPLIT_BETWEEN",
            "sic": 4911,
            "security_type": "common",
            "rule": "common_default",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def test_utility_sic_fails_rule_3_under_an_explicit_range(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Rule 3 is a per-hypothesis choice (ADR 0006 amendment 2026-10-11, #1540)."""
    _insert_utility_sic(fixture_store)
    settings = _settings(exclude_sic_ranges=[[4900, 4999]])
    excluded = _excluded(universe_as_of(fixture_store, T_SPLIT_BETWEEN, settings))
    assert excluded["SEC_SPLIT_BETWEEN"] == (3, "sector", "sic:4911")


def test_utility_sic_is_admitted_under_the_default(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _insert_utility_sic(fixture_store)
    u = universe_as_of(fixture_store, T_SPLIT_BETWEEN, _settings())
    assert "sector" not in u.exclusions["rule_name"].to_list()


def test_a_rebalance_on_a_stored_utility_exclusion_still_excludes_the_name(
    fixture_store: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """ADR 0006 amendment (#1540), property 1: a registration storing [[4900, 4999]]
    loads under the live default `()` and the rebalance still drops SIC 4911."""
    from tradepartner.backtest import hypothesis

    _insert_utility_sic(fixture_store)
    fixture = Path(__file__).parent / "fixtures" / "hypotheses" / "fixture-momentum.md"
    stored = _settings(exclude_sic_ranges=[[4900, 4999]])
    parsed = hypothesis.parse_file(fixture)
    from tradepartner.store import registry

    registry.register_hypothesis(
        fixture_store,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=fixture.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=hypothesis.frozen_params(parsed, stored),
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=stored,
    )
    live = _settings()
    assert live.universe.exclude_sic_ranges == ()
    loaded = hypothesis.load_frozen(fixture_store, parsed.slug, settings=live)
    u = universe_as_of(fixture_store, T_SPLIT_BETWEEN, loaded)
    assert _excluded(u)["SEC_SPLIT_BETWEEN"] == (3, "sector", "sic:4911")


def test_liquidity_rule_off_is_skipped_and_recorded(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    on = universe_as_of(fixture_store, T_LATE, _settings(min_median_dollar_volume=1e15))
    assert _members(on) == set()
    assert any(rule == 5 for rule, _, _ in _excluded(on).values())
    off = universe_as_of(
        fixture_store,
        T_LATE,
        _settings(min_median_dollar_volume=1e15, liquidity_rule_enabled=False),
    )
    assert off.rules_enabled["liquidity"] is False
    assert all(rule != 5 for rule, _, _ in _excluded(off).values())
    assert _members(off)


def test_output_records_rules_and_settings(fixture_store: duckdb.DuckDBPyConnection) -> None:
    settings = _settings()
    u = universe_as_of(fixture_store, T_LATE, settings)
    assert u.t == T_LATE
    assert u.session == date(2019, 6, 28)
    assert set(u.rules_enabled) == set(RULES)
    assert u.settings["fill_price"] == settings.execution.fill_price
    assert u.settings["universe"] == settings.universe.model_dump(mode="json")


def test_override_settings_change_the_result_without_the_environment(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    env_before = dict(os.environ)
    default = universe_as_of(fixture_store, T_LATE, _settings())
    top1 = universe_as_of(fixture_store, T_LATE, _settings(top_n_by_cap=1))
    assert dict(os.environ) == env_before
    assert len(set(default.members["cik"].to_list())) > 1
    assert len(set(top1.members["cik"].to_list())) == 1
    assert _members(top1) < _members(default)
    ranks = default.members.sort("company_rank")
    assert set(top1.members["cik"].to_list()) == {ranks["cik"][0]}


@pytest.mark.parametrize(
    ("override", "rule"),
    [
        ({"security_types": ["preferred"]}, 1),
        ({"exchanges": ["NYSE_AMERICAN"]}, 2),
        ({"min_price": 1e9}, 4),
        ({"min_median_dollar_volume": 1e15}, 5),
        ({"min_history_months": 120}, 6),
        ({"max_shares_age_days": 1}, 7),
        ({"top_n_by_cap": 1}, 8),
    ],
)
def test_each_key_override_changes_membership(
    fixture_store: duckdb.DuckDBPyConnection, override: dict[str, Any], rule: int
) -> None:
    default = universe_as_of(fixture_store, T_LATE, _settings())
    changed = universe_as_of(fixture_store, T_LATE, _settings(**override))
    assert _members(changed) != _members(default)
    newly_out = _members(default) - _members(changed)
    assert newly_out
    assert {_excluded(changed)[sid][0] for sid in newly_out} == {rule}


def _insert_shares(
    conn: duckdb.DuckDBPyConnection, security_id: str, class_member: str, value: float
) -> None:
    known = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
    insert_row(
        conn,
        "facts",
        {
            "security_id": security_id,
            "fact_name": "shares_outstanding",
            "as_of_date": date(2019, 2, 28),
            "class_member": class_member,
            "value": value,
            "filing_accession": "",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def test_a_class_row_wins_over_a_total_and_is_never_summed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # quant-auditor on #136: a total and a class value on one security.
    _insert_shares(fixture_store, "SEC_SPLIT_BETWEEN", "", 99_000_000)
    _insert_shares(fixture_store, "SEC_SPLIT_BETWEEN", "ClassA", 16_000_000)
    row = _member(universe_as_of(fixture_store, T_LATE, _settings()), "SEC_SPLIT_BETWEEN")
    assert row["shares"] == pytest.approx(16_000_000)


def test_two_class_rows_on_one_security_are_ambiguous(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _insert_shares(fixture_store, "SEC_SPLIT_BETWEEN", "ClassA", 16_000_000)
    _insert_shares(fixture_store, "SEC_SPLIT_BETWEEN", "ClassB", 2_000_000)
    excluded = _excluded(universe_as_of(fixture_store, T_LATE, _settings()))
    assert excluded["SEC_SPLIT_BETWEEN"] == (7, "shares", "ambiguous_shares")


def test_company_cap_counts_a_class_that_fails_liquidity(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # quant-auditor on #136: size is the company's; tradability is the
    # class's. SEC_DUAL_B fails liquidity (its dollar volume is lower), but
    # still counts toward the company's cap; only SEC_DUAL_A is admitted.
    session = date(2018, 12, 17)
    a_close, b_close = _raw_close("SEC_DUAL_A", session), _raw_close("SEC_DUAL_B", session)
    both = universe_as_of(fixture_store, T_DUAL, _settings())
    dv_a = both.members.filter(both.members["security_id"] == "SEC_DUAL_A")
    assert dv_a.height == 1
    threshold = _median_dollar_volume("SEC_DUAL_B", session) * 1.01
    assert _median_dollar_volume("SEC_DUAL_A", session) >= threshold
    u = universe_as_of(fixture_store, T_DUAL, _settings(min_median_dollar_volume=threshold))
    assert _excluded(u)["SEC_DUAL_B"][:2] == (5, "liquidity")
    row = _member(u, "SEC_DUAL_A")
    assert row["company_cap"] == pytest.approx(10_000_000 * a_close + 4_000_000 * b_close)


def _median_dollar_volume(security_id: str, session: date) -> float:
    with (UNIVERSE_DIR / "prices_daily.csv").open(newline="") as fh:
        rows = [
            r
            for r in csv.DictReader(fh)
            if r["security_id"] == security_id and r["session"] <= session.isoformat()
        ]
    rows.sort(key=lambda r: r["session"])
    last = rows[-20:]
    values = sorted(float(r["close"]) * float(r["volume"]) for r in last)
    mid = len(values) // 2
    return (values[mid - 1] + values[mid]) / 2


def test_no_bar_at_t_is_missing_history_not_price(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # SEC_TRUNC_DELIST: last bar 2018-05-25, Form 25 known only 2018-06-14.
    t = datetime(2018, 6, 5, 20, 0, tzinfo=UTC)
    excluded = _excluded(universe_as_of(fixture_store, t, _settings(min_price=1e9)))
    assert excluded["SEC_TRUNC_DELIST"] == (6, "history", "missing_bars")


def test_transfer_is_a_member_on_the_new_exchange_once_known(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, datetime(2018, 11, 2, 20, 0, tzinfo=UTC), _settings())
    assert _member(u, "SEC_TRANSFER")["exchange"] == "NASDAQ"


def test_form_25_nse_ends_the_listing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    u = universe_as_of(fixture_store, datetime(2018, 9, 28, 20, 0, tzinfo=UTC), _settings())
    assert _excluded(u)["SEC_25NSE"] == (2, "exchange", "delisted")


def test_missing_data_reasons_are_the_rule_1_6_7_kinds() -> None:
    assert {
        "unclassified",
        "unclassifiable",
        "missing_bars",
        "no_shares",
        "stale_shares",
        "ambiguous_shares",
    } == MISSING_DATA_REASONS


def test_first_span_lead_bars_admit_no_name_before_its_listing_is_known() -> None:
    """#974 no-look-ahead, at the read: bars stored under the first-span
    lead from 2018-07 (each known at its session's close) never admit the
    name before the FB listing (known 2019-07-25) is known: rule 2
    `not_listed`, the #35 decision. Once it is known, rule 6 reads them."""
    from tradepartner.calendar import next_session, session_close
    from tradepartner.store.db import configure_connection
    from tradepartner.store.schema import init_schema

    sid, filed = "0001326801", datetime(2012, 2, 1, 21, tzinfo=UTC)
    listed = datetime(2019, 7, 25, 21, tzinfo=UTC)
    common = {"ingested_at": RUN_INGEST, "source": "edgar", "provenance": "filing"}
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    insert_row(
        conn,
        "securities",
        {"security_id": sid, "cik": sid, "name": "Facebook", "known_at": filed, **common},
    )
    insert_row(
        conn,
        "classifications",
        {
            "security_id": sid,
            "sic": 7370,
            "security_type": "common",
            "rule": "common_default",
            "known_at": filed,
            **common,
        },
    )
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": "FB",
            "exchange": "NASDAQ",
            "class_title": "Class A Common Stock",
            "valid_from": date(2019, 7, 24),
            "known_at": listed,
            **common,
        },
    )
    insert_row(
        conn,
        "facts",
        {
            "security_id": sid,
            "fact_name": "shares_outstanding",
            "as_of_date": date(2019, 10, 25),
            "class_member": "",
            "value": 2_400_000_000,
            "filing_accession": None,
            "known_at": datetime(2019, 10, 30, 21, tzinfo=UTC),
            **common,
        },
    )
    session = date(2018, 7, 2)
    while session <= date(2019, 11, 29):
        close = session_close(session)
        bar = {"open": 200.0, "high": 200.0, "low": 200.0, "close": 200.0, "volume": 10**6}
        insert_row(
            conn,
            "prices_daily",
            {"security_id": sid, "session": session, **bar, "known_at": close}
            | {"ingested_at": RUN_INGEST, "source": "alpaca_sip", "provenance": "bar"},
        )
        session = next_session(session)

    before = universe_as_of(conn, session_close(date(2019, 6, 28)), _settings())
    assert _excluded(before)[sid] == (2, "exchange", "not_listed")
    after = universe_as_of(conn, session_close(date(2019, 11, 29)), _settings())
    assert sid in _members(after)  # past rule 6 on the lead bars, and the rest
    conn.close()


# --- #1305: the bar read is bounded by the rules' windows ---------------------------


def _first_needed_session(t: datetime, settings: Settings) -> date:
    """The earliest session rules 4-6 look at: the history window's first session or
    the liquidity window's, whichever is earlier."""
    session = last_completed_session(t)
    history = sessions_in_month_window(session, settings.universe.min_history_months)
    liquidity = universe_module._last_sessions(session, settings.universe.liquidity_window)
    return min(history[0], min(liquidity))


@pytest.mark.parametrize("t", [T_SPLIT_BETWEEN, T_LATE, T_STALE])
def test_the_bar_read_starts_at_the_rules_windows_not_the_store_start(
    fixture_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, t: datetime
) -> None:
    # #1305: the universe read every traded bar since the store's first session, so
    # its cost grew with the calendar date of the step. Rules 4-6 look only at the
    # session, the liquidity window and the history window: a bar before them is
    # never read, however much history the store holds.
    settings = _settings()
    read: list[pl.DataFrame] = []
    real = universe_module.prices_as_of

    def recording(*args: Any, **kwargs: Any) -> pl.DataFrame:
        frame = real(*args, **kwargs)
        read.append(frame)
        return frame

    monkeypatch.setattr(universe_module, "prices_as_of", recording)
    universe_as_of(fixture_store, t, settings)
    first = _first_needed_session(t, settings)
    older = prices_as_of(fixture_store, t).filter(pl.col("session") < first)
    assert older.height > 0  # the store does hold bars before the windows
    assert read and all(frame.height > 0 for frame in read)
    assert min(frame["session"].min() for frame in read) >= first  # type: ignore[type-var]


@pytest.mark.parametrize("t", [T_DUAL, T_SPLIT_BETWEEN, T_STALE, T_WINDOW_DELIST, T_LATE])
def test_the_bounded_bar_read_gives_the_full_history_universe(
    fixture_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch, t: datetime
) -> None:
    # The bound is an optimisation only: with the bar read made unbounded again the
    # universe is identical, member for member and exclusion for exclusion.
    settings = _settings()
    bounded = universe_as_of(fixture_store, t, settings)
    real = universe_module.prices_as_of

    def unbounded(*args: Any, **kwargs: Any) -> pl.DataFrame:
        kwargs.pop("sessions_from", None)
        return real(*args, **kwargs)

    monkeypatch.setattr(universe_module, "prices_as_of", unbounded)
    full = universe_as_of(fixture_store, t, settings)
    assert bounded.members.equals(full.members)
    assert bounded.exclusions.equals(full.exclusions)
    assert bounded.shares_fallbacks.equals(full.shares_fallbacks)


# --- `universe.rank_by` (ADR 0023 D4, T212) ---------------------------------------------

#: The probe instants above, one per fixture case.
_RANK_BY_PROBES = (
    T_DUAL,
    T_SPLIT_FUTURE,
    T_SPLIT_BETWEEN,
    T_STALE,
    T_TRANSFER,
    T_WINDOW_DELIST,
    T_LATE,
    datetime(2020, 6, 30, 21, 0, tzinfo=UTC),
)

#: SHA-256 of `universe_as_of` under the default settings at `_RANK_BY_PROBES`: the member
#: and exclusion rows, the rules, the shares fallbacks and the fill price. Computed on
#: `origin/main`'s `universe.py` before the key existed (6a9e2dec); it moves on any drift of
#: `cap`, the default, for any universe, backtest or paper read.
_CAP_DIGEST = "877e7484094758330f09c5537c080c8db3be30dca6834ac1cd61d7cf2713c3fe"

#: The fixture's first assets snapshot is fetched 2020-06-26 21:00 UTC.
_BEFORE_SNAPSHOT = datetime(2020, 6, 25, 21, 0, tzinfo=UTC)
_AFTER_SNAPSHOT = datetime(2020, 6, 30, 21, 0, tzinfo=UTC)


def _digest(conn: duckdb.DuckDBPyConnection, settings: Settings) -> str:
    h = hashlib.sha256()
    for t in _RANK_BY_PROBES:
        u = universe_as_of(conn, t, settings)
        payload = [
            u.members.columns,
            u.members.rows(),
            u.exclusions.rows(),
            u.rules_enabled,
            u.shares_fallbacks.rows(),
            u.settings["fill_price"],
        ]
        h.update(json.dumps(payload, default=str, sort_keys=True).encode())
    return h.hexdigest()


def _rank_settings(**universe: Any) -> Settings:
    return _settings(rank_by="dollar_volume", **universe)


def _history_median(security_id: str, session: date, months: int = 12) -> float:
    """The median raw close x volume over the history window, from the fixture's first-seen
    bars (a hand value, never `universe_as_of`'s own read)."""
    window = {d.isoformat() for d in sessions_in_month_window(session, months)}
    with (UNIVERSE_DIR / "prices_daily.csv").open(newline="") as fh:
        first_seen: dict[str, dict[str, str]] = {}
        for r in sorted(csv.DictReader(fh), key=lambda r: r["known_at"]):
            if r["security_id"] == security_id and r["session"] in window:
                first_seen.setdefault(r["session"], r)
    assert len(first_seen) == len(window)
    return statistics.median(float(r["close"]) * float(r["volume"]) for r in first_seen.values())


def test_rank_by_defaults_to_cap_and_refuses_anything_else() -> None:
    assert Settings(_env_file=None).universe.rank_by == "cap"  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        _settings(rank_by="market_cap")


def test_cap_is_byte_identical_to_main_on_every_probe(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert _digest(fixture_store, Settings(_env_file=None)) == _CAP_DIGEST  # type: ignore[call-arg]
    assert _digest(fixture_store, _settings(rank_by="cap")) == _CAP_DIGEST
    u = universe_as_of(fixture_store, T_DUAL, _settings(rank_by="cap"))
    assert u.members.columns == [
        "security_id",
        "cik",
        "ticker",
        "exchange",
        "close",
        "shares",
        "market_cap",
        "company_cap",
        "company_rank",
    ]
    assert (u.n_assets_intersected, u.assets_intersection_skipped) == (0, False)


def test_dollar_volume_ranks_by_the_median_over_the_history_window(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_DUAL, _rank_settings())
    session = last_completed_session(T_DUAL)
    # DUALA and DUALB are one company: its value is its classes' medians summed.
    expected = {
        "SEC_SPLIT_FUTURE": _history_median("SEC_SPLIT_FUTURE", session),
        "SEC_DUAL_A": _history_median("SEC_DUAL_A", session)
        + _history_median("SEC_DUAL_B", session),
        "SEC_SPLIT_BETWEEN": _history_median("SEC_SPLIT_BETWEEN", session),
    }
    for security_id, value in expected.items():
        assert _member(u, security_id)["company_dollar_volume"] == pytest.approx(value)
    values = u.members.unique("cik").sort("company_rank")["company_dollar_volume"].to_list()
    assert values == sorted(values, reverse=True)
    # The same instant under `cap` ranks by shares x close instead: the two orders differ.
    cap = universe_as_of(fixture_store, T_DUAL, _settings())
    assert u.members.sort("company_rank", "security_id")["security_id"].to_list() != (
        cap.members["security_id"].to_list()
    )
    assert _member(u, "SEC_DUAL_A")["company_rank"] == _member(u, "SEC_DUAL_B")["company_rank"]


def test_dollar_volume_needs_no_shares_fact(fixture_store: duckdb.DuckDBPyConnection) -> None:
    assert universe_as_of(fixture_store, T_LATE, _settings()).members.height > 0
    with_facts = universe_as_of(fixture_store, T_LATE, _rank_settings())
    fixture_store.execute("DELETE FROM facts")
    assert universe_as_of(fixture_store, T_LATE, _settings()).members.is_empty()
    u = universe_as_of(fixture_store, T_LATE, _rank_settings())
    assert u.members["security_id"].to_list() == with_facts.members["security_id"].to_list()
    assert u.members["shares"].null_count() == u.members.height
    assert u.members["market_cap"].null_count() == u.members.height
    assert not u.rules_enabled["shares"]
    assert u.shares_fallbacks.is_empty()
    assert not (set(_excluded(u).values()) & {(7, "shares", r) for r in MISSING_DATA_REASONS})


def test_dollar_volume_keeps_the_high_volume_low_shares_name(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """SPFT has the fewest shares of the three names `cap` ranks at T_DUAL and the most
    dollar volume: it is last under `cap` and first under `dollar_volume`."""
    cap = universe_as_of(fixture_store, T_DUAL, _settings(top_n_by_cap=2))
    assert "SEC_SPLIT_BETWEEN" not in _members(cap)
    top = universe_as_of(fixture_store, T_DUAL, _rank_settings(top_n_by_cap=1))
    assert _members(top) == {"SEC_SPLIT_FUTURE"}
    assert _excluded(top)["SEC_SPLIT_BETWEEN"] == (8, "size", "top_n_by_cap")


def test_the_floors_still_apply_under_dollar_volume(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_DUAL, _rank_settings(min_median_dollar_volume=1e18))
    assert u.members.is_empty()
    assert {reason for _, _, reason in _excluded(u).values()} >= {"min_median_dollar_volume"}


def test_intersection_removes_an_untradable_asset_after_the_first_snapshot(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings())
    before = universe_as_of(fixture_store, _BEFORE_SNAPSHOT, _rank_settings())
    assert not u.assets_intersection_skipped
    # The latest snapshot lists SPLT, TRNS, RDAT, TCKB (and BNDX, delisted): those stay.
    assert {m["ticker"] for m in u.members.iter_rows(named=True)} == {
        "SPLT",
        "RDAT",
        "TRNS",
        "TCKB",
    }
    untradable = {
        s for s, (_, name, reason) in _excluded(u).items() if reason == "untradable_asset"
    }
    assert untradable and untradable <= _members(before)
    assert all(_excluded(u)[s][:2] == (8, "size") for s in untradable)
    assert u.n_assets_intersected == u.members.height + len(untradable)


def test_a_flagged_untradable_asset_is_excluded_and_an_inactive_one_too(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute(
        "UPDATE borrow_status SET tradable = FALSE WHERE symbol = 'SPLT'"
        " AND known_at > '2020-06-29'"
    )
    fixture_store.execute(
        "UPDATE borrow_status SET status = 'inactive' WHERE symbol = 'RDAT'"
        " AND known_at > '2020-06-29'"
    )
    u = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings())
    assert {m["ticker"] for m in u.members.iter_rows(named=True)} == {"TRNS", "TCKB"}


def test_top_n_is_taken_before_the_intersection(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """The cut is the ranked set's, then the intersection removes from it: a name beyond
    the top N never takes the place of a removed one."""
    full = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings())
    cut = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings(top_n_by_cap=1))
    assert cut.members.height <= 1
    assert _members(cut) <= _members(full)


def test_before_the_first_snapshot_the_intersection_is_skipped_and_recorded(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, _BEFORE_SNAPSHOT, _rank_settings())
    assert u.assets_intersection_skipped
    assert u.n_assets_intersected == 0
    assert all(r != "untradable_asset" for _, _, r in _excluded(u).values())
    # Never today's list read backwards: the store's later snapshots leave it unchanged.
    fixture_store.execute("DELETE FROM borrow_status")
    assert universe_as_of(fixture_store, _BEFORE_SNAPSHOT, _rank_settings()).members.equals(
        u.members
    )
    # And with no snapshot at all (even after the instant), the same rule.
    after = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings())
    assert after.assets_intersection_skipped and after.members.height > 0


def test_a_store_without_the_assets_table_skips_the_intersection(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute("DROP TABLE borrow_status")
    u = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings())
    assert u.assets_intersection_skipped and u.members.height > 0


def test_rank_by_is_a_frozen_universe_key_in_the_config_dump(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_DUAL, _rank_settings())
    assert u.settings["universe"]["rank_by"] == "dollar_volume"


def test_a_nyse_class_ticker_matches_its_alpaca_symbol_through_class_symbols(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The broker names a NYSE class share by `alpaca.class_symbols` (`BFB` -> `BF.B`,
    #1219): the intersection matches on the same symbol, never the filer's spelling."""
    fixture_store.execute("UPDATE borrow_status SET symbol = 'SPL.T' WHERE symbol = 'SPLT'")
    plain = universe_as_of(fixture_store, _AFTER_SNAPSHOT, _rank_settings())
    assert "SEC_SPLIT_PLAIN" not in _members(plain)
    mapped = Settings(
        _env_file=None,
        universe={"rank_by": "dollar_volume"},
        alpaca={"class_symbols": {"SPLT": "SPL.T"}},
    )
    assert "SEC_SPLIT_PLAIN" in _members(universe_as_of(fixture_store, _AFTER_SNAPSHOT, mapped))
