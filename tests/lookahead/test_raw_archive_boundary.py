"""Only ingest and re-derive code may open the raw archive (plan T346, #2751; ADR
0028 Look-ahead guard; data-foundation spec amendment #2714 item 4).

The archive holds every raw body ever fetched, including bodies fetched after a
trial's data cutoff, and reading an evicted (dataless) file there silently
downloads it (T354). So the guard is the source, as for `index_members.csv`
(`test_check_only_inputs.py`): no module under `src/` outside the allowlist may
name the archive helper, its path setting or its config. A text scan, not only an
AST walk, so a dynamic import, an environment read or a settings attribute is
caught too. Backtest, universe, signal and research readers are rejected by name
below; every other module is rejected by default.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"

#: The modules that may name the archive. `config.py` defines the setting;
#: `store/raw_archive_config.py` checks it; `store/raw_archive.py` is the one
#: module that opens archive paths. Ingest (`ingest.py`, the ingest adapters) and
#: re-derive (`store/raw_rederive.py`, T352) call the helper.
_ALLOWED = frozenset(
    {
        "config.py",
        "ingest.py",
        "store/raw_archive.py",
        "store/raw_archive_config.py",
        "store/raw_rederive.py",
    }
)
#: Ingest adapters may call the helper; the broker side of `adapters/` may not.
_ADAPTERS = "adapters/"
_BROKER_ADAPTERS = frozenset(
    {
        "adapters/alpaca_broker.py",
        "adapters/alpaca_trading_raw.py",
        "adapters/broker.py",
        "adapters/broker_refusals.py",
        "adapters/fake_broker.py",
    }
)
#: Any text naming the archive: the helper or its config module (import path or
#: dynamic import string), `Settings.raw_archive`, `Settings.tradepartner_raw_archive`
#: or the `TRADEPARTNER_RAW_ARCHIVE` variable, and the re-derive module that reads
#: through the helper.
_NAMING = re.compile(r"raw_archive|raw_rederive", re.I)


def _allowed(relative: str) -> bool:
    if relative in _ALLOWED:
        return True
    return relative.startswith(_ADAPTERS) and relative not in _BROKER_ADAPTERS


def _offences(relative: str, text: str) -> list[str]:
    """Why the module at `relative` (under `src/tradepartner/`) breaks the rule."""
    if _allowed(relative):
        return []
    found = [
        f"{relative}:{text.count(chr(10), 0, m.start()) + 1}: names the raw archive"
        for m in _NAMING.finditer(text)
    ]
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names = {alias.name for alias in node.names}
            if node.module == "tradepartner.store" and names & {
                "raw_archive",
                "raw_archive_config",
                "raw_rederive",
            }:
                found.append(f"{relative}: imports the raw archive (line {node.lineno})")
    return found


def test_the_source_scan_finds_no_archive_opener_outside_ingest_and_rederive() -> None:
    offences = [
        offence
        for path in sorted(_SRC.rglob("*.py"))
        for offence in _offences(path.relative_to(_SRC).as_posix(), path.read_text("utf-8"))
    ]
    assert offences == []
    # The allowed opener really opens archive paths: the scan is not vacuous.
    helper = (_SRC / "store" / "raw_archive.py").read_text("utf-8")
    assert "require_archive_root" in helper
    assert "def read_body" in helper


_READERS = [
    "backtest/run.py",
    "backtest/engine.py",
    "backtest/signals.py",
    "backtest/signals_combined.py",
    "backtest/store_provider.py",
    "backtest/provider.py",
    "universe.py",
    "gap.py",
    "research/experiment.py",
    "research/event_study/study.py",
    "research/labeling/gold.py",
    "store/asof.py",
    "execution/run.py",
    "dashboard/lab_run.py",
    "health.py",
    "cli.py",
    "adapters/alpaca_broker.py",
]

_OPENERS = [
    "from tradepartner.store.raw_archive import read_body\n",
    "from tradepartner.store import (\n    asof,\n    raw_archive,\n)\n",
    "import tradepartner.store.raw_archive as ra\n",
    'mod = importlib.import_module("tradepartner.store.raw_archive")\n',
    "from tradepartner.store.raw_archive_config import require_archive_root\n",
    "root = settings.tradepartner_raw_archive.get_secret_value()\n",
    'root = os.environ["TRADEPARTNER_RAW_ARCHIVE"]\n',
    "floor = settings.raw_archive.min_free_disk_gb\n",
    "from tradepartner.store.raw_rederive import rederive\n",
]


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize("text", _OPENERS)
def test_the_source_scan_refuses_an_archive_opener_in_a_reader(reader: str, text: str) -> None:
    assert _offences(reader, text)


@pytest.mark.parametrize(
    "module",
    [
        "ingest.py",
        "adapters/edgar_raw.py",
        "adapters/daily_index.py",
        "adapters/edgar_insider_bulk.py",
        "adapters/form4_route.py",
        "store/raw_rederive.py",
        "store/raw_archive.py",
    ],
)
@pytest.mark.parametrize("text", _OPENERS)
def test_ingest_and_rederive_may_open_the_archive(module: str, text: str) -> None:
    assert _offences(module, text) == []


def test_unrelated_text_is_not_an_offence() -> None:
    text = 'ARCHIVE = "data/archive"\n# the raw files are kept by ingest\nraw = archive\n'
    assert _offences("universe.py", text) == []
