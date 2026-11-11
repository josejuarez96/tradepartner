"""Release coverage (#3274): a read-only before/after check of each security's
trading days across a data release.

A count check proves a repair's size, not that it is right: nothing else notices
a liquid security losing months of bars. This compares the distinct
`prices_daily` sessions per `security_id` in a release's pre-release backup
(attached read-only) with the current store and lists every security that lost
more than a threshold of sessions. A lost session is "moved" when another
`security_id` gained a bar for the same session that equals the lost bar (same
volume, OHLC within a relative tolerance), as a merge into a successor id does. The check fails
when any security's *unmoved* loss exceeds the threshold. It writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

import duckdb

_ALIAS = re.compile(r"[a-z_][a-z0-9_]*")
_MAX_SUCCESSORS = 3


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

    @property
    def unmoved(self) -> int:
        """Lost sessions that no other id gained."""
        return self.lost - self.moved


def release_coverage(
    conn: duckdb.DuckDBPyConnection,
    *,
    backup_alias: str,
    max_lost_sessions: int,
    price_tolerance: float = 1e-6,
) -> list[SessionLoss]:
    """Every security whose lost sessions exceed `max_lost_sessions`, by id.

    `conn` is read-only on the current store with the backup attached read-only
    as `backup_alias`. Only distinct sessions count, so a revised bar (a second
    `known_at`) is no loss. A security new since the backup has no loss.
    """
    if _ALIAS.fullmatch(backup_alias) is None:
        raise ValueError(f"bad backup alias {backup_alias!r}")
    bk = backup_alias
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
        lost AS (SELECT * FROM b ANTI JOIN c USING (security_id, session)),
        gained AS (SELECT * FROM c ANTI JOIN b USING (security_id, session)),
        moved AS (
            SELECT l.security_id, l.session, min(g.security_id) AS successor,
                   list(DISTINCT g.security_id) AS successors
            FROM lost l
            JOIN bb ON bb.security_id = l.security_id AND bb.session = l.session
            JOIN gained g ON g.session = l.session AND g.security_id <> l.security_id
            JOIN cb ON cb.security_id = g.security_id AND cb.session = g.session
             AND cb.v = bb.v
             AND abs(cb.o - bb.o) <= ? * greatest(1, abs(bb.o))
             AND abs(cb.h - bb.h) <= ? * greatest(1, abs(bb.h))
             AND abs(cb.lo - bb.lo) <= ? * greatest(1, abs(bb.lo))
             AND abs(cb.cl - bb.cl) <= ? * greatest(1, abs(bb.cl))
            GROUP BY ALL
        ),
        agg AS (
            SELECT l.security_id, min(l.session) AS first, max(l.session) AS last,
                   count(*) AS lost, count(m.session) AS moved,
                   flatten(list(m.successors) FILTER (WHERE m.session IS NOT NULL)) AS succ
            FROM lost l LEFT JOIN moved m USING (security_id, session)
            GROUP BY l.security_id
            HAVING count(*) > ?
        )
        SELECT a.security_id, a.first, a.last, a.lost, a.moved, a.succ,
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
        [price_tolerance] * 4 + [max_lost_sessions],
    ).fetchall()
    out: list[SessionLoss] = []
    for sid, first, last, lost, moved, succ, name, ticker in rows:
        ids = sorted({str(x) for x in (succ or [])})[:_MAX_SUCCESSORS]
        out.append(SessionLoss(sid, name, ticker, first, last, lost, moved, tuple(ids)))
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
    return (
        f"{x.security_id} {x.name or '-'} {x.ticker or '-'}: lost {x.first}..{x.last}, "
        f"{x.lost} sessions, {where}"
    )
