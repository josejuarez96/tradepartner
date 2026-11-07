"""Tests for the desk-read exposure backfill (plan T359, #3116; research-registry spec
amendment 2026-10-31, "Backfill").

The backfill reads a reviewed TOML manifest, pins every file it names by SHA-256, and
writes each entry through `research.record_exposure`, the only writer, inside one
transaction under the open data release the manifest names. A dry run computes every
row on a read-only connection and writes nothing. Run twice, it adds no row.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import exposure_backfill as eb
from tradepartner.store import registry, research, schema

RELEASE = "exposures-test"
POSTED_AT = datetime(2026, 10, 31, 19, 29, 2, tzinfo=UTC)
READ_2 = datetime(2026, 10, 31, 19, 30, tzinfo=UTC)
READ_3 = datetime(2026, 10, 31, 19, 40, tzinfo=UTC)
KILL_URL = "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186"
DESK = "docs/research/2026-10-31-cash-profitability-long-only-desk-read.md"


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _file(path: Path, text: str, mtime: datetime | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in value.items()) + " }"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Fixture:
    """A manifest directory: two `store` launches of candidate 5 in `profitability`
    (one probe whose hash the kill line posted before the read, one whose mtime is
    before the read) and two `open_data` reads (one with its probe, one unavailable)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "backfill"
        frozen = _file(self.dir / "probes" / "frozen.py", "print('launch 2')\n")
        audited = _file(
            root / "probes" / "probe.py", "print('launch 3')\n", READ_3 - timedelta(minutes=5)
        )
        kill = _file(
            self.dir / "kill-lines" / "2689.md",
            f"Kill line: fail if t < 2. Frozen probe sha256 {_hash(frozen)}\n",
        )
        open_kill = _file(self.dir / "kill-lines" / "2675.md", "CBOperProf kill line\n")
        cz = _file(root / "cz" / "signals.csv", "permno,ret\n1,0.1\n", READ_2 - timedelta(days=1))
        open_probe = _file(root / "cbop" / "compute.py", "print('cbop')\n", READ_2)
        self.frozen, self.audited, self.cz = frozen, audited, cz
        base: dict[str, Any] = {
            "candidate_ref": "#2689",
            "source": "store",
            "family": "profitability",
            "issue_url": "https://github.com/josejuarez96/tradepartner/issues/2689",
            "kill_line_url": KILL_URL,
            "kill_line_file": "kill-lines/2689.md",
            "kill_line_sha256": _hash(kill),
            "kill_line_posted_at": POSTED_AT.isoformat(),
            "window_start": "2020-08-31",
            "window_end": "2023-12-29",
            "n_configurations": 1,
            "desk_read_path": DESK,
            "pins": {"store_max_ingested_at": "2026-10-31T04:41:00+00:00"},
            "date_seen": "2026-11-06",
            "evidence": ["audit section 1"],
        }
        self.entries: list[dict[str, Any]] = [
            {
                **base,
                "key": "c5-launch-2",
                "probe_path": "probes/frozen.py",
                "probe_sha256": _hash(frozen),
                "read_at": READ_2.isoformat(),
                "read_at_basis": "bound",
                "read_at_evidence": "after the kill line, before the edited file's mtime",
                "verdict": "incomplete",
            },
            {
                **base,
                "key": "c5-launch-3",
                "probe_path": str(audited),
                "probe_sha256": _hash(audited),
                "read_at": READ_3.isoformat(),
                "read_at_basis": "bound",
                "read_at_evidence": "after the probe's mtime, before out.txt's",
                "verdict": "fail",
            },
            {
                **base,
                "key": "cbop-rerun",
                "candidate_ref": "CBOperProf",
                "source": "open_data",
                "kill_line_url": "https://github.com/josejuarez96/tradepartner/issues/2675#issuecomment-1",
                "kill_line_file": "kill-lines/2675.md",
                "kill_line_sha256": _hash(open_kill),
                "kill_line_posted_at": "2026-10-31T05:06:46+00:00",
                "probe_path": str(open_probe),
                "probe_sha256": _hash(open_probe),
                "inputs": [{"path": str(cz), "sha256": _hash(cz)}],
                "pins": {},
                "read_at": READ_2.isoformat(),
                "read_at_basis": "measured",
                "read_at_evidence": "the exploratory-cut ledger",
                "window_start": "2009-01-31",
                "window_end": "2025-12-31",
                "n_configurations": 12,
                "verdict": "fail",
                "desk_read_path": "docs/research/2026-10-31-cash-op-profitability-desk-read.md",
            },
            {
                **base,
                "key": "cbop-first",
                "candidate_ref": "CBOperProf",
                "source": "open_data",
                "kill_line_url": "https://github.com/josejuarez96/tradepartner/issues/2675#issuecomment-1",
                "kill_line_file": "kill-lines/2675.md",
                "kill_line_sha256": _hash(open_kill),
                "kill_line_posted_at": "2026-10-31T05:06:46+00:00",
                "probe_unavailable_reason": "overwritten by a display-only edit",
                "pins": {},
                "read_at": (READ_2 - timedelta(minutes=2)).isoformat(),
                "read_at_basis": "unverified",
                "read_at_evidence": "the ledger's first-run time",
                "window_start": "2009-01-31",
                "window_end": "2025-12-31",
                "n_configurations": 12,
                "verdict": "fail",
                "desk_read_path": "docs/research/2026-10-31-cash-op-profitability-desk-read.md",
            },
        ]

    def write(self, release: str = RELEASE, **header: Any) -> Path:
        lines = ["[manifest]", "schema = 1", f'release = "{release}"', 'title = "test"']
        lines += [f"{k} = {_toml_value(v)}" for k, v in header.items()]
        for entry in self.entries:
            lines += ["", "[[exposure]]"]
            lines += [f"{k} = {_toml_value(v)}" for k, v in entry.items()]
        path = self.dir / "manifest.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "store.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def real(store_path: Path) -> Settings:
    """Settings whose `store.path` is the test store, so records are not synthetic."""
    return Settings(_env_file=None, store={"path": str(store_path)})


def _open_release(conn: duckdb.DuckDBPyConnection, name: str = RELEASE) -> None:
    registry.open_data_release(conn, conn, name=name, backup_path="b.duckdb", reason="#3116")


def _rows(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT count(*) FROM research_exposures").fetchone()
    assert row is not None
    return int(row[0])


def _apply(
    conn: duckdb.DuckDBPyConnection, path: Path, settings: Settings, tmp: Path
) -> eb.BackfillResult:
    manifest = eb.load_manifest(path)
    return eb.apply_backfill(
        conn, manifest, release=RELEASE, settings=settings, recorded_by="owner", repo_dir=tmp
    )


# --- the manifest ---------------------------------------------------------------


def test_load_resolves_paths_and_reads_timestamps_as_utc(fx: Fixture) -> None:
    manifest = eb.load_manifest(fx.write())
    assert manifest.release == RELEASE
    assert manifest.sha256 == _hash(fx.dir / "manifest.toml")
    first = manifest.entries[0]
    assert first.key == "c5-launch-2"
    assert first.spec.probe_path == fx.frozen
    assert first.spec.kill_line_file == fx.dir / "kill-lines" / "2689.md"
    assert first.spec.read_at == READ_2 and first.spec.read_at.tzinfo is not None
    assert first.spec.window_start == date(2020, 8, 31)
    assert manifest.entries[3].spec.probe_unavailable_reason is not None
    assert manifest.entries[2].spec.inputs == (fx.cz,)


def test_load_expands_a_home_path(fx: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(fx.root))
    fx.entries[1]["probe_path"] = "~/probes/probe.py"
    assert eb.load_manifest(fx.write()).entries[1].spec.probe_path == fx.audited


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"read_at": "2026-10-31T19:40:00"}, "naive"),
        ({"kill_line_posted_at": "2026-10-31T19:29:02"}, "naive"),
        ({"read_at_basis": "guessed"}, "read_at_basis"),
        ({"evidence": []}, "evidence"),
        ({"date_seen": "yesterday"}, "date_seen"),
        ({"surprise": 1}, "unknown field"),
        ({"kill_line_sha256": "0" * 64}, "kill-line copy"),
        ({"probe_path": "probes/missing.py"}, "probe"),
    ],
)
def test_load_refuses_a_bad_entry(fx: Fixture, change: dict[str, Any], message: str) -> None:
    fx.entries[0].update(change)
    with pytest.raises(eb.BackfillRefused, match=message):
        eb.load_manifest(fx.write())


def test_load_refuses_a_missing_required_field(fx: Fixture) -> None:
    del fx.entries[0]["verdict"]
    with pytest.raises(eb.BackfillRefused, match="verdict"):
        eb.load_manifest(fx.write())


def test_load_refuses_an_input_whose_hash_moved(fx: Fixture) -> None:
    fx.cz.write_text("permno,ret\n1,0.2\n", encoding="utf-8")
    with pytest.raises(eb.BackfillRefused, match="input"):
        eb.load_manifest(fx.write())


def test_load_refuses_a_probe_whose_hash_moved(fx: Fixture) -> None:
    fx.entries[1]["probe_sha256"] = "1" * 64
    with pytest.raises(eb.BackfillRefused, match="probe"):
        eb.load_manifest(fx.write())


def test_load_refuses_a_repeated_key_or_launch(fx: Fixture) -> None:
    fx.entries.append(dict(fx.entries[0]))
    with pytest.raises(eb.BackfillRefused, match="repeat"):
        eb.load_manifest(fx.write())
    fx.entries[-1]["key"] = "another"
    with pytest.raises(eb.BackfillRefused, match="repeat"):
        eb.load_manifest(fx.write())


def test_load_refuses_a_bad_header(fx: Fixture) -> None:
    with pytest.raises(eb.BackfillRefused, match="release"):
        eb.load_manifest(fx.write(release="Bad Name"))
    path = fx.write()
    path.write_text(path.read_text().replace("schema = 1", "schema = 2"), encoding="utf-8")
    with pytest.raises(eb.BackfillRefused, match="schema"):
        eb.load_manifest(path)


# --- the dry run ----------------------------------------------------------------


def test_dry_run_computes_every_row_and_writes_nothing(
    fx: Fixture, store_path: Path, real: Settings, tmp_path: Path
) -> None:
    manifest = eb.load_manifest(fx.write())
    with duckdb.connect(str(store_path), read_only=True) as ro:
        plan = eb.plan_backfill(ro, manifest, settings=real, repo_dir=tmp_path)
    assert [i.entry.key for i in plan.new] == [e.key for e in manifest.entries]
    assert plan.existing == ()
    rows = {i.entry.key: i.row for i in plan.new}
    # Launch 2's probe was hashed after the read, but the kill line posted the hash first.
    assert rows["c5-launch-2"]["probe_verified"] is True
    assert rows["c5-launch-2"]["kill_line_basis"] == "posted_before_read"
    assert rows["c5-launch-3"]["probe_verified"] is True
    assert rows["cbop-first"]["probe_verified"] is False
    assert all(not r["synthetic"] for r in rows.values())
    assert plan.n_added == {"profitability": 2}
    assert "release exposures-test" in rows["c5-launch-2"]["note"]
    assert "read_at bound" in rows["c5-launch-2"]["note"]
    assert "UNVERIFIED" in rows["cbop-first"]["note"]
    with duckdb.connect(str(store_path)) as conn:
        assert _rows(conn) == 0


def test_dry_run_refuses_what_the_writer_refuses(
    fx: Fixture, store_path: Path, real: Settings, tmp_path: Path
) -> None:
    """A `store` probe hashed after the read with its hash nowhere durable is refused
    before anything could be written."""
    fx.entries[0]["kill_line_file"] = "kill-lines/2675.md"
    fx.entries[0]["kill_line_sha256"] = _hash(fx.dir / "kill-lines" / "2675.md")
    manifest = eb.load_manifest(fx.write())
    with (
        duckdb.connect(str(store_path), read_only=True) as ro,
        pytest.raises(eb.BackfillRefused, match=r"c5-launch-2.*not verified"),
    ):
        eb.plan_backfill(ro, manifest, settings=real, repo_dir=tmp_path)


# --- the write ------------------------------------------------------------------


def test_the_write_needs_the_manifests_release_open(
    fx: Fixture, store_path: Path, real: Settings, tmp_path: Path
) -> None:
    path = fx.write()
    with duckdb.connect(str(store_path)) as conn:
        with pytest.raises(eb.BackfillRefused, match="no release is open"):
            _apply(conn, path, real, tmp_path)
        _open_release(conn, "other-release")
        with pytest.raises(eb.BackfillRefused, match="'other-release' is open"):
            _apply(conn, path, real, tmp_path)
        assert _rows(conn) == 0
    manifest = eb.load_manifest(path)
    with duckdb.connect(str(store_path)) as conn, pytest.raises(eb.BackfillRefused, match="names"):
        eb.apply_backfill(conn, manifest, release="other-release", settings=real, recorded_by="o")


def test_the_write_records_every_entry_once_and_a_rerun_adds_none(
    fx: Fixture, store_path: Path, real: Settings, tmp_path: Path
) -> None:
    path = fx.write()
    with duckdb.connect(str(store_path)) as conn:
        _open_release(conn)
        first = _apply(conn, path, real, tmp_path)
        assert len(first.written) == 4 and first.existing == ()
        assert _rows(conn) == 4
        assert research.family_exposure_count(conn, "profitability") == 2
        again = _apply(conn, path, real, tmp_path)
        assert again.written == ()
        assert sorted(r.exposure_id for r in again.existing) == sorted(
            r.exposure_id for r in first.written
        )
        assert _rows(conn) == 4
        assert research.family_exposure_count(conn, "profitability") == 2
        plan = eb.plan_backfill(conn, eb.load_manifest(path), settings=real)
        assert plan.new == () and plan.n_added == {}
        assert len(plan.existing) == 4


def test_a_refusal_anywhere_writes_nothing(
    fx: Fixture, store_path: Path, real: Settings, tmp_path: Path
) -> None:
    """All or nothing: the last entry's window ends after its read, so the first three,
    which would be valid, are not written either."""
    fx.entries[3]["window_end"] = "2026-12-31"
    path = fx.write()
    with duckdb.connect(str(store_path)) as conn:
        _open_release(conn)
        with pytest.raises(eb.BackfillRefused, match="cbop-first"):
            _apply(conn, path, real, tmp_path)
        assert _rows(conn) == 0


def test_a_failure_inside_the_transaction_rolls_back(
    fx: Fixture,
    store_path: Path,
    real: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    original = research.record_exposure

    def flaky(*args: Any, **kwargs: Any) -> research.ExposureRecord:
        calls["n"] += 1
        if calls["n"] == 3:
            raise duckdb.IOException("disk went away")
        return original(*args, **kwargs)

    monkeypatch.setattr(research, "record_exposure", flaky)
    path = fx.write()
    with duckdb.connect(str(store_path)) as conn:
        _open_release(conn)
        with pytest.raises(duckdb.IOException):
            _apply(conn, path, real, tmp_path)
        assert _rows(conn) == 0


def test_no_back_stamp_known_at_is_the_write_time(
    fx: Fixture, store_path: Path, real: Settings, tmp_path: Path
) -> None:
    """No look-ahead: every record's `read_at` is the declared past launch and its
    `known_at` is the moment of the backfill, never the launch time."""
    path = fx.write()
    with duckdb.connect(str(store_path)) as conn:
        _open_release(conn)
        before = datetime.now(UTC)
        result = _apply(conn, path, real, tmp_path)
        after = datetime.now(UTC)
    for record in result.written:
        assert record.read_at < before <= record.known_at <= after


def test_records_on_a_store_that_is_not_the_real_one_are_synthetic(
    fx: Fixture, store_path: Path, tmp_path: Path
) -> None:
    other = Settings(_env_file=None, store={"path": str(tmp_path / "elsewhere.duckdb")})
    with duckdb.connect(str(store_path)) as conn:
        _open_release(conn)
        result = _apply(conn, fx.write(), other, tmp_path)
        assert all(r.synthetic for r in result.written)
        assert research.family_exposure_count(conn, "profitability") == 0


# --- the committed manifest ------------------------------------------------------

COMMITTED = Path(__file__).parents[2] / "docs" / "research" / "exposure-backfill"


def test_the_committed_manifest_parses_and_its_kill_lines_hash() -> None:
    """The reviewed manifest's structure and its in-repo kill-line copies; the probe
    files live on the owner's Mac, so their hashes are checked at the sitting."""
    raw = eb.read_manifest_text(COMMITTED / "manifest.toml")
    assert raw.release == "exposures-2026-11-09"
    for entry in raw.entries:
        copy = COMMITTED / entry["kill_line_file"]
        assert _hash(copy) == entry["kill_line_sha256"], entry["key"]
    store = [e for e in raw.entries if e["source"] == "store"]
    assert {e["candidate_ref"] for e in store} == {"#2689"}
    assert {e["family"] for e in store} == {"profitability"}
    assert sum(e["n_configurations"] for e in store) == 2
    candidates = {e["candidate_ref"] for e in raw.entries}
    for expected in ("#2689", "#2675", "I1", "#2669", "#2828", "#2888", "#2946", "#3092"):
        assert expected in candidates
