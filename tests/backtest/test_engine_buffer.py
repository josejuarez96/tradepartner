"""The engine applies the buy/hold buffer (backtest spec amendment #657, items 2 to 5;
plan T239).

Locked tests (split trial, phase 1): written before the build, against the plan line's
Tests clause. The first section is the pin: on `main`'s code, before any buffer wiring,
the momentum fixture twin's run digests, rebalance rows and reference metrics are
literals, and the runs with `strategy.hold_top_fraction` absent and with it equal to
`strategy.top_fraction` (normalised to 0.0) are byte-identical to them. The later
sections are the new behaviour: a hand fixture over three rebalances, the public
`plan` guard, and the successor-chain case.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import duckdb
import polars as pl
import pytest

from backtest import test_engine, test_engine_hold
from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult, plan, run
from tradepartner.backtest.results import metric_rows
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import all_sessions, last_session_of_month, session_close
from tradepartner.config import Settings
from tradepartner.store import registry, schema

# --- the fixture twin's pins, taken on `main`'s code (plan T239's first commit) --------


def _with(settings: Settings, section: str, **updates: Any) -> Settings:
    """`settings` with `section`'s fields replaced, validated as any construction is."""
    values = settings.model_dump()
    values[section] = {**values[section], **updates}
    return Settings(_env_file=None, **values)


#: SHA-256 over the twin's rebalance rows (each row's `columns()` plus its counts
#: mapping, floats rounded to nine places), taken on `main` before the buffer landed.
TWIN_REBALANCE_DIGESTS = {
    0.0: "3d07ef6b4fda8ffc57e8810f4aa7828c42859dceea6c617e694f26af7533d502",
    30.0: "669469ced894a1c17312c6c780b78aa963ac076e21d87b0ddedf65f199935b22",
    60.0: "9f1933fcf739b90f34da472a14d4a28c0eb2ccd20eedd78e38ea206f4bdb3c84",
    100.0: "54d4cb26ac2bc509a14092919460cc7740420e8477c6b5c7d86edf2573dacb88",
}


def _twin_results(settings: Settings) -> dict[float, BacktestResult]:
    levels = tuple(settings.costs.sensitivity_per_side_bps)
    return run(
        settings,
        test_engine._provider(),
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        levels,
        family="momentum",
    )


def _rebalances_digest(result: BacktestResult) -> str:
    payload = [
        test_engine._rounded({**row.columns(), "counts": row.counts}) for row in result.rebalances
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _twin_metrics_digest(settings: Settings) -> str:
    """`test_engine_hold`'s reference-metrics digest (the twin's benchmark provider)."""
    base = test_engine._price_rows()
    extra = test_engine_hold._prices(
        test_engine_hold._closes(test_engine_hold._BENCHMARK_GROWTH, {}, test_engine.SESSIONS)
    )
    provider = FakeProvider(
        prices=pl.concat([base, extra]),
        members={
            session: sorted(test_engine.GROWTH)
            for session in rebalance_sessions(test_engine.T0, test_engine.HISTORY_END)
        },
        benchmarks=test_engine_hold._BENCHMARKS,
    )
    levels = sorted({settings.costs.per_side_bps, *settings.costs.sensitivity_per_side_bps})
    results = run(
        settings,
        provider,
        test_engine.T0,
        test_engine.T4,
        test_engine._handle(),
        levels,
        family="momentum",
    )
    rows = metric_rows(results, settings, cadence="month_end", family="momentum")
    payload = sorted(
        (row.series, row.cost_per_side_bps, row.metric, test_engine._rounded(row.value))
        for row in rows
    )
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def _twin_settings_variants() -> Sequence[Settings]:
    """The twin with `strategy.hold_top_fraction` absent, and named equal to
    `strategy.top_fraction` (normalised to 0.0 by the validator): one hypothesis."""
    default = test_engine._twin_settings()
    assert default.strategy.hold_top_fraction == 0.0
    at_top = _with(default, "strategy", hold_top_fraction=default.strategy.top_fraction)
    assert at_top.strategy.hold_top_fraction == 0.0
    return (default, at_top)


def test_twin_run_digests_and_rebalance_rows_pinned_at_default_and_hold_top() -> None:
    """The twin's run digests and rebalance rows stay byte-identical with the key
    absent and with it equal to `top_fraction` (the plan line's pin)."""
    for settings in _twin_settings_variants():
        results = _twin_results(settings)
        digests = {level: test_engine._run_digest(result) for level, result in results.items()}
        assert digests == test_engine.TWIN_RUN_DIGESTS
        rebalances = {level: _rebalances_digest(result) for level, result in results.items()}
        assert rebalances == TWIN_REBALANCE_DIGESTS
        assert len(results[0.0].rebalances) == 4


def test_twin_reference_metrics_pinned_at_default_and_hold_top() -> None:
    for settings in _twin_settings_variants():
        assert _twin_metrics_digest(settings) == test_engine_hold.TWIN_REFERENCE_METRICS_SHA256


# --- the hand fixture: three rebalances (spec amendment #657, items 2 to 4) ----------

HISTORY_START, HISTORY_END = date(2022, 11, 1), date(2024, 4, 30)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]
T0, T1, T2, T3 = (
    date(2024, 1, 31),
    date(2024, 2, 29),
    date(2024, 3, 28),
    date(2024, 4, 30),
)
REBALANCES = rebalance_sessions(T0, T3, "month_end")
CAPITAL, PER_SIDE = 100_000.0, 15.0
#: The universe the runs target; `PRICE_NAMES` adds the chain successor E, whose rows a
#: four-name run never reads (it scores members alone).
NAMES = ("A", "B", "C", "D")
PRICE_NAMES = ("A", "B", "C", "D", "E")

#: The close `momentum` uses is the one at the two anchor bars alone; the marker sets them
#: far enough back (`formation_months=14`, `skip_months=13`) that every anchor precedes the
#: run, so every other session closes at `DEFAULT_CLOSE` and the book does not drift inside
#: a step. The anchors drive the ranking: A > B > C > D at T0 (top two {A, B}), then
#: C > D > A > B from T1, so A ranks third at T1 and T2 -- inside `hold` (three of four),
#: outside `top` (two) -- and is kept, while B ranks fourth, outside `hold`, and is sold.
DEFAULT_CLOSE = 100.0
_ANCHOR_CLOSES: dict[date, dict[str, float]] = {
    last_session_of_month(2022, 11): {"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0, "E": 100.0},
    last_session_of_month(2022, 12): {"A": 110.0, "B": 105.0, "C": 102.0, "D": 101.0, "E": 100.0},
    last_session_of_month(2023, 1): {"A": 132.0, "B": 110.0, "C": 204.0, "D": 141.0, "E": 125.0},
    last_session_of_month(2023, 2): {"A": 145.0, "B": 112.0, "C": 306.0, "D": 170.0, "E": 150.0},
}


def _anchor_close(session: date, sid: str) -> float:
    return _ANCHOR_CLOSES.get(session, {}).get(sid, DEFAULT_CLOSE)


def hand_prices(
    revisions: Mapping[tuple[str, date], tuple[float, datetime]] | None = None,
) -> pl.DataFrame:
    """The hand fixture's bars: `DEFAULT_CLOSE` apart from the anchor closes, plus any
    revision `(sid, session) -> (close, known_at)` as a later `known_at` row."""
    revisions = revisions or {}
    rows = []
    for sid in PRICE_NAMES:
        for session in SESSIONS:
            close = _anchor_close(session, sid)
            rows.append((sid, session, close, close, session_close(session)))
            if (sid, session) in revisions:
                revised, known_at = revisions[(sid, session)]
                rows.append((sid, session, revised, revised, known_at))
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


def hand_provider(
    revisions: Mapping[tuple[str, date], tuple[float, datetime]] | None = None,
    *,
    members: Mapping[date, Sequence[str]] | None = None,
    chain_pairs: tuple[tuple[str, str], ...] = (),
) -> FakeProvider:
    """The hand fixture's provider: the members at every rebalance session."""
    if members is None:
        members = {
            session: list(NAMES) for session in rebalance_sessions(T0, HISTORY_END, "month_end")
        }
    return FakeProvider(
        prices=hand_prices(revisions), members=members, benchmarks={}, chain_pairs=chain_pairs
    )


def hand_settings(hold: float) -> Settings:
    """The hand fixture's frozen settings: four names, so `k = 2` and `h = 3`."""
    return Settings(
        _env_file=None,
        capital=CAPITAL,
        schedule={"rebalance_cadence": "month_end"},
        strategy={
            "formation_months": 14,
            "skip_months": 13,
            "top_fraction": 0.5,
            "hold_top_fraction": hold,
        },
        costs={"per_side_bps": PER_SIDE, "sensitivity_per_side_bps": [0.0]},
    )


def hand_run(hold: float, provider: FakeProvider, end: date = T3) -> dict[float, BacktestResult]:
    return run(
        hand_settings(hold),
        provider,
        T0,
        end,
        test_engine._handle(),
        (0.0, PER_SIDE),
        family="momentum",
    )


def _targets(result: BacktestResult) -> dict[date, list[str]]:
    return {fill: sorted(names) for fill, names in result.targets.items()}


def test_the_hand_fixture_keeps_a_name_between_top_and_hold_and_sells_one_below() -> None:
    """At T0 the top two are A and B; from T1 the ranking is C > D > A > B, so A ranks
    third (inside `hold`, outside `top`) and is kept, and B ranks fourth (outside `hold`)
    and is sold. Without the buffer the same fills target C and D alone."""
    buffered = _targets(hand_run(0.75, hand_provider())[0.0])
    plain = _targets(hand_run(0.0, hand_provider())[0.0])
    assert buffered == {
        fill_session(T0, "month_end"): ["A", "B"],
        fill_session(T1, "month_end"): ["A", "C", "D"],
        fill_session(T2, "month_end"): ["A", "C", "D"],
    }
    assert plain == {
        fill_session(T0, "month_end"): ["A", "B"],
        fill_session(T1, "month_end"): ["C", "D"],
        fill_session(T2, "month_end"): ["C", "D"],
    }


def test_the_hand_fixture_turnover_and_cost_are_hand_values() -> None:
    """The three rebalances at 0 bp are 0.5 (buy two halves from cash), 2/3 (sell B
    whole, trim A from 1/2 to 1/3, buy C and D at 1/3) and 0.0 (nothing moves). Without
    the buffer the second is 1.0 (sell A and B whole, buy C and D). At 15 bp each trade
    costs its notional times 15/10_000, so `cost_paid` is the traded notional -- turnover
    times twice the equity before the fill -- at 15 bp."""
    buffered = hand_run(0.75, hand_provider())
    plain = hand_run(0.0, hand_provider())
    assert [row.turnover for row in buffered[0.0].rebalances] == pytest.approx(
        [0.5, 2 / 3, 0.0], rel=1e-9
    )
    assert [row.turnover for row in plain[0.0].rebalances] == pytest.approx(
        [0.5, 1.0, 0.0], rel=1e-9
    )
    # At 15 bp each buy is shaded by its own cost: with cash h = 100_000 and rate r the
    # buy notional is h / (1 + r) and its cost is that times r. Step 0 buys two halves, so
    # equity after it is 100_000 minus step 0's cost; the step-1 hand value follows.
    rate = PER_SIDE / 10_000
    cost0 = 2 * (CAPITAL / 2 / (1 + rate)) * rate
    equity1 = CAPITAL - cost0
    rows = buffered[PER_SIDE].rebalances
    assert rows[0].cost_paid == pytest.approx(cost0, rel=1e-12)
    assert rows[1].turnover == pytest.approx(2 / 3, abs=0.002)
    assert rows[1].cost_paid == pytest.approx(2 * equity1 * rows[1].turnover * rate, rel=1e-9)
    assert buffered[0.0].rebalances[2].turnover == 0.0
    assert rows[2].turnover == pytest.approx(0.0, abs=0.002)


def test_n_retained_lands_in_trial_rebalance_counts_only_with_the_buffer() -> None:
    """`n_retained` is reported only when the buffer is on (item 2): absent from a
    default run's rebalance counts, and present by session when named."""
    for hold in (0.75, 0.0):
        with duckdb.connect(":memory:") as conn:
            schema.init_schema(conn)
            handle = _registered_handle(conn, hand_settings(hold))
            result = hand_run(hold, hand_provider())[PER_SIDE]
            registry.write_rebalances(conn, handle, result.rebalances)
            counts = registry.rebalance_counts(conn, handle.trial_id)
        for row in result.rebalances:
            assert ("n_retained" in row.counts) == (hold != 0.0)
            assert ("n_retained" in counts[row.session]) == (hold != 0.0)
        if hold != 0.0:
            assert [counts[row.session]["n_retained"] for row in result.rebalances] == [0, 1, 1]


def test_a_chain_renamed_held_name_between_top_and_hold_is_retained() -> None:
    """A is held at T0; the chains rename A to E, which scores 0.25 at T1 (between D's
    0.396 and B's 0.048), so E ranks third -- inside `hold`, outside `top` -- and is
    retained: the T1 target set is C, D and E."""
    members = {
        session: (["A", "B", "C", "D"] if session == T0 else ["E", "B", "C", "D"])
        for session in rebalance_sessions(T0, HISTORY_END, "month_end")
    }
    provider = hand_provider(members=members, chain_pairs=(("A", "E"),))
    result = hand_run(0.75, provider)[PER_SIDE]
    assert _targets(result)[fill_session(T1, "month_end")] == ["C", "D", "E"]
    assert result.rebalances[1].counts["n_retained"] == 1


def test_plan_without_previous_targets_raises_for_a_buffered_registration() -> None:
    """The public `plan` refuses a buffered registration whose `previous_targets` is
    omitted, naming the key; an explicit empty mapping is a run's first plan."""
    provider = hand_provider()
    with pytest.raises(ValueError, match=r"strategy\.hold_top_fraction"):
        plan(provider, hand_settings(0.75), T0, "momentum")
    first = plan(provider, hand_settings(0.75), T0, "momentum", previous_targets=())
    assert sorted(first.targets) == ["A", "B"]
    assert first.counts["n_retained"] == 0
    held = plan(provider, hand_settings(0.75), T1, "momentum", previous_targets=("A", "B"))
    assert sorted(held.targets) == ["A", "C", "D"]
    assert held.counts["n_retained"] == 1


def test_plan_is_unchanged_at_the_default() -> None:
    """With `strategy.hold_top_fraction` at its default `plan` takes no new argument and
    returns the unbuffered plan it always did."""
    provider = hand_provider()
    planned = plan(provider, hand_settings(0.0), T1, "momentum")
    assert sorted(planned.targets) == ["C", "D"]
    assert "n_retained" not in planned.counts
    assert engine._apply_buffer(planned, hand_settings(0.0), None) == planned


def _registered_handle(conn: Any, params: Settings) -> registry.TrialHandle:
    """A synthetic `momentum` trial on a scratch store at base 15 bp."""
    settings = Settings(_env_file=None)
    hypothesis = registry.register_hypothesis(
        conn,
        slug="t239-hand",
        family="momentum",
        title="T239 hand fixture",
        doc_path="docs/hypotheses/t239.md",
        doc_sha256="0" * 64,
        params={registry.BASE_COST_KEY: params.costs.per_side_bps},
        in_sample_start=date(2010, 1, 1),
        holdout_start=date(2025, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=T0,
        end_session=T3,
        data_cutoff=None,
        synthetic=True,
        run_by="test",
        settings=settings,
    )
