"""The security fence: reads over the append-only `security_fence` table (#3501,
plan task T401; data-foundation spec amendment 2026-11-16, items 1, 2, 4 and 5).

A fence excludes a security from backtests and paper trading inside a closed
window of sessions and counts it; the fenced bars stay in the store. This module
is pure over rows and a connection: it reads, it never writes (`fence_cmd` is the
only writer).

- `fence_as_of(conn, read_at)`: the active rows. A row is keyed by
  (`security_id`, `from_session`, `rule`); the key's latest row with `known_at`
  and `ingested_at` at or before `read_at` decides, and the key is active when
  that row's `action` is `fence`. A lift is a later row, so a read as of a time
  before the lift still returns the fence.
- `fenced_ids(rows, session)`: the ids fenced on a session.
- `fence_vintage(conn, read_at)`: the latest `known_at` over **every** row (fence
  or lift, active or not) visible at `read_at`, None only with no row at all. A
  vintage over active rows alone would be wrong after a lift.
- `split_findings(rows, findings)`: a health finding is accepted by a fence row
  of the same rule and id whose window holds the finding's **whole** span.
- `fence_share(...)`: the count line's numbers (item 5).

The fence is read at a run's read time, not at a backtest's cutoff (spec item 2):
it is a decision made today about past sessions. A store older than schema
version 50 has no table and reads as an empty fence.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

import duckdb

from tradepartner.timeutil import ensure_tz_aware_utc

TABLE: Final = "security_fence"
FENCE: Final = "fence"
LIFT: Final = "lift"

#: The rule name of a fence the owner writes by id and window, not from a health
#: finding. Always allowed.
MANUAL_RULE: Final = "manual"

#: The health rules that accept a finding by fence (`split_findings` is called by
#: their checks). Empty until T403 wires each rule and adds it here; `fence add`
#: refuses any other rule but `manual`, so no counted row can accept nothing.
ACCEPTING_RULES: frozenset[str] = frozenset()

_COLUMNS: Final = (
    "security_id, from_session, to_session, rule, reason, issue, action, batch, "
    "known_at, ingested_at"
)

type FenceKey = tuple[str, date, str]


@dataclass(frozen=True)
class FenceRow:
    """One `security_fence` row."""

    security_id: str
    from_session: date
    to_session: date
    rule: str
    reason: str
    issue: int
    action: str
    batch: int
    known_at: datetime
    ingested_at: datetime

    @property
    def key(self) -> FenceKey:
        """The row's key: a lift names the same key as the fence it lifts."""
        return (self.security_id, self.from_session, self.rule)

    def holds(self, session: date) -> bool:
        """Whether the closed window `[from_session, to_session]` holds `session`."""
        return self.from_session <= session <= self.to_session


@dataclass(frozen=True)
class Finding:
    """A health finding to match against the fence: rule `rule`, security
    `security_id`, sessions `first_session` to `last_session` inclusive (equal for
    a single-session finding)."""

    rule: str
    security_id: str
    first_session: date
    last_session: date


@dataclass(frozen=True)
class FenceShare:
    """The count line's numbers (spec item 5)."""

    fenced_securities: int
    universe_securities: int
    fenced_pairs: int
    universe_pairs: int
    fenced_delisted: int
    universe_delisted: int

    @property
    def security_share(self) -> float:
        """Fenced securities over the universe's (the `fence.max_security_share` ratio)."""
        return _ratio(self.fenced_securities, self.universe_securities)

    @property
    def pair_share(self) -> float:
        """Fenced (security, session) pairs over the universe's bar pairs."""
        return _ratio(self.fenced_pairs, self.universe_pairs)

    @property
    def delisted_universe_share(self) -> float:
        """The delisted share of the universe, for comparison."""
        return _ratio(self.universe_delisted, self.universe_securities)

    def line(self) -> str:
        """The one count line `health` and `fence list` print."""
        return (
            f"fence: {self.fenced_securities} of {self.universe_securities} securities "
            f"({self.security_share:.2%}), {self.fenced_pairs} of {self.universe_pairs} "
            f"(security, session) pairs ({self.pair_share:.2%}); {self.fenced_delisted} "
            f"fenced delisted; delisted share of the universe "
            f"{self.universe_delisted} of {self.universe_securities} "
            f"({self.delisted_universe_share:.2%})"
        )


def _ratio(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def has_fence_table(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether the store has `security_fence` (version 50). A read-only older store
    lacks it and reads as an empty fence."""
    found = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = ?",
        [TABLE],
    ).fetchone()
    return found is not None and found[0] > 0


def _visible_rows(conn: duckdb.DuckDBPyConnection, read_at: datetime) -> list[FenceRow]:
    """Every row with `known_at` and `ingested_at` at or before `read_at`, oldest
    first (ties by write batch). Empty on a store without the table."""
    at = ensure_tz_aware_utc(read_at, field_name="read_at")
    if not has_fence_table(conn):
        return []
    found = conn.execute(
        f"SELECT {_COLUMNS} FROM {TABLE} WHERE known_at <= ? AND ingested_at <= ? "
        "ORDER BY known_at, ingested_at, batch",
        [at, at],
    ).fetchall()
    return [FenceRow(*row) for row in found]


def active_rows(rows: Iterable[FenceRow]) -> list[FenceRow]:
    """The rows still in force: per key the latest by (`known_at`, `ingested_at`,
    `batch`), kept only when its `action` is `fence`. Pure; sorted by key."""
    latest: dict[FenceKey, FenceRow] = {}
    for row in sorted(rows, key=lambda r: (r.known_at, r.ingested_at, r.batch)):
        latest[row.key] = row
    return [latest[key] for key in sorted(latest) if latest[key].action == FENCE]


def fence_as_of(conn: duckdb.DuckDBPyConnection, read_at: datetime) -> list[FenceRow]:
    """The active fence rows as of `read_at`: a row written after it is invisible,
    and a lift removes a fence only from its own `known_at`."""
    return active_rows(_visible_rows(conn, read_at))


def fenced_ids(rows: Iterable[FenceRow], session: date) -> frozenset[str]:
    """The ids that `rows` (active rows) fence on `session`."""
    return frozenset(r.security_id for r in rows if r.action == FENCE and r.holds(session))


def fence_vintage(conn: duckdb.DuckDBPyConnection, read_at: datetime) -> datetime | None:
    """The latest `known_at` over every fence and lift row visible at `read_at`,
    active or not; None only when no row is visible. A run records it so a
    reproduction reads the fence as of that vintage."""
    rows = _visible_rows(conn, read_at)
    return max((r.known_at for r in rows), default=None)


def split_findings(
    rows: Iterable[FenceRow], findings: Sequence[Finding]
) -> tuple[list[Finding], list[Finding]]:
    """Split `findings` into (accepted, failing) by the active fence `rows`.

    A finding of rule `R` for security `S` over `[first, last]` is accepted when
    a fence row for `S` with rule `R` has a window that holds the **whole** span;
    a span that runs past `to_session` fails, as do another rule's finding, a
    finding outside the window and one on an unfenced name. Order is kept."""
    by_key: dict[tuple[str, str], list[FenceRow]] = {}
    for row in rows:
        if row.action == FENCE:
            by_key.setdefault((row.security_id, row.rule), []).append(row)
    accepted: list[Finding] = []
    failing: list[Finding] = []
    for finding in findings:
        windows = by_key.get((finding.security_id, finding.rule), ())
        if any(w.holds(finding.first_session) and w.holds(finding.last_session) for w in windows):
            accepted.append(finding)
        else:
            failing.append(finding)
    return accepted, failing


def fence_share(
    rows: Iterable[FenceRow],
    session: date,
    *,
    universe_ids: Collection[str],
    delisted_ids: Collection[str],
    bar_sessions: Mapping[str, Collection[date]],
) -> FenceShare:
    """The count line's numbers over the active fence `rows`.

    `universe_ids` are the members before the fence on the denominator's own
    `session`; the numerator is the members whose row holds `session` (one
    numerator for health and paper, spec item 5). `bar_sessions` maps a member to
    the sessions it has a bar on; a pair is fenced when any active window of the
    member holds its session. `delisted_ids` marks the delisted members."""
    active = [r for r in rows if r.action == FENCE]
    universe = set(universe_ids)
    delisted = set(delisted_ids)
    windows: dict[str, list[FenceRow]] = {}
    for row in active:
        windows.setdefault(row.security_id, []).append(row)
    fenced = {sid for sid in universe if any(w.holds(session) for w in windows.get(sid, ()))}
    universe_pairs = 0
    fenced_pairs = 0
    for sid in universe:
        days = bar_sessions.get(sid, ())
        universe_pairs += len(days)
        held = windows.get(sid, ())
        fenced_pairs += sum(1 for d in days if any(w.holds(d) for w in held))
    return FenceShare(
        fenced_securities=len(fenced),
        universe_securities=len(universe),
        fenced_pairs=fenced_pairs,
        universe_pairs=universe_pairs,
        fenced_delisted=len(fenced & delisted),
        universe_delisted=len(universe & delisted),
    )
