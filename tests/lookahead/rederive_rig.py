"""A fixture store, raw archive, disk and iCloud for the raw re-derive tests (plan
T352, #2714). Every write lands under the test's `tmp_path`; the disk and iCloud
are fakes, so no test touches iCloud or the owner's store.

A writer run is written the way ingest writes it: the body archived by hash, the
stored rows inserted, one manifest entry per input listing each written row's key
and digest, the manifest archived and its digest put in the run message.
`wrong=` stores a row whose value differs from what the body parses to, standing
for a parser mistake the re-derive corrects.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradepartner import diskfloor
from tradepartner.adapters import daily_index as di
from tradepartner.adapters.edgar import parse_form4
from tradepartner.adapters.edgar_insider_bulk import read_insider_bulk
from tradepartner.adapters.form4_archive import (
    BULK_SOURCE,
    XML_SOURCE,
    RawInput,
    build_manifest,
    manifest_entry,
    quarter_message,
)
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, write_run
from tradepartner.store import raw_archive, registry
from tradepartner.store.db import insert_row, open_for_write, open_read_only
from tradepartner.store.raw_archive import ItemStatus
from tradepartner.store.schema import init_schema

GB = 1_000_000_000
PLENTY = 500 * GB
LOW = 5 * GB  # below the 10 GB floor
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "sec_forms"
ACC = "0001577526-26-000036"
BULK_ACC = "0001977272-26-000004"  # another accession of the recorded zip
XML = (FIXTURES / f"bulk_parity_{ACC}.xml").read_bytes()
ACCEPTED = datetime(2026, 3, 30, 20, 1, tzinfo=UTC)
FETCHED = datetime(2026, 4, 2, 10, tzinfo=UTC)
RUN_AT = datetime(2026, 4, 2, 11, tzinfo=UTC)
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
FILE_DATE = date(2026, 3, 2)
INDEX_ACC = "0000012345-26-000001"
DAILY = (
    b"Form Type   Company Name   CIK   Date Filed   File Name\n"
    b"---------------------------------------------------------\n"
    b"8-K              ACME CORP                                                     "
    b"12345       20260302    edgar/data/12345/0000012345-26-000001.txt\n"
)


def bulk_zip() -> bytes:
    """The recorded 2026q1 insider tables, zipped as the SEC ships them."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for table in sorted((FIXTURES / "insider_bulk_2026q1").glob("*.tsv")):
            z.writestr(
                zipfile.ZipInfo(table.name, date_time=(2026, 4, 6, 0, 0, 0)), table.read_bytes()
            )
    return buffer.getvalue()


class Disk:
    """`diskfloor.free_bytes`, faked: a queue of readings, the last repeated."""

    def __init__(self, *readings: int) -> None:
        self.readings = list(readings)
        self.calls = 0

    def __call__(self, _path: Path) -> int:
        self.calls += 1
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]


class FakeICloud:
    """Every body local unless named `offline`; a download request lands only for
    the paths in `lands`."""

    def __init__(self) -> None:
        self.offline: set[Path] = set()
        self.lands: set[Path] = set()
        self.downloads: list[Path] = []

    def status(self, paths: Sequence[Path]) -> Mapping[Path, ItemStatus]:
        return {
            p: ItemStatus(uploaded=True, current=p not in self.offline, dataless=p in self.offline)
            for p in paths
        }

    def evict(self, path: Path) -> None:  # never called by a re-derive
        raise AssertionError(f"evict {path}")

    def download(self, path: Path) -> None:
        self.downloads.append(path)
        if path in self.lands:
            self.offline.discard(path)


class Rig:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ("TRADEPARTNER_RAW_ARCHIVE", "tradepartner_raw_archive"):
            monkeypatch.delenv(key, raising=False)
        self.root = tmp_path / "archive"
        self.root.mkdir()
        self.settings = Settings(
            _env_file=None,
            store={"path": str(tmp_path / "store.duckdb")},
            tradepartner_raw_archive=str(self.root),
            raw_archive={"download_wait_min_s": 0.01, "poll_interval_s": 0.001},
        )
        self.monkeypatch = monkeypatch
        self.disk(PLENTY)
        self.icloud = FakeICloud()
        with open_for_write(self.settings) as conn:
            init_schema(conn)

    def disk(self, *readings: int) -> Disk:
        disk = Disk(*readings)
        self.monkeypatch.setattr(diskfloor, "free_bytes", disk)
        return disk

    def body_path(self, body: bytes) -> Path:
        return raw_archive.body_path(self.root, hashlib.sha256(body).hexdigest())

    def _run(
        self,
        conn: Any,
        *,
        run_id: str,
        source: str,
        mode: str,
        manifest: bytes,
        at: datetime = RUN_AT,
    ) -> str:
        digest = raw_archive.archive_manifest(self.settings, manifest).sha256
        message = f"provenance_sha256={digest}; form4_compare_markers=0"
        write_run(conn, run_id, at, at, SourceRun(source, "ok", 1, "", message), mode)
        return digest

    def form4_rows(
        self,
        *,
        row_source: str,
        body: bytes,
        wrong: Mapping[str, Any] | None = None,
        accession: str = ACC,
        wrong_line: int = 0,
    ) -> list[dict[str, Any]]:
        if row_source == XML_SOURCE:
            transactions = parse_form4(body.decode("utf-8")).transactions
        else:
            transactions = read_insider_bulk(io.BytesIO(body)).accessions[accession].transactions
        common = {
            "accepted_at": ACCEPTED,
            "known_at": ACCEPTED,
            "ingested_at": RUN_AT,
            "source": row_source,
            "provenance": "filing",
        }
        rows = [{"accession": accession, **asdict(t), **common} for t in transactions]
        rows[wrong_line].update(wrong or {})
        return rows

    def form4_run(
        self,
        *,
        run_id: str = "run-1",
        row_source: str = XML_SOURCE,
        body: bytes = XML,
        wrong: Mapping[str, Any] | None = None,
        mode: str = "backfill",
        writer: str = "sec_forms",
        entry_source: str | None = None,
        entry_rows: list[dict[str, Any]] | None = None,
        fetched_at: datetime | None = FETCHED,
        store: bool = True,
        outcome: str = "added",
        accession: str = ACC,
        at: datetime = RUN_AT,
        zips: list[dict[str, Any]] | None = None,
        wrong_line: int = 0,
    ) -> str:
        """One writer run of `ACC`'s rows; returns its manifest digest."""
        sha = raw_archive.archive_body(self.settings, body).sha256
        rows = self.form4_rows(
            row_source=row_source,
            body=body,
            wrong=wrong,
            accession=accession,
            wrong_line=wrong_line,
        )
        bulk = row_source == BULK_SOURCE
        listed = entry_rows if entry_rows is not None else rows
        entry = manifest_entry(
            accession=accession,
            row_source=entry_source or row_source,
            quarter="2026q1" if bulk else None,
            raw=RawInput(sha, "https://www.sec.gov/x", fetched_at, "p=1"),
            outcome=outcome,
            rows={"sec_form4_transactions": listed if outcome == "added" else []},
        )
        if zips is None:
            zips = [{"quarter": "2026q1", "sha256": sha}] if bulk else []
        manifest = build_manifest(
            run_id=run_id, source=writer, mode=mode, entries=[entry], zips=zips
        )
        with open_for_write(self.settings) as conn:
            if store:
                for row in rows:
                    insert_row(conn, "sec_form4_transactions", row)
            digest = self._run(
                conn, run_id=run_id, source=writer, mode=mode, manifest=manifest, at=at
            )
            if bulk:  # the zero-width quarter row citing the same digest
                message = quarter_message(checked=sha, provenance=digest, zip_sha256=sha, rest="")
                run = SourceRun(BULK_SOURCE, "ok", 0, "2026q1", message)
                write_run(conn, f"{run_id}-q", at, at, run, mode)
        return digest

    def index_run(
        self,
        *,
        wrong_name: str = "ACME CORP",
        mode: str = "session",
        position: int = 0,
        fetched_at: datetime | None = FETCHED,
    ) -> str:
        sha = raw_archive.archive_body(self.settings, DAILY).sha256
        row = {
            "cik": "0000012345",
            "accession": INDEX_ACC,
            "form": "8-K",
            "company_name": wrong_name,
            "filed_on": FILE_DATE,
            "accepted_at": ACCEPTED,
            "listed_on": FILE_DATE,
            "list_position": position,
            "known_at": ACCEPTED,
            "ingested_at": RUN_AT,
            "source": "edgar",
            "provenance": "filing",
        }
        raw = RawInput(sha, "https://www.sec.gov/d", fetched_at, di.DAILY_PARSER_VERSION)
        entry = di.index_entry(path=di.daily_path(FILE_DATE), raw=raw, parsed=True, rows=[row])
        manifest = di.build_index_manifest(run_id="idx-1", mode=mode, entries=[entry])
        with open_for_write(self.settings) as conn:
            insert_row(conn, "filing_index", row)
            return self._run(
                conn, run_id="idx-1", source=di.INDEX_WRITER, mode=mode, manifest=manifest
            )

    def open_release(self, name: str = "rederive-2714") -> None:
        with open_for_write(self.settings) as conn:
            registry.open_data_release(conn, conn, name=name, backup_path="b.duckdb", reason="T352")

    def open_paper_window(self, started_at: datetime) -> None:
        with open_for_write(self.settings) as conn:
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
                    "started_at": started_at,
                    "frozen_json": "{}",
                    "frozen_sha256": "0" * 64,
                    "known_at": started_at,
                    "ingested_at": started_at,
                },
            )

    def table(self, table: str = "sec_form4_transactions") -> list[dict[str, Any]]:
        with open_read_only(self.settings) as conn:
            cursor = conn.execute(f"SELECT * FROM {table} ORDER BY ALL")
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]

    def runs(self) -> list[tuple[Any, ...]]:
        with open_read_only(self.settings) as conn:
            return conn.execute(
                "SELECT run_id, source, mode, status, message FROM ingestion_runs ORDER BY rowid"
            ).fetchall()


WRONG_SHARES = {"shares": Decimal("999")}
