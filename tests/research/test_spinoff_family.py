"""The `spinoff` root family (#2399): an experiment-only family for the C2 event study.

It registers on an experiment file, its runs count only in its own N, and the backtest
engine and paper refuse it (it has no signal).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import get_args

import duckdb
import pytest

from tradepartner.backtest.results import family_n_split
from tradepartner.backtest.strategies import signal_for
from tradepartner.config import (
    ENGINE_FAMILIES,
    FAMILIES,
    FAMILY_PARENTS,
    PAPER_FAMILIES,
    HypothesisFamily,
    Settings,
)
from tradepartner.research.experiment import hash_file, parse_experiment_file
from tradepartner.store import research, schema

_FILE = """# Experiment: spinoff family fixture

```toml experiment
slug = "sp-family"
kind = "return"
stage = 2
title = "Fixture: a spinoff event study"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "spinoff"
claims = ["FX-1"]
seed = 1

[dataset]
name = "sp-events"

[window]
start = 2017-01-01
end = 2019-12-31

splits = ["full"]

[primary]
metric = "children_6m_spread_net30_bp"
direction = "greater"
ci_level = 0.95
min_clusters = 12
inference = "month-clustered SE"
secondary = []
comparison_set = "x"

[multiplicity]
method = "none"

[budget]
runs = 1
configurations = 2
stop_rule = "x"
expected_effect = "x"
```
"""


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


def test_spinoff_is_a_root_family_the_engine_and_paper_refuse() -> None:
    assert "spinoff" in get_args(HypothesisFamily)
    assert FAMILY_PARENTS["spinoff"] is None
    assert FAMILIES["spinoff"].parent is None
    assert "spinoff" not in ENGINE_FAMILIES
    assert "spinoff" not in PAPER_FAMILIES
    with pytest.raises(KeyError):
        signal_for("spinoff")


def test_a_spinoff_experiment_registers_and_counts_only_in_its_own_n(
    tmp_path: Path, settings: Settings
) -> None:
    root = tmp_path / "tree"
    (root / "experiments").mkdir(parents=True)
    (root / "research").mkdir()
    (root / "research" / "claims.toml").write_text(
        '[[claim]]\nid = "FX-1"\ngrade = "SUPPORTED"\n', encoding="utf-8"
    )
    path = root / "experiments" / "sp-family.md"
    path.write_text(_FILE, encoding="utf-8")
    parsed = parse_experiment_file(path, root / "experiments", settings=settings)
    assert parsed.family == "spinoff"

    conn = duckdb.connect(str(tmp_path / "scratch.duckdb"))
    schema.init_schema(conn)
    research.register_experiment(conn, parsed, "owner")
    csv = tmp_path / "events.csv"
    csv.write_text("event_date,value\n2018-03-01,1\n2019-06-03,2\n", encoding="utf-8")
    dataset = research.register_dataset(
        conn,
        name="sp-events",
        version="v1",
        path=str(csv),
        sha256=hash_file(csv),
        event_start=date(2018, 3, 1),
        event_end=date(2019, 6, 3),
        n_rows=2,
        event_column="event_date",
        split_path=None,
        split_sha256=None,
        split_spans=None,
        sealed_splits=(),
        sealed_periods=(),
        split_row_dates=None,
        locked=False,
        seed=None,
        repo_dir=tmp_path,
    )
    handle = research.open_run(
        conn,
        "sp-family",
        dataset.dataset_id,
        "full",
        {"seed": 1},
        "test",
        settings=settings,
        repo_dir=tmp_path,
        configurations=2,
    )
    assert handle.refusal is None
    research.write_result(
        conn,
        handle,
        primary_value=1.0,
        primary_ci_low=0.5,
        primary_ci_high=1.5,
        n_observations=100,
        n_clusters=36,
        n_configurations=2,
        artifact_sha256="a" * 64,
        artifact_path="/tmp/report.html",
    )
    assert json.loads(parsed.params_json)["family"] == "spinoff"
    assert family_n_split(conn, "spinoff").total == 2
    for other in ("momentum", "combined", "profitability"):
        assert family_n_split(conn, other).total == 0


def test_a_hypothesis_or_sweep_file_for_the_family_is_refused(tmp_path: Path) -> None:
    from tradepartner.backtest.hypothesis import HypothesisFileError, parse_file
    from tradepartner.backtest.sweep import SweepFileError, parse_sweep_file

    hyp = tmp_path / "sp-h.md"
    hyp.write_text(
        '```toml hypothesis\nslug = "sp-h"\nfamily = "spinoff"\ntitle = "x"\n```\n',
        encoding="utf-8",
    )
    with pytest.raises(HypothesisFileError, match="experiment-only"):
        parse_file(hyp)
    swp = tmp_path / "sp-s.md"
    swp.write_text(
        '```toml sweep\nslug = "sp-s"\nfamily = "spinoff"\ntitle = "x"\n```\n',
        encoding="utf-8",
    )
    with pytest.raises(SweepFileError, match="experiment-only"):
        parse_sweep_file(swp, Settings(_env_file=None))
