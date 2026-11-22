"""Fake-clock and fake-cash checks for the wrapper's buys phase (T60e)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import Accept, FillAt
from tradepartner.calendar import session_open
from tradepartner.config import BookExecution, RiskConfig, Settings
from tradepartner.errors import LimitBreachError
from tradepartner.execution.wrapper import _ledger_equity, paper_execution_fields
from tradepartner.store.journal import (
    DecisionRow,
    PaperRunResultRow,
    PaperWindowRow,
    ReconcileBreakRow,
)

from .test_wrapper_phases import (
    CUT,
    FROZEN,
    PREV,
    PRICE,
    SYMBOLS,
    A,
    B,
    C,
    Env,
    FixedClock,
    S,
    _append,
    _bar,
    _decision,
    _execute,
    _gate,
    _hold,
    _override,
    _query,
    _retick,
    _run,
    _settings,
    _stale_order,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)


@pytest.fixture
def timed_env(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> Env:
    """Build the fixture's run after moving the clock before the open."""
    settings = _settings(journal_settings.store.path)
    _retick(settings)
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
    opening = session_open(S)
    fixed_clock.now = opening - timedelta(minutes=20)
    run = _run(settings, open_window, S, opening - timedelta(minutes=25))
    return Env(settings, open_window, fixed_clock, earlier, run)


def _submission_times(env: Env) -> list[tuple[OrderRequest, datetime]]:
    times: list[tuple[OrderRequest, datetime]] = []
    env.fake.on_submit = lambda request: times.append((request, env.clock.now))
    return times


def test_buy_cash_excludes_positive_open_name_cash_break(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=640.0)
    at = env.run.started_at
    _append(
        env.settings,
        ReconcileBreakRow(
            break_id=f"{env.window.window_id}:{A}:1",
            window_id=env.window.window_id,  # type: ignore[arg-type]
            book_id=env.window.book_id,
            event="opened",
            parts_json='[{"kind":"name_cash","cash_amount":30.0,"activity_ids":["credit"]}]',
            cause="corporate_action",
            security_id=A,
            cash_amount=30.0,
            material=True,
            dollar_size=30.0,
            first_seen_session=PREV,
            reconciliation_id=1,
            known_at=at,
            ingested_at=at,
        ),
    )
    gate = _gate(env, alerter_conn)
    book = gate._read_book(env.run, [])
    assert gate._buys_cash(env.fake.account(), book, S) == pytest.approx(610.0)


def test_buy_waits_until_sell_deadline_when_sell_stays_live(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _hold(env, A, 10.0)
    _hold(env, C, 10.0)
    opening = session_open(S)
    sell = _decision(env, A, "sell", notional=500.0)
    second_sell = _decision(env, C, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())
    env.fake.script(Accept())
    times = _submission_times(env)

    _execute(_gate(env, alerter_conn), env, [sell, second_sell, buy])

    assert [request.side for request, _ in times] == [Side.SELL, Side.SELL, Side.BUY]
    assert times[0][1] < opening
    assert times[2][1] >= opening + timedelta(seconds=env.settings.paper.sell_wait_seconds)
    assert env.fake.get_order(times[0][0].client_order_id).status.value == "filled"
    assert env.fake.get_order(times[1][0].client_order_id).status.value == "accepted"
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(True,)]


def test_buy_starts_as_soon_as_every_sell_is_terminal(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _hold(env, A, 10.0)
    opening = session_open(S)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())
    times = _submission_times(env)

    _execute(_gate(env, alerter_conn), env, [sell, buy])

    assert [request.side for request, _ in times] == [Side.SELL, Side.BUY]
    assert (
        times[0][1]
        <= times[1][1]
        < opening + timedelta(seconds=env.settings.paper.sell_wait_seconds)
    )
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(False,)]


def test_whole_share_buy_is_last_and_fills_stay_within_buffered_cash(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    starting_cash = Decimal("610.00")
    env.new_fake(cash=float(starting_cash), round_cash_to_cent=True)
    whole = _decision(env, A, "buy", notional=300.0, whole_share=True)
    fractional = _decision(env, B, "buy", notional=300.0)
    other = _decision(env, C, "buy", notional=300.0)
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    buffered_price = Decimal(str(PRICE)) * (
        Decimal(1) + Decimal(str(FROZEN.whole_share_price_buffer))
    )
    env.fake.script(FillAt(float(buffered_price)))
    start_cash = Decimal(repr(env.fake.account().cash))
    times = _submission_times(env)

    outcome = _execute(_gate(env, alerter_conn), env, [whole, fractional, other])

    submitted = [request for request, _ in times]
    assert submitted == _submits(env.fake)
    assert submitted[-1].symbol == "DUALA"
    rate = Decimal(1) + Decimal(str(env.settings.costs.per_side_bps)) / Decimal(10_000)
    per_buy = (starting_cash / rate / Decimal(3)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    assert [request.notional for request in submitted[:2]] == [float(per_buy)] * 2
    assert submitted[-1].quantity == 1.0
    assert (starting_cash / rate / Decimal(3) / Decimal(str(PRICE))).to_integral_value(
        rounding=ROUND_DOWN
    ) == 2  # an unbuffered sizing would order two shares
    assert all(request.side is Side.BUY for request in submitted)
    commission_per_order = Decimal(str(env.settings.costs.commission_per_order))
    commission_per_share = Decimal(str(env.settings.costs.commission_per_share))
    buffered_cost = sum(
        (
            Decimal(str(request.notional)) * rate
            + Decimal(str(request.notional)) / Decimal(str(PRICE)) * commission_per_share
            + commission_per_order
            for request in submitted[:2]
        ),
        Decimal(0),
    )
    buffered_cost += (
        Decimal(str(submitted[-1].quantity)) * buffered_price * rate
        + Decimal(str(submitted[-1].quantity)) * commission_per_share
        + commission_per_order
    )
    assert buffered_cost <= Decimal(str(outcome.cash))
    spent = sum(
        (Decimal(repr(fill.quantity)) * Decimal(repr(fill.price)) for fill in env.fake.fills()),
        Decimal(0),
    )
    assert spent <= start_cash
    assert env.fake.account().cash >= 0
    assert outcome.cash_left is not None and outcome.cash_left >= 0


# --- the cash sleeve and item 6's line (paper spec amendment 2026-11-01, T370) -----


def _mapped(env: Env, **execution: Any) -> None:
    """Put the env's book in `paper.execution` with `execution`'s fields."""
    paper = env.settings.paper.model_copy(
        update={"execution": {env.window.book_id: BookExecution(**execution)}}
    )
    env.settings = env.settings.model_copy(update={"paper": paper})


def _variant_lines(notes: list[str]) -> list[str]:
    return [n for n in notes if n.startswith("paper_execution ")]


def _equity(env: Env, alerter_conn: duckdb.DuckDBPyConnection) -> float:
    gate = _gate(env, alerter_conn)
    return _ledger_equity(gate._read_book(env.run, []))


def _three_buys(env: Env) -> list[DecisionRow]:
    return [_decision(env, sid, "buy", notional=300.0) for sid in (A, B, C)]


def _per_buy(env: Env, cash: Decimal) -> float:
    rate = Decimal(1) + Decimal(str(env.settings.costs.per_side_bps)) / Decimal(10_000)
    return float((cash / rate / Decimal(3)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


@pytest.mark.parametrize("in_map", [False, True])
def test_c_zero_sizes_and_journals_as_today(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, in_map: bool
) -> None:
    """A book outside the map and a book in it with c = 0 send the same orders,
    pinned to today's sizing; only the mapped book writes item 6's line."""
    if in_map:
        _mapped(env)
    env.new_fake(cash=610.0)
    for _ in range(3):
        env.fake.script(FillAt())
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, _three_buys(env), (), notes=notes)

    per_buy = _per_buy(env, Decimal("610.00"))
    assert [r.notional for r in _submits(env.fake)] == [per_buy] * 3
    assert outcome.cash == pytest.approx(610.0)
    rows = _query(
        env.settings,
        "SELECT phase, side, notional, quantity, attempt, sells_in_flight_at_submit,"
        " time_in_force FROM orders WHERE session = ? ORDER BY client_order_id",
        [S],
    )
    assert rows == [("buy", "buy", per_buy, None, 1, False, "day")] * 3
    lines = _variant_lines(notes)
    if not in_map:
        assert lines == []
        return
    (line,) = lines
    fields = paper_execution_fields(line)
    assert fields["sleeve_fraction"] == 0.0
    assert fields["sleeve_cash"] == 0.0
    assert fields["arm"] == "V0" and fields["fallback"] == "none"


def test_sleeve_is_held_out_of_the_buys_cash(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """(ii)'s cash is `_buys_cash` less c x E: the buys size from 610 and the sleeve
    stays in the account after every buy fills; the line parses back to the run's
    values."""
    c = 0.05
    _mapped(env, cash_sleeve_fraction=c)
    sleeve = c * _equity(env, alerter_conn)
    start = 610.0 + sleeve
    env.new_fake(cash=start)
    for _ in range(3):
        env.fake.script(FillAt())
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, _three_buys(env), (), notes=notes)

    assert outcome.cash == pytest.approx(610.0)
    assert [r.notional for r in _submits(env.fake)] == [_per_buy(env, Decimal("610.00"))] * 3
    assert env.fake.account().cash >= sleeve
    (line,) = _variant_lines(notes)
    fields = paper_execution_fields(line)
    assert fields == {
        "arm": "V0",
        "timing": "after_sells",
        "sleeve_fraction": c,
        "sleeve_cash": sleeve,
        "idle_cash": start,
        "min_cash": start,
        "open_buys": 0,
        "open_notional": 0.0,
        "open_unsent": 0,
        "fallback": "none",
        "opg_after_cutoff": 0,
        "submit_at": "open",
    }


def test_sleeve_above_the_cash_floors_the_buys_cash_at_zero(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    c = 0.10
    _mapped(env, cash_sleeve_fraction=c)
    sleeve = c * _equity(env, alerter_conn)
    env.new_fake(cash=sleeve / 2)
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, _three_buys(env), (), notes=notes)

    assert outcome.cash == 0.0
    assert _submits(env.fake) == []
    (line,) = _variant_lines(notes)
    assert paper_execution_fields(line)["min_cash"] == sleeve / 2


def test_idle_cash_is_read_after_the_sells_last_submit(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """With a sells phase, B comes from one read after the sells' submits, before
    any sell fills: the sell's proceeds are not in it."""
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    _hold(env, A, 10.0)
    before = env.fake.account().cash
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=300.0)
    env.fake.script(Accept())  # the sell stays live: (ii) waits for the deadline
    env.fake.script(FillAt())
    notes: list[str] = []
    reads: list[float] = []
    account = env.fake.account

    def counted() -> Any:
        reading = account()
        reads.append(reading.cash)
        return reading

    env.fake.account = counted  # type: ignore[method-assign]

    _gate(env, alerter_conn).execute(env.run, [sell, buy], (), notes=notes)

    (line,) = _variant_lines(notes)
    fields = paper_execution_fields(line)
    # Two reads: B after the sell's submit, then (ii)'s; neither holds a proceed.
    assert reads == [before, before]
    assert fields["idle_cash"] == pytest.approx(before)
    assert fields["min_cash"] == pytest.approx(before)
    assert [r.side for r in _submits(env.fake)][-2:] == [Side.SELL, Side.BUY]


@pytest.mark.parametrize(
    ("execution", "arm", "fallback"),
    [
        ({"buy_timing": "at_open"}, "V1", "open_tranche_not_built"),
        ({"buy_timing": "alternate", "alternate_start": S}, "V1", "open_tranche_not_built"),
        ({"buy_timing": "alternate", "alternate_start": PREV}, "V0", "none"),
    ],
)
def test_the_line_carries_the_arm_and_a_v1_session_still_runs_two_phases(
    env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    execution: dict[str, Any],
    arm: str,
    fallback: str,
) -> None:
    """Until T371 a V1 session keeps its arm and says why it ran as V0; its orders
    are the V0 run's."""
    _mapped(env, **execution)
    env.new_fake(cash=610.0)
    for _ in range(3):
        env.fake.script(FillAt())
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, _three_buys(env), (), notes=notes)

    assert [r.notional for r in _submits(env.fake)] == [_per_buy(env, Decimal("610.00"))] * 3
    (line,) = _variant_lines(notes)
    fields = paper_execution_fields(line)
    assert (fields["arm"], fields["fallback"]) == (arm, fallback)
    assert fields["timing"] == execution["buy_timing"]


def test_the_line_is_written_when_the_batch_halts(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A fault in the buys phase still leaves item 6's line, with the cash read."""
    _mapped(env)
    env.new_fake(cash=610.0)
    notes: list[str] = []
    gate = _gate(env, alerter_conn, RiskConfig(max_orders_per_run=1))

    with pytest.raises(LimitBreachError):
        gate.execute(env.run, _three_buys(env), (), notes=notes)

    assert _submits(env.fake) == []
    (line,) = _variant_lines(notes)
    assert paper_execution_fields(line)["min_cash"] == 610.0


def test_paper_execution_fields_refuses_another_line() -> None:
    with pytest.raises(ValueError, match="not a paper_execution line"):
        paper_execution_fields("paper_phase buys_sized=2026-10-01T13:00:00+00:00")
    with pytest.raises(ValueError, match="missing fields"):
        paper_execution_fields("paper_execution arm=V0")


def test_a_sleeve_not_yet_full_shrinks_the_buy_never_the_sell(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The sell goes at its planned notional; (ii) buys from the cash above the
    sleeve, so the sleeve is still in the account once both have filled."""
    env = timed_env
    c = 0.05
    _mapped(env, cash_sleeve_fraction=c)
    sleeve = c * _equity(env, alerter_conn)
    env.new_fake(cash=sleeve + 2000.0)
    _hold(env, A, 10.0)  # the fake spends 1000 on it
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [sell, buy], (), notes=notes)

    sent_sell, sent_buy = _submits(env.fake)
    assert (sent_sell.side, sent_sell.quantity) == (Side.SELL, 5.0)  # 500 at the $100 price
    assert sent_buy.side is Side.BUY
    assert outcome.cash == pytest.approx(1500.0)
    assert sent_buy.notional is not None and sent_buy.notional < 1500.0
    assert env.fake.account().cash >= sleeve
    (line,) = _variant_lines(notes)
    assert paper_execution_fields(line)["sleeve_cash"] == pytest.approx(sleeve)


@pytest.mark.parametrize("with_sell", [False, True])
def test_a_mapped_book_with_the_switch_engaged_still_skips_before_any_read(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, with_sell: bool
) -> None:
    """#692 on a book in the map: the record's reads never run ahead of the switch,
    so an unpriced open buy still ends `skipped_kill_switch` with no broker call."""
    _mapped(env, cash_sleeve_fraction=0.05)
    env.new_fake(cash=10_000.0)
    _stale_order(env, "buy", quantity=3.0)
    batch = [_decision(env, B, "buy", notional=1000.0)]
    if with_sell:
        _hold(env, A, 10.0)
        batch.insert(0, _decision(env, A, "sell", notional=300.0))
    _override(env)
    calls = len(env.fake.calls)
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, batch, (), notes=notes)

    assert outcome.status == "skipped_kill_switch"
    assert not env.fake.calls[calls:]
    (line,) = _variant_lines(notes)
    assert paper_execution_fields(line)["idle_cash"] is None


def test_a_sells_only_batch_on_a_mapped_book_reads_no_cash(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """With no buys phase to follow, no `account()` read is added after the sells:
    the line's `idle_cash` and `min_cash` are `na`."""
    _mapped(env, cash_sleeve_fraction=0.05)
    _hold(env, A, 10.0)
    trim = _decision(env, A, "sell", notional=500.0)
    env.fake.script(FillAt())
    calls = len(env.fake.calls)
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [trim], (), notes=notes)

    assert outcome.status == "ok"
    assert "account" not in [c.method for c in env.fake.calls[calls:]]
    (line,) = _variant_lines(notes)
    fields = paper_execution_fields(line)
    assert (fields["idle_cash"], fields["min_cash"]) == (None, None)
