"""Tests for the research-registry API (plan T81, `store/research.py` and
`research/__init__.py`).

Spec `docs/specs/research-registry.md` reqs 1 to 9 and 11: inserts and reads
only; the store-level req 2 refusals and the amendment chain; req 11's store rules
for datasets; every gate's refusal recorded as a run row with its outcome, on a
fixture hypothesis whose holdout is `[2024-01-01, 2024-12-31]`; repeats across
slugs, dataset names and backtest spends; budgets; the confirmatory basis;
results, verdicts and the two "changed during run" failures; the file-identity
refusal of a synthetic run on `settings.store.path`; `family_run_count`;
`attach_run`; `load_dataset`'s hash check before parsing; and
`ResearchNotInitialised` on a pre-migration store.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

import tradepartner.research as research_pkg
from tradepartner.config import Settings
from tradepartner.research import (
    DatasetChanged,
    NoRunHandle,
    RunHandle,
    load_dataset,
    require_handle,
)
from tradepartner.research.experiment import (
    ParsedExperiment,
    hash_file,
    parse_experiment_file,
    split_event_spans,
)
from tradepartner.research.gates import Flags, Reasons
from tradepartner.store import registry, research, schema

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "experiments"
SOURCE = Path(research.__file__)
HOLDOUT = (date(2024, 1, 1), date(2024, 12, 31))
SPEND = Flags(spend_holdout=True)
REPEAT = Flags(spend_holdout=True, holdout_repeat=True)
WHY = Reasons(holdout_reason="owner's planned look")


# --- fixtures and helpers ------------------------------------------------------------


def _connect(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    return conn


@pytest.fixture
def conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """A registry on a temp file that is not `settings.store.path`."""
    return _connect(tmp_path / "scratch.duckdb")


@pytest.fixture
def e1h(settings: Settings) -> ParsedExperiment:
    """The spec's E1-H example, parsed from the T82 fixture."""
    return parse_experiment_file(
        FIXTURES / "e1h-demand-deterioration-revenue.md", FIXTURES, settings=settings
    )


def _variant(base: ParsedExperiment, slug: str, **changes: Any) -> ParsedExperiment:
    """`base` under another slug, with a params hash of its own (the store never
    recomputes it)."""
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


def _hypothesis(conn: duckdb.DuckDBPyConnection, settings: Settings) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
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


def _export(
    tmp_path: Path,
    stem: str,
    dates: list[date],
    splits: list[str] | None = None,
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
    event_column: str | None = "event_date",
) -> research.DatasetRecord:
    dates = dates if dates is not None else [date(2021, 1, 4), date(2022, 6, 30)]
    csv, split_file = _export(tmp_path, stem or name, dates, splits)
    row_dates = (
        {
            s: (
                list(dates)
                if s in research.EVERY_ROW_SPLITS
                else [d for d, x in zip(dates, splits or (), strict=False) if x == s]
            )
            for s in sealed
        }
        if sealed
        else None
    )
    return research.register_dataset(
        conn,
        name=name,
        version="v1",
        path=str(csv),
        sha256=hash_file(csv),
        event_start=min(dates),
        event_end=max(dates),
        n_rows=len(dates),
        event_column=event_column,
        split_path=str(split_file) if split_file else None,
        split_sha256=hash_file(split_file) if split_file else None,
        split_spans=split_event_spans(dates, splits) if splits else None,
        sealed_splits=sealed,
        sealed_periods=periods,
        split_row_dates=row_dates,
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


def _result(conn: duckdb.DuckDBPyConnection, run_id: int) -> tuple[Any, ...] | None:
    return conn.execute(
        "SELECT outcome, message, verdict FROM research_results WHERE run_id = ?", [run_id]
    ).fetchone()


# --- append-only and the one insert into research_runs --------------------------------


def test_store_research_has_no_update_or_delete() -> None:
    assert not re.search(r"\b(UPDATE|DELETE)\b", SOURCE.read_text(encoding="utf-8"), re.I)


def _inserts_into_runs(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and re.search(r"INSERT\s+INTO\s+research_runs\b", sub.value, re.I)
        ):
            return True
        if (
            isinstance(sub, ast.Call)
            and getattr(sub.func, "id", getattr(sub.func, "attr", None)) == "insert_row"
            and any(isinstance(a, ast.Constant) and a.value == "research_runs" for a in sub.args)
        ):
            return True
    return False


def test_only_open_run_inserts_into_research_runs() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    inserting = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _inserts_into_runs(node)
    }
    assert inserting == {"open_run"}


def test_the_insert_detector_sees_sql_text_and_insert_row() -> None:
    assert _inserts_into_runs(ast.parse('def f():\n    c.execute("INSERT INTO research_runs")'))
    assert _inserts_into_runs(ast.parse('def f():\n    insert_row(c, "research_runs", {})'))
    assert not _inserts_into_runs(ast.parse('def f():\n    insert_row(c, "research_results", {})'))


def test_a_research_read_on_a_pre_migration_store_raises(tmp_path: Path) -> None:
    """A store without the research tables (version 11's shape, opened read-only)
    raises `ResearchNotInitialised` on a research read, and the backtest's
    `family_holdout_spends` still reads its trial spends."""
    path = tmp_path / "v11.duckdb"
    with duckdb.connect(str(path)) as c:
        for ddl in schema._REGISTRY_TABLE_DDL:
            c.execute(ddl)
    with duckdb.connect(str(path), read_only=True) as c:
        with pytest.raises(schema.ResearchNotInitialised):
            research.list_runs(c)
        with pytest.raises(schema.ResearchNotInitialised):
            research.family_run_count(c, "momentum")
        assert registry.family_holdout_spends(c, "momentum") == []


# --- registration (store-level req 2) --------------------------------------------------


def test_registering_the_fixture_stores_its_hashes(
    conn: duckdb.DuckDBPyConnection, e1h: ParsedExperiment
) -> None:
    record = research.register_experiment(conn, e1h, "owner")
    assert (record.registration_id, record.amends_registration_id) == (1, None)
    assert (record.doc_sha256, record.params_json, record.params_sha256) == (
        e1h.doc_sha256,
        e1h.params_json,
        e1h.params_sha256,
    )
    assert record.claims == e1h.claims
    assert record.known_at.tzinfo is not None
    assert research.get_registration(conn, e1h.slug) == record


def test_a_registered_slug_without_amends_sha256_is_refused(
    conn: duckdb.DuckDBPyConnection, e1h: ParsedExperiment
) -> None:
    research.register_experiment(conn, e1h, "owner")
    with pytest.raises(research.ResearchError, match="already registered"):
        research.register_experiment(conn, replace(e1h, params_sha256="f" * 64), "owner")


def test_a_stale_amends_sha256_is_refused(
    conn: duckdb.DuckDBPyConnection, e1h: ParsedExperiment
) -> None:
    first = research.register_experiment(conn, e1h, "owner")
    research.register_experiment(
        conn, replace(e1h, amends_sha256=first.params_sha256, params_sha256="b" * 64), "owner"
    )
    with pytest.raises(research.ResearchError, match="stale amendment"):
        research.register_experiment(
            conn, replace(e1h, amends_sha256=first.params_sha256, params_sha256="c" * 64), "owner"
        )


def test_amends_sha256_on_a_new_slug_is_refused(
    conn: duckdb.DuckDBPyConnection, e1h: ParsedExperiment
) -> None:
    with pytest.raises(research.ResearchError, match="not registered yet"):
        research.register_experiment(conn, replace(e1h, amends_sha256="a" * 64), "owner")


@pytest.mark.parametrize(("method", "size"), [("bh", 3), ("holm", 4)])
def test_a_family_id_reused_with_another_method_or_size_is_refused(
    conn: duckdb.DuckDBPyConnection, e1h: ParsedExperiment, method: str, size: int
) -> None:
    research.register_experiment(
        conn,
        _variant(
            e1h,
            "first",
            multiplicity_method="holm",
            multiplicity_family_id="fam",
            multiplicity_family_size=3,
        ),
        "owner",
    )
    research.register_experiment(
        conn,
        _variant(
            e1h,
            "same",
            multiplicity_method="holm",
            multiplicity_family_id="fam",
            multiplicity_family_size=3,
        ),
        "owner",
    )
    with pytest.raises(research.ResearchError, match="multiplicity family 'fam'"):
        research.register_experiment(
            conn,
            _variant(
                e1h,
                "other",
                multiplicity_method=method,
                multiplicity_family_id="fam",
                multiplicity_family_size=size,
            ),
            "owner",
        )


def test_an_amendment_points_at_the_old_row_and_records_a_budget_amend(
    conn: duckdb.DuckDBPyConnection,
    e1h: ParsedExperiment,
    settings: Settings,
    tmp_path: Path,
) -> None:
    """The amendment row, its `budget_amend` decision, the old registration
    refusing `open_run`, and the chain's run count spanning both rows."""
    e1h = replace(e1h, confirmatory=False)  # an amendment postdates the dataset
    old = research.register_experiment(conn, replace(e1h, budget_runs=1), "owner")
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    first = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    assert first.refusal is None
    over = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    assert over.refusal == "refused_budget"
    new = research.register_experiment(
        conn,
        replace(
            e1h,
            budget_runs=3,
            budget_configurations=3,
            amends_sha256=old.params_sha256,
            params_sha256="e" * 64,
        ),
        "owner",
        amend_reason="a rerun for a bug",
    )
    assert new.amends_registration_id == old.registration_id
    decision = conn.execute(
        "SELECT kind, registration_id, values_json, reason FROM research_decisions"
    ).fetchone()
    assert decision is not None
    assert decision[:2] == ("budget_amend", new.registration_id)
    assert json.loads(decision[2])["old"]["runs"] == 1
    assert json.loads(decision[2])["new"]["runs"] == 3
    assert decision[3] == "a rerun for a bug"
    with pytest.raises(research.ResearchError, match="superseded"):
        _open(
            conn,
            settings,
            tmp_path,
            e1h.slug,
            ds.dataset_id,
            "pilot",
            registration_id=old.registration_id,
        )
    third = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    assert (third.refusal, third.registration_id) == (None, new.registration_id)
    fourth = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    assert fourth.refusal == "refused_budget"  # 3 runs along the chain: two old, one new


# --- datasets (req 11 store rules) ----------------------------------------------------


def test_without_a_split_file_full_spans_the_declared_span(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    ds = _dataset(conn, tmp_path)
    assert ds.split_spans == {"full": (date(2021, 1, 4), date(2022, 6, 30))}
    assert ds.known_at.tzinfo is not None


def test_a_split_file_without_an_event_column_is_refused(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(research.ResearchError, match="split without event column"):
        _dataset(conn, tmp_path, splits=["dev", "dev"], event_column=None)


_TEST_PERIOD = ((date(2024, 1, 1), date(2024, 12, 31)),)
_DEV_TEST = [date(2023, 3, 1), date(2024, 3, 1)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"splits": ["dev", "test"], "sealed": ("test",), "event_column": None},
        {"splits": ["dev", "test"], "sealed": ("test",), "periods": ()},
        {"splits": ["dev", "test"], "periods": ((date(2024, 6, 1), date(2024, 12, 31)),)},
        {"splits": ["dev", "test"]},
        {"sealed": ("full",), "event_column": None},
    ],
    ids=["no-event-column", "no-period", "test-row-outside", "implied-test", "directory-like"],
)
def test_a_sealed_split_without_a_period_is_refused(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    with pytest.raises(research.ResearchError, match="without"):
        _dataset(conn, tmp_path, dates=_DEV_TEST, **kwargs)


def test_dataset_versions_under_one_name(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Same export, split file and sealed set: the existing row. A larger sealed
    set or another split file: a new version. A shrinking sealed set: refused."""
    base = _dataset(conn, tmp_path, dates=_DEV_TEST, splits=["dev", "test"], periods=_TEST_PERIOD)
    assert base.sealed_splits == ("test",)
    again = _dataset(conn, tmp_path, dates=_DEV_TEST, splits=["dev", "test"], periods=_TEST_PERIOD)
    assert again == base
    larger = _dataset(
        conn,
        tmp_path,
        dates=_DEV_TEST,
        splits=["dev", "test"],
        periods=(*_TEST_PERIOD, (date(2023, 1, 1), date(2023, 1, 31))),
    )
    assert larger.dataset_id == base.dataset_id + 1
    relabelled = _dataset(
        conn,
        tmp_path,
        stem="relabelled",
        dates=_DEV_TEST,
        splits=["cal", "test"],
        periods=(*_TEST_PERIOD, (date(2023, 1, 1), date(2023, 1, 31))),
    )
    assert relabelled.dataset_id == larger.dataset_id + 1
    with pytest.raises(research.ResearchError, match="sealed set shrinks"):
        _dataset(conn, tmp_path, dates=_DEV_TEST, splits=["dev", "test"], periods=_TEST_PERIOD)


def test_register_dataset_refuses_an_unknown_sealed_split_name(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """#1149: `register_dataset` itself (not only the CLI) refuses a sealed name
    outside `RESEARCH_SPLITS`, so a direct API caller cannot record a sealed name
    the store never recognises."""
    with pytest.raises(research.ResearchError, match="not among the splits"):
        _dataset(conn, tmp_path, dates=_DEV_TEST, sealed=("cla",), periods=_TEST_PERIOD)


def test_register_dataset_checks_a_sealed_full_against_the_whole_event_span(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """#1149: a sealed `full` has no entry in `split_spans` (a split file never
    labels a row `full`), so the per-split span check used to skip it entirely.
    `full` binds every row, so it must be checked against `[event_start,
    event_end]`: the dataset's dev row (2023-03-01) lies outside the sealed
    period, which only the test row (2024-03-01) falls in."""
    with pytest.raises(research.ResearchError, match="sealed split without period"):
        _dataset(
            conn,
            tmp_path,
            dates=_DEV_TEST,
            splits=["dev", "test"],
            sealed=("full",),
            periods=_TEST_PERIOD,
        )


def test_register_dataset_checks_a_sealed_none_against_the_whole_event_span(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """#1149: same gap as `full`, for `none` (and without a split file, so `spans`
    never has a `none` entry either)."""
    with pytest.raises(research.ResearchError, match="sealed split without period"):
        _dataset(
            conn,
            tmp_path,
            dates=_DEV_TEST,
            sealed=("none",),
            periods=_TEST_PERIOD,
        )


def test_register_dataset_refuses_a_sealed_row_that_falls_between_two_periods(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """#1174: the sealed-span check tested only the split's two endpoints, so a row
    sitting in the gap between two disjoint sealed periods passed, though no period
    holds it. With the per-split row dates the store refuses it, extending #1149 from
    the endpoints to every row."""
    periods = (
        (date(2020, 1, 1), date(2020, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
    )
    with pytest.raises(research.ResearchError, match="sealed split without period"):
        _dataset(
            conn,
            tmp_path,
            dates=[date(2020, 1, 2), date(2022, 6, 1), date(2024, 1, 2)],
            sealed=("full",),
            periods=periods,
        )


def test_register_dataset_accepts_a_sealed_split_confined_to_disjoint_periods(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """#1174: disjoint sealed periods are refused only when a row falls in the gap
    between them; a rowless gap is fine, so the store must not require the merged
    periods to cover the whole span."""
    periods = (
        (date(2020, 1, 1), date(2020, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 31)),
    )
    record = _dataset(
        conn,
        tmp_path,
        dates=[date(2020, 1, 2), date(2024, 1, 2)],
        sealed=("full",),
        periods=periods,
    )
    assert record.sealed_splits == ("full",)
    assert record.sealed_periods == periods


# --- gates: every refusal is a run row with its outcome --------------------------------


def _gated(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, slug: str = "r1"
) -> ParsedExperiment:
    _hypothesis(conn, settings)
    parsed = _returns(e1h, slug)
    research.register_experiment(conn, parsed, "owner")
    return parsed


def test_a_dataset_outside_the_window_is_refused_window_with_a_row(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    _gated(conn, settings, e1h)
    ds = _dataset(conn, tmp_path, dates=[date(2019, 6, 3), date(2023, 1, 3)])
    handle = _open(conn, settings, tmp_path, "r1", ds.dataset_id)
    assert handle.refusal == "refused_window"
    assert _result(conn, handle.run_id) == ("refused_window", handle.message, None)
    with pytest.raises(NoRunHandle, match="refused"):
        load_dataset(handle)


def test_an_as_of_bound_after_close_of_window_end_is_refused_window(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    _gated(conn, settings, e1h)
    ds = _dataset(conn, tmp_path, dates=[date(2021, 1, 4), date(2022, 6, 30)])
    handle = _open(
        conn, settings, tmp_path, "r1", ds.dataset_id, as_of=datetime(2025, 1, 2, 21, tzinfo=UTC)
    )
    assert handle.refusal == "refused_window"


def test_a_split_the_registration_does_not_list_is_refused_split(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    _gated(conn, settings, e1h)
    ds = _dataset(conn, tmp_path, dates=[date(2021, 1, 4), date(2022, 6, 30)])
    handle = _open(conn, settings, tmp_path, "r1", ds.dataset_id, "cal")
    assert (handle.refusal, _result(conn, handle.run_id)[0]) == (  # type: ignore[index]
        "refused_split",
        "refused_split",
    )


def test_a_family_holdout_is_refused_then_spent_then_a_repeat(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """The spec's momentum example: refused without flags, a spend with them
    (decision row, `family_holdout_spends` lists it), and a second spend under
    another slug needs `--holdout-repeat`."""
    _gated(conn, settings, e1h)
    research.register_experiment(conn, _returns(e1h, "r2"), "owner")
    ds = _dataset(conn, tmp_path, dates=[date(2023, 1, 3), date(2024, 6, 28)])
    refused = _open(conn, settings, tmp_path, "r1", ds.dataset_id)
    assert refused.refusal == "refused_holdout"
    spent = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert spent.refusal is None
    row = conn.execute(
        "SELECT holdout_spent, holdout_repeat, holdout_reason FROM research_runs WHERE run_id = ?",
        [spent.run_id],
    ).fetchone()
    assert row == (True, False, WHY.holdout_reason)
    decision = conn.execute(
        "SELECT values_json, reason FROM research_decisions WHERE kind = 'holdout_spend'"
    ).fetchone()
    assert decision is not None
    assert json.loads(decision[0])["family_holdouts"] == [["2024-01-01", "2024-12-31"]]
    spends = registry.family_holdout_spends(conn, "momentum")
    assert [(s.source, s.trial_id, s.slug) for s in spends] == [
        ("research_run", spent.run_id, "r1")
    ]
    second = _open(conn, settings, tmp_path, "r2", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert second.refusal == "refused_holdout"
    assert "already spent" in str(second.message)
    repeat = _open(conn, settings, tmp_path, "r2", ds.dataset_id, flags=REPEAT, reasons=WHY)
    assert repeat.refusal is None
    assert conn.execute(
        "SELECT holdout_repeat FROM research_runs WHERE run_id = ?", [repeat.run_id]
    ).fetchone() == (True,)


def test_the_boundary_before_the_holdout_leaves_the_holdout_gate_as_it_was(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """ADR 0016 point 2 (plan T142b): every session after a 2023-12-29 boundary in the
    window is the family's holdout, so the holdout gate decides as before."""
    _gated(conn, settings, e1h)
    registry.write_development_boundary(conn, boundary=date(2023, 12, 29), reason="test")
    ds = _dataset(conn, tmp_path, dates=[date(2023, 1, 3), date(2024, 6, 28)])
    assert _open(conn, settings, tmp_path, "r1", ds.dataset_id).refusal == "refused_holdout"
    spent = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert spent.refusal is None


@pytest.mark.parametrize(
    ("boundary", "window_end", "dead"),
    [
        # The dead months between the boundary and holdout.start.
        (date(2022, 12, 30), date(2024, 12, 31), "(2023-01-03..2023-12-29)"),
        # Sessions past the last holdout.end.
        (date(2023, 12, 29), date(2025, 6, 30), "(2025-01-02..2025-06-30)"),
    ],
)
def test_a_window_reading_past_the_boundary_outside_the_holdout_is_refused(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    e1h: ParsedExperiment,
    tmp_path: Path,
    boundary: date,
    window_end: date,
    dead: str,
) -> None:
    """The boundary is one more protected edge, and no flag reads past it."""
    _hypothesis(conn, settings)
    research.register_experiment(conn, _returns(e1h, "r1", window_end=window_end), "owner")
    registry.write_development_boundary(conn, boundary=boundary, reason="test")
    ds = _dataset(conn, tmp_path, dates=[date(2021, 1, 4), date(2022, 6, 30)])
    for flags in (None, SPEND):
        handle = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=flags, reasons=WHY)
        assert handle.refusal == "refused_window"
        assert f"development boundary {boundary}" in str(handle.message)
        assert dead in str(handle.message)
        assert _result(conn, handle.run_id)[0] == "refused_window"  # type: ignore[index]
    assert registry.family_holdout_spends(conn, "momentum") == []


def test_a_family_with_no_registered_hypothesis_has_no_boundary_edge(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """The edge protects a backtest family's months; a family with no registered
    hypothesis has none (code-review and quant-auditor on #1345)."""
    _hypothesis(conn, settings)
    registry.write_development_boundary(conn, boundary=date(2022, 12, 30), reason="test")
    research.register_experiment(conn, _returns(e1h, "r1", family="profitability"), "owner")
    ds = _dataset(conn, tmp_path, dates=[date(2021, 1, 4), date(2022, 6, 30)])
    assert _open(conn, settings, tmp_path, "r1", ds.dataset_id).refusal is None


def test_a_research_spend_after_a_backtest_holdout_trial_is_a_repeat(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    _gated(conn, settings, e1h)
    hypothesis = registry.get_hypothesis(conn, "h1")
    registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="holdout",
        start_session=HOLDOUT[0],
        end_session=HOLDOUT[1],
        data_cutoff=None,
        synthetic=False,
        run_by="owner",
        holdout_reason="backtest spend",
        settings=settings,
        repo_dir=tmp_path,
    )
    ds = _dataset(conn, tmp_path)
    assert (
        _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY).refusal
        == "refused_holdout"
    )
    assert (
        _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=REPEAT, reasons=WHY).refusal
        is None
    )


def test_the_sealed_test_is_scored_once_per_dataset_name(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """A `benchmark` run on `split=test` is a spend; a second `test` scoring on
    the same dataset name under another registration is a repeat."""
    for slug in ("b1", "b2"):
        research.register_experiment(
            conn,
            _variant(
                e1h,
                slug,
                kind="benchmark",
                stage=3,
                splits=("dev", "test"),
                dataset_name="labels",
                budget_runs=5,
                budget_configurations=5,
            ),
            "owner",
        )
    dates = [date(2018, 3, 1), date(2022, 3, 1)]
    ds = _dataset(
        conn,
        tmp_path,
        name="labels",
        dates=dates,
        splits=["dev", "test"],
        periods=((date(2022, 1, 1), date(2022, 12, 31)),),
        seed=e1h.seed,
    )
    assert _open(conn, settings, tmp_path, "b1", ds.dataset_id, "test").refusal == (
        "refused_holdout"
    )
    first = _open(conn, settings, tmp_path, "b1", ds.dataset_id, "test", flags=SPEND, reasons=WHY)
    assert first.refusal is None
    again = _open(conn, settings, tmp_path, "b2", ds.dataset_id, "test", flags=SPEND, reasons=WHY)
    assert again.refusal == "refused_holdout"
    repeat = _open(conn, settings, tmp_path, "b2", ds.dataset_id, "test", flags=REPEAT, reasons=WHY)
    assert repeat.refusal is None
    dev = _open(conn, settings, tmp_path, "b1", ds.dataset_id, "dev")
    assert dev.refusal is None  # its rows lie outside the sealed 2022 period


def test_a_dev_row_inside_a_sealed_period_is_a_spend(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(
        conn,
        _variant(e1h, "b1", kind="benchmark", stage=3, splits=("dev",), dataset_name="labels"),
        "owner",
    )
    ds = _dataset(
        conn,
        tmp_path,
        name="labels",
        dates=[date(2018, 3, 1), date(2022, 3, 1), date(2022, 6, 1)],
        splits=["dev", "dev", "test"],
        periods=((date(2022, 1, 1), date(2022, 12, 31)),),
    )
    assert _open(conn, settings, tmp_path, "b1", ds.dataset_id, "dev").refusal == (
        "refused_holdout"
    )


def test_a_confirmatory_run_on_a_dataset_that_precedes_it_is_refused(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """`dataset precedes registration`, unchanged by re-hashing the export as a
    new version; with a sealed split drawn under the registered seed the run
    opens with `confirmatory_basis = sealed_split`."""
    dates = [date(2018, 3, 1), date(2022, 3, 1)]
    _dataset(conn, tmp_path, name=e1h.dataset_name, dates=dates, splits=["pilot", "pilot"])
    research.register_experiment(
        conn, replace(e1h, splits=("pilot", "test"), budget_configurations=5), "owner"
    )
    rehashed = _dataset(
        conn,
        tmp_path,
        name=e1h.dataset_name,
        stem="rehashed",
        dates=[*dates, date(2022, 4, 1)],
        splits=["pilot", "pilot", "pilot"],
    )
    refused = _open(conn, settings, tmp_path, e1h.slug, rehashed.dataset_id, "pilot")
    assert (refused.refusal, refused.message) == ("refused_split", "dataset precedes registration")
    sealed = _dataset(
        conn,
        tmp_path,
        name=e1h.dataset_name,
        stem="sealed",
        dates=dates,
        splits=["pilot", "test"],
        periods=((date(2022, 1, 1), date(2022, 12, 31)),),
        seed=e1h.seed,
    )
    opened = _open(
        conn, settings, tmp_path, e1h.slug, sealed.dataset_id, "test", flags=SPEND, reasons=WHY
    )
    assert opened.refusal is None
    assert conn.execute(
        "SELECT confirmatory, confirmatory_basis FROM research_runs WHERE run_id = ?",
        [opened.run_id],
    ).fetchone() == (True, "sealed_split")


def test_a_registration_before_its_dataset_opens_as_predates_dataset(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(conn, e1h, "owner")
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    handle = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    assert handle.refusal is None
    assert conn.execute(
        "SELECT confirmatory_basis FROM research_runs WHERE run_id = ?", [handle.run_id]
    ).fetchone() == ("predates_dataset",)


def test_a_confirmatory_agreement_run_on_an_unlocked_export_is_refused(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    agreement = parse_experiment_file(
        FIXTURES / "pilot-agreement-d1-d3.md", FIXTURES, settings=settings
    )
    research.register_experiment(conn, agreement, "owner")
    ds = _dataset(conn, tmp_path, name=agreement.dataset_name, splits=["pilot", "pilot"])
    handle = _open(conn, settings, tmp_path, agreement.slug, ds.dataset_id, "pilot")
    assert (handle.refusal, handle.message) == ("refused_split", "label export not locked")


def test_budgets_count_refusals_but_not_synthetic_runs(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(
        conn,
        _variant(e1h, "b", confirmatory=False, budget_runs=2, budget_configurations=3),
        "owner",
    )
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    assert _open(conn, settings, tmp_path, "b", ds.dataset_id, "cal").refusal == "refused_split"
    synthetic = _open(conn, settings, tmp_path, "b", ds.dataset_id, "pilot", synthetic=True)
    assert synthetic.refusal is None
    over = _open(conn, settings, tmp_path, "b", ds.dataset_id, "pilot", configurations=3)
    assert (over.refusal, "configurations" in str(over.message)) == ("refused_budget", True)
    # Two non-synthetic rows now (the refused split and the refused budget).
    assert _open(conn, settings, tmp_path, "b", ds.dataset_id, "pilot").refusal == (
        "refused_budget"
    )


def test_omitted_configurations_declare_one(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(conn, e1h, "owner")
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    handle = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    assert handle.n_configurations_declared == 1
    assert _ok(conn, handle, n_configurations=2) == "failed"
    assert _result(conn, handle.run_id) == ("failed", "configurations exceeded declaration", None)


# --- results ----------------------------------------------------------------------------


@pytest.fixture
def opened(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> RunHandle:
    """An open run of the E1-H fixture (`direction = less`, `threshold = 0`,
    `min_clusters = 30`)."""
    research.register_experiment(conn, e1h, "owner")
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    return _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")


@pytest.mark.parametrize(
    ("overrides", "verdict"),
    [
        ({}, "pass"),
        ({"primary_value": -0.2, "primary_ci_high": 0.0}, "fail"),
        ({"n_clusters": 29}, "underpowered"),
    ],
)
def test_write_result_records_ok_with_a_verdict_from_the_interval(
    conn: duckdb.DuckDBPyConnection, opened: RunHandle, overrides: dict[str, Any], verdict: str
) -> None:
    assert _ok(conn, opened, secondary={"n_firms": 40}, exploratory={"x": 1}, **overrides) == "ok"
    row = conn.execute(
        "SELECT outcome, verdict, secondary_json, exploratory_json, n_configurations "
        "FROM research_results WHERE run_id = ?",
        [opened.run_id],
    ).fetchone()
    assert row == ("ok", verdict, '{"n_firms":40}', '{"x":1}', 1)


def test_no_threshold_is_n_a(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(conn, replace(e1h, primary_threshold=None), "owner")
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    handle = _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    _ok(conn, handle)
    assert _result(conn, handle.run_id) == ("ok", None, "n/a")


def test_an_undeclared_secondary_metric_is_refused(
    conn: duckdb.DuckDBPyConnection, opened: RunHandle
) -> None:
    with pytest.raises(ValueError, match="undeclared secondary"):
        _ok(conn, opened, secondary={"sharpe": 1.0})


def test_a_second_result_raises_and_abandon_closes_once(
    conn: duckdb.DuckDBPyConnection, opened: RunHandle, settings: Settings
) -> None:
    research.close_run(conn, opened, "abandoned", "owner stopped it")
    assert _result(conn, opened.run_id) == ("abandoned", "owner stopped it", None)
    with pytest.raises(research.RunAlreadyClosed):
        research.close_run(conn, opened, "abandoned", "again")
    with pytest.raises(research.RunAlreadyClosed):
        _ok(conn, opened)
    with pytest.raises(ValueError, match="outcome"):
        research.close_run(conn, opened, "ok")


def test_dataset_changed_during_run(
    conn: duckdb.DuckDBPyConnection, opened: RunHandle, tmp_path: Path
) -> None:
    Path(opened.dataset.path).write_text("event_date,value\n2020-01-02,9\n", encoding="utf-8")
    assert _ok(conn, opened) == "failed"
    assert _result(conn, opened.run_id) == ("failed", "dataset changed during run", None)


def test_store_changed_during_run(conn: duckdb.DuckDBPyConnection, opened: RunHandle) -> None:
    conn.execute(
        "INSERT INTO securities (security_id, cik, name, benchmark, known_at, ingested_at, "
        "source, provenance) VALUES ('S1', '0000000001', 'Acme', FALSE, now(), now(), "
        "'edgar', 'filing')"
    )
    assert _ok(conn, opened) == "failed"
    assert _result(conn, opened.run_id) == ("failed", "store changed during run", None)


def test_a_handle_from_another_store_is_refused_by_every_write(
    opened: RunHandle, tmp_path: Path
) -> None:
    other = _connect(tmp_path / "other.duckdb")
    with pytest.raises(research.ResearchError, match="another store"):
        research.close_run(other, opened, "failed")
    with pytest.raises(research.ResearchError, match="another store"):
        _ok(other, opened)


# --- handles, attach, the real store ----------------------------------------------------


def test_a_handle_is_built_only_by_the_registry() -> None:
    with pytest.raises(TypeError):
        RunHandle()
    with pytest.raises(NoRunHandle):
        require_handle(1)
    with pytest.raises(NoRunHandle):
        load_dataset(1)


def test_a_synthetic_run_is_refused_on_the_real_store(
    settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    real = Path(settings.store.path)
    _connect(real).close()
    link = tmp_path / "link.duckdb"
    link.symlink_to(real)
    with duckdb.connect(str(link)) as c:
        research.register_experiment(c, e1h, "owner")
        ds = _dataset(c, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
        with pytest.raises(registry.RealStoreRefused):
            _open(c, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot", synthetic=True)
        assert c.execute("SELECT COUNT(*) FROM research_runs").fetchone() == (0,)
        assert _open(c, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot").refusal is None


def test_attach_run_and_its_four_refusals(
    settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    real = _connect(Path(settings.store.path))
    research.register_experiment(
        real, replace(e1h, budget_runs=9, budget_configurations=9), "owner"
    )
    ds = _dataset(real, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    run = _open(real, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot")
    attached = research.attach_run(real, run.run_id, settings=settings)
    assert (attached.run_id, attached.refusal, attached.dataset) == (run.run_id, None, ds)
    with pytest.raises(research.UnknownRegistration):
        research.attach_run(real, 99, settings=settings)
    research.close_run(real, attached, "failed", "crashed")
    with pytest.raises(research.RunAlreadyClosed):
        research.attach_run(real, run.run_id, settings=settings)
    # A synthetic row on the real store (as if written before the check existed).
    real.execute(
        "INSERT INTO research_runs SELECT * REPLACE (2 AS run_id, TRUE AS synthetic) "
        "FROM research_runs WHERE run_id = 1"
    )
    with pytest.raises(registry.RealStoreRefused):
        research.attach_run(real, 2, settings=settings)
    real.close()
    scratch = _connect(tmp_path / "scratch.duckdb")
    research.register_experiment(
        scratch, replace(e1h, budget_runs=9, budget_configurations=9), "owner"
    )
    ds2 = _dataset(scratch, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    plain = _open(scratch, settings, tmp_path, e1h.slug, ds2.dataset_id, "pilot")
    with pytest.raises(research.ResearchError, match="only on the real store"):
        research.attach_run(scratch, plain.run_id, settings=settings)
    synthetic = _open(
        scratch, settings, tmp_path, e1h.slug, ds2.dataset_id, "pilot", synthetic=True
    )
    assert research.attach_run(scratch, synthetic.run_id, settings=settings).synthetic


# --- load_dataset -----------------------------------------------------------------------


def test_load_dataset_returns_only_the_bound_split(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(
        conn, replace(e1h, splits=("pilot", "dev", "full"), budget_configurations=5), "owner"
    )
    ds = _dataset(
        conn,
        tmp_path,
        name=e1h.dataset_name,
        dates=[date(2018, 1, 2), date(2019, 1, 2), date(2020, 1, 2)],
        splits=["pilot", "dev", "pilot"],
    )
    pilot = load_dataset(_open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "pilot"))
    assert pilot["value"].to_list() == [0, 2]
    full = load_dataset(_open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "full"))
    assert isinstance(full, pl.DataFrame)
    assert full.height == 3


@pytest.mark.parametrize("which", ["export", "split file"])
def test_load_dataset_checks_hashes_before_parsing(
    opened: RunHandle, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    parsed: list[bytes] = []
    real_parse = research_pkg._parse_tabular

    def recording(data: bytes, suffix: str) -> pl.DataFrame:
        parsed.append(data)
        return real_parse(data, suffix)

    monkeypatch.setattr(research_pkg, "_parse_tabular", recording)
    target = opened.dataset.path if which == "export" else opened.dataset.split_path
    assert target is not None
    with Path(target).open("a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(DatasetChanged, match=which):
        load_dataset(opened)
    assert parsed == []


# --- family_run_count -------------------------------------------------------------------


def test_family_run_count_sums_ok_return_configurations_outside_holdout_spends(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """Counted: an `ok`, non-synthetic return run's configurations. Not counted: a
    synthetic run, a `benchmark` run, a failed run, a holdout-spending run."""
    _gated(conn, settings, e1h)
    before_holdout = date(2023, 12, 31)
    for parsed in (
        _returns(e1h, "r0", window_end=before_holdout),
        _returns(
            e1h,
            "bench",
            kind="benchmark",
            stage=3,
            touches_returns=False,
            window_end=before_holdout,
        ),
    ):
        research.register_experiment(conn, parsed, "owner")
    ds = _dataset(conn, tmp_path)
    counted = _open(conn, settings, tmp_path, "r0", ds.dataset_id, configurations=3)
    assert _ok(conn, counted, n_configurations=3) == "ok"
    synthetic = _open(conn, settings, tmp_path, "r0", ds.dataset_id, synthetic=True)
    _ok(conn, synthetic)
    bench = _open(conn, settings, tmp_path, "bench", ds.dataset_id)
    _ok(conn, bench)
    failed = _open(conn, settings, tmp_path, "r0", ds.dataset_id)
    research.close_run(conn, failed, "failed", "crash")
    spend = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert spend.refusal is None
    _ok(conn, spend)
    assert research.family_run_count(conn, "momentum") == 3
    listed = research.list_runs(conn, family="momentum")
    assert [r.run_id for r in listed] == sorted((r.run_id for r in listed), reverse=True)
    assert all(not r.synthetic for r in listed)
    assert len(research.list_runs(conn, family="momentum", include_synthetic=True)) == (
        len(listed) + 1
    )


def test_a_run_without_a_result_lists_as_unfinished(
    conn: duckdb.DuckDBPyConnection, opened: RunHandle
) -> None:
    (summary,) = research.list_runs(conn, registration=opened.slug)
    assert (summary.run_id, summary.outcome, summary.verdict) == (opened.run_id, "unfinished", None)


# --- quant-auditor pass 1 (PR #1007): every-row splits and repeat scoping ------------


def test_a_split_file_may_not_label_rows_full_or_none(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(research.ResearchError, match="`full` or `none`"):
        _dataset(conn, tmp_path, dates=_DEV_TEST, splits=["none", "dev"])


def test_a_full_run_binds_every_row_so_it_touches_every_sealed_period(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    """`full` reads the sealed `test` rows too, so the gate checks the dataset's
    whole span (req 5), not one label's."""
    research.register_experiment(
        conn,
        _variant(
            e1h,
            "b1",
            kind="benchmark",
            stage=3,
            confirmatory=False,
            dataset_name="labels",
            splits=("full",),
            budget_configurations=5,
        ),
        "owner",
    )
    ds = _dataset(
        conn,
        tmp_path,
        name="labels",
        dates=[date(2018, 3, 1), date(2022, 3, 1)],
        splits=["dev", "test"],
        periods=((date(2022, 1, 1), date(2022, 12, 31)),),
    )
    assert _open(conn, settings, tmp_path, "b1", ds.dataset_id).refusal == "refused_holdout"
    spent = _open(conn, settings, tmp_path, "b1", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert spent.refusal is None
    (values,) = conn.execute(
        "SELECT values_json FROM research_decisions WHERE run_id = ?", [spent.run_id]
    ).fetchone()  # type: ignore[misc]
    assert json.loads(values)["sealed_periods"] == [["2022-01-01", "2022-12-31"]]
    assert load_dataset(spent).height == 2


def test_a_split_the_dataset_has_no_rows_of_is_refused_before_any_write(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(conn, replace(e1h, splits=("pilot", "dev")), "owner")
    ds = _dataset(conn, tmp_path, name=e1h.dataset_name, splits=["pilot", "pilot"])
    with pytest.raises(research.ResearchError, match="no 'dev' rows"):
        _open(conn, settings, tmp_path, e1h.slug, ds.dataset_id, "dev")
    assert conn.execute("SELECT COUNT(*) FROM research_runs").fetchone() == (0,)


def test_a_moved_or_newly_locked_export_is_a_new_version(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    dates = [date(2021, 1, 4), date(2022, 6, 30)]
    csv, _ = _export(tmp_path, "labels", dates)
    moved = tmp_path / "moved.csv"
    moved.write_bytes(csv.read_bytes())

    def register(path: Path, locked: bool) -> research.DatasetRecord:
        return research.register_dataset(
            conn,
            name="labels",
            version="v1",
            path=str(path),
            sha256=hash_file(path),
            event_start=dates[0],
            event_end=dates[1],
            locked=locked,
            repo_dir=tmp_path,
        )

    first = register(csv, locked=False)
    assert register(csv, locked=False) == first
    locked = register(csv, locked=True)
    assert (locked.dataset_id, locked.locked) == (first.dataset_id + 1, True)
    assert register(moved, locked=True).path == str(moved)


def test_a_spend_in_another_family_on_another_dataset_is_not_a_repeat(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    _gated(conn, settings, e1h)
    registry.register_hypothesis(
        conn,
        slug="o1",
        family="oracle",
        title="o1",
        doc_path="docs/hypotheses/o1.md",
        doc_sha256="o" * 64,
        params={"costs.per_side_bps": 15.0},
        in_sample_start=date(2016, 1, 29),
        holdout_start=HOLDOUT[0],
        holdout_end=HOLDOUT[1],
        registered_by="owner",
        settings=settings,
    )
    research.register_experiment(
        conn, _returns(e1h, "o", family="oracle", dataset_name="other"), "owner"
    )
    other = _dataset(conn, tmp_path, name="other")
    assert (
        _open(conn, settings, tmp_path, "o", other.dataset_id, flags=SPEND, reasons=WHY).refusal
        is None
    )
    ds = _dataset(conn, tmp_path)
    mine = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert mine.refusal is None
    assert conn.execute(
        "SELECT holdout_repeat FROM research_runs WHERE run_id = ?", [mine.run_id]
    ).fetchone() == (False,)


def test_a_synthetic_spend_is_not_a_repeat(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    _gated(conn, settings, e1h)
    ds = _dataset(conn, tmp_path)
    synthetic = _open(
        conn, settings, tmp_path, "r1", ds.dataset_id, synthetic=True, flags=SPEND, reasons=WHY
    )
    assert synthetic.refusal is None
    real = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY)
    assert real.refusal is None
    assert conn.execute(
        "SELECT holdout_repeat FROM research_runs WHERE run_id = ?", [real.run_id]
    ).fetchone() == (False,)


def test_the_sealed_test_scored_on_one_version_makes_a_redrawn_version_a_repeat(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    research.register_experiment(
        conn,
        _variant(
            e1h,
            "b1",
            kind="benchmark",
            stage=3,
            splits=("dev", "test"),
            dataset_name="labels",
            budget_runs=5,
            budget_configurations=5,
        ),
        "owner",
    )
    p2022 = (date(2022, 1, 1), date(2022, 12, 31))
    v1 = _dataset(
        conn,
        tmp_path,
        name="labels",
        stem="v1",
        dates=[date(2018, 3, 1), date(2022, 3, 1)],
        splits=["dev", "test"],
        periods=(p2022,),
        seed=e1h.seed,
    )
    assert (
        _open(
            conn, settings, tmp_path, "b1", v1.dataset_id, "test", flags=SPEND, reasons=WHY
        ).refusal
        is None
    )
    v2 = _dataset(
        conn,
        tmp_path,
        name="labels",
        stem="v2",
        dates=[date(2018, 3, 1), date(2023, 3, 1)],
        splits=["dev", "test"],
        periods=(p2022, (date(2023, 1, 1), date(2023, 12, 31))),
        seed=e1h.seed,
    )
    again = _open(conn, settings, tmp_path, "b1", v2.dataset_id, "test", flags=SPEND, reasons=WHY)
    assert again.refusal == "refused_holdout"
    assert "the sealed split was already scored" in str(again.message)


def test_a_research_spend_marks_a_later_backtest_holdout_run_a_repeat(
    conn: duckdb.DuckDBPyConnection, settings: Settings, e1h: ParsedExperiment, tmp_path: Path
) -> None:
    from tradepartner.backtest import holdout as backtest_holdout

    _gated(conn, settings, e1h)
    hypothesis = registry.get_hypothesis(conn, "h1")
    ds = _dataset(conn, tmp_path)
    spent = _open(conn, settings, tmp_path, "r1", ds.dataset_id, flags=SPEND, reasons=WHY)
    (spend,) = registry.family_holdout_spends(conn, "momentum")
    assert (spend.source, spend.trial_id, spend.hypothesis_id) == (
        "research_run",
        spent.run_id,
        None,
    )
    decision = backtest_holdout.decide(
        backtest_holdout.Window(*HOLDOUT),
        backtest_holdout.Frozen(
            hypothesis_id=hypothesis.hypothesis_id,
            in_sample_start=hypothesis.in_sample_start,
            holdout_start=hypothesis.holdout_start,
            holdout_end=hypothesis.holdout_end,
            gap_count_share_threshold=0.05,
        ),
        backtest_holdout.Flags(spend_holdout=True),
        backtest_holdout.Reasons(holdout_reason="backtest look"),
        None,
        [spend],
    )
    assert (decision.outcome, decision.holdout_repeat) == ("needs_gap", True)
