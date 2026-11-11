"""Match journaled dividend cash to dated dues, oldest eligible due first."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date

from tradepartner.calendar import previous_session
from tradepartner.store.journal import AdjustmentRow


def net_dividend_dues(
    dues: Mapping[str, Sequence[tuple[date, float]]],
    credits: Mapping[str, Sequence[AdjustmentRow]],
    *,
    credit_sessions: int,
    cash_tolerance: float,
) -> tuple[dict[str, list[tuple[date, float]]], list[tuple[AdjustmentRow, date, float]]]:
    """Return remaining dated dues and each credit's matched signed amount.

    A credit can consume only a same-sign due inside its own session window.
    Reconciliation and the paper report use the same allocation; the report
    uses the matches to attribute a late credit in the period that absorbs it.
    """
    outstanding: dict[str, list[tuple[date, float]]] = {}
    matches: list[tuple[AdjustmentRow, date, float]] = []
    for name, dated in dues.items():
        remaining = sorted(dated)
        ordered_credits = sorted(
            credits.get(name, ()), key=lambda a: (a.session, a.adjustment_id or 0)
        )
        for credit in ordered_credits:
            cash = credit.cash
            if cash is None:
                continue
            credit_window_start = credit.session
            for _ in range(credit_sessions):
                credit_window_start = previous_session(credit_window_start)
            for index, (ex_date, amount) in enumerate(remaining):
                if (
                    not credit_window_start < ex_date <= credit.session
                    or amount == 0
                    or cash * amount <= 0
                ):
                    continue
                paid = min(abs(cash), abs(amount))
                signed_paid = math.copysign(paid, cash)
                remainder = amount - signed_paid
                remaining[index] = (
                    ex_date,
                    0.0 if abs(remainder) <= cash_tolerance else remainder,
                )
                matches.append((credit, ex_date, signed_paid))
                cash -= signed_paid
                if cash == 0.0:
                    break
        outstanding[name] = remaining
    return outstanding, matches
