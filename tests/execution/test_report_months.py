"""Tracking comparison, monthly decomposition (Phase 4 spec req 10; plan T65).

Every "Tracking comparison" acceptance criterion on synthetic ledgers and a
fixture trial, following `test_residue.py`'s idiom: hand-built journal rows,
no store, no real broker.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.execution import report as report_module
from tradepartner.execution.plan import stop_session
from tradepartner.execution.report import (
    Journal,
    PriceOf,
    TrialPeriods,
    compare_periods,
    compare_targets,
)
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    FillRow,
    OrderedFill,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    PositionDailyRow,
    RebalanceEventRow,
)

WINDOW_ID = 7
A = "SEC_A"
B = "SEC_B"
T0 = date(2026, 9, 30)  # a rebalance session, month 0 start
T1 = date(2026, 10, 30)  # month 0 end / month 1 start
T2 = date(2026, 11, 30)  # month 1 end
_IDS = itertools.count(100)


def _utc(day: date, hour: int = 20) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _window(
    *,
    tracking_rule: str = "raw",
    tracking_k: float = 2.0,
    window_id: int = WINDOW_ID,
    fill_price: str = "close",
) -> PaperWindowRow:
    frozen = {
        "paper.tracking_k": tracking_k,
        "paper.tracking_rule": tracking_rule,
        "execution.fill_price": fill_price,
    }
    return PaperWindowRow(
        window_id=window_id,
        hypothesis_id=1,
        first_rebalance_session=T0,
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=_utc(T0),
        frozen_json=json.dumps(frozen),
        frozen_sha256="0" * 64,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )


def _trial(
    equity: Mapping[date, float],
    cost_paid: Mapping[date, float],
    sessions: Sequence[date] = (T0, T1),
) -> TrialPeriods:
    return TrialPeriods(sessions=sessions, equity=equity, cost_paid=cost_paid)


def _cash_mark(session: date, cash: float, *, run_id: int = 1) -> PositionDailyRow:
    return PositionDailyRow(
        run_id=run_id,
        session=session,
        security_id=None,
        quantity=0.0,
        cash=cash,
        known_at=_utc(session),
        ingested_at=_utc(session),
    )


def _position_mark(
    session: date,
    security_id: str,
    quantity: float,
    price: float,
    *,
    run_id: int = 1,
    tradable: bool | None = True,
    cash: float | None = None,
) -> PositionDailyRow:
    """A held name's row; `cash` is the ledger's cash, which `marks_for`
    carries on every row of a held session (it writes no cash-only row then)."""
    return PositionDailyRow(
        run_id=run_id,
        session=session,
        security_id=security_id,
        quantity=quantity,
        mark_price=price,
        value=quantity * price,
        cash=cash,
        tradable=tradable,
        known_at=_utc(session),
        ingested_at=_utc(session),
    )


def _no_actions() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
            "known_at": pl.Datetime(time_zone="UTC"),
            "cancelled": pl.Boolean,
        }
    )


def _action(
    security_id: str,
    action_type: str,
    ex_date: date,
    ratio_or_amount: float,
    known_at: datetime,
    *,
    cancelled: bool = False,
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [security_id],
            "action_type": [action_type],
            "ex_date": [ex_date],
            "ratio_or_amount": [ratio_or_amount],
            "known_at": [known_at],
            "cancelled": [cancelled],
        },
        schema=_no_actions().schema,
    )


def _actions(*frames: pl.DataFrame) -> pl.DataFrame:
    if not frames:
        return _no_actions()
    return pl.concat(frames)


def _no_price(_security_id: str, _session: date) -> float | None:
    return None


def _prices(closes: Mapping[tuple[str, date], float]) -> PriceOf:
    def price_of(security_id: str, session: date) -> float | None:
        return closes.get((security_id, session))

    return price_of


def _fill(
    client_order_id: str,
    side: str,
    security_id: str,
    filled_at: datetime,
    quantity: float,
    price: float,
    *,
    fill_id: int | None = None,
    superseded_by: int | None = None,
) -> OrderedFill:
    return OrderedFill(
        fill=FillRow(
            fill_id=fill_id if fill_id is not None else next(_IDS),
            client_order_id=client_order_id,
            filled_at=filled_at,
            quantity=quantity,
            price=price,
            price_implied=False,
            broker_fill_id=f"b-{client_order_id}",
            source="broker_feed",
            superseded_by=superseded_by,
            known_at=filled_at,
            ingested_at=filled_at,
        ),
        side=side,
        security_id=security_id,
        symbol=security_id,
        run_id=1,
        window_id=WINDOW_ID,
    )


def _run(run_id: int, day: date) -> PaperRunRow:
    return PaperRunRow(
        run_id=run_id,
        window_id=WINDOW_ID,
        session=day,
        kind="rebalance",
        started_at=_utc(day),
        invoked_by="scheduler",
        code_version="abc",
        known_at=_utc(day),
        ingested_at=_utc(day),
    )


def _journal(
    *,
    positions_daily: Sequence[PositionDailyRow] = (),
    adjustments: Sequence[AdjustmentRow] = (),
    decisions: Sequence[DecisionRow] = (),
    decision_events: Sequence[DecisionEventRow] = (),
    fills: Sequence[OrderedFill] = (),
    orders: Sequence[OrderRow] = (),
    runs: Sequence[PaperRunRow] = (_run(1, T0), _run(2, T1)),
    rebalance_events: Sequence[RebalanceEventRow] = (),
) -> Journal:
    return Journal(
        positions_daily=positions_daily,
        adjustments=adjustments,
        decisions=decisions,
        decision_events=decision_events,
        fills=fills,
        orders=orders,
        runs=runs,
        rebalance_events=rebalance_events,
    )


def _flat_marks(equity: Mapping[date, float]) -> list[PositionDailyRow]:
    return [_cash_mark(session, cash) for session, cash in equity.items()]


def test_journal_residue_cash_exit_uses_absorbed_cash() -> None:
    start, end = date(2026, 10, 26), date(2026, 10, 27)
    name = "0001739445"
    quantity = 3.356124388
    mark = _position_mark(
        start, name, quantity, 149.15, tradable=False, cash=100_000 - quantity * 149.15
    )
    mark = replace(
        mark,
        known_at=datetime(2026, 10, 27, 12, 5, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 27, 12, 5, tzinfo=UTC),
    )
    ending = _cash_mark(end, 100_002.854048)
    ending = replace(
        ending,
        known_at=datetime(2026, 10, 28, 12, 5, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 28, 12, 5, tzinfo=UTC),
    )
    adjustment = AdjustmentRow(
        window_id=WINDOW_ID,
        session=end,
        kind="corporate_action_cash",
        security_id=name,
        quantity=-quantity,
        cash=503.42,
        explanation_json='{"reason":"listing ended"}',
        known_at=datetime(2026, 10, 27, 12, 5, tzinfo=UTC),
        ingested_at=datetime(2026, 10, 27, 12, 5, tzinfo=UTC),
    )
    journal = _journal(
        positions_daily=[mark, ending],
        adjustments=[
            adjustment,
            AdjustmentRow(
                window_id=WINDOW_ID,
                session=start,
                kind="carried_residue",
                origin="dust",
                security_id=name,
                quantity=quantity,
                known_at=_utc(start),
                ingested_at=_utc(start),
            ),
        ],
    )
    result = compare_periods(
        _window(),
        _trial({start: 100_000, end: 100_002.854048}, {start: 0}, (start, end)),
        journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == pytest.approx((503.42 - quantity * 149.15) / 100_000)
    assert result.periods[0].residue_untradable == (name,)


def test_equal_returns_both_rules_pass() -> None:
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(positions_daily=_flat_marks(equity))
    for rule in ("raw", "residual"):
        result = compare_periods(
            _window(tracking_rule=rule), trial, journal, _no_actions(), _no_price, None
        )
        assert result.passed, result.periods
        assert result.periods[0].raw == pytest.approx(0.0)


def test_modelled_cost_uses_trial_equity_not_paper_equity() -> None:
    # Paper's account is 10x smaller than the trial it is tracked against.
    # Spec req 10: "the trial's base-level cost_paid at T_i over its equity
    # at close(T_i)" -- "its" is the trial's, so the threshold must not
    # scale with paper's account size.
    paper_equity = {T0: 10_000.0, T1: 10_100.0}  # paper return = 0.01
    trial_equity = {T0: 100_000.0, T1: 101_000.0}  # trial return = 0.01
    trial = _trial(trial_equity, {T0: 1_000.0})  # modelled cost = 1_000/100_000 = 0.01
    journal = _journal(positions_daily=_flat_marks(paper_equity))
    result = compare_periods(
        _window(tracking_rule="raw", tracking_k=1.0),
        trial,
        journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].modelled_cost == pytest.approx(0.01)
    assert result.periods[0].raw == pytest.approx(0.0)  # both returns are 0.01
    assert result.passed
    # Had the cost instead divided by paper's equity (10_000), the modelled
    # cost would be 0.1, ten times too loose: the check would still pass
    # here, but the printed modelled_cost value pins the right number.


def test_action_identity_matches_store_asof_source_action_id() -> None:
    # A re-dated dividend under one `source_action_id`: the store's own
    # identity (`security_id`, `source_action_id`) must collapse both
    # revisions to the latest-known one, never keep both alive at once
    # (`store.asof._ACTION_IDENTITY_PARTITION`). The original revision is
    # dated INSIDE this month; the later, latest-known revision moves it
    # OUTSIDE this month (a correction): a wrong identity that groups by
    # `(security_id, action_type, ex_date)` instead of `source_action_id`
    # would treat these as two distinct actions and keep the original's
    # in-month dividend alive, double-reporting the term as nonzero instead
    # of the correct zero.
    ex_date = date(2026, 10, 15)  # inside this month (T0, T1]
    record_date = previous_session(ex_date)
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 0.0})
    marks = [*_flat_marks(equity), _position_mark(record_date, A, 100.0, 50.0)]
    original = pl.DataFrame(
        {
            "security_id": [A],
            "action_type": ["dividend"],
            "ex_date": [ex_date],  # originally dated inside this month
            "ratio_or_amount": [0.5],
            "known_at": [_utc(T0)],
            "cancelled": [False],
            "source_action_id": ["div-1"],
        }
    )
    revised = pl.DataFrame(
        {
            "security_id": [A],
            "action_type": ["dividend"],
            "ex_date": [date(2026, 9, 1)],  # corrected to a date outside this month
            "ratio_or_amount": [0.5],
            "known_at": [_utc(T1)],  # the latest-known revision of the same source id
            "cancelled": [False],
            "source_action_id": ["div-1"],
        }
    )
    actions = pl.concat([original, revised])
    journal = _journal(positions_daily=marks)
    result = compare_periods(
        _window(tracking_rule="residual"), trial, journal, actions, _no_price, None
    )
    # The correction moved the dividend out of this month entirely: the
    # correct identity (by source_action_id) leaves nothing to count here.
    # A wrong identity (keeping the original's in-month row alive too) would
    # report 0.5*100/100_000 = 0.0005 instead.
    assert result.periods[0].dividend_term == pytest.approx(0.0)


def test_dividend_credit_known_only_after_month_end_does_not_zero_term() -> None:
    # A `dividend_cash` credit journaled (known) only after close(T_{i+1})
    # was not knowable when month i was reported and must not retroactively
    # zero its dividend term.
    ex_date = date(2026, 10, 15)
    record_date = previous_session(ex_date)
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 0.0})
    marks = [*_flat_marks(equity), _position_mark(record_date, A, 100.0, 50.0)]
    dividends = _action(A, "dividend", ex_date, 0.5, _utc(T1))
    late_credit = _journal(
        positions_daily=marks,
        adjustments=[
            AdjustmentRow(
                adjustment_id=next(_IDS),
                window_id=WINDOW_ID,
                run_id=1,
                session=ex_date,
                kind="dividend_cash",
                security_id=A,
                cash=50.0,
                known_at=session_close(T1) + timedelta(seconds=1),
                ingested_at=session_close(T1) + timedelta(seconds=1),
            )
        ],
    )
    result = compare_periods(
        _window(tracking_rule="residual"), trial, late_credit, dividends, _no_price, None
    )
    expected = (0.5 * 100.0) / 100_000.0
    assert result.periods[0].dividend_term == pytest.approx(expected)


def test_raw_boundary_exact_passes_one_above_fails() -> None:
    # Every number below is a dyadic fraction (an exact power-of-two divisor)
    # so the arithmetic `compare_periods` does is bit-exact, and the "exactly
    # at the threshold" case is not a coin flip of float rounding.
    equity_i = 100_000.0
    cost = {T0: 48.828125}  # modelled cost = 48.828125 / 100_000 = 0.00048828125 (2^-11)
    tracking_k = 2.0  # threshold = 2 * 2^-11 = 2^-10 = 0.0009765625
    threshold = 0.0009765625
    trial_equity = {T0: equity_i, T1: 101_562.5}  # trial return = 0.015625 (1/64)
    trial_return = 0.015625
    target_paper_return = trial_return + threshold  # 0.0166015625
    boundary_next = equity_i * (1 + target_paper_return)  # 101_660.15625

    boundary = _journal(positions_daily=_flat_marks({T0: equity_i, T1: boundary_next}))
    ok = compare_periods(
        _window(tracking_rule="raw", tracking_k=tracking_k),
        _trial(trial_equity, cost),
        boundary,
        _no_actions(),
        _no_price,
        None,
    )
    assert ok.periods[0].raw == threshold  # bit-exact, not merely approx
    assert ok.passed

    # Comfortably over (far beyond any float noise) fails, naming T0.
    over = _journal(positions_daily=_flat_marks({T0: equity_i, T1: boundary_next + 1.0}))
    bad = compare_periods(
        _window(tracking_rule="raw", tracking_k=tracking_k),
        _trial(trial_equity, cost),
        over,
        _no_actions(),
        _no_price,
        None,
    )
    assert not bad.passed
    assert bad.failing_period == T0
    assert not bad.periods[0].passed


def test_dividend_term_and_dividend_cash_zero() -> None:
    ex_date = date(2026, 10, 15)
    record_date = previous_session(ex_date)
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 0.0})
    # Entitlement is held as of the session before the ex-date (the record
    # date), not the ex-date's own close (`_dividends` in reconcile_run.py).
    marks = [
        _position_mark(T0, A, 100.0, 50.0, cash=95_000.0),
        _cash_mark(T1, equity[T1]),
        _position_mark(record_date, A, 100.0, 50.0),
    ]
    dividends = _action(A, "dividend", ex_date, 0.5, _utc(T1))  # amount 0.5/share

    with_dividend = _journal(positions_daily=marks)
    result = compare_periods(
        _window(tracking_rule="residual"),
        trial,
        with_dividend,
        dividends,
        _no_price,
        None,
    )
    expected_term = (0.5 * 100.0) / 100_000.0
    month = result.periods[0]
    assert month.dividend_term == pytest.approx(expected_term)
    # raw is negative (paper ahead by less than expected, forcing the base case below);
    # build a case where raw alone fails but the dividend term rescues the residual.
    raw_fail_equity = {T0: 100_000.0, T1: 100_000.0}  # paper flat, trial up 1%: raw = -0.01
    raw_fail_trial = _trial({T0: 100_000.0, T1: 101_000.0}, {T0: 1.0})
    raw_fail_journal = _journal(
        positions_daily=[
            *_flat_marks(raw_fail_equity),
            _position_mark(record_date, A, 100.0, 50.0),
        ]
    )
    raw_result = compare_periods(
        _window(tracking_rule="raw"),
        raw_fail_trial,
        raw_fail_journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert not raw_result.passed  # raw alone, tracking_k=2, cost=0 -> any nonzero raw fails
    residual_result = compare_periods(
        _window(tracking_rule="residual"),
        raw_fail_trial,
        raw_fail_journal,
        dividends,
        _no_price,
        None,
    )
    # dividend term = 0.5*100/100_000 = 0.0005, nowhere near rescuing -0.01, so use a
    # dividend sized to exactly offset raw: amount such that dividend_term == 0.01.
    big_dividend = _action(A, "dividend", ex_date, 10.0, _utc(T1))  # 10*100/100_000 = 0.01
    rescued = compare_periods(
        _window(tracking_rule="residual"),
        raw_fail_trial,
        raw_fail_journal,
        big_dividend,
        _no_price,
        None,
    )
    assert rescued.periods[0].residual == pytest.approx(0.0)
    assert rescued.passed
    assert not residual_result.passed  # sanity: the small dividend does not rescue it

    # The same dividend, but the broker already credited it: contributes zero,
    # whether journaled on the ex-date itself...
    credited = _journal(
        positions_daily=marks,
        adjustments=[
            AdjustmentRow(
                adjustment_id=next(_IDS),
                window_id=WINDOW_ID,
                run_id=1,
                session=ex_date,
                kind="dividend_cash",
                security_id=A,
                cash=50.0,
                known_at=_utc(ex_date),
                ingested_at=_utc(ex_date),
            )
        ],
    )
    credited_result = compare_periods(
        _window(tracking_rule="residual"), trial, credited, dividends, _no_price, None
    )
    assert credited_result.periods[0].dividend_term == pytest.approx(0.0)

    # ...or, as `reconcile_run._dividends` actually stamps it, several
    # sessions later at the pay date: a session-equality match would miss
    # this and double-count the dividend (the bug this case pins).
    pay_date = ex_date + timedelta(days=5)
    credited_lagged = _journal(
        positions_daily=marks,
        adjustments=[
            AdjustmentRow(
                adjustment_id=next(_IDS),
                window_id=WINDOW_ID,
                run_id=1,
                session=pay_date,
                kind="dividend_cash",
                security_id=A,
                cash=50.0,
                known_at=_utc(pay_date),
                ingested_at=_utc(pay_date),
            )
        ],
    )
    credited_lagged_result = compare_periods(
        _window(tracking_rule="residual"),
        trial,
        credited_lagged,
        dividends,
        _no_price,
        None,
    )
    assert credited_lagged_result.periods[0].dividend_term == pytest.approx(0.0)


def test_fill_timing_term_positive_for_costly_buy_and_sell() -> None:
    fill_day = date(2026, 10, 15)
    close = 100.0
    closes = _prices({(A, fill_day): close})
    equity = {T0: 100_000.0, T1: 100_000.0}
    trial = _trial(equity, {T0: 0.0})

    # A buy filled above the close: positive term.
    buy = _journal(
        positions_daily=_flat_marks(equity),
        fills=[_fill("buy-1", "buy", A, _utc(fill_day), 10.0, close + 1.0)],
    )
    buy_result = compare_periods(
        _window(tracking_rule="raw"), trial, buy, _no_actions(), closes, None
    )
    expected_buy = (10.0 * 1.0) / 100_000.0
    assert buy_result.periods[0].fill_timing_term == pytest.approx(expected_buy)
    assert buy_result.periods[0].fill_timing_term > 0

    # A sell filled below the close: also positive.
    sell = _journal(
        positions_daily=_flat_marks(equity),
        fills=[_fill("sell-1", "sell", A, _utc(fill_day), 10.0, close - 1.0)],
    )
    sell_result = compare_periods(
        _window(tracking_rule="raw"), trial, sell, _no_actions(), closes, None
    )
    expected_sell = (10.0 * 1.0) / 100_000.0
    assert sell_result.periods[0].fill_timing_term == pytest.approx(expected_sell)
    assert sell_result.periods[0].fill_timing_term > 0

    # Residual equals the hand-computed value (raw=0, dividend=0, fill_timing=expected).
    assert sell_result.periods[0].residual == pytest.approx(expected_sell)


def _residue_case(
    q_i: float,
    price_i: float,
    q_next: float,
    price_next: float,
    *,
    cash_flow: float = 0.0,
    kind: str = "corporate_action_cash",
    quantity_flow: float | None = None,
    tradable: bool | None = True,
    residue_quantity: float | None = None,
) -> float:
    cash_i = 100_000 - q_i * price_i
    cash_next = cash_i + cash_flow
    marks = [
        _position_mark(T0, A, q_i, price_i, cash=cash_i, tradable=tradable),
        _position_mark(T1, A, q_next, price_next, cash=cash_next, tradable=tradable),
    ]
    adjustments = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=residue_quantity if residue_quantity is not None else q_i,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        )
    ]
    if cash_flow or quantity_flow:
        adjustments.append(
            AdjustmentRow(
                window_id=WINDOW_ID,
                session=T1,
                kind=kind,
                security_id=A,
                quantity=quantity_flow,
                cash=cash_flow,
                known_at=_utc(T1),
                ingested_at=_utc(T1),
            )
        )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: cash_next + q_next * price_next}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=adjustments),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_untradable == ((A,) if tradable is not True else ())
    return result.periods[0].residue_term


def test_residue_split_and_partial_holding_read_marks() -> None:
    assert _residue_case(10, 50, 20, 27.5) == pytest.approx(50 / 100_000)
    assert _residue_case(10.3, 20, 10.3, 21, residue_quantity=0.3) == pytest.approx(0.3 / 100_000)
    assert _residue_case(
        7, 10, 3, 21, cash_flow=10.5, kind="cash_in_lieu", quantity_flow=-0.5
    ) == pytest.approx(3.5 / 100_000)


def test_residue_dividend_credit_does_not_double_count() -> None:
    assert _residue_case(10, 20, 10, 19.5, cash_flow=5, kind="dividend_cash") == pytest.approx(0)
    assert _residue_case(10, 20, 10, 19.5) == pytest.approx(-5 / 100_000)


def test_residue_untradable_null_flag() -> None:
    assert _residue_case(10, 40, 10, 40, tradable=None) == pytest.approx(0)


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("carried_residue", "carried_residue inside period"),
        ("unknown_cash", "unclassified unknown_cash"),
    ],
)
def test_residue_refuses_unhandled_period_row(kind: str, message: str) -> None:
    marks = [
        _position_mark(T0, A, 10, 20, cash=99_800),
        _position_mark(T1, A, 10, 20, cash=99_800),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=10,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind=kind,
            security_id=A,
            quantity=1,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    journal = _journal(positions_daily=marks, adjustments=rows)
    with pytest.raises(ValueError, match=message):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
            journal,
            _no_actions(),
            _no_price,
            None,
        )


def test_residue_live_fill_values_opening_holding() -> None:
    marks = [
        _position_mark(T0, A, 10.3, 20, cash=99_794),
        _position_mark(T1, A, 15.3, 21, cash=99_694),
    ]
    carried = AdjustmentRow(
        adjustment_id=next(_IDS),
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=0.3,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )
    journal = _journal(
        positions_daily=marks,
        adjustments=[carried],
        fills=[
            _fill("opening", "buy", A, _utc(T0), 10, 20),
            _fill("fill", "buy", A, _utc(T1), 5, 20),
        ],
        orders=[
            OrderRow(
                client_order_id="fill",
                decision_id=1,
                run_id=1,
                session=T1,
                attempt=1,
                phase="buy",
                security_id=A,
                symbol=A,
                side="buy",
                quantity=5,
                sells_in_flight_at_submit=False,
                known_at=_utc(T1),
                ingested_at=_utc(T1),
            ),
            OrderRow(
                client_order_id="opening",
                decision_id=1,
                run_id=1,
                session=T0,
                attempt=1,
                phase="buy",
                security_id=A,
                symbol=A,
                side="buy",
                quantity=10,
                sells_in_flight_at_submit=False,
                known_at=_utc(T0),
                ingested_at=_utc(T0),
            ),
        ],
    )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
        journal,
        _no_actions(),
        _prices({(A, T0): 20, (A, T1): 20}),
        None,
    )
    assert result.periods[0].residue_term == pytest.approx(0.3 / 100_000)


def test_late_credit_enters_first_mark_that_absorbs_it() -> None:
    marks = [
        _position_mark(T0, A, 10, 20, cash=99_800, run_id=1),
        _position_mark(previous_session(T1), A, 10, 20, cash=99_800, run_id=2),
        _position_mark(T1, A, 10, 20, cash=99_800, run_id=2),
        _position_mark(T2, A, 10, 20, cash=99_805, run_id=3),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=10,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="dividend_cash",
            security_id=A,
            cash=5,
            known_at=_utc(T2),
            ingested_at=_utc(T2),
        ),
    ]
    journal = _journal(
        positions_daily=marks, adjustments=rows, runs=(_run(1, T0), _run(2, T1), _run(3, T2))
    )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000, T2: 100_005}, {T0: 0, T1: 0}, (T0, T1, T2)),
        journal,
        _action(A, "dividend", T1, 0.5, _utc(T1)),
        _no_price,
        None,
    )
    assert [m.residue_term for m in result.periods] == pytest.approx([0, 5 / 100_000])
    assert [m.dividend_term for m in result.periods] == pytest.approx([5 / 100_000, 0])


def test_later_dust_decision_cannot_create_past_residue() -> None:
    marks = [
        _position_mark(T0, A, 10, 20, cash=99_800, run_id=1),
        _position_mark(T1, A, 10, 21, cash=99_800, run_id=2),
    ]
    decision_id = next(_IDS)
    decision = DecisionRow(
        decision_id=decision_id,
        run_id=2,
        rebalance_session=None,
        security_id=A,
        whole_share=False,
        decision="forced_exit",
        reason="window_stop",
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    event = DecisionEventRow(
        decision_id=decision_id,
        run_id=2,
        status="skipped",
        reason="dust",
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    journal = _journal(positions_daily=marks, decisions=[decision], decision_events=[event])
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_010}, {T0: 0}),
        journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == 0


def test_split_action_does_not_change_mark_based_residue() -> None:
    cash = 99_500.0
    marks = [_position_mark(T0, A, 10, 50, cash=cash), _position_mark(T1, A, 20, 27.5, cash=cash)]
    carried = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=10,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )
    journal = _journal(positions_daily=marks, adjustments=[carried])
    trial = _trial({T0: 100_000, T1: 100_050}, {T0: 0})
    for actions in (_no_actions(), _action(A, "split", date(2026, 10, 15), 2.0, _utc(T1))):
        result = compare_periods(_window(), trial, journal, actions, _no_price, None)
        assert result.periods[0].residue_term == pytest.approx(50 / 100_000)


def test_whole_residue_identity_over_split_dividend_and_cash_exit() -> None:
    t3 = date(2026, 12, 31)
    marks = [
        _position_mark(T0, A, 10, 50, cash=99_500, run_id=1),
        _position_mark(T1, A, 20, 27.5, cash=99_510, run_id=2),
        _position_mark(T2, A, 20, 28, cash=99_510, run_id=3),
        _cash_mark(t3, 100_080, run_id=4),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=10,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="dividend_cash",
            security_id=A,
            cash=10,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=t3,
            kind="corporate_action_cash",
            security_id=A,
            quantity=-20,
            cash=570,
            known_at=_utc(t3),
            ingested_at=_utc(t3),
        ),
    ]
    trial = _trial(
        {T0: 100_000, T1: 100_060, T2: 100_070, t3: 100_080},
        {T0: 0, T1: 0, T2: 0},
        (T0, T1, T2, t3),
    )
    journal = _journal(
        positions_daily=marks,
        adjustments=rows,
        runs=(_run(1, T0), _run(2, T1), _run(3, T2), _run(4, t3)),
    )
    actions = _action(A, "split", date(2026, 10, 15), 2.0, _utc(T1))
    result = compare_periods(_window(), trial, journal, actions, _no_price, None)
    for period in result.periods:
        paper_return = (
            trial.equity[period.next_session] / trial.equity[period.rebalance_session] - 1
        )
        assert period.residue_term == pytest.approx(paper_return)


def test_residue_missing_exit_refuses_but_other_short_is_skipped() -> None:
    marks = [_position_mark(T0, A, 10, 20, cash=99_800), _cash_mark(T1, 100_000)]
    carried = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=10,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )
    journal = _journal(positions_daily=marks, adjustments=[carried])
    trial = _trial({T0: 100_000, T1: 100_000}, {T0: 0})
    with pytest.raises(ValueError, match=f"{A}.*{T0}.*{T1}.*unexplained"):
        compare_periods(_window(), trial, journal, _no_actions(), _no_price, None)
    short = replace(_position_mark(T0, B, -2, 10, cash=100_020), position_side="short")
    short_next = replace(_position_mark(T1, B, -2, 10, cash=100_020), position_side="short")
    journal = _journal(positions_daily=[short, short_next])
    result = compare_periods(_window(), trial, journal, _no_actions(), _no_price, None)
    assert result.periods[0].residue_term == 0


def test_adjustment_known_after_first_close_cannot_create_first_residue() -> None:
    later = _utc(T2)
    marks = [_position_mark(T0, A, 10, 20, cash=99_800), _position_mark(T1, A, 10, 21, cash=99_800)]
    credit = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=10,
        known_at=later,
        ingested_at=later,
    )
    journal = _journal(positions_daily=marks, adjustments=[credit])
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_010}, {T0: 0}),
        journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == 0
    assert result.periods[0].residue_untradable == ()


@pytest.mark.parametrize("kind", ["merger_receipt", "spinoff_receipt"])
def test_receipt_on_receiving_name_values_source(kind: str) -> None:
    marks = [_position_mark(T0, A, 5, 20, cash=99_900), _position_mark(T1, B, 2, 60, cash=99_900)]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=5,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="corporate_action_cash",
            security_id=A,
            quantity=-5,
            cash=0,
            explanation_json='{"merger_id":"m1"}',
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind=kind,
            security_id=B,
            quantity=2,
            explanation_json='{"merger_id":"m1"}',
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    journal = _journal(positions_daily=marks, adjustments=rows)
    actions = _no_actions()
    if kind == "spinoff_receipt":
        actions = pl.DataFrame(
            {
                "security_id": [B],
                "source_action_id": [A],
                "action_type": ["spinoff"],
                "ex_date": [T1],
                "ratio_or_amount": [0.4],
                "known_at": [_utc(T1)],
                "cancelled": [False],
            }
        )
    result = compare_periods(
        _window(), _trial({T0: 100_000, T1: 100_020}, {T0: 0}), journal, actions, _no_price, None
    )
    assert result.periods[0].residue_term == pytest.approx(20 / 100_000)


def test_receipt_without_merger_source_refuses() -> None:
    marks = [_position_mark(T0, A, 5, 20, cash=99_900), _position_mark(T1, B, 2, 60, cash=99_900)]
    receipt = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T1,
        kind="merger_receipt",
        security_id=B,
        quantity=2,
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    with pytest.raises(ValueError, match="no merger source"):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_020}, {T0: 0}),
            _journal(positions_daily=marks, adjustments=[receipt]),
            _no_actions(),
            _no_price,
            None,
        )


@pytest.mark.parametrize("missing", ["action", "child_mark"])
def test_spinoff_receipt_requires_live_action_and_child_mark(missing: str) -> None:
    marks = [_position_mark(T0, A, 5, 20, cash=99_900), _position_mark(T1, A, 5, 19, cash=99_900)]
    if missing != "child_mark":
        marks.append(_position_mark(T1, B, 2, 10, cash=99_900))
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=5,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="spinoff_receipt",
            security_id=B,
            quantity=2,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    actions = (
        _no_actions()
        if missing == "action"
        else pl.DataFrame(
            {
                "security_id": [B],
                "source_action_id": [A],
                "action_type": ["spinoff"],
                "ex_date": [T1],
                "ratio_or_amount": [0.4],
                "known_at": [_utc(T1)],
                "cancelled": [False],
            }
        )
    )
    with pytest.raises(
        ValueError, match=("spinoff action" if missing == "action" else "mark_price")
    ):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_015}, {T0: 0}),
            _journal(positions_daily=marks, adjustments=rows),
            actions,
            _no_price,
            None,
        )


def test_spinoff_parent_fill_with_receipt_refuses() -> None:
    marks = [
        _position_mark(T0, A, 5, 20, cash=99_900),
        _position_mark(T1, A, 6, 19, cash=99_880),
        _position_mark(T1, B, 2, 10, cash=99_880),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=5,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="spinoff_receipt",
            security_id=B,
            quantity=2,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    action = pl.DataFrame(
        {
            "security_id": [B],
            "source_action_id": [A],
            "action_type": ["spinoff"],
            "ex_date": [T1],
            "ratio_or_amount": [0.4],
            "known_at": [_utc(T1)],
            "cancelled": [False],
        }
    )
    with pytest.raises(ValueError, match="fill with quantity-bearing period row"):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_014}, {T0: 0}),
            _journal(
                positions_daily=marks,
                adjustments=rows,
                fills=[_fill("buy", "buy", A, _utc(T1), 1, 20)],
            ),
            action,
            _prices({(A, T1): 20}),
            None,
        )


def test_merger_cash_and_later_payment_use_journal_value() -> None:
    carried = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=5,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )
    removal = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T1,
        kind="corporate_action_cash",
        security_id=A,
        quantity=-5,
        cash=110,
        explanation_json='{"merger_id":"m1"}',
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    later = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T2,
        kind="corporate_action_cash",
        security_id=A,
        cash=10,
        explanation_json='{"merger_id":"m1"}',
        known_at=_utc(T2),
        ingested_at=_utc(T2),
    )
    journal = _journal(
        positions_daily=[
            _position_mark(T0, A, 5, 20, cash=99_900),
            _cash_mark(T1, 100_010),
            _cash_mark(T2, 100_020),
        ],
        adjustments=[carried, removal, later],
    )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000, T2: 100_000}, {T0: 0, T1: 0}, (T0, T1, T2)),
        journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert [p.residue_term for p in result.periods] == pytest.approx([10 / 100_000, 10 / 100_010])


def test_late_cash_uses_rebalance_exit_start_and_mark_cut() -> None:
    before_exit = previous_session(T1)
    marks = [
        _position_mark(T0, A, 10.3, 20, cash=99_794),
        _position_mark(before_exit, A, 5.3, 20, cash=99_894),
        _cash_mark(T1, 99_894),
        _cash_mark(T2, 99_994),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=0.3,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="corporate_action_cash",
            security_id=A,
            quantity=-5.3,
            cash=0,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T2,
            kind="corporate_action_cash",
            security_id=A,
            cash=100,
            explanation_json='{"delisting_cash":true}',
            known_at=_utc(T2),
            ingested_at=_utc(T2),
        ),
    ]
    trial = _trial({T0: 100_000, T1: 100_000, T2: 100_000}, {T0: 0, T1: 0}, (T0, T1, T2))
    result = compare_periods(
        _window(),
        trial,
        _journal(positions_daily=marks, adjustments=rows),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[1].residue_term == pytest.approx((100 * 0.3 / 10.3) / 99_894)
    late = replace(rows[-1], known_at=_utc(T2) + timedelta(days=2))
    result_late = compare_periods(
        _window(),
        trial,
        _journal(positions_daily=marks, adjustments=[*rows[:-1], late]),
        _no_actions(),
        _no_price,
        None,
    )
    assert result_late.periods[1].residue_term == 0


def test_unheld_dividend_credit_without_residue_is_ignored() -> None:
    marks = [_cash_mark(T0, 100_000), _cash_mark(T1, 100_005)]
    row = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T1,
        kind="dividend_cash",
        security_id=A,
        cash=5,
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=[row]),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == 0


def test_late_dividend_credit_after_residue_exit_uses_entitlement() -> None:
    prior = previous_session(T1)
    marks = [
        _position_mark(T0, A, 10.3, 20, cash=99_794),
        _position_mark(prior, A, 10.3, 20, cash=99_794),
        _cash_mark(T1, 99_794),
        _cash_mark(T2, 99_799.15),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=0.3,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="corporate_action_cash",
            security_id=A,
            quantity=-10.3,
            cash=0,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T2,
            kind="dividend_cash",
            security_id=A,
            cash=5.15,
            known_at=_utc(T2),
            ingested_at=_utc(T2),
        ),
    ]
    window = _window()
    frozen = json.loads(window.frozen_json)
    frozen["risk.reconcile_dividend_credit_sessions"] = 30
    window = replace(window, frozen_json=json.dumps(frozen))
    result = compare_periods(
        window,
        _trial({T0: 100_000, T1: 100_000, T2: 100_000}, {T0: 0, T1: 0}, (T0, T1, T2)),
        _journal(positions_daily=marks, adjustments=rows),
        _action(A, "dividend", T1, 0.5, _utc(T1)),
        _no_price,
        None,
    )
    assert result.periods[1].residue_term == pytest.approx(0.15 / 99_794)


def test_late_delisting_cash_without_exit_period_has_zero_residue() -> None:
    marks = [_cash_mark(T0, 100_000), _cash_mark(T1, 100_100)]
    row = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T1,
        kind="corporate_action_cash",
        security_id=A,
        cash=100,
        explanation_json='{"delisting_cash":true}',
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=[row]),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == 0


def test_merger_receipt_with_two_sources_refuses() -> None:
    marks = [
        _position_mark(T0, A, 5, 20, cash=99_900),
        _position_mark(T0, "OTHER", 1, 10, cash=99_900),
        _position_mark(T1, B, 2, 60, cash=99_900),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="corporate_action_cash",
            security_id=name,
            quantity=-1,
            explanation_json='{"merger_id":"m1"}',
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        )
        for name in (A, "OTHER")
    ]
    rows.append(
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="merger_receipt",
            security_id=B,
            quantity=2,
            explanation_json='{"merger_id":"m1"}',
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        )
    )
    with pytest.raises(ValueError, match="multiple merger source"):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_020}, {T0: 0}),
            _journal(positions_daily=marks, adjustments=rows),
            _no_actions(),
            _no_price,
            None,
        )


def test_received_merger_shares_are_excluded_from_acquirer_residue() -> None:
    marks = [
        _position_mark(T0, A, 5, 20, cash=99_850),
        _position_mark(T0, B, 1, 50, cash=99_850),
        _position_mark(T1, B, 3, 60, cash=99_850),
    ]
    carried = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=name,
            quantity=quantity,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        )
        for name, quantity in ((A, 5), (B, 1))
    ]
    rows = [
        *carried,
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="corporate_action_cash",
            security_id=A,
            quantity=-5,
            cash=0,
            explanation_json='{"merger_id":"m1"}',
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="merger_receipt",
            security_id=B,
            quantity=2,
            explanation_json='{"merger_id":"m1"}',
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_030}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=rows),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == pytest.approx(30 / 100_000)


def test_fill_with_quantity_cash_in_lieu_refuses() -> None:
    marks = [
        _position_mark(T0, A, 10.3, 20, cash=99_794),
        _position_mark(T1, A, 15, 21, cash=99_685),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=0.3,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="cash_in_lieu",
            security_id=A,
            quantity=-0.3,
            cash=6.3,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    with pytest.raises(ValueError, match="fill with quantity-bearing period row"):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
            _journal(
                positions_daily=marks,
                adjustments=rows,
                fills=[_fill("buy", "buy", A, _utc(T1), 5, 20)],
            ),
            _no_actions(),
            _prices({(A, T1): 20}),
            None,
        )


def test_dividend_cash_with_fill_uses_pre_ex_date_holding() -> None:
    prior = previous_session(T1)
    marks = [
        _position_mark(T0, A, 10.3, 20, cash=99_794),
        _position_mark(prior, A, 5.3, 20, cash=99_894, run_id=3),
        _position_mark(T1, A, 10.3, 19.5, cash=99_799.15),
    ]
    rows = [
        AdjustmentRow(
            adjustment_id=next(_IDS),
            window_id=WINDOW_ID,
            session=date(2026, 6, 30),
            kind="dividend_cash",
            security_id=A,
            cash=5,
            known_at=_utc(date(2026, 6, 30)),
            ingested_at=_utc(date(2026, 6, 30)),
        ),
        AdjustmentRow(
            adjustment_id=next(_IDS),
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=0.3,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            adjustment_id=next(_IDS),
            window_id=WINDOW_ID,
            session=T1,
            kind="dividend_cash",
            security_id=A,
            cash=2.65,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    fills = [
        _fill("opening", "buy", A, _utc(T0), 10, 20),
        _fill("sale", "sell", A, _utc(prior), 5, 20),
        _fill("later", "buy", A, _utc(T1), 5, 20),
    ]
    orders = [
        OrderRow(
            client_order_id=label,
            decision_id=1,
            run_id=1,
            session=day,
            attempt=1,
            phase=side,
            security_id=A,
            symbol=A,
            side=side,
            quantity=quantity,
            sells_in_flight_at_submit=False,
            known_at=_utc(day),
            ingested_at=_utc(day),
        )
        for label, day, quantity, side in (
            ("opening", T0, 10, "buy"),
            ("sale", prior, 5, "sell"),
            ("later", T1, 5, "buy"),
        )
    ]
    actions = _action(A, "dividend", T1, 0.5, _utc(T1))
    decision_id = next(_IDS)
    decision = DecisionRow(
        decision_id=decision_id,
        run_id=3,
        security_id=A,
        whole_share=False,
        decision="forced_exit",
        reason="window_stop",
        known_at=_utc(prior),
        ingested_at=_utc(prior),
    )
    event = DecisionEventRow(
        decision_id=decision_id,
        run_id=3,
        status="skipped",
        reason="dust",
        known_at=_utc(prior),
        ingested_at=_utc(prior),
    )
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
        _journal(
            positions_daily=marks,
            adjustments=rows,
            fills=fills,
            orders=orders,
            decisions=[decision],
            decision_events=[event],
            runs=(_run(1, T0), _run(2, T1), _run(3, prior)),
        ),
        actions,
        _prices({(A, T0): 20, (A, prior): 20, (A, T1): 20}),
        None,
    )
    assert result.periods[0].residue_term == pytest.approx(0)


@pytest.mark.parametrize("kind", ["owner_true_up", "auto_true_up"])
def test_residue_true_up_is_listed(kind: str) -> None:
    marks = [_position_mark(T0, A, 5, 20, cash=99_900), _position_mark(T1, A, 6, 20, cash=99_900)]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=5,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind=kind,
            security_id=A,
            quantity=1,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
    ]
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_020}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=rows),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == pytest.approx(20 / 100_000)
    assert result.periods[0].residue_trued_up == (A,)


def test_residue_quantity_uses_close_cut_even_when_mark_is_written_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mark_time = datetime(2026, 10, 1, 12, 5, tzinfo=UTC)
    late_time = session_close(T0) + timedelta(seconds=1)
    marks = [
        replace(
            _position_mark(T0, A, 10, 20, cash=99_800), known_at=mark_time, ingested_at=mark_time
        ),
        _position_mark(T1, A, 10, 21, cash=99_800),
    ]
    late = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=10,
        known_at=late_time,
        ingested_at=late_time,
    )
    original = report_module.residue_of
    received: list[Sequence[AdjustmentRow]] = []

    def record(*args: object, **kwargs: object) -> float:
        received.append(args[1])  # type: ignore[arg-type]
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "residue_of", record)
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_010}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=[late]),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == 0
    assert received and all(late not in rows for rows in received)


def test_short_mark_on_residue_name_refuses() -> None:
    mark_i = _position_mark(T0, A, 10, 20, cash=99_800)
    mark_next = replace(_position_mark(T1, A, -10, 20, cash=100_200), position_side="short")
    carried = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=10,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )
    journal = _journal(positions_daily=[mark_i, mark_next], adjustments=[carried])
    with pytest.raises(ValueError, match=f"{A}.*{T0}.*{T1}.*short residue"):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
            journal,
            _no_actions(),
            _no_price,
            None,
        )


def test_short_opening_mark_on_carried_residue_refuses() -> None:
    marks = [
        replace(_position_mark(T0, A, -10, 20, cash=100_200), position_side="short"),
        replace(_position_mark(T1, A, -10, 20, cash=100_200), position_side="short"),
    ]
    carried = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T0,
        kind="carried_residue",
        origin="dust",
        security_id=A,
        quantity=10,
        known_at=_utc(T0),
        ingested_at=_utc(T0),
    )
    with pytest.raises(ValueError, match=f"{A}.*{T0}.*{T1}.*short residue"):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
            _journal(positions_daily=marks, adjustments=[carried]),
            _no_actions(),
            _no_price,
            None,
        )


def test_non_residue_credit_and_true_up_do_not_refuse() -> None:
    marks = [_position_mark(T0, A, 10, 20, cash=99_800), _position_mark(T1, A, 10, 20, cash=99_800)]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind=kind,
            security_id=A,
            cash=0,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        )
        for kind in ("dividend_cash", "owner_true_up")
    ]
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
        _journal(positions_daily=marks, adjustments=rows),
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].residue_term == 0


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("carried_residue", "carried_residue inside period"),
        ("unknown_cash", "unclassified unknown_cash"),
    ],
)
def test_unclassified_row_refuses_even_without_residue(kind: str, message: str) -> None:
    marks = [_position_mark(T0, A, 10, 20, cash=99_800), _position_mark(T1, A, 10, 20, cash=99_800)]
    row = AdjustmentRow(
        window_id=WINDOW_ID,
        session=T1,
        kind=kind,
        security_id=A,
        quantity=1,
        known_at=_utc(T1),
        ingested_at=_utc(T1),
    )
    with pytest.raises(ValueError, match=message):
        compare_periods(
            _window(),
            _trial({T0: 100_000, T1: 100_000}, {T0: 0}),
            _journal(positions_daily=marks, adjustments=[row]),
            _no_actions(),
            _no_price,
            None,
        )


def test_late_cash_on_exited_residue_uses_exit_fraction() -> None:
    marks = [
        _position_mark(T0, A, 10, 20, cash=99_800),
        _cash_mark(T1, 99_800),
        _cash_mark(T2, 99_900),
    ]
    rows = [
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=10,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T1,
            kind="corporate_action_cash",
            security_id=A,
            quantity=-10,
            cash=0,
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        ),
        AdjustmentRow(
            window_id=WINDOW_ID,
            session=T2,
            kind="corporate_action_cash",
            security_id=A,
            cash=100,
            explanation_json='{"delisting_cash":true}',
            known_at=_utc(T2),
            ingested_at=_utc(T2),
        ),
    ]
    result = compare_periods(
        _window(),
        _trial({T0: 100_000, T1: 100_000, T2: 100_000}, {T0: 0, T1: 0}, (T0, T1, T2)),
        _journal(positions_daily=marks, adjustments=rows),
        _no_actions(),
        _no_price,
        None,
    )
    assert [p.residue_term for p in result.periods] == pytest.approx([-200 / 100_000, 100 / 99_800])


def test_raw_rule_excludes_missed_lists_override() -> None:
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    # A large raw difference that would otherwise fail.
    journal = _journal(
        positions_daily=_flat_marks({T0: 100_000.0, T1: 110_000.0}),
        rebalance_events=[
            RebalanceEventRow(
                rebalance_session=T0,
                run_id=1,
                status="missed",
                reason="catch_up_lapsed",
                known_at=_utc(T0),
                ingested_at=_utc(T0),
            )
        ],
    )
    result = compare_periods(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, None
    )
    assert result.passed  # excluded from the check
    assert result.periods[0].missed
    assert result.periods[0].excluded

    # An override month: listed, but NOT excluded under raw.
    override_journal = _journal(
        positions_daily=_flat_marks({T0: 100_000.0, T1: 110_000.0}),
        decisions=[
            DecisionRow(
                decision_id=next(_IDS),
                run_id=1,
                rebalance_session=T0,
                security_id=A,
                whole_share=False,
                decision="override",
                known_at=_utc(T0),
                ingested_at=_utc(T0),
            )
        ],
    )
    override_result = compare_periods(
        _window(tracking_rule="raw"),
        trial,
        override_journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert override_result.periods[0].override
    assert not override_result.periods[0].excluded
    assert not override_result.passed  # the large raw diff still fails the check


def test_residual_rule_excludes_missed_and_override() -> None:
    equity = {T0: 100_000.0, T1: 110_000.0}
    trial = _trial({T0: 100_000.0, T1: 101_000.0}, {T0: 100.0})
    override_journal = _journal(
        positions_daily=_flat_marks(equity),
        decisions=[
            DecisionRow(
                decision_id=next(_IDS),
                run_id=1,
                rebalance_session=T0,
                security_id=A,
                whole_share=False,
                decision="override",
                known_at=_utc(T0),
                ingested_at=_utc(T0),
            )
        ],
    )
    result = compare_periods(
        _window(tracking_rule="residual"),
        trial,
        override_journal,
        _no_actions(),
        _no_price,
        None,
    )
    assert result.periods[0].override
    assert result.periods[0].excluded
    assert result.passed


def test_skip_decision_listed_not_excluded() -> None:
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(
        positions_daily=_flat_marks(equity),
        decisions=[
            DecisionRow(
                decision_id=next(_IDS),
                run_id=1,
                rebalance_session=T0,
                security_id=B,
                whole_share=False,
                decision="skip_below_minimum",
                known_at=_utc(T0),
                ingested_at=_utc(T0),
            )
        ],
    )
    result = compare_periods(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, None
    )
    assert result.periods[0].skip_names == (B,)
    assert not result.periods[0].excluded
    assert result.passed


def test_frozen_rule_not_live_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live Settings would say "residual"; the window freezes "raw".
    monkeypatch.setenv("PAPER__TRACKING_RULE", "residual")
    live = Settings(_env_file=None)
    assert live.paper.tracking_rule == "residual"
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(positions_daily=_flat_marks(equity))
    result = compare_periods(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, None
    )
    assert result.tracking_rule == "raw"  # the window's frozen value, not the live default


def test_superseded_fill_counted_once() -> None:
    fill_day = date(2026, 10, 15)
    close = 100.0
    closes = _prices({(A, fill_day): close})
    equity = {T0: 100_000.0, T1: 100_000.0}
    trial = _trial(equity, {T0: 0.0})
    superseded = _fill("buy-1", "buy", A, _utc(fill_day), 10.0, close + 5.0, fill_id=1)
    replacement = _fill("buy-1", "buy", A, _utc(fill_day), 10.0, close + 1.0, fill_id=2)
    superseded_marked = replace(superseded, fill=replace(superseded.fill, superseded_by=2))
    journal = _journal(positions_daily=_flat_marks(equity), fills=[superseded_marked, replacement])
    result = compare_periods(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), closes, None
    )
    expected = (10.0 * 1.0) / 100_000.0
    assert result.periods[0].fill_timing_term == pytest.approx(expected)


def test_no_look_ahead_late_dividend_not_counted() -> None:
    ex_date = date(2026, 10, 15)
    record_date = previous_session(ex_date)
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 0.0})
    marks = [*_flat_marks(equity), _position_mark(record_date, A, 100.0, 50.0)]
    # Known only AFTER close(T1): must not affect month 0's dividend term.
    late = _action(A, "dividend", ex_date, 5.0, session_close(T1) + timedelta(seconds=1))
    journal = _journal(positions_daily=marks)
    result = compare_periods(_window(tracking_rule="raw"), trial, journal, late, _no_price, None)
    assert result.periods[0].dividend_term == pytest.approx(0.0)

    # The same dividend, known just in time (at close(T1)): counted.
    on_time = _action(A, "dividend", ex_date, 5.0, session_close(T1))
    on_time_result = compare_periods(
        _window(tracking_rule="raw"), trial, journal, on_time, _no_price, None
    )
    assert on_time_result.periods[0].dividend_term == pytest.approx((5.0 * 100.0) / 100_000.0)


def test_month_uncompared_at_or_after_stop_session() -> None:
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(positions_daily=_flat_marks(equity))
    result = compare_periods(
        _window(tracking_rule="raw"),
        trial,
        journal,
        _no_actions(),
        _no_price,
        # A stop requested after close(T1) still falls in T1 (plan.stop_session).
        stop_session=stop_session(_utc(T1, hour=22)),
    )
    assert result.periods == ()
    assert result.passed  # vacuously, nothing to compare


def _stop_row(at: datetime, state: str) -> PaperWindowStopRow:
    return PaperWindowStopRow(window_id=WINDOW_ID, at=at, state=state, known_at=at, ingested_at=at)


@pytest.mark.parametrize(
    ("requested_at", "expected"),
    [
        pytest.param(_utc(T1, hour=22), T1, id="after-the-close"),
        # Saturday 2026-10-31: the next session, Monday 2026-11-02.
        pytest.param(datetime(2026, 10, 31, 15, tzinfo=UTC), date(2026, 11, 2), id="saturday"),
    ],
)
def test_report_stop_session_is_plan_stop_session(requested_at: datetime, expected: date) -> None:
    """report reads the stop session through `plan.stop_session` (#592), from the
    earliest `requested` row; a `closed` row is not a request."""
    stops = [
        _stop_row(requested_at + timedelta(days=3), "closed"),
        _stop_row(requested_at, "requested"),
    ]
    assert report_module._stop_session_of(stops) == stop_session(requested_at) == expected
    assert report_module._stop_session_of([_stop_row(requested_at, "closed")]) is None


# --- the window's cadence (ADR 0017 part C, #1286) -----------------------------------

#: Three consecutive ISO-week ends (Fridays); at `month_end` these dates hold one
#: rebalance session (2026-11-30) and no compared period.
W0, W1, W2 = date(2026, 11, 27), date(2026, 12, 4), date(2026, 12, 11)
WEEK_TRIAL_ID = 1


def _week_end_trial_store(settings: Settings) -> None:
    """A `week_end` hypothesis and its tracking trial over [W0, W2] on the fixture
    store: base-level equity at each week-end, a modelled cost at W0 and W1."""
    params = json.dumps({"costs.per_side_bps": 5.0, "schedule.rebalance_cadence": "week_end"})
    with open_for_write(settings) as conn:
        conn.execute(
            "INSERT INTO hypotheses (hypothesis_id, slug, family, title, doc_path, doc_sha256, "
            "params_json, params_sha256, in_sample_start, holdout_start, holdout_end, "
            "registered_at, registered_by) VALUES (1, 'h-week', 'momentum', 'week', "
            "'docs/hypotheses/h-week.md', repeat('0', 64), ?, repeat('0', 64), ?, ?, ?, ?, "
            "'test')",
            [params, W0, W0, W0, _utc(W0)],
        )
        conn.execute(
            "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
            "end_session, code_version, synthetic, run_by) VALUES "
            "(?, 1, 'tracking', ?, ?, ?, 'test', FALSE, 'test')",
            [WEEK_TRIAL_ID, _utc(W0), W0, W2],
        )
        for session, equity in ((W0, 100_000.0), (W1, 100_500.0), (W2, 101_000.0)):
            conn.execute(
                "INSERT INTO trial_equity (trial_id, series, cost_per_side_bps, session, equity) "
                "VALUES (?, 'strategy', 5.0, ?, ?)",
                [WEEK_TRIAL_ID, session, equity],
            )
        for session, cost_paid in ((W0, 50.0), (W1, 25.0)):
            conn.execute(
                "INSERT INTO trial_rebalances (trial_id, cost_per_side_bps, session, "
                "fill_session, n_universe, n_static_listings, n_targets, turnover, cost_paid, "
                "n_missing_fill, n_delisting_exits, n_stale_exits, n_excluded_no_history, "
                "n_dropped_dividends, n_late_dividends) "
                "VALUES (?, 5.0, ?, ?, 1, 1, 1, 0.0, ?, 0, 0, 0, 0, 0, 0)",
                [WEEK_TRIAL_ID, session, session, cost_paid],
            )


@pytest.fixture
def week_settings(fixture_store_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(fixture_store_path)})


def test_week_end_report_compares_per_iso_week(week_settings: Settings) -> None:
    """At `week_end` the report's trial periods are the ISO weeks of the window's
    cadence: `_trial_periods` reads [W0, W1, W2] (a `month_end` read finds no period
    here), and `compare_periods` compares (W0, W1) and (W1, W2), each against its own
    week's modelled cost."""
    _week_end_trial_store(week_settings)
    with open_read_only(week_settings) as conn:
        trial = report_module._trial_periods(conn, WEEK_TRIAL_ID, "week_end")
    assert tuple(trial.sessions) == (W0, W1, W2)
    assert trial.cadence == "week_end"
    assert dict(trial.equity) == {W0: 100_000.0, W1: 100_500.0, W2: 101_000.0}

    equity = {W0: 100_000.0, W1: 100_500.0, W2: 101_000.0}
    window = replace(_window(), first_rebalance_session=W0)
    result = compare_periods(
        window,
        trial,
        _journal(positions_daily=_flat_marks(equity), runs=()),
        _no_actions(),
        _no_price,
        None,
    )
    assert [(m.rebalance_session, m.next_session) for m in result.periods] == [(W0, W1), (W1, W2)]
    assert [m.modelled_cost for m in result.periods] == [
        pytest.approx(50.0 / 100_000.0),
        pytest.approx(25.0 / 100_500.0),
    ]


def test_week_end_target_comparison_reads_fill_sessions_at_the_window_cadence(
    week_settings: Settings,
) -> None:
    """`compare_targets` passes the trial's cadence to `fill_session` (#1286): a week-end
    T_i is not a `month_end` rebalance session, so the `month_end` fallback raised here;
    with no decision and no trial weight there is nothing to report."""
    _week_end_trial_store(week_settings)
    with open_read_only(week_settings) as conn:
        trial = report_module._trial_periods(conn, WEEK_TRIAL_ID, "week_end")
        result = compare_targets(_window(), trial, _journal(runs=()), conn)
    assert result.rows == ()
