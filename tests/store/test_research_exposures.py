"""Tests for desk-read exposure records (research-registry spec amendment 2026-10-31,
#2723; plan T356, `store/research.py`).

`record_exposure` is the only writer: every refusal in the amendment's acceptance list
writes no row; it hashes the probe, the kill-line copy and each input itself; it
computes `kill_line_basis`, `probe_verified`, `overlaps_holdout`, `post_boundary` and
`synthetic`; a repeated launch returns the first row. The owner's answers (PR #2727):
`open_data` records raise no N, a `store` record over a family holdout is a spend with a
reason and leaves N, one record per launch, unverified records for `open_data` only.
The no-back-stamp test needs a backtest result row and lives beside the other N tests
in `tests/backtest/test_results.py` (`TestExposuresInN`).
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import registry, research, schema

SOURCE = Path(research.__file__)
#: The fixture hypothesis's holdout in family `profitability`.
HOLDOUT = (date(2024, 1, 1), date(2024, 12, 31))
READ_AT = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
POSTED_AT = datetime(2026, 10, 31, 19, 29, 2, tzinfo=UTC)
KILL_URL = "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186"


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "scratch.duckdb"
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.close()
    return path


@pytest.fixture
def conn(store_path: Path) -> duckdb.DuckDBPyConnection:
    """A store on a temp file that is not `settings.store.path` (records synthetic)."""
    return duckdb.connect(str(store_path))


@pytest.fixture
def real(store_path: Path) -> Settings:
    """Settings whose `store.path` is the test store, so records are not synthetic."""
    return Settings(_env_file=None, store={"path": str(store_path)})


def _hypothesis(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    registry.register_hypothesis(
        conn,
        slug="b3",
        family="profitability",
        title="b3",
        doc_path="docs/hypotheses/b3.md",
        doc_sha256="d" * 64,
        params={"costs.per_side_bps": 15.0},
        in_sample_start=date(2016, 1, 29),
        holdout_start=HOLDOUT[0],
        holdout_end=HOLDOUT[1],
        registered_by="owner",
        settings=settings,
    )


def _file(path: Path, text: str, mtime: datetime | None = None) -> Path:
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime.timestamp(), mtime.timestamp()))
    return path


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _spec(tmp_path: Path, tag: str = "p", **changes: Any) -> research.ExposureInput:
    """A `store` launch in `profitability` over in-sample months, whose probe's mtime
    is an hour before `read_at`."""
    probe = _file(tmp_path / f"probe-{tag}.py", f"print({tag!r})\n", READ_AT - timedelta(hours=1))
    kill = _file(tmp_path / f"kill-{tag}.md", "kill line: fail if t < 2\n")
    fields: dict[str, Any] = {
        "candidate_ref": "#2689",
        "source": "store",
        "family": "profitability",
        "kill_line_url": KILL_URL,
        "kill_line_file": kill,
        "kill_line_posted_at": POSTED_AT,
        "probe_path": probe,
        "probe_sha256": _hash(probe),
        "read_at": READ_AT,
        "window_start": date(2020, 8, 31),
        "window_end": date(2023, 12, 29),
        "pins": {"store_max_ingested_at": "2026-10-31T04:41:00+00:00"},
        "n_configurations": 1,
        "verdict": "fail",
        "desk_read_path": "docs/research/2026-10-31-cash-profitability-long-only-desk-read.md",
    }
    fields.update(changes)
    return research.ExposureInput(**fields)


def _record(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    spec: research.ExposureInput,
    tmp_path: Path,
) -> research.ExposureRecord:
    return research.record_exposure(conn, spec, "test", settings=settings, repo_dir=tmp_path)


def _rows(conn: duckdb.DuckDBPyConnection) -> int:
    (n,) = conn.execute("SELECT COUNT(*) FROM research_exposures").fetchone()  # type: ignore[misc]
    return int(n)


# --- append-only and the one insert ------------------------------------------------


def _inserts_into_exposures(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and re.search(r"INSERT\s+INTO\s+research_exposures\b", sub.value, re.I)
        ):
            return True
        if (
            isinstance(sub, ast.Call)
            and getattr(sub.func, "id", getattr(sub.func, "attr", None)) == "insert_row"
            and any(
                isinstance(a, ast.Constant) and a.value == "research_exposures" for a in sub.args
            )
        ):
            return True
    return False


def test_store_research_still_has_no_update_or_delete() -> None:
    assert not re.search(r"\b(UPDATE|DELETE)\b", SOURCE.read_text(encoding="utf-8"), re.I)


def test_only_record_exposure_inserts_into_research_exposures() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    inserting = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and _inserts_into_exposures(node)
    }
    assert inserting == {"record_exposure"}


def test_the_insert_detector_sees_sql_text_and_insert_row() -> None:
    def sees(body: str) -> bool:
        return _inserts_into_exposures(ast.parse(f"def f():\n    {body}"))

    assert sees('c.execute("INSERT INTO research_exposures")')
    assert sees('insert_row(c, "research_exposures", {})')
    assert not sees('insert_row(c, "research_runs", {})')


def test_a_repeated_launch_returns_the_first_row_and_adds_none(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    spec = _spec(tmp_path)
    first = _record(conn, real, spec, tmp_path)
    again = _record(conn, real, replace(spec, note="run twice"), tmp_path)
    assert again == first
    assert _rows(conn) == 1
    # Another launch (another read_at) of the same probe is another record.
    _record(conn, real, replace(spec, read_at=READ_AT + timedelta(minutes=5)), tmp_path)
    assert _rows(conn) == 2


def test_a_record_carries_what_the_write_measured(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    before = datetime.now(UTC)
    spec = _spec(tmp_path, audit_url="https://github.com/o/r/pull/1#issuecomment-2")
    record = _record(conn, real, spec, tmp_path)
    assert spec.probe_path is not None
    assert record.exposure_id == 1
    assert record.kill_line_sha256 == _hash(spec.kill_line_file)
    assert record.probe_sha256 == _hash(spec.probe_path)
    assert record.probe_verified
    assert record.read_at == READ_AT
    assert record.known_at >= before  # the write time, never read_at
    assert not record.synthetic
    assert not record.overlaps_holdout
    assert record.data_pin["pins"]["store_max_ingested_at"] == {
        "value": "2026-10-31T04:41:00+00:00",
        "basis": "declared",
    }


# --- refusals: each writes no row -----------------------------------------------------


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


REFUSALS: dict[str, tuple[dict[str, Any], str]] = {
    "blank-candidate": ({"candidate_ref": "  "}, "candidate_ref"),
    "bad-source": ({"source": "web"}, "source"),
    "store-without-family": ({"family": None}, "needs a family"),
    "unknown-family": ({"family": "astrology"}, "hypotheses.families"),
    "store-without-pin": ({"pins": {}}, "store_max_ingested_at pin"),
    "naive-pin": ({"pins": {"store_max_ingested_at": "2026-10-31T04:41:00"}}, "naive"),
    "naive-read-at": ({"read_at": _naive(READ_AT)}, "naive"),
    "naive-posted-at": ({"kill_line_posted_at": _naive(POSTED_AT)}, "naive"),
    "read-at-after-write": ({"read_at": datetime.now(UTC) + timedelta(days=1)}, "after the write"),
    "window-end-after-read": ({"window_end": date(2026, 11, 1)}, "after read_at"),
    "window-end-before-start": ({"window_end": date(2020, 1, 1)}, "before window_start"),
    "bad-verdict": ({"verdict": "maybe"}, "verdict"),
    "no-configurations": ({"n_configurations": 0}, "at least one"),
    "desk-read-outside": ({"desk_read_path": "notes/desk-read.md"}, "docs/research/"),
    "desk-read-escape": ({"desk_read_path": "docs/research/../plans/x.md"}, "docs/research/"),
    "desk-read-absolute": ({"desk_read_path": "/docs/research/x.md"}, "docs/research/"),
    "bad-kill-line-url": ({"kill_line_url": "https://github.com/o/r/issues/2689"}, "kill_line_url"),
    "bad-audit-url": ({"audit_url": "see the PR"}, "audit_url"),
    "bad-probe-hash": ({"probe_sha256": "abc"}, "SHA-256"),
    "probe-mismatch": ({"probe_sha256": "0" * 64}, "probe sha mismatch"),
    "no-probe": ({"probe_path": None, "probe_sha256": None}, "probe file"),
    "store-probe-unavailable": (
        {"probe_path": None, "probe_sha256": None, "probe_unavailable_reason": "overwritten"},
        "open_data records only",
    ),
    "reason-without-spend": ({"holdout_reason": "planned look"}, "holdout reason"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_each_refusal_writes_no_row(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path, case: str
) -> None:
    changes, message = REFUSALS[case]
    with pytest.raises(research.ResearchError, match=re.escape(message)):
        _record(conn, real, _spec(tmp_path, **changes), tmp_path)
    assert _rows(conn) == 0


def test_a_probe_mismatch_is_refused_before_any_input_is_read(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    """The mismatch is refused first: a missing input file is never reached."""
    spec = _spec(tmp_path, probe_sha256="0" * 64, inputs=(tmp_path / "absent.zip",))
    with pytest.raises(research.ResearchError, match="probe sha mismatch"):
        _record(conn, real, spec, tmp_path)
    assert _rows(conn) == 0


# --- computed fields -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("posted_at", "basis"),
    [
        (POSTED_AT, "posted_before_read"),
        (READ_AT, "posted_before_read"),
        (READ_AT + timedelta(seconds=1), "posted_after_read"),
    ],
)
def test_kill_line_basis_is_computed(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path, posted_at: datetime, basis: str
) -> None:
    record = _record(conn, real, _spec(tmp_path, kill_line_posted_at=posted_at), tmp_path)
    assert record.kill_line_basis == basis


def test_a_blob_at_commit_kill_line_url_is_accepted(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    url = "https://github.com/o/r/blob/2bc99668/docs/research/hypothesis-backlog.md"
    assert _record(conn, real, _spec(tmp_path, kill_line_url=url), tmp_path).kill_line_url == url


def test_inputs_are_hashed_and_pinned_by_mtime(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    early = _file(tmp_path / "companyfacts.zip", "facts", READ_AT - timedelta(days=1))
    late = _file(tmp_path / "late.parquet", "later", READ_AT + timedelta(hours=1))
    record = _record(conn, real, _spec(tmp_path, inputs=(early, late)), tmp_path)
    pinned = {entry["name"]: entry for entry in record.data_pin["inputs"]}
    assert pinned["companyfacts.zip"]["sha256"] == _hash(early)
    assert pinned["companyfacts.zip"]["basis"] == "measured"
    assert pinned["late.parquet"]["basis"] == "declared"


def test_a_probe_changed_after_the_read_is_unverified(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    """A matching hash with an mtime after `read_at` and no earlier durable hash proves
    nothing about what ran: refused for `store`, recorded unverified for `open_data`."""
    spec = _spec(tmp_path)
    assert spec.probe_path is not None
    os.utime(spec.probe_path, ((READ_AT + timedelta(hours=1)).timestamp(),) * 2)
    with pytest.raises(research.ResearchError, match="probe not verified"):
        _record(conn, real, spec, tmp_path)
    assert _rows(conn) == 0
    record = _record(conn, real, replace(spec, source="open_data", pins=None), tmp_path)
    assert record.probe_sha256 == _hash(spec.probe_path)
    assert not record.probe_verified


def test_a_hash_in_a_kill_line_posted_before_the_read_verifies_the_probe(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    """Candidate 5's launch 2: the frozen file is rebuilt today (mtime after the read),
    and its hash was in the kill line posted before the read."""
    spec = _spec(tmp_path)
    assert spec.probe_path is not None and spec.probe_sha256 is not None
    os.utime(spec.probe_path, ((READ_AT + timedelta(days=1)).timestamp(),) * 2)
    kill = _file(tmp_path / "kill-hash.md", f"frozen probe sha256 {spec.probe_sha256}\n")
    record = _record(conn, real, replace(spec, kill_line_file=kill), tmp_path)
    assert record.probe_verified
    # The same text posted after the read proves nothing.
    late = replace(
        spec,
        kill_line_file=kill,
        kill_line_posted_at=READ_AT + timedelta(minutes=1),
        read_at=READ_AT - timedelta(minutes=1),
        candidate_ref="#late",
    )
    with pytest.raises(research.ResearchError, match="probe not verified"):
        _record(conn, real, late, tmp_path)


def test_an_unavailable_probe_is_an_unverified_open_data_record(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    """Open question 4 (b): CBOperProf's first run, whose file was overwritten."""
    spec = _spec(
        tmp_path,
        source="open_data",
        pins=None,
        probe_path=None,
        probe_sha256=None,
        probe_unavailable_reason="a display-only edit overwrote it",
        n_configurations=12,
    )
    record = _record(conn, real, spec, tmp_path)
    assert (record.probe_path, record.probe_sha256, record.probe_verified) == (None, None, False)
    assert record.note == "probe unavailable: a display-only edit overwrote it"
    assert _record(conn, real, spec, tmp_path) == record  # NULL hash compared as empty
    assert _rows(conn) == 1
    with pytest.raises(research.ResearchError, match="non-blank reason"):
        _record(conn, real, replace(spec, probe_unavailable_reason=" "), tmp_path)


def test_overlaps_holdout_and_the_spend_rule(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    """Open question 2 (b): a `store` record over a family holdout is a spend; it needs a
    reason and leaves the count. An `open_data` overlap is only shown."""
    _hypothesis(conn, real)
    outside = _record(conn, real, _spec(tmp_path, "out", window_end=date(2023, 12, 29)), tmp_path)
    assert not outside.overlaps_holdout
    into = _spec(tmp_path, "in", candidate_ref="#in", window_end=date(2024, 3, 29))
    with pytest.raises(research.ResearchError, match="needs a holdout reason"):
        _record(conn, real, into, tmp_path)
    spend = _record(conn, real, replace(into, holdout_reason="owner's look"), tmp_path)
    assert spend.overlaps_holdout and spend.holdout_reason == "owner's look"
    shown = _record(
        conn, real, replace(into, candidate_ref="#open", source="open_data", pins=None), tmp_path
    )
    assert shown.overlaps_holdout and shown.holdout_reason is None
    assert research.family_exposure_count(conn, "profitability") == 1


def test_post_boundary_follows_the_newest_boundary_row(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    assert not _record(conn, real, _spec(tmp_path, "none"), tmp_path).post_boundary
    registry.write_development_boundary(conn, boundary=date(2023, 12, 29), reason="test")
    first = _record(conn, real, _spec(tmp_path, "on", candidate_ref="#on"), tmp_path)
    assert first.post_boundary  # window_end on the boundary
    registry.write_development_boundary(conn, boundary=date(2025, 6, 30), reason="later")
    second = _record(conn, real, _spec(tmp_path, "before", candidate_ref="#b"), tmp_path)
    assert not second.post_boundary


def test_synthetic_is_by_file_identity(
    conn: duckdb.DuckDBPyConnection, real: Settings, settings: Settings, tmp_path: Path
) -> None:
    assert not _record(conn, real, _spec(tmp_path, "r"), tmp_path).synthetic
    temp = _record(conn, settings, _spec(tmp_path, "t", candidate_ref="#t"), tmp_path)
    assert temp.synthetic


# --- counts and listings ---------------------------------------------------------------


def test_family_exposure_count_counts_non_synthetic_store_records(
    conn: duckdb.DuckDBPyConnection, real: Settings, settings: Settings, tmp_path: Path
) -> None:
    _record(conn, real, _spec(tmp_path, "a", candidate_ref="#a", n_configurations=1), tmp_path)
    _record(conn, real, _spec(tmp_path, "b", candidate_ref="#b", n_configurations=1), tmp_path)
    _record(conn, real, _spec(tmp_path, "c", candidate_ref="#c", source="open_data"), tmp_path)
    _record(conn, settings, _spec(tmp_path, "d", candidate_ref="#d"), tmp_path)
    assert research.family_exposure_count(conn, "profitability") == 2
    assert research.family_exposure_count(conn, "momentum") == 0


def test_list_exposures_hides_synthetic_and_filters(
    conn: duckdb.DuckDBPyConnection, real: Settings, settings: Settings, tmp_path: Path
) -> None:
    kept = _record(conn, real, _spec(tmp_path, "a", candidate_ref="#a"), tmp_path)
    temp = _record(conn, settings, _spec(tmp_path, "t", candidate_ref="#t"), tmp_path)
    assert research.list_exposures(conn) == [kept]
    assert research.list_exposures(conn, include_synthetic=True) == [temp, kept]
    assert research.list_exposures(conn, candidate="#t", include_synthetic=True) == [temp]
    assert research.list_exposures(conn, family="momentum") == []


def test_exposure_candidates_since_a_date(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    def at(tag: str, ref: str, read_at: datetime, verdict: str) -> research.ExposureRecord:
        spec = _spec(tmp_path, tag, candidate_ref=ref, read_at=read_at, verdict=verdict)
        assert spec.probe_path is not None
        os.utime(spec.probe_path, ((read_at - timedelta(hours=1)).timestamp(),) * 2)
        return _record(conn, real, spec, tmp_path)

    old = datetime(2026, 10, 20, 12, tzinfo=UTC)
    at("o", "#old", old, "fail")
    early = at("e1", "#again", old, "fail")
    later = at("e2", "#again", datetime(2026, 10, 30, 1, tzinfo=UTC), "insufficient")
    c5a = at("c1", "#2689", READ_AT, "incomplete")
    c5b = at("c2", "#2689", READ_AT + timedelta(minutes=3), "fail")
    found = research.exposure_candidates(conn, date(2026, 10, 30))
    assert [c.candidate_ref for c in found] == ["#again", "#2689"]
    again, c5 = found
    assert again.first_read_at == later.read_at and again.records == (later,)
    assert again.earlier_records and early not in again.records
    assert c5.records == (c5a, c5b) and c5.verdicts == ("incomplete", "fail")
    assert not c5.earlier_records


@pytest.mark.parametrize(
    "reader",
    [
        lambda c: research.family_exposure_count(c, "profitability"),
        lambda c: research.list_exposures(c),
        lambda c: research.exposure_candidates(c, date(2026, 10, 30)),
    ],
    ids=["count", "list", "candidates"],
)
def test_a_store_without_the_table_raises_exposures_not_initialised(
    conn: duckdb.DuckDBPyConnection, reader: Any
) -> None:
    conn.execute("DROP TABLE research_exposures")
    with pytest.raises(research.ExposuresNotInitialised):
        reader(conn)
    assert issubclass(research.ExposuresNotInitialised, schema.ResearchNotInitialised)


def test_record_exposure_refuses_a_store_without_the_table(
    conn: duckdb.DuckDBPyConnection, real: Settings, tmp_path: Path
) -> None:
    conn.execute("DROP TABLE research_exposures")
    with pytest.raises(research.ExposuresNotInitialised):
        _record(conn, real, _spec(tmp_path), tmp_path)
