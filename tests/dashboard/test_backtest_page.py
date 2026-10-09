"""Tests for the backtest page (Phase 3 T43, spec req 17).

The page is driven headless through `streamlit.testing.v1.AppTest` on the
shell (`dashboard/app.py`), with the store path set through `STORE__PATH`
exactly as `tests/dashboard/test_app.py` does. The store is a temp file
seeded through `store.registry`, so every row the page reads went through
the registry API. `load_trial_view` is also exercised directly: it is the
page's only reader and needs no Streamlit.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.backtest.metrics import METRIC_KEYS, deflated_sharpe
from tradepartner.config import Settings
from tradepartner.dashboard import backtest_page, theme
from tradepartner.research.experiment import hash_file, parse_experiment_file
from tradepartner.store import lab_registry, lab_schema, registry, research, schema
from tradepartner.store.db import insert_row, open_for_write

_APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "dashboard" / "app.py"
)

BASE = 15.0
LADDER = (15.0, 30.0)
SESSIONS = (date(2020, 1, 31), date(2020, 2, 28), date(2020, 3, 31))
FILLS = (date(2020, 2, 3), date(2020, 3, 2), date(2020, 4, 1))


def _metrics(sharpe: float, excess: float) -> dict[str, float | None]:
    return {
        "cagr": 0.08,
        "vol_annual": 0.15,
        "sharpe_period": sharpe,
        "sharpe_annual": sharpe * math.sqrt(12),
        "sharpe_period_excess_spy": excess,
        "sharpe_annual_excess_spy": excess * math.sqrt(12),
        "max_drawdown": -0.2,
        "turnover_period": 0.1,
        "turnover_annual": 1.2,
        "cost_drag": 0.002,
        "excess_cagr_spy": 0.01,
        "excess_cagr_mtum": -0.005,
        "tracking_error_spy": 0.05,
        "tracking_error_mtum": 0.04,
        "skew_period": -0.3,
        "kurtosis_period": 3.5,
        "skew_period_excess_spy": 0.1,
        "kurtosis_period_excess_spy": 3.2,
        "n_periods": 36.0,
        "periods_per_year": 12.0,
    }


def _hypothesis(
    conn: duckdb.DuckDBPyConnection, seed_settings: Settings, slug: str, top: float
) -> int:
    record = registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": BASE, "strategy.top_fraction": top},
        in_sample_start=date(2017, 1, 31),
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="owner",
        settings=seed_settings,
    )
    return record.hypothesis_id


def _open(
    conn: duckdb.DuckDBPyConnection,
    seed_settings: Settings,
    hypothesis_id: int,
    **kwargs: Any,
) -> registry.TrialHandle:
    values: dict[str, Any] = {
        "kind": "in_sample",
        "start_session": SESSIONS[0],
        "end_session": SESSIONS[-1],
        "data_cutoff": datetime(2020, 3, 31, 20, tzinfo=UTC),
        "synthetic": False,
        "run_by": "owner",
    }
    values.update(kwargs)
    return registry.open_trial(conn, hypothesis_id=hypothesis_id, settings=seed_settings, **values)


def _ok_trial(
    conn: duckdb.DuckDBPyConnection,
    seed_settings: Settings,
    hypothesis_id: int,
    sharpe: float,
    excess: float,
    *,
    details: bool = False,
    **kwargs: Any,
) -> registry.TrialHandle:
    handle = _open(conn, seed_settings, hypothesis_id, **kwargs)
    rows = [
        registry.MetricRow(series, cost, key, value)
        for series in ("strategy", "SPY", "MTUM")
        for cost in LADDER
        for key, value in _metrics(sharpe, excess).items()
    ]
    registry.write_metrics(conn, handle, rows)
    if details:
        registry.write_equity(
            conn,
            handle,
            [
                registry.EquityRow(series, cost, day, 100.0 * (1 + i / 10) * scale, None)
                for series, scale in (("strategy", 1.0), ("SPY", 0.9), ("MTUM", 1.1))
                for cost in LADDER
                for i, day in enumerate(SESSIONS)
            ],
        )
        registry.write_rebalances(
            conn,
            handle,
            [
                registry.RebalanceRow(
                    cost_per_side_bps=cost,
                    session=day,
                    fill_session=fill,
                    n_universe=480 + i,
                    n_static_listings=17 + i,
                    n_targets=48,
                    turnover=0.25 + i / 100,
                    cost_paid=12.5 + i,
                    gap_count_share=0.031 + i / 1000,
                    gap_size_share=0.004,
                    n_missing_fill=0,
                    n_delisting_exits=1,
                    n_stale_exits=0,
                    counts={"n_excluded_no_history": 3},
                    n_dropped_dividends=2,
                    n_late_dividends=5 + i,
                )
                for cost in LADDER
                for i, (day, fill) in enumerate(zip(SESSIONS, FILLS, strict=True))
            ],
        )
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
            red_flag=kwargs.get("kind") == "holdout",
            gap_max_count_share=0.033,
            gap_max_size_share=0.004,
        ),
    )
    return handle


class Seeded:
    """Trial ids of the seeded store, by role."""

    def __init__(self) -> None:
        self.detailed = 0
        self.refused = 0
        self.unfinished = 0
        self.holdout = 0
        self.synthetic = 0


def _seed(store_path: Path, tmp_path: Path) -> Seeded:
    """A temp store with a detailed `ok` trial (first, so later trials change
    today's N and V), two more `ok` trials, a refused one, an unfinished one,
    a red-flagged holdout repeat and a synthetic one."""
    store_settings = Settings(_env_file=None, store={"path": str(store_path)})
    # Registry calls get a different store path so synthetic trials are allowed here.
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    seeded = Seeded()
    with open_for_write(store_settings) as conn:
        schema.init_schema(conn)
        h1 = _hypothesis(conn, seed_settings, "h1-momentum-12-1", 0.1)
        h2 = _hypothesis(conn, seed_settings, "h2-momentum-top-20", 0.2)
        seeded.detailed = _ok_trial(conn, seed_settings, h1, 0.20, 0.05, details=True).trial_id
        _ok_trial(conn, seed_settings, h2, 0.10, 0.02)
        _ok_trial(conn, seed_settings, h2, 0.30, 0.09, start_session=date(2018, 1, 31))
        refused = _open(conn, seed_settings, h1, kind="holdout", start_session=date(2023, 1, 31))
        registry.close_trial(conn, refused, "refused_holdout", "window touches the holdout")
        seeded.refused = refused.trial_id
        seeded.unfinished = _open(conn, seed_settings, h1).trial_id
        holdout = _ok_trial(
            conn,
            seed_settings,
            h1,
            0.25,
            0.07,
            kind="holdout",
            holdout_repeat=True,
            holdout_reason="owner spends it",
        )
        seeded.holdout = holdout.trial_id
        seeded.synthetic = _ok_trial(conn, seed_settings, h1, 0.5, 0.1, synthetic=True).trial_id
    return seeded


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.sidebar.radio[0].set_value("Backtest").run()
    return at


def _text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.subheader]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


def _pick(at: AppTest, trial_id: int) -> AppTest:
    [picker] = at.selectbox
    assert any(o.startswith(f"#{trial_id} ") for o in picker.options)
    picker.set_value(trial_id).run()
    return at


@pytest.fixture
def seeded_store(tmp_path: Path) -> tuple[Path, Seeded]:
    store_path = tmp_path / "store.duckdb"
    return store_path, _seed(store_path, tmp_path)


# --- load_trial_view (pure reader) -----------------------------------------


def test_recomputed_dsr_uses_todays_n_and_v(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, seeded = seeded_store
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        view = backtest_page.load_trial_view(conn, seeded.detailed)
    finally:
        conn.close()

    # Today: three ok non-synthetic in-sample trials, three distinct pairs, each
    # Sharpe annualised with its trial's periods per year (strategy-lab spec req 9).
    r12 = math.sqrt(12)
    expected_raw = deflated_sharpe(
        _metrics(0.20, 0.05),
        "raw",
        n_trials=3,
        pair_sharpes=[0.20 * r12, 0.10 * r12, 0.30 * r12],
        periods_per_year=12,
    )
    expected_excess = deflated_sharpe(
        _metrics(0.20, 0.05),
        "excess_spy",
        n_trials=3,
        pair_sharpes=[0.05 * r12, 0.02 * r12, 0.09 * r12],
        periods_per_year=12,
    )
    raw, excess = view.dsr_rows
    assert raw.basis == "raw"
    assert raw.stored_n == 1
    assert raw.stored_dsr == pytest.approx(0.9)
    assert raw.today == expected_raw
    assert excess.today == expected_excess
    assert raw.today is not None and raw.today.sharpe_variance == pytest.approx(12 * 0.01)
    assert excess.stored_dsr == pytest.approx(0.6)


def _stored(conn: duckdb.DuckDBPyConnection, trial_id: int, unit: str | None) -> None:
    """Give an `ok` trial stored statistics V = 0.01 and SR* = 0.19 in `unit`. A
    pre-version-15 row has NULL there; only a test rewrites a result row."""
    conn.execute(
        "UPDATE trial_results SET sharpe_variance = 0.01, sr_star = 0.19, "
        "sharpe_variance_excess = 0.004, sr_star_excess = 0.12, sharpe_unit = ? "
        "WHERE trial_id = ?",
        [unit, trial_id],
    )


@pytest.mark.parametrize(
    ("unit", "scale_v", "scale_sr"),
    [(None, 12.0, math.sqrt(12)), ("monthly", 12.0, math.sqrt(12)), ("annual", 1.0, 1.0)],
)
def test_stored_v_and_sr_star_are_shown_in_annual_units(
    seeded_store: tuple[Path, Seeded], unit: str | None, scale_v: float, scale_sr: float
) -> None:
    """A pre-lab row (`sharpe_unit` NULL, read as monthly) is converted to annual
    beside today's values; an `annual` row is shown as stored (req 9)."""
    store_path, seeded = seeded_store
    with duckdb.connect(str(store_path)) as conn:
        _stored(conn, seeded.detailed, unit)
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        raw, excess = backtest_page.load_trial_view(conn, seeded.detailed).dsr_rows
    finally:
        conn.close()
    assert raw.stored_v == pytest.approx(0.01 * scale_v, rel=1e-12)
    assert raw.stored_sr_star == pytest.approx(0.19 * scale_sr, rel=1e-12)
    assert excess.stored_v == pytest.approx(0.004 * scale_v, rel=1e-12)
    assert excess.stored_sr_star == pytest.approx(0.12 * scale_sr, rel=1e-12)


def test_annual_stored_keeps_none() -> None:
    assert backtest_page.annual_stored(None, 0.0, None) == (None, 0.0)
    assert backtest_page.annual_stored(None, None, "annual") == (None, None)


def test_page_reads_only_the_period_keys() -> None:
    """Text check (strategy-lab spec, "Metrics" acceptance): the page reads no
    `*_monthly` key and no `n_months`; the metrics table lists `METRIC_KEYS`."""
    source = Path(backtest_page.__file__).read_text(encoding="utf-8")
    assert "_monthly" not in source and "n_months" not in source
    assert not any("monthly" in key for key in METRIC_KEYS)


def test_view_reads_base_level_rows(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, seeded = seeded_store
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        view = backtest_page.load_trial_view(conn, seeded.detailed)
    finally:
        conn.close()

    assert view.base_cost == BASE
    assert {row["series"] for row in view.equity} == {"strategy", "SPY", "MTUM"}
    assert len(view.equity) == 3 * len(SESSIONS)
    assert [row["n_universe"] for row in view.rebalances] == [480, 481, 482]
    assert sorted({row["cost_per_side_bps"] for row in view.metrics}) == list(LADDER)
    strategy_dd = [row["drawdown"] for row in view.drawdowns if row["series"] == "strategy"]
    assert strategy_dd == [0.0, 0.0, 0.0]


def test_drawdowns_from_equity() -> None:
    rows = [
        {"series": "strategy", "session": SESSIONS[i], "equity": value}
        for i, value in enumerate((100.0, 120.0, 90.0))
    ]
    assert [r["drawdown"] for r in backtest_page.drawdowns(rows)] == pytest.approx(
        [0.0, 0.0, -0.25]
    )


# --- headless render ---------------------------------------------------------


def test_page_is_in_the_shell_navigation(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, _ = seeded_store
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert at.sidebar.radio[0].options == [
        "Data health",
        "Backtest",
        "Trial registry",
        "Research",
        "Operations",
        "Override",
    ]


def test_render_detailed_trial_shows_every_element(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.detailed)
    assert not at.exception
    text = _text(at)

    for heading in (
        "Equity",
        "Drawdowns",
        "Turnover and costs",
        "Metrics per cost level",
        "Deflated Sharpe",
        "Per rebalance",
        "Holdout spends",
    ):
        assert heading in text, heading
    assert "log" in text.lower()
    assert "N at run time" in text

    charts = at.get("vega_lite_chart")
    assert len(charts) == 4  # equity, drawdowns, turnover, costs
    titles = [json.loads(c.proto.spec)["encoding"]["y"]["title"] for c in charts[2:]]
    assert titles == ["turnover per period (one-sided)", "cost paid per period ($)"]
    equity_spec = json.loads(charts[0].proto.spec)
    assert equity_spec["encoding"]["y"]["scale"] == {"type": "log"}
    assert equity_spec["encoding"]["color"]["field"] == "series"

    dsr = next(df.value for df in at.dataframe if "stored DSR (N at run time)" in df.value.columns)
    assert set(dsr["basis"]) == {"raw", "excess_spy"}
    for column in ("stored N", "stored V", "stored SR*", "today N", "today V", "today SR*"):
        assert column in dsr.columns
    assert list(dsr["today N"]) == [3, 3]

    per_rebalance = next(df.value for df in at.dataframe if "n_universe" in df.value.columns)
    for column in ("gap_count_share", "gap_size_share", "n_static_listings", "n_late_dividends"):
        assert column in per_rebalance.columns
    assert list(per_rebalance["n_universe"]) == [480, 481, 482]

    metrics = next(df.value for df in at.dataframe if "metric" in df.value.columns)
    assert {"strategy @ 15.0 bps", "strategy @ 30.0 bps"} <= set(metrics.columns)

    assert "in_sample" in text
    spends = next(df.value for df in at.dataframe if "holdout_reason" in df.value.columns)
    assert set(spends["trial_id"]) == {seeded.refused, seeded.holdout}


def test_render_family_with_no_holdout_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path = tmp_path / "one.duckdb"
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        schema.init_schema(conn)
        h1 = _hypothesis(conn, seed_settings, "h1-momentum-12-1", 0.1)
        _ok_trial(conn, seed_settings, h1, 0.2, 0.05, details=True)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "holdout not spent in family `momentum`" in _text(at).lower()
    dsr = next(df.value for df in at.dataframe if "today DSR" in df.value.columns)
    assert list(dsr["today label"]) == ["psr", "psr"]


def test_render_refused_trial_shows_state_and_message(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.refused)
    assert not at.exception
    text = _text(at)
    assert "refused_holdout" in text
    assert "window touches the holdout" in text


def test_render_unfinished_trial(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.unfinished)
    assert not at.exception
    assert "unfinished" in _text(at)


def test_render_holdout_repeat_red_flag_and_spends(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.holdout)
    assert not at.exception
    text = _text(at).lower()
    assert "holdout repeat" in text
    assert "red flag" in text
    spends = next(df.value for df in at.dataframe if "holdout_reason" in df.value.columns)
    assert set(spends["trial_id"]) == {seeded.refused, seeded.holdout}


def test_picked_trial_survives_a_new_trial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seeded_store: tuple[Path, Seeded]
) -> None:
    """A run recorded on the CLI while the page is open must not move the
    picker to another trial (the option list and labels change)."""
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.unfinished)
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        h1 = registry.get_hypothesis(conn, "h1-momentum-12-1")
        _ok_trial(conn, seed_settings, h1.hypothesis_id, 0.15, 0.03)
    at.run()
    assert not at.exception
    assert at.selectbox[0].value == seeded.unfinished
    assert f"Trial #{seeded.unfinished}:" in _text(at)


def test_render_synthetic_trial_is_labelled(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.synthetic)
    assert not at.exception
    assert "synthetic" in _text(at).lower()


def test_page_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    at = _app(monkeypatch, store_path)
    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    _pick(at, seeded.detailed)
    assert not at.exception
    assert opened == [True]


def test_render_empty_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "empty.duckdb"
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        schema.init_schema(conn)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "no trials" in _text(at).lower()
    assert not at.selectbox


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
    assert not at.selectbox


def test_header_shows_as_of_and_last_updated(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    text = _text(at)
    assert "as of" in text and "last updated" in text


def test_no_colour_literal_in_page_code() -> None:
    source = Path(backtest_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


def test_equity_chart_accents_the_strategy_and_mutes_benchmarks(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _pick(_app(monkeypatch, store_path), seeded.detailed)
    spec = json.loads(at.get("vega_lite_chart")[0].proto.spec)
    palette = theme.PALETTES["light"]
    color = spec["encoding"]["color"]["scale"]
    assert color["domain"][0] == "strategy"
    assert color["range"][0] == palette.accent
    assert set(color["range"][1:]) == {palette.text_secondary}
    dash = spec["encoding"]["strokeDash"]["scale"]["range"]
    assert dash[0] == [] and all(d for d in dash[1:])
    assert spec["config"]["axis"]["gridColor"] == palette.border


# --- today's N split: backtest trials and research runs (T83b) ------------------------

_EXPERIMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "experiments"


def _research_run(store_path: Path, tmp_path: Path, configurations: int) -> None:
    """One counted research run in family `momentum` (an `ok`, non-synthetic return
    run outside the seeded holdout) evaluating `configurations` configurations."""
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    parsed = replace(
        parse_experiment_file(
            _EXPERIMENTS / "e1h-demand-deterioration-revenue.md",
            _EXPERIMENTS,
            settings=seed_settings,
        ),
        slug="r0",
        params_sha256="e" * 64,
        kind="return",
        stage=6,
        touches_returns=True,
        family="momentum",
        confirmatory=False,
        window_start=date(2018, 1, 1),
        window_end=date(2022, 12, 31),
        splits=("full",),
        dataset_name="panel",
        budget_runs=10,
        budget_configurations=20,
    )
    dates = [date(2019, 6, 28), date(2021, 6, 30)]
    csv = tmp_path / "panel.csv"
    csv.write_text(
        "event_date,value\n" + "".join(f"{d.isoformat()},{i}\n" for i, d in enumerate(dates)),
        encoding="utf-8",
    )
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        research.register_experiment(conn, parsed, "owner")
        dataset = research.register_dataset(
            conn,
            name="panel",
            version="v1",
            path=str(csv),
            sha256=hash_file(csv),
            event_start=min(dates),
            event_end=max(dates),
            n_rows=len(dates),
            event_column="event_date",
            repo_dir=tmp_path,
        )
        handle = research.open_run(
            conn,
            "r0",
            dataset.dataset_id,
            "full",
            {"seed": 1},
            "owner",
            settings=seed_settings,
            repo_dir=tmp_path,
            configurations=configurations,
        )
        assert handle.refusal is None, handle.message
        outcome = research.write_result(
            conn,
            handle,
            primary_value=-0.5,
            primary_ci_low=-0.9,
            primary_ci_high=-0.1,
            n_observations=400,
            n_clusters=40,
            n_configurations=configurations,
            artifact_sha256="a" * 64,
            artifact_path="/tmp/report.html",
        )
        assert outcome == "ok"


def _pre_migration(store_path: Path) -> None:
    """Turn the seeded store into one a read-only connection sees before version 12:
    no research table, no `trial_results.n_research`, and version 11 its latest."""
    with duckdb.connect(str(store_path)) as conn:
        for table in reversed(schema.RESEARCH_TABLE_NAMES):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("ALTER TABLE trial_results DROP COLUMN n_research")
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version VALUES (11, now())")


def test_todays_n_is_split_into_backtest_trials_and_research_runs(
    seeded_store: tuple[Path, Seeded], tmp_path: Path
) -> None:
    """Today's N = the three counted trials + the research run's three configurations;
    V is the three trials' pairs alone (research-registry spec req 9)."""
    store_path, seeded = seeded_store
    _research_run(store_path, tmp_path, configurations=3)
    with duckdb.connect(str(store_path)) as conn:
        conn.execute(
            "UPDATE trial_results SET n_research = 2 WHERE trial_id = ?", [seeded.detailed]
        )
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        raw, excess = backtest_page.load_trial_view(conn, seeded.detailed).dsr_rows
    finally:
        conn.close()
    r12 = math.sqrt(12)
    expected = deflated_sharpe(
        _metrics(0.20, 0.05),
        "raw",
        n_trials=6,
        pair_sharpes=[0.20 * r12, 0.10 * r12, 0.30 * r12],
        periods_per_year=12,
    )
    assert raw.today == expected
    for row in (raw, excess):
        assert (row.today_n_backtest, row.today_n_research) == (3, 3)
        assert row.today is not None and row.today.n_trials == 6
        assert row.stored_n_research == 2


def test_render_shows_the_n_split(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded], tmp_path: Path
) -> None:
    store_path, seeded = seeded_store
    _research_run(store_path, tmp_path, configurations=3)
    at = _pick(_app(monkeypatch, store_path), seeded.detailed)
    assert not at.exception
    dsr = next(df.value for df in at.dataframe if "stored DSR (N at run time)" in df.value.columns)
    assert list(dsr["today N"]) == [6, 6]
    assert list(dsr["today N backtest"]) == [3, 3]
    assert list(dsr["today N research"]) == [3, 3]
    assert "stored N research" in dsr.columns
    assert "research runs" in _text(at)


def test_a_pre_migration_store_renders_with_n_research_blank(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    """A read-only open of a store before the research registry: the trial renders,
    today's N is the backtest count, and both research cells are blank."""
    store_path, seeded = seeded_store
    _pre_migration(store_path)
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        raw, _ = backtest_page.load_trial_view(conn, seeded.detailed).dsr_rows
    finally:
        conn.close()
    assert (raw.stored_n_research, raw.today_n_research, raw.today_n_backtest) == (None, None, 3)
    assert raw.today is not None and raw.today.n_trials == 3
    at = _pick(_app(monkeypatch, store_path), seeded.detailed)
    assert not at.exception
    dsr = next(df.value for df in at.dataframe if "stored DSR (N at run time)" in df.value.columns)
    assert list(dsr["today N"]) == [3, 3]
    assert dsr["today N research"].isna().all()
    assert dsr["stored N research"].isna().all()


# --- detail level and the spend cap (strategy-lab spec req 12, amendment 12; T112) -----


def _detail_store(tmp_path: Path, *, lab: bool) -> tuple[Path, int, int]:
    """A store with one `full` trial carrying weights at two fill sessions and one
    `summary` trial (detail rows written, then `detail_level` set as a sweep variant's
    is), plus one holdout spend; with `lab`, the lab tables and the family rules."""
    store_path = tmp_path / "detail.duckdb"
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        schema.init_schema(conn)
        if lab:
            lab_schema.apply_lab_schema(conn)
        h1 = _hypothesis(conn, seed_settings, "h1-momentum-12-1", 0.1)
        full = _ok_trial(conn, seed_settings, h1, 0.20, 0.05, details=True)
        # The result row is written, so the weights go in as rows (the writer refuses
        # a closed trial).
        for fill, security, weight, price, shares in (
            (FILLS[0], "SEC-A", 0.5, 10.0, 5.0),
            (FILLS[1], "SEC-A", 0.4, 11.0, 4.0),
            (FILLS[1], "SEC-B", 0.6, 20.0, 3.0),
        ):
            insert_row(
                conn,
                "trial_weights",
                {
                    "trial_id": full.trial_id,
                    "fill_session": fill,
                    "security_id": security,
                    "target_weight": weight,
                    "fill_price": price,
                    "shares": shares,
                },
            )
        summary = _ok_trial(conn, seed_settings, h1, 0.10, 0.02, details=True)
        conn.execute(
            "UPDATE trials SET detail_level = 'summary' WHERE trial_id = ?", [summary.trial_id]
        )
        _ok_trial(conn, seed_settings, h1, 0.25, 0.07, kind="holdout", holdout_reason="spend")
        if lab:
            lab_registry.write_family_rules(
                conn,
                family="momentum",
                first_hypothesis_id=h1,
                parent_family=None,
                holdout_start=date(2023, 1, 1),
                holdout_end=date(2025, 12, 31),
                in_sample_start=date(2017, 1, 31),
                fixed_params={"costs.per_side_bps": BASE},
                sr_star_seed_annual=None,
                settings=seed_settings.model_copy(
                    update={
                        "lab": seed_settings.lab.model_copy(update={"max_family_holdout_spends": 2})
                    }
                ),
            )
    return store_path, full.trial_id, summary.trial_id


def test_view_reads_the_detail_level_and_the_last_weights(tmp_path: Path) -> None:
    store_path, full, summary = _detail_store(tmp_path, lab=False)
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        full_view = backtest_page.load_trial_view(conn, full)
        summary_view = backtest_page.load_trial_view(conn, summary)
    assert full_view.detail_level == "full"
    assert full_view.weights is not None
    assert [(w["security_id"], w["target_weight"]) for w in full_view.weights] == [
        ("SEC-B", 0.6),
        ("SEC-A", 0.4),
    ]
    assert summary_view.detail_level == "summary"
    assert summary_view.weights is None
    assert summary_view.equity  # its base-level equity is still shown
    assert {r["series"] for r in summary_view.equity} == {"strategy", "SPY", "MTUM"}


def test_render_full_trial_shows_weights_and_its_detail_level(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path, full, _summary = _detail_store(tmp_path, lab=False)
    at = _pick(_app(monkeypatch, store_path), full)
    assert not at.exception
    text = _text(at)
    assert "Detail level `full`." in text
    assert "Weights" in text
    weights = next(df.value for df in at.dataframe if "target_weight" in df.value.columns)
    assert list(weights["security_id"]) == ["SEC-B", "SEC-A"]


def test_render_summary_trial_shows_equity_and_no_weights_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path, _full, summary = _detail_store(tmp_path, lab=False)
    at = _pick(_app(monkeypatch, store_path), summary)
    assert not at.exception
    text = _text(at)
    assert "Detail level `summary`" in text
    assert "no weights stored" in text
    assert "Weights" not in [h.value for h in at.subheader]
    assert not any("target_weight" in df.value.columns for df in at.dataframe)
    equity_spec = json.loads(at.get("vega_lite_chart")[0].proto.spec)
    assert equity_spec["encoding"]["y"]["scale"] == {"type": "log"}


def test_render_spend_cap_from_the_family_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path, full, _summary = _detail_store(tmp_path, lab=True)
    at = _pick(_app(monkeypatch, store_path), full)
    assert not at.exception
    assert "Family `momentum`: 1 of 2 holdout spends (the family cap)." in _text(at)


def test_render_spend_cap_live_for_a_family_without_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path, full, _summary = _detail_store(tmp_path, lab=True)
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        conn.execute("DELETE FROM family_rules")
    live = Settings(_env_file=None).lab.max_family_holdout_spends
    at = _pick(_app(monkeypatch, store_path), full)
    assert not at.exception
    assert f"Family `momentum`: 1 of {live} holdout spends (the family cap)." in _text(at)


def test_render_no_spend_cap_without_the_lab_tables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path, full, _summary = _detail_store(tmp_path, lab=False)
    at = _pick(_app(monkeypatch, store_path), full)
    assert not at.exception
    assert "1 holdout spends; no family cap" in _text(at)


def test_holdout_spend_table_names_trial_and_research_sources(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    with duckdb.connect(str(store_path), read_only=True) as conn:
        view = backtest_page.load_trial_view(conn, seeded.holdout)
    trial = view.holdout_spends[0]
    research_spend = replace(trial, trial_id=99, source="research_run")
    view = replace(view, holdout_spends=(trial, research_spend))
    frames: list[Any] = []
    monkeypatch.setattr(backtest_page.st, "subheader", lambda *_: None)
    monkeypatch.setattr(backtest_page.st, "caption", lambda *_: None)
    monkeypatch.setattr(backtest_page.st, "dataframe", lambda frame, **_: frames.append(frame))

    backtest_page._render_holdout_spends(view)

    assert frames[0]["source"].to_list() == ["trial", "research_run"]
