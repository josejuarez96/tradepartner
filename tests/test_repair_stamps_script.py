"""Tests for scripts/repair_stamps.py, the #1382 stamps repair cache driver (T166a).

Every SGML answer goes through `parse_sgml_header` over a header built from the
#572 E15 fixture (`sgml_header_plain_issuer.txt`): Apple's real header for its
real accession, and the same header re-labelled for the synthetic CIKs. No
request leaves the process: the fetch is a fake that counts its calls.
"""

from __future__ import annotations

import csv
import gzip
import importlib.util
import json
import os
import sys
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from tradepartner.adapters.edgar import parse_sgml_header
from tradepartner.adapters.stamp_repair import shift_stamp

_SPEC = importlib.util.spec_from_file_location(
    "repair_stamps", Path(__file__).resolve().parents[1] / "scripts" / "repair_stamps.py"
)
assert _SPEC is not None and _SPEC.loader is not None
repair_stamps = importlib.util.module_from_spec(_SPEC)
sys.modules["repair_stamps"] = repair_stamps
_SPEC.loader.exec_module(repair_stamps)

NY = ZoneInfo("America/New_York")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "edgar"
APPLE_HEADER = (FIXTURES / "sgml_header_plain_issuer.txt").read_text()
APPLE = "0000320193"
APPLE_ACC = "0000320193-25-000079"
APPLE_TRUE = datetime(2025, 10, 31, 10, 1, 26, tzinfo=UTC)  # E15: SGML 20251031060126 EDT
WINDOW = date(2026, 10, 9)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


def early(true: datetime) -> datetime:
    """The early stamp the bulk run wrote: New York wall clock labelled UTC."""
    return true.astimezone(NY).replace(tzinfo=UTC)


def header_for(cik: str, accession: str, accepted: datetime) -> str:
    """The E15 fixture header re-labelled for ``cik``/``accession`` at ``accepted``."""
    wall = accepted.astimezone(NY).strftime("%Y%m%d%H%M%S")
    return (
        APPLE_HEADER.replace(APPLE_ACC, accession)
        .replace("20251031060126", wall)
        .replace(APPLE, cik)
    )


class FakeSec:
    """Serves SGML headers by (cik, accession) and records every request."""

    def __init__(self, truth: dict[tuple[str, str], datetime]) -> None:
        self.truth = dict(truth)
        self.calls: list[tuple[str, str]] = []
        self.fail_after: int | None = None

    def __call__(self, cik: str, accession: str) -> datetime:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            request = httpx.Request("GET", "https://www.sec.gov/x")
            raise httpx.HTTPStatusError(
                "503", request=request, response=httpx.Response(503, request=request)
            )
        self.calls.append((cik, accession))
        if (cik, accession) == (APPLE, APPLE_ACC) and cik not in self.truth:
            text = APPLE_HEADER
        else:
            text = header_for(cik, accession, self.truth[(cik, accession)])
        return parse_sgml_header(text, cik=cik).accepted_at


# --- a fixture cache --------------------------------------------------------


class Cache:
    """A temp EDGAR cache: stamps files, a form.idx and the frozen inputs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "cache"
        self.stamps = self.dir / "stamps" / "v1"
        self.stamps.mkdir(parents=True)
        self.index_rows: list[tuple[str, str, date]] = []
        self.bulk: list[tuple[str, str, datetime]] = []
        self.classes: dict[str, str] = {}
        self.store: list[tuple[str, str]] = []

    def add_cik(
        self,
        cik: str,
        records: Iterable[tuple[str, datetime | None, date, bool]],
        *,
        cls: str | None = None,
        stored: Iterable[str] = (),
        items_refreshed: bool = True,
    ) -> None:
        """``records``: (accession, stamp, index date, bulk-written)."""
        data: dict[str, Any] = {
            "version": 1,
            "layout": 2,
            "cik": cik,
            "items_refreshed": items_refreshed,
            "records": {},
        }
        for accession, stamp, filed, bulk in records:
            data["records"][accession] = [
                "10-K",
                "doc.htm",
                True,
                stamp.isoformat() if stamp else None,
                "2.02,9.01",
            ]
            self.index_rows.append((cik, accession, filed))
            if bulk and stamp is not None:
                self.bulk.append((cik, accession, stamp))
        (self.stamps / f"{cik}.json").write_bytes(json.dumps(data).encode())
        if cls is not None:
            self.classes[cik] = cls
        self.store.extend((cik, a) for a in stored)

    def finish(self) -> None:
        lines = ["Form Type   Company Name   CIK   Date Filed  File Name", "-" * 40]
        for cik, accession, filed in self.index_rows:
            lines.append(
                f"10-K             Some Co Inc        {int(cik)}  {filed.isoformat()}  "
                f"edgar/data/{int(cik)}/{accession}.txt"
            )
        index = self.dir / "index"
        index.mkdir(exist_ok=True)
        (index / "2025-QTR4.idx.gz").write_bytes(gzip.compress("\n".join(lines).encode()))
        self._csv(
            "bulk.csv",
            ("cik", "accession", "stamp"),
            [(c, a, s.isoformat()) for c, a, s in self.bulk],
        )
        self._csv("classes.csv", ("cik", "cls"), sorted(self.classes.items()))
        self._csv("store.csv", ("cik", "accession"), self.store)

    def _csv(self, name: str, header: tuple[str, ...], rows: Iterable[Iterable[Any]]) -> None:
        with (self.root / name).open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    def argv(self, *extra: str, release: str = "restamp-1382") -> list[str]:
        return [
            "--cache-dir",
            str(self.dir),
            "run",
            "--bulk-written",
            str(self.root / "bulk.csv"),
            "--classes",
            str(self.root / "classes.csv"),
            "--store-ciks",
            str(self.root / "store.csv"),
            "--window-start",
            WINDOW.isoformat(),
            "--release",
            release,
            *extra,
        ]

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.dir)): p.read_bytes()
            for p in sorted(self.dir.rglob("*"))
            if p.is_file()
        }

    def records(self, cik: str) -> dict[str, list[Any]]:
        data = json.loads((self.stamps / f"{cik}.json").read_bytes())
        return dict(data["records"])

    def stamp(self, cik: str, accession: str) -> datetime:
        return datetime.fromisoformat(self.records(cik)[accession][3])

    def manifest(self, release: str = "restamp-1382") -> list[dict[str, str]]:
        path = self.dir / "stamps" / f"v1.{release}.manifest.csv"
        with path.open(newline="") as f:
            return list(csv.DictReader(f))


# Synthetic CIKs and accessions.
TRUE_CIK = "0000000202"
MIXED = "0000000303"
UNDET = "0000000404"
LONELY = "0000000505"
API = "0000000101"
API_ACC = "0000000101-24-000001"
API_STAMP = utc(2024, 3, 15, 15, 0)  # Friday 11:00 EDT, filed Monday: provably early
API_TRUE = utc(2024, 3, 15, 23, 0)  # Friday 19:00 EDT, after the cutoff


def acc(cik: str, n: int) -> str:
    return f"{cik}-24-{n:06d}"


def scenario(tmp_path: Path) -> tuple[Cache, FakeSec]:
    """One CIK of every kind the plan line names."""
    c = Cache(tmp_path)
    truth: dict[tuple[str, str], datetime] = {}
    # Early class (Apple, the real E15 header): the bulk-written record moves;
    # a record added after 10-01, and one moved since 10-01, do not.
    added = utc(2025, 11, 3, 14, 0)
    c.add_cik(
        APPLE,
        [
            (APPLE_ACC, early(APPLE_TRUE), date(2025, 10, 31), True),
            (acc(APPLE, 2), added, date(2025, 11, 3), False),
            (acc(APPLE, 3), utc(2025, 10, 6, 13, 0), date(2025, 10, 6), False),
        ],
        cls="early",
        stored=[APPLE_ACC],
    )
    c.bulk.append((APPLE, acc(APPLE, 3), utc(2025, 10, 6, 9, 0)))  # differs from the cache
    # A provably early API stamp of a true CIK takes its SGML value.
    c.add_cik(API, [(API_ACC, API_STAMP, date(2024, 3, 18), False)], cls="true")
    truth[(API, API_ACC)] = API_TRUE
    # A true CIK changes nothing.
    t = utc(2024, 5, 1, 14, 0)
    c.add_cik(
        TRUE_CIK,
        [(acc(TRUE_CIK, 1), t, date(2024, 5, 1), True)],
        cls="true",
        stored=[acc(TRUE_CIK, 1)],
    )
    # A mixed CIK takes SGML per stored filing; its other record is unrepaired.
    m1_true, m2 = utc(2024, 6, 3, 14, 0), utc(2024, 6, 4, 14, 0)
    c.add_cik(
        MIXED,
        [
            (acc(MIXED, 1), early(m1_true), date(2024, 6, 3), True),
            (acc(MIXED, 2), m2, date(2024, 6, 4), True),
            (acc(MIXED, 3), utc(2024, 6, 5, 14, 0), date(2024, 6, 5), True),
        ],
        cls="mixed",
        stored=[acc(MIXED, 1), acc(MIXED, 2)],
    )
    truth[(MIXED, acc(MIXED, 1))] = m1_true
    truth[(MIXED, acc(MIXED, 2))] = m2
    # An undetermined store-backing CIK probes early and is shifted whole.
    u = [utc(2024, 7, d, 14, 0) for d in (1, 2, 3)]
    c.add_cik(
        UNDET,
        [(acc(UNDET, i + 1), early(u[i]), date(2024, 7, i + 1), True) for i in range(3)],
        cls="undetermined_with_pe",
        stored=[acc(UNDET, 1), acc(UNDET, 2)],
    )
    for i in range(3):
        truth[(UNDET, acc(UNDET, i + 1))] = u[i]
    # An undetermined CIK backing no stored row is left unrepaired.
    c.add_cik(
        LONELY,
        [(acc(LONELY, 1), utc(2024, 8, 1, 14, 0), date(2024, 8, 1), True)],
        cls="undetermined",
    )
    c.finish()
    return c, FakeSec(truth)


def run(c: Cache, sec: FakeSec, *extra: str, release: str = "restamp-1382") -> int:
    return repair_stamps.main(c.argv(*extra, release=release), fetch=sec)


# --- the repair -------------------------------------------------------------


def test_apply_repairs_each_kind_of_cik(tmp_path: Path) -> None:
    c, sec = scenario(tmp_path)
    before = c.snapshot()
    assert run(c, sec, "--apply") == 0

    # Early CIK: the bulk-written record moves by exactly the offset (4 h, EDT).
    assert c.stamp(APPLE, APPLE_ACC) == APPLE_TRUE
    assert APPLE_TRUE - early(APPLE_TRUE) == timedelta(hours=4)
    # ... a record added after 10-01, and one whose stamp differs from
    # --bulk-written, do not move.
    assert c.stamp(APPLE, acc(APPLE, 2)) == utc(2025, 11, 3, 14, 0)
    assert c.stamp(APPLE, acc(APPLE, 3)) == utc(2025, 10, 6, 13, 0)
    # A provably early API record takes its SGML value.
    assert c.stamp(API, API_ACC) == API_TRUE
    # A true CIK changes nothing: its file is byte-identical.
    assert c.snapshot()[f"stamps/v1/{TRUE_CIK}.json"] == before[f"stamps/v1/{TRUE_CIK}.json"]
    # A mixed CIK takes SGML per stored filing.
    assert c.stamp(MIXED, acc(MIXED, 1)) == utc(2024, 6, 3, 14, 0)
    assert c.stamp(MIXED, acc(MIXED, 2)) == utc(2024, 6, 4, 14, 0)
    # An undetermined store-backing CIK probed early: all its bulk records move.
    for i, d in enumerate((1, 2, 3)):
        assert c.stamp(UNDET, acc(UNDET, i + 1)) == utc(2024, 7, d, 14, 0)

    rows = {(r["cik"], r["accession"]): r for r in c.manifest()}
    assert rows[(APPLE, APPLE_ACC)]["method"] == "shift"
    assert rows[(API, API_ACC)]["method"] == "sgml"
    assert rows[(MIXED, acc(MIXED, 1))]["method"] == "sgml"
    assert rows[(MIXED, acc(MIXED, 2))]["method"] == "kept"
    assert rows[(UNDET, acc(UNDET, 3))]["method"] == "shift"
    assert (TRUE_CIK, acc(TRUE_CIK, 1)) not in rows
    # Unrepaired stamps sit beside the manifest, with their CIKs listed.
    side = c.dir / "stamps" / "v1.restamp-1382.manifest"
    with gzip.open(f"{side}.unrepaired.csv.gz", "rt", newline="") as f:
        unrepaired = {(r["cik"], r["accession"]) for r in csv.DictReader(f)}
    assert (LONELY, acc(LONELY, 1)) in unrepaired
    assert (MIXED, acc(MIXED, 3)) in unrepaired
    assert (APPLE, acc(APPLE, 2)) in unrepaired  # early CIK, not bulk-written
    assert LONELY in Path(f"{side}.unrepaired_ciks.txt").read_text().split()
    # The backup holds the cache as it was.
    bak = c.dir / "stamps" / "v1.restamp-1382.bak"
    assert {p.name: p.read_bytes() for p in bak.iterdir()} == {
        k.rsplit("/", 1)[1]: v for k, v in before.items() if k.startswith("stamps/v1/")
    }


def test_unchanged_records_items_and_flag_stay_byte_identical(tmp_path: Path) -> None:
    c, sec = scenario(tmp_path)
    raw_before = (c.stamps / f"{APPLE}.json").read_bytes()
    assert run(c, sec, "--apply") == 0
    raw_after = (c.stamps / f"{APPLE}.json").read_bytes()
    before, after = json.loads(raw_before), json.loads(raw_after)
    assert after["items_refreshed"] == before["items_refreshed"]
    for accession, row in before["records"].items():
        if accession == APPLE_ACC:
            assert after["records"][accession][:3] == row[:3]
            assert after["records"][accession][4] == row[4]  # items
        else:
            assert json.dumps(after["records"][accession]) == json.dumps(row)
    # Only the one stamp string differs in the raw bytes.
    expected = raw_before.replace(
        early(APPLE_TRUE).isoformat().encode(), APPLE_TRUE.isoformat().encode()
    )
    assert raw_after == expected


def test_dry_run_writes_nothing_and_requests_nothing(tmp_path: Path, capsys: Any) -> None:
    c, sec = scenario(tmp_path)
    before = c.snapshot()
    assert run(c, sec) == 0
    assert sec.calls == []
    assert c.snapshot() == before
    assert not (c.dir / "stamps" / "v1.restamp-1382.bak").exists()
    report = json.loads(capsys.readouterr().out)
    assert report["step1_shifts"] == 1
    assert report["step2_requests"] == 1
    assert report["step3_requests"] == 2
    assert report["step4_known_requests"] == 2
    assert report["requests_known"] == 5


def test_phase_universe_touches_only_universe_ciks(tmp_path: Path, capsys: Any) -> None:
    c, sec = scenario(tmp_path)
    universe = tmp_path / "universe.txt"
    universe.write_text(f"{APPLE}\n{MIXED}\n")
    before = c.snapshot()
    phase = ("--phase", "universe", "--universe-ciks", str(universe))
    assert run(c, sec, *phase) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["phase"] == "universe"
    assert report["ciks"] == 2
    assert report["requests_known"] == 2  # MIXED's two stored filings
    assert run(c, sec, *phase, "--apply", release="restamp-1382a") == 0
    after = c.snapshot()
    for cik in (API, TRUE_CIK, UNDET, LONELY):
        assert after[f"stamps/v1/{cik}.json"] == before[f"stamps/v1/{cik}.json"]
    assert c.stamp(APPLE, APPLE_ACC) == APPLE_TRUE
    assert {r["cik"] for r in c.manifest("restamp-1382a")} == {APPLE, MIXED}
    # Phase B covers the rest and leaves phase A's CIKs alone.
    capsys.readouterr()
    assert run(c, sec, "--phase", "rest", "--universe-ciks", str(universe)) == 0
    assert json.loads(capsys.readouterr().out)["ciks"] == 4


def test_phase_needs_the_universe_list(tmp_path: Path) -> None:
    c, sec = scenario(tmp_path)
    assert run(c, sec, "--phase", "universe") == 1


# --- refusals ---------------------------------------------------------------


def test_apply_refuses_when_backup_name_is_taken(tmp_path: Path) -> None:
    c, sec = scenario(tmp_path)
    (c.dir / "stamps" / "v1.restamp-1382.bak").mkdir()
    before = c.snapshot()
    assert run(c, sec, "--apply") == 1
    assert c.snapshot() == before
    assert sec.calls == []


def test_apply_refuses_without_disk_room(tmp_path: Path, monkeypatch: Any) -> None:
    c, sec = scenario(tmp_path)

    class Usage:
        free = 10

    monkeypatch.setattr(repair_stamps.shutil, "disk_usage", lambda _p: Usage())
    before = c.snapshot()
    assert run(c, sec, "--apply") == 1
    assert c.snapshot() == before


def test_step2_over_the_limit_stops_before_any_request(tmp_path: Path) -> None:
    c, sec = scenario(tmp_path)
    before = c.snapshot()
    assert run(c, sec, "--apply", "--max-step2-requests", "0") == 1
    assert sec.calls == []
    assert c.snapshot() == before


def test_a_change_in_the_window_aborts_the_whole_run(tmp_path: Path, capsys: Any) -> None:
    c, sec = scenario(tmp_path)
    late_true = utc(2026, 10, 9, 14, 0)  # on the window start
    c2 = "0000000606"
    c.add_cik(c2, [(acc(c2, 1), early(late_true), date(2026, 10, 9), True)], cls="early")
    c.finish()
    before = {k: v for k, v in c.snapshot().items() if k.startswith("stamps/v1/")}
    assert run(c, sec, "--apply") == 1
    after = {k: v for k, v in c.snapshot().items() if k.startswith("stamps/v1/")}
    assert after == before
    assert not (c.dir / "stamps" / "v1.restamp-1382.manifest.csv").exists()
    assert acc(c2, 1) in capsys.readouterr().err


# --- the audit --------------------------------------------------------------


def _many_early(tmp_path: Path, n: int, bad: int) -> tuple[Cache, FakeSec]:
    """``n`` early CIKs with one stored record each; the first ``bad`` have an
    SGML header that does not equal the shift."""
    c = Cache(tmp_path)
    truth = {}
    for i in range(n):
        cik = f"{9000000000 + i:010d}"
        true = utc(2024, 9, 3, 14, 0) + timedelta(minutes=i)
        c.add_cik(
            cik,
            [(acc(cik, 1), early(true), date(2024, 9, 3), True)],
            cls="early",
            stored=[acc(cik, 1)],
        )
        truth[(cik, acc(cik, 1))] = true + (timedelta(minutes=7) if i < bad else timedelta())
    c.finish()
    return c, FakeSec(truth)


def test_an_audit_mismatch_sends_that_cik_filing_by_filing(tmp_path: Path) -> None:
    c, sec = _many_early(tmp_path, 100, 1)  # 1 of 100 is not over 1%: no stop
    assert run(c, sec, "--apply") == 0
    bad = f"{9000000000:010d}"
    assert c.stamp(bad, acc(bad, 1)) == utc(2024, 9, 3, 14, 7)  # its SGML value
    rows = {(r["cik"], r["accession"]): r for r in c.manifest()}
    assert rows[(bad, acc(bad, 1))]["method"] == "sgml"
    good = f"{9000000001:010d}"
    assert rows[(good, acc(good, 1))]["method"] == "shift"


def test_the_stop_rule_writes_nothing(tmp_path: Path) -> None:
    c, sec = _many_early(tmp_path, 100, 2)  # 2 of 100 is over 1%: stop
    before = {k: v for k, v in c.snapshot().items() if k.startswith("stamps/v1/")}
    assert run(c, sec, "--apply") == 1
    after = {k: v for k, v in c.snapshot().items() if k.startswith("stamps/v1/")}
    assert after == before
    assert not (c.dir / "stamps" / "v1.restamp-1382.manifest.csv").exists()


# --- resume -----------------------------------------------------------------


def test_a_resumed_run_repeats_no_request(tmp_path: Path) -> None:
    c, sec = scenario(tmp_path)
    sec.fail_after = 3
    with pytest.raises(httpx.HTTPStatusError):
        run(c, sec, "--apply")
    first = list(sec.calls)
    assert len(first) == 3
    sec.fail_after = None
    assert run(c, sec, "--apply") == 1  # the backup name is taken without --resume
    assert run(c, sec, "--apply", "--resume") == 0
    second = sec.calls[3:]
    assert not set(first) & set(second)
    assert len(sec.calls) == len(set(sec.calls))
    assert c.stamp(APPLE, APPLE_ACC) == APPLE_TRUE


# --- export -----------------------------------------------------------------


def test_export_lists_only_files_still_dated_2026_10_01(tmp_path: Path) -> None:
    c, _ = scenario(tmp_path)
    bulk_day = datetime(2026, 10, 1, 12, tzinfo=NY).timestamp()
    later = datetime(2026, 10, 5, 12, tzinfo=NY).timestamp()
    for p in c.stamps.iterdir():
        os.utime(p, (bulk_day, bulk_day))
    os.utime(c.stamps / f"{MIXED}.json", (later, later))
    out = tmp_path / "export.csv"
    before = c.snapshot()
    assert repair_stamps.main(["--cache-dir", str(c.dir), "export", "--out", str(out)]) == 0
    assert c.snapshot() == before
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert {r["cik"] for r in rows} == {APPLE, API, TRUE_CIK, UNDET, LONELY}
    apple = {r["accession"]: r["stamp"] for r in rows if r["cik"] == APPLE}
    assert apple[APPLE_ACC] == early(APPLE_TRUE).isoformat()
    # Frozen: a second export to the same path refuses.
    with pytest.raises(FileExistsError):
        repair_stamps.main(["--cache-dir", str(c.dir), "export", "--out", str(out)])


def test_shift_matches_t166(tmp_path: Path) -> None:
    """The driver's shift is T166's: the EST fixture header (E15) moves by 5 h."""
    dual = (FIXTURES / "sgml_header_dual_class.txt").read_text()
    true = parse_sgml_header(dual, cik="0001652044").accepted_at
    assert true == utc(2026, 2, 5, 2, 56, 3)
    assert shift_stamp(early(true)) == true
    assert true - early(true) == timedelta(hours=5)
