"""Position valuation within one marking frame and stitched returns (spec req 2, T37).

The split and dividend cases read the marking frame through the real
`adjusted_prices_as_of` on a synthetic store, so they check that one frame per step
makes a split cancel and reinvests a dividend in the paying name, and compare equity
with a hand count of shares.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import math
import random
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from backtest.test_engine import F1, T3, _params, _provider, _run
from tradepartner.backtest import valuation
from tradepartner.backtest.metrics import series_metrics
from tradepartner.backtest.riskfree import constant_period_returns
from tradepartner.backtest.valuation import (
    FillPrice,
    StepFrame,
    carry_to_fill,
    stitched_returns,
    value_positions,
)
from tradepartner.calendar import all_sessions, next_session, session_close
from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row

VALUATION_PY = Path(__file__).parents[2] / "src" / "tradepartner" / "backtest" / "valuation.py"

# February 2024: T_0 = 01-31, F_0 = 02-01, T_1 = 02-29.
T0 = date(2024, 1, 31)
F0 = date(2024, 2, 1)
D2, D5, D6, D7, D8 = (date(2024, 2, d) for d in (2, 5, 6, 7, 8))
T1 = date(2024, 2, 29)


def test_signed_short_split_and_dividend_marks_to_hand_values() -> None:
    split = _frame([("S", F0, 25.0, 25.0), ("S", D2, 25.0, 25.0)])
    values = value_positions({"S": -50.0}, split, F0, fill_price="close", through=D2, signed=True)
    assert _values(values, "S") == pytest.approx({F0: -50.0, D2: -50.0}, abs=1e-9)

    # Raw prior close 100, D=2, factor=.98; adjusted prior=98, ex raw close=99.
    dividend = _frame([("S", F0, 98.0, 98.0), ("S", D2, 99.0, 99.0)])
    marked = value_positions(
        {"S": -50.0}, dividend, F0, fill_price="close", through=D2, signed=True
    )
    marked_equity = 100.0 + _values(marked, "S")[D2]
    cash_payment_equity = 100.0 - 50.0 * 99.0 / 100.0 - 0.5 * 2.0
    assert marked_equity - cash_payment_equity == pytest.approx(
        0.5 * 2.0 * (1.0 - 99.0 / (100.0 - 2.0)), abs=1e-9
    )


def test_signed_carry_to_fill_preserves_negative_value() -> None:
    frame = _frame([("S", T0, 20.0, 20.0), ("S", F0, 22.0, 22.0)])
    got = carry_to_fill(
        {"S": -50.0}, frame, T0, F0, fill_price="close", marked_at={"S": T0}, signed=True
    )
    assert got == pytest.approx({"S": -55.0}, abs=1e-9)


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


# --- T207: the engine's standard fixture pinned on the per-name loop (#1742) ---------


def _digest(result: Any) -> dict[str, Any]:
    """A cost level's run reduced to hashes of its exact rows plus readable counts."""
    equity = [(r.series, r.session, r.equity, r.cash) for r in result.equity]
    rebalances = [repr(r) for r in result.rebalances]
    strategy = [e.equity for e in result.equity if e.series == "strategy"]
    values = result.position_values.sort("security_id", "session").rows()
    daily = [b / a - 1 for a, b in itertools.pairwise(strategy)]
    metrics = series_metrics(
        "strategy",
        period_returns=daily,
        gross_period_returns=daily,
        daily_equity=strategy,
        turnover=[row.turnover for row in result.rebalances],
        spy_period_returns=[r * 0.5 + 0.0001 * (i % 3) for i, r in enumerate(daily)],
        mtum_period_returns=[r * 0.25 - 0.0001 * (i % 2) for i, r in enumerate(daily)],
        periods_per_year=252,
        risk_free_period_returns=constant_period_returns(0.0, 252, len(daily)),
    )
    return {
        "metrics_sha": hashlib.sha256(
            repr(
                sorted(
                    (k, None if v is None else round(v, 9))
                    for k, v in metrics.items()
                    # T211's reported-only keys postdate these pins (taken on 6a9e2dec)
                    if k not in ("beta_spy", "alpha_spy_annual")
                )
            ).encode()
        ).hexdigest()[:16],
        "equity_sha": hashlib.sha256(repr(equity).encode()).hexdigest()[:16],
        "rebalances_sha": hashlib.sha256(repr(rebalances).encode()).hexdigest()[:16],
        "positions_sha": hashlib.sha256(repr(values).encode()).hexdigest()[:16],
        "final_equity": repr(strategy[-1]),
        "n_equity": len(equity),
        "n_rebalances": len(rebalances),
    }


def _standard_cases() -> dict[str, dict[float, dict[str, Any]]]:
    """The test_engine fixture (A..D, T0..T3) under close and open fills and with the
    fill bars of A and C missing on F1, each run through the engine and digested."""
    cases: dict[str, tuple[Any, Any]] = {
        "close": (_provider(), _params(execution={"fill_price": "close"})),
        "open": (_provider(), _params(execution={"fill_price": "open"})),
        "missed": (_provider(skip=[("A", F1), ("C", F1)]), _params()),
    }
    return {
        name: {
            level: _digest(res)
            for level, res in _run(provider, params, end=T3, levels=(0.0, 15.0)).items()
        }
        for name, (provider, params) in cases.items()
    }


#: Digests of the engine's standard fixture, taken on main's per-name loop (6a9e2dec)
#: before `valuation.py` was vectorised (T207, #1742); the vectorised path must match.
STANDARD_PINS: dict[str, dict[float, dict[str, Any]]] = {
    "close": {
        0.0: {
            "equity_sha": "41b42afe01bbaf08",
            "final_equity": "118909.48789598294",
            "metrics_sha": "ccc320d72324675c",
            "n_equity": 63,
            "n_rebalances": 3,
            "positions_sha": "30f815c8ffc807ed",
            "rebalances_sha": "ceea09fda7475722",
        },
        15.0: {
            "equity_sha": "2213b471eda2b663",
            "final_equity": "118553.47731502213",
            "metrics_sha": "bf1a5e2788148f76",
            "n_equity": 63,
            "n_rebalances": 3,
            "positions_sha": "2e9fb107aba9f127",
            "rebalances_sha": "574d658d89ad7fe2",
        },
    },
    "missed": {
        0.0: {
            "equity_sha": "99638c6f46d57b30",
            "final_equity": "117814.81281860062",
            "metrics_sha": "c14bb8b8890c5dd4",
            "n_equity": 63,
            "n_rebalances": 3,
            "positions_sha": "584da82b920751ff",
            "rebalances_sha": "91d7130735c51c57",
        },
        15.0: {
            "equity_sha": "f2a1319309739751",
            "final_equity": "117464.94162804325",
            "metrics_sha": "02d24ffcf069f038",
            "n_equity": 63,
            "n_rebalances": 3,
            "positions_sha": "8ca51261b130ef3f",
            "rebalances_sha": "1c00cdcaac4ab8fd",
        },
    },
    "open": {
        0.0: {
            "equity_sha": "0e0935d9c8f7f80e",
            "final_equity": "119060.66687801277",
            "metrics_sha": "22cf634476d0d13d",
            "n_equity": 63,
            "n_rebalances": 3,
            "positions_sha": "87c382d6717a27ab",
            "rebalances_sha": "ceea09fda7475722",
        },
        15.0: {
            "equity_sha": "62dd0e81ce9b3baa",
            "final_equity": "118704.20784205146",
            "metrics_sha": "a78cb88097585d4b",
            "n_equity": 63,
            "n_rebalances": 3,
            "positions_sha": "d91647f5c08858f3",
            "rebalances_sha": "05339d60644fba88",
        },
    },
}


@pytest.mark.parametrize("case", sorted(STANDARD_PINS))
def test_the_standard_fixture_matches_the_pinned_loop_results(case: str) -> None:
    """Equity, rebalance rows, position values and metrics are byte-identical to the
    per-name loop's: the pins were taken before the vectorised path existed."""
    assert _standard_cases()[case] == STANDARD_PINS[case]


# --- T207: the vectorised paths against main's per-name loops, kept here verbatim ----


def _loop_fill_value(sid: str, bar: Any, fill_price: str) -> float:
    value = bar.open if fill_price == "open" else bar.close
    return valuation._check_price(sid, bar.session, fill_price, value)


def loop_value_positions(
    positions: Mapping[str, float],
    marking_frame: pl.DataFrame,
    fill_session: date,
    *,
    fill_price: FillPrice,
    through: date,
) -> pl.DataFrame:
    valuation._check_fill_price(fill_price)
    valuation._check_positions(positions)
    sessions = valuation._sessions(fill_session, through)
    bars = valuation._bars(marking_frame, set(positions))
    rows: list[tuple[str, date, float, date]] = []
    for sid in sorted(positions):
        series = bars.get(sid, [])
        base_bar = valuation._last_at_or_before(series, fill_session)
        if base_bar is None:
            raise ValueError(f"no bar for {sid} at or before {fill_session.isoformat()}")
        if base_bar.session == fill_session:
            base = _loop_fill_value(sid, base_bar, fill_price)
        else:
            base = valuation._check_price(sid, base_bar.session, "close", base_bar.close)
        closes = {bar.session: bar.close for bar in series}
        value, marked_at = positions[sid], base_bar.session
        for session in sessions:
            if session in closes:
                close = valuation._check_price(sid, session, "close", closes[session])
                value, marked_at = positions[sid] * close / base, session
            rows.append((sid, session, value, marked_at))
    return pl.DataFrame(rows, schema=valuation._VALUE_SCHEMA, orient="row")


def loop_carry_to_fill(
    positions: Mapping[str, float],
    marking_frame: pl.DataFrame,
    rebalance_session: date,
    fill_session: date,
    *,
    fill_price: FillPrice,
    marked_at: Mapping[str, date],
) -> dict[str, float]:
    valuation._check_fill_price(fill_price)
    valuation._check_positions(positions)
    if fill_session != next_session(rebalance_session):
        raise ValueError(
            f"fill_session {fill_session.isoformat()} is not the next session after "
            f"rebalance_session {rebalance_session.isoformat()}"
        )
    bars = valuation._bars(marking_frame, set(positions))
    carried: dict[str, float] = {}
    for sid in sorted(positions):
        mark = marked_at.get(sid)
        if mark is None or mark > rebalance_session:
            raise ValueError(
                f"{sid} needs a marked_at session at or before {rebalance_session.isoformat()}, "
                f"got {mark}"
            )
        series = bars.get(sid, [])
        closes = {bar.session: bar.close for bar in series}
        if mark not in closes:
            raise ValueError(f"no bar for {sid} on its last mark {mark.isoformat()}")
        base = valuation._check_price(sid, mark, "close", closes[mark])
        target_bar = valuation._last_at_or_before(series, fill_session)
        if target_bar is None:  # unreachable: the bar on `mark` is at or before it
            raise ValueError(f"no bar for {sid} at or before {fill_session.isoformat()}")
        if target_bar.session == fill_session:
            target = _loop_fill_value(sid, target_bar, fill_price)
        else:
            target = valuation._check_price(sid, target_bar.session, "close", target_bar.close)
        carried[sid] = positions[sid] * target / base
    return carried


def _gappy_frame(seed: int, names: int = 12) -> pl.DataFrame:
    """Random-walk bars for `names` securities from 2024-01-02 through 2024-03-28 with
    ~15% of bars missing (seeded), a split-like level shift and a closed-form open."""
    rng = random.Random(seed)
    sessions = [s for s in all_sessions() if date(2024, 1, 2) <= s <= date(2024, 3, 28)]
    rows = []
    for n in range(names):
        close = rng.uniform(20, 400)
        for session in sessions:
            previous, close = close, close * (1 + rng.uniform(-0.03, 0.03))
            if rng.random() < 0.15:
                continue
            rows.append((f"S{n:02d}", session, (previous + close) / 2, close))
    return _frame(rows)


def _random_positions(seed: int, frame: pl.DataFrame) -> dict[str, float]:
    rng = random.Random(seed)
    ids = sorted(frame["security_id"].unique())
    return {sid: rng.uniform(0, 50_000) for sid in rng.sample(ids, 9)}


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("fill_price", ["close", "open"])
def test_value_positions_is_byte_identical_to_the_loop(seed: int, fill_price: FillPrice) -> None:
    frame = _gappy_frame(seed)
    positions = _random_positions(seed, frame)
    # Fill sessions with and without a bar for some held names; names whose first bar
    # is later than the fill session raise in both paths.
    fill, through = date(2024, 2, 1), date(2024, 3, 15)
    try:
        expected = loop_value_positions(
            positions, frame, fill, fill_price=fill_price, through=through
        )
    except ValueError as loop_error:
        with pytest.raises(ValueError) as error:
            value_positions(positions, frame, fill, fill_price=fill_price, through=through)
        assert str(error.value) == str(loop_error)
        return
    got = value_positions(positions, frame, fill, fill_price=fill_price, through=through)
    assert got.schema == expected.schema
    assert got.equals(expected)
    assert got.height == len(positions) * len(valuation._sessions(fill, through))


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("fill_price", ["close", "open"])
def test_carry_to_fill_is_byte_identical_to_the_loop(seed: int, fill_price: FillPrice) -> None:
    frame = _gappy_frame(seed)
    positions = _random_positions(seed, frame)
    rebalance = date(2024, 2, 29)
    fill = next_session(rebalance)
    bars = valuation._bars(frame, set(positions))
    marked = {sid: bars[sid][-1].session for sid in positions if bars.get(sid)}
    marked = {sid: min(m, rebalance) for sid, m in marked.items()}
    marked = {
        sid: next(b.session for b in reversed(bars[sid]) if b.session <= m)
        for sid, m in marked.items()
    }
    kwargs: dict[str, Any] = {"fill_price": fill_price, "marked_at": marked}
    expected = loop_carry_to_fill(positions, frame, rebalance, fill, **kwargs)
    got = carry_to_fill(positions, frame, rebalance, fill, **kwargs)
    assert list(got) == list(expected)
    assert [repr(v) for v in got.values()] == [repr(v) for v in expected.values()]


def _error_cases() -> list[tuple[str, dict[str, float], pl.DataFrame, FillPrice]]:
    good = _frame([("A", F0, 10.0, 11.0), ("A", D2, 11.0, 12.0)])
    return [
        ("negative position", {"A": -1.0}, good, "close"),
        ("no bar at or before", {"B": 5.0}, good, "close"),
        ("bad close", {"A": 5.0}, _frame([("A", F0, 10.0, 0.0)]), "close"),
        (
            "bad later close",
            {"A": 5.0},
            _frame([("A", F0, 10.0, 11.0), ("A", D2, 1.0, -2.0)]),
            "close",
        ),
        ("bad open", {"A": 5.0}, _frame([("A", F0, math.nan, 11.0)]), "open"),
        (
            "bad base close",
            {"A": 5.0},
            _frame([("A", T0, 10.0, math.inf), ("A", D2, 1.0, 2.0)]),
            "close",
        ),
        (
            "open fill, no open column",
            {"A": 5.0},
            _frame([("A", F0, 1.0, 1.0)]).drop("open"),
            "open",
        ),
        ("duplicate bar", {"A": 5.0}, _frame([("A", F0, 1.0, 1.0), ("A", F0, 1.0, 1.0)]), "close"),
        (
            "null close",
            {"A": 5.0},
            _frame([("A", F0, 1.0, 1.0)]).with_columns(close=pl.lit(None, pl.Float64)),
            "close",
        ),
    ]


@pytest.mark.parametrize("case", _error_cases(), ids=lambda c: c[0])
def test_value_positions_refuses_what_the_loop_refuses(
    case: tuple[str, dict[str, float], pl.DataFrame, FillPrice],
) -> None:
    _, positions, frame, fill_price = case
    with pytest.raises(ValueError) as expected:
        loop_value_positions(positions, frame, F0, fill_price=fill_price, through=D5)
    with pytest.raises(ValueError) as got:
        value_positions(positions, frame, F0, fill_price=fill_price, through=D5)
    assert str(got.value) == str(expected.value)


@pytest.mark.parametrize(
    ("marks", "frame"),
    [
        ({"A": T0}, _frame([("A", T0, 1.0, 2.0), ("A", F0, 1.0, 2.0)])),
        ({"A": D2}, _frame([("A", T0, 1.0, 2.0)])),
        ({}, _frame([("A", T0, 1.0, 2.0)])),
        ({"A": T0}, _frame([("A", T0, 1.0, 0.0), ("A", F0, 1.0, 2.0)])),
        ({"A": T0}, _frame([("A", T0, 1.0, 2.0), ("A", F0, math.nan, 2.0)])),
    ],
    ids=["ok", "mark after rebalance", "no mark", "bad base", "bad open target"],
)
def test_carry_to_fill_agrees_with_the_loop_on_edge_cases(
    marks: dict[str, date], frame: pl.DataFrame
) -> None:
    def run(fn: Any) -> Any:
        try:
            return fn({"A": 100.0}, frame, T0, F0, fill_price="open", marked_at=marks)
        except ValueError as error:
            return str(error)

    assert run(carry_to_fill) == run(loop_carry_to_fill)


def test_empty_positions_give_an_empty_typed_frame() -> None:
    frame = _frame([("A", F0, 1.0, 1.0)])
    got = value_positions({}, frame, F0, fill_price="close", through=D2)
    assert got.height == 0
    assert got.schema == loop_value_positions({}, frame, F0, fill_price="close", through=D2).schema
    assert carry_to_fill({}, frame, T0, F0, fill_price="close", marked_at={}) == {}


@pytest.mark.parametrize("fill_price", ["close", "open"])
def test_a_single_name_single_price_ratio_is_byte_identical_to_the_loop(
    fill_price: FillPrice,
) -> None:
    """Polars divides a constant-like column by multiplying with its reciprocal, one ulp
    off the loop's `pos * close / base`; this single-name frame found it (the H1 twin)."""
    frame = _frame(
        [
            ("A", F0, 321.4488391715713, 322.09045362101557),
            ("A", D2, 322.73463452825763, 323.37881543549963),
        ]
    )
    kwargs: dict[str, Any] = {"fill_price": fill_price, "through": D2}
    got = value_positions({"A": 100000.0}, frame, F0, **kwargs)
    assert got.equals(loop_value_positions({"A": 100000.0}, frame, F0, **kwargs))
    marks: dict[str, date] = {"A": F0}
    carry = {"fill_price": fill_price, "marked_at": marks}
    assert carry_to_fill({"A": 7.3}, frame, F0, D2, **carry) == loop_carry_to_fill(
        {"A": 7.3}, frame, F0, D2, **carry
    )
