"""No look-ahead under the rung-4 delay (backtest spec amendment #1820, item 1, and req 13;
plan T217c).

Locked tests (split trial, phase 1). Under `strategy.signal_delay_sessions = d > 0` the
plan at T_i reads only at t' = `read_time(R_i)`, its read session's close (R_i is the
latest rebalance session at or before the d-th XNYS session before T_i), never at T_i's
own t and never after it. So a fact first known in (t', t] -- a universe membership
change, a bar revision -- enters neither the delayed plan's members nor its scores,
while the undelayed plan at T_i (which reads at t) does see it: that is each case's
teeth.

`FakeProvider` cases (`test_engine_many`'s random walk) state "first known" exactly
(`members` by session close, a revision row's `known_at`); the fixture-store cases use
B10's screened month-end set-up (`test_engine_delay`) with a revision planted on a copy.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
from backtest import test_engine_delay, test_engine_many
from backtest.fake_provider import FakeProvider
from backtest.test_results import BENCHMARKS, GAPS, NAMES, SESSIONS, _prices

from lookahead.test_backtest_invariance import _revise_bar
from tradepartner.backtest.engine import Plan, plan, run
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.calendar import session_close
from tradepartner.config import Cadence, Settings

#: When a planted fact becomes known: an hour after close(R_i), inside (t', t].
AFTER_READ = timedelta(hours=1)
PLAN_ONLY = ("universe", "survivorship_gap", "static_listing_count")


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _delayed(settings: Settings, d: int) -> Settings:
    return test_engine_delay._delayed(settings, d)


# --- every read of a delayed plan is at t', none at or after t -----------------------


@pytest.mark.parametrize(
    ("cadence", "session", "d"),
    [
        ("daily", date(2024, 5, 1), 1),
        ("daily", date(2024, 5, 1), 2),
        ("week_end", date(2024, 5, 3), 1),
        ("week_end", date(2024, 4, 5), 6),
        ("month_end", date(2024, 4, 30), 1),
        ("month_end", date(2024, 4, 30), 5),
    ],
)
def test_every_read_of_a_delayed_plan_is_at_the_read_sessions_close(
    cadence: Cadence, session: date, d: int
) -> None:
    provider = test_engine_many._provider()
    plan(provider, _delayed(test_engine_many._params(cadence), d), session, "momentum")
    t_prime = read_time(test_engine_delay._read_session(session, d, cadence), cadence)
    assert t_prime < read_time(session, cadence)
    assert provider.calls, "the plan read nothing"
    assert provider.read_times() == {t_prime}
    assert {c.method for c in provider.calls} >= set(PLAN_ONLY)


@pytest.mark.parametrize(
    "window", list(test_engine_many.WINDOWS.values()), ids=list(test_engine_many.WINDOWS)
)
def test_a_delayed_run_plans_only_at_read_sessions_and_never_after_its_own_t(
    window: tuple[Cadence, date, date],
) -> None:
    """Over a whole run at d = 1, the plan-only reads (universe, gap, static listings)
    are made exactly at the read sessions' closes, and no read of any kind is after the
    last rebalance's close, the run's data cutoff."""
    cadence, start, end = window
    provider = test_engine_many._provider()
    settings = _delayed(test_engine_many._params(cadence), 1)
    run(settings, provider, start, end, test_engine_many._handle(1), (0.0,), family="momentum")
    planned = rebalance_sessions(start, end, cadence)[:-1]
    expected = {read_time(test_engine_delay._read_session(s, 1, cadence), cadence) for s in planned}
    for method in PLAN_ONLY:
        assert {c.t for c in provider.calls if c.method == method} == expected, method
    assert max(provider.read_times()) <= read_time(end, cadence)


# --- a membership change first known in (t', t] (FakeProvider) -----------------------

T_MONTH, R_MONTH = date(2024, 4, 30), date(2024, 3, 28)


def _members_with_newcomer() -> dict[date, list[str]]:
    """N, the best name by far, is a member only from T_MONTH's close on."""
    return {s: [*NAMES, *(["N"] if s >= T_MONTH else [])] for s in SESSIONS}


def _newcomer_prices() -> pl.DataFrame:
    base = _prices(7)
    a = base.filter(pl.col("security_id") == "A")
    n = a.with_columns(
        pl.lit("N").alias("security_id"),
        pl.col("open") * (1 + pl.int_range(pl.len()) * 0.01),
        pl.col("close") * (1 + pl.int_range(pl.len()) * 0.01),
    )
    return pl.concat([base, n])


def test_a_membership_change_known_in_t_prime_t_enters_neither_members_nor_scores() -> None:
    provider = FakeProvider(
        prices=_newcomer_prices(),
        members=_members_with_newcomer(),
        benchmarks=BENCHMARKS,
        gaps=GAPS,
    )
    base = test_engine_many._params("month_end")
    undelayed = plan(provider, base, T_MONTH, "momentum")
    assert "N" in undelayed.members and "N" in undelayed.targets  # the teeth
    got = plan(provider, _delayed(base, 1), T_MONTH, "momentum")
    assert "N" not in got.members
    assert "N" not in got.scores
    assert "N" not in got.targets
    test_engine_delay._assert_redated(
        got, plan(provider, base, R_MONTH, "momentum"), T_MONTH, "month_end"
    )


# --- a bar revision first known in (t', t] (FakeProvider) ----------------------------


def _one_month() -> Settings:
    """1-0 momentum: the anchors of T_MONTH are (R_MONTH, T_MONTH), so the undelayed
    plan reads R_MONTH's bar and a revision of it reaches its scores."""
    return test_engine_many._params("month_end", formation_months=1, skip_months=0)


def _revised_prices(known_at: datetime) -> pl.DataFrame:
    """B's bar on R_MONTH again, its close halved, known at `known_at`."""
    base = _prices(7)
    row = base.filter((pl.col("security_id") == "B") & (pl.col("session") == R_MONTH))
    revised = row.with_columns(
        pl.col("close") * 0.5, pl.lit(known_at).cast(base.schema["known_at"]).alias("known_at")
    )
    return pl.concat([base, revised])


def _revision_provider(known_at: datetime) -> FakeProvider:
    members = {s: list(NAMES) for s in SESSIONS}
    return FakeProvider(
        prices=_revised_prices(known_at), members=members, benchmarks=BENCHMARKS, gaps=GAPS
    )


def test_a_bar_revision_known_in_t_prime_t_enters_neither_members_nor_scores() -> None:
    t_prime = read_time(R_MONTH, "month_end")
    clean = test_engine_many._provider()
    revised = _revision_provider(t_prime + AFTER_READ)
    base = _one_month()
    # The teeth: the undelayed plan at T reads the revision and its scores move.
    assert (
        plan(revised, base, T_MONTH, "momentum").scores
        != plan(clean, base, T_MONTH, "momentum").scores
    )
    got = plan(revised, _delayed(base, 1), T_MONTH, "momentum")
    want = plan(clean, base, R_MONTH, "momentum")
    test_engine_delay._assert_redated(got, want, T_MONTH, "month_end")


def test_the_same_revision_known_before_t_prime_does_reach_the_delayed_plan() -> None:
    """Control: known an hour before close(R), the revision is the read session's own
    fact, so the delayed plan carries it (the delay moves the read, it filters nothing)."""
    t_prime = read_time(R_MONTH, "month_end")
    early = _revision_provider(t_prime - AFTER_READ)
    base = _one_month()
    got = plan(early, _delayed(base, 1), T_MONTH, "momentum")
    want = plan(_revision_provider(t_prime - AFTER_READ), base, R_MONTH, "momentum")
    test_engine_delay._assert_redated(got, want, T_MONTH, "month_end")
    clean = plan(test_engine_many._provider(), base, R_MONTH, "momentum")
    assert got.scores != clean.scores


# --- the fixture store: B10's screened month-end -------------------------------------

#: SEC_TRANSFER, scored at both month-ends, revised on its 2019-01-31 bar.
STORE_T, STORE_R, STORE_SID = date(2019, 2, 28), date(2019, 1, 31), "SEC_TRANSFER"
#: SEC_TRANSFER leaves the universe at the 2019-05-31 close (it is a member at 04-30).
LEAVE_T, LEAVE_R = date(2019, 5, 31), date(2019, 4, 30)


@pytest.fixture(scope="module")
def clean_store() -> Iterator[duckdb.DuckDBPyConnection]:
    with test_engine_delay.open_fixture_store() as conn:
        yield conn


@pytest.fixture(scope="module")
def revised_store() -> Iterator[duckdb.DuckDBPyConnection]:
    """The fixture store plus one bar revision known an hour after close(STORE_R)."""
    with test_engine_delay.open_fixture_store() as conn:
        known_at = session_close(STORE_R) + AFTER_READ
        _revise_bar(conn, STORE_SID, STORE_R, known_at, factor=1.5)
        yield conn


def _store_plan(conn: duckdb.DuckDBPyConnection, settings: Settings, session: date) -> Plan:
    return test_engine_delay._store_plans(conn, settings, [session])[session]


def test_store_bar_revision_known_in_t_prime_t_enters_neither_members_nor_scores(
    clean_store: duckdb.DuckDBPyConnection, revised_store: duckdb.DuckDBPyConnection
) -> None:
    base = test_engine_delay._monthly_screened()
    clean_now = _store_plan(clean_store, base, STORE_T)
    revised_now = _store_plan(revised_store, base, STORE_T)
    assert STORE_SID in clean_now.scores
    assert revised_now.scores[STORE_SID] != clean_now.scores[STORE_SID]  # the teeth
    got = _store_plan(revised_store, _delayed(base, 1), STORE_T)
    want = _store_plan(clean_store, base, STORE_R)
    test_engine_delay._assert_redated(got, want, STORE_T, "month_end")


def test_store_membership_change_in_t_prime_t_leaves_the_delayed_plans_members(
    clean_store: duckdb.DuckDBPyConnection,
) -> None:
    base = test_engine_delay._monthly_screened()
    assert STORE_SID not in _store_plan(clean_store, base, LEAVE_T).members  # the teeth
    got = _store_plan(clean_store, _delayed(base, 1), LEAVE_T)
    want = _store_plan(clean_store, base, LEAVE_R)
    assert STORE_SID in want.members
    test_engine_delay._assert_redated(got, want, LEAVE_T, "month_end")
