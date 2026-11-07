"""A new root family's signal-input check and the window V floor (#3126, #3127;
strategy-lab spec, "Amendment 2026-11-06 (#3126, #3127)").

#3126: a root family's first `family_rules` row is refused when the family's
`signal_inputs` (`config_backtest.FAMILIES`) overlap those of another family that has
a hypothesis registered at the registration instant, unless an `owner_decisions` row
of kind `family_root`, made at or before that instant, approves the root and names
every overlapping family; the approved family's N then counts the named families'
trials. Every read is of rows known at the registration instant (the no-look-ahead
cases pass an earlier `as_of`).

#3127: the V floor is the larger of the stored `min_sharpe_variance_annual` and the
null variance of an annual Sharpe over the family window's monthly periods.

The Audit B noise case (`~/tradepartner-probes/auditB/scripts/tp_lab.py demo`): an
idea refused in `momentum` and retried as a root family reading the same inputs with
one variant at an annual excess Sharpe of 0.043 was promoted at N = 1 and SR* = 0. It
is now refused at registration, and with the owner's approval its N counts momentum's
trials, so the promotion is refused.

Families whose inputs overlap are made by monkeypatching `FAMILIES` (the real table's
roots do not overlap `momentum`): the stand-in for a new family whose code reads prices.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from conftest import mark_pre_lab

from tradepartner import config_backtest
from tradepartner.backtest import frozen, hypothesis, promotion, results
from tradepartner.backtest.metrics import expected_max_sharpe, null_sharpe_variance_annual
from tradepartner.backtest.promotion import PromotionRefused
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings
from tradepartner.store import lab_registry, registry, schema
from tradepartner.store.db import insert_row, utc_now

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses"
IN_SAMPLE_START = date(2018, 1, 31)
HOLDOUT = (date(2019, 6, 3), date(2020, 6, 30))
CUTOFF = datetime(2019, 5, 31, 20, 0, tzinfo=UTC)
CODE = "c" * 64
#: Month-end sessions 2018-01-31 .. 2019-05-31: 17 sessions, 16 monthly periods.
WINDOW_PERIODS = 16


def _overlapping(monkeypatch: pytest.MonkeyPatch, inputs: tuple[str, ...]) -> None:
    """`profitability`'s spec with `inputs` as its signal inputs (a new root whose
    signal reads what `momentum`'s does)."""
    spec = config_backtest.FAMILIES["profitability"]
    monkeypatch.setitem(
        config_backtest.FAMILIES,
        "profitability",
        dataclasses.replace(spec, signal_inputs=inputs),  # type: ignore[arg-type]
    )


def _text(family: str, slug: str, top: float | None = None) -> str:
    name = "fixture-momentum.md" if family == "momentum" else "fixture-profitability.md"
    text = (FIXTURES / name).read_text()
    edits = [
        ("in_sample_start = 2017-01-31", f"in_sample_start = {IN_SAMPLE_START}"),
        ("start = 2023-01-03", f"start = {HOLDOUT[0]}"),
        ("end = 2025-12-31", f"end = {HOLDOUT[1]}"),
        (f'slug = "fixture-{family}"', f'slug = "{slug}"'),
    ]
    if top is not None:
        edits.append(("top_fraction = 0.10", f"top_fraction = {top}"))
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new, 1)
    return text


class Lab:
    """Two families on a lab fixture store, rows by hand (as `test_promotion`)."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, settings: Settings, root: Path) -> None:
        self.conn = conn
        self.settings = settings
        self.root = root
        self.vintage = registry.data_vintage(conn, CUTOFF)

    def file(self, family: str, slug: str, top: float | None = None) -> Path:
        path = self.root / f"{slug}.md"
        path.write_text(_text(family, slug, top))
        return path

    def direct(self, path: Path, *, slug: str | None = None) -> registry.HypothesisRecord:
        parsed = hypothesis.parse_file(path)
        return registry.register_hypothesis(
            self.conn,
            slug=slug or parsed.slug,
            family=parsed.family,
            title=parsed.title,
            doc_path=path.as_posix(),
            doc_sha256=parsed.doc_sha256,
            params=hypothesis.frozen_params(parsed, self.settings),
            in_sample_start=parsed.in_sample_start,
            holdout_start=parsed.holdout_start,
            holdout_end=parsed.holdout_end,
            registered_by="test",
            settings=self.settings,
        )

    def twin(self, family: str) -> registry.HypothesisRecord:
        record = self.direct(self.file(family, f"{family}-twin"))
        mark_pre_lab(self.conn, record.hypothesis_id)
        lab_registry.write_fingerprint(self.conn, record.hypothesis_id, _fp(record))
        return record

    def rules(self, family: str, first: registry.HypothesisRecord) -> lab_registry.FamilyRules:
        return lab_registry.write_family_rules(
            self.conn,
            family=family,
            first_hypothesis_id=first.hypothesis_id,
            parent_family=None,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            in_sample_start=IN_SAMPLE_START,
            fixed_params={
                k: v
                for k, v in frozen.frozen_values(first).items()
                if k.startswith(FORBIDDEN_AXIS_PREFIXES)
            },
            sr_star_seed_annual=None,
            settings=self.settings,
        )

    def sweep(
        self, family: str, slug: str, count: int
    ) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
        tops = [round(0.15 + i / 10, 2) for i in range(count)]
        grid: dict[str, list[object]] = (
            {"strategy.top_fraction": list(tops)}
            if family == "momentum"
            else {"schedule.rebalance_cadence": ["month_end"]}
        )
        sweep = lab_registry.register_sweep(
            self.conn,
            slug=slug,
            family=family,
            title=slug,
            doc_path=f"docs/sweeps/{slug}.md",
            doc_sha256="s" * 64,
            grid=grid,
            n_variants=count,
            selection_statistic="sharpe_annual_excess_spy",
            expected_excess_cagr_spy_pp=0.0,
            expected_range_pp=(-2.0, 2.0),
            promote_at_least=0.5,
            retire_below=0.0,
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="owner",
            settings=self.settings,
        )
        records = []
        for index in range(1, count + 1):
            variant = f"{slug}--r{sweep.sweep_id}-v{index}"
            top = tops[index - 1] if family == "momentum" else None
            record = self.direct(self.file(family, f"{variant}-src", top), slug=variant)
            params: dict[str, object] = (
                {"strategy.top_fraction": top}
                if family == "momentum"
                else {"schedule.rebalance_cadence": "month_end"}
            )
            lab_registry.write_sweep_variant(
                self.conn,
                sweep_id=sweep.sweep_id,
                variant_index=index,
                hypothesis_id=record.hypothesis_id,
                fingerprint=_fp(record),
                variant_params=params,
            )
            lab_registry.write_fingerprint(self.conn, record.hypothesis_id, _fp(record))
            records.append(record)
        return sweep, records

    def trial(self, record: registry.HypothesisRecord, sharpe: float) -> None:
        """A counted trial at `sharpe` (annual excess) over 40 monthly periods."""
        from tradepartner.backtest.holdout import Frozen, default_in_sample_window

        values = frozen.frozen_values(record)
        window = default_in_sample_window(
            Frozen.from_hypothesis(record), values["schedule.rebalance_cadence"]
        )
        (trial_id,) = self.conn.execute(  # type: ignore[misc]
            "SELECT COALESCE(MAX(trial_id), 0) + 1 FROM trials"
        ).fetchone()
        insert_row(
            self.conn,
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
                "code_dirty": False,
                "synthetic": False,
                "holdout_repeat": False,
                "run_by": "test",
                "detail_level": "summary",
                "data_vintage": self.vintage,
                "code_tree_sha256": CODE,
            },
        )
        insert_row(
            self.conn,
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
        metrics = {
            "excess_cagr_spy": sharpe / 20,
            "sharpe_annual_excess_spy": sharpe,
            "sharpe_period_excess_spy": sharpe / math.sqrt(12),
            "skew_period_excess_spy": 0.0,
            "kurtosis_period_excess_spy": 3.0,
            "sharpe_period": sharpe / math.sqrt(12),
            "cost_drag": 0.004,
            "turnover_annual": 5.5,
            "max_drawdown": -0.2,
            "n_periods": 40.0,
            "periods_per_year": 12.0,
        }
        for metric, value in metrics.items():
            insert_row(
                self.conn,
                "trial_metrics",
                {
                    "trial_id": trial_id,
                    "series": "strategy",
                    "cost_per_side_bps": float(values[registry.BASE_COST_KEY]),
                    "metric": metric,
                    "value": value,
                },
            )

    def promoted_file(
        self, family: str, sweep: lab_registry.SweepRecord, variant: registry.HypothesisRecord
    ) -> Path:
        top = frozen.frozen_values(variant).get("strategy.top_fraction")
        text = _text(family, f"{sweep.slug}-final", top if family == "momentum" else None)
        declared = lab_registry.family_declared_count(self.conn, family)
        text += (
            "\n## Sweep provenance\n\n"
            f"- **Sweep:** `{sweep.slug}`, registration id {sweep.sweep_id}; the family's "
            f"declared count n = {declared} at promotion\n"
            f"- **Variant:** `{variant.slug}`, rank 1 (the argmax) of the registration's "
            f"{sweep.n_variants} variants by sharpe_annual_excess_spy\n"
            "- **Base-level in-sample statistics at promotion:** placeholders\n"
        )
        path = self.root / f"{sweep.slug}-final.md"
        path.write_text(text)
        return path


def _fp(record: registry.HypothesisRecord) -> str:
    return frozen.fingerprint(record.family, frozen.frozen_values(record), record.in_sample_start)


def _approve(
    conn: duckdb.DuckDBPyConnection, family: str = "profitability", overlap: list[str] | None = None
) -> int:
    return registry.record_decision(
        conn,
        kind="family_root",
        reason="a new root on momentum's inputs; N counts momentum's trials",
        values={"family": family, "overlapping_families": overlap or ["momentum"]},
    )


@pytest.fixture
def lab(lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path) -> Lab:
    root = tmp_path / "files"
    root.mkdir()
    built = Lab(lab_store, settings, root)
    built.rules("momentum", built.twin("momentum"))
    return built


# --- the table ------------------------------------------------------------------------


def test_every_registered_family_names_signal_inputs_from_the_stores_tables() -> None:
    for family, spec in config_backtest.FAMILIES.items():
        if family == "oracle":
            continue
        assert spec.signal_inputs, family
        assert set(spec.signal_inputs) <= set(schema.TABLE_NAMES), family


def test_the_real_roots_that_have_rules_on_the_store_do_not_overlap() -> None:
    """`momentum` and `profitability`, the two families with rules on the owner's store
    (read-only registry read, 2026-11-06), read disjoint inputs, so no stored
    registration would have been refused."""
    inputs = {f: set(s.signal_inputs) for f, s in config_backtest.FAMILIES.items()}
    assert not inputs["momentum"] & inputs["profitability"]


# --- the root check (#3126) -------------------------------------------------------------


def test_a_root_reading_an_existing_familys_inputs_is_refused_and_writes_nothing(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    _overlapping(monkeypatch, ("prices_daily",))
    twin = lab.twin("profitability")
    with pytest.raises(lab_registry.RootFamilyRefused, match=r"momentum.*prices_daily"):
        lab.rules("profitability", twin)
    assert lab_registry.family_rules(lab.conn, "profitability") is None


def test_a_root_with_no_signal_inputs_is_refused(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    _overlapping(monkeypatch, ())
    twin = lab.twin("profitability")
    with pytest.raises(lab_registry.RootFamilyRefused, match="names no signal inputs"):
        lab.rules("profitability", twin)


def test_a_root_with_disjoint_inputs_registers_with_its_own_n(lab: Lab) -> None:
    twin = lab.twin("profitability")
    lab.rules("profitability", twin)
    assert lab_registry.linked_families(lab.conn, "profitability") == ()


def test_an_approved_root_registers_and_its_n_counts_the_overlapping_familys_trials(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    _overlapping(monkeypatch, ("prices_daily",))
    _, momentum = lab.sweep("momentum", "idea-v1", 2)
    for record, sharpe in zip(momentum, (0.45, 0.2), strict=True):
        lab.trial(record, sharpe)
    twin = lab.twin("profitability")
    _approve(lab.conn)
    lab.rules("profitability", twin)
    assert lab_registry.linked_families(lab.conn, "profitability") == ("momentum",)
    split = results.family_n_split(lab.conn, "profitability")
    assert (split.trials, split.linked) == (0, 2)
    assert results.family_n(lab.conn, "profitability") == 2
    assert results.family_n(lab.conn, "momentum") == 2


def test_an_approval_not_naming_every_overlapping_family_is_refused(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    _overlapping(monkeypatch, ("prices_daily",))
    twin = lab.twin("profitability")
    _approve(lab.conn, overlap=["filing_delay"])
    with pytest.raises(lab_registry.RootFamilyRefused, match=r"does not name.*momentum"):
        lab.rules("profitability", twin)


def _exposure(conn: duckdb.DuckDBPyConnection, family: str, known_at: datetime) -> None:
    """A `research_exposures` row of `family` (an experiment-only family's N source)."""
    insert_row(
        conn,
        "research_exposures",
        {
            "exposure_id": 1,
            "candidate_ref": "c2",
            "source": "store",
            "family": family,
            "kill_line_url": "https://example.invalid/kill",
            "kill_line_sha256": "k" * 64,
            "kill_line_posted_at": known_at,
            "kill_line_basis": "posted_before_read",
            "probe_path": "p.py",
            "probe_sha256": "p" * 64,
            "probe_verified": True,
            "read_at": known_at,
            "window_start": date(2020, 1, 1),
            "window_end": date(2023, 12, 31),
            "data_pin_json": "{}",
            "n_configurations": 3,
            "verdict": "fail",
            "desk_read_path": "docs/x.md",
            "audit_url": None,
            "overlaps_holdout": False,
            "holdout_reason": None,
            "post_boundary": False,
            "synthetic": False,
            "recorded_by": "test",
            "code_version": "abc",
            "code_dirty": False,
            "note": None,
            "known_at": known_at,
        },
    )


def test_an_experiment_only_familys_research_rows_count_as_existing(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`spinoff` has N from research exposures but no hypotheses: a new root reading
    `corporate_actions` overlaps it, from the instant the exposure is known."""
    _overlapping(monkeypatch, ("corporate_actions",))
    known = utc_now()
    _exposure(lab.conn, "spinoff", known)
    refusal = lab_registry.root_family_refusal
    assert refusal(lab.conn, "profitability", as_of=known - timedelta(seconds=1)) is None
    assert "spinoff" in str(refusal(lab.conn, "profitability", as_of=utc_now()))


def test_a_child_family_is_not_checked(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    _overlapping(monkeypatch, ("prices_daily",))
    twin = lab.twin("profitability")
    lab_registry.write_family_rules(
        lab.conn,
        family="profitability",
        first_hypothesis_id=twin.hypothesis_id,
        parent_family="momentum",
        holdout_start=HOLDOUT[0],
        holdout_end=HOLDOUT[1],
        in_sample_start=IN_SAMPLE_START,
        fixed_params={},
        sr_star_seed_annual=0.3,
        settings=lab.settings,
    )


# --- no look-ahead ------------------------------------------------------------------------


def test_the_check_reads_only_rows_known_at_the_registration_instant(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An approval made after `as_of` does not approve; a hypothesis registered after
    `as_of` does not overlap. Rows stamped later than the instant are invisible."""
    _overlapping(monkeypatch, ("prices_daily",))
    (momentum_at,) = lab.conn.execute(  # type: ignore[misc]
        "SELECT MIN(registered_at) FROM hypotheses WHERE family = 'momentum'"
    ).fetchone()
    before_momentum = momentum_at - timedelta(seconds=1)
    assert (
        lab_registry.root_family_refusal(lab.conn, "profitability", as_of=before_momentum) is None
    )
    now = utc_now()
    assert "momentum" in str(lab_registry.root_family_refusal(lab.conn, "profitability", as_of=now))
    _approve(lab.conn)
    (approved_at,) = lab.conn.execute(  # type: ignore[misc]
        "SELECT made_at FROM owner_decisions WHERE kind = 'family_root'"
    ).fetchone()
    just_before = approved_at - timedelta(microseconds=1)
    assert lab_registry.root_family_refusal(lab.conn, "profitability", as_of=just_before)
    assert lab_registry.root_family_refusal(lab.conn, "profitability", as_of=approved_at) is None


def test_a_later_approval_never_links_a_family_registered_before_it(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`linked_families` reads the approval known when the rules were written: a
    `family_root` row made afterwards for a family registered without overlap links
    nothing (and moves no stored registration's N)."""
    twin = lab.twin("profitability")
    lab.rules("profitability", twin)
    _approve(lab.conn)
    assert lab_registry.linked_families(lab.conn, "profitability") == ()


# --- the family_root decision ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "match"),
    [
        ({"family": "profitability"}, "exactly"),
        ({"family": "profitability", "overlapping_families": []}, "non-empty"),
        ({"family": "profitability", "overlapping_families": ["profitability"]}, "itself"),
        ({"family": "profitability", "overlapping_families": ["nope"]}, "unknown"),
        ({"family": "combined", "overlapping_families": ["momentum"]}, "not a root"),
        ({"family": "profitability", "overlapping_families": ["momentum", "momentum"]}, "twice"),
    ],
)
def test_a_malformed_family_root_decision_is_refused(
    lab: Lab, values: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        registry.record_decision(lab.conn, kind="family_root", reason="r", values=values)


# --- the V floor (#3127) ---------------------------------------------------------------------


def test_the_null_variance_of_an_annual_sharpe_over_forty_months_is_three_tenths() -> None:
    assert null_sharpe_variance_annual(40, 12) == pytest.approx(0.30)
    assert null_sharpe_variance_annual(40, 12, sharpe_annual=1.2) == pytest.approx(
        12 / 40 * (1 + (1.2**2 / 12) / 2)
    )
    with pytest.raises(ValueError):
        null_sharpe_variance_annual(0, 12)


def test_the_v_floor_is_the_windows_null_variance(lab: Lab) -> None:
    rules = lab_registry.family_rules(lab.conn, "momentum")
    assert rules is not None
    assert lab_registry.family_window_periods(rules) == WINDOW_PERIODS
    null = 12 / WINDOW_PERIODS
    assert lab_registry.family_v_floor(rules, lab.settings) == pytest.approx(null)
    mark = lab_registry.family_sr_star_high_water_mark(
        lab.conn,
        "momentum",
        n_trials_today=15,
        sharpe_variance_annual_today=0.0953,
        settings=lab.settings,
    )
    assert mark == pytest.approx(expected_max_sharpe(15, null))


def test_the_configured_sharpe_only_raises_the_floor(lab: Lab) -> None:
    rules = lab_registry.family_rules(lab.conn, "momentum")
    assert rules is not None
    raised = Settings(_env_file=None, lab={"v_floor_null_sharpe_annual": 1.0})
    assert lab_registry.family_v_floor(rules, raised) > lab_registry.family_v_floor(
        rules, lab.settings
    )
    with pytest.raises(ValueError):
        Settings(_env_file=None, lab={"v_floor_null_sharpe_annual": -0.5})


def test_a_stored_floor_above_the_null_still_binds(lab: Lab) -> None:
    rules = lab_registry.family_rules(lab.conn, "momentum")
    assert rules is not None
    high = dataclasses.replace(rules, min_sharpe_variance_annual=2.0)
    assert lab_registry.family_v_floor(high, lab.settings) == 2.0


# --- Audit B's noise case ----------------------------------------------------------------


def test_audit_b_noise_retry_as_a_new_root_is_no_longer_promoted(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tp_lab.py demo: idea v1 refused in `momentum` (2 variants), its "improved" retry
    registered as a root on the same inputs, one variant at an annual excess Sharpe of
    0.043. On origin/main the retry's family had N = 1, SR* = 0, and it was promoted.
    Now its rules are refused without the owner's `family_root` row, and with it the
    retry's N counts momentum's trials and the promotion is refused."""
    _overlapping(monkeypatch, ("prices_daily",))
    v1, v1_records = lab.sweep("momentum", "idea-v1", 2)
    for record, sharpe in zip(v1_records, (0.45, 0.31), strict=True):
        lab.trial(record, sharpe)
    with pytest.raises(PromotionRefused):
        promotion.promote(
            lab.conn,
            "idea-v1",
            lab.promoted_file("momentum", v1, v1_records[0]),
            "argmax",
            lab.settings,
            code_vintage=CODE,
        )
    twin = lab.twin("profitability")
    with pytest.raises(lab_registry.RootFamilyRefused):
        lab.rules("profitability", twin)
    _approve(lab.conn)
    lab.rules("profitability", twin)
    v2, v2_records = lab.sweep("profitability", "idea-v2", 1)
    lab.trial(v2_records[0], 0.043)
    assert results.family_n(lab.conn, "profitability") == 3
    with pytest.raises(PromotionRefused, match="below promote_at_least"):
        promotion.promote(
            lab.conn,
            "idea-v2",
            lab.promoted_file("profitability", v2, v2_records[0]),
            "argmax",
            lab.settings,
            code_vintage=CODE,
        )
    assert lab.conn.execute(
        "SELECT COUNT(*) FROM owner_decisions WHERE kind = 'promotion'"
    ).fetchone() == (0,)
