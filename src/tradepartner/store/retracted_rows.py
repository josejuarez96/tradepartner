"""Retracted rows (ADR 0029, #3281, T389): a repair moves rows out of a fact table
into `retracted_rows` instead of deleting them, and `restore` puts them back.

`retract(conn, table, keys, reason, release, rule)` moves **every revision** of each
logical key out of `prices_daily`, `corporate_actions`, `listings` or `delistings`.
The table then holds exactly what a `DELETE` of those keys would leave, so every
as-of reader (which never reads `retracted_rows`) returns what it returned after a
delete; the side table keeps the whole row (every column, `known_at` included) as
text, with the reason, the release and the rule that judged it.

`restore(conn, ...)` reverses one logical key, or every unrestored row of one
release, with each row's original `known_at`, so a restored row is no later than it
was. It refuses, naming the offender, when:

- the logical key is held in the table by any revision whatever its `known_at`
  (a later ingest may have re-added the key);
- another security id already holds the row's ticker and session: for a bar, a
  different id whose listing active on that session carries the same ticker and
  which has a bar on that session; for a listing row, a different id with a live
  listing of the same ticker starting the same day. (`corporate_actions` and
  `delistings` rows have no ticker-session of their own: only the key check applies.)

The logical keys (`KEY_COLUMNS`) are the tables' natural identities without
`known_at`: a bar is `(security_id, session)`; an action with a `source_action_id`
is `(security_id, source_action_id)` (a re-dated event is one event), else
`(security_id, action_type, ex_date)`; a listing is `(security_id, ticker, exchange,
valid_from)`; a delisting is `(security_id, form, class_title, exchange, filed_at)`.

Both functions run in one transaction (the caller's if one is open). Inside a caller's
transaction DuckDB may refuse to re-insert a key it deleted in the same transaction:
retract and restore in separate ones. No reader under `backtest/`, `research/`,
`universe.py` or the signals may read `retracted_rows` (a source scan in
`tests/lookahead/test_check_only_inputs.py`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import duckdb

from tradepartner.store.db import ensure_tz_aware, utc_now
from tradepartner.store.schema import RETRACTED_SOURCE_TABLES, atomic

#: Each source table's logical key, in the order `retract` takes a key tuple.
KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "prices_daily": ("security_id", "session"),
    "corporate_actions": ("security_id", "source_action_id", "action_type", "ex_date"),
    "listings": ("security_id", "ticker", "exchange", "valid_from"),
    "delistings": ("security_id", "form", "class_title", "exchange", "filed_at"),
}


class RetractionRefused(ValueError):
    """A retraction that cannot be made; nothing was moved."""


class RestoreRefused(ValueError):
    """A restore that cannot be made; nothing was put back."""


def _check_table(table: str) -> None:
    if table not in RETRACTED_SOURCE_TABLES:
        raise ValueError(
            f"cannot retract from {table!r}: only {', '.join(RETRACTED_SOURCE_TABLES)}"
        )


def _key_expr(table: str, alias: str) -> str:
    """SQL for a row's logical key as a `VARCHAR[]`."""
    if table == "corporate_actions":
        a = alias
        # An id-bearing action is one event across re-dated revisions.
        return (
            f"[CAST({a}.security_id AS VARCHAR), {a}.source_action_id, "
            f"CASE WHEN {a}.source_action_id = '' THEN {a}.action_type ELSE '' END, "
            f"CASE WHEN {a}.source_action_id = '' THEN CAST({a}.ex_date AS VARCHAR) ELSE '' END]"
        )
    return "[" + ", ".join(f"CAST({alias}.{c} AS VARCHAR)" for c in KEY_COLUMNS[table]) + "]"


def _match(table: str, row: str, keys: str) -> str:
    """SQL: source row `row` is a revision of the key in `keys`."""
    if table == "corporate_actions":
        return (
            f"{row}.security_id = {keys}.security_id AND "
            f"{row}.source_action_id = {keys}.source_action_id AND "
            f"({keys}.source_action_id <> '' OR "
            f"({row}.action_type = {keys}.action_type AND {row}.ex_date = {keys}.ex_date))"
        )
    return " AND ".join(f"{row}.{c} = {keys}.{c}" for c in KEY_COLUMNS[table])


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[tuple[str, str]]:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [(str(r[1]), str(r[2])) for r in rows]


def _need_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetractionRefused(f"a retraction needs a non-empty {name}")
    return value


def retract(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    keys: Sequence[Sequence[Any]],
    reason: str,
    release: str,
    rule: str,
    *,
    at: datetime | None = None,
) -> int:
    """Move every revision of each logical key in `keys` (tuples in
    `KEY_COLUMNS[table]` order; for an action with a `source_action_id` the last two
    entries are ignored) out of `table` into `retracted_rows`, and return the rows
    moved. A key that matches no row, an unknown table, an empty `keys` or an empty
    `reason`, `release` or `rule` raises `RetractionRefused` before anything moves."""
    _check_table(table)
    _need_text("reason", reason)
    _need_text("release", release)
    _need_text("rule", rule)
    moved_at = ensure_tz_aware(utc_now() if at is None else at, field="at")
    names = KEY_COLUMNS[table]
    unique = list(dict.fromkeys(tuple(k) for k in keys))
    if not unique:
        raise RetractionRefused("a retraction needs at least one key")
    for key in unique:
        if len(key) != len(names):
            raise RetractionRefused(f"a {table} key is {names}, got {key!r}")
    columns = _columns(conn, table)
    names_sql = ", ".join(f"'{c}'" for c, _ in columns)
    values_sql = ", ".join(f"CAST(t.{c} AS VARCHAR)" for c, _ in columns)
    with atomic(conn):
        conn.execute(
            f"CREATE OR REPLACE TEMP TABLE _retract_keys AS "
            f"SELECT {', '.join(names)} FROM {table} LIMIT 0"
        )
        try:
            conn.executemany(
                f"INSERT INTO _retract_keys VALUES ({', '.join('?' for _ in names)})", unique
            )
            missing = conn.execute(
                f"SELECT {', '.join(f'k.{c}' for c in names)} FROM _retract_keys k "
                f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {_match(table, 't', 'k')}) "
                "ORDER BY ALL"
            ).fetchall()
            if missing:
                raise RetractionRefused(
                    f"{len(missing)} {table} key(s) match no row, nothing moved: {missing[:5]!r}"
                )
            [base] = conn.execute(
                "SELECT coalesce(max(retraction_id), 0) FROM retracted_rows"
            ).fetchone() or (0,)
            exists = f"EXISTS (SELECT 1 FROM _retract_keys k WHERE {_match(table, 't', 'k')})"
            conn.execute(
                "INSERT INTO retracted_rows (retraction_id, table_name, row_key, row_values, "
                "reason, release, rule, retracted_at, restored_at) "
                f"SELECT ? + row_number() OVER (ORDER BY {_key_expr(table, 't')}, t.known_at), "
                f"?, {_key_expr(table, 't')}, MAP([{names_sql}], [{values_sql}]), ?, ?, ?, ?, NULL "
                f"FROM {table} t WHERE {exists}",
                [base, table, reason, release, rule, moved_at],
            )
            [moved] = conn.execute(
                "SELECT count(*) FROM retracted_rows WHERE retraction_id > ?", [base]
            ).fetchone() or (0,)
            conn.execute(f"DELETE FROM {table} AS t WHERE {exists}")
        finally:
            conn.execute("DROP TABLE IF EXISTS _retract_keys")
    return int(moved)


def _key_text(conn: duckdb.DuckDBPyConnection, table: str, key: Sequence[Any]) -> list[str]:
    """`key` as the `row_key` text the table's own column types give it."""
    names = KEY_COLUMNS[table]
    conn.execute(
        f"CREATE OR REPLACE TEMP TABLE _restore_key AS SELECT {', '.join(names)} "
        f"FROM {table} LIMIT 0"
    )
    try:
        conn.execute(
            f"INSERT INTO _restore_key VALUES ({', '.join('?' for _ in names)})", list(key)
        )
        [text] = conn.execute(f"SELECT {_key_expr(table, 'k')} FROM _restore_key k").fetchone() or (
            [],
        )
    finally:
        conn.execute("DROP TABLE IF EXISTS _restore_key")
    return [str(v) for v in text]


def _select_ids(
    conn: duckdb.DuckDBPyConnection,
    release: str | None,
    table: str | None,
    key: Sequence[Any] | None,
) -> list[int]:
    if release is None and (table is None or key is None):
        raise RestoreRefused("name a release, or a table and a key, to restore")
    if (table is None) != (key is None):
        raise RestoreRefused("a table and a key go together")
    where = ["restored_at IS NULL"]
    params: list[Any] = []
    if release is not None:
        where.append("release = ?")
        params.append(release)
    if table is not None and key is not None:
        _check_table(table)
        if len(key) != len(KEY_COLUMNS[table]):
            raise RestoreRefused(f"a {table} key is {KEY_COLUMNS[table]}, got {tuple(key)!r}")
        text = _key_text(conn, table, key)
        where += ["table_name = ?", "row_key = ?"]
        params += [table, text]
    rows = conn.execute(
        f"SELECT retraction_id FROM retracted_rows WHERE {' AND '.join(where)} "
        "ORDER BY retraction_id",
        params,
    ).fetchall()
    return [int(r[0]) for r in rows]


def restore(
    conn: duckdb.DuckDBPyConnection,
    *,
    release: str | None = None,
    table: str | None = None,
    key: Sequence[Any] | None = None,
    at: datetime | None = None,
) -> int:
    """Put retracted rows back, each with its original `known_at`, and return the
    rows restored. `release` restores every unrestored row of that release; `table`
    and `key` (as `retract` takes it) restore every unrestored revision of that
    logical key; with both, the key's rows of that release. Refuses (`RestoreRefused`,
    nothing restored) when there is nothing to restore, when a logical key is held in
    its table by any revision, or when another id holds a bar's or listing's ticker
    and session (module docstring); the message names each offender."""
    restored_at = ensure_tz_aware(utc_now() if at is None else at, field="at")
    with atomic(conn):
        ids = _select_ids(conn, release, table, key)
        if not ids:
            raise RestoreRefused("no unrestored retracted row matches")
        conn.execute("CREATE OR REPLACE TEMP TABLE _restore_ids (retraction_id BIGINT)")
        try:
            conn.executemany("INSERT INTO _restore_ids VALUES (?)", [(i,) for i in ids])
            tables = [
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT table_name FROM retracted_rows "
                    "WHERE retraction_id IN (SELECT retraction_id FROM _restore_ids) "
                    "ORDER BY 1"
                ).fetchall()
            ]
            problems: list[str] = []
            for name in tables:
                problems += _held_keys(conn, name)
                problems += _duplicate_revisions(conn, name)
            if problems:
                raise RestoreRefused("; ".join(problems) + "; nothing restored")
            # Listings first, then the rest: the ticker-session checks read the table as
            # it will be (a bar's listing may come back in the same release), and a
            # refusal rolls the whole restore back.
            for name in sorted(tables, key=lambda n: n != "listings"):
                _insert_back(conn, name)
            if "prices_daily" in tables:
                problems += _held_bar_tickers(conn)
            if "listings" in tables:
                problems += _held_listing_tickers(conn)
            if problems:
                raise RestoreRefused("; ".join(problems) + "; nothing restored")
            conn.execute(
                "UPDATE retracted_rows SET restored_at = ? "
                "WHERE retraction_id IN (SELECT retraction_id FROM _restore_ids)",
                [restored_at],
            )
        finally:
            conn.execute("DROP TABLE IF EXISTS _restore_ids")
    return len(ids)


_IDS = "retraction_id IN (SELECT retraction_id FROM _restore_ids)"


def _held_keys(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT r.row_key FROM retracted_rows r "
        f"WHERE r.table_name = ? AND r.{_IDS} AND EXISTS ("
        f"SELECT 1 FROM {table} t WHERE {_key_expr(table, 't')} = r.row_key) ORDER BY 1",
        [table],
    ).fetchall()
    return [f"{table} key {tuple(r[0])!r} is held by a row in the table" for r in rows]


def _duplicate_revisions(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT row_key, row_values['known_at'] FROM retracted_rows "
        f"WHERE table_name = ? AND {_IDS} GROUP BY ALL HAVING count(*) > 1 ORDER BY 1",
        [table],
    ).fetchall()
    return [
        f"{table} key {tuple(r[0])!r} known at {r[1]} is retracted twice in this restore"
        for r in rows
    ]


_ACTIVE = (
    "l.valid_from = (SELECT max(x.valid_from) FROM listings x "
    "WHERE x.security_id = l.security_id AND NOT x.retracted AND x.valid_from <= {day})"
)


def _held_bar_tickers(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Another id with a bar on the session whose listing active that day carries a
    ticker the bar's own id carries."""
    rows = conn.execute(
        f"""
        WITH batch AS (
            SELECT row_values['security_id'] AS sid, CAST(row_values['session'] AS DATE) AS day
            FROM retracted_rows WHERE table_name = 'prices_daily' AND {_IDS}
        ),
        mine AS (
            SELECT DISTINCT b.sid, b.day, l.ticker FROM batch b
            JOIN listings l ON l.security_id = b.sid AND NOT l.retracted
             AND {_ACTIVE.format(day="b.day")}
        )
        SELECT DISTINCT m.sid, m.day, m.ticker, l.security_id
        FROM mine m JOIN listings l ON l.ticker = m.ticker AND l.security_id <> m.sid
         AND NOT l.retracted AND {_ACTIVE.format(day="m.day")}
        WHERE EXISTS (SELECT 1 FROM prices_daily p
                      WHERE p.security_id = l.security_id AND p.session = m.day)
        ORDER BY ALL
        """
    ).fetchall()
    return [
        f"bar {sid} {day}: ticker {ticker} on that session is held by {other}"
        for sid, day, ticker, other in rows
    ]


def _held_listing_tickers(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        f"""
        WITH batch AS (
            SELECT row_values['security_id'] AS sid, row_values['ticker'] AS ticker,
                   CAST(row_values['valid_from'] AS DATE) AS day
            FROM retracted_rows WHERE table_name = 'listings' AND {_IDS}
        )
        SELECT DISTINCT b.sid, b.day, b.ticker, l.security_id FROM batch b
        JOIN listings l ON l.ticker = b.ticker AND l.valid_from = b.day
         AND l.security_id <> b.sid AND NOT l.retracted
        ORDER BY ALL
        """
    ).fetchall()
    return [
        f"listing {sid} {ticker} from {day}: that ticker and session is held by {other}"
        for sid, day, ticker, other in rows
    ]


def _insert_back(conn: duckdb.DuckDBPyConnection, table: str) -> None:
    columns = _columns(conn, table)
    known = {c for c, _ in columns}
    [stored] = conn.execute(
        f"SELECT list(DISTINCT k) FROM (SELECT unnest(map_keys(row_values)) AS k "
        f"FROM retracted_rows WHERE table_name = ? AND {_IDS})",
        [table],
    ).fetchone() or ([],)
    extra = sorted(set(stored or []) - known)
    if extra:
        raise RestoreRefused(f"{table} has no column {extra} that the retracted rows carry")
    present = [(c, t) for c, t in columns if c in set(stored or [])]
    names = ", ".join(c for c, _ in present)
    casts = ", ".join(f"CAST(r.row_values['{c}'] AS {t})" for c, t in present)
    conn.execute(
        f"INSERT INTO {table} ({names}) SELECT {casts} FROM retracted_rows r "
        f"WHERE r.table_name = ? AND r.{_IDS} ORDER BY r.retraction_id",
        [table],
    )
