"""`sweep register` (strategy-lab spec req 1): in an existing family (the "Sweep
registration and guardrails" criterion's store-level part, the "Fingerprint" and
"Family readiness" criteria; plan task T104) and a new family's first registration (the
"Family rules" criterion's new-family part; plan task T104b).

Every case runs on `lab_store` with H1's fixture twin registered, given its
`pre_lab_hypotheses` and `hypothesis_fingerprints` rows and the family rules the lab
migration (T113) would write from it, and run once (an `ok`, non-synthetic, in-sample
trial over its default window) unless the case is about readiness. The twin and the
sweep are the fixture files with `in_sample_start` moved to 2018-01-31, so that a
12-month formation anchor at the first rebalance falls inside the fixture bars (first
session 2017-01-03) and refusal 1(d) is exercised only where a case asks for it.

The new-family cases register a `profitability` sweep written by `_profitability_sweep`.
`profitability` is a root in the real `FAMILY_PARENTS`; the child cases make it
momentum's child with a test-only `monkeypatch` of the table.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, get_args

import duckdb
import pytest
from conftest import load_universe_fixtures, mark_pre_lab

from tradepartner import config
from tradepartner.backtest import frozen, hypothesis, results, sweep
from tradepartner.backtest.holdout import Frozen, default_in_sample_window
from tradepartner.backtest.sweep import (
    AnchorInfeasibleError,
    DevelopmentBoundaryError,
    FamilyLatticeError,
    FamilyNotReadyError,
    FamilyRuleError,
    FingerprintRegisteredError,
    NewFamilyError,
    NoFamilyRulesError,
    SweepFileError,
    SweepRegistration,
    register,
)
from tradepartner.config import Settings
from tradepartner.store import lab_registry, lab_schema, registry, schema
from tradepartner.store.db import configure_connection
from tradepartner.store.lab_schema import LabNotInitialised

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SWEEP_SOURCE = FIXTURES / "sweeps" / "fixture-sweep.md"
TWIN_SOURCE = FIXTURES / "hypotheses" / "fixture-momentum.md"
REFUSALS = FIXTURES / "sweeps" / "refusals"
IN_SAMPLE_START = "in_sample_start = 2018-01-31"
_TABLES = ("hypotheses", "sweeps", "sweep_variants", "hypothesis_fingerprints", "family_rules")


def _settings(**lab: Any) -> Settings:
    return Settings(_env_file=None, lab=lab) if lab else Settings(_env_file=None)


def _copy(tmp_path: Path, source: Path, *edits: tuple[str, str], slug: str | None = None) -> Path:
    """`source` with `in_sample_start` moved to 2018-01-31, `edits` applied in order and,
    with `slug`, renamed (file and block)."""
    text = source.read_text().replace("in_sample_start = 2017-01-31", IN_SAMPLE_START)
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new, 1)
    name = slug or source.stem
    text = text.replace(f'slug = "{source.stem}"', f'slug = "{name}"')
    directory = tmp_path / "files"
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(text)
    return path


def _counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _TABLES:
        present = conn.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = ?", [table]
        ).fetchone()
        if present and present[0]:
            (counts[table],) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # type: ignore[misc]
    return counts


def _run_ok(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    record: registry.HypothesisRecord,
    *,
    synthetic: bool = False,
    boundary: date | None = None,
) -> None:
    """An `ok` in-sample trial of `record` over its default window (under `boundary`)."""
    window = default_in_sample_window(Frozen.from_hypothesis(record), "month_end", boundary)
    handle = registry.open_trial(
        conn,
        hypothesis_id=record.hypothesis_id,
        kind="in_sample",
        start_session=window.start,
        end_session=window.end,
        data_cutoff=datetime(window.end.year, window.end.month, window.end.day, 21, tzinfo=UTC),
        synthetic=synthetic,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )
    # The base-level metrics `registry.family_sharpes` reads for V on both bases.
    registry.write_metrics(
        conn,
        handle,
        [
            registry.MetricRow("strategy", 15.0, "sharpe_period", 0.1),
            registry.MetricRow("strategy", 15.0, "sharpe_period_excess_spy", 0.1),
            registry.MetricRow("strategy", 15.0, "periods_per_year", 12.0),
        ],
    )
    assert registry.write_result(conn, handle, registry.ResultStatistics()) == "ok"


def _spend_holdout(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    record: registry.HypothesisRecord,
) -> None:
    """A `holdout` trial of `record` (any outcome is a spend)."""
    end = record.holdout_end
    registry.open_trial(
        conn,
        hypothesis_id=record.hypothesis_id,
        kind="holdout",
        start_session=record.holdout_start,
        end_session=end,
        data_cutoff=datetime(end.year, end.month, end.day, 21, tzinfo=UTC),
        synthetic=False,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
        holdout_reason="test spend",
    )


def _direct(
    conn: duckdb.DuckDBPyConnection, path: Path, settings: Settings
) -> registry.HypothesisRecord:
    """`path` registered straight through the registry, as before the lab (H1's twin)
    or as `sweep promote` does: `hypothesis.register` refuses a plain standalone file
    on a lab store (T104c)."""
    parsed = hypothesis.parse_file(path)
    return registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=path.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=hypothesis.frozen_params(parsed, settings),
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )


def _twin(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    *,
    run: bool = True,
    rules_settings: Settings | None = None,
    seed: float | None = None,
) -> registry.HypothesisRecord:
    """H1's fixture twin registered, marked pre-lab, fingerprinted, its family rules
    written as the lab migration would, and (with `run`) run once."""
    record = _direct(conn, _copy(tmp_path, TWIN_SOURCE), settings)
    mark_pre_lab(conn, record.hypothesis_id)
    lab_registry.write_fingerprint(
        conn,
        record.hypothesis_id,
        frozen.fingerprint(record.family, frozen.frozen_values(record), record.in_sample_start),
    )
    lab_registry.write_family_rules(
        conn,
        family=record.family,
        first_hypothesis_id=record.hypothesis_id,
        parent_family=None,
        holdout_start=record.holdout_start,
        holdout_end=record.holdout_end,
        in_sample_start=record.in_sample_start,
        fixed_params=sweep.family_rule_params(frozen.frozen_values(record)),
        sr_star_seed_annual=seed,
        settings=rules_settings or settings,
    )
    if run:
        _run_ok(conn, settings, tmp_path, record)
    return record


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
def ready(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> duckdb.DuckDBPyConnection:
    """`lab_store` with the twin registered, its rules written and its first trial run."""
    _twin(lab_store, settings, tmp_path)
    return lab_store


def _refused(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    settings: Settings,
    error: type[Exception],
    match: str | None,
) -> None:
    before = _counts(conn)
    with pytest.raises(error, match=match):
        register(conn, path, settings, registered_by="test")
    assert _counts(conn) == before


# ── the rows a registration writes ──────────────────────────────────────────────


def test_register_writes_the_sweep_its_four_variants_and_their_rows(
    ready: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    settings = _settings(max_promotions_per_sweep=2, max_failures_per_variant=3)
    path = _copy(tmp_path, SWEEP_SOURCE)
    before = _counts(ready)

    result = register(ready, path, settings, registered_by="owner")

    assert isinstance(result, SweepRegistration) and result.created
    after = _counts(ready)
    assert after["sweeps"] == before["sweeps"] + 1
    for table in ("hypotheses", "sweep_variants", "hypothesis_fingerprints"):
        assert after[table] == before[table] + 4, table
    assert after["family_rules"] == before["family_rules"]

    row = result.sweep
    assert (row.slug, row.family, row.n_variants, row.registered_by) == (
        "fixture-sweep",
        "momentum",
        4,
        "owner",
    )
    assert row.doc_path == path.as_posix()
    assert row.grid == {
        "strategy.top_fraction": [0.05, 0.2],
        "schedule.rebalance_cadence": ["month_end", "week_end"],
    }
    # The caps and floor copied from the settings in force.
    assert (row.max_promotions, row.min_dsr_floor, row.max_failures_per_variant) == (
        2,
        settings.lab.promotion_min_dsr_excess,
        3,
    )
    assert row.axis_lattice == settings.lab.axis_lattice
    assert (row.selection_statistic, row.promote_at_least, row.retire_below) == (
        "sharpe_annual_excess_spy",
        0.5,
        0.0,
    )

    slugs = [h.slug for h in result.hypotheses]
    assert slugs == [f"fixture-sweep--r{row.sweep_id}-v{i}" for i in range(1, 5)]
    shas = [h.params_sha256 for h in result.hypotheses]
    assert shas == sorted(shas)
    for record, variant in zip(result.hypotheses, result.variants, strict=True):
        assert record.hypothesis_id == variant.hypothesis_id
        assert (record.in_sample_start, record.holdout_start, record.holdout_end) == (
            date(2018, 1, 31),
            date(2023, 1, 3),
            date(2025, 12, 31),
        )
        assert record.doc_path == path.as_posix()
        assert record.family == "momentum"
        assert variant.fingerprint == frozen.fingerprint(
            "momentum", record.params, record.in_sample_start
        )
        held = lab_registry.fingerprint_registered(ready, variant.fingerprint)
        assert held is not None and held.hypothesis_id == record.hypothesis_id
    assert [v.variant_index for v in result.variants] == [1, 2, 3, 4]
    assert {(v.variant_params["strategy.top_fraction"]) for v in result.variants} == {0.05, 0.2}


def test_register_is_idempotent_for_an_unchanged_file(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    path = _copy(tmp_path, SWEEP_SOURCE)
    first = register(ready, path, settings, registered_by="test")
    before = _counts(ready)

    again = register(ready, path, settings, registered_by="test")

    assert not again.created
    assert again.sweep == first.sweep
    assert again.hypotheses == first.hypotheses
    assert _counts(ready) == before


def test_idempotence_compares_canonical_frozen_sets_not_raw_hashes(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """A stored variant whose set lacks a `FROZEN_KEY_DEFAULTS` key at its default (a
    registration from before the key landed) is the same frozen set."""
    path = _copy(tmp_path, SWEEP_SOURCE)
    first = register(ready, path, settings, registered_by="test")
    for record in first.hypotheses:
        params = {k: v for k, v in record.params.items() if k != "schedule.signal_anchor"}
        ready.execute(
            "UPDATE hypotheses SET params_json = ?, params_sha256 = ? WHERE hypothesis_id = ?",
            [
                registry.canonical_params_json(params),
                registry.params_sha256(params),
                record.hypothesis_id,
            ],
        )
    before = _counts(ready)

    again = register(ready, path, settings, registered_by="test")

    assert not again.created
    assert again.sweep == first.sweep
    assert _counts(ready) == before


def test_a_changed_file_is_a_new_registration_whose_unchanged_variants_are_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    first = register(ready, _copy(tmp_path, SWEEP_SOURCE), settings, registered_by="test")
    edited = _copy(tmp_path, SWEEP_SOURCE, ("Placeholder.", "A prose edit."))

    _refused(
        ready, edited, settings, FingerprintRegisteredError, re.escape(first.hypotheses[0].slug)
    )


def test_a_changed_file_with_only_new_variants_registers_as_a_second_registration(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    register(ready, _copy(tmp_path, SWEEP_SOURCE), settings, registered_by="test")
    edited = _copy(
        tmp_path,
        SWEEP_SOURCE,
        ('"strategy.top_fraction" = [0.05, 0.20]', '"strategy.top_fraction" = [0.15, 0.25]'),
    )

    second = register(ready, edited, settings, registered_by="test")

    assert second.created and second.sweep.sweep_id == 2
    assert second.hypotheses[0].slug == "fixture-sweep--r2-v1"
    latest = lab_registry.sweep_by_slug(ready, "fixture-sweep")
    assert latest is not None and latest.sweep_id == 2


# ── refusals: lab, oracle, family rules ─────────────────────────────────────────


def test_a_plain_fixture_store_raises_lab_not_initialised_and_writes_nothing(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _refused(
        fixture_store,
        _copy(tmp_path, SWEEP_SOURCE),
        settings,
        LabNotInitialised,
        "not initialised",
    )


def test_oracle_is_refused_on_the_real_store(tmp_path: Path) -> None:
    store = tmp_path / "real.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store)})
    conn = duckdb.connect(str(store))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, FIXTURES / "universe")
        lab_schema.apply_lab_schema(conn)
        oracle = _copy(tmp_path, SWEEP_SOURCE, ('family = "momentum"', 'family = "oracle"'))
        _refused(conn, oracle, settings, registry.RealStoreRefused, "real store")
    finally:
        conn.close()


def test_a_family_with_hypotheses_but_no_rules_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    # A store the lab migration did not populate: the twin registered, no rules row.
    _direct(lab_store, _copy(tmp_path, TWIN_SOURCE), settings)

    _refused(
        lab_store, _copy(tmp_path, SWEEP_SOURCE), settings, NoFamilyRulesError, "no family rules"
    )


@pytest.mark.parametrize(
    ("edit", "rule"),
    [
        (("start = 2023-01-03", "start = 2023-02-01"), "holdout.start"),
        (("end = 2025-12-31", "end = 2025-11-28"), "holdout.end"),
        ((IN_SAMPLE_START, "in_sample_start = 2018-02-28"), "in_sample_start"),
        (("[gap]", "[universe]\ntop_n_by_cap = 400\n\n[gap]"), "universe.top_n_by_cap"),
        (("[gap]", '[execution]\nfill_price = "open"\n\n[gap]'), "execution.fill_price"),
        (("count_share_threshold = 0.05", "count_share_threshold = 0.04"), "gap.count_share"),
        (("[gap]", "[backtest]\ninitial_capital = 50000.0\n\n[gap]"), "backtest.initial_capital"),
        (
            ("[0.0, 30.0, 60.0, 100.0]", "[0.0, 30.0, 60.0]"),
            "costs.sensitivity_per_side_bps",
        ),
        (("per_side_bps = 15.0", "per_side_bps = 10.0"), r"costs.per_side_bps \(lower"),
    ],
)
def test_a_file_off_the_family_rules_is_refused_naming_the_rule(
    ready: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    edit: tuple[str, str],
    rule: str,
) -> None:
    _refused(ready, _copy(tmp_path, SWEEP_SOURCE, edit), settings, FamilyRuleError, rule)


def test_a_file_naming_another_parent_family_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    path = _copy(
        tmp_path,
        SWEEP_SOURCE,
        ('family = "momentum"', 'family = "momentum"\nparent_family = "profitability"'),
    )

    _refused(ready, path, settings, FamilyRuleError, "parent_family")


def test_raising_the_base_cost_is_accepted(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    path = _copy(tmp_path, SWEEP_SOURCE, ("per_side_bps = 15.0", "per_side_bps = 20.0"))

    result = register(ready, path, settings, registered_by="test")

    assert result.created
    assert {h.params["costs.per_side_bps"] for h in result.hypotheses} == {20.0}


# ── refusals: fingerprints ──────────────────────────────────────────────────────


def test_a_second_sweep_holding_a_fingerprint_of_the_first_is_refused_naming_the_variant(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    first = register(ready, _copy(tmp_path, SWEEP_SOURCE), settings, registered_by="test")
    # Another slug, another selection statistic and [lab] values, two shared points:
    # top_fraction 0.20 at either cadence.
    other = _copy(
        tmp_path,
        SWEEP_SOURCE,
        ('"strategy.top_fraction" = [0.05, 0.20]', '"strategy.top_fraction" = [0.20, 0.30]'),
        (
            'selection_statistic = "sharpe_annual_excess_spy"',
            'selection_statistic = "excess_cagr_spy"',
        ),
        ("promote_at_least = 0.5", "promote_at_least = 0.8"),
        slug="other-sweep",
    )
    shared = "|".join(
        re.escape(h.slug)
        for h, v in zip(first.hypotheses, first.variants, strict=True)
        if v.variant_params["strategy.top_fraction"] == 0.2
    )
    _refused(ready, other, settings, FingerprintRegisteredError, shared)


def test_a_sweep_holding_the_twins_fingerprint_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    # The twin is top_fraction 0.10 at month_end with the fixed values of the sweep.
    holding = _copy(
        tmp_path,
        SWEEP_SOURCE,
        ('"strategy.top_fraction" = [0.05, 0.20]', '"strategy.top_fraction" = [0.10, 0.20]'),
    )

    _refused(ready, holding, settings, FingerprintRegisteredError, "'fixture-momentum'")


# ── refusals: family readiness ──────────────────────────────────────────────────


def test_readiness_refuses_before_the_twins_first_ok_trial_and_accepts_after(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    twin = _twin(lab_store, settings, tmp_path, run=False)
    path = _copy(tmp_path, SWEEP_SOURCE)
    _refused(lab_store, path, settings, FamilyNotReadyError, "'fixture-momentum'")
    # A synthetic trial is no reading of the twin.
    _run_ok(lab_store, settings, tmp_path, twin, synthetic=True)
    _refused(lab_store, path, settings, FamilyNotReadyError, "'fixture-momentum'")

    _run_ok(lab_store, settings, tmp_path, twin)

    assert register(lab_store, path, settings, registered_by="test").created


def test_readiness_reads_the_twins_default_window_under_the_boundary(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """ADR 0016 point 2 (plan T142b): with a boundary that moves the twin's default
    window, its run over the old window is no reading of it."""
    moved = date(2021, 6, 30)
    registry.write_development_boundary(ready, boundary=moved, reason="test")
    path = _copy(tmp_path, SWEEP_SOURCE)
    _refused(ready, path, settings, FamilyNotReadyError, "'fixture-momentum'")
    _run_ok(
        ready,
        settings,
        tmp_path,
        registry.get_hypothesis(ready, "fixture-momentum"),
        boundary=moved,
    )
    assert register(ready, path, settings, registered_by="test").created


@pytest.mark.parametrize("start", ["2021-06-30", "2022-01-31"])
def test_an_in_sample_start_on_or_after_the_boundary_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path, start: str
) -> None:
    """ADR 0016 point 2: no registration's default window is ever empty. The refusal
    comes before the family rules' (the file's `in_sample_start` differs from them)."""
    registry.write_development_boundary(ready, boundary=date(2021, 6, 30), reason="test")
    path = _copy(tmp_path, SWEEP_SOURCE, (IN_SAMPLE_START, f"in_sample_start = {start}"))
    _refused(ready, path, settings, DevelopmentBoundaryError, "development boundary 2021-06-30")


def test_an_unrun_promoted_hypothesis_blocks_the_next_sweep(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    promoted = _copy(
        tmp_path,
        TWIN_SOURCE,
        ("top_fraction = 0.10", "top_fraction = 0.15"),
        slug="fixture-promoted",
    )
    record = _direct(ready, promoted, settings)
    path = _copy(tmp_path, SWEEP_SOURCE)

    _refused(ready, path, settings, FamilyNotReadyError, "'fixture-promoted'")

    _run_ok(ready, settings, tmp_path, record)
    assert register(ready, path, settings, registered_by="test").created


# ── refusals: anchors and the family lattice ────────────────────────────────────


def test_a_month_end_anchor_with_no_skip_at_week_end_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    path = _copy(tmp_path, SWEEP_SOURCE, ("skip_months = 1", "skip_months = 0"))

    _refused(ready, path, settings, AnchorInfeasibleError, "can fall after T")


def test_a_formation_anchor_before_the_stores_first_session_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    first = lab_registry.first_session(ready)
    assert first == date(2017, 1, 3)
    path = _copy(tmp_path, SWEEP_SOURCE, ("formation_months = 12", "formation_months = 24"))

    _refused(ready, path, settings, AnchorInfeasibleError, "precedes the store's first session")


def test_a_grid_value_off_the_family_rules_lattice_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    # The family's lattice was copied at its first registration (step 0.05); the live
    # step (0.01) admits 0.12 at parse time, the family's does not.
    _twin(
        lab_store,
        settings,
        tmp_path,
        rules_settings=_settings(axis_lattice={"strategy.top_fraction": 0.05}),
    )
    path = _copy(
        tmp_path,
        SWEEP_SOURCE,
        ('"strategy.top_fraction" = [0.05, 0.20]', '"strategy.top_fraction" = [0.05, 0.12]'),
    )

    _refused(lab_store, path, settings, FamilyLatticeError, "0.12")


def test_a_non_positive_family_lattice_step_is_refused_not_divided_by(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _twin(
        lab_store,
        settings,
        tmp_path,
        rules_settings=_settings(axis_lattice={"strategy.top_fraction": 0.0}),
    )

    _refused(lab_store, _copy(tmp_path, SWEEP_SOURCE), settings, FamilyLatticeError, "not > 0")


@pytest.mark.parametrize("source", sorted(REFUSALS.glob("*.md")), ids=lambda p: p.stem)
def test_a_file_level_refusal_writes_nothing(
    ready: duckdb.DuckDBPyConnection, settings: Settings, source: Path
) -> None:
    _refused(ready, source, _settings(max_variants_per_sweep=3), SweepFileError, None)


def test_family_rule_params_are_the_forbidden_prefix_keys() -> None:
    params = {
        "strategy.top_fraction": 0.1,
        "schedule.rebalance_cadence": "month_end",
        "costs.per_side_bps": 15.0,
        "universe.top_n_by_cap": 1000,
        "holdout.start": "2023-01-03",
        "execution.fill_price": "close",
        "benchmarks": ["SPY"],
        "alpaca.historical_feed": "sip",
    }
    assert sweep.family_rule_params(params) == {
        "costs.per_side_bps": 15.0,
        "universe.top_n_by_cap": 1000,
        "holdout.start": "2023-01-03",
        "execution.fill_price": "close",
        "benchmarks": ["SPY"],
        "alpaca.historical_feed": "sip",
    }


# ── a new family's first registration (T104b) ───────────────────────────────────

#: A holdout after the twin's (2023-01-03 to 2025-12-31), so no overlap.
LATER_HOLDOUT = (date(2026, 1, 2), date(2026, 6, 30))


def _profitability_sweep(
    tmp_path: Path,
    *,
    slug: str = "profitability-sweep",
    holdout: tuple[date, date] = LATER_HOLDOUT,
    parent: str | None = None,
    cadences: tuple[str, ...] = ("month_end", "week_end"),
) -> Path:
    """A `profitability` sweep file over `schedule.rebalance_cadence`."""
    parent_line = f'parent_family = "{parent}"\n' if parent is not None else ""
    grid = ", ".join(f'"{c}"' for c in cadences)
    text = f"""# Sweep: fixture profitability sweep (test only)

```toml sweep
slug = "{slug}"
family = "profitability"
{parent_line}title = "Fixture profitability sweep"
in_sample_start = 2018-01-31

[holdout]
start = {holdout[0].isoformat()}
end = {holdout[1].isoformat()}

[profitability]
basis = "gross"
annual_period_days = [350, 380]
max_fact_age_days = 548
exclude_sic_ranges = [[6000, 6999]]
include_derived = true
top_fraction = 0.2
weighting = "equal"

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[gap]
count_share_threshold = 0.05

[grid]
"schedule.rebalance_cadence" = [{grid}]

[lab]
selection_statistic = "sharpe_annual_excess_spy"
expected_excess_cagr_spy_pp = 0.0
expected_range_pp = [-2.0, 2.0]
promote_at_least = 0.5
retire_below = 0.0
```

Placeholder prose.
"""
    directory = tmp_path / "files"
    directory.mkdir(exist_ok=True)
    path = directory / f"{slug}.md"
    path.write_text(text)
    return path


@pytest.fixture
def child_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only: `profitability` as `momentum`'s child in `FAMILY_PARENTS`."""
    monkeypatch.setitem(config.FAMILY_PARENTS, "profitability", "momentum")


def test_every_non_oracle_family_has_a_family_parents_entry() -> None:
    families = set(get_args(config.HypothesisFamily))
    assert set(config.FAMILY_PARENTS) == families - {"oracle"}
    for parent in config.FAMILY_PARENTS.values():
        assert parent is None or parent in families - {"oracle"}


def test_a_root_familys_first_sweep_writes_its_rules_with_no_parent_and_its_own_mark(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    # The twin has spent nothing: a root needs no parent's spend.
    assert config.FAMILY_PARENTS["profitability"] is None
    assert lab_registry.family_rules(ready, "profitability") is None
    before = _counts(ready)

    result = register(ready, _profitability_sweep(tmp_path), settings, registered_by="test")

    assert result.created
    assert _counts(ready)["family_rules"] == before["family_rules"] + 1
    rules = lab_registry.family_rules(ready, "profitability")
    assert rules is not None
    assert rules.parent_family is None
    assert rules.sr_star_seed_annual is None
    assert rules.first_hypothesis_id == result.hypotheses[0].hypothesis_id
    assert (rules.holdout_start, rules.holdout_end) == LATER_HOLDOUT
    assert rules.in_sample_start == date(2018, 1, 31)
    assert rules.fixed_params == sweep.family_rule_params(result.hypotheses[0].params)
    assert rules.fixed_params["costs.per_side_bps"] == 15.0
    assert rules.fixed_params["holdout.start"] == "2026-01-02"
    lab = settings.lab
    assert (
        rules.max_family_holdout_spends,
        rules.max_family_promotions,
        rules.min_sharpe_variance_annual,
        rules.axis_lattice,
    ) == (
        lab.max_family_holdout_spends,
        lab.max_family_promotions,
        lab.min_sharpe_variance_annual,
        lab.axis_lattice,
    )


def test_a_root_family_on_an_empty_lab_store_registers(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    result = register(lab_store, _profitability_sweep(tmp_path), settings, registered_by="test")

    assert result.created
    rules = lab_registry.family_rules(lab_store, "profitability")
    assert rules is not None and rules.parent_family is None


def test_a_new_familys_holdout_overlapping_an_existing_familys_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    path = _profitability_sweep(tmp_path, holdout=(date(2025, 6, 2), date(2026, 6, 30)))

    _refused(ready, path, settings, NewFamilyError, "overlaps family 'momentum'")


def test_an_oracle_familys_holdout_is_no_overlap(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    twin = registry.get_hypothesis(ready, "fixture-momentum")
    lab_registry.write_family_rules(
        ready,
        family="oracle",
        first_hypothesis_id=twin.hypothesis_id,
        parent_family=None,
        holdout_start=LATER_HOLDOUT[0],
        holdout_end=LATER_HOLDOUT[1],
        in_sample_start=date(2018, 1, 31),
        fixed_params={},
        sr_star_seed_annual=None,
        settings=settings,
    )

    assert register(ready, _profitability_sweep(tmp_path), settings, registered_by="test").created


def test_a_new_family_naming_another_parent_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    path = _profitability_sweep(tmp_path, parent="momentum")

    _refused(ready, path, settings, FamilyRuleError, "parent_family 'momentum'")


@pytest.mark.usefixtures("child_table")
def test_a_child_naming_its_own_parent_is_accepted(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    twin = _twin(lab_store, settings, tmp_path)
    _spend_holdout(lab_store, settings, tmp_path, twin)

    path = _profitability_sweep(tmp_path, parent="momentum")

    assert register(lab_store, path, settings, registered_by="test").created


@pytest.mark.usefixtures("child_table")
def test_a_child_whose_parent_has_no_rules_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _refused(
        lab_store,
        _profitability_sweep(tmp_path),
        settings,
        NewFamilyError,
        "parent 'momentum' has no family rules",
    )


@pytest.mark.usefixtures("child_table")
def test_a_child_holding_out_before_the_parents_holdout_end_is_refused(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    twin = registry.get_hypothesis(ready, "fixture-momentum")
    _spend_holdout(ready, settings, tmp_path, twin)
    # Before the parent's holdout, so no overlap: only the ordering rule refuses it.
    path = _profitability_sweep(tmp_path, holdout=(date(2022, 1, 3), date(2022, 12, 30)))

    _refused(ready, path, settings, NewFamilyError, "is not after parent family 'momentum'")


@pytest.mark.usefixtures("child_table")
def test_a_child_is_refused_until_the_parent_spends_and_its_mark_starts_at_the_parents(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    twin = _twin(lab_store, settings, tmp_path, seed=0.77)
    path = _profitability_sweep(tmp_path)
    _refused(lab_store, path, settings, NewFamilyError, "neither a holdout spend")

    _spend_holdout(lab_store, settings, tmp_path, twin)
    parent_mark = lab_registry.family_sr_star_high_water_mark(
        lab_store,
        "momentum",
        n_trials_today=results.family_n(lab_store, "momentum"),
        sharpe_variance_annual_today=registry.family_sharpes(lab_store, "momentum").variance(
            "excess_spy"
        ),
    )
    result = register(lab_store, path, settings, registered_by="test")

    assert result.created
    rules = lab_registry.family_rules(lab_store, "profitability")
    assert rules is not None
    assert rules.parent_family == "momentum"
    assert rules.sr_star_seed_annual == parent_mark
    assert parent_mark >= 0.77
    # The child's own mark starts at the parent's.
    assert (
        lab_registry.family_sr_star_high_water_mark(
            lab_store, "profitability", n_trials_today=0, sharpe_variance_annual_today=None
        )
        == parent_mark
    )


@pytest.mark.usefixtures("child_table")
def test_a_child_is_accepted_when_the_parent_reached_its_spend_cap(
    lab_store: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    # The cap path of `family_holdout_spent_or_capped`: one spend at a cap of one.
    settings = _settings(max_family_holdout_spends=1)
    twin = _twin(lab_store, settings, tmp_path)
    _spend_holdout(lab_store, settings, tmp_path, twin)

    assert register(
        lab_store, _profitability_sweep(tmp_path), settings, registered_by="test"
    ).created


def test_the_rules_are_copied_once_and_a_later_config_change_changes_nothing(
    ready: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    first_settings = _settings(
        max_family_holdout_spends=2,
        max_family_promotions=1,
        min_sharpe_variance_annual=0.09,
        axis_lattice={"strategy.top_fraction": 0.05},
    )
    register(ready, _profitability_sweep(tmp_path), first_settings, registered_by="test")
    written = lab_registry.family_rules(ready, "profitability")
    assert written is not None
    assert (
        written.max_family_holdout_spends,
        written.max_family_promotions,
        written.min_sharpe_variance_annual,
        written.axis_lattice,
    ) == (2, 1, 0.09, {"strategy.top_fraction": 0.05})
    before = _counts(ready)

    # A second sweep in the family under the live defaults: the existing family's path.
    second = register(
        ready,
        _profitability_sweep(tmp_path, slug="profitability-daily", cadences=("daily",)),
        _settings(),
        registered_by="test",
    )

    assert second.created
    assert _counts(ready)["family_rules"] == before["family_rules"]
    assert lab_registry.family_rules(ready, "profitability") == written


def test_a_non_oracle_family_missing_from_family_parents_is_refused(
    ready: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(config.FAMILY_PARENTS, "profitability")

    path = _profitability_sweep(tmp_path)

    _refused(ready, path, settings, NewFamilyError, "no entry in FAMILY_PARENTS")
