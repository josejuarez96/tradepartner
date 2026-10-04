"""Tests for tradepartner.repair (#819): removing the Alpaca rows the
resolver no longer assigns to their security (a reused ticker's bars on a
delisted security), in one recorded transaction."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.repair import REPAIR, REPAIRED, misattributed, repair_resolution, store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

RUN = datetime(2026, 10, 4, 18, tzinfo=UTC)
FILED = datetime(2021, 5, 21, 20, 30, tzinfo=UTC)
EFFECTIVE = date(2021, 5, 31)
DEAD, LIVE = "0000000001", "0000000002"


def _listing(sid: str, ticker: str, valid_from: date) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NYSE",
        "class_title": "Common Stock",
        "valid_from": valid_from,
        "known_at": datetime(valid_from.year, valid_from.month, valid_from.day, 21, tzinfo=UTC),
        "ingested_at": RUN,
        "source": "edgar",
        "provenance": "filing",
    }


def _bar(
    sid: str,
    session: date,
    *,
    close: float = 10.0,
    known: datetime | None = None,
    source: str = "alpaca_sip",
) -> dict[str, Any]:
    known = known or session_close(session)
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000,
        "known_at": known,
        "ingested_at": RUN,
        "source": source,
        "provenance": "bar",
    }


def _dividend(sid: str, ex_date: date) -> dict[str, Any]:
    known = session_close(previous_session(ex_date))
    return {
        "security_id": sid,
        "action_type": "dividend",
        "ex_date": ex_date,
        "ratio_or_amount": 0.25,
        "announced_at": None,
        "source_action_id": f"id-{sid}-{ex_date}",
        "cancelled": False,
        "known_at": known,
        "ingested_at": RUN,
        "source": "alpaca",
        "provenance": "action",
    }


@pytest.fixture
def store(settings: Settings) -> Settings:
    """DEAD lists GONE from 2019 and is delisted from 2021-05-31; another
    equity's GONE bars and dividend sit on it after that day. LIVE trades."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _listing(DEAD, "GONE", date(2019, 1, 2)),
            _listing(LIVE, "LIVE", date(2019, 1, 2)),
        ):
            insert_row(conn, "listings", row)
        insert_row(
            conn,
            "delistings",
            {
                "security_id": DEAD,
                "form": "25-NSE",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": FILED,
                "effective_on": EFFECTIVE,
                "known_at": FILED,
                "ingested_at": RUN,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for row in (
            _bar(DEAD, date(2021, 5, 27)),
            _bar(DEAD, date(2021, 6, 1), close=25.0),
            _bar(DEAD, date(2021, 6, 1), close=26.0, known=datetime(2021, 6, 3, tzinfo=UTC)),
            _bar(DEAD, date(2021, 6, 2), close=25.5),
            _bar(DEAD, date(2021, 6, 3), source="fixture"),  # another source: never touched
            _bar(LIVE, date(2021, 6, 1)),
        ):
            insert_row(conn, "prices_daily", row)
        insert_row(conn, "corporate_actions", _dividend(DEAD, date(2021, 5, 20)))
        insert_row(conn, "corporate_actions", _dividend(DEAD, date(2021, 6, 15)))
    return settings


def _rows(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(sql).fetchall()


def _bars(settings: Settings) -> list[tuple[Any, ...]]:
    return _rows(
        settings,
        "SELECT security_id, session, close, source FROM prices_daily "
        "ORDER BY security_id, session, known_at",
    )


def test_a_dry_run_counts_and_changes_nothing(store: Settings) -> None:
    before = _bars(store)
    result = repair_resolution(store, clock=lambda: RUN, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (3, 1)
    assert sorted(result.found.bars) == [(DEAD, date(2021, 6, 1)), (DEAD, date(2021, 6, 2))]
    assert (result.bar_keys_checked, result.action_keys_checked) == (4, 2)
    assert result.summary().startswith("would delete 3 bar rows (2 of 4 keys) and 1 action rows")
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_the_repair_deletes_every_revision_of_a_misattributed_row(store: Settings) -> None:
    result = repair_resolution(store, clock=lambda: RUN)
    assert (result.bar_rows, result.action_rows) == (3, 1)
    assert _bars(store) == [
        (DEAD, date(2021, 5, 27), 10.0, "alpaca_sip"),
        (DEAD, date(2021, 6, 3), 10.0, "fixture"),
        (LIVE, date(2021, 6, 1), 10.0, "alpaca_sip"),
    ]
    assert _rows(store, "SELECT security_id, ex_date FROM corporate_actions") == [
        (DEAD, date(2021, 5, 20))
    ]


def test_the_repair_is_recorded_and_never_reads_as_a_fresh_ingest(store: Settings) -> None:
    result = repair_resolution(store, clock=lambda: RUN)
    [(source, status, mode, message)] = _rows(
        store, "SELECT source, status, mode, message FROM ingestion_runs"
    )
    assert (source, status, mode) == ("alpaca", REPAIRED, REPAIR)
    assert status != "ok"  # execution and health read only ok runs as fresh
    assert message == result.summary()
    assert f"on 1 securities: {DEAD}" in message


def test_a_second_repair_deletes_nothing(store: Settings) -> None:
    repair_resolution(store, clock=lambda: RUN)
    again = repair_resolution(store, clock=lambda: RUN)
    assert (again.bar_rows, again.action_rows) == (0, 0)
    assert again.found.securities == ()


def test_the_verdict_is_the_ingest_resolvers(store: Settings) -> None:
    with duckdb.connect(store.store.path, read_only=True) as conn:
        resolver = store_resolver(conn, RUN, store)
    assert resolver.resolve("GONE", date(2021, 5, 28)) == DEAD
    assert resolver.resolve("GONE", EFFECTIVE) is None
    found = misattributed(
        resolver,
        [(DEAD, date(2021, 5, 28), 1), (DEAD, date(2021, 6, 1), 2), (LIVE, date(2021, 6, 1), 1)],
        [(DEAD, date(2021, 6, 1), 1)],  # resolves on 2021-05-28, before the effective day
    )
    assert dict(found.bars) == {(DEAD, date(2021, 6, 1)): 2}
    assert dict(found.actions) == {}


def test_the_command_dry_run_and_repair(store: Settings) -> None:
    app = cli.make_app(settings=lambda: store, clock=lambda: RUN)
    runner = CliRunner()
    dry = runner.invoke(app, ["repair-resolution", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith("would delete 3 bar rows")
    done = runner.invoke(app, ["repair-resolution"])
    assert done.exit_code == 0, done.output
    assert done.output.startswith("deleted 3 bar rows")
    assert len(_bars(store)) == 3


def test_the_command_refuses_a_missing_store(settings: Settings) -> None:
    app = cli.make_app(settings=lambda: settings, clock=lambda: RUN)
    result = CliRunner().invoke(app, ["repair-resolution"])
    assert result.exit_code != 0
