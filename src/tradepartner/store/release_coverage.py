"""Release coverage (#3274): a read-only before/after check of each security's
trading days across a data release.

A count check proves a repair's size, not that it is right: nothing else notices
a liquid security losing months of bars. This compares the distinct
`prices_daily` sessions per `security_id` in a release's pre-release backup
(attached read-only) with the current store and lists every security that lost
sessions. A lost session is "moved" when another
`security_id` gained a bar for the same session that equals the lost bar (same
volume, OHLC within a relative tolerance with an absolute floor at price 1),
as a merge into a successor id does. With `retraction_release`, a session the release
retracted from `retracted_rows` (T393, ADR 0029) is also moved when another id
holds an equal bar for it in the current store, gained or not: the retracted bar
was a duplicate of the owner's. The caller checks per-security and total
unmoved losses against configured thresholds. A retracted session whose receiving
id gained a bar for it (none in the backup) with the same open and close and the same issuer
(the id's CIK), but another volume, high or low, is moved too, counted as a vendor
revision (#3432): the vendor now serves a different bar for that day. It writes nothing.

A price also matches when the older (lost or retracted) row holds it at the configured
`price_decimals` and is the newer price truncated or rounded to it (#3465):
the vendor first served the bar at 4 decimals (SNTI open 0.1732) and now at 5 (0.17325).
A price a whole unit or more away at that precision is still another price.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

import duckdb

_ALIAS = re.compile(r"[a-z_][a-z0-9_]*")
_MAX_SUCCESSORS = 3
# Float noise when a price is scaled to its decimal grid; not a matching tolerance.
_GRID_EPS = 1e-6


def price_match_sql(new: str, old: str, tolerance: float, decimals: int | None) -> str:
    """A SQL predicate: price `new` equals the older row's price `old` (SQL
    expressions). Equal within `tolerance` relative to `old` with an absolute floor at
    price 1; or, with `decimals`, `old` lies on the `decimals` grid and `new` is in
    [old - u/2, old + u) for u one unit of that last decimal, i.e. `old` is `new`
    truncated or rounded to the older row's stored precision (#3465). Two prices that both hold
    `decimals` places match only when equal, so a real different price still fails."""
    tol = float(tolerance)
    base = f"abs({new} - {old}) <= {tol!r} * greatest(1, abs({old}))"
    if decimals is None:
        return f"({base})"
    if not 0 <= int(decimals) <= 8:
        raise ValueError(f"price decimals out of range: {decimals!r}")
    unit = 10 ** int(decimals)
    return (
        f"({base} OR (abs({old} * {unit} - round({old} * {unit})) <= {_GRID_EPS!r} "
        f"AND {new} * {unit} - {old} * {unit} >= {-0.5 - _GRID_EPS!r} "
        f"AND {new} * {unit} - {old} * {unit} < {1 - _GRID_EPS!r}))"
    )


class ReleaseCoverageError(RuntimeError):
    """A release backup lacks a table required for the coverage check."""


@dataclass(frozen=True)
class SessionLoss:
    """One security that lost sessions across the release."""

    security_id: str
    name: str | None
    ticker: str | None
    first: date
    last: date
    lost: int
    moved: int
    moved_to: tuple[str, ...]
    revised: int = 0

    @property
    def unmoved(self) -> int:
        """Lost sessions that no other id gained."""
        return self.lost - self.moved


def release_coverage(
    conn: duckdb.DuckDBPyConnection,
    *,
    backup_alias: str,
    price_tolerance: float = 1e-6,
    price_decimals: int | None = None,
    retraction_release: str | None = None,
    accepted: Collection[tuple[str, date]] = (),
) -> list[SessionLoss]:
    """Every security with lost sessions, by id.

    `conn` is read-only on the current store with the backup attached read-only
    as `backup_alias`; `retraction_release` names a release whose unrestored
    `retracted_rows` bars count as moved when another id holds an equal bar.
    `price_decimals` is the precision an older row may hold a price at
    (`price_match_sql`). `accepted` bar keys (`security_id`, session) are left out
    of the losses: the owner-approved orphans of the release (`registry.approved_orphans`).
    Only distinct sessions count, so a revised bar (a second
    `known_at`) is no loss. A security new since the backup has no loss.
    """
    if _ALIAS.fullmatch(backup_alias) is None:
        raise ValueError(f"bad backup alias {backup_alias!r}")
    bk = backup_alias
    present = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_catalog = ? AND table_schema = 'main'",
            [bk],
        ).fetchall()
    }
    missing = {"prices_daily", "securities", "listings"} - present
    if missing:
        names = ", ".join(sorted(missing))
        raise ReleaseCoverageError(f"backup missing required table(s): {names}")
    retr = (
        "SELECT row_key[1] AS security_id, CAST(row_key[2] AS DATE) AS session "
        "FROM main.retracted_rows WHERE release = ? AND table_name = 'prices_daily' "
        "AND restored_at IS NULL"
        if retraction_release is not None
        and conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_catalog = current_database() AND table_name = 'retracted_rows' "
            "AND table_schema = 'main'"
        ).fetchone()
        == (1,)
        else "SELECT NULL::VARCHAR AS security_id, NULL::DATE AS session WHERE false"
    )
    retr_params = [retraction_release] if "?" in retr else []
    keys = sorted(accepted)

    def same(col: str) -> str:
        return price_match_sql(f"cb.{col}", f"bb.{col}", price_tolerance, price_decimals)

    rows = conn.execute(
        f"""
        WITH bb AS (
            SELECT security_id, session, arg_max(open, known_at) AS o, arg_max(high, known_at) AS h,
                   arg_max(low, known_at) AS lo, arg_max(close, known_at) AS cl,
                   arg_max(volume, known_at) AS v
            FROM {bk}.prices_daily GROUP BY security_id, session),
        cb AS (
            SELECT security_id, session, arg_max(open, known_at) AS o, arg_max(high, known_at) AS h,
                   arg_max(low, known_at) AS lo, arg_max(close, known_at) AS cl,
                   arg_max(volume, known_at) AS v
            FROM main.prices_daily GROUP BY security_id, session),
        b AS (SELECT security_id, session FROM bb),
        c AS (SELECT security_id, session FROM cb),
        acc AS (SELECT unnest(?::VARCHAR[]) AS security_id, unnest(?::DATE[]) AS session),
        lost AS (SELECT * FROM b ANTI JOIN c USING (security_id, session)
                 ANTI JOIN acc USING (security_id, session)),
        gained AS (SELECT * FROM c ANTI JOIN b USING (security_id, session)),
        retr AS ({retr}),
        moved AS (
            SELECT l.security_id, l.session, min(cb.security_id) AS successor,
                   list(DISTINCT cb.security_id) AS successors,
                   bool_or(cb.v = bb.v AND {same("h")} AND {same("lo")}) AS exact
            FROM lost l
            JOIN bb ON bb.security_id = l.security_id AND bb.session = l.session
            JOIN cb ON cb.session = l.session AND cb.security_id <> l.security_id
             AND {same("o")}
             AND {same("cl")}
             AND (
                 (cb.v = bb.v
                  AND {same("h")}
                  AND {same("lo")}
                  AND (EXISTS (SELECT 1 FROM gained g
                               WHERE g.security_id = cb.security_id AND g.session = cb.session)
                       OR EXISTS (SELECT 1 FROM retr r
                                  WHERE r.security_id = l.security_id
                                    AND r.session = l.session)))
                 OR (split_part(split_part(cb.security_id, ':', 1), '@', 1)
                         = split_part(split_part(l.security_id, ':', 1), '@', 1)
                     AND EXISTS (SELECT 1 FROM gained g
                             WHERE g.security_id = cb.security_id AND g.session = cb.session)
                     AND EXISTS (SELECT 1 FROM retr r
                                 WHERE r.security_id = l.security_id AND r.session = l.session))
             )
            GROUP BY ALL
        ),
        agg AS (
            SELECT l.security_id, min(l.session) AS first, max(l.session) AS last,
                   count(*) AS lost, count(m.session) AS moved,
                   count(m.session) FILTER (WHERE NOT m.exact) AS revised,
                   flatten(list(m.successors) FILTER (WHERE m.session IS NOT NULL)) AS succ
            FROM lost l LEFT JOIN moved m USING (security_id, session)
            GROUP BY l.security_id
        )
        SELECT a.security_id, a.first, a.last, a.lost, a.moved, a.succ, a.revised,
               coalesce(
                   (SELECT arg_max(name, known_at) FROM main.securities s
                    WHERE s.security_id = a.security_id),
                   (SELECT arg_max(name, known_at) FROM {bk}.securities s
                    WHERE s.security_id = a.security_id)),
               coalesce(
                   (SELECT arg_max(ticker, known_at) FROM main.listings s
                    WHERE s.security_id = a.security_id),
                   (SELECT arg_max(ticker, known_at) FROM {bk}.listings s
                    WHERE s.security_id = a.security_id))
        FROM agg a ORDER BY a.lost DESC, a.security_id
        """,
        [[k[0] for k in keys], [k[1] for k in keys], *retr_params],
    ).fetchall()
    out: list[SessionLoss] = []
    for sid, first, last, lost, moved, succ, revised, name, ticker in rows:
        ids = sorted({str(x) for x in (succ or [])})[:_MAX_SUCCESSORS]
        out.append(SessionLoss(sid, name, ticker, first, last, lost, moved, tuple(ids), revised))
    return out


def failing(losses: list[SessionLoss], *, max_lost_sessions: int) -> list[SessionLoss]:
    """The losses whose unmoved sessions still exceed the threshold."""
    return [x for x in losses if x.unmoved > max_lost_sessions]


def format_loss(x: SessionLoss) -> str:
    """One report line: id, name, ticker, span, count and where the bars went."""
    if x.moved == 0:
        where = "NOT MOVED"
    elif x.unmoved == 0:
        where = f"moved to {','.join(x.moved_to)}"
    else:
        where = f"PARTLY MOVED ({x.moved} of {x.lost}) to {','.join(x.moved_to)}"
    if x.revised:
        where += f" ({x.revised} as a vendor revision: another volume, high or low)"
    return (
        f"{x.security_id} {x.name or '-'} {x.ticker or '-'}: lost {x.first}..{x.last}, "
        f"{x.lost} sessions, {where}"
    )
