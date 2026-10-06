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
PROF_FIXTURE = FIXTURE.with_name("fixture-profitability.md")
H1_FILE = SRC.parents[1] / "docs" / "hypotheses" / "h1-momentum-12-1.md"
SCHEDULE_KEYS = ("schedule.rebalance_cadence", "schedule.signal_anchor")
IN_SAMPLE_START = date(2019, 11, 29)


@dataclass(frozen=True)
class _Record:
    params: dict[str, Any] = field(default_factory=dict)
    family: str = "momentum"


def _new_params(settings: Settings) -> dict[str, Any]:
    """A file registered after the lab: both `schedule` keys stored."""
    return hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), settings)


def _pre_lab_params(settings: Settings) -> dict[str, Any]:
    """The same file as registered before the lab: no `schedule.*` keys."""
    return {k: v for k, v in _new_params(settings).items() if k not in SCHEDULE_KEYS}


def test_existing_table_entries_are_pinned_by_value() -> None:
    """Append-only and immutable: a later entry goes after these, never in place."""
    # `is_default` (JSON form), not `==`: a `True` edited to `1` must fail (#1022).
    assert frozen.is_default(
        frozen.FROZEN_KEY_DEFAULTS[:2],
        (
            ("schedule.rebalance_cadence", "month_end", 12),
            ("schedule.signal_anchor", "month_end", 12),
        ),
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
        assert frozen.is_default(dumped[section][name], default), key


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


@pytest.mark.parametrize(
    ("default", "stored", "kept"),
    [
        (1, 1, False),
        (1, True, True),
        (1, 1.0, True),
        (True, 1, True),
        (True, True, False),
        (1.0, 1, True),
        ([1, 2], [1, 2], False),
        ([1, 2], [1.0, 2], True),
        ([1, 2], [True, 2], True),
        ([1, 2], (1, 2), False),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}, False),
    ],
)
def test_canonical_set_compares_a_default_by_type_as_well(
    monkeypatch: pytest.MonkeyPatch, default: Any, stored: Any, kept: bool
) -> None:
    """#1022: `True == 1 == 1.0` in Python, but they are different frozen values; a
    stored value is left out only when it is the default in the same JSON form."""
    monkeypatch.setattr(
        frozen,
        "FROZEN_KEY_DEFAULTS",
        (*frozen.FROZEN_KEY_DEFAULTS, ("schedule.extra", default, 99)),
    )
    canonical = frozen.canonical_frozen_set({"schedule.extra": stored}, "momentum")
    assert ("schedule.extra" in canonical) is kept
    # The fingerprint tells a kept value from the default, and ignores a dropped one.
    as_default = frozen.fingerprint("momentum", {"schedule.extra": default}, IN_SAMPLE_START)
    as_stored = frozen.fingerprint("momentum", {"schedule.extra": stored}, IN_SAMPLE_START)
    assert (as_stored != as_default) is kept


def test_is_default_refuses_nan() -> None:
    with pytest.raises(ValueError):
        frozen.is_default(float("nan"), float("nan"))


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


def test_profitability_fingerprint_hashes_its_own_section_only(settings: Settings) -> None:
    params = hypothesis.frozen_params(hypothesis.parse_file(PROF_FIXTURE), settings)
    base = frozen.fingerprint("profitability", params, IN_SAMPLE_START)
    assert (
        frozen.fingerprint(
            "profitability", {**params, "strategy.top_fraction": 0.9}, IN_SAMPLE_START
        )
        == base
    )
    assert (
        frozen.fingerprint(
            "profitability", {**params, "profitability.top_fraction": 0.9}, IN_SAMPLE_START
        )
        != base
    )


# --- the `profitability` family (backtest spec amendment #720, T85) -----------

#: Computed on main at cace512, before T85, over `Settings(_env_file=None)`.
TWIN_FINGERPRINT = "bdbb8d9f81d744f24e8d2fc9c8598bbe4436e19c17e036636f19895f340fb496"
TWIN_PARAMS_SHA256 = "77b0e33b533eb09ea964c04fdcb85dbce425d1b31d8fa99fdae53e383696e7d6"
TWIN_PRE_LAB_PARAMS_SHA256 = "22fedb1cf5f53d68ecf69f9e6d5ec912cb573aee07ba2932728dcab3c178403d"
H1_PRE_LAB_PARAMS_SHA256 = "09f34af0bb58adab0883ea6172683cf3dc11c457adfe4a5595868b36d5eeeb46"
H1_FINGERPRINT = "534917a58a4f8c5dfe17330b3e2d06faf38111de553fd467886a06c147f609a4"

PROFITABILITY_DEFAULTS = (
    ("profitability.basis", "gross", 12),
    ("profitability.annual_period_days", [350, 380], 12),
    ("profitability.max_fact_age_days", 548, 12),
    ("profitability.exclude_sic_ranges", [[6000, 6999]], 12),
    ("profitability.include_derived", True, 12),
    ("profitability.top_fraction", 0.10, 12),
    ("profitability.weighting", "equal", 12),
)


def _defaults_settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _prof_params(settings: Settings) -> dict[str, Any]:
    return hypothesis.frozen_params(hypothesis.parse_file(PROF_FIXTURE), settings)


def test_profitability_table_entries_are_pinned_by_value() -> None:
    assert frozen.is_default(frozen.FROZEN_KEY_DEFAULTS[2:9], PROFITABILITY_DEFAULTS)
    assert frozen.FAMILY_SIGNAL_SECTIONS["profitability"] == "profitability"


def test_h1_twin_canonical_set_fingerprint_and_hash_unchanged() -> None:
    from tradepartner.store import registry

    settings = _defaults_settings()
    new = _new_params(settings)
    pre_lab = _pre_lab_params(settings)
    assert not any(k.startswith("profitability.") for k in new)
    assert registry.params_sha256(new) == TWIN_PARAMS_SHA256
    assert registry.params_sha256(pre_lab) == TWIN_PRE_LAB_PARAMS_SHA256
    for params in (new, pre_lab):
        assert frozen.fingerprint("momentum", params, date(2017, 1, 31)) == TWIN_FINGERPRINT
        canonical = frozen.canonical_frozen_set(params, "momentum")
        assert not any(k.startswith("profitability.") for k in canonical)
    # The real H1 file over default settings: its pre-lab stored set and fingerprint.
    parsed = hypothesis.parse_file(H1_FILE)
    h1 = hypothesis.frozen_params(parsed, settings)
    h1_pre_lab = {k: v for k, v in h1.items() if k not in SCHEDULE_KEYS}
    assert registry.params_sha256(h1_pre_lab) == H1_PRE_LAB_PARAMS_SHA256
    assert frozen.fingerprint("momentum", h1, parsed.in_sample_start) == H1_FINGERPRINT


def test_momentum_record_overlays_no_profitability_default(settings: Settings) -> None:
    values = frozen.frozen_values(_Record(_pre_lab_params(settings)))
    assert not any(k.startswith("profitability.") for k in values)
    prof = frozen.frozen_values(_Record({}, family="profitability"))
    assert prof["profitability.max_fact_age_days"] == 548


def test_profitability_fingerprint_differs_from_the_twin_by_family(settings: Settings) -> None:
    momentum, prof = _new_params(settings), _prof_params(settings)
    shared = set(momentum) & set(prof)
    assert shared == {k for k in momentum if not k.startswith("strategy.")}
    assert all(momentum[k] == prof[k] for k in shared)
    assert frozen.fingerprint("profitability", prof, IN_SAMPLE_START) != frozen.fingerprint(
        "momentum", momentum, IN_SAMPLE_START
    )
    canonical = frozen.canonical_frozen_set(prof, "profitability")
    assert {k for k in canonical if k.startswith("profitability.")} == {
        key for key, _default, _version in PROFITABILITY_DEFAULTS
    }  # its own section kept in full, default-valued keys included
    assert not any(k.startswith("strategy.") for k in canonical)


def test_profitability_hash_and_fingerprint_ignore_live_strategy(settings: Settings) -> None:
    from tradepartner.store import registry

    base = _prof_params(settings)
    live = settings.model_copy(
        update={"strategy": settings.strategy.model_copy(update={"top_fraction": 0.5})}
    )
    moved = _prof_params(live)
    assert registry.params_sha256(moved) == registry.params_sha256(base)
    assert frozen.fingerprint("profitability", moved, IN_SAMPLE_START) == frozen.fingerprint(
        "profitability", base, IN_SAMPLE_START
    )
    # A stray `strategy.*` key in a profitability set is never fingerprinted.
    stray = {**base, "strategy.top_fraction": 0.5}
    assert frozen.fingerprint("profitability", stray, IN_SAMPLE_START) == frozen.fingerprint(
        "profitability", base, IN_SAMPLE_START
    )


# Today's readers of a `HypothesisRecord`'s `.params` outside `frozen.frozen_values` and
# `load_frozen`. T97, T98, T100 and T111 each remove their files from this list.
PARAMS_READERS_ALLOWLIST = {
    "cli.py",
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
