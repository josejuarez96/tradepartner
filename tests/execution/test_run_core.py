"""The tracking run core (Phase 4 spec req 7, reqs 4, 5 and 11; plan T63).

On a temp-file copy of the fixture store (`journal_settings`) with the
scripted fake and a fixed clock. Two windows:

- the **mark** window: its first rebalance (2019-05-31) is after every
  session used, so each run is a `mark` run. It starts on Friday 2019-05-10
  after the close, flat, with the fake's cash.
- the **rebalance** window: T_0 = 2019-04-30, F_0 = 2019-05-01, on a
  registered hypothesis (set up as `test_planning.py` does), so the run on
  F_0 plans and then reaches the trade step (replaced by `step_fails`; the
  trade step itself is `test_run_trade.py`'s, T63d).

Every run needs an `ok` ingestion run covering S-1 for `SPY` (the fixture's
reference symbol), seeded per test. The step-5 and planning cases at run level
beyond the two smoke cases are T63i's.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import FakeBroker, PartialFill, Reject
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import ReconciliationError, RejectionCapError, StaleDataError
from tradepartner.execution import run as run_module
from tradepartner.execution.lock import run_lock
from tradepartner.execution.run import RunOutcome, invoked_by, tracking_run
from tradepartner.store import registry
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    OverrideRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    ReconciliationRow,
    append,
)

FAKE_CASH = 100_000.0
PRICE = 100.0
SPY = "SEC_SPY"
MARK_START = datetime(2019, 5, 10, 22, 0, tzinfo=UTC)  # Friday, after the close
MARK_FIRST_REBALANCE = date(2019, 5, 31)
MON = date(2019, 5, 13)
TUE = date(2019, 5, 14)
WED = date(2019, 5, 15)
T_0 = date(2019, 4, 30)
F_0 = date(2019, 5, 1)
HOLDOUT_START, HOLDOUT_END = date(2018, 7, 2), date(2019, 3, 29)
IN_SAMPLE_START = date(2017, 1, 3)
MAX_CATCH_UP = 2
PLAN_TABLES = ("trials", "signals", "decisions", "paper_plans")


class Clock:
    """A settable clock (as conftest's `FixedClock`)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _at(day: date, hour: int = 13) -> datetime:
    """`hour`:00 UTC on `day` (13:00 UTC is 09:00 New York in May)."""
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _frozen_json(frozen: RiskConfig) -> str:
    values: dict[str, Any] = {f"risk.{k}": v for k, v in frozen.model_dump(mode="json").items()}
    values["paper.max_catch_up_sessions"] = MAX_CATCH_UP
    return json.dumps(values, sort_keys=True)


@dataclass
class Env:
    settings: Settings
    clock: Clock
    fake: FakeBroker

    def connect(self) -> Any:
        return open_for_write(self.settings)

    def run(self, at: datetime) -> RunOutcome:
        self.clock.now = at
        return tracking_run(self.settings, self.connect, self.fake, self.clock)

    def append(self, *rows: object) -> list[int | None]:
        with open_for_write(self.settings) as conn:
            return [append(conn, row) for row in rows]  # type: ignore[arg-type]

    def query(self, sql: str, params: list[object] | None = None) -> list[tuple[Any, ...]]:
        with open_read_only(self.settings) as conn:
            return conn.execute(sql, params or []).fetchall()

    def count(self, table: str) -> int:
        return int(self.query(f"SELECT count(*) FROM {table}")[0][0])

    def ingest(self, finished: datetime, status: str = "ok") -> None:
        with open_for_write(self.settings) as conn:
            conn.execute(
                "INSERT INTO ingestion_runs (run_id, started_at, finished_at, status, source, "
                "mode) VALUES (?, ?, ?, ?, 'alpaca', 'daily')",
                [
                    f"ingest-{finished.isoformat()}",
                    finished - timedelta(minutes=5),
                    finished,
                    status,
                ],
            )

    def window(
        self,
        *,
        first: date = MARK_FIRST_REBALANCE,
        started: datetime = MARK_START,
        frozen: RiskConfig | None = None,
        hypothesis_id: int = 1,
    ) -> PaperWindowRow:
        row = PaperWindowRow(
            hypothesis_id=hypothesis_id,
            first_rebalance_session=first,
            account_id="PA1",
            starting_cash=FAKE_CASH,
            starting_equity=FAKE_CASH,
            code_version="test",
            started_at=started,
            frozen_json=_frozen_json(frozen or RiskConfig()),
            frozen_sha256="0" * 64,
            known_at=started,
            ingested_at=started,
        )
        (window_id,) = self.append(row)
        return replace(row, window_id=window_id)

    def past_run(self, window: PaperWindowRow, at: datetime, *, status: str | None = "ok") -> int:
        """A run of an earlier session; `status=None` leaves it unfinished."""
        (run_id,) = self.append(
            PaperRunRow(
                window_id=window.window_id,  # type: ignore[arg-type]
                session=at.date(),
                kind="mark",
                started_at=at,
                invoked_by="scheduler",
                code_version="test",
                known_at=at,
                ingested_at=at,
            )
        )
        assert run_id is not None
        if status is not None:
            done = at + timedelta(minutes=1)
            self.append(
                PaperRunResultRow(
                    run_id=run_id,
                    finished_at=done,
                    status=status,
                    clock_fault=False,
                    known_at=done,
                    ingested_at=done,
                )
            )
        return run_id

    def order(
        self,
        run_id: int,
        coid: str,
        at: datetime,
        *,
        quantity: float = 1.0,
        submit: bool = True,
    ) -> OrderRow:
        """A forced-exit buy's decision, `orders` row and `pending` event, and,
        unless `submit=False` (the broker never got it), the fake's submit and
        the `accepted` event."""
        (decision_id,) = self.append(
            DecisionRow(
                run_id=run_id,
                rebalance_session=None,
                security_id=SPY,
                side="buy",
                planned_quantity=quantity,
                whole_share=False,
                decision="forced_exit",
                reason="delisted",
                known_at=at,
                ingested_at=at,
            )
        )
        assert decision_id is not None
        order = OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=at.date(),
            attempt=1,
            phase="exit",
            security_id=SPY,
            symbol="SPY",
            side="buy",
            quantity=quantity,
            sells_in_flight_at_submit=False,
            known_at=at,
            ingested_at=at,
        )
        rows: list[object] = [
            order,
            OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
        ]
        if submit:
            self.clock.now = at
            placed = self.fake.submit(OrderRequest(coid, "SPY", Side.BUY, quantity=quantity))
            rows.append(
                OrderEventRow(
                    client_order_id=coid,
                    status="accepted",
                    broker_order_id=placed.broker_order_id,
                    known_at=at,
                    ingested_at=at,
                )
            )
        self.append(*rows)
        return order

    def results(self) -> dict[int, tuple[str, str | None, str | None]]:
        rows = self.query("SELECT run_id, status, fault_type, message FROM paper_run_results")
        return {r[0]: (r[1], r[2], r[3]) for r in rows}

    def alerts(self) -> list[tuple[str, int | None, date]]:
        return [
            (r[0], r[1], r[2])
            for r in self.query("SELECT kind, run_id, session FROM alerts ORDER BY alert_id")
        ]

    def engaged(self) -> list[tuple[str, str | None, int | None, int | None]]:
        return [
            (r[0], r[1], r[2], r[3])
            for r in self.query(
                "SELECT source, fault_type, run_id, override_id FROM kill_switch "
                "WHERE state = 'engaged' ORDER BY event_id"
            )
        ]

    def latest_run(self) -> int:
        return int(self.query("SELECT max(run_id) FROM paper_runs")[0][0])

    def broker_methods(self) -> list[str]:
        return [call.method for call in self.fake.calls]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # StoreProvider compares the frozen calendar with the live one; no `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)


@pytest.fixture
def env(journal_settings: Settings) -> Env:
    clock = Clock(_at(TUE))
    fake = FakeBroker(
        clock=clock, price_of=lambda _s: PRICE, auto_fill=False, cash=FAKE_CASH, account_id="PA1"
    )
    return Env(journal_settings, clock, fake)


@pytest.fixture
def exits_done(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """The exits stub (T63d) replaced by a recorder, so a `mark` run can end `ok`."""
    calls: list[object] = []
    monkeypatch.setattr(run_module, "exits_step", calls.append)
    return calls


@pytest.fixture
def step_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """The trade and exits steps (T63d) replaced by a step that raises, for the
    `failed` path of a run past step 5."""

    def broken(_context: object) -> None:
        raise RuntimeError("the step failed")

    monkeypatch.setattr(run_module, "exits_step", broken)
    monkeypatch.setattr(run_module, "trade_step", broken)


@pytest.fixture
def rebalance_env(env: Env, tmp_path: Path) -> Iterator[tuple[Env, PaperWindowRow]]:
    """The rebalance window on a registered hypothesis (as test_planning.py)."""
    params = Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": HOLDOUT_START.isoformat(), "end": HOLDOUT_END.isoformat()},
    )
    elsewhere = Settings(_env_file=None, store={"path": str(tmp_path / "elsewhere.duckdb")})
    with open_for_write(env.settings) as conn:
        hypothesis = registry.register_hypothesis(
            conn,
            slug="h-run-core",
            family="momentum",
            title="tracking run core test",
            doc_path="docs/hypotheses/h-run-core.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(params),
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT_START,
            holdout_end=HOLDOUT_END,
            registered_by="test",
            settings=elsewhere,
        )
    window = env.window(first=T_0, started=_at(T_0, 12), hypothesis_id=hypothesis.hypothesis_id)
    yield env, window


def _marked(env: Env, frozen: RiskConfig | None = None) -> PaperWindowRow:
    """The mark window, with the ingest covering Monday for a Tuesday run."""
    window = env.window(frozen=frozen)
    env.ingest(_at(MON, 21))
    return window


# --- entry: lock, no window, no session ------------------------------------------------


def test_a_second_instance_writes_a_locked_alert_and_closes_nothing(env: Env) -> None:
    window = _marked(env)
    crashed = env.past_run(window, _at(MON), status=None)
    with run_lock(env.settings):
        outcome = env.run(_at(TUE))
    assert outcome.status == "locked"
    assert outcome.exit_code != 0
    assert outcome.run_id is None
    assert env.alerts() == [("locked", None, TUE)]
    assert crashed not in env.results()  # only the lock holder may close it
    assert env.count("paper_runs") == 1
    assert env.fake.calls == ()


def test_no_window_before_a_start_alerts_once_per_session_over_a_weekend(env: Env) -> None:
    saturday, sunday = date(2019, 5, 11), date(2019, 5, 12)
    first = env.run(_at(saturday, 15))
    second = env.run(_at(sunday, 15))
    assert first.status == second.status == "no_window"
    assert first.exit_code != 0
    assert env.alerts() == [("no_window", None, MON)]  # the next session, deduped
    assert env.run(_at(MON)).status == "no_window"
    assert env.alerts() == [("no_window", None, MON)]
    assert env.count("paper_runs") == 0
    assert env.fake.calls == ()


def test_no_window_after_a_stop(env: Env) -> None:
    window = _marked(env)
    stop = _at(MON, 22)
    env.append(
        PaperWindowStopRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            at=stop,
            state="closed",
            known_at=stop,
            ingested_at=stop,
        )
    )
    assert env.run(_at(TUE)).status == "no_window"
    assert env.alerts() == [("no_window", None, TUE)]
    assert env.count("paper_runs") == 0
    assert env.fake.calls == ()


def test_a_non_session_ends_no_session_without_a_broker_call(env: Env) -> None:
    window = _marked(env)
    outcome = env.run(_at(date(2019, 5, 11), 15))  # Saturday
    assert outcome.status == "no_session"
    assert outcome.exit_code == 0
    assert env.query("SELECT window_id, session, kind FROM paper_runs") == [
        (window.window_id, None, None)
    ]
    assert env.results()[env.latest_run()][0] == "no_session"
    assert env.fake.calls == ()


# --- step 1 -----------------------------------------------------------------------------


def test_an_unfinished_earlier_run_is_closed_crashed_and_the_run_skips(env: Env) -> None:
    window = _marked(env)
    crashed = env.past_run(window, _at(MON), status=None)
    outcome = env.run(_at(TUE))
    assert outcome.status == "skipped_kill_switch"
    assert outcome.run_id is not None
    results = env.results()
    assert results[crashed][0] == "crashed"
    assert results[outcome.run_id][0] == "skipped_kill_switch"
    assert "submit" not in env.broker_methods()


def test_an_engage_kill_switch_override_engages_at_a_mark_run_once(env: Env) -> None:
    window = _marked(env)
    made = _at(MON, 23)
    (override_id,) = env.append(
        OverrideRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            made_at=made,
            kind="engage_kill_switch",
            reason="owner pauses trading for the test",
            known_at=made,
            ingested_at=made,
        )
    )
    first = env.run(_at(TUE))
    assert first.kind == "mark"
    assert first.status == "skipped_kill_switch"
    assert env.engaged() == [("owner", None, None, override_id)]
    env.ingest(_at(TUE, 21))
    assert env.run(_at(WED)).status == "skipped_kill_switch"
    assert env.engaged() == [("owner", None, None, override_id)]  # once
    assert TUE in {r[0] for r in env.query("SELECT session FROM positions_daily")}


def test_invoked_by_is_scheduler_only_with_the_variable_and_no_tty() -> None:
    assert invoked_by({"TRADEPARTNER_INVOKED_BY": "scheduler"}, stdin_is_tty=False) == "scheduler"
    assert invoked_by({"TRADEPARTNER_INVOKED_BY": "scheduler"}, stdin_is_tty=True) == "tty"
    assert invoked_by({}, stdin_is_tty=False) == "tty"
    assert invoked_by({"TRADEPARTNER_INVOKED_BY": "cron"}, stdin_is_tty=False) == "tty"


def test_the_run_row_records_invoked_by(
    env: Env, monkeypatch: pytest.MonkeyPatch, exits_done: list[object]
) -> None:
    _marked(env)
    monkeypatch.setenv("TRADEPARTNER_INVOKED_BY", "scheduler")
    monkeypatch.setattr(run_module, "_stdin_is_tty", lambda: False)
    env.run(_at(TUE))
    assert env.query("SELECT invoked_by, kind, session FROM paper_runs") == [
        ("scheduler", "mark", TUE)
    ]


def test_a_different_account_id_halts_through_the_halt_path(env: Env) -> None:
    _marked(env)
    env.fake = FakeBroker(
        clock=env.clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        cash=FAKE_CASH,
        account_id="SOMEONE-ELSE",
    )
    with pytest.raises(ReconciliationError, match="account"):
        env.run(_at(TUE))
    run_id = env.latest_run()
    assert env.results()[run_id][:2] == ("halted", "ReconciliationError")
    assert env.engaged() == [("fault", "ReconciliationError", run_id, None)]
    assert ("halted", run_id, TUE) in env.alerts()
    assert env.count("reconciliations") == 0  # halted before step 4
    assert env.count("positions_daily") == 0


# --- step 2: staleness -------------------------------------------------------------------


def test_staleness_halts_before_any_plan_read_or_reconciliation(
    rebalance_env: tuple[Env, PaperWindowRow],
) -> None:
    env, _window = rebalance_env
    env.ingest(_at(date(2019, 4, 29), 21))  # covers 04-29, not S-1 = 04-30
    before = {t: env.count(t) for t in PLAN_TABLES}
    with pytest.raises(StaleDataError, match="SPY"):
        env.run(_at(F_0))
    run_id = env.latest_run()
    assert env.query("SELECT kind FROM paper_runs WHERE run_id = ?", [run_id]) == [("rebalance",)]
    assert env.results()[run_id][:2] == ("stale", "StaleDataError")
    assert ("stale_data", run_id, F_0) in env.alerts()
    assert env.engaged() == []  # a data fault leaves the switch alone
    assert {t: env.count(t) for t in PLAN_TABLES} == before
    assert env.count("reconciliations") == 0
    assert env.count("rebalance_events") == 0  # the rebalance stays pending
    assert not {"positions", "open_orders", "fills", "get_order", "assets"} & set(
        env.broker_methods()
    )


def test_staleness_reads_no_ingest_finished_after_the_clock(env: Env) -> None:
    """No look-ahead: an `ok` ingest finishing after the run's clock reading does
    not count, although its row is already in the store."""
    env.window()
    env.ingest(_at(TUE, 14))  # after the 13:00 run
    with pytest.raises(StaleDataError):
        env.run(_at(TUE))


def test_staleness_reads_no_bar_known_after_the_last_ok_ingest(env: Env) -> None:
    """No look-ahead: the S-1 bar (known 20:00 UTC) must be known by the last
    `ok` ingest's finish; a later failed ingest does not count."""
    env.window()
    env.ingest(_at(MON, 19))
    env.ingest(_at(MON, 22), status="failed")
    with pytest.raises(StaleDataError):
        env.run(_at(TUE))


# --- step 3: collection, the rejection cap, the lag bound ------------------------------------


def test_rejections_over_the_cap_on_an_earlier_run_halt_this_run(env: Env) -> None:
    window = _marked(env, RiskConfig(max_rejections_per_run=1))
    earlier = env.past_run(window, _at(MON))
    for coid in ("tp-a", "tp-b"):
        env.order(earlier, coid, _at(MON))
        env.fake.apply(coid, Reject())
    env.order(earlier, "tp-c", _at(MON))  # still open: not every order rejected
    with pytest.raises(RejectionCapError, match=f"run {earlier}"):
        env.run(_at(TUE))
    run_id = env.latest_run()
    assert env.results()[run_id][:2] == ("halted", "RejectionCapError")
    assert env.engaged() == [("fault", "RejectionCapError", run_id, None)]


def test_every_order_of_a_run_rejected_halts_below_the_cap(env: Env) -> None:
    window = _marked(env)  # the default cap (5) is far above one rejection
    earlier = env.past_run(window, _at(MON))
    env.order(earlier, "tp-a", _at(MON))
    env.fake.apply("tp-a", Reject())
    with pytest.raises(RejectionCapError, match="every order"):
        env.run(_at(TUE))
    assert env.results()[env.latest_run()][0] == "halted"


def _lagging(env: Env) -> str:
    """An earlier run's order partly filled at the broker, its fill never
    delivered by the feed, first listed lagging by a reconciliation on Monday."""
    window = _marked(env)
    earlier = env.past_run(window, _at(MON))
    env.fake.lag_fills(None)
    env.order(earlier, "tp-lag", _at(MON), quantity=2.0)
    env.fake.apply("tp-lag", PartialFill(1.0, PRICE))
    seen = _at(MON, 14)
    env.append(
        ReconciliationRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            run_id=earlier,
            at=seen,
            status="fills_lagging",
            mismatches_json=json.dumps({"lagging": ["tp-lag"]}),
            known_at=seen,
            ingested_at=seen,
        )
    )
    return "tp-lag"


def test_a_lagging_order_past_the_bound_halts_once_then_reports_mismatch(env: Env) -> None:
    coid = _lagging(env)
    with pytest.raises(ReconciliationError, match=coid):
        env.run(_at(TUE))
    first = env.latest_run()
    assert env.results()[first][:2] == ("halted", "ReconciliationError")
    assert env.engaged() == [("fault", "ReconciliationError", first, None)]

    env.ingest(_at(TUE, 21))
    second = env.run(_at(WED))  # already engaged: no new halt, and the run still marks
    assert second.status == "skipped_kill_switch"
    assert env.engaged() == [("fault", "ReconciliationError", first, None)]
    statuses = [
        r[0]
        for r in env.query(
            "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
            [second.run_id],
        )
    ]
    assert statuses[0] == "mismatch"
    assert TUE in {r[0] for r in env.query("SELECT session FROM positions_daily")}


# --- step 4: reconciliation with a pending order -----------------------------------------------


def _not_received_crash(env: Env) -> int:
    """A crashed run whose submit never reached the broker: its order `pending`."""
    window = _marked(env)
    crashed = env.past_run(window, _at(MON), status=None)
    env.order(crashed, "tp-lost", _at(MON), submit=False)
    return crashed


def test_a_pending_order_is_never_read_and_the_run_does_not_halt(env: Env) -> None:
    crashed = _not_received_crash(env)
    outcome = env.run(_at(TUE))
    assert outcome.status == "skipped_kill_switch"
    assert env.results()[crashed][0] == "crashed"
    reads = [c for c in env.fake.calls if c.method == "get_order"]
    assert all("tp-lost" not in c.args for c in reads)
    assert env.query("SELECT status FROM reconciliations WHERE run_id = ?", [outcome.run_id]) == [
        ("pending_unresolved",)
    ]
    assert env.engaged() == []  # engaged by derivation from the crash, no new row


def test_a_pending_order_does_not_hide_an_unrelated_mismatch(env: Env) -> None:
    _not_received_crash(env)
    env.fake.submit(OrderRequest("owner-1", "SPY", Side.BUY, quantity=1.0))  # foreign
    with pytest.raises(ReconciliationError):
        env.run(_at(TUE))
    run_id = env.latest_run()
    assert env.results()[run_id][:2] == ("halted", "ReconciliationError")
    assert env.query("SELECT status FROM reconciliations WHERE run_id = ?", [run_id]) == [
        ("mismatch",)
    ]


# --- steps 5 to 9 ----------------------------------------------------------------------------


def test_a_mark_run_writes_marks_then_fails_in_the_exits_step_alert_first(
    env: Env, step_fails: None
) -> None:
    _marked(env)
    try:
        env.run(_at(TUE))
    except RuntimeError:
        # The store, inspected before the exception has left the run's caller.
        run_id = env.latest_run()
        assert ("run_failed", run_id, TUE) in env.alerts()
        assert env.results()[run_id][:2] == ("failed", "RuntimeError")
    else:
        pytest.fail("the exits step did not raise")
    marks = env.query("SELECT session, security_id, quantity, cash FROM positions_daily")
    assert marks == [
        (date(2019, 5, 10), None, 0.0, FAKE_CASH),
        (MON, None, 0.0, FAKE_CASH),
    ]
    assert ("missed_run", env.latest_run(), TUE) in env.alerts()
    assert env.engaged() == []  # `failed` engages through the derived state only


def test_the_next_run_derives_engaged_from_a_failed_run(env: Env, step_fails: None) -> None:
    _marked(env)
    with pytest.raises(RuntimeError):
        env.run(_at(TUE))
    env.ingest(_at(TUE, 21))
    assert env.run(_at(WED)).status == "skipped_kill_switch"


def test_an_ok_run_reconciles_twice_and_ends_ok(env: Env, exits_done: list[object]) -> None:
    _marked(env)
    outcome = env.run(_at(TUE))
    assert outcome.status == "ok"
    assert outcome.exit_code == 0
    assert outcome.run_id is not None
    assert len(exits_done) == 1
    assert env.results()[outcome.run_id][0] == "ok"
    assert env.query("SELECT status FROM reconciliations WHERE run_id = ?", [outcome.run_id]) == [
        ("ok",),
        ("ok",),
    ]
    assert "submit" not in env.broker_methods()


def test_a_rebalance_session_writes_decisions_before_the_trade_step(
    rebalance_env: tuple[Env, PaperWindowRow], step_fails: None
) -> None:
    env, _window = rebalance_env
    env.ingest(_at(T_0, 21))
    with pytest.raises(RuntimeError, match="the step failed"):
        env.run(_at(F_0))
    run_id = env.latest_run()
    assert env.query("SELECT kind FROM paper_runs WHERE run_id = ?", [run_id]) == [("rebalance",)]
    assert env.count("decisions") > 0
    assert env.query("SELECT rebalance_session FROM paper_plans") == [(T_0,)]
    assert env.results()[run_id][:2] == ("failed", "RuntimeError")
    assert ("run_failed", run_id, F_0) in env.alerts()
    assert env.count("orders") == 0


# --- the broker-facing path halts; secrets are masked ---------------------------------------


def _hold_spy(env: Env, window: PaperWindowRow) -> None:
    """One SPY share in the ledger (a receipt) and at the fake (a filled foreign
    order, paid from extra cash), so the run's `assets` read has a name."""
    from tradepartner.store.journal import AdjustmentRow

    env.fake = FakeBroker(
        clock=env.clock,
        price_of=lambda _s: PRICE,
        auto_fill=False,
        cash=FAKE_CASH + PRICE,
        account_id="PA1",
    )
    env.clock.now = _at(MON)
    env.fake.submit(OrderRequest("seed-1", "SPY", Side.BUY, quantity=1.0))
    env.fake.simulate_fill("seed-1")
    env.append(
        AdjustmentRow(
            window_id=window.window_id,  # type: ignore[arg-type]
            session=MON,
            kind="spinoff_receipt",
            security_id=SPY,
            quantity=1.0,
            known_at=_at(MON),
            ingested_at=_at(MON),
        )
    )


@pytest.mark.parametrize("method", ["account", "get_order", "positions", "assets"])
def test_a_broker_read_that_raises_halts_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    window = _marked(env)
    if method == "get_order":
        env.order(env.past_run(window, _at(MON)), "tp-open", _at(MON))
    if method == "assets":
        _hold_spy(env, window)

    def broken(*_args: object) -> object:
        raise ConnectionError(f"{method} endpoint down")

    monkeypatch.setattr(env.fake, method, broken)
    with pytest.raises(ConnectionError, match=method):
        env.run(_at(TUE))
    run_id = env.latest_run()
    assert env.results()[run_id][:2] == ("halted", "ConnectionError")
    assert env.engaged() == [("fault", "ConnectionError", run_id, None)]
    kinds = [kind for kind, alert_run, _ in env.alerts() if alert_run == run_id]
    assert "halted" in kinds
    assert "run_failed" not in kinds


def test_an_assets_read_that_raises_inside_planning_halts_the_run(
    rebalance_env: tuple[Env, PaperWindowRow], monkeypatch: pytest.MonkeyPatch
) -> None:
    env, _window = rebalance_env
    env.ingest(_at(T_0, 21))

    def broken(*_args: object) -> object:
        raise ConnectionError("assets endpoint down")

    monkeypatch.setattr(env.fake, "assets", broken)  # the window is flat: planning reads it
    with pytest.raises(ConnectionError):
        env.run(_at(F_0))
    run_id = env.latest_run()
    assert env.results()[run_id][:2] == ("halted", "ConnectionError")
    assert env.engaged() == [("fault", "ConnectionError", run_id, None)]
    assert "run_failed" not in [kind for kind, _, _ in env.alerts()]
    assert {t: env.count(t) for t in ("signals", "decisions", "paper_plans")} == {
        "signals": 0,
        "decisions": 0,
        "paper_plans": 0,
    }


def test_a_secret_in_a_failure_is_masked_in_the_result_row_and_the_alert(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "Ab\\cDef-9876xyz"  # a backslash, so its repr-escaped form differs
    env.settings = Settings(
        _env_file=None, store={"path": env.settings.store.path}, alpaca_paper_api_secret=secret
    )
    _marked(env)

    def leaky(_context: object) -> None:
        raise RuntimeError(f"adapter said {secret} / {secret.upper()} / {secret!r}")

    monkeypatch.setattr(run_module, "exits_step", leaky)
    with pytest.raises(RuntimeError):
        env.run(_at(TUE))
    (result,) = env.query("SELECT message FROM paper_run_results")
    (alert,) = env.query("SELECT message FROM alerts WHERE kind = 'run_failed'")
    escaped = repr(secret)[1:-1]
    for text in (result[0], alert[0]):
        assert "***" in text
        for form in (secret, secret.upper(), escaped, escaped.upper()):
            assert form not in text


def test_a_late_fill_collected_at_step_3_does_not_halt_step_4(
    env: Env, exits_done: list[object]
) -> None:
    """Step 4 reads the journal as of a clock reading taken just before it, so a
    fill of an earlier run's order that step 3 collects today (known after
    close(S-1)) is in the ledger it reconciles (#488's corrected guidance)."""
    window = _marked(env)
    earlier = env.past_run(window, _at(MON))
    env.order(earlier, "tp-late", _at(MON))
    env.fake.simulate_fill("tp-late")  # filled Monday; nothing collected it yet
    outcome = env.run(_at(TUE))
    assert outcome.status == "ok"
    known = env.query("SELECT known_at FROM fills WHERE client_order_id = 'tp-late'")
    assert [k[0] > _at(MON, 20) for k in known] == [True]  # collected after close(S-1)
    assert env.query("SELECT status FROM reconciliations WHERE run_id = ?", [outcome.run_id]) == [
        ("ok",),
        ("ok",),
    ]
