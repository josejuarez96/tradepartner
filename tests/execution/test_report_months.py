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

import polars as pl
import pytest

from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.execution import report as report_module
from tradepartner.execution.plan import stop_session
from tradepartner.execution.report import (
    Journal,
    PriceOf,
    TrialMonths,
    compare_months,
)
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    FillRow,
    OrderedFill,
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
) -> TrialMonths:
    return TrialMonths(sessions=sessions, equity=equity, cost_paid=cost_paid)


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
) -> PositionDailyRow:
    return PositionDailyRow(
        run_id=run_id,
        session=session,
        security_id=security_id,
        quantity=quantity,
        mark_price=price,
        value=quantity * price,
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
    runs: Sequence[PaperRunRow] = (_run(1, T0), _run(2, T1)),
    rebalance_events: Sequence[RebalanceEventRow] = (),
) -> Journal:
    return Journal(
        positions_daily=positions_daily,
        adjustments=adjustments,
        decisions=decisions,
        decision_events=decision_events,
        fills=fills,
        runs=runs,
        rebalance_events=rebalance_events,
    )


def _flat_marks(equity: Mapping[date, float]) -> list[PositionDailyRow]:
    return [_cash_mark(session, cash) for session, cash in equity.items()]


def test_equal_returns_both_rules_pass() -> None:
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(positions_daily=_flat_marks(equity))
    for rule in ("raw", "residual"):
        result = compare_months(
            _window(tracking_rule=rule), trial, journal, _no_actions(), _no_price, _no_price, None
        )
        assert result.passed, result.months
        assert result.months[0].raw == pytest.approx(0.0)


def test_modelled_cost_uses_trial_equity_not_paper_equity() -> None:
    # Paper's account is 10x smaller than the trial it is tracked against.
    # Spec req 10: "the trial's base-level cost_paid at T_i over its equity
    # at close(T_i)" -- "its" is the trial's, so the threshold must not
    # scale with paper's account size.
    paper_equity = {T0: 10_000.0, T1: 10_100.0}  # paper return = 0.01
    trial_equity = {T0: 100_000.0, T1: 101_000.0}  # trial return = 0.01
    trial = _trial(trial_equity, {T0: 1_000.0})  # modelled cost = 1_000/100_000 = 0.01
    journal = _journal(positions_daily=_flat_marks(paper_equity))
    result = compare_months(
        _window(tracking_rule="raw", tracking_k=1.0),
        trial,
        journal,
        _no_actions(),
        _no_price,
        _no_price,
        None,
    )
    assert result.months[0].modelled_cost == pytest.approx(0.01)
    assert result.months[0].raw == pytest.approx(0.0)  # both returns are 0.01
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
    result = compare_months(
        _window(tracking_rule="residual"), trial, journal, actions, _no_price, _no_price, None
    )
    # The correction moved the dividend out of this month entirely: the
    # correct identity (by source_action_id) leaves nothing to count here.
    # A wrong identity (keeping the original's in-month row alive too) would
    # report 0.5*100/100_000 = 0.0005 instead.
    assert result.months[0].dividend_term == pytest.approx(0.0)


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
    result = compare_months(
        _window(tracking_rule="residual"), trial, late_credit, dividends, _no_price, _no_price, None
    )
    expected = (0.5 * 100.0) / 100_000.0
    assert result.months[0].dividend_term == pytest.approx(expected)


def test_raw_boundary_exact_passes_one_above_fails() -> None:
    # Every number below is a dyadic fraction (an exact power-of-two divisor)
    # so the arithmetic `compare_months` does is bit-exact, and the "exactly
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
    ok = compare_months(
        _window(tracking_rule="raw", tracking_k=tracking_k),
        _trial(trial_equity, cost),
        boundary,
        _no_actions(),
        _no_price,
        _no_price,
        None,
    )
    assert ok.months[0].raw == threshold  # bit-exact, not merely approx
    assert ok.passed

    # Comfortably over (far beyond any float noise) fails, naming T0.
    over = _journal(positions_daily=_flat_marks({T0: equity_i, T1: boundary_next + 1.0}))
    bad = compare_months(
        _window(tracking_rule="raw", tracking_k=tracking_k),
        _trial(trial_equity, cost),
        over,
        _no_actions(),
        _no_price,
        _no_price,
        None,
    )
    assert not bad.passed
    assert bad.failing_month == T0
    assert not bad.months[0].passed


def test_dividend_term_and_dividend_cash_zero() -> None:
    ex_date = date(2026, 10, 15)
    record_date = previous_session(ex_date)
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 0.0})
    # Entitlement is held as of the session before the ex-date (the record
    # date), not the ex-date's own close (`_dividends` in reconcile_run.py).
    marks = [
        *_flat_marks(equity),
        _position_mark(record_date, A, 100.0, 50.0),
    ]
    dividends = _action(A, "dividend", ex_date, 0.5, _utc(T1))  # amount 0.5/share

    with_dividend = _journal(positions_daily=marks)
    result = compare_months(
        _window(tracking_rule="residual"),
        trial,
        with_dividend,
        dividends,
        _no_price,
        _no_price,
        None,
    )
    expected_term = (0.5 * 100.0) / 100_000.0
    month = result.months[0]
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
    raw_result = compare_months(
        _window(tracking_rule="raw"),
        raw_fail_trial,
        raw_fail_journal,
        _no_actions(),
        _no_price,
        _no_price,
        None,
    )
    assert not raw_result.passed  # raw alone, tracking_k=2, cost=0 -> any nonzero raw fails
    residual_result = compare_months(
        _window(tracking_rule="residual"),
        raw_fail_trial,
        raw_fail_journal,
        dividends,
        _no_price,
        _no_price,
        None,
    )
    # dividend term = 0.5*100/100_000 = 0.0005, nowhere near rescuing -0.01, so use a
    # dividend sized to exactly offset raw: amount such that dividend_term == 0.01.
    big_dividend = _action(A, "dividend", ex_date, 10.0, _utc(T1))  # 10*100/100_000 = 0.01
    rescued = compare_months(
        _window(tracking_rule="residual"),
        raw_fail_trial,
        raw_fail_journal,
        big_dividend,
        _no_price,
        _no_price,
        None,
    )
    assert rescued.months[0].residual == pytest.approx(0.0)
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
    credited_result = compare_months(
        _window(tracking_rule="residual"), trial, credited, dividends, _no_price, _no_price, None
    )
    assert credited_result.months[0].dividend_term == pytest.approx(0.0)

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
    credited_lagged_result = compare_months(
        _window(tracking_rule="residual"),
        trial,
        credited_lagged,
        dividends,
        _no_price,
        _no_price,
        None,
    )
    assert credited_lagged_result.months[0].dividend_term == pytest.approx(0.0)


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
    buy_result = compare_months(
        _window(tracking_rule="raw"), trial, buy, _no_actions(), closes, _no_price, None
    )
    expected_buy = (10.0 * 1.0) / 100_000.0
    assert buy_result.months[0].fill_timing_term == pytest.approx(expected_buy)
    assert buy_result.months[0].fill_timing_term > 0

    # A sell filled below the close: also positive.
    sell = _journal(
        positions_daily=_flat_marks(equity),
        fills=[_fill("sell-1", "sell", A, _utc(fill_day), 10.0, close - 1.0)],
    )
    sell_result = compare_months(
        _window(tracking_rule="raw"), trial, sell, _no_actions(), closes, _no_price, None
    )
    expected_sell = (10.0 * 1.0) / 100_000.0
    assert sell_result.months[0].fill_timing_term == pytest.approx(expected_sell)
    assert sell_result.months[0].fill_timing_term > 0

    # Residual equals the hand-computed value (raw=0, dividend=0, fill_timing=expected).
    assert sell_result.months[0].residual == pytest.approx(expected_sell)


def test_residue_term_hand_computed_and_split_adjusted() -> None:
    equity = {T0: 100_000.0, T1: 100_000.0}
    trial = _trial(equity, {T0: 0.0})
    close_i, close_next = 50.0, 55.0
    closes = _prices({(A, T0): close_i, (A, T1): close_next})

    # A carried residue of 10 shares held at T0, no decisions, no splits. Cash
    # is reduced by the position's value so total equity at T0 is exactly
    # 100_000, matching `equity` and the hand-computed expectation below.
    marks = [
        _cash_mark(T0, 100_000.0 - 10.0 * close_i),
        _cash_mark(T1, 100_000.0),
        _position_mark(T0, A, 10.0, close_i),
    ]
    adjustments = [
        AdjustmentRow(
            adjustment_id=next(_IDS),
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=10.0,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        )
    ]
    journal = _journal(positions_daily=marks, adjustments=adjustments)
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, closes, None
    )
    expected = 10.0 * (close_next - close_i) / 100_000.0
    assert result.months[0].residue_term == pytest.approx(expected)
    assert result.months[0].residual == pytest.approx(0.0)  # never folded into the residual

    # A realistic forward 2:1 split inside the month: the observed market
    # close roughly HALVES (one pre-split share becomes two post-split
    # shares at about half the price), so bringing it back to T0's basis
    # MULTIPLIES by the split ratio, never divides (`execution.ledger`'s own
    # `_split_factor` convention: a holding's *quantity* is multiplied
    # forward by the ratio, so a *price* read on the post-split side must be
    # multiplied, not divided, to land back on the pre-split basis). A test
    # that instead doubled the post-split close would pass under either sign
    # convention and hide the bug; this one fails under a wrong `/ factor`.
    split_close_next = close_next / 2.0  # the actual post-split observed close
    split_closes = _prices({(A, T0): close_i, (A, T1): split_close_next})
    split_actions = _action(A, "split", date(2026, 10, 15), 2.0, _utc(T1))
    split_result = compare_months(
        _window(tracking_rule="raw"), trial, journal, split_actions, _no_price, split_closes, None
    )
    assert split_result.months[0].residue_term == pytest.approx(expected)


def test_residue_term_no_look_ahead_on_later_decision() -> None:
    # A name held at T0 with no decision yet; a *later* run (T1) writes a
    # `window_stop` forced exit closed `dust`, which would make the whole
    # holding a residue -- but only from T1 on. The residue term at month 0
    # (evaluated on rows known at close(T0)) must not see that future row.
    equity = {T0: 100_000.0, T1: 100_000.0}
    trial = _trial(equity, {T0: 0.0})
    close_i, close_next = 50.0, 55.0
    closes = _prices({(A, T0): close_i, (A, T1): close_next})
    marks = [
        _cash_mark(T0, 100_000.0 - 10.0 * close_i),
        _cash_mark(T1, 100_000.0 - 10.0 * close_i),
        _position_mark(T0, A, 10.0, close_i, run_id=1),
        _position_mark(T1, A, 10.0, close_next, run_id=2, tradable=False),
    ]
    future_decision_id = next(_IDS)
    decisions = [
        DecisionRow(
            decision_id=future_decision_id,
            run_id=2,  # the T1 run, after month 0's T_i = T0
            rebalance_session=None,
            security_id=A,
            whole_share=False,
            decision="forced_exit",
            reason="window_stop",
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        )
    ]
    decision_events = [
        DecisionEventRow(
            decision_id=future_decision_id,
            run_id=2,
            status="skipped",
            reason="dust",
            known_at=_utc(T1),
            ingested_at=_utc(T1),
        )
    ]
    journal = _journal(
        positions_daily=marks,
        decisions=decisions,
        decision_events=decision_events,
        runs=(_run(1, T0), _run(2, T1)),
    )
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, closes, None
    )
    # Month 0 (T0 -> T1): the dust decision is dated at T1, not known at
    # close(T0), so it must not make T0's residue term nonzero.
    assert result.months[0].residue_term == pytest.approx(0.0)


def test_residue_term_uses_close_even_when_fill_price_is_open() -> None:
    # #606: a window frozen with `execution.fill_price = "open"` binds
    # `prices` (the fill-timing accessor `report()` passes through) to the
    # open bar, but the residue term must still value residues at the
    # close, like the marks (module docstring) -- through the separate
    # `closes` accessor the report entry point always binds to "close",
    # regardless of the frozen fill_price. `prices` here is deliberately
    # bound to *open* values that differ from `closes`'s close values: if
    # the residue term ever read through `prices` instead of `closes`, it
    # would land on these open-based numbers and fail the assertion below.
    equity = {T0: 100_000.0, T1: 100_000.0}
    trial = _trial(equity, {T0: 0.0})
    close_i, close_next = 50.0, 55.0  # close moves +5
    open_i, open_next = 48.0, 70.0  # open moves +22: a different delta, so a residue
    # term computed from the open values lands on a visibly different number than one
    # computed from the close values, not a coincidentally equal one (quant-auditor
    # finding on PR #654: the first draft moved both bars by the same +5 and so
    # could not actually distinguish an open-priced residue term from a close-priced
    # one).
    closes = _prices({(A, T0): close_i, (A, T1): close_next})
    open_prices = _prices({(A, T0): open_i, (A, T1): open_next})

    marks = [
        _cash_mark(T0, 100_000.0 - 10.0 * close_i),
        _cash_mark(T1, 100_000.0),
        _position_mark(T0, A, 10.0, close_i),
    ]
    adjustments = [
        AdjustmentRow(
            adjustment_id=next(_IDS),
            window_id=WINDOW_ID,
            session=T0,
            kind="carried_residue",
            origin="dust",
            security_id=A,
            quantity=10.0,
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        )
    ]
    journal = _journal(positions_daily=marks, adjustments=adjustments)
    result = compare_months(
        _window(tracking_rule="raw", fill_price="open"),
        trial,
        journal,
        _no_actions(),
        open_prices,
        closes,
        None,
    )
    expected = 10.0 * (close_next - close_i) / 100_000.0
    assert result.months[0].residue_term == pytest.approx(expected)


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
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, _no_price, None
    )
    assert result.passed  # excluded from the check
    assert result.months[0].missed
    assert result.months[0].excluded

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
    override_result = compare_months(
        _window(tracking_rule="raw"),
        trial,
        override_journal,
        _no_actions(),
        _no_price,
        _no_price,
        None,
    )
    assert override_result.months[0].override
    assert not override_result.months[0].excluded
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
    result = compare_months(
        _window(tracking_rule="residual"),
        trial,
        override_journal,
        _no_actions(),
        _no_price,
        _no_price,
        None,
    )
    assert result.months[0].override
    assert result.months[0].excluded
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
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, _no_price, None
    )
    assert result.months[0].skip_names == (B,)
    assert not result.months[0].excluded
    assert result.passed


def test_frozen_rule_not_live_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Live Settings would say "residual"; the window freezes "raw".
    monkeypatch.setenv("PAPER__TRACKING_RULE", "residual")
    live = Settings(_env_file=None)
    assert live.paper.tracking_rule == "residual"
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(positions_daily=_flat_marks(equity))
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), _no_price, _no_price, None
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
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, _no_actions(), closes, _no_price, None
    )
    expected = (10.0 * 1.0) / 100_000.0
    assert result.months[0].fill_timing_term == pytest.approx(expected)


def test_no_look_ahead_late_dividend_not_counted() -> None:
    ex_date = date(2026, 10, 15)
    record_date = previous_session(ex_date)
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 0.0})
    marks = [*_flat_marks(equity), _position_mark(record_date, A, 100.0, 50.0)]
    # Known only AFTER close(T1): must not affect month 0's dividend term.
    late = _action(A, "dividend", ex_date, 5.0, session_close(T1) + timedelta(seconds=1))
    journal = _journal(positions_daily=marks)
    result = compare_months(
        _window(tracking_rule="raw"), trial, journal, late, _no_price, _no_price, None
    )
    assert result.months[0].dividend_term == pytest.approx(0.0)

    # The same dividend, known just in time (at close(T1)): counted.
    on_time = _action(A, "dividend", ex_date, 5.0, session_close(T1))
    on_time_result = compare_months(
        _window(tracking_rule="raw"), trial, journal, on_time, _no_price, _no_price, None
    )
    assert on_time_result.months[0].dividend_term == pytest.approx((5.0 * 100.0) / 100_000.0)


def test_month_uncompared_at_or_after_stop_session() -> None:
    equity = {T0: 100_000.0, T1: 101_000.0}
    trial = _trial(equity, {T0: 100.0})
    journal = _journal(positions_daily=_flat_marks(equity))
    result = compare_months(
        _window(tracking_rule="raw"),
        trial,
        journal,
        _no_actions(),
        _no_price,
        _no_price,
        # A stop requested after close(T1) still falls in T1 (plan.stop_session).
        stop_session=stop_session(_utc(T1, hour=22)),
    )
    assert result.months == ()
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
