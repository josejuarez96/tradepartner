"""The shared delisting exit rule (plan T410): each branch of the policy, merger cash,
stock and mixed, the ended-listing choice and the merger-frame wiring."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from tradepartner.backtest import delisting_value as dv
from tradepartner.config_backtest import DELISTING_POLICIES

FAILURE = DELISTING_POLICIES["failure_v1"]
SEVERE = DELISTING_POLICIES["severe_v1"]
END = date(2024, 2, 20)
FILED = datetime(2024, 2, 1, 12, tzinfo=UTC)
LISTING = {"exchange": "NASDAQ", "delisting_form": "25-NSE", "delisting_filed_at": FILED}


def _reason(reason: str, exchange: str = "NASDAQ") -> dict[str, object]:
    return {
        "delisting_form": "25-NSE",
        "delisting_filed_at": FILED,
        "delisting_exchange": exchange,
        "reason": reason,
    }


def _merger(
    cash: float | None, ratio: float | None, close: float | None, effective: date = END
) -> dict[str, object]:
    return {
        "effective_date": effective,
        "cash_per_share": cash,
        "share_ratio": ratio,
        "acquirer_raw_close": close,
    }


def _counts() -> dict[str, int]:
    return dict.fromkeys(
        (
            "n_failure_shocks",
            "n_merger_consideration",
            "n_merger_fallbacks",
            "n_merger_conflicts",
            "n_reason_conflicts",
            "n_unknown_exits",
        ),
        0,
    )


def _value(
    reasons: list[dict[str, object]],
    mergers: list[dict[str, object]],
    terms: object = FAILURE,
    listing: dict[str, object] | None = None,
    final: date | None = END,
) -> tuple[dv.ExitValuation, dict[str, int]]:
    counts = _counts()
    valuation = dv.delisting_exit_valuation(
        "X",
        listing or LISTING,
        reasons,
        mergers,
        final,
        terms,
        "p",
        counts,  # type: ignore[arg-type]
    )
    return valuation, counts


@pytest.mark.parametrize("reason", ["performance_failure", "bankruptcy"])
def test_a_confirmed_failure_takes_the_exchange_shock(reason: str) -> None:
    valuation, counts = _value([_reason(reason)], [])
    assert valuation == dv.ExitValuation(multiplier=1 - 0.55)
    assert dv.delisting_exit_factor(valuation, 10.0) == pytest.approx(0.45)
    assert counts["n_failure_shocks"] == 1


def test_each_exchange_takes_its_own_shock() -> None:
    nyse = {**LISTING, "exchange": "NYSE"}
    valuation, _ = _value([_reason("bankruptcy", "NYSE")], [], listing=nyse)
    assert valuation.multiplier == pytest.approx(0.70)


def test_a_non_failure_reason_is_held_at_the_last_close() -> None:
    valuation, counts = _value([_reason("voluntary_other")], [])
    assert valuation == dv.ExitValuation()
    assert dv.delisting_exit_factor(valuation, 10.0) == 1.0
    assert counts["n_failure_shocks"] == 0
    assert counts["n_unknown_exits"] == 0


def test_unknown_is_shocked_only_under_severe() -> None:
    mild, counts = _value([], [], FAILURE)
    assert mild == dv.ExitValuation()
    assert counts["n_unknown_exits"] == 1
    severe, counts = _value([], [], SEVERE)
    assert severe.multiplier == pytest.approx(0.0)
    assert counts["n_failure_shocks"] == 1 and counts["n_unknown_exits"] == 1


def test_severe_does_not_shock_an_unknown_exit_with_a_merger_row() -> None:
    valuation, _ = _value([], [_merger(5.0, None, None)], SEVERE)
    assert valuation == dv.ExitValuation()


def test_two_reason_rows_are_a_conflict_and_unknown() -> None:
    valuation, counts = _value([_reason("bankruptcy"), _reason("voluntary_other")], [])
    assert valuation == dv.ExitValuation()
    assert counts["n_reason_conflicts"] == 1 and counts["n_unknown_exits"] == 1


def test_a_reason_row_for_another_exchange_or_filing_does_not_match() -> None:
    valuation, counts = _value([_reason("bankruptcy", "NYSE")], [])
    assert valuation == dv.ExitValuation() and counts["n_unknown_exits"] == 1
    bare = {"exchange": "NASDAQ"}
    valuation, counts = _value([_reason("bankruptcy")], [], listing=bare)
    assert valuation == dv.ExitValuation() and counts["n_unknown_exits"] == 1


def test_a_missing_exchange_in_the_shock_map_raises() -> None:
    odd = {**LISTING, "exchange": "OTC"}
    with pytest.raises(ValueError, match=r"X: listing exchange 'OTC' is outside.*'p'"):
        _value([_reason("bankruptcy", "OTC")], [], listing=odd)
    none_listing = {"delisting_form": "25-NSE", "delisting_filed_at": FILED}
    with pytest.raises(ValueError, match="outside"):
        dv.delisting_exit_valuation("X", none_listing, [], [], END, SEVERE, "severe_v1", _counts())


def test_the_unmapped_exchange_is_only_checked_on_the_shocked_branch() -> None:
    odd = {**LISTING, "exchange": "OTC"}
    valuation, _ = _value([_reason("voluntary_other", "OTC")], [], listing=odd)
    assert valuation == dv.ExitValuation()


@pytest.mark.parametrize(
    ("cash", "ratio", "close", "per_share"),
    [(120.0, None, None, 120.0), (None, 0.5, 40.0, 20.0), (10.0, 0.5, 40.0, 30.0)],
)
def test_merger_consideration_cash_stock_and_mixed(
    cash: float | None, ratio: float | None, close: float | None, per_share: float
) -> None:
    valuation, counts = _value([_reason("merger_acquisition")], [_merger(cash, ratio, close)])
    assert valuation.per_share == pytest.approx(per_share)
    assert dv.delisting_exit_factor(valuation, 8.0) == pytest.approx(per_share / 8.0)
    assert counts["n_merger_consideration"] == 1
    assert counts["n_failure_shocks"] == 0


def test_a_merger_without_a_usable_row_falls_back_and_is_counted() -> None:
    reasons = [_reason("merger_acquisition")]
    none_row, counts = _value(reasons, [])
    assert none_row == dv.ExitValuation() and counts["n_merger_fallbacks"] == 1
    no_close, counts = _value(reasons, [_merger(None, 0.5, None)])
    assert no_close == dv.ExitValuation() and counts["n_merger_fallbacks"] == 1
    two, counts = _value(reasons, [_merger(5.0, None, None), _merger(6.0, None, None)])
    assert two == dv.ExitValuation()
    assert counts["n_merger_fallbacks"] == 1 and counts["n_merger_conflicts"] == 1
    assert counts["n_merger_consideration"] == 0


def test_a_merger_outside_the_window_is_not_matched() -> None:
    late = _merger(5.0, None, None, effective=date(2024, 4, 1))
    early = _merger(5.0, None, None, effective=date(2024, 2, 1))
    reasons = [_reason("merger_acquisition")]
    for row in (late, early):
        valuation, counts = _value(reasons, [row])
        assert valuation == dv.ExitValuation() and counts["n_merger_fallbacks"] == 1
    valuation, _ = _value(reasons, [_merger(5.0, None, None)], final=None)
    assert valuation == dv.ExitValuation()


def test_the_window_end_is_the_session_n_after() -> None:
    assert dv.window_end(date(2024, 2, 20), 0) == date(2024, 2, 20)
    assert dv.window_end(date(2024, 2, 16), 10) == date(2024, 3, 4)


def test_ended_listings_keeps_delisted_current_rows_only() -> None:
    frame = pl.DataFrame(
        {
            "security_id": ["A", "B", "C", "D"],
            "status": ["delisted", "transferred", "listed", "delisted"],
            "end_session": [END, END, None, END],
            "valid_from": [date(2020, 1, 2)] * 3 + [date(2025, 1, 2)],
            "exchange": ["NYSE"] * 4,
        }
    )
    out = dv.ended_listings(frame, date(2024, 3, 1))
    assert out == {
        "A": {
            "end_session": END,
            "exchange": "NYSE",
            "delisting_form": None,
            "delisting_filed_at": None,
        }
    }
