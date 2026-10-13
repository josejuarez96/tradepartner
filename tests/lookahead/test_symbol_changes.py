"""T172b point-in-time name changes and spin-off stamp guard."""

from __future__ import annotations

import csv
import shutil
from datetime import date
from pathlib import Path

import duckdb
import pytest

from lookahead.broken_adapter import UNIVERSE_DIR, early_spinoff
from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.adapters.fixture_prices import FixtureContractError, FixturePriceSource
from tradepartner.calendar import session_close
from tradepartner.health import SPINOFF_KNOWN_AT_FLOOR, _new_action_stamp_rules
from tradepartner.store.asof import live_actions_as_of, symbol_changes_as_of


def test_name_change_appears_after_process_date(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    process = date(2018, 11, 7)
    before = symbol_changes_as_of(fixture_store, session_close(process), ["SEC_TICKCHANGE"])
    after = symbol_changes_as_of(
        fixture_store, session_close(date(2018, 11, 8)), ["SEC_TICKCHANGE"]
    )
    assert before.is_empty()
    assert after.select("old_symbol", "new_symbol").row(0) == ("TCKA", "TCKB")
    assert after["known_at"].to_list() == [session_close(date(2018, 11, 8))]


def test_new_facts_are_truncation_invariant(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    cut = TruncatedStore(fixture_store)
    try:
        assert {"symbol_changes", "corporate_actions"} <= set(cut.tables)
        for t in probe_timestamps(fixture_store):
            assert symbol_changes_as_of(fixture_store, t).equals(symbol_changes_as_of(cut.at(t), t))
            assert live_actions_as_of(fixture_store, t).equals(live_actions_as_of(cut.at(t), t))
    finally:
        cut.close()


def test_broken_adapter_early_spinoff_fails_stamp_rule(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    broken = early_spinoff()
    action = broken.record
    assert action.known_at < session_close(date(2018, 12, 12))
    fixture_store.execute(
        "UPDATE corporate_actions SET known_at=? WHERE action_type='spinoff'",
        [action.known_at],
    )
    assert _new_action_stamp_rules(fixture_store)[SPINOFF_KNOWN_AT_FLOOR].height == 1


def test_broken_adapter_early_spinoff_is_refused_by_the_fixture_contract(
    tmp_path: Path,
) -> None:
    """The broken adapter's early spin-off, written into a copy of the
    universe fixture, is refused by contract rule 2's per-type bound."""
    early = early_spinoff().record
    universe = tmp_path / "universe"
    shutil.copytree(UNIVERSE_DIR, universe)
    path = universe / "corporate_actions.csv"
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    spins = [row for row in rows if row["action_type"] == "spinoff"]
    assert len(spins) == 1
    spins[0]["known_at"] = early.known_at.isoformat()
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(FixtureContractError, match="spinoff"):
        FixturePriceSource(universe)
