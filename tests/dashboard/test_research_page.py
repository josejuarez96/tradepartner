"""Tests for the research view (research-registry spec req 15; plan T83c).

The page is driven headless through `streamlit.testing.v1.AppTest` on the shell
(`dashboard/app.py`), with the store path set through `STORE__PATH` exactly as
`tests/dashboard/test_backtest_page.py` does. The store is a temp file seeded
through `store.research`, so every row the page reads went through the registry
API. `load_research_view` is also exercised directly: it is the page's only
reader and needs no Streamlit.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.backtest.results import family_n
from tradepartner.config import Settings
from tradepartner.dashboard import research_page
from tradepartner.research import RunHandle
from tradepartner.research.experiment import (
    ParsedExperiment,
    hash_file,
    parse_experiment_file,
    split_event_spans,
)
from tradepartner.research.gates import Flags, Reasons
from tradepartner.store import registry, research, schema
from tradepartner.store.db import open_for_write

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "experiments"
_APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "dashboard" / "app.py"
)
HOLDOUT = (date(2024, 1, 1), date(2024, 12, 31))
SPEND = Flags(spend_holdout=True)
REPEAT = Flags(spend_holdout=True, holdout_repeat=True)
WHY = Reasons(holdout_reason="owner's planned look")


class Seeded:
    """Run ids of the seeded store, by role."""

    def __init__(self) -> None:
        self.ok = 0
        self.failed = 0
        self.window = 0
        self.split = 0
        self.holdout = 0
        self.spent = 0
        self.repeat = 0
        self.synthetic = 0
        self.abandoned = 0
        self.budget = 0
        self.labels = 0
        self.confirmatory = 0


def _variant(base: ParsedExperiment, slug: str, **changes: Any) -> ParsedExperiment:
    tag = f"{slug}:{sorted(changes.items())!r}"
    return replace(base, slug=slug, params_sha256=sha256(tag.encode()).hexdigest(), **changes)


def _returns(base: ParsedExperiment, slug: str, **changes: Any) -> ParsedExperiment:
    """An exploratory `return` experiment in family `momentum` whose window
    reaches the fixture hypothesis's 2024 holdout."""
    fields: dict[str, Any] = {
        "kind": "return",
        "stage": 6,
        "touches_returns": True,
        "family": "momentum",
        "confirmatory": False,
        "window_start": date(2020, 1, 1),
        "window_end": date(2024, 12, 31),
        "splits": ("full", "dev", "test"),
        "dataset_name": "panel",
        "budget_runs": 10,
        "budget_configurations": 20,
    }
    fields.update(changes)
    return _variant(base, slug, **fields)


def _labels(base: ParsedExperiment, slug: str, **changes: Any) -> ParsedExperiment:
    """A confirmatory `benchmark` experiment bound to the sealed `labels` export."""
    fields: dict[str, Any] = {
        "kind": "benchmark",
        "stage": 3,
        "confirmatory": True,
        "touches_returns": False,
        "family": None,
        "splits": ("dev", "test"),
        "dataset_name": "labels",
        "budget_runs": 5,
        "budget_configurations": 5,
    }
    fields.update(changes)
    return _variant(base, slug, **fields)


def _hypothesis(conn: duckdb.DuckDBPyConnection, settings: Settings) -> int:
    record = registry.register_hypothesis(
        conn,
        slug="h1",
        family="momentum",
        title="h1",
        doc_path="docs/hypotheses/h1.md",
        doc_sha256="d" * 64,
        params={"costs.per_side_bps": 15.0},
        in_sample_start=date(2016, 1, 29),
        holdout_start=HOLDOUT[0],
        holdout_end=HOLDOUT[1],
        registered_by="owner",
        settings=settings,
    )
    return record.hypothesis_id


def _counted_trial(conn: duckdb.DuckDBPyConnection, settings: Settings, hypothesis_id: int) -> None:
    """One `ok`, non-synthetic `in_sample` trial, so the family's backtest N is 1."""
    handle = registry.open_trial(
        conn,
        hypothesis_id=hypothesis_id,
        kind="in_sample",
        start_session=date(2021, 1, 31),
        end_session=date(2022, 12, 31),
        data_cutoff=datetime(2022, 12, 31, 21, tzinfo=UTC),
        synthetic=False,
        run_by="owner",
        settings=settings,
    )
    assert registry.write_result(conn, handle, registry.ResultStatistics()) == "ok"


def _export(
    tmp_path: Path, stem: str, dates: list[date], splits: list[str] | None = None
) -> tuple[Path, Path | None]:
    csv = tmp_path / f"{stem}.csv"
    csv.write_text(
        "event_date,value\n" + "".join(f"{d.isoformat()},{i}\n" for i, d in enumerate(dates)),
        encoding="utf-8",
    )
    if splits is None:
        return csv, None
    split_file = tmp_path / f"{stem}.splits.json"
    split_file.write_text(json.dumps({"splits": splits}), encoding="utf-8")
    return csv, split_file


def _dataset(
    conn: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    *,
    name: str = "panel",
    stem: str | None = None,
    dates: list[date] | None = None,
    splits: list[str] | None = None,
    sealed: tuple[str, ...] = (),
    periods: tuple[tuple[date, date], ...] = (),
    seed: int | None = None,
    locked: bool = False,
) -> research.DatasetRecord:
    dates = dates if dates is not None else [date(2021, 1, 4), date(2022, 6, 30)]
    csv, split_file = _export(tmp_path, stem or name, dates, splits)
    return research.register_dataset(
        conn,
        name=name,
        version="v1",
        path=str(csv),
        sha256=hash_file(csv),
        event_start=min(dates),
        event_end=max(dates),
        n_rows=len(dates),
        event_column="event_date",
        split_path=str(split_file) if split_file else None,
        split_sha256=hash_file(split_file) if split_file else None,
        split_spans=split_event_spans(dates, splits) if splits else None,
        sealed_splits=sealed,
        sealed_periods=periods,
        locked=locked,
        seed=seed,
        repo_dir=tmp_path,
    )


def _open(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    slug: str,
    dataset_id: int,
    split: str = "full",
    **kwargs: Any,
) -> RunHandle:
    return research.open_run(
        conn,
        slug,
        dataset_id,
        split,
        {"seed": 1},
        "test",
        settings=settings,
        repo_dir=tmp_path,
        **kwargs,
    )


def _ok(conn: duckdb.DuckDBPyConnection, handle: RunHandle, **overrides: Any) -> str:
    values: dict[str, Any] = {
        "primary_value": -0.5,
        "primary_ci_low": -0.9,
        "primary_ci_high": -0.1,
        "n_observations": 400,
        "n_clusters": 40,
        "n_configurations": 1,
        "artifact_sha256": "a" * 64,
        "artifact_path": "/tmp/report.html",
    }
    values.update(overrides)
    return research.write_result(conn, handle, **values)


def _seed(store_path: Path, tmp_path: Path) -> Seeded:
    store_settings = Settings(_env_file=None, store={"path": str(store_path)})
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    seeded = Seeded()
    with open_for_write(store_settings) as conn:
        schema.init_schema(conn)
        e1h = parse_experiment_file(
            FIXTURES / "e1h-demand-deterioration-revenue.md", FIXTURES, settings=seed_settings
        )
        hypothesis_id = _hypothesis(conn, seed_settings)
        _counted_trial(conn, seed_settings, hypothesis_id)

        # A sealed benchmark export registered before its (confirmatory) registration,
        # so the run's basis is `sealed_split`.
        labels = _dataset(
            conn,
            tmp_path,
            name="labels",
            dates=[date(2022, 2, 1), date(2022, 8, 1)],
            splits=["dev", "test"],
            periods=((date(2022, 1, 1), date(2022, 12, 31)),),
            seed=e1h.seed,
        )
        research.register_experiment(conn, _labels(e1h, "lb1"), "owner")
        seeded.labels = _open(
            conn,
            seed_settings,
            tmp_path,
            "lb1",
            labels.dataset_id,
            "test",
            flags=SPEND,
            reasons=WHY,
        ).run_id

        # The E1-H chain: a registration and an amendment (a budget_amend decision),
        # then its dataset registered afterwards, so its run's basis is
        # `predates_dataset`.
        first = research.register_experiment(conn, replace(e1h, budget_runs=1), "owner")
        research.register_experiment(
            conn,
            replace(e1h, budget_runs=5, amends_sha256=first.params_sha256),
            "owner",
            amend_reason="raise the run budget",
        )
        e1h_ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
        seeded.confirmatory = _open(
            conn, seed_settings, tmp_path, e1h.slug, e1h_ds.dataset_id, "pilot"
        ).run_id

        # The momentum return runs, one per req 15 state. `r1` and `b1` end before the
        # family's 2024 holdout, so their datasets lie outside it; `h1r` reaches it.
        panel = _dataset(conn, tmp_path)
        outside = _dataset(
            conn, tmp_path, stem="panel-out", dates=[date(2019, 6, 3), date(2023, 1, 3)]
        )
        holdout_ds = _dataset(
            conn, tmp_path, stem="panel-holdout", dates=[date(2023, 1, 3), date(2024, 6, 28)]
        )
        research.register_experiment(
            conn, _returns(e1h, "r1", window_end=date(2023, 12, 31)), "owner"
        )
        research.register_experiment(conn, _returns(e1h, "h1r"), "owner")

        ok = _open(conn, seed_settings, tmp_path, "r1", panel.dataset_id, "full")
        assert _ok(conn, ok) == "ok"
        seeded.ok = ok.run_id
        failed = _open(conn, seed_settings, tmp_path, "r1", panel.dataset_id, "full")
        assert _ok(conn, failed, n_configurations=2) == "failed"
        seeded.failed = failed.run_id
        seeded.window = _open(
            conn, seed_settings, tmp_path, "r1", outside.dataset_id, "full"
        ).run_id
        seeded.split = _open(conn, seed_settings, tmp_path, "r1", panel.dataset_id, "cal").run_id
        seeded.holdout = _open(
            conn, seed_settings, tmp_path, "h1r", holdout_ds.dataset_id, "full"
        ).run_id
        seeded.spent = _open(
            conn,
            seed_settings,
            tmp_path,
            "h1r",
            holdout_ds.dataset_id,
            "full",
            flags=SPEND,
            reasons=WHY,
        ).run_id
        seeded.repeat = _open(
            conn,
            seed_settings,
            tmp_path,
            "h1r",
            holdout_ds.dataset_id,
            "full",
            flags=REPEAT,
            reasons=WHY,
        ).run_id
        seeded.synthetic = _open(
            conn, seed_settings, tmp_path, "r1", panel.dataset_id, "full", synthetic=True
        ).run_id

        # A second chain whose budget is reached: one abandoned run, then a refusal.
        research.register_experiment(
            conn,
            _returns(
                e1h,
                "b1",
                window_end=date(2023, 12, 31),
                budget_runs=1,
                budget_configurations=1,
            ),
            "owner",
        )
        abandon = _open(conn, seed_settings, tmp_path, "b1", panel.dataset_id, "full")
        research.close_run(conn, abandon, "abandoned", "changed mind")
        seeded.abandoned = abandon.run_id
        seeded.budget = _open(conn, seed_settings, tmp_path, "b1", panel.dataset_id, "full").run_id
    return seeded


@pytest.fixture
def seeded_store(tmp_path: Path) -> tuple[Path, Seeded]:
    store_path = tmp_path / "store.duckdb"
    return store_path, _seed(store_path, tmp_path)


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.sidebar.radio[0].set_value("Research").run()
    return at


def _text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.subheader]
    parts += [e.value for kind in (at.info, at.warning, at.error) for e in kind]
    return "\n".join(str(p) for p in parts)


def _frame(at: AppTest, column: str) -> Any:
    return next(df.value for df in at.dataframe if column in df.value.columns)


# --- load_research_view (pure reader) ----------------------------------------


def test_reader_lists_runs_and_derives_chain_usage(
    seeded_store: tuple[Path, Seeded],
) -> None:
    store_path, seeded = seeded_store
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        view = research_page.load_research_view(conn)
    finally:
        conn.close()

    assert seeded.synthetic not in {run.run_id for run in view.runs}
    assert research_page._chain_usage(view.runs)["r1"] == (4, 4)  # type: ignore[attr-defined]
    by_slug = {reg.slug: reg for reg in view.registrations if reg.amends_registration_id is None}
    assert by_slug["e1h-demand-deterioration-revenue"].budget_runs == 1
    assert any(reg.amends_registration_id is not None for reg in view.registrations)
    assert {f.family for f in view.families} == {"momentum"}


# --- headless render ----------------------------------------------------------


def test_page_is_in_the_shell_navigation(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    assert at.sidebar.radio[0].options == [
        "Data health",
        "Backtest",
        "Trial registry",
        "Research",
        "Operations",
        "Override",
    ]


def test_render_lists_registrations_budgets_and_amendments(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    registrations = _frame(at, "budget runs")
    for column in ("slug", "kind", "confirmatory", "family", "budget configurations", "amends"):
        assert column in registrations.columns
    budgets = dict(zip(registrations["slug"], registrations["budget runs"], strict=True))
    assert budgets["r1"] == "4/10"
    assert budgets["b1"] == "2/1"
    assert any(value is not None for value in registrations["amends"])


def test_render_family_sums_beside_the_backtest_n(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, _ = seeded_store
    at = _app(monkeypatch, store_path)
    assert not at.exception
    families = _frame(at, "backtest N")
    assert list(families.columns) == [
        "family",
        "research runs",
        "research configurations",
        "backtest N",
    ]
    row = families.iloc[0].to_dict()
    assert row["family"] == "momentum"
    assert row["research runs"] == 9
    assert row["research configurations"] == 9
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        assert row["backtest N"] == family_n(conn, "momentum") == 1
    finally:
        conn.close()


def test_render_runs_newest_first_with_every_state_and_synthetic_hidden(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _app(monkeypatch, store_path)
    assert not at.exception
    runs = _frame(at, "outcome")
    for column in ("verdict", "confirmatory", "basis", "provenance", "holdout spent", "message"):
        assert column in runs.columns
    listed = list(runs["run"])
    assert listed == sorted(listed, reverse=True)
    states = {"ok", "failed", "refused_window", "refused_split", "refused_holdout", "abandoned"}
    assert states <= set(runs["outcome"])
    assert "unfinished" in set(runs["outcome"])
    assert seeded.synthetic not in listed
    by_run = {run_id: index for index, run_id in enumerate(listed)}
    assert runs["verdict"].iloc[by_run[seeded.ok]] == "pass"
    assert set(runs["basis"]) >= {"predates_dataset", "sealed_split", "none"}
    spent = runs.iloc[by_run[seeded.spent]]
    repeat = runs.iloc[by_run[seeded.repeat]]
    assert bool(spent["holdout spent"]) is True
    assert bool(spent["holdout repeat"]) is False
    assert bool(repeat["holdout repeat"]) is True


def test_render_shows_synthetic_runs_when_asked(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, seeded = seeded_store
    at = _app(monkeypatch, store_path)
    assert not at.exception
    label = "Show synthetic runs"
    checkbox = next(box for box in at.checkbox if box.label == label)
    checkbox.check().run()
    runs = _frame(at, "outcome")
    assert seeded.synthetic in set(runs["run"])


def test_render_datasets_with_sealed_splits_periods_and_spends(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    datasets = _frame(at, "sealed splits (spent)")
    labels = datasets[datasets["name"] == "labels"].iloc[0]
    assert labels["sealed splits (spent)"] == "test (spent)"
    assert "2022-01-01 to 2022-12-31 (spent)" in labels["sealed periods (spent)"]


def test_render_decisions(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    decisions = _frame(at, "made_by")
    assert {"budget_amend", "holdout_spend"} <= set(decisions["kind"])
    assert any("raise the run budget" in reason for reason in decisions["reason"])


def test_render_not_initialised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "v11.duckdb"
    conn = duckdb.connect(str(store_path))
    try:
        for ddl in schema._REGISTRY_TABLE_DDL:  # type: ignore[attr-defined]
            conn.execute(ddl)
        conn.execute("CREATE TABLE schema_version (version INTEGER, applied_at TIMESTAMPTZ)")
        conn.execute("INSERT INTO schema_version VALUES (11, now())")
    finally:
        conn.close()
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "research registry not initialised" in _text(at).lower()


def test_page_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, _ = seeded_store
    at = _app(monkeypatch, store_path)
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    at.run()
    assert not at.exception
    assert opened == [True]
