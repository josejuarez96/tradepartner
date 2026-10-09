"""Tests for the trial-registry view (Phase 3 T44, spec req 17), and its
families card and sweeps table (strategy-lab plan T112, spec req 14, the "Pages"
criterion), the lab cases on the `lab_store` fixture and on a temp-file store
with the lab tables applied.

Driven headless through `streamlit.testing.v1.AppTest` on the shell, as
`test_backtest_page.py` does, over a temp store seeded only through
`store.registry`. `load_registry_view` is also exercised directly: it is
the page's only reader and needs no Streamlit.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.backtest import results
from tradepartner.backtest.metrics import expected_max_sharpe
from tradepartner.config import Settings
from tradepartner.dashboard import trials_page
from tradepartner.store import lab_registry, lab_schema, registry, schema
from tradepartner.store.db import insert_row, open_for_write, utc_now
from tradepartner.store.lab_schema import LabNotInitialised

_APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "dashboard" / "app.py"
)
_PAGE = "Trial registry"


def _hypothesis(conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str) -> int:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": 15.0, "strategy.top_fraction": 0.1},
        in_sample_start=date(2017, 1, 31),
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="owner",
        settings=settings,
    ).hypothesis_id


def _open(
    conn: duckdb.DuckDBPyConnection, settings: Settings, hypothesis_id: int, **kwargs: Any
) -> registry.TrialHandle:
    values: dict[str, Any] = {
        "kind": "in_sample",
        "start_session": date(2020, 1, 31),
        "end_session": date(2020, 3, 31),
        "data_cutoff": datetime(2020, 3, 31, 20, tzinfo=UTC),
        "synthetic": False,
        "run_by": "owner",
    }
    values.update(kwargs)
    return registry.open_trial(conn, hypothesis_id=hypothesis_id, settings=settings, **values)


def _ok(conn: duckdb.DuckDBPyConnection, handle: registry.TrialHandle) -> None:
    registry.write_result(
        conn,
        handle,
        registry.ResultStatistics(
            n_trials=1,
            sharpe_variance=None,
            sr_star=0.0,
            psr_zero=0.9,
            dsr=0.9,
            sharpe_variance_excess=None,
            sr_star_excess=0.0,
            psr_zero_excess=0.6,
            dsr_excess=0.6,
            dsr_basis="psr",
            red_flag=False,
            gap_max_count_share=0.03,
            gap_max_size_share=0.004,
        ),
    )


class Seeded:
    def __init__(self) -> None:
        self.ok = 0
        self.failed = 0
        self.refused = 0
        self.unfinished = 0
        self.synthetic = 0
        self.other = 0


def _seed(store_path: Path, tmp_path: Path) -> Seeded:
    """Two hypotheses; on h1 an ok, a failed, a refused, an unfinished and a
    synthetic trial; on h2 one ok trial; and two owner decisions."""
    store_settings = Settings(_env_file=None, store={"path": str(store_path)})
    # A different "real store" path, so synthetic trials are allowed here.
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    seeded = Seeded()
    with open_for_write(store_settings) as conn:
        schema.init_schema(conn)
        h1 = _hypothesis(conn, seed_settings, "h1-momentum-12-1")
        h2 = _hypothesis(conn, seed_settings, "h2-momentum-top-20")
        ok = _open(conn, seed_settings, h1)
        _ok(conn, ok)
        seeded.ok = ok.trial_id
        failed = _open(conn, seed_settings, h1)
        registry.close_trial(conn, failed, "failed", "provider raised: no bars for SPY")
        seeded.failed = failed.trial_id
        refused = _open(conn, seed_settings, h1, kind="holdout", start_session=date(2023, 1, 31))
        registry.close_trial(conn, refused, "refused_gap", "gap 7.1% over the 5% threshold")
        seeded.refused = refused.trial_id
        seeded.unfinished = _open(conn, seed_settings, h1).trial_id
        synthetic = _open(conn, seed_settings, h1, synthetic=True)
        _ok(conn, synthetic)
        seeded.synthetic = synthetic.trial_id
        other = _open(conn, seed_settings, h2)
        _ok(conn, other)
        seeded.other = other.trial_id
        registry.record_decision(
            conn,
            kind="gap_signoff",
            reason="free-data gap accepted for H1",
            values={"gap_count_share": 0.031},
            hypothesis_id=h1,
        )
        registry.record_decision(
            conn,
            kind="gap_override",
            reason="override for the h2 exploratory run",
            values={"gap_count_share": 0.061},
            trial_id=other.trial_id,
        )
    return seeded


@pytest.fixture
def seeded_store(tmp_path: Path) -> tuple[Path, Seeded]:
    store_path = tmp_path / "store.duckdb"
    return store_path, _seed(store_path, tmp_path)


def _read(store_path: Path, **kwargs: Any) -> trials_page.RegistryView:
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        return trials_page.load_registry_view(conn, **kwargs)
    finally:
        conn.close()


# --- load_registry_view (pure reader) -----------------------------------------


def test_trials_newest_first_synthetic_hidden(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path)
    assert [t.trial_id for t in view.trials] == [s.other, s.unfinished, s.refused, s.failed, s.ok]


def test_synthetic_shown_when_asked(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path, include_synthetic=True)
    assert s.synthetic in [t.trial_id for t in view.trials]
    assert [t.trial_id for t in view.trials] == sorted(
        (t.trial_id for t in view.trials), reverse=True
    )


def test_hypothesis_filter(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path, slug="h2-momentum-top-20")
    assert [t.trial_id for t in view.trials] == [s.other]
    assert view.slugs == ("h1-momentum-12-1", "h2-momentum-top-20")


def test_failed_refused_and_unfinished_keep_their_message(
    seeded_store: tuple[Path, Seeded],
) -> None:
    store_path, s = seeded_store
    by_id = {t.trial_id: t for t in _read(store_path).trials}
    assert (by_id[s.failed].status, by_id[s.failed].message) == (
        "failed",
        "provider raised: no bars for SPY",
    )
    assert by_id[s.refused].status == "refused_gap"
    assert by_id[s.unfinished].status == registry.UNFINISHED
    assert by_id[s.unfinished].finished_at is None


def test_decisions_newest_first_and_filtered(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path)
    assert [d.kind for d in view.decisions] == ["gap_override", "gap_signoff"]
    override = view.decisions[0]
    assert (override.trial_id, override.slug) == (s.other, "h2-momentum-top-20")
    assert override.values_json == '{"gap_count_share":0.061}'
    # A decision tied to a trial is found through that trial's hypothesis.
    h1 = _read(store_path, slug="h1-momentum-12-1")
    assert [d.reason for d in h1.decisions] == ["free-data gap accepted for H1"]


def test_status_counts(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, _ = seeded_store
    counts = _read(store_path).status_counts
    assert counts == {"ok": 2, "failed": 1, "refused_gap": 1, "unfinished": 1}


def _summary(trial_id: int, message: str | None, note: str | None) -> registry.TrialSummary:
    return registry.TrialSummary(
        trial_id=trial_id,
        hypothesis_id=1,
        slug="h1",
        family="momentum",
        kind="in_sample",
        started_at=datetime(2020, 4, 1, tzinfo=UTC),
        start_session=date(2020, 1, 31),
        end_session=date(2020, 3, 31),
        synthetic=False,
        holdout_repeat=False,
        run_by="owner",
        note=note,
        status="failed" if message else "ok",
        message=message,
        finished_at=None if message else datetime(2020, 4, 1, 1, tzinfo=UTC),
    )


def test_tables_keep_a_late_message_after_100_empty_rows() -> None:
    # polars infers types from the first 100 rows; an all-null message
    # column there must not reject an older trial's message.
    trials = (
        *tuple(_summary(200 - i, None, None) for i in range(150)),
        _summary(1, "provider raised", "rerun later"),
    )
    table = trials_page._trials_table(trials)
    assert table["message"].to_list()[-1] == "provider raised"
    assert table["note"].to_list()[-1] == "rerun later"
    decisions = (
        *tuple(
            trials_page.DecisionRow(
                200 - i, datetime(2020, 4, 1, tzinfo=UTC), "gap_signoff", None, None, "{}", "ok"
            )
            for i in range(150)
        ),
        trials_page.DecisionRow(
            1, datetime(2020, 4, 1, tzinfo=UTC), "gap_override", "h1", 7, "{}", "late"
        ),
    )
    table = trials_page._decisions_table(decisions)
    assert (table["hypothesis"][-1], table["trial"][-1]) == ("h1", 7)


# --- headless render ------------------------------------------------------------


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run()
    return at


def _text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.subheader]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


def _trial_ids(at: AppTest) -> list[int]:
    return [int(i) for i in at.dataframe[0].value["trial"].to_list()]


def test_page_is_in_the_shell_navigation(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    assert at.sidebar.radio[0].options == [
        "Data health",
        "Backtest",
        _PAGE,
        "Research",
        "Operations",
        "Override",
    ]


def test_render_lists_trials_and_decisions(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, s = seeded_store
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert _trial_ids(at) == [s.other, s.unfinished, s.refused, s.failed, s.ok]
    trials = at.dataframe[0].value
    assert "provider raised: no bars for SPY" in trials["message"].to_list()
    decisions = at.dataframe[1].value
    assert decisions["reason"].to_list() == [
        "override for the h2 exploratory run",
        "free-data gap accepted for H1",
    ]
    text = _text(at)
    assert "1 unfinished" in text and "1 failed" in text and "1 refused_gap" in text


def test_render_synthetic_toggle(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, s = seeded_store
    at = _app(monkeypatch, store_path)
    [toggle] = at.checkbox
    assert toggle.value is False
    assert s.synthetic not in _trial_ids(at)
    toggle.check().run()
    assert s.synthetic in _trial_ids(at)


def test_render_hypothesis_filter(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, s = seeded_store
    at = _app(monkeypatch, store_path)
    [picker] = at.selectbox
    assert picker.options == ["All hypotheses", "h1-momentum-12-1", "h2-momentum-top-20"]
    picker.set_value("h2-momentum-top-20").run()
    assert _trial_ids(at) == [s.other]
    assert at.dataframe[1].value["reason"].to_list() == ["override for the h2 exploratory run"]


def test_render_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    at = _app(monkeypatch, seeded_store[0])
    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    at.checkbox[0].check().run()
    assert not at.exception
    assert opened == [True]


def test_render_empty_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "empty.duckdb"
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        schema.init_schema(conn)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "no trials" in _text(at).lower()
    assert not at.dataframe


def test_render_registry_not_initialised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "v2.duckdb"
    conn = duckdb.connect(str(store_path))
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER, applied_at TIMESTAMPTZ)")
        conn.execute("INSERT INTO schema_version VALUES (2, now())")
    finally:
        conn.close()
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "registry not initialised" in _text(at).lower()
    assert not at.dataframe


def test_header_shows_as_of_and_last_updated(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    text = _text(at)
    assert "as of" in text and "last updated" in text


def test_no_colour_literal_in_page_code() -> None:
    source = Path(trials_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


# --- families card and sweeps table (strategy-lab spec req 14; plan T112) -------------

LAB_IN_SAMPLE_START = date(2020, 8, 31)
LAB_HOLDOUT_START = date(2024, 1, 2)
LAB_HOLDOUT_END = date(2026, 9, 30)
#: The default in-sample window's end at `month_end` (as in test_sweep_report.py).
LAB_DEFAULT_END = date(2023, 12, 29)
LAB_CUTOFF = datetime(2023, 12, 29, 21, 0, tzinfo=UTC)
LAB_CODE = "c" * 64
LAB_BASE = 15.0
LAB_FIXED = {
    "costs.per_side_bps": LAB_BASE,
    "execution.fill_price": "close",
    "gap.count_share_threshold": 0.02,
    "universe.top_n_by_cap": 500,
}
#: The wall-clock bound on loading the families card and the sweeps table over a
#: generated registry of 10,000 `ok` trials (seconds), with ample headroom.
RENDER_SECONDS_LIMIT = 20.0
#: The complete sweep's variants: (excess_cagr_spy, sharpe_annual_excess_spy, red flag).
#: v2 and v4 tie on the selection statistic; v2 is the argmax by canonical index.
COMPLETE = [(0.01, 0.4, False), (0.025, 0.9, False), (-0.015, 0.2, False), (0.5, 0.9, True)]


def _lab_metrics(excess_cagr: float, sharpe_annual_excess: float) -> dict[str, float]:
    return {
        "excess_cagr_spy": excess_cagr,
        "sharpe_annual_excess_spy": sharpe_annual_excess,
        "sharpe_period_excess_spy": sharpe_annual_excess / math.sqrt(12),
        "skew_period_excess_spy": -0.1,
        "kurtosis_period_excess_spy": 3.5,
        "sharpe_period": 0.2,
        "cost_drag": 0.004,
        "turnover_annual": 5.5,
        "max_drawdown": -0.21,
        "n_periods": 40.0,
        "periods_per_year": 12.0,
    }


def _lab_register(
    conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str, top_fraction: float
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=slug,
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params={**LAB_FIXED, "strategy.top_fraction": top_fraction},
        in_sample_start=LAB_IN_SAMPLE_START,
        holdout_start=LAB_HOLDOUT_START,
        holdout_end=LAB_HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )


def _next_id(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> int:
    (value,) = conn.execute(  # type: ignore[misc]
        f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}"
    ).fetchone()
    return int(value)


def _lab_trial(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    code: str,
    *,
    metrics: dict[str, float],
    kind: str = "in_sample",
    stale: bool = False,
    red_flag: bool = False,
) -> int:
    """A finished `ok` trial inserted as rows, current unless `stale`."""
    trial_id = _next_id(conn, "trials", "trial_id")
    vintage = registry.data_vintage(conn, LAB_CUTOFF)
    insert_row(
        conn,
        "trials",
        {
            "trial_id": trial_id,
            "hypothesis_id": hypothesis_id,
            "kind": kind,
            "started_at": utc_now(),
            "start_session": LAB_IN_SAMPLE_START if kind == "in_sample" else LAB_HOLDOUT_START,
            "end_session": LAB_DEFAULT_END if kind == "in_sample" else LAB_HOLDOUT_END,
            "data_cutoff": LAB_CUTOFF,
            "store_max_ingested_at": None,
            "code_version": "abc",
            "code_dirty": False,
            "synthetic": False,
            "holdout_repeat": False,
            "run_by": "test",
            "detail_level": "summary",
            "data_vintage": datetime(2000, 1, 1, tzinfo=UTC) if stale else vintage,
            "code_tree_sha256": code,
        },
    )
    insert_row(
        conn,
        "trial_results",
        {
            "trial_id": trial_id,
            "finished_at": utc_now(),
            "status": "ok",
            "red_flag": red_flag,
            "dsr_excess": 0.999,
            "sharpe_unit": "annual",
        },
    )
    for metric, value in metrics.items():
        insert_row(
            conn,
            "trial_metrics",
            {
                "trial_id": trial_id,
                "series": "strategy",
                "cost_per_side_bps": LAB_BASE,
                "metric": metric,
                "value": value,
            },
        )
    return trial_id


def _lab_sweep(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    slug: str,
    values: list[float],
    fingerprint_offset: int,
) -> tuple[lab_registry.SweepRecord, list[registry.HypothesisRecord]]:
    sweep = lab_registry.register_sweep(
        conn,
        slug=slug,
        family="momentum",
        title=f"{slug} grid",
        doc_path=f"docs/sweeps/{slug}.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": values},
        n_variants=len(values),
        selection_statistic="sharpe_annual_excess_spy",
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=(-2.0, 2.0),
        promote_at_least=0.5,
        retire_below=0.0,
        in_sample_start=LAB_IN_SAMPLE_START,
        holdout_start=LAB_HOLDOUT_START,
        holdout_end=LAB_HOLDOUT_END,
        registered_by="owner",
        settings=settings,
    )
    records = []
    for index, value in enumerate(values, start=1):
        record = _lab_register(conn, settings, f"{slug}--r{sweep.sweep_id}-v{index}", value)
        fingerprint = f"{fingerprint_offset + index:064x}"
        lab_registry.write_sweep_variant(
            conn,
            sweep_id=sweep.sweep_id,
            variant_index=index,
            hypothesis_id=record.hypothesis_id,
            fingerprint=fingerprint,
            variant_params={"strategy.top_fraction": value},
        )
        lab_registry.write_fingerprint(conn, record.hypothesis_id, fingerprint)
        records.append(record)
    return sweep, records


def _decision(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    hypothesis_id: int | None,
    sweep_id: int,
    reason: str,
) -> int:
    decision_id = _next_id(conn, "owner_decisions", "decision_id")
    insert_row(
        conn,
        "owner_decisions",
        {
            "decision_id": decision_id,
            "made_at": utc_now(),
            "kind": kind,
            "hypothesis_id": hypothesis_id,
            "trial_id": None,
            "values_json": json.dumps({"sweep_id": sweep_id}),
            "reason": reason,
        },
    )
    return decision_id


class Lab:
    """What `_build_lab` registered."""

    def __init__(self) -> None:
        self.twin = 0
        self.holdout_trial = 0
        self.complete_slugs: list[str] = []
        self.stale_slug = ""
        self.promoted_slug = ""
        self.promotion = 0
        self.retirement = 0


def _build_lab(conn: duckdb.DuckDBPyConnection, settings: Settings, code: str) -> Lab:
    """On a lab-initialised store: H1's twin with the family rules (parent
    `profitability`), one `ok` in-sample trial and one holdout spend; a complete
    sweep `mom-grid` (four counted variants, one red-flagged) with a promotion; an
    incomplete sweep `mom-edge` (one counted variant, one stale) with a retirement."""
    lab = Lab()
    twin = _lab_register(conn, settings, "h1", 0.1)
    lab.twin = twin.hypothesis_id
    lab_registry.write_family_rules(
        conn,
        family="momentum",
        first_hypothesis_id=twin.hypothesis_id,
        parent_family="profitability",
        holdout_start=LAB_HOLDOUT_START,
        holdout_end=LAB_HOLDOUT_END,
        in_sample_start=LAB_IN_SAMPLE_START,
        fixed_params=LAB_FIXED,
        sr_star_seed_annual=None,
        settings=settings,
    )
    _lab_trial(conn, twin.hypothesis_id, code, metrics=_lab_metrics(0.01, 0.3))
    lab.holdout_trial = _lab_trial(
        conn, twin.hypothesis_id, code, metrics=_lab_metrics(0.02, 0.5), kind="holdout"
    )
    grid, records = _lab_sweep(conn, settings, "mom-grid", [0.15, 0.2, 0.25, 0.3], 0)
    for record, (excess, sharpe, flag) in zip(records, COMPLETE, strict=True):
        _lab_trial(
            conn,
            record.hypothesis_id,
            code,
            metrics=_lab_metrics(excess, sharpe),
            red_flag=flag,
        )
    lab.complete_slugs = [r.slug for r in records]
    promoted = _lab_register(conn, settings, "h2-promoted", 0.2)
    lab.promoted_slug = promoted.slug
    lab.promotion = _decision(conn, "promotion", promoted.hypothesis_id, grid.sweep_id, "won")
    edge, edge_records = _lab_sweep(conn, settings, "mom-edge", [0.35, 0.4], 100)
    _lab_trial(conn, edge_records[0].hypothesis_id, code, metrics=_lab_metrics(0.0, 0.1))
    _lab_trial(
        conn, edge_records[1].hypothesis_id, code, metrics=_lab_metrics(0.0, 0.2), stale=True
    )
    lab.stale_slug = edge_records[1].slug
    lab.retirement = _decision(conn, "sweep_retired", None, edge.sweep_id, "edge is noise")
    return lab


def test_lab_view_shows_the_families_card(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    lab = _build_lab(lab_store, settings, LAB_CODE)

    view = trials_page.load_lab_view(lab_store, settings, code_vintage=LAB_CODE)

    [card] = [c for c in view.families if c.family == "momentum"]
    n = results.family_n(lab_store, "momentum")
    sharpes = registry.family_sharpes(lab_store, "momentum")
    variance = sharpes.variance("excess_spy")
    assert variance is not None
    assert card.n == n == 7  # the twin's in-sample trial and six variant trials
    assert card.sharpe_variance_annual_excess == pytest.approx(variance)
    assert card.sharpe_variance_annual_raw == pytest.approx(sharpes.variance("raw"))
    assert card.sr_star_annual == pytest.approx(expected_max_sharpe(n, variance))
    assert card.declared_count == 6
    # the stale variant of mom-edge has no counted trial: N_declared = N + 1.
    assert card.n_at_declared_count == n + 1
    assert card.sr_star_annual_at_declared_count == pytest.approx(
        expected_max_sharpe(n + 1, variance)
    )
    assert card.rules is not None and card.rules.parent_family == "profitability"
    assert card.parent_n == results.family_n(lab_store, "profitability") == 0
    assert [s.trial_id for s in card.holdout_spends] == [lab.holdout_trial]
    assert card.max_holdout_spends == settings.lab.max_family_holdout_spends
    assert [d.decision_id for d in card.promotions] == [lab.promotion]
    assert card.max_promotions == settings.lab.max_family_promotions
    assert card.sr_star_high_water_annual == pytest.approx(
        lab_registry.family_sr_star_high_water_mark(
            lab_store, "momentum", n_trials_today=n, sharpe_variance_annual_today=variance
        )
    )


def test_lab_view_sweeps_table_argmax_only_for_a_complete_sweep(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    lab = _build_lab(lab_store, settings, LAB_CODE)

    view = trials_page.load_lab_view(lab_store, settings, code_vintage=LAB_CODE)

    by_slug = {s.report.slug: s for s in view.sweeps}
    assert list(by_slug) == ["mom-edge", "mom-grid"]  # newest registration first
    grid, edge = by_slug["mom-grid"], by_slug["mom-edge"]
    assert grid.report.complete
    assert grid.report.verdicts is not None
    assert grid.report.verdicts.argmax.slug == lab.complete_slugs[1]
    assert [d.decision_id for d in grid.decisions] == [lab.promotion]
    assert not edge.report.complete
    assert edge.report.verdicts is None
    assert edge.report.stale == (lab.stale_slug,)
    assert [d.decision_id for d in edge.decisions] == [lab.retirement]

    table = trials_page._sweeps_table(view.sweeps)
    assert table.width <= 8  # the design standard's compact table
    rows = {row["sweep"]: row for row in table.to_dicts()}
    grid_row, edge_row = rows["mom-grid (r1)"], rows["mom-edge (r2)"]
    assert grid_row["declared / run"] == "4 / 4"
    assert grid_row["state"] == "complete; 0 terminal-failed"
    assert grid_row["argmax"].startswith(f"{lab.complete_slugs[1]} (v2)")
    assert "promote_at_least" in grid_row["argmax"] and "retire_below" in grid_row["argmax"]
    assert grid_row["red flags"] == 1
    assert grid_row["dsr_excess > 0.5"] == pytest.approx(grid.report.dsr_excess_share_above)
    d = grid.report.distribution
    assert d is not None
    assert grid_row["q1 / median / q3"] == (
        f"sharpe_annual_excess_spy: {d.q1:.4f} / {d.median:.4f} / {d.q3:.4f}"
    )
    assert grid_row["promotion / retirement"] == f"promotion {lab.promotion}: h2-promoted"
    assert edge_row["declared / run"] == "2 / 2"
    assert edge_row["state"] == "incomplete (stale); 1 stale, 0 terminal-failed"
    assert edge_row["argmax"] == "-"
    assert edge_row["promotion / retirement"] == f"retired {lab.retirement}: edge is noise"


def test_lab_view_on_a_family_without_rules_uses_the_live_caps(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    twin = _lab_register(lab_store, settings, "h1", 0.1)
    _lab_trial(lab_store, twin.hypothesis_id, LAB_CODE, metrics=_lab_metrics(0.01, 0.3))

    view = trials_page.load_lab_view(lab_store, settings, code_vintage=LAB_CODE)

    [card] = view.families
    assert card.rules is None and card.parent_n is None
    assert card.sr_star_high_water_annual is None
    assert card.max_holdout_spends == settings.lab.max_family_holdout_spends
    assert card.max_promotions == settings.lab.max_family_promotions
    assert card.declared_count == 0
    assert card.n_at_declared_count == card.n == 1
    assert view.sweeps == ()


def test_lab_view_refuses_a_store_without_the_lab_tables(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    with pytest.raises(LabNotInitialised):
        trials_page.load_lab_view(fixture_store, settings, code_vintage=LAB_CODE)


class _Recording:
    """A connection that records every statement and its parameters."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self.statements: list[tuple[str, Any]] = []

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.statements.append((sql, args[0] if args else None))
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_lab_view_over_ten_thousand_trials_recomputes_with_one_query_per_basis(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    conn = lab_store
    twin = _lab_register(conn, settings, "h1", 0.1)
    lab_registry.write_family_rules(
        conn,
        family="momentum",
        first_hypothesis_id=twin.hypothesis_id,
        parent_family=None,
        holdout_start=LAB_HOLDOUT_START,
        holdout_end=LAB_HOLDOUT_END,
        in_sample_start=LAB_IN_SAMPLE_START,
        fixed_params=LAB_FIXED,
        sr_star_seed_annual=None,
        settings=settings,
    )
    _sweep, records = _lab_sweep(conn, settings, "mom-grid", [0.15, 0.2, 0.25, 0.3], 0)
    ids = [twin.hypothesis_id] + [r.hypothesis_id for r in records]
    start = _next_id(conn, "trials", "trial_id")
    conn.execute(
        "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
        "end_session, data_cutoff, code_version, code_dirty, synthetic, holdout_repeat, "
        "run_by, detail_level, data_vintage, code_tree_sha256) "
        "SELECT $start + i, list_extract($ids, (i % 5) + 1), 'in_sample', now(), $s, $e, "
        "$cutoff, 'abc', false, false, false, 'test', 'summary', $vintage, $code "
        "FROM range(10000) r(i)",
        {
            "start": start,
            "ids": ids,
            "s": LAB_IN_SAMPLE_START,
            "e": LAB_DEFAULT_END,
            "cutoff": LAB_CUTOFF,
            "vintage": registry.data_vintage(conn, LAB_CUTOFF),
            "code": LAB_CODE,
        },
    )
    conn.execute(
        "INSERT INTO trial_results (trial_id, finished_at, status, red_flag, sharpe_unit) "
        "SELECT $start + i, now(), 'ok', false, 'annual' FROM range(10000) r(i)",
        {"start": start},
    )
    base_metrics = _lab_metrics(0.01, 0.5)
    conn.execute(
        "INSERT INTO trial_metrics (trial_id, series, cost_per_side_bps, metric, value) "
        "SELECT $start + i, 'strategy', $base, m.metric, "
        "m.value + CASE WHEN m.metric LIKE 'sharpe%' THEN (i % 97) / 1000.0 ELSE 0 END "
        "FROM range(10000) r(i), "
        "(SELECT UNNEST($names::VARCHAR[]) AS metric, UNNEST($values::DOUBLE[]) AS value) m",
        {
            "start": start,
            "base": LAB_BASE,
            "names": list(base_metrics),
            "values": list(base_metrics.values()),
        },
    )
    recording = _Recording(conn)

    began = time.perf_counter()
    view = trials_page.load_lab_view(
        recording,  # type: ignore[arg-type]
        settings,
        code_vintage=LAB_CODE,
    )
    elapsed = time.perf_counter() - began

    [card] = view.families
    assert card.n == results.family_n(conn, "momentum") == 10_000
    [sweep] = view.sweeps
    assert sweep.report.complete
    # `lab status`'s row tally of every registry table counts `trial_metrics` once; it
    # reads no metric value.
    metric_reads = [
        (sql, p)
        for sql, p in recording.statements
        if "trial_metrics" in sql and "COUNT(*) AS n FROM" not in sql
    ]
    by_basis = Counter(
        p["metric"] for _sql, p in metric_reads if isinstance(p, dict) and "metric" in p
    )
    # One set-based query per basis per `family_sharpes` read: the card's (through
    # `lab_status`) and the sweep report's; plus the report's one read of its counted
    # trials' metrics. Independent of the 10,000 trials.
    assert by_basis == {"sharpe_period": 2, "sharpe_period_excess_spy": 2}
    assert len(metric_reads) == 5
    assert elapsed < RENDER_SECONDS_LIMIT


def _lab_file_store(tmp_path: Path) -> tuple[Path, Lab]:
    """A temp-file store with the lab tables and `_build_lab`'s rows, its trials at
    the checkout's code vintage so the page (which hashes the checkout) reads them
    current."""
    store_path = tmp_path / "lab.duckdb"
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    code = registry.code_tree_sha256()
    assert code is not None
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        schema.init_schema(conn)
        lab_schema.apply_lab_schema(conn)
        # One fact known before the cutoff, so the trials record a data vintage: a
        # trial with none is never current (`lab_queries`, the one classifier, #1221).
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "SEC_LAB",
                "session": date(2023, 12, 28),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": datetime(2023, 12, 28, 21, 0, tzinfo=UTC),
                "ingested_at": datetime(2023, 12, 28, 22, 0, tzinfo=UTC),
                "source": "test",
                "provenance": "bar",
            },
        )
        lab = _build_lab(conn, seed_settings, code)
    return store_path, lab


def test_render_shows_the_families_card_and_the_sweeps_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path, lab = _lab_file_store(tmp_path)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    text = _text(at)
    for heading in ("Families", "Sweeps", "Trials", "Owner decisions"):
        assert heading in text, heading
    assert "Family `momentum`" in text
    assert "parent family `profitability`, N 0" in text
    assert "declared count 6" in text
    assert "Holdout spends: 1 of 3: h1 (trial " in text
    assert "Promotions: 1 of 2: h2-promoted" in text
    assert "Family rules: holdout 2024-01-02 to 2026-09-30" in text
    labels = [m.label for m in at.metric]
    for label in ("N today", "V excess (annual)", "SR*_annual", "SR* high-water mark"):
        assert label in labels, label
    assert at.metric[labels.index("N today")].value == "7"

    sweeps = at.dataframe[0].value
    by_sweep = dict(zip(sweeps["sweep"], sweeps["argmax"], strict=True))
    assert by_sweep["mom-grid (r1)"].startswith(f"{lab.complete_slugs[1]} (v2)")
    assert by_sweep["mom-edge (r2)"] == "-"
    states = dict(zip(sweeps["sweep"], sweeps["state"], strict=True))
    assert states["mom-edge (r2)"] == "incomplete (stale); 1 stale, 0 terminal-failed"
    # The trial table still follows.
    assert lab.holdout_trial in [int(i) for i in at.dataframe[1].value["trial"].to_list()]
    assert not at.button


def test_render_lab_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    store_path, _lab = _lab_file_store(tmp_path)
    at = _app(monkeypatch, store_path)
    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    at.checkbox[0].check().run()
    assert not at.exception
    assert opened == [True]


def test_render_lab_not_initialised_on_a_store_without_the_lab_tables(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    text = _text(at).lower()
    assert "strategy lab not initialised" in text
    assert "trials" in text  # the trial table renders as before


def test_one_unreadable_sweep_loses_only_its_row(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _build_lab(lab_store, settings, LAB_CODE)
    # mom-edge's counted variant loses a metric the report needs.
    lab_store.execute(
        "DELETE FROM trial_metrics WHERE metric = 'skew_period_excess_spy' AND trial_id IN (SELECT "
        "t.trial_id FROM trials t JOIN hypotheses h USING (hypothesis_id) "
        "WHERE h.slug LIKE 'mom-edge%')"
    )

    view = trials_page.load_lab_view(lab_store, settings, code_vintage=LAB_CODE)

    assert [s.report.slug for s in view.sweeps] == ["mom-grid"]
    [error] = view.errors
    assert error.startswith("sweep mom-edge:")
    assert [c.family for c in view.families] == ["momentum"]


def test_a_promotion_naming_only_a_trial_counts_for_its_family(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    lab = _build_lab(lab_store, settings, LAB_CODE)
    decision_id = _next_id(lab_store, "owner_decisions", "decision_id")
    insert_row(
        lab_store,
        "owner_decisions",
        {
            "decision_id": decision_id,
            "made_at": utc_now(),
            "kind": "promotion",
            "hypothesis_id": None,
            "trial_id": lab.holdout_trial,
            "values_json": json.dumps({"sweep_id": 1}),
            "reason": "named by its trial",
        },
    )

    view = trials_page.load_lab_view(lab_store, settings, code_vintage=LAB_CODE)

    [card] = view.families
    assert [d.decision_id for d in card.promotions] == [lab.promotion, decision_id]
    assert card.promotions[1].slug == "h1"
