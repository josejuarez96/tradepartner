"""No look-ahead in stored `known_at` against the SGML acceptance (#2936).

A fixture store holds rows stamped the #1382 way (an Eastern wall clock
labelled UTC) for CIKs the repair never reached, and rows stamped right. The
`audit-store` command of `scripts/repair_stamps.py` reads the store read-only,
asks SGML for a seeded sample of accessions and must flag every early stamp,
and exit 0 on a store with none. The sample is the class guard: a sampled
early stamp, or a sweep that could not read its headers, fails it. (A sample bounds the early rate;
it does not prove there is none.)
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "repair_stamps", Path(__file__).resolve().parents[2] / "scripts" / "repair_stamps.py"
)
assert _SPEC is not None and _SPEC.loader is not None
repair_stamps = importlib.util.module_from_spec(_SPEC)
sys.modules["repair_stamps"] = repair_stamps
_SPEC.loader.exec_module(repair_stamps)

NY = ZoneInfo("America/New_York")


def _ny(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=NY).astimezone(UTC)


def _as_utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# accession -> (cik, true acceptance)
TRUTH = {
    "0001558370-17-008504": ("0000000101", _ny("2017-11-08 17:53:25")),  # after the close
    "0001558370-18-000001": ("0000000101", _ny("2018-01-09 10:00:00")),
    "0000000202-24-000007": ("0000000202", _ny("2024-08-06 12:00:00")),
}


def _store(path: Path, stamps: dict[str, datetime]) -> Path:
    con = duckdb.connect(str(path))
    con.execute("create table securities(security_id varchar, cik varchar)")
    con.execute(
        "create table statement_facts(cik varchar, filing_accession varchar, known_at timestamptz)"
    )
    con.execute(
        "create table facts(security_id varchar, filing_accession varchar, known_at timestamptz)"
    )
    for acc, (cik, _) in TRUTH.items():
        con.execute("insert into securities values (?, ?)", (cik + "-S", cik))
        con.execute("insert into statement_facts values (?, ?, ?)", (cik, acc, stamps[acc]))
        con.execute("insert into facts values (?, ?, ?)", (cik + "-S", acc, stamps[acc]))
    con.close()
    return path


def _fetch(cik: str, accession: str) -> datetime:
    assert TRUTH[accession][0] == cik
    return TRUTH[accession][1]


def _run(
    store: Path, capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, dict[str, object]]:
    code = repair_stamps.main(
        [
            "--cache-dir",
            str(store.parent),
            "audit-store",
            "--store",
            str(store),
            "--seed",
            "7",
            *extra,
        ],
        fetch=_fetch,
    )
    return code, json.loads(capsys.readouterr().out)


def test_early_stamps_are_flagged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stamps = {
        acc: _as_utc(t.astimezone(NY).strftime("%Y-%m-%d %H:%M:%S"))
        for acc, (_, t) in TRUTH.items()
    }
    store = _store(tmp_path / "early.duckdb", stamps)
    code, report = _run(store, capsys, "--sample", "100", "--apply")
    assert code == 1
    tables = report["tables"]
    assert isinstance(tables, dict)
    for name in ("facts", "statement_facts"):
        assert tables[name]["checked"] == 3
        assert tables[name]["early"] == 3
        assert tables[name]["early_by_ny_offset"] == 3
    assert tables["statement_facts"]["session_early"] == 1  # the 17:53 ET acceptance
    assert report["look_ahead"] is True


def test_a_clean_store_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path / "clean.duckdb", {acc: t for acc, (_, t) in TRUTH.items()})
    code, report = _run(store, capsys, "--sample", "100", "--apply")
    assert code == 0
    assert report["look_ahead"] is False


def test_a_dry_run_makes_no_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path / "dry.duckdb", {acc: t for acc, (_, t) in TRUTH.items()})

    def boom(cik: str, accession: str) -> datetime:
        raise AssertionError("a dry run must not fetch")

    code = repair_stamps.main(
        ["--cache-dir", str(tmp_path), "audit-store", "--store", str(store), "--sample", "2"],
        fetch=boom,
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["requests"] == 2
    assert report["apply"] is False


def test_the_store_ciks_export_lists_every_stored_accession(tmp_path: Path) -> None:
    store = _store(tmp_path / "s.duckdb", {acc: t for acc, (_, t) in TRUTH.items()})
    out = tmp_path / "store-ciks.csv"
    code = repair_stamps.main(
        ["--cache-dir", str(tmp_path), "store-ciks", "--store", str(store), "--out", str(out)]
    )
    assert code == 0
    with out.open(newline="") as f:
        rows = {(r["cik"], r["accession"]) for r in csv.DictReader(f)}
    assert rows == {(cik, acc) for acc, (cik, _) in TRUTH.items()}


def test_unreadable_headers_do_not_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path / "unread.duckdb", {acc: t for acc, (_, t) in TRUTH.items()})

    def broken(cik: str, accession: str) -> datetime:
        raise ValueError("no header")

    code = repair_stamps.main(
        ["--cache-dir", str(tmp_path), "audit-store", "--store", str(store), "--apply"],
        fetch=broken,
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["incomplete"] is True


def test_null_accessions_are_skipped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = _store(tmp_path / "null.duckdb", {acc: t for acc, (_, t) in TRUTH.items()})
    con = duckdb.connect(str(store))
    con.execute("insert into facts values ('0000000101-S', null, now())")
    con.close()
    code, report = _run(store, capsys, "--sample", "100", "--apply")
    assert code == 0
    assert report["look_ahead"] is False
