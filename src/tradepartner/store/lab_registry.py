"""The strategy-lab registry: writes and the registration-time reads
(strategy-lab plan T103; spec "Data / interfaces" > Tables and Interfaces).

The only code that writes the lab tables `lab_schema.apply_lab_schema` creates,
except `pre_lab_hypotheses` (the lab migration, T113, and the conftest's
`mark_pre_lab`) and `store_markers` (the fixture loader). **Inserts and reads
only**, like `registry.py`, with one exception: `close_sweep_run` fills the
end-of-run columns of the `sweep_runs` row `open_sweep_run` inserted, once,
while they are still NULL (`lab_schema`'s docstring: those columns are known
only at the run's end). Nothing here deletes, drops, truncates or vacuums
(text check in the tests). Ids are `MAX + 1` inside the caller's write
transaction, as for the trial registry; every function leaves the transaction
to the caller.

Every function calls `lab_schema.require_lab` first, so on a store without the
lab tables (the owner's store before T113, a plain fixture store) it raises
`LabNotInitialised` and the Phase 3 rules stay in force (plan, choice 2).

**Copied at write time, never read live afterwards.** `write_family_rules`
copies `lab.max_family_holdout_spends`, `lab.max_family_promotions`,
`lab.min_sharpe_variance_annual` and `lab.axis_lattice` from `Settings` into
the family's one row, and `register_sweep` copies `lab.max_promotions_per_sweep`,
`lab.promotion_min_dsr_excess`, `lab.max_failures_per_variant` and
`lab.axis_lattice` into the sweep's row, so a later config change never
loosens a registered family or sweep (spec req 1, Definitions > Family rules).

**SR\\* values.** `close_sweep_run` stores the `n_trials_at_end` and
`sr_star_annual_at_end` its caller computed (the runner, T107, from
`results.family_n` and `family_sharpes` with V floored at the family rules'
`min_sharpe_variance_annual`); `family_sr_star_high_water_mark` takes today's
N and annual V the same way, as arguments, and floors V itself. This module
computes no annual V.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import duckdb

from tradepartner.backtest.frozen import FROZEN_KEY_DEFAULTS, frozen_values, is_default
from tradepartner.backtest.holdout import Frozen, default_in_sample_window
from tradepartner.backtest.metrics import expected_max_sharpe
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings, get_settings
from tradepartner.store.db import insert_row, utc_now
from tradepartner.store.lab_schema import require_lab
from tradepartner.store.registry import (
    BASE_COST_KEY,
    UNREAD,
    HypothesisRecord,
    RegistryError,
    Unread,
    boundary_date,
    canonical_params_json,
    code_version,
    family_holdout_spends,
    get_hypothesis_by_id,
)

CADENCE_KEY: Final = "schedule.rebalance_cadence"


class LabRegistryError(RegistryError):
    """A lab registry call that would break a lab registry rule."""


@dataclass(frozen=True)
class FamilyRules:
    """One `family_rules` row, its JSON columns decoded."""

    family: str
    first_hypothesis_id: int
    parent_family: str | None
    holdout_start: date
    holdout_end: date
    in_sample_start: date
    fixed_params: dict[str, Any]
    fixed_params_sha256: str
    max_family_holdout_spends: int
    max_family_promotions: int
    min_sharpe_variance_annual: float
    axis_lattice: dict[str, float]
    sr_star_seed_annual: float | None
    registered_at: datetime


@dataclass(frozen=True)
class SweepRecord:
    """One `sweeps` row (one sweep registration), its JSON columns decoded."""

    sweep_id: int
    slug: str
    family: str
    title: str
    doc_path: str
    doc_sha256: str
    grid: dict[str, list[Any]]
    grid_sha256: str
    n_variants: int
    selection_statistic: str
    expected_excess_cagr_spy_pp: float
    expected_range_lo_pp: float
    expected_range_hi_pp: float
    promote_at_least: float
    retire_below: float
    max_promotions: int
    min_dsr_floor: float
    max_failures_per_variant: int
    axis_lattice: dict[str, float]
    in_sample_start: date
    holdout_start: date
    holdout_end: date
    registered_at: datetime
    registered_by: str


@dataclass(frozen=True)
class SweepVariant:
    """One `sweep_variants` row; `variant_params` holds the varied keys only."""

    sweep_id: int
    variant_index: int
    hypothesis_id: int
    fingerprint: str
    variant_params: dict[str, Any]


@dataclass(frozen=True)
class PromotionDecision:
    """An `owner_decisions` row of kind `promotion`; `hypothesis_id` is the
    promoted hypothesis (the row's `hypothesis_id` column, req 4)."""

    decision_id: int
    made_at: datetime
    hypothesis_id: int
    values: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class OperationsBookDecision:
    """An `owner_decisions` row of kind `operations_book` (strategy-lab spec req 1,
    amendment 2026-10-10); `hypothesis_id` is the registered operations file (the
    row's `hypothesis_id` column), `values` names the sweep and the variant."""

    decision_id: int
    made_at: datetime
    hypothesis_id: int
    values: dict[str, Any]
    reason: str


@dataclass(frozen=True)
class GrandfatheredMember:
    """A pre-lab member whose frozen values differ from its family's rules
    (spec req 13); `differences` names each differing rule."""

    hypothesis_id: int
    family: str
    slug: str
    differences: tuple[str, ...]


#: Dataclass field -> the JSON column it decodes.
_JSON_COLUMNS: Final = {
    "fixed_params": "fixed_params_json",
    "axis_lattice": "axis_lattice_json",
    "grid": "grid_json",
    "variant_params": "variant_params_json",
    "values": "values_json",
}


def _select[Row](
    conn: duckdb.DuckDBPyConnection, cls: type[Row], table: str, where: str, args: list[Any]
) -> list[Row]:
    names = [f.name for f in fields(cls)]  # type: ignore[arg-type]
    columns = ", ".join(_JSON_COLUMNS.get(name, name) for name in names)
    rows = conn.execute(f"SELECT {columns} FROM {table} WHERE {where}", args).fetchall()
    return [
        cls(
            **{
                name: json.loads(value) if name in _JSON_COLUMNS else value
                for name, value in zip(names, row, strict=True)
            }
        )
        for row in rows
    ]


def _next_id(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> int:
    (next_id,) = conn.execute(  # type: ignore[misc]
        f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}"
    ).fetchone()
    return int(next_id)


def _json_sha256(value: Mapping[str, Any]) -> tuple[str, str]:
    text = canonical_params_json(value)
    return text, sha256(text.encode()).hexdigest()


def _exists(conn: duckdb.DuckDBPyConnection, table: str, column: str, value: object) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", [value]).fetchone() is not None


# --- fingerprints ------------------------------------------------------------


def fingerprint_registered(
    conn: duckdb.DuckDBPyConnection, fingerprint: str
) -> HypothesisRecord | None:
    """The earliest registration (lowest `hypothesis_id`) holding `fingerprint`,
    or None. Earliest, so a grandfathered duplicate (spec req 13) or a promoted
    file sharing its variant's fingerprint never hides the first record."""
    require_lab(conn)
    row = conn.execute(
        "SELECT MIN(hypothesis_id) FROM hypothesis_fingerprints WHERE fingerprint = ?",
        [fingerprint],
    ).fetchone()
    return None if row is None or row[0] is None else get_hypothesis_by_id(conn, row[0])


def write_fingerprint(
    conn: duckdb.DuckDBPyConnection, hypothesis_id: int, fingerprint: str
) -> None:
    """Append the one `hypothesis_fingerprints` row of `hypothesis_id`. Refuses
    an unknown hypothesis and a second row for one hypothesis. The once-only
    rule across registrations is the registration's check (T104), not this
    write: a promotion shares its variant's fingerprint (req 4)."""
    require_lab(conn)
    get_hypothesis_by_id(conn, hypothesis_id)
    if _exists(conn, "hypothesis_fingerprints", "hypothesis_id", hypothesis_id):
        raise LabRegistryError(f"hypothesis {hypothesis_id} already has its fingerprint")
    insert_row(
        conn,
        "hypothesis_fingerprints",
        {"hypothesis_id": hypothesis_id, "fingerprint": fingerprint},
    )


# --- family rules ------------------------------------------------------------


def family_rules(conn: duckdb.DuckDBPyConnection, family: str) -> FamilyRules | None:
    """The family's rules row, or None before its first registration."""
    require_lab(conn)
    found = _select(conn, FamilyRules, "family_rules", "family = ?", [family])
    return found[0] if found else None


def write_family_rules(
    conn: duckdb.DuckDBPyConnection,
    *,
    family: str,
    first_hypothesis_id: int,
    parent_family: str | None,
    holdout_start: date,
    holdout_end: date,
    in_sample_start: date,
    fixed_params: Mapping[str, Any],
    sr_star_seed_annual: float | None,
    settings: Settings | None = None,
) -> FamilyRules:
    """Write the family's one rules row at its first registration, copying the
    caps, `min_sharpe_variance_annual` and `axis_lattice` from `settings`, and
    return it. Refuses a family that already has its row: the rules are fixed
    once, and a later config change never reaches them."""
    require_lab(conn)
    settings = settings if settings is not None else get_settings()
    if family_rules(conn, family) is not None:
        raise LabRegistryError(f"family {family!r} already has its family rules")
    get_hypothesis_by_id(conn, first_hypothesis_id)
    fixed_json, fixed_hash = _json_sha256(fixed_params)
    lab = settings.lab
    insert_row(
        conn,
        "family_rules",
        {
            "family": family,
            "first_hypothesis_id": first_hypothesis_id,
            "parent_family": parent_family,
            "holdout_start": holdout_start,
            "holdout_end": holdout_end,
            "in_sample_start": in_sample_start,
            "fixed_params_json": fixed_json,
            "fixed_params_sha256": fixed_hash,
            "max_family_holdout_spends": lab.max_family_holdout_spends,
            "max_family_promotions": lab.max_family_promotions,
            "min_sharpe_variance_annual": lab.min_sharpe_variance_annual,
            "axis_lattice_json": canonical_params_json(lab.axis_lattice),
            "sr_star_seed_annual": sr_star_seed_annual,
            "registered_at": utc_now(),
        },
    )
    rules = family_rules(conn, family)
    assert rules is not None
    return rules


# --- sweeps ------------------------------------------------------------------


def register_sweep(
    conn: duckdb.DuckDBPyConnection,
    *,
    slug: str,
    family: str,
    title: str,
    doc_path: str,
    doc_sha256: str,
    grid: Mapping[str, list[Any]],
    n_variants: int,
    selection_statistic: str,
    expected_excess_cagr_spy_pp: float,
    expected_range_pp: tuple[float, float],
    promote_at_least: float,
    retire_below: float,
    in_sample_start: date,
    holdout_start: date,
    holdout_end: date,
    registered_by: str,
    settings: Settings | None = None,
) -> SweepRecord:
    """Append a `sweeps` row (one sweep registration) and return it, copying
    `lab.max_promotions_per_sweep`, `lab.promotion_min_dsr_excess`,
    `lab.max_failures_per_variant` and `lab.axis_lattice` from `settings`.
    The file's refusals (req 1) are the caller's (T104)."""
    require_lab(conn)
    settings = settings if settings is not None else get_settings()
    grid_json, grid_hash = _json_sha256(grid)
    lab = settings.lab
    sweep_id = _next_id(conn, "sweeps", "sweep_id")
    insert_row(
        conn,
        "sweeps",
        {
            "sweep_id": sweep_id,
            "slug": slug,
            "family": family,
            "title": title,
            "doc_path": doc_path,
            "doc_sha256": doc_sha256,
            "grid_json": grid_json,
            "grid_sha256": grid_hash,
            "n_variants": n_variants,
            "selection_statistic": selection_statistic,
            "expected_excess_cagr_spy_pp": expected_excess_cagr_spy_pp,
            "expected_range_lo_pp": expected_range_pp[0],
            "expected_range_hi_pp": expected_range_pp[1],
            "promote_at_least": promote_at_least,
            "retire_below": retire_below,
            "max_promotions": lab.max_promotions_per_sweep,
            "min_dsr_floor": lab.promotion_min_dsr_excess,
            "max_failures_per_variant": lab.max_failures_per_variant,
            "axis_lattice_json": canonical_params_json(lab.axis_lattice),
            "in_sample_start": in_sample_start,
            "holdout_start": holdout_start,
            "holdout_end": holdout_end,
            "registered_at": utc_now(),
            "registered_by": registered_by,
        },
    )
    return _select(conn, SweepRecord, "sweeps", "sweep_id = ?", [sweep_id])[0]


def write_sweep_variant(
    conn: duckdb.DuckDBPyConnection,
    *,
    sweep_id: int,
    variant_index: int,
    hypothesis_id: int,
    fingerprint: str,
    variant_params: Mapping[str, Any],
) -> None:
    """Append one `sweep_variants` row. Refuses an unknown sweep or hypothesis
    and an index outside `1..n_variants` of the sweep."""
    require_lab(conn)
    found = _select(conn, SweepRecord, "sweeps", "sweep_id = ?", [sweep_id])
    if not found:
        raise LabRegistryError(f"no sweep has id {sweep_id}")
    if not 1 <= variant_index <= found[0].n_variants:
        raise LabRegistryError(
            f"variant index {variant_index} is outside 1..{found[0].n_variants} of sweep {sweep_id}"
        )
    get_hypothesis_by_id(conn, hypothesis_id)
    insert_row(
        conn,
        "sweep_variants",
        {
            "sweep_id": sweep_id,
            "variant_index": variant_index,
            "hypothesis_id": hypothesis_id,
            "fingerprint": fingerprint,
            "variant_params_json": canonical_params_json(variant_params),
        },
    )


def sweep_by_slug(conn: duckdb.DuckDBPyConnection, slug: str) -> SweepRecord | None:
    """The latest registration (highest `sweep_id`) of `slug`, or None."""
    require_lab(conn)
    found = _select(
        conn,
        SweepRecord,
        "sweeps",
        "sweep_id = (SELECT MAX(sweep_id) FROM sweeps WHERE slug = ?)",
        [slug],
    )
    return found[0] if found else None


def sweep_variants(conn: duckdb.DuckDBPyConnection, sweep_id: int) -> list[SweepVariant]:
    """The variants of one sweep registration, in canonical (index) order."""
    require_lab(conn)
    return _select(
        conn, SweepVariant, "sweep_variants", "sweep_id = ? ORDER BY variant_index", [sweep_id]
    )


# --- sweep runs ----------------------------------------------------------------


def open_sweep_run(
    conn: duckdb.DuckDBPyConnection,
    *,
    sweep_id: int,
    time_budget_minutes: int,
    n_declared: int,
    n_planned: int,
    code_tree_sha256: str,
    run_by: str,
    note: str | None = None,
    repo_dir: Path | None = None,
) -> int:
    """Insert the `sweep_runs` row of a run starting now and return its id; the
    end-of-run columns stay NULL until `close_sweep_run`. Captures
    `code_version` and `code_dirty` as `open_trial` does. The only writer of
    new `sweep_runs` rows (AST check in the tests)."""
    require_lab(conn)
    if not _exists(conn, "sweeps", "sweep_id", sweep_id):
        raise LabRegistryError(f"no sweep has id {sweep_id}")
    version, dirty = code_version(repo_dir)
    sweep_run_id = _next_id(conn, "sweep_runs", "sweep_run_id")
    insert_row(
        conn,
        "sweep_runs",
        {
            "sweep_run_id": sweep_run_id,
            "sweep_id": sweep_id,
            "started_at": utc_now(),
            "time_budget_minutes": time_budget_minutes,
            "n_declared": n_declared,
            "n_planned": n_planned,
            "code_tree_sha256": code_tree_sha256,
            "code_version": version,
            "code_dirty": dirty,
            "run_by": run_by,
            "note": note,
        },
    )
    return sweep_run_id


def _require_open_run(conn: duckdb.DuckDBPyConnection, sweep_run_id: int) -> None:
    row = conn.execute(
        "SELECT finished_at FROM sweep_runs WHERE sweep_run_id = ?", [sweep_run_id]
    ).fetchone()
    if row is None:
        raise LabRegistryError(f"no sweep run has id {sweep_run_id}")
    if row[0] is not None:
        raise LabRegistryError(f"sweep run {sweep_run_id} is already closed")


def close_sweep_run(
    conn: duckdb.DuckDBPyConnection,
    sweep_run_id: int,
    *,
    n_ok: int,
    n_failed: int,
    n_terminal_failed: int,
    seconds: float,
    n_trials_at_end: int,
    sr_star_annual_at_end: float,
    completed: bool,
) -> None:
    """Fill the end-of-run columns of an open `sweep_runs` row, once.
    `n_trials_at_end` and `sr_star_annual_at_end` are the caller's (module
    docstring, SR\\* values). Refuses an unknown or already closed run."""
    require_lab(conn)
    _require_open_run(conn, sweep_run_id)
    conn.execute(
        "UPDATE sweep_runs SET finished_at = ?, n_ok = ?, n_failed = ?, "
        "n_terminal_failed = ?, seconds = ?, n_trials_at_end = ?, "
        "sr_star_annual_at_end = ?, completed = ? "
        "WHERE sweep_run_id = ? AND finished_at IS NULL",
        [
            utc_now(),
            n_ok,
            n_failed,
            n_terminal_failed,
            seconds,
            n_trials_at_end,
            sr_star_annual_at_end,
            completed,
            sweep_run_id,
        ],
    )


def write_sweep_trial(
    conn: duckdb.DuckDBPyConnection,
    *,
    sweep_run_id: int,
    trial_id: int,
    read_group_index: int,
    seconds: float,
) -> None:
    """Append the `sweep_trials` row of a trial an open sweep run opened.
    Refuses an unknown or closed run and an unknown trial."""
    require_lab(conn)
    _require_open_run(conn, sweep_run_id)
    if not _exists(conn, "trials", "trial_id", trial_id):
        raise LabRegistryError(f"no trial has id {trial_id}")
    insert_row(
        conn,
        "sweep_trials",
        {
            "sweep_run_id": sweep_run_id,
            "trial_id": trial_id,
            "read_group_index": read_group_index,
            "seconds": seconds,
        },
    )


# --- registration-time reads ---------------------------------------------------


def first_session(conn: duckdb.DuckDBPyConnection) -> date | None:
    """`MIN(session)` of the bars table (`prices_daily`), the earliest session a
    formation anchor may reach (req 1(d)); None on a store with no bars."""
    require_lab(conn)
    (session,) = conn.execute("SELECT MIN(session) FROM prices_daily").fetchone()  # type: ignore[misc]
    return session if isinstance(session, date) else None


def family_sr_star_high_water_mark(
    conn: duckdb.DuckDBPyConnection,
    family: str,
    *,
    n_trials_today: int,
    sharpe_variance_annual_today: float | None,
) -> float:
    """The family's SR\\*_annual high-water mark (spec, Definitions): the maximum
    of every `sweep_runs.sr_star_annual_at_end` of every sweep in the family,
    the parent's mark seeded into the rules (`sr_star_seed_annual`), and today's
    value, `expected_max_sharpe(n_trials_today, V)` with V floored at the rules'
    `min_sharpe_variance_annual` (a None V, fewer than two pairs, is the floor).
    Today's value is 0 with no counted trial. Refuses a family with no rules
    and a non-finite V."""
    require_lab(conn)
    rules = family_rules(conn, family)
    if rules is None:
        raise LabRegistryError(f"family {family!r} has no family rules")
    if sharpe_variance_annual_today is not None and not math.isfinite(sharpe_variance_annual_today):
        raise LabRegistryError(f"today's annual V is not finite: {sharpe_variance_annual_today}")
    variance = max(sharpe_variance_annual_today or 0.0, rules.min_sharpe_variance_annual)
    marks = [expected_max_sharpe(n_trials_today, variance) if n_trials_today >= 1 else 0.0]
    if rules.sr_star_seed_annual is not None:
        marks.append(rules.sr_star_seed_annual)
    (runs,) = conn.execute(  # type: ignore[misc]
        "SELECT MAX(r.sr_star_annual_at_end) FROM sweep_runs r JOIN sweeps s USING (sweep_id) "
        "WHERE s.family = ?",
        [family],
    ).fetchone()
    if runs is not None:
        marks.append(float(runs))
    return max(marks)


def family_holdout_spent_or_capped(conn: duckdb.DuckDBPyConnection, family: str) -> bool:
    """Whether `family` has at least one holdout spend (`registry.family_holdout_spends`,
    any outcome) or has reached its rules' `max_family_holdout_spends`: the parent
    condition a child family's first registration needs (req 1)."""
    require_lab(conn)
    spends = len(family_holdout_spends(conn, family))
    rules = family_rules(conn, family)
    return spends >= 1 or (rules is not None and spends >= rules.max_family_holdout_spends)


def family_declared_count(conn: duckdb.DuckDBPyConnection, family: str) -> int:
    """The declared count n (spec, Definitions): distinct fingerprints declared
    by every sweep registration in `family`, run or not."""
    require_lab(conn)
    (count,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(DISTINCT v.fingerprint) FROM sweep_variants v "
        "JOIN sweeps s USING (sweep_id) WHERE s.family = ?",
        [family],
    ).fetchone()
    return int(count)


def family_ready_for_sweep(
    conn: duckdb.DuckDBPyConnection, family: str, boundary: date | Unread | None = UNREAD
) -> bool:
    """Req 1(c): false while any standalone hypothesis of `family` (one that is
    no sweep's variant: H1, a promoted file), read as the latest registration
    of each slug (the one `backtest <slug>` runs; an older row of a re-registered
    slug never gets a trial), has no `ok`, non-synthetic `in_sample` trial over
    its default window (`holdout.default_in_sample_window` at its frozen
    cadence, under the development `boundary`, ADR 0016 point 2). True for a
    family with no standalone hypothesis. `boundary` is read from the store
    (`registry.development_boundary`) unless the caller already read it."""
    require_lab(conn)
    if isinstance(boundary, Unread):
        boundary = boundary_date(conn)
    standalone = conn.execute(
        "SELECT MAX(hypothesis_id) FROM hypotheses WHERE family = ? AND hypothesis_id NOT IN "
        "(SELECT hypothesis_id FROM sweep_variants) GROUP BY slug ORDER BY 1",
        [family],
    ).fetchall()
    for (hypothesis_id,) in standalone:
        record = get_hypothesis_by_id(conn, hypothesis_id)
        cadence = frozen_values(record)[CADENCE_KEY]
        window = default_in_sample_window(Frozen.from_hypothesis(record), cadence, boundary)
        found = conn.execute(
            "SELECT 1 FROM trials t JOIN trial_results r USING (trial_id) "
            "WHERE t.hypothesis_id = ? AND t.kind = 'in_sample' AND NOT t.synthetic "
            "AND r.status = 'ok' AND t.start_session = ? AND t.end_session = ?",
            [hypothesis_id, window.start, window.end],
        ).fetchone()
        if found is None:
            return False
    return True


def is_pre_lab(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> bool:
    """Whether `hypothesis_id` has a `pre_lab_hypotheses` row: the marker, and
    only the marker (spec, Definitions), never its params or registration date."""
    require_lab(conn)
    return _exists(conn, "pre_lab_hypotheses", "hypothesis_id", hypothesis_id)


def promotion_for(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> PromotionDecision | None:
    """The earliest `promotion` decision naming `hypothesis_id` as the promoted
    hypothesis (the row's `hypothesis_id`, req 4), or None."""
    require_lab(conn)
    found = _select(
        conn,
        PromotionDecision,
        "owner_decisions",
        "kind = 'promotion' AND hypothesis_id = ? ORDER BY decision_id LIMIT 1",
        [hypothesis_id],
    )
    return found[0] if found else None


def operations_book_for(
    conn: duckdb.DuckDBPyConnection, hypothesis_id: int
) -> OperationsBookDecision | None:
    """The earliest `operations_book` decision naming `hypothesis_id` as the
    registered operations file (the row's `hypothesis_id`), or None. Never a
    promotion: `promotion_for` stays None for it."""
    require_lab(conn)
    found = _select(
        conn,
        OperationsBookDecision,
        "owner_decisions",
        "kind = 'operations_book' AND hypothesis_id = ? ORDER BY decision_id LIMIT 1",
        [hypothesis_id],
    )
    return found[0] if found else None


def grandfathered_fingerprints(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[int, ...]]:
    """Fingerprints held by two or more pre-lab registrations (spec req 13: a
    Phase 3 prose-only re-registration), each with its hypothesis ids in
    order. A promotion sharing its variant's fingerprint is not pre-lab and is
    not listed."""
    require_lab(conn)
    rows = conn.execute(
        "SELECT f.fingerprint, LIST(f.hypothesis_id ORDER BY f.hypothesis_id) "
        "FROM hypothesis_fingerprints f JOIN pre_lab_hypotheses p USING (hypothesis_id) "
        "GROUP BY f.fingerprint HAVING COUNT(*) >= 2 ORDER BY MIN(f.hypothesis_id)"
    ).fetchall()
    return {fingerprint: tuple(ids) for fingerprint, ids in rows}


def _rule_differences(record: HypothesisRecord, rules: FamilyRules) -> tuple[str, ...]:
    differences = [
        column
        for column in ("holdout_start", "holdout_end", "in_sample_start")
        if getattr(record, column) != getattr(rules, column)
    ]
    values = frozen_values(record)
    for key, rule in sorted(rules.fixed_params.items()):
        value = values.get(key)
        if key == BASE_COST_KEY and isinstance(value, int | float) and value >= rule:
            continue
        if key not in values or not is_default(value, rule):
            differences.append(key)
    # Rules written before a table key landed lack it: they are read at its default too,
    # as `frozen_values` reads the registration (#1199's `gap.stale_listing_sessions`).
    defaults = {key: default for key, default, _version in FROZEN_KEY_DEFAULTS}
    differences += sorted(
        key
        for key in values
        if key not in rules.fixed_params
        and key.startswith(FORBIDDEN_AXIS_PREFIXES)
        and not (key in defaults and is_default(values[key], defaults[key]))
    )
    return tuple(differences)


def grandfathered_members(conn: duckdb.DuckDBPyConnection) -> list[GrandfatheredMember]:
    """Pre-lab registrations whose window or frozen values differ from their
    family's rules (spec req 13; `costs.per_side_bps` higher is no difference;
    a `FORBIDDEN_AXIS_PREFIXES` key the rules lack is one), in id order. A
    family without rules lists nothing."""
    require_lab(conn)
    rows = conn.execute(
        "SELECT p.hypothesis_id FROM pre_lab_hypotheses p "
        "JOIN hypotheses h USING (hypothesis_id) "
        "JOIN family_rules r ON r.family = h.family ORDER BY p.hypothesis_id"
    ).fetchall()
    members: list[GrandfatheredMember] = []
    for (hypothesis_id,) in rows:
        record = get_hypothesis_by_id(conn, hypothesis_id)
        rules = family_rules(conn, record.family)
        assert rules is not None
        differences = _rule_differences(record, rules)
        if differences:
            members.append(
                GrandfatheredMember(hypothesis_id, record.family, record.slug, differences)
            )
    return members


def registry_size_bytes(conn: duckdb.DuckDBPyConnection) -> int:
    """The store's size on disk: its database file plus its write-ahead log,
    for `lab.registry_size_*` (req 13); an in-memory store's allocated blocks."""
    require_lab(conn)
    (path,) = conn.execute(  # type: ignore[misc]
        "SELECT path FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchone()
    if not path:
        (size,) = conn.execute(  # type: ignore[misc]
            "SELECT total_blocks * block_size FROM pragma_database_size() "
            "WHERE database_name = current_database()"
        ).fetchone()
        return int(size or 0)
    wal = f"{path}.wal"
    return os.path.getsize(path) + (os.path.getsize(wal) if os.path.exists(wal) else 0)
