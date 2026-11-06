"""The rotating drift check of the incremental EDGAR ingest (#2629, T321).

A rule changed in code without a `BUILD_RULES_VERSION` bump would leave the
untouched CIKs stale, because a nightly run rebuilds only the touched ones.
Each night therefore rebuilds a rotating share of the untouched issuer CIKs
read-only and compares the result with the store (spec amendment 2026-10-30,
"A rule changed in code without a bump").

Everything here is pure over explicit inputs: the caller lists the CIKs, runs
the read-only rebuild, reads the stored rows and writes the run message.

- **Rotation.** :func:`rotation_share` gives each CIK one slot in
  ``range(rotation_days)`` from a hash of ``(seed, cik)``, and the night's
  share is the CIKs whose slot is that night's. Nights are numbered in XNYS
  sessions (:func:`session_night`), not calendar days: a run on a weekend or
  holiday repeats the previous session's share, and a 28-day count of
  calendar days would never reach the slots that fall on weekends. Any
  ``rotation_days`` consecutive sessions therefore cover every CIK once,
  whatever CIKs come and go between them, and the same seed and night give
  the same share on any machine. A session the job misses is a share skipped
  until the next cycle.
- **Comparison.** :func:`compare_rows` is the writer's own decision. For a
  table with a natural key in `keys`, the stored side is reduced to each
  key's latest live revision (latest ``known_at`` then ``ingested_at``; a
  retracted latest revision is no row), and the two sides are compared on
  their value columns, ignoring ``known_at``, ``ingested_at`` and
  ``retracted``: a revision history, a retraction or a row stamped at an
  earlier run is not drift, a changed value or a missing or extra key is.
  A table with no key is compared as a multiset of rows minus
  :data:`IGNORED_COLUMNS`.
- **Where the figures go.** The driver (T323) writes ``incremental_drift``
  and ``index_stamp_changed`` (both, zero included) on the ``edgar_index``
  run row's message; ``health`` reads the latest run of ``edgar`` or
  ``edgar_index`` that carries them.
- **Report.** :class:`DriftReport` counts the CIKs that differ and names up to
  :data:`DRIFT_NAMED_MAX` of them (lowest CIK first); :func:`drift_message`
  writes the run-message figures ``health`` reads.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from tradepartner.calendar import all_sessions

#: At most this many drifted CIKs are named in the run message (spec).
DRIFT_NAMED_MAX = 10
#: Columns that record when a row was written, not what it says.
IGNORED_COLUMNS: frozenset[str] = frozenset({"ingested_at"})

#: ``cik -> table -> rows``: the shape of both sides of a comparison.
RowsByCik = Mapping[str, Mapping[str, Iterable[Mapping[str, Any]]]]


def rotation_slot(cik: str, rotation_days: int, seed: str) -> int:
    """The night, in ``range(rotation_days)``, on which `cik` is checked."""
    if rotation_days < 1:
        raise ValueError(f"rotation_days must be at least 1, got {rotation_days}")
    digest = hashlib.sha256(f"{seed}:{cik}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % rotation_days


def session_night(on: date) -> int:
    """The rotation's night number for `on`: the index of the XNYS session
    at or before `on` (so a weekend or holiday run repeats the last
    session's night)."""
    return max(bisect_right(all_sessions(), on) - 1, 0)


def rotation_share(
    ciks: Iterable[str], night: int, rotation_days: int, seed: str
) -> tuple[str, ...]:
    """The CIKs of `ciks` to rebuild read-only on `night` (a
    :func:`session_night`), sorted. Pass the untouched issuer CIKs; a touched
    CIK is rebuilt for real that night. Pure."""
    night = night % rotation_days if rotation_days >= 1 else 0
    return tuple(sorted({c for c in ciks if rotation_slot(c, rotation_days, seed) == night}))


#: Columns that stamp a revision rather than say anything.
STAMP_COLUMNS: frozenset[str] = frozenset({"known_at", "ingested_at", "retracted"})


def _freeze(row: Mapping[str, Any], ignore: frozenset[str]) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted((k, v) for k, v in row.items() if k not in ignore))


def _normalized(
    rows: Iterable[Mapping[str, Any]], ignore: frozenset[str]
) -> Counter[tuple[tuple[str, Any], ...]]:
    return Counter(_freeze(row, ignore) for row in rows)


def _live_by_key(
    rows: Iterable[Mapping[str, Any]], key: tuple[str, ...], ignore: frozenset[str]
) -> set[tuple[tuple[str, Any], ...]]:
    """The latest revision per natural key, dropping a retracted one."""
    latest: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in rows:
        k = tuple(row[c] for c in key)
        stamp = (row["known_at"], row["ingested_at"])
        held = latest.get(k)
        if held is None or stamp >= (held["known_at"], held["ingested_at"]):
            latest[k] = row
    return {
        _freeze(row, ignore | STAMP_COLUMNS) for row in latest.values() if not row.get("retracted")
    }


@dataclass(frozen=True)
class DriftReport:
    """The outcome of one night's comparison.

    `checked` is the CIKs compared and `drifted` every one that differs,
    sorted. `named` is the first :data:`DRIFT_NAMED_MAX` of them.
    """

    checked: int
    drifted: tuple[str, ...]

    @property
    def count(self) -> int:
        """How many CIKs differ."""
        return len(self.drifted)

    @property
    def named(self) -> tuple[str, ...]:
        """The CIKs the run message names."""
        return self.drifted[:DRIFT_NAMED_MAX]


def compare_rows(
    stored: RowsByCik,
    rebuilt: RowsByCik,
    ignore: frozenset[str] = IGNORED_COLUMNS,
    keys: Mapping[str, tuple[str, ...]] | None = None,
) -> DriftReport:
    """Compare the stored rows of each CIK with the read-only rebuild's
    (module docstring, "Comparison"). `keys` maps a table to its natural key;
    the rebuild's rows are one current row per key. A CIK is checked when
    either side holds it. Pure.
    """
    keys = keys or {}

    def differs(
        table: str, mine: Iterable[Mapping[str, Any]], theirs: Iterable[Mapping[str, Any]]
    ) -> bool:
        if table in keys:
            return _live_by_key(mine, keys[table], ignore) != _live_by_key(
                theirs, keys[table], ignore
            )
        return _normalized(mine, ignore) != _normalized(theirs, ignore)

    ciks = sorted(stored.keys() | rebuilt.keys())
    drifted: list[str] = []
    for cik in ciks:
        mine, theirs = stored.get(cik, {}), rebuilt.get(cik, {})
        if any(
            differs(table, mine.get(table, ()), theirs.get(table, ()))
            for table in mine.keys() | theirs.keys()
        ):
            drifted.append(cik)
    return DriftReport(checked=len(ciks), drifted=tuple(drifted))


def drift_message(report: DriftReport) -> str:
    """The run-message figures of `report`: ``incremental_drift: N`` and,
    while N is positive, ``incremental_drift_ciks: a b c`` naming up to
    :data:`DRIFT_NAMED_MAX` CIKs (the ``health`` block parses both)."""
    out = f"incremental_drift: {report.count}"
    if report.named:
        out += f", incremental_drift_ciks: {' '.join(report.named)}"
    return out
