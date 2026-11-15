"""The OpenFIGI identity cross-check (plan T399, #3437; owner 2026-11-14).

A read-only check, never a source: it compares what the store says about each
security id (its CIK, its listing, its successor chain) with what OpenFIGI said
when it was asked (the `openfigi_types` observation). Nothing here writes, and
no universe, signal, backtest, repair or ingest path may import it (the source
scan in `tests/lookahead/test_check_only_inputs.py`).

**Which observation.** The latest `openfigi_types` row per id known at `t`.
Every comparison reads the store as it stood at the observation's exact
`known_at` (`listings_as_of`, `securities_as_of`, `live_chains_as_of` at that
instant), never as it is now and never at the end of that day, so a revision
stamped later on the same day is not seen. A comparison between two ids reads
the store at the later of their two `known_at` values, the first instant both
observations existed.

**Kinds** (one row per disagreement, none for an agreement):

- `share_class_same_cik`: two ids under one CIK that OpenFIGI puts in one
  share class (a possible duplicate identity).
- `successor_share_class_differs`: two ids joined by a live successor chain
  that OpenFIGI puts in different share classes.
- `unmapped_while_listed`: an id whose listing is live at the observation's
  `known_at` and that OpenFIGI could not map (`mapped = FALSE`), with the
  listing's ticker and exchange.
- `figi_shared_across_ciks`: two ids with different CIKs whose observed
  `figi` (or `composite_figi`) is the same.

**What it cannot check.** OpenFIGI's stored `ticker` is our own query echo (the
ingest asks about our ticker and stores it back), so it says nothing about the
ticker; and `exch_code` is always `US`, so it says nothing about the venue.
Neither is compared. Only the FIGI, composite FIGI and share-class FIGI, the
mapped flag and the security type carry independent information.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import last_completed_session
from tradepartner.config import Settings
from tradepartner.store.delistings import LISTED, TRANSFERRED, listing_ends_as_of
from tradepartner.store.listing_selection import prefer_current_listing
from tradepartner.store.master import securities_as_of
from tradepartner.store.retraction import live_chains_as_of
from tradepartner.timeutil import ensure_tz_aware_utc

SHARE_CLASS_SAME_CIK = "share_class_same_cik"
SUCCESSOR_SHARE_CLASS_DIFFERS = "successor_share_class_differs"
UNMAPPED_WHILE_LISTED = "unmapped_while_listed"
FIGI_SHARED_ACROSS_CIKS = "figi_shared_across_ciks"

#: Every kind, in report order.
KINDS: tuple[str, ...] = (
    SHARE_CLASS_SAME_CIK,
    SUCCESSOR_SHARE_CLASS_DIFFERS,
    UNMAPPED_WHILE_LISTED,
    FIGI_SHARED_ACROSS_CIKS,
)

#: What the check states it cannot see (module docstring); health prints it.
CANNOT_CHECK: tuple[str, ...] = (
    "ticker: OpenFIGI's stored ticker is our own query echo",
    "exchange: OpenFIGI's exch_code is always US",
)

IDENTITY_SCHEMA: dict[str, Any] = {
    "kind": pl.Utf8,
    "security_id": pl.Utf8,
    "other_id": pl.Utf8,
    "session": pl.Date,
    "field": pl.Utf8,
    "value": pl.Utf8,
    "other_value": pl.Utf8,
    "cik": pl.Utf8,
    "other_cik": pl.Utf8,
    "ticker": pl.Utf8,
    "exchange": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
}

_OBSERVATIONS_SQL = """
    SELECT security_id, figi, composite_figi, share_class_figi, mapped, known_at
    FROM openfigi_types WHERE known_at <= ? ORDER BY known_at, security_id
"""


@dataclass(frozen=True)
class _Observation:
    security_id: str
    figi: str
    composite_figi: str
    share_class_figi: str
    mapped: bool
    known_at: datetime


class _AsOf:
    """The store read at one instant, each read made once (`t` is the
    observation's `known_at`, or the later of two)."""

    def __init__(self, conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
        self._conn = conn
        self._settings = settings
        self._ciks: dict[datetime, dict[str, str]] = {}
        self._chains: dict[datetime, frozenset[tuple[str, str]]] = {}

    def cik(self, at: datetime, security_id: str) -> str | None:
        """The id's CIK in the `securities` row known at `at`, else `None`."""
        known = self._ciks.setdefault(at, {})
        if security_id not in known:
            frame = securities_as_of(self._conn, at, [security_id])
            known[security_id] = frame["cik"][0] if frame.height else ""
        return known[security_id] or None

    def chained(self, at: datetime) -> frozenset[tuple[str, str]]:
        """The `(predecessor, successor)` pairs of the chains live at `at`."""
        if at not in self._chains:
            self._chains[at] = frozenset(
                (row["predecessor_id"], row["successor_id"])
                for row in live_chains_as_of(self._conn, at)
            )
        return self._chains[at]

    def live_listing(self, at: datetime, security_id: str) -> dict[str, Any] | None:
        """The id's current listing at `at` when it covers the last completed
        session then (`listings_as_of` through `listing_ends_as_of`), else
        `None`."""
        session = last_completed_session(at)
        held: dict[str, Any] | None = None
        ends = listing_ends_as_of(self._conn, at, self._settings, [security_id])
        for row in ends.iter_rows(named=True):
            if row["valid_from"] > session:
                continue
            if prefer_current_listing(row, held, session):
                held = row
        if held is None or not _covers(held, session):
            return None
        return held


def _covers(row: dict[str, Any], session: date) -> bool:
    """Whether a `listing_ends_as_of` row covers `session` (as `health`)."""
    if row["valid_from"] > session:
        return False
    if row["status"] == LISTED:
        return True
    end = row["end_session"]
    if end is None:
        return bool(row["status"] == TRANSFERRED)
    return bool(end >= session)


def _latest_observations(conn: duckdb.DuckDBPyConnection, t: datetime) -> dict[str, _Observation]:
    """The latest `openfigi_types` row per id known at `t`."""
    present = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = 'openfigi_types'"
    ).fetchone()
    if present is None or not present[0]:
        return {}
    latest: dict[str, _Observation] = {}
    for sid, figi, composite, share_class, mapped, known_at in conn.execute(
        _OBSERVATIONS_SQL, [t]
    ).fetchall():
        latest[sid] = _Observation(
            sid,
            figi,
            composite,
            share_class,
            bool(mapped),
            ensure_tz_aware_utc(known_at, field_name="known_at"),
        )
    return latest


def _row(
    kind: str,
    at: datetime,
    security_id: str,
    field: str,
    value: str,
    *,
    other_id: str = "",
    other_value: str = "",
    cik: str = "",
    other_cik: str = "",
    ticker: str = "",
    exchange: str = "",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "security_id": security_id,
        "other_id": other_id,
        "session": last_completed_session(at),
        "field": field,
        "value": value,
        "other_value": other_value,
        "cik": cik,
        "other_cik": other_cik,
        "ticker": ticker,
        "exchange": exchange,
        "known_at": at,
    }


def _pairs(ids: list[str], key: dict[str, str]) -> list[tuple[str, str]]:
    """Every sorted pair of `ids` whose non-empty `key` value is equal."""
    by_value: dict[str, list[str]] = {}
    for sid in ids:
        if key[sid]:
            by_value.setdefault(key[sid], []).append(sid)
    return [
        (a, b) for group in by_value.values() for i, a in enumerate(group) for b in group[i + 1 :]
    ]


def figi_identity_rows(
    conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings
) -> tuple[int, pl.DataFrame]:
    """`(observations, rows)` at `t`: how many ids have an observation known at
    `t` (mapped or not), and one row per disagreement (module docstring), sorted
    by kind order, then `security_id`, `other_id`, `field`. Read-only."""
    t = ensure_tz_aware_utc(t, field_name="t")
    latest = _latest_observations(conn, t)
    asof = _AsOf(conn, settings)
    rows: list[dict[str, Any]] = []
    mapped_ids = sorted(sid for sid, obs in latest.items() if obs.mapped)

    # (a) one share class under one CIK; successors in different share classes.
    share = {sid: latest[sid].share_class_figi for sid in mapped_ids}
    for a, b in _pairs(mapped_ids, share):
        at = max(latest[a].known_at, latest[b].known_at)
        cik_a, cik_b = asof.cik(at, a), asof.cik(at, b)
        if cik_a is not None and cik_a == cik_b:
            rows.append(
                _row(
                    SHARE_CLASS_SAME_CIK,
                    at,
                    a,
                    "share_class_figi",
                    share[a],
                    other_id=b,
                    other_value=share[b],
                    cik=cik_a,
                    other_cik=cik_b,
                )
            )
    candidates = (
        conn.execute(
            "SELECT DISTINCT predecessor_id, successor_id FROM successor_chains "
            "WHERE known_at <= ?",
            [t],
        ).fetchall()
        if _has_table(conn, "successor_chains")
        else []
    )
    for predecessor, successor in candidates:
        if predecessor not in share or successor not in share:
            continue
        if not share[predecessor] or not share[successor]:
            continue
        if share[predecessor] == share[successor]:
            continue
        at = max(latest[predecessor].known_at, latest[successor].known_at)
        if (predecessor, successor) not in asof.chained(at):
            continue
        rows.append(
            _row(
                SUCCESSOR_SHARE_CLASS_DIFFERS,
                at,
                successor,
                "share_class_figi",
                share[successor],
                other_id=predecessor,
                other_value=share[predecessor],
                cik=asof.cik(at, successor) or "",
                other_cik=asof.cik(at, predecessor) or "",
            )
        )

    # (b) listed but unmapped; one FIGI under two CIKs.
    for sid in sorted(latest):
        obs = latest[sid]
        if obs.mapped:
            continue
        listing = asof.live_listing(obs.known_at, sid)
        if listing is None:
            continue
        rows.append(
            _row(
                UNMAPPED_WHILE_LISTED,
                obs.known_at,
                sid,
                "mapped",
                "FALSE",
                cik=asof.cik(obs.known_at, sid) or "",
                ticker=listing["ticker"],
                exchange=listing["exchange"],
            )
        )
    for field in ("figi", "composite_figi"):
        values = {sid: getattr(latest[sid], field) for sid in mapped_ids}
        for a, b in _pairs(mapped_ids, values):
            at = max(latest[a].known_at, latest[b].known_at)
            cik_a, cik_b = asof.cik(at, a), asof.cik(at, b)
            if cik_a is None or cik_b is None or cik_a == cik_b:
                continue
            rows.append(
                _row(
                    FIGI_SHARED_ACROSS_CIKS,
                    at,
                    a,
                    field,
                    values[a],
                    other_id=b,
                    other_value=values[b],
                    cik=cik_a,
                    other_cik=cik_b,
                )
            )
    order = {kind: i for i, kind in enumerate(KINDS)}
    rows.sort(key=lambda r: (order[r["kind"]], r["security_id"], r["other_id"], r["field"]))
    return len(latest), pl.DataFrame(rows, schema=IDENTITY_SCHEMA)


def _has_table(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    found = conn.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE table_name = ?", [name]
    ).fetchone()
    return found is not None and bool(found[0])
