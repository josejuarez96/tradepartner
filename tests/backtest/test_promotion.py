"""Tests for promotion and retirement (strategy-lab plan T109, `backtest/promotion.py`;
spec req 4 and the "Promotion" acceptance criterion, the five V-shrink cases included).

Every case builds a family on the `lab_store` fixture the way the lab leaves it: H1's
fixture twin registered, marked pre-lab, fingerprinted and with the family rules
written from it; sweeps registered through `lab_registry` with one real hypothesis
file per variant (the momentum fixture at another `top_fraction`), so fingerprints and
canonical frozen sets are the real ones. Trials, results and base-level metrics are
inserted as rows so each variant's state and statistic are set exactly; the checkout's
code vintage is passed in as `CODE`. `sweep_runs` rows stand in for the runner's (T107)
end-of-run SR\\* marks. The last test runs the promoted slug through the real backtest
path on a fixture store file.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import mark_pre_lab

from tradepartner.backtest import frozen, hypothesis, promotion, results, sweep_report
from tradepartner.backtest.holdout import Flags, Frozen, Reasons, default_in_sample_window
from tradepartner.backtest.hypothesis import LabRegistrationError
from tradepartner.backtest.metrics import expected_max_sharpe
from tradepartner.backtest.promotion import PromotionRefused
from tradepartner.backtest.run import run_hypothesis
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings
from tradepartner.store import lab_registry, lab_schema, registry
from tradepartner.store.db import configure_connection, insert_row, utc_now
from tradepartner.store.lab_schema import LabNotInitialised

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"
IN_SAMPLE_START = date(2018, 1, 31)
HOLDOUT = (date(2019, 6, 3), date(2020, 6, 30))
#: A spend window inside the holdout (as `tests/backtest/test_run.py`'s).
HOLDOUT_WINDOW = (date(2019, 5, 31), date(2019, 8, 30))
CUTOFF = datetime(2019, 5, 31, 20, 0, tzinfo=UTC)
CODE = "c" * 64
TWIN = "h1"
GRID = "mom-grid"
PROMOTED = "h-promoted"
REASON = "the argmax, as pre-registered"


# --- the family --------------------------------------------------------------------


def _text(slug: str, top_fraction: float, cost: float) -> str:
    """The momentum fixture at the module's window, `top_fraction` and `cost`."""
    text = FIXTURE.read_text()
    for old, new in (
        ("in_sample_start = 2017-01-31", f"in_sample_start = {IN_SAMPLE_START}"),
        ("start = 2023-01-03", f"start = {HOLDOUT[0]}"),
        ("end = 2025-12-31", f"end = {HOLDOUT[1]}"),
        ("top_fraction = 0.10", f"top_fraction = {top_fraction}"),
        ("per_side_bps = 15.0", f"per_side_bps = {cost}"),
        ('slug = "fixture-momentum"', f'slug = "{slug}"'),
    ):
        assert old in text, old
        text = text.replace(old, new, 1)
    return text


def _metrics(sharpe_annual_excess: float, *, n_periods: float = 40.0) -> dict[str, float]:
    return {
        "excess_cagr_spy": sharpe_annual_excess / 20,
        "sharpe_annual_excess_spy": sharpe_annual_excess,
        "sharpe_period_excess_spy": sharpe_annual_excess / math.sqrt(12),
        "skew_period_excess_spy": -0.1,
        "kurtosis_period_excess_spy": 3.5,
        "sharpe_period": sharpe_annual_excess / math.sqrt(12),
        "cost_drag": 0.004,
        "turnover_annual": 5.5,
        "max_drawdown": -0.21,
        "n_periods": n_periods,
        "periods_per_year": 12.0,
    }


class Lab:
    """A lab family on `conn`: files under `root`, rows by hand."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, settings: Settings, root: Path) -> None:
        self.conn = conn
        self.settings = settings
        self.root = root
        self.vintage = registry.data_vintage(conn, CUTOFF)

    def file(self, slug: str, top_fraction: float = 0.5, cost: float = 15.0) -> Path:
        path = self.root / f"{slug}.md"
        path.write_text(_text(slug, top_fraction, cost))
        return path

    def direct(
        self, path: Path, *, slug: str | None = None, drop: tuple[str, ...] = ()
    ) -> registry.HypothesisRecord:
        """`path` registered straight through the registry (a pre-lab twin, or a
        variant as `sweep.register` writes it); `drop` leaves keys out of the stored
        params, as a registration from before those keys landed."""
        parsed = hypothesis.parse_file(path)
        params = hypothesis.frozen_params(parsed, self.settings)
        return registry.register_hypothesis(
            self.conn,
            slug=slug or parsed.slug,
            family=parsed.family,
            title=parsed.title,
            doc_path=path.as_posix(),
            doc_sha256=parsed.doc_sha256,
            params={k: v for k, v in params.items() if k not in drop},
            in_sample_start=parsed.in_sample_start,
            holdout_start=parsed.holdout_start,
            holdout_end=parsed.holdout_end,
            registered_by="test",
            settings=self.settings,
        )

    def twin(self, rules: Settings | None = None) -> registry.HypothesisRecord:
        """H1's twin as the lab migration leaves it, with the family rules."""
        record = self.direct(self.file(TWIN))
        mark_pre_lab(self.conn, record.hypothesis_id)
        lab_registry.write_fingerprint(self.conn, record.hypothesis_id, _fingerprint(record))
        lab_registry.write_family_rules(
            self.conn,
            family="momentum",
            first_hypothesis_id=record.hypothesis_id,
            parent_family=None,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            in_sample_start=IN_SAMPLE_START,
            fixed_params={
                k: v
                for k, v in frozen.frozen_values(record).items()
                if k.startswith(FORBIDDEN_AXIS_PREFIXES)
            },
            sr_star_seed_annual=None,
            settings=rules or self.settings,
        )
        return record

    def sweep(
        self,
        top_fractions: list[float],
        *,
        slug: str = GRID,
        cost: float = 15.0,
        settings: Settings | None = None,
        drop: tuple[str, ...] = (),
    ) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
        sweep = lab_registry.register_sweep(
            self.conn,
            slug=slug,
            family="momentum",
            title="top fraction grid",
            doc_path=f"docs/sweeps/{slug}.md",
            doc_sha256="s" * 64,
            grid={"strategy.top_fraction": top_fractions},
            n_variants=len(top_fractions),
            selection_statistic="sharpe_annual_excess_spy",
            expected_excess_cagr_spy_pp=0.0,
            expected_range_pp=(-2.0, 2.0),
            promote_at_least=0.5,
            retire_below=0.0,
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="owner",
            settings=settings or self.settings,
        )
        records = []
        for index, top_fraction in enumerate(top_fractions, start=1):
            variant_slug = f"{slug}--r{sweep.sweep_id}-v{index}"
            path = self.file(f"{variant_slug}-src", top_fraction, cost)
            record = self.direct(path, slug=variant_slug, drop=drop)
            fingerprint = _fingerprint(record)
            lab_registry.write_sweep_variant(
                self.conn,
                sweep_id=sweep.sweep_id,
                variant_index=index,
                hypothesis_id=record.hypothesis_id,
                fingerprint=fingerprint,
                variant_params={"strategy.top_fraction": top_fraction},
            )
            lab_registry.write_fingerprint(self.conn, record.hypothesis_id, fingerprint)
            records.append(record)
        return sweep, records

    def trial(
        self, record: registry.HypothesisRecord, sharpe: float, *, dirty: bool = False
    ) -> int:
        """A counted (current, `ok`, in-sample) trial of `record` with `sharpe` as its
        annual excess Sharpe at its base cost level."""
        conn = self.conn
        values = frozen.frozen_values(record)
        window = default_in_sample_window(
            Frozen.from_hypothesis(record), values["schedule.rebalance_cadence"]
        )
        (trial_id,) = conn.execute(  # type: ignore[misc]
            "SELECT COALESCE(MAX(trial_id), 0) + 1 FROM trials"
        ).fetchone()
        insert_row(
            conn,
            "trials",
            {
                "trial_id": trial_id,
                "hypothesis_id": record.hypothesis_id,
                "kind": "in_sample",
                "started_at": utc_now(),
                "start_session": window.start,
                "end_session": window.end,
                "data_cutoff": CUTOFF,
                "store_max_ingested_at": None,
                "code_version": "abc",
                "code_dirty": dirty,
                "synthetic": False,
                "holdout_repeat": False,
                "run_by": "test",
                "detail_level": "summary",
                "data_vintage": self.vintage,
                "code_tree_sha256": CODE,
            },
        )
        insert_row(
            conn,
            "trial_results",
            {
                "trial_id": trial_id,
                "finished_at": utc_now(),
                "status": "ok",
                "message": None,
                "red_flag": False,
                "dsr_excess": None,
                "sharpe_unit": "annual",
            },
        )
        for metric, value in _metrics(sharpe).items():
            insert_row(
                conn,
                "trial_metrics",
                {
                    "trial_id": trial_id,
                    "series": "strategy",
                    "cost_per_side_bps": float(values[registry.BASE_COST_KEY]),
                    "metric": metric,
                    "value": value,
                },
            )
        return int(trial_id)

    def run_mark(self, sweep_id: int, sr_star: float, *, completed: bool = True) -> None:
        """A closed `sweep_runs` row with `sr_star` as its end-of-run SR\\* (the runner's
        `n_trials_at_end` and mark, T107)."""
        run_id = lab_registry.open_sweep_run(
            self.conn,
            sweep_id=sweep_id,
            time_budget_minutes=480,
            n_declared=2,
            n_planned=2,
            code_tree_sha256=CODE,
            run_by="test",
        )
        lab_registry.close_sweep_run(
            self.conn,
            run_id,
            n_ok=2,
            n_failed=0,
            n_terminal_failed=0,
            seconds=10.0,
            n_trials_at_end=results.family_n(self.conn, "momentum"),
            sr_star_annual_at_end=sr_star,
            completed=completed,
        )

    def promoted_file(
        self,
        sweep: lab_registry.SweepRecord,
        variant: registry.HypothesisRecord,
        *,
        slug: str = PROMOTED,
        provenance: str | None = None,
        cost: float = 15.0,
        edits: tuple[tuple[str, str], ...] = (),
    ) -> Path:
        """The argmax's file as a standalone `slug` with its provenance section."""
        top = frozen.frozen_values(variant)["strategy.top_fraction"]
        text = _text(slug, top, cost)
        for old, new in edits:
            assert old in text, old
            text = text.replace(old, new, 1)
        if provenance is None:
            declared = lab_registry.family_declared_count(self.conn, "momentum")
            provenance = _provenance(sweep.slug, sweep.sweep_id, variant.slug, declared=declared)
        path = self.root / f"{slug}.md"
        path.write_text(text + provenance)
        return path

    def promote(self, path: Path, *, slug: str = GRID) -> promotion.PromotionOutcome:
        return promotion.promote(self.conn, slug, path, REASON, self.settings, code_vintage=CODE)

    def report(self, slug: str = GRID) -> sweep_report.SweepReport:
        return sweep_report.sweep_report(self.conn, slug, code_vintage=CODE)


def _provenance(
    sweep_slug: str, sweep_id: int, variant_slug: str, *, declared: int = 2, rank: int = 1
) -> str:
    return (
        "\n## Sweep provenance\n\n"
        f"- **Sweep:** `{sweep_slug}`, registration id {sweep_id}; the family's declared "
        f"count n = {declared} at promotion\n"
        f"- **Variant:** `{variant_slug}`, rank {rank} (the argmax) of the registration's 2 "
        "variants by sharpe_annual_excess_spy\n"
        "- **Base-level in-sample statistics at promotion:** placeholders\n"
    )


def _fingerprint(record: registry.HypothesisRecord) -> str:
    return frozen.fingerprint(record.family, frozen.frozen_values(record), record.in_sample_start)


_WRITTEN = ("hypotheses", "hypothesis_fingerprints", "owner_decisions", "trials")


def _counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # type: ignore[index]
        for table in _WRITTEN
    }


@pytest.fixture
def lab(lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path) -> Lab:
    """H1's twin with the family rules and one counted trial."""
    root = tmp_path / "files"
    root.mkdir()
    built = Lab(lab_store, settings, root)
    built.trial(built.twin(), 0.3)
    return built


def _won(
    lab: Lab, sharpes: tuple[float, ...] = (1.5, 0.4)
) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
    """A complete two-variant sweep whose argmax is v1 (a clear winner)."""
    sweep, variants = lab.sweep([round(0.15 + i / 10, 2) for i in range(len(sharpes))])
    for variant, sharpe in zip(variants, sharpes, strict=True):
        lab.trial(variant, sharpe)
    return sweep, variants


def _refused(lab: Lab, path: Path, match: str, *, slug: str = GRID) -> None:
    before = _counts(lab.conn)
    with pytest.raises(PromotionRefused, match=match):
        lab.promote(path, slug=slug)
    assert _counts(lab.conn) == before


# --- the promotion -----------------------------------------------------------------


def test_the_argmax_file_registers_with_its_decision_and_n_is_unchanged(lab: Lab) -> None:
    sweep, (winner, _other) = _won(lab)
    report = lab.report()
    assert report.verdicts is not None and report.verdicts.argmax.slug == winner.slug
    n_before = results.family_n(lab.conn, "momentum")

    outcome = lab.promote(lab.promoted_file(sweep, winner))

    promoted = registry.get_hypothesis(lab.conn, PROMOTED)
    assert outcome.promoted == promoted
    assert outcome.variant == winner
    assert _fingerprint(promoted) == _fingerprint(winner)
    (stored,) = lab.conn.execute(  # type: ignore[misc]
        "SELECT fingerprint FROM hypothesis_fingerprints WHERE hypothesis_id = ?",
        [promoted.hypothesis_id],
    ).fetchone()
    assert stored == _fingerprint(winner)
    decision = lab_registry.promotion_for(lab.conn, promoted.hypothesis_id)
    assert decision is not None and decision.decision_id == outcome.decision_id
    assert decision.reason == REASON
    verdicts = report.verdicts
    assert decision.values == {
        "sweep_id": sweep.sweep_id,
        "sweep_slug": GRID,
        "variant_hypothesis_id": winner.hypothesis_id,
        "variant_slug": winner.slug,
        "promoted_hypothesis_id": promoted.hypothesis_id,
        "declared_count": 2,
        "family_n": n_before,
        "sr_star_high_water_annual": pytest.approx(verdicts.sr_star_high_water_annual),
        "sr_star_today_annual": pytest.approx(report.sr_star_annual),
        "selection_statistic": "sharpe_annual_excess_spy",
        "statistic_at_promotion": pytest.approx(1.5),
        "dsr_excess_at_high_water": pytest.approx(verdicts.dsr_excess_at_high_water),
    }
    assert results.family_n(lab.conn, "momentum") == n_before
    # The trials page finds the decision by the `sweep_id` key of its stored values.
    (found,) = lab.conn.execute(  # type: ignore[misc]
        "SELECT TRY_CAST(json_extract_string(values_json, '$.sweep_id') AS BIGINT) "
        "FROM owner_decisions WHERE kind = 'promotion'"
    ).fetchone()
    assert found == sweep.sweep_id


def test_a_promoted_file_registered_outside_promote_is_still_refused(lab: Lab) -> None:
    """`hypothesis register` on the CLI path never passes `promotion_of`: the promoted
    file is a one-value-sweep refusal there (T104c), so `promote` is its only road."""
    sweep, (winner, _other) = _won(lab)
    path = lab.promoted_file(sweep, winner)
    with pytest.raises(LabRegistrationError, match="one-value sweep"):
        hypothesis.register(lab.conn, path, registered_by="owner", settings=lab.settings)


def test_refused_while_the_sweep_is_incomplete(lab: Lab) -> None:
    sweep, (winner, _unrun) = lab.sweep([0.15, 0.25])
    lab.trial(winner, 1.5)
    _refused(lab, lab.promoted_file(sweep, winner), "incomplete")


def test_refused_when_a_counted_trial_is_code_dirty(lab: Lab) -> None:
    sweep, (winner, other) = lab.sweep([0.15, 0.25])
    lab.trial(winner, 1.5)
    lab.trial(other, 0.4, dirty=True)
    _refused(lab, lab.promoted_file(sweep, winner), "code_dirty")


def test_refused_after_sweep_retire_which_leaves_n_alone(lab: Lab) -> None:
    sweep, (winner, _other) = _won(lab)
    n_before = results.family_n(lab.conn, "momentum")
    decision_id = promotion.retire(lab.conn, GRID, "the idea is dead")
    assert results.family_n(lab.conn, "momentum") == n_before
    kind, values = lab.conn.execute(
        "SELECT kind, values_json FROM owner_decisions WHERE decision_id = ?", [decision_id]
    ).fetchone()  # type: ignore[misc]
    assert kind == "sweep_retired"
    assert json.loads(values)["sweep_id"] == sweep.sweep_id
    _refused(lab, lab.promoted_file(sweep, winner), "retired")
    with pytest.raises(PromotionRefused, match="already retired"):
        promotion.retire(lab.conn, GRID, "again")


def test_retire_refuses_an_unknown_sweep_and_a_blank_reason(lab: Lab) -> None:
    with pytest.raises(ValueError, match="no sweep"):
        promotion.retire(lab.conn, "nope", "reason")
    _won(lab)
    with pytest.raises(ValueError, match="reason"):
        promotion.retire(lab.conn, GRID, "  ")


def test_refused_when_the_argmax_meets_retire_below(lab: Lab) -> None:
    sweep, variants = lab.sweep([0.15, 0.25])
    lab.trial(variants[0], -0.2)
    lab.trial(variants[1], -0.5)
    _refused(lab, lab.promoted_file(sweep, variants[0]), "retire_below")


def test_refused_with_a_changed_key_outside_the_fingerprint(lab: Lab) -> None:
    sweep, (winner, _other) = _won(lab)
    path = lab.promoted_file(
        sweep, winner, edits=(("count_share_threshold = 0.05", "count_share_threshold = 0.06"),)
    )
    parsed = hypothesis.parse_file(path)
    params = hypothesis.frozen_params(parsed, lab.settings)
    # The fingerprint is intact; only the canonical frozen set differs.
    assert frozen.fingerprint("momentum", params, parsed.in_sample_start) == _fingerprint(winner)
    _refused(lab, path, "gap.count_share_threshold")


def test_refused_for_a_non_argmax_file(lab: Lab) -> None:
    sweep, (_winner, other) = _won(lab)
    _refused(lab, lab.promoted_file(sweep, other), "strategy.top_fraction")


def test_a_frozen_key_default_added_after_the_sweep_changes_nothing(lab: Lab) -> None:
    """The variants were stored before `gap.stale_listing_sessions` (#1199) and the
    `schedule.*` keys landed; the promoted file holds both at their defaults."""
    later = ("gap.stale_listing_sessions", "schedule.rebalance_cadence", "schedule.signal_anchor")
    sweep, (winner, other) = lab.sweep([0.15, 0.25], drop=later)
    assert not set(later) & set(winner.params)
    lab.trial(winner, 1.5)
    lab.trial(other, 0.4)
    outcome = lab.promote(lab.promoted_file(sweep, winner))
    assert set(later) <= set(outcome.promoted.params)


@pytest.mark.parametrize(
    ("provenance", "match"),
    [
        ("", "no '## Sweep provenance' section"),
        (_provenance("other-grid", 1, f"{GRID}--r1-v1"), "as the sweep"),
        (_provenance(GRID, 7, f"{GRID}--r1-v1"), "as the sweep"),
        (_provenance(GRID, 1, f"{GRID}--r1-v2"), "as the variant"),
        (_provenance(GRID, 1, f"{GRID}--r1-v1", rank=2), "at rank 1"),
        (_provenance(GRID, 1, f"{GRID}--r1-v1", declared=3), "declared count n = 3"),
    ],
    ids=[
        "missing",
        "other-sweep",
        "other-registration",
        "other-variant",
        "other-rank",
        "other-declared-count",
    ],
)
def test_refused_without_a_matching_sweep_provenance_section(
    lab: Lab, provenance: str, match: str
) -> None:
    sweep, (winner, _other) = _won(lab)
    assert sweep.sweep_id == 1
    _refused(lab, lab.promoted_file(sweep, winner, provenance=provenance), match)


def test_refused_for_a_slug_already_registered(lab: Lab) -> None:
    """A slug already in the registry (here the twin's) never comes back from
    `register` as an existing record that `promote` would then mark promoted."""
    sweep, (winner, _other) = _won(lab)
    _refused(lab, lab.promoted_file(sweep, winner, slug=TWIN), "already registered")


def test_refused_below_promote_at_least_at_the_high_water_mark(lab: Lab) -> None:
    sweep, (winner, _other) = _won(lab)
    lab.run_mark(sweep.sweep_id, 5.0)
    _refused(lab, lab.promoted_file(sweep, winner), "below promote_at_least")


def test_a_second_promotion_is_refused_at_the_copied_max_promotions(lab: Lab) -> None:
    sweep, (winner, _other) = _won(lab)
    assert sweep.max_promotions == 1
    lab.promote(lab.promoted_file(sweep, winner))
    # A later config change to 2 does not loosen the registered sweep.
    lab.settings = Settings(
        _env_file=None,
        store={"path": lab.settings.store.path},
        lab={"max_promotions_per_sweep": 2},
    )
    again = lab.promoted_file(sweep, winner, slug="h-promoted-again")
    _refused(lab, again, "max_promotions of 1")


def test_a_promotion_identity_is_promoted_once_at_any_cost_base(lab: Lab) -> None:
    sweep, (winner, _other) = _won(lab)
    lab.promote(lab.promoted_file(sweep, winner))
    # The same strategy re-gridded at a higher cost base: new fingerprints, so the sweep
    # registers, but the promotion identity is the one already promoted.
    dearer, variants = lab.sweep([0.15, 0.35], slug="mom-dear", cost=20.0)
    lab.trial(variants[0], 1.6)
    lab.trial(variants[1], 0.2)
    assert _fingerprint(variants[0]) != _fingerprint(winner)
    path = lab.promoted_file(dearer, variants[0], slug="h-promoted-dear", cost=20.0)
    _refused(lab, path, "already promoted", slug="mom-dear")


def test_the_family_cap_counts_promotions_across_sweeps(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    lab = Lab(lab_store, settings, root)
    lab.trial(lab.twin(rules=Settings(_env_file=None, lab={"max_family_promotions": 1})), 0.3)
    sweep, (winner, _other) = _won(lab)
    lab.promote(lab.promoted_file(sweep, winner))
    second, variants = lab.sweep([0.35, 0.45], slug="mom-second")
    lab.trial(variants[0], 1.4)
    lab.trial(variants[1], 0.1)
    path = lab.promoted_file(second, variants[0], slug="h-promoted-second")
    _refused(lab, path, "max_family_promotions of 1", slug="mom-second")


def test_refused_on_a_store_without_the_lab(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(LabNotInitialised):
        promotion.promote(fixture_store, GRID, tmp_path / "x.md", REASON, settings)
    with pytest.raises(LabNotInitialised):
        promotion.retire(fixture_store, GRID, REASON)


# --- V shrink: five cases ----------------------------------------------------------

#: A broad, dispersed sweep's four annual excess Sharpes: a high V, so a high SR* mark.
BROAD = (3.0, -3.0, 2.5, -2.5)


def _mark_now(lab: Lab) -> float:
    """The family's SR\\* mark as the runner records it at a run's end."""
    sharpes = registry.family_sharpes(lab.conn, "momentum")
    return lab_registry.family_sr_star_high_water_mark(
        lab.conn,
        "momentum",
        n_trials_today=results.family_n(lab.conn, "momentum"),
        sharpe_variance_annual_today=sharpes.variance("excess_spy"),
    )


def _mark_today(lab: Lab) -> float:
    """Today's SR\\* alone (unfloored V), what a clustered sweep's own run records."""
    report = lab.report()
    assert report.sr_star_annual is not None
    return report.sr_star_annual


def _clustered(lab: Lab, slug: str, sharpe: float, count: int) -> lab_registry.SweepRecord:
    """A sweep of `count` near-identical variants, all counted; v`count` is the argmax."""
    sweep, variants = lab.sweep([round(0.11 + i / 100, 2) for i in range(count)], slug=slug)
    for i, variant in enumerate(variants):
        lab.trial(variant, sharpe + i / 1000)
    return sweep


def _judged_at(lab: Lab, sweep: lab_registry.SweepRecord, mark: float) -> None:
    """The clustered `sweep` lowered today's SR\\* below `mark`, yet its argmax is
    judged at `mark` and refused."""
    report = lab.report(sweep.slug)
    verdicts = report.verdicts
    assert verdicts is not None and report.sr_star_annual is not None
    assert report.sr_star_annual < mark
    assert verdicts.sr_star_high_water_annual == pytest.approx(mark)
    winner = registry.get_hypothesis(lab.conn, verdicts.argmax.slug)
    _refused(lab, lab.promoted_file(sweep, winner), "below promote_at_least", slug=sweep.slug)


def test_v_shrink_a_refusal_at_completion_survives_a_later_clustered_sweep(lab: Lab) -> None:
    sweep, variants = lab.sweep([0.15, 0.25, 0.35, 0.45])
    for variant, sharpe in zip(variants, (0.9, -1.5, 1.5, -2.5), strict=True):
        lab.trial(variant, sharpe)
    winner = variants[2]
    lab.run_mark(sweep.sweep_id, _mark_now(lab))
    path = lab.promoted_file(sweep, winner)
    _refused(lab, path, "below promote_at_least")
    sr_star_at_completion = lab.report().sr_star_annual
    assert sr_star_at_completion is not None

    _clustered(lab, "mom-cluster", 1.0, 12)

    today = lab.report()
    assert today.sr_star_annual is not None and today.sr_star_annual < sr_star_at_completion
    # The file restated at today's declared count.
    _refused(lab, lab.promoted_file(sweep, winner), "below promote_at_least")


def test_v_shrink_a_clustered_sweep_is_judged_at_an_earlier_sweeps_mark(lab: Lab) -> None:
    broad, variants = lab.sweep([0.15, 0.25, 0.35, 0.45], slug="mom-broad")
    for variant, sharpe in zip(variants, BROAD, strict=True):
        lab.trial(variant, sharpe)
    lab.run_mark(broad.sweep_id, _mark_now(lab))
    earlier_mark = _mark_now(lab)

    clustered = _clustered(lab, GRID, 1.0, 12)
    lab.run_mark(clustered.sweep_id, _mark_today(lab))
    _judged_at(lab, clustered, earlier_mark)


def test_v_shrink_a_first_clustered_sweep_faces_the_variance_floor(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """No twin trial: the family's only counted trials are the sweep's two, with
    near-identical Sharpes (V ~ 0). SR\\* at the floored V is positive with N = 2."""
    root = tmp_path / "files"
    root.mkdir()
    lab = Lab(lab_store, settings, root)
    lab.twin()
    sweep, variants = lab.sweep([0.15, 0.25])
    lab.trial(variants[0], 0.1)
    lab.trial(variants[1], 0.0999)
    report = lab.report()
    assert report.sharpe_variance_annual_excess is not None
    assert report.sharpe_variance_annual_excess < 1e-6
    verdicts = report.verdicts
    assert verdicts is not None
    rules = lab_registry.family_rules(lab.conn, "momentum")
    assert rules is not None
    assert verdicts.sr_star_high_water_annual == pytest.approx(
        expected_max_sharpe(2, rules.min_sharpe_variance_annual)
    )
    assert verdicts.sr_star_high_water_annual > 0
    # Against the unfloored SR* (about 0) the argmax would have cleared 0.5.
    assert report.rows[0].dsr_excess is not None and report.rows[0].dsr_excess >= 0.5
    _refused(lab, lab.promoted_file(sweep, variants[0]), "below promote_at_least")


def test_v_shrink_a_broad_sweep_stopped_short_still_set_the_mark(lab: Lab) -> None:
    broad, variants = lab.sweep([0.15, 0.25, 0.35, 0.45, 0.55], slug="mom-broad")
    for variant, sharpe in zip(variants[:4], BROAD, strict=True):
        lab.trial(variant, sharpe)
    stopped_mark = _mark_now(lab)
    lab.run_mark(broad.sweep_id, stopped_mark, completed=False)

    clustered = _clustered(lab, GRID, 1.0, 12)
    _judged_at(lab, clustered, stopped_mark)


def test_v_shrink_a_later_rise_in_todays_sr_star_takes_a_promotion_below_the_floor(
    lab: Lab,
) -> None:
    sweep, (winner, _other) = _won(lab, (1.2, 1.0))
    report = lab.report()
    assert report.verdicts is not None and report.verdicts.promote_at_least_met
    lab.run_mark(sweep.sweep_id, _mark_now(lab))

    # A later, dispersed sweep raises today's N and V, so today's SR* rises.
    _later, variants = lab.sweep([0.35, 0.45, 0.55, 0.65], slug="mom-later")
    for variant, sharpe in zip(variants, (2.5, -2.5, 2.0, -2.0), strict=True):
        lab.trial(variant, sharpe)
    risen = lab.report()
    assert risen.verdicts is not None
    assert risen.verdicts.sr_star_high_water_annual > report.verdicts.sr_star_high_water_annual
    _refused(lab, lab.promoted_file(sweep, winner), "below promote_at_least")


# --- the promoted slug on the backtest path ------------------------------------------


@pytest.fixture
def lab_path(
    fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """`fixture_store_path` with the lab tables, and live settings whose store is
    another temp file (as `tests/backtest/test_run.py`'s)."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
    conn = duckdb.connect(str(fixture_store_path))
    configure_connection(conn)
    lab_schema.apply_lab_schema(conn)
    conn.close()
    yield fixture_store_path


def _spend(slug: str, path: Path, *, repeat: bool = False) -> Any:
    flags = Flags(spend_holdout=True, override_gap=True, holdout_repeat=repeat)
    reasons = Reasons(holdout_reason="spend", gap_reason="owner accepts the June gap")
    return run_hypothesis(
        slug, *HOLDOUT_WINDOW, flags, reasons=reasons, synthetic=True, store_path=path
    )


def test_the_promoted_slug_runs_and_spends_under_the_phase_3_rules(
    lab_path: Path, settings: Settings, tmp_path: Path
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    conn = duckdb.connect(str(lab_path))
    configure_connection(conn)
    lab = Lab(conn, settings, root)
    lab.twin()
    sweep, (winner, _other) = _won(lab)
    lab.promote(lab.promoted_file(sweep, winner))
    conn.close()

    in_sample = run_hypothesis(PROMOTED, None, None, Flags(), store_path=lab_path)
    assert in_sample.status == "ok"
    twin_spend = _spend(TWIN, lab_path)
    first = _spend(PROMOTED, lab_path)
    second = _spend(PROMOTED, lab_path)
    assert (twin_spend.status, first.status) == ("ok", "ok")
    assert second.status == "refused_holdout"

    read = duckdb.connect(str(lab_path), read_only=True)
    try:
        rows = {
            trial_id: (kind, repeat)
            for trial_id, kind, repeat in read.execute(
                "SELECT trial_id, kind, holdout_repeat FROM trials"
            ).fetchall()
        }
        assert rows[in_sample.trial_id] == ("in_sample", False)
        assert rows[twin_spend.trial_id] == ("holdout", False)
        assert rows[first.trial_id] == ("holdout", True)
        (message,) = read.execute(  # type: ignore[misc]
            "SELECT message FROM trial_results WHERE trial_id = ?", [second.trial_id]
        ).fetchone()
        assert "holdout-repeat" in message
    finally:
        read.close()


def test_promotion_module_has_no_destructive_statement() -> None:
    text = Path(promotion.__file__).read_text()
    assert re.search(r"\b(DROP|DELETE|TRUNCATE|VACUUM)\b", text, re.IGNORECASE) is None
