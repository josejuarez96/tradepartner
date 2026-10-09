"""Sweep file parser and grid, pure (strategy-lab spec req 1 and Definitions "Grid",
"Read group"; the "Sweep registration and guardrails" criterion as far as parsing
reaches; plan task T102).

Store-level refusals (family rules, a registered fingerprint, family readiness, the anchor
check against the store's first session, the family rules' lattice, `oracle` on the real
store) are T104's, on a store; everything here reads a file and `Settings` only.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tradepartner import config
from tradepartner.backtest import frozen, hypothesis, sweep
from tradepartner.backtest.sweep import (
    FORBIDDEN_AXIS_PREFIXES,
    AxisNotSweepableError,
    CadenceAxisStatisticError,
    DuplicateFingerprintError,
    DuplicateGridValueError,
    FixedAndGriddedError,
    InvalidVariantError,
    LabBlockError,
    MissingRequiredKeyError,
    OffLatticeError,
    SweepFileError,
    TooManyVariantsError,
    Variant,
    expand_grid,
    parse_sweep_file,
    read_groups,
    variant_slug,
)
from tradepartner.config import Settings
from tradepartner.store import registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sweeps"
SWEEP = FIXTURES / "fixture-sweep.md"
GROUPS = FIXTURES / "fixture-sweep-groups.md"
REFUSALS = FIXTURES / "refusals"


def _settings(**sections: Any) -> Settings:
    return Settings(_env_file=None, **sections)


def _rewrite(tmp_path: Path, source: Path, old: str, new: str, *, slug: str = "edited") -> Path:
    """A copy of `source` named `<slug>.md` with one block line replaced."""
    text = source.read_text()
    assert old in text, old
    text = text.replace(old, new).replace(f'slug = "{source.stem}"', f'slug = "{slug}"')
    path = tmp_path / f"{slug}.md"
    path.write_text(text)
    return path


# ── parsing the fixture ─────────────────────────────────────────────────────────


def test_forbidden_axis_prefixes_pinned_and_reexported() -> None:
    assert FORBIDDEN_AXIS_PREFIXES == (
        "costs.",
        "universe.",
        "holdout.",
        "gap.",
        "adjust.",
        "master.",
        "metrics.",
        "backtest.",
        "benchmarks",
        "alpaca.",
        "execution.",
    )
    assert FORBIDDEN_AXIS_PREFIXES is config.FORBIDDEN_AXIS_PREFIXES


def test_fixture_sweep_parses() -> None:
    parsed = parse_sweep_file(SWEEP, _settings())
    assert parsed.slug == "fixture-sweep"
    assert parsed.family == "momentum"
    assert parsed.in_sample_start == date(2017, 1, 31)
    assert (parsed.holdout_start, parsed.holdout_end) == (date(2023, 1, 3), date(2025, 12, 31))
    assert parsed.parent_family is None
    assert parsed.grid == {
        "strategy.top_fraction": (0.05, 0.2),
        "schedule.rebalance_cadence": ("month_end", "week_end"),
    }
    assert "strategy.top_fraction" not in parsed.fixed_params
    assert parsed.fixed_params["holdout.start"] == date(2023, 1, 3)
    assert parsed.lab.selection_statistic == "sharpe_annual_excess_spy"
    assert parsed.lab.expected_range_pp == (-2.0, 2.0)
    assert parsed.lab.promote_at_least == 0.5
    assert parsed.lab.retire_below == 0.0
    assert len(parsed.doc_sha256) == 64


def test_parse_reads_live_settings_when_none_given() -> None:
    assert parse_sweep_file(SWEEP).slug == "fixture-sweep"


# ── the grid ────────────────────────────────────────────────────────────────────


def test_expand_grid_four_variants_in_canonical_order() -> None:
    settings = _settings()
    variants = expand_grid(parse_sweep_file(SWEEP, settings), settings)
    assert [v.index for v in variants] == [1, 2, 3, 4]
    shas = [v.params_sha256 for v in variants]
    assert shas == sorted(shas)
    assert {
        (v.values["strategy.top_fraction"], v.values["schedule.rebalance_cadence"])
        for v in variants
    } == {
        (0.05, "month_end"),
        (0.05, "week_end"),
        (0.2, "month_end"),
        (0.2, "week_end"),
    }
    assert len({v.fingerprint for v in variants}) == 4
    for v in variants:
        assert v.frozen_set["holdout.start"] == "2023-01-03"
        assert v.frozen_set["holdout.end"] == "2025-12-31"
        assert v.frozen_set["gap.count_share_threshold"] == 0.05
        assert v.params_sha256 == registry.params_sha256(v.frozen_set)
        assert v.fingerprint == frozen.fingerprint("momentum", v.frozen_set, date(2017, 1, 31))


def test_variant_frozen_set_is_built_as_a_hypothesis_file_would_be(tmp_path: Path) -> None:
    """Each variant's set equals `hypothesis.frozen_params` of the standalone file that
    pins the same values (file values over live `Settings`, validated, JSON form)."""
    settings = _settings()
    variants = expand_grid(parse_sweep_file(SWEEP, settings), settings)
    for v in variants:
        text = SWEEP.read_text()
        block_start = text.index("```toml sweep")
        block = text[block_start : text.index("```", block_start + 3) + 3]
        lines = [
            line
            for line in block.splitlines()
            if not line.startswith('"') and line not in ("[grid]",)
        ]
        lab_at = lines.index("[lab]")
        lines = [*lines[:lab_at], "```"]
        lines[0] = "```toml hypothesis"
        body = "\n".join(lines).replace('slug = "fixture-sweep"', f'slug = "variant-{v.index}"')
        body = body.replace(
            "[strategy]\n", f"[strategy]\ntop_fraction = {v.values['strategy.top_fraction']}\n"
        )
        body = body.replace(
            "[schedule]\n",
            f'[schedule]\nrebalance_cadence = "{v.values["schedule.rebalance_cadence"]}"\n',
        )
        path = tmp_path / f"variant-{v.index}.md"
        path.write_text(body)
        standalone = hypothesis.frozen_params(hypothesis.parse_file(path), settings)
        assert standalone == v.frozen_set


def test_too_many_variants_refused() -> None:
    settings = _settings(lab={"max_variants_per_sweep": 3})
    with pytest.raises(TooManyVariantsError, match="4 variants"):
        expand_grid(parse_sweep_file(SWEEP, settings), settings)
    settings = _settings()
    with pytest.raises(TooManyVariantsError, match="110 variants"):
        expand_grid(parse_sweep_file(REFUSALS / "too-many-variants.md", settings), settings)


def test_two_variants_with_one_fingerprint_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unreachable through the default `lab.sweepable_keys` (every axis is a fingerprint
    # key and grid values are distinct after validation), so forced here.
    settings = _settings()
    parsed = parse_sweep_file(SWEEP, settings)
    monkeypatch.setattr(sweep.frozen, "fingerprint", lambda *_args: "same")
    with pytest.raises(DuplicateFingerprintError, match="one fingerprint"):
        expand_grid(parsed, settings)


def test_expand_grid_is_deterministic() -> None:
    settings = _settings()
    parsed = parse_sweep_file(SWEEP, settings)
    assert expand_grid(parsed, settings) == expand_grid(parsed, settings)


# ── file-level refusals, one fixture each ───────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "error", "match"),
    [
        ("missing-strategy-key", MissingRequiredKeyError, "strategy.weighting"),
        ("missing-costs-key", MissingRequiredKeyError, "costs.commission_per_order"),
        ("missing-cadence", MissingRequiredKeyError, "schedule.rebalance_cadence"),
        ("fixed-and-gridded", FixedAndGriddedError, "strategy.top_fraction"),
        ("axis-not-sweepable", AxisNotSweepableError, "costs.per_side_bps"),
        ("lab-missing-field", LabBlockError, "retire_below"),
        ("promote-below-floor", LabBlockError, "promote_at_least"),
        ("retire-not-below-promote", LabBlockError, "retire_below"),
        ("dsr-excess-cadence-axis", CadenceAxisStatisticError, "dsr_excess"),
        ("off-lattice", OffLatticeError, "0.105"),
        ("duplicate-grid-value", DuplicateGridValueError, "strategy.formation_months"),
    ],
)
def test_refusal_fixture(name: str, error: type[SweepFileError], match: str) -> None:
    with pytest.raises(error, match=re.escape(match)):
        parse_sweep_file(REFUSALS / f"{name}.md", _settings())


def test_every_refusal_fixture_is_refused() -> None:
    settings = _settings()
    for path in sorted(REFUSALS.glob("*.md")):
        with pytest.raises(SweepFileError):
            expand_grid(parse_sweep_file(path, settings), settings)


def test_typed_errors_are_value_errors() -> None:
    assert issubclass(SweepFileError, ValueError)
    for error in (
        MissingRequiredKeyError,
        FixedAndGriddedError,
        AxisNotSweepableError,
        LabBlockError,
        CadenceAxisStatisticError,
        OffLatticeError,
        DuplicateGridValueError,
        TooManyVariantsError,
        DuplicateFingerprintError,
        InvalidVariantError,
    ):
        assert issubclass(error, SweepFileError)


@pytest.mark.parametrize(
    "field",
    [
        "selection_statistic",
        "expected_excess_cagr_spy_pp",
        "expected_range_pp",
        "promote_at_least",
        "retire_below",
    ],
)
def test_lab_block_missing_any_field_refused(tmp_path: Path, field: str) -> None:
    text = SWEEP.read_text()
    line = next(ln for ln in text.splitlines() if ln.startswith(f"{field} = "))
    path = _rewrite(tmp_path, SWEEP, line + "\n", "")
    with pytest.raises(LabBlockError, match=field):
        parse_sweep_file(path, _settings())


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            'selection_statistic = "sharpe_annual_excess_spy"',
            'selection_statistic = "cagr"',
            "selection_statistic",
        ),
        ("expected_range_pp = [-2.0, 2.0]", "expected_range_pp = [2.0, -2.0]", "expected_range_pp"),
        ("expected_range_pp = [-2.0, 2.0]", "expected_range_pp = [1.0]", "expected_range_pp"),
        ("retire_below = 0.0", 'retire_below = "low"', "retire_below"),
        ("retire_below = 0.0", "retire_below = 0.0\nextra = 1", "extra"),
    ],
)
def test_lab_block_bad_values_refused(tmp_path: Path, old: str, new: str, match: str) -> None:
    with pytest.raises(LabBlockError, match=match):
        parse_sweep_file(_rewrite(tmp_path, SWEEP, old, new), _settings())


def test_promotion_floor_reads_config(tmp_path: Path) -> None:
    settings = _settings(lab={"promotion_min_dsr_excess": 0.8})
    with pytest.raises(LabBlockError, match="promote_at_least"):
        parse_sweep_file(SWEEP, settings)


def test_retire_below_not_checked_against_promote_under_other_statistics(tmp_path: Path) -> None:
    path = _rewrite(tmp_path, SWEEP, "retire_below = 0.0", "retire_below = 5.0")
    assert parse_sweep_file(path, _settings()).lab.retire_below == 5.0


def test_cadence_axis_allows_the_two_other_statistics(tmp_path: Path) -> None:
    path = _rewrite(
        tmp_path,
        SWEEP,
        'selection_statistic = "sharpe_annual_excess_spy"',
        'selection_statistic = "excess_cagr_spy"',
    )
    assert parse_sweep_file(path, _settings()).lab.selection_statistic == "excess_cagr_spy"


def test_axis_outside_sweepable_keys_refused_by_config(tmp_path: Path) -> None:
    settings = _settings(
        lab={"sweepable_keys": ["strategy.top_fraction", "schedule.rebalance_cadence"]}
    )
    path = _rewrite(
        tmp_path,
        SWEEP,
        "formation_months = 12\n",
        "",
    )
    path.write_text(
        path.read_text().replace("[grid]\n", '[grid]\n"strategy.formation_months" = [6, 12]\n')
    )
    with pytest.raises(AxisNotSweepableError, match=r"strategy\.formation_months"):
        parse_sweep_file(path, settings)


def test_off_lattice_reads_config(tmp_path: Path) -> None:
    settings = _settings(lab={"axis_lattice": {"strategy.top_fraction": 0.05}})
    path = _rewrite(tmp_path, SWEEP, "[0.05, 0.20]", "[0.05, 0.12]")
    with pytest.raises(OffLatticeError, match=r"0\.12"):
        parse_sweep_file(path, settings)
    assert parse_sweep_file(SWEEP, settings).grid["strategy.top_fraction"] == (0.05, 0.2)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("```toml sweep", "```toml hypothesis", "toml sweep"),
        ('slug = "fixture-sweep"', 'slug = "other"', "slug"),
        ('family = "momentum"', 'family = "value"', "family"),
        ("in_sample_start = 2017-01-31", "in_sample_start = 2024-01-31", "in_sample_start"),
        (
            "count_share_threshold = 0.05",
            "count_share_threshold = 0.05\nnot_a_key = 1",
            "gap.not_a_key",
        ),
        ("[0.05, 0.20]", "[]", "non-empty"),
        ("[0.05, 0.20]", "0.05", "non-empty"),
        (
            "signal_total_return = true",
            "signal_total_return = true\n\n[profitability]\ntop_fraction = 0.1",
            "profitability.top_fraction",
        ),
        ('weighting = "equal"', 'weighting = "bogus"', "weighting = 'bogus' fails validation"),
    ],
)
def test_structural_refusals(tmp_path: Path, old: str, new: str, match: str) -> None:
    text = SWEEP.read_text().replace(old, new)
    path = tmp_path / "fixture-sweep.md"
    path.write_text(text)
    with pytest.raises(SweepFileError, match=match):
        parse_sweep_file(path, _settings())


def test_parent_family_accepted_as_a_string(tmp_path: Path) -> None:
    path = _rewrite(
        tmp_path, SWEEP, 'family = "momentum"', 'family = "momentum"\nparent_family = "momentum"'
    )
    assert parse_sweep_file(path, _settings()).parent_family == "momentum"


# ── read groups ─────────────────────────────────────────────────────────────────


def test_read_groups() -> None:
    settings = _settings()
    variants = expand_grid(parse_sweep_file(GROUPS, settings), settings)
    groups = read_groups(variants)
    assert len(variants) == 8
    assert len(groups) == 4
    for group in groups:
        # Members differ only in top_fraction: one cadence, one signal_total_return.
        assert len(group) == 2
        assert len({v.values["schedule.rebalance_cadence"] for v in group}) == 1
        assert len({v.values["strategy.signal_total_return"] for v in group}) == 1
        assert {v.values["strategy.top_fraction"] for v in group} == {0.15, 0.25}
        assert [v.index for v in group] == sorted(v.index for v in group)
    lowest = [group[0].index for group in groups]
    assert lowest == sorted(lowest)
    assert lowest[0] == 1
    assert sorted(v.index for g in groups for v in g) == list(range(1, 9))


def test_every_free_key_shares_a_group() -> None:
    settings = _settings()
    base = expand_grid(parse_sweep_file(SWEEP, settings), settings)[0]

    def variant(index: int, **changes: Any) -> Variant:
        return Variant(
            index=index,
            values={},
            frozen_set={**base.frozen_set, **changes},
            params_sha256=str(index),
            fingerprint=str(index),
        )

    shared = [
        variant(1),
        variant(2, **{"strategy.formation_months": 6}),
        variant(3, **{"strategy.skip_months": 0}),
        variant(4, **{"strategy.top_fraction": 0.3}),
        variant(5, **{"strategy.weighting": "cap"}),
        variant(6, **{"schedule.signal_anchor": "offset"}),
    ]
    split = [
        variant(7, **{"schedule.rebalance_cadence": "daily"}),
        variant(
            8,
            **{"strategy.signal_total_return": not base.frozen_set["strategy.signal_total_return"]},
        ),
        variant(9, **{"universe.top_n_by_cap": 500}),
    ]
    groups = read_groups(shared + split)
    assert [[v.index for v in g] for g in groups] == [[1, 2, 3, 4, 5, 6], [7], [8], [9]]


def test_read_groups_of_nothing() -> None:
    assert read_groups([]) == []


# ── variant slugs ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("index", "n_variants", "expected"),
    [
        (1, 4, "s--r7-v1"),
        (4, 4, "s--r7-v4"),
        (3, 10, "s--r7-v03"),
        (10, 10, "s--r7-v10"),
        (9, 9, "s--r7-v9"),
        (7, 100, "s--r7-v007"),
        (100, 100, "s--r7-v100"),
    ],
)
def test_variant_slug_width(index: int, n_variants: int, expected: str) -> None:
    assert variant_slug("s", 7, index, n_variants) == expected


@pytest.mark.parametrize(
    ("index", "n_variants", "sweep_id"), [(0, 4, 1), (5, 4, 1), (1, 0, 1), (1, 4, -1)]
)
def test_variant_slug_refuses_out_of_range(index: int, n_variants: int, sweep_id: int) -> None:
    with pytest.raises(ValueError):
        variant_slug("s", sweep_id, index, n_variants)


def test_fixture_variant_slugs() -> None:
    settings = _settings()
    variants = expand_grid(parse_sweep_file(SWEEP, settings), settings)
    slugs = [variant_slug("fixture-sweep", 1, v.index, len(variants)) for v in variants]
    assert slugs == [f"fixture-sweep--r1-v{i}" for i in range(1, 5)]


# ── review fixes ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("near", ["0.1000000001", "0.10000000001"])
def test_near_duplicate_on_the_lattice_refused(tmp_path: Path, near: str) -> None:
    path = _rewrite(tmp_path, SWEEP, "[0.05, 0.20]", f"[0.10, {near}]")
    with pytest.raises((OffLatticeError, DuplicateGridValueError)):
        parse_sweep_file(path, _settings())


def test_one_value_near_the_lattice_refused(tmp_path: Path) -> None:
    path = _rewrite(tmp_path, SWEEP, "[0.05, 0.20]", "[0.1000000001]")
    with pytest.raises(OffLatticeError):
        parse_sweep_file(path, _settings())


def test_lattice_points_with_float_round_off_accepted(tmp_path: Path) -> None:
    values = ", ".join(f"{0.01 * k:.2f}" for k in range(1, 101))
    path = _rewrite(tmp_path, SWEEP, "[0.05, 0.20]", f"[{values}]")
    path.write_text(path.read_text().replace('["month_end", "week_end"]', '["month_end"]'))
    grid = parse_sweep_file(path, _settings()).grid["strategy.top_fraction"]
    assert len(grid) == 100


@pytest.mark.parametrize("step", [0.0, -0.01])
def test_nonpositive_lattice_step_refused(step: float) -> None:
    settings = _settings(lab={"axis_lattice": {"strategy.top_fraction": step}})
    with pytest.raises(SweepFileError, match="must be > 0"):
        parse_sweep_file(SWEEP, settings)


def _two_window_axes(tmp_path: Path, formation: str, skip: str) -> Path:
    path = _rewrite(tmp_path, SWEEP, "formation_months = 12\nskip_months = 1\n", "")
    path.write_text(
        path.read_text().replace(
            "[grid]\n",
            f'[grid]\n"strategy.formation_months" = {formation}\n"strategy.skip_months" = {skip}\n',
        )
    )
    return path


def test_grid_values_are_judged_alone_not_against_live_settings(tmp_path: Path) -> None:
    # formation_months = 1 fails against the live skip_months = 1, but the file grids
    # skip_months too: the parse must not refuse it, and the (1, 0) variant is valid.
    path = _two_window_axes(tmp_path, "[1, 12]", "[0]")
    settings = _settings()
    variants = expand_grid(parse_sweep_file(path, settings), settings)
    assert {v.values["strategy.formation_months"] for v in variants} == {1, 12}


def test_a_combination_settings_refuses_is_a_typed_error(tmp_path: Path) -> None:
    path = _two_window_axes(tmp_path, "[2, 12]", "[1, 3]")
    settings = _settings()
    parsed = parse_sweep_file(path, settings)
    with pytest.raises(InvalidVariantError, match=r"strategy\.formation_months"):
        expand_grid(parsed, settings)


def test_cadence_axis_refusal_comes_before_the_retire_check(tmp_path: Path) -> None:
    path = _rewrite(
        tmp_path,
        REFUSALS / "dsr-excess-cadence-axis.md",
        "retire_below = 0.0",
        "retire_below = 0.5",
    )
    with pytest.raises(CadenceAxisStatisticError):
        parse_sweep_file(path, _settings())


def test_groups_fixture_shares_no_fingerprint_with_the_sweep_fixture() -> None:
    settings = _settings()
    first = expand_grid(parse_sweep_file(SWEEP, settings), settings)
    groups = expand_grid(parse_sweep_file(GROUPS, settings), settings)
    assert not {v.fingerprint for v in first} & {v.fingerprint for v in groups}


def test_in_tolerance_duplicate_is_one_lattice_point(tmp_path: Path) -> None:
    path = _rewrite(tmp_path, SWEEP, "[0.05, 0.20]", "[0.10, 0.10000000000001]")
    with pytest.raises(DuplicateGridValueError, match=r"one lab\.axis_lattice point"):
        parse_sweep_file(path, _settings())


def test_in_tolerance_value_is_stored_as_the_lattice_point(tmp_path: Path) -> None:
    path = _rewrite(tmp_path, SWEEP, "[0.05, 0.20]", "[0.05, 0.10000000000001]")
    assert parse_sweep_file(path, _settings()).grid["strategy.top_fraction"] == (0.05, 0.1)


def test_bool_axis_is_never_lattice_checked(tmp_path: Path) -> None:
    settings = _settings(
        lab={"axis_lattice": {"strategy.top_fraction": 0.01, "strategy.signal_total_return": 0.3}}
    )
    grid = parse_sweep_file(GROUPS, settings).grid["strategy.signal_total_return"]
    assert grid == (True, False)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            "count_share_threshold = 0.05",
            "count_share_threshold = 0.05\n\n[universe]\nexclude_sic_ranges = [[1, 2]]",
            "fixed block",
        ),
        ("end = 2025-12-31", "end = 2023-01-02", "fixed block"),
    ],
)
def test_fixed_block_validated_whole_outside_axis_sections(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    with pytest.raises(SweepFileError, match=match):
        parse_sweep_file(_rewrite(tmp_path, SWEEP, old, new), _settings())


# ── the turnover key (backtest spec amendment #1358, T165) ──────────────────────

TURNOVER_KEY = "strategy.turnover_top_fraction"
_COMBINED_SECTIONS = """[combined]
top_fraction = 0.2
weighting = "equal"

[profitability]
basis = "gross"
annual_period_days = [350, 380]
max_fact_age_days = 548
exclude_sic_ranges = [[6000, 6999]]
include_derived = true
top_fraction = 0.10
weighting = "equal"

[strategy]
"""


def _turnover_sweep(
    tmp_path: Path, *, family: str = "momentum", fixed: str = "", grid: str | None = None
) -> Path:
    """The fixture sweep as `family`, with `fixed` lines added to `[strategy]` and, when
    `grid` is given, the turnover key as the one grid axis beside `strategy.top_fraction`
    fixed at 0.10 (so the variants differ only in the screen)."""
    text = SWEEP.read_text().replace('slug = "fixture-sweep"', 'slug = "turnover"')
    text = text.replace('family = "momentum"', f'family = "{family}"')
    head = _COMBINED_SECTIONS if family == "combined" else "[strategy]\n"
    text = text.replace("[strategy]\n", head + fixed, 1)
    if grid is not None:
        text = text.replace(
            '"strategy.top_fraction" = [0.05, 0.20]\n"schedule.rebalance_cadence" = '
            '["month_end", "week_end"]',
            f'"{TURNOVER_KEY}" = {grid}',
        )
        text = text.replace("[schedule]\n", '[schedule]\nrebalance_cadence = "month_end"\n')
        text = text.replace("[strategy]\n", "[strategy]\ntop_fraction = 0.10\n", 1)
    path = tmp_path / "turnover.md"
    path.write_text(text)
    return path


def test_a_momentum_fixed_block_without_the_key_parses_and_freezes_the_default(
    tmp_path: Path,
) -> None:
    live = _settings(strategy={"turnover_top_fraction": 0.5})
    parsed = parse_sweep_file(_turnover_sweep(tmp_path), live)
    assert TURNOVER_KEY not in parsed.fixed_params
    assert TURNOVER_KEY not in hypothesis.required_keys("momentum")
    for variant in expand_grid(parsed, live):
        assert variant.frozen_set[TURNOVER_KEY] == 1.0


def test_a_momentum_sweep_grids_the_screen_and_its_control(tmp_path: Path) -> None:
    settings = _settings()
    assert TURNOVER_KEY in settings.lab.sweepable_keys
    parsed = parse_sweep_file(_turnover_sweep(tmp_path, grid="[0.20, 1.0]"), settings)
    assert parsed.grid == {TURNOVER_KEY: (0.2, 1.0)}
    variants = expand_grid(parsed, settings)
    assert sorted(v.values[TURNOVER_KEY] for v in variants) == [0.2, 1.0]
    assert len({v.fingerprint for v in variants}) == 2
    (control,) = [v for v in variants if v.values[TURNOVER_KEY] == 1.0]
    assert TURNOVER_KEY not in frozen.canonical_frozen_set(control.frozen_set, "momentum")


def test_a_combined_sweep_gridding_the_key_is_refused(tmp_path: Path) -> None:
    path = _turnover_sweep(tmp_path, family="combined", grid="[0.20, 1.0]")
    with pytest.raises(AxisNotSweepableError, match=re.escape(TURNOVER_KEY)):
        parse_sweep_file(path, _settings())


def test_a_combined_fixed_block_naming_a_screen_is_refused(tmp_path: Path) -> None:
    path = _turnover_sweep(tmp_path, family="combined", fixed="turnover_top_fraction = 0.5\n")
    with pytest.raises(SweepFileError, match=re.escape(TURNOVER_KEY)):
        parse_sweep_file(path, _settings())


def test_a_combined_fixed_block_at_the_default_is_accepted(tmp_path: Path) -> None:
    for fixed in ("turnover_top_fraction = 1\n", ""):
        path = _turnover_sweep(tmp_path, family="combined", fixed=fixed)
        parsed = parse_sweep_file(path, _settings(strategy={"turnover_top_fraction": 0.5}))
        variants = expand_grid(parsed, _settings(strategy={"turnover_top_fraction": 0.5}))
        assert all(v.frozen_set[TURNOVER_KEY] == 1.0 for v in variants)
