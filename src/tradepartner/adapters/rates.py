"""`RateSource` interface, the `RateObservation` record and the rates stamp
rule (data-foundation spec req 18 and "Data / interfaces" > Amendment
2026-10-11 (#1498); ADR 0020).

Three things live here, and nothing else:

- **The record.** `RateObservation(series, observation_date, value,
  vintage_date)`, frozen and validated at construction: every field set,
  both dates bare `date`s (a `datetime`, naive or aware, is refused: the
  source gives dates and no time), `value` a finite number in the series'
  own unit (DTB3: percent, discount basis; nothing is converted here), and
  `vintage_date` never before `observation_date` (a value cannot be served
  before the day it describes). It carries no `known_at`: **adapters stamp
  nothing** (ADR 0003 rule 2: the timing rule lives in the core). A missing
  value (FRED's `.`) is never a record; the adapter counts it
  (`RateSource.missing_values`).
- **The stamp rule.** `rate_known_at(vintage_date)` = the close of the first
  XNYS session strictly after the vintage date (req 5's date-only rule, the
  `release` provenance): an observation for business day D, served from
  D+1, is stamped close(D+2). The ingest applies it; `health --check`
  recomputes it from the stored `vintage_date`.
- **The interface.** `RateSource.observations(series, realtime_start,
  observation_start)`: every observation of `series` dated on or after
  `observation_start` and every value of it current at some point on or
  after `realtime_start`, each with its vintage. A source whose real-time
  window starts at the request's `realtime_start` reports that date as the
  vintage of a value first served earlier (FRED's lower bound), which can
  only make the stamp later, never earlier.
"""

from __future__ import annotations

import abc
import math
from dataclasses import dataclass
from datetime import date, datetime

from tradepartner.calendar import next_session, session_close


def _bare_date(record: object, field_name: str) -> None:
    value = getattr(record, field_name)
    # `datetime` is a subclass of `date`: refuse it explicitly, naive or aware.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TypeError(f"{field_name} must be a date (not a datetime), got {value!r}")


@dataclass(frozen=True)
class RateObservation:
    """One published value of a rate series for one observation date, with
    the date the source first served it (module docstring)."""

    series: str
    observation_date: date
    value: float
    vintage_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.series, str) or not self.series:
            raise ValueError(f"series must be a non-empty string, got {self.series!r}")
        _bare_date(self, "observation_date")
        _bare_date(self, "vintage_date")
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            raise TypeError(f"value must be a number, got {self.value!r}")
        if not math.isfinite(self.value):
            raise ValueError(f"value must be finite, got {self.value!r}")
        object.__setattr__(self, "value", float(self.value))
        if self.vintage_date < self.observation_date:
            raise ValueError(
                f"vintage_date {self.vintage_date.isoformat()} is before observation_date "
                f"{self.observation_date.isoformat()} for {self.series}"
            )


def rate_known_at(vintage_date: date) -> datetime:
    """The `known_at` of a rate row: `session_close(next_session(vintage_date))`,
    the tz-aware UTC close of the first XNYS session strictly after the vintage
    date (module docstring). A session, a weekend or a holiday vintage date all
    stamp at the next session's close. Raises `TypeError` on a `datetime`."""
    if isinstance(vintage_date, datetime) or not isinstance(vintage_date, date):
        raise TypeError(f"vintage_date must be a date (not a datetime), got {vintage_date!r}")
    return session_close(next_session(vintage_date))


class RateSource(abc.ABC):
    """Rate observations, as parsed records (spec "Interfaces").

    `missing_values` is the number of missing values (FRED's `.`) the last
    `observations` call met and dropped; the ingest reads it into the run
    row's `rates_missing_values`."""

    #: The `source` column the ingest writes for this adapter's rows (`fred`).
    name: str

    def __init__(self) -> None:
        self.missing_values = 0

    @abc.abstractmethod
    def observations(
        self, series: str, realtime_start: date, observation_start: date
    ) -> list[RateObservation]:
        """Every value of `series` for an observation date on or after
        `observation_start` that was current on or after `realtime_start`,
        sorted by `(observation_date, vintage_date)` (module docstring)."""
