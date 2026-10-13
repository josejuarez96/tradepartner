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


def test_planning_reference_price_ignores_spinoff(
    fixture_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradepartner.execution import planning

    actions = live_actions_as_of(fixture_store, session_close(date(2018, 12, 14)))
    without = actions.filter(pl.col("action_type") != "spinoff")
    assert actions.height == without.height + 1
    stale_bar = pl.DataFrame({"security_id": [_CHILD], "session": [_AFTER], "close": [100.0]})
    monkeypatch.setattr(planning, "prices_as_of", lambda *_args: stale_bar)
    with_spin = planning.reference_prices(fixture_store, _THROUGH, [_CHILD], actions)
    no_spin = planning.reference_prices(fixture_store, _THROUGH, [_CHILD], without)
    assert with_spin == no_spin == {_CHILD: 50.0}


def test_phases_guard_rejects_an_early_read_of_spinoff(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    actions = live_actions_as_of(fixture_store, session_close(date(2018, 12, 14))).filter(
        pl.col("action_type") == "spinoff"
    )
    assert actions.height == 1
    with pytest.raises(ValueError, match="not known"):
        phases._check_actions(actions, date(2018, 12, 14))
