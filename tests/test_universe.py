"""Tests for `universe_as_of` (ADR 0006 rules 1-8; plan T13).

Probe times are the fixture README's documented `probe T` values.
"""

from __future__ import annotations

import ast
import csv
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import RULES, Universe, universe_as_of

UNIVERSE_DIR = Path(__file__).resolve().parent / "fixtures" / "universe"

T_DUAL = datetime(2018, 12, 17, 21, 0, tzinfo=UTC)
T_SPLIT_FUTURE = datetime(2018, 11, 23, 18, 0, tzinfo=UTC)
T_SPLIT_BETWEEN = datetime(2019, 1, 28, 21, 0, tzinfo=UTC)
T_STALE = datetime(2020, 4, 6, 20, 0, tzinfo=UTC)
T_TRANSFER = datetime(2018, 10, 25, 20, 0, tzinfo=UTC)
T_WINDOW_DELIST = datetime(2019, 6, 24, 20, 0, tzinfo=UTC)
T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)


def _settings(**universe: Any) -> Settings:
    return Settings(_env_file=None, universe=universe)


def _raw_close(security_id: str, session: date) -> float:
    with (UNIVERSE_DIR / "prices_daily.csv").open(newline="") as fh:
        rows = [
            r
            for r in csv.DictReader(fh)
            if r["security_id"] == security_id and r["session"] == session.isoformat()
        ]
    first_seen = min(rows, key=lambda r: r["known_at"])
    return float(first_seen["close"])


def _members(u: Universe) -> set[str]:
    return set(u.members["security_id"].to_list())


def _excluded(u: Universe) -> dict[str, tuple[int, str, str]]:
    return {
        row["security_id"]: (row["rule"], row["rule_name"], row["reason"])
        for row in u.exclusions.iter_rows(named=True)
    }


def _member(u: Universe, security_id: str) -> dict[str, Any]:
    (row,) = u.members.filter(u.members["security_id"] == security_id).iter_rows(named=True)
    return row


def test_rules_are_the_adr_rules_in_order() -> None:
    assert RULES == (
        "security_type",
        "exchange",
        "sector",
        "price",
        "liquidity",
        "history",
        "shares",
        "size",
    )


def test_a_bare_date_is_refused(fixture_store: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TypeError):
        universe_as_of(fixture_store, date(2019, 1, 28), _settings())  # type: ignore[arg-type]


def test_every_known_security_is_a_member_or_excluded_once(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_LATE, _settings())
    members, excluded = _members(u), set(u.exclusions["security_id"].to_list())
    assert not members & excluded
    assert u.exclusions.height == len(excluded)
    sql = "SELECT DISTINCT security_id FROM securities WHERE known_at <= ?"
    known = {r[0] for r in fixture_store.execute(sql, [T_LATE]).fetchall()}
    assert members | excluded == known


def test_first_failing_rule_is_reported(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # SEC_UNCLASSIFIABLE also has under 12 months of bars at T_LATE (bars
    # from 2019-03), yet rule 1 comes first.
    excluded = _excluded(universe_as_of(fixture_store, T_LATE, _settings()))
    assert excluded["SEC_UNCLASSIFIABLE"] == (1, "security_type", "unclassifiable")
    assert excluded["SEC_SPY"][:2] == (1, "security_type")
    assert excluded["SEC_MTUM"][:2] == (1, "security_type")


def test_dual_class_cap_is_summed_and_both_common_classes_admitted(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, T_DUAL, _settings())
    assert {"SEC_DUAL_A", "SEC_DUAL_B"} <= _members(u)
    assert _excluded(u)["SEC_DUAL_PFD"][:2] == (1, "security_type")
    session = date(2018, 12, 17)
    expected = 10_000_000 * _raw_close("SEC_DUAL_A", session) + 4_000_000 * _raw_close(
        "SEC_DUAL_B", session
    )
    a, b = _member(u, "SEC_DUAL_A"), _member(u, "SEC_DUAL_B")
    assert a["company_cap"] == b["company_cap"] == pytest.approx(expected)
    assert a["company_rank"] == b["company_rank"]


def test_split_with_ex_date_on_or_before_t_adjusts_shares(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Shares fact 5M as of 2018-10-29, 3-for-1 split ex 2019-01-11 <= T.
    row = _member(universe_as_of(fixture_store, T_SPLIT_BETWEEN, _settings()), "SEC_SPLIT_BETWEEN")
    assert row["shares"] == pytest.approx(15_000_000)
    close = _raw_close("SEC_SPLIT_BETWEEN", date(2019, 1, 28))
    assert row["close"] == pytest.approx(close)
    assert row["market_cap"] == pytest.approx(15_000_000 * close)


def test_split_known_before_t_with_ex_date_after_t_is_ignored(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # 4-for-1 split known 2018-11-15, ex 2019-02-14 > T: raw close, raw shares.
    row = _member(universe_as_of(fixture_store, T_SPLIT_FUTURE, _settings()), "SEC_SPLIT_FUTURE")
    close = _raw_close("SEC_SPLIT_FUTURE", date(2018, 11, 23))
    assert row["shares"] == pytest.approx(7_000_000)
    assert row["close"] == pytest.approx(close)
    assert row["market_cap"] == pytest.approx(7_000_000 * close)


def test_stale_shares_fact_fails_rule_7(fixture_store: duckdb.DuckDBPyConnection) -> None:
    excluded = _excluded(universe_as_of(fixture_store, T_STALE, _settings()))
    assert excluded["SEC_FACTS_STALE"] == (7, "shares", "stale_shares")


def test_a_known_form_25_removes_the_listing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # Between the Form 25's known_at and the new listing's: delisted at T.
    excluded = _excluded(universe_as_of(fixture_store, T_TRANSFER, _settings()))
    assert excluded["SEC_TRANSFER"] == (2, "exchange", "delisted")


def test_delisted_name_present_on_its_last_session_absent_after(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert "SEC_WINDOW_DELIST" in _members(
        universe_as_of(fixture_store, T_WINDOW_DELIST, _settings())
    )
    later = datetime(2019, 12, 31, 21, 0, tzinfo=UTC)
    assert _excluded(universe_as_of(fixture_store, later, _settings()))["SEC_WINDOW_DELIST"][0] == 2


def test_utility_sic_fails_rule_3(fixture_store: duckdb.DuckDBPyConnection) -> None:
    known = datetime(2018, 1, 2, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "classifications",
        {
            "security_id": "SEC_SPLIT_BETWEEN",
            "sic": 4911,
            "security_type": "common",
            "rule": "common_default",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    excluded = _excluded(universe_as_of(fixture_store, T_SPLIT_BETWEEN, _settings()))
    assert excluded["SEC_SPLIT_BETWEEN"] == (3, "sector", "sic:4911")


def test_liquidity_rule_off_is_skipped_and_recorded(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    on = universe_as_of(fixture_store, T_LATE, _settings(min_median_dollar_volume=1e15))
    assert _members(on) == set()
    assert any(rule == 5 for rule, _, _ in _excluded(on).values())
    off = universe_as_of(
        fixture_store,
        T_LATE,
        _settings(min_median_dollar_volume=1e15, liquidity_rule_enabled=False),
    )
    assert off.rules_enabled["liquidity"] is False
    assert all(rule != 5 for rule, _, _ in _excluded(off).values())
    assert _members(off)


def test_output_records_rules_and_settings(fixture_store: duckdb.DuckDBPyConnection) -> None:
    settings = _settings()
    u = universe_as_of(fixture_store, T_LATE, settings)
    assert u.t == T_LATE
    assert u.session == date(2019, 6, 28)
    assert set(u.rules_enabled) == set(RULES)
    assert u.settings["fill_price"] == settings.execution.fill_price
    assert u.settings["universe"] == settings.universe.model_dump(mode="json")


def test_override_settings_change_the_result_without_the_environment(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    env_before = dict(os.environ)
    default = universe_as_of(fixture_store, T_LATE, _settings())
    top1 = universe_as_of(fixture_store, T_LATE, _settings(top_n_by_cap=1))
    assert dict(os.environ) == env_before
    assert len(set(default.members["cik"].to_list())) > 1
    assert len(set(top1.members["cik"].to_list())) == 1
    assert _members(top1) < _members(default)
    ranks = default.members.sort("company_rank")
    assert set(top1.members["cik"].to_list()) == {ranks["cik"][0]}


@pytest.mark.parametrize(
    ("override", "rule"),
    [
        ({"security_types": ["preferred"]}, 1),
        ({"exchanges": ["NYSE_AMERICAN"]}, 2),
        ({"min_price": 1e9}, 4),
        ({"min_median_dollar_volume": 1e15}, 5),
        ({"min_history_months": 120}, 6),
        ({"max_shares_age_days": 1}, 7),
        ({"top_n_by_cap": 1}, 8),
    ],
)
def test_each_key_override_changes_membership(
    fixture_store: duckdb.DuckDBPyConnection, override: dict[str, Any], rule: int
) -> None:
    default = universe_as_of(fixture_store, T_LATE, _settings())
    changed = universe_as_of(fixture_store, T_LATE, _settings(**override))
    assert _members(changed) != _members(default)
    newly_out = _members(default) - _members(changed)
    assert newly_out
    assert {_excluded(changed)[sid][0] for sid in newly_out} == {rule}


def test_universe_module_has_no_numeric_literals_but_0_1_minus_1() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "tradepartner" / "universe.py"
    tree = ast.parse(path.read_text())
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    }
    assert literals <= {0, 1, -1}
