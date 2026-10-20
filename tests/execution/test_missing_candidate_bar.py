# ruff: noqa: F811
"""A universe candidate with no T_0 bar (a partial ingest) is excluded, visibly (#1997).

Spec: paper-trading.md, amendment 2026-10-20 (owner, #1997). The fixture is
test_run_trade's: T_0 = 2019-04-30, the plan buys DUALB, SPFT, TRNS a third each.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

import execution.test_run_trade as run_trade
from execution.test_run_trade import F_0, T_0, Env, at, env, window  # noqa: F401
from tradepartner.config import FAMILIES, RiskConfig, Settings
from tradepartner.execution.alerts import ALERT_KINDS
from tradepartner.execution.report import Journal, TrialPeriods, compare_targets
from tradepartner.store import journal
from tradepartner.store.db import configure_connection
from tradepartner.store.journal import PaperWindowRow
from tradepartner.store.schema import init_schema

TRNS = "SEC_TRANSFER"
INTENDED = {"SEC_DUAL_B", "SEC_SPLIT_FUTURE", TRNS}


def drop_bar(env: Env, security_id: str, session: date) -> None:
    with env.connect() as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session = ?",
            [security_id, session],
        )


def signal_reasons(env: Env) -> dict[str, str]:
    rows = env.query("SELECT security_id, reason FROM signals WHERE run_id = ?", [env.latest_run()])
    return {r[0]: r[1] for r in rows}


def test_reason_and_alert_kind_are_declared() -> None:
    assert "missing_candidate_bar" in ALERT_KINDS
    assert all("no_t0_bar" in spec.exclusion_reasons for spec in FAMILIES.values())


def test_candidate_with_no_t0_bar_is_excluded_with_the_reason_and_alerts(
    env: Env, window: PaperWindowRow
) -> None:
    drop_bar(env, TRNS, T_0)  # partial ingest: SPY's T_0 bar is there
    outcome = env.run(at(F_0), prices={})
    assert outcome.status == "ok"
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    alerts = env.alerts("missing_candidate_bar")
    assert len(alerts) == 1 and TRNS in str(alerts[0])
    assert env.alerts("stale_data") == []


def test_the_run_continues_and_does_not_silently_retarget(env: Env, window: PaperWindowRow) -> None:
    drop_bar(env, TRNS, T_0)
    assert env.run(at(F_0), prices={}).status == "ok"
    bought = {o[1] for o in env.orders()}
    assert bought and TRNS not in bought
    # The retarget (the fixture's next name in, TRNS out) is on the record: the
    # signals row and the alert, the two things the scratch break test asked for.
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    assert len(env.alerts("missing_candidate_bar")) == 1


def test_a_complete_ingest_raises_nothing(env: Env, window: PaperWindowRow) -> None:
    env.run(at(F_0))
    assert env.alerts("missing_candidate_bar") == []
    assert "excluded_no_t0_bar" not in signal_reasons(env).values()
    assert {o[1] for o in env.orders()} == INTENDED


def test_a_t0_bar_known_after_the_cut_does_not_count(env: Env, window: PaperWindowRow) -> None:
    """No look-ahead: TRNS's T_0 bar exists in the store and was ingested before the
    run, but after the plan's read time (close of T_0): as of the cut it is missing."""
    with env.connect() as conn:
        conn.execute(
            "UPDATE prices_daily SET known_at = ?, ingested_at = ? "
            "WHERE security_id = ? AND session = ?",
            [at(T_0, 23), at(T_0, 23), TRNS, T_0],
        )
    env.run(at(F_0), prices={})
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    assert len(env.alerts("missing_candidate_bar")) == 1


def test_a_name_with_a_long_gap_is_not_a_missing_t0_candidate(
    env: Env, window: PaperWindowRow
) -> None:
    """A name missing T_0 and another history session is a history exclusion, not
    this one: no signals row, no alert."""
    prior = date(2019, 4, 29)
    drop_bar(env, TRNS, T_0)
    drop_bar(env, TRNS, prior)
    env.run(at(F_0), prices={})
    assert env.alerts("missing_candidate_bar") == []
    assert TRNS not in signal_reasons(env)


def test_the_exclusion_leaves_n_universe_unchanged(env: Env, window: PaperWindowRow) -> None:
    """The candidate was never a member: n_universe is the members only."""
    drop_bar(env, TRNS, T_0)
    env.run(at(F_0), prices={})
    (n_universe,) = env.query("SELECT n_universe FROM paper_plans")[0]
    members = env.query(
        "SELECT count(*) FROM signals WHERE run_id = ? AND reason NOT LIKE 'excluded_no_t0%'",
        [env.latest_run()],
    )[0][0]
    assert n_universe == members


def test_the_kill_switch_stays_clear_and_the_alert_precedes_the_first_order(
    env: Env, window: PaperWindowRow
) -> None:
    drop_bar(env, TRNS, T_0)
    env.run(at(F_0), prices={})
    assert env.query("SELECT count(*) FROM kill_switch")[0][0] == 0
    first_order = env.query("SELECT min(known_at) FROM orders")[0][0]
    alert_at = env.query("SELECT \"at\" FROM alerts WHERE kind = 'missing_candidate_bar'")[0][0]
    assert alert_at <= first_order


def test_a_reused_plan_raises_no_second_alert(env: Env, window: PaperWindowRow) -> None:
    drop_bar(env, TRNS, T_0)
    env.run(at(F_0, 12, 0), prices={})
    env.run(at(F_0, 13, 0), prices={})
    assert len(env.alerts("missing_candidate_bar")) == 1


def test_a_held_name_with_no_bar_fails_the_run_at_the_marks_when_the_stale_limit_is_0(
    env: Env, window: PaperWindowRow
) -> None:
    """A window frozen before #1763 reads `paper.max_stale_mark_sessions` as 0: a held
    name missing its S-1 bar (= T_1 here) fails the run at the marks, before any plan."""
    env.run(at(F_0))
    orders = len(env.orders())
    drop_bar(env, TRNS, date(2019, 5, 31))
    with pytest.raises(ValueError, match=TRNS):
        env.run(at(date(2019, 6, 3)), prices={"TRNS": 50.0})
    assert env.alerts("missing_candidate_bar") == []
    assert len(env.orders()) == orders


T_1 = date(2019, 5, 31)  # the rebalance after F_0; its fill session is F_1
F_1 = date(2019, 6, 3)
T_2, F_2 = date(2019, 6, 28), date(2019, 7, 1)


@pytest.fixture
def window3(env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PaperWindowRow:
    """The window with `paper.max_stale_mark_sessions = 3` frozen into `frozen_json`
    (and its matching `frozen_sha256`), as `paper start` writes it."""
    original = run_trade.frozen_json

    def with_limit(frozen: RiskConfig) -> str:
        values = json.loads(original(frozen))
        values["paper.max_stale_mark_sessions"] = 3
        return json.dumps(values, sort_keys=True)

    monkeypatch.setattr(run_trade, "frozen_json", with_limit)
    # Loose enough that two names at one half each (TRNS dropped) are within the limits.
    loose = RiskConfig(max_position_weight=0.6, max_order_notional_fraction=0.6)
    return env.open_window(tmp_path=tmp_path, frozen=loose)


def decisions_at(env: Env, rebalance: date) -> dict[str, tuple[str, float, float, str | None]]:
    rows = env.query(
        "SELECT security_id, decision, target_weight, drifted_weight, reason FROM decisions "
        "WHERE rebalance_session = ?",
        [rebalance],
    )
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def bar_row(env: Env, security_id: str, session: date) -> dict[str, object]:
    with env.connect() as conn:
        cursor = conn.execute(
            "SELECT * FROM prices_daily WHERE security_id = ? AND session = ?",
            [security_id, session],
        )
        names = [c[0] for c in cursor.description]
        (row,) = cursor.fetchall()
    return dict(zip(names, row, strict=True))


def reingest(env: Env, row: dict[str, object], when: datetime) -> None:
    env.insert("prices_daily", {**row, "ingested_at": when})


def equity_at(env: Env, session: date) -> float:
    """Cash plus every held name's quantity times its latest close at or before `session`."""
    total = env.fake.account().cash
    for position in env.fake.positions().values():
        sid = next(k for k, v in run_trade.SYMBOLS.items() if v == position.symbol)
        close = env.query(
            "SELECT close FROM prices_daily WHERE security_id = ? AND session <= ? "
            "ORDER BY session DESC LIMIT 1",
            [sid, session],
        )[0][0]
        total += position.quantity * float(close)
    return total


def keep_wndx_trading(env: Env) -> None:
    """The F_1 plan buys SEC_WINDOW_DELIST, whose fixture bars end 2019-06-24; carry it to
    T_2 so the next rebalance's marks (not this test's subject) find a bar."""
    last = bar_row(env, "SEC_WINDOW_DELIST", date(2019, 6, 24))
    for day in (25, 26, 27, 28):
        session = date(2019, 6, day)
        stamp = at(session, 20, 0)
        env.insert(
            "prices_daily",
            {**last, "session": session, "known_at": stamp, "ingested_at": stamp},
        )


def trns_orders(env: Env, run_id: int) -> list[str]:
    return [o[0] for o in env.orders() if o[1] == TRNS and o[5] == run_id]


def hold_trns_without_t1_bar(env: Env) -> dict[str, object]:
    """The F_0 buys filled, then TRNS's T_1 bar is lost (a partial ingest); returns it."""
    run_trade.bought(env)
    row = bar_row(env, TRNS, T_1)
    drop_bar(env, TRNS, T_1)
    return row


def test_a_held_name_with_no_bar_is_kept_not_sold(env: Env, window3: PaperWindowRow) -> None:
    hold_trns_without_t1_bar(env)
    quantity = env.held()["TRNS"]
    outcome = env.run(at(F_1), prices={"TRNS": 50.0})
    assert outcome.status == "ok"
    run_id = env.latest_run()
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    kept = decisions_at(env, T_1)[TRNS]
    assert kept[0] == "skip_zero" and kept[1] == 0.0 and kept[2] > 0.0 and kept[3] is None
    assert trns_orders(env, run_id) == []
    assert env.held()["TRNS"] == quantity
    assert env.query("SELECT count(*) FROM kill_switch")[0][0] == 0
    assert len(env.alerts("stale_mark")) == 1
    (alert,) = env.alerts("missing_candidate_bar")
    assert TRNS in alert[2] and "kept at their current quantity" in alert[2]
    first_order = env.query("SELECT min(known_at) FROM orders WHERE run_id = ?", [run_id])[0][0]
    alert_at = env.query("SELECT \"at\" FROM alerts WHERE kind = 'missing_candidate_bar'")[0][0]
    assert alert_at <= first_order
    # The kept weight is its weight at S: quantity times its latest close over equity.
    closes = dict(
        env.query(
            "SELECT security_id, close FROM prices_daily WHERE session = ? "
            "AND security_id IN ('SEC_DUAL_B', 'SEC_SPLIT_FUTURE')",
            [T_1],
        )
    )
    assert 0.0 < kept[2] < 1.0 and closes
    assert kept[2] == pytest.approx(
        quantity
        * float(
            env.query(
                "SELECT close FROM prices_daily WHERE security_id = ? AND session < ? "
                "ORDER BY session DESC LIMIT 1",
                [TRNS, T_1],
            )[0][0]
        )
        / equity_at(env, T_1),
        rel=1e-6,
    )


def test_the_target_comparison_writes_no_row_against_a_trial_that_lacks_the_bar(
    env: Env, window3: PaperWindowRow
) -> None:
    hold_trns_without_t1_bar(env)
    env.run(at(F_1), prices={"TRNS": 50.0})
    with env.connect() as conn:
        decisions = tuple(d.decision for d in journal.decisions_for(conn, window3.window_id or 0))
        plans = tuple(journal.plans_for(conn, window3.window_id or 0))
    trial = TrialPeriods(sessions=(T_1, date(2019, 6, 28)), equity={}, cost_paid={}, trial_id=1)
    memory = duckdb.connect(":memory:")
    configure_connection(memory)
    init_schema(memory)
    # The trial also lacks the bar: it holds nothing for TRNS at F_1.
    result = compare_targets(
        window3,
        trial,
        Journal(positions_daily=(), adjustments=(), decisions=decisions, plans=plans),
        memory,
    )
    assert [r for r in result.rows if r.security_id == TRNS] == []


def test_a_same_day_rerun_reuses_the_decision(env: Env, window3: PaperWindowRow) -> None:
    hold_trns_without_t1_bar(env)
    env.run(at(F_1, 12, 0), prices={"TRNS": 50.0})
    before = decisions_at(env, T_1)
    env.run(at(F_1, 13, 0), prices={"TRNS": 50.0})
    assert decisions_at(env, T_1) == before
    assert len(env.alerts("missing_candidate_bar")) == 1


def test_a_catch_up_plan_keeps_it_too(env: Env, window3: PaperWindowRow) -> None:
    hold_trns_without_t1_bar(env)
    catch_up = date(2019, 6, 4)
    outcome = env.run(at(catch_up), prices={"TRNS": 50.0})
    assert outcome.kind == "catch_up"
    assert decisions_at(env, T_1)[TRNS][0] == "skip_zero"
    assert trns_orders(env, env.latest_run()) == []


def test_at_the_next_rebalance_the_name_is_a_member_once_reingested(
    env: Env, window3: PaperWindowRow
) -> None:
    row = hold_trns_without_t1_bar(env)
    env.run(at(F_1), prices={"TRNS": 50.0})
    assert decisions_at(env, T_1)[TRNS][0] == "skip_zero"
    reingest(env, row, at(F_1, 20, 0))
    keep_wndx_trading(env)
    env.run(at(F_2), prices={"TRNS": 50.0})
    # Planned by the ordinary rules: nothing is excluded or kept for TRNS any more. (The
    # fixture's TRNS leaves the universe at T_2 for its own reasons, so it is not a member
    # there; the member case is the unit test's.)
    assert TRNS not in [
        r[0]
        for r in env.query(
            "SELECT security_id FROM signals WHERE run_id = ? AND reason = 'excluded_no_t0_bar'",
            [env.latest_run()],
        )
    ]
    assert decisions_at(env, T_2)[TRNS][0] != "skip_zero"


def test_at_the_next_rebalance_with_the_bar_still_missing_it_is_sold_left_universe(
    env: Env, window3: PaperWindowRow
) -> None:
    hold_trns_without_t1_bar(env)
    env.run(at(F_1), prices={"TRNS": 50.0})
    keep_wndx_trading(env)
    env.run(at(F_2), prices={"TRNS": 50.0})
    later = decisions_at(env, T_2)[TRNS]
    assert (later[0], later[3]) == ("trade", "left_universe")
    assert TRNS not in [
        r[0]
        for r in env.query(
            "SELECT security_id FROM signals WHERE run_id = ? AND reason = 'excluded_no_t0_bar'",
            [env.latest_run()],
        )
    ]


def test_a_t1_bar_known_after_the_cut_still_makes_the_name_kept(
    env: Env, window3: PaperWindowRow, tmp_path: Path
) -> None:
    """No look-ahead: the bar exists but became known after the plan's read time, so as of
    the cut it is missing; the decisions equal those over a store truncated at the cut
    (the same store with the bar absent)."""
    truncated_path = tmp_path / "truncated.duckdb"
    shutil.copy(env.settings.store.path, truncated_path)
    truncated = Env(
        Settings(
            _env_file=None,
            store={"path": str(truncated_path)},
            alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48},
        ),
        run_trade.Clock(at(F_0)),
    )
    truncated.window = env.window
    run_trade.bought(env)
    run_trade.bought(truncated)
    with env.connect() as conn:
        conn.execute(
            "UPDATE prices_daily SET known_at = ?, ingested_at = ? "
            "WHERE security_id = ? AND session = ?",
            [at(T_1, 23), at(T_1, 23), TRNS, T_1],
        )
    drop_bar(truncated, TRNS, T_1)
    env.run(at(F_1), prices={"TRNS": 50.0})
    truncated.run(at(F_1), prices={"TRNS": 50.0})
    assert decisions_at(env, T_1)[TRNS][0] == "skip_zero"
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    assert decisions_at(env, T_1) == decisions_at(truncated, T_1)
