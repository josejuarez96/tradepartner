"""Fixture-store contract for the manifest-driven Form 4 role repair."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.repair_form4_roles import RoleRepairRefused, build_manifest, repair_form4_roles
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

OLD = datetime(2021, 1, 5, tzinfo=UTC)
NOW = datetime(2026, 10, 31, tzinfo=UTC)
KEY = ("0000000001-21-000001", "0000000002", 0)


def _fixture(
    settings: Settings, tmp_path: Path, *, source: str = "sec_insider_bulk"
) -> tuple[Path, Path]:
    zip_path = tmp_path / "2021q1_form345.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "REPORTINGOWNER.tsv",
            "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNER_RELATIONSHIP\n"
            f"{KEY[0]}\t{KEY[1]}\tDirectorOther\n",
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
                "is_director": False,
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
                "security_title": "Common",
                "date_of_original_submission": None,
                "accepted_at": OLD,
                "known_at": OLD,
                "ingested_at": OLD,
                "source": source,
                "provenance": "filing",
            },
        )
        registry.open_data_release(
            conn, conn, name="roles-2691", backup_path="b.duckdb", reason="#2697"
        )
    manifest = tmp_path / "roles.json"
    build_manifest(settings, [zip_path], manifest)
    return zip_path, manifest


def _row(settings: Settings) -> tuple[object, ...]:
    with open_for_write(settings) as conn:
        return tuple(
            conn.execute(
                "SELECT is_director, is_officer, is_ten_percent_owner, is_other, "
                "known_at, accepted_at, ingested_at FROM sec_form4_transactions"
            ).fetchone()
        )


def test_dry_real_idempotent_and_known_at(settings: Settings, tmp_path: Path) -> None:
    _, manifest = _fixture(settings, tmp_path)
    before = _row(settings)
    assert (
        repair_form4_roles(settings, manifest, "roles-2691", 1, dry_run=True, clock=lambda: NOW)
        == 1
    )
    assert _row(settings) == before
    assert repair_form4_roles(settings, manifest, "roles-2691", 1, clock=lambda: NOW) == 1
    assert _row(settings) == (True, False, False, True, OLD, OLD, NOW)
    assert repair_form4_roles(settings, manifest, "roles-2691", 0, clock=lambda: NOW) == 0


def test_cli_builder_and_dry_run(settings: Settings, tmp_path: Path) -> None:
    _fixture(settings, tmp_path)
    output = tmp_path / "built.json"
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    built = CliRunner().invoke(
        app, ["build-form4-roles-manifest", "--zip-dir", str(tmp_path), "--output", str(output)]
    )
    assert built.exit_code == 0, built.output
    assert len(json.loads(output.read_text())["rows"]) == 1
    dry = CliRunner().invoke(
        app,
        [
            "repair-form4-roles",
            "--manifest",
            str(output),
            "--release",
            "roles-2691",
            "--expect-rows",
            "1",
            "--dry-run",
        ],
    )
    assert dry.exit_code == 0, dry.output
    assert "1 rows would change" in dry.output


@pytest.mark.parametrize("change", ["source", "stale", "decode", "sha", "count", "release"])
def test_refusals_are_atomic(settings: Settings, tmp_path: Path, change: str) -> None:
    zip_path, manifest = _fixture(settings, tmp_path)
    before = _row(settings)
    expected = 1
    release = "roles-2691"
    if change == "source":
        with open_for_write(settings) as conn:
            conn.execute("UPDATE sec_form4_transactions SET source = 'edgar' ")
    elif change == "stale":
        with open_for_write(settings) as conn:
            conn.execute("UPDATE sec_form4_transactions SET is_officer = true")
    elif change == "decode":
        data = json.loads(manifest.read_text())
        data["rows"][0]["new_flags"] = [False, True, False, True]
        manifest.write_text(json.dumps(data))
    elif change == "sha":
        with zipfile.ZipFile(zip_path, "a") as archive:
            archive.writestr("extra", "changed")
    elif change == "count":
        expected = 2
    else:
        release = "wrong"
    with pytest.raises(RoleRepairRefused):
        repair_form4_roles(settings, manifest, release, expected, clock=lambda: NOW)
    after = _row(settings)
    assert after[-3:] == before[-3:]
