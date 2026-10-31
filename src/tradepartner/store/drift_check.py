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
  share is the CIKs whose slot is that night's. Any ``rotation_days``
  consecutive nights therefore cover every CIK once, whatever CIKs come and
  go between them, and the same seed and night give the same share on any
  machine.
- **Comparison.** :func:`compare_rows` compares, per CIK and table, the
  multiset of stored rows with the multiset of rebuilt rows. ``ingested_at``
  is ignored: it is the clock of the run that wrote the row, and a rebuild
  that matches the store in every fact column has not drifted. A CIK with
  rows on one side only has drifted.
- **Report.** :class:`DriftReport` counts the CIKs that differ and names up to
  :data:`DRIFT_NAMED_MAX` of them (lowest CIK first); :func:`drift_message`
  writes the run-message figures ``health`` reads.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

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


def rotation_share(ciks: Iterable[str], on: date, rotation_days: int, seed: str) -> tuple[str, ...]:
    """The CIKs of `ciks` to rebuild read-only on night `on` (module
    docstring), sorted. Pass the untouched issuer CIKs; a touched CIK is
    rebuilt for real that night. `on` is the run's session date, the day
    number its position in the rotation. Pure."""
    night = on.toordinal() % rotation_days if rotation_days >= 1 else 0
    return tuple(sorted({c for c in ciks if rotation_slot(c, rotation_days, seed) == night}))


def _normalized(
    rows: Iterable[Mapping[str, Any]], ignore: frozenset[str]
) -> Counter[tuple[tuple[str, Any], ...]]:
    return Counter(tuple(sorted((k, v) for k, v in row.items() if k not in ignore)) for row in rows)


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
) -> DriftReport:
    """Compare the stored rows of each CIK with the read-only rebuild's.

    A CIK is checked when either side holds it. It drifted when any table's
    rows differ as a multiset once the `ignore` columns are dropped. Pure.
    """
    ciks = sorted(stored.keys() | rebuilt.keys())
    drifted: list[str] = []
    for cik in ciks:
        mine, theirs = stored.get(cik, {}), rebuilt.get(cik, {})
        if any(
            _normalized(mine.get(table, ()), ignore) != _normalized(theirs.get(table, ()), ignore)
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
