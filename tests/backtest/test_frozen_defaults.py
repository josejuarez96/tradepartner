"""Frozen-key defaults, `frozen_values`, the canonical set and the fingerprint
(strategy-lab spec "Pre-lab registrations and the defaults table", as far as
`backtest/frozen.py` reaches; T96)."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tradepartner.backtest import frozen, hypothesis
from tradepartner.config import Settings

SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"
SCHEDULE_KEYS = ("schedule.rebalance_cadence", "schedule.signal_anchor")
IN_SAMPLE_START = date(2019, 11, 29)


@dataclass(frozen=True)
class _Record:
    params: dict[str, Any] = field(default_factory=dict)


def _new_params(settings: Settings) -> dict[str, Any]:
    """A file registered after the lab: both `schedule` keys stored."""
    return hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), settings)


def _pre_lab_params(settings: Settings) -> dict[str, Any]:
    """The same file as registered before the lab: no `schedule.*` keys."""
    return {k: v for k, v in _new_params(settings).items() if k not in SCHEDULE_KEYS}


def test_existing_table_entries_are_pinned_by_value() -> None:
    """Append-only and immutable: a later entry goes after these, never in place."""
    assert frozen.FROZEN_KEY_DEFAULTS[:2] == (
        ("schedule.rebalance_cadence", "month_end", 12),
        ("schedule.signal_anchor", "month_end", 12),
    )


def test_frozen_keys_outside_the_baseline_all_have_table_entries() -> None:
    keys = set(hypothesis.frozen_keys())
    table = {key for key, _default, _version in frozen.FROZEN_KEY_DEFAULTS}
    assert keys - frozen.LAB_BASELINE_FROZEN_KEYS <= table
    assert keys >= frozen.LAB_BASELINE_FROZEN_KEYS  # no frozen key removed or renamed
    assert table <= keys


def test_table_defaults_are_the_config_defaults() -> None:
    dumped = Settings(_env_file=None).model_dump(mode="json")  # type: ignore[call-arg]
    for key, default, _version in frozen.FROZEN_KEY_DEFAULTS:
        section, _, name = key.partition(".")
        assert dumped[section][name] == default, key


def test_frozen_module_is_a_leaf() -> None:
    tree = ast.parse((SRC / "backtest" / "frozen.py").read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    ours = {m for m in imported if m.startswith("tradepartner")}
    assert ours == {"tradepartner.config"}


def test_pre_lab_record_reads_month_end_and_keeps_its_hash(settings: Settings) -> None:
    from tradepartner.store import registry

    stored = _pre_lab_params(settings)
    before = registry.params_sha256(stored)
    values = frozen.frozen_values(_Record(stored))
    assert values["schedule.rebalance_cadence"] == "month_end"
    assert values["schedule.signal_anchor"] == "month_end"
    assert registry.params_sha256(stored) == before  # the stored set is not touched
    assert values == _new_params(settings)


def test_stored_values_win_over_the_defaults() -> None:
    values = frozen.frozen_values(_Record({"schedule.rebalance_cadence": "daily"}))
    assert values["schedule.rebalance_cadence"] == "daily"
    assert values["schedule.signal_anchor"] == "month_end"


def test_pre_lab_fingerprint_equals_the_file_with_defaults_written_out(
    settings: Settings,
) -> None:
    pre_lab, new = _pre_lab_params(settings), _new_params(settings)
    assert frozen.fingerprint("momentum", pre_lab, IN_SAMPLE_START) == frozen.fingerprint(
        "momentum", new, IN_SAMPLE_START
    )
    assert frozen.canonical_frozen_set(pre_lab, "momentum") == frozen.canonical_frozen_set(
        new, "momentum"
    )
    other_cadence = {**new, "schedule.rebalance_cadence": "week_end"}
    assert frozen.fingerprint("momentum", other_cadence, IN_SAMPLE_START) != frozen.fingerprint(
        "momentum", new, IN_SAMPLE_START
    )


def test_a_key_added_with_a_table_entry_changes_no_fingerprint_or_canonical_set(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = [_pre_lab_params(settings), _new_params(settings)]
    prints = [frozen.fingerprint("momentum", p, IN_SAMPLE_START) for p in stored]
    sets = [frozen.canonical_frozen_set(p, "momentum") for p in stored]
    monkeypatch.setattr(
        frozen,
        "FROZEN_KEY_DEFAULTS",
        (*frozen.FROZEN_KEY_DEFAULTS, ("universe.new_rule", False, 99)),
    )
    assert [frozen.fingerprint("momentum", p, IN_SAMPLE_START) for p in stored] == prints
    assert [frozen.canonical_frozen_set(p, "momentum") for p in stored] == sets
    # A file registered afterwards stores the key at its default: same fingerprint.
    with_key = {**stored[1], "universe.new_rule": False}
    assert frozen.fingerprint("momentum", with_key, IN_SAMPLE_START) == prints[1]


def test_family_and_its_signal_section_are_in_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = {
        "strategy.formation_months": 12,
        "strategy.skip_months": 1,
        "strategy.extra": 1,
        "schedule.rebalance_cadence": "month_end",
        "universe.min_price": 5.0,
    }
    a = frozen.fingerprint("momentum", params, IN_SAMPLE_START)
    b = frozen.fingerprint("oracle", params, IN_SAMPLE_START)
    assert a != b
    # A table key inside the family's signal section stays even at its default; a
    # `schedule.*` key at its default leaves.
    monkeypatch.setattr(
        frozen,
        "FROZEN_KEY_DEFAULTS",
        (*frozen.FROZEN_KEY_DEFAULTS, ("strategy.extra", 1, 99)),
    )
    momentum = frozen.canonical_frozen_set(params, "momentum")
    assert momentum["strategy.extra"] == 1
    assert "schedule.rebalance_cadence" not in momentum
    assert "strategy.extra" not in frozen.canonical_frozen_set(params, "oracle")


def test_fingerprint_reads_only_the_keys_that_decide_a_run(settings: Settings) -> None:
    params = _new_params(settings)
    base = frozen.fingerprint("momentum", params, IN_SAMPLE_START)
    for key, value in (
        ("metrics.risk_free_rate", 0.05),
        ("costs.sensitivity_per_side_bps", [1.0]),
        ("backtest.initial_capital", 1.0),
    ):
        assert frozen.fingerprint("momentum", {**params, key: value}, IN_SAMPLE_START) == base
    for key, value in (
        ("costs.per_side_bps", 99.0),
        ("execution.fill_price", "open" if params["execution.fill_price"] != "open" else "close"),
        ("holdout.start", "2024-02-01"),
        ("universe.min_price", 99.0),
        ("strategy.top_fraction", 0.5),
    ):
        assert frozen.fingerprint("momentum", {**params, key: value}, IN_SAMPLE_START) != base
    assert frozen.fingerprint("momentum", params, date(2017, 1, 31)) != base


# Today's readers of a `HypothesisRecord`'s `.params` outside `frozen.frozen_values` and
# `load_frozen`. T97, T98, T100 and T111 each remove their files from this list.
PARAMS_READERS_ALLOWLIST = {
    "cli.py",
    "backtest/holdout.py",
    "dashboard/backtest_page.py",
    "execution/check.py",
    "execution/report.py",
    "execution/window.py",
}
#: `.params` attributes that are not a `HypothesisRecord` (a plan-read bundle's `Settings`).
NOT_A_RECORD = {"execution/planning.py"}


def test_no_module_reads_record_params_outside_the_accessor() -> None:
    readers: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Attribute) and n.attr == "params" for n in ast.walk(tree)):
            readers.add(path.relative_to(SRC).as_posix())
    allowed = {"backtest/frozen.py", "backtest/hypothesis.py"}
    assert readers - allowed - NOT_A_RECORD <= PARAMS_READERS_ALLOWLIST
    assert "store/registry.py" not in readers
