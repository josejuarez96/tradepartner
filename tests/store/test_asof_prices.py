"""`adjusted_prices_as_of(..., sessions_from=...)` (strategy-lab plan T99, #944).

The session-range filter drops bars with `session < sessions_from` from the
returned frame **after** the as-of selection: the `known_at` cut, the latest
revision per bar and every adjustment factor are computed exactly as the
unbounded read computes them, so the bounded frame is the unbounded frame's
rows at or after the bound, row for row. The probes are the fixture README's
split (SEC_SPLIT_BETWEEN, 3-for-1, ex 2019-01-11) and revised dividend
(SEC_DIV_REVISED, ex 2019-02-25, revised 2019-03-25).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of

IDS = ["SEC_SPLIT_BETWEEN", "SEC_DIV_REVISED", "SEC_SPLIT_FUTURE", "SEC_SPY"]

#: Three cutoffs: the split effective and the dividend known but not yet
#: ex; both effective at the first dividend amount; the revised amount.
CUTOFFS = [
    datetime(2019, 1, 31, 21, 0, tzinfo=UTC),
    datetime(2019, 3, 15, 20, 0, tzinfo=UTC),
    datetime(2019, 4, 30, 20, 0, tzinfo=UTC),
]

#: Bounds before the split's ex-date, on the dividend's prior close, and on
#: its ex-date.
BOUNDS = [date(2019, 1, 7), date(2019, 2, 22), date(2019, 2, 25)]

SPLIT_ID, SPLIT_EX, SPLIT_RATIO = "SEC_SPLIT_BETWEEN", date(2019, 1, 11), 3.0
DIV_ID, DIV_EX = "SEC_DIV_REVISED", date(2019, 2, 25)

#: Every (cutoff, bound) pair with the bound before the cutoff's session.
PAIRS = [(t, bound) for t in CUTOFFS for bound in BOUNDS if bound < t.date()]


@pytest.mark.parametrize(("t", "sessions_from"), PAIRS)
@pytest.mark.parametrize("include_dividends", [False, True])
@pytest.mark.parametrize("traded_only", [False, True])
def test_bounded_frame_is_the_unbounded_frames_rows_at_or_after_the_bound(
    fixture_store: duckdb.DuckDBPyConnection,
    t: datetime,
    sessions_from: date,
    include_dividends: bool,
    traded_only: bool,
) -> None:
    kwargs = {"include_dividends": include_dividends, "traded_only": traded_only}
    unbounded = adjusted_prices_as_of(fixture_store, t, IDS, **kwargs)  # type: ignore[arg-type]
    bounded = adjusted_prices_as_of(
        fixture_store,
        t,
        IDS,
        sessions_from=sessions_from,
        **kwargs,  # type: ignore[arg-type]
    )
    want = unbounded.filter(pl.col("session") >= sessions_from)
    assert bounded.equals(want)
    assert bounded.schema == unbounded.schema
    # The bound actually bites, and leaves something: the comparison is not vacuous.
    assert 0 < bounded.height < unbounded.height


@pytest.mark.parametrize("t", CUTOFFS)
def test_none_is_the_unbounded_read(fixture_store: duckdb.DuckDBPyConnection, t: datetime) -> None:
    assert adjusted_prices_as_of(fixture_store, t, IDS, sessions_from=None).equals(
        adjusted_prices_as_of(fixture_store, t, IDS)
    )


def _close(frame: pl.DataFrame, security_id: str, session: date) -> float:
    row = frame.filter((pl.col("security_id") == security_id) & (pl.col("session") == session))
    assert row.height == 1, (security_id, session)
    return float(row["close"].item())


def test_a_bar_between_the_bound_and_a_later_split_keeps_its_adjustment(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The split's ex-date is after the bound, so the bars from the bound up to it are
    still divided by the split ratio: the factor sees every event, not just the bars
    left in the frame."""
    t = CUTOFFS[0]
    session = date(2019, 1, 10)  # the last bar before the ex-date
    bounded = adjusted_prices_as_of(fixture_store, t, [SPLIT_ID], sessions_from=date(2019, 1, 7))
    raw = prices_as_of(fixture_store, t, [SPLIT_ID])
    assert _close(bounded, SPLIT_ID, session) == pytest.approx(
        _close(raw, SPLIT_ID, session) / SPLIT_RATIO
    )


@pytest.mark.parametrize("t", CUTOFFS[1:])
def test_a_dividend_whose_prior_close_is_cut_off_still_adjusts(
    fixture_store: duckdb.DuckDBPyConnection, t: datetime
) -> None:
    """A bound on the ex-date leaves the dividend's prior close out of the frame, but
    the factor is computed from the stored bars before the cut, so the dividend is not
    dropped and the frame's adjusted closes are the unbounded read's."""
    bounded = adjusted_prices_as_of(
        fixture_store, t, [DIV_ID], include_dividends=True, sessions_from=DIV_EX
    )
    unbounded = adjusted_prices_as_of(fixture_store, t, [DIV_ID], include_dividends=True)
    assert bounded["session"].min() == DIV_EX
    joined = bounded.join(unbounded, on=["security_id", "session"], suffix="_full")
    assert joined.height == bounded.height
    for column in ("open", "high", "low", "close"):
        assert joined[column].equals(joined[f"{column}_full"], check_names=False)


def test_a_bound_after_every_bar_returns_the_empty_frame(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    t = CUTOFFS[0]
    bounded = adjusted_prices_as_of(fixture_store, t, IDS, sessions_from=date(2030, 1, 2))
    assert bounded.height == 0
    assert bounded.schema == adjusted_prices_as_of(fixture_store, t, IDS).schema


def test_a_datetime_bound_is_refused(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """`sessions_from` is a session date; a `datetime` (a `date` subclass) is a
    caller mixing up a read time and a session, so it is refused."""
    with pytest.raises(TypeError, match="sessions_from"):
        adjusted_prices_as_of(
            fixture_store,
            CUTOFFS[0],
            IDS,
            sessions_from=datetime(2019, 1, 7, tzinfo=UTC),
        )
