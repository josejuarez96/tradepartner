"""T248c (#1762) point-in-time mergers: the fixture's cash and stock mergers are
invisible at the close of their `process_date` and visible at the next session's
close; truncation invariance holds with `mergers` in the truncated set; and nothing
a trial reads touches the table."""

from __future__ import annotations

import ast
import re
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.calendar import next_session, session_close
from tradepartner.store.asof import mergers_as_of

_SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
_FIXTURE_MERGERS = {
    "SEC_CLEAN_MERGER": "fixture-merger-cash-1",
    "SEC_DIV_CANCELLED": "fixture-merger-stock-1",
}


def test_each_fixture_merger_is_invisible_at_its_process_date_close_and_visible_at_the_next(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = fixture_store.execute(
        "SELECT security_id, source_action_id, process_date FROM mergers ORDER BY 1"
    ).fetchall()
    assert {sid: action_id for sid, action_id, _ in rows} == _FIXTURE_MERGERS
    for security_id, action_id, process_date in rows:
        same_day = session_close(process_date)
        next_close = session_close(next_session(process_date))
        assert mergers_as_of(fixture_store, same_day, [security_id]).is_empty()
        just_before = mergers_as_of(fixture_store, next_close - timedelta(microseconds=1))
        assert security_id not in just_before["security_id"].to_list()
        seen = mergers_as_of(fixture_store, next_close, [security_id])
        assert seen["source_action_id"].to_list() == [action_id]
        assert seen["known_at"].to_list() == [next_close]


def test_mergers_are_truncation_invariant(fixture_store: duckdb.DuckDBPyConnection) -> None:
    cut = TruncatedStore(fixture_store)
    try:
        assert "mergers" in cut.tables
        probes = probe_timestamps(fixture_store)
        assert len(probes) > 4
        for t in probes:
            full = mergers_as_of(fixture_store, t)
            assert full.equals(mergers_as_of(cut.at(t), t)), t
            filtered = mergers_as_of(fixture_store, t, ["SEC_SPLIT_PLAIN", "SEC_CLEAN_MERGER"])
            assert filtered.equals(
                mergers_as_of(cut.at(t), t, ["SEC_SPLIT_PLAIN", "SEC_CLEAN_MERGER"])
            ), t
    finally:
        cut.close()


@pytest.mark.parametrize(
    "path",
    [
        *sorted((_SRC / "backtest").rglob("*.py")),
        _SRC / "universe.py",
        _SRC / "gap.py",
    ],
    ids=lambda p: str(p.relative_to(_SRC)),
)
def test_nothing_a_trial_reads_names_the_mergers_table(path: Path) -> None:
    """No backtest module, `universe.py`, the signals (`backtest/signals*.py`) or
    the survivorship gap reads `mergers` (spec "Consumers"), except the one read the
    delisting failure-return amendment adds (#2670, plan T328b): the provider's
    `merger_exits`, read only at the data cutoff under a non-`none` policy and
    tested for look-ahead in `tests/backtest/test_store_provider.py`."""
    text = path.read_text()
    if path.relative_to(_SRC) in _T328B_READERS:
        text = _without_merger_exits(text)
    assert re.search(r"\bmergers(_as_of)?\b", text) is None


#: The modules allowed to name `mergers`, each only inside `merger_exits` (T328b).
_T328B_READERS = {Path("backtest/provider.py"), Path("backtest/store_provider.py")}


def _without_merger_exits(text: str) -> str:
    """`text` with the `merger_exits` method, the module docstring and the import list
    left out, so a second reader of `mergers` in the module still fails the guard."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    drop: set[int] = set()
    doc = tree.body[0]
    if isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant):
        drop.update(range(doc.lineno, (doc.end_lineno or doc.lineno) + 1))
    for node in ast.walk(tree):
        is_reader = isinstance(node, ast.FunctionDef) and node.name == "merger_exits"
        is_import = isinstance(node, ast.ImportFrom) and node.module == "tradepartner.store.asof"
        if is_reader or is_import:
            drop.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return "".join(line for number, line in enumerate(lines, 1) if number not in drop)


def test_the_guard_still_catches_a_second_mergers_reader() -> None:
    """The T328b exemption covers `merger_exits` only."""
    source = (_SRC / "backtest" / "store_provider.py").read_text()
    assert re.search(r"\bmergers_as_of\b", _without_merger_exits(source)) is None
    planted = source + "\n\ndef _sneak(conn):\n    return mergers_as_of(conn, None)\n"
    assert re.search(r"\bmergers_as_of\b", _without_merger_exits(planted)) is not None
