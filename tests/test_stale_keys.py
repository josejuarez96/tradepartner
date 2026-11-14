"""Reviewed and config entries whose security id left the store (#3387).

Fixture stores only: a temp DuckDB with the schema and a few `securities` rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

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
        found = sk.stale_keys(
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
            sk.stale_keys(
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
        found = sk.stale_keys(conn, accepted_relistings=[], keep_successors=[])
    sources = {f.source for f in found}
    assert sk.DIVIDEND_REVIEWS in sources
    assert sk.SPINOFF_SPLITS in sources
    assert len(found) >= len(dividend_reviews.reviewed_groups())


def test_stale_keys_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    with _conn(path, ["0000000001"]) as conn:
        before = conn.execute("SELECT count(*) FROM securities").fetchone()
        sk.stale_keys(conn, accepted_relistings=["9"], keep_successors=[])
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


def test_a_kept_successor_superseded_by_a_later_id_of_its_cik_is_stale(tmp_path: Path) -> None:
    ids = ["0000000002@2021-08-09", "0000000002@2021-07-01", "0000000003@2020-01-02"]
    with _conn(tmp_path / "s.duckdb", ids) as conn:
        found = sk.stale_keys(
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
