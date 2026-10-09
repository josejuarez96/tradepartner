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

from tradepartner import config
from tradepartner.backtest import frozen, hypothesis
from tradepartner.config import Settings

SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"
PROF_FIXTURE = FIXTURE.with_name("fixture-profitability.md")
COMBINED_FIXTURE = FIXTURE.with_name("fixture-combined.md")
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
    # Since T128 (ADR 0014 point 2), `oracle` resolves through its registry entry
    # whose `sections=("strategy",)`, so default-valued `strategy.*` keys are kept
    # as for `momentum`. The hash still differs by `family` in the payload (above).
    assert frozen.canonical_frozen_set(params, "oracle")["strategy.extra"] == 1


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


# --- The family registry: unlisted-family failures (ADR 0014, T128) ----------


def test_inert_sections_raises_for_an_unlisted_family() -> None:
    """The momentum fallback goes (ADR 0014 point 2)."""
    with pytest.raises(KeyError):
        frozen.inert_sections("nosuch")


def test_fingerprint_raises_for_an_unlisted_family() -> None:
    with pytest.raises(KeyError):
        frozen.fingerprint("nosuch", {}, IN_SAMPLE_START)


def test_three_section_family_hashes_all_and_has_no_inert_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test-only spec that lists three sections freezes and hashes all three and has no
    inert section (ADR 0014 point 2: `combined` is the real caller, T130)."""
    from tradepartner import config as config_module

    original = config_module.FAMILIES["momentum"]
    composed = type(original)(
        sections=("strategy", "profitability", "combined"),
        params_model=original.params_model,
        parent=None,
        engine_ready=True,
        paper_ready=False,
        exclusion_reasons=original.exclusion_reasons,
        count_names=original.count_names,
        benchmark=None,
        sweepable_keys=(),
    )
    monkeypatch.setitem(config_module.FAMILIES, "momentum", composed)
    assert frozen.inert_sections("momentum") == frozenset()
    params = {
        "strategy.top_fraction": 0.3,
        "profitability.top_fraction": 0.4,
        "combined.top_fraction": 0.5,
    }
    base = frozen.fingerprint("momentum", params, IN_SAMPLE_START)
    for key in ("strategy.top_fraction", "profitability.top_fraction", "combined.top_fraction"):
        moved = {**params, key: params[key] + 0.1}
        assert frozen.fingerprint("momentum", moved, IN_SAMPLE_START) != base


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


def test_oracle_fingerprint_keeps_its_momentum_signal_section(settings: Settings) -> None:
    params = _new_params(settings)
    base = frozen.fingerprint("oracle", params, IN_SAMPLE_START)
    assert (
        frozen.fingerprint("oracle", {**params, "strategy.top_fraction": 0.9}, IN_SAMPLE_START)
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

#: The `combined` fixture twin's frozen values (T130), computed on this branch over
#: `Settings(_env_file=None)`. `COMBINED_LATER_KEYS` landed after: the params hash is
#: over the set without them (a `combined` registration stored before #1358).
COMBINED_FINGERPRINT = "810686f134111c9bddfc9194050de71ed65a497ba0acf7c4fd43052e26a3b9d2"
COMBINED_PARAMS_SHA256 = "239dd0b6cfbc1fc1ef21e48831dfdae80b6d67a3688ab3d172a9bcb290163094"
COMBINED_DEFAULTS = (
    ("combined.top_fraction", 0.10, 16),
    ("combined.weighting", "equal", 16),
)
COMBINED_LATER_KEYS = ("strategy.turnover_top_fraction",)


def _before(params: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if k not in keys}


def _defaults_settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _prof_params(settings: Settings) -> dict[str, Any]:
    return hypothesis.frozen_params(hypothesis.parse_file(PROF_FIXTURE), settings)


def test_profitability_table_entries_are_pinned_by_value() -> None:
    assert frozen.is_default(frozen.FROZEN_KEY_DEFAULTS[2:9], PROFITABILITY_DEFAULTS)
    assert frozen.FAMILY_SIGNAL_SECTIONS["profitability"] == "profitability"


def test_stale_listing_entry_is_pinned_by_value() -> None:
    """#1199: the key enters at its real default (strategy-lab spec, Frozen-key defaults)."""
    assert frozen.is_default(
        frozen.FROZEN_KEY_DEFAULTS[9:10], (("gap.stale_listing_sessions", 63, 16),)
    )


#: Frozen keys that landed after the twin hashes were pinned: a registration made
#: before them stores none, so the pinned hashes are over the set without them.
LATER_KEYS = ("gap.stale_listing_sessions", "strategy.turnover_top_fraction")


def test_h1_twin_canonical_set_fingerprint_and_hash_unchanged() -> None:
    from tradepartner.store import registry

    settings = _defaults_settings()
    current = _new_params(settings)
    new = {k: v for k, v in current.items() if k not in LATER_KEYS}
    pre_lab = {k: v for k, v in _pre_lab_params(settings).items() if k not in LATER_KEYS}
    assert not any(k.startswith("profitability.") for k in new)
    assert registry.params_sha256(new) == TWIN_PARAMS_SHA256
    assert registry.params_sha256(pre_lab) == TWIN_PRE_LAB_PARAMS_SHA256
    for params in (current, new, pre_lab):
        assert frozen.fingerprint("momentum", params, date(2017, 1, 31)) == TWIN_FINGERPRINT
        canonical = frozen.canonical_frozen_set(params, "momentum")
        assert not any(k.startswith("profitability.") for k in canonical)
    # The real H1 file over default settings: its pre-lab stored set and fingerprint.
    parsed = hypothesis.parse_file(H1_FILE)
    h1 = hypothesis.frozen_params(parsed, settings)
    h1_pre_lab = {k: v for k, v in h1.items() if k not in (*SCHEDULE_KEYS, *LATER_KEYS)}
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


# --- the `combined` family (hypothesis backlog B4; ADR 0014 point 6, T130) ------


def _combined_params(settings: Settings) -> dict[str, Any]:
    return hypothesis.frozen_params(hypothesis.parse_file(COMBINED_FIXTURE), settings)


def _combined_defaults_settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_combined_table_entries_are_pinned_by_value() -> None:
    """Append-only: the `combined.*` defaults land after the stale-listing entry."""
    assert frozen.is_default(frozen.FROZEN_KEY_DEFAULTS[10:12], COMBINED_DEFAULTS)
    assert frozen.FAMILY_SIGNAL_SECTIONS["combined"] == "combined"


def test_combined_twin_frozen_set_and_fingerprint_pinned() -> None:
    from tradepartner.store import registry

    settings = _combined_defaults_settings()
    params = _combined_params(settings)
    assert registry.params_sha256(_before(params, COMBINED_LATER_KEYS)) == COMBINED_PARAMS_SHA256
    for stored in (params, _before(params, COMBINED_LATER_KEYS)):
        assert frozen.fingerprint("combined", stored, date(2017, 1, 31)) == COMBINED_FINGERPRINT
    canonical = frozen.canonical_frozen_set(params, "combined")
    # Its listed sections are kept whole: both sub-signals' keys, default-valued ones too.
    assert {k for k in canonical if k.startswith("combined.")} == {
        key for key, _default, _version in COMBINED_DEFAULTS
    }
    assert any(k.startswith("strategy.") for k in canonical)
    assert any(k.startswith("profitability.") for k in canonical)


def test_combined_twin_ignores_live_sub_signal_keys() -> None:
    """The fixture names both sub-signals' keys, so a live `strategy.*` or
    `profitability.*` change moves neither the stored frozen set nor its hash (T130)."""
    from tradepartner.store import registry

    settings = _combined_defaults_settings()
    base = _combined_params(settings)
    live = settings.model_copy(
        update={
            "strategy": settings.strategy.model_copy(
                update={"formation_months": 6, "skip_months": 0, "top_fraction": 0.5}
            ),
            "profitability": settings.profitability.model_copy(
                update={"top_fraction": 0.9, "max_fact_age_days": 100}
            ),
        }
    )
    moved = _combined_params(live)
    assert moved == base
    assert registry.params_sha256(_before(moved, COMBINED_LATER_KEYS)) == COMBINED_PARAMS_SHA256
    assert frozen.fingerprint("combined", moved, date(2017, 1, 31)) == COMBINED_FINGERPRINT
    assert frozen.canonical_frozen_set(moved, "combined") == frozen.canonical_frozen_set(
        base, "combined"
    )


def test_momentum_and_combined_fingerprints_still_ignore_each_other_s_sections(
    settings: Settings,
) -> None:
    """A `strategy.*` key in a profitability set is ignored, and `combined`'s sections are
    frozen in full, so the pinned momentum twin is untouched by T130."""
    momentum = _new_params(settings)
    assert frozen.fingerprint("momentum", momentum, IN_SAMPLE_START) == frozen.fingerprint(
        "momentum", {**momentum, "combined.top_fraction": 0.9}, IN_SAMPLE_START
    )
    assert not any(
        k.startswith("combined.") for k in frozen.canonical_frozen_set(momentum, "momentum")
    )


# Today's readers of a `HypothesisRecord`'s `.params` outside `frozen.frozen_values` and
# `load_frozen`. T97, T98, T100 and T111 each remove their files from this list.
PARAMS_READERS_ALLOWLIST: set[str] = set()
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


# --- literal pins taken on `main`'s code before the turnover key (#1358, T165) -------

#: B3 (the profitability fixture twin and the real B3 file) and `oracle` (the momentum
#: fixture twin read as `oracle`), computed on `main` at 3de454d7 over
#: `Settings(_env_file=None)`, before `strategy.turnover_top_fraction` existed. Canonical
#: sets are pinned by `registry.params_sha256` of the set.
B3_TWIN_FINGERPRINT = "0d4670860b79d1591c1d00ad34ba98c34ba5451745d3257ec3b42c5a1d9caf1c"
B3_TWIN_CANONICAL_SHA256 = "6059282b4af951d4ac6088150a8eacc3c7e6877410660bcc1f3e169843869642"
B3_TWIN_PARAMS_SHA256 = "7e72f97a0594055f002d33de2dde181a1fda8341e2a67117a57f50b7bed643f0"
B3_FILE_FINGERPRINT = "0ef4ebf725560b73ccc8157e4d477f07facc16116bea44016bbef4f06ec9ae83"
B3_FILE_CANONICAL_SHA256 = "ada9a26f7a0172abbc27419bf6adca29443be3d64032847c024951df0f398a2c"
B3_FILE_PARAMS_SHA256 = "c1b7e7002cf6160828210c3fbc72483113622740b9c21b526762d8d63d72f1d4"
ORACLE_TWIN_FINGERPRINT = "2fc196fad5d1fbe618d51a1adc7d8e02f664fae55b32d3cbfef90d011ca17ae5"
ORACLE_TWIN_CANONICAL_SHA256 = "22fedb1cf5f53d68ecf69f9e6d5ec912cb573aee07ba2932728dcab3c178403d"
B3_FILE = H1_FILE.with_name("b3-gross-profitability.md")


@pytest.mark.parametrize(
    ("path", "fingerprint", "canonical_sha256", "params_sha256"),
    [
        (PROF_FIXTURE, B3_TWIN_FINGERPRINT, B3_TWIN_CANONICAL_SHA256, B3_TWIN_PARAMS_SHA256),
        (B3_FILE, B3_FILE_FINGERPRINT, B3_FILE_CANONICAL_SHA256, B3_FILE_PARAMS_SHA256),
    ],
)
def test_b3_fingerprint_canonical_set_and_hash_are_pinned(
    path: Path, fingerprint: str, canonical_sha256: str, params_sha256: str
) -> None:
    from tradepartner.store import registry

    parsed = hypothesis.parse_file(path)
    params = hypothesis.frozen_params(parsed, _defaults_settings())
    assert registry.params_sha256(params) == params_sha256
    assert frozen.fingerprint(parsed.family, params, parsed.in_sample_start) == fingerprint
    canonical = frozen.canonical_frozen_set(params, parsed.family)
    assert registry.params_sha256(canonical) == canonical_sha256
    # B3's seven keys stay in its canonical set (decision 13 holds for them).
    assert {key for key, _default, _version in PROFITABILITY_DEFAULTS} <= set(canonical)


def test_oracle_fingerprint_and_canonical_set_are_pinned() -> None:
    from tradepartner.store import registry

    parsed = hypothesis.parse_file(FIXTURE)
    params = hypothesis.frozen_params(parsed, _defaults_settings())
    assert frozen.fingerprint("oracle", params, parsed.in_sample_start) == ORACLE_TWIN_FINGERPRINT
    canonical = frozen.canonical_frozen_set(params, "oracle")
    assert registry.params_sha256(canonical) == ORACLE_TWIN_CANONICAL_SHA256


# --- the turnover key and decision 13's carve-out (#1358, T165) ----------------------

TURNOVER_KEY = "strategy.turnover_top_fraction"


def test_turnover_entry_is_last_and_pinned_by_value() -> None:
    assert frozen.is_default(frozen.FROZEN_KEY_DEFAULTS[12:], ((TURNOVER_KEY, 1.0, 19),))


def test_post_registration_own_keys_pinned_and_each_a_listed_table_key() -> None:
    """Append-only (removing a member moves every stored own-section fingerprint)."""
    assert frozenset({TURNOVER_KEY}) == frozen.POST_REGISTRATION_OWN_KEYS
    table = {key for key, _default, _version in frozen.FROZEN_KEY_DEFAULTS}
    listed = {section for spec in config.FAMILIES.values() for section in spec.sections}
    for key in frozen.POST_REGISTRATION_OWN_KEYS:
        assert key in table
        assert key.partition(".")[0] in listed


def test_a_post_registration_key_at_its_default_is_left_out_of_every_own_section() -> None:
    settings = _defaults_settings()
    for family, params in (
        ("momentum", _new_params(settings)),
        ("oracle", _new_params(settings)),
        ("combined", _combined_params(settings)),
    ):
        assert params[TURNOVER_KEY] == 1.0  # stored at its default
        assert TURNOVER_KEY not in frozen.canonical_frozen_set(params, family)
        # A key the family registered with stays in at its default (decision 13).
        assert "strategy.top_fraction" in frozen.canonical_frozen_set(
            {**params, "strategy.top_fraction": 0.10}, family
        )


def _twin_with(tmp_path: Path, line: str | None) -> Path:
    text = FIXTURE.read_text()
    if line is not None:
        text = text.replace(
            "signal_total_return = true\n", f"signal_total_return = true\n{line}\n", 1
        )
    path = tmp_path / FIXTURE.name
    path.write_text(text)
    return path


def test_a_momentum_file_naming_the_default_is_the_twin_and_another_value_is_not(
    tmp_path: Path,
) -> None:
    from tradepartner.store import registry

    settings = _defaults_settings()
    twin = _new_params(settings)
    twin_canonical = frozen.canonical_frozen_set(twin, "momentum")
    explicit = hypothesis.frozen_params(
        hypothesis.parse_file(_twin_with(tmp_path, "turnover_top_fraction = 1.0")), settings
    )
    assert explicit == twin
    assert frozen.fingerprint("momentum", explicit, date(2017, 1, 31)) == TWIN_FINGERPRINT
    assert frozen.canonical_frozen_set(explicit, "momentum") == twin_canonical
    assert registry.params_sha256(twin_canonical) == TWIN_PRE_LAB_PARAMS_SHA256
    screened = hypothesis.frozen_params(
        hypothesis.parse_file(_twin_with(tmp_path, "turnover_top_fraction = 0.20")), settings
    )
    assert screened[TURNOVER_KEY] == 0.2
    assert frozen.fingerprint("momentum", screened, date(2017, 1, 31)) != TWIN_FINGERPRINT
    canonical = frozen.canonical_frozen_set(screened, "momentum")
    assert canonical[TURNOVER_KEY] == 0.2
    assert {k: v for k, v in canonical.items() if k != TURNOVER_KEY} == twin_canonical
