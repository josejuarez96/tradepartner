"""The in-window restamp pre-check, a read-only query (plan task T166f).

Amendment 2026-10-11 (#1497), decision (d), of the data-foundation plan: a
stamp inside an open paper window may be corrected earlier, to its SGML header
time, only when no paper decision read an affected row. This module holds the
read-only query both repair tools import (the cache driver through the file
`scripts/restamp_precheck.py` writes, the store restamp inside its own
transaction):

- `window_first_session`: the first session of the earliest open window.
- `read_instants`: every instant a paper run read the store at.
- `journal_high_water`: the newest `known_at` over the paper journal tables
  that add instants.
- `candidate_rows`: per table, the rows a correction could move inside the
  window, the ones it could make the store restamp refuse, and the ones
  within reach of an instant.
- `compute_precheck` / `Precheck`: the whole check and its JSON file,
  `precheck.<release>.json`.

Nothing here writes; every query runs on a read-only connection.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import (
    is_session,
    next_session,
    previous_session,
    session_close,
)
from tradepartner.store.journal import CLOSING_STOP_STATES
from tradepartner.store.registry import store_max_ingested_at
from tradepartner.timeutil import ensure_tz_aware_utc

_NEW_YORK = ZoneInfo("America/New_York")

CORRECTION_REACH = timedelta(hours=5)
"""How far a correction can move a stamp earlier: New York's largest UTC
offset (5 h in EST), so the bound covers EDT and EST dates alike."""

CANDIDATE_TABLES: tuple[str, ...] = (
    "securities",
    "listings",
    "classifications",
    "facts",
    "statement_facts",
    "delistings",
    "filing_events",
)
"""The tables whose `known_at` an in-window correction could move."""

JOURNAL_HIGH_WATER_TABLES: tuple[str, ...] = (
    "paper_runs",
    "paper_plans",
    "paper_reports",
    "reconciliations",
)
"""The journal tables whose new rows add read instants."""

_OPEN_WINDOWS_SQL = (
    "SELECT window_id, book_id, first_rebalance_session, started_at FROM paper_windows "
    "WHERE window_id NOT IN (SELECT window_id FROM paper_window_stops "
    "WHERE list_contains(?, state)) ORDER BY window_id"
)


def _utc(value: datetime, field: str) -> datetime:
    return ensure_tz_aware_utc(value, field_name=field).astimezone(UTC)


def _ny_date(instant: datetime) -> date:
    return instant.astimezone(_NEW_YORK).date()


def _ny_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=_NEW_YORK).astimezone(UTC)


def _session_on_or_after(day: date) -> date:
    return day if is_session(day) else next_session(day)


@dataclass(frozen=True)
class OpenWindow:
    """A paper window with no `closed` or `abandoned` stop."""

    window_id: int
    book_id: str
    first_rebalance_session: date
    started_at: datetime

    def to_json(self) -> dict[str, Any]:
        """The window as JSON values (ISO date and UTC instant)."""
        return {
            "window_id": self.window_id,
            "book_id": self.book_id,
            "first_rebalance_session": self.first_rebalance_session.isoformat(),
            "started_at": self.started_at.isoformat(),
        }


def open_windows(conn: duckdb.DuckDBPyConnection) -> list[OpenWindow]:
    """Every open paper window, by `window_id`."""
    rows = conn.execute(_OPEN_WINDOWS_SQL, [list(CLOSING_STOP_STATES)]).fetchall()
    return [OpenWindow(int(w), str(b), f, _utc(s, "started_at")) for w, b, f, s in rows]


def window_first_session(conn: duckdb.DuckDBPyConnection) -> date | None:
    """The earliest first session of any open paper window (one with no
    `closed` or `abandoned` stop): the XNYS session on or after its
    `started_at`'s New York date. None when no window is open."""
    windows = open_windows(conn)
    if not windows:
        return None
    started = min(w.started_at for w in windows)
    return _session_on_or_after(_ny_date(started))


@dataclass(frozen=True)
class ReadInstant:
    """One instant a paper run read the store at, and where it comes from."""

    at: datetime
    source: str

    def to_json(self) -> dict[str, str]:
        """The instant as JSON values."""
        return {"at": self.at.isoformat(), "source": self.source}


def read_instant_sources(conn: duckdb.DuckDBPyConnection) -> list[ReadInstant]:
    """Every read instant of the open windows, with its source, sorted.

    Per decision (d) (2): each open window's `started_at`; for every
    `paper_runs` row of those windows with a session, whatever its kind,
    `close(previous_session(S))` and `close(S)`; for every `paper_plans` row,
    its `plan_trial_id`'s `trials.data_cutoff`; for every `paper_reports`
    row, `close(through_session)`; and for every `reconciliations` row,
    `close(previous_session(session of at))`, the session of ``at`` being
    its New York date when that is a session, else the latest session before
    it, as `paper reconcile` states it. A plan whose trial or `data_cutoff`
    is missing raises `ValueError`: an instant is never dropped.
    """
    windows = open_windows(conn)
    ids = [w.window_id for w in windows]
    found: list[ReadInstant] = [
        ReadInstant(w.started_at, f"window {w.window_id} started_at") for w in windows
    ]
    if not ids:
        return found
    runs = conn.execute(
        "SELECT run_id, session FROM paper_runs WHERE list_contains(?, window_id) "
        "AND session IS NOT NULL ORDER BY run_id",
        [ids],
    ).fetchall()
    for run_id, session in runs:
        found.append(
            ReadInstant(session_close(previous_session(session)), f"run {run_id} close(S-1)")
        )
        found.append(ReadInstant(session_close(session), f"run {run_id} close(S)"))
    plans = conn.execute(
        "SELECT p.run_id, t.data_cutoff FROM paper_plans p "
        "JOIN paper_runs r ON r.run_id = p.run_id "
        "LEFT JOIN trials t ON t.trial_id = p.plan_trial_id "
        "WHERE list_contains(?, r.window_id) ORDER BY p.run_id",
        [ids],
    ).fetchall()
    for run_id, cutoff in plans:
        if cutoff is None:
            raise ValueError(f"plan {run_id}: its trial or data_cutoff is missing")
        found.append(ReadInstant(_utc(cutoff, "data_cutoff"), f"plan {run_id} data_cutoff"))
    reports = conn.execute(
        "SELECT window_id, through_session FROM paper_reports "
        "WHERE list_contains(?, window_id) ORDER BY window_id, through_session",
        [ids],
    ).fetchall()
    for window_id, through in reports:
        found.append(
            ReadInstant(
                session_close(through), f"report window {window_id} close({through.isoformat()})"
            )
        )
    recs = conn.execute(
        'SELECT reconciliation_id, "at" FROM reconciliations '
        "WHERE list_contains(?, window_id) ORDER BY reconciliation_id",
        [ids],
    ).fetchall()
    for rec_id, at in recs:
        # `paper reconcile`'s session: the clock's New York date when it is
        # a session, else the latest session before it
        # (`execution.reconcile_run.command_session`).
        day = _ny_date(_utc(at, "at"))
        session = day if is_session(day) else previous_session(day)
        found.append(
            ReadInstant(
                session_close(previous_session(session)), f"reconciliation {rec_id} close(S-1)"
            )
        )
    return sorted(found, key=lambda i: (i.at, i.source))


def read_instants(conn: duckdb.DuckDBPyConnection) -> list[datetime]:
    """Every instant a paper run of an open window read the store at, UTC,
    sorted and distinct (`read_instant_sources` without the sources)."""
    return sorted({i.at for i in read_instant_sources(conn)})


def journal_high_water(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    """The newest `known_at` over `JOURNAL_HIGH_WATER_TABLES`; None when they
    are empty. A new paper run, plan, report or reconciliation moves it."""
    union = " UNION ALL ".join(
        f"SELECT max(known_at) AS m FROM {table}" for table in JOURNAL_HIGH_WATER_TABLES
    )
    row = conn.execute(f"SELECT max(m) FROM ({union})").fetchone()
    if row is None or row[0] is None:
        return None
    return _utc(row[0], "known_at")


def _near_boundary(stamp: datetime) -> bool:
    """Whether ``stamp`` lies within `CORRECTION_REACH` after a New York
    midnight or after a session close, so an earlier correction could move
    its New York date or its session (the store restamp's refusals)."""
    day = _ny_date(stamp)
    midnight = _ny_midnight(day)
    if midnight <= stamp <= midnight + CORRECTION_REACH:
        return True
    if is_session(day):
        close = session_close(day)
        if close <= stamp <= close + CORRECTION_REACH:
            return True
    return False


def _in_reach(stamp: datetime, instants: Sequence[datetime]) -> datetime | None:
    """The first instant ``t`` with ``stamp`` in ``(t, t + CORRECTION_REACH]``."""
    for t in instants:
        if t < stamp <= t + CORRECTION_REACH:
            return t
    return None


@dataclass(frozen=True)
class TableCandidates:
    """One table's in-window candidate rows."""

    rows: int
    refusal_candidates: int
    in_reach: int

    def to_json(self) -> dict[str, int]:
        """The counts as JSON values."""
        return {
            "rows": self.rows,
            "refusal_candidates": self.refusal_candidates,
            "in_reach": self.in_reach,
        }


@dataclass(frozen=True)
class InReachRow:
    """Rows of one table at one `known_at` within reach of an instant."""

    table: str
    known_at: datetime
    rows: int
    instant: datetime

    def to_json(self) -> dict[str, Any]:
        """The group as JSON values."""
        return {
            "table": self.table,
            "known_at": self.known_at.isoformat(),
            "rows": self.rows,
            "instant": self.instant.isoformat(),
        }


def candidate_rows(
    conn: duckdb.DuckDBPyConnection,
    window_start: date | None,
    instants: Sequence[datetime],
) -> tuple[dict[str, TableCandidates], list[InReachRow]]:
    """The candidate rows of decision (d) (3) and the ones failing (4).

    A candidate is a row of a `CANDIDATE_TABLES` table whose `known_at` New
    York date is on or after ``window_start``. Per table: how many there are,
    how many are refusal candidates (within `CORRECTION_REACH` after a New
    York midnight or a session close, where an earlier correction could move
    a date or session a stored column derives from), and how many have
    `known_at` in ``(t, t + CORRECTION_REACH]`` for some instant ``t``; those
    last are also returned grouped by table and `known_at`. With no window,
    every count is zero.
    """
    counts: dict[str, TableCandidates] = {}
    failing: list[InReachRow] = []
    ordered = sorted(_utc(t, "instant") for t in instants)
    for table in CANDIDATE_TABLES:
        if window_start is None:
            counts[table] = TableCandidates(0, 0, 0)
            continue
        groups = conn.execute(
            f"SELECT known_at, count(*) FROM {table} WHERE known_at >= ? "
            "GROUP BY known_at ORDER BY known_at",
            [_ny_midnight(window_start)],
        ).fetchall()
        rows = refusals = reach = 0
        for raw, n in groups:
            stamp = _utc(raw, "known_at")
            rows += n
            if _near_boundary(stamp):
                refusals += n
            hit = _in_reach(stamp, ordered)
            if hit is not None:
                reach += n
                failing.append(InReachRow(table, stamp, int(n), hit))
        counts[table] = TableCandidates(rows, refusals, reach)
    return counts, failing


@dataclass(frozen=True)
class Precheck:
    """The pre-check's result: what `precheck.<release>.json` holds.

    ``passed`` is True when there is no read instant, or when no candidate
    row has `known_at` within `CORRECTION_REACH` after one.
    """

    release: str
    window_first_session: date | None
    passed: bool
    instants: tuple[datetime, ...]
    journal_high_water: datetime | None
    made_at: datetime
    store_max_ingested_at: datetime | None
    open_windows: tuple[OpenWindow, ...]
    instant_sources: tuple[ReadInstant, ...]
    candidates: Mapping[str, TableCandidates]
    in_reach: tuple[InReachRow, ...]

    def to_json(self) -> dict[str, Any]:
        """The file's JSON object."""

        def iso(value: datetime | date | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "release": self.release,
            "window_first_session": iso(self.window_first_session),
            "pass": self.passed,
            "instants": [t.isoformat() for t in self.instants],
            "journal_high_water": iso(self.journal_high_water),
            "made_at": self.made_at.isoformat(),
            "store_max_ingested_at": iso(self.store_max_ingested_at),
            "open_windows": [w.to_json() for w in self.open_windows],
            "instant_sources": [i.to_json() for i in self.instant_sources],
            "candidates": {t: c.to_json() for t, c in self.candidates.items()},
            "in_reach": [r.to_json() for r in self.in_reach],
        }

    def to_bytes(self) -> bytes:
        """The file's bytes: sorted-key, indented JSON with a final newline."""
        return (json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n").encode()


def compute_precheck(
    conn: duckdb.DuckDBPyConnection, *, release: str, made_at: datetime
) -> Precheck:
    """Run the whole pre-check on ``conn`` (read-only) for ``release``."""
    first = window_first_session(conn)
    sources = read_instant_sources(conn)
    instants = tuple(sorted({i.at for i in sources}))
    counts, failing = candidate_rows(conn, first, instants)
    return Precheck(
        release=release,
        window_first_session=first,
        passed=not failing,
        instants=instants,
        journal_high_water=journal_high_water(conn),
        made_at=_utc(made_at, "made_at"),
        store_max_ingested_at=store_max_ingested_at(conn),
        open_windows=tuple(open_windows(conn)),
        instant_sources=tuple(sources),
        candidates=counts,
        in_reach=tuple(failing),
    )


def precheck_file_name(release: str) -> str:
    """The pre-check file's name for ``release``."""
    return f"precheck.{release}.json"


def file_sha256(path: Path) -> str:
    """SHA-256 over the file's bytes, the hash the `before` record carries."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class PrecheckFile:
    """The fields of a written pre-check file the repair tools check."""

    release: str
    window_first_session: date | None
    passed: bool
    instants: tuple[datetime, ...]
    journal_high_water: datetime | None
    made_at: datetime
    sha256: str


def _field(data: Mapping[str, Any], name: str, path: Path) -> Any:
    if name not in data:
        raise ValueError(f"{path}: {name} is missing")
    return data[name]


def _instant_field(value: Any, name: str, path: Path) -> datetime:
    """A tz-aware ISO instant, as UTC; anything else is refused."""
    if not isinstance(value, str):
        raise ValueError(f"{path}: {name} is not an ISO instant: {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{path}: {name} is not an ISO instant: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{path}: {name} is not tz-aware: {value!r}")
    return parsed.astimezone(UTC)


def load_precheck(path: Path) -> PrecheckFile:
    """Read a `precheck.<release>.json` written by `Precheck.to_bytes`.

    Every field the repair tools check is type-checked, and a missing or
    mistyped one raises `ValueError` (a gate fails closed): `pass` must be a
    JSON boolean, `release` a string, `window_first_session` an ISO date or
    null, `instants` a list of tz-aware ISO instants, `journal_high_water` a
    tz-aware ISO instant or null, and `made_at` a tz-aware ISO instant.
    """
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a JSON object")
    passed = _field(data, "pass", path)
    if not isinstance(passed, bool):
        raise ValueError(f"{path}: pass is not a JSON boolean: {passed!r}")
    release = _field(data, "release", path)
    if not isinstance(release, str):
        raise ValueError(f"{path}: release is not a string: {release!r}")
    first = _field(data, "window_first_session", path)
    window_first: date | None = None
    if first is not None:
        if not isinstance(first, str) or len(first) != 10:
            raise ValueError(f"{path}: window_first_session is not an ISO date: {first!r}")
        try:
            window_first = date.fromisoformat(first)
        except ValueError as exc:
            raise ValueError(f"{path}: window_first_session is not an ISO date: {first!r}") from exc
    instants = _field(data, "instants", path)
    if not isinstance(instants, list):
        raise ValueError(f"{path}: instants is not a list: {instants!r}")
    high = _field(data, "journal_high_water", path)
    return PrecheckFile(
        release=release,
        window_first_session=window_first,
        passed=passed,
        instants=tuple(_instant_field(t, "instants", path) for t in instants),
        journal_high_water=None
        if high is None
        else _instant_field(high, "journal_high_water", path),
        made_at=_instant_field(_field(data, "made_at", path), "made_at", path),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def crossed_instants(old: datetime, new: datetime, instants: Iterable[datetime]) -> list[datetime]:
    """The instants ``t`` with ``new <= t < old``: a decision at ``t`` read the
    row's old stamp as not yet known and would read the new one as known.
    Any one refuses the change (decision (d), the exact check)."""
    return [t for t in instants if new <= t < old]


__all__ = [
    "CANDIDATE_TABLES",
    "CORRECTION_REACH",
    "JOURNAL_HIGH_WATER_TABLES",
    "InReachRow",
    "OpenWindow",
    "Precheck",
    "PrecheckFile",
    "ReadInstant",
    "TableCandidates",
    "candidate_rows",
    "compute_precheck",
    "crossed_instants",
    "file_sha256",
    "journal_high_water",
    "load_precheck",
    "open_windows",
    "precheck_file_name",
    "read_instant_sources",
    "read_instants",
    "window_first_session",
]
