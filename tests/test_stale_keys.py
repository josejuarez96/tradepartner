"""Reviewed and config entries whose security id left the store (#3387).

Fixture stores only: a temp DuckDB with the schema and a few `securities` rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.store import dividend_reviews, listing_reviews, schema, spinoff_splits
from tradepartner.store import stale_keys as sk
from tradepartner.store.db import configure_connection, insert_row

T0 = datetime(2026, 1, 5, 12, tzinfo=UTC)
T1 = datetime(2026, 11, 1, 12, tzinfo=UTC)
READING = datetime(2026, 11, 15, 12, tzinfo=UTC)


def _stale_keys(conn: duckdb.DuckDBPyConnection, **kwargs: Any) -> tuple[sk.StaleKey, ...]:
    return sk.stale_keys(conn, settings=Settings(_env_file=None), reading=READING, **kwargs)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _security(sid: str, at: datetime = T0, *, retracted: bool | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "security_id": sid,
        "cik": sid[:10],
        "name": sid,
        "benchmark": False,
        "known_at": at,
        "ingested_at": at,
        "source": "edgar",
        "provenance": "filing",
        "snapshot": None,
        "snapshot_static": None,
        "filing": None,
    }
    if retracted is not None:
        row["retracted"] = retracted
    return row


@contextmanager
def _conn(path: Path, ids: list[str]) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        for sid in ids:
            insert_row(conn, "securities", _fix(conn, _security(sid)))
        yield conn
    finally:
        conn.close()


def _fix(conn: duckdb.DuckDBPyConnection, row: dict[str, object]) -> dict[str, object]:
    cols = {
        c
        for (c,) in conn.execute(
            "SELECT column_name FROM duckdb_columns() WHERE table_name='securities'"
        ).fetchall()
    }
    return {k: v for k, v in row.items() if k in cols}


def _dividend(sid: str) -> dividend_reviews.ReviewedGroup:
    return dividend_reviews.ReviewedGroup(
        security_id=sid,
        ex_date=date(2024, 3, 4),
        amount=0.5,
        verdict="distinct",
        retract_source_action_id="",
        ticker="APA",
        evidence="x",
    )


def _spinoff(sid: str) -> spinoff_splits.SpinoffSplit:
    return spinoff_splits.SpinoffSplit(
        security_id=sid,
        source_action_id="a-1",
        ex_date=date(2020, 4, 1),
        ratio=0.25,
        ticker="HWM",
        child="Arconic",
        evidence="x",
    )


def _listing(sid: str) -> listing_reviews.ReviewedListing:
    return listing_reviews.ReviewedListing(
        security_id=sid,
        ticker="DDS",
        exchange="TXSE",
        class_title=None,
        valid_from=date(2026, 9, 30),
        known_at=T0,
        accession="0002054847-26-000023",
        reason="x",
        issue="#3293",
    )


def test_every_source_reports_its_missing_id_and_only_that(tmp_path: Path) -> None:
    with _conn(tmp_path / "s.duckdb", ["0000000001", "0000000002@2021-08-09"]) as conn:
        found = _stale_keys(
            conn,
            accepted_relistings=["0000000001", "0000000003"],
            keep_successors=["0000000002@2021-08-09", "0000000002@2021-08-10"],
            dividends=[_dividend("0000000001"), _dividend("0000000004")],
            listings=[_listing("0000000005")],
            spinoffs=[_spinoff("0000000006"), _spinoff("0000000001")],
        )
    assert [(f.source, f.security_id) for f in found] == [
        (sk.DIVIDEND_REVIEWS, "0000000004"),
        (sk.LISTING_REVIEWS, "0000000005"),
        (sk.SPINOFF_SPLITS, "0000000006"),
        (sk.ACCEPTED_RELISTINGS, "0000000003"),
        (sk.KEEP_SUCCESSORS, "0000000002@2021-08-10"),
    ]
    assert found[0].line == "dividend_reviews.csv: 0000000004 (APA ex 2024-03-04)"


def test_nothing_stale_is_empty(tmp_path: Path) -> None:
    with _conn(tmp_path / "s.duckdb", ["0000000001"]) as conn:
        assert (
            _stale_keys(
                conn,
                accepted_relistings=["0000000001"],
                keep_successors=[],
                dividends=[_dividend("0000000001")],
                listings=[],
                spinoffs=[],
            )
            == ()
        )


def test_a_retracted_id_is_stale_and_a_rederived_one_is_live(tmp_path: Path) -> None:
    with _conn(tmp_path / "s.duckdb", ["0000000001", "0000000002"]) as conn:
        for sid in ("0000000001", "0000000002"):
            insert_row(conn, "securities", _fix(conn, _security(sid, T1, retracted=True)))
        later = datetime(2026, 11, 5, tzinfo=UTC)
        insert_row(conn, "securities", _fix(conn, _security("0000000002", later, retracted=False)))
        assert sk.live_security_ids(conn) == {"0000000002"}


def test_the_packaged_files_are_read_by_default(tmp_path: Path) -> None:
    with _conn(tmp_path / "s.duckdb", []) as conn:
        found = _stale_keys(conn, accepted_relistings=[], keep_successors=[])
    sources = {f.source for f in found}
    assert sk.DIVIDEND_REVIEWS in sources
    assert sk.SPINOFF_SPLITS in sources
    assert len(found) >= len(dividend_reviews.reviewed_groups())


def test_stale_keys_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    with _conn(path, ["0000000001"]) as conn:
        before = conn.execute("SELECT count(*) FROM securities").fetchone()
        _stale_keys(conn, accepted_relistings=["9"], keep_successors=[])
        assert conn.execute("SELECT count(*) FROM securities").fetchone() == before


def _settings(path: Path, **extra: object) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)}, **extra)  # type: ignore[arg-type]


def test_cli_exits_1_and_lists_a_stale_config_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "s.duckdb"
    with _conn(path, ["0000000001"]):
        pass
    s = _settings(
        path,
        alpaca={"accepted_relistings": ["0000000099"]},
        master={"keep_successors": ["0000000001@2021-08-09"]},
    )
    monkeypatch.setattr(dividend_reviews, "reviewed_groups", lambda: ())
    monkeypatch.setattr(listing_reviews, "reviewed_listings", lambda: ())
    monkeypatch.setattr(spinoff_splits, "spinoff_splits", lambda: ())
    app = cli.make_app(settings=lambda: s)
    result = CliRunner().invoke(app, ["stale-keys"])
    assert result.exit_code == 1, result.output
    assert "alpaca.accepted_relistings: 0000000099" in result.output
    assert "master.keep_successors: 0000000001@2021-08-09" in result.output
    assert "2 stale" in result.output
    assert "never drop a keep entry without the owner" in result.output


def test_cli_exits_0_when_nothing_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "s.duckdb"
    with _conn(path, ["0000000001"]):
        pass
    monkeypatch.setattr(dividend_reviews, "reviewed_groups", lambda: ())
    monkeypatch.setattr(listing_reviews, "reviewed_listings", lambda: ())
    monkeypatch.setattr(spinoff_splits, "spinoff_splits", lambda: ())
    app = cli.make_app(settings=lambda: _settings(path))
    result = CliRunner().invoke(app, ["stale-keys"])
    assert result.exit_code == 0, result.output
    assert "no stale keys" in result.output


def _stored_listing(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    start: date,
    *,
    ticker: str = "ABC",
    class_title: str = "Common Stock",
    at: datetime = T0,
) -> None:
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": ticker,
            "exchange": "NYSE",
            "class_title": class_title,
            "valid_from": start,
            "known_at": at,
            "ingested_at": at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _stored_delisting(
    conn: duckdb.DuckDBPyConnection, sid: str, end: date, *, class_title: str = "Common Stock"
) -> None:
    insert_row(
        conn,
        "delistings",
        {
            "security_id": sid,
            "form": "25",
            "class_title": class_title,
            "exchange": "NYSE",
            "filed_at": T0,
            "effective_on": end,
            "known_at": T0,
            "ingested_at": T0,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _stored_bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 100,
            "known_at": T0,
            "ingested_at": T0,
            "source": "alpaca",
            "provenance": "bar",
        },
    )


def test_two_relistings_do_not_supersede_the_older_kept_successor(tmp_path: Path) -> None:
    older = "0000000002@2021-08-09"
    newer = "0000000002@2024-03-04"
    with _conn(tmp_path / "s.duckdb", [older, newer]) as conn:
        _stored_listing(conn, older, date(2021, 8, 9))
        _stored_delisting(conn, older, date(2022, 1, 1))
        _stored_listing(conn, newer, date(2024, 3, 4))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[older],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_two_relistings_without_form_25_use_the_older_ids_last_bar(tmp_path: Path) -> None:
    older = "0000000002@2021-08-09"
    newer = "0000000002@2024-03-04"
    with _conn(tmp_path / "s.duckdb", [older, newer]) as conn:
        _stored_listing(conn, older, date(2021, 8, 9))
        _stored_listing(conn, newer, date(2024, 3, 4))
        _stored_bar(conn, older, date(2022, 1, 3))
        _stored_bar(conn, newer, date(2024, 3, 4))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[older],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_a_live_keep_without_listing_evidence_is_not_called_superseded(tmp_path: Path) -> None:
    older = "0000000002@2021-08-09"
    newer = "0000000002@2024-03-04"
    with _conn(tmp_path / "s.duckdb", [older, newer]) as conn:
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[older],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_a_live_keep_with_no_bars_and_no_form_25_has_no_replacement_evidence(
    tmp_path: Path,
) -> None:
    older = "0000000002@2021-08-09"
    newer = "0000000002@2024-03-04"
    with _conn(tmp_path / "s.duckdb", [older, newer]) as conn:
        _stored_listing(conn, older, date(2021, 8, 9))
        _stored_listing(conn, newer, date(2024, 3, 4))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[older],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_a_kept_successor_replaced_for_the_same_span_is_superseded(tmp_path: Path) -> None:
    ids = ["0000000002@2021-08-09", "0000000002@2021-07-01", "0000000003@2020-01-02"]
    with _conn(tmp_path / "s.duckdb", ids) as conn:
        _stored_listing(conn, ids[0], date(2021, 8, 9))
        _stored_listing(conn, ids[1], date(2021, 7, 1))
        _stored_bar(conn, ids[0], date(2021, 8, 9))
        _stored_bar(conn, ids[1], date(2021, 8, 9))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=["0000000002@2021-08-09", "0000000003@2020-01-02"],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert [(f.source, f.security_id) for f in found] == [
        (sk.KEEP_SUCCESSORS, "0000000002@2021-08-09")
    ]
    assert "superseded" in found[0].detail


def test_a_replacement_without_bars_has_no_overlap_evidence(tmp_path: Path) -> None:
    old = "0000000002@2021-08-09"
    replacement = "0000000002@2021-07-01"
    with _conn(tmp_path / "s.duckdb", [old, replacement]) as conn:
        _stored_listing(conn, old, date(2021, 8, 9))
        _stored_listing(conn, replacement, date(2021, 7, 1))
        _stored_bar(conn, old, date(2021, 8, 9))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[old],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_an_emptied_kept_id_is_flagged_when_earlier_replacement_has_its_bars(
    tmp_path: Path,
) -> None:
    old = "0000000002@2021-08-09"
    replacement = "0000000002@2021-07-01"
    with _conn(tmp_path / "s.duckdb", [old, replacement]) as conn:
        _stored_listing(conn, old, date(2021, 8, 9))
        _stored_listing(conn, replacement, date(2021, 7, 1))
        _stored_bar(conn, replacement, date(2021, 8, 9))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[old],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert len(found) == 1
    assert "superseded" in found[0].detail


def test_a_barless_candidate_starting_inside_the_kept_span_is_flagged(tmp_path: Path) -> None:
    kept = "0000000002@2021-07-01"
    candidate = "0000000002@2021-08-09"
    with _conn(tmp_path / "s.duckdb", [kept, candidate]) as conn:
        _stored_listing(conn, kept, date(2021, 7, 1))
        _stored_listing(conn, candidate, date(2021, 8, 9))
        _stored_bar(conn, kept, date(2021, 9, 1))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[kept],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert len(found) == 1
    assert "superseded" in found[0].detail


def test_an_older_candidate_without_bars_does_not_supersede_a_newer_keep(tmp_path: Path) -> None:
    older = "0000000002@2021-08-09"
    newer = "0000000002@2024-03-04"
    with _conn(tmp_path / "s.duckdb", [older, newer]) as conn:
        _stored_listing(conn, older, date(2021, 8, 9))
        _stored_listing(conn, newer, date(2024, 3, 4))
        _stored_bar(conn, newer, date(2024, 3, 4))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[newer],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_rename_then_relist_does_not_keep_the_first_ticker_span_open(tmp_path: Path) -> None:
    older = "0000000002@2021-08-09"
    newer = "0000000002@2024-03-04"
    with _conn(tmp_path / "s.duckdb", [older, newer]) as conn:
        _stored_listing(conn, older, date(2021, 8, 9))
        _stored_listing(conn, older, date(2022, 1, 3), ticker="XYZ")
        _stored_delisting(conn, older, date(2023, 1, 3))
        _stored_listing(conn, newer, date(2024, 3, 4))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[older],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert found == ()


def test_a_renamed_replacement_of_the_same_class_and_span_is_superseded(tmp_path: Path) -> None:
    old = "0000000002@2021-08-09"
    replacement = "0000000002@2021-07-01"
    with _conn(tmp_path / "s.duckdb", [old, replacement]) as conn:
        _stored_listing(conn, old, date(2021, 8, 9))
        _stored_listing(conn, replacement, date(2021, 7, 1), ticker="XYZ")
        _stored_bar(conn, old, date(2021, 8, 9))
        _stored_bar(conn, replacement, date(2021, 8, 9))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[old],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert len(found) == 1
    assert "superseded" in found[0].detail


def test_a_warrant_form_25_does_not_end_the_replacement_span(tmp_path: Path) -> None:
    old = "0000000002@2021-08-09"
    replacement = "0000000002@2021-07-01"
    with _conn(tmp_path / "s.duckdb", [old, replacement]) as conn:
        _stored_listing(conn, old, date(2021, 8, 9))
        _stored_listing(conn, replacement, date(2021, 7, 1))
        _stored_listing(conn, replacement, date(2021, 7, 2), ticker="ABCW", class_title="Warrants")
        _stored_delisting(conn, replacement, date(2021, 8, 1), class_title="Warrants")
        _stored_bar(conn, old, date(2021, 8, 9))
        _stored_bar(conn, replacement, date(2021, 8, 9))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[old],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert len(found) == 1
    assert "superseded" in found[0].detail


def test_a_second_live_listing_row_does_not_hide_the_replaced_span(tmp_path: Path) -> None:
    old = "0000000002@2021-08-09"
    replacement = "0000000002@2021-07-01"
    with _conn(tmp_path / "s.duckdb", [old, replacement]) as conn:
        _stored_listing(conn, old, date(2021, 8, 9))
        _stored_listing(conn, old, date(2024, 3, 4), at=T1)
        _stored_listing(conn, replacement, date(2021, 7, 1))
        _stored_bar(conn, old, date(2021, 8, 9))
        _stored_bar(conn, replacement, date(2021, 8, 9))
        found = _stale_keys(
            conn,
            accepted_relistings=[],
            keep_successors=[old],
            dividends=[],
            listings=[],
            spinoffs=[],
        )
    assert len(found) == 1
    assert "superseded" in found[0].detail
