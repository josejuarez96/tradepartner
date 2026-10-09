"""The wrapper's two-phase driver (Phase 4 plan T60b; spec req 3; ADR 0010 points
1 to 3 and its 2026-10-01 amendment; #469, #481, #487, #518).

Every case runs `RiskGatedBroker.execute` on a temp copy of the fixture store,
with the scripted fake and a fixed clock that the injected `sleep` advances.
The re-attempt and buys-timing integration cases are T60e's."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol

import duckdb
import pytest

from tradepartner import calendar
from tradepartner.adapters.broker import Asset, OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire, FakeBroker, FillAt, Reject, Vanish
from tradepartner.calendar import session_close, session_open
from tradepartner.config import FROZEN_PAPER_KEYS, CostsConfig, RiskConfig, Settings
from tradepartner.errors import (
    AcknowledgementTimeoutError,
    LimitBreachError,
    RejectionCapError,
    SkipCapError,
)
from tradepartner.execution import switch, wrapper
from tradepartner.execution.alerts import Alerter
from tradepartner.execution.plan import State
from tradepartner.execution.wrapper import BatchOutcome, RiskGatedBroker
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    OverrideRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    append,
)
from tradepartner.store.schema import ORDER_SHAPE_DEFAULTS


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


FROZEN = RiskConfig()
S = date(2026, 10, 1)  # the conftest clock's session
PREV = date(2026, 9, 30)
CUT = session_close(PREV)  # close(S-1)
T_I = date(2026, 9, 30)  # the batch's rebalance session
A, B, C = "SEC_DUAL_A", "SEC_DUAL_B", "SEC_STATIC_PRE2019"
GONE = "SEC_WINDOW_DELIST"  # its listing ended in 2019
SYMBOLS = {A: "DUALA", B: "DUALB", C: "PRE9", GONE: "WNDX"}
PRICE = 100.0


@dataclass
class Env:
    """The store's settings, the open window, the clock, the fake, the earlier
    run (S-1, ended `ok`) that holds positions, and this run (in progress)."""

    settings: Settings
    window: PaperWindowRow
    clock: FixedClock
    earlier: PaperRunRow
    run: PaperRunRow
    prices: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SYMBOLS.values(), PRICE))
    fake: FakeBroker = field(init=False)

    def __post_init__(self) -> None:
        self.new_fake()

    def new_fake(self, **kwargs: Any) -> FakeBroker:
        """A non-filling fake on the clock, pricing by `prices`."""
        self.fake = FakeBroker(
            clock=self.clock,
            price_of=lambda symbol: self.prices[symbol],
            auto_fill=False,
            account_id="PA1",
            **kwargs,
        )
        return self.fake


def _settings(path: str, **sections: dict[str, Any]) -> Settings:
    alpaca = {"quantity_decimals": 6, "client_order_id_max_length": 48}
    return Settings(
        _env_file=None,
        store={"path": path},
        alpaca={**alpaca, **sections.pop("alpaca", {})},
        **sections,
    )


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _query(settings: Settings, sql: str, params: list[object] | None = None) -> list[Any]:
    with open_for_write(settings) as conn:
        return conn.execute(sql, params or []).fetchall()


def _bar(settings: Settings, security_id: str, close: float, known_at: datetime = CUT) -> None:
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": security_id,
                "session": PREV,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
                "known_at": known_at - timedelta(hours=1),
                "ingested_at": known_at - timedelta(hours=1),
                "source": "alpaca",
                "provenance": "bar",
            },
        )


def _split(settings: Settings, security_id: str, ratio: float, known_at: datetime) -> None:
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": security_id,
                "action_type": "split",
                "ex_date": S,
                "ratio_or_amount": ratio,
                "known_at": known_at,
                "ingested_at": known_at,
                "source": "alpaca",
                "provenance": "action",
            },
        )


def _run(settings: Settings, window: PaperWindowRow, session: date, at: datetime) -> PaperRunRow:
    row = PaperRunRow(
        window_id=window.window_id,  # type: ignore[arg-type]
        session=session,
        kind="rebalance",
        started_at=at,
        invoked_by="scheduler",
        code_version="test",
        known_at=at,
        ingested_at=at,
    )
    (run_id,) = _append(settings, row)
    return replace(row, run_id=run_id)


@pytest.fixture
def alerter_conn(journal_settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(journal_settings.store.path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def env(journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock) -> Env:
    settings = _settings(journal_settings.store.path)
    for security_id in SYMBOLS:
        _bar(settings, security_id, PRICE)
    earlier = _run(settings, open_window, PREV, CUT - timedelta(hours=3))
    _append(
        settings,
        PaperRunResultRow(
            run_id=earlier.run_id,  # type: ignore[arg-type]
            finished_at=CUT - timedelta(hours=2),
            status="ok",
            clock_fault=False,
            known_at=CUT - timedelta(hours=2),
            ingested_at=CUT - timedelta(hours=2),
        ),
    )
    run = _run(settings, open_window, S, fixed_clock.now - timedelta(minutes=5))
    return Env(settings, open_window, fixed_clock, earlier, run)


def _decision(
    env: Env,
    security_id: str,
    side: str,
    *,
    notional: float | None = None,
    quantity: float | None = None,
    weight: float | None = 0.04,
    decision: str = "trade",
    reason: str | None = None,
    whole_share: bool = False,
) -> DecisionRow:
    at = env.run.started_at
    row = DecisionRow(
        run_id=env.run.run_id,  # type: ignore[arg-type]
        rebalance_session=None if decision == "forced_exit" else T_I,
        security_id=security_id,
        target_weight=weight if side == "buy" else None,
        side=side,
        planned_notional=notional,
        planned_quantity=quantity,
        target_notional=notional if side == "buy" else None,
        whole_share=whole_share,
        decision=decision,
        reason=reason,
        known_at=at,
        ingested_at=at,
    )
    (decision_id,) = _append(env.settings, row)
    return replace(row, decision_id=decision_id)


def _hold(env: Env, security_id: str, quantity: float) -> None:
    """Journal a filled buy of an earlier run on S-1, filled at the fake too."""
    at = CUT - timedelta(hours=3)
    now, env.clock.now = env.clock.now, at
    earlier = env.earlier
    held = _decision(env, security_id, "buy", notional=quantity * PRICE)
    coid = f"fx-hold-{security_id}"
    symbol = SYMBOLS[security_id]
    order = env.fake.submit(OrderRequest(coid, symbol, Side.BUY, quantity=quantity))
    env.fake.simulate_fill(coid)
    (fill,) = [f for f in env.fake.fills() if f.client_order_id == coid]
    _append(
        env.settings,
        OrderRow(
            client_order_id=coid,
            decision_id=held.decision_id,  # type: ignore[arg-type]
            run_id=earlier.run_id,
            session=PREV,
            attempt=1,
            phase="buy",
            security_id=security_id,
            symbol=symbol,
            side="buy",
            quantity=quantity,
            sells_in_flight_at_submit=False,
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=order.broker_order_id,
            known_at=at,
            ingested_at=at,
        ),
        FillRow(
            client_order_id=coid,
            filled_at=fill.filled_at,
            quantity=fill.quantity,
            price=fill.price,
            price_implied=False,
            broker_fill_id=fill.broker_fill_id,
            source="broker_feed",
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id=coid, status="filled", known_at=at, ingested_at=at),
    )
    env.clock.now = now


def _gate(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, frozen: RiskConfig = FROZEN
) -> RiskGatedBroker:
    return RiskGatedBroker(
        env.fake,
        env.clock,
        frozen,
        env.settings,
        lambda: open_for_write(env.settings),
        calendar,
        Alerter(env.settings, alerter_conn, env.clock),
        sleep=lambda seconds: env.clock.advance(seconds=seconds),
    )


def _submits(fake: FakeBroker) -> list[OrderRequest]:
    """The wrapper's submits (the helpers' set-up orders are `fx-` ids)."""
    requests = [c.args[0] for c in fake.calls if c.method == "submit"]
    return [r for r in requests if not r.client_order_id.startswith("fx-")]  # type: ignore[attr-defined]


def _missed(settings: Settings) -> list[tuple[str, str]]:
    return [
        (status, reason)
        for status, reason in _query(settings, "SELECT status, reason FROM rebalance_events")
    ]


def _decision_events(settings: Settings) -> list[tuple[int, str, str | None]]:
    return [
        (decision_id, status, reason)
        for decision_id, status, reason in _query(
            settings, "SELECT decision_id, status, reason FROM decision_events ORDER BY rowid"
        )
    ]


def _execute(
    gate: RiskGatedBroker,
    env: Env,
    decisions: list[DecisionRow],
    exits: tuple[DecisionRow, ...] = (),
) -> BatchOutcome:
    return gate.execute(env.run, decisions, exits)


# --- the reference price ---------------------------------------------------------------


@pytest.mark.parametrize(("known_by_cut", "quantity"), [(True, 15.0), (False, 7.5)])
def test_a_split_on_s_known_at_close_s_minus_1_halves_the_reference_price(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, known_by_cut: bool, quantity: float
) -> None:
    """A 2:1 split with ex_date = S known at close(S-1) halves the reference price
    and doubles the holding, from one frame (#518 items 5, 6): a $750 trim sells
    15 post-split shares, more than the 10 an unsplit ledger would hold (so it
    would breach `sell_within_holding`). Known only after close(S-1), it changes
    nothing: 7.5."""
    _hold(env, A, 10.0)
    _split(
        env.settings,
        A,
        2.0,
        CUT - timedelta(minutes=1) if known_by_cut else CUT + timedelta(minutes=1),
    )
    trim = _decision(env, A, "sell", notional=750.0)

    outcome = _execute(_gate(env, alerter_conn), env, [trim])

    (request,) = _submits(env.fake)
    assert request.quantity == quantity
    assert request.notional is None
    assert outcome.status == "ok"


def test_every_book_fact_comes_from_one_close_s_minus_1_actions_read(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#518 items 2 and 5: each phase's book reads `live_actions_as_of` once,
    at close(S-1), and that same frame builds the ledger, the reference
    prices, every decision state and every residue, and reaches
    `sell_orders`' look-ahead check."""
    reads: list[Any] = []
    seen: dict[str, list[Any]] = {}

    def spy(name: str, real: Any, position: int) -> Any:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            seen.setdefault(name, []).append(args[position])
            return real(*args, **kwargs)

        return wrapped

    def read(conn: Any, cut: datetime) -> Any:
        assert cut == CUT
        frame = live_actions(conn, cut)
        reads.append(frame)
        return frame

    live_actions = wrapper.live_actions_as_of
    monkeypatch.setattr(wrapper, "live_actions_as_of", read)
    monkeypatch.setattr(wrapper, "from_journal", spy("ledger", wrapper.from_journal, 3))
    monkeypatch.setattr(wrapper, "reference_prices", spy("prices", wrapper.reference_prices, 3))
    monkeypatch.setattr(wrapper, "decision_state", spy("states", wrapper.decision_state, 5))
    monkeypatch.setattr(wrapper, "residue", spy("residues", wrapper.residue, 6))
    sells = wrapper.phases.sell_orders
    monkeypatch.setattr(wrapper.phases, "sell_orders", spy("sell_orders", sells, 5))

    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=300.0)
    assert _execute(_gate(env, alerter_conn), env, [trim]).status == "ok"

    (frame,) = reads
    assert set(seen) == {"ledger", "prices", "states", "residues", "sell_orders"}
    assert all(arg is frame for args in seen.values() for arg in args)


def test_cash_left_excludes_a_buy_check_phase_drops_as_skip_delisted(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#604: `buy_orders` skips a buy of a name whose listing ended
    (`book.ended`) as `skip_delisted` before sizing, so it takes no share of
    the kept buy's sizing (the pre-#604 behavior sized it in and had
    `check_phase` drop it afterward, shrinking the kept buy); `cash_left` is
    recomputed over the verdict's orders either way."""
    cash = Decimal("600.00")
    env.new_fake(cash=float(cash), round_cash_to_cent=True)
    kept = _decision(env, A, "buy", notional=300.0)
    delisted = _decision(env, GONE, "buy", notional=300.0)

    outcome = _execute(_gate(env, alerter_conn), env, [kept, delisted])

    (request,) = _submits(env.fake)
    assert request.symbol == "DUALA" and request.notional is not None
    assert [s.reason for s in outcome.skips] == ["skip_delisted"]
    rate = Decimal(1) + Decimal(str(CostsConfig().per_side_bps)) / Decimal(10_000)
    assert outcome.cash == float(cash)
    spent = Decimal(str(request.notional)) * rate
    assert outcome.cash_left == pytest.approx(float(cash - spent))
    assert outcome.cash_left is not None and outcome.cash_left > float(cash) / 2 - 1


def test_an_ended_buy_is_skipped_before_sizing_so_the_kept_buys_are_not_shrunk(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#604 wrapper-level: cash (610) comfortably covers the two kept buys'
    combined target (600, scale 1, full notional) but not that total plus
    the ended name's (900, which would force scale well under 1). Only
    excluding the ended buy from sizing, not merely dropping it from
    `check_phase`'s verdict afterward, leaves the kept buys unshrunk; the
    ended buy is still journaled `skip_delisted` and counted once toward the
    skip cap."""
    env.new_fake(cash=610.0, round_cash_to_cent=True)
    kept1 = _decision(env, A, "buy", notional=300.0)
    kept2 = _decision(env, B, "buy", notional=300.0)
    delisted = _decision(env, GONE, "buy", notional=300.0)

    outcome = _execute(_gate(env, alerter_conn), env, [kept1, kept2, delisted])

    submits = {r.symbol: r for r in _submits(env.fake)}
    assert set(submits) == {"DUALA", "DUALB"}
    assert submits["DUALA"].notional == pytest.approx(300.0)
    assert submits["DUALB"].notional == pytest.approx(300.0)
    assert [s.reason for s in outcome.skips] == ["skip_delisted"]
    assert sum(s.counts_toward_cap for s in outcome.skips) == 1
    assert _decision_events(env.settings) == [(delisted.decision_id, "skipped", "skip_delisted")]


# --- the batch limits and the skip cap ---------------------------------------------------


@pytest.mark.parametrize(
    ("override", "rule"),
    [
        ({}, None),
        ({"max_position_weight": 0.03}, "max_position_weight"),
        ({"max_order_notional_fraction": 0.01}, "max_order_notional_fraction"),
        ({"max_gross_exposure": 0.01}, "max_gross_exposure"),
        ({"max_orders_per_run": 1}, "max_orders_per_run"),
    ],
)
def test_each_batch_limit_halts_with_zero_submits_and_the_missed_row(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, override: dict[str, float], rule: str | None
) -> None:
    """One limit at a time in the frozen values turns the passing batch (the
    first case) into a `LimitBreachError` naming the rule, with no submit."""
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, B, "buy", notional=3000.0)]
    gate = _gate(env, alerter_conn, FROZEN.model_copy(update=override))
    if rule is None:
        assert _execute(gate, env, buys).status == "ok"
        assert len(_submits(env.fake)) == 2
        return
    with pytest.raises(LimitBreachError, match=rule):
        _execute(gate, env, buys)
    assert _submits(env.fake) == []
    assert _missed(env.settings) == [("missed", "limit_breach")]


@pytest.mark.parametrize(
    ("held_qty", "open_qty", "notional", "capped"),
    [
        (10.0, 8.0, 500.0, 2.0),
        # Fractional (#605 pass 1's SHOULD FIX): the old `float` cap could
        # trip `sell_sum_within_holding` by a rounding ulp on exactly this
        # shape of input; it is now exact in `Decimal` on both sides.
        (6.21089, 0.859, 540.0, 5.35189),
    ],
)
def test_a_trim_is_capped_by_the_names_open_sell_instead_of_halting_the_batch(
    env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    held_qty: float,
    open_qty: float,
    notional: float,
    capped: float,
) -> None:
    """#605 owner decision: an earlier session's sell still open, and a trim
    that would otherwise sell more than what is left of the holding, sum to
    over the holding. Instead of halting on `sell_sum_within_holding` (the
    pre-#605 behaviour), the trim's cap subtracts the open sell and the batch
    submits the smaller sell."""
    _hold(env, A, held_qty)
    _open_sell(env, A, open_qty)
    trim = _decision(env, A, "sell", notional=notional)
    outcome = _execute(_gate(env, alerter_conn), env, [trim])
    assert outcome.status == "ok"
    submits = _submits(env.fake)
    assert len(submits) == 1
    assert (submits[0].quantity, submits[0].notional) == (capped, None)
    assert _missed(env.settings) == []


def test_a_trim_wiped_out_by_an_open_sell_is_held_not_skipped(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, caplog: pytest.LogCaptureFixture
) -> None:
    """#605 pass 1's second SHOULD FIX, end to end: without the open sell the
    trim's cap is the full 10 held, so its 5-share remainder would be a valid
    whole-share order; the open sell (9.5 of 10) cuts the cap to 0.5, which
    floors to 0 and would be `skip_below_one_share`. A skip here would close
    the decision for good; held instead, the run neither halts nor journals
    `skipped`, so the trim can be re-attempted once the open sell
    terminates."""
    _hold(env, A, 10.0)
    _open_sell(env, A, 9.5)
    trim = _decision(env, A, "sell", notional=500.0, whole_share=True)
    gate = _gate(env, alerter_conn)
    with caplog.at_level("WARNING", logger=wrapper.__name__):
        outcome = _execute(gate, env, [trim])
    assert outcome.status == "ok"
    assert _submits(env.fake) == []
    assert outcome.skips == ()
    assert _decision_events(env.settings) == []
    assert _missed(env.settings) == []
    # #719 item 3: the decision is still open after the run.
    book = gate._read_book(env.run, [trim])
    assert book.states[trim.decision_id].state is State.OPEN  # type: ignore[index]
    # #719 item 2: one structured line per held sell, with both quantities.
    (record,) = [r for r in caplog.records if r.name == wrapper.__name__]
    assert record.levelname == "WARNING"
    assert record.getMessage() == (
        f"sell held: run_id={env.run.run_id} session={S.isoformat()} "
        f"decision_id={trim.decision_id} security_id={A} "
        "uncapped_quantity=5.0 capped_quantity=0.0 capped_skip=skip_below_one_share"
    )


def test_a_trim_after_a_gap_down_sells_the_holding_with_no_limit_breach(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#719 item 6 acceptance, end to end: a $950 trim planned at $100 is 19
    shares at the $50 reference price after a gap-down, above the 10 held. It
    sells the holding less the residue (none here; the residue case is
    `test_phases`' `test_a_trim_after_a_price_drop_is_capped_at_the_holding_less_residue`)
    and the batch never halts on `limit_breach`."""
    _hold(env, A, 10.0)
    _query(env.settings, "UPDATE prices_daily SET close = ? WHERE security_id = ?", [50.0, A])
    trim = _decision(env, A, "sell", notional=950.0)
    outcome = _execute(_gate(env, alerter_conn), env, [trim])
    assert outcome.status == "ok"
    (request,) = _submits(env.fake)
    assert (request.quantity, request.notional) == (10.0, None)
    assert _missed(env.settings) == []
    assert _decision_events(env.settings) == []


def test_a_held_trim_sells_the_rest_once_the_open_sell_expires(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#719 item 6 acceptance, end to end: a trim beside an earlier session's
    open own sell is held (no order, no `decision_events` row, no skip count,
    decision open); once that sell expires, the next run trims the rest."""
    _hold(env, A, 10.0)
    _open_sell(env, A, 9.5)
    trim = _decision(env, A, "sell", notional=500.0, whole_share=True)
    gate = _gate(env, alerter_conn)
    held = _execute(gate, env, [trim])
    assert (held.status, held.skips, _submits(env.fake)) == ("ok", (), [])
    assert _decision_events(env.settings) == []
    assert gate._read_book(env.run, [trim]).states[trim.decision_id].state is State.OPEN  # type: ignore[index]

    env.fake.apply(f"fx-open-{A}", Expire())
    at = env.clock.now
    _append(
        env.settings,
        OrderEventRow(
            client_order_id=f"fx-open-{A}", status="expired", known_at=at, ingested_at=at
        ),
    )
    _append(
        env.settings,
        PaperRunResultRow(
            run_id=env.run.run_id,  # type: ignore[arg-type]
            finished_at=at,
            status="ok",
            clock_fault=False,
            known_at=at,
            ingested_at=at,
        ),
    )
    env.clock.advance(seconds=1)
    env.run = _run(env.settings, env.window, S, env.clock.now)
    rest = _execute(_gate(env, alerter_conn), env, [trim])
    assert rest.status == "ok"
    (request,) = _submits(env.fake)
    assert (request.quantity, request.notional) == (5.0, None)  # the whole $500 remainder
    assert _missed(env.settings) == []


def _open_sell(env: Env, security_id: str, quantity: float) -> None:
    """An acknowledged sell of the earlier run on S-1, still open at the fake."""
    at = CUT - timedelta(hours=2)
    earlier = env.earlier
    decision = _decision(env, security_id, "sell", quantity=quantity, reason="left_targets")
    coid = f"fx-open-{security_id}"
    order = env.fake.submit(OrderRequest(coid, SYMBOLS[security_id], Side.SELL, quantity=quantity))
    _append(
        env.settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision.decision_id,  # type: ignore[arg-type]
            run_id=earlier.run_id,
            session=PREV,
            attempt=1,
            phase="sell",
            security_id=security_id,
            symbol=SYMBOLS[security_id],
            side="sell",
            quantity=quantity,
            sells_in_flight_at_submit=True,
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=order.broker_order_id,
            known_at=at,
            ingested_at=at,
        ),
    )


def test_a_second_full_exit_for_one_name_is_a_value_error_halt(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#518: two sells of one name in a phase raise from `sell_orders`."""
    _hold(env, A, 10.0)
    exit_ = _decision(env, A, "sell", quantity=10.0, reason="left_targets")
    forced = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
    calls = len(env.fake.calls)
    with pytest.raises(ValueError, match="two sell decisions"):
        _execute(_gate(env, alerter_conn), env, [exit_], [forced])
    assert "submit" not in [c.method for c in env.fake.calls[calls:]]
    assert _missed(env.settings) == []


def test_cash_left_costs_the_verdict_s_whole_share_upgrade(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A buy the verdict returns as `whole_share` is costed at the buffered price
    in `cash_left`, as the verdict's orders say, not as the built orders did
    (#534 item 1). `buy_orders` reads the same `assets` as `check_phase`, so the
    two agree today; the stubs make them disagree: the built buy loses its flag
    and `check_phase` gives it back, as its upgrade does for a name not
    `fractionable`."""
    built_by, checked_by = wrapper.phases.buy_orders, wrapper.check_phase

    def built_without_flag(*args: Any, **kwargs: Any) -> wrapper.phases.PhaseOrders:
        built = built_by(*args, **kwargs)
        return replace(built, orders=tuple(replace(o, whole_share=False) for o in built.orders))

    def check_upgrading(candidates: list[Any], *args: Any, **kwargs: Any) -> Any:
        upgraded = [replace(c, whole_share=c.quantity is not None) for c in candidates]
        return checked_by(upgraded, *args, **kwargs)

    monkeypatch.setattr(wrapper.phases, "buy_orders", built_without_flag)
    monkeypatch.setattr(wrapper, "check_phase", check_upgrading)

    buy = _decision(env, A, "buy", notional=3000.0, whole_share=True)
    outcome = _execute(_gate(env, alerter_conn), env, [buy])

    (request,) = _submits(env.fake)
    assert request.quantity is not None and request.quantity == int(request.quantity) > 0
    rate = Decimal(1) + Decimal(str(CostsConfig().per_side_bps)) / Decimal(10_000)
    buffered = Decimal(str(PRICE)) * (1 + Decimal(str(FROZEN.whole_share_price_buffer)))
    assert outcome.cash is not None
    spent = Decimal(str(request.quantity)) * buffered * rate
    assert outcome.cash_left == pytest.approx(float(Decimal(repr(outcome.cash)) - spent))


def test_the_skip_cap_halts_before_any_decision_events_row(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.fake.set_asset(
        "DUALA", Asset(tradable=False, fractionable=True, status="active", cusip=None)
    )
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, B, "buy", notional=3000.0)]
    with pytest.raises(SkipCapError, match="max_skips_per_run"):
        _execute(
            _gate(env, alerter_conn, FROZEN.model_copy(update={"max_skips_per_run": 0})), env, buys
        )
    assert _submits(env.fake) == []
    assert _decision_events(env.settings) == []
    assert _missed(env.settings) == [("missed", "skip_cap")]


def test_phase_time_skips_are_journaled_while_the_rest_submits(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.fake.set_asset(
        "DUALA", Asset(tradable=False, fractionable=True, status="active", cusip=None)
    )
    untradable = _decision(env, A, "buy", notional=3000.0)
    delisted = _decision(env, GONE, "buy", notional=3000.0)
    kept = _decision(env, B, "buy", notional=3000.0)

    outcome = _execute(_gate(env, alerter_conn), env, [untradable, delisted, kept])

    assert [r.symbol for r in _submits(env.fake)] == ["DUALB"]
    assert sorted(_decision_events(env.settings)) == sorted(
        [
            (untradable.decision_id, "skipped", "skip_untradable"),
            (delisted.decision_id, "skipped", "skip_delisted"),
        ]
    )
    assert {s.reason for s in outcome.skips} == {"skip_untradable", "skip_delisted"}


def test_a_forced_exits_only_batch_that_halts_leaves_the_rebalance_pending(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A full exit's own open sell no longer trips `sell_sum_within_holding`
    (#647 item 5; see
    `test_a_full_exit_nets_out_its_own_open_sell_instead_of_halting_the_batch`
    below), so an unrelated batch limit exercises the same claim here: a
    halt in a forced-exits-only phase still leaves the rebalance pending."""
    _hold(env, A, 10.0)
    _hold(env, B, 5.0)  # untouched this batch; breaches the tightened cap below
    _decision(env, C, "buy", notional=3000.0)  # the pending rebalance, not in this batch
    forced = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
    gate = _gate(env, alerter_conn, FROZEN.model_copy(update={"max_gross_exposure": 0.0}))
    with pytest.raises(LimitBreachError, match="max_gross_exposure"):
        _execute(gate, env, [], [forced])
    assert _missed(env.settings) == []


def test_a_sells_phase_breach_marks_a_held_buys_only_rebalance_missed(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The batch holds the rebalance (buys only) and a forced exit: the sells
    phase holds only the exit, and its breach still marks the rebalance
    `missed` (ADR 0010 point 2: per batch, not per phase)."""
    _hold(env, A, 10.0)
    _hold(env, B, 5.0)  # untouched this batch; breaches the tightened cap below
    buy = _decision(env, C, "buy", notional=3000.0)
    forced = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
    gate = _gate(env, alerter_conn, FROZEN.model_copy(update={"max_gross_exposure": 0.0}))
    with pytest.raises(LimitBreachError, match="max_gross_exposure"):
        _execute(gate, env, [buy], (forced,))
    assert _missed(env.settings) == [("missed", "limit_breach")]
    assert _submits(env.fake) == []


def test_a_full_exit_nets_out_its_own_open_sell_instead_of_halting_the_batch(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#647 item 5 owner decision, end to end: this is the exact shape the
    two tests above used to exercise pre-#647 (10 held, 8 open-sold: the old
    full exit sold the whole 10 and tripped `sell_sum_within_holding` at 18
    over the 10 held). Now the full exit nets the open sell out and submits
    the smaller sell, so the batch neither halts nor skips."""
    _hold(env, A, 10.0)
    _open_sell(env, A, 8.0)
    forced = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
    outcome = _execute(_gate(env, alerter_conn), env, [], [forced])
    assert outcome.status == "ok"
    submits = _submits(env.fake)
    assert len(submits) == 1
    assert (submits[0].quantity, submits[0].notional) == (2.0, None)  # 10 - 0 - 8
    assert _missed(env.settings) == []


def test_several_holding_capped_trims_already_at_residue_do_not_trip_the_skip_cap(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """#647 item 7 owner decision, end to end: a broad price drop can leave
    several trims already sold down to their residue (here, simply never
    held at all, which caps each one's quantity at 0 just the same). Before
    #647 each one's holding-capped quantity closed as a real skip and
    counted toward the cap (the #602-era bug); now each is held instead, so
    more of them than `max_skips_per_run` never trips `SkipCapError`, writes
    no skip `decision_events` row, and leaves every decision open."""
    trims = [_decision(env, sid, "sell", notional=300.0) for sid in (A, B, C)]
    gate = _gate(env, alerter_conn, FROZEN.model_copy(update={"max_skips_per_run": 0}))
    outcome = _execute(gate, env, trims)
    assert outcome.status == "ok"
    assert _submits(env.fake) == []
    assert outcome.skips == ()
    assert _decision_events(env.settings) == []
    assert _missed(env.settings) == []


# --- the journal before the broker ---------------------------------------------------------


def test_orders_rows_and_pending_events_exist_before_the_first_submit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=500.0)
    buys = [_decision(env, B, "buy", notional=3000.0), _decision(env, C, "buy", notional=3000.0)]
    seen: list[tuple[str, list[Any]]] = []

    def inspect_store(request: OrderRequest) -> None:
        rows = _query(
            env.settings,
            "SELECT o.client_order_id, e.status, o.phase, o.sells_in_flight_at_submit "
            "FROM orders o JOIN order_events e USING (client_order_id) "
            "WHERE o.run_id = ? AND e.status = 'pending' ORDER BY o.client_order_id",
            [env.run.run_id],
        )
        seen.append((request.client_order_id, rows))

    env.fake.script(FillAt())  # the sell fills, so the buys phase is the last
    env.fake.on_submit = inspect_store
    _execute(_gate(env, alerter_conn), env, [trim, *buys])

    sell_id, sell_rows = seen[0]
    assert sell_rows == [(sell_id, "pending", "sell", True)]
    buy_ids = [coid for coid, _ in seen[1:]]
    for _, rows in seen[1:]:
        assert {r[0] for r in rows} == {sell_id, *buy_ids}
        assert all(r[3] is False for r in rows if r[2] == "buy")  # the last phase


def test_ids_are_per_attempt_off_the_run_session(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A buy whose first attempt on S expired while a sell was in flight is
    re-attempted with attempt 2 (ADR 0010 point 3)."""
    buy = _decision(env, A, "buy", notional=3000.0)
    first = "tp-main-20261001-SEC_DUAL_A-buy-1"
    at = env.run.started_at
    _append(
        env.settings,
        OrderRow(
            client_order_id=first,
            decision_id=buy.decision_id,  # type: ignore[arg-type]
            run_id=env.run.run_id,  # type: ignore[arg-type]
            session=S,
            attempt=1,
            phase="buy",
            security_id=A,
            symbol="DUALA",
            side="buy",
            notional=3000.0,
            sells_in_flight_at_submit=True,
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id=first, status="pending", known_at=at, ingested_at=at),
        OrderEventRow(client_order_id=first, status="accepted", known_at=at, ingested_at=at),
        OrderEventRow(client_order_id=first, status="expired", known_at=at, ingested_at=at),
    )
    _execute(_gate(env, alerter_conn), env, [buy])
    (request,) = _submits(env.fake)
    assert request.client_order_id == "tp-main-20261001-SEC_DUAL_A-buy-2"
    (attempt,) = _query(
        env.settings,
        "SELECT attempt FROM orders WHERE client_order_id = ?",
        [request.client_order_id],
    )
    assert attempt == (2,)


def test_the_accepted_event_carries_the_brokers_order_id(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _execute(_gate(env, alerter_conn), env, [_decision(env, A, "buy", notional=3000.0)])
    (request,) = _submits(env.fake)
    rows = _query(
        env.settings,
        "SELECT status, broker_order_id FROM order_events WHERE client_order_id = ? ORDER BY rowid",
        [request.client_order_id],
    )
    assert rows == [
        ("pending", None),
        ("accepted", env.fake.get_order(request.client_order_id).broker_order_id),
    ]


#: `orders`' eight shape columns (ADR 0015 seam 3): the five defaulted ones and
#: the three nullable ones.
_SHAPE_DEFAULTED = ("order_type", "time_in_force", "asset_class", "order_class", "multiplier")
_SHAPE_NULLABLE = ("limit_price", "stop_price", "parent_order_id")


def test_the_journaled_order_rows_carry_the_default_shape(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Every `orders` row a batch journals, a sell and a buy, reads
    `schema.ORDER_SHAPE_DEFAULTS` and no limit price, stop price or parent
    (ADR 0015 seam 3, plan T135b)."""
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=500.0)
    env.fake.script(FillAt())
    _execute(_gate(env, alerter_conn), env, [trim, _decision(env, B, "buy", notional=3000.0)])
    columns = ", ".join((*_SHAPE_DEFAULTED, *_SHAPE_NULLABLE))
    rows = _query(
        env.settings, f"SELECT side, {columns} FROM orders WHERE run_id = ?", [env.run.run_id]
    )
    expected = (*(ORDER_SHAPE_DEFAULTS[k] for k in _SHAPE_DEFAULTED), None, None, None)
    assert sorted(rows) == [("buy", *expected), ("sell", *expected)]


def test_the_wrapper_writes_every_shape_column_explicitly() -> None:
    """Each `OrderRow(...)` the wrapper builds names all eight shape columns,
    the five defaulted ones read from `ORDER_SHAPE_DEFAULTS` and the three
    nullable ones `None`, never left to the row type's defaults (plan T135b)."""
    calls = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(wrapper)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "OrderRow"
    ]
    assert calls
    for call in calls:
        given = {k.arg: k.value for k in call.keywords}
        for name in _SHAPE_DEFAULTED:
            value = given[name]
            assert isinstance(value, ast.Subscript), name
            assert ast.unparse(value) == f"ORDER_SHAPE_DEFAULTS['{name}']", name
        for name in _SHAPE_NULLABLE:
            value = given[name]
            assert isinstance(value, ast.Constant) and value.value is None, name


def test_a_validation_error_on_the_last_request_halts_with_zero_submits(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """`tp-main-20261001-SEC_STATIC_PRE2019-buy-1` is 41 characters, the others 33."""
    env.settings = _settings(env.settings.store.path, alpaca={"client_order_id_max_length": 30})
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, C, "buy", notional=3000.0)]
    with pytest.raises(ValueError, match="longer than 30"):
        _execute(_gate(env, alerter_conn), env, buys)
    assert _submits(env.fake) == []
    assert _query(
        env.settings, "SELECT count(*) FROM orders WHERE run_id = ?", [env.run.run_id]
    ) == [(0,)]


def test_a_name_unknown_to_the_master_is_refused_before_any_broker_call(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _bar(env.settings, "SEC_NOT_LISTED", PRICE)
    buy = _decision(env, "SEC_NOT_LISTED", "buy", notional=3000.0)
    with pytest.raises(ValueError, match="no listing known at close"):
        _execute(_gate(env, alerter_conn), env, [buy])
    assert env.fake.calls == ()


# --- acknowledgement, rejections and the kill switch ---------------------------------------


def test_the_ack_timeout_leaves_the_order_pending(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.fake.script(Vanish())
    gate = _gate(env, alerter_conn)
    start = env.clock.now
    with pytest.raises(AcknowledgementTimeoutError):
        _execute(gate, env, [_decision(env, A, "buy", notional=3000.0)])
    assert env.clock.now - start >= timedelta(seconds=env.settings.paper.accept_wait_seconds)
    (request,) = _submits(env.fake)
    statuses = _query(
        env.settings,
        "SELECT status FROM order_events WHERE client_order_id = ?",
        [request.client_order_id],
    )
    assert statuses == [("pending",)]


def test_the_rejection_cap_names_the_submitting_run(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.fake.script(Reject())
    env.fake.script(FillAt())
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, B, "buy", notional=3000.0)]
    frozen = FROZEN.model_copy(update={"max_rejections_per_run": 0})
    with pytest.raises(RejectionCapError, match=f"run {env.run.run_id} had 1 of 2"):
        _execute(_gate(env, alerter_conn, frozen), env, buys)


def test_every_order_rejected_halts_below_the_cap(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The sells all rejected: the halt comes from their collection, before any buy."""
    _hold(env, A, 10.0)
    env.fake.script(Reject())
    trim = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    with pytest.raises(RejectionCapError, match=f"every order of run {env.run.run_id}"):
        _execute(_gate(env, alerter_conn), env, [trim, buy])
    assert [r.side for r in _submits(env.fake)] == [Side.SELL]


def _override(env: Env) -> None:
    at = env.clock.now
    _append(
        env.settings,
        OverrideRow(
            window_id=env.window.window_id,  # type: ignore[arg-type]
            made_at=at,
            kind="engage_kill_switch",
            reason="owner engages the switch between the phases",
            known_at=at,
            ingested_at=at,
        ),
    )


def test_a_kill_between_the_phases_ends_skipped_kill_switch_with_the_buys_open(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())
    env.fake.on_submit = lambda _request: _override(env)

    outcome = _execute(_gate(env, alerter_conn), env, [trim, buy])

    assert outcome.status == "skipped_kill_switch"
    assert [r.side for r in _submits(env.fake)] == [Side.SELL]
    assert outcome.written_off == ()
    engaged = _query(env.settings, "SELECT count(*) FROM kill_switch WHERE override_id IS NOT NULL")
    assert engaged == [(1,)]
    switch.engage_from_overrides(
        env.settings, env.clock, window_id=env.window.window_id, run_id=None
    )  # type: ignore[arg-type]
    assert _query(
        env.settings, "SELECT count(*) FROM kill_switch WHERE override_id IS NOT NULL"
    ) == [(1,)]
    gate = _gate(env, alerter_conn)
    book = gate._read_book(env.run, [trim, buy])
    assert book.states[buy.decision_id].state is State.OPEN  # type: ignore[index]


def test_an_engaged_switch_before_the_sells_submits_nothing(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    _override(env)
    trim = _decision(env, A, "sell", notional=500.0)
    outcome = _execute(_gate(env, alerter_conn), env, [trim])
    assert outcome == BatchOutcome("skipped_kill_switch")
    assert _submits(env.fake) == []
    assert _query(
        env.settings, "SELECT count(*) FROM orders WHERE run_id = ?", [env.run.run_id]
    ) == [(0,)]


# --- the timing and the cash ------------------------------------------------------------------


def test_sells_go_before_the_open_and_no_buy_before_the_sells_deadline(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    opening = session_open(S)
    env.clock.now = opening - timedelta(minutes=20)
    trim = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    at: dict[Side, datetime] = {}
    env.fake.on_submit = lambda request: at.setdefault(request.side, env.clock.now)

    _execute(_gate(env, alerter_conn), env, [trim, buy])  # the sell stays open

    assert at[Side.SELL] < opening
    assert at[Side.BUY] >= opening + timedelta(seconds=env.settings.paper.sell_wait_seconds)
    (in_flight,) = _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE run_id = ? AND side = 'buy'",
        [env.run.run_id],
    )
    assert in_flight == (True,)


def test_buys_are_sized_from_cash_never_buying_power(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=2000.0, buying_power=1_000_000.0)
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, B, "buy", notional=3000.0)]
    outcome = _execute(_gate(env, alerter_conn), env, buys)
    assert outcome.cash == 2000.0
    total = sum(r.notional or 0.0 for r in _submits(env.fake))
    assert 0 < total <= 2000.0
    assert outcome.cash_left is not None and 0 <= outcome.cash_left < 1.0


def test_the_one_cash_read_is_buys_cash(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declared stub (plan T60b): `account()` is read once, in
    `RiskGatedBroker._buys_cash`'s caller, and that value sizes and checks the
    buys. A T48b amendment of the cash input lands here."""
    reads: list[float] = []
    original = RiskGatedBroker._buys_cash

    def spy(self: RiskGatedBroker, account: Any, book: Any, session: date) -> float:
        cash = original(self, account, book, session)
        reads.append(cash)
        return cash

    monkeypatch.setattr(RiskGatedBroker, "_buys_cash", spy)
    _execute(_gate(env, alerter_conn), env, [_decision(env, A, "buy", notional=3000.0)])
    assert [c.method for c in env.fake.calls].count("account") == 1
    assert reads == [env.fake.account().cash]  # no fill yet, no reserve


def _open_buy(env: Env, security_id: str, notional: float) -> None:
    """An acknowledged notional buy of the earlier run, still open at the fake."""
    at = CUT - timedelta(hours=2)
    earlier = env.earlier
    decision = _decision(env, security_id, "buy", notional=notional)
    coid = f"fx-openbuy-{security_id}"
    order = env.fake.submit(OrderRequest(coid, SYMBOLS[security_id], Side.BUY, notional=notional))
    _append(
        env.settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision.decision_id,  # type: ignore[arg-type]
            run_id=earlier.run_id,
            session=PREV,
            attempt=1,
            phase="buy",
            security_id=security_id,
            symbol=SYMBOLS[security_id],
            side="buy",
            notional=notional,
            sells_in_flight_at_submit=False,
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=order.broker_order_id,
            known_at=at,
            ingested_at=at,
        ),
    )


def test_an_earlier_runs_open_buy_shrinks_the_phases_buys(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=5000.0)
    _open_buy(env, C, 2000.0)
    outcome = _execute(_gate(env, alerter_conn), env, [_decision(env, A, "buy", notional=4000.0)])
    assert outcome.cash == 3000.0
    (request,) = [r for r in _submits(env.fake) if r.symbol == "DUALA"]
    assert request.notional is not None and request.notional <= 3000.0


def test_a_batch_sized_against_the_full_cash_breaches_the_cash_rule(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.new_fake(cash=5000.0)
    _open_buy(env, C, 2000.0)
    sized = wrapper.phases.buy_orders

    def full_cash(*args: Any, **kwargs: Any) -> Any:
        return sized(args[0], args[1], 5000.0, *args[3:], **kwargs)

    monkeypatch.setattr(wrapper.phases, "buy_orders", full_cash)
    calls = len(env.fake.calls)
    with pytest.raises(LimitBreachError, match="buys_within_cash"):
        _execute(_gate(env, alerter_conn), env, [_decision(env, A, "buy", notional=4000.0)])
    assert "submit" not in [c.method for c in env.fake.calls[calls:]]


@pytest.mark.parametrize("engaged", [False, True])
def test_a_reserve_at_or_above_cash_defers_every_buy_without_a_breach(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, engaged: bool
) -> None:
    """Cash 0 after the reserve: the buy is deferred, no breach. The phase is
    the last, so a completed one writes it off; a switch engaged before its
    first submit writes off no deferred buy (#487)."""
    env.new_fake(cash=2000.0)
    _open_buy(env, C, 2000.0)
    buy = _decision(env, A, "buy", notional=3000.0)
    if engaged:
        _override(env)
    calls = len(env.fake.calls)
    outcome = _execute(_gate(env, alerter_conn), env, [buy])
    assert "submit" not in [c.method for c in env.fake.calls[calls:]]
    if engaged:
        assert outcome.status == "skipped_kill_switch"
        assert outcome.written_off == ()
        assert _decision_events(env.settings) == []
    else:
        assert (outcome.status, outcome.cash, outcome.deferred) == ("ok", 0.0, (buy.decision_id,))
        assert outcome.written_off == (buy.decision_id,)
        assert _decision_events(env.settings) == [(buy.decision_id, "written_off", "unfunded")]


def test_a_reserve_error_halts_the_buys_with_zero_buy_submits(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("actions_as_of holds a split known after close(S-1)")

    monkeypatch.setattr(wrapper, "open_buy_reserve", refuse)
    with pytest.raises(ValueError, match="after close"):
        _execute(_gate(env, alerter_conn), env, [_decision(env, A, "buy", notional=3000.0)])
    assert _submits(env.fake) == []


# --- a name with no bar at close(S-1) (#569, owner option (b)) ----------------------------------

NO_BAR = "SEC_NO_BAR"  # no `prices_daily` row at all, so no reference price on S


def _stale_order(
    env: Env, side: str, *, quantity: float | None = None, notional: float | None = None
) -> None:
    """A non-terminal order of the earlier run on S-1 for `NO_BAR`, journaled
    only (accepted, never filled): the stale order #569 says must not halt
    every batch."""
    at = CUT - timedelta(hours=2)
    decision = _decision(
        env,
        NO_BAR,
        side,
        notional=notional,
        quantity=quantity,
        reason="left_targets" if side == "sell" else None,
    )
    coid = f"fx-stale-{side}"
    _append(
        env.settings,
        OrderRow(
            client_order_id=coid,
            decision_id=decision.decision_id,  # type: ignore[arg-type]
            run_id=env.earlier.run_id,
            session=PREV,
            attempt=1,
            phase=side,
            security_id=NO_BAR,
            symbol="NOBR",
            side=side,
            notional=notional,
            quantity=quantity,
            sells_in_flight_at_submit=side == "sell",
            known_at=at,
            ingested_at=at,
        ),
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
        OrderEventRow(
            client_order_id=coid,
            status="accepted",
            broker_order_id=f"broker-{coid}",
            known_at=at,
            ingested_at=at,
        ),
    )


@pytest.mark.parametrize(
    ("side", "quantity", "notional"),
    [
        # The reserve takes a notional buy's unfilled notional as it stands.
        ("buy", None, 500.0),
        # The open-sell check counts a quantity sell's shares, unpriced.
        ("sell", 3.0, None),
        # A notional sell would be priced, but the check reads only the names
        # the batch sells (`_open_sells`).
        ("sell", None, 300.0),
    ],
)
def test_an_unpriced_open_order_no_number_reads_does_not_halt_the_batch(
    env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    side: str,
    quantity: float | None,
    notional: float | None,
) -> None:
    """#569 (b): a non-terminal order whose name has no bar at close(S-1), and
    whose price neither the open-buy reserve nor the open-sell check reads,
    leaves both phases of the batch to proceed."""
    env.new_fake(cash=10_000.0)
    _hold(env, A, 10.0)
    _stale_order(env, side, quantity=quantity, notional=notional)
    trim = _decision(env, A, "sell", notional=300.0)
    buy = _decision(env, B, "buy", notional=1000.0)
    outcome = _execute(_gate(env, alerter_conn), env, [trim, buy])
    assert outcome.status == "ok"
    assert sorted(r.symbol for r in _submits(env.fake)) == ["DUALA", "DUALB"]
    assert _missed(env.settings) == []


@pytest.mark.parametrize("with_sell", [False, True])
def test_an_unpriced_open_quantity_buy_halts_at_the_reserve_before_any_submit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, with_sell: bool
) -> None:
    """The reserve prices an open quantity buy's unfilled shares at the
    reference price, so a missing bar there still halts. A batch with buys
    runs the reserve on its first book, so even a sell-and-buy batch halts
    before its sells are submitted, not after (#569)."""
    env.new_fake(cash=10_000.0)
    _stale_order(env, "buy", quantity=3.0)
    batch = [_decision(env, B, "buy", notional=1000.0)]
    if with_sell:
        _hold(env, A, 10.0)
        batch.insert(0, _decision(env, A, "sell", notional=300.0))
    calls = len(env.fake.calls)
    with pytest.raises(ValueError, match=NO_BAR):
        _execute(_gate(env, alerter_conn), env, batch)
    assert not env.fake.calls[calls:]


@pytest.mark.parametrize("with_sell", [False, True])
def test_an_engaged_switch_skips_before_the_reserve_reads_an_unpriced_open_buy(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, with_sell: bool
) -> None:
    """#692 (owner Q1 = B): the switch is read before the reserve pre-check, so
    an engaged switch ends the batch `skipped_kill_switch`, never a fault, even
    when an open quantity buy has no price; nothing written, nothing sent."""
    env.new_fake(cash=10_000.0)
    _stale_order(env, "buy", quantity=3.0)
    batch = [_decision(env, B, "buy", notional=1000.0)]
    if with_sell:
        _hold(env, A, 10.0)
        batch.insert(0, _decision(env, A, "sell", notional=300.0))
    _override(env)
    calls = len(env.fake.calls)
    outcome = _execute(_gate(env, alerter_conn), env, batch)
    assert outcome.status == "skipped_kill_switch"
    assert not env.fake.calls[calls:]
    assert _query(
        env.settings, "SELECT count(*) FROM orders WHERE run_id = ?", [env.run.run_id]
    ) == [(0,)]


def test_the_reserve_pre_check_runs_after_the_switch_read_and_before_any_sell(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#692: on a sell-and-buy batch the order is switch read, then the
    reserve pre-check, then the sells phase's own switch read and submits."""
    env.new_fake(cash=10_000.0)
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=300.0)
    buy = _decision(env, B, "buy", notional=1000.0)
    gate = _gate(env, alerter_conn)
    seen: list[str] = []
    engaged, reserve = gate._engaged, wrapper.open_buy_reserve

    def spy_engaged(*args: Any, **kwargs: Any) -> bool:
        seen.append("switch")
        return engaged(*args, **kwargs)

    def spy_reserve(*args: Any, **kwargs: Any) -> Any:
        seen.append("reserve")
        return reserve(*args, **kwargs)

    monkeypatch.setattr(gate, "_engaged", spy_engaged)
    monkeypatch.setattr(wrapper, "open_buy_reserve", spy_reserve)
    env.fake.on_submit = lambda _request: seen.append("submit")
    _execute(gate, env, [trim, buy])
    assert seen[:4] == ["switch", "reserve", "switch", "submit"]


def test_a_sells_only_batch_never_reads_the_reserve(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """No buy, no reserve: an unpriced open quantity buy does not halt it."""
    _hold(env, A, 10.0)
    _stale_order(env, "buy", quantity=3.0)
    outcome = _execute(_gate(env, alerter_conn), env, [_decision(env, A, "sell", notional=300.0)])
    assert outcome.status == "ok"
    assert [r.symbol for r in _submits(env.fake)] == ["DUALA"]


@pytest.mark.parametrize("stale", [False, True])
def test_a_batch_name_with_no_bar_halts_before_any_broker_call(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, stale: bool
) -> None:
    """A name inside the batch is priced at the book read, with or without a
    stale open order of its own: no bar halts before any broker call."""
    if stale:
        _stale_order(env, "buy", notional=500.0)
    buy = _decision(env, NO_BAR, "buy", notional=1000.0)
    calls = len(env.fake.calls)
    with pytest.raises(ValueError, match="no bar on or before"):
        _execute(_gate(env, alerter_conn), env, [buy])
    assert not env.fake.calls[calls:]


# --- frozen versus settings -------------------------------------------------------------------


def test_limits_come_from_frozen_and_run_time_keys_from_settings(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Live `risk.*` in `settings` that would halt change nothing; the
    run-time `paper.order_id_prefix` is read from `settings`."""
    env.settings = _settings(
        env.settings.store.path, risk={"max_orders_per_run": 1}, paper={"order_id_prefix": "zz"}
    )
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, B, "buy", notional=3000.0)]
    _execute(_gate(env, alerter_conn), env, buys)
    assert [r.client_order_id[:3] for r in _submits(env.fake)] == ["zz-", "zz-"]


def test_the_wrapper_reads_only_risk_keys_from_frozen() -> None:
    """Every `self._frozen.<key>` is a `RiskConfig` field; no `settings.risk`, no
    `settings.costs` (the costs are the window's, #534), and no frozen
    `paper.*` key, is read from `settings`."""
    tree = ast.parse(inspect.getsource(wrapper))
    frozen_keys: set[str] = set()
    settings_sections: set[str] = set()
    paper_keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
        ):
            settings_sections.add(node.attr)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            owner = node.value
            if isinstance(owner.value, ast.Name) and owner.value.id == "self":
                if owner.attr == "_frozen":
                    frozen_keys.add(node.attr)
                if owner.attr == "_settings":
                    settings_sections.add(node.attr)
            if owner.attr == "paper":
                paper_keys.add(node.attr)
    assert frozen_keys and frozen_keys <= set(RiskConfig.model_fields)
    assert "risk" not in settings_sections
    assert "costs" not in settings_sections
    assert not paper_keys & set(FROZEN_PAPER_KEYS)


def test_a_costs_edit_mid_window_does_not_change_the_buys_cash_sizing(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The window froze the default costs; live `costs.*` that would size the
    buys far smaller change nothing (#534): the buys are scaled to the cash at
    the frozen per-side rate, and `cash_left` is costed the same way."""
    cash = Decimal("600.00")
    env.new_fake(cash=float(cash), round_cash_to_cent=True)
    env.settings = _settings(
        env.settings.store.path,
        costs={"per_side_bps": 500.0, "commission_per_share": 0.5, "commission_per_order": 5.0},
    )
    buys = [_decision(env, A, "buy", notional=300.0), _decision(env, B, "buy", notional=300.0)]

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    rate = Decimal(1) + Decimal(str(CostsConfig().per_side_bps)) / Decimal(10_000)
    per_buy = (cash / rate / Decimal(2)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    assert [r.notional for r in _submits(env.fake)] == [float(per_buy)] * 2
    assert outcome.cash == float(cash)
    assert outcome.cash_left == pytest.approx(float(cash - 2 * per_buy * rate))


def test_a_window_without_frozen_costs_fails_closed_before_any_broker_call(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window whose `frozen_json` lacks the cost keys (started before #534)
    raises a `ValueError` naming them; there is no fallback to `settings.costs`,
    and the broker is never called."""
    _hold(env, A, 10.0)
    older = replace(env.window, frozen_json="{}")
    monkeypatch.setattr(wrapper, "open_window", lambda _conn: older)
    batch = [_decision(env, A, "sell", notional=500.0), _decision(env, B, "buy", notional=300.0)]
    calls = len(env.fake.calls)

    with pytest.raises(ValueError, match=r"lacks cost keys \['costs.per_side_bps'"):
        _execute(_gate(env, alerter_conn), env, batch)
    assert not env.fake.calls[calls:]


@pytest.mark.parametrize(
    "frozen_json",
    [
        "not json",
        "[]",
        '{"costs.per_side_bps": -1, "costs.commission_per_share": 0, '
        '"costs.commission_per_order": 0}',
        '{"costs.per_side_bps": 15, "costs.commission_per_share": 0}',
    ],
)
def test_frozen_costs_refuse_anything_but_the_three_valid_keys(frozen_json: str) -> None:
    with pytest.raises(ValueError, match="frozen"):
        wrapper._frozen_costs(PaperWindowRow(**{**_window_fields(), "frozen_json": frozen_json}))


def _window_fields() -> dict[str, Any]:
    at = CUT - timedelta(days=2)
    return {
        "window_id": 7,
        "hypothesis_id": 1,
        "first_rebalance_session": T_I,
        "account_id": "PA1",
        "starting_cash": 1.0,
        "starting_equity": 1.0,
        "code_version": "test",
        "started_at": at,
        "frozen_json": "{}",
        "frozen_sha256": "0" * 64,
        "known_at": at,
        "ingested_at": at,
    }


def test_execute_needs_a_journaled_run(env: Env, alerter_conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="journaled run"):
        _gate(env, alerter_conn).execute(replace(env.run, run_id=None), [], [])
