"""Retracted rows (ADR 0029, #3281, T389): the version-49 side table, `retract` and
`restore`.

A retract then restore reproduces every row column for column; every as-of reader
returns what it returns on a store where the rows were deleted; restore refuses a
held logical key and a ticker-session another id holds, naming it; the migration
adds the table and changes no row.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Settings
from tradepartner.health import integrity_checks
from tradepartner.store import registry, retracted_rows, schema
from tradepartner.store.asof import (
    adjusted_prices_as_of,
    dropped_dividends_as_of,
    price_jumps_as_of,
)
from tradepartner.store.db import configure_connection
from tradepartner.universe import universe_as_of

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 11, 13, 0, tzinfo=UTC)
RULE = "resolver: listing end 2020-06-17"


def _fixture_conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, _FIXTURES)
    return conn


def _rows(conn: duckdb.DuckDBPyConnection, table: str) -> list[tuple[Any, ...]]:
    return conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()


def _bits(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Rows with each float as its IEEE bytes, so equality is byte for byte (NaN too)."""
    return [tuple(struct.pack("d", v) if isinstance(v, float) else v for v in row) for row in rows]


def _as_version_48(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version VALUES (48, TIMESTAMPTZ '2026-11-10 00:00:00+00')")


# --- the migration -----------------------------------------------------------------


def test_the_table_is_a_state_table_outside_the_fact_tables() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == schema._PRE_RETRACTED_ROWS_VERSION + 1 == 49
    assert "retracted_rows" not in schema.TABLE_NAMES
    assert schema.RETRACTED_ROWS_TABLE_NAMES == ("retracted_rows",)
    conn = duckdb.connect(":memory:")
    try:
        schema.init_schema(conn)
        columns = [r[1] for r in conn.execute("PRAGMA table_info('retracted_rows')").fetchall()]
        assert columns == [
            "retraction_id",
            "table_name",
            "row_key",
            "row_values",
            "reason",
            "release",
            "rule",
            "retracted_at",
            "restored_at",
        ]
    finally:
        conn.close()


def test_version_49_adds_the_table_to_a_version_48_store_and_changes_no_row() -> None:
    conn = _fixture_conn()
    try:
        conn.execute("DROP TABLE retracted_rows")
        _as_version_48(conn)
        before = {t: _rows(conn, t) for t in schema.TABLE_NAMES if t != "schema_version"}
        [top] = conn.execute("SELECT max(version) FROM schema_version").fetchone() or (0,)
        assert top == schema._PRE_RETRACTED_ROWS_VERSION == 48
        schema.init_schema(conn)
        assert {t: _rows(conn, t) for t in schema.TABLE_NAMES if t != "schema_version"} == before
        assert _rows(conn, "retracted_rows") == []
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone() == (49,)
        schema.init_schema(conn)
        assert conn.execute(
            "SELECT count(*) FROM schema_version WHERE version = 49"
        ).fetchone() == (1,)
    finally:
        conn.close()


def test_a_read_only_version_48_store_opens(tmp_path: Path) -> None:
    path = tmp_path / "v48.duckdb"
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        conn.execute("DROP TABLE retracted_rows")
        _as_version_48(conn)
    finally:
        conn.close()
    with duckdb.connect(str(path), read_only=True) as ro:
        schema.init_schema(ro)
        assert schema.stored_version(ro) == 48


# --- retract then restore ------------------------------------------------------------

_BAR = ("SEC_SPLIT_BETWEEN", date(2019, 3, 4))


def _keys_for_all_tables(conn: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "prices_daily": [("SEC_SPLIT_BETWEEN", date(2019, 3, 4)), ("SEC_SPY", date(2018, 6, 25))],
        # an id-less dividend with two revisions, and an id-bearing re-dated split
        "corporate_actions": [
            ("SEC_DIV_REVISED", "", "dividend", date(2019, 2, 25)),
            ("SEC_SPLIT_REDATED", "fixture-redated-split", "", None),
        ],
        "listings": [("SEC_TRANSFER", "TRNS", "NASDAQ", date(2018, 10, 26))],
        "delistings": [
            tuple(
                conn.execute(
                    "SELECT security_id, form, class_title, exchange, filed_at FROM delistings "
                    "ORDER BY ALL LIMIT 1"
                ).fetchone()
                or ()
            )
        ],
    }


def test_a_retract_then_restore_reproduces_every_row_byte_for_byte() -> None:
    conn = _fixture_conn()
    try:
        # awkward doubles in a bar: the text round trip must keep every bit
        conn.execute(
            "UPDATE prices_daily SET open = 0.1 + 0.2::DOUBLE, high = 1e-310, low = 1.0 / 3, "
            "close = 123456789.123456789 WHERE security_id = ? AND session = ?",
            list(_BAR),
        )
        before = {t: _bits(_rows(conn, t)) for t in schema.RETRACTED_SOURCE_TABLES}
        for table, keys in _keys_for_all_tables(conn).items():
            moved = retracted_rows.retract(conn, table, keys, "why", "rel-1", RULE, at=T0)
            assert moved >= len(keys)
            assert len(_rows(conn, table)) < len(before[table])
        assert conn.execute("SELECT count(*) FROM retracted_rows").fetchone() == (
            sum(len(before[t]) - len(_rows(conn, t)) for t in schema.RETRACTED_SOURCE_TABLES),
        )
        restored = retracted_rows.restore(conn, release="rel-1", at=T1)
        assert restored == conn.execute("SELECT count(*) FROM retracted_rows").fetchone()[0]
        after = {t: _bits(_rows(conn, t)) for t in schema.RETRACTED_SOURCE_TABLES}
        assert after == before  # every column, known_at and ingested_at included
        assert conn.execute(
            "SELECT count(*) FROM retracted_rows WHERE restored_at IS NULL"
        ).fetchone() == (0,)
    finally:
        conn.close()


def test_a_retraction_records_what_judged_it() -> None:
    conn = _fixture_conn()
    try:
        retracted_rows.retract(conn, "prices_daily", [_BAR], "Form 25 tail", "rel-9", RULE, at=T0)
        [row] = conn.execute(
            "SELECT table_name, row_key, reason, release, rule, retracted_at, restored_at, "
            "row_values['close'] IS NOT NULL, row_values['known_at'] IS NOT NULL "
            "FROM retracted_rows"
        ).fetchall()
        assert row == (
            "prices_daily",
            ["SEC_SPLIT_BETWEEN", "2019-03-04"],
            "Form 25 tail",
            "rel-9",
            RULE,
            T0,
            None,
            True,
            True,
        )
    finally:
        conn.close()


def test_a_restored_row_keeps_its_original_known_at_and_nothing_is_stamped_later() -> None:
    conn = _fixture_conn()
    try:
        original = conn.execute(
            "SELECT known_at, ingested_at FROM prices_daily WHERE security_id = ? AND session = ?",
            list(_BAR),
        ).fetchall()
        retracted_rows.retract(conn, "prices_daily", [_BAR], "r", "rel", RULE, at=T0)
        retracted_rows.restore(conn, table="prices_daily", key=_BAR, at=T1)
        assert (
            conn.execute(
                "SELECT known_at, ingested_at FROM prices_daily "
                "WHERE security_id = ? AND session = ?",
                list(_BAR),
            ).fetchall()
            == original
        )
        assert original[0][1] < T1  # the restore came later; the row did not move with it
    finally:
        conn.close()


def test_every_revision_of_a_key_moves_and_comes_back() -> None:
    conn = _fixture_conn()
    try:
        key = ("SEC_DIV_REVISED", "", "dividend", date(2019, 2, 25))
        assert retracted_rows.retract(conn, "corporate_actions", [key], "r", "rel", RULE) == 2
        assert retracted_rows.restore(conn, release="rel", table="corporate_actions", key=key) == 2
        assert conn.execute(
            "SELECT count(*) FROM corporate_actions WHERE security_id = 'SEC_DIV_REVISED'"
        ).fetchone() == (2,)
    finally:
        conn.close()


def test_a_retract_that_names_a_missing_key_or_no_reason_moves_nothing() -> None:
    conn = _fixture_conn()
    try:
        before = _rows(conn, "prices_daily")
        with pytest.raises(retracted_rows.RetractionRefused, match="match no row"):
            retracted_rows.retract(
                conn, "prices_daily", [_BAR, ("SEC_SPY", date(1999, 1, 4))], "r", "rel", RULE
            )
        with pytest.raises(retracted_rows.RetractionRefused, match="reason"):
            retracted_rows.retract(conn, "prices_daily", [_BAR], " ", "rel", RULE)
        with pytest.raises(ValueError, match="only"):
            retracted_rows.retract(conn, "securities", [("x",)], "r", "rel", RULE)
        assert _rows(conn, "prices_daily") == before
        assert _rows(conn, "retracted_rows") == []
    finally:
        conn.close()


# --- every as-of reader equals a deleted-row store's -----------------------------------

_TS = [
    datetime(2018, 12, 17, 21, tzinfo=UTC),
    datetime(2019, 3, 29, 21, tzinfo=UTC),
    datetime(2019, 12, 31, 21, tzinfo=UTC),
    datetime(2020, 6, 30, 21, tzinfo=UTC),
]
_LATE = ("SEC_SPY", "", "dividend", date(2019, 3, 1))


def _seed_late_dividend(conn: duckdb.DuckDBPyConnection) -> None:
    stamp = datetime(2019, 3, 20, 21, tzinfo=UTC)
    conn.execute(
        "INSERT INTO corporate_actions (security_id, action_type, ex_date, ratio_or_amount, "
        "known_at, ingested_at, source, provenance) "
        "VALUES ('SEC_SPY', 'dividend', DATE '2019-03-01', 0.5, ?, ?, 'alpaca', 'action')",
        [stamp, stamp],
    )


def _take_out(conn: duckdb.DuckDBPyConnection, how: str) -> None:
    """Remove the same rows from `conn` by retraction or by delete."""
    bar_keys = [("SEC_SPLIT_BETWEEN", date(2019, 3, d)) for d in (4, 5, 6, 7, 8, 11, 12)]
    action_keys = [
        ("SEC_DIV_REVISED", "", "dividend", date(2019, 2, 25)),
        ("SEC_SPLIT_PLAIN", "", "split", date(2018, 12, 13)),
        _LATE,
    ]
    listing_key = ("SEC_UNCLASSIFIABLE", "UNCX", "NYSE_AMERICAN", date(2019, 3, 1))
    delisting = conn.execute(
        "SELECT security_id, form, class_title, exchange, filed_at FROM delistings "
        "ORDER BY ALL LIMIT 1"
    ).fetchone()
    assert delisting is not None
    plan = [
        ("prices_daily", bar_keys, "security_id = ? AND session = ?"),
        (
            "corporate_actions",
            action_keys,
            "security_id = ? AND source_action_id = ? AND action_type = ? AND ex_date = ?",
        ),
        (
            "listings",
            [listing_key],
            "security_id = ? AND ticker = ? AND exchange = ? AND valid_from = ?",
        ),
        (
            "delistings",
            [tuple(delisting)],
            "security_id = ? AND form = ? AND class_title = ? AND exchange = ? AND filed_at = ?",
        ),
    ]
    for table, keys, where in plan:
        if how == "retract":
            retracted_rows.retract(conn, table, keys, "fixture", "rel", RULE)
        else:
            for key in keys:
                conn.execute(f"DELETE FROM {table} WHERE {where}", list(key))


@contextmanager
def _handle_provider(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> Iterator[StoreProvider]:
    @contextmanager
    def connect() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-retracted",
        family="momentum",
        title="retracted rows",
        doc_path="docs/hypotheses/h-retracted.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": 15.0},
        in_sample_start=date(2018, 1, 2),
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    handle = registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=date(2018, 3, 1),
        end_session=date(2020, 6, 30),
        data_cutoff=datetime(2020, 6, 30, 20, tzinfo=UTC),
        synthetic=True,
        run_by="test",
        settings=settings,
    )
    with StoreProvider(connect, handle, settings) as provider:
        yield provider


def _readers(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> dict[str, Callable[[datetime], Any]]:
    return {
        "adjusted_prices_as_of": lambda t: adjusted_prices_as_of(
            conn, t, include_dividends=True, settings=settings
        ),
        "universe.universe_as_of": lambda t: (lambda u: (u.members, u.exclusions))(
            universe_as_of(conn, t, settings)
        ),
        "asof.price_jumps_as_of": lambda t: price_jumps_as_of(conn, t, settings=settings),
        "asof.dropped_dividends_as_of": lambda t: dropped_dividends_as_of(
            conn, t, settings=settings
        ),
        "health.integrity_checks": lambda t: [
            (c.rule, c.violations) for c in integrity_checks(conn, t, settings)
        ],
    }


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, pl.DataFrame):
        return bool(a.equals(b))
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


def test_every_as_of_reader_returns_what_a_deleted_row_store_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    settings = Settings(_env_file=None)
    kept, retracted, deleted = _fixture_conn(), _fixture_conn(), _fixture_conn()
    try:
        for conn in (kept, retracted, deleted):
            _seed_late_dividend(conn)
        _take_out(retracted, "retract")
        _take_out(deleted, "delete")
        for table in schema.RETRACTED_SOURCE_TABLES:
            assert _rows(retracted, table) == _rows(deleted, table)
        differing = set()
        for name, reader in _readers(retracted, settings).items():
            other, untouched = _readers(deleted, settings)[name], _readers(kept, settings)[name]
            for t in _TS:
                assert _same(reader(t), other(t)), (name, t)
                if not _same(reader(t), untouched(t)):
                    differing.add(name)
        # the comparison has teeth: the rows were visible to the main readers before
        assert {"adjusted_prices_as_of", "universe.universe_as_of"} <= differing
        t_prev, t = datetime(2019, 3, 10, 21, tzinfo=UTC), datetime(2019, 4, 1, 21, tzinfo=UTC)
        with (
            _handle_provider(kept, settings) as p_kept,
            _handle_provider(retracted, settings) as p_ret,
            _handle_provider(deleted, settings) as p_del,
        ):
            ids = ["SEC_SPY", "SEC_DIV_REVISED"]
            late_kept = p_kept.late_dividends(t_prev, t, ids)
            assert late_kept["security_id"].to_list() == ["SEC_SPY"]
            late = p_ret.late_dividends(t_prev, t, ids)
            assert late.is_empty()
            assert late.equals(p_del.late_dividends(t_prev, t, ids))
    finally:
        for conn in (kept, retracted, deleted):
            conn.close()


# --- restore refusals ------------------------------------------------------------------


def test_restore_refuses_a_key_held_by_a_later_revision_with_a_different_known_at() -> None:
    conn = _fixture_conn()
    try:
        retracted_rows.retract(conn, "prices_daily", [_BAR], "r", "rel", RULE, at=T0)
        later = datetime(2026, 11, 11, 11, tzinfo=UTC)
        conn.execute(
            "INSERT INTO prices_daily VALUES (?, ?, 1, 2, 0.5, 1.5, 10, ?, ?, 'alpaca', 'bar')",
            [*_BAR, later, later],
        )
        before = _rows(conn, "prices_daily")
        with pytest.raises(retracted_rows.RestoreRefused, match=r"SEC_SPLIT_BETWEEN.*2019-03-04"):
            retracted_rows.restore(conn, release="rel", at=T1)
        assert _rows(conn, "prices_daily") == before
        assert conn.execute(
            "SELECT count(*) FROM retracted_rows WHERE restored_at IS NULL"
        ).fetchone() == (1,)
    finally:
        conn.close()


def test_restore_refuses_one_held_key_and_restores_none_of_the_release() -> None:
    conn = _fixture_conn()
    try:
        retracted_rows.retract(
            conn, "prices_daily", [_BAR, ("SEC_SPY", date(2018, 6, 25))], "r", "rel", RULE
        )
        later = datetime(2026, 11, 11, 11, tzinfo=UTC)
        conn.execute(
            "INSERT INTO prices_daily VALUES ('SEC_SPY', DATE '2018-06-25', 1, 2, 0.5, 1.5, 10, "
            "?, ?, 'alpaca', 'bar')",
            [later, later],
        )
        with pytest.raises(retracted_rows.RestoreRefused, match="SEC_SPY"):
            retracted_rows.restore(conn, release="rel")
        assert conn.execute(
            "SELECT count(*) FROM prices_daily WHERE security_id = 'SEC_SPLIT_BETWEEN' "
            "AND session = DATE '2019-03-04'"
        ).fetchone() == (0,)
    finally:
        conn.close()


def _two_ids_one_ticker() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    stamp = datetime(2020, 1, 2, 21, tzinfo=UTC)
    for sid, ticker, start in (("A", "TICK", "2020-01-02"), ("B", "OTHER", "2019-01-02")):
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, class_title, valid_from, "
            "known_at, ingested_at, source, provenance) "
            "VALUES (?, ?, 'NYSE', 'Common Stock', ?::DATE, ?, ?, 'edgar', 'filing')",
            [sid, ticker, start, stamp, stamp],
        )
    for sid in ("A", "B"):
        conn.execute(
            "INSERT INTO prices_daily VALUES (?, DATE '2020-01-06', 1, 2, 0.5, 1.5, 10, ?, ?, "
            "'alpaca', 'bar')",
            [sid, stamp, stamp],
        )
    return conn


def test_restore_refuses_a_session_whose_ticker_another_id_now_holds_and_names_it() -> None:
    conn = _two_ids_one_ticker()
    try:
        key = ("A", date(2020, 1, 6))
        retracted_rows.retract(conn, "prices_daily", [key], "r", "rel", RULE)
        stamp = datetime(2020, 1, 3, 21, tzinfo=UTC)
        # B's listing is re-pointed to A's ticker from before the session
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, class_title, valid_from, "
            "known_at, ingested_at, source, provenance) "
            "VALUES ('B', 'TICK', 'NYSE', 'Common Stock', DATE '2020-01-03', ?, ?, 'edgar', "
            "'filing')",
            [stamp, stamp],
        )
        with pytest.raises(retracted_rows.RestoreRefused) as caught:
            retracted_rows.restore(conn, release="rel")
        message = str(caught.value)
        assert "A" in message and "TICK" in message and "B" in message and "2020-01-06" in message
        assert conn.execute(
            "SELECT count(*) FROM prices_daily WHERE security_id = 'A'"
        ).fetchone() == (0,)
    finally:
        conn.close()


def test_restore_puts_the_bar_back_when_no_other_id_holds_the_ticker() -> None:
    conn = _two_ids_one_ticker()
    try:
        key = ("A", date(2020, 1, 6))
        retracted_rows.retract(conn, "prices_daily", [key], "r", "rel", RULE)
        assert retracted_rows.restore(conn, release="rel") == 1
        assert conn.execute(
            "SELECT count(*) FROM prices_daily WHERE security_id = 'A'"
        ).fetchone() == (1,)
    finally:
        conn.close()


def test_restore_refuses_a_listing_whose_ticker_and_day_another_id_holds() -> None:
    conn = _two_ids_one_ticker()
    try:
        key = ("A", "TICK", "NYSE", date(2020, 1, 2))
        retracted_rows.retract(conn, "listings", [key], "r", "rel", RULE)
        stamp = datetime(2020, 1, 3, 21, tzinfo=UTC)
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, class_title, valid_from, "
            "known_at, ingested_at, source, provenance) "
            "VALUES ('B', 'TICK', 'NYSE', 'Common Stock', DATE '2020-01-02', ?, ?, 'edgar', "
            "'filing')",
            [stamp, stamp],
        )
        with pytest.raises(retracted_rows.RestoreRefused, match="B"):
            retracted_rows.restore(conn, table="listings", key=key)
    finally:
        conn.close()


def test_restore_with_nothing_to_restore_refuses() -> None:
    conn = _fixture_conn()
    try:
        with pytest.raises(retracted_rows.RestoreRefused, match="no unrestored"):
            retracted_rows.restore(conn, release="nope")
        with pytest.raises(retracted_rows.RestoreRefused, match="name a release"):
            retracted_rows.restore(conn)
    finally:
        conn.close()


def test_a_release_restoring_a_bar_and_its_listing_together_still_refuses_a_held_ticker() -> None:
    conn = _two_ids_one_ticker()
    try:
        retracted_rows.retract(conn, "prices_daily", [("A", date(2020, 1, 6))], "r", "rel", RULE)
        retracted_rows.retract(
            conn, "listings", [("A", "TICK", "NYSE", date(2020, 1, 2))], "r", "rel", RULE
        )
        stamp = datetime(2020, 1, 3, 21, tzinfo=UTC)
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, class_title, valid_from, "
            "known_at, ingested_at, source, provenance) "
            "VALUES ('B', 'TICK', 'NYSE', 'Common Stock', DATE '2020-01-03', ?, ?, 'edgar', "
            "'filing')",
            [stamp, stamp],
        )
        with pytest.raises(retracted_rows.RestoreRefused, match="held by B"):
            retracted_rows.restore(conn, release="rel")
        assert conn.execute(
            "SELECT count(*) FROM prices_daily WHERE security_id = 'A'"
        ).fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM listings WHERE security_id = 'A'").fetchone() == (
            0,
        )
    finally:
        conn.close()
