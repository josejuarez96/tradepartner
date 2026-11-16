"""A split known after its ex-date, or re-dated by the vendor (#3096, Audit A s03).

Every step before the first read that knows a split marked the ex-date with the splits
it knew, so a position carried across the ex-date booked the raw price jump. The first
step whose read knows it re-bases the carried value by the provider's `late_splits`
ratio (shares x ratio on the ex-date, as the broker books it) before it carries,
fills or values anything, and counts `n_late_splits`. Equity rows already written are
not restated. When a fill or an exit has already used the unrepaired value, no
re-base can make the book tradable, and the trial is refused.

The fake does no adjustment, so `_frame` builds the adjusted bar revisions each split
state implies: one row per bar at its own `known_at`, then a revision at every later
split `known_at`. No rebalance read falls between a split's `known_at` and its ex-date
here, the one case the fake's frame cannot model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast

import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.calendar import all_sessions, previous_session, session_close
from tradepartner.config import Settings
from tradepartner.store import registry

T0, T1, T2, T3 = date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28), date(2024, 4, 30)
F0, F1 = fill_session(T0), fill_session(T1)
SESSIONS = [s for s in all_sessions() if date(2022, 12, 1) <= s <= date(2024, 5, 31)]
LEVEL = 10.0
EX = date(2024, 2, 15)
#: The s03 shape: first known after close(T1), inside step 1's window.
LATE = datetime(2024, 3, 4, 21, 30, tzinfo=UTC)
#: Known after close(T2): step 1's fill on F1 has already used the unrepaired value.
TOO_LATE = datetime(2024, 4, 2, 21, 30, tzinfo=UTC)
ON_TIME = datetime(2024, 2, 14, 21, 30, tzinfo=UTC)

Split = tuple[str, str, date, float, datetime]  # security, action id, ex-date, ratio, known_at
#: A `Split` revision with this ratio cancels the action (`cancelled` on the fake's row).
CANCEL = 0.0


def _handle() -> registry.TrialHandle:
    return registry._issue_handle(
        trial_id=1,
        hypothesis_id=1,
        family="momentum",
        params_sha256="0" * 64,
        kind="in_sample",
        synthetic=True,
        started_at=datetime(2024, 6, 1, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
    )


def _params() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 1.0},
        costs={"per_side_bps": LEVEL, "sensitivity_per_side_bps": [0.0]},
    )


def _raw_close(sid: str, session: date, jumps: dict[str, tuple[date, float, float]]) -> float:
    ex, before, after = jumps[sid]
    return before if session < ex else after


def _frame(
    jumps: dict[str, tuple[date, float, float]], splits: Sequence[Split]
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """`(adjusted, raw, split_rows)`: raw closes step from `before` to `after` on the
    jump date, and each adjusted revision divides a bar before a live split's ex-date by
    its ratio, the store's rule."""
    rows = pl.DataFrame(
        [
            (sid, action, ex, ratio, ratio == CANCEL, known)
            for sid, action, ex, ratio, known in splits
        ],
        schema={
            "security_id": pl.Utf8,
            "action_id": pl.Utf8,
            "ex_date": pl.Date,
            "ratio": pl.Float64,
            "cancelled": pl.Boolean,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )
    times = sorted({known for *_, known in splits})

    def live(at: datetime) -> list[tuple[str, date, float]]:
        latest: dict[tuple[str, str], tuple[datetime, date, float]] = {}
        for sid, action, ex, ratio, known in splits:
            if known <= at and ((sid, action) not in latest or latest[(sid, action)][0] < known):
                latest[(sid, action)] = (known, ex, ratio)
        return [(sid, ex, ratio) for (sid, _), (_, ex, ratio) in latest.items() if ratio != CANCEL]

    adjusted, raw = [], []
    for sid in sorted(jumps):
        for session in SESSIONS:
            close = _raw_close(sid, session, jumps)
            bar_known = session_close(session)
            raw.append((sid, session, close, close, 1e6, bar_known))
            for at in [bar_known, *(k for k in times if k > bar_known)]:
                factor = math.prod(
                    1 / ratio for s, ex, ratio in live(at) if s == sid and session < ex
                )
                adjusted.append((sid, session, close * factor, close * factor, at))
    schema = {
        "security_id": pl.Utf8,
        "session": pl.Date,
        "open": pl.Float64,
        "close": pl.Float64,
        "known_at": pl.Datetime("us", "UTC"),
    }
    raw_schema = {**{k: v for k, v in schema.items() if k != "known_at"}, "volume": pl.Float64}
    raw_schema["known_at"] = schema["known_at"]
    return (
        pl.DataFrame(adjusted, schema=schema, orient="row"),
        pl.DataFrame(raw, schema=raw_schema, orient="row"),
        rows,
    )


#: s03: A splits 2:1 (100 -> 50), B reverse-splits 1:10 (5 -> 50), both ex 02-15.
S03 = {"A": (EX, 100.0, 50.0), "B": (EX, 5.0, 50.0)}


def _s03(known: datetime | None) -> list[Split]:
    if known is None:
        return []
    return [("A", "a", EX, 2.0, known), ("B", "b", EX, 0.1, known)]


def _provider(
    jumps: dict[str, tuple[date, float, float]],
    splits: Sequence[Split],
    benchmarks: dict[str, str] | None = None,
) -> FakeProvider:
    adjusted, raw, split_rows = _frame(jumps, splits)
    members = {
        session: sorted(set(jumps) - set((benchmarks or {}).values()))
        for session in rebalance_sessions(T0, date(2024, 5, 31))
    }
    return FakeProvider(
        prices=adjusted, raw=raw, split_rows=split_rows, members=members,
        benchmarks=benchmarks or {},
    )  # fmt: skip


def _run(provider: FakeProvider, end: date) -> BacktestResult:
    return run(_params(), provider, T0, end, _handle(), (LEVEL,), family="momentum")[LEVEL]


def _equity(result: BacktestResult, series: str = "strategy") -> dict[date, float]:
    return {row.session: row.equity for row in result.equity if row.series == series}


def _late_counts(result: BacktestResult) -> dict[date, int | None]:
    return {row.session: row.counts.get("n_late_splits") for row in result.rebalances}


def test_a_late_split_is_rebased_at_the_first_step_that_knows_it() -> None:
    """s03: the late book equals the on-time book from F1 on (TP was +424%, tradable
    -0.10%); the period-0 rows keep the booked jump (equity is never restated), and the
    step that knows the two splits counts them."""
    late = _run(_provider(S03, _s03(LATE)), T2)
    on_time = _run(_provider(S03, _s03(ON_TIME)), T2)
    e_late, e_on = _equity(late), _equity(on_time)
    assert set(e_late) == set(e_on)
    for session in e_late:
        if session >= F1:
            assert e_late[session] == pytest.approx(e_on[session], rel=1e-12), session
    assert e_on[T2] == pytest.approx(e_on[F0], rel=1e-2)  # flat to costs
    assert e_late[T1] > 4 * e_on[T1]  # step 0 booked the raw jump; its rows stand
    assert _late_counts(late) == {T0: None, T1: 2}
    assert _late_counts(on_time) == {T0: None, T1: None}


def test_a_vendor_re_date_undoes_the_phantom_split() -> None:
    """FMFC's shape: a 1:10 known on time for 02-09 that never happened, re-dated (the
    same action) to 03-13 where the price really jumps, known 03-14. Step 0 booked the
    phantom -90%; step 1 knows the re-date and multiplies by 10 before its fill, so the
    book equals one that only ever knew the real split."""
    jumps = {"A": (EX, 100.0, 100.0), "B": (date(2024, 3, 13), 5.0, 50.0)}
    phantom = datetime(2024, 2, 8, 21, 30, tzinfo=UTC)
    redated = [
        ("B", "b", date(2024, 2, 9), 0.1, phantom),
        ("B", "b", date(2024, 3, 13), 0.1, datetime(2024, 3, 14, 21, 30, tzinfo=UTC)),
    ]
    real = [("B", "b", date(2024, 3, 13), 0.1, datetime(2024, 3, 12, 21, 30, tzinfo=UTC))]
    got = _run(_provider(jumps, redated), T3)
    want = _run(_provider(jumps, real), T3)
    e_got, e_want = _equity(got), _equity(want)
    for session in e_got:
        if session >= F1:
            assert e_got[session] == pytest.approx(e_want[session], rel=1e-12), session
    assert e_got[T1] < 0.6 * e_want[T1]  # the phantom -90% on B's half stands in step 0
    assert _late_counts(got)[T1] == 1


def test_a_late_split_after_a_fill_used_the_unrepaired_value_refuses_the_trial() -> None:
    """Known only after close(T2): the fill on F1 was sized from the booked jump, so no
    re-base can make the book tradable; the run fails closed, naming the split."""
    with pytest.raises(ValueError, match=r"late split.*A.*2024-02-15"):
        _run(_provider(S03, _s03(TOO_LATE)), T3)
    # The same rows read through T2 only are not yet known: nothing to refuse.
    _run(_provider(S03, _s03(TOO_LATE)), T2)


def test_a_late_split_on_a_benchmark_is_rebased() -> None:
    """A buy-and-hold benchmark has no fill after F0, so the re-base is exact."""
    jumps = {**S03, "S": (EX, 100.0, 50.0)}
    late = _run(_provider(jumps, [("S", "s", EX, 2.0, LATE)], {"SPY": "S"}), T2)
    on_time = _run(_provider(jumps, [("S", "s", EX, 2.0, ON_TIME)], {"SPY": "S"}), T2)
    e_late, e_on = _equity(late, "SPY"), _equity(on_time, "SPY")
    assert e_late[T2] == pytest.approx(e_on[T2], rel=1e-12)
    assert e_late[T1] == pytest.approx(e_on[T1] / 2, rel=1e-9)


def test_no_look_ahead_the_step_before_the_split_is_known_never_sees_it() -> None:
    """Through close(T1) a split known 03-04 is invisible: a run to T1, and the prefix
    and T1 plan of a longer run, equal those of a store that never learns the split, and
    every read time is a rebalance close."""
    never = _provider(S03, _s03(None))
    late = _provider(S03, _s03(LATE))
    short_never, short_late = _run(never, T1), _run(late, T1)
    assert short_late.equity == short_never.equity
    assert short_late.rebalances == short_never.rebalances
    longer, longer_never = _run(_provider(S03, _s03(LATE)), T2), _run(_provider(S03, []), T2)
    assert longer.targets[F1] == longer_never.targets[F1]
    assert [row for row in longer.equity if row.session <= T1] == list(short_late.equity)
    probe = _provider(S03, _s03(LATE))
    _run(probe, T2)
    reads = [call for call in probe.calls if call.method == "late_splits"]
    assert reads and all(call.t_prev is not None and call.t_prev < call.t for call in reads)
    assert probe.read_times() <= {read_time(t) for t in rebalance_sessions(T0, T2)}


def test_a_split_cancelled_after_its_ex_date_is_undone() -> None:
    """A 2:1 known in time for 02-15 that never happened (the price never moves),
    cancelled 03-04: step 0 booked a fake +100% on A; step 1 multiplies by 1/2 before
    its fill, so the book equals one that never knew the split."""
    jumps = {"A": (EX, 100.0, 100.0), "B": (EX, 5.0, 5.0)}
    cancelled = [("A", "a", EX, 2.0, ON_TIME), ("A", "a", EX, CANCEL, LATE)]
    got = _run(_provider(jumps, cancelled), T2)
    want = _run(_provider(jumps, []), T2)
    e_got, e_want = _equity(got), _equity(want)
    for session in e_got:
        if session >= F1:
            assert e_got[session] == pytest.approx(e_want[session], rel=1e-12), session
    assert e_got[T1] > 1.4 * e_want[T1]
    assert _late_counts(got)[T1] == 1


def _book(
    positions: dict[str, float],
    marked: dict[str, date],
    held: dict[str, set[date]],
    fills: Sequence[date],
) -> engine._Book:
    book = engine._Book(level=LEVEL, cash=0.0, positions=dict(positions))
    book.marked_at, book.held_on = dict(marked), {sid: set(days) for sid, days in held.items()}
    book.rebalances = cast(Any, [SimpleNamespace(fill_session=day) for day in fills])
    return book


def _late(*rows: tuple[str, date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        list(rows),
        schema={"security_id": pl.Utf8, "ex_date": pl.Date, "ratio": pl.Float64},
        orient="row",
    )


HELD = {previous_session(EX), EX, T1}


class TestRebaseBranches:
    """`_rebase_late_splits`' branches one by one (quant-auditor, #3112)."""

    def test_held_across_with_no_fill_since_is_multiplied(self) -> None:
        book = _book({"A": 500.0}, {"A": T1}, {"A": HELD}, [F0])
        assert engine._rebase_late_splits(book, _late(("A", EX, 2.0)), signed=False) == 1
        assert book.positions == {"A": 1000.0}

    def test_a_mark_before_the_ex_date_is_counted_not_rebased(self) -> None:
        """A halted name last marked before the ex-date booked nothing: this step's
        frame prices it from that mark, so a re-base would apply the split twice."""
        halted = date(2024, 2, 12)
        book = _book({"A": 500.0}, {"A": halted}, {"A": HELD}, [F0])
        assert engine._rebase_late_splits(book, _late(("A", EX, 2.0)), signed=False) == 1
        assert book.positions == {"A": 500.0}

    def test_a_name_not_held_before_the_ex_date_is_left_alone(self) -> None:
        book = _book({"A": 500.0}, {"A": T1}, {"A": {EX, T1}}, [F0])
        assert engine._rebase_late_splits(book, _late(("A", EX, 2.0)), signed=False) == 0
        assert book.positions == {"A": 500.0}

    @pytest.mark.parametrize(
        ("positions", "fills", "signed"),
        [
            ({}, [F0], False),  # sold or exited since the ex-date: the jump is in cash
            ({"A": 500.0}, [F0, date(2024, 2, 16)], False),  # a fill since sized from it
            ({"A": 500.0}, [F0], True),  # a signed book's cover tests weighed it
        ],
        ids=["exited", "filled-since", "long-short"],
    )
    def test_an_unrepairable_late_split_refuses_the_trial(
        self, positions: dict[str, float], fills: list[date], signed: bool
    ) -> None:
        book = _book(positions, {"A": T1}, {"A": HELD}, fills)
        with pytest.raises(ValueError, match=r"late split on A ex 2024-02-15"):
            engine._rebase_late_splits(book, _late(("A", EX, 2.0)), signed=signed)

    def test_a_benchmark_is_rebased_only_after_its_buy(self) -> None:
        book = _book({}, {}, {}, [F0])
        book.benchmarks = {
            "SPY": engine._Holding("S", cash=0.0, value=500.0, marked_at=T1),
            "MTUM": engine._Holding("M", cash=0.0, value=500.0, marked_at=T1),
        }
        late = _late(("S", EX, 2.0), ("M", F0, 2.0))
        assert engine._rebase_late_splits(book, late, signed=False) == 0
        assert book.benchmarks["SPY"].value == 1000.0  # marked across 02-15 after F0
        assert book.benchmarks["MTUM"].value == 500.0  # bought on the ex-date: no jump
