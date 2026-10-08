"""Decisions from a plan (Phase 4 spec Definitions > Decision, reqs 3, 7 step 6, 9; plan T53b).

Hand-built plans and ledgers, one case per decision kind and reason: the full
exits (`left_targets`, `left_universe`, `exclude_name`), notional trades, both
override kinds, `skip_delisted`, `skip_below_minimum`, plan-time `dust` (with
ADR 0010's 2026-09-30 whole-share full-exit rule), `whole_share` from the
assets read, the forced-exit exclusion, the `signals` rows, the buy targets and
the live-capital count. The no-look-ahead boundaries: the plan side comes only
from the close(T_i) `Plan`, and the split view only from rows known at
close(S-1). An AST check keeps every adapter out of `execution/plan.py`.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.backtest.costs import Commissions, buy_notional_after_costs
from tradepartner.backtest.engine import Plan
from tradepartner.backtest.provider import GapReading
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import (
    BuyCosts,
    Decision,
    Decisions,
    Signal,
    decisions_from,
    is_full_exit,
    remainder,
)
from tradepartner.store.journal import DecisionRow, OverrideRow, SignalRow

PLAN_PY = Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "execution" / "plan.py"

T = date(2026, 9, 30)  # rebalance session T_i (a month-end)
S = date(2026, 10, 1)  # its fill session, the run's S; S-1 is T
LATER = date(2026, 10, 2)  # a session after S
FROZEN = RiskConfig()  # min_order_notional 1.0, whole_share_price_buffer 0.02
SETTINGS = Settings(_env_file=None)  # paper.live_capital_reference 100.0
COSTS = BuyCosts(per_side_bps=10.0, commissions=Commissions(per_share=0.0, per_order=0.0))
PRICES = {"A": 50.0, "B": 20.0, "C": 100.0, "D": 30.0, "E": 5.0, "X": 10.0, "W": 100.0}
STAMP = datetime(2026, 9, 30, 21, tzinfo=UTC)


def price_of(security_id: str) -> float:
    return PRICES[security_id]


def _plan(
    targets: Mapping[str, float] | None = None,
    scores: Mapping[str, float] | None = None,
    members: tuple[str, ...] = ("A", "B", "C", "D", "E"),
    excluded: tuple[str, ...] = ("E",),
    exclusions: Mapping[str, tuple[str, ...]] | None = None,
) -> Plan:
    scores = dict(scores or {"A": 0.5, "B": 0.4, "C": 0.1, "D": -0.2})
    # Momentum's one declared reason by default, as every plan built here was before
    # T127b (#1209) generalised `Plan.exclusions` to every family's declared reasons.
    exclusions = dict(exclusions) if exclusions is not None else {"no_history": excluded}
    return Plan(
        session=T,
        fill_session=S,
        targets=dict(targets if targets is not None else {"A": 0.5, "B": 0.5}),
        n_universe=len(members),
        n_static_listings=0,
        n_excluded_no_history=len(excluded),
        gap=GapReading(count_share=0.0, size_share=0.0),
        members=members,
        scores=scores,
        excluded_no_history=excluded,
        exclusions=exclusions,
    )


def _ledger(positions: Mapping[str, float], cash: float = 1000.0) -> Ledger:
    return Ledger(positions=dict(positions), cash=cash, through=S)


def _assets(*whole_share: str) -> dict[str, Asset]:
    return {
        sid: Asset(tradable=True, fractionable=sid not in whole_share, status="active", cusip=None)
        for sid in PRICES
    }


def _actions(*rows: tuple[str, date, float, datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "security_id": sid,
                "action_type": "split",
                "ex_date": ex_date,
                "ratio_or_amount": ratio,
                "known_at": known_at,
            }
            for sid, ex_date, ratio, known_at in rows
        ],
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        },
    )


def _override(
    kind: str, security_id: str | None, *, override_id: int, session: date | None = T
) -> OverrideRow:
    return OverrideRow(
        override_id=override_id,
        window_id=1,
        made_at=STAMP,
        rebalance_session=session,
        security_id=security_id,
        kind=kind,
        reason="the owner's reason, long enough",
        known_at=STAMP,
        ingested_at=STAMP,
    )


def _run(
    plan: Plan | None = None,
    ledger: Ledger | None = None,
    *,
    overrides: tuple[OverrideRow, ...] = (),
    assets: Mapping[str, Asset] | None = None,
    listings_at: Mapping[str, date | None] | None = None,
    open_forced_exits: frozenset[str] = frozenset(),
    frozen: RiskConfig = FROZEN,
    settings: Settings = SETTINGS,
    actions: pl.DataFrame | None = None,
    costs: BuyCosts = COSTS,
) -> Decisions:
    return decisions_from(
        plan if plan is not None else _plan(),
        ledger if ledger is not None else _ledger({"A": 10.0, "C": 5.0, "X": 1.0}),
        overrides,
        assets if assets is not None else _assets(),
        listings_at or {},
        open_forced_exits,
        frozen,
        settings,
        price_of=price_of,
        actions_as_of=actions if actions is not None else _actions(),
        costs=costs,
    )


def _by_name(result: Decisions) -> dict[str, Decision]:
    names = [d.security_id for d in result.decisions]
    assert len(names) == len(set(names)), "one decision per name"
    return {d.security_id: d for d in result.decisions}


# --- the base case, by hand ----------------------------------------------------------
# Held A 10 x 50 = 500, C 5 x 100 = 500, X 1 x 10 = 10; cash 1000; equity 2010.
EQUITY = 2010.0


def test_one_decision_per_held_or_target_name_with_its_kind_and_reason() -> None:
    got = _by_name(_run())
    assert set(got) == {"A", "B", "C", "X"}
    a, b, c, x = got["A"], got["B"], got["C"], got["X"]
    # A: a target held below its weight, bought for target minus drifted weight.
    assert (a.decision, a.side, a.reason) == ("trade", "buy", None)
    assert a.target_weight == 0.5
    assert a.drifted_weight == pytest.approx(500 / EQUITY)
    assert a.planned_notional == pytest.approx((0.5 - 500 / EQUITY) * EQUITY)
    assert a.planned_quantity is None
    # B: a target not held.
    assert (b.decision, b.side, b.reason) == ("trade", "buy", None)
    assert (b.drifted_weight, b.planned_notional) == (0.0, pytest.approx(0.5 * EQUITY))
    # C: held, in the universe, out of the targets: sold whole by quantity.
    assert (c.decision, c.side, c.reason) == ("trade", "sell", "left_targets")
    assert (c.planned_quantity, c.planned_notional, c.target_weight) == (5.0, None, 0.0)
    # X: held, outside the universe: sold whole by quantity too.
    assert (x.decision, x.side, x.reason) == ("trade", "sell", "left_universe")
    assert (x.planned_quantity, x.planned_notional) == (1.0, None)
    for d in got.values():
        assert d.rebalance_session == T
        assert d.whole_share is False
        assert d.override_id is None


def test_buy_targets_follow_the_definitions_formula() -> None:
    """Target = planned notional x min(1, spendable / sum of planned buys), spendable
    from `buy_notional_after_costs` on cash plus every plan sell at the price."""
    got = _by_name(_run())
    buys = [got["A"], got["B"]]
    total = sum(d.planned_notional or 0.0 for d in buys)
    for d in buys:
        spendable = buy_notional_after_costs(
            1000.0 + 500.0 + 10.0,
            COSTS.per_side_bps,
            COSTS.commissions,
            price=PRICES[d.security_id],
        )
        assert spendable < total  # the cost reserve binds
        want = (d.planned_notional or 0.0) * min(1.0, spendable / total)
        assert d.target_notional == pytest.approx(want)
    for d in (got["C"], got["X"]):
        assert d.target_notional is None


def test_a_buy_target_never_exceeds_its_planned_notional() -> None:
    # Targets summing to one half: the buys need half the cash, so no reserve binds.
    rich = _run(_plan(targets={"A": 0.25, "B": 0.25}), _ledger({"A": 10.0}, cash=1_000_000.0))
    for d in rich.decisions:
        if d.side == "buy":
            assert d.target_notional == pytest.approx(d.planned_notional)


def test_a_trim_is_a_notional_sell_of_target_minus_drifted_weight() -> None:
    # A at 30 x 50 = 1500 of equity 1000 + 1500 = 2500: drifted 0.6, target 0.5.
    got = _by_name(_run(_plan(targets={"A": 0.5, "B": 0.5}), _ledger({"A": 30.0})))
    a = got["A"]
    assert (a.decision, a.side, a.reason) == ("trade", "sell", None)
    assert a.planned_notional == pytest.approx(0.1 * 2500.0)
    assert (a.planned_quantity, a.target_notional) == (None, None)


def test_signals_rank_the_scored_members_and_list_the_excluded() -> None:
    signals = {s.security_id: s for s in _run().signals}
    assert set(signals) == {"A", "B", "C", "D", "E"}
    assert (signals["A"].reason, signals["A"].rank, signals["A"].score) == ("selected", 1, 0.5)
    assert (signals["B"].reason, signals["B"].rank) == ("selected", 2)
    assert (signals["C"].reason, signals["C"].rank) == ("below_cut", 3)
    assert (signals["D"].reason, signals["D"].rank, signals["D"].score) == ("below_cut", 4, -0.2)
    assert (signals["E"].reason, signals["E"].rank, signals["E"].score) == (
        "excluded_no_history",
        None,
        None,
    )
    assert all(s.rebalance_session == T for s in signals.values())


def test_equal_scores_rank_by_security_id() -> None:
    plan = _plan(targets={"B": 1.0}, scores={"A": 0.2, "B": 0.2}, members=("A", "B"), excluded=())
    signals = {s.security_id: s for s in _run(plan, _ledger({})).signals}
    assert (signals["A"].rank, signals["B"].rank) == (1, 2)
    assert (signals["A"].reason, signals["B"].reason) == ("below_cut", "selected")


def test_rows_carry_the_run_and_stamps() -> None:
    result = _run()
    stamp = datetime(2026, 10, 1, 12, tzinfo=UTC)
    rows = [
        d.row(run_id=7, known_at=stamp, ingested_at=stamp, book_id="main") for d in result.decisions
    ]
    assert all(isinstance(r, DecisionRow) and r.run_id == 7 for r in rows)
    assert all(r.decision_id is None and r.known_at == stamp for r in rows)
    [c] = [r for r in rows if r.security_id == "C"]
    assert (c.decision, c.reason, c.planned_quantity) == ("trade", "left_targets", 5.0)
    signal_rows = [s.row(run_id=7, known_at=stamp, ingested_at=stamp) for s in result.signals]
    assert all(isinstance(r, SignalRow) and r.run_id == 7 for r in signal_rows)


# --- generic exclusions (T127b, #1209: the paper planner reads the generic plan) -----


def test_momentum_fixture_plans_signals_rows_are_byte_identical() -> None:
    """The default plan (momentum's one `no_history` reason) journals exactly the
    rows it did before `Plan.exclusions` generalised past momentum: no reordering,
    no reason-string change."""
    assert [(s.security_id, s.reason, s.score, s.rank) for s in _run().signals] == [
        ("A", "selected", 0.5, 1),
        ("B", "selected", 0.4, 2),
        ("C", "below_cut", 0.1, 3),
        ("D", "below_cut", -0.2, 4),
        ("E", "excluded_no_history", None, None),
    ]


def test_signals_journal_every_declared_exclusion_reason() -> None:
    """A plan whose family declares more than one exclusion reason (profitability's
    shape, not momentum's) journals one `excluded_<reason>` row per reason."""
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1},
        members=("A", "B", "C", "D", "E"),
        excluded=(),
        exclusions={"sector": ("D",), "no_facts": ("E",)},
    )
    signals = {s.security_id: s for s in _run(plan, _ledger({})).signals}
    assert signals["D"].reason == "excluded_sector"
    assert signals["E"].reason == "excluded_no_facts"
    assert (signals["D"].score, signals["D"].rank) == (None, None)
    assert (signals["E"].score, signals["E"].rank) == (None, None)


def test_a_member_excluded_for_two_reasons_raises() -> None:
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1},
        members=("A", "B", "C", "D", "E"),
        excluded=(),
        exclusions={"sector": ("D",), "no_facts": ("D", "E")},
    )
    with pytest.raises(ValueError, match="both"):
        _run(plan, _ledger({}))


def test_a_member_left_out_of_scores_and_exclusions_raises() -> None:
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1},
        members=("A", "B", "C", "D", "E"),
        excluded=(),
        exclusions={"sector": ("D",)},  # E is neither scored nor excluded
    )
    with pytest.raises(ValueError, match="partition"):
        _run(plan, _ledger({}))


def test_a_scored_member_also_excluded_raises() -> None:
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1, "D": 0.0},
        members=("A", "B", "C", "D", "E"),
        excluded=(),
        exclusions={"sector": ("D",), "no_facts": ("E",)},  # D scored and excluded
    )
    with pytest.raises(ValueError, match="partition"):
        _run(plan, _ledger({}))


def test_excluded_no_history_outside_the_declared_set_raises() -> None:
    """`excluded_no_history` non-empty while the family's `exclusions` do not
    declare `no_history` at all names a reason outside the family's declared set."""
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1, "D": 0.0},
        members=("A", "B", "C", "D", "E"),
        excluded=("E",),
        exclusions={"sector": ("E",)},  # declares "sector", not "no_history"
    )
    with pytest.raises(ValueError, match="no_history"):
        _run(plan, _ledger({}))


def test_excluded_no_history_disagreeing_with_its_declared_ids_raises() -> None:
    """`no_history` is declared, but `excluded_no_history` names a different id
    than `exclusions["no_history"]` does: the legacy field and the generic
    mapping disagree, rather than one of them being simply absent."""
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1},
        members=("A", "B", "C", "D", "E"),
        excluded=("D",),  # the legacy field says D
        exclusions={"no_history": ("E",)},  # the mapping says E
    )
    with pytest.raises(ValueError, match="disagrees"):
        _run(plan, _ledger({}))


def test_an_undeclared_reason_key_raises() -> None:
    """A reason key of the wrong shape, or already carrying the `excluded_`
    prefix the database adds, is refused: the only thing `_signals` can check
    against, since `Plan` carries no family, is the key's own shape, which the
    database's prefix-only `CHECK` would not itself catch."""
    plan = _plan(
        scores={"A": 0.5, "B": 0.4, "C": 0.1},
        members=("A", "B", "C", "D", "E"),
        excluded=(),
        exclusions={"": ("D",), "excluded_no_facts": ("E",)},
    )
    with pytest.raises(ValueError, match="malformed"):
        _run(plan, _ledger({}))


# --- overrides ----------------------------------------------------------------------


def test_exclude_name_sells_a_held_target_whole_and_leaves_its_weight_in_cash() -> None:
    result = _run(overrides=(_override("exclude_name", "A", override_id=11),))
    got = _by_name(result)
    a = got["A"]
    assert (a.decision, a.side, a.reason, a.override_id) == ("override", "sell", "exclude_name", 11)
    assert (a.planned_quantity, a.planned_notional) == (10.0, None)
    # Not re-spread: B keeps its plan weight.
    assert got["B"].planned_notional == pytest.approx(0.5 * EQUITY)
    assert got["B"].override_id is None
    # The signals row still records what the plan selected.
    assert {s.security_id: s.reason for s in result.signals}["A"] == "selected"


def test_exclude_name_on_a_target_not_held_buys_nothing() -> None:
    b = _by_name(_run(overrides=(_override("exclude_name", "B", override_id=12),)))["B"]
    assert (b.decision, b.side, b.reason, b.override_id) == ("override", None, "exclude_name", 12)
    assert (b.planned_notional, b.target_notional) == (None, None)


def test_keep_name_keeps_a_held_name_at_its_drifted_weight() -> None:
    c = _by_name(_run(overrides=(_override("keep_name", "C", override_id=13),)))["C"]
    assert (c.decision, c.side, c.reason, c.override_id) == ("override", None, "keep_name", 13)
    assert (c.planned_quantity, c.planned_notional) == (None, None)
    assert c.drifted_weight == pytest.approx(500 / EQUITY)


def test_keep_name_on_a_target_held_below_weight_buys_nothing() -> None:
    a = _by_name(_run(overrides=(_override("keep_name", "A", override_id=14),)))["A"]
    assert (a.decision, a.side, a.override_id) == ("override", None, 14)


def test_an_excluded_name_leaves_the_buy_targets_alone() -> None:
    """An exclude sell is a sell the plan made: its proceeds fund the buys."""
    got = _by_name(_run(overrides=(_override("exclude_name", "A", override_id=11),)))
    spendable = buy_notional_after_costs(
        1000.0 + 500.0 + 500.0 + 10.0, COSTS.per_side_bps, COSTS.commissions, price=PRICES["B"]
    )
    b = got["B"]
    want = (b.planned_notional or 0.0) * min(1.0, spendable / (b.planned_notional or 1.0))
    assert b.target_notional == pytest.approx(want)


def test_overrides_of_another_rebalance_or_the_kill_switch_are_ignored() -> None:
    result = _run(
        overrides=(
            _override("exclude_name", "A", override_id=15, session=date(2026, 8, 31)),
            _override("engage_kill_switch", None, override_id=16, session=None),
        )
    )
    assert _by_name(result) == _by_name(_run())


def _settle_order(security_id: str, *, override_id: int) -> OverrideRow:
    """A `settle_order` row (spec req 17, #571) on rebalance T's order of a name."""
    return replace(
        _override("settle_order", security_id, override_id=override_id),
        client_order_id=f"tp-{security_id}",
    )


def test_a_settle_order_override_makes_no_decision() -> None:
    """Spec req 17: `plan.decisions_from` ignores `settle_order` rows; no
    `override` decision exists for them, held or target, on their own session."""
    settlements = (_settle_order("A", override_id=22), _settle_order("B", override_id=23))
    result = _run(overrides=settlements)
    assert _by_name(result) == _by_name(_run())
    assert all(d.decision != "override" for d in _by_name(result).values())


def test_a_settle_order_row_does_not_clash_with_a_name_override() -> None:
    """A settlement on a name the owner also excludes is not a second override of
    the name (only `exclude_name` and `keep_name` are)."""
    alone = _by_name(_run(overrides=(_override("exclude_name", "A", override_id=24),)))
    both = _by_name(
        _run(
            overrides=(
                _override("exclude_name", "A", override_id=24),
                _settle_order("A", override_id=25),
            )
        )
    )
    assert both == alone


def test_an_override_naming_no_decided_name_makes_no_decision() -> None:
    got = _by_name(_run(overrides=(_override("keep_name", "D", override_id=17),)))
    assert "D" not in got


def test_two_overrides_on_one_name_raise() -> None:
    with pytest.raises(ValueError, match="A"):
        _run(
            overrides=(
                _override("exclude_name", "A", override_id=18),
                _override("keep_name", "A", override_id=19),
            )
        )


def test_an_exclude_or_keep_override_without_a_name_raises() -> None:
    with pytest.raises(ValueError, match="no security"):
        _run(overrides=(_override("keep_name", None, override_id=20),))


# --- skips and dust -----------------------------------------------------------------


def test_a_listing_ended_at_close_s_minus_1_is_skip_delisted() -> None:
    got = _by_name(_run(listings_at={"C": T, "B": None}))
    assert (got["C"].decision, got["C"].side) == ("skip_delisted", None)
    assert (got["B"].decision, got["B"].side) == ("skip_delisted", None)
    assert got["A"].decision == "trade"


def test_a_listing_ending_after_s_minus_1_is_traded() -> None:
    got = _by_name(_run(listings_at={"C": S}))
    assert (got["C"].decision, got["C"].reason) == ("trade", "left_targets")


def test_a_trade_below_the_minimum_is_skip_below_minimum() -> None:
    # A at exactly 1000 of equity 2000 vs a 0.5 target: zero; B small.
    frozen = RiskConfig(min_order_notional=5.0)
    plan = _plan(targets={"A": 0.5, "B": 0.5})
    got = _by_name(_run(plan, _ledger({"A": 20.0, "B": 49.9}, cash=2.0), frozen=frozen))
    # Equity 1000 + 998 + 2 = 2000: A trade 0, B trade 2 below 5.
    assert (got["A"].decision, got["A"].side) == ("skip_zero", None)
    assert (got["B"].decision, got["B"].side) == ("skip_below_minimum", None)
    assert got["B"].planned_notional is None


def test_a_full_exit_below_the_minimum_is_plan_time_dust() -> None:
    # X: 0.05 x 10 = 0.5 below 1.0.
    x = _by_name(_run(ledger=_ledger({"A": 10.0, "X": 0.05})))["X"]
    assert (x.decision, x.side, x.reason) == ("dust", None, "left_universe")


def test_a_whole_share_full_exit_below_one_share_is_dust() -> None:
    x = _by_name(_run(ledger=_ledger({"A": 10.0, "X": 0.5}), assets=_assets("X")))["X"]
    assert (x.decision, x.whole_share, x.reason) == ("dust", True, "left_universe")


def test_a_whole_share_full_exit_is_never_dust_by_the_buffered_share_minimum() -> None:
    """ADR 0010 amendment 2026-09-30: 1.01 shares at 100 is 101, under one share at
    the buffered price (102) but above one share and the minimum: still sold."""
    plan = _plan(
        members=("A", "B", "C", "D", "E", "W"),
        scores={"A": 0.5, "B": 0.4, "C": 0.1, "D": -0.2, "W": 0.1},
    )
    got = _by_name(_run(plan, _ledger({"W": 1.01}), assets=_assets("W")))
    w = got["W"]
    assert (w.decision, w.side, w.reason, w.whole_share) == ("trade", "sell", "left_targets", True)
    assert w.planned_quantity == 1.01


def test_a_whole_share_name_comes_from_the_assets_read() -> None:
    got = _by_name(_run(assets=_assets("B", "C")))
    assert (got["A"].whole_share, got["B"].whole_share, got["C"].whole_share) == (
        False,
        True,
        True,
    )


def test_a_name_missing_from_the_assets_read_raises() -> None:
    assets = _assets()
    del assets["B"]
    with pytest.raises(ValueError, match="B"):
        _run(assets=assets)


# --- forced exits -------------------------------------------------------------------


def test_no_decision_for_a_name_with_an_open_or_in_flight_forced_exit() -> None:
    result = _run(open_forced_exits=frozenset({"C"}))
    got = _by_name(result)
    assert "C" not in got
    # Its holding still counts in equity, and its proceeds are not the plan's.
    assert got["A"].drifted_weight == pytest.approx(500 / EQUITY)
    spendable = buy_notional_after_costs(
        1000.0 + 10.0, COSTS.per_side_bps, COSTS.commissions, price=PRICES["B"]
    )
    total = (got["A"].planned_notional or 0.0) + (got["B"].planned_notional or 0.0)
    want = (got["B"].planned_notional or 0.0) * min(1.0, spendable / total)
    assert got["B"].target_notional == pytest.approx(want)


# --- the live-capital count -----------------------------------------------------------


def test_orders_below_the_minimum_at_live_capital_are_counted_by_hand() -> None:
    """At live capital 100 on equity 2010 every order scales by 100/2010: A buy 505
    -> 25.1, B buy 1005 -> 50.0, C sell 500 -> 24.9, X sell 10 -> 0.50 (< 1.0)."""
    assert _run().n_orders_below_min_at_live_capital == 1
    small = Settings(_env_file=None, paper={"live_capital_reference": 40.0})
    # At 40: A 10.0, B 20.0, C 9.95, X 0.199: still only X.
    assert _run(settings=small).n_orders_below_min_at_live_capital == 1
    tiny = Settings(_env_file=None, paper={"live_capital_reference": 4.0})
    # At 4: A 1.005, B 2.0, C 0.995, X 0.0199: C and X.
    assert _run(settings=tiny).n_orders_below_min_at_live_capital == 2


def test_the_live_capital_count_includes_orders_already_below_the_minimum() -> None:
    # X at 0.05 shares is plan-time dust at full capital and below the minimum at 100.
    result = _run(ledger=_ledger({"A": 10.0, "C": 5.0, "X": 0.05}))
    assert _by_name(result)["X"].decision == "dust"
    assert result.n_orders_below_min_at_live_capital == 1


def test_overrides_and_skips_without_an_order_are_not_counted() -> None:
    result = _run(
        overrides=(_override("keep_name", "X", override_id=21),),
        listings_at={"C": T},
    )
    assert result.n_orders_below_min_at_live_capital == 0


# --- no look-ahead and the split basis --------------------------------------------------


def test_planned_quantities_are_stated_for_t_i_units() -> None:
    """A 2-for-1 split ex S, known at close(S-1): the ledger's 10 shares of C at S are
    5 shares in T_i units, as `plan.remainder` reads `planned_quantity`; the price at
    S (post-split, 50) and the ledger value agree."""
    prices = dict(PRICES, C=50.0)
    actions = _actions(("C", S, 2.0, session_close(T)))
    result = decisions_from(
        _plan(),
        _ledger({"A": 10.0, "C": 10.0, "X": 1.0}),
        (),
        _assets(),
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=prices.__getitem__,
        actions_as_of=actions,
        costs=COSTS,
    )
    c = _by_name(result)["C"]
    assert c.planned_quantity == pytest.approx(5.0)
    assert c.drifted_weight == pytest.approx(500 / EQUITY)


def test_a_catch_up_split_inside_t_i_to_s_minus_1_round_trips_through_remainder() -> None:
    """Catch-up on S = 2026-10-05 (S-1 = 2026-10-02): a 3-for-1 split of C ex
    2026-10-02, strictly inside (T_i, S-1], known at close(2026-10-01). The 15
    shares held at S are 5 in T_i units, and `remainder` on S, reading the same
    frame, restates them to the 15 the ledger holds."""
    catch_up, ex_date = date(2026, 10, 5), date(2026, 10, 2)
    prices = dict(PRICES, C=100.0 / 3)
    actions = _actions(("C", ex_date, 3.0, session_close(S)))
    result = decisions_from(
        _plan(),
        Ledger(positions={"A": 10.0, "C": 15.0, "X": 1.0}, cash=1000.0, through=catch_up),
        (),
        _assets(),
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=prices.__getitem__,
        actions_as_of=actions,
        costs=COSTS,
    )
    c = _by_name(result)["C"]
    assert c.planned_quantity == pytest.approx(5.0)
    row = replace(c.row(run_id=1, known_at=STAMP, ingested_at=STAMP, book_id="main"), decision_id=1)
    left = remainder(row, [], [], [], actions, prices.__getitem__, session=catch_up)
    assert left.quantity == pytest.approx(15.0)
    assert left.notional == pytest.approx(500.0)


def test_a_split_known_after_close_s_minus_1_is_refused() -> None:
    late = datetime(2026, 10, 1, 13, tzinfo=UTC)  # known on S, before the open
    assert late > session_close(T)
    with pytest.raises(ValueError, match="known after"):
        _run(actions=_actions(("C", S, 2.0, late)))


def test_a_split_ex_dated_after_s_is_not_applied() -> None:
    actions = _actions(("C", LATER, 2.0, session_close(T)))
    assert _by_name(_run(actions=actions))["C"].planned_quantity == 5.0


def test_the_plan_side_comes_only_from_the_plan() -> None:
    """Targets, members, scores and exclusions are the close(T_i) plan's: another
    ledger, other prices or splits change no signal row and no target weight."""
    base = _run()
    other = decisions_from(
        _plan(),
        _ledger({"D": 3.0}, cash=50.0),
        (),
        _assets(),
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=lambda sid: PRICES[sid] * 3,
        actions_as_of=_actions(("D", S, 3.0, session_close(T))),
        costs=COSTS,
    )
    assert base.signals == other.signals
    targets = {d.security_id: d.target_weight for d in other.decisions if d.target_weight}
    assert targets == _plan().targets


def test_a_ledger_not_stated_for_a_session_after_t_i_raises() -> None:
    with pytest.raises(ValueError, match="after the rebalance"):
        _run(ledger=Ledger(positions={"A": 1.0}, cash=1.0, through=T))


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_price_that_is_not_positive_and_finite_raises(bad: float) -> None:
    prices = dict(PRICES, C=bad)
    with pytest.raises(ValueError, match="C"):
        decisions_from(
            _plan(),
            _ledger({"A": 10.0, "C": 5.0}),
            (),
            _assets(),
            {},
            frozenset(),
            FROZEN,
            SETTINGS,
            price_of=prices.__getitem__,
            actions_as_of=_actions(),
            costs=COSTS,
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1])
def test_a_target_weight_that_is_not_finite_and_non_negative_raises(bad: float) -> None:
    with pytest.raises(ValueError, match="target weight of A"):
        _run(_plan(targets={"A": bad, "B": 0.5}))


def test_a_short_ledger_raises() -> None:
    with pytest.raises(ValueError, match="short"):
        _run(ledger=_ledger({"A": -1.0}))


# --- the boundary ---------------------------------------------------------------------


def test_plan_py_imports_no_adapter() -> None:
    tree = ast.parse(PLAN_PY.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    assert imported
    assert not [m for m in imported if m.startswith("tradepartner.adapters")], imported
    assert not [m for m in imported if m.split(".")[0] in {"alpaca", "requests", "httpx"}]


def test_signal_value_type_is_exported() -> None:
    assert Signal.__name__ == "Signal"


def test_is_full_exit_reads_every_sell_decisions_from_writes() -> None:
    """T54c: `plan.is_full_exit` accepts every decision `decisions_from` writes,
    true exactly for the whole-holding sells (spec Definitions > Full exit)."""
    stamp = datetime(2026, 10, 1, 21, tzinfo=UTC)
    runs = [
        _run(),  # C left_targets, X left_universe, A and B buys
        _run(_plan(targets={"A": 0.5, "B": 0.5}), _ledger({"A": 30.0})),  # A a trim
        _run(overrides=(_override("exclude_name", "A", override_id=11),)),
    ]
    seen = set()
    for result in runs:
        for d in result.decisions:
            row = d.row(run_id=1, known_at=stamp, ingested_at=stamp, book_id="main")
            full = is_full_exit(row)
            assert full == (d.side == "sell" and d.reason is not None), d
            seen.add((d.decision, d.side, d.reason, full))
    assert ("trade", "sell", None, False) in seen
    assert ("trade", "sell", "left_targets", True) in seen
    assert ("trade", "sell", "left_universe", True) in seen
    assert ("override", "sell", "exclude_name", True) in seen
