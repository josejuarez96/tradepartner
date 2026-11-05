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
    assert _mergers_reader_violations(path.read_text(), path.relative_to(_SRC)) == []


#: The two class methods allowed to read mergers for exit evidence (T328b).
_T328B_READERS = {
    Path("backtest/provider.py"): "DataProvider",
    Path("backtest/store_provider.py"): "StoreProvider",
}


def _mergers_reader_violations(source: str, path: Path) -> list[int]:
    """Return lines that name the merger reader or read its table outside T328b."""
    tree = ast.parse(source)
    allowed_class = _T328B_READERS.get(path)
    allowed_methods = {
        id(method)
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == allowed_class
        for method in cls.body
        if isinstance(method, ast.FunctionDef) and method.name == "merger_exits"
    }
    violations: list[int] = []

    class ReaderVisitor(ast.NodeVisitor):
        def visit_Module(self, node: ast.Module) -> None:
            body = node.body
            if path in _T328B_READERS and ast.get_docstring(node):
                body = body[1:]
            for statement in body:
                self.visit(statement)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if id(node) not in allowed_methods:
                self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                if alias.name in {"mergers", "mergers_as_of"}:
                    permitted = (
                        path == Path("backtest/store_provider.py")
                        and node.module == "tradepartner.store.asof"
                        and alias.name == "mergers_as_of"
                        and alias.asname is None
                    )
                    if not permitted:
                        violations.append(node.lineno)

        def visit_Import(self, node: ast.Import) -> None:
            if any("mergers" in alias.name.split(".") for alias in node.names):
                violations.append(node.lineno)

        def visit_Name(self, node: ast.Name) -> None:
            if node.id in {"mergers", "mergers_as_of"}:
                violations.append(node.lineno)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in {"mergers", "mergers_as_of"}:
                violations.append(node.lineno)
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if isinstance(node.value, str) and re.search(r"\bmergers(_as_of)?\b", node.value):
                violations.append(node.lineno)

    ReaderVisitor().visit(tree)
    return violations


@pytest.mark.parametrize(
    ("extra", "path"),
    [
        (
            "from tradepartner.store.asof import mergers_as_of as _m\n"
            "def sneak(conn):\n    return _m(conn, None)\n",
            Path("backtest/store_provider.py"),
        ),
        (
            "from tradepartner.store import asof as m\n"
            "def sneak(conn):\n    return m.mergers_as_of(conn, None)\n",
            Path("backtest/store_provider.py"),
        ),
        (
            "import tradepartner.store.mergers as m\n"
            "def sneak(conn):\n    return m.mergers_as_of(conn, None)\n",
            Path("backtest/store_provider.py"),
        ),
        (
            "class OtherProvider:\n"
            "    def merger_exits(self, conn):\n"
            "        return mergers_as_of(conn, None)\n",
            Path("backtest/store_provider.py"),
        ),
        (
            "def merger_exits(conn):\n    return mergers_as_of(conn, None)\n",
            Path("backtest/provider.py"),
        ),
        (
            "def sneak(conn):\n    return conn.sql('SELECT * FROM mergers')\n",
            Path("backtest/store_provider.py"),
        ),
        (
            "def sneak(conn):\n    return conn.sql('SELECT * FROM main.mergers')\n",
            Path("backtest/store_provider.py"),
        ),
        (
            "def sneak(conn):\n    return getattr(conn, 'mergers_as_of')\n",
            Path("backtest/store_provider.py"),
        ),
    ],
)
def test_guard_catches_second_reader(extra: str, path: Path) -> None:
    assert _mergers_reader_violations(extra, path)


@pytest.mark.parametrize("path", sorted(_T328B_READERS))
def test_guard_allows_real_provider_method(path: Path) -> None:
    assert _mergers_reader_violations((_SRC / path).read_text(), path) == []
