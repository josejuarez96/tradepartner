"""`backtest/riskfree.py` (backtest spec amendment 2026-10-11, #1498): the bill-yield
conversion, the session accruals, the period product and the constant source."""

from __future__ import annotations

import math
from datetime import date

import pytest

from tradepartner.backtest.riskfree import (
    RiskFreeMissing,
    annualised,
    constant_period_returns,
    period_risk_free,
    session_accruals,
    to_yield,
)

# Thu 2024-02-29, Fri 03-01, Mon 03-04, Tue 03-05, Wed 03-06, Thu 03-07.
THU, FRI, MON, TUE, WED, NEXT_THU = (
    date(2024, m, d) for m, d in [(2, 29), (3, 1), (3, 4), (3, 5), (3, 6), (3, 7)]
)
SESSIONS = [THU, FRI, MON, TUE, WED, NEXT_THU]


def test_discount_to_yield_at_five_percent() -> None:
    assert to_yield(0.05) == pytest.approx(365 * 0.05 / (360 - 91 * 0.05), rel=1e-15)
    assert to_yield(0.05) == pytest.approx(0.0513434, abs=1e-7)
    assert to_yield(0.05, "discount") == to_yield(0.05)


def test_yield_basis_leaves_the_value_as_is() -> None:
    assert to_yield(0.05, "yield") == 0.05


def test_unknown_basis_is_refused() -> None:
    with pytest.raises(ValueError, match="basis"):
        to_yield(0.05, "money")  # type: ignore[arg-type]


def test_a_weekend_accrues_to_monday() -> None:
    acc = session_accruals([THU, FRI, MON], {FRI: 0.0365, MON: 0.0365})
    assert acc[FRI] == pytest.approx(0.0365 * 1 / 365, rel=1e-15)
    assert acc[MON] == pytest.approx(0.0365 * 3 / 365, rel=1e-15)
    assert THU not in acc


def test_a_missing_rate_names_the_series_and_the_session() -> None:
    with pytest.raises(RiskFreeMissing, match=r"risk_free_missing.*DTB3.*2024-03-04") as err:
        session_accruals([THU, FRI, MON], {FRI: 0.03})
    assert err.value.session == MON
    assert err.value.series == "DTB3"
    assert isinstance(err.value, ValueError)


def test_sessions_must_ascend() -> None:
    with pytest.raises(ValueError, match="ascending"):
        session_accruals([FRI, THU], {THU: 0.01})


def test_period_risk_free_equals_the_hand_product() -> None:
    rates = {FRI: 0.05, MON: 0.05, TUE: 0.05, WED: 0.04, NEXT_THU: 0.04}
    y5, y4 = to_yield(0.05), to_yield(0.04)
    # Periods: (THU, MON] = FRI, MON ; (MON, NEXT_THU] = TUE, WED, NEXT_THU.
    out = period_risk_free(SESSIONS, [THU, MON, NEXT_THU], rates)
    first = (1 + y5 * 1 / 365) * (1 + y5 * 3 / 365) - 1
    second = (1 + y5 / 365) * (1 + y4 / 365) * (1 + y4 / 365) - 1
    assert out == [pytest.approx(first, rel=1e-14), pytest.approx(second, rel=1e-14)]


def test_period_risk_free_yield_basis_uses_the_value_unconverted() -> None:
    rates = dict.fromkeys(SESSIONS[1:], 0.05)
    out = period_risk_free(SESSIONS, [THU, NEXT_THU], rates, basis="yield")
    assert out == [
        pytest.approx((1 + 0.05 / 365) ** 2 * (1 + 0.05 * 3 / 365) * (1 + 0.05 / 365) ** 2 - 1)
    ]


def test_period_risk_free_raises_on_a_session_without_a_rate() -> None:
    rates = {FRI: 0.05, MON: 0.05, TUE: 0.05, NEXT_THU: 0.05}
    with pytest.raises(RiskFreeMissing, match="2024-03-06"):
        period_risk_free(SESSIONS, [THU, NEXT_THU], rates, series="DTB3")


def test_period_risk_free_checks_its_anchors() -> None:
    rates = dict.fromkeys(SESSIONS[1:], 0.05)
    with pytest.raises(ValueError, match="at least two"):
        period_risk_free(SESSIONS, [THU], rates)
    with pytest.raises(ValueError, match="first to the last"):
        period_risk_free(SESSIONS, [FRI, NEXT_THU], rates)


def test_constant_period_returns_match_the_old_scalar_expression() -> None:
    out = constant_period_returns(0.03, 12, 5)
    assert out == [(1 + 0.03) ** (1 / 12) - 1] * 5
    assert constant_period_returns(0.0, 52, 3) == [0.0, 0.0, 0.0]
    assert constant_period_returns(0.03, 12, 0) == []


def test_constant_period_returns_rejects_bad_counts() -> None:
    with pytest.raises(ValueError):
        constant_period_returns(0.03, 0, 3)
    with pytest.raises(ValueError):
        constant_period_returns(0.03, 12, -1)


def test_annualised_of_the_constant_is_the_rate() -> None:
    assert annualised(constant_period_returns(0.0, 12, 6), 12) == 0.0
    assert annualised(constant_period_returns(0.05, 12, 7), 12) == pytest.approx(0.05, rel=1e-12)
    assert annualised([0.01, 0.02], 12) == pytest.approx(math.sqrt(1.01 * 1.02) ** 12 - 1)
    with pytest.raises(ValueError):
        annualised([], 12)


def test_a_nan_rate_and_a_stray_period_end_are_refused() -> None:
    with pytest.raises(ValueError, match="finite"):
        to_yield(float("nan"))
    rates = dict.fromkeys(SESSIONS[1:], 0.05)
    with pytest.raises(ValueError, match="sessions of the run"):
        period_risk_free(SESSIONS, [THU, date(2024, 3, 2), NEXT_THU], rates)
