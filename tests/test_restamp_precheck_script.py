"""Tests for `scripts/restamp_precheck.py` (plan task T166f).

A fixture store on a temp file (never the owner's); no network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.restamp_precheck import load_precheck
from tradepartner.store.schema import init_schema

_SPEC = importlib.util.spec_from_file_location(
    "restamp_precheck_script",
    Path(__file__).resolve().parents[1] / "scripts" / "restamp_precheck.py",
)
assert _SPEC is not None and _SPEC.loader is not None
script = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = script
_SPEC.loader.exec_module(script)

STARTED = datetime(2026, 10, 9, 18, tzinfo=UTC)
MARK = date(2026, 10, 12)
MADE = datetime(2026, 10, 12, 23, tzinfo=UTC)


def _store(settings: Settings, *, late_filing: bool) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "paper_windows",
            {
                "window_id": 1,
                "hypothesis_id": 1,
                "first_rebalance_session": date(2026, 10, 30),
                "account_id": "paper",
                "starting_cash": 100_000.0,
                "starting_equity": 100_000.0,
                "code_version": "test",
                "started_at": STARTED,
                "frozen_json": "{}",
                "frozen_sha256": "0" * 64,
                "known_at": STARTED,
                "ingested_at": STARTED,
            },
        )
        at = session_close(MARK) + timedelta(minutes=30)
        insert_row(
            conn,
            "paper_runs",
            {
                "run_id": 1,
                "window_id": 1,
                "session": MARK,
                "kind": "mark",
                "started_at": at,
                "invoked_by": "scheduler",
                "code_version": "test",
                "known_at": at,
                "ingested_at": at,
            },
        )
        if late_filing:
            stamp = session_close(MARK) + timedelta(hours=2)
            insert_row(
                conn,
                "filing_events",
                {
                    "cik": "0000320193",
                    "accession": "0000320193-26-000001",
                    "form": "8-K",
                    "items": "2.02",
                    "accepted_at": stamp,
                    "known_at": stamp,
                    "ingested_at": stamp,
                    "source": "edgar",
                    "provenance": "filing",
                },
            )


def test_writes_a_passing_file_whose_sha256_is_over_its_bytes(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _store(settings, late_filing=False)
    out = tmp_path / "release"
    code = script.main(
        ["--release", "restamp-1382a2", "--out", str(out)], now=MADE, settings=settings
    )
    path = out / "precheck.restamp-1382a2.json"
    assert code == 0
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    printed = capsys.readouterr().out
    assert f"sha256 {digest}" in printed
    loaded = load_precheck(path)
    assert loaded.sha256 == digest
    assert loaded.release == "restamp-1382a2"
    assert loaded.window_first_session == date(2026, 10, 9)
    assert loaded.passed
    assert loaded.made_at == MADE
    assert loaded.instants == (
        STARTED,
        session_close(date(2026, 10, 9)),
        session_close(MARK),
    )
    assert loaded.journal_high_water == session_close(MARK) + timedelta(minutes=30)
    data = json.loads(path.read_text())
    assert set(data) >= {
        "release",
        "window_first_session",
        "pass",
        "instants",
        "journal_high_water",
        "made_at",
        "store_max_ingested_at",
        "open_windows",
        "candidates",
        "in_reach",
    }


def test_a_row_within_reach_fails_and_still_writes_the_file(
    settings: Settings, tmp_path: Path
) -> None:
    _store(settings, late_filing=True)
    code = script.main(["--release", "r2", "--out", str(tmp_path)], now=MADE, settings=settings)
    assert code == 1
    data = json.loads((tmp_path / "precheck.r2.json").read_text())
    assert data["pass"] is False
    assert data["candidates"]["filing_events"] == {
        "rows": 1,
        "refusal_candidates": 1,
        "in_reach": 1,
    }


def test_the_store_is_opened_read_only_and_left_unchanged(
    settings: Settings, tmp_path: Path
) -> None:
    _store(settings, late_filing=True)
    before = Path(settings.store.path).read_bytes()
    script.main(["--release", "r3", "--out", str(tmp_path)], now=MADE, settings=settings)
    assert Path(settings.store.path).read_bytes() == before
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM paper_runs").fetchone() == (1,)


def test_an_existing_file_is_never_overwritten(settings: Settings, tmp_path: Path) -> None:
    _store(settings, late_filing=False)
    (tmp_path / "precheck.r4.json").write_text("{}")
    code = script.main(["--release", "r4", "--out", str(tmp_path)], now=MADE, settings=settings)
    assert code == 2
    assert (tmp_path / "precheck.r4.json").read_text() == "{}"


def test_a_bad_release_name_is_refused(settings: Settings, tmp_path: Path) -> None:
    assert script.main(["--release", "Bad Name", "--out", str(tmp_path)], settings=settings) == 2
