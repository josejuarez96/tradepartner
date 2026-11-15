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
unmoved losses against configured thresholds. It writes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

import duckdb

_ALIAS = re.compile(r"[a-z_][a-z0-9_]*")
_MAX_SUCCESSORS = 3


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

    @property
    def unmoved(self) -> int:
        """Lost sessions that no other id gained."""
        return self.lost - self.moved


def release_coverage(
    conn: duckdb.DuckDBPyConnection,
    *,
    backup_alias: str,
    price_tolerance: float = 1e-6,
    retraction_release: str | None = None,
    accepted: Collection[tuple[str, date]] = (),
) -> list[SessionLoss]:
    """Every security with lost sessions, by id.

    `conn` is read-only on the current store with the backup attached read-only
    as `backup_alias`; `retraction_release` names a release whose unrestored
    `retracted_rows` bars count as moved when another id holds an equal bar.
    `accepted` bar keys (`security_id`, session) are left out of the losses: the
    owner-approved orphans of the release (`registry.approved_orphans`).
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
                   list(DISTINCT cb.security_id) AS successors
            FROM lost l
            JOIN bb ON bb.security_id = l.security_id AND bb.session = l.session
            JOIN cb ON cb.session = l.session AND cb.security_id <> l.security_id
             AND (EXISTS (SELECT 1 FROM gained g
                          WHERE g.security_id = cb.security_id AND g.session = cb.session)
                  OR EXISTS (SELECT 1 FROM retr r
                             WHERE r.security_id = l.security_id AND r.session = l.session))
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
        [[k[0] for k in keys], [k[1] for k in keys], *retr_params, *([price_tolerance] * 4)],
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
