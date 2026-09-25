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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.backtest.metrics import deflated_sharpe
from tradepartner.config import Settings
from tradepartner.dashboard import backtest_page
from tradepartner.store import registry, schema
from tradepartner.store.db import open_for_write

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
        "sharpe_monthly": sharpe,
        "sharpe_annual": sharpe * math.sqrt(12),
        "sharpe_monthly_excess_spy": excess,
        "max_drawdown": -0.2,
        "turnover_monthly": 0.1,
        "cost_drag": 0.002,
        "excess_cagr_spy": 0.01,
        "excess_cagr_mtum": -0.005,
        "tracking_error_spy": 0.05,
        "tracking_error_mtum": 0.04,
        "skew_monthly": -0.3,
        "kurtosis_monthly": 3.5,
        "skew_monthly_excess_spy": 0.1,
        "kurtosis_monthly_excess_spy": 3.2,
        "n_months": 36.0,
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
                    n_excluded_no_history=3,
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

    # Today: three ok non-synthetic in-sample trials, three distinct pairs.
    expected_raw = deflated_sharpe(
        _metrics(0.20, 0.05), "raw", n_trials=3, pair_sharpes=[0.20, 0.10, 0.30]
    )
    expected_excess = deflated_sharpe(
        _metrics(0.20, 0.05), "excess_spy", n_trials=3, pair_sharpes=[0.05, 0.02, 0.09]
    )
    raw, excess = view.dsr_rows
    assert raw.basis == "raw"
    assert raw.stored_n == 1
    assert raw.stored_dsr == pytest.approx(0.9)
    assert raw.today == expected_raw
    assert excess.today == expected_excess
    assert excess.stored_dsr == pytest.approx(0.6)


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
    assert at.sidebar.radio[0].options == ["Data health", "Backtest", "Trial registry"]


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
