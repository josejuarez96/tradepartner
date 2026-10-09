"""Window, holdout and gap-gate decisions (backtest spec req 11; plan T36)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradepartner.backtest import hypothesis as hypothesis_module
from tradepartner.backtest import run as run_module
from tradepartner.backtest.frozen import FROZEN_KEY_DEFAULTS
from tradepartner.backtest.holdout import (
    Decision,
    Flags,
    Frozen,
    LabState,
    Reasons,
    Window,
    decide,
    default_in_sample_window,
    first_tracking_session,
    gap_sessions,
    is_forward,
    tracking_start,
    window_touches_holdout,
)
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.provider import DataProvider
from tradepartner.backtest.run import run_hypothesis
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Cadence, Settings
from tradepartner.store import registry
from tradepartner.store.db import open_for_write
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


# --- tracking windows (Phase 4 spec req 10; plan T53) ------------------------------

#: FROZEN's holdout ends on 2026-08-31, a month-end session; tracking starts at the
#: next rebalance session.
FIRST_TRACKING = date(2026, 9, 30)


def _track(window: Window, flags: Flags = NO_FLAGS, reasons: Reasons = NO_REASONS) -> Decision:
    return decide(window, FROZEN, flags, reasons, None, (_spend(HYPOTHESIS_ID),), tracking=True)


def test_first_tracking_session_is_the_rebalance_after_holdout_end() -> None:
    assert first_tracking_session(FROZEN, "month_end") == FIRST_TRACKING
    mid_month = Frozen(**{**FROZEN.__dict__, "holdout_end": date(2026, 8, 14)})
    assert first_tracking_session(mid_month, "month_end") == date(2026, 8, 31)
    year_end = Frozen(**{**FROZEN.__dict__, "holdout_end": date(2026, 12, 31)})
    assert first_tracking_session(year_end, "month_end") == date(2027, 1, 29)


@pytest.mark.parametrize(
    ("holdout_end", "cadence", "expected"),
    [
        # 2026-08-31 is a Monday: the week's last session is Friday 2026-09-04.
        (date(2026, 8, 31), "week_end", date(2026, 9, 4)),
        (date(2026, 8, 31), "daily", date(2026, 9, 1)),
        # 2026-08-14 is a Friday, itself a week end: the next one is 2026-08-21.
        (date(2026, 8, 14), "week_end", date(2026, 8, 21)),
        (date(2026, 8, 14), "daily", date(2026, 8, 17)),
        # 2026-09-04 is the Friday before Labor Day: the next session is Tuesday.
        (date(2026, 9, 4), "daily", date(2026, 9, 8)),
        # 2026-04-02 is the Thursday before Good Friday, the week's last session.
        (date(2026, 3, 31), "week_end", date(2026, 4, 2)),
    ],
)
def test_first_tracking_session_follows_the_cadence(
    holdout_end: date, cadence: Cadence, expected: date
) -> None:
    """ADR 0015 seam 4: the first rebalance session of the hypothesis's cadence
    strictly after `holdout.end`, not always a month end."""
    frozen = Frozen(**{**FROZEN.__dict__, "holdout_end": holdout_end})
    assert first_tracking_session(frozen, cadence) == expected


def test_a_tracking_window_reads_decides_cadence() -> None:
    """`_tracking` takes `decide`'s `cadence`: a week-end tracking window may start at
    the first week end after `holdout.end`, which a month-end hypothesis refuses."""
    window = Window(date(2026, 9, 4), date(2026, 12, 31))
    week = decide(window, FROZEN, NO_FLAGS, NO_REASONS, None, (), tracking=True, cadence="week_end")
    assert (week.outcome, week.kind) == ("run", "tracking")
    month = decide(window, FROZEN, NO_FLAGS, NO_REASONS, None, (), tracking=True)
    assert month.outcome == "refused_window"
    assert str(FIRST_TRACKING) in month.message


@pytest.mark.parametrize(
    "start",
    [date(2026, 8, 31), date(2026, 8, 3), date(2026, 9, 1), date(2026, 9, 29), date(2024, 1, 31)],
)
def test_a_tracking_window_starting_before_the_first_tracking_session_is_refused(
    start: date,
) -> None:
    """On or before `holdout.end`'s session, or between it and the next rebalance."""
    decision = _track(Window(start, date(2026, 12, 31)))
    assert (decision.outcome, decision.kind) == ("refused_window", None)
    assert "holdout.end" in decision.message


def test_a_tracking_window_after_holdout_end_runs_as_tracking_and_spends_nothing() -> None:
    """Even with a prior spend by the same hypothesis and the holdout flags set."""
    flags = Flags(spend_holdout=True, holdout_repeat=True, override_gap=True)
    reasons = Reasons(holdout_reason="not a spend", gap_reason="no gate")
    for window in (
        Window(FIRST_TRACKING, date(2027, 3, 31)),
        Window(date(2026, 11, 30), date(2026, 11, 30)),
    ):
        decision = _track(window, flags, reasons)
        assert (decision.outcome, decision.kind) == ("run", "tracking")
        assert decision.holdout_repeat is False
        assert decision.holdout_reason is None
        assert decision.gap_override_reason is None
        assert decision.gap_sessions == ()


def test_a_tracking_window_ending_before_its_start_is_refused() -> None:
    decision = _track(Window(date(2026, 11, 30), FIRST_TRACKING))
    assert decision.outcome == "refused_window"


def test_without_the_tracking_flag_a_post_holdout_window_is_still_refused() -> None:
    """The Phase 3 rule is unchanged: sessions after `holdout.end` belong to tracking."""
    decision = _decide(Window(FIRST_TRACKING, date(2027, 3, 31)))
    assert decision.outcome == "refused_window"


# --- cadence (strategy-lab spec req 6, "Holdout at cadence"; plan T98) ------------------

#: A holdout starting on a Wednesday: 2024-12-31 is the last session before it, the
#: Friday 2024-12-27 the last week end (its ISO week ends on 2025-01-03, inside it).
FROZEN_2025 = Frozen(
    hypothesis_id=1,
    in_sample_start=date(2017, 1, 31),
    holdout_start=date(2025, 1, 1),
    holdout_end=date(2026, 8, 31),
    gap_count_share_threshold=0.05,
)


@pytest.mark.parametrize(
    ("cadence", "end"),
    [
        ("month_end", date(2024, 12, 31)),
        ("week_end", date(2024, 12, 27)),
        ("daily", date(2024, 12, 31)),
    ],
)
def test_default_window_at_cadence_ends_before_holdout_start(cadence: Cadence, end: date) -> None:
    window = default_in_sample_window(FROZEN_2025, cadence)
    assert window == Window(FROZEN_2025.in_sample_start, end)
    assert end == rebalance_sessions(date(2024, 12, 1), date(2024, 12, 31), cadence)[-1]


def test_gap_sessions_at_week_end_are_the_week_ends_in_the_window() -> None:
    sessions = gap_sessions(HOLDOUT, "week_end")
    assert sessions == tuple(rebalance_sessions(HOLDOUT.start, HOLDOUT.end, "week_end"))
    assert date(2024, 3, 28) in sessions  # the Good Friday week ends on Thursday
    assert gap_sessions(Window(HOLDOUT.end, HOLDOUT.start), "week_end") == ()


#: Touches the holdout (from 2024-01-01) only at the week end 2024-01-05, which ends no
#: month: no month-end rebalance session of the window lies in the holdout.
WEEK_ONLY = Window(date(2023, 12, 1), date(2024, 1, 5))


def test_a_week_end_window_reaching_the_holdout_only_mid_month_is_refused_holdout() -> None:
    weekly = decide(WEEK_ONLY, FROZEN, NO_FLAGS, NO_REASONS, None, (), cadence="week_end")
    assert weekly.outcome == "refused_holdout"
    assert "--spend-holdout" in weekly.message
    # The same window at month_end reaches none of the holdout's rebalance sessions.
    monthly = decide(WEEK_ONLY, FROZEN, NO_FLAGS, NO_REASONS, None, ())
    assert monthly.outcome == "refused_window"
    assert "reaches none of its rebalance sessions" in monthly.message


def test_the_gap_gate_at_week_end_names_every_week_end_of_the_window() -> None:
    decision = decide(WEEK_ONLY, FROZEN, SPEND, SPEND_REASON, None, (), cadence="week_end")
    assert decision.outcome == "needs_gap"
    week_ends = tuple(rebalance_sessions(WEEK_ONLY.start, WEEK_ONLY.end, "week_end"))
    assert decision.gap_sessions == week_ends
    series = dict.fromkeys(week_ends, 0.0)
    with pytest.raises(ValueError, match="gap series has no value"):
        partial = dict.fromkeys(week_ends[:-1], 0.0)
        decide(WEEK_ONLY, FROZEN, SPEND, SPEND_REASON, partial, (), cadence="week_end")
    ran = decide(WEEK_ONLY, FROZEN, SPEND, SPEND_REASON, series, (), cadence="week_end")
    assert ran.outcome == "run"
    assert ran.gap_sessions == week_ends


class TestGapGateOnTheRunPath:
    """A `week_end` hypothesis's gap gate reads `survivorship_gap` at every week-end
    close of the window and nowhere else (recording provider, fixture store)."""

    SLUG = "h-weekly-gap"
    #: The fixture's gap count share is 0.0769 at 2019-06-28, a week end, so the frozen
    #: threshold 0.05 refuses the spend after the gate reads every week end.
    WINDOW = (date(2019, 5, 31), date(2019, 7, 12))

    @pytest.fixture
    def store(
        self, fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> Path:
        monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
        monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
        frozen = Settings(
            _env_file=None,
            strategy={"top_fraction": 0.5},
            schedule={"rebalance_cadence": "week_end"},
            holdout={"start": date(2019, 6, 3), "end": date(2020, 6, 30)},
            gap={"count_share_threshold": 0.05},
        )
        store = Settings(_env_file=None, store={"path": str(fixture_store_path)})
        with open_for_write(store) as conn:
            registry.register_hypothesis(
                conn,
                slug=self.SLUG,
                family="momentum",
                title="weekly gap gate",
                doc_path=f"docs/hypotheses/{self.SLUG}.md",
                doc_sha256="0" * 64,
                params=frozen_params_of(frozen, family="momentum"),
                in_sample_start=date(2018, 1, 31),
                holdout_start=date(2019, 6, 3),
                holdout_end=date(2020, 6, 30),
                registered_by="test",
                settings=frozen,
            )
        return fixture_store_path

    def test_reads_the_gap_at_every_week_end_close_and_nowhere_else(
        self, store: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, datetime]] = []

        class Recording(StoreProvider):
            pass

        def wrap(name: str) -> Any:
            def method(self: StoreProvider, *args: Any, **kwargs: Any) -> Any:
                calls.append((name, args[0]))
                return getattr(StoreProvider, name)(self, *args, **kwargs)

            return method

        for name in DataProvider.__dict__:
            if not name.startswith("_") and callable(getattr(StoreProvider, name, None)):
                setattr(Recording, name, wrap(name))
        monkeypatch.setattr(run_module, "StoreProvider", Recording)

        outcome = run_hypothesis(
            self.SLUG, *self.WINDOW, SPEND, store_path=store, reasons=SPEND_REASON
        )
        assert outcome.status == "refused_gap"
        week_ends = rebalance_sessions(*self.WINDOW, "week_end")
        assert len(week_ends) == 7
        assert calls == [("survivorship_gap", read_time(s, "week_end")) for s in week_ends]


# --- strategy-lab rules (spec req 5; plan T110) -------------------------------------

PRE_LAB = LabState(is_variant=False, is_pre_lab=True, promoted=False, max_family_holdout_spends=3)


def _lab(**overrides: Any) -> LabState:
    return replace(PRE_LAB, **overrides)


def _decide_lab(
    window: Window,
    lab: LabState | None,
    flags: Flags = SPEND,
    reasons: Reasons = SPEND_REASON,
    prior_spends: tuple[HoldoutSpend, ...] = (),
    tracking: bool = False,
) -> Decision:
    return decide(
        window, FROZEN, flags, reasons, _gap(0.0), prior_spends, tracking=tracking, lab=lab
    )


def _research_spend(run_id: int = 9) -> HoldoutSpend:
    return HoldoutSpend(
        trial_id=run_id,
        hypothesis_id=None,
        slug="exp-1",
        started_at=datetime(2026, 9, 2, tzinfo=UTC),
        synthetic=False,
        holdout_reason="research spend",
        status="ok",
        source="research_run",
    )


@pytest.mark.parametrize("window", [IN_SAMPLE, HOLDOUT])
@pytest.mark.parametrize("tracking", [False, True])
def test_a_variant_is_refused_before_any_other_rule(window: Window, tracking: bool) -> None:
    decision = _decide_lab(window, _lab(is_variant=True), tracking=tracking)
    assert (decision.outcome, decision.kind) == ("refused_variant", None)
    assert "sweep run" in decision.message


def test_without_lab_state_a_not_pre_lab_spend_runs_as_in_phase_3() -> None:
    """`lab=None` (a store without the lab tables) is the Phase 3 decision exactly."""
    for spends in [(), (_spend(HYPOTHESIS_ID + 1), _spend(HYPOTHESIS_ID + 2, trial_id=2))]:
        assert _decide_lab(HOLDOUT, None, prior_spends=spends) == _decide(
            HOLDOUT, SPEND, SPEND_REASON, gap_series=_gap(0.0), prior_spends=spends
        )


def test_a_spend_by_a_hypothesis_neither_pre_lab_nor_promoted_is_refused_holdout() -> None:
    decision = _decide_lab(HOLDOUT, _lab(is_pre_lab=False))
    assert (decision.outcome, decision.kind) == ("refused_holdout", None)
    assert "pre-lab or promoted" in decision.message


@pytest.mark.parametrize(
    "lab", [_lab(), _lab(is_pre_lab=False, promoted=True)], ids=["pre-lab", "promoted"]
)
def test_a_pre_lab_or_promoted_hypothesis_spends(lab: LabState) -> None:
    decision = _decide_lab(HOLDOUT, lab)
    assert (decision.outcome, decision.kind, decision.holdout_repeat) == ("run", "holdout", False)


def test_the_spend_gate_does_not_touch_an_in_sample_run() -> None:
    decision = _decide_lab(IN_SAMPLE, _lab(is_pre_lab=False, max_family_holdout_spends=1))
    assert (decision.outcome, decision.kind) == ("run", "in_sample")


def test_the_family_cap_refuses_a_spend_naming_the_prior_spends() -> None:
    prior = (_spend(HYPOTHESIS_ID + 1, trial_id=4),)
    decision = _decide_lab(HOLDOUT, _lab(max_family_holdout_spends=1), prior_spends=prior)
    assert (decision.outcome, decision.kind) == ("refused_holdout", None)
    assert "cap of 1 holdout spends" in decision.message
    assert "h8 (trial 4, ok)" in decision.message


def test_below_the_cap_a_family_repeat_runs_marked_holdout_repeat() -> None:
    prior = (_spend(HYPOTHESIS_ID + 1, trial_id=4),)
    decision = _decide_lab(HOLDOUT, _lab(max_family_holdout_spends=2), prior_spends=prior)
    assert (decision.outcome, decision.kind, decision.holdout_repeat) == ("run", "holdout", True)


def test_a_research_spend_counts_against_the_family_cap() -> None:
    decision = _decide_lab(
        HOLDOUT, _lab(max_family_holdout_spends=1), prior_spends=(_research_spend(),)
    )
    assert decision.outcome == "refused_holdout"
    assert "exp-1 (research run 9, ok)" in decision.message


def test_the_cap_counts_spends_of_any_outcome() -> None:
    failed = replace(_spend(HYPOTHESIS_ID + 1, trial_id=4), status="refused_gap")
    decision = _decide_lab(HOLDOUT, _lab(max_family_holdout_spends=1), prior_spends=(failed,))
    assert decision.outcome == "refused_holdout"
    assert "(trial 4, refused_gap)" in decision.message


def test_lab_state_refuses_a_cap_below_one() -> None:
    with pytest.raises(ValueError, match="positive"):
        _lab(max_family_holdout_spends=0)


# --- the development boundary (ADR 0016 points 2 and 4; plan T142) -------------------

#: The owner's boundary (answer 1 on #1320): the last practice rebalance of H1 and B3.
BOUNDARY = date(2023, 12, 29)
#: A boundary well inside FROZEN_2025's practice years (2022-06-15 is a Wednesday).
EARLY_BOUNDARY = date(2022, 6, 15)
HYPOTHESES = Path(__file__).resolve().parents[2] / "docs" / "hypotheses"


@pytest.mark.parametrize(
    ("cadence", "end"),
    [
        ("month_end", date(2022, 5, 31)),
        ("week_end", date(2022, 6, 10)),
        ("daily", date(2022, 6, 15)),
    ],
)
def test_the_boundary_shortens_the_default_window_at_each_cadence(
    cadence: Cadence, end: date
) -> None:
    window = default_in_sample_window(FROZEN_2025, cadence, EARLY_BOUNDARY)
    assert window == Window(FROZEN_2025.in_sample_start, end)
    assert end == rebalance_sessions(date(2022, 5, 1), EARLY_BOUNDARY, cadence)[-1]


@pytest.mark.parametrize("cadence", ["month_end", "week_end", "daily"])
def test_a_boundary_after_the_holdout_start_leaves_the_default_window(cadence: Cadence) -> None:
    """The earlier of the two ends: a later boundary never stretches the window."""
    later = default_in_sample_window(FROZEN_2025, cadence, date(2025, 6, 30))
    assert later == default_in_sample_window(FROZEN_2025, cadence)


def test_a_boundary_on_a_rebalance_session_is_the_default_window_end() -> None:
    """'On or before' the boundary: a boundary on a month end keeps that month end."""
    window = default_in_sample_window(FROZEN_2025, "month_end", date(2022, 6, 30))
    assert window.end == date(2022, 6, 30)


@pytest.mark.parametrize("slug", ["h1-momentum-12-1", "b3-gross-profitability"])
@pytest.mark.parametrize("cadence", ["month_end", "week_end", "daily"])
def test_h1_and_b3_default_windows_are_unchanged_by_the_owners_boundary(
    slug: str, cadence: Cadence
) -> None:
    """ADR 0016 open question 1: at 2023-12-29 no registered family's window moves."""
    parsed = hypothesis_module.parse_file(HYPOTHESES / f"{slug}.md")
    frozen = Frozen(
        hypothesis_id=1,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        gap_count_share_threshold=0.05,
    )
    assert default_in_sample_window(frozen, cadence, BOUNDARY) == default_in_sample_window(
        frozen, cadence
    )
    if cadence == "month_end":
        assert default_in_sample_window(frozen, cadence, BOUNDARY) == Window(
            date(2020, 8, 31), BOUNDARY
        )


def test_the_boundary_is_not_a_frozen_key() -> None:
    """It enters no fingerprint and no family rule (ADR 0016 point 1)."""
    keys = {key for key, _default, _version in FROZEN_KEY_DEFAULTS}
    assert not any("boundary" in key for key in keys)
    assert not any("boundary" in key for key in hypothesis_module.frozen_keys())


def test_a_boundary_before_in_sample_start_raises() -> None:
    before = FROZEN.in_sample_start - timedelta(days=1)
    with pytest.raises(ValueError, match="development boundary"):
        default_in_sample_window(FROZEN, "month_end", before)
    with pytest.raises(ValueError, match="development boundary"):
        decide(IN_SAMPLE, FROZEN, NO_FLAGS, NO_REASONS, None, (), boundary=before)


def test_a_boundary_leaving_no_rebalance_session_raises() -> None:
    """A boundary on in_sample_start's day but before its month's last session."""
    frozen = Frozen(**{**FROZEN.__dict__, "in_sample_start": date(2017, 1, 3)})
    with pytest.raises(ValueError, match="no rebalance session"):
        default_in_sample_window(frozen, "month_end", date(2017, 1, 3))


def _decide_bounded(
    window: Window,
    boundary: date | None = EARLY_BOUNDARY,
    flags: Flags = NO_FLAGS,
    reasons: Reasons = NO_REASONS,
    lab: LabState | None = None,
) -> Decision:
    return decide(window, FROZEN, flags, reasons, None, (), lab=lab, boundary=boundary)


@pytest.mark.parametrize(
    "window",
    [
        Window(date(2017, 1, 31), date(2022, 6, 16)),  # one day past the boundary
        Window(date(2017, 1, 31), date(2023, 12, 29)),  # today's default window
        Window(date(2023, 1, 31), date(2023, 6, 30)),  # wholly in the dead months
    ],
)
def test_an_in_sample_window_past_the_boundary_is_refused_window(window: Window) -> None:
    for flags in (NO_FLAGS, Flags(spend_holdout=True, override_gap=True, holdout_repeat=True)):
        decision = _decide_bounded(window, flags=flags, reasons=SPEND_REASON)
        assert (decision.outcome, decision.kind) == ("refused_window", None)
        assert "development boundary 2022-06-15" in decision.message
    # Without a boundary the same window runs in sample, as before T142.
    assert _decide_bounded(window, boundary=None).outcome == "run"


def test_an_in_sample_window_ending_on_the_boundary_runs() -> None:
    decision = _decide_bounded(Window(date(2017, 1, 31), EARLY_BOUNDARY))
    assert (decision.outcome, decision.kind) == ("run", "in_sample")


def test_the_boundary_refusal_comes_before_the_lab_rules_except_the_variant_rule() -> None:
    late = Window(date(2017, 1, 31), date(2023, 12, 29))
    decision = _decide_bounded(late, lab=_lab())
    assert decision.outcome == "refused_window"
    variant = _decide_bounded(late, lab=_lab(is_variant=True))
    assert variant.outcome == "refused_variant"


@pytest.mark.parametrize(
    "window",
    [
        Window(date(2017, 1, 31), date(2024, 3, 31)),  # practice through the holdout
        Window(date(2023, 12, 29), date(2024, 3, 31)),  # one session before holdout.start
    ],
)
def test_a_spend_window_starting_before_the_holdout_is_refused_window(window: Window) -> None:
    """ADR 0016 point 2: a spend covers only [holdout.start, holdout.end], with or
    without the flag, before the holdout rule and the gap gate are read."""
    for flags in (NO_FLAGS, SPEND):
        decision = _decide_bounded(window, flags=flags, reasons=SPEND_REASON)
        assert (decision.outcome, decision.kind) == ("refused_window", None)
        assert "before holdout.start 2024-01-01" in decision.message
    # Without a boundary today's rule spends it.
    before = _decide_bounded(window, boundary=None, flags=SPEND, reasons=SPEND_REASON)
    assert (before.outcome, before.kind) == ("needs_gap", "holdout")


def test_a_spend_window_inside_the_holdout_follows_the_holdout_rules() -> None:
    assert _decide_bounded(HOLDOUT).outcome == "refused_holdout"
    spend = _decide_bounded(HOLDOUT, flags=SPEND, reasons=SPEND_REASON)
    assert (spend.outcome, spend.kind) == ("needs_gap", "holdout")
    assert spend.gap_sessions == HOLDOUT_GAP_SESSIONS


def test_a_spend_window_past_holdout_end_is_still_refused_window() -> None:
    decision = _decide_bounded(
        Window(date(2024, 1, 1), date(2026, 9, 30)), flags=SPEND, reasons=SPEND_REASON
    )
    assert decision.outcome == "refused_window"
    assert "after holdout.end" in decision.message


@pytest.mark.parametrize(
    "window",
    [
        IN_SAMPLE,
        HOLDOUT,
        Window(date(2017, 1, 31), date(2024, 3, 31)),
        Window(date(2016, 1, 4), date(2020, 1, 31)),
        Window(date(2020, 1, 31), date(2019, 1, 31)),
        Window(date(2026, 9, 30), date(2027, 3, 31)),
    ],
)
@pytest.mark.parametrize("flags", [NO_FLAGS, SPEND])
def test_no_boundary_decides_exactly_as_before(window: Window, flags: Flags) -> None:
    """`boundary=None`, passed or omitted, is the pre-T142 rule."""
    implicit = decide(window, FROZEN, flags, SPEND_REASON, None, ())
    explicit = decide(window, FROZEN, flags, SPEND_REASON, None, (), boundary=None)
    assert implicit == explicit


def test_the_boundary_does_not_touch_a_tracking_window() -> None:
    window = Window(FIRST_TRACKING, date(2027, 3, 31))
    decision = decide(
        window, FROZEN, NO_FLAGS, NO_REASONS, None, (), tracking=True, boundary=EARLY_BOUNDARY
    )
    assert (decision.outcome, decision.kind) == ("run", "tracking")


# --- forward holdouts (ADR 0016 point 4) ------------------------------------------------

#: A family registered on 2026-10-09 whose holdout starts on 2026-11-02 (a Monday).
FORWARD = Frozen(
    hypothesis_id=11,
    in_sample_start=date(2020, 8, 31),
    holdout_start=date(2026, 11, 2),
    holdout_end=date(2027, 4, 30),
    gap_count_share_threshold=0.05,
    registered_on=date(2026, 10, 9),
)


@pytest.mark.parametrize(
    ("registered_on", "forward"),
    [
        (None, False),
        (date(2026, 10, 9), True),
        (date(2026, 11, 1), True),  # the day before: forward by one day (ADR 0016)
        (date(2026, 11, 2), False),  # the holdout's first day existed at registration
        (date(2026, 12, 1), False),
    ],
)
def test_is_forward_is_holdout_start_after_the_registration_day(
    registered_on: date | None, forward: bool
) -> None:
    assert is_forward(replace(FORWARD, registered_on=registered_on)) is forward


def test_h1_is_not_forward() -> None:
    """H1 registered in October 2026 with a holdout from 2024-01-01."""
    assert is_forward(replace(FROZEN, registered_on=date(2026, 10, 5))) is False


def test_frozen_from_hypothesis_takes_the_family_registration_day() -> None:
    record = _record({"gap.count_share_threshold": 0.03})
    assert Frozen.from_hypothesis(record).registered_on is None
    frozen = Frozen.from_hypothesis(record, registered_on=date(2023, 6, 1))
    assert frozen.registered_on == date(2023, 6, 1)
    assert is_forward(frozen)


@pytest.mark.parametrize(
    ("cadence", "first"),
    [
        ("month_end", date(2026, 11, 30)),
        ("week_end", date(2026, 11, 6)),
        ("daily", date(2026, 11, 2)),
    ],
)
def test_a_forward_holdouts_tracking_starts_at_the_first_rebalance_from_holdout_start(
    cadence: Cadence, first: date
) -> None:
    assert tracking_start(FORWARD, cadence) == first
    inside = Window(first, date(2027, 6, 30))
    decision = decide(
        inside, FORWARD, NO_FLAGS, NO_REASONS, None, (), tracking=True, cadence=cadence
    )
    assert (decision.outcome, decision.kind) == ("run", "tracking")
    early = decide(
        Window(first - timedelta(days=1), date(2027, 6, 30)),
        FORWARD,
        NO_FLAGS,
        NO_REASONS,
        None,
        (),
        tracking=True,
        cadence=cadence,
    )
    assert early.outcome == "refused_window"
    assert "on or after holdout.start 2026-11-02 (a forward holdout)" in early.message


def test_a_forward_holdout_starting_on_a_rebalance_session_tracks_from_that_session() -> None:
    frozen = replace(FORWARD, holdout_start=date(2026, 11, 30))
    assert tracking_start(frozen, "month_end") == date(2026, 11, 30)


def test_a_historical_holdouts_tracking_start_is_unchanged() -> None:
    for registered_on in (None, date(2026, 10, 5)):
        frozen = replace(FROZEN, registered_on=registered_on)
        for cadence in ("month_end", "week_end", "daily"):
            assert tracking_start(frozen, cadence) == first_tracking_session(FROZEN, cadence)
    without = replace(FORWARD, registered_on=None)
    assert tracking_start(without, "month_end") == date(2027, 5, 28)
    refused = decide(
        Window(date(2026, 11, 30), date(2027, 6, 30)),
        without,
        NO_FLAGS,
        NO_REASONS,
        None,
        (),
        tracking=True,
    )
    assert refused.outcome == "refused_window"
    assert "after holdout.end 2027-04-30" in refused.message


def test_a_forward_holdouts_in_sample_window_stops_at_the_boundary() -> None:
    """B4's case (ADR 0016 point 5): practice [2020-08-31, 2023-12-29], dead months to
    the holdout."""
    assert default_in_sample_window(FORWARD, "month_end", BOUNDARY) == Window(
        date(2020, 8, 31), BOUNDARY
    )
    dead = decide(
        Window(date(2020, 8, 31), date(2026, 9, 30)),
        FORWARD,
        NO_FLAGS,
        NO_REASONS,
        None,
        (),
        boundary=BOUNDARY,
    )
    assert dead.outcome == "refused_window"
    assert "development boundary" in dead.message
