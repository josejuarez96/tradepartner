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
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
import typer
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings, get_settings
from tradepartner.research.experiment import hash_file
from tradepartner.store import registry, research, schema
from tradepartner.store.db import open_for_write

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
    assert set(groups[("experiment",)].commands) == {"register", "open", "abandon"}
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
