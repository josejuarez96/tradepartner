"""Promotion and retirement of a sweep (strategy-lab spec req 4; plan task T109).

`promote(conn, slug, file, reason, settings)` turns a complete sweep's argmax into a
standalone hypothesis whose holdout the owner may spend once under the Phase 3 rules.
It reads the sweep's latest registration through `sweep_report.sweep_report` (the
variant states, the argmax with ties by canonical index, and the argmax's
`dsr_excess` recomputed against the family's SR\\* high-water mark) and refuses, in
this order, raising `PromotionRefused` before anything is written:

1. a sweep that is not complete (every variant counted or terminal-failed), or one
   with no counted variant, so no argmax;
2. a counted trial whose `code_dirty` is not false;
3. a `sweep_retired` decision on any registration of the slug (a re-registered file
   is no road around a retirement);
4. an argmax that meets `retire_below` (its selection statistic below it);
5. the sweep row's copied `max_promotions` promotions already made from this
   registration, or the family rules' `max_family_promotions` already made in the
   family (both copied at registration, so a later config change loosens neither);
6. a file whose family, `in_sample_start` or canonical frozen set
   (`frozen.canonical_frozen_set`, default-valued table keys left out) differs from the
   argmax variant's, so no key inside or outside the fingerprint can differ from what
   won, and a `FROZEN_KEY_DEFAULTS` entry the file writes out at its default changes
   nothing;
7. a file without a `## Sweep provenance` section whose `**Sweep:**` line names this
   sweep's slug and registration id and whose `**Variant:**` line names the argmax
   variant's slug (`docs/templates/hypothesis.md`);
8. a promotion identity (the fingerprint without `costs.per_side_bps`) that any
   earlier `promotion` decision's hypothesis already holds, from this or any sweep, at
   any cost base;
9. an argmax whose `dsr_excess` at the family's SR\\* high-water mark is below the
   sweep row's `promote_at_least`.

Then it registers the file with `hypothesis.register(..., promotion_of=<variant id>)`
(which adds the structural checks: the variant's fingerprint, the family rules, the
anchor), writes the promoted hypothesis's `hypothesis_fingerprints` row pointing at the
variant's fingerprint, and appends the `promotion` decision through
`registry.record_decision`, naming the promoted hypothesis, with the values req 4 lists
(`sweep_id` first among them, the key the trials page finds a sweep's decisions by).
**`promote` is the only caller that passes `promotion_of`**, and it passes it only
after every refusal above; that is where the "at most once per promotion identity" and
the caps live, not in `register` (T104c's open question, #1242). Every refusal comes
before the first write, and the three writes go into the caller's one transaction
(`store.db.open_for_write`), so a decision never exists without its registration or
the other way round.

`retire(conn, slug, reason)` appends a `sweep_retired` decision naming the slug's
latest registration as `sweep_id`. Neither function writes a trial, so neither
changes the family's N (`results.family_n`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

import duckdb

from tradepartner.backtest import frozen, hypothesis, results, sweep_report
from tradepartner.backtest.sweep_report import SweepReport, VariantRow
from tradepartner.config import Settings
from tradepartner.store import lab_registry, registry
from tradepartner.store.lab_schema import require_lab
from tradepartner.store.registry import BASE_COST_KEY, HypothesisRecord

__all__ = [
    "PROVENANCE_HEADING",
    "PromotionOutcome",
    "PromotionRefused",
    "promote",
    "promotion_identity",
    "retire",
]

#: The section a promoted file must carry (`docs/templates/hypothesis.md`).
PROVENANCE_HEADING: Final = "## Sweep provenance"

_SWEEP_LINE: Final = re.compile(r"\*\*Sweep:\*\*\s*`([^`]+)`,\s*registration id\s+(\d+)")
_VARIANT_LINE: Final = re.compile(r"\*\*Variant:\*\*\s*`([^`]+)`")

#: `registry.record_decision` with its `kind` open to the lab's two decision kinds
#: (`lab_schema.LAB_DECISION_KINDS`): its `Literal` annotation predates them, and the
#: store's `CHECK` on `owner_decisions.kind` accepts them on a lab store.
_record_decision: Callable[..., int] = registry.record_decision


class PromotionRefused(lab_registry.LabRegistryError):
    """`sweep promote` or `sweep retire` refused under req 4; nothing was written."""


@dataclass(frozen=True)
class PromotionOutcome:
    """A promotion: the promoted hypothesis, the `promotion` decision's id, the
    variant it promotes, and the values stored on the decision."""

    promoted: HypothesisRecord
    decision_id: int
    sweep_id: int
    variant: HypothesisRecord
    values: dict[str, Any]


def promotion_identity(family: str, params: Mapping[str, Any], in_sample_start: date) -> str:
    """The fingerprint computed without `costs.per_side_bps` (spec, Definitions,
    Promotion): one identity for a strategy at every cost base."""
    without_cost = {k: v for k, v in params.items() if k != BASE_COST_KEY}
    return frozen.fingerprint(family, without_cost, in_sample_start)


def _record_identity(record: HypothesisRecord) -> str:
    return promotion_identity(record.family, frozen.frozen_values(record), record.in_sample_start)


def _promotions(conn: duckdb.DuckDBPyConnection) -> list[tuple[int | None, int | None, str | None]]:
    """Every `promotion` decision as (promoted hypothesis id, values' `sweep_id`,
    promoted hypothesis's family)."""
    rows = conn.execute(
        "SELECT d.hypothesis_id, "
        "TRY_CAST(json_extract_string(d.values_json, '$.sweep_id') AS BIGINT), h.family "
        "FROM owner_decisions d LEFT JOIN hypotheses h USING (hypothesis_id) "
        "WHERE d.kind = 'promotion' ORDER BY d.decision_id"
    ).fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


def _retired(conn: duckdb.DuckDBPyConnection, slug: str) -> bool:
    """Whether any registration of `slug` has a `sweep_retired` decision."""
    row = conn.execute(
        "SELECT 1 FROM owner_decisions d JOIN sweeps s ON s.sweep_id = "
        "TRY_CAST(json_extract_string(d.values_json, '$.sweep_id') AS BIGINT) "
        "WHERE d.kind = 'sweep_retired' AND s.slug = ? LIMIT 1",
        [slug],
    ).fetchone()
    return row is not None


def _dirty_counted(conn: duckdb.DuckDBPyConnection, report: SweepReport) -> list[str]:
    """Slugs of counted variants whose counted trial is not `code_dirty = false`."""
    counted = {r.trial_id: r.slug for r in report.rows if r.status == "counted" and r.trial_id}
    if not counted:
        return []
    rows = conn.execute(
        "SELECT trial_id FROM trials WHERE list_contains($ids::BIGINT[], trial_id) "
        "AND code_dirty IS DISTINCT FROM FALSE ORDER BY trial_id",
        {"ids": sorted(counted)},
    ).fetchall()
    return [counted[trial_id] for (trial_id,) in rows]


def _provenance_refusal(text: str, report: SweepReport, argmax: VariantRow) -> str | None:
    """Refusal 7 (module docstring), or None."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == PROVENANCE_HEADING)
    except StopIteration:
        return f"it has no {PROVENANCE_HEADING!r} section"
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    section = "\n".join(body)
    sweep = _SWEEP_LINE.search(section)
    if sweep is None or (sweep.group(1), int(sweep.group(2))) != (report.slug, report.sweep_id):
        named = "nothing" if sweep is None else f"{sweep.group(1)!r} r{sweep.group(2)}"
        return (
            f"its {PROVENANCE_HEADING!r} section names {named} as the sweep, not "
            f"`{report.slug}`, registration id {report.sweep_id}"
        )
    variant = _VARIANT_LINE.search(section)
    if variant is None or variant.group(1) != argmax.slug:
        named = "nothing" if variant is None else repr(variant.group(1))
        return (
            f"its {PROVENANCE_HEADING!r} section names {named} as the variant, not the "
            f"argmax `{argmax.slug}`"
        )
    return None


def _frozen_set_differences(
    parsed: hypothesis.HypothesisFile, params: dict[str, Any], variant: HypothesisRecord
) -> list[str]:
    """Refusal 6: what differs between the file and the argmax variant."""
    differences: list[str] = []
    if parsed.family != variant.family:
        differences.append(f"family ({parsed.family} vs {variant.family})")
    if parsed.in_sample_start != variant.in_sample_start:
        differences.append(
            f"in_sample_start ({parsed.in_sample_start} vs {variant.in_sample_start})"
        )
    if differences:
        return differences
    mine = frozen.canonical_frozen_set(params, parsed.family)
    theirs = frozen.canonical_frozen_set(frozen.frozen_values(variant), variant.family)
    return sorted(
        f"{key} ({mine.get(key)!r} vs {theirs.get(key)!r})"
        for key in set(mine) | set(theirs)
        if key not in mine or key not in theirs or not frozen.is_default(mine[key], theirs[key])
    )


def _statistic(row: VariantRow, statistic: str) -> float:
    value = getattr(row, statistic)
    if not isinstance(value, float):
        raise PromotionRefused(f"argmax {row.slug} has no {statistic}")
    return value


def promote(
    conn: duckdb.DuckDBPyConnection,
    slug: str,
    file: Path,
    reason: str,
    settings: Settings,
    *,
    registered_by: str = "owner",
    code_vintage: str | None = None,
) -> PromotionOutcome:
    """`sweep promote <slug> --file <file> --reason <reason>` (module docstring).

    Raises `PromotionRefused` for each refusal of req 4, `LabNotInitialised` on a store
    without the lab tables, `ValueError` for an unknown slug or a blank reason, and
    whatever `hypothesis.register` raises for the file. `code_vintage` is the
    checkout's `registry.code_tree_sha256()` unless given (a test passes one). The
    transaction is the caller's.
    """
    require_lab(conn)
    if not reason.strip():
        raise ValueError("a promotion needs a reason")
    report = sweep_report.sweep_report(conn, slug, code_vintage=code_vintage)
    if not report.complete:
        raise PromotionRefused(
            f"sweep {slug!r} (r{report.sweep_id}) is {report.state}: "
            f"{report.n_counted} of {report.n_declared} variants counted; "
            "run it to completion first"
        )
    verdicts = report.verdicts
    if verdicts is None:
        raise PromotionRefused(f"sweep {slug!r} (r{report.sweep_id}) has no counted variant")
    dirty = _dirty_counted(conn, report)
    if dirty:
        raise PromotionRefused(
            f"sweep {slug!r}: counted trials with code_dirty = true ({', '.join(dirty)}); "
            "rerun them from a clean checkout"
        )
    if _retired(conn, slug):
        raise PromotionRefused(f"sweep {slug!r} is retired (`sweep retire`); it promotes nothing")
    argmax = verdicts.argmax
    statistic = report.selection_statistic
    value = _statistic(argmax, statistic)
    if verdicts.retire_below_met:
        raise PromotionRefused(
            f"sweep {slug!r}: the argmax {argmax.slug} has {statistic} {value:.4f}, below "
            f"retire_below {report.retire_below}: the pre-registered retirement condition "
            "holds, so it promotes nothing"
        )
    sweep = lab_registry.sweep_by_slug(conn, slug)
    rules = lab_registry.family_rules(conn, report.family)
    if sweep is None or rules is None:
        raise PromotionRefused(f"sweep {slug!r} or family {report.family!r} has no registry row")
    promotions = _promotions(conn)
    from_sweep = sum(1 for _, sweep_id, _ in promotions if sweep_id == sweep.sweep_id)
    if from_sweep >= sweep.max_promotions:
        raise PromotionRefused(
            f"sweep {slug!r} (r{sweep.sweep_id}) already has {from_sweep} promotion(s), its "
            f"max_promotions of {sweep.max_promotions}"
        )
    in_family = sum(1 for _, _, family in promotions if family == report.family)
    if in_family >= rules.max_family_promotions:
        raise PromotionRefused(
            f"family {report.family!r} already has {in_family} promotion(s), its rules' "
            f"max_family_promotions of {rules.max_family_promotions}"
        )
    variant = registry.get_hypothesis(conn, argmax.slug)
    parsed = hypothesis.parse_file(file)
    params = hypothesis.frozen_params(parsed, settings)
    differences = _frozen_set_differences(parsed, params, variant)
    if differences:
        raise PromotionRefused(
            f"{file}: its frozen set differs from the argmax {argmax.slug}'s "
            f"({'; '.join(differences)}); only the argmax is promoted, and a different "
            "rule is a new sweep"
        )
    refusal = _provenance_refusal(file.read_text(), report, argmax)
    if refusal is not None:
        raise PromotionRefused(f"{file}: {refusal}")
    identity = _record_identity(variant)
    for promoted_id, _, _ in promotions:
        if promoted_id is None:
            continue
        earlier = registry.get_hypothesis_by_id(conn, promoted_id)
        if _record_identity(earlier) == identity:
            raise PromotionRefused(
                f"the argmax {argmax.slug} has the promotion identity of {earlier.slug} "
                f"(hypothesis {promoted_id}), already promoted; a strategy is promoted once, "
                "at any cost base"
            )
    if not verdicts.promote_at_least_met:
        raise PromotionRefused(
            f"sweep {slug!r}: the argmax {argmax.slug} has dsr_excess "
            f"{verdicts.dsr_excess_at_high_water:.4f} at the family's SR* high-water mark "
            f"{verdicts.sr_star_high_water_annual:.4f}, below promote_at_least "
            f"{report.promote_at_least}"
        )
    fingerprint = _variant_fingerprint(conn, variant.hypothesis_id)
    promoted = hypothesis.register(
        conn,
        file,
        registered_by=registered_by,
        settings=settings,
        promotion_of=variant.hypothesis_id,
    )
    lab_registry.write_fingerprint(conn, promoted.hypothesis_id, fingerprint)
    values: dict[str, Any] = {
        "sweep_id": report.sweep_id,
        "sweep_slug": report.slug,
        "variant_hypothesis_id": variant.hypothesis_id,
        "variant_slug": variant.slug,
        "promoted_hypothesis_id": promoted.hypothesis_id,
        "declared_count": report.declared_count,
        "family_n": results.family_n(conn, report.family),
        "sr_star_high_water_annual": verdicts.sr_star_high_water_annual,
        "sr_star_today_annual": report.sr_star_annual,
        "selection_statistic": statistic,
        "statistic_at_promotion": value,
        "dsr_excess_at_high_water": verdicts.dsr_excess_at_high_water,
    }
    decision_id = _record_decision(
        conn,
        kind="promotion",
        reason=reason,
        values=values,
        hypothesis_id=promoted.hypothesis_id,
    )
    return PromotionOutcome(promoted, decision_id, report.sweep_id, variant, values)


def _variant_fingerprint(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> str:
    row = conn.execute(
        "SELECT fingerprint FROM sweep_variants WHERE hypothesis_id = ?", [hypothesis_id]
    ).fetchone()
    if row is None:
        raise PromotionRefused(f"hypothesis {hypothesis_id} is not a sweep variant")
    return str(row[0])


def retire(conn: duckdb.DuckDBPyConnection, slug: str, reason: str) -> int:
    """`sweep retire <slug> --reason <reason>`: append a `sweep_retired` decision whose
    values name the slug's latest registration as `sweep_id`, and return its id.
    Refuses an unknown slug (`ValueError`), a blank reason (`ValueError`) and a sweep
    already retired (`PromotionRefused`). Writes no trial, so N is unchanged."""
    require_lab(conn)
    sweep = lab_registry.sweep_by_slug(conn, slug)
    if sweep is None:
        raise ValueError(f"no sweep is registered as {slug!r}")
    if _retired(conn, slug):
        raise PromotionRefused(f"sweep {slug!r} is already retired")
    return _record_decision(
        conn,
        kind="sweep_retired",
        reason=reason,
        values={"sweep_id": sweep.sweep_id, "sweep_slug": slug},
    )
