"""Spread, impact and ADV at every fill the engine books (backtest spec amendment
2026-10-18 #1731, "Cost terms", and amendment #1792 item 3; plan T210c).

Locked tests (split trial, phase 1), written before the build against the plan line's
Tests clause. The cost of a trade of notional `n` on a name with ADV `adv` is
`n * (per_side_bps + spread_bps) / 1e4 + n * impact_coeff * sqrt(n / adv)` (no
commissions here); ADV is the median of raw `close * volume` over the traded bars of the
`costs.adv_sessions` sessions ending at the plan session T_i, from the step's raw read.

"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import polars as pl
import pytest

from backtest import test_engine, test_frozen_defaults
from backtest.fake_provider import FakeProvider
from tradepartner.backtest.engine import STRATEGY_SERIES, BacktestResult, run
from tradepartner.backtest.results import metric_rows
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import CostsConfig, Settings

# --- fixture ---------------------------------------------------------------------------

HISTORY_START, HISTORY_END = date(2022, 12, 1), date(2024, 5, 31)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]
T0, T1, T4 = date(2024, 1, 31), date(2024, 2, 29), date(2024, 5, 31)
F0 = fill_session(T0)  # 2024-02-01
#: A and B are the top half (targets at T0); the benchmarks are XSPY and XMTUM.
GROWTH = {"A": 0.004, "B": 0.003, "C": 0.001, "D": -0.001, "XSPY": 0.0005, "XMTUM": 0.0008}
#: Shares traded per session (constant), so dollar volume grows with the close.
VOLUME = {"A": 200_000, "B": 150_000, "C": 100_000, "D": 50_000, "XSPY": 5e6, "XMTUM": 1e6}
BENCHMARKS = {"SPY": "XSPY", "MTUM": "XMTUM"}
MEMBERS = ["A", "B", "C", "D"]

PER_SIDE, SPREAD, IMPACT, ADV_SESSIONS = 15.0, 5.0, 0.1, 20
CAPITAL = 100_000.0

_SCHEMA = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "open": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "known_at": pl.Datetime("us", "UTC"),
}


def _close(sid: str, session: date) -> float:
    return 100.0 * (1 + GROWTH[sid]) ** (SESSIONS.index(session) + 1)


def _bars(revisions: Sequence[tuple[str, date, float, date]] = ()) -> pl.DataFrame:
    """Every name's bar on every session, known at its close; each revision
    `(sid, session, volume, known_on)` adds a later row for that bar with the same
    prices and a new volume, known at the close of `known_on`."""
    rows = []
    for sid in GROWTH:
        for session in SESSIONS:
            close = _close(sid, session)
            previous = close / (1 + GROWTH[sid])
            opened = (previous + close) / 2
            rows.append((sid, session, opened, close, VOLUME[sid], session_close(session)))
    for sid, session, volume, known_on in revisions:
        close = _close(sid, session)
        opened = (close / (1 + GROWTH[sid]) + close) / 2
        rows.append((sid, session, opened, close, volume, session_close(known_on)))
    return pl.DataFrame(rows, schema=_SCHEMA, orient="row")


#: B's listing ends on Feb 15, known that day: a forced exit inside step 0.
B_END = date(2024, 2, 15)
_LISTING_SCHEMA = {
    "security_id": pl.Utf8,
    "status": pl.Utf8,
    "end_session": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
}


def _provider(
    revisions: Sequence[tuple[str, date, float, date]] = (), *, delist_b: bool = False
) -> FakeProvider:
    listing_ends = pl.DataFrame(
        [("B", "delisted", B_END, session_close(B_END))] if delist_b else [],
        schema=_LISTING_SCHEMA,
        orient="row",
    )
    return FakeProvider(
        prices=_bars().drop("volume"),
        raw=_bars(revisions),
        members={session: MEMBERS for session in rebalance_sessions(T0, HISTORY_END)},
        benchmarks=BENCHMARKS,
        listing_ends_rows=listing_ends,
    )


def _settings(**costs: Any) -> Settings:
    values: dict[str, Any] = {
        "per_side_bps": PER_SIDE,
        "sensitivity_per_side_bps": [0.0],
        **costs,
    }
    return Settings(_env_file=None, strategy={"top_fraction": 0.5}, costs=values)


def _termed(**overrides: Any) -> Settings:
    terms = {"spread_bps": SPREAD, "impact_coeff": IMPACT, "adv_sessions": ADV_SESSIONS}
    return _settings(**(terms | overrides))


def _run(
    settings: Settings, provider: FakeProvider, *, end: date = T1, levels: Sequence[float] = (15.0,)
) -> dict[float, BacktestResult]:
    return run(settings, provider, T0, end, test_engine._handle(), levels, family="momentum")


# --- hand arithmetic -------------------------------------------------------------------


def hand_adv(
    sid: str, through: date, volumes: Mapping[date, float] | None = None, n: int = ADV_SESSIONS
) -> float:
    """Median raw close x volume over the `n` sessions ending at `through`."""
    window = [s for s in SESSIONS if s <= through][-n:]
    volume = volumes or {}
    return statistics.median(_close(sid, s) * volume.get(s, VOLUME[sid]) for s in window)


def hand_cost(notional: float, adv: float, *, per_side: float = PER_SIDE) -> float:
    return notional * (per_side + SPREAD) / 1e4 + notional * IMPACT * math.sqrt(notional / adv)


def hand_buy(cash: float, adv: float) -> float:
    """The largest `n` with `n + hand_cost(n) <= cash` (bisection to the last bits)."""
    low, high = 0.0, cash
    for _ in range(200):
        mid = (low + high) / 2
        low, high = (mid, high) if mid + hand_cost(mid, adv) <= cash else (low, mid)
    return low


def _values(result: BacktestResult) -> dict[tuple[str, date], float]:
    rows = result.position_values.select("security_id", "session", "value").iter_rows()
    return {(sid, session): value for sid, session, value in rows}


def _series(result: BacktestResult, series: str) -> dict[date, float]:
    return {row.session: row.equity for row in result.equity if row.series == series}


# --- the unchanged pins at the frozen defaults (pass on main; must stay green) ---------


def test_h1_and_twin_fingerprint_canonical_set_and_hash_unchanged() -> None:
    test_frozen_defaults.test_h1_twin_canonical_set_fingerprint_and_hash_unchanged()


def _twin_digests(settings: Settings) -> dict[float, str]:
    results = run(
        settings,
        test_engine._provider(),
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        tuple(settings.costs.sensitivity_per_side_bps),
        family="momentum",
    )
    return {level: test_engine._run_digest(result) for level, result in results.items()}


def test_twin_run_digests_and_final_equity_unchanged() -> None:
    assert _twin_digests(test_engine._twin_settings()) == test_engine.TWIN_RUN_DIGESTS


def test_the_twin_with_the_three_keys_named_at_their_defaults_is_byte_identical() -> None:
    twin = test_engine._twin_settings()
    named = twin.model_copy(
        update={
            "costs": CostsConfig(
                **(
                    twin.costs.model_dump()
                    | {"spread_bps": 0.0, "impact_coeff": 0.0, "adv_sessions": 20}
                )
            )
        }
    )
    assert _twin_digests(named) == test_engine.TWIN_RUN_DIGESTS


#: SHA-256 over this fixture's `metric_rows` at the defaults (every series, level and
#: key, floats rounded to nine places), taken on `main` at e0c21931 before T210c.
REFERENCE_METRICS_SHA256 = "eeee0345ec86dd6758005f6c3ad20a3c0910108d1b5699e5fdaea638ab174865"


def _metrics_digest(settings: Settings) -> str:
    levels = sorted({settings.costs.per_side_bps, *settings.costs.sensitivity_per_side_bps})
    results = _run(settings, _provider(), end=T4, levels=levels)
    rows = metric_rows(results, settings, cadence="month_end", family="momentum")
    payload = sorted(
        (row.series, row.cost_per_side_bps, row.metric, test_engine._rounded(row.value))
        for row in rows
    )
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def test_reference_metrics_at_the_defaults_unchanged() -> None:
    default = Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        costs={"per_side_bps": PER_SIDE, "sensitivity_per_side_bps": [0.0, 30.0, 100.0]},
    )
    assert _metrics_digest(default) == REFERENCE_METRICS_SHA256


# --- the first step's costs by hand (fail until T210c wires the terms) -----------------


def test_target_buys_pay_the_hand_cost_over_their_adv_at_t0() -> None:
    """A then B (security_id order), each to half of 100,000: A's buy is the largest
    notional whose cost fits 50,000; B's the same from the 50,000 left."""
    result = _run(_termed(), _provider())[15.0]
    adv_a, adv_b = hand_adv("A", T0), hand_adv("B", T0)
    n_a = hand_buy(CAPITAL / 2, adv_a)
    n_b = hand_buy(CAPITAL - n_a - hand_cost(n_a, adv_a), adv_b)
    values = _values(result)
    assert values[("A", F0)] == pytest.approx(n_a, rel=1e-9)
    assert values[("B", F0)] == pytest.approx(n_b, rel=1e-9)
    row = result.rebalances[0]
    assert row.cost_paid == pytest.approx(hand_cost(n_a, adv_a) + hand_cost(n_b, adv_b), rel=1e-9)


def test_a_forced_exit_pays_the_hand_cost_over_the_names_adv_at_t0() -> None:
    """B, delisted on Feb 15, is sold at its Feb 15 value in step 0 and pays the full
    cost model over its ADV at T_0."""
    result = _run(_termed(), _provider(delist_b=True))[15.0]
    adv_a, adv_b = hand_adv("A", T0), hand_adv("B", T0)
    n_a = hand_buy(CAPITAL / 2, adv_a)
    n_b = hand_buy(CAPITAL - n_a - hand_cost(n_a, adv_a), adv_b)
    sold = n_b * _close("B", B_END) / _close("B", F0)
    row = result.rebalances[0]
    assert row.n_delisting_exits == 1
    expected = hand_cost(n_a, adv_a) + hand_cost(n_b, adv_b) + min(hand_cost(sold, adv_b), sold)
    assert row.cost_paid == pytest.approx(expected, rel=1e-9)


def test_the_benchmark_buy_pays_the_full_cost_model_over_its_own_adv() -> None:
    """The benchmark's one buy at F_0: lower than the untermed run's notional by the hand
    value, and its equity over the untermed run's is one constant from F_0 on."""
    termed = _run(_termed(), _provider(), end=T4)[15.0]
    untermed = _run(_settings(), _provider(), end=T4)[15.0]
    for name, sid in BENCHMARKS.items():
        plain = CAPITAL / (1 + PER_SIDE / 1e4)
        bought = hand_buy(CAPITAL, hand_adv(sid, T0))
        with_terms, without = _series(termed, name), _series(untermed, name)
        assert without[F0] == pytest.approx(plain, rel=1e-9)
        assert with_terms[F0] == pytest.approx(bought, rel=1e-9)
        assert without[F0] - with_terms[F0] == pytest.approx(plain - bought, rel=1e-6)
        ratios = [with_terms[s] / without[s] for s in sorted(without) if s >= F0]
        assert ratios == pytest.approx([bought / plain] * len(ratios), rel=1e-9)


# --- no look-ahead through ADV ---------------------------------------------------------


def test_a_volume_revision_known_by_the_steps_read_enters_adv_as_it_enters_the_fill() -> None:
    """A's volume on Jan 3, the first session of the window ending at T_0, revised to
    10x and known on Feb 15 (after the plan's read, before the step's read at
    close(T_1)): ADV moves with it (the bar leaves the low half of the window)."""
    revised = date(2024, 1, 3)
    assert revised == [s for s in SESSIONS if s <= T0][-ADV_SESSIONS]
    result = _run(_termed(), _provider([("A", revised, 10 * VOLUME["A"], B_END)]))[15.0]
    adv_a = hand_adv("A", T0, {revised: 10 * VOLUME["A"]})
    assert adv_a != hand_adv("A", T0)
    n_a = hand_buy(CAPITAL / 2, adv_a)
    assert _values(result)[("A", F0)] == pytest.approx(n_a, rel=1e-9)


def test_a_planted_volume_revision_at_or_after_the_fill_never_enters_adv() -> None:
    """Volume revisions on F_0 and on the session after it (known by the step's read)
    leave step 0's costs and fills exactly as they were."""
    planted = [
        ("A", F0, 100 * VOLUME["A"], B_END),
        ("B", date(2024, 2, 2), 100 * VOLUME["B"], B_END),
        ("XSPY", F0, 100 * VOLUME["XSPY"], B_END),
    ]
    base = _run(_termed(), _provider())[15.0]
    moved = _run(_termed(), _provider(planted))[15.0]
    assert moved.rebalances[0].cost_paid == base.rebalances[0].cost_paid
    assert _values(moved)[("A", F0)] == _values(base)[("A", F0)]
    assert _series(moved, "SPY")[F0] == _series(base, "SPY")[F0]


# --- monotone in the impact coefficient ------------------------------------------------


def test_a_higher_impact_coefficient_never_raises_net_cagr() -> None:
    cagrs = []
    for impact in (0.0, 0.05, 0.1, 0.5):
        settings = _termed(impact_coeff=impact)
        results = _run(settings, _provider(), end=T4, levels=(0.0, 15.0))
        rows = metric_rows(results, settings, cadence="month_end", family="momentum")
        (cagr,) = [
            r.value
            for r in rows
            if r.series == STRATEGY_SERIES and r.cost_per_side_bps == 15.0 and r.metric == "cagr"
        ]
        cagrs.append(cagr)
    assert cagrs == sorted(cagrs, reverse=True)
    assert cagrs[0] > cagrs[-1]
