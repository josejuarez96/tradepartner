"""Window, holdout and gap-gate decisions (backtest spec req 11; plan T36)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

import pytest

from tradepartner.backtest.holdout import (
    Decision,
    Flags,
    Frozen,
    Reasons,
    Window,
    decide,
    default_in_sample_window,
    gap_sessions,
    window_touches_holdout,
)
from tradepartner.store.registry import HoldoutSpend, HypothesisRecord

HYPOTHESIS_ID = 7
FROZEN = Frozen(
    hypothesis_id=HYPOTHESIS_ID,
    in_sample_start=date(2017, 1, 31),
    holdout_start=date(2024, 1, 1),
    holdout_end=date(2026, 8, 31),
    gap_count_share_threshold=0.05,
)
IN_SAMPLE = Window(date(2017, 1, 31), date(2023, 12, 29))
HOLDOUT = Window(date(2024, 1, 1), date(2024, 3, 31))
# Month-end XNYS sessions of HOLDOUT: 2024-03-29 is Good Friday, so March ends on the 28th.
HOLDOUT_GAP_SESSIONS = (date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28))
NO_FLAGS = Flags()
NO_REASONS = Reasons()
SPEND = Flags(spend_holdout=True)
SPEND_REASON = Reasons(holdout_reason="gap below threshold on the vendor source")


def _gap(value: float, **overrides: float) -> dict[date, float]:
    series = dict.fromkeys(HOLDOUT_GAP_SESSIONS, value)
    for key, share in overrides.items():
        series[date.fromisoformat(key.removeprefix("d").replace("_", "-"))] = share
    return series


def _spend(hypothesis_id: int, trial_id: int = 1) -> HoldoutSpend:
    return HoldoutSpend(
        trial_id=trial_id,
        hypothesis_id=hypothesis_id,
        slug=f"h{hypothesis_id}",
        started_at=datetime(2026, 9, 1, tzinfo=UTC),
        synthetic=False,
        holdout_reason="earlier spend",
        status="ok",
    )


def _decide(
    window: Window,
    flags: Flags = NO_FLAGS,
    reasons: Reasons = NO_REASONS,
    gap_series: dict[date, float] | None = None,
    prior_spends: tuple[HoldoutSpend, ...] = (),
) -> Decision:
    return decide(window, FROZEN, flags, reasons, gap_series, prior_spends)


# --- default window and overlap ----------------------------------------------


def test_default_window_ends_at_last_rebalance_session_before_holdout_start() -> None:
    # 2023-12-31 is a Sunday: the last December session is Friday the 29th.
    assert default_in_sample_window(FROZEN) == IN_SAMPLE


def test_default_window_is_strictly_before_a_holdout_starting_on_a_month_end() -> None:
    frozen = Frozen(
        hypothesis_id=1,
        in_sample_start=date(2017, 1, 31),
        holdout_start=date(2023, 12, 29),
        holdout_end=date(2026, 8, 31),
        gap_count_share_threshold=0.05,
    )
    assert default_in_sample_window(frozen) == Window(date(2017, 1, 31), date(2023, 11, 30))


def test_default_window_refuses_a_holdout_leaving_no_in_sample_rebalance() -> None:
    frozen = Frozen(
        hypothesis_id=1,
        in_sample_start=date(2023, 12, 30),  # after December's last session, the 29th
        holdout_start=date(2024, 1, 1),
        holdout_end=date(2026, 8, 31),
        gap_count_share_threshold=0.05,
    )
    with pytest.raises(ValueError, match="no rebalance session"):
        default_in_sample_window(frozen)


def test_default_window_decides_as_an_in_sample_run() -> None:
    decision = _decide(default_in_sample_window(FROZEN))
    assert decision.outcome == "run"
    assert decision.kind == "in_sample"
    assert decision.holdout_repeat is False


@pytest.mark.parametrize(
    ("window", "touches"),
    [
        (IN_SAMPLE, False),
        (Window(date(2023, 6, 1), date(2023, 12, 31)), False),
        (Window(date(2023, 6, 1), date(2024, 1, 1)), True),  # overlap at the start edge
        (Window(date(2026, 8, 31), date(2026, 8, 31)), True),  # overlap at the end edge
        (Window(date(2024, 5, 1), date(2024, 6, 30)), True),  # inside
        (Window(date(2026, 9, 1), date(2026, 12, 31)), False),  # after: tracking, not holdout
    ],
)
def test_window_touches_holdout(window: Window, touches: bool) -> None:
    assert window_touches_holdout(window, FROZEN) is touches


def test_gap_sessions_are_the_month_end_sessions_in_the_window() -> None:
    assert gap_sessions(HOLDOUT) == HOLDOUT_GAP_SESSIONS


# --- window rules ------------------------------------------------------------


def test_start_before_in_sample_start_is_refused_window() -> None:
    decision = _decide(Window(date(2017, 1, 30), date(2020, 12, 31)))
    assert decision.outcome == "refused_window"
    assert "in_sample_start" in decision.message


def test_end_after_holdout_end_is_refused_window_even_with_the_spend_flag() -> None:
    decision = _decide(Window(date(2024, 1, 1), date(2026, 9, 1)), SPEND, SPEND_REASON)
    assert decision.outcome == "refused_window"
    assert "holdout.end" in decision.message


@pytest.mark.parametrize(
    "window",
    [
        Window(date(2024, 1, 2), date(2024, 1, 20)),  # inside the holdout, no month end
        Window(date(2023, 6, 1), date(2024, 1, 15)),  # month ends only before the holdout
    ],
)
def test_holdout_window_with_no_holdout_rebalance_session_is_refused_window(
    window: Window,
) -> None:
    decision = _decide(window, SPEND, SPEND_REASON, gap_series={})
    assert decision.outcome == "refused_window"
    assert decision.kind is None


def test_start_after_end_is_refused_window() -> None:
    decision = _decide(Window(date(2020, 12, 31), date(2020, 1, 31)))
    assert decision.outcome == "refused_window"


# --- holdout -----------------------------------------------------------------


@pytest.mark.parametrize(
    "window",
    [
        Window(date(2023, 6, 1), date(2024, 1, 31)),
        Window(date(2026, 8, 31), date(2026, 8, 31)),
        HOLDOUT,
    ],
)
def test_window_touching_the_holdout_without_the_flag_is_refused_holdout(window: Window) -> None:
    decision = _decide(window, gap_series=_gap(0.0))
    assert decision.outcome == "refused_holdout"
    assert decision.kind is None


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_spend_flag_without_a_reason_is_refused_holdout(reason: str | None) -> None:
    decision = _decide(HOLDOUT, SPEND, Reasons(holdout_reason=reason), gap_series=_gap(0.0))
    assert decision.outcome == "refused_holdout"
    assert "reason" in decision.message


def test_spend_with_a_reason_runs_as_a_holdout_trial() -> None:
    decision = _decide(HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.01))
    assert decision.outcome == "run"
    assert decision.kind == "holdout"
    assert decision.holdout_reason == SPEND_REASON.holdout_reason
    assert decision.holdout_repeat is False
    assert decision.gap_override_reason is None


def test_spend_flag_on_an_in_sample_window_is_an_ordinary_in_sample_run() -> None:
    decision = _decide(IN_SAMPLE, SPEND, SPEND_REASON)
    assert decision.outcome == "run"
    assert decision.kind == "in_sample"
    assert decision.holdout_reason is None
    assert "--spend-holdout ignored" in decision.message


def test_a_prior_family_spend_marks_repeat() -> None:
    decision = _decide(
        HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.0), prior_spends=(_spend(99),)
    )
    assert decision.outcome == "run"
    assert decision.holdout_repeat is True


def test_a_prior_spend_by_the_same_hypothesis_needs_the_repeat_flag() -> None:
    spends = (_spend(99, trial_id=1), _spend(HYPOTHESIS_ID, trial_id=2))
    refused = _decide(HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.0), prior_spends=spends)
    assert refused.outcome == "refused_holdout"
    assert "--holdout-repeat" in refused.message

    allowed = _decide(
        HOLDOUT,
        Flags(spend_holdout=True, holdout_repeat=True),
        SPEND_REASON,
        gap_series=_gap(0.0),
        prior_spends=spends,
    )
    assert allowed.outcome == "run"
    assert allowed.kind == "holdout"
    assert allowed.holdout_repeat is True


def test_in_sample_run_ignores_prior_spends() -> None:
    decision = _decide(IN_SAMPLE, prior_spends=(_spend(HYPOTHESIS_ID),))
    assert decision.outcome == "run"
    assert decision.holdout_repeat is False


def test_holdout_refusals_come_before_the_gap_series_is_needed() -> None:
    # No gap series: the window and holdout rules decide without any provider read.
    assert _decide(HOLDOUT).outcome == "refused_holdout"
    assert _decide(Window(date(2016, 1, 29), date(2017, 6, 30))).outcome == "refused_window"


# --- gap gate ----------------------------------------------------------------


def test_holdout_run_without_the_gap_series_needs_the_gap_reads_first() -> None:
    decision = _decide(HOLDOUT, SPEND, SPEND_REASON)
    assert decision.outcome == "needs_gap"
    assert decision.gap_sessions == HOLDOUT_GAP_SESSIONS


def test_in_sample_run_has_no_gap_gate() -> None:
    decision = _decide(IN_SAMPLE, gap_series=None)
    assert decision.outcome == "run"
    assert decision.gap_sessions == ()


def test_gap_above_threshold_at_one_date_is_refused_gap() -> None:
    decision = _decide(HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.01, d2024_02_29=0.051))
    assert decision.outcome == "refused_gap"
    assert decision.gap_breaches == ((date(2024, 2, 29), 0.051),)
    assert "2024-02-29" in decision.message


def test_gap_at_exactly_the_threshold_passes() -> None:
    decision = _decide(HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.05))
    assert decision.outcome == "run"
    assert decision.gap_breaches == ()


def test_nan_gap_share_counts_as_a_breach() -> None:
    decision = _decide(HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.0, d2024_01_31=math.nan))
    assert decision.outcome == "refused_gap"


def test_gap_override_with_a_reason_runs_and_records_the_reason() -> None:
    decision = _decide(
        HOLDOUT,
        Flags(spend_holdout=True, override_gap=True),
        Reasons(holdout_reason="spend", gap_reason="owner accepts the free-data bias"),
        gap_series=_gap(0.2),
    )
    assert decision.outcome == "run"
    assert decision.kind == "holdout"
    assert decision.gap_override_reason == "owner accepts the free-data bias"
    assert len(decision.gap_breaches) == len(HOLDOUT_GAP_SESSIONS)


def test_unused_gap_override_records_no_override() -> None:
    decision = _decide(
        HOLDOUT,
        Flags(spend_holdout=True, override_gap=True),
        Reasons(holdout_reason="spend", gap_reason="just in case"),
        gap_series=_gap(0.0),
    )
    assert decision.outcome == "run"
    assert decision.gap_override_reason is None


@pytest.mark.parametrize("reason", [None, ""])
def test_gap_override_flag_without_a_reason_is_refused_gap(reason: str | None) -> None:
    decision = _decide(
        HOLDOUT,
        Flags(spend_holdout=True, override_gap=True),
        Reasons(holdout_reason="spend", gap_reason=reason),
        gap_series=_gap(0.2),
    )
    assert decision.outcome == "refused_gap"
    assert "reason" in decision.message


def test_gap_series_missing_a_rebalance_session_is_an_error() -> None:
    series = _gap(0.0)
    del series[date(2024, 2, 29)]
    with pytest.raises(ValueError, match="2024-02-29"):
        _decide(HOLDOUT, SPEND, SPEND_REASON, gap_series=series)


def test_threshold_comes_from_the_frozen_parameters() -> None:
    strict = Frozen(
        hypothesis_id=HYPOTHESIS_ID,
        in_sample_start=FROZEN.in_sample_start,
        holdout_start=FROZEN.holdout_start,
        holdout_end=FROZEN.holdout_end,
        gap_count_share_threshold=0.005,
    )
    decision = decide(HOLDOUT, strict, SPEND, SPEND_REASON, _gap(0.01), ())
    assert decision.outcome == "refused_gap"


@pytest.mark.parametrize("threshold", [-0.01, 1.0, 2.0, math.nan, math.inf])
def test_frozen_refuses_a_threshold_outside_a_share(threshold: float) -> None:
    with pytest.raises(ValueError, match="finite share"):
        Frozen(
            hypothesis_id=1,
            in_sample_start=date(2017, 1, 31),
            holdout_start=date(2024, 1, 1),
            holdout_end=date(2026, 8, 31),
            gap_count_share_threshold=threshold,
        )


@pytest.mark.parametrize(
    ("in_sample_start", "holdout_start", "holdout_end"),
    [
        (date(2024, 1, 1), date(2024, 1, 1), date(2026, 8, 31)),
        (date(2017, 1, 31), date(2026, 8, 31), date(2024, 1, 1)),
    ],
)
def test_frozen_refuses_misordered_dates(
    in_sample_start: date, holdout_start: date, holdout_end: date
) -> None:
    with pytest.raises(ValueError, match="frozen dates"):
        Frozen(1, in_sample_start, holdout_start, holdout_end, 0.05)


# --- frozen parameters from the registry -------------------------------------


def _record(params: dict[str, Any]) -> HypothesisRecord:
    return HypothesisRecord(
        hypothesis_id=3,
        slug="h1",
        family="momentum",
        title="H1",
        doc_path="docs/hypotheses/h1.md",
        doc_sha256="0" * 64,
        params=params,
        params_sha256="1" * 64,
        in_sample_start=date(2017, 1, 31),
        holdout_start=date(2024, 1, 1),
        holdout_end=date(2026, 8, 31),
        registered_at=datetime(2026, 9, 25, tzinfo=UTC),
        registered_by="owner",
    )


def test_frozen_from_hypothesis_reads_the_registered_row() -> None:
    frozen = Frozen.from_hypothesis(_record({"gap.count_share_threshold": 0.03}))
    assert frozen == Frozen(
        hypothesis_id=3,
        in_sample_start=date(2017, 1, 31),
        holdout_start=date(2024, 1, 1),
        holdout_end=date(2026, 8, 31),
        gap_count_share_threshold=0.03,
    )


def test_frozen_from_hypothesis_ignores_the_live_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOLDOUT__START", "2020-01-01")
    monkeypatch.setenv("GAP__COUNT_SHARE_THRESHOLD", "0.9")
    frozen = Frozen.from_hypothesis(_record({"gap.count_share_threshold": 0.03}))
    assert frozen.holdout_start == date(2024, 1, 1)
    assert frozen.gap_count_share_threshold == 0.03


def test_frozen_from_hypothesis_refuses_a_missing_threshold() -> None:
    with pytest.raises(ValueError, match=r"gap\.count_share_threshold"):
        Frozen.from_hypothesis(_record({}))
