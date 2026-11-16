"""The `fence add`, `fence lift` and `fence list` logic (#3501, plan task T401;
data-foundation spec amendment 2026-11-16, item 3).

`cli.py` owns the options and the connections; this module owns what a fence write
means. The owner's exact count is the approval, as in `release accept-unreceived`
(#3464, `registry.record_approved_orphans`): `plan` builds and checks the rows a
request would write (and the share they would reach), `write` appends them only
when the count equals the number of rows `plan` printed. Any other count, or none,
writes nothing.

Refusals (`FenceRefused`, both for the dry run and the write, so a dry run never
promises a write that would fail):

- a data release is open (`registry.open_release`);
- the id is held by an open paper book: a non-zero position on its latest mark, or
  an order not yet terminal or placed after the last mark. The text is
  "held position: owner decision pending", the interim until paper implements "no
  new buys, leaves at the next normal rebalance"; no code decides it here;
- the fence window overlaps the sessions of an open paper window (from its first
  rebalance session on) where the window's universe holds the id: the window's
  registration lacks `universe.fence`, and a change mid-window would silently change
  its universe. A fence wholly before every open window is allowed. A lift is a
  change of the same kind and is checked the same way;
- a rule outside `fence.ACCEPTING_RULES` other than `manual`;
- an unknown id, an already active identical fence, a lift of a key that is not
  active, a blank reason, a non-positive issue or a reversed window.

One `owner_decisions` row of kind `fence_write` names the batch; every
`security_fence` row of the write carries its `decision_id` as `batch`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

import duckdb

from tradepartner.calendar import all_sessions, last_completed_session, session_close
from tradepartner.config import Settings
from tradepartner.store import fence, journal, registry
from tradepartner.store.db import insert_row
from tradepartner.store.restamp_precheck import OpenWindow, open_windows
from tradepartner.store.schema import has_retracted
from tradepartner.timeutil import ensure_tz_aware_utc

HELD_TEXT: Final = "held position: owner decision pending"

Action = Literal["fence", "lift"]
MembersOn = Callable[[date], Collection[str]]
FindingSource = Callable[[duckdb.DuckDBPyConnection, datetime], Sequence[fence.Finding]]

#: Health rule -> the source of its failing findings, for `--from-rule`. Empty until
#: T403 wires each rule and adds it here and to `fence.ACCEPTING_RULES`.
FINDING_SOURCES: dict[str, FindingSource] = {}


class FenceRefused(ValueError):
    """A fence write the rules refuse; nothing is written."""


@dataclass(frozen=True)
class Request:
    """What `fence add` or `fence lift` was asked for."""

    action: Action
    reason: str
    issue: int
    rule: str = fence.MANUAL_RULE
    security_ids: tuple[str, ...] = ()
    from_session: date | None = None
    to_session: date | None = None
    from_rule: str | None = None


@dataclass(frozen=True)
class Plan:
    """The rows a request would write, and the lines to print."""

    action: Action
    rows: tuple[fence.FenceRow, ...]
    lines: tuple[str, ...]


def members_provider(conn: duckdb.DuckDBPyConnection, settings: Settings) -> MembersOn:
    """The default `MembersOn`: the `universe_as_of` members at a session's close,
    memoised. Tests replace it; the CLI calls it through this module."""
    from tradepartner.universe import universe_as_of

    cache: dict[date, frozenset[str]] = {}

    def members(session: date) -> frozenset[str]:
        if session not in cache:
            found = universe_as_of(conn, session_close(session), settings)
            cache[session] = frozenset(str(x) for x in found.members["security_id"].to_list())
        return cache[session]

    return members


def row_line(row: fence.FenceRow) -> str:
    """One printed row, `FENCE` or `LIFT`."""
    return (
        f"{row.action.upper()} {row.security_id} {row.from_session.isoformat()}.."
        f"{row.to_session.isoformat()} rule={row.rule} issue=#{row.issue}"
    )


def _check_rule(rule: str) -> None:
    if rule != fence.MANUAL_RULE and rule not in fence.ACCEPTING_RULES:
        accepting = ", ".join(sorted(fence.ACCEPTING_RULES)) or "none yet"
        raise FenceRefused(
            f"rule {rule!r} is not accepted by fence (health accepts: {accepting}; "
            f"or {fence.MANUAL_RULE!r})"
        )


def _row(
    request: Request,
    security_id: str,
    from_session: date,
    to_session: date,
    rule: str,
    read_at: datetime,
) -> fence.FenceRow:
    return fence.FenceRow(
        security_id=security_id,
        from_session=from_session,
        to_session=to_session,
        rule=rule,
        reason=request.reason,
        issue=request.issue,
        action=request.action,
        batch=0,  # the write sets it to the decision id
        known_at=read_at,
        ingested_at=read_at,
    )


def _rule_findings(
    conn: duckdb.DuckDBPyConnection, rule: str, read_at: datetime
) -> list[fence.Finding]:
    _check_rule(rule)
    if rule == fence.MANUAL_RULE:
        raise FenceRefused("--from-rule takes a health rule, not 'manual'")
    source = FINDING_SOURCES.get(rule)
    if source is None:
        raise FenceRefused(f"health rule {rule!r} has no finding source for fence")
    return list(source(conn, read_at))


def _candidates(
    conn: duckdb.DuckDBPyConnection,
    request: Request,
    active: Sequence[fence.FenceRow],
    read_at: datetime,
) -> list[fence.FenceRow]:
    if request.from_rule is not None:
        if request.action != "fence":
            raise FenceRefused("--from-rule is for fence add only")
        if request.security_ids:
            raise FenceRefused("--from-rule and --security-id are exclusive")
        merged: dict[fence.FenceKey, fence.FenceRow] = {}
        for finding in _rule_findings(conn, request.from_rule, read_at):
            row = _row(
                request,
                finding.security_id,
                finding.first_session,
                finding.last_session,
                finding.rule,
                read_at,
            )
            held = merged.get(row.key)
            if held is None or row.to_session > held.to_session:
                merged[row.key] = row
        return [merged[key] for key in sorted(merged)]
    _check_rule(request.rule)
    if not request.security_ids:
        raise FenceRefused("give --security-id (repeatable) or --from-rule")
    if request.from_session is None:
        raise FenceRefused("--from is required")
    if request.action == "fence":
        if request.to_session is None:
            raise FenceRefused("--to is required")
        return [
            _row(request, sid, request.from_session, request.to_session, request.rule, read_at)
            for sid in sorted(set(request.security_ids))
        ]
    by_key = {r.key: r for r in active}
    rows: list[fence.FenceRow] = []
    for sid in sorted(set(request.security_ids)):
        held = by_key.get((sid, request.from_session, request.rule))
        if held is None:
            raise FenceRefused(
                f"{sid} has no active fence from {request.from_session.isoformat()} "
                f"under rule {request.rule!r}; nothing to lift"
            )
        rows.append(_row(request, sid, held.from_session, held.to_session, held.rule, read_at))
    return rows


def _known_ids(
    conn: duckdb.DuckDBPyConnection, ids: Collection[str], read_at: datetime
) -> set[str]:
    if not ids:
        return set()
    found = conn.execute(
        "SELECT DISTINCT security_id FROM securities WHERE known_at <= ? "
        "AND security_id IN (SELECT unnest(?))",
        [read_at, sorted(ids)],
    ).fetchall()
    return {str(r[0]) for r in found}


def _held_by_open_books(
    conn: duckdb.DuckDBPyConnection, windows: Sequence[OpenWindow], ids: Collection[str]
) -> dict[str, str]:
    """Id -> where an open paper book holds it (a position or an open order)."""
    held: dict[str, str] = {}
    wanted = set(ids)
    for window in windows:
        marks = journal.positions_daily_for(conn, window.window_id)
        last_marked = marks[-1].session if marks else None
        for mark in marks:
            if (
                mark.session == last_marked
                and mark.security_id in wanted
                and mark.quantity != 0
                and mark.security_id not in held
            ):
                held[str(mark.security_id)] = (
                    f"a position of {mark.quantity:g} in book {window.book_id!r} "
                    f"(window {window.window_id})"
                )
        open_orders = {
            o.client_order_id for o in journal.non_terminal_orders(conn, window_id=window.window_id)
        }
        for order in journal.orders_for(conn, window_id=window.window_id):
            if order.security_id not in wanted or order.security_id in held:
                continue
            if (
                order.client_order_id in open_orders
                or last_marked is None
                or order.session > last_marked
            ):
                held[order.security_id] = (
                    f"an open order {order.client_order_id} in book {window.book_id!r} "
                    f"(window {window.window_id})"
                )
    return held


def _window_overlap_refusals(
    rows: Sequence[fence.FenceRow],
    windows: Sequence[OpenWindow],
    read_at: datetime,
    members_on: MembersOn,
) -> list[str]:
    """Rows whose window overlaps an open paper window's sessions where that window's
    universe holds the id. A window's sessions run from its first rebalance session on;
    its universe is read at each completed overlap session (at the last completed one
    when none has completed yet)."""
    if not windows:
        return []
    completed = last_completed_session(read_at)
    sessions = all_sessions()
    problems: list[str] = []
    for row in rows:
        for window in windows:
            if row.to_session < window.first_rebalance_session:
                continue
            start = max(row.from_session, window.first_rebalance_session)
            overlap = [d for d in sessions if start <= d <= min(row.to_session, completed)]
            probe = overlap or [completed]
            if any(row.security_id in members_on(day) for day in probe):
                problems.append(
                    f"{row.security_id}: {row.from_session.isoformat()}.."
                    f"{row.to_session.isoformat()} "
                    f"overlaps open window {window.window_id} (book {window.book_id!r}, from "
                    f"{window.first_rebalance_session.isoformat()}) whose universe holds it"
                )
                break
    return problems


def _share_inputs(
    conn: duckdb.DuckDBPyConnection, universe: Collection[str], session: date, read_at: datetime
) -> tuple[set[str], dict[str, set[date]]]:
    """The delisted members and each member's bar sessions, one grouped query each."""
    ids = sorted(universe)
    if not ids:
        return set(), {}
    live = " AND NOT retracted"
    delisted_live = live if has_retracted(conn, "delistings") else ""
    delisted = {
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT security_id FROM delistings WHERE known_at <= ? "
            f"AND effective_on <= ?{delisted_live} AND security_id IN (SELECT unnest(?))",
            [read_at, session, ids],
        ).fetchall()
    }
    bars_live = live if has_retracted(conn, "prices_daily") else ""
    bars: dict[str, set[date]] = {}
    for sid, day in conn.execute(
        "SELECT DISTINCT security_id, session FROM prices_daily WHERE known_at <= ? "
        f"AND security_id IN (SELECT unnest(?)){bars_live}",
        [read_at, ids],
    ).fetchall():
        bars.setdefault(str(sid), set()).add(day)
    return delisted, bars


def share_line(
    conn: duckdb.DuckDBPyConnection,
    rows: Sequence[fence.FenceRow],
    read_at: datetime,
    members_on: MembersOn,
) -> str:
    """The count line for the active `rows`, over the universe at the latest
    completed session (spec item 5)."""
    session = last_completed_session(read_at)
    universe = set(members_on(session))
    delisted, bars = _share_inputs(conn, universe, session, read_at)
    share = fence.fence_share(
        rows, session, universe_ids=universe, delisted_ids=delisted, bar_sessions=bars
    )
    return share.line()


def plan(
    conn: duckdb.DuckDBPyConnection,
    request: Request,
    read_at: datetime,
    members_on: MembersOn,
) -> Plan:
    """Build and check the rows `request` would write, as of `read_at`.

    Raises `FenceRefused` for any refusal in the module docstring. The returned lines
    are each row, the row count, and the count line for the fence as it would stand
    after the write."""
    at = ensure_tz_aware_utc(read_at, field_name="read_at")
    if not request.reason.strip():
        raise FenceRefused("a fence write needs a reason")
    if request.issue <= 0:
        raise FenceRefused("--issue must be a GitHub issue number")
    if (
        request.from_session is not None
        and request.to_session is not None
        and request.from_session > request.to_session
    ):
        raise FenceRefused("--from is after --to")
    if (current := registry.open_release(conn)) is not None:
        raise FenceRefused(f"data release {current.name!r} is open; close it first")
    active = fence.fence_as_of(conn, at)
    rows = _candidates(conn, request, active, at)
    if not rows:
        raise FenceRefused("no rows to write")
    unknown = {r.security_id for r in rows} - _known_ids(conn, {r.security_id for r in rows}, at)
    if unknown:
        raise FenceRefused(f"unknown security id(s): {', '.join(sorted(unknown))}")
    if request.action == "fence":
        same = {(r.key, r.to_session) for r in active}
        for row in rows:
            if (row.key, row.to_session) in same:
                raise FenceRefused(f"{row.security_id} is already fenced: {row_line(row)}")
    windows = open_windows(conn)
    problems: list[str] = []
    if request.action == "fence":
        held = _held_by_open_books(conn, windows, {r.security_id for r in rows})
        problems += [f"{sid}: {HELD_TEXT} ({why})" for sid, why in sorted(held.items())]
    problems += _window_overlap_refusals(rows, windows, at, members_on)
    if problems:
        raise FenceRefused("; ".join(problems))
    after = _after(active, rows)
    lines = [row_line(r) for r in rows]
    lines.append(f"{len(rows)} row(s) to {request.action}")
    lines.append(share_line(conn, after, at, members_on))
    return Plan(request.action, tuple(rows), tuple(lines))


def _after(
    active: Sequence[fence.FenceRow], rows: Sequence[fence.FenceRow]
) -> list[fence.FenceRow]:
    """The active rows after `rows` are written (a later row wins per key)."""
    return fence.active_rows([*active, *rows])


def write(conn: duckdb.DuckDBPyConnection, made: Plan, *, count: int, read_at: datetime) -> int:
    """Append `made.rows` and one `fence_write` decision, and return the batch (the
    decision id). Writes nothing and raises `FenceRefused` unless `count` equals the
    number of rows `plan` printed: the owner's count is the approval."""
    if count != len(made.rows):
        raise FenceRefused(
            f"{len(made.rows)} row(s) to {made.action} and --count is {count}; "
            "the owner's exact count is required"
        )
    at = ensure_tz_aware_utc(read_at, field_name="read_at")
    first = made.rows[0]
    values: Mapping[str, Any] = {
        "action": made.action,
        "count": count,
        "issue": first.issue,
        "rows": [
            f"{r.security_id}|{r.from_session.isoformat()}|{r.to_session.isoformat()}|{r.rule}"
            for r in made.rows
        ],
    }
    batch = registry.record_decision(conn, kind="fence_write", reason=first.reason, values=values)
    for row in made.rows:
        insert_row(
            conn,
            fence.TABLE,
            {
                "security_id": row.security_id,
                "from_session": row.from_session,
                "to_session": row.to_session,
                "rule": row.rule,
                "reason": row.reason,
                "issue": row.issue,
                "action": row.action,
                "batch": batch,
                "known_at": at,
                "ingested_at": at,
            },
        )
    return batch


def list_lines(
    conn: duckdb.DuckDBPyConnection, read_at: datetime, members_on: MembersOn
) -> list[str]:
    """`fence list`: each active row, the row count and the count line."""
    rows = fence.fence_as_of(conn, read_at)
    lines = [
        f"{row_line(r)} batch={r.batch} known_at={r.known_at.isoformat()} reason={r.reason}"
        for r in rows
    ]
    lines.append(f"{len(rows)} active fence row(s)")
    lines.append(share_line(conn, rows, read_at, members_on))
    return lines
