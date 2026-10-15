"""Spin-offs share the action frame but leave split-only paper helpers unchanged."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution import ledger, marks, outcomes, phases, plan, reserve, risk
from tradepartner.store.asof import live_actions_as_of

_CHILD = "SEC_SPLIT_PLAIN"
_AFTER = date(2018, 12, 12)
_THROUGH = date(2018, 12, 17)


@pytest.mark.parametrize(
    ("reader", "name"),
    [
        (lambda a: ledger._splits_by_security(a, _THROUGH), "ledger"),
        (lambda a: plan._split_factor(a, _CHILD, _AFTER, _THROUGH), "plan"),
        (lambda a: outcomes._split_factor(a, _CHILD, _AFTER, _THROUGH), "outcomes"),
        (lambda a: marks._split_factor(a, _CHILD, _AFTER, _THROUGH), "marks"),
        (lambda a: risk._split_count(a, _CHILD, _AFTER, _THROUGH), "risk"),
        (lambda a: reserve._split_factor(a, _CHILD, _AFTER, _THROUGH), "reserve"),
        (lambda a: phases._check_actions(a, _THROUGH), "phases"),
    ],
)
def test_paper_action_helpers_ignore_a_real_spinoff(
    fixture_store: duckdb.DuckDBPyConnection,
    reader: Callable[[pl.DataFrame], Any],
    name: str,
) -> None:
    actions = live_actions_as_of(fixture_store, session_close(date(2018, 12, 14)))
    assert actions.filter(pl.col("action_type") == "spinoff").height == 1
    without = actions.filter(pl.col("action_type") != "spinoff")
    assert reader(actions) == reader(without), name


def _planning(conn: duckdb.DuckDBPyConnection, actions: pl.DataFrame) -> Any:
    from tradepartner.execution import planning

    return planning.reference_prices(conn, _THROUGH, [_CHILD], actions)


def _wrapper(conn: duckdb.DuckDBPyConnection, actions: pl.DataFrame) -> Any:
    from tradepartner.execution import wrapper

    return wrapper._optional_prices(conn, _THROUGH, [_CHILD], actions)


def _run(conn: duckdb.DuckDBPyConnection, actions: pl.DataFrame) -> Any:
    from tradepartner.execution import run

    return run._optional_prices(conn, _THROUGH, [_CHILD], actions)


@pytest.mark.parametrize(
    ("reader", "expected"),
    [
        (_planning, {_CHILD: 50.0}),
        (_wrapper, ({_CHILD: 50.0}, {})),
        (_run, ({_CHILD: 50.0}, {})),
    ],
    ids=["planning", "wrapper", "run"],
)
def test_reference_price_readers_ignore_spinoff(
    fixture_store: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
    reader: Callable[[duckdb.DuckDBPyConnection, pl.DataFrame], Any],
    expected: Any,
) -> None:
    """`planning.reference_prices` and the `_optional_prices` of `wrapper` and
    `run` (the run path's readers of `live_actions_as_of`) divide by the
    split alone: the same-day spin-off of the same child changes nothing."""
    from tradepartner.execution import planning

    actions = live_actions_as_of(fixture_store, session_close(date(2018, 12, 14)))
    without = actions.filter(pl.col("action_type") != "spinoff")
    assert actions.height == without.height + 1
    stale_bar = pl.DataFrame({"security_id": [_CHILD], "session": [_AFTER], "close": [100.0]})
    monkeypatch.setattr(planning, "prices_as_of", lambda *_args: stale_bar)
    assert reader(fixture_store, actions) == reader(fixture_store, without) == expected


def test_phases_guard_rejects_an_early_read_of_spinoff(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    actions = live_actions_as_of(fixture_store, session_close(date(2018, 12, 14))).filter(
        pl.col("action_type") == "spinoff"
    )
    assert actions.height == 1
    with pytest.raises(ValueError, match="not known"):
        phases._check_actions(actions, date(2018, 12, 14))
