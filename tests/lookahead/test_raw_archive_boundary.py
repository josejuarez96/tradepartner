"""Only re-derive reads the raw archive; only named ingest writers write to it
(plan T346, #2751; ADR 0028 Look-ahead guard; data-foundation spec amendment
#2714 item 4).

The archive holds every raw body ever fetched, including bodies fetched after a
trial's data cutoff, and reading an evicted (dataless) file there silently
downloads it (T354). So the guard is the source, as for `index_members.csv`
(`test_check_only_inputs.py`). The scan covers `src/` and `scripts/`, denies by
default, and splits by capability:

- **Readers** (`read_body`, `download_inputs`, `ArchivedBody`): named only in
  `store/raw_archive.py` and `store/raw_rederive.py` (T352). Daily ingest never
  reads archived bodies (#2714 item 4), and readers import ingest modules
  transitively (`universe.py` -> `adapters/alpaca_prices.py`, `store/asof.py` ->
  `adapters/edgar.py`, `backtest/run.py` -> `ingest.py`), so a body reader in any
  ingest module would reach a backtest.
- **Writers** (`_WRITERS`, a named list): may call the write side
  (`archive_body`, `archive_manifest`, `is_archived`, `evict_uploaded`,
  `read_manifest`, `MacICloud`) but never a reader, the body path builder or the
  archive root.
- **Everything else**, including every unlisted adapter: may not name the archive
  at all (the helper, its config module, `settings.raw_archive`,
  `TRADEPARTNER_RAW_ARCHIVE`, the re-derive module).

A text scan, not only an AST walk, so a dynamic import, an environment read or a
settings attribute is caught too. T347, T348, T352 and T361 extend the lists for
the modules they add.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src" / "tradepartner"
_SCRIPTS = _REPO / "scripts"

#: May read bodies: the helper itself and re-derive.
_READERS_ALLOWED = frozenset({"store/raw_archive.py", "store/raw_rederive.py"})
#: May name the archive's root and config: they define and check it.
_ROOT_ALLOWED = frozenset({"config.py", "store/raw_archive_config.py", *_READERS_ALLOWED})
#: The ingest modules that may write through the helper (#2714 items 1 and 5):
#: the driver, the Form 4 XML and daily-index fetchers, the insider bulk zips and
#: the two Form 4 routes. Every other adapter is denied by default.
_WRITERS = frozenset(
    {
        "ingest.py",
        "adapters/daily_index.py",
        "adapters/edgar_insider_bulk.py",
        "adapters/edgar_raw.py",
        "adapters/form4_bulk_route.py",
        "adapters/form4_route.py",
    }
)
#: Any text naming the archive or the re-derive module.
_NAMING = re.compile(r"raw_archive|raw_rederive", re.I)
#: Body-reading names: refused everywhere outside `_READERS_ALLOWED`.
_READER_NAMES = re.compile(r"\b(?:read_body|download_inputs|ArchivedBody)\b")
#: Names a writer may not use: they build archive paths or the root.
_PATH_NAMES = re.compile(
    r"\b(?:body_path|require_archive_root|archive_root|BODY_DIR)\b|tradepartner_raw_archive",
    re.I,
)


def _line(text: str, match: re.Match[str]) -> int:
    return text.count("\n", 0, match.start()) + 1


def _offences(relative: str, text: str) -> list[str]:
    """Why the module at `relative` (under `src/tradepartner/`, or `scripts/...`)
    breaks the rule."""
    if relative in _READERS_ALLOWED:
        return []
    found = [
        f"{relative}:{_line(text, m)}: names an archive body reader ({m.group(0)})"
        for m in _READER_NAMES.finditer(text)
    ]
    if relative in _ROOT_ALLOWED:
        return found
    if relative in _WRITERS:
        found += [
            f"{relative}:{_line(text, m)}: a writer names an archive path ({m.group(0)})"
            for m in _PATH_NAMES.finditer(text)
        ]
        if re.search(r"raw_rederive", text):
            found.append(f"{relative}: a writer names the re-derive module")
        return found
    found += [f"{relative}:{_line(text, m)}: names the raw archive" for m in _NAMING.finditer(text)]
    return found


def _modules() -> list[tuple[str, str]]:
    out = [(p.relative_to(_SRC).as_posix(), p.read_text("utf-8")) for p in _SRC.rglob("*.py")]
    out += [
        ("scripts/" + p.relative_to(_SCRIPTS).as_posix(), p.read_text("utf-8"))
        for p in _SCRIPTS.rglob("*.py")
    ]
    return sorted(out)


def test_the_source_scan_finds_no_archive_opener_outside_its_allowlists() -> None:
    modules = _modules()
    assert any(rel.startswith("scripts/") for rel, _ in modules)
    offences = [offence for rel, text in modules for offence in _offences(rel, text)]
    assert offences == []
    # The allowed opener really opens archive paths: the scan is not vacuous.
    helper = (_SRC / "store" / "raw_archive.py").read_text("utf-8")
    assert "require_archive_root" in helper
    assert "def read_body" in helper


def test_every_listed_writer_exists() -> None:
    """A renamed module must not leave a stale allowance behind."""
    for relative in _WRITERS | {"config.py", "store/raw_archive_config.py"}:
        assert (_SRC / relative).is_file(), relative


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
    "adapters/alpaca_prices.py",
    "adapters/fixture_prices.py",
    "adapters/edgar.py",
    "adapters/filings.py",
    "adapters/new_broker.py",
    "scripts/repair_stamps.py",
    "scripts/new_tool.py",
]

_OPENERS = [
    "from tradepartner.store.raw_archive import archive_body\n",
    "from tradepartner.store.raw_archive import read_body\n",
    "from tradepartner.store import (\n    asof,\n    raw_archive,\n)\n",
    "import tradepartner.store.raw_archive as ra\n",
    'mod = importlib.import_module("tradepartner.store.raw_archive")\n',
    "from tradepartner.store.raw_archive_config import require_archive_root\n",
    "root = settings.tradepartner_raw_archive.get_secret_value()\n",
    'root = os.environ["TRADEPARTNER_RAW_ARCHIVE"]\n',
    "floor = settings.raw_archive.min_free_disk_gb\n",
    "from tradepartner.store.raw_rederive import rederive\n",
    "data = read_body(settings, sha, icloud)\n",
    "got = download_inputs(settings, hashes, icloud)\n",
]


@pytest.mark.parametrize("module", _READERS)
@pytest.mark.parametrize("text", _OPENERS)
def test_the_scan_refuses_any_archive_name_outside_the_allowlists(module: str, text: str) -> None:
    """Deny by default: a backtest, universe, signal or research reader, an
    unlisted adapter (also a new one) or a script may not name the archive."""
    assert _offences(module, text)


_WRITE_SIDE = [
    "from tradepartner.store.raw_archive import archive_body, archive_manifest\n",
    "from tradepartner.store import raw_archive\nraw_archive.evict_uploaded(s, h, i)\n",
    "from tradepartner.store.raw_archive import is_archived, read_manifest, MacICloud\n",
    "floor = settings.raw_archive.min_free_disk_gb\n",
]


@pytest.mark.parametrize("module", sorted(_WRITERS))
@pytest.mark.parametrize("text", _WRITE_SIDE)
def test_a_listed_writer_may_use_the_write_side(module: str, text: str) -> None:
    assert _offences(module, text) == []


_READ_SIDE = [
    "from tradepartner.store.raw_archive import read_body\n",
    "from tradepartner.store import raw_archive\nraw_archive.download_inputs(s, h, i)\n",
    "def f(x: ArchivedBody) -> None: ...\n",
    "from tradepartner.store.raw_archive import body_path\n",
    "from tradepartner.store.raw_archive_config import require_archive_root\n",
    "root = settings.tradepartner_raw_archive\n",
    'root = os.environ["TRADEPARTNER_RAW_ARCHIVE"]\n',
    "from tradepartner.store.raw_rederive import rederive\n",
]


@pytest.mark.parametrize("module", sorted(_WRITERS))
@pytest.mark.parametrize("text", _READ_SIDE)
def test_a_writer_may_not_read_bodies_or_build_archive_paths(module: str, text: str) -> None:
    assert _offences(module, text)


@pytest.mark.parametrize("module", ["config.py", "store/raw_archive_config.py"])
def test_the_config_modules_may_not_read_bodies(module: str) -> None:
    assert _offences(module, "from tradepartner.store.raw_archive import read_body\n")
    assert _offences(module, "root = settings.tradepartner_raw_archive\n") == []


@pytest.mark.parametrize("module", sorted(_READERS_ALLOWED))
@pytest.mark.parametrize("text", _OPENERS + _READ_SIDE)
def test_rederive_and_the_helper_may_read(module: str, text: str) -> None:
    assert _offences(module, text) == []


def test_unrelated_text_is_not_an_offence() -> None:
    text = 'ARCHIVE = "data/archive"\n# the raw files are kept by ingest\nraw = archive\n'
    assert _offences("universe.py", text) == []
    assert _offences("scripts/repair_stamps.py", "def manifest_path(d): ...\n") == []
