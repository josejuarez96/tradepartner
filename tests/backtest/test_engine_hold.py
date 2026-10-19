"""Hold mode, the no-trade band and cash accrual in the engine (backtest spec amendment
2026-10-18 #1731, "Hold mode and the no-trade band" and "Cash accrual"; plan T209b).

Locked tests (split trial, phase 1): written before the build, against the plan line's
Tests clause. The fixtures are `daily` with `close` fills, so the fill session F_i is
T_{i+1} and a position's value at its fill session's close is its value after the trade;
the hand values are at the 0 bp level unless a test says otherwise.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import pairwise
from typing import Any

import polars as pl
import pytest

from backtest import test_engine, test_frozen_defaults
from backtest.fake_provider import FakeProvider
from tradepartner.backtest.engine import STRATEGY_SERIES, BacktestResult, run
from tradepartner.backtest.results import metric_rows
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings

# --- shared fixture ------------------------------------------------------------------

HISTORY_START, HISTORY_END = date(2022, 12, 1), date(2024, 3, 29)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]
#: The run window: rebalance (daily) sessions Feb 1, 2, 5, 6, 7, 8, 9 and 12, 2024.
START, END = date(2024, 2, 1), date(2024, 2, 12)
REBALANCES = rebalance_sessions(START, END, "daily")
FEB = {day: date(2024, 2, day) for day in (1, 2, 5, 6, 7, 8, 9, 12)}

_PRICE_SCHEMA = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "open": pl.Float64,
    "close": pl.Float64,
    "known_at": pl.Datetime("us", "UTC"),
}


def _closes(
    growth: Mapping[str, float],
    jumps: Mapping[tuple[str, date], float],
    sessions: Sequence[date] = SESSIONS,
) -> dict[tuple[str, date], float]:
    """`(sid, session) -> close`: 100 grown by `growth[sid]` per session, times
    `1 + jump` on a jump session (and every session after it)."""
    out: dict[tuple[str, date], float] = {}
    for sid, rate in growth.items():
        close = 100.0
        for session in sessions:
            close *= 1 + rate
            close *= 1 + jumps.get((sid, session), 0.0)
            out[(sid, session)] = close
    return out


def _prices(closes: Mapping[tuple[str, date], float]) -> pl.DataFrame:
    rows = []
    previous: dict[str, float] = {}
    for (sid, session), close in sorted(closes.items()):
        prior = previous.get(sid, close)
        rows.append((sid, session, (prior + close) / 2, close, session_close(session)))
        previous[sid] = close
    return pl.DataFrame(rows, schema=_PRICE_SCHEMA, orient="row")


def _settings(**sections: dict[str, Any]) -> Settings:
    values: dict[str, Any] = {
        "schedule": {"rebalance_cadence": "daily"},
        "costs": {"per_side_bps": 15.0, "sensitivity_per_side_bps": [0.0]},
    }
    values.update(sections)
    return Settings(_env_file=None, **values)


def _with(settings: Settings, section: str, **updates: Any) -> Settings:
    """`settings` with `section`'s fields replaced, validated as any construction is."""
    values = settings.model_dump()
    values[section] = {**values[section], **updates}
    return Settings(_env_file=None, **values)


def _run(
    settings: Settings, provider: FakeProvider, levels: Sequence[float] = (0.0, 15.0)
) -> dict[float, BacktestResult]:
    return run(settings, provider, START, END, test_engine._handle(), levels, family="momentum")


def _equity(result: BacktestResult) -> dict[date, tuple[float, float | None]]:
    return {
        row.session: (row.equity, row.cash)
        for row in result.equity
        if row.series == STRATEGY_SERIES
    }


def _values(result: BacktestResult) -> dict[tuple[str, date], float]:
    rows = result.position_values.select("security_id", "session", "value").iter_rows()
    return {(sid, session): value for sid, session, value in rows}


def _held(result: BacktestResult, session: date) -> set[str]:
    return {sid for (sid, day), value in _values(result).items() if day == session and value > 0}


def _row(result: BacktestResult, fill: date) -> Any:
    (row,) = [row for row in result.rebalances if row.fill_session == fill]
    return row


# --- the unchanged pins (pass on main; must stay green) ------------------------------


def test_h1_and_twin_fingerprint_canonical_set_and_hash_unchanged() -> None:
    """H1's and the TWIN's fingerprint, canonical set and params hash (the pinned test
    in `test_frozen_defaults.py`, run from here so this file carries the line's pin)."""
    test_frozen_defaults.test_h1_twin_canonical_set_fingerprint_and_hash_unchanged()


def _twin_results(settings: Settings) -> dict[float, BacktestResult]:
    levels = tuple(settings.costs.sensitivity_per_side_bps)
    return run(
        settings,
        test_engine._provider(),
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        levels,
        family="momentum",
    )


def test_twin_run_digests_and_final_equity_unchanged() -> None:
    results = _twin_results(test_engine._twin_settings())
    digests = {level: test_engine._run_digest(result) for level, result in results.items()}
    assert digests == test_engine.TWIN_RUN_DIGESTS
    finals = {level: result.equity[-1].equity for level, result in results.items()}
    assert finals == pytest.approx(test_engine.TWIN_FINAL_EQUITY, rel=1e-12)


#: Two benchmark ids for the reference-metrics run: SPY and MTUM are mandatory series.
_BENCHMARKS = {"SPY": "XSPY", "MTUM": "XMTUM"}
_BENCHMARK_GROWTH = {"XSPY": 0.0005, "XMTUM": 0.0008}

#: SHA-256 over the twin's `metric_rows` (every series, level and key, floats rounded to
#: nine places), taken on `main` at e0c21931 before T209b.
TWIN_REFERENCE_METRICS_SHA256 = "5ec2297df285c2b9785f72832512abdbcaaa4236f20cfe114cab0c53a18f416c"


def _reference_metrics_digest(settings: Settings) -> str:
    base = test_engine._price_rows()
    extra = _prices(_closes(_BENCHMARK_GROWTH, {}, test_engine.SESSIONS))
    provider = FakeProvider(
        prices=pl.concat([base, extra]),
        members={
            session: sorted(test_engine.GROWTH)
            for session in rebalance_sessions(test_engine.T0, test_engine.HISTORY_END)
        },
        benchmarks=_BENCHMARKS,
    )
    levels = sorted({settings.costs.per_side_bps, *settings.costs.sensitivity_per_side_bps})
    results = run(
        settings,
        provider,
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        levels,
        family="momentum",
    )
    rows = metric_rows(results, settings, cadence="month_end", family="momentum")
    payload = sorted(
        (row.series, row.cost_per_side_bps, row.metric, test_engine._rounded(row.value))
        for row in rows
    )
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def test_twin_reference_metrics_unchanged() -> None:
    assert _reference_metrics_digest(test_engine._twin_settings()) == TWIN_REFERENCE_METRICS_SHA256


def test_a_band_of_zero_named_explicitly_is_the_twin_run_to_the_last_bit() -> None:
    """A band of 0 reproduces today's trades exactly: the twin with
    `strategy.rebalance_band = 0.0` and `strategy.weighting = "equal"` named."""
    settings = _with(test_engine._twin_settings(), "strategy", rebalance_band=0.0)
    results = _twin_results(settings)
    digests = {level: test_engine._run_digest(result) for level, result in results.items()}
    assert digests == test_engine.TWIN_RUN_DIGESTS


# --- the no-trade band (fails until T209b applies the band to the targets) -----------

#: A, B, C are the top half of six; B jumps 10% on Feb 7, so at the Feb 7 fill B's drift
#: is 6.45% of its target while A's and C's stay under 3.7% (hand-checked).
BAND_GROWTH = {"A": 0.004, "B": 0.003, "C": 0.002, "D": 0.001, "E": 0.0, "F": -0.001}
BAND_JUMP = ("B", FEB[7])
BAND = 0.05
BAND_CLOSES = _closes(BAND_GROWTH, {BAND_JUMP: 0.10})


def _band_provider() -> FakeProvider:
    """C leaves the universe at the Feb 9 plan, so the Feb 12 fill sells it whole and
    buys D, the next ranked name."""
    members = {
        session: sorted(set(BAND_GROWTH) - ({"C"} if session >= FEB[9] else set()))
        for session in REBALANCES
    }
    return FakeProvider(prices=_prices(BAND_CLOSES), members=members, benchmarks={})


def _band_settings(band: float) -> Settings:
    return _with(_settings(strategy={"top_fraction": 0.5}), "strategy", rebalance_band=band)


def test_band_fixture_preconditions_at_band_zero() -> None:
    """At band 0 the fixture trades on every fill (the drift is rebalanced daily) and
    its targets are A, B, C until C leaves, then A, B, D."""
    result = _run(_band_settings(0.0), _band_provider())[15.0]
    assert all(row.turnover > 0 for row in result.rebalances)
    assert {fill: sorted(t) for fill, t in result.targets.items()} == {
        **{FEB[d]: ["A", "B", "C"] for d in (2, 5, 6, 7, 8, 9)},
        FEB[12]: ["A", "B", "D"],
    }


def test_a_band_suppresses_the_drifts_inside_it() -> None:
    """Fills at Feb 5, 6, 8 and 9: every drift is inside 5% of the target, so no order
    is placed and nothing is paid, at 0 bp and at 15 bp."""
    for result in _run(_band_settings(BAND), _band_provider()).values():
        for day in (5, 6, 8, 9):
            row = _row(result, FEB[day])
            assert row.turnover == 0.0
            assert row.cost_paid == 0.0


def test_a_band_trades_only_the_name_outside_it_back_to_its_target() -> None:
    """Feb 7 fill (0 bp): B, outside the band, is trimmed to exactly a third of equity;
    A and C, inside it, are not traded, so their values are buy-and-hold from Feb 2."""
    result = _run(_band_settings(BAND), _band_provider())[0.0]
    values = _values(result)
    equity, _ = _equity(result)[FEB[7]]
    assert values[("B", FEB[7])] == pytest.approx(equity / 3, rel=1e-12)
    for sid in ("A", "C"):
        growth = BAND_CLOSES[(sid, FEB[7])] / BAND_CLOSES[(sid, FEB[2])]
        assert values[(sid, FEB[7])] == pytest.approx(values[(sid, FEB[2])] * growth, rel=1e-12)
    b_before = values[("B", FEB[6])] * BAND_CLOSES[("B", FEB[7])] / BAND_CLOSES[("B", FEB[6])]
    trimmed = b_before - equity / 3
    assert trimmed > 0
    assert _row(result, FEB[7]).turnover == pytest.approx(trimmed / (2 * equity), rel=1e-9)


def test_a_band_never_holds_back_an_exit_or_an_entry() -> None:
    """Feb 12 fill (0 bp): C, out of the targets, is sold whole and D enters at a third of
    equity; A and B, inside the band, keep their buy-and-hold values."""
    result = _run(_band_settings(BAND), _band_provider())[0.0]
    values = _values(result)
    equity, _ = _equity(result)[FEB[12]]
    assert _held(result, FEB[12]) == {"A", "B", "D"}
    assert values[("D", FEB[12])] == pytest.approx(equity / 3, rel=1e-12)
    for sid in ("A", "B"):
        growth = BAND_CLOSES[(sid, FEB[12])] / BAND_CLOSES[(sid, FEB[9])]
        assert values[(sid, FEB[12])] == pytest.approx(values[(sid, FEB[9])] * growth, rel=1e-12)


# --- hold mode (fails until T209b builds hold membership in the engine) --------------

HOLD_GROWTH = {"A": 0.004, "B": 0.003, "C": 0.002, "D": 0.001}
HOLD_CLOSES = _closes(HOLD_GROWTH, {})


def _hold_provider() -> FakeProvider:
    """Plan 0 (Feb 1) ranks A, B, C, D; plans 1-2 (Feb 2, 5) see only C and D; plans 3+
    (Feb 6 on) see only C. With two target names held three sessions: A and B enter at
    plan 0 and exit at plan 3, where C (the one ranked name) enters alone."""

    def members(index: int) -> list[str]:
        return ["A", "B", "C", "D"] if index == 0 else ["C", "D"] if index < 3 else ["C"]

    return FakeProvider(
        prices=_prices(HOLD_CLOSES),
        members={session: members(i) for i, session in enumerate(REBALANCES)},
        benchmarks={},
    )


def _hold_settings() -> Settings:
    return _with(
        _settings(strategy={"top_fraction": 0.5}),
        "strategy",
        weighting="hold",
        hold_sessions=3,
        hold_target_names=2,
    )


def test_hold_mode_holds_each_entry_for_n_sessions_and_exits_on_session_n() -> None:
    """A and B are held from the Feb 2 fill through Feb 6 although they leave the
    universe after plan 0, and are sold at plan 3's fill (Feb 7), not before or after."""
    result = _run(_hold_settings(), _hold_provider())[0.0]
    for day in (2, 5, 6):
        assert _held(result, FEB[day]) == {"A", "B"}
    for day in (7, 8, 9, 12):
        assert _held(result, FEB[day]) == {"C"}


def test_hold_mode_sizes_entries_at_one_over_target_names_and_never_reweights() -> None:
    """0 bp hand values: A and B are bought at 50,000 each at the Feb 2 close and then
    only drift with their closes (no re-weighting while live)."""
    result = _run(_hold_settings(), _hold_provider())[0.0]
    values = _values(result)
    for sid in ("A", "B"):
        assert values[(sid, FEB[2])] == pytest.approx(50_000.0, rel=1e-12)
        for day in (5, 6):
            growth = HOLD_CLOSES[(sid, FEB[day])] / HOLD_CLOSES[(sid, FEB[2])]
            assert values[(sid, FEB[day])] == pytest.approx(50_000.0 * growth, rel=1e-12)


def test_hold_mode_holds_the_remainder_in_cash() -> None:
    """Feb 7 fill (0 bp): A and B are sold and C, the only name to enter, is bought at
    half the equity; the other half is cash. C is not re-weighted on Feb 8 and 9, and at
    plan 6 (Feb 9) its hold ends and it re-enters at half the Feb 12 equity."""
    result = _run(_hold_settings(), _hold_provider())[0.0]
    equity = _equity(result)
    values = _values(result)
    e7 = math.fsum(
        50_000.0 * HOLD_CLOSES[(sid, FEB[7])] / HOLD_CLOSES[(sid, FEB[2])] for sid in ("A", "B")
    )
    assert equity[FEB[7]][0] == pytest.approx(e7, rel=1e-12)
    assert equity[FEB[7]][1] == pytest.approx(e7 / 2, rel=1e-12)
    assert values[("C", FEB[7])] == pytest.approx(e7 / 2, rel=1e-12)
    for day in (8, 9):
        c_value = e7 / 2 * HOLD_CLOSES[("C", FEB[day])] / HOLD_CLOSES[("C", FEB[7])]
        assert values[("C", FEB[day])] == pytest.approx(c_value, rel=1e-12)
        assert equity[FEB[day]] == pytest.approx((e7 / 2 + c_value, e7 / 2), rel=1e-12)
    e12 = e7 / 2 + e7 / 2 * HOLD_CLOSES[("C", FEB[12])] / HOLD_CLOSES[("C", FEB[7])]
    assert equity[FEB[12]] == pytest.approx((e12, e12 / 2), rel=1e-12)
    assert values[("C", FEB[12])] == pytest.approx(e12 / 2, rel=1e-12)


def test_hold_mode_places_no_order_while_every_entry_is_live() -> None:
    """At 15 bp too: the fills between entries and exits (Feb 5, 6, 8, 9) trade nothing
    and pay nothing; an ulp-sized re-weighting would show here."""
    for result in _run(_hold_settings(), _hold_provider()).values():
        for day in (5, 6, 8, 9):
            row = _row(result, FEB[day])
            assert row.turnover == 0.0
            assert row.cost_paid == 0.0


# --- cash accrual (fails until T209b accrues `rf(s)` under `cash_rate = store`) ------

#: `rates` rows in percent, on the `yield` basis so the hand accruals are round: 3.65%
#: is 0.0001 a calendar day, and 7.30% (known from the Feb 7 close) 0.0002.
_RATE_ROWS = [
    ("DTB3", date(2024, 1, 30), 3.65, date(2024, 1, 31), session_close(date(2024, 1, 31))),
    ("DTB3", date(2024, 2, 6), 7.30, date(2024, 2, 7), session_close(FEB[7])),
]
_RATE_SCHEMA = {
    "series": pl.Utf8,
    "observation_date": pl.Date,
    "value": pl.Float64,
    "vintage_date": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
}
#: The hand growth of cash on each session after T_0 (Feb 1): `1 + y * days / 365`.
CASH_GROWTH = {
    FEB[2]: 1.0001,
    FEB[5]: 1.0003,  # Friday to Monday: three calendar days
    FEB[6]: 1.0001,
    FEB[7]: 1.0002,  # 7.30% is known at the Feb 7 close
    FEB[8]: 1.0002,
    FEB[9]: 1.0002,
    FEB[12]: 1.0006,
}


def _cash_provider(rate_rows: Sequence[tuple[Any, ...]] = tuple(_RATE_ROWS)) -> FakeProvider:
    """An empty universe at every plan, so the book is all cash."""
    return FakeProvider(
        prices=_prices(HOLD_CLOSES),
        members={session: [] for session in REBALANCES},
        benchmarks={},
        rate_rows=pl.DataFrame(list(rate_rows), schema=_RATE_SCHEMA, orient="row"),
    )


def _cash_settings() -> Settings:
    return _settings(
        backtest={"cash_rate": "store"},
        metrics={"risk_free_source": "store", "risk_free_basis": "yield"},
    )


def _hand_cash() -> dict[date, float]:
    cash, out = 100_000.0, {START: 100_000.0}
    for session in REBALANCES[1:]:
        cash *= CASH_GROWTH[session]
        out[session] = cash
    return out


def test_cash_rate_store_accrues_the_fixture_series_hand_product_per_session() -> None:
    expected = _hand_cash()
    for result in _run(_cash_settings(), _cash_provider()).values():
        equity = _equity(result)
        assert sorted(equity) == REBALANCES
        for session, cash in expected.items():
            assert equity[session] == pytest.approx((cash, cash), rel=1e-12)


def test_cash_rate_store_period_returns_are_the_hand_period_products() -> None:
    """Each period's equity return is the product of its sessions' accruals."""
    result = _run(_cash_settings(), _cash_provider())[0.0]
    equity = {session: value for session, (value, _) in _equity(result).items()}
    for previous, session in pairwise(REBALANCES):
        assert equity[session] / equity[previous] - 1 == pytest.approx(
            CASH_GROWTH[session] - 1, rel=1e-9
        )


def test_cash_accrual_reads_no_rate_before_it_is_known() -> None:
    """No look-ahead: the 7.30% row moved to a later `known_at` leaves Feb 2-6 as they
    were and starts the higher accrual at its own close; a row known after the run's
    cutoff changes nothing."""
    later = [
        _RATE_ROWS[0],
        ("DTB3", date(2024, 2, 6), 7.30, date(2024, 2, 9), session_close(FEB[9])),
        ("DTB3", date(2024, 2, 12), 50.0, date(2024, 2, 13), session_close(date(2024, 2, 13))),
    ]
    result = _run(_cash_settings(), _cash_provider(later))[0.0]
    equity = {session: value for session, (value, _) in _equity(result).items()}
    base = _hand_cash()
    for day in (1, 2, 5, 6):
        assert equity[FEB[day]] == pytest.approx(base[FEB[day]], rel=1e-12)
    cash = base[FEB[6]]
    for day, growth in ((7, 1.0001), (8, 1.0001), (9, 1.0002), (12, 1.0006)):
        cash *= growth
        assert equity[FEB[day]] == pytest.approx(cash, rel=1e-12)


def test_cash_rate_zero_earns_nothing() -> None:
    """The default `0.0` is today's behaviour: an all-cash book stays at its capital."""
    result = _run(_settings(), _cash_provider())[0.0]
    assert {value for value, _ in _equity(result).values()} == {100_000.0}
