"""The halt policy in the wrapper: the per-name refusal allowlist as skips, the
skip cap as a share of the plan (paper spec amendment 2026-10-18 items 2, 3 and 5;
ADR 0023 D2; plan T202, as amended by #1808).

Every case runs `RiskGatedBroker.execute` on a temp copy of the fixture store with
the scripted fake (`tests/execution/test_wrapper_phases.py`'s harness). A submit-time
refusal is scripted as a `TransportFault` carrying the typed error the Alpaca adapter
maps the broker's refusal to (`tests/adapters/test_alpaca_broker.py` pins that map);
the `assets` read's unknown name is the fake's `UNRESOLVED_ASSET` answer (#1729)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingError
from tradepartner.adapters.broker import Asset
from tradepartner.adapters.fake_broker import FakeBroker, TransportFault
from tradepartner.config import RiskConfig
from tradepartner.errors import (
    AssetNotFoundError,
    AssetNotTradableError,
    ClockError,
    NotFractionableError,
    ReconciliationError,
    SkipCapError,
)
from tradepartner.execution import wrapper
from tradepartner.execution.alerts import Alerter
from tradepartner.execution.collect import WriteOffContext, collect
from tradepartner.execution.reconcile_run import frozen_risk
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.journal import DecisionRow, OrderEventRow, OrderRow, append

from .test_wrapper_phases import (
    CUT,
    FROZEN,
    PREV,
    PRICE,
    SYMBOLS,
    T_I,
    A,
    B,
    C,
    Env,
    _decision,
    _decision_events,
    _execute,
    _gate,
    _hold,
    _missed,
    _query,
    _submits,
)
from .test_wrapper_phases import (
    alerter_conn as alerter_conn,
)
from .test_wrapper_phases import (
    env as env,
)

#: Each typed refusal and the skip reason it journals on a buy (spec item 2).
REFUSALS = [
    (AssetNotFoundError, "skip_asset_unknown"),
    (NotFractionableError, "skip_not_fractionable"),
    (AssetNotTradableError, "skip_untradable"),
]
SKIP_REASONS = {"skip_asset_unknown", "skip_not_fractionable", "skip_untradable"}
#: A buy's notional and weight in the many-name plans: 200 x $400 = $80,000 of
#: the window's $100,000, each $400 under 5 % of equity and 0.004 under 0.05.
NOTIONAL = 400.0
WEIGHT = 0.004


# --- helpers -----------------------------------------------------------------------


def _many_names(env: Env, count: int, unknown: int) -> list[str]:
    """`count` extra listed, priced names in the temp store (one write chunk), the
    first `unknown` of them absent from the fake's catalog (its `assets` read
    answers `UNRESOLVED_ASSET`, Alpaca's 404, #1729); returns their security ids."""
    master_at = datetime(2018, 1, 2, 21, tzinfo=CUT.tzinfo)
    names: list[tuple[str, str]] = []
    with open_for_write(env.settings) as conn:
        for i in range(count):
            sid = f"SEC_ZQ{i:03d}"
            ticker = f"ZQ{chr(ord('A') + i // 26)}{chr(ord('A') + i % 26)}"
            names.append((sid, ticker))
            common = {
                "known_at": master_at,
                "ingested_at": master_at,
                "source": "edgar",
                "provenance": "filing",
            }
            insert_row(
                conn,
                "securities",
                {"security_id": sid, "cik": f"CIK{9000000 + i:010d}", "name": sid, **common},
            )
            insert_row(
                conn,
                "listings",
                {
                    "security_id": sid,
                    "ticker": ticker,
                    "exchange": "NYSE",
                    "class_title": "Common Stock",
                    "valid_from": master_at.date(),
                    **common,
                },
            )
            insert_row(
                conn,
                "prices_daily",
                {
                    "security_id": sid,
                    "session": PREV,
                    "open": PRICE,
                    "high": PRICE,
                    "low": PRICE,
                    "close": PRICE,
                    "volume": 1000,
                    "known_at": CUT - timedelta(hours=1),
                    "ingested_at": CUT - timedelta(hours=1),
                    "source": "alpaca",
                    "provenance": "bar",
                },
            )
    env.prices.update({ticker: PRICE for _, ticker in names})
    known = [ticker for _, ticker in names[unknown:]]
    env.new_fake(assets=FakeBroker.active_assets(*SYMBOLS.values(), *known))
    return [sid for sid, _ in names]


def _buys(env: Env, security_ids: list[str]) -> list[DecisionRow]:
    """One buy decision per name, $400 at weight 0.004, in one write chunk."""
    at = env.run.started_at
    rows = []
    with open_for_write(env.settings) as conn:
        for sid in security_ids:
            row = DecisionRow(
                run_id=env.run.run_id,  # type: ignore[arg-type]
                rebalance_session=T_I,
                security_id=sid,
                target_weight=WEIGHT,
                side="buy",
                planned_notional=NOTIONAL,
                planned_quantity=None,
                target_notional=NOTIONAL,
                whole_share=False,
                decision="trade",
                reason=None,
                known_at=at,
                ingested_at=at,
            )
            rows.append(replace(row, decision_id=append(conn, row)))
    return rows


def _skipped_alerts(env: Env) -> list[tuple[int | None, str]]:
    return [
        (run_id, message)
        for run_id, message in _query(
            env.settings, "SELECT run_id, message FROM alerts WHERE kind = 'skipped'"
        )
    ]


def _events_of(env: Env, coid: str) -> list[tuple[str, str | None]]:
    return [
        (status, reason)
        for status, reason in _query(
            env.settings,
            "SELECT status, reason FROM order_events WHERE client_order_id = ? ORDER BY rowid",
            [coid],
        )
    ]


def _orders_count(env: Env) -> int:
    ((count,),) = _query(
        env.settings, "SELECT count(*) FROM orders WHERE run_id = ?", [env.run.run_id]
    )
    return int(count)


def _pre_t202_frozen(env: Env, max_skips_per_run: int) -> RiskConfig:
    """The frozen section of a window started before `risk.max_skip_share`
    existed (the two open windows): today's other `risk.*` keys, through
    `frozen_risk`, which reads the missing key as 0 (spec item 3)."""
    values: dict[str, Any] = {
        f"risk.{k}": v
        for k, v in RiskConfig().model_dump(mode="json").items()
        if k != "max_skip_share"
    }
    values["risk.max_skips_per_run"] = max_skips_per_run
    return frozen_risk(replace(env.window, frozen_json=json.dumps(values)))


# --- the assets read: an unknown buy is one skip, never a halt ---------------------


def test_one_unknown_asset_in_a_200_name_plan_is_one_skip_and_199_orders(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Spec item 5's acceptance: 199 orders submitted, one `skip_asset_unknown`,
    one `skipped` alert naming the name and its reason, the batch ends `ok`."""
    names = _many_names(env, 200, unknown=1)
    rows = _buys(env, names)

    outcome = _execute(_gate(env, alerter_conn), env, rows)

    assert outcome.status == "ok"
    assert len(_submits(env.fake)) == 199
    assert _orders_count(env) == 199
    assert _decision_events(env.settings) == [
        (rows[0].decision_id, "skipped", "skip_asset_unknown")
    ]
    ((run_id, message),) = _skipped_alerts(env)
    assert run_id == env.run.run_id
    assert "skip_asset_unknown" in message
    assert names[0] in message or "ZQAA" in message
    assert _missed(env.settings) == []


def test_21_unknown_assets_in_a_200_name_plan_halt_at_the_share_cap(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """max(10, floor(0.10 x 200)) = 20: the 21st skip halts the batch before any
    submit, with no `decision_events` row and the rebalance `missed`."""
    names = _many_names(env, 200, unknown=21)
    rows = _buys(env, names)
    frozen = RiskConfig(max_skips_per_run=10, max_skip_share=0.10)

    with pytest.raises(SkipCapError):
        _execute(_gate(env, alerter_conn, frozen), env, rows)

    assert _submits(env.fake) == []
    assert _orders_count(env) == 0
    assert _decision_events(env.settings) == []
    assert _missed(env.settings) == [("missed", "skip_cap")]


def test_5_unknown_assets_under_the_absolute_floor_do_not_halt(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A 20-name plan: the share gives floor(0.10 x 20) = 2, the floor
    `max_skips_per_run` = 10 binds, so 5 skips pass and 15 orders go out."""
    names = _many_names(env, 20, unknown=5)
    rows = _buys(env, names)
    frozen = RiskConfig(max_skips_per_run=10, max_skip_share=0.10)

    outcome = _execute(_gate(env, alerter_conn, frozen), env, rows)

    assert outcome.status == "ok"
    assert len(_submits(env.fake)) == 15
    assert sorted(_decision_events(env.settings)) == sorted(
        (row.decision_id, "skipped", "skip_asset_unknown") for row in rows[:5]
    )
    ((_, message),) = _skipped_alerts(env)
    assert message.count("skip_asset_unknown") >= 1


def test_a_window_frozen_before_the_key_halts_at_exactly_its_frozen_max_skips_per_run(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A window whose `frozen_json` predates `risk.max_skip_share` reads it as 0:
    max(10, 0) = 10, so 11 unknown names of 200 halt, where the default share
    (0.10, a cap of 20) would have passed them."""
    frozen = _pre_t202_frozen(env, 10)
    assert frozen.max_skip_share == 0
    names = _many_names(env, 200, unknown=11)
    rows = _buys(env, names)

    with pytest.raises(SkipCapError):
        _execute(_gate(env, alerter_conn, frozen), env, rows)

    assert _submits(env.fake) == []
    assert _decision_events(env.settings) == []


# --- a held name is never in the allowlist -------------------------------------------


@pytest.mark.parametrize("decision", ["trade", "forced_exit"])
def test_a_held_name_the_assets_read_does_not_know_halts_reconciliation(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, decision: str
) -> None:
    """PR #1730 answered a broker 404 as an untradable asset for any name; T202
    narrows that to buys: a trim or forced exit of a held name the broker does
    not know means the ledger and the broker disagree."""
    _hold(env, A, 10.0)
    env.fake.remove_asset(SYMBOLS[A])
    if decision == "forced_exit":
        sell = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
        batch: tuple[list[DecisionRow], tuple[DecisionRow, ...]] = ([], (sell,))
    else:
        batch = ([_decision(env, A, "sell", notional=300.0)], ())

    with pytest.raises(ReconciliationError):
        _execute(_gate(env, alerter_conn), env, *batch)

    assert _submits(env.fake) == []
    assert _decision_events(env.settings) == []


@pytest.mark.parametrize(
    ("decision", "reason"), [("trade", "skip_untradable"), ("forced_exit", "untradable")]
)
def test_a_held_untradable_name_keeps_its_skip(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, decision: str, reason: str
) -> None:
    """Req 3(c)'s held-name rule is unchanged: `tradable = false` on a held name is
    `skip_untradable` for a trim and `untradable` for a forced exit, never a halt."""
    _hold(env, A, 10.0)
    env.fake.set_asset(
        SYMBOLS[A], Asset(tradable=False, fractionable=True, status="active", cusip=None)
    )
    if decision == "forced_exit":
        sell = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
        outcome = _execute(_gate(env, alerter_conn), env, [], (sell,))
    else:
        sell = _decision(env, A, "sell", notional=300.0)
        outcome = _execute(_gate(env, alerter_conn), env, [sell])

    assert outcome.status == "ok"
    assert _submits(env.fake) == []
    assert _decision_events(env.settings) == [(sell.decision_id, "skipped", reason)]


@pytest.mark.parametrize("error", [AssetNotFoundError, NotFractionableError])
@pytest.mark.parametrize("decision", ["trade", "forced_exit"])
def test_a_refusal_of_a_held_names_sell_at_submit_halts_reconciliation(
    env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    error: type[Exception],
    decision: str,
) -> None:
    """A 404 on a held name's sell or forced exit, or a sell refused as not
    fractionable, is never a skip: `ReconciliationError`, no `skipped` row."""
    _hold(env, A, 10.0)
    env.fake.script(TransportFault(error=error("refused")))
    if decision == "forced_exit":
        sell = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="delisted")
        batch: tuple[list[DecisionRow], tuple[DecisionRow, ...]] = ([], (sell,))
    else:
        batch = ([_decision(env, A, "sell", notional=300.0)], ())

    with pytest.raises(ReconciliationError):
        _execute(_gate(env, alerter_conn), env, *batch)

    assert _decision_events(env.settings) == []
    assert _skipped_alerts(env) == []


# --- a refusal on submit: closed `rejected` with its skip reason ---------------------


@pytest.mark.parametrize(("error", "reason"), REFUSALS)
def test_an_allowlisted_submit_refusal_closes_rejected_with_its_skip_reason(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, error: type[Exception], reason: str
) -> None:
    """The refused buy's `pending` order gets one terminal `rejected` event with
    the skip reason, its decision one `skipped` row with the same reason (and no
    `written_off`); the other buys submit; one `skipped` alert; `ok`. With
    `max_rejections_per_run` = 0 the refusal is not a rejection (spec item 2)."""
    buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B, C)]
    env.fake.script(TransportFault(error=error("refused")))
    frozen = FROZEN.model_copy(update={"max_rejections_per_run": 0})

    outcome = _execute(_gate(env, alerter_conn, frozen), env, buys)

    assert outcome.status == "ok"
    first, *rest = _submits(env.fake)
    assert first.symbol == SYMBOLS[A]
    assert [r.symbol for r in rest] == [SYMBOLS[B], SYMBOLS[C]]
    assert _events_of(env, first.client_order_id) == [("pending", None), ("rejected", reason)]
    for request in rest:
        assert [s for s, _ in _events_of(env, request.client_order_id)][:2] == [
            "pending",
            "accepted",
        ]
    assert _decision_events(env.settings) == [(buys[0].decision_id, "skipped", reason)]
    ((run_id, message),) = _skipped_alerts(env)
    assert run_id == env.run.run_id
    assert reason in message
    assert A in message or SYMBOLS[A] in message


def test_the_skipped_row_is_written_before_the_rejected_event(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decision's `skipped` row is journaled first, then the order's `rejected`
    event, as the phase-time skips precede the `orders` rows."""
    real_append = wrapper.append
    written: list[tuple[str, str]] = []

    def spy(conn: duckdb.DuckDBPyConnection, row: Any) -> Any:
        written.append((type(row).__name__, str(getattr(row, "status", ""))))
        return real_append(conn, row)

    monkeypatch.setattr(wrapper, "append", spy)
    buy = _decision(env, A, "buy", notional=3000.0)
    env.fake.script(TransportFault(error=AssetNotFoundError("refused")))
    _execute(_gate(env, alerter_conn), env, [buy])

    skipped = written.index(("DecisionEventRow", "skipped"))
    rejected = written.index(("OrderEventRow", "rejected"))
    assert skipped < rejected


def test_a_fault_between_the_skipped_row_and_the_rejected_event_leaves_neither(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One write chunk: a fault injected at the `rejected` event's write rolls the
    `skipped` row back too, so the order stays `pending` (settled by `paper
    resume`) and the next collection refuses it rather than back-filling a
    `written_off` row for a terminal buy that lacks its skip."""
    real_append = wrapper.append

    def faulty(conn: duckdb.DuckDBPyConnection, row: Any) -> Any:
        if isinstance(row, OrderEventRow) and row.status == "rejected":
            raise RuntimeError("injected store fault")
        return real_append(conn, row)

    monkeypatch.setattr(wrapper, "append", faulty)
    buy = _decision(env, A, "buy", notional=3000.0)
    env.fake.script(TransportFault(error=AssetNotFoundError("refused")))

    with pytest.raises(RuntimeError, match="injected"):
        _execute(_gate(env, alerter_conn), env, [buy])

    ((coid,),) = _query(
        env.settings, "SELECT client_order_id FROM orders WHERE run_id = ?", [env.run.run_id]
    )
    at = env.run.started_at
    row = OrderRow(
        client_order_id=coid,
        decision_id=buy.decision_id,  # type: ignore[arg-type]
        run_id=env.run.run_id,
        session=env.run.session,  # type: ignore[arg-type]
        attempt=1,
        phase="buy",
        security_id=A,
        symbol=SYMBOLS[A],
        side="buy",
        notional=3000.0,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )
    assert _events_of(env, row.client_order_id) == [("pending", None)]
    assert _decision_events(env.settings) == []

    monkeypatch.setattr(wrapper, "append", real_append)
    with pytest.raises(ValueError, match="paper resume"):
        collect(
            env.fake,
            lambda: open_for_write(env.settings),
            [row],
            env.clock,
            "run",
            env.run.run_id,  # type: ignore[arg-type]
            FROZEN,
            env.settings,
            write_offs=WriteOffContext(
                window_id=env.window.window_id,  # type: ignore[arg-type]
                actions_as_of=pl.DataFrame(),
                price_of=lambda _sid: PRICE,
                session=env.run.session,  # type: ignore[arg-type]
            ),
        )
    assert _decision_events(env.settings) == []


def test_a_batch_whose_every_order_is_an_allowlisted_refusal_ends_ok(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Three buys, three allowlisted refusals: not "every order rejected" (the
    refusals leave the run's order total too), three skips under the cap of 10,
    one `skipped` alert listing all three, `ok`."""
    buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B, C)]
    for error, _ in REFUSALS:
        env.fake.script(TransportFault(error=error("refused")))

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    assert outcome.status == "ok"
    assert _decision_events(env.settings) == [
        (buy.decision_id, "skipped", reason)
        for buy, (_, reason) in zip(buys, REFUSALS, strict=True)
    ]
    ((_, message),) = _skipped_alerts(env)
    assert all(reason in message for reason in SKIP_REASONS)


@pytest.mark.parametrize("phase_time_skip", [False, True])
def test_a_submit_time_skip_over_the_cap_halts_before_the_next_submit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, phase_time_skip: bool
) -> None:
    """`max_skips_per_run` = 1 on a plan under the share (floor(0.10 x 3) = 0):
    the second skip, at submit, raises `SkipCapError` before the next submit. The
    count includes a phase-time skip: one `skip_asset_unknown` from the `assets`
    read plus one refusal at submit is two. Both refused orders are closed
    `rejected` with their skip reasons; the order never submitted stays `pending`."""
    frozen = FROZEN.model_copy(update={"max_skips_per_run": 1})
    if phase_time_skip:
        env.fake.remove_asset(SYMBOLS[C])
        buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B, C)]
        env.fake.script(TransportFault(error=NotFractionableError("refused")))
        refused, expected_submits = ["skip_not_fractionable"], 1
    else:
        buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B, C)]
        env.fake.script(TransportFault(error=AssetNotFoundError("refused")))
        env.fake.script(TransportFault(error=NotFractionableError("refused")))
        refused, expected_submits = ["skip_asset_unknown", "skip_not_fractionable"], 2

    with pytest.raises(SkipCapError):
        _execute(_gate(env, alerter_conn, frozen), env, buys)

    submits = _submits(env.fake)
    assert len(submits) == expected_submits
    assert [_events_of(env, r.client_order_id) for r in submits] == [
        [("pending", None), ("rejected", reason)] for reason in refused
    ]
    submitted = {r.client_order_id for r in submits}
    never = [
        coid
        for (coid,) in _query(
            env.settings, "SELECT client_order_id FROM orders WHERE run_id = ?", [env.run.run_id]
        )
        if coid not in submitted
    ]
    assert never and all(_events_of(env, coid) == [("pending", None)] for coid in never)


# --- review findings (T202 quant-auditor and safety-reviewer passes, #1829) --------


@pytest.mark.parametrize("via", ["assets_read", "submit"])
def test_a_buy_adding_to_a_held_name_the_broker_does_not_know_halts_reconciliation(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, via: str
) -> None:
    """Spec item 2: a held name is never in the allowlist, whatever the side. A buy
    that adds to a ledger-held name the broker cannot find (a 404 on the `assets`
    read, or `AssetNotFoundError` on submit) is the same ledger/broker disagreement
    as a sell's: `ReconciliationError`, never `skip_asset_unknown`."""
    _hold(env, A, 10.0)
    if via == "assets_read":
        env.fake.remove_asset(SYMBOLS[A])
    else:
        env.fake.script(TransportFault(error=AssetNotFoundError("refused")))
    buy = _decision(env, A, "buy", notional=3000.0)

    with pytest.raises(ReconciliationError):
        _execute(_gate(env, alerter_conn), env, [buy])

    assert _decision_events(env.settings) == []
    assert _skipped_alerts(env) == []
    if via == "assets_read":
        assert _submits(env.fake) == []


def test_a_refused_buys_cash_stays_in_cash_left(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The quant-auditor's probe: three $3,000 buys at the frozen 15 bps, the first
    refused at submit. Only the two submitted buys are spent, $3,004.50 each, so
    `cash_left` = 100,000 - 6,009.00 = 93,991.00 (not 90,986.50), and the run's
    `unspent_cash` test sees the refused $3,000 back."""
    buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B, C)]
    env.fake.script(TransportFault(error=AssetNotFoundError("refused")))

    outcome = _execute(_gate(env, alerter_conn), env, buys)

    assert outcome.status == "ok"
    assert outcome.cash == pytest.approx(100_000.0)
    assert outcome.cash_left == pytest.approx(93_991.0)


def test_a_halt_at_the_skip_cap_during_submit_still_alerts_the_refused_names(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The safety review's NIT 3: the two refusals that took the run over the cap are
    journaled skips, so the run's `skipped` alert names them and their reasons
    even though the batch halts `SkipCapError`."""
    frozen = FROZEN.model_copy(update={"max_skips_per_run": 1})
    buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B, C)]
    env.fake.script(TransportFault(error=AssetNotFoundError("refused")))
    env.fake.script(TransportFault(error=NotFractionableError("refused")))

    with pytest.raises(SkipCapError):
        _execute(_gate(env, alerter_conn, frozen), env, buys)

    ((run_id, message),) = _skipped_alerts(env)
    assert run_id == env.run.run_id
    assert "skip_asset_unknown" in message and "skip_not_fractionable" in message
    assert A in message or SYMBOLS[A] in message
    assert B in message or SYMBOLS[B] in message


def test_a_clock_error_writing_the_skipped_alert_is_not_swallowed(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The safety review's NIT 2. The run writes its other alerts with no guard
    (`run._alert`: an error raises and takes the run's fault path), so a
    `ClockError` met writing the `skipped` alert raises out of `execute` too, to
    halt, rather than being logged and dropped."""
    real_write = Alerter.write

    def clock_fault(self: Alerter, kind: str, *args: Any, **kwargs: Any) -> Any:
        if kind == "skipped":
            raise ClockError("clock went back")
        return real_write(self, kind, *args, **kwargs)

    monkeypatch.setattr(Alerter, "write", clock_fault)
    env.fake.remove_asset(SYMBOLS[C])
    buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, C)]

    with pytest.raises(ClockError):
        _execute(_gate(env, alerter_conn), env, buys)


# --- anything else still halts ---------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (422, {"code": 42210000, "message": "invalid order"}),
        (403, {"code": 40310000, "message": "insufficient buying power"}),
        (404, {"code": 40400000, "message": "not found"}),
    ],
)
def test_an_unknown_4xx_on_a_buy_still_halts(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, status: int, body: dict[str, Any]
) -> None:
    """A 4xx outside the allowlist halts under req 4 as before: no skip, no
    `rejected` event, the order left `pending`."""
    buys = [_decision(env, sid, "buy", notional=3000.0) for sid in (A, B)]
    # `script_call_fault`, not `TransportFault`: the raw error is raised as is
    # (a `TransportFault` copies its error, and this one's keyword-only
    # `retried` does not survive `copy.copy`).
    env.fake.script_call_fault(
        "submit", AlpacaTradingError(status, json.dumps(body), retried=False)
    )

    with pytest.raises(AlpacaTradingError):
        _execute(_gate(env, alerter_conn), env, buys)

    (first,) = _submits(env.fake)
    assert _events_of(env, first.client_order_id) == [("pending", None)]
    assert _decision_events(env.settings) == []
    assert _skipped_alerts(env) == []
