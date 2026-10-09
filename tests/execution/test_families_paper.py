"""`profitability` and `combined` on paper (ADR 0017 part D; plan T152).

A fixture-store round trip of one plan per newly paper-ready family: the window's
stored family reaches `engine.plan` through `plan_rebalance`, the plan becomes
journaled `signals` (`_signals`, with the family's own `excluded_<reason>` rows),
`decisions` (`decisions_from`) and a `paper_plans` row, and a re-run reads the
journal back instead of planning again. The environment is `test_planning.py`'s
(T_i 2019-04-30 on the fixture universe, two held names), registered in the family
under test. The issuer of `SEC_WINDOW_DELIST` has its statement facts deleted from
the temp copy, so the family's `no_facts` exclusion is journaled, not vacuous.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from execution.test_planning import HELD, T_I, Env, _decisions, _Lend, open_env
from tradepartner.backtest import engine
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import FAMILIES, PAPER_FAMILIES, HypothesisFamily
from tradepartner.store import journal, registry

NEW_FAMILIES: tuple[HypothesisFamily, ...] = ("profitability", "combined")
NO_FACTS = "SEC_WINDOW_DELIST"


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # StoreProvider compares the frozen calendar with the live one; no `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@pytest.fixture(params=NEW_FAMILIES)
def family_env(
    request: pytest.FixtureRequest, fixture_store_path: Path, tmp_path: Path
) -> Iterator[Env]:
    with open_env(fixture_store_path, tmp_path, request.param) as env:
        env.conn.execute(
            "DELETE FROM statement_facts WHERE cik IN "
            "(SELECT cik FROM securities WHERE security_id = ?)",
            [NO_FACTS],
        )
        yield env


def _family(env: Env) -> HypothesisFamily:
    return registry.get_hypothesis_by_id(env.conn, env.hypothesis_id).family  # type: ignore[return-value]


def _engine_plan(env: Env, family: HypothesisFamily) -> engine.Plan:
    """`engine.plan` at T_i for `family` under a trial of its own, rolled back."""
    env.conn.begin()
    try:
        handle = registry.open_trial(
            env.conn,
            hypothesis_id=env.hypothesis_id,
            kind="tracking",
            start_session=T_I,
            end_session=T_I,
            data_cutoff=read_time(T_I),
            synthetic=False,
            run_by="test",
            settings=env.settings,
        )
        with StoreProvider(lambda: _Lend(env.conn), handle, env.params) as provider:
            return engine.plan(provider, env.params, T_I, family=family)
    finally:
        env.conn.rollback()


def test_paper_families_are_the_four_families() -> None:
    assert PAPER_FAMILIES == ("momentum", "oracle", "profitability", "combined")
    assert all(FAMILIES[family].paper_ready for family in NEW_FAMILIES)


def test_a_plan_round_trips_through_the_journal(
    family_env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = family_env
    family = _family(env)
    assert family in NEW_FAMILIES
    real_plan = engine.plan
    seen: list[str] = []

    def record(*args: Any, **kwargs: Any) -> engine.Plan:
        seen.append(kwargs["family"])
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(engine, "plan", record)
    before = env.counts()
    outcome = env.plan()
    assert (outcome.status, outcome.rebalance_session) == ("planned", T_I)
    # `plan_rebalance` dispatched on the window's stored family.
    assert seen == [family]
    monkeypatch.setattr(engine, "plan", real_plan)
    plan = _engine_plan(env, family)

    # `_signals`: one row per member, the family's own exclusion reasons.
    signals = {s.security_id: s for s in journal.signals_for(env.conn, env.run.run_id)}  # type: ignore[arg-type]
    assert set(signals) == set(plan.members)
    declared = FAMILIES[family].exclusion_reasons
    assert set(plan.exclusions) == set(declared)
    excluded = {sid: reason for reason, ids in plan.exclusions.items() for sid in ids}
    assert excluded == {NO_FACTS: "no_facts"}
    for sid, signal in signals.items():
        if sid in excluded:
            assert (signal.reason, signal.score, signal.rank) == (
                f"excluded_{excluded[sid]}",
                None,
                None,
            )
        else:
            assert signal.reason == ("selected" if sid in plan.targets else "below_cut")
            assert signal.score == plan.scores[sid]
    assert {sid for sid, s in signals.items() if s.reason == "selected"} == set(plan.targets)

    # `decisions_from`: the family's targets bought, the held names sold.
    decided = _decisions(outcome)
    assert set(decided) == set(plan.targets) | set(HELD)
    for sid in plan.targets:
        assert (decided[sid].decision, decided[sid].side) == ("trade", "buy")
        assert decided[sid].target_notional is not None
    assert (decided["SEC_SPY"].side, decided["SEC_SPY"].reason) == ("sell", "left_universe")
    assert (decided["SEC_DUAL_A"].side, decided["SEC_DUAL_A"].reason) == ("sell", "left_targets")
    journaled = journal.decisions_for(env.conn, env.window.window_id, rebalance_session=T_I)  # type: ignore[arg-type]
    assert [d.decision for d in journaled] == list(outcome.decisions)

    # The plan row, and the rows written: one trial, one result, the signals,
    # the decisions and one plan.
    [plan_row] = journal.plans_for(env.conn, env.window.window_id)  # type: ignore[arg-type]
    assert plan_row.plan_trial_id == outcome.plan_trial_id
    assert (plan_row.n_universe, plan_row.n_targets) == (plan.n_universe, len(plan.targets))
    after = env.counts()
    assert after == {
        "trials": before["trials"] + 1,
        "trial_results": before["trial_results"] + 1,
        "signals": before["signals"] + len(plan.members),
        "decisions": before["decisions"] + len(decided),
        "paper_plans": before["paper_plans"] + 1,
    }

    # A re-run reads the journal back and never plans again.
    def never(*args: Any, **kwargs: Any) -> engine.Plan:
        raise AssertionError("a journaled plan must not be re-planned")

    monkeypatch.setattr(engine, "plan", never)
    again = env.plan()
    assert again.decisions == outcome.decisions
    assert env.counts() == after
