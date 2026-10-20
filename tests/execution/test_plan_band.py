"""The no-trade band and the hold rule in the paper planner, pinned to the backtest by
one shared fixture (backtest spec amendment 2026-10-18 #1731, "Hold mode and the no-trade
band"; ADR 0023; plan T205).

Locked tests (split trial, phase 1), written before the build against the plan line's
Tests clause:

- on ONE fixture (`_band_plan`, `_band_ledger`) the paper plan (`plan.decisions_from`)
  and the backtest plan (`backtest.portfolio.apply_rebalance_band` then `trades_from`,
  T209a's pure functions, which `engine.py` will call in T209b) produce the same trade
  set under the same `strategy.rebalance_band`;
- on one fixture (`_hold_plan`, `_hold_ledger`) the paper plan places the entries and
  exits `backtest.portfolio.hold_membership` gives for the same state under
  `strategy.weighting = "hold"`;
- a band of 0 and `equal` weighting give rows byte-identical to today's: the
  `decisions_from` answer and `plan_rebalance`'s journaled rows hash to digests taken on
  `main` at 3b261961, before T205.

`config.py` still refuses `strategy.weighting = "hold"` and a non-zero
`strategy.rebalance_band` until T209b (`StrategyConfig._refuse_hold_mode_until_wired`),
and T205's Files do not list it, so the planning tests build their frozen parameters with
`model_copy`, which skips that validator (the T209b lock's `_with` pattern).

The fixtures are hand-sized: every price is $10 and the 0 bp, no-commission costs make a
buy's spendable cash exactly the cash plus the plan's sells.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.backtest import engine
from tradepartner.backtest.costs import Commissions
from tradepartner.backtest.engine import Plan
from tradepartner.backtest.portfolio import (
    apply_rebalance_band,
    drifted_weights,
    hold_membership,
    trades_from,
)
from tradepartner.backtest.provider import GapReading
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import plan as plan_module
from tradepartner.execution import planning
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts, Decisions, decisions_from, is_full_exit
from tradepartner.store import journal

from .test_planning import Env, open_env

T = date(2026, 9, 30)  # rebalance session T_i
S = date(2026, 10, 1)  # the run's session S (T_i's fill session)
PRICE = 10.0
SETTINGS = Settings(_env_file=None)
#: A half-dollar minimum keeps every trade of the band fixture above it at every band.
FROZEN = RiskConfig(min_order_notional=0.5)
COSTS = BuyCosts(per_side_bps=0.0, commissions=Commissions(per_share=0.0, per_order=0.0))
ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
        "known_at": pl.Datetime("us", "UTC"),
    }
)


def price_of(security_id: str) -> float:
    return PRICE


def _assets(names: str) -> dict[str, Asset]:
    return {
        sid: Asset(tradable=True, fractionable=True, status="active", cusip=None) for sid in names
    }


def _plan(targets: Mapping[str, float], scores: Mapping[str, float]) -> Plan:
    members = tuple(sorted(scores))
    return Plan(
        session=T,
        fill_session=S,
        targets=dict(targets),
        n_universe=len(members),
        n_static_listings=0,
        n_excluded_no_history=0,
        gap=GapReading(count_share=0.0, size_share=0.0),
        members=members,
        scores=dict(scores),
        excluded_no_history=(),
        exclusions={"no_history": ()},
    )


def _decide(plan: Plan, ledger: Ledger, **construction: Any) -> Decisions:
    """`decisions_from` on the fixture; `construction` is T205's new keywords
    (`band=`, `hold=`), left out for today's call."""
    return decisions_from(
        plan,
        ledger,
        (),
        _assets("ABCDEFX"),
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=price_of,
        actions_as_of=ACTIONS,
        costs=COSTS,
        **construction,
    )


def _paper_trades(decided: Decisions) -> dict[str, float]:
    """The paper plan's trade set: signed dollars per name with an order to place (a
    buy's planned notional, a trim's planned notional, a full exit's planned quantity at
    the reference price; sells negative)."""
    trades: dict[str, float] = {}
    for d in decided.decisions:
        if d.side is None:
            continue
        if d.planned_notional is not None:
            amount = d.planned_notional
        else:
            assert d.planned_quantity is not None
            amount = d.planned_quantity * price_of(d.security_id)
        trades[d.security_id] = amount if d.side == "buy" else -amount
    return trades


def _by_name(decided: Decisions) -> dict[str, Any]:
    return {d.security_id: d for d in decided.decisions}


def _digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode()).hexdigest()


# --- the shared band fixture -----------------------------------------------------------

#: Four equal targets of the engine's plan (`equal` weighting, top_fraction 0.8 of five).
BAND_TARGETS = dict.fromkeys("ABCD", 0.25)
BAND_SCORES = {"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "X": 1.0}
#: Shares at $10: A $27 (weight 0.27), B $20 (0.20), C $26 (0.26), X $10 (0.10, a member
#: no longer a target); $17 cash; equity $100. D is a target not held.
BAND_SHARES = {"A": 2.7, "B": 2.0, "C": 2.6, "X": 1.0}
BAND_CASH = 17.0
EQUITY = 100.0


def _band_plan() -> Plan:
    return _plan(BAND_TARGETS, BAND_SCORES)


def _band_ledger() -> Ledger:
    return Ledger(positions=dict(BAND_SHARES), cash=BAND_CASH, through=S)


def _backtest_trades(band: float) -> dict[str, float]:
    """The backtest plan on the same fixture: T209a's band over the drifted weights, then
    `trades_from`, in dollars of the same equity."""
    values = {sid: shares * PRICE for sid, shares in BAND_SHARES.items()}
    drifted = drifted_weights(values, BAND_CASH)
    effective = apply_rebalance_band(BAND_TARGETS, drifted, band)
    equity = math.fsum([*values.values(), BAND_CASH])
    return {sid: delta * equity for sid, delta in trades_from(effective, drifted).items()}


def test_band_fixture_preconditions() -> None:
    """Hand check of the fixture: equity $100 and today's (band 0) trades A -$2, B +$5,
    C -$1, D +$25 and X sold whole for $10, as the backtest would trade them too."""
    ledger = _band_ledger()
    marks = dict.fromkeys(BAND_SHARES, PRICE)
    assert ledger.equity(marks) == pytest.approx(EQUITY, rel=1e-15)
    today = _paper_trades(_decide(_band_plan(), ledger))
    expected = {"A": -2.0, "B": 5.0, "C": -1.0, "D": 25.0, "X": -10.0}
    assert today == pytest.approx(expected, rel=1e-12)
    assert _backtest_trades(0.0) == pytest.approx(expected, rel=1e-12)


@pytest.mark.parametrize("band", [0.0, 0.05, 0.1, 0.5])
def test_paper_and_backtest_trade_the_same_set_under_the_same_band(band: float) -> None:
    """The line's pin: one fixture, one `strategy.rebalance_band`, one trade set (names,
    sides and dollars) on both sides."""
    paper = _paper_trades(_decide(_band_plan(), _band_ledger(), band=band))
    backtest = _backtest_trades(band)
    assert set(paper) == set(backtest)
    assert paper == pytest.approx(backtest, rel=1e-12)


def test_a_band_of_ten_percent_trades_the_hand_set() -> None:
    """Band 0.1 (a drift of at most 0.025 of a 0.25 target is left alone): A (drift 0.02)
    and C (0.01) are not traded; they keep 0.53 of equity, so B and D, outside the band,
    share the 0.47 left, scaled by 0.47 / 0.5 to 0.235 each: B buys $3.50 and D $23.50;
    X, out of the targets, is still sold whole (an exit is never held back)."""
    decided = _decide(_band_plan(), _band_ledger(), band=0.1)
    assert _paper_trades(decided) == pytest.approx({"B": 3.5, "D": 23.5, "X": -10.0}, rel=1e-12)
    rows = _by_name(decided)
    assert set(rows) == set("ABCDX")
    for sid, drifted in (("A", 0.27), ("C", 0.26)):
        row = rows[sid]
        assert (row.decision, row.side) == ("skip_zero", None)
        assert row.target_weight == 0.25  # the engine's target, not the banded weight
        assert row.drifted_weight == pytest.approx(drifted, rel=1e-12)
    exit_ = rows["X"]
    assert (exit_.decision, exit_.side, exit_.reason) == ("trade", "sell", "left_targets")
    assert exit_.planned_quantity == pytest.approx(1.0, rel=1e-12)
    for sid, dollars in (("B", 3.5), ("D", 23.5)):
        row = rows[sid]
        assert (row.decision, row.side) == ("trade", "buy")
        # $17 cash + the $10 exit is $27 spendable, exactly the $27 of buys.
        assert row.target_notional == pytest.approx(dollars, rel=1e-12)


def test_a_band_wide_enough_trades_only_the_exit_and_the_entry() -> None:
    """Band 0.5: every held target is inside it, so only D enters (a quarter of equity)
    and X exits; the signals are the engine's, unchanged by the band."""
    banded = _decide(_band_plan(), _band_ledger(), band=0.5)
    assert _paper_trades(banded) == pytest.approx({"D": 25.0, "X": -10.0}, rel=1e-12)
    assert banded.signals == _decide(_band_plan(), _band_ledger()).signals


# --- band 0 and `equal`: byte-identical to today's rows --------------------------------

#: `repr(decisions_from(...))` on the band fixture with today's arguments, on `main` at
#: 3b261961 before T205.
TODAY_DECISIONS_SHA256 = "a636dc5de0d30f8fd3332f4f9dc21602f64f116e89d2f466f7aa6526d727774b"


def test_today_decisions_on_the_band_fixture_are_pinned() -> None:
    """Passes before and after T205: the call with no new keyword is today's plan."""
    assert _digest(_decide(_band_plan(), _band_ledger())) == TODAY_DECISIONS_SHA256


def test_band_zero_and_equal_named_explicitly_are_todays_decisions_byte_for_byte() -> None:
    decided = _decide(_band_plan(), _band_ledger(), band=0.0, hold=None)
    assert _digest(decided) == TODAY_DECISIONS_SHA256


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # StoreProvider compares the frozen calendar with the live one; no `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@pytest.fixture
def env(fixture_store_path: Path, tmp_path: Path) -> Iterator[Env]:
    with open_env(fixture_store_path, tmp_path) as opened:
        yield opened


def _journaled(env: Env) -> list[object]:
    """Every row `plan_rebalance` journals for the plan, ids and stamps included."""
    assert env.window.window_id is not None and env.run.run_id is not None
    return [
        *journal.signals_for(env.conn, env.run.run_id),
        *journal.decisions_for(env.conn, env.window.window_id),
        *journal.plans_for(env.conn, env.window.window_id),
    ]


#: `repr` of the rows `plan_rebalance` journals on `test_planning`'s fixture-store window
#: (H1's shape: momentum, `equal`, band 0), on `main` at 3b261961 before T205.
TODAY_PLAN_ROWS_SHA256 = "e6b60a7d7e2c35988ead9d4e661354325e06f12e497ebc6d921ae526cf9022df"


def test_band_zero_and_equal_plan_rows_are_todays_byte_for_byte(env: Env) -> None:
    """Passes before and after T205: a window frozen with the defaults journals exactly
    today's `signals`, `decisions` and `paper_plans` rows."""
    outcome = env.plan()
    assert outcome.status == "planned"
    assert _digest(_journaled(env)) == TODAY_PLAN_ROWS_SHA256


def _with_strategy(monkeypatch: pytest.MonkeyPatch, **updates: Any) -> None:
    """The window's frozen parameters with `strategy` fields replaced, skipping
    `config.py`'s T209b guard (module docstring)."""
    real = planning.load_frozen

    def frozen(*args: Any, **kwargs: Any) -> Settings:
        params = real(*args, **kwargs)
        part = params.strategy.model_copy(update=updates)
        return params.model_copy(update={"strategy": part})

    monkeypatch.setattr(planning, "load_frozen", frozen)


def _spy_decisions_from(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = planning.decisions_from

    def spy(*args: Any, **kwargs: Any) -> Decisions:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(planning, "decisions_from", spy)
    return calls


def test_planning_passes_the_windows_frozen_band(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """The band the paper plan applies is the window's frozen `strategy.rebalance_band`
    (read from the hypothesis's frozen parameters, never live `Settings`)."""
    _with_strategy(monkeypatch, rebalance_band=0.1)
    calls = _spy_decisions_from(monkeypatch)
    assert env.plan().status == "planned"
    assert len(calls) == 1
    assert calls[0]["band"] == 0.1
    assert calls[0].get("hold") is None


def test_planning_passes_band_zero_for_a_default_window(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy_decisions_from(monkeypatch)
    assert env.plan().status == "planned"
    assert [(c["band"], c.get("hold")) for c in calls] == [(0.0, None)]


def test_a_profitability_window_reads_its_own_section_not_strategy(
    fixture_store_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The band is read from the family's own construction section, as `engine._plan`
    reads `top_fraction` and `weighting`; `profitability` has no band key yet, so its band
    is 0 whatever `strategy.rebalance_band` holds."""
    with open_env(fixture_store_path, tmp_path, "profitability") as opened:
        _with_strategy(monkeypatch, rebalance_band=0.1)
        calls = _spy_decisions_from(monkeypatch)
        assert opened.plan().status == "planned"
        assert [c["band"] for c in calls] == [0.0]


def test_a_hold_window_is_refused_before_the_engine_plans(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Until the window's hold entry state has a decided source (#1832),
    a frozen `strategy.weighting = "hold"` is refused with `PlanTrialError` naming the key,
    before `engine.plan` is called and with no row written: never an equal-weight plan
    under a hold label."""
    _with_strategy(monkeypatch, weighting="hold", hold_sessions=3, hold_target_names=2)
    planned: list[object] = []
    real = engine.plan

    def recording(*args: Any, **kwargs: Any) -> Plan:
        planned.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "plan", recording)
    before = env.counts()
    with pytest.raises(planning.PlanTrialError, match=r"strategy\.weighting"):
        env.plan()
    assert planned == []
    assert env.counts() == before


# --- hold: the same entries and exits as the backtest's hold membership ----------------

#: The engine's `equal` targets (top 0.6 of five) are B, D and E; under `hold` the paper
#: plan must not read them.
HOLD_TARGETS = dict.fromkeys("BDE", 1 / 3)
#: Ranked B, D, E, A, F. C, held, has left the universe.
HOLD_SCORES = {"B": 5.0, "D": 4.0, "E": 3.0, "A": 2.0, "F": 1.0}
#: Held at $10: A $100 and B $120 (both entered at ordinal 0), C $80 (entered at 2); no
#: cash; equity $300.
HOLD_SHARES = {"A": 10.0, "B": 12.0, "C": 8.0}
HOLD_ENTRIES = {"A": 0, "B": 0, "C": 2}
ORDINAL = 3
HOLD_SESSIONS = 3
HOLD_TARGET_NAMES = 3


def _hold_plan() -> Plan:
    return _plan(HOLD_TARGETS, HOLD_SCORES)


def _hold_ledger() -> Ledger:
    return Ledger(positions=dict(HOLD_SHARES), cash=0.0, through=S)


def _hold_book() -> Any:
    # Looked up at call time, so only the hold tests fail while `HoldBook` is missing.
    return plan_module.HoldBook(
        entries=dict(HOLD_ENTRIES),
        ordinal=ORDINAL,
        hold_sessions=HOLD_SESSIONS,
        hold_target_names=HOLD_TARGET_NAMES,
        top_fraction=0.6,
    )


def _backtest_membership() -> Any:
    ranked = sorted(HOLD_SCORES, key=lambda sid: (-HOLD_SCORES[sid], sid))
    return hold_membership(
        ranked, HOLD_ENTRIES, ORDINAL, HOLD_SESSIONS, HOLD_TARGET_NAMES, top_fraction=0.6
    )


def test_hold_fixture_backtest_membership_by_hand() -> None:
    """At ordinal 3 with a 3-session hold, A and B (entered at 0) exit and C (entered at
    2) is live; two places are free, so B and D, the best names not live, enter: B exits
    and re-enters (its hold restarts)."""
    membership = _backtest_membership()
    assert membership.exiting == ("A", "B")
    assert membership.entering == ("B", "D")
    assert membership.held == ("C", "B", "D")
    assert dict(membership.entries) == {"C": 2, "B": 3, "D": 3}


def test_paper_hold_plan_enters_and_exits_as_the_backtest() -> None:
    """The line's pin: the paper plan's new entries are the backtest's `entering` less the
    held names, its full exits the backtest's `exiting` less the re-entries, and nothing
    else is decided (E, an engine target, is neither held nor entering)."""
    membership = _backtest_membership()
    held = set(HOLD_SHARES)
    rows = _by_name(_decide(_hold_plan(), _hold_ledger(), hold=_hold_book()))
    assert set(rows) == held | set(membership.entering)
    entries = {sid for sid, d in rows.items() if d.side == "buy" and sid not in held}
    exits = {sid for sid, d in rows.items() if d.side == "sell" and d.planned_quantity is not None}
    assert entries == set(membership.entering) - held
    assert exits == set(membership.exiting) - set(membership.entering)


def test_paper_hold_plan_rows_by_hand() -> None:
    """Each entry is a third of the $300 equity: D buys $100; B, re-entering at $100 from
    $120, trims $20; A, whose hold ended, is sold whole (10 shares, `left_targets`); C,
    live although out of the universe, is not re-weighted (`skip_zero`), never sold as
    `left_universe`."""
    decided = _decide(_hold_plan(), _hold_ledger(), hold=_hold_book())
    rows = _by_name(decided)
    assert _paper_trades(decided) == pytest.approx({"A": -100.0, "B": -20.0, "D": 100.0}, rel=1e-12)
    a, b, c, d = rows["A"], rows["B"], rows["C"], rows["D"]
    assert (a.decision, a.side, a.reason) == ("trade", "sell", "left_targets")
    assert a.planned_quantity == pytest.approx(10.0, rel=1e-12)
    assert (b.decision, b.side, b.reason) == ("trade", "sell", None)
    assert b.planned_notional == pytest.approx(20.0, rel=1e-12)
    assert (c.decision, c.side) == ("skip_zero", None)
    assert (d.decision, d.side) == ("trade", "buy")
    # Spendable is the $100 exit plus the $20 trim, above the $100 buy.
    assert d.target_notional == pytest.approx(100.0, rel=1e-12)


# --- a held name with no read-session bar is kept (spec amendment 2026-10-20, #2023; T253) ---
# Hand book (zero costs, $10): equity 100,000.00, cash 0. A (kept) 1,000 sh = 10,000 (0.10);
# B and C 4,500 sh = 45,000 each (0.45). Targets B 0.5, D 0.5; C has left the targets.


def _kept_book(*, kept: tuple[str, ...] = ("A",)) -> tuple[Plan, Ledger]:
    plan = _plan({"B": 0.5, "D": 0.5}, {"B": 0.4, "C": 0.1, "D": 0.3})
    plan = replace(plan, exclusions={"no_history": (), "no_t0_bar": kept})
    ledger = Ledger(positions={"A": 1000.0, "B": 4500.0, "C": 4500.0}, cash=0.0, through=S)
    return plan, ledger


def test_kept_name_at_band_0_is_skip_zero_and_the_others_share_the_rest() -> None:
    plan, ledger = _kept_book()
    got = {d.security_id: d for d in _decide(plan, ledger).decisions}
    a = got["A"]
    assert (a.decision, a.target_weight, a.drifted_weight) == ("skip_zero", 0.0, 0.10)
    assert a.side is None and a.reason is None and a.planned_quantity is None
    assert not is_full_exit(a)
    b = got["B"]
    assert b.side is None and abs(b.target_weight - 0.5) < 1e-12
    assert abs(0.5 * 0.9 - b.drifted_weight) < 0.01  # delta under 0.01
    c = got["C"]
    assert (c.decision, c.side, c.reason) == ("trade", "sell", "left_targets")
    assert c.planned_quantity is not None and c.planned_quantity * PRICE == pytest.approx(45_000.0)
    d = got["D"]
    assert (d.decision, d.side) == ("trade", "buy")
    assert d.planned_notional == pytest.approx(45_000.0)


def test_the_same_book_without_the_candidate_flag_sells_a_today() -> None:
    plan, ledger = _kept_book(kept=())
    got = {d.security_id: d for d in _decide(plan, ledger).decisions}
    assert (got["A"].decision, got["A"].side, got["A"].reason) == ("trade", "sell", "left_universe")
    assert got["A"].planned_quantity == 1000.0
    assert got["B"].planned_notional == pytest.approx(5_000.0)
    assert got["D"].planned_notional == pytest.approx(50_000.0)


def test_kept_name_at_band_0_2_counts_b_inside_the_band() -> None:
    plan, ledger = _kept_book()
    got = {d.security_id: d for d in _decide(plan, ledger, band=0.2).decisions}
    assert got["A"].decision == "skip_zero" and got["A"].drifted_weight == 0.10
    assert got["B"].side is None  # held within the band: H = 0.45
    assert got["D"].planned_notional == pytest.approx(45_000.0)
    assert got["C"].reason == "left_targets"


def test_the_helper_returns_h_and_the_scaled_targets() -> None:
    drifted = {"A": 0.10, "B": 0.45, "C": 0.45}
    targets = {"B": 0.5, "D": 0.5}
    at_0 = plan_module.kept_effective_weights(targets, drifted, 0.0, {"A"})
    assert at_0 == {"B": pytest.approx(0.45), "D": pytest.approx(0.45)}
    at_band = plan_module.kept_effective_weights(targets, drifted, 0.2, {"A"})
    assert at_band == {"B": 0.45, "D": pytest.approx(0.45)}  # B at its current weight, D 0.5*0.9
    # With nothing kept it is `apply_rebalance_band` exactly.
    for band in (0.0, 0.2):
        assert plan_module.kept_effective_weights(
            targets, drifted, band, frozenset()
        ) == apply_rebalance_band(targets, drifted, band)


def test_a_kept_weight_of_one_or_more_zeroes_every_traded_target() -> None:
    drifted = {"A": 1.0}
    assert plan_module.kept_effective_weights({"B": 0.5, "D": 0.5}, drifted, 0.0, {"A"}) == {}
    plan = replace(
        _plan({"B": 0.5, "D": 0.5}, {"B": 0.4, "D": 0.3}),
        exclusions={"no_t0_bar": ("A",)},
    )
    ledger = Ledger(positions={"A": 5000.0}, cash=0.0, through=S)
    got = {d.security_id: d for d in _decide(plan, ledger).decisions}
    assert got["A"].decision == "skip_zero"
    assert all(d.side is None for d in got.values())  # nothing bought


def test_a_kept_name_under_long_short_raises() -> None:
    with pytest.raises(ValueError, match="long_only"):
        plan_module.kept_effective_weights({"B": 0.5}, {"A": 0.1}, 0.0, {"A"}, side="long_short")


def test_an_empty_kept_set_is_todays_decisions() -> None:
    plan, ledger = _kept_book(kept=())
    bare = replace(plan, exclusions={"no_history": ()})
    assert _decide(plan, ledger).decisions == _decide(bare, ledger).decisions
    assert _decide(plan, ledger, band=0.2).decisions == _decide(bare, ledger, band=0.2).decisions
