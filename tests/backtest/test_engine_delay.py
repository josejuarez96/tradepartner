"""The kill ladder's rung-4 delay in the engine (backtest spec amendment #1820, item 1;
ADR 0023; plan T217c).

Locked tests (split trial, phase 1): written before the build, against the plan line's
Tests clause. Under `strategy.signal_delay_sessions = d > 0` the plan at rebalance
session T_i is built from the reads of its **read session** R_i, the latest rebalance
session at the cadence at or before the d-th XNYS session before T_i, read at
`read_time(R_i)`, exactly as R_i's own plan reads them; `Plan.session`, `fill_session`
and the fill stay T_i's, and the construction step reads T_i's book. So under `equal`
with band 0 the delayed plan is R_i's plan re-dated (`_redated`), field by field.

Three fixtures: the fixture store (`tests/fixtures/universe`, through `StoreProvider`) at
`daily` and, with B10's turnover screen (`harness.SCREENED_FIXTURE`'s `strategy` block),
at `month_end`; `test_engine_many`'s random-walk `FakeProvider` for the read groups and
`week_end`; and a hand-built `FakeProvider` for the `hold` case (T209b).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures
from lookahead.harness import FIXTURE_UNIVERSE, screened_strategy

from backtest import test_engine, test_engine_hold, test_engine_many
from backtest.fake_provider import FakeProvider
from tradepartner.backtest.engine import BacktestResult, Plan, plan, run, run_many
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Cadence, Settings
from tradepartner.store import registry, schema
from tradepartner.store.db import configure_connection

#: Every `Plan` field that comes from the reads (all but `session` and `fill_session`).
READ_FIELDS = (
    "members",
    "scores",
    "targets",
    "n_universe",
    "n_static_listings",
    "gap",
    "n_excluded_no_history",
    "excluded_no_history",
    "counts",
    "exclusions",
)


def _with(settings: Settings, section: str, **updates: Any) -> Settings:
    """`settings` with `section`'s fields replaced, validated as any construction is."""
    values = settings.model_dump()
    values[section] = {**values[section], **updates}
    return Settings(_env_file=None, **values)


def _delayed(settings: Settings, d: int) -> Settings:
    return _with(settings, "strategy", signal_delay_sessions=d)


def _rebalance_on_or_before(day: date, cadence: Cadence) -> date:
    """The latest rebalance session at `cadence` at or before `day`."""
    return rebalance_sessions(day - timedelta(days=45), day, cadence)[-1]


def _read_session(session: date, d: int, cadence: Cadence) -> date:
    """The spec's read session by hand: `d` XNYS sessions back, then back to a rebalance
    session (amendment #1820, item 1)."""
    day = session
    for _ in range(d):
        day = previous_session(day)
    return _rebalance_on_or_before(day, cadence)


def _redated(read: Plan, session: date, cadence: Cadence) -> Plan:
    return replace(read, session=session, fill_session=fill_session(session, cadence))


def _assert_redated(got: Plan, read: Plan, session: date, cadence: Cadence) -> None:
    """`got` (the delayed plan at `session`) is `read` (the read session's own plan)
    re-dated: every read field equal, `session` and `fill_session` the rebalance's."""
    assert got.session == session
    assert got.fill_session == fill_session(session, cadence)
    for name in READ_FIELDS:
        assert getattr(got, name) == getattr(read, name), (session, read.session, name)
    assert got == _redated(read, session, cadence)


# --- the defaults are byte-identical (pass on main; must stay green) -----------------


def test_h1_and_twin_fingerprint_canonical_set_and_hash_unchanged() -> None:
    test_engine_hold.test_h1_and_twin_fingerprint_canonical_set_and_hash_unchanged()


def test_twin_run_digests_and_final_equity_unchanged() -> None:
    test_engine_hold.test_twin_run_digests_and_final_equity_unchanged()


def test_twin_reference_metrics_unchanged() -> None:
    test_engine_hold.test_twin_reference_metrics_unchanged()


def test_a_delay_of_zero_named_explicitly_is_the_twin_run_to_the_last_bit() -> None:
    """d = 0 is today's call: the helper returns the rebalance session itself."""
    settings = _delayed(test_engine._twin_settings(), 0)
    results = test_engine_hold._twin_results(settings)
    digests = {level: test_engine._run_digest(result) for level, result in results.items()}
    assert digests == test_engine.TWIN_RUN_DIGESTS


# --- the read session (week_end on the random-walk fake) -----------------------------


@pytest.mark.parametrize(
    ("session", "d", "read"),
    [
        # Good Friday 2024-03-29 is closed, so its week ends on Thursday 03-28.
        (date(2024, 4, 5), 1, date(2024, 3, 28)),
        (date(2024, 4, 5), 4, date(2024, 3, 28)),
        (date(2024, 4, 5), 5, date(2024, 3, 28)),  # the 5th session back is 03-28 itself
        (date(2024, 4, 5), 6, date(2024, 3, 22)),  # 03-27, so the week end before
        (date(2024, 5, 3), 1, date(2024, 4, 26)),
    ],
)
def test_week_end_reads_the_latest_week_end_at_or_before_the_dth_prior_session(
    session: date, d: int, read: date
) -> None:
    assert _read_session(session, d, "week_end") == read  # the hand helper agrees
    base = test_engine_many._params("week_end")
    own = plan(test_engine_many._provider(), base, session, "momentum")
    expected = plan(test_engine_many._provider(), base, read, "momentum")
    assert any(getattr(own, name) != getattr(expected, name) for name in READ_FIELDS)
    got = plan(test_engine_many._provider(), _delayed(base, d), session, "momentum")
    _assert_redated(got, expected, session, "week_end")


# --- daily on the fixture store ------------------------------------------------------

#: The `daily` window of the look-ahead suite (`test_backtest_invariance.CASES`).
DAILY_START, DAILY_END = date(2019, 1, 2), date(2019, 2, 15)
#: The `month_end` window: the first rebalance (2019-01-31) reads 2018-12-31 under any
#: delay, before `start`; the fixture holds bars and members there
#: (`test_fixture_holds_the_first_read_session_before_start`).
MONTHLY_START, MONTHLY_END = date(2019, 1, 2), date(2019, 6, 28)
#: Sessions where the fixture's `daily` plan changes from the session before: a new
#: month's anchors (01-02, 02-01) and SEC_WINDOW_DELIST joining the universe (01-23).
DAILY_CHANGES = (date(2019, 1, 2), date(2019, 1, 23), date(2019, 2, 1))


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live ones.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@contextmanager
def open_fixture_store() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh in-memory fixture store: the schema and `tests/fixtures/universe`."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, FIXTURE_UNIVERSE)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    """One fixture store for the module (nothing here revises it)."""
    with open_fixture_store() as conn:
        yield conn


def _lend(conn: duckdb.DuckDBPyConnection) -> Any:
    @contextmanager
    def _factory() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    return _factory


#: One trial per (store, cadence); the connection is kept beside its handle so its `id`
#: is never reused by another module's store while the entry lives.
_HANDLES: dict[tuple[int, Cadence], tuple[duckdb.DuckDBPyConnection, registry.TrialHandle]] = {}


def _trial(conn: duckdb.DuckDBPyConnection, settings: Settings) -> registry.TrialHandle:
    """A synthetic trial on the module's store, one per (store, cadence)."""
    cadence = settings.schedule.rebalance_cadence
    key = (id(conn), cadence)
    if key not in _HANDLES:
        start, end = (
            (DAILY_START, DAILY_END) if cadence == "daily" else (MONTHLY_START, MONTHLY_END)
        )
        hypothesis = registry.register_hypothesis(
            conn,
            slug=f"h-delay-{cadence.replace('_', '-')}",
            family="momentum",
            title="delay tests",
            doc_path="docs/hypotheses/h-delay.md",
            doc_sha256="0" * 64,
            params={"costs.per_side_bps": 15.0},
            in_sample_start=start,
            holdout_start=date(2023, 1, 3),
            holdout_end=date(2025, 12, 31),
            registered_by="test",
            settings=settings,
        )
        _HANDLES[key] = (
            conn,
            registry.open_trial(
                conn,
                hypothesis_id=hypothesis.hypothesis_id,
                kind="in_sample",
                start_session=start,
                end_session=end,
                data_cutoff=read_time(end, cadence),
                synthetic=True,
                run_by="test",
                settings=settings,
            ),
        )
    return _HANDLES[key][1]


def _daily() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        schedule={"rebalance_cadence": "daily"},
        benchmarks=[],
    )


def _monthly_screened() -> Settings:
    return Settings(
        _env_file=None,
        strategy=screened_strategy(),
        schedule={"rebalance_cadence": "month_end"},
        benchmarks=[],
    )


def _store_plans(
    conn: duckdb.DuckDBPyConnection, settings: Settings, sessions: Sequence[date]
) -> dict[date, Plan]:
    with StoreProvider(_lend(conn), _trial(conn, settings), settings) as provider:
        return {s: plan(provider, settings, s, "momentum") for s in sessions}


def _store_run(conn: duckdb.DuckDBPyConnection, settings: Settings) -> dict[float, BacktestResult]:
    cadence = settings.schedule.rebalance_cadence
    start, end = (DAILY_START, DAILY_END) if cadence == "daily" else (MONTHLY_START, MONTHLY_END)
    handle = _trial(conn, settings)
    with StoreProvider(_lend(conn), handle, settings) as provider:
        return run(settings, provider, start, end, handle, (0.0, 15.0), family="momentum")


def test_fixture_holds_the_first_read_session_before_start(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """The first delayed read falls before `start` (2018-12-31 for both windows, and
    12-28 at `daily` with d = 2); the fixture has members, bars and scores there."""
    daily = _store_plans(store, _daily(), [date(2018, 12, 28), date(2018, 12, 31)])
    monthly = _store_plans(store, _monthly_screened(), [date(2018, 12, 31)])
    for read in (*daily.values(), *monthly.values()):
        assert read.members and read.scores and read.targets, read.session


def test_daily_fixture_plans_change_where_the_delay_would_show(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Precondition (the teeth): the undelayed plan differs from the previous session's
    at each of `DAILY_CHANGES`, so a delay that read at T_i would fail below."""
    sessions = rebalance_sessions(DAILY_START, DAILY_END, "daily")
    plans = _store_plans(store, _daily(), [date(2018, 12, 31), *sessions])
    for session in DAILY_CHANGES:
        before = plans[previous_session(session)]
        assert any(
            getattr(plans[session], name) != getattr(before, name) for name in READ_FIELDS
        ), session


@pytest.mark.parametrize("d", [1, 2])
def test_daily_delay_is_the_plan_d_sessions_back_re_dated(
    store: duckdb.DuckDBPyConnection, d: int
) -> None:
    """At `daily` the read session is exactly d sessions back; under `equal` with band 0
    the delayed plan is that session's plan with `session` and `fill_session` re-dated,
    field by field, at every rebalance of the window (the first reads before `start`)."""
    base = _daily()
    sessions = rebalance_sessions(DAILY_START, DAILY_END, "daily")
    reads = {s: _read_session(s, d, "daily") for s in sessions}
    expected = _store_plans(store, base, sorted(set(reads.values())))
    got = _store_plans(store, _delayed(base, d), sessions)
    for session in sessions:
        back = session
        for _ in range(d):
            back = previous_session(back)
        assert reads[session] == back
        _assert_redated(got[session], expected[reads[session]], session, "daily")


def test_daily_delay_run_trades_the_read_sessions_targets_at_t_i_s_fill(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Through the loop (`run`, so `_plan_all`): the targets filled at F_i are R_i's,
    and each rebalance row's plan counts and gap are R_i's, while the row stays dated
    T_i / F_i."""
    base = _daily()
    sessions = rebalance_sessions(DAILY_START, DAILY_END, "daily")
    planned = sessions[:-1]  # no plan at the last rebalance
    reads = {s: previous_session(s) for s in planned}
    expected = _store_plans(store, base, sorted(set(reads.values())))
    for result in _store_run(store, _delayed(base, 1)).values():
        assert [row.session for row in result.rebalances] == planned
        for row in result.rebalances:
            read = expected[reads[row.session]]
            assert row.fill_session == fill_session(row.session, "daily")
            assert result.targets[row.fill_session] == read.targets, row.session
            assert row.n_universe == read.n_universe
            assert row.n_static_listings == read.n_static_listings
            assert row.n_targets == len(read.targets)
            assert (row.gap_count_share, row.gap_size_share) == (
                read.gap.count_share,
                read.gap.size_share,
            )
            assert dict(row.counts) == dict(read.counts)


# --- month_end with B10's turnover screen on the fixture store -----------------------


def test_monthly_screened_fixture_preconditions(store: duckdb.DuckDBPyConnection) -> None:
    """The screen bites (names excluded `no_turnover`, `n_screened` reported) and every
    month-end's plan differs from the previous one's, so a delay has teeth here."""
    sessions = rebalance_sessions(MONTHLY_START, MONTHLY_END, "month_end")
    plans = _store_plans(store, _monthly_screened(), [date(2018, 12, 31), *sessions])
    ordered = [plans[s] for s in sorted(plans)]
    for previous, current in pairwise(ordered):
        assert current.targets != previous.targets or current.scores != previous.scores
    assert all(p.exclusions.get("no_turnover") for p in ordered)
    assert all(p.counts.get("n_screened", 0) > 0 for p in ordered)


@pytest.mark.parametrize("d", [1, 5])
def test_monthly_screened_delay_is_the_previous_month_ends_plan_re_dated(
    store: duckdb.DuckDBPyConnection, d: int
) -> None:
    """At `month_end` any d below a month's length reads the previous month-end, so d = 1
    and d = 5 give the same plan: the previous month-end's, screen and all, re-dated."""
    base = _monthly_screened()
    sessions = rebalance_sessions(MONTHLY_START, MONTHLY_END, "month_end")
    previous = [date(2018, 12, 31), *sessions[:-1]]
    assert [_read_session(s, d, "month_end") for s in sessions] == previous
    expected = _store_plans(store, base, previous)
    got = _store_plans(store, _delayed(base, d), sessions)
    for session, read in zip(sessions, previous, strict=True):
        _assert_redated(got[session], expected[read], session, "month_end")


def test_monthly_screened_delay_run_trades_the_previous_month_ends_targets(
    store: duckdb.DuckDBPyConnection,
) -> None:
    base = _monthly_screened()
    sessions = rebalance_sessions(MONTHLY_START, MONTHLY_END, "month_end")
    reads = dict(zip(sessions[:-1], [date(2018, 12, 31), *sessions[:-2]], strict=True))
    expected = _store_plans(store, base, sorted(reads.values()))
    for result in _store_run(store, _delayed(base, 1)).values():
        assert [row.session for row in result.rebalances] == sessions[:-1]
        for row in result.rebalances:
            read = expected[reads[row.session]]
            assert row.fill_session == fill_session(row.session, "month_end")
            assert result.targets[row.fill_session] == read.targets, row.session
            assert row.n_universe == read.n_universe
            assert dict(row.counts) == dict(read.counts)


# --- read groups: `run_many` at delay 1 equals separate runs -------------------------


@pytest.mark.parametrize(
    "window", list(test_engine_many.WINDOWS.values()), ids=list(test_engine_many.WINDOWS)
)
def test_run_many_at_delay_1_equals_separate_runs_and_reads_like_one(
    window: tuple[Cadence, date, date],
) -> None:
    """Two `top_fraction` variants at d = 1 equal two separate `run` calls to the last
    bit, and the group makes exactly one delayed run's provider calls at every read time:
    one shared pre-read per read session, none at an instant no variant uses."""
    cadence, start, end = window
    first = _delayed(test_engine_many._params(cadence, top_fraction=0.4), 1)
    second = _delayed(test_engine_many._params(cadence, top_fraction=0.8), 1)
    one = test_engine_many._provider()
    run(
        first,
        one,
        start,
        end,
        test_engine_many._handle(1),
        test_engine_many.LEVELS,
        family="momentum",
    )
    shared = test_engine_many._provider()
    out = run_many(
        test_engine_many._variants(first, second),
        shared,
        start,
        end,
        test_engine_many.LEVELS,
        family="momentum",
    )
    assert not out.failures
    assert test_engine_many._calls(shared) == test_engine_many._calls(one)
    test_engine_many._assert_same(out[1], test_engine_many._alone(first, 1, start, end))
    test_engine_many._assert_same(out[2], test_engine_many._alone(second, 2, start, end))
    undelayed = test_engine_many._alone(
        test_engine_many._params(cadence, top_fraction=0.4), 1, start, end
    )
    assert out[1][15.0].targets != undelayed[15.0].targets  # the delay does move the run


def test_run_many_mixing_delays_equals_separate_runs_and_reads_only_their_instants() -> None:
    """A group of d = 0 and d = 1 (one pre-read per distinct read session) equals the two
    separate runs, and every universe read is at an instant one of them reads."""
    cadence, start, end = test_engine_many.WINDOWS["month_end"]
    now = test_engine_many._params(cadence)
    late = _delayed(now, 1)
    shared = test_engine_many._provider()
    out = run_many(
        test_engine_many._variants(now, late),
        shared,
        start,
        end,
        test_engine_many.LEVELS,
        family="momentum",
    )
    assert not out.failures
    test_engine_many._assert_same(out[1], test_engine_many._alone(now, 1, start, end))
    test_engine_many._assert_same(out[2], test_engine_many._alone(late, 2, start, end))
    sessions = rebalance_sessions(start, end, cadence)
    reads = {read_time(s, cadence) for s in sessions[:-1]}
    reads |= {read_time(_read_session(s, 1, cadence), cadence) for s in sessions[:-1]}
    universe = {c.t for c in shared.calls if c.method == "universe"}
    assert universe == reads


# --- hold (T209b): T_i's live entries kept, open slots from R_i's ranking -------------

#: Ranked A > B > C > D > E by growth whenever members (momentum over any window).
HOLD_GROWTH = {"A": 0.004, "B": 0.003, "C": 0.002, "D": 0.001, "E": 0.0}
HOLD_START, HOLD_END = date(2024, 2, 1), date(2024, 2, 12)
HOLD_REBALANCES = rebalance_sessions(HOLD_START, HOLD_END, "daily")
FEB = {day: date(2024, 2, day) for day in (1, 2, 5, 6, 7, 8, 9, 12)}
#: The universe at each session's close. Under d = 1 the plan at T_i reads the session
#: before, so plan 0 (Feb 1) reads Jan 31.
HOLD_MEMBERS: Mapping[date, list[str]] = {
    date(2024, 1, 31): ["A"],
    FEB[1]: ["B", "C"],
    FEB[2]: ["C", "D"],
    FEB[5]: ["D", "E"],
    FEB[6]: ["E"],
    FEB[7]: ["E"],
    FEB[8]: ["E"],
    FEB[9]: ["E"],
    FEB[12]: ["E"],
}
#: Held after each fill, by hand (hold_sessions = 3, hold_target_names = 2, d = 1):
#: plan 0 reads [A] and enters A (one slot open, cash); plan 1 reads [B, C], keeps A live
#: and fills the open slot with B (T_i's own ranking, [C, D], would have entered C);
#: plan 2 keeps A, B; plan 3 exits A (entered at 0) and enters D from Feb 5's [D, E];
#: plan 4 exits B and enters E; plan 5 keeps D, E; plan 6 exits D, E is live.
HOLD_DELAYED_HELD = {
    FEB[2]: {"A"},
    FEB[5]: {"A", "B"},
    FEB[6]: {"A", "B"},
    FEB[7]: {"B", "D"},
    FEB[8]: {"D", "E"},
    FEB[9]: {"D", "E"},
    FEB[12]: {"E"},
}


def _hold_provider() -> FakeProvider:
    closes = test_engine_hold._closes(HOLD_GROWTH, {})
    return FakeProvider(
        prices=test_engine_hold._prices(closes), members=HOLD_MEMBERS, benchmarks={}
    )


def _hold_settings(d: int) -> Settings:
    settings = test_engine_hold._with(
        test_engine_hold._settings(strategy={"top_fraction": 0.5}),
        "strategy",
        weighting="hold",
        hold_sessions=3,
        hold_target_names=2,
    )
    return _delayed(settings, d)


def _hold_run(d: int) -> dict[float, BacktestResult]:
    return run(
        _hold_settings(d),
        _hold_provider(),
        HOLD_START,
        HOLD_END,
        test_engine_many._handle(1),
        (0.0, 15.0),
        family="momentum",
    )


def _held(result: BacktestResult, session: date) -> set[str]:
    values = result.position_values.filter(pl.col("session") == session)
    return {sid for sid, value in values.select("security_id", "value").iter_rows() if value > 0}


def test_hold_delay_fixture_differs_from_the_undelayed_run() -> None:
    """Precondition: at d = 0 plan 0 enters B and C (Feb 1's members), not A."""
    result = _hold_run(0)[0.0]
    assert _held(result, FEB[2]) == {"B", "C"}


def test_hold_delay_keeps_live_entries_and_fills_open_slots_from_the_read_ranking() -> None:
    for result in _hold_run(1).values():
        for fill, held in HOLD_DELAYED_HELD.items():
            assert _held(result, fill) == held, fill


def test_hold_delay_never_trades_a_live_entry() -> None:
    """0 bp: A, bought at the Feb 2 fill at half the equity, only drifts with its closes
    through Feb 6 while B enters beside it at the Feb 5 fill."""
    result = _hold_run(1)[0.0]
    closes = test_engine_hold._closes(HOLD_GROWTH, {})
    values = {
        (sid, day): value
        for sid, day, value in result.position_values.select(
            "security_id", "session", "value"
        ).iter_rows()
    }
    assert values[("A", FEB[2])] == pytest.approx(50_000.0, rel=1e-12)
    for day in (5, 6):
        growth = closes[("A", FEB[day])] / closes[("A", FEB[2])]
        assert values[("A", FEB[day])] == pytest.approx(50_000.0 * growth, rel=1e-12)


def test_hold_delay_plan_targets_are_the_read_sessions_candidates() -> None:
    """`Plan.targets` under `hold` are R_i's top `hold_target_names` (its candidates)."""
    result = _hold_run(1)[0.0]
    for session in HOLD_REBALANCES[:-1]:
        read = HOLD_MEMBERS[previous_session(session)]
        top = sorted(read, key=lambda sid: -HOLD_GROWTH[sid])[:2]
        expected = dict.fromkeys(sorted(top), 0.5)
        assert result.targets[fill_session(session, "daily")] == expected, session


def test_session_close_of_the_read_is_before_the_rebalance_close() -> None:
    """Sanity on the hand helper: every read session's close is before T_i's."""
    for session in HOLD_REBALANCES:
        assert session_close(_read_session(session, 1, "daily")) < session_close(session)
