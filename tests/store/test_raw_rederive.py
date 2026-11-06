"""The bounded raw re-derive (plan T352, #2714; spec amendment #2714 items 1, 3, 4).

Each test builds a fixture store and archive under `tmp_path` (`lookahead.rederive_rig`),
fakes the disk and iCloud, and never fetches: the parse reads only archived bodies.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
from lookahead.rederive_rig import (
    ACC,
    ACCEPTED,
    BULK_ACC,
    DAILY,
    FETCHED,
    FILE_DATE,
    LOW,
    NOW,
    PLENTY,
    RUN_AT,
    WRONG_SHARES,
    XML,
    Rig,
    bulk_zip,
)

from tradepartner.adapters.form4_archive import BULK_SOURCE, XML_SOURCE
from tradepartner.store import raw_archive, raw_rederive
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.raw_archive import (
    RawArchiveIntegrityError,
    RawArchiveMissingError,
    RawArchiveOfflineError,
)
from tradepartner.store.raw_archive_config import RawArchiveFloorError
from tradepartner.store.raw_rederive import RederiveRefused, rederive

REL = "rederive-2714"


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Rig:
    return Rig(tmp_path, monkeypatch)


def _run(rig: Rig, **kw: object) -> object:
    kw.setdefault("mode", "backfill")
    kw.setdefault("accessions", [ACC])
    return rederive(rig.settings, rig.icloud, clock=lambda: NOW, **kw)  # type: ignore[arg-type]


def _digest(rig: Rig, **kw: object) -> str:
    """The dry run's correction digest, which a write must name."""
    return _run(rig, **kw).digest  # type: ignore[attr-defined, no-any-return]


# --- dry run: the match ------------------------------------------------------------


def test_a_dry_run_maps_the_row_reads_its_body_and_writes_nothing(rig: Rig) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    before, runs = rig.table(), rig.runs()

    result = _run(rig)

    assert result.runs == ("run-1",)  # type: ignore[attr-defined]
    assert result.hashes == (hashlib.sha256(XML).hexdigest(),)  # type: ignore[attr-defined]
    (fix,) = result.corrections  # type: ignore[attr-defined]
    assert fix.new == {"shares": Decimal("10000")}
    assert fix.known_at == ACCEPTED
    assert result.unchanged == 1  # type: ignore[attr-defined]
    assert result.refused == ()  # type: ignore[attr-defined]
    assert rig.table() == before
    assert rig.runs() == runs


def test_a_bulk_row_maps_to_its_writer_not_the_zero_width_quarter_run(rig: Rig) -> None:
    zipped = bulk_zip()
    rig.form4_run(row_source=BULK_SOURCE, body=zipped, writer="edgar", wrong=WRONG_SHARES)
    assert any(r[1] == BULK_SOURCE for r in rig.runs())  # the quarter row cites the digest

    result = _run(rig)

    assert result.runs == ("run-1",)  # type: ignore[attr-defined]
    assert result.hashes == (hashlib.sha256(zipped).hexdigest(),)  # type: ignore[attr-defined]
    assert [c.new for c in result.corrections] == [  # type: ignore[attr-defined]
        {"shares": Decimal("10000")}
    ]


def test_a_daily_index_row_maps_by_its_file_date(rig: Rig) -> None:
    rig.index_run(wrong_name="ACME CORP.")

    result = rederive(
        rig.settings, rig.icloud, mode="session", file_dates=[FILE_DATE], clock=lambda: NOW
    )

    (fix,) = result.corrections
    assert fix.new == {"company_name": "ACME CORP"}
    assert result.hashes == (hashlib.sha256(DAILY).hexdigest(),)


def test_a_daily_row_keeps_its_first_listing_position(rig: Rig) -> None:
    rig.index_run(position=5)  # a revision keeps the first listing's position
    result = rederive(
        rig.settings, rig.icloud, mode="session", file_dates=[FILE_DATE], clock=lambda: NOW
    )
    assert result.corrections == () and result.unchanged == 1


def test_expect_without_a_release_is_refused(rig: Rig) -> None:
    rig.form4_run()
    with pytest.raises(RederiveRefused, match="--expect needs --release"):
        _run(rig, expect=1)


def test_a_known_inside_the_window_row_is_refused(rig: Rig) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_paper_window(ACCEPTED - timedelta(days=60))  # opened before known_at
    dry = _run(rig)
    assert len(dry.refused) == 1 and "open paper window" in dry.refused[0]  # type: ignore[attr-defined]


def test_a_bulk_entry_whose_zip_its_manifest_omits_is_refused(rig: Rig) -> None:
    rig.form4_run(
        row_source=BULK_SOURCE, body=bulk_zip(), writer="edgar", accession=BULK_ACC, zips=[]
    )
    _refused(rig, "lists no such quarter zip", accessions=[BULK_ACC])


def _refused(rig: Rig, match: str, **kw: object) -> None:
    before = rig.table()
    with pytest.raises(RederiveRefused, match=match):
        _run(rig, **kw)
    assert rig.table() == before
    assert rig.icloud.downloads == []


def test_an_orphan_row_fails_the_dry_run(rig: Rig) -> None:
    rig.form4_run(store=False)
    rig.form4_run(run_id="run-2", body=XML, entry_rows=[], wrong=WRONG_SHARES)
    _refused(rig, "no manifest entry wrote")


def test_a_row_two_writer_runs_claim_fails_the_dry_run(rig: Rig) -> None:
    rig.form4_run()
    rig.form4_run(run_id="run-2", store=False)
    _refused(rig, "more than one manifest entry")


def test_a_row_whose_entry_has_another_row_source_fails(rig: Rig) -> None:
    rig.form4_run(entry_source=BULK_SOURCE)
    _refused(rig, "no manifest entry wrote")


def test_a_row_whose_writer_ran_in_another_mode_fails(rig: Rig) -> None:
    rig.form4_run(mode="session")
    _refused(rig, "no manifest entry wrote")


def test_a_row_whose_key_or_digest_differs_from_the_entry_fails(rig: Rig) -> None:
    rows = rig.form4_rows(row_source=XML_SOURCE, body=XML)
    rows[0]["line_no"] = 7
    rig.form4_run(entry_rows=rows)
    _refused(rig, "no manifest entry wrote")


def test_a_manifest_naming_another_run_fails(rig: Rig) -> None:
    rig.form4_run()
    with raw_archive_conn(rig) as conn:
        conn.execute("UPDATE ingestion_runs SET run_id = 'other' WHERE run_id = 'run-1'")
    _refused(rig, "names run run-1")


def test_an_input_fetched_after_its_run_finished_fails(rig: Rig) -> None:
    rig.form4_run(fetched_at=RUN_AT + timedelta(days=30))
    _refused(rig, "fetched after run run-1")


def test_an_xml_entry_with_no_fetch_time_fails(rig: Rig) -> None:
    rig.form4_run(fetched_at=None, wrong=WRONG_SHARES)
    _refused(rig, "has no fetch time")


def test_a_daily_entry_with_no_fetch_time_fails(rig: Rig) -> None:
    rig.index_run(wrong_name="ACME CORP.", fetched_at=None)
    with pytest.raises(RederiveRefused, match="has no fetch time"):
        rederive(
            rig.settings, rig.icloud, mode="session", file_dates=[FILE_DATE], clock=lambda: NOW
        )
    assert rig.icloud.downloads == []


def test_a_bulk_entry_with_no_fetch_time_maps_through_its_listed_zip(rig: Rig) -> None:
    # A zip cached before T348 carries no fetch record; the run's own zips list,
    # which also has no fetch time, names its quarter and hash.
    rig.form4_run(
        row_source=BULK_SOURCE,
        body=bulk_zip(),
        writer="edgar",
        fetched_at=None,
        wrong=WRONG_SHARES,
    )
    result = _run(rig)
    assert [c.new for c in result.corrections] == [{"shares": Decimal("10000")}]  # type: ignore[attr-defined]


def test_a_bulk_entry_with_no_fetch_time_whose_zip_has_one_fails(rig: Rig) -> None:
    zipped = bulk_zip()
    sha = hashlib.sha256(zipped).hexdigest()
    rig.form4_run(
        row_source=BULK_SOURCE,
        body=zipped,
        writer="edgar",
        fetched_at=None,
        zips=[{"quarter": "2026q1", "sha256": sha, "fetched_at": FETCHED.isoformat()}],
    )
    _refused(rig, "has no fetch time")


def test_a_selection_with_no_stored_row_fails(rig: Rig) -> None:
    rig.form4_run()
    _refused(rig, "no Form 4 XML or bulk row", accessions=["0000000001-26-000001"])
    _refused(rig, "not both", file_dates=[FILE_DATE])


# --- inputs: all bodies before any parse; the floor before and during -------------


def test_every_named_body_is_requested_before_any_parse_and_one_offline_fails_all(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    zipped = bulk_zip()
    rig.form4_run(wrong=WRONG_SHARES)
    rig.form4_run(
        run_id="run-2", row_source=BULK_SOURCE, body=zipped, writer="edgar", accession=BULK_ACC
    )
    xml_path, zip_path = rig.body_path(XML), rig.body_path(zipped)
    rig.icloud.offline |= {xml_path, zip_path}
    rig.icloud.lands = {zip_path}  # the XML never comes back
    parsed: list[object] = []
    monkeypatch.setattr(raw_archive, "read_body", lambda *a: parsed.append(a) or b"")

    with pytest.raises(RawArchiveOfflineError, match="nothing was parsed"):
        _run(rig, accessions=[ACC, BULK_ACC])

    assert set(rig.icloud.downloads) == {xml_path, zip_path}
    assert parsed == []


def _spy_downloads(rig: Rig, monkeypatch: pytest.MonkeyPatch, then: object = None) -> list[int]:
    """Wrap `download_inputs`: record how many store connections the re-derive held
    open while it ran (an in-process DuckDB write connection would not block a second
    one, so the lock itself is not probed), then run `then(rig)`, a change between
    the passes."""
    real = raw_archive.download_inputs
    held: list[int] = []
    open_now = [0]

    def counted(opener: Callable[..., Any]) -> Callable[..., Any]:
        @contextmanager
        def wrapper(*args: object, **kw: object) -> Iterator[object]:
            with opener(*args, **kw) as conn:
                open_now[0] += 1
                try:
                    yield conn
                finally:
                    open_now[0] -= 1

        return wrapper

    def spy(*args: object, **kw: object) -> object:
        held.append(open_now[0])
        out = real(*args, **kw)  # type: ignore[arg-type]
        if callable(then):
            then(rig)
        return out

    monkeypatch.setattr(raw_rederive, "open_for_write", counted(raw_rederive.open_for_write))
    monkeypatch.setattr(raw_rederive, "open_read_only", counted(raw_rederive.open_read_only))
    monkeypatch.setattr(raw_archive, "download_inputs", spy)
    return held


@pytest.mark.parametrize("write", [False, True])
def test_the_download_holds_no_store_connection(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, write: bool
) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_release()
    digest = _digest(rig)
    held = _spy_downloads(rig, monkeypatch)
    kw: dict[str, object] = {"release": REL, "expect": 1, "expect_digest": digest} if write else {}

    result = _run(rig, **kw)

    assert held == [0]  # one download, no store connection open
    assert len(result.corrections) == 1  # type: ignore[attr-defined]


def test_a_body_evicted_after_the_download_fails_under_the_lock(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_release()
    before, runs, digest = rig.table(), rig.runs(), _digest(rig)
    _spy_downloads(rig, monkeypatch, then=lambda r: r.icloud.offline.add(r.body_path(XML)))

    with pytest.raises(RawArchiveOfflineError, match="not local"):
        _run(rig, release=REL, expect=1, expect_digest=digest)

    assert rig.table() == before and rig.runs() == runs


def test_inputs_that_change_after_the_download_fail_under_the_lock(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_release()
    before, digest = rig.table(), _digest(rig)

    def swap(r: Rig) -> None:  # another writer run, of other bytes, now claims the rows
        r.form4_run(run_id="run-2", body=XML + b"\n", wrong=WRONG_SHARES, store=False)
        with open_for_write(r.settings) as conn:
            conn.execute("DELETE FROM ingestion_runs WHERE run_id = 'run-1'")

    _spy_downloads(rig, monkeypatch, then=swap)
    with pytest.raises(RederiveRefused, match="changed since the download"):
        _run(rig, release=REL, expect=1, expect_digest=digest)
    assert rig.table() == before


def test_a_missing_body_fails_before_any_request(rig: Rig) -> None:
    rig.form4_run()
    rig.body_path(XML).unlink()  # the test removes it; the code never unlinks
    with pytest.raises(RawArchiveMissingError):
        _run(rig)
    assert rig.icloud.downloads == []


def test_a_body_that_no_longer_matches_its_hash_fails(rig: Rig) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    path = rig.body_path(XML)
    path.chmod(0o644)
    path.write_bytes(XML.replace(b"10000", b"20000"))
    with pytest.raises(RawArchiveIntegrityError):
        _run(rig)


def test_the_floor_is_checked_before_the_run(rig: Rig) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.disk(LOW)
    with pytest.raises(RawArchiveFloorError):
        _run(rig)


def test_the_floor_is_checked_again_during_the_run(rig: Rig) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_release()
    before, digest = rig.table(), _digest(rig)
    disk = rig.disk(PLENTY, LOW)  # the preflight passes, the first parse step does not
    with pytest.raises(RawArchiveFloorError):
        _run(rig, release=REL, expect=1, expect_digest=digest)
    assert disk.calls == 2
    assert rig.table() == before


# --- the write: release, count, open window; known_at kept ------------------------


def test_a_correction_keeps_known_at_and_writes_a_repair_run_inside_the_release(
    rig: Rig,
) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_release()

    result = _run(rig, release=REL, expect=1, expect_digest=_digest(rig))

    assert len(result.corrections) == 1  # type: ignore[attr-defined]
    row = next(r for r in rig.table() if r["line_no"] == 0)
    assert row["shares"] == Decimal("10000")
    assert row["known_at"] == ACCEPTED and row["accepted_at"] == ACCEPTED
    assert row["ingested_at"] == NOW
    run = rig.runs()[-1]
    assert run[1:4] == ("edgar", "repair", "repaired")
    assert f"release {REL}" in run[4]
    # The run's own manifest maps the corrected row, the writer's the untouched one,
    # so a second re-derive finds nothing left to correct.
    again = _run(rig)
    assert again.corrections == () and again.unchanged == 2  # type: ignore[attr-defined]
    assert set(again.runs) == {"run-1", run[0]}  # type: ignore[attr-defined]


def test_a_write_needs_the_open_release_and_the_dry_run_count_and_digest(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    before, digest = rig.table(), _digest(rig)
    downloads = _spy_downloads(rig, monkeypatch)
    with pytest.raises(RederiveRefused, match="pass --expect"):
        _run(rig, release=REL)
    with pytest.raises(RederiveRefused, match="pass --expect"):
        _run(rig, release=REL, expect=1)  # the count alone is not enough
    with pytest.raises(RederiveRefused, match="no release is open"):
        _run(rig, release=REL, expect=1, expect_digest=digest)
    assert downloads == []  # a closed release is refused before any download
    rig.open_release()
    with pytest.raises(RederiveRefused, match="--expect is 2"):
        _run(rig, release=REL, expect=2, expect_digest=digest)
    with pytest.raises(RederiveRefused, match="digest"):
        _run(rig, release=REL, expect=1, expect_digest="0" * 12)
    assert rig.table() == before


def test_the_digest_names_which_rows_and_values_not_just_how_many(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def digest(name: str, **kw: object) -> str:
        (tmp_path / name).mkdir()
        rig = Rig(tmp_path / name, monkeypatch)
        rig.form4_run(**kw)  # type: ignore[arg-type]
        result = _run(rig)
        assert len(result.corrections) == 1  # type: ignore[attr-defined]
        assert f"digest {result.digest}" in "\n".join(result.lines())  # type: ignore[attr-defined]
        return result.digest  # type: ignore[attr-defined, no-any-return]

    one = digest("one", wrong=WRONG_SHARES)
    assert len(one) == 12 and digest("again", wrong=WRONG_SHARES) == one
    assert digest("line", wrong=WRONG_SHARES, wrong_line=1) != one  # another row
    assert digest("old", wrong={"shares": Decimal("998")}) != one  # another stored value


def test_expect_digest_without_a_release_is_refused(rig: Rig) -> None:
    rig.form4_run()
    with pytest.raises(RederiveRefused, match="--expect-digest needs --release"):
        _run(rig, expect_digest="0" * 12)


def test_a_never_true_row_an_open_window_read_is_refused(rig: Rig) -> None:
    rig.form4_run(wrong=WRONG_SHARES)
    rig.open_release()
    rig.open_paper_window(datetime(2026, 10, 29, 14, tzinfo=UTC))  # read after known_at
    before = rig.table()

    dry = _run(rig)
    assert len(dry.refused) == 1 and "open paper window" in dry.refused[0]  # type: ignore[attr-defined]
    with pytest.raises(RederiveRefused, match="open paper window"):
        _run(rig, release=REL, expect=1, expect_digest=dry.digest)  # type: ignore[attr-defined]
    assert rig.table() == before


def test_a_row_the_reparse_no_longer_produces_is_refused(rig: Rig) -> None:
    rows = rig.form4_rows(row_source=XML_SOURCE, body=XML)
    rows.append({**rows[1], "line_no": 9})
    rig.form4_run(entry_rows=rows)
    with raw_archive_conn(rig) as conn:
        insert_row(conn, "sec_form4_transactions", rows[-1])
    dry = _run(rig)
    assert dry.refused == (  # type: ignore[attr-defined]
        f"  sec_form4_transactions {[ACC, rows[1]['filer_cik'], 9]}: "
        "the re-parse no longer produces it",
    )


def test_a_reparse_that_adds_a_row_is_refused(rig: Rig) -> None:
    rows = rig.form4_rows(row_source=XML_SOURCE, body=XML)
    rig.form4_run(entry_rows=rows[:1])
    with raw_archive_conn(rig) as conn:
        conn.execute("DELETE FROM sec_form4_transactions WHERE line_no = 1")
    dry = _run(rig)
    assert len(dry.refused) == 1 and "adds rows" in dry.refused[0]  # type: ignore[attr-defined]


def raw_archive_conn(rig: Rig) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
    return open_for_write(rig.settings)
