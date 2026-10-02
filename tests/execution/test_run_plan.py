"""The planning call at run level (Phase 4 spec req 7 step 6, Definitions > Plan
trial, req 8's no-plan-while-lagging rule; plan T63i).

Every case runs `tracking_run` on a temp-file copy of the fixture store with the
scripted fake and a settable clock (the shape of `test_run_trade.py`, T63d). The
window: T_0 = 2019-04-30, F_0 = 2019-05-01, on a registered hypothesis whose
plan at close(T_0) buys DUALB, SPFT and TRNS a third each from a flat start; the
fake fills every open order when the run sleeps, at the store's close of S-1.
May 2019 is EDT: a run at 12:30 UTC is inside the submit window [12:00, 14:00].
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import FakeBroker, PartialFill
from tradepartner.backtest import engine
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.calendar import previous_session
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import planning
from tradepartner.execution import run as run_module
from tradepartner.execution.planning import PlanTrialError
from tradepartner.execution.run import RunOutcome, StepContext, tracking_run
from tradepartner.store import registry
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    append,
)

T_0, F_0 = date(2019, 4, 30), date(2019, 5, 1)
F_0_PLUS_1 = date(2019, 5, 2)
HOLDOUT_START, HOLDOUT_END = date(2018, 7, 2), date(2019, 3, 29)
IN_SAMPLE_START = date(2017, 1, 3)
MAX_CATCH_UP = 2
FAKE_CASH = 100_000.0
FAMILY = "momentum"
SYMBOLS = {
    "SEC_DUAL_B": "DUALB",
    "SEC_SPLIT_FUTURE": "SPFT",
    "SEC_TRANSFER": "TRNS",
    "SEC_SPY": "SPY",
}
TARGETS = ("SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER")
PLAN_TABLES = ("signals", "decisions", "paper_plans")
#: Loose enough that a third of equity is one order and one position.
FROZEN = RiskConfig(max_position_weight=0.4, max_order_notional_fraction=0.4)


class Clock:
    """A settable clock."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def at(day: date, hour: int = 12, minute: int = 30) -> datetime:
    """`hour`:`minute` UTC on `day` (12:30 UTC: inside the submit window)."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


@dataclass
class Env:
    """The store, the clock, the fake and the window; `run` is one `paper run`."""

    settings: Settings
    clock: Clock
    prices: dict[str, float] = field(default_factory=dict)
    window: PaperWindowRow | None = None
    fake: FakeBroker = field(init=False)

    def __post_init__(self) -> None:
        self.fake = FakeBroker(
            clock=self.clock,
            price_of=lambda symbol: self.prices[symbol],
            auto_fill=False,
            cash=FAKE_CASH,
            account_id="PA1",
        )

    def connect(self) -> Any:
        return open_for_write(self.settings)

    def sleep(self, seconds: float) -> None:
        self.clock.now += timedelta(seconds=seconds)
        for order in self.fake.open_orders():
            self.fake.simulate_fill(order.client_order_id)

    def run(self, now: datetime) -> RunOutcome:
        """One run at `now`, with an ingest covering S-1 and the fake pricing at
        the closes of S-1."""
        session = now.date()
        self.ingest(at(previous_session(session), 21, 0))
        rows = self.query(
            "SELECT security_id, close FROM prices_daily WHERE session = ?",
            [previous_session(session)],
        )
        closes = dict(rows)
        for sid, symbol in SYMBOLS.items():
            if sid in closes:
                self.prices[symbol] = float(closes[sid])
        self.clock.now = now
        return tracking_run(self.settings, self.connect, self.fake, self.clock, sleep=self.sleep)

    def ingest(self, finished: datetime) -> None:
        run_id = f"ingest-{finished.isoformat()}"
        if self.query("SELECT 1 FROM ingestion_runs WHERE run_id = ?", [run_id]):
            return
        with open_for_write(self.settings) as conn:
            conn.execute(
                "INSERT INTO ingestion_runs (run_id, started_at, finished_at, status, source, "
                "mode) VALUES (?, ?, ?, 'ok', 'alpaca', 'daily')",
                [run_id, finished - timedelta(minutes=5), finished],
            )

    def append(self, *rows: object) -> list[int | None]:
        with open_for_write(self.settings) as conn:
            return [append(conn, row) for row in rows]  # type: ignore[arg-type]

    def open_window(self, tmp_path: Path) -> PaperWindowRow:
        """The window on a registered hypothesis (as `test_planning.py`)."""
        params = Settings(
            _env_file=None,
            strategy={"top_fraction": 0.5},
            holdout={"start": HOLDOUT_START.isoformat(), "end": HOLDOUT_END.isoformat()},
        )
        elsewhere = Settings(_env_file=None, store={"path": str(tmp_path / "elsewhere.duckdb")})
        with open_for_write(self.settings) as conn:
            hypothesis = registry.register_hypothesis(
                conn,
                slug="h-run-plan",
                family=FAMILY,
                title="tracking run planning call test",
                doc_path="docs/hypotheses/h-run-plan.md",
                doc_sha256="0" * 64,
                params=frozen_params_of(params),
                in_sample_start=IN_SAMPLE_START,
                holdout_start=HOLDOUT_START,
                holdout_end=HOLDOUT_END,
                registered_by="test",
                settings=elsewhere,
            )
        started = at(T_0, 12, 0)
        values: dict[str, Any] = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
        values["paper.max_catch_up_sessions"] = MAX_CATCH_UP
        row = PaperWindowRow(
            hypothesis_id=hypothesis.hypothesis_id,
            first_rebalance_session=T_0,
            account_id="PA1",
            starting_cash=FAKE_CASH,
            starting_equity=FAKE_CASH,
            code_version="test",
            started_at=started,
            frozen_json=json.dumps(values, sort_keys=True),
            frozen_sha256="0" * 64,
            known_at=started,
            ingested_at=started,
        )
        (window_id,) = self.append(row)
        self.window = replace(row, window_id=window_id)
        return self.window

    # --- reads -------------------------------------------------------------------

    def query(self, sql: str, params: list[object] | None = None) -> list[tuple[Any, ...]]:
        with open_read_only(self.settings) as conn:
            return conn.execute(sql, params or []).fetchall()

    def count(self, table: str) -> int:
        return int(self.query(f"SELECT count(*) FROM {table}")[0][0])

    def counts(self) -> dict[str, int]:
        """The planning rows and the plan trials (`kind = tracking`)."""
        counts = {t: self.count(t) for t in PLAN_TABLES}
        counts["tracking trials"] = int(
            self.query("SELECT count(*) FROM trials WHERE kind = 'tracking'")[0][0]
        )
        return counts

    def latest_run(self) -> int:
        return int(self.query("SELECT max(run_id) FROM paper_runs")[0][0])

    def result(self, run_id: int | None) -> tuple[str, str | None, str | None]:
        (row,) = self.query(
            "SELECT status, fault_type, message FROM paper_run_results WHERE run_id = ?", [run_id]
        )
        return row[0], row[1], row[2]

    def kind(self, run_id: int) -> str:
        return str(self.query("SELECT kind FROM paper_runs WHERE run_id = ?", [run_id])[0][0])

    def alerts(self, run_id: int) -> list[str]:
        return [
            r[0]
            for r in self.query(
                "SELECT kind FROM alerts WHERE run_id = ? ORDER BY alert_id", [run_id]
            )
        ]

    def engaged(self) -> list[tuple[str, str | None, int | None]]:
        return [
            (r[0], r[1], r[2])
            for r in self.query(
                "SELECT source, fault_type, run_id FROM kill_switch WHERE state = 'engaged' "
                "ORDER BY event_id"
            )
        ]

    def decision_ids(self) -> list[int]:
        return [r[0] for r in self.query("SELECT decision_id FROM decisions ORDER BY 1")]

    def rebalance_events(self) -> list[tuple[date, str, str | None, int]]:
        return [
            (r[0], r[1], r[2], r[3])
            for r in self.query(
                "SELECT rebalance_session, status, reason, run_id FROM rebalance_events "
                "ORDER BY known_at, rebalance_session"
            )
        ]

    def submits(self) -> int:
        return sum(1 for c in self.fake.calls if c.method == "submit")

    def family_sharpes(self) -> registry.FamilySharpes:
        with open_read_only(self.settings) as conn:
            return registry.family_sharpes(conn, FAMILY)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # StoreProvider compares the frozen calendar with the live one; no `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)


@pytest.fixture
def env(fixture_store_path: Path, tmp_path: Path) -> Env:
    settings = Settings(
        _env_file=None,
        store={"path": str(fixture_store_path)},
        alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48},
    )
    env = Env(settings, Clock(at(F_0)))
    env.open_window(tmp_path)
    return env


def spy_on_trade_step(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[planning.PlanOutcome | None, dict[str, int], int]]:
    """Record, at the trade step (so before the run returns), the plan it is
    handed, the planning rows in the store and the `orders` count; then trade."""
    original = run_module.trade_step
    seen: list[tuple[planning.PlanOutcome | None, dict[str, int], int]] = []

    def spy(context: StepContext) -> object:
        seen.append((context.plan, env.counts(), env.count("orders")))
        return original(context)

    monkeypatch.setattr(run_module, "trade_step", spy)
    return seen


# --- the plan trial -------------------------------------------------------------------------


def test_the_plan_trial_is_opened_and_closed_ok_with_no_metrics(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run on F_0 plans under a `kind=tracking` trial over [T_0, T_0], the
    trial `paper_plans` references, closed `ok` with no metrics, leaving N and
    V (`family_sharpes`) unchanged; the decisions are in the store before the
    trade step and before any order, and the run then trades them."""
    sharpes = env.family_sharpes()
    seen = spy_on_trade_step(env, monkeypatch)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert outcome.kind == "rebalance"

    ((plan, counts, orders),) = seen
    assert plan is not None and plan.status == "planned"
    assert counts["decisions"] == len(plan.decisions) == len(TARGETS)
    assert counts["paper_plans"] == counts["tracking trials"] == 1
    assert orders == 0  # every decision before any order
    assert [d.decision_id for d in plan.decisions] == env.decision_ids()

    ((trial_id, run_id),) = env.query("SELECT plan_trial_id, run_id FROM paper_plans")
    assert (trial_id, run_id) == (plan.plan_trial_id, outcome.run_id)
    kind, start, end = env.query(
        "SELECT kind, start_session, end_session FROM trials WHERE trial_id = ?", [trial_id]
    )[0]
    assert (kind, start, end) == ("tracking", T_0, T_0)
    assert env.query(
        "SELECT status, n_trials, dsr FROM trial_results WHERE trial_id = ?", [trial_id]
    ) == [("ok", None, None)]
    for table in ("trial_metrics", "trial_equity", "trial_weights", "trial_rebalances"):
        assert env.query(f"SELECT count(*) FROM {table} WHERE trial_id = ?", [trial_id]) == [
            (0,)
        ], table
    assert env.family_sharpes() == sharpes
    assert env.count("orders") == len(TARGETS)
    assert env.rebalance_events() == [(T_0, "executed", None, outcome.run_id)]


@pytest.mark.parametrize(
    "where",
    [
        pytest.param("write_result", id="trial-result-failed"),
        pytest.param("engine.plan", id="engine-plan-raised"),
    ],
)
def test_a_rolled_back_plan_trial_fails_the_run_and_the_next_run_is_engaged(
    env: Env, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """A plan trial opened and then rolled back raises `PlanTrialError`: the run
    ends `failed` with a `run_failed` alert, no `engaged` row (not the halt
    path), no planning row, no trial and no order; the next run derives
    `engaged` from that `failed` result (T59) and trades nothing."""
    before = env.counts()

    def broken(*_args: object, **_kwargs: object) -> object:
        raise ValueError("the engine failed")

    with monkeypatch.context() as patch:
        if where == "write_result":
            patch.setattr(registry, "write_result", lambda *_a, **_k: "failed")
        else:
            patch.setattr(engine, "plan", broken)
        with pytest.raises(PlanTrialError):
            env.run(at(F_0))
    failed = env.latest_run()
    assert env.result(failed)[:2] == ("failed", "PlanTrialError")
    assert "run_failed" in env.alerts(failed)
    assert "halted" not in env.alerts(failed)
    assert env.engaged() == []
    assert env.counts() == before
    assert env.count("orders") == 0
    assert env.submits() == 0

    nxt = env.run(at(F_0_PLUS_1))
    assert nxt.status == "skipped_kill_switch", env.result(nxt.run_id)
    assert nxt.kind == "catch_up"
    assert env.engaged() == []  # engaged by derivation, no row
    assert env.counts() == before  # skipped before step 6: nothing planned
    assert env.count("orders") == 0
    assert env.submits() == 0


def test_an_assets_read_raising_inside_the_planning_step_halts_with_every_halt_row(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window is flat, so the first broker `assets` read is the planning
    step's, for the targets, inside its open transaction. The fake raises
    there; the error goes through the run's classified broker path (no read
    has an allowlist) to the halt path: afterwards the `engaged` row, the
    `halted` result row with the fault's type and the `halted` alert are all
    in the store, and no trial, signal, decision or plan row is."""
    before = env.counts()
    calls: list[tuple[str, ...]] = []

    def broken(symbols: Sequence[str]) -> object:
        calls.append(tuple(symbols))
        raise ConnectionError("assets endpoint down")

    monkeypatch.setattr(env.fake, "assets", broken)
    with pytest.raises(ConnectionError, match="assets endpoint down"):
        env.run(at(F_0))
    run_id = env.latest_run()
    assert env.kind(run_id) == "rebalance"
    assert [sorted(c) for c in calls] == [sorted(SYMBOLS[sid] for sid in TARGETS)]  # the targets
    assert env.engaged() == [("fault", "ConnectionError", run_id)]
    assert env.result(run_id)[:2] == ("halted", "ConnectionError")
    assert "halted" in env.alerts(run_id)
    assert "run_failed" not in env.alerts(run_id)
    assert env.counts() == before
    assert env.count("orders") == 0
    assert env.submits() == 0


# --- re-use and the lag rule -------------------------------------------------------------------


def test_a_catch_up_session_reuses_the_journaled_decisions(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F_0's run is before the submit window: it plans and trades nothing. The
    catch-up on F_0 + 1 is handed the same journaled decisions (`reused`),
    opens no second plan trial and writes no second plan, and orders exactly
    those decisions under its own session's ids."""
    early = env.run(at(F_0, 11, 0))
    assert early.status == "ok", env.result(early.run_id)
    assert env.count("orders") == 0
    planned = env.counts()
    ids = env.decision_ids()
    assert len(ids) == len(TARGETS)

    seen = spy_on_trade_step(env, monkeypatch)
    later = env.run(at(F_0_PLUS_1))
    assert later.status == "ok", env.result(later.run_id)
    assert later.kind == "catch_up"
    ((plan, counts, _),) = seen
    assert plan is not None and plan.status == "reused"
    assert [d.decision_id for d in plan.decisions] == ids
    assert counts == planned
    assert env.counts() == planned  # no re-plan
    assert env.decision_ids() == ids
    orders = env.query("SELECT decision_id, session, run_id FROM orders ORDER BY decision_id")
    assert orders == [(i, F_0_PLUS_1, later.run_id) for i in ids]
    assert env.rebalance_events() == [(T_0, "executed", None, later.run_id)]


def test_a_lagging_fill_leaves_the_rebalance_pending_with_no_plan(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An earlier run's order half filled at the broker, the fill never
    delivered by the feed: the F_0 run finds it `fills_lagging` (inside the
    bound, so no halt), makes no plan (no trial, signal, decision or plan row),
    submits nothing and leaves T_0 pending; the run still ends `ok`."""
    window = env.window
    assert window is not None and window.window_id is not None
    earlier_at = at(T_0, 13, 0)
    (earlier,) = env.append(
        PaperRunRow(
            window_id=window.window_id,
            session=T_0,
            kind="mark",
            started_at=earlier_at,
            invoked_by="scheduler",
            code_version="test",
            known_at=earlier_at,
            ingested_at=earlier_at,
        )
    )
    assert earlier is not None
    done = earlier_at + timedelta(minutes=1)
    env.append(
        PaperRunResultRow(
            run_id=earlier,
            finished_at=done,
            status="ok",
            clock_fault=False,
            known_at=done,
            ingested_at=done,
        )
    )
    (decision_id,) = env.append(
        DecisionRow(
            run_id=earlier,
            rebalance_session=None,
            security_id="SEC_SPY",
            side="buy",
            planned_quantity=2.0,
            whole_share=False,
            decision="forced_exit",
            reason="delisted",
            known_at=earlier_at,
            ingested_at=earlier_at,
        )
    )
    assert decision_id is not None
    coid = "tp-lag"
    env.prices["SPY"] = 100.0
    env.clock.now = earlier_at
    env.fake.lag_fills(None)
    placed = env.fake.submit(OrderRequest(coid, "SPY", Side.BUY, quantity=2.0))
    env.append(
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=earlier,
            session=T_0,
            attempt=1,
            phase="exit",
            security_id="SEC_SPY",
            symbol="SPY",
            side="buy",
            quantity=2.0,
            sells_in_flight_at_submit=False,
            known_at=earlier_at,
            ingested_at=earlier_at,
        ),
        OrderEventRow(
            client_order_id=coid, status="pending", known_at=earlier_at, ingested_at=earlier_at
        ),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=placed.broker_order_id,
            known_at=earlier_at,
            ingested_at=earlier_at,
        ),
    )
    env.fake.apply(coid, PartialFill(1.0, 100.0))
    before = env.counts()
    seen = spy_on_trade_step(env, monkeypatch)

    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert outcome.kind == "rebalance"
    ((plan, _, _),) = seen
    assert plan is not None and plan.status == "lagging"
    assert plan.decisions == ()
    assert env.counts() == before
    assert env.submits() == 1  # the earlier run's order only
    assert env.count("fills") == 0  # never delivered
    assert env.rebalance_events() == []  # T_0 stays pending
    assert env.engaged() == []
    statuses = env.query(
        "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
        [outcome.run_id],
    )
    assert statuses == [("fills_lagging",), ("fills_lagging",)]
    assert any("fills_lagging" in note for note in outcome.notes)
