"""The tracking run's trade step and step 7b (Phase 4 spec req 7 steps 6 and 7b,
req 3; plan T63d).

Every case runs `tracking_run` on a temp-file copy of the fixture store
(`journal_settings`) with the scripted fake and a settable clock that the
injected `sleep` advances. The window is the rebalance window of
`test_run_core.py`: T_0 = 2019-04-30, F_0 = 2019-05-01, on a registered
hypothesis whose plan at close(T_0) buys three names (DUALB, SPFT, TRNS) a
third each from a flat start. The frozen `risk` section loosens the position
and order limits so a third of equity is one order.

The fake fills at the store's close of S-1 (the reference price), so the marks
and the reconciliation agree with the fills. By default it fills nothing at
submit: every open order fills when the run sleeps (`Env.fill_on_sleep`), so
step 7b is what collects the buys. May 2019 is EDT: the open is 13:30 UTC and
the submit window [12:00, 14:00] UTC.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.broker import Asset, OrderRequest
from tradepartner.adapters.fake_broker import FakeBroker, PartialFill
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.calendar import next_session, previous_session, session_close, session_open
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import LimitBreachError
from tradepartner.execution import run as run_module
from tradepartner.execution import switch
from tradepartner.execution.run import RunOutcome, submit_window, tracking_run
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write, open_read_only
from tradepartner.store.journal import (
    OverrideRow,
    PaperWindowRow,
    append,
)

T_0 = date(2019, 4, 30)
F_0 = date(2019, 5, 1)
F_0_PLUS_1 = date(2019, 5, 2)
T_PREV = date(2019, 3, 29)  # T_{-1}: the window starts after its close
F_PREV = date(2019, 4, 1)
HOLDOUT_START, HOLDOUT_END = date(2018, 7, 2), date(2019, 3, 29)
IN_SAMPLE_START = date(2017, 1, 3)
MAX_CATCH_UP = 2
FAKE_CASH = 100_000.0
SYMBOLS = {
    "SEC_DUAL_B": "DUALB",
    "SEC_SPLIT_FUTURE": "SPFT",
    "SEC_TRANSFER": "TRNS",
    "SEC_SPY": "SPY",
    "SEC_WINDOW_DELIST": "WNDX",
    "SEC_STATIC_PRE2019": "PRE9",
    "SEC_DUAL_A": "DUALA",
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
    """`hour`:`minute` UTC on `day` (12:30 UTC is 08:30 New York in May, an
    hour before the open and inside the submit window)."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def frozen_json(frozen: RiskConfig) -> str:
    values: dict[str, Any] = {f"risk.{k}": v for k, v in frozen.model_dump(mode="json").items()}
    values["paper.max_catch_up_sessions"] = MAX_CATCH_UP
    return json.dumps(values, sort_keys=True)


@dataclass
class Env:
    """The store, the clock, the fake and the window; `run` is one `paper run`."""

    settings: Settings
    clock: Clock
    prices: dict[str, float] = field(default_factory=dict)
    fill_on_sleep: bool = True
    on_sleep: list[Callable[[datetime], None]] = field(default_factory=list)
    slept: list[float] = field(default_factory=list)
    window: PaperWindowRow | None = None
    fake: FakeBroker = field(init=False)

    def __post_init__(self) -> None:
        self.new_fake()

    def new_fake(self, **kwargs: Any) -> FakeBroker:
        self.fake = FakeBroker(
            clock=self.clock,
            price_of=lambda symbol: self.prices[symbol],
            auto_fill=False,
            cash=kwargs.pop("cash", FAKE_CASH),
            account_id="PA1",
            **kwargs,
        )
        return self.fake

    def connect(self) -> Any:
        return open_for_write(self.settings)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.now += timedelta(seconds=seconds)
        for hook in self.on_sleep:
            hook(self.clock.now)
        if self.fill_on_sleep:
            self.fill_open()

    def fill_open(self) -> None:
        for order in self.fake.open_orders():
            self.fake.simulate_fill(order.client_order_id)

    def run(self, now: datetime, prices: dict[str, float] | None = None) -> RunOutcome:
        """One run at `now`, with an ingest covering S-1 and the fake pricing at
        the closes of S-1 (or `prices`, by symbol)."""
        session = now.date()
        self.ingest(at(previous_session(session), 21, 0))
        self.price_at(session)
        self.prices.update(prices or {})
        self.clock.now = now
        return tracking_run(self.settings, self.connect, self.fake, self.clock, sleep=self.sleep)

    def price_at(self, session: date) -> None:
        """The fake prices every symbol at its store close of S-1."""
        rows = self.query(
            "SELECT security_id, close FROM prices_daily WHERE session = ?",
            [previous_session(session)],
        )
        closes = {sid: close for sid, close in rows}
        for sid, symbol in SYMBOLS.items():
            if sid in closes:
                self.prices[symbol] = float(closes[sid])

    def append(self, *rows: object) -> list[int | None]:
        with open_for_write(self.settings) as conn:
            return [append(conn, row) for row in rows]  # type: ignore[arg-type]

    def insert(self, table: str, row: dict[str, Any]) -> None:
        with open_for_write(self.settings) as conn:
            insert_row(conn, table, row)

    def query(self, sql: str, params: list[object] | None = None) -> list[tuple[Any, ...]]:
        with open_read_only(self.settings) as conn:
            return conn.execute(sql, params or []).fetchall()

    def count(self, table: str) -> int:
        return int(self.query(f"SELECT count(*) FROM {table}")[0][0])

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
        *,
        first: date = T_0,
        started: datetime | None = None,
        frozen: RiskConfig = FROZEN,
        tmp_path: Path,
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
                slug="h-run-trade",
                family="momentum",
                title="tracking run trade step test",
                doc_path="docs/hypotheses/h-run-trade.md",
                doc_sha256="0" * 64,
                params=frozen_params_of(params),
                in_sample_start=IN_SAMPLE_START,
                holdout_start=HOLDOUT_START,
                holdout_end=HOLDOUT_END,
                registered_by="test",
                settings=elsewhere,
            )
        started = started or at(T_0, 12, 0)
        row = PaperWindowRow(
            hypothesis_id=hypothesis.hypothesis_id,
            first_rebalance_session=first,
            account_id="PA1",
            starting_cash=FAKE_CASH,
            starting_equity=FAKE_CASH,
            code_version="test",
            started_at=started,
            frozen_json=frozen_json(frozen),
            frozen_sha256="0" * 64,
            known_at=started,
            ingested_at=started,
        )
        (window_id,) = self.append(row)
        self.window = replace(row, window_id=window_id)
        return self.window

    # --- reads -------------------------------------------------------------------

    def latest_run(self) -> int:
        return int(self.query("SELECT max(run_id) FROM paper_runs")[0][0])

    def result(self, run_id: int) -> tuple[str, str | None, str | None]:
        (row,) = self.query(
            "SELECT status, fault_type, message FROM paper_run_results WHERE run_id = ?", [run_id]
        )
        return row[0], row[1], row[2]

    def rebalance_events(self) -> list[tuple[date, str, str | None, int]]:
        return [
            (r[0], r[1], r[2], r[3])
            for r in self.query(
                "SELECT rebalance_session, status, reason, run_id FROM rebalance_events "
                "ORDER BY known_at, rebalance_session"
            )
        ]

    def orders(self) -> list[tuple[str, str, str, str, date, int]]:
        """(client_order_id, security_id, side, phase, session, run_id) by id."""
        return [
            (r[0], r[1], r[2], r[3], r[4], r[5])
            for r in self.query(
                "SELECT client_order_id, security_id, side, phase, session, run_id FROM orders "
                "ORDER BY known_at, client_order_id"
            )
        ]

    def submits(self) -> list[OrderRequest]:
        return [c.args[0] for c in self.fake.calls if c.method == "submit"]  # type: ignore[misc]

    def alerts(self, kind: str) -> list[tuple[int | None, date, str]]:
        return [
            (r[0], r[1], r[2])
            for r in self.query(
                "SELECT run_id, session, message FROM alerts WHERE kind = ? ORDER BY alert_id",
                [kind],
            )
        ]

    def held(self) -> dict[str, float]:
        return {p.symbol: p.quantity for p in self.fake.positions().values()}


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


@pytest.fixture
def window(env: Env, tmp_path: Path) -> PaperWindowRow:
    return env.open_window(tmp_path=tmp_path)


def bought(env: Env) -> RunOutcome:
    """The F_0 rebalance traded in the window and filled: the three targets held."""
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert sorted(env.held()) == ["DUALB", "SPFT", "TRNS"]
    return outcome


# --- the submit window ---------------------------------------------------------------


def test_the_submit_window_is_open_minus_before_to_open_plus_after(env: Env) -> None:
    start, end = submit_window(env.settings, F_0)
    opening = session_open(F_0)
    assert (start, end) == (opening - timedelta(minutes=90), opening + timedelta(minutes=30))
    custom = Settings(
        _env_file=None,
        store={"path": env.settings.store.path},
        paper={"submit_window_before_open_minutes": 10, "submit_window_after_open_minutes": 5},
    )
    assert submit_window(custom, F_0) == (
        opening - timedelta(minutes=10),
        opening + timedelta(minutes=5),
    )


@pytest.mark.parametrize(
    "now",
    [
        pytest.param(at(F_0, 11, 59), id="before"),
        pytest.param(at(F_0, 14, 1), id="after"),
    ],
)
def test_a_fill_session_run_outside_the_window_plans_but_submits_nothing(
    env: Env, window: PaperWindowRow, now: datetime
) -> None:
    outcome = env.run(now)
    assert outcome.status == "ok"
    assert outcome.kind == "rebalance"
    assert env.count("decisions") == 3  # planned: the plan is step 6's first half
    assert env.count("orders") == 0
    assert "submit" not in [c.method for c in env.fake.calls]
    assert env.rebalance_events() == []  # still pending
    assert any("outside the submit window" in note for note in outcome.notes)


def test_a_later_in_window_run_trades_the_pending_rebalance(
    env: Env, window: PaperWindowRow
) -> None:
    early = env.run(at(F_0, 11, 0))
    assert env.count("orders") == 0
    later = env.run(at(F_0, 12, 30))
    assert later.status == "ok"
    assert later.kind == "rebalance"
    assert env.count("decisions") == 3  # re-used, never re-planned
    assert sorted(o[1] for o in env.orders()) == sorted(TARGETS)
    assert {o[5] for o in env.orders()} == {later.run_id}
    assert env.rebalance_events() == [(T_0, "executed", None, later.run_id)]
    assert early.run_id != later.run_id


def test_a_rebalance_run_that_leaves_the_window_in_its_exits_read_trades_on_the_next_run(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`trade_step` checks the window again just before `execute`: a fill
    session run whose clock leaves the window while it reads the forced exits
    submits nothing and leaves the rebalance pending; the next in-window run
    (the catch-up on F_0 + 1) trades it from the same decisions (#549)."""
    original = run_module._forced_exits

    def slow(context: run_module.StepContext, **kwargs: object) -> object:
        handed = original(context, **kwargs)  # type: ignore[arg-type]
        _start, end = submit_window(env.settings, F_0)
        env.clock.now = end + timedelta(seconds=1)
        return handed

    monkeypatch.setattr(run_module, "_forced_exits", slow)
    first = env.run(at(F_0, 13, 55))
    assert first.status == "ok", env.result(env.latest_run())
    assert first.kind == "rebalance"
    assert env.count("decisions") == 3
    assert env.count("orders") == 0
    assert "submit" not in [c.method for c in env.fake.calls]
    assert env.rebalance_events() == []
    assert any("outside the submit window" in note for note in first.notes)

    monkeypatch.setattr(run_module, "_forced_exits", original)
    later = env.run(at(F_0_PLUS_1))
    assert later.status == "ok", env.result(env.latest_run())
    assert later.kind == "catch_up"
    assert env.count("decisions") == 3  # re-used, never re-planned
    assert sorted(o[1] for o in env.orders()) == sorted(TARGETS)
    assert {o[5] for o in env.orders()} == {later.run_id}
    assert env.rebalance_events() == [(T_0, "executed", None, later.run_id)]


def test_the_window_bounds_are_inclusive(env: Env, window: PaperWindowRow) -> None:
    start, _end = submit_window(env.settings, F_0)
    outcome = env.run(start)
    assert outcome.status == "ok"
    assert env.count("orders") == 3


def test_inside_the_window_sells_go_before_the_open_and_buys_after(
    env: Env, window: PaperWindowRow
) -> None:
    """The F_0 buys, then on F_1 an `exclude_name` override sells one held name
    whole and the plan buys its replacement: the sell is submitted before the
    open, it fills at the open, and the buys follow."""
    bought(env)
    t_1, f_1 = date(2019, 5, 31), date(2019, 6, 3)
    stamp = at(T_0, 22)
    assert window.window_id is not None
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=t_1,
            security_id="SEC_TRANSFER",
            kind="exclude_name",
            reason="the owner's reason, long enough",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    opening = session_open(f_1)
    env.fill_on_sleep = False

    def fill_at_the_open(now: datetime) -> None:
        if now >= opening:
            env.fill_open()

    env.on_sleep.append(fill_at_the_open)
    outcome = env.run(at(f_1, 12, 30))
    assert outcome.status == "ok", env.result(env.latest_run())
    accepted = {
        r[0]: r[1]
        for r in env.query(
            "SELECT client_order_id, event_at FROM order_events WHERE status = 'accepted'"
        )
    }
    mine = [o for o in env.orders() if o[5] == outcome.run_id]
    sells = [o for o in mine if o[2] == "sell"]
    buys = [o for o in mine if o[2] == "buy"]
    assert "SEC_TRANSFER" in [o[1] for o in sells]
    assert buys
    assert all(accepted[o[0]] < opening for o in sells)
    assert all(accepted[o[0]] >= opening for o in buys)
    assert env.held().get("TRNS", 0.0) < 1e-6


# --- decisions precede orders --------------------------------------------------------------


def test_every_decision_precedes_the_first_order_of_its_rebalance(
    env: Env, window: PaperWindowRow
) -> None:
    """At each submit, the store already holds every decision of the
    rebalance and the order's own row (the fake's `on_submit` reads it)."""
    seen: list[tuple[str, int, int]] = []

    def at_submit(request: OrderRequest) -> None:
        decisions = env.query("SELECT count(*) FROM decisions WHERE rebalance_session = ?", [T_0])
        rows = env.query(
            "SELECT count(*) FROM orders WHERE client_order_id = ?", [request.client_order_id]
        )
        seen.append((request.client_order_id, decisions[0][0], rows[0][0]))

    env.fake.on_submit = at_submit
    bought(env)
    assert len(seen) == 3
    assert {(n, r) for _, n, r in seen} == {(3, 1)}


# --- the missed-rebalance criterion --------------------------------------------------------


def test_a_windows_first_run_plans_nothing_for_t_minus_1(env: Env, tmp_path: Path) -> None:
    """`paper start` between close(T_{-1}) and F_{-1}: the run on F_{-1} is a
    `mark` run, plans nothing and trades nothing (spec req 7, ADR 0006)."""
    env.open_window(started=at(T_PREV, 21, 0), tmp_path=tmp_path)
    outcome = env.run(at(F_PREV))
    assert outcome.status == "ok"
    assert outcome.kind == "mark"
    assert env.count("decisions") == 0
    assert env.count("paper_plans") == 0
    assert env.count("orders") == 0
    assert env.rebalance_events() == []


def split(env: Env, security_id: str, ex_date: date, ratio: float, known_at: datetime) -> None:
    env.insert(
        "corporate_actions",
        {
            "security_id": security_id,
            "action_type": "split",
            "ex_date": ex_date,
            "ratio_or_amount": ratio,
            "announced_at": None,
            "source_action_id": f"test-split-{security_id}-{ex_date}",
            "cancelled": False,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "alpaca",
            "provenance": "action",
        },
    )


def test_a_catch_up_uses_its_own_session_ids_and_applies_its_split_once(
    env: Env, window: PaperWindowRow
) -> None:
    """No run on F_1 (no plan, no event of any kind); the run on F_1 + 1 is
    `catch_up`, plans at close(T_1), and sells the excluded name whole for the
    doubled quantity of a 2:1 split whose ex-date is the catch-up session,
    once: the order's quantity is the reconciled holding times two, never four."""
    bought(env)
    t_1, f_1 = date(2019, 5, 31), date(2019, 6, 3)
    catch_up = next_session(f_1)
    assert window.window_id is not None
    stamp = at(T_0, 22)
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=t_1,
            security_id="SEC_TRANSFER",
            kind="exclude_name",
            reason="the owner's reason, long enough",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    held = env.held()["TRNS"]
    split(env, "SEC_TRANSFER", catch_up, 2.0, at(f_1, 19, 0))
    # The broker applies the split before the catch-up session's open.
    env.fake._net_quantity["TRNS"] *= 2  # the fake has no corporate actions
    close = env.query(
        "SELECT close FROM prices_daily WHERE security_id = 'SEC_TRANSFER' AND session = ?",
        [f_1],
    )[0][0]
    outcome = env.run(at(catch_up), prices={"TRNS": float(close) / 2.0})
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "catch_up"
    assert env.query("SELECT rebalance_session FROM paper_plans ORDER BY 1") == [(T_0,), (t_1,)]
    mine = [o for o in env.orders() if o[5] == outcome.run_id]
    assert mine
    assert all(o[4] == catch_up for o in mine)
    assert all(catch_up.strftime("%Y%m%d") in o[0] for o in mine)
    ((quantity,),) = env.query(
        "SELECT quantity FROM orders WHERE run_id = ? AND security_id = 'SEC_TRANSFER'",
        [outcome.run_id],
    )
    assert quantity == pytest.approx(held * 2.0)
    assert env.held().get("TRNS", 0.0) < 1e-6  # sold whole, to the quantity precision
    assert (t_1, "executed", None, outcome.run_id) in env.rebalance_events()


def delist(env: Env, security_id: str, known_at: datetime) -> None:
    """A Form 25 for `security_id`'s listing, accepted at `known_at`."""
    (row,) = env.query(
        "SELECT exchange, class_title FROM listings WHERE security_id = ? "
        "ORDER BY valid_from DESC LIMIT 1",
        [security_id],
    )
    env.insert(
        "delistings",
        {
            "security_id": security_id,
            "form": "25",
            "class_title": row[1],
            "exchange": row[0],
            "filed_at": known_at,
            "effective_on": known_at.date() + timedelta(days=10),
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def test_a_catch_up_executes_with_a_skip_decision(env: Env, window: PaperWindowRow) -> None:
    """No run on F_0; a target's listing ends between close(T_0) and
    close(F_0), so the catch-up on F_0 + 1, planning at close(T_0), journals
    it `skip_delisted` and buys the other two; the rebalance still reaches
    `executed`."""
    delist(env, "SEC_DUAL_B", at(F_0, 19, 0))
    outcome = env.run(at(F_0_PLUS_1))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert outcome.kind == "catch_up"
    decisions = dict(env.query("SELECT security_id, decision FROM decisions"))
    assert decisions["SEC_DUAL_B"] == "skip_delisted"
    assert sorted(o[1] for o in env.orders()) == ["SEC_SPLIT_FUTURE", "SEC_TRANSFER"]
    assert all(o[4] == F_0_PLUS_1 and "20190502" in o[0] for o in env.orders())
    assert env.rebalance_events() == [(T_0, "executed", None, outcome.run_id)]


def test_a_fill_collected_late_by_step_3_executes_not_misses(
    env: Env, window: PaperWindowRow
) -> None:
    """The last catch-up run leaves an order open at step 7b's deadline; the
    next run, beyond the catch-up bound, collects its fill at step 3 and writes
    `executed`, before the lapse rule could write `missed`."""
    env.fill_on_sleep = False
    last = env.run(at(date(2019, 5, 3)))  # F_0 + 2: the last catch-up session
    assert last.kind == "catch_up"
    assert env.count("orders") == 3
    assert env.rebalance_events() == []  # open at the deadline: pending
    env.fill_open()
    later = env.run(at(date(2019, 5, 6)))
    assert later.kind == "mark"
    assert env.rebalance_events() == [(T_0, "executed", None, later.run_id)]


# --- step 7b: same-session collection ---------------------------------------------------


def test_same_session_collection_explains_a_partial_fill_and_an_open_order(
    env: Env, window: PaperWindowRow
) -> None:
    """Step 7b collects this run's orders: one fills, one fills partly, one
    stays open. The closing reconciliation passes (the partial fill explained
    by its `fills` rows, the open order by `open_orders()`), and the rebalance
    stays pending while a decision is open or in flight."""
    env.fill_on_sleep = False
    filled: list[str] = []

    def some(now: datetime) -> None:
        if filled:
            return
        orders = sorted(env.fake.open_orders(), key=lambda o: o.client_order_id)
        assert len(orders) == 3
        env.fake.simulate_fill(orders[0].client_order_id)
        env.fake.apply(orders[1].client_order_id, PartialFill(100.0, env.prices[orders[1].symbol]))
        filled.extend(o.client_order_id for o in orders)

    env.on_sleep.append(some)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    full, partial, still = filled
    fills = dict(env.query("SELECT client_order_id, sum(quantity) FROM fills GROUP BY 1"))
    assert set(fills) == {full, partial}
    assert fills[partial] == pytest.approx(100.0)
    statuses: dict[str, set[str]] = {}
    for coid, status in env.query("SELECT client_order_id, status FROM order_events"):
        statuses.setdefault(coid, set()).add(status)
    assert "filled" in statuses[full]
    terminal = {"filled", "expired", "rejected", "cancelled"}
    assert not statuses[partial] & terminal
    assert not statuses[still] & terminal
    assert env.query(
        "SELECT status FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id",
        [outcome.run_id],
    ) == [("ok",), ("ok",)]
    assert env.rebalance_events() == []
    assert any(still in note for note in outcome.notes)


def test_step_7b_stops_at_its_absolute_deadline(env: Env, window: PaperWindowRow) -> None:
    env.fill_on_sleep = False
    outcome = env.run(at(F_0))
    assert outcome.status == "ok"
    assert sum(env.slept) <= env.settings.paper.accept_wait_seconds
    assert max(env.slept) <= env.settings.paper.poll_interval_seconds
    assert env.rebalance_events() == []
    assert env.count("fills") == 0


# --- the unspent_cash alert --------------------------------------------------------------


def test_unspent_cash_above_the_frozen_fraction_alerts_at_executed(
    env: Env, window: PaperWindowRow
) -> None:
    """A target not tradable at the plan is `skip_untradable`: its third of
    the cash stays unspent, above the frozen `risk.max_unspent_cash_fraction`
    of equity, alerted when the rebalance reaches `executed`."""
    env.fake.set_asset(
        "SPFT", Asset(tradable=False, fractionable=True, status="active", cusip=None)
    )
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.rebalance_events() == [(T_0, "executed", None, outcome.run_id)]
    ((run_id, session, message),) = env.alerts("unspent_cash")
    assert (run_id, session) == (outcome.run_id, F_0)
    assert "max_unspent_cash_fraction" in message


def test_no_unspent_cash_alert_within_the_frozen_fraction(env: Env, window: PaperWindowRow) -> None:
    outcome = env.run(at(F_0))
    assert env.rebalance_events() == [(T_0, "executed", None, outcome.run_id)]
    assert env.alerts("unspent_cash") == []


# --- overrides halt like any order ------------------------------------------------------


def test_a_limit_breaking_exclude_name_override_halts_the_phase(env: Env, tmp_path: Path) -> None:
    """An `exclude_name` override's order goes through the same batch checks:
    with the frozen `risk.max_orders_per_run` at 3, F_0's three buys pass, and
    on F_1 the override's sell (TRNS, sold whole) and the three buys it frees
    the cash for make four: the buys phase halts with `LimitBreachError`
    before any of its submits, the rebalance `missed` with `limit_breach`."""
    window = env.open_window(
        frozen=FROZEN.model_copy(update={"max_orders_per_run": 3}), tmp_path=tmp_path
    )
    assert window.window_id is not None
    bought(env)
    t_1, f_1 = date(2019, 5, 31), date(2019, 6, 3)
    stamp = at(T_0, 22)
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=t_1,
            security_id="SEC_TRANSFER",
            kind="exclude_name",
            reason="the owner's reason, long enough",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    with pytest.raises(LimitBreachError, match="max_orders_per_run"):
        env.run(at(f_1))
    halted = env.latest_run()
    status, fault, message = env.result(halted)
    assert (status, fault) == ("halted", "LimitBreachError")
    assert message is not None and "max_orders_per_run" in message
    mine = [o for o in env.orders() if o[5] == halted]
    assert [(o[1], o[2]) for o in mine] == [("SEC_TRANSFER", "sell")]  # no buy submitted
    assert (t_1, "missed", "limit_breach", halted) in env.rebalance_events()
    assert env.query("SELECT source, fault_type, run_id FROM kill_switch") == [
        ("fault", "LimitBreachError", halted)
    ]


# --- the kill switch inside a run (#523) ----------------------------------------------------


def test_a_kill_written_during_step_4_is_caught_before_any_submit(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run reads the switch once, before its row; a `paper kill` written
    while the run is at step 4 (its `positions` read) is caught by the
    wrapper's own read before the sells phase: nothing is submitted, the run
    ends `skipped_kill_switch` and the rebalance stays pending (#523)."""
    assert window.window_id is not None
    window_id = window.window_id
    original = env.fake.positions
    killed: list[int | switch.WriteFailed] = []

    def kill_at_step_4() -> object:
        if not killed:
            killed.append(
                switch.engage(
                    env.settings,
                    env.clock,
                    window_id=window_id,
                    source="owner",
                    reason="the owner kills the run at step 4",
                )
            )
        return original()

    monkeypatch.setattr(env.fake, "positions", kill_at_step_4)
    outcome = env.run(at(F_0))
    assert [isinstance(k, int) for k in killed] == [True]
    assert outcome.status == "skipped_kill_switch", env.result(env.latest_run())
    assert outcome.exit_code == 0
    assert env.result(env.latest_run())[0] == "skipped_kill_switch"
    assert "submit" not in [c.method for c in env.fake.calls]
    assert env.count("orders") == 0
    assert env.rebalance_events() == []


@pytest.mark.parametrize(
    ("engaged", "reason"),
    [pytest.param(True, "kill_switch", id="engaged"), pytest.param(False, "catch_up_lapsed")],
)
def test_a_rebalance_skipped_past_its_catch_up_bound_lapses_with_its_reason(
    env: Env, window: PaperWindowRow, engaged: bool, reason: str
) -> None:
    """Run level, through `tracking_run`: T_0's rebalance, never traded through
    F_0 + the frozen `paper.max_catch_up_sessions` (2: 2019-05-03), lapses on
    the next run's session with reason `kill_switch` when the switch was
    engaged over that span (the F_0 run skipped by it), else
    `catch_up_lapsed` (spec req 7; #523, quant-auditor on #519)."""
    assert window.window_id is not None
    if engaged:
        switch.engage(
            env.settings,
            env.clock,
            window_id=window.window_id,
            source="owner",
            reason="the owner pauses the window",
        )
        skipped = env.run(at(F_0))
        assert skipped.status == "skipped_kill_switch"
        assert env.count("orders") == 0
    later = env.run(at(date(2019, 5, 6)))
    assert later.status == ("skipped_kill_switch" if engaged else "ok")
    assert env.rebalance_events() == [(T_0, "missed", reason, later.run_id)]
    ((run_id, session, message),) = env.alerts("missed_rebalance")
    assert (run_id, session) == (later.run_id, date(2019, 5, 6))
    assert reason in message
    assert env.count("orders") == 0


# --- step 8 reads step 7's own fills (#523) --------------------------------------------------


def test_step_8_reconciles_against_the_fills_step_7b_journaled(
    env: Env, window: PaperWindowRow
) -> None:
    """Step 8's journal cut is a clock reading taken just before it, so the
    fills step 7b journaled this session (known after close(S-1)) are in the
    ledger it reconciles; a cut at close(S-1) would drop them while the broker
    holds the names, and halt (#523, safety-reviewer on #519)."""
    outcome = bought(env)
    cut = session_close(previous_session(F_0))
    known = [
        r[0]
        for r in env.query(
            "SELECT f.known_at FROM fills f JOIN orders o USING (client_order_id) "
            "WHERE o.run_id = ?",
            [outcome.run_id],
        )
    ]
    assert len(known) == 3
    assert all(k > cut for k in known)
    rows = env.query(
        'SELECT status, "at" FROM reconciliations WHERE run_id = ? ORDER BY reconciliation_id',
        [outcome.run_id],
    )
    assert [r[0] for r in rows] == ["ok", "ok"]
    step_4, step_8 = rows[0][1], rows[1][1]
    assert step_4 < min(known)  # step 4 ran before any fill: an empty book
    assert step_8 >= max(known)
