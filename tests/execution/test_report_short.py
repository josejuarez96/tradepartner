"""Short tracking terms from hand marked journal rows."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import polars as pl
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution.report import Journal, TrialPeriods, compare_periods
from tradepartner.store.journal import PaperWindowRow, PositionDailyRow

T0 = date(2026, 10, 1)
T1 = date(2026, 10, 2)


def _mark(session: date, name: str, quantity: float, price: float, side: str) -> PositionDailyRow:
    at = session_close(session)
    return PositionDailyRow(
        run_id=1,
        session=session,
        security_id=name,
        quantity=quantity,
        mark_price=price,
        value=quantity * price,
        cash=1100.0,
        position_side=side,
        known_at=at,
        ingested_at=at,
    )


def test_short_report_terms_are_hand_values() -> None:
    at = datetime(2026, 10, 1, 13, tzinfo=UTC)
    window = PaperWindowRow(
        window_id=1,
        hypothesis_id=1,
        first_rebalance_session=T0,
        account_id="PA1",
        starting_cash=1000.0,
        starting_equity=1000.0,
        code_version="test",
        started_at=at,
        frozen_json=json.dumps(
            {
                "paper.tracking_k": 2.0,
                "paper.tracking_rule": "raw",
                "portfolio.side": "long_short",
                "costs.borrow_bps_per_year": 360.0,
            }
        ),
        frozen_sha256="0" * 64,
        known_at=at,
        ingested_at=at,
    )
    marks = (_mark(T0, "SHORT", 10, 10, "short"), _mark(T1, "SHORT", 10, 10, "short"))
    journal = Journal(positions_daily=marks, adjustments=(), decisions=())
    actions = pl.DataFrame(
        {
            "security_id": ["SHORT"],
            "action_type": ["dividend"],
            "ex_date": [T1],
            "ratio_or_amount": [0.5],
            "known_at": [session_close(T1)],
        }
    )
    trial = TrialPeriods(sessions=(T0, T1), equity={T0: 1000.0, T1: 1000.0}, cost_paid={T0: 0.0})
    period = compare_periods(
        window, trial, journal, actions, lambda _name, _date: 10.0, lambda _name, _date: 10.0, None
    ).periods[0]
    assert period.short_dividend_term == 5.0 / 1000.0
    assert period.dividend_term == -5.0 / 1000.0
    assert period.short_borrow_fee_term == 0.01 / 1000.0
    assert period.short_report_line == "short dividends 0.005000 headline borrow fee 0.000010"


def test_long_report_has_no_short_line() -> None:
    at = datetime(2026, 10, 1, 13, tzinfo=UTC)
    window = PaperWindowRow(
        window_id=1,
        hypothesis_id=1,
        first_rebalance_session=T0,
        account_id="PA1",
        starting_cash=1000.0,
        starting_equity=1000.0,
        code_version="test",
        started_at=at,
        frozen_json=json.dumps({"paper.tracking_k": 2.0, "paper.tracking_rule": "raw"}),
        frozen_sha256="0" * 64,
        known_at=at,
        ingested_at=at,
    )
    marks = (_mark(T0, "LONG", 10, 10, "long"), _mark(T1, "LONG", 10, 10, "long"))
    journal = Journal(positions_daily=marks, adjustments=(), decisions=())
    trial = TrialPeriods(sessions=(T0, T1), equity={T0: 1200.0, T1: 1200.0}, cost_paid={T0: 0.0})
    period = compare_periods(
        window,
        trial,
        journal,
        pl.DataFrame(),
        lambda _name, _date: 10.0,
        lambda _name, _date: 10.0,
        None,
    ).periods[0]
    assert period.short_report_line is None


def test_short_fee_refuses_missing_intermediate_mark() -> None:
    at = datetime(2026, 10, 1, 13, tzinfo=UTC)
    t2 = date(2026, 10, 5)
    window = PaperWindowRow(
        window_id=1,
        hypothesis_id=1,
        first_rebalance_session=T0,
        account_id="PA1",
        starting_cash=1000.0,
        starting_equity=1000.0,
        code_version="test",
        started_at=at,
        frozen_json=json.dumps(
            {
                "paper.tracking_k": 2.0,
                "paper.tracking_rule": "raw",
                "portfolio.side": "long_short",
                "costs.borrow_bps_per_year": 360.0,
            }
        ),
        frozen_sha256="0" * 64,
        known_at=at,
        ingested_at=at,
    )
    journal = Journal(
        positions_daily=(_mark(T0, "SHORT", 10, 10, "short"), _mark(t2, "SHORT", 10, 10, "short")),
        adjustments=(),
        decisions=(),
    )
    trial = TrialPeriods(sessions=(T0, t2), equity={T0: 1000.0, t2: 1000.0}, cost_paid={T0: 0.0})
    with pytest.raises(ValueError, match="no mark for short fee"):
        compare_periods(
            window, trial, journal, pl.DataFrame(), lambda _n, _d: 10.0, lambda _n, _d: 10.0, None
        )
