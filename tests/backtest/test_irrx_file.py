"""The `irrx` family's registration (backtest plan T414; spec amendment 2026-11-16, #3504,
items 6, 11 and 14 "Registration"). The twin is `tests/fixtures/hypotheses/fixture-irrx.md`:
nothing here runs a signal (`engine_ready` is False until T417)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, get_args

import duckdb
import pytest

from tradepartner.backtest import frozen, hypothesis, strategies
from tradepartner.backtest.hypothesis import HypothesisFileError
from tradepartner.config import (
    ENGINE_FAMILIES,
    FAMILIES,
    PAPER_FAMILIES,
    HypothesisFamily,
    Settings,
)
from tradepartner.store import registry, schema
from tradepartner.store.registry import RegistryError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses"
IRRX = FIXTURES / "fixture-irrx.md"


@pytest.fixture
def conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(str(tmp_path / "scratch.duckdb"))
    schema.init_schema(connection)
    return connection


def _variant(tmp_path: Path, old: str, new: str) -> Path:
    """The twin with the first `old` replaced by `new` (`new` may be empty)."""
    text = IRRX.read_text()
    assert old in text, old
    path = tmp_path / "hypotheses" / IRRX.name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text.replace(old, new, 1))
    return path


def _register(conn: duckdb.DuckDBPyConnection, path: Path, settings: Settings) -> Any:
    return hypothesis.register(conn, path, registered_by="test", settings=settings)


def test_registry_refuses_irrx_until_the_engine_is_ready(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """`engine_ready = False` (T417 flips it): the registry's engine-family guard holds."""
    with pytest.raises(RegistryError, match="experiment-only"):
        _register(conn, IRRX, settings)


def test_the_twin_registers_and_stores_no_strategy_key(
    conn: duckdb.DuckDBPyConnection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # What T417 makes true: the family is an engine family. Only the guard is patched.
    monkeypatch.setattr(registry, "ENGINE_FAMILIES", (*ENGINE_FAMILIES, "irrx"))
    parsed = hypothesis.parse_file(IRRX)
    required = hypothesis.required_keys("irrx")
    assert required <= set(parsed.file_params)
    assert {k for k in required if k.startswith("irrx.")} == {
        key for key, _default, _version in frozen.FROZEN_KEY_DEFAULTS if key.startswith("irrx.")
    }
    record = _register(conn, IRRX, settings)
    assert record.family == "irrx"
    assert set(record.params) == set(hypothesis.family_frozen_keys("irrx"))
    assert not any(
        k.startswith(("strategy.", "profitability.", "combined.")) for k in record.params
    )
    assert record.params["portfolio.side"] == "long_short"
    assert record.params["portfolio.net_exposure"] == 0.0
    assert record.params["schedule.rebalance_cadence"] == "month_end"
    assert record.params["execution.fill_price"] == "open"
    assert frozen.inert_sections("irrx") == frozen._all_sections() - {"irrx"}


@pytest.mark.parametrize(
    "key", sorted(k for k, _d, _v in frozen.FROZEN_KEY_DEFAULTS if k[:5] == "irrx.")
)
def test_a_missing_irrx_key_is_refused(tmp_path: Path, key: str) -> None:
    name = key.partition(".")[2]
    text = IRRX.read_text()
    (line,) = re.findall(rf"^{name} = .*$", text.split("[portfolio]")[0], re.MULTILINE)
    path = _variant(tmp_path, line + "\n", "")
    with pytest.raises(HypothesisFileError, match=re.escape(key)):
        hypothesis.parse_file(path)


@pytest.mark.parametrize(
    "extra", ["[strategy]\ntop_fraction = 0.1", "[profitability]\ntop_fraction = 0.1"]
)
def test_another_family_signal_section_is_refused(tmp_path: Path, extra: str) -> None:
    path = _variant(tmp_path, "[portfolio]", f"{extra}\n\n[portfolio]")
    with pytest.raises(HypothesisFileError, match="irrx"):
        hypothesis.parse_file(path)


def test_an_irrx_key_in_a_momentum_file_is_refused(tmp_path: Path) -> None:
    momentum = FIXTURES / "fixture-momentum.md"
    text = momentum.read_text().replace("[gap]", "[irrx]\ntop_fraction = 0.2\n\n[gap]", 1)
    path = tmp_path / "hypotheses" / momentum.name
    path.parent.mkdir()
    path.write_text(text)
    with pytest.raises(HypothesisFileError, match=r"irrx\.top_fraction"):
        hypothesis.parse_file(path)


def test_hold_and_a_band_are_refused_portfolio() -> None:
    settings = Settings(_env_file=None)
    base = hypothesis.frozen_params(hypothesis.parse_file(IRRX), settings)
    assert hypothesis.long_short_refusal(base) is None
    for extra in ({"strategy.weighting": "hold"}, {"strategy.rebalance_band": 0.05}):
        refusal = hypothesis.long_short_refusal({**base, **extra})
        assert refusal is not None
        assert refusal.startswith("refused_portfolio")
    with pytest.raises(ValueError, match="equal"):
        Settings(_env_file=None, irrx={"weighting": "hold"})


def test_no_borrow_grid_is_refused_borrow_rate(
    tmp_path: Path,
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "ENGINE_FAMILIES", (*ENGINE_FAMILIES, "irrx"))
    path = _variant(
        tmp_path, "borrow_sensitivity_bps = [0.0, 300.0]", "borrow_sensitivity_bps = [0.0]"
    )
    with pytest.raises(HypothesisFileError, match="refused_borrow_rate"):
        _register(conn, path, settings)
    path = _variant(tmp_path, "borrow_bps_per_year = 30.0", "borrow_bps_per_year = 0.0")
    with pytest.raises(HypothesisFileError, match="refused_borrow_rate"):
        _register(conn, path, settings)


def test_the_literal_equals_the_registry_and_the_family_has_no_signal_yet() -> None:
    assert tuple(get_args(HypothesisFamily)) == tuple(FAMILIES)
    spec = FAMILIES["irrx"]
    assert spec.sections == ("irrx",)
    assert spec.parent is None
    assert (spec.engine_ready, spec.paper_ready) == (False, False)
    assert spec.benchmark is None
    assert spec.sweepable_keys == ()
    assert "irrx" not in ENGINE_FAMILIES
    assert "irrx" not in PAPER_FAMILIES
    with pytest.raises(KeyError):
        strategies.signal_for("irrx")


def test_the_count_names_and_reasons_are_item_6s() -> None:
    spec = FAMILIES["irrx"]
    assert spec.count_names == (
        "n_ranked",
        "n_excluded_no_sic",
        "n_excluded_thin_industry",
        "n_excluded_no_events",
        "n_excluded_stamp",
        "n_excluded_late_event",
        "n_excluded_short_window",
        "n_excluded_adv",
        "n_adjusted",
        "n_dropped_sessions",
    )
    # Item 6's precedence: the first failing rule decides a name's reason.
    assert spec.exclusion_reasons == (
        "no_sic",
        "no_events",
        "stamp",
        "late_event",
        "thin_industry",
        "short_window",
        "adv",
        "no_t0_bar",
    )
