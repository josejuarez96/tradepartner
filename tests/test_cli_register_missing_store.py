"""The register/open commands refuse a store that does not exist (#3332).

Run from the wrong directory, or with a mistyped `STORE__PATH`, they used to
create a fresh empty store and lose the registration. Each now exits 1, names
the path and creates no file.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tradepartner import cli


@pytest.fixture
def missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    store = tmp_path / "elsewhere" / "tradepartner.duckdb"
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(store))
    monkeypatch.chdir(tmp_path)
    return store


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.make_app(), list(args))
    return result.exit_code, result.output


def _refused(code: int, output: str, store: Path) -> None:
    assert code == 1, output
    assert "no store at" in output
    assert str(store) in output
    assert not store.exists()
    assert not store.parent.exists()


def test_hypothesis_register_refuses_a_missing_store(missing: Path, tmp_path: Path) -> None:
    f = tmp_path / "h.md"
    f.write_text("x", encoding="utf-8")
    _refused(*_run("hypothesis", "register", str(f)), missing)


def test_experiment_register_refuses_a_missing_store(missing: Path, tmp_path: Path) -> None:
    f = tmp_path / "e.md"
    f.write_text("x", encoding="utf-8")
    _refused(*_run("experiment", "register", str(f)), missing)


def test_experiment_open_refuses_a_missing_store(missing: Path, tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text("{}", encoding="utf-8")
    _refused(
        *_run("experiment", "open", "s", "--dataset", "1", "--split", "full", "--config", str(cfg)),
        missing,
    )


def test_dataset_register_refuses_a_missing_store(missing: Path, tmp_path: Path) -> None:
    f = tmp_path / "d.csv"
    f.write_text("event_date\n2023-01-03\n", encoding="utf-8")
    args = ["dataset", "register", "--name", "n", "--version", "v1", "--path", str(f)]
    args += ["--event-start", "2023-01-01", "--event-end", "2023-12-31"]
    _refused(*_run(*args), missing)


def test_exposure_record_refuses_a_missing_store(missing: Path, tmp_path: Path) -> None:
    probe = tmp_path / "probe-2689.py"
    probe.write_text("print('probe')\n", encoding="utf-8")
    probe_mtime = (
        datetime.fromisoformat("2026-10-31T20:00:00+00:00") - timedelta(hours=1)
    ).timestamp()
    os.utime(probe, (probe_mtime, probe_mtime))
    kill = tmp_path / "kill.md"
    kill.write_text("kill line: fail if t < 2\n", encoding="utf-8")
    args = [
        "exposure",
        "record",
        "--candidate",
        "#2689",
        "--source",
        "store",
        "--family",
        "momentum",
        "--kill-line-url",
        "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186",
        "--kill-line-file",
        str(kill),
        "--kill-line-posted-at",
        "2026-10-31T19:29:02+00:00",
        "--probe",
        str(probe),
        "--probe-sha256",
        hashlib.sha256(probe.read_bytes()).hexdigest(),
        "--read-at",
        "2026-10-31T20:00:00+00:00",
        "--pin",
        "store_max_ingested_at=2026-10-31T04:41:00+00:00",
        "--window-start",
        "2020-08-31",
        "--window-end",
        "2023-12-29",
        "--configurations",
        "1",
        "--verdict",
        "fail",
        "--desk-read",
        "docs/research/2026-10-31-desk-read.md",
    ]
    _refused(*_run(*args), missing)
