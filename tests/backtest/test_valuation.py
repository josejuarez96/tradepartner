"""Position valuation within one marking frame and stitched returns (spec req 2, T37).

The split and dividend cases read the marking frame through the real
`adjusted_prices_as_of` on a synthetic store, so they check that one frame per step
makes a split cancel and reinvests a dividend in the paying name, and compare equity
with a hand count of shares.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tradepartner.backtest.valuation import (
    StepFrame,
    carry_to_fill,
    stitched_returns,
    value_positions,
)
from tradepartner.calendar import session_close
from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row

VALUATION_PY = Path(__file__).parents[2] / "src" / "tradepartner" / "backtest" / "valuation.py"

# February 2024: T_0 = 01-31, F_0 = 02-01, T_1 = 02-29.
T0 = date(2024, 1, 31)
F0 = date(2024, 2, 1)
D2, D5, D6, D7, D8 = (date(2024, 2, d) for d in (2, 5, 6, 7, 8))
T1 = date(2024, 2, 29)


def _frame(rows: list[tuple[str, date, float, float]]) -> pl.DataFrame:
    """A marking frame from `(security_id, session, open, close)` rows."""
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
        },
        orient="row",
    )


def _values(result: pl.DataFrame, security_id: str) -> dict[date, float]:
    rows = result.filter(pl.col("security_id") == security_id).select("session", "value")
    return dict(rows.iter_rows())


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date, close: float) -> None:
    known_at = session_close(session)
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "bar",
        },
    )


def _action(
    conn: duckdb.DuckDBPyConnection, sid: str, kind: str, ex_date: date, value: float
) -> None:
    known_at = session_close(D2)
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": sid,
            "action_type": kind,
            "ex_date": ex_date,
            "ratio_or_amount": value,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "action",
        },
    )


def _split_and_dividend_store(conn: duckdb.DuckDBPyConnection) -> None:
    """Raw closes 100, 104, then a 2:1 split ex 02-05 (53), a $1 dividend ex 02-06 (52), 54."""
    for session, close in [(T0, 99.0), (F0, 100.0), (D2, 104.0), (D5, 53.0), (D6, 52.0)]:
        _bar(conn, "A", session, close)
    _bar(conn, "A", D7, 54.0)
    _action(conn, "A", "split", D5, 2.0)
    _action(conn, "A", "dividend", D6, 1.0)


def test_split_inside_the_month_cancels_and_dividend_is_reinvested(
    store: duckdb.DuckDBPyConnection,
) -> None:
    _split_and_dividend_store(store)
    frame = adjusted_prices_as_of(store, session_close(D7), ["A"], include_dividends=True)
    result = _values(value_positions({"A": 1000.0}, frame, F0, fill_price="close", through=D7), "A")

    # Hand count: $1000 buys 10 shares at 100; the split makes them 20; the $1 dividend
    # on 20 shares is $20, reinvested at the ex-date close of 52.
    shares_after_dividend = 20 + 20 * 1.0 / 52.0
    expected = {
        F0: 10 * 100.0,
        D2: 10 * 104.0,
        D5: 20 * 53.0,  # no jump: 1060, not 530
        D6: 20 * 52.0 + 20 * 1.0,  # price fell by the dividend, cash reinvested
        D7: shares_after_dividend * 54.0,
    }
    assert result.keys() == expected.keys()
    for session, value in expected.items():
        assert result[session] == pytest.approx(value, rel=1e-12), session


def test_valuation_takes_every_ratio_from_the_frame(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Given the raw (unadjusted) frame, the split is priced as a halving: valuation
    does no adjustment of its own, so the frame it is handed decides every ratio."""
    _split_and_dividend_store(store)
    raw = prices_as_of(store, session_close(D7), ["A"])
    result = _values(value_positions({"A": 1000.0}, raw, F0, fill_price="close", through=D5), "A")
    assert result[D5] == pytest.approx(530.0)


def test_open_fill_values_from_the_fill_session_open() -> None:
    frame = _frame([("A", F0, 50.0, 55.0), ("A", D2, 56.0, 60.0)])
    result = _values(value_positions({"A": 100.0}, frame, F0, fill_price="open", through=D2), "A")
    assert result == {F0: pytest.approx(110.0), D2: pytest.approx(120.0)}


def test_a_name_with_no_bar_keeps_its_last_mark() -> None:
    frame = _frame(
        [
            ("A", F0, 10.0, 10.0),
            ("A", D2, 11.0, 11.0),
            # no bar on 02-05 or 02-06
            ("A", D7, 12.0, 12.0),
            ("B", F0, 20.0, 20.0),
            ("B", D2, 20.0, 21.0),
            ("B", D5, 20.0, 22.0),
            ("B", D6, 20.0, 23.0),
            ("B", D7, 20.0, 24.0),
        ]
    )
    result = value_positions({"A": 100.0, "B": 100.0}, frame, F0, fill_price="close", through=D7)
    assert _values(result, "A") == {
        F0: pytest.approx(100.0),
        D2: pytest.approx(110.0),
        D5: pytest.approx(110.0),
        D6: pytest.approx(110.0),
        D7: pytest.approx(120.0),
    }
    assert _values(result, "B")[D7] == pytest.approx(120.0)


def test_every_calendar_session_is_valued_even_with_no_bar_for_any_name() -> None:
    frame = _frame([("A", F0, 10.0, 10.0), ("A", D8, 10.0, 15.0)])
    result = _values(value_positions({"A": 10.0}, frame, F0, fill_price="close", through=D8), "A")
    assert list(result) == [F0, D2, D5, D6, D7, D8]
    assert result[D7] == pytest.approx(10.0)
    assert result[D8] == pytest.approx(15.0)


def test_a_held_name_with_no_bar_on_the_fill_session_is_marked_from_its_last_close() -> None:
    """An unsold name with a missing fill keeps its last mark (req 4): its carried value
    is priced from its last close before the fill session, whatever the fill price."""
    frame = _frame([("A", T0, 40.0, 50.0), ("A", D2, 1.0, 55.0)])
    result = _values(value_positions({"A": 100.0}, frame, F0, fill_price="open", through=D2), "A")
    assert result == {F0: pytest.approx(100.0), D2: pytest.approx(110.0)}


def test_bars_outside_the_window_are_ignored() -> None:
    frame = _frame(
        [("A", T0, 1.0, 1.0), ("A", F0, 10.0, 10.0), ("A", D2, 11.0, 11.0), ("A", D5, 99.0, 99.0)]
    )
    result = value_positions({"A": 10.0}, frame, F0, fill_price="close", through=D2)
    assert result["session"].to_list() == [F0, D2]


def test_no_positions_gives_an_empty_frame_with_the_schema() -> None:
    result = value_positions({}, _frame([]), F0, fill_price="close", through=D2)
    assert result.is_empty()
    assert result.schema == {
        "security_id": pl.Utf8,
        "session": pl.Date,
        "value": pl.Float64,
        "marked_at": pl.Date,
    }


def test_marked_at_is_the_session_of_the_bar_behind_each_value() -> None:
    frame = _frame([("A", T0, 1.0, 9.0), ("A", D2, 11.0, 11.0), ("A", D6, 12.0, 12.0)])
    result = value_positions({"A": 90.0}, frame, F0, fill_price="close", through=D7)
    assert result.select("session", "marked_at").rows() == [
        (F0, T0),  # missed fill: still marked at the last close before F0
        (D2, D2),
        (D5, D2),
        (D6, D6),
        (D7, D6),
    ]


def test_a_zero_position_stays_zero() -> None:
    frame = _frame([("A", F0, 10.0, 10.0), ("A", D2, 12.0, 12.0)])
    result = _values(value_positions({"A": 0.0}, frame, F0, fill_price="close", through=D2), "A")
    assert result == {F0: 0.0, D2: 0.0}


@pytest.mark.parametrize(
    ("positions", "frame", "message"),
    [
        ({"A": -1.0}, _frame([("A", F0, 1.0, 1.0)]), "non-negative"),
        ({"A": math.nan}, _frame([("A", F0, 1.0, 1.0)]), "non-negative"),
        ({"A": 1.0}, _frame([("A", F0, 1.0, 1.0), ("A", F0, 2.0, 2.0)]), "duplicate"),
        ({"A": 1.0}, _frame([("A", D2, 1.0, 1.0)]), "no bar"),
        ({"A": 1.0}, _frame([("A", F0, 1.0, 0.0)]), "positive"),
        ({"A": 1.0}, _frame([("A", F0, 1.0, 1.0), ("A", D2, 1.0, math.inf)]), "positive"),
    ],
)
def test_invalid_inputs_raise(
    positions: dict[str, float], frame: pl.DataFrame, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        value_positions(positions, frame, F0, fill_price="close", through=D2)


def test_window_must_run_forward_from_a_session() -> None:
    frame = _frame([("A", F0, 1.0, 1.0)])
    with pytest.raises(ValueError, match="through"):
        value_positions({"A": 1.0}, frame, D2, fill_price="close", through=F0)
    with pytest.raises(ValueError, match="session"):
        value_positions({"A": 1.0}, frame, date(2024, 2, 3), fill_price="close", through=D5)


def test_unknown_fill_price_is_refused() -> None:
    frame = _frame([("A", F0, 1.0, 1.0)])
    with pytest.raises(ValueError, match="fill_price"):
        value_positions({"A": 1.0}, frame, F0, fill_price="vwap", through=F0)  # type: ignore[arg-type]


class TestCarryToFill:
    """The drifted value at close(T_i) priced forward to F_i by the next frame (req 2)."""

    def test_close_and_open_fills(self) -> None:
        frame = _frame([("A", T0, 9.0, 10.0), ("A", F0, 11.0, 12.0)])
        marked = {"A": T0}
        assert carry_to_fill({"A": 100.0}, frame, T0, F0, fill_price="close", marked_at=marked) == {
            "A": pytest.approx(120.0)
        }
        assert carry_to_fill({"A": 100.0}, frame, T0, F0, fill_price="open", marked_at=marked) == {
            "A": pytest.approx(110.0)
        }

    def test_no_bar_on_the_fill_session_keeps_the_last_mark(self) -> None:
        frame = _frame([("A", T0, 9.0, 10.0)])
        assert carry_to_fill(
            {"A": 100.0}, frame, T0, F0, fill_price="open", marked_at={"A": T0}
        ) == {"A": 100.0}

    def test_a_value_marked_before_the_rebalance_session_is_priced_from_that_mark(self) -> None:
        frame = _frame([("A", date(2024, 1, 29), 1.0, 10.0), ("A", F0, 1.0, 15.0)])
        assert carry_to_fill(
            {"A": 100.0}, frame, T0, F0, fill_price="close", marked_at={"A": date(2024, 1, 29)}
        ) == {"A": pytest.approx(150.0)}

    def test_a_late_bar_between_the_mark_and_the_fill_is_counted_once(self) -> None:
        """The previous frame marked A at 01-29 (no T_0 bar yet); this frame has a late
        T_0 bar. The move 01-29 -> T_0 -> F_0 is priced once, from the mark."""
        frame = _frame([("A", date(2024, 1, 29), 1.0, 10.0), ("A", T0, 1.0, 11.0)])
        marked = {"A": date(2024, 1, 29)}
        # No F_0 bar: the value moves to the late T_0 close, its new last mark.
        assert carry_to_fill({"A": 100.0}, frame, T0, F0, fill_price="close", marked_at=marked) == {
            "A": pytest.approx(110.0)
        }
        with_fill = _frame([*frame.rows(), ("A", F0, 12.0, 13.0)])
        assert carry_to_fill(
            {"A": 100.0}, with_fill, T0, F0, fill_price="open", marked_at=marked
        ) == {"A": pytest.approx(120.0)}

    def test_chained_with_value_positions_telescopes_to_the_frame_ratio(self) -> None:
        """Carry then value within one frame: close(s) / close(mark), whatever the fill."""
        frame = _frame(
            [
                ("A", date(2024, 1, 29), 1.0, 10.0),
                ("A", T0, 1.0, 11.0),
                ("A", F0, 12.0, 13.0),
                ("A", D2, 1.0, 14.0),
            ]
        )
        marked = {"A": date(2024, 1, 29)}
        for fill_price in ("close", "open"):
            carried = carry_to_fill(
                {"A": 100.0}, frame, T0, F0, fill_price=fill_price, marked_at=marked
            )
            valued = value_positions(carried, frame, F0, fill_price=fill_price, through=D2)
            assert _values(valued, "A")[D2] == pytest.approx(140.0)

    def test_a_split_known_at_the_later_read_cancels(self) -> None:
        """The next frame restates the pre-split close, so the carry has no jump."""
        restated = _frame([("A", T0, 50.0, 50.0), ("A", F0, 51.0, 51.0)])
        assert carry_to_fill(
            {"A": 1000.0}, restated, T0, F0, fill_price="close", marked_at={"A": T0}
        ) == {"A": pytest.approx(1020.0)}

    def test_a_retracted_mark_bar_raises(self) -> None:
        """The value was marked at T_0 but this frame no longer has that bar."""
        frame = _frame([("A", date(2024, 1, 29), 1.0, 10.0), ("A", F0, 1.0, 1.0)])
        with pytest.raises(ValueError, match="last mark"):
            carry_to_fill({"A": 1.0}, frame, T0, F0, fill_price="close", marked_at={"A": T0})

    def test_marked_at_must_be_given_and_not_after_the_rebalance_session(self) -> None:
        frame = _frame([("A", T0, 1.0, 1.0), ("A", F0, 1.0, 1.0)])
        with pytest.raises(ValueError, match="marked_at"):
            carry_to_fill({"A": 1.0}, frame, T0, F0, fill_price="close", marked_at={})
        with pytest.raises(ValueError, match="marked_at"):
            carry_to_fill({"A": 1.0}, frame, T0, F0, fill_price="close", marked_at={"A": F0})

    def test_fill_session_must_be_the_next_session(self) -> None:
        frame = _frame([("A", T0, 1.0, 1.0)])
        marked = {"A": T0}
        with pytest.raises(ValueError, match="next session"):
            carry_to_fill({"A": 1.0}, frame, F0, T0, fill_price="close", marked_at=marked)
        with pytest.raises(ValueError, match="next session"):
            carry_to_fill({"A": 1.0}, frame, T0, D2, fill_price="close", marked_at=marked)


class TestStitchedReturns:
    # Step 0 holds (T_0, T_1], read at close(T_1); step 1 holds (T_1, T_2], read at close(T_2).
    T2 = date(2024, 3, 28)
    M1, M2 = date(2024, 3, 1), date(2024, 3, 4)

    def _steps(self) -> list[StepFrame]:
        # Step 0's frame does not know a 2:1 split ex 03-01; step 1's frame does, so it
        # restates every earlier bar (halved). Each step's ratios come from its own frame.
        frame0 = _frame([("A", T0, 99.0, 100.0), ("A", F0, 101.0, 102.0), ("A", T1, 108.0, 110.0)])
        frame1 = _frame(
            [
                ("A", T0, 49.5, 50.0),
                ("A", F0, 50.5, 51.0),
                ("A", T1, 54.0, 55.0),
                ("A", self.M1, 56.0, 56.1),
                ("A", self.M2, 57.0, 57.2),
                ("B", T1, 10.0, 10.0),
                ("B", self.M1, 10.5, 11.0),
            ]
        )
        return [
            StepFrame(start=T0, end=T1, frame=frame0),
            StepFrame(start=T1, end=self.T2, frame=frame1),
        ]

    def test_one_step_reproduces_the_frame(self) -> None:
        steps = self._steps()[:1]
        result = stitched_returns(steps).filter(pl.col("security_id") == "A")
        assert result["session"].to_list() == [F0, T1]
        assert result["close"].to_list() == pytest.approx([102.0, 110.0])
        assert result["open"].to_list() == pytest.approx([101.0, 108.0])
        assert result["close_return"].to_list() == pytest.approx(
            [102.0 / 100.0 - 1, 110.0 / 102.0 - 1]
        )

    def test_ratios_come_from_the_step_that_contains_the_session(self) -> None:
        result = stitched_returns(self._steps()).filter(pl.col("security_id") == "A")
        close = dict(zip(result["session"].to_list(), result["close"].to_list(), strict=True))
        opens = dict(zip(result["session"].to_list(), result["open"].to_list(), strict=True))
        # Step 0's ratio, from the unsplit frame.
        assert close[T1] / close[F0] == pytest.approx(110.0 / 102.0)
        # Step 1's ratios, from the restated frame: no split jump across the boundary.
        assert close[self.M1] / close[T1] == pytest.approx(56.1 / 55.0)
        assert opens[self.M1] / close[T1] == pytest.approx(56.0 / 55.0)
        assert close[self.M2] / close[self.M1] == pytest.approx(57.2 / 56.1)
        returns = dict(
            zip(result["session"].to_list(), result["close_return"].to_list(), strict=True)
        )
        assert returns[self.M1] == pytest.approx(56.1 / 55.0 - 1)

    def test_a_name_first_seen_in_a_later_step_is_anchored_at_that_frame(self) -> None:
        result = stitched_returns(self._steps()).filter(pl.col("security_id") == "B")
        assert result["session"].to_list() == [self.M1]
        assert result["close"].to_list() == pytest.approx([11.0])
        assert result["open"].to_list() == pytest.approx([10.5])

    def test_a_later_step_never_changes_an_earlier_one(self) -> None:
        """Prefix invariance: stitching fewer steps gives the same rows up to their end,
        even when the later frame corrects an earlier bar (T_1 close 110 -> 100)."""
        step0, step1 = self._steps()
        corrected = step1.frame.with_columns(
            pl.when((pl.col("security_id") == "A") & (pl.col("session") == T1))
            .then(pl.lit(100.0))
            .otherwise(pl.col("close"))
            .alias("close")
        )
        full = stitched_returns([step0, StepFrame(start=T1, end=self.T2, frame=corrected)])
        prefix = stitched_returns([step0])
        assert full.filter(pl.col("session") <= T1).equals(prefix)

    def test_the_index_is_continuous_across_steps_whose_frames_lack_the_name(self) -> None:
        """A sold then re-bought name continues from its last level, priced across the
        gap by the frame it returns in."""
        step0 = StepFrame(
            start=T0, end=T1, frame=_frame([("A", T0, 1.0, 10.0), ("A", T1, 1.0, 12.0)])
        )
        step1 = StepFrame(start=T1, end=self.T2, frame=_frame([("B", self.M1, 1.0, 1.0)]))
        t3 = date(2024, 4, 30)
        returning = _frame([("A", T1, 1.0, 12.0), ("A", self.T2, 1.0, 15.0), ("A", t3, 1.0, 18.0)])
        step2 = StepFrame(start=self.T2, end=t3, frame=returning)
        result = stitched_returns([step0, step1, step2]).filter(pl.col("security_id") == "A")
        assert result.select("session", "close").rows() == [
            (T1, pytest.approx(12.0)),
            (t3, pytest.approx(18.0)),
        ]

    def test_a_frame_missing_the_last_stitched_bar_raises(self) -> None:
        step0, _ = self._steps()
        retracted = _frame([("A", F0, 1.0, 51.0), ("A", self.M1, 1.0, 56.0)])
        with pytest.raises(ValueError, match="last stitched session"):
            stitched_returns([step0, StepFrame(start=T1, end=self.T2, frame=retracted)])

    def test_steps_must_be_contiguous_and_ascending(self) -> None:
        step0, step1 = self._steps()
        gap = StepFrame(start=F0, end=self.T2, frame=step1.frame)
        with pytest.raises(ValueError, match="contiguous"):
            stitched_returns([step0, gap])
        with pytest.raises(ValueError, match="start"):
            stitched_returns([StepFrame(start=T1, end=T0, frame=step0.frame)])

    def test_no_steps_gives_an_empty_frame(self) -> None:
        result = stitched_returns([])
        assert result.is_empty()
        assert result.columns == ["security_id", "session", "open", "close", "close_return"]


# Spec acceptance (Engine and timing): no numeric literals in valuation.py other than these.
ALLOWED_LITERALS = {0, 1}


def test_valuation_module_has_no_stray_numeric_literals() -> None:
    tree = ast.parse(VALUATION_PY.read_text(encoding="utf-8"))
    stray = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value not in ALLOWED_LITERALS
    ]
    assert stray == []
