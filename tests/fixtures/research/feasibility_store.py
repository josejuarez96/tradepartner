"""Synthetic, file-backed store for the feasibility command (T453, #3742; used by T454, T455).

`build_feasibility_store(path)` builds a small DuckDB file, closes it and returns a
`FeasibilityStore` that names every planted case. It is deterministic (no clock, no
randomness: two builds hold the same rows) and fast (prices go in with `executemany`).

The fixture designs in `feasibility/` read it with window 2018-01-02 to 2019-03-29, a
hold of 20 sessions (10 for `form25`), a 20-session lookback and edges $200M and $50M.

Issuers (`security_id`, `cik`), each with the band its median `close * volume` falls in
over any 20 sessions of its life:

| issuer | band | bars                                   | why it is here                       |
|--------|------|----------------------------------------|--------------------------------------|
| AAA    | top  | 2018-01-02 to 2019-04-05               | two events; a same-date cluster      |
| BBB    | mid  | 2018-01-02 to 2019-04-05               | weekend event; hold past window end  |
| CCC    | low  | 2018-01-02 to 2019-04-05               | holiday event                        |
| DDD    | mid  | 2018-01-02 to 2018-09-14 (delists)     | event whose issuer delists in hold   |
| EEE    | top  | none                                   | "no bar" issuer, delisted, no bars   |
| FFF    | mid  | 2018-01-02 to 2018-11-09 (delists)     | delisted with bars, no event         |
| GGG    | low  | 2018-01-02 to 2019-04-05               | delists after the window             |

Closes are never constant, and every issuer's close-to-close change differs from every
other's, so a mutant reader that read a close-to-close change would show in the block.
The planted prices are deliberately not at a round number: `close * volume` varies by a
few percent around each issuer's level, enough to move a median but never across a band
edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from tradepartner.calendar import all_sessions, session_close
from tradepartner.research.feasibility_design import SOURCES
from tradepartner.store import registry, schema
from tradepartner.store.db import configure_connection, insert_row

BOUNDARY = date(2019, 12, 31)
"""The development boundary written into the store."""
WINDOW_START = date(2018, 1, 2)
WINDOW_END = date(2019, 3, 29)
"""The fixture designs' window: its last session is a Friday."""
LAST_BAR_SESSION = date(2019, 4, 5)
"""Bars run past the window's end so a reader that over-reads would show."""

DESIGN_DIR = Path(__file__).parent / "feasibility"
SOURCE_TABLES: dict[str, str] = {name: spec.table for name, spec in SOURCES.items()}

# issuer -> (cik, level close, level volume); LAST_BAR holds each issuer's last bar session.
ISSUERS: dict[str, tuple[str, float, int]] = {
    "AAA": ("0000001", 100.0, 3_000_000),
    "BBB": ("0000002", 50.0, 2_000_000),
    "CCC": ("0000003", 10.0, 1_000_000),
    "DDD": ("0000004", 60.0, 2_000_000),
    "EEE": ("0000005", 100.0, 3_000_000),
    "FFF": ("0000006", 40.0, 1_500_000),
    "GGG": ("0000007", 20.0, 1_500_000),
}
LAST_BAR: dict[str, date | None] = {
    "AAA": None,
    "BBB": None,
    "CCC": None,
    "DDD": date(2018, 9, 14),
    "EEE": date(1900, 1, 1),  # no bars at all
    "FFF": date(2018, 11, 9),
    "GGG": None,
}
CIK = {name: cik for name, (cik, _, _) in ISSUERS.items()}

# (issuer, form25 filed_at, effective_on): DDD and FFF end inside the window; EEE ends inside
# it with no bars; GGG ends after it.
DELISTINGS: tuple[tuple[str, datetime, date], ...] = (
    ("DDD", datetime(2018, 9, 7, 13, tzinfo=UTC), date(2018, 9, 17)),
    ("EEE", datetime(2018, 10, 1, 13, tzinfo=UTC), date(2018, 10, 11)),
    ("FFF", datetime(2018, 11, 1, 13, tzinfo=UTC), date(2018, 11, 12)),
    ("GGG", datetime(2019, 5, 1, 13, tzinfo=UTC), date(2019, 5, 13)),
)

# (issuer, accepted_at UTC, transaction_code, is_officer, filer, what it is for)
FORM4_EVENTS: tuple[tuple[str, datetime, str, bool, str, str], ...] = (
    (
        "AAA",
        datetime(2018, 1, 10, 15, tzinfo=UTC),
        "P",
        True,
        "F1",
        "entry before a full lookback: no bar",
    ),
    (
        "AAA",
        datetime(2018, 3, 12, 15, tzinfo=UTC),
        "P",
        True,
        "F1",
        "same-date cluster of three issuers",
    ),
    (
        "AAA",
        datetime(2018, 3, 12, 15, tzinfo=UTC),
        "P",
        True,
        "F2",
        "second filer, same issuer and session: one event",
    ),
    (
        "BBB",
        datetime(2018, 3, 12, 15, tzinfo=UTC),
        "P",
        True,
        "F3",
        "same-date cluster of three issuers",
    ),
    (
        "CCC",
        datetime(2018, 3, 12, 15, tzinfo=UTC),
        "P",
        True,
        "F4",
        "same-date cluster of three issuers",
    ),
    (
        "AAA",
        datetime(2018, 3, 19, 15, tzinfo=UTC),
        "P",
        True,
        "F1",
        "one issuer, two events, overlapping holds",
    ),
    ("EEE", datetime(2018, 4, 9, 15, tzinfo=UTC), "P", True, "F5", "issuer with no bar at all"),
    (
        "AAA",
        datetime(2018, 4, 16, 15, tzinfo=UTC),
        "S",
        True,
        "F1",
        "filtered out: transaction_code",
    ),
    ("BBB", datetime(2018, 4, 17, 15, tzinfo=UTC), "P", False, "F3", "filtered out: is_officer"),
    (
        "BBB",
        datetime(2018, 5, 12, 15, tzinfo=UTC),
        "P",
        True,
        "F3",
        "accepted on a Saturday: entry the Monday",
    ),
    (
        "CCC",
        datetime(2018, 7, 3, 20, tzinfo=UTC),
        "P",
        True,
        "F4",
        "before the 4 July holiday: entry 5 July",
    ),
    (
        "DDD",
        datetime(2018, 8, 20, 15, tzinfo=UTC),
        "P",
        True,
        "F6",
        "issuer delists inside the hold",
    ),
    ("AAA", datetime(2019, 2, 4, 15, tzinfo=UTC), "P", True, "F1", "second calendar year"),
    (
        "BBB",
        datetime(2019, 3, 27, 15, tzinfo=UTC),
        "P",
        True,
        "F3",
        "hold runs past the window's end",
    ),
    (
        "AAA",
        datetime(2019, 4, 2, 15, tzinfo=UTC),
        "P",
        True,
        "F1",
        "known after the window's last close",
    ),
    (
        "BBB",
        datetime(2020, 2, 3, 15, tzinfo=UTC),
        "P",
        True,
        "F3",
        "known after the development boundary",
    ),
)


@dataclass(frozen=True)
class FeasibilityStore:
    """The built file and the constants a test needs about it."""

    path: Path
    boundary: date
    window_start: date
    window_end: date
    sessions: tuple[date, ...]
    """Every session with a bar for AAA: the calendar the fixture was built on."""
    rows: dict[str, int]
    """Row counts of the tables the builder filled."""

    def connect(self, *, read_only: bool = True) -> duckdb.DuckDBPyConnection:
        """Open the built file (read-only by default)."""
        return duckdb.connect(str(self.path), read_only=read_only)


def _close_at(session_no: int, issuer_no: int, level: float) -> float:
    """A deterministic, non-constant close: a different path for each issuer."""
    wobble = ((session_no * (3 + issuer_no)) % 11 - 5) / 250.0  # within +-2 percent
    return round(level * (1.0 + wobble), 4)


def _volume_at(session_no: int, issuer_no: int, level: int) -> int:
    swing = ((session_no * (5 + issuer_no)) % 7 - 3) / 150.0  # within +-2 percent
    return int(level * (1.0 + swing))


def _insert_prices(conn: duckdb.DuckDBPyConnection, sessions: tuple[date, ...]) -> int:
    rows: list[tuple[object, ...]] = []
    for issuer_no, (name, (_, level, volume)) in enumerate(ISSUERS.items()):
        last = LAST_BAR[name]
        for session_no, session in enumerate(sessions):
            if last is not None and session > last:
                continue
            close = _close_at(session_no, issuer_no, level)
            stamp = session_close(session)
            rows.append(
                (
                    f"SEC_{name}",
                    session,
                    close * 0.99,  # open, high and low differ from close on purpose
                    close * 1.02,
                    close * 0.97,
                    close,
                    _volume_at(session_no, issuer_no, volume),
                    stamp,
                    stamp,
                    "test",
                    "bar",
                )
            )
    # A restatement of one old bar, known only after the development boundary.
    restated = datetime(2020, 1, 15, 15, tzinfo=UTC)
    rows.append(
        ("SEC_AAA", date(2018, 3, 1), 1.0, 1.0, 1.0, 1.0, 1, restated, restated, "test", "bar")
    )
    conn.executemany(
        "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
        "known_at, ingested_at, source, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def _insert_issuers(conn: duckdb.DuckDBPyConnection) -> None:
    early = datetime(2017, 12, 1, 15, tzinfo=UTC)
    for name, (cik, _, _) in ISSUERS.items():
        base = {"known_at": early, "ingested_at": early, "source": "test", "provenance": "snapshot"}
        insert_row(
            conn,
            "securities",
            {"security_id": f"SEC_{name}", "cik": cik, "name": name, "benchmark": False, **base},
        )
        insert_row(
            conn,
            "listings",
            {
                "security_id": f"SEC_{name}",
                "ticker": name,
                "exchange": "XNYS",
                "valid_from": date(2017, 12, 1),
                **base,
            },
        )


def _insert_form4(conn: duckdb.DuckDBPyConnection) -> None:
    for number, (issuer, accepted, code, officer, filer, _why) in enumerate(FORM4_EVENTS):
        insert_row(
            conn,
            "sec_form4_transactions",
            {
                "accession": f"0000000000-18-{number:06d}",
                "issuer_cik": CIK[issuer],
                "filer_cik": f"99{filer}",
                "line_no": 0,
                "is_director": False,
                "is_officer": officer,
                "is_ten_percent_owner": False,
                "is_other": False,
                "transaction_date": accepted.date() - timedelta(days=1),
                "transaction_code": code,
                "acquired_disposed": "A" if code == "P" else "D",
                "shares": 100,
                "ownership_form": "D",
                "security_title": "Common Stock",
                "accepted_at": accepted,
                "known_at": accepted,
                "ingested_at": accepted,
                "source": "test",
                "provenance": "filing",
            },
        )


def _insert_statement_facts(conn: duckdb.DuckDBPyConnection) -> None:
    # (issuer, form, comparative, period_end, known_at)
    rows = (
        ("AAA", "10-K", False, date(2017, 12, 31), datetime(2018, 3, 1, 21, tzinfo=UTC)),
        ("BBB", "10-Q", False, date(2018, 3, 31), datetime(2018, 5, 10, 11, tzinfo=UTC)),
        ("CCC", "10-K", True, date(2016, 12, 31), datetime(2018, 2, 20, 21, tzinfo=UTC)),
        ("DDD", "8-K", False, date(2018, 3, 31), datetime(2018, 5, 11, 11, tzinfo=UTC)),
        ("BBB", "10-Q", False, date(2018, 12, 31), datetime(2019, 4, 2, 11, tzinfo=UTC)),
        ("AAA", "10-K", False, date(2019, 12, 31), datetime(2020, 2, 28, 21, tzinfo=UTC)),
    )
    for issuer, form, comparative, period_end, known in rows:
        insert_row(
            conn,
            "statement_facts",
            {
                "cik": CIK[issuer],
                "fact_name": "revenue",
                "xbrl_tag": "Revenues",
                "period_start": period_end - timedelta(days=90),
                "period_end": period_end,
                "period_days": 90,
                "value": 1_000_000.0,
                "unit": "USD",
                "form": form,
                "filing_accession": f"0000000001-18-{period_end:%y%m%d}",
                "basis": "reported",
                "comparative": comparative,
                "known_at": known,
                "ingested_at": known,
                "source": "test",
                "provenance": "filing",
            },
        )


def _insert_corporate_actions(conn: duckdb.DuckDBPyConnection) -> None:
    rows = (
        ("AAA", "split", date(2018, 6, 15), datetime(2018, 6, 12, 21, tzinfo=UTC)),
        ("BBB", "dividend", date(2018, 8, 1), datetime(2018, 7, 20, 21, tzinfo=UTC)),
        ("CCC", "split", date(2018, 10, 10), datetime(2018, 10, 5, 21, tzinfo=UTC)),
        ("BBB", "split", date(2019, 4, 8), datetime(2019, 4, 3, 21, tzinfo=UTC)),
        ("CCC", "split", date(2020, 2, 18), datetime(2020, 2, 13, 21, tzinfo=UTC)),
    )
    for issuer, kind, ex_date, known in rows:
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": f"SEC_{issuer}",
                "action_type": kind,
                "ex_date": ex_date,
                "ratio_or_amount": 2.0,
                "known_at": known,
                "ingested_at": known,
                "source": "test",
                "provenance": "action",
            },
        )


def _insert_delistings(conn: duckdb.DuckDBPyConnection) -> None:
    for issuer, filed, effective in DELISTINGS:
        insert_row(
            conn,
            "delistings",
            {
                "security_id": f"SEC_{issuer}",
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "XNYS",
                "filed_at": filed,
                "effective_on": effective,
                "known_at": filed,
                "ingested_at": filed,
                "source": "test",
                "provenance": "filing",
            },
        )


def build_feasibility_store(path: Path) -> FeasibilityStore:
    """Build the store at `path` (a temp file, never a real store) and return its description."""
    sessions = tuple(s for s in all_sessions() if WINDOW_START <= s <= LAST_BAR_SESSION)
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        _insert_issuers(conn)
        n_prices = _insert_prices(conn, sessions)
        _insert_form4(conn)
        _insert_statement_facts(conn)
        _insert_corporate_actions(conn)
        _insert_delistings(conn)
        started = datetime(2018, 1, 1, 12, tzinfo=UTC)
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": "feasibility-fixture-1",
                "started_at": started,
                "finished_at": started + timedelta(hours=1),
                "status": "ok",
                "source": "test",
                "mode": "fixture",
                "rows_added": n_prices,
            },
        )
        registry.write_development_boundary(conn, boundary=BOUNDARY, reason="feasibility fixture")
        rows = {
            table: int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # type: ignore[index]
            for table in (*SOURCE_TABLES.values(), "prices_daily", "securities", "listings")
        }
    finally:
        conn.close()
    return FeasibilityStore(
        path=path,
        boundary=BOUNDARY,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        sessions=sessions,
        rows=rows,
    )
