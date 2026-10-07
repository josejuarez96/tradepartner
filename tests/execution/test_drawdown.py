"""The drawdown check's read of the marks (`execution.drawdown.check`, #1116).

A selected session whose rows cannot state its ledger equity is refused, never
skipped (#713 (ii)(a)): an unreadable mark that is not the last one used to be
passed over without a word, so it could never trip the kill switch.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tradepartner.calendar import next_session, session_close
from tradepartner.execution import drawdown
from tradepartner.execution.marks import UnreadableMarkError
from tradepartner.store.journal import PaperWindowRow, PositionDailyRow

D1 = date(2026, 10, 5)
D2 = next_session(D1)
S = next_session(D2)
MAX_DRAWDOWN = 0.30


def _window() -> PaperWindowRow:
    at = datetime(2026, 10, 1, 13, tzinfo=UTC)
    return PaperWindowRow(
        window_id=1,
        hypothesis_id=1,
        first_rebalance_session=date(2026, 10, 30),
        account_id="PA1",
        starting_cash=1000.0,
        starting_equity=1000.0,
        code_version="abc",
        started_at=at,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=at,
        ingested_at=at,
    )


def _row(
    session: date, security_id: str | None, *, cash: float | None, value: float | None = None
) -> PositionDailyRow:
    at = session_close(session)
    return PositionDailyRow(
        run_id=1,
        session=session,
        security_id=security_id,
        quantity=0.0 if security_id is None else 1.0,
        mark_price=value,
        value=value,
        cash=cash,
        known_at=at,
        ingested_at=at,
    )


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(_row(D1, "SEC_A", cash=900.0, value=None), id="no-value"),
        pytest.param(_row(D1, "SEC_A", cash=None, value=100.0), id="no-cash"),
        pytest.param(_row(D1, "SEC_A", cash=900.0, value=float("nan")), id="nan-value"),
    ],
)
def test_an_unreadable_mark_before_the_last_refuses(bad: PositionDailyRow) -> None:
    """D1 cannot be read; D2 (the last mark, 1,000.0) can and does not cross.
    Before #1116 the check skipped D1 and returned None."""
    rows = [bad, _row(D2, None, cash=1000.0)]
    with pytest.raises(UnreadableMarkError, match=D1.isoformat()):
        drawdown.check(_window(), rows, [], MAX_DRAWDOWN, set(), S)


def test_a_held_session_with_no_cash_only_row_is_checked() -> None:
    """The writer's held-session shape (cash on each position row): 100 + 100
    = 200 is below 1,000 x (1 - 0.30) = 700, so D1 crosses."""
    rows = [_row(D1, "SEC_A", cash=100.0, value=100.0), _row(D2, None, cash=1000.0)]
    crossing = drawdown.check(_window(), rows, [], MAX_DRAWDOWN, set(), S)
    assert crossing is not None
    assert (crossing.session, crossing.equity, crossing.peak) == (D1, 200.0, 1000.0)
