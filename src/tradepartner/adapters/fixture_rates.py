"""In-memory `RateSource` for tests (data-foundation spec, Amendment 2026-10-11
(#1498), "Fixture"; ADR 0020 point 5: nothing recorded from FRED, the fixture is
generated).

Built from `RateObservation` records, or from the generated
`tests/fixtures/universe/rates.csv` (`from_csv`), whose table-level rows it
reads back as observations: `series`, `observation_date`, `value` and
`vintage_date` only. The CSV's `known_at`, `ingested_at`, `source` and
`provenance` are never served, so the ingest's own rules, not this adapter's,
make the rows.

`observations(series, realtime_start, observation_start)` answers as FRED's
`output_type=1` request does with `realtime_end` open: for each observation
dated on or after `observation_start`, every value whose real-time period
(from its vintage to the day before the next vintage's, or open) reaches
`realtime_start` or later, with the vintage reported as the later of its own
and `realtime_start` (the lower bound the spec describes: a value first served
before the window reports the window's start). A value superseded before
`realtime_start` is not returned. `missing` lists the (series,
observation_date) pairs the source holds as a missing value (FRED's `.`): they
are never records, and each one inside the request counts in
`missing_values`.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from tradepartner.adapters.rates import RateObservation, RateSource

_CSV_COLUMNS: tuple[str, ...] = ("series", "observation_date", "value", "vintage_date")


class FixtureRateSource(RateSource):
    """A `RateSource` over records held in memory (module docstring)."""

    def __init__(
        self,
        observations: Iterable[RateObservation] = (),
        *,
        missing: Iterable[tuple[str, date]] = (),
        name: str = "fred",
    ) -> None:
        super().__init__()
        self.name = name
        by_key: defaultdict[tuple[str, date], list[RateObservation]] = defaultdict(list)
        for record in observations:
            by_key[(record.series, record.observation_date)].append(record)
        self._versions: dict[tuple[str, date], list[RateObservation]] = {}
        for key, records in by_key.items():
            records.sort(key=lambda r: r.vintage_date)
            vintages = [r.vintage_date for r in records]
            if len(set(vintages)) != len(vintages):
                raise ValueError(f"two values of {key[0]} {key[1].isoformat()} share a vintage")
            self._versions[key] = records
        self._missing = sorted(set(missing))
        for series, day in self._missing:
            if (series, day) in self._versions:
                raise ValueError(f"{series} {day.isoformat()} is both a value and missing")

    @classmethod
    def from_csv(cls, path: Path, *, name: str = "fred") -> FixtureRateSource:
        """The observations of a table-level `rates.csv` (module docstring)."""
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            absent = [c for c in _CSV_COLUMNS if c not in (reader.fieldnames or ())]
            if absent:
                raise ValueError(f"{path} lacks column(s) {absent}")
            records = [
                RateObservation(
                    series=row["series"],
                    observation_date=date.fromisoformat(row["observation_date"]),
                    value=float(row["value"]),
                    vintage_date=date.fromisoformat(row["vintage_date"]),
                )
                for row in reader
            ]
        return cls(records, name=name)

    def observations(
        self, series: str, realtime_start: date, observation_start: date
    ) -> list[RateObservation]:
        """The values FRED would return for this request (module docstring),
        sorted by `(observation_date, vintage_date)`; sets `missing_values`."""
        out: list[RateObservation] = []
        for (key_series, observation_date), versions in self._versions.items():
            if key_series != series or observation_date < observation_start:
                continue
            for i, record in enumerate(versions):
                following = versions[i + 1].vintage_date if i + 1 < len(versions) else None
                if following is not None and following - timedelta(days=1) < realtime_start:
                    continue
                vintage = max(record.vintage_date, realtime_start)
                out.append(RateObservation(record.series, observation_date, record.value, vintage))
        out.sort(key=lambda r: (r.observation_date, r.vintage_date))
        self.missing_values = sum(
            1 for s, day in self._missing if s == series and day >= observation_start
        )
        return out
