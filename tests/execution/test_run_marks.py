"""Step 5 of the tracking run at run level: the marks, the drawdown check, the
`missed_run` alert, the lapses and the due outcomes (Phase 4 spec req 7 step 5,
reqs 5 and 8; plan T63i).

Every case runs `tracking_run` on a temp-file copy of the fixture store with the
scripted fake and a settable clock (the shape of `test_run_trade.py`, T63d).
Two windows on a registered hypothesis:

- the **rebalance** window: T_0 = 2019-04-30, F_0 = 2019-05-01. Its plan at
  close(T_0) buys DUALB, SPFT and TRNS a third each from a flat start; the fake
  fills every open order when the run sleeps, at the store's close of S-1.
- the **mark** window: first rebalance 2019-05-31, started Friday 2019-05-10
  after the close, so every run in mid-May is a `mark` run.

May and June 2019 are EDT: a run at 12:30 UTC is inside the submit window.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.calendar import previous_session
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import run as run_module
from tradepartner.execution import switch
from tradepartner.execution.run import RunOutcome, StepContext, tracking_run
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write, open_read_only
from tradepartner.store.journal import PaperWindowRow, append

T_0, F_0 = date(2019, 4, 30), date(2019, 5, 1)
T_1, F_1 = date(2019, 5, 31), date(2019, 6, 3)
MARK_START = datetime(2019, 5, 10, 22, 0, tzinfo=UTC)  # Friday, after the close
MON, TUE = date(2019, 5, 13), date(2019, 5, 14)
HOLDOUT_START, HOLDOUT_END = date(2018, 7, 2), date(2019, 3, 29)
IN_SAMPLE_START = date(2017, 1, 3)
MAX_CATCH_UP = 2
FAKE_CASH = 100_000.0
SYMBOLS = {
    "SEC_DUAL_B": "DUALB",
    "SEC_SPLIT_FUTURE": "SPFT",
    "SEC_TRANSFER": "TRNS",
    "SEC_SPY": "SPY",
}
TARGETS = ("SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER")
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

    def run(self, now: datetime, prices: dict[str, float] | None = None) -> RunOutcome:
        """One run at `now`, with an ingest covering S-1 and the fake pricing at
        the closes of S-1 (or `prices`, by symbol)."""
        session = now.date()
        self.ingest(at(previous_session(session), 21, 0))
        for sid, symbol in SYMBOLS.items():
            close = self.close(sid, previous_session(session))
            if close is not None:
                self.prices[symbol] = close
        self.prices.update(prices or {})
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

    def open_window(
        self,
        tmp_path: Path,
        *,
        first: date = T_0,
        started: datetime | None = None,
        starting_equity: float = FAKE_CASH,
    ) -> PaperWindowRow:
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
                slug="h-run-marks",
                family="momentum",
                title="tracking run marks test",
                doc_path="docs/hypotheses/h-run-marks.md",
                doc_sha256="0" * 64,
                params=frozen_params_of(params),
                in_sample_start=IN_SAMPLE_START,
                holdout_start=HOLDOUT_START,
                holdout_end=HOLDOUT_END,
                registered_by="test",
                settings=elsewhere,
            )
        started = started or at(T_0, 12, 0)
        values: dict[str, Any] = {f"risk.{k}": v for k, v in FROZEN.model_dump(mode="json").items()}
        values["paper.max_catch_up_sessions"] = MAX_CATCH_UP
        row = PaperWindowRow(
            hypothesis_id=hypothesis.hypothesis_id,
            first_rebalance_session=first,
            account_id="PA1",
            starting_cash=FAKE_CASH,
            starting_equity=starting_equity,
            code_version="test",
            started_at=started,
            frozen_json=json.dumps(values, sort_keys=True),
            frozen_sha256="0" * 64,
            known_at=started,
            ingested_at=started,
        )
        with open_for_write(self.settings) as conn:
            window_id = append(conn, row)
        self.window = replace(row, window_id=window_id)
        return self.window

    # --- reads -------------------------------------------------------------------

    def query(self, sql: str, params: list[object] | None = None) -> list[tuple[Any, ...]]:
        with open_read_only(self.settings) as conn:
            return conn.execute(sql, params or []).fetchall()

    def count(self, table: str) -> int:
        return int(self.query(f"SELECT count(*) FROM {table}")[0][0])

    def close(self, security_id: str, session: date) -> float | None:
        rows = self.query(
            "SELECT close FROM prices_daily WHERE security_id = ? AND session = ? "
            "ORDER BY known_at DESC LIMIT 1",
            [security_id, session],
        )
        return float(rows[0][0]) if rows else None

    def latest_run(self) -> int:
        return int(self.query("SELECT max(run_id) FROM paper_runs")[0][0])

    def result(self, run_id: int | None) -> tuple[str, str | None, str | None]:
        (row,) = self.query(
            "SELECT status, fault_type, message FROM paper_run_results WHERE run_id = ?", [run_id]
        )
        return row[0], row[1], row[2]

    def marks(self, run_id: int | None) -> list[tuple[date, str | None, float, Any, Any, Any]]:
        """(session, security_id, quantity, mark_price, value, cash) of one run."""
        return [
            (r[0], r[1], r[2], r[3], r[4], r[5])
            for r in self.query(
                "SELECT session, security_id, quantity, mark_price, value, cash "
                "FROM positions_daily WHERE run_id = ? ORDER BY session, security_id NULLS FIRST",
                [run_id],
            )
        ]

    def held(self) -> dict[str, float]:
        """The journaled fills' quantity per security."""
        rows = self.query(
            "SELECT o.security_id, sum(f.quantity) FROM fills f "
            "JOIN orders o USING (client_order_id) GROUP BY 1"
        )
        return {r[0]: float(r[1]) for r in rows}

    def rebalance_events(self) -> list[tuple[date, str, str | None, int]]:
        return [
            (r[0], r[1], r[2], r[3])
            for r in self.query(
                "SELECT rebalance_session, status, reason, run_id FROM rebalance_events "
                "ORDER BY known_at, rebalance_session"
            )
        ]

    def alerts(self, kind: str) -> list[tuple[int | None, date, str]]:
        return [
            (r[0], r[1], r[2])
            for r in self.query(
                "SELECT run_id, session, message FROM alerts WHERE kind = ? ORDER BY alert_id",
                [kind],
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

    def submits(self) -> int:
        return sum(1 for c in self.fake.calls if c.method == "submit")


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # StoreProvider compares the frozen calendar with the live one; no `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)


@pytest.fixture
def env(fixture_store_path: Path) -> Env:
    settings = Settings(
        _env_file=None,
        store={"path": str(fixture_store_path)},
        alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48},
    )
    return Env(settings, Clock(at(F_0)))


def bought(env: Env, tmp_path: Path) -> RunOutcome:
    """The rebalance window, its F_0 rebalance traded and filled."""
    env.open_window(tmp_path)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert set(env.held()) == set(TARGETS)
    return outcome


def snapshot_at[R](monkeypatch: pytest.MonkeyPatch, step: str, read: Callable[[], R]) -> list[R]:
    """Replace `run_module.<step>` by a wrapper that records `read()` (the store
    as step 6 finds it, so before the run returns) and then runs the step."""
    original = getattr(run_module, step)
    seen: list[R] = []

    def wrapped(context: StepContext) -> object:
        seen.append(read())
        return original(context)

    monkeypatch.setattr(run_module, step, wrapped)
    return seen


# --- the marks -------------------------------------------------------------------------


def test_a_mark_session_writes_marks_and_no_order(env: Env, tmp_path: Path) -> None:
    env.open_window(tmp_path, first=T_1, started=MARK_START)
    outcome = env.run(at(MON))
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert outcome.kind == "mark"
    # From the window's start date through S-1: the one cash-only row.
    assert env.marks(outcome.run_id) == [(date(2019, 5, 10), None, 0.0, None, None, FAKE_CASH)]
    assert env.count("orders") == 0
    assert env.count("decisions") == 0
    assert env.submits() == 0
    assert env.alerts("missed_run") == []


def test_two_sessions_without_a_run_are_back_filled_by_the_next_run(
    env: Env, tmp_path: Path
) -> None:
    """F_0's run marks T_0; no run on 05-02 or 05-03; the run on 05-06 marks
    05-01, 05-02 and 05-03, each name at its ledger quantity and the raw close
    of that session, each session once, and alerts `missed_run` for 05-03."""
    bought(env, tmp_path)
    held = env.held()
    later = env.run(at(date(2019, 5, 6)))
    assert later.status == "ok", env.result(later.run_id)
    assert later.kind == "mark"
    rows = env.marks(later.run_id)
    days = [F_0, date(2019, 5, 2), date(2019, 5, 3)]
    assert sorted({r[0] for r in rows}) == days
    for day in days:
        names = {r[1]: r for r in rows if r[0] == day and r[1] is not None}
        assert set(names) == set(TARGETS)
        for sid, (_, _, quantity, mark_price, value, _) in names.items():
            close = env.close(sid, day)
            assert quantity == pytest.approx(held[sid])
            assert mark_price == pytest.approx(close)
            assert value == pytest.approx(held[sid] * close)  # type: ignore[operator]
    per_name = env.query("SELECT session, security_id, count(*) FROM positions_daily GROUP BY 1, 2")
    assert {n for _, _, n in per_name} == {1}  # no session marked twice
    assert sorted({r[0] for r in env.query("SELECT session FROM positions_daily")}) == [
        T_0,
        *days,
    ]
    ((run_id, session, message),) = env.alerts("missed_run")
    assert (run_id, session) == (later.run_id, date(2019, 5, 6))
    assert "2019-05-03" in message


# --- a split on the session ----------------------------------------------------------------


def split_trns(env: Env, ex_date: date) -> None:
    """A 2:1 split of TRNS with `ex_date`, known before close(F_0), booked by
    the broker before the open of `ex_date` (the fake has no corporate actions)."""
    with open_for_write(env.settings) as conn:
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": "SEC_TRANSFER",
                "action_type": "split",
                "ex_date": ex_date,
                "ratio_or_amount": 2.0,
                "announced_at": None,
                "source_action_id": "test-split-trns",
                "cancelled": False,
                "known_at": at(F_0, 19, 0),
                "ingested_at": at(F_0, 19, 0),
                "source": "alpaca",
                "provenance": "action",
            },
        )
    env.fake._net_quantity["TRNS"] *= 2


def test_a_split_on_the_session_marks_pre_split_and_reconciles_doubled(
    env: Env, tmp_path: Path
) -> None:
    """A 2:1 split of TRNS with `ex_date = S` (known before close(S-1)): the run
    on S marks S-1 with the pre-split quantity and the raw close (no jump),
    reconciles on S against the broker's doubled holding, and sells nothing,
    breaches nothing and leaves the drawdown state alone (spec acceptance
    "Split on the session")."""
    bought(env, tmp_path)
    s = date(2019, 5, 2)
    held = env.held()
    split_trns(env, s)
    raw = env.close("SEC_TRANSFER", F_0)
    assert raw is not None
    submits = env.submits()
    outcome = env.run(at(s), prices={"TRNS": raw / 2.0})
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert outcome.kind == "mark"
    rows = env.marks(outcome.run_id)
    assert {r[0] for r in rows} == {F_0}  # through S-1 only
    names = {r[1]: r for r in rows if r[1] is not None}
    _, _, quantity, mark_price, value, _ = names["SEC_TRANSFER"]
    assert quantity == pytest.approx(held["SEC_TRANSFER"])  # pre-split
    assert mark_price == pytest.approx(raw)  # raw close, not divided
    assert value == pytest.approx(held["SEC_TRANSFER"] * raw)
    assert env.query(
        "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
        [outcome.run_id],
    ) == [("ok",), ("ok",)]
    assert env.submits() == submits  # no sell
    assert env.count("orders") == len(TARGETS)
    assert env.query("SELECT count(*) FROM kill_switch") == [(0,)]  # no breach, no drawdown
    assert env.alerts("missed_run") == []  # F_0 had its run


def test_a_split_inside_the_back_filled_sessions_marks_each_on_its_own_basis(
    env: Env, tmp_path: Path
) -> None:
    """The same 2:1 TRNS split with `ex_date` 05-02, but no run on 05-02 or
    05-03: the run on 05-06 marks 05-01 at the pre-split quantity and 05-02 and
    05-03 at the doubled one, each at that session's raw close (never S-1's
    quantity on every back-filled session, never the split ignored), and
    reconciles `ok` against the broker's doubled holding."""
    bought(env, tmp_path)
    held = env.held()["SEC_TRANSFER"]
    split_trns(env, date(2019, 5, 2))
    outcome = env.run(at(date(2019, 5, 6)))
    assert outcome.status == "ok", env.result(outcome.run_id)
    trns = {r[0]: r for r in env.marks(outcome.run_id) if r[1] == "SEC_TRANSFER"}
    expected = {F_0: held, date(2019, 5, 2): 2 * held, date(2019, 5, 3): 2 * held}
    assert set(trns) == set(expected)
    for day, quantity in expected.items():
        close = env.close("SEC_TRANSFER", day)
        assert close is not None
        _, _, marked, mark_price, value, _ = trns[day]
        assert marked == pytest.approx(quantity), day
        assert mark_price == pytest.approx(close), day
        assert value == pytest.approx(quantity * close), day
    assert env.query(
        "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
        [outcome.run_id],
    ) == [("ok",), ("ok",)]
    assert env.count("orders") == len(TARGETS)
    assert env.query("SELECT count(*) FROM kill_switch") == [(0,)]


# --- the drawdown check -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("divisor", "fires"),
    [
        pytest.param(0.69, True, id="below-the-line"),
        pytest.param(0.70, False, id="at-the-threshold"),
        pytest.param(0.71, False, id="above-the-line"),
    ],
)
def test_the_drawdown_check_engages_with_source_drawdown_and_its_alert(
    env: Env, tmp_path: Path, divisor: float, fires: bool
) -> None:
    """The flat mark window's equity (its cash) against a peak set by its
    `starting_equity`: below `peak * (1 - risk.max_drawdown)` (0.30) the run
    engages the switch with source `drawdown`, alerts, and skips; at or above
    the line nothing engages. A second run does not engage again."""
    assert FROZEN.max_drawdown == pytest.approx(0.30)
    env.open_window(tmp_path, first=T_1, started=MARK_START, starting_equity=FAKE_CASH / divisor)
    outcome = env.run(at(MON))
    if not fires:
        assert outcome.status == "ok", env.result(outcome.run_id)
        assert env.engaged() == []
        assert env.alerts("drawdown") == []
        return
    assert outcome.status == "skipped_kill_switch", env.result(outcome.run_id)
    assert env.engaged() == [("drawdown", None, outcome.run_id)]
    ((run_id, session, message),) = env.alerts("drawdown")
    assert (run_id, session) == (outcome.run_id, MON)
    assert "max_drawdown" in message
    assert env.submits() == 0
    again = env.run(at(TUE))
    assert again.status == "skipped_kill_switch"
    assert env.engaged() == [("drawdown", None, outcome.run_id)]  # once per crossing
    assert len(env.alerts("drawdown")) == 1


# --- the lapses -------------------------------------------------------------------------------


def test_two_lapsed_rebalances_get_a_row_each_and_one_alert_before_the_run_returns(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No run from T_0 until 06-06, past both T_0's and T_1's catch-up bound
    (F + 2 sessions): the run writes a `missed` row with reason
    `catch_up_lapsed` for each, and one `missed_rebalance` alert naming both,
    all in the store by step 6 (snapshot in `exits_step`)."""
    env.open_window(tmp_path)
    s = date(2019, 6, 6)

    def read() -> tuple[
        list[tuple[date, str, str | None, int]], list[tuple[int | None, date, str]]
    ]:
        return env.rebalance_events(), env.alerts("missed_rebalance")

    seen = snapshot_at(monkeypatch, "exits_step", read)
    outcome = env.run(at(s))
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert outcome.kind == "mark"
    expected = [
        (T_0, "missed", "catch_up_lapsed", outcome.run_id),
        (T_1, "missed", "catch_up_lapsed", outcome.run_id),
    ]
    assert len(seen) == 1
    events, alerts = seen[0]
    assert events == expected
    ((run_id, session, message),) = alerts
    assert (run_id, session) == (outcome.run_id, s)
    assert T_0.isoformat() in message and T_1.isoformat() in message
    assert env.rebalance_events() == expected
    assert env.count("orders") == 0
    assert env.count("decisions") == 0


def test_a_rebalance_skipped_by_the_switch_lapses_with_reason_kill_switch(
    env: Env, tmp_path: Path
) -> None:
    """The switch engaged over F_0's span: T_0's lapse on the next run past the
    catch-up bound carries reason `kill_switch`, with its alert, although that
    run ends `skipped_kill_switch` at step 6."""
    window = env.open_window(tmp_path)
    assert window.window_id is not None
    switch.engage(
        env.settings,
        env.clock,
        window_id=window.window_id,
        source="owner",
        reason="the owner pauses the window",
    )
    assert env.run(at(F_0)).status == "skipped_kill_switch"
    later = env.run(at(date(2019, 5, 6)))
    assert later.status == "skipped_kill_switch"
    assert env.rebalance_events() == [(T_0, "missed", "kill_switch", later.run_id)]
    ((run_id, session, message),) = env.alerts("missed_rebalance")
    assert (run_id, session) == (later.run_id, date(2019, 5, 6))
    assert "kill_switch" in message
    assert env.count("orders") == 0


# --- the outcomes -------------------------------------------------------------------------------


def test_outcomes_are_written_at_the_first_run_after_close_t_1(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F_0's three buys belong to rebalance T_0, so their horizon is close(T_1):
    the run on T_1 itself writes no outcome; the run on F_1 writes one
    `position_return` per order at step 5, before its trade step, from the
    T_1 mark over the order's average fill price."""
    bought(env, tmp_path)
    on_t_1 = env.run(at(T_1))
    assert on_t_1.status == "ok", env.result(on_t_1.run_id)
    assert env.count("outcomes") == 0  # not due yet: S is not after close(T_1)

    def read() -> list[tuple[Any, ...]]:
        return env.query(
            "SELECT client_order_id, through_session, kind, value, mark_price FROM outcomes "
            "ORDER BY client_order_id"
        )

    seen: list[list[tuple[Any, ...]]] = []

    def trade_step(_context: StepContext) -> None:  # records, trades nothing
        seen.append(read())

    monkeypatch.setattr(run_module, "trade_step", trade_step)
    outcome = env.run(at(F_1))
    assert outcome.status == "ok", env.result(outcome.run_id)
    assert outcome.kind == "rebalance"
    (rows,) = seen
    fills = {
        r[0]: (r[1], float(r[2]))
        for r in env.query(
            "SELECT f.client_order_id, o.security_id, sum(f.quantity * f.price) / sum(f.quantity) "
            "FROM fills f JOIN orders o USING (client_order_id) GROUP BY 1, 2"
        )
    }
    assert len(fills) == len(TARGETS)
    assert [r[0] for r in rows] == sorted(fills)
    for coid, through, kind, value, mark_price in rows:
        sid, price = fills[coid]
        close = env.close(sid, T_1)
        assert close is not None
        assert (through, kind) == (T_1, "position_return")
        assert mark_price == pytest.approx(close)
        assert value == pytest.approx(close / price - 1.0)
    assert env.count("outcomes") == len(TARGETS)
