"""The research-registry commands: `experiment register|open|abandon`,
`experiments` and `dataset register` (research-registry plan T83; spec req 11
and req 14).

The live `settings.store.path` (from `STORE__PATH`, no `.env`) is a fresh temp
file and `research.experiments_dir` a temp directory whose sibling
`research/claims.toml` is the fixture claims file: the CLI has no store-path or
`--synthetic` option, so every run it opens is a non-synthetic run on the
"real" store, which is what the owner gets. The holdout scenario is the spec's:
a fixture hypothesis in family `momentum` with holdout `[2024-01-01,
2024-12-31]` and a `return` experiment whose dataset spans `[2023-01-01,
2024-06-30]`.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import polars as pl
import pytest
import typer
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.calendar import all_sessions
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings, get_settings
from tradepartner.research import _issue_run_handle
from tradepartner.research.event_study.run import run_study
from tradepartner.research.experiment import hash_file, parse_experiment_file
from tradepartner.store import registry, research, schema
from tradepartner.store.db import configure_connection, insert_row, open_for_write

FIXTURES = Path(__file__).resolve().parent / "fixtures"
E1H = "e1h-demand-deterioration-revenue"
SLUG = "cli-return-panel"
USER_AGENT = "Test Owner cli-owner@example.com"
ALPACA_KEY = "PK" + "T" * 20  # key-shaped for the scrub, not a real key
ALPACA_SECRET = "not-a-real-secret-" + "s" * 12

RETURN_EXPERIMENT = f"""# Experiment: CLI test (fixture, not a real experiment)

```toml experiment
slug = "{SLUG}"
kind = "return"
stage = 6
title = "CLI test return run"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "momentum"
claims = ["ER-4"]
seed = 7

[dataset]
name = "panel"

[window]
start = 2020-01-01
end = 2024-12-31

splits = ["full"]

[primary]
metric = "rank_ic"
direction = "greater"
threshold = 0.0
ci_level = 0.95
min_clusters = 10
inference = "issuer-cluster bootstrap"
secondary = []
comparison_set = "none"

[multiplicity]
method = "none"

[budget]
runs = 10
configurations = 20
stop_rule = "none"
expected_effect = "none"
```
"""


BENCH = "cli-benchmark-plain"
BENCH_EXPERIMENT = (
    RETURN_EXPERIMENT.replace(SLUG, BENCH)
    .replace('kind = "return"', 'kind = "benchmark"')
    .replace("stage = 6", "stage = 3")
    .replace("touches_returns = true", "touches_returns = false")
    .replace('family = "momentum"\n', "")
    .replace('name = "panel"', 'name = "plain"')
)


@pytest.fixture(autouse=True)
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The live store is a temp file; secrets are set so a leak is detectable."""
    experiments = tmp_path / "docs" / "experiments"
    experiments.mkdir(parents=True)
    (tmp_path / "docs" / "research").mkdir()
    shutil.copy(FIXTURES / "research" / "claims.toml", tmp_path / "docs" / "research")
    shutil.copy(FIXTURES / "experiments" / f"{E1H}.md", experiments)
    (experiments / f"{SLUG}.md").write_text(RETURN_EXPERIMENT, encoding="utf-8")
    (experiments / f"{BENCH}.md").write_text(BENCH_EXPERIMENT, encoding="utf-8")
    store = tmp_path / "store.duckdb"
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(store))
    monkeypatch.setenv("RESEARCH__EXPERIMENTS_DIR", str(experiments))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", USER_AGENT)
    monkeypatch.setenv("ALPACA_API_KEY", ALPACA_KEY)
    monkeypatch.setenv("ALPACA_API_SECRET", ALPACA_SECRET)
    return store


@pytest.fixture
def experiments_dir(live: Path) -> Path:
    return Path(get_settings().research.experiments_dir)


class Out:
    def __init__(self, result: Any) -> None:
        self.exit_code: int = result.exit_code
        self.stdout: str = result.stdout
        self.output: str = result.output


def _cli(*args: str) -> Out:
    out = Out(CliRunner().invoke(cli.make_app(), list(args)))
    _, hits = scrub_text(out.output, secrets=[USER_AGENT, ALPACA_KEY, ALPACA_SECRET])
    assert hits == 0, out.output
    return out


def _rows(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with duckdb.connect(get_settings().store.path, read_only=True) as conn:
        return conn.execute(sql, list(params)).fetchall()


def _count(table: str) -> int:
    path = Path(get_settings().store.path)
    if not path.exists():
        return 0
    with duckdb.connect(str(path), read_only=True) as conn:
        present = conn.execute(
            "SELECT COUNT(*) FROM duckdb_tables() WHERE table_name = ?", [table]
        ).fetchone()
        assert present is not None
        if not present[0]:
            return 0
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _csv(
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


def _first_int(out: Out, prefix: str) -> int:
    for line in out.stdout.splitlines():
        if line.startswith(prefix + " "):
            return int(line.split()[1].rstrip(":"))
    raise AssertionError(f"no {prefix} id printed:\n{out.output}")


def _dataset(tmp_path: Path, *extra: str, stem: str = "panel", name: str = "panel") -> int:
    dates = [date(2023, 1, 3), date(2024, 6, 28)]
    csv, _ = _csv(tmp_path, stem, dates)
    out = _cli(
        "dataset",
        "register",
        "--name",
        name,
        "--version",
        "v1",
        "--path",
        str(csv),
        "--event-start",
        "2023-01-01",
        "--event-end",
        "2024-06-30",
        "--event-column",
        "event_date",
        *extra,
    )
    assert out.exit_code == 0, out.output
    return _first_int(out, "dataset")


def _hypothesis() -> None:
    """A fixture hypothesis in `momentum` whose holdout is 2024."""
    s = get_settings()
    with open_for_write(s) as conn:
        schema.init_schema(conn)
        registry.register_hypothesis(
            conn,
            slug="h1",
            family="momentum",
            title="h1",
            doc_path="docs/hypotheses/h1.md",
            doc_sha256="d" * 64,
            params={"costs.per_side_bps": 15.0},
            in_sample_start=date(2016, 1, 29),
            holdout_start=date(2024, 1, 1),
            holdout_end=date(2024, 12, 31),
            registered_by="owner",
            settings=s,
        )


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"seed": 7, "grid": {"lookback": [3, 6, 12]}}), encoding="utf-8")
    return path


@pytest.fixture
def ready(experiments_dir: Path, tmp_path: Path) -> int:
    """The return experiment registered, the hypothesis in, one `panel` dataset; its id."""
    _hypothesis()
    assert _cli("experiment", "register", str(experiments_dir / f"{SLUG}.md")).exit_code == 0
    return _dataset(tmp_path)


def _open(dataset_id: int, config_file: Path, *extra: str) -> Out:
    return _cli(
        "experiment",
        "open",
        SLUG,
        "--dataset",
        str(dataset_id),
        "--split",
        "full",
        "--config",
        str(config_file),
        *extra,
    )


# --- experiment register ---------------------------------------------------------------


def test_register_prints_the_hashes_and_a_refusal_writes_nothing(experiments_dir: Path) -> None:
    out = _cli("experiment", "register", str(experiments_dir / f"{E1H}.md"))
    assert out.exit_code == 0, out.output
    (params_sha, doc_sha) = _rows(
        "SELECT params_sha256, doc_sha256 FROM research_registrations WHERE slug = ?", E1H
    )[0]
    assert params_sha in out.stdout and doc_sha in out.stdout
    assert out.stdout.startswith("registration 1: " + E1H)

    again = _cli("experiment", "register", str(experiments_dir / f"{E1H}.md"))
    assert again.exit_code == 2
    assert "amends_sha256" in again.output
    assert _count("research_registrations") == 1


def test_register_refuses_a_file_outside_the_experiments_dir(tmp_path: Path) -> None:
    stray = tmp_path / f"{E1H}.md"
    shutil.copy(FIXTURES / "experiments" / f"{E1H}.md", stray)
    out = _cli("experiment", "register", str(stray))
    assert out.exit_code == 2
    assert "research.experiments_dir" in out.output
    assert _count("research_registrations") == 0


def test_register_refuses_a_missing_file(experiments_dir: Path) -> None:
    out = _cli("experiment", "register", str(experiments_dir / "nope.md"))
    assert out.exit_code == 2
    assert _count("research_registrations") == 0


# --- experiment open ---------------------------------------------------------------------


def test_open_refused_holdout_is_recorded_and_exits_2(ready: int, config_file: Path) -> None:
    out = _open(ready, config_file)
    assert out.exit_code == 2, out.output
    run_id = _first_int(out, "run")
    assert "refused_holdout" in out.stdout
    assert _rows("SELECT outcome FROM research_results WHERE run_id = ?", run_id) == [
        ("refused_holdout",)
    ]


def test_open_with_the_spend_flags_opens_a_spend(ready: int, config_file: Path) -> None:
    out = _open(ready, config_file, "--spend-holdout", "--holdout-reason", "planned look")
    assert out.exit_code == 0, out.output
    run_id = _first_int(out, "run")
    assert "holdout spent" in out.stdout
    assert _rows(
        "SELECT holdout_spent, holdout_repeat, holdout_reason, synthetic FROM research_runs "
        "WHERE run_id = ?",
        run_id,
    ) == [(True, False, "planned look", False)]
    assert _rows("SELECT kind FROM research_decisions WHERE run_id = ?", run_id) == [
        ("holdout_spend",)
    ]
    assert _count("research_results") == 0  # unfinished

    second = _open(ready, config_file, "--spend-holdout", "--holdout-reason", "again")
    assert second.exit_code == 2, second.output
    assert "refused_holdout" in second.stdout
    repeat = _open(
        ready, config_file, "--spend-holdout", "--holdout-reason", "again", "--holdout-repeat"
    )
    assert repeat.exit_code == 0, repeat.output
    assert "repeat" in repeat.stdout
    assert _rows(
        "SELECT holdout_repeat FROM research_runs WHERE run_id = ?", _first_int(repeat, "run")
    ) == [(True,)]


@pytest.mark.parametrize(
    "flags",
    [
        ["--spend-holdout"],
        ["--spend-holdout", "--holdout-reason", "   "],
        ["--holdout-repeat"],
        ["--holdout-repeat", "--holdout-reason", "why"],
        ["--holdout-reason", "why"],
        ["--configurations", "0"],
    ],
)
def test_inconsistent_flags_are_refused_before_any_write(
    ready: int, config_file: Path, flags: list[str]
) -> None:
    out = _open(ready, config_file, *flags)
    assert out.exit_code == 2
    assert _count("research_runs") == 0


def test_a_config_that_is_not_a_json_object_is_refused_before_any_write(
    ready: int, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert _open(ready, bad).exit_code == 2
    bad.write_text("{not json", encoding="utf-8")
    assert _open(ready, bad).exit_code == 2
    assert _open(ready, tmp_path / "missing.json").exit_code == 2
    assert _count("research_runs") == 0


def test_an_unknown_slug_or_dataset_is_refused_with_nothing_written(
    ready: int, config_file: Path
) -> None:
    out = _cli(
        "experiment",
        "open",
        "no-such-slug",
        "--dataset",
        str(ready),
        "--split",
        "full",
        "--config",
        str(config_file),
    )
    assert out.exit_code == 2
    assert _open(ready + 99, config_file).exit_code == 2
    assert _count("research_runs") == 0


def test_configurations_default_to_one_and_are_recorded(
    experiments_dir: Path, config_file: Path, tmp_path: Path
) -> None:
    assert _cli("experiment", "register", str(experiments_dir / f"{BENCH}.md")).exit_code == 0
    plain = _dataset(tmp_path, stem="plain", name="plain")
    first = _cli(
        "experiment",
        "open",
        BENCH,
        "--dataset",
        str(plain),
        "--split",
        "full",
        "--config",
        str(config_file),
    )
    assert first.exit_code == 0, first.output
    assert "holdout" not in first.stdout
    second = _cli(
        "experiment",
        "open",
        BENCH,
        "--dataset",
        str(plain),
        "--split",
        "full",
        "--config",
        str(config_file),
        "--configurations",
        "3",
        "--note",
        "grid of three",
    )
    assert second.exit_code == 0, second.output
    assert _rows(
        "SELECT n_configurations_declared, note, config_json FROM research_runs ORDER BY run_id"
    ) == [
        (1, None, '{"grid":{"lookback":[3,6,12]},"seed":7}'),
        (3, "grid of three", '{"grid":{"lookback":[3,6,12]},"seed":7}'),
    ]


# --- experiment abandon -------------------------------------------------------------------


@pytest.fixture
def open_run(ready: int, config_file: Path) -> int:
    out = _open(ready, config_file, "--spend-holdout", "--holdout-reason", "planned look")
    assert out.exit_code == 0, out.output
    return _first_int(out, "run")


def test_abandon_appends_one_abandoned_row(open_run: int) -> None:
    out = _cli("experiment", "abandon", "--run", str(open_run), "--reason", "bug in the build")
    assert out.exit_code == 0, out.output
    assert _rows("SELECT outcome, message FROM research_results WHERE run_id = ?", open_run) == [
        ("abandoned", "bug in the build")
    ]
    again = _cli("experiment", "abandon", "--run", str(open_run), "--reason", "twice")
    assert again.exit_code == 2
    assert _count("research_results") == 1


def test_abandon_refuses_a_blank_reason_and_an_unknown_run(open_run: int) -> None:
    assert _cli("experiment", "abandon", "--run", str(open_run), "--reason", " ").exit_code == 2
    assert _cli("experiment", "abandon", "--run", "999", "--reason", "x").exit_code == 2
    assert _count("research_results") == 0


# --- experiments ----------------------------------------------------------------------------


def test_experiments_lists_runs_newest_first_with_their_state(
    ready: int, config_file: Path
) -> None:
    refused = _first_int(_open(ready, config_file), "run")
    spent = _first_int(
        _open(ready, config_file, "--spend-holdout", "--holdout-reason", "look"), "run"
    )
    out = _cli("experiments")
    assert out.exit_code == 0, out.output
    lines = [line for line in out.stdout.splitlines()[1:] if line.strip()]
    assert [int(line.split()[0]) for line in lines] == [spent, refused]
    assert "unfinished" in lines[0] and "spent" in lines[0] and "exploratory" in lines[0]
    assert "refused_holdout" in lines[1] and "full" in lines[1]

    assert _cli("experiments", "--registration", "other").stdout.splitlines()[1:] == []
    assert len(_cli("experiments", "--family", "momentum").stdout.splitlines()[1:]) == 2
    assert _cli("experiments", "--kind", "benchmark").stdout.splitlines()[1:] == []


def test_experiments_hides_synthetic_runs_unless_asked(
    ready: int, tmp_path: Path, config_file: Path
) -> None:
    s = get_settings()
    elsewhere = Settings(_env_file=None, store={"path": str(tmp_path / "elsewhere.duckdb")})
    with open_for_write(s) as conn:
        clean = _dataset_row(conn, tmp_path)
        research.open_run(
            conn, SLUG, clean, "full", {"seed": 1}, "test", synthetic=True, settings=elsewhere
        )
    assert _cli("experiments").stdout.splitlines()[1:] == []
    shown = _cli("experiments", "--include-synthetic").stdout.splitlines()[1:]
    assert len(shown) == 1 and "synthetic" in shown[0]


def _dataset_row(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> int:
    dates = [date(2021, 1, 4), date(2022, 6, 30)]
    csv, _ = _csv(tmp_path, "synthetic", dates)
    return research.register_dataset(
        conn,
        name="panel",
        version="vs",
        path=str(csv),
        sha256=hash_file(csv),
        event_start=min(dates),
        event_end=max(dates),
    ).dataset_id


def test_experiments_scrubs_a_secret_in_a_run_message(open_run: int) -> None:
    reason = f"leaked {ALPACA_KEY} and {USER_AGENT}"
    assert _cli("experiment", "abandon", "--run", str(open_run), "--reason", reason).exit_code == 0
    out = _cli("experiments")  # `_cli` asserts no scrub hit in the output
    assert "abandoned" in out.stdout


def test_experiments_without_a_store_exits_1() -> None:
    out = _cli("experiments")
    assert out.exit_code == 1


def test_experiments_on_an_uninitialised_store_file_exits_1(live: Path) -> None:
    duckdb.connect(str(live)).close()
    out = _cli("experiments")
    assert out.exit_code == 1
    assert "research registry not initialised" in out.output


def test_experiments_on_a_store_without_research_tables_says_so(live: Path) -> None:
    with duckdb.connect(str(live)) as conn:
        schema.init_schema(conn)
        for table in schema.RESEARCH_TABLE_NAMES:
            conn.execute(f"DROP TABLE {table}")
    out = _cli("experiments")
    assert out.exit_code == 1
    assert "research registry not initialised" in out.output


# --- dataset register ---------------------------------------------------------------------


def _register(path: Path, *extra: str, name: str = "labels", version: str = "v1") -> Out:
    return _cli(
        "dataset",
        "register",
        "--name",
        name,
        "--version",
        version,
        "--path",
        str(path),
        "--event-start",
        "2020-01-01",
        "--event-end",
        "2024-12-31",
        *extra,
    )


def test_dataset_register_records_hash_rows_spans_and_sealing(tmp_path: Path) -> None:
    dates = [date(2021, 3, 1), date(2022, 3, 1), date(2024, 3, 1), date(2024, 9, 1)]
    csv, split_file = _csv(tmp_path, "labels", dates, ["dev", "dev", "test", "test"])
    assert split_file is not None
    out = _register(
        csv,
        "--event-column",
        "event_date",
        "--split-json",
        str(split_file),
        "--sealed-period",
        "2024-01-01",
        "2024-12-31",
        "--seed",
        "11",
        "--locked",
        "--note",
        "fixture labels",
    )
    assert out.exit_code == 0, out.output
    dataset_id = _first_int(out, "dataset")
    with duckdb.connect(get_settings().store.path, read_only=True) as conn:
        record = research.get_dataset(conn, dataset_id)
    assert record.sha256 == hash_file(csv)
    assert record.split_sha256 == hash_file(split_file)
    assert record.n_rows == 4
    assert record.event_column == "event_date"
    assert record.sealed_splits == ("test",)  # by implication
    assert record.sealed_periods == ((date(2024, 1, 1), date(2024, 12, 31)),)
    assert record.split_spans == {
        "dev": (date(2021, 3, 1), date(2022, 3, 1)),
        "test": (date(2024, 3, 1), date(2024, 9, 1)),
    }
    assert record.locked and record.seed == 11 and record.note == "fixture labels"
    assert record.sha256 in out.stdout

    same = _register(
        csv,
        "--event-column",
        "event_date",
        "--split-json",
        str(split_file),
        "--sealed-period",
        "2024-01-01",
        "2024-12-31",
        "--seed",
        "11",
        "--locked",
        "--note",
        "fixture labels",
    )
    assert _first_int(same, "dataset") == dataset_id

    shrinks = _register(csv, "--event-column", "event_date", version="v2")
    assert shrinks.exit_code == 2
    assert "sealed set shrinks" in shrinks.output


def test_dataset_register_without_a_split_file_spans_full(tmp_path: Path) -> None:
    csv, _ = _csv(tmp_path, "plain", [date(2021, 3, 1)])
    out = _register(csv)
    assert out.exit_code == 0, out.output
    with duckdb.connect(get_settings().store.path, read_only=True) as conn:
        record = research.get_dataset(conn, _first_int(out, "dataset"))
    assert record.split_spans == {"full": (date(2020, 1, 1), date(2024, 12, 31))}
    assert record.n_rows == 1


def test_dataset_register_hashes_a_directory_export(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    (export / "a.txt").write_text("a", encoding="utf-8")
    out = _register(export)
    assert out.exit_code == 0, out.output
    with duckdb.connect(get_settings().store.path, read_only=True) as conn:
        record = research.get_dataset(conn, _first_int(out, "dataset"))
    assert record.n_rows is None

    sealed = _register(export, "--sealed", "dev", "--sealed-period", "2020-01-01", "2020-12-31")
    assert sealed.exit_code == 2
    assert "sealed split without period" in sealed.output
    column = _register(export, "--event-column", "event_date")
    assert column.exit_code == 2
    assert "tabular export" in column.output


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--split-json", "SPLIT"], "split without event column"),
        (["--event-column", "event_date", "--sealed", "test"], "sealed split without period"),
        (
            [
                "--event-column",
                "event_date",
                "--split-json",
                "SPLIT",
            ],
            "sealed split without period",
        ),
        (
            [
                "--event-column",
                "event_date",
                "--split-json",
                "SPLIT",
                "--sealed-period",
                "2024-01-01",
                "2024-06-30",
            ],
            "sealed split without period",
        ),
        (["--event-column", "missing"], "event column"),
        (["--sealed-period", "2024-01-01"], "--sealed-period"),
        (["--sealed-period", "2024-01-01", "not-a-date"], "--sealed-period"),
        (["--synthetic"], "--synthetic"),
        (["--sealed", "cla"], "not among the splits"),
        (
            [
                "--event-column",
                "event_date",
                "--split-json",
                "SPLIT",
                "--sealed",
                "full",
                "--sealed-period",
                "2024-01-01",
                "2024-12-31",
            ],
            "sealed split without period",
        ),
        (
            [
                "--event-column",
                "event_date",
                "--sealed",
                "none",
                "--sealed-period",
                "2030-01-01",
                "2030-01-02",
            ],
            "sealed split without period",
        ),
        (["--event-column", "event_date", "--split-json", "MISSING"], "refused"),
    ],
)
def test_dataset_register_refusals_write_nothing(
    tmp_path: Path, extra: list[str], message: str
) -> None:
    dates = [date(2021, 3, 1), date(2024, 9, 1)]
    csv, split_file = _csv(tmp_path, "labels", dates, ["dev", "test"])
    swap = {"SPLIT": str(split_file), "MISSING": str(tmp_path / "missing.json")}
    args = [swap.get(a, a) for a in extra]
    out = _register(csv, *args)
    assert out.exit_code == 2, out.output
    assert message in out.output
    assert _count("research_datasets") == 0


def test_dataset_register_refuses_an_unparseable_export(tmp_path: Path) -> None:
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not parquet at all")
    out = _register(bad, "--event-column", "event_date")
    assert out.exit_code == 2, out.output
    assert _count("research_datasets") == 0


def test_dataset_register_refuses_a_declared_span_the_data_exceed(tmp_path: Path) -> None:
    csv, _ = _csv(tmp_path, "wide", [date(2019, 6, 1), date(2021, 3, 1)])
    out = _register(csv, "--event-column", "event_date")
    assert out.exit_code == 2
    assert "exceeded" in out.output
    assert _count("research_datasets") == 0


def test_dataset_register_refuses_a_missing_export(tmp_path: Path) -> None:
    out = _register(tmp_path / "absent.csv")
    assert out.exit_code == 2
    assert _count("research_datasets") == 0


# --- experiment study (amendment 2026-10-28 #2440, plan T304) -----------------------------

STUDY = "cli-event-study"
STUDY_SECURITIES = tuple(f"S{i}" for i in range(1, 8))
#: Monthly event dates (all XNYS sessions), across two calendar quarters.
STUDY_DAYS = (date(2018, 2, 14), date(2018, 3, 14), date(2018, 4, 13), date(2018, 5, 15))


def _study_experiment(secondary: Sequence[str] = ()) -> str:
    """A small C1b-shaped `[study]` file on the synthetic store below."""
    listed = ", ".join(f'"{key}"' for key in secondary)
    return f'''# Experiment: CLI study test (fixture, not a real experiment)

```toml experiment
slug = "{STUDY}"
kind = "return"
stage = 6
title = "CLI study test"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "momentum"
claims = ["ER-4"]
seed = 7
splits = ["full"]

[dataset]
name = "events"

[window]
start = 2018-01-02
end = 2018-07-31

[primary]
metric = "early_minus_matched_5_net30_bp"
direction = "greater"
ci_level = 0.95
min_clusters = 2
inference = "month-clustered, one-sided 95%"
secondary = [{listed}]
comparison_set = "none"

[multiplicity]
method = "none"

[budget]
runs = 5
configurations = 10
stop_rule = "none"
expected_effect = "none"

[study]
configurations = 2
horizons = [5]
groups = ["early", "late", "normal"]
peer_group = "normal"
entry = "next_session_after_date"
facts_read = "event_known_at"
bucket_by = ["quarter", "stratum"]
peer_by = ["stratum"]
n_buckets = 1
min_bucket_events = 1
price_floor = 1.0
volume_floor = 1.0
volume_lookback = 5
min_volume_bars = 3
shares_stale_days = 400
shares_fact = "shares_outstanding"
benchmark_security = "SPY"
peer_window = 2
min_peers = 1
near_k = 2
beta_lookback = 20
beta_min_bars = 10
cost_bps = [15.0, 30.0]
borrow_bps = [0.0, 30.0]
z = 1.645
cluster_by = ["month", "quarter"]

[[study.kill]]
name = "long"
side = "long"
group = "early"
horizon = 5
readings = ["raw"]
metric = "gross_spread_bp"
floor = 60.0
t_metric = "net_bp"
t_floor = 1.0
t = "min"
cost_bps = 30.0
borrow_bps = 0.0

[[study.kill]]
name = "short"
side = "short"
group = "late"
horizon = 5
readings = ["raw"]
metric = "net_bp"
floor = 60.0
t_metric = "net_bp"
t_floor = 1.0
t = "min"
cost_bps = 30.0
borrow_bps = 30.0
```
'''


def _study_store(path: Path) -> None:
    """A synthetic market store: seven names and SPY, bars over the window, shares."""
    sessions = [d for d in all_sessions() if date(2017, 12, 1) <= d <= date(2018, 7, 31)]
    early = datetime(2017, 11, 1, 21, tzinfo=UTC)
    with duckdb.connect(str(path)) as conn:
        configure_connection(conn)
        schema.init_schema(conn)
        for sid, ticker, benchmark in (
            *((sid, f"T{sid}", False) for sid in STUDY_SECURITIES),
            ("SPY_ID", "SPY", True),
        ):
            for table, row in (
                ("securities", {"security_id": sid, "cik": sid, "name": sid}),
                (
                    "listings",
                    {
                        "security_id": sid,
                        "ticker": ticker,
                        "exchange": "XNYS",
                        "valid_from": date(2017, 1, 3),
                    },
                ),
            ):
                extra = {"benchmark": benchmark} if table == "securities" else {}
                insert_row(
                    conn,
                    table,
                    {
                        **row,
                        **extra,
                        "known_at": early,
                        "ingested_at": early,
                        "source": "test",
                        "provenance": "snapshot",
                    },
                )
            insert_row(
                conn,
                "facts",
                {
                    "security_id": sid,
                    "fact_name": "shares_outstanding",
                    "as_of_date": date(2017, 11, 1),
                    "class_member": "",
                    "value": 1_000_000.0 * (1 + len(ticker)),
                    "known_at": early,
                    "ingested_at": early,
                    "source": "test",
                    "provenance": "filing",
                },
            )
            k = 0 if benchmark else int(sid[1:])
            for t, session in enumerate(sessions):
                close = 20.0 + k + 0.01 * t * (k - 3) + 0.4 * ((t * (k + 2) + k) % 5)
                known_at = datetime.combine(session, datetime.min.time(), UTC).replace(hour=21)
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": session,
                        "open": close * 0.995,
                        "high": close,
                        "low": close * 0.99,
                        "close": close,
                        "volume": 100_000,
                        "known_at": known_at,
                        "ingested_at": known_at,
                        "source": "test",
                        "provenance": "bar",
                    },
                )


def _study_events(tmp_path: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Per month one early, one late and three normal events, as a parquet export."""
    rows: list[dict[str, Any]] = []
    for m, day in enumerate(STUDY_DAYS):
        known_at = datetime.combine(day, datetime.min.time(), UTC).replace(hour=21)
        for j, (sid, group) in enumerate(
            (
                (STUDY_SECURITIES[m % 2], "early"),
                (STUDY_SECURITIES[2 + m % 2], "late"),
                (STUDY_SECURITIES[4], "normal"),
                (STUDY_SECURITIES[5], "normal"),
                (STUDY_SECURITIES[6], "normal"),
            )
        ):
            rows.append(
                {
                    "event_id": f"ev-{m}-{j}",
                    "security_id": sid,
                    "known_at": known_at,
                    "group": group,
                    "stratum": "10-Q",
                }
            )
    path = tmp_path / "events.parquet"
    pl.DataFrame(rows).write_parquet(path)
    return path, rows


def _study_dataset(conn: duckdb.DuckDBPyConnection, export: Path) -> int:
    return research.register_dataset(
        conn,
        name="events",
        version="v1",
        path=str(export),
        sha256=hash_file(export),
        event_start=STUDY_DAYS[0],
        event_end=STUDY_DAYS[-1],
    ).dataset_id


def _study_settings(store: Path, artifacts: Path) -> Settings:
    s = get_settings()
    return s.model_copy(
        update={
            "store": s.store.model_copy(update={"path": str(store)}),
            "research": s.research.model_copy(update={"data_dir": str(artifacts)}),
        }
    )


def _dry_secondary(s: Settings, rows: list[dict[str, Any]], tmp_path: Path) -> list[str]:
    """The runner's secondary keys for the study file, from an unregistered dry run
    (so the file can declare exactly what the run will report)."""
    parsed = parse_experiment_file(
        _write_study_file(tmp_path / "dry" / "experiments", ()),
        tmp_path / "dry" / "experiments",
        settings=s,
    )
    handle = _issue_run_handle(
        run_id=999,
        slug=STUDY,
        dataset=SimpleNamespace(
            event_start=STUDY_DAYS[0], event_end=STUDY_DAYS[-1], split_spans={}
        ),
        n_configurations_declared=2,
        refusal=None,
        database=str(Path(s.store.path).resolve()),
        split="full",
    )
    dry = s.model_copy(
        update={"research": s.research.model_copy(update={"data_dir": str(tmp_path / "dry")})}
    )
    return sorted(run_study(handle, parsed, rows, settings=dry).secondary)


def _write_study_file(directory: Path, secondary: Sequence[str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    research_dir = directory.parent / "research"
    research_dir.mkdir(exist_ok=True)
    shutil.copy(FIXTURES / "research" / "claims.toml", research_dir)
    path = directory / f"{STUDY}.md"
    path.write_text(_study_experiment(secondary), encoding="utf-8")
    return path


@pytest.fixture
def study_ready(
    live: Path, experiments_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    """The study file registered on the live store (secondary declared), its dataset id."""
    monkeypatch.setenv("RESEARCH__DATA_DIR", str(tmp_path / "artifacts"))
    _study_store(live)
    export, rows = _study_events(tmp_path)
    s = _study_settings(live, tmp_path / "artifacts")
    _write_study_file(experiments_dir, _dry_secondary(s, rows, tmp_path))
    out = _cli("experiment", "register", str(experiments_dir / f"{STUDY}.md"))
    assert out.exit_code == 0, out.output
    with open_for_write(s) as conn:
        return _study_dataset(conn, export)


def _open_study(dataset_id: int, config_file: Path, *extra: str) -> Out:
    return _cli(
        "experiment",
        "open",
        STUDY,
        "--dataset",
        str(dataset_id),
        "--split",
        "full",
        "--config",
        str(config_file),
        *extra,
    )


def test_open_takes_configurations_from_the_study_table(
    study_ready: int, config_file: Path
) -> None:
    out = _open_study(study_ready, config_file)
    assert out.exit_code == 0, out.output
    assert "2 configurations" in out.stdout
    same = _open_study(study_ready, config_file, "--configurations", "2")
    assert same.exit_code == 0, same.output
    assert _rows("SELECT n_configurations_declared FROM research_runs ORDER BY run_id") == [
        (2,),
        (2,),
    ]


def test_open_refuses_configurations_other_than_the_study_table(
    study_ready: int, config_file: Path
) -> None:
    for given in ("1", "3"):
        out = _open_study(study_ready, config_file, "--configurations", given)
        assert out.exit_code == 2, out.output
        assert "study.configurations" in out.output
    assert _count("research_runs") == 0
    assert _count("research_results") == 0


def test_study_writes_an_ok_result_with_the_artifact(
    study_ready: int, config_file: Path, tmp_path: Path
) -> None:
    run = _first_int(_open_study(study_ready, config_file), "run")
    out = _cli("experiment", "study", "--run", str(run))
    assert out.exit_code == 0, out.output
    assert f"run {run}: ok" in out.stdout and "kill lines: long" in out.stdout
    [(outcome, n_config, sha, artifact, secondary)] = _rows(
        "SELECT outcome, n_configurations, artifact_sha256, artifact_path, secondary_json "
        "FROM research_results WHERE run_id = ?",
        run,
    )
    assert (outcome, n_config) == ("ok", 2)
    assert Path(artifact).parent == tmp_path / "artifacts" / STUDY
    assert hash_file(Path(artifact)) == sha
    recorded = json.loads(secondary)
    assert recorded["n_traded_early"] == recorded["n_traded_late"] == len(STUDY_DAYS)
    assert recorded["kill_long"] in (0.0, 1.0)
    again = _cli("experiment", "study", "--run", str(run))
    assert again.exit_code == 2
    assert "already has its result row" in again.output
    assert _count("research_results") == 1


def test_study_refuses_a_run_with_a_result_row(study_ready: int, config_file: Path) -> None:
    run = _first_int(_open_study(study_ready, config_file), "run")
    assert _cli("experiment", "abandon", "--run", str(run), "--reason", "x").exit_code == 0
    out = _cli("experiment", "study", "--run", str(run))
    assert out.exit_code == 2
    assert "already has its result row" in out.output
    assert _rows("SELECT outcome FROM research_results") == [("abandoned",)]


def test_study_refuses_a_registration_without_a_study_table(open_run: int) -> None:
    out = _cli("experiment", "study", "--run", str(open_run))
    assert out.exit_code == 2
    assert "no [study] table" in out.output
    assert _count("research_results") == 0  # the run stays unfinished, nothing written


def test_study_refuses_a_handle_from_another_store(
    study_ready: int, config_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    other = tmp_path / "other.duckdb"
    shutil.copy(get_settings().store.path, other)
    s_other = _study_settings(other, tmp_path / "artifacts")
    with open_for_write(s_other) as conn:
        foreign = research.open_run(
            conn, STUDY, study_ready, "full", {}, "test", configurations=2, settings=s_other
        )
    monkeypatch.setattr(cli.research, "attach_run", lambda *args, **kwargs: foreign)
    out = _cli("experiment", "study", "--run", str(foreign.run_id))
    assert out.exit_code == 2
    assert "another store" in out.output
    assert _count("research_runs") == 0 and _count("research_results") == 0
    with duckdb.connect(str(other), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_results").fetchall() == [(0,)]


def test_study_closes_the_run_failed_on_any_exception_scrubbed(
    study_ready: int, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"bars unreadable {ALPACA_KEY}")

    monkeypatch.setattr(cli, "run_study", boom)
    run = _first_int(_open_study(study_ready, config_file), "run")
    out = _cli("experiment", "study", "--run", str(run))  # `_cli` asserts no secret printed
    assert out.exit_code == 1
    assert f"run {run}: failed" in out.stdout
    [(outcome, message)] = _rows(
        "SELECT outcome, message FROM research_results WHERE run_id = ?", run
    )
    assert outcome == "failed"
    assert message.startswith("RuntimeError: bars unreadable") and ALPACA_KEY not in message


def test_study_closes_the_run_failed_on_ctrl_c_and_exits_130(
    study_ready: int, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_study", interrupted)
    run = _first_int(_open_study(study_ready, config_file), "run")
    out = _cli("experiment", "study", "--run", str(run))
    assert out.exit_code == cli.INTERRUPTED_EXIT == 130
    assert _rows("SELECT outcome, message FROM research_results WHERE run_id = ?", run) == [
        ("failed", "KeyboardInterrupt: ")
    ]


def test_study_refuses_a_study_table_without_configurations(
    study_ready: int, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _first_int(_open_study(study_ready, config_file), "run")
    monkeypatch.setattr(cli, "_study_table", lambda record: {"horizons": [5]})
    out = _cli("experiment", "study", "--run", str(run))
    assert out.exit_code == 2
    assert "study.configurations is missing" in out.output
    assert _count("research_results") == 0


def test_study_of_a_synthetic_run_on_a_temp_store_copy_is_excluded_from_counts(
    study_ready: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = get_settings()
    copy = tmp_path / "copy.duckdb"
    shutil.copy(real.store.path, copy)
    s_copy = _study_settings(copy, tmp_path / "artifacts")
    with open_for_write(s_copy) as conn:
        handle = research.open_run(
            conn,
            STUDY,
            study_ready,
            "full",
            {},
            "test",
            synthetic=True,
            configurations=2,
            settings=real,
        )
    cli._experiment_study(s_copy, handle.run_id, real=real)
    assert f"run {handle.run_id}: ok" in capsys.readouterr().out
    with duckdb.connect(str(copy), read_only=True) as conn:
        assert conn.execute(
            "SELECT outcome, n_configurations FROM research_results WHERE run_id = ?",
            [handle.run_id],
        ).fetchall() == [("ok", 2)]
        assert research.family_run_count(conn, "momentum") == 0
        assert research.list_runs(conn) == []
    assert _count("research_runs") == 0  # the original store is untouched
    # On its configured store the command never attaches a synthetic run.
    with pytest.raises(typer.Exit) as refused:
        with open_for_write(s_copy) as conn:
            again = research.open_run(
                conn, STUDY, study_ready, "full", {}, "test", synthetic=True, settings=real
            )
        cli._experiment_study(s_copy, again.run_id)
    assert refused.value.exit_code == 2


# --- the parser: no forbidden flag or subcommand ------------------------------------------

FORBIDDEN_SUBCOMMANDS = {
    "import-interim",
    "import",
    "edit",
    "delete",
    "unseal",
    "reopen",
    "update",
    "synthetic",
}


def _walk(command: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    found: list[tuple[tuple[str, ...], Any]] = [(path, command)]
    for name, sub in getattr(command, "commands", {}).items():
        found.extend(_walk(sub, (*path, name)))
    return found


def test_no_forbidden_flag_or_subcommand_exists() -> None:
    root = typer.main.get_command(cli.make_app())
    tree = _walk(root)
    names = {path[-1] for path, _ in tree if path}
    assert {"experiment", "experiments", "dataset"} <= names
    groups = {path: cmd for path, cmd in tree if hasattr(cmd, "commands") and path}
    assert set(groups[("experiment",)].commands) == {"register", "open", "abandon", "study"}
    assert set(groups[("dataset",)].commands) == {"register"}
    assert not names & FORBIDDEN_SUBCOMMANDS
    for path, command in tree:
        for param in command.params:
            opts = set(getattr(param, "opts", ())) | set(getattr(param, "secondary_opts", ()))
            assert "--synthetic" not in opts, path
            assert not any("store" in opt for opt in opts), path


@pytest.mark.parametrize(
    "args",
    [
        ["experiment", "import-interim"],
        ["experiment", "reopen"],
        ["dataset", "unseal"],
        ["experiment", "open", SLUG, "--synthetic"],
        ["experiments", "--synthetic"],
    ],
)
def test_forbidden_invocations_fail_as_usage_errors(args: list[str]) -> None:
    assert _cli(*args).exit_code == 2
    assert _count("research_runs") == 0
