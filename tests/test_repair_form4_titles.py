"""Fixture-store contract for the manifest-driven Form 4 title repair."""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner import repair_form4_titles as titles_module
from tradepartner.config import Settings
from tradepartner.repair_form4_titles import TitleRepairRefused, build_manifest, repair_form4_titles
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

OLD = datetime(2021, 1, 5, tzinfo=UTC)
NOW = datetime(2026, 10, 31, tzinfo=UTC)
KEY = ("0000000001-21-000001", "0000000002", 0)
NEW = 'Common Stock ("Shares")'
QUOTED = '"Common Stock (""Shares"")"'


def _fixture(settings: Settings, tmp_path: Path, title: str = QUOTED) -> tuple[Path, Path]:
    zip_path = tmp_path / "2021q1_form345.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "SUBMISSION.tsv",
            "ACCESSION_NUMBER\tFILING_DATE\tDATE_OF_ORIG_SUB\tDOCUMENT_TYPE\tISSUERCIK\n"
            f"{KEY[0]}\t05-JAN-2021\t\t4\t0000000003\n",
        )
        archive.writestr(
            "REPORTINGOWNER.tsv",
            f"ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNER_RELATIONSHIP\n{KEY[0]}\t{KEY[1]}\tDirector\n",
        )
        archive.writestr(
            "NONDERIV_TRANS.tsv",
            "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tTRANS_DATE\t"
            "DEEMED_EXECUTION_DATE\tTRANS_CODE\tTRANS_ACQUIRED_DISP_CD\tTRANS_SHARES\t"
            "TRANS_PRICEPERSHARE\tSHRS_OWND_FOLWNG_TRANS\tDIRECT_INDIRECT_OWNERSHIP\n"
            f"{KEY[0]}\t1\t{QUOTED}\t04-JAN-2021\t\tP\tA\t1\t10\t1\tD\n",
        )
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "sec_form4_transactions",
            {
                "accession": KEY[0],
                "issuer_cik": "0000000003",
                "filer_cik": KEY[1],
                "line_no": 0,
                "is_director": True,
                "is_officer": False,
                "is_ten_percent_owner": False,
                "is_other": False,
                "transaction_date": date(2021, 1, 4),
                "deemed_execution_date": None,
                "transaction_code": "P",
                "acquired_disposed": "A",
                "shares": 1,
                "price": 10,
                "shares_owned_after": 1,
                "ownership_form": "D",
                "security_title": title,
                "date_of_original_submission": None,
                "accepted_at": OLD,
                "known_at": OLD,
                "ingested_at": OLD,
                "source": "sec_insider_bulk",
                "provenance": "filing",
            },
        )
        registry.open_data_release(
            conn, conn, name="titles-2708", backup_path="b.duckdb", reason="#2708"
        )
    manifest = tmp_path / "titles.json"
    return zip_path, manifest


def _row(settings: Settings) -> tuple[object, ...]:
    with open_for_write(settings) as conn:
        return tuple(conn.execute("SELECT * FROM sec_form4_transactions").fetchone())


def test_builder_quoted_clean_and_deterministic(settings: Settings, tmp_path: Path) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    assert build_manifest(settings, [zip_path], manifest) == 1
    first = manifest.read_bytes()
    assert json.loads(first)["rows"][0]["old_title"] == QUOTED
    assert json.loads(first)["rows"][0]["new_title"] == NEW
    assert build_manifest(settings, [zip_path], manifest) == 1
    assert manifest.read_bytes() == first
    with open_for_write(settings) as conn:
        conn.execute("UPDATE sec_form4_transactions SET security_title = ?", [NEW])
    assert build_manifest(settings, [zip_path], manifest) == 0


@pytest.mark.parametrize("change", ["different", "unmatched"])
def test_builder_refuses_unrelated_titles_and_unmatched_rows(
    settings: Settings, tmp_path: Path, change: str
) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    with open_for_write(settings) as conn:
        if change == "different":
            conn.execute("UPDATE sec_form4_transactions SET security_title = 'Wrong'")
        else:
            conn.execute("UPDATE sec_form4_transactions SET accession = 'unmatched'")
    with pytest.raises(TitleRepairRefused, match="manifest not written"):
        build_manifest(settings, [zip_path], manifest)
    assert not manifest.exists()


def test_apply_dry_real_idempotent_and_all_other_columns(
    settings: Settings, tmp_path: Path
) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    build_manifest(settings, [zip_path], manifest)
    before = _row(settings)
    assert repair_form4_titles(settings, manifest, "titles-2708", 1, dry_run=True) == 1
    assert _row(settings) == before
    assert repair_form4_titles(settings, manifest, "titles-2708", 1, clock=lambda: NOW) == 1
    after = _row(settings)
    assert after[:16] == before[:16]
    assert after[16] == NEW
    assert after[17:] == before[17:]
    with open_for_write(settings) as conn:
        runs = conn.execute(
            "SELECT status, mode, message, finished_at FROM ingestion_runs"
        ).fetchall()
    assert len(runs) == 1
    assert runs[0][:2] == ("repaired", "repair")
    assert "manifest sha256" in runs[0][2]
    assert runs[0][3].tzinfo is not None
    assert repair_form4_titles(settings, manifest, "titles-2708", 0) == 0
    assert _row(settings) == after


@pytest.mark.parametrize("change", ["count", "release", "stale", "sha"])
def test_apply_refusals_leave_store_unchanged(
    settings: Settings, tmp_path: Path, change: str
) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    build_manifest(settings, [zip_path], manifest)
    expected = 2 if change == "count" else 1
    release = "missing" if change == "release" else "titles-2708"
    if change == "stale":
        with open_for_write(settings) as conn:
            conn.execute("UPDATE sec_form4_transactions SET security_title = 'Wrong'")
    if change == "sha":
        with zipfile.ZipFile(zip_path, "a") as archive:
            archive.writestr("extra", "changed")
    before = _row(settings)
    with pytest.raises(TitleRepairRefused):
        repair_form4_titles(settings, manifest, release, expected)
    assert _row(settings) == before
    with open_for_write(settings) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()[0] == 0


def test_cli_and_store_lock(settings: Settings, tmp_path: Path) -> None:
    _fixture(settings, tmp_path)
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    manifest = tmp_path / "built.json"
    built = CliRunner().invoke(
        app, ["build-form4-titles-manifest", "--zip-dir", str(tmp_path), "--output", str(manifest)]
    )
    assert built.exit_code == 0, built.output
    assert "sha256" in built.output
    dry = CliRunner().invoke(
        app,
        [
            "repair-form4-titles",
            "--manifest",
            str(manifest),
            "--release",
            "titles-2708",
            "--expect-rows",
            "1",
            "--dry-run",
        ],
    )
    assert dry.exit_code == 0, dry.output
    locked = Settings(_env_file=None, store={"path": settings.store.path, "lock_retry_seconds": 1})
    holder = (
        "import duckdb, sys, time\n"
        "conn = duckdb.connect(sys.argv[1])\n"
        "print('held', flush=True)\n"
        "time.sleep(5)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", holder, settings.store.path], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        blocked = CliRunner().invoke(
            cli.make_app(settings=lambda: locked, clock=lambda: NOW),
            [
                "repair-form4-titles",
                "--manifest",
                str(manifest),
                "--release",
                "titles-2708",
                "--expect-rows",
                "1",
            ],
        )
        assert blocked.exit_code == 1
        assert "store busy:" in blocked.output
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert _row(settings)[16] == QUOTED


def test_closed_release_refuses(settings: Settings, tmp_path: Path) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    build_manifest(settings, [zip_path], manifest)
    with open_for_write(settings) as conn:
        registry.close_data_release(
            conn,
            name="titles-2708",
            sessions_from=date(2021, 1, 4),
            sessions_to=date(2021, 1, 4),
            reason="#2708",
        )
    with pytest.raises(TitleRepairRefused):
        repair_form4_titles(settings, manifest, "titles-2708", 1)
    assert _row(settings)[16] == QUOTED


def test_update_count_mismatch_rolls_back(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    build_manifest(settings, [zip_path], manifest)
    real_open = open_for_write

    class WrongCount:
        def fetchone(self) -> tuple[int]:
            return (0,)

    class Connection:
        def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
            self.conn = conn

        def execute(self, query: str, params: list[object] | None = None) -> object:
            result = (
                self.conn.execute(query, params) if params is not None else self.conn.execute(query)
            )
            if query.startswith("UPDATE sec_form4_transactions AS stored SET"):
                return WrongCount()
            return result

        def executemany(self, query: str, params: list[list[object]]) -> object:
            return self.conn.executemany(query, params)

    @contextmanager
    def wrong_count_open(settings: Settings) -> Iterator[Connection]:
        with real_open(settings) as conn:
            yield Connection(conn)

    monkeypatch.setattr(titles_module, "open_for_write", wrong_count_open)
    with pytest.raises(TitleRepairRefused, match="rolled back"):
        repair_form4_titles(settings, manifest, "titles-2708", 1)
    assert _row(settings)[16] == QUOTED
