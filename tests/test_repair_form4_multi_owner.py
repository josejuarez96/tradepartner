"""Multi-owner XML recheck contracts on a fixture store."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner import repair_form4_multi_owner as multi_module
from tradepartner.adapters.form4_bulk_route import RequestBudgetStopped
from tradepartner.config import Settings
from tradepartner.repair_form4_multi_owner import (
    MultiOwnerRepairRefused,
    count_candidates,
    recheck_form4_multi_owner,
    repair_form4_multi_owner,
)
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

NOW = datetime(2026, 10, 31, tzinfo=UTC)


def test_selection_counts_distinct_bulk_owners(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        for accession, owner, line, source in (
            ("0000000001-25-000001", "0000000001", 0, "sec_insider_bulk"),
            ("0000000001-25-000001", "0000000001", 1, "sec_insider_bulk"),
            ("0000000001-25-000001", "0000000002", 0, "sec_insider_bulk"),
            ("0000000001-25-000002", "0000000001", 0, "sec_insider_bulk"),
            ("0000000001-25-000003", "0000000001", 0, "edgar"),
            ("0000000001-25-000003", "0000000002", 0, "edgar"),
            ("0000000001-25-000004", "0000000001", 0, "sec_insider_bulk"),
            ("0000000001-25-000004", "0000000002", 0, "edgar"),
        ):
            insert_row(
                conn,
                "sec_form4_transactions",
                {
                    "accession": accession,
                    "issuer_cik": "0000000009",
                    "filer_cik": owner,
                    "line_no": line,
                    "is_director": False,
                    "is_officer": False,
                    "is_ten_percent_owner": False,
                    "is_other": False,
                    "transaction_date": date(2025, 1, 2),
                    "deemed_execution_date": None,
                    "transaction_code": "P",
                    "acquired_disposed": "A",
                    "shares": 1,
                    "price": 10,
                    "shares_owned_after": 1,
                    "ownership_form": "D",
                    "security_title": "Common",
                    "date_of_original_submission": None,
                    "accepted_at": datetime(2025, 1, 3, tzinfo=UTC),
                    "known_at": datetime(2025, 1, 3, tzinfo=UTC),
                    "ingested_at": datetime(2025, 1, 3, tzinfo=UTC),
                    "source": source,
                    "provenance": "filing",
                },
            )
    assert count_candidates(settings) == {"2025q1": 1}


def test_quarter_uses_new_york_acceptance_date(settings: Settings) -> None:
    _four_accessions(settings)
    with open_for_write(settings) as conn:
        conn.execute(
            "UPDATE sec_form4_transactions SET accepted_at = ?, known_at = ?, ingested_at = ?",
            [datetime(2025, 4, 1, 0, 30, tzinfo=UTC)] * 3,
        )
    assert count_candidates(settings, from_quarter="2025q1", to_quarter="2025q1") == {"2025q1": 4}
    assert count_candidates(settings, from_quarter="2025q2", to_quarter="2025q2") == {}


def test_invalid_quarter_refused(settings: Settings) -> None:
    with pytest.raises(ValueError, match="quarter"):
        count_candidates(settings, from_quarter="2025q5")


def test_mixed_source_accession_compares_bulk_rows_only(settings: Settings, tmp_path: Path) -> None:
    accessions = _four_accessions(settings)
    with open_for_write(settings) as conn:
        source = conn.execute(
            "SELECT * FROM sec_form4_transactions WHERE accession = ? AND filer_cik = ?",
            [accessions[0], "0000000001"],
        )
        columns = [column[0] for column in source.description]
        row = dict(zip(columns, source.fetchone(), strict=True))
        row["filer_cik"] = "0000000003"
        row["source"] = "edgar"
        insert_row(conn, "sec_form4_transactions", row)
    assert count_candidates(settings) == {"2025q1": 4}
    output = tmp_path / "manifest.json"
    summary = recheck_form4_multi_owner(
        settings,
        output,
        max_requests=8,
        fetch_xml=lambda _s, _c, _i, _a: _xml(),
    )
    assert summary["counts"]["agrees"] == 4
    assert json.loads(output.read_text())["results"][accessions[0]]["class"] == "agrees"


def _xml(*, director: bool = False, shares: int = 1) -> str:
    return f"""<ownershipDocument>
<issuer><issuerCik>0000000009</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000000001</rptOwnerCik></reportingOwnerId>
<reportingOwnerRelationship><isDirector>{int(director)}</isDirector></reportingOwnerRelationship></reportingOwner>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000000002</rptOwnerCik></reportingOwnerId></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction>
<securityTitle><value>Common</value></securityTitle>
<transactionDate><value>2025-01-02</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
<transactionPricePerShare><value>10</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>1</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


def _four_accessions(settings: Settings) -> list[str]:
    accessions = [f"0000000001-25-{number:06d}" for number in range(1, 5)]
    with open_for_write(settings) as conn:
        init_schema(conn)
        for accession in accessions:
            for owner in ("0000000001", "0000000002"):
                insert_row(
                    conn,
                    "sec_form4_transactions",
                    {
                        "accession": accession,
                        "issuer_cik": "0000000009",
                        "filer_cik": owner,
                        "line_no": 0,
                        "is_director": False,
                        "is_officer": False,
                        "is_ten_percent_owner": False,
                        "is_other": False,
                        "transaction_date": date(2025, 1, 2),
                        "deemed_execution_date": None,
                        "transaction_code": "P",
                        "acquired_disposed": "A",
                        "shares": 1,
                        "price": 10,
                        "shares_owned_after": 1,
                        "ownership_form": "D",
                        "security_title": "Common",
                        "date_of_original_submission": None,
                        "accepted_at": datetime(2025, 1, 3, tzinfo=UTC),
                        "known_at": datetime(2025, 1, 3, tzinfo=UTC),
                        "ingested_at": datetime(2025, 1, 3, tzinfo=UTC),
                        "source": "sec_insider_bulk",
                        "provenance": "filing",
                    },
                )
    return accessions


def test_classification_budget_resume_and_apply(settings: Settings, tmp_path: Path) -> None:
    accessions = _four_accessions(settings)
    xmls = {
        accessions[0]: _xml(),
        accessions[1]: _xml(director=True),
        accessions[2]: _xml(shares=2),
        accessions[3]: "bad xml",
    }
    called: list[str] = []

    def fake(_settings: Settings, _client: httpx.Client, _issuer: str, accession: str) -> str:
        called.append(accession)
        return xmls[accession]

    output = tmp_path / "manifest.json"
    with pytest.raises(MultiOwnerRepairRefused, match="need at least 8 requests"):
        recheck_form4_multi_owner(settings, output, max_requests=7, fetch_xml=fake)
    assert not called
    assert count_candidates(settings) == {"2025q1": 4}
    assert not called
    summary = recheck_form4_multi_owner(settings, output, max_requests=8, fetch_xml=fake)
    assert summary["counts"] == {
        "agrees": 1,
        "roles_differ": 1,
        "other_difference": 1,
        "unreadable": 1,
        "retry": 0,
    }
    assert summary["other_difference"] == [accessions[2]]
    assert summary["unreadable"] == [accessions[3]]
    manifest = json.loads(output.read_text())
    assert len(manifest["rows"]) == 1
    assert manifest["rows"][0]["key"] == [accessions[1], "0000000001", 0]
    first = output.read_bytes()
    called.clear()
    recheck_form4_multi_owner(settings, output, max_requests=0, resume=output, fetch_xml=fake)
    assert not called
    assert output.read_bytes() == first
    with open_for_write(settings) as conn:
        registry.open_data_release(
            conn, conn, name="multi-2720", backup_path="b.duckdb", reason="#2720"
        )
    before = _all_rows(settings)
    assert (
        repair_form4_multi_owner(settings, output, "multi-2720", 1, dry_run=True, clock=lambda: NOW)
        == 1
    )
    assert _all_rows(settings) == before
    with pytest.raises(MultiOwnerRepairRefused, match="expected 2"):
        repair_form4_multi_owner(settings, output, "multi-2720", 2, clock=lambda: NOW)
    assert _all_rows(settings) == before
    with pytest.raises(MultiOwnerRepairRefused, match="no open data release"):
        repair_form4_multi_owner(settings, output, "missing", 1, clock=lambda: NOW)
    with open_for_write(settings) as conn:
        conn.execute(
            "UPDATE sec_form4_transactions SET shares = 2 WHERE accession = ?",
            [accessions[1]],
        )
    with pytest.raises(MultiOwnerRepairRefused, match="non-role fields differ"):
        repair_form4_multi_owner(settings, output, "multi-2720", 1, clock=lambda: NOW)
    with open_for_write(settings) as conn:
        conn.execute(
            "UPDATE sec_form4_transactions SET shares = 1 WHERE accession = ?",
            [accessions[1]],
        )
    assert repair_form4_multi_owner(settings, output, "multi-2720", 1, clock=lambda: NOW) == 1
    after = _all_rows(settings)
    assert len(after) == len(before)
    changed = [(old, new) for old, new in zip(before, after, strict=True) if old != new]
    assert len(changed) == 1
    old, new = changed[0]
    assert old[3] is False and new[3] is True
    assert old[-3] == new[-3]  # known_at
    assert old[-2] != new[-2]  # ingested_at
    assert old[:3] == new[:3] and old[4:-2] == new[4:-2] and old[-1] == new[-1]
    assert repair_form4_multi_owner(settings, output, "multi-2720", 0, clock=lambda: NOW) == 0
    with open_for_write(settings) as conn:
        run = conn.execute("SELECT source, status, mode, rows_added FROM ingestion_runs").fetchall()
        assert run == [("edgar", "repaired", "repair", 0)]
        unseen = conn.execute(
            "SELECT count(*) FROM sec_form4_transactions WHERE known_at <= ? AND accession = ?",
            [datetime(2025, 1, 2, tzinfo=UTC), accessions[1]],
        ).fetchone()
        assert unseen == (0,)
        registry.close_data_release(
            conn,
            name="multi-2720",
            sessions_from=date(2025, 1, 2),
            sessions_to=date(2025, 1, 2),
            reason="#2720",
        )
    with pytest.raises(MultiOwnerRepairRefused, match="no open data release"):
        repair_form4_multi_owner(settings, output, "multi-2720", 0, clock=lambda: NOW)


def test_cli_refuses_store_lock(settings: Settings, tmp_path: Path) -> None:
    accessions = _four_accessions(settings)
    output = tmp_path / "manifest.json"
    recheck_form4_multi_owner(
        settings,
        output,
        max_requests=8,
        fetch_xml=lambda _s, _c, _i, a: _xml(director=a == accessions[0]),
    )
    with open_for_write(settings) as conn:
        registry.open_data_release(
            conn, conn, name="multi-2720", backup_path="b.duckdb", reason="#2720"
        )
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
        result = CliRunner().invoke(
            cli.make_app(settings=lambda: locked, clock=lambda: NOW),
            [
                "repair-form4-multi-owner",
                "--manifest",
                str(output),
                "--release",
                "multi-2720",
                "--expect-rows",
                "1",
            ],
        )
        assert result.exit_code == 1
        assert "store busy:" in result.output
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_cli_count_only_makes_no_requests(settings: Settings) -> None:
    _four_accessions(settings)
    calls = 0

    def unexpected(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        result = CliRunner().invoke(
            cli.make_app(settings=lambda: settings, edgar_client=client),
            ["recheck-form4-multi-owner", "--count-only"],
        )
    assert result.exit_code == 0, result.output
    assert "2025q1: 4 accessions, at least 8 requests" in result.output
    assert calls == 0


def test_transient_http_retried_on_resume(settings: Settings, tmp_path: Path) -> None:
    accessions = _four_accessions(settings)
    output = tmp_path / "manifest.json"
    failed = {accessions[0], accessions[1]}
    calls: list[str] = []

    def fetch(_s: Settings, _c: httpx.Client, _i: str, accession: str) -> str:
        calls.append(accession)
        if accession in failed:
            if accession == accessions[0]:
                raise httpx.ConnectError("offline")
            request = httpx.Request("GET", "https://example.test/file.xml")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("server error", request=request, response=response)
        return _xml()

    summary = recheck_form4_multi_owner(settings, output, max_requests=8, fetch_xml=fetch)
    assert summary["counts"]["retry"] == 2
    assert summary["counts"]["unreadable"] == 0
    assert not json.loads(output.read_text())["rows"]
    with pytest.raises(MultiOwnerRepairRefused, match="resume the recheck"):
        repair_form4_multi_owner(settings, output, "multi-2720", 0)
    failed.clear()
    calls.clear()
    summary = recheck_form4_multi_owner(
        settings, output, max_requests=4, resume=output, fetch_xml=fetch
    )
    assert calls == accessions[:2]
    assert summary["counts"]["agrees"] == 4


def test_http_404_is_unreadable_and_not_refetched(settings: Settings, tmp_path: Path) -> None:
    accessions = _four_accessions(settings)
    output = tmp_path / "manifest.json"
    calls: list[str] = []

    def fetch(_s: Settings, _c: httpx.Client, _i: str, accession: str) -> str:
        calls.append(accession)
        if accession == accessions[0]:
            request = httpx.Request("GET", "https://example.test/file.xml")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return _xml()

    summary = recheck_form4_multi_owner(settings, output, max_requests=8, fetch_xml=fetch)
    assert summary["unreadable"] == accessions[:1]
    calls.clear()
    recheck_form4_multi_owner(settings, output, max_requests=0, resume=output, fetch_xml=fetch)
    assert calls == []


def test_cli_budget_stop_keeps_partial_manifest(settings: Settings, tmp_path: Path) -> None:
    _four_accessions(settings)
    output = tmp_path / "manifest.json"

    def stopped(*_args: object, **_kwargs: object) -> dict[str, object]:
        output.write_text('{"version": 1, "results": {}}')
        raise RequestBudgetStopped

    with patch.object(cli, "recheck_form4_multi_owner", stopped):
        result = CliRunner().invoke(
            cli.make_app(settings=lambda: settings),
            ["recheck-form4-multi-owner", "--output", str(output), "--max-requests", "8"],
        )
    assert result.exit_code != 0
    assert "--resume" in result.output
    assert str(output) in result.output
    assert json.loads(output.read_text())["results"] == {}


def test_apply_value_error_is_clean_cli_refusal(settings: Settings, tmp_path: Path) -> None:
    accessions = _four_accessions(settings)
    manifest = tmp_path / "manifest.json"
    recheck_form4_multi_owner(
        settings,
        manifest,
        max_requests=8,
        fetch_xml=lambda _s, _c, _i, a: _xml(director=a == accessions[0]),
    )
    with open_for_write(settings) as conn:
        registry.open_data_release(
            conn, conn, name="multi-2720", backup_path="b.duckdb", reason="#2720"
        )
    before = _all_rows(settings)
    original = multi_module._classify
    calls = 0

    def fail_in_transaction(*args: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("bad XML during apply")
        return original(*args)  # type: ignore[arg-type]

    with patch.object(multi_module, "_classify", fail_in_transaction):
        result = CliRunner().invoke(
            cli.make_app(settings=lambda: settings, clock=lambda: NOW),
            [
                "repair-form4-multi-owner",
                "--manifest",
                str(manifest),
                "--release",
                "multi-2720",
                "--expect-rows",
                "1",
            ],
        )
    assert result.exit_code == 1
    assert "repair refused: XML recheck failed" in result.output
    assert "bad XML during apply" in result.output
    assert "Traceback" not in result.output
    assert _all_rows(settings) == before


def test_update_count_mismatch_rolls_back(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accessions = _four_accessions(settings)
    manifest = tmp_path / "manifest.json"
    recheck_form4_multi_owner(
        settings,
        manifest,
        max_requests=8,
        fetch_xml=lambda _s, _c, _i, a: _xml(director=a == accessions[0]),
    )
    with open_for_write(settings) as conn:
        registry.open_data_release(
            conn, conn, name="multi-2720", backup_path="b.duckdb", reason="#2720"
        )
    before = _all_rows(settings)
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

    monkeypatch.setattr(multi_module, "open_for_write", wrong_count_open)
    with pytest.raises(MultiOwnerRepairRefused, match="rolled back"):
        repair_form4_multi_owner(settings, manifest, "multi-2720", 1, clock=lambda: NOW)
    assert _all_rows(settings) == before
    with open_for_write(settings) as conn:
        assert conn.execute("SELECT count(*) FROM ingestion_runs").fetchone() == (0,)


def _all_rows(settings: Settings) -> list[tuple[object, ...]]:
    with open_for_write(settings) as conn:
        return conn.execute(
            "SELECT accession, filer_cik, line_no, is_director, is_officer, "
            "is_ten_percent_owner, is_other, transaction_date, shares, "
            "known_at, ingested_at, source "
            "FROM sec_form4_transactions ORDER BY accession, filer_cik, line_no"
        ).fetchall()
