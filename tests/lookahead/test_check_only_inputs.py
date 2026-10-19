"""Check-only inputs never reach a universe, a backtest or a paper run (#1682,
T189b; spec amendment 2026-10-14, "The check").

The S&P 500 answer key (`store/index_members.csv`, T189) is a committee's
after-the-fact selection, with look-ahead and survivorship in it, so it is a
`health` check and never a source:

- **Source scan.** No module under `src/` outside `health.py` and the loader
  imports `store.index_members` or names its file; a text scan, not only an
  AST walk, so a path or SQL string is caught too (`test_the_source_scan_*`).
- **Byte identity.** `universe_as_of`, a fixture backtest run and a fixture
  paper run give the same outputs with the key present, absent and emptied.
  The variants are served through `importlib.resources.files` (how the loader
  reads the packaged file) from a temp copy of the package's data files, and
  an audit hook records every `open` of a file named `index_members.csv` in
  the process, so a reader taking any other path is caught as well: none of
  the three runs may open it.

T189c adds its own cases here (the `openfigi_types` string scan and the
byte-identity runs with that table populated and empty).
"""

from __future__ import annotations

import ast
import importlib.resources
import re
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Literal

import duckdb
import pytest
from backtest.test_run import SLUG, _register_more
from conftest import load_universe_fixtures
from execution.test_run_trade import F_0, Clock, Env, at

import tradepartner.store
from tradepartner.backtest.holdout import Flags
from tradepartner.backtest.run import run_hypothesis
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import configure_connection
from tradepartner.store.index_members import load_index_members
from tradepartner.universe import universe_as_of

_SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
_KEY_FILE = "index_members.csv"
#: The two modules allowed to read the key: its loader and `health`.
_ALLOWED = frozenset({"health.py", "store/index_members.py"})
#: The `openfigi_types` check-only table (#1713, T189c): the string may appear
#: only where the table is defined (`store/schema.py`), written (`ingest.py`'s
#: `openfigi` branch) and read (`health.py`), never on a universe, signal,
#: backtest, paper or research path. `store/registry.py` names it only in its
#: trial data-vintage classification comment (the table is excluded there by
#: construction), so it is allowed by name, not as a reader.
_FIGI_ALLOWED = frozenset({"health.py", "ingest.py", "store/schema.py", "store/registry.py"})
#: A text naming the key's module or file: an import path, a file name or a
#: `from tradepartner.store import ..., index_members` list.
_NAMING = (
    re.compile(r"index_members\.(csv|py)\b"),
    re.compile(r"store[./]index_members\b"),
    re.compile(r"from\s+tradepartner\.store\s+import\s+\(?[^)]*?\bindex_members\b", re.S),
    re.compile(r"[\"']index_members[\"']"),
    re.compile(r"\b(?:FROM|JOIN)\s+index_members\b", re.I),
)

Variant = Literal["present", "absent", "emptied"]
VARIANTS: tuple[Variant, ...] = ("present", "absent", "emptied")

#: Paths named `index_members.csv` opened while `_OPENS` is armed (an audit
#: hook cannot be removed, so it is installed once and armed per run).
_OPENS: list[str] = []
_ARMED: list[bool] = [False]


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if _ARMED[0] and event == "open" and args and str(args[0]).endswith(_KEY_FILE):
        _OPENS.append(str(args[0]))


sys.addaudithook(_audit)


def _offences(relative: str, text: str) -> list[str]:
    """Why the module at `relative` (under `src/tradepartner/`) breaks the rule."""
    if relative in _ALLOWED:
        return []
    found = [f"{relative}: names the key ({p.pattern})" for p in _NAMING if p.search(text)]
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names = {alias.name for alias in node.names}
            if "index_members" in node.module or (
                node.module == "tradepartner.store" and "index_members" in names
            ):
                found.append(f"{relative}: imports the key's loader (line {node.lineno})")
        elif isinstance(node, ast.Import):
            if any("index_members" in alias.name for alias in node.names):
                found.append(f"{relative}: imports the key's loader (line {node.lineno})")
    return found


def test_the_source_scan_finds_no_reader_outside_health_and_the_loader() -> None:
    offences = [
        offence
        for path in sorted(_SRC.rglob("*.py"))
        for offence in _offences(path.relative_to(_SRC).as_posix(), path.read_text("utf-8"))
    ]
    assert offences == []
    # The allowed reader really reads it: the scan is not vacuous.
    assert "load_index_members" in (_SRC / "health.py").read_text("utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "from tradepartner.store.index_members import resolve\n",
        "from tradepartner.store import (\n    asof,\n    index_members,\n)\n",
        "import tradepartner.store.index_members as key\n",
        'PATH = "src/tradepartner/store/index_members.csv"\n',
        "SQL = \"SELECT * FROM read_csv('index_members.csv')\"\n",
        'mod = importlib.import_module("tradepartner.store.index_members")\n',
        'name = "index_members"\n',
        'SQL = "SELECT ticker FROM index_members"\n',
    ],
)
def test_the_source_scan_has_teeth(text: str) -> None:
    assert _offences("universe.py", text)
    assert _offences("health.py", text) == []


def test_a_config_key_or_report_field_of_that_name_is_not_an_offence() -> None:
    text = "index_members: bool = True\nblock.index_members.verdict\n"
    assert _offences("config.py", text) == []


def _figi_offences(relative: str, text: str) -> list[str]:
    """Why the module at `relative` (under `src/tradepartner/`) names the
    `openfigi_types` table outside the three modules allowed to."""
    if relative in _FIGI_ALLOWED or "openfigi_types" not in text:
        return []
    return [f"{relative}: names the openfigi_types table"]


def test_the_figi_source_scan_finds_no_reader_outside_the_check_and_its_writer() -> None:
    offences = [
        offence
        for path in sorted(_SRC.rglob("*.py"))
        for offence in _figi_offences(path.relative_to(_SRC).as_posix(), path.read_text("utf-8"))
    ]
    assert offences == []
    # The allowed reader really reads it: the scan is not vacuous.
    assert "openfigi_types" in (_SRC / "health.py").read_text("utf-8")


def test_the_figi_source_scan_has_teeth() -> None:
    assert _figi_offences("universe.py", "SELECT * FROM openfigi_types")
    assert _figi_offences("store/asof.py", 'TABLE = "openfigi_types"')
    assert _figi_offences("health.py", "FROM openfigi_types") == []
    assert _figi_offences("ingest.py", "openfigi_types") == []
    assert _figi_offences("store/schema.py", "CREATE TABLE openfigi_types") == []
    assert _figi_offences("store/registry.py", "# excluded: openfigi_types") == []


@contextmanager
def _served(variant: Variant, root: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`importlib.resources.files("tradepartner.store")` served from a temp
    copy of the package's data files with the key `variant`, the audit armed."""
    real = Path(tradepartner.store.__file__).parent
    data = root / variant
    data.mkdir(parents=True)
    for path in real.iterdir():
        if path.is_file() and path.suffix != ".py":
            shutil.copy2(path, data / path.name)
    if variant == "absent":
        (data / _KEY_FILE).unlink()
    elif variant == "emptied":
        (data / _KEY_FILE).write_text("", encoding="utf-8")
    original = importlib.resources.files

    def files(anchor: Any) -> Any:
        return data if anchor == "tradepartner.store" else original(anchor)

    with monkeypatch.context() as patch:
        patch.setattr(importlib.resources, "files", files)
        _OPENS.clear()
        _ARMED[0] = True
        try:
            yield
        finally:
            _ARMED[0] = False


def test_the_variants_are_what_they_say(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness: the loader sees each variant, and the audit sees its open."""
    with _served("present", tmp_path, monkeypatch):
        assert load_index_members().commit == "a2430f2af0c79ddf0748e91de11bdeb1616ab5a7"
        assert _OPENS
    with _served("absent", tmp_path, monkeypatch), pytest.raises(FileNotFoundError):
        load_index_members()
    with _served("emptied", tmp_path, monkeypatch), pytest.raises(ValueError, match="comment line"):
        load_index_members()


def _new_store(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, _FIXTURES)
    finally:
        conn.close()
    return path


def _universe(store: Path) -> str:
    settings = Settings(_env_file=None, store={"path": str(store)})
    conn = duckdb.connect(str(store), read_only=True)
    try:
        out = []
        for session in (date(2019, 12, 31), date(2020, 6, 30)):
            u = universe_as_of(conn, session_close(session), settings)
            out.append((u.members.to_dicts(), u.exclusions.to_dicts()))
    finally:
        conn.close()
    return repr(out)


def _backtest(store: Path) -> str:
    _register_more(store, SLUG)
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)
    assert outcome.status == "ok", outcome.error
    return repr(
        {
            level: (
                r.equity,
                r.rebalances,
                r.weights,
                r.targets,
                r.position_values.to_dicts(),
            )
            for level, r in sorted(outcome.results.items())
        }
    )


def _paper(store: Path, tmp_path: Path) -> str:
    settings = Settings(
        _env_file=None,
        store={"path": str(store)},
        alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48},
    )
    env = Env(settings, Clock(at(F_0)))
    env.open_window(tmp_path=tmp_path)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok"
    orders = env.query(
        "SELECT client_order_id, security_id, side, quantity FROM orders ORDER BY client_order_id"
    )
    fills = env.query("SELECT client_order_id, quantity, price FROM fills ORDER BY client_order_id")
    return repr((outcome.kind, sorted(env.held()), orders, fills, env.fake.positions()))


Run = Callable[[Path, Path], str]
RUNS: dict[str, Run] = {
    "universe": lambda store, _tmp: _universe(store),
    "backtest": lambda store, _tmp: _backtest(store),
    "paper": _paper,
}


@pytest.mark.parametrize("name", list(RUNS))
def test_the_run_is_byte_identical_with_the_key_present_absent_and_emptied(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)
    outputs: dict[Variant, str] = {}
    for variant in VARIANTS:
        work = tmp_path / name / variant
        store = _new_store(work / "store.duckdb")
        with _served(variant, tmp_path / "served" / name, monkeypatch):
            outputs[variant] = RUNS[name](store, work)
            assert _OPENS == [], f"{name} opened the key: {_OPENS}"
    assert outputs["present"] == outputs["absent"] == outputs["emptied"]
    assert len(outputs["present"]) > 100  # the run produced something to compare


def _populate_figi(store: Path) -> None:
    """One `openfigi_types` row for a fixture security, as the ingest writes
    one: a `common` name OpenFIGI also calls "Common Stock" (an agreement, so
    the check has nothing to report but the row must still reach no reader)."""
    conn = duckdb.connect(str(store))
    try:
        configure_connection(conn)
        conn.execute(
            "INSERT INTO openfigi_types (security_id, ticker, figi, composite_figi, "
            "share_class_figi, security_type, security_type2, market_sector, name, "
            "exch_code, mapped, known_at, ingested_at, provenance, source) "
            "SELECT 'SEC_FACTS_RESTATED', ticker, 'BBG1', 'BBG1', 'BBG2', 'Common Stock', "
            "'Common Stock', 'Equity', ticker, 'US', TRUE, known_at, ingested_at, "
            "'openfigi', 'openfigi' FROM listings WHERE security_id = 'SEC_FACTS_RESTATED' "
            "ORDER BY valid_from LIMIT 1"
        )
    finally:
        conn.close()


@pytest.mark.parametrize("name", list(RUNS))
def test_the_run_is_byte_identical_with_the_figi_table_populated_and_empty(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)
    outputs: dict[str, str] = {}
    for variant in ("empty", "populated"):
        work = tmp_path / f"figi-{name}" / variant
        store = _new_store(work / "store.duckdb")
        if variant == "populated":
            _populate_figi(store)
        outputs[variant] = RUNS[name](store, work)
    assert outputs["empty"] == outputs["populated"]
    assert len(outputs["empty"]) > 100  # the run produced something to compare
