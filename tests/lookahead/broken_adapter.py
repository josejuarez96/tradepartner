"""A deliberately broken `PriceSource` for the no-look-ahead suite (spec
"Look-ahead": "the broken adapter proves each check has teeth"; plan T10).

`BrokenPriceSource(violation)` replays the fixture universe like
`FixturePriceSource`, with exactly one violation injected: the five the spec
names, plus an adjusted re-fetch as a second form of pre-adjusted prices:

- `EARLY_KNOWN_AT`: SEC_SPLIT_PLAIN's bars are stamped at the session
  open, and its split a week before the proxy with no announcement (#83).
- `PRE_ADJUSTED_PRICES`: every bar before a split's ex-date comes back
  already split-adjusted, as a vendor's adjusted feed would.
- `PRE_ADJUSTED_ON_REFETCH`: first fetches are raw, but a later re-fetch
  from an adjusted feed rewrites every pre-split bar as a correctly stamped
  revision (quant-auditor on #126: the likelier real failure).
- `TICKER_RESOLUTION`: an id the adapter does not know is looked up as a
  ticker in `listings.csv` instead of raising `UnknownSecurityIdError`.
- `KNOWN_AT_AFTER_INGESTED_AT`: SEC_SPLIT_PLAIN's last bar was fetched an
  hour before its session closed (a partial intraday bar), yet stamped at
  the close.
- `BACK_DATED_REVISION`: SEC_SPLIT_BACKFILLED's re-fetched bar keeps the
  original bar's `known_at` instead of its own `ingested_at`.

A `PriceSource` record carries no `ingested_at` (ingest stamps it), so the
suite checks an adapter's **ingest history**: each record paired with the
`ingested_at` it was written at. For the fixture that pairing is the
fixture CSVs themselves, which are the recorded ingest log.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import cache
from pathlib import Path

from tradepartner.adapters.fixture_prices import FixturePriceSource
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    UnknownSecurityIdError,
    action_first_seen_known_at,
    check_request,
)
from tradepartner.calendar import session_close, session_open

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
ALL_TIME = (date(2000, 1, 1), date(2030, 12, 31))

_EARLY_ID = "SEC_SPLIT_PLAIN"
_REVISED_BAR_ID = "SEC_SPLIT_BACKFILLED"


@dataclass(frozen=True)
class Ingested:
    """One record as the store received it: the record plus its `ingested_at`."""

    record: Bar | CorporateAction
    ingested_at: datetime


class Violation(StrEnum):
    """The five violations the spec's broken adapter must exhibit."""

    EARLY_KNOWN_AT = "early_known_at"
    PRE_ADJUSTED_PRICES = "pre_adjusted_prices"
    PRE_ADJUSTED_ON_REFETCH = "pre_adjusted_on_refetch"
    TICKER_RESOLUTION = "ticker_resolution"
    KNOWN_AT_AFTER_INGESTED_AT = "known_at_after_ingested_at"
    BACK_DATED_REVISION = "back_dated_revision"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def security_ids(universe_dir: Path = UNIVERSE_DIR) -> list[str]:
    """Every `security_id` in the fixture's `securities.csv`, sorted."""
    return sorted({row["security_id"] for row in _read_csv(universe_dir / "securities.csv")})


def tickers(universe_dir: Path = UNIVERSE_DIR) -> dict[str, str]:
    """Ticker -> the first `security_id` (by id) ever listed under it."""
    by_ticker: dict[str, str] = {}
    for row in sorted(
        _read_csv(universe_dir / "listings.csv"), key=lambda r: (r["ticker"], r["security_id"])
    ):
        by_ticker.setdefault(row["ticker"], row["security_id"])
    return by_ticker


def fixture_history(
    source: PriceSource, ids: Sequence[str], universe_dir: Path = UNIVERSE_DIR
) -> list[Ingested]:
    """`source`'s bars and actions for `ids`, each paired with the
    `ingested_at` the fixture CSVs record for it (matched on natural key and
    `known_at`). A record the CSVs do not hold raises `KeyError`."""
    log: dict[tuple[object, ...], datetime] = {}
    for row in _read_csv(universe_dir / "prices_daily.csv"):
        key = ("bar", row["security_id"], date.fromisoformat(row["session"]))
        log[(*key, datetime.fromisoformat(row["known_at"]))] = datetime.fromisoformat(
            row["ingested_at"]
        )
    for row in _read_csv(universe_dir / "corporate_actions.csv"):
        action_key = (
            "action",
            row["security_id"],
            row["action_type"],
            date.fromisoformat(row["ex_date"]),
        )
        log[(*action_key, datetime.fromisoformat(row["known_at"]))] = datetime.fromisoformat(
            row["ingested_at"]
        )
    history = [
        Ingested(bar, log[("bar", *bar.key, bar.known_at)]) for bar in source.bars(ids, *ALL_TIME)
    ]
    history += [
        Ingested(action, log[("action", *action.key, action.known_at)])
        for action in source.corporate_actions(ids, *ALL_TIME)
    ]
    return history


def _early_known_at(history: list[Ingested]) -> list[Ingested]:
    out = []
    for item in history:
        record = item.record
        if record.security_id == _EARLY_ID and isinstance(record, Bar):
            record = replace(record, known_at=session_open(record.session))
        elif record.security_id == _EARLY_ID and isinstance(record, CorporateAction):
            proxy = action_first_seen_known_at(record.ex_date)
            record = replace(record, known_at=proxy - timedelta(days=7), announced_at=None)
        out.append(Ingested(record, item.ingested_at))
    return out


def _pre_adjusted_prices(history: list[Ingested]) -> list[Ingested]:
    splits = [
        item.record
        for item in history
        if isinstance(item.record, CorporateAction) and item.record.action_type is ActionType.SPLIT
    ]
    out = []
    for item in history:
        record = item.record
        if isinstance(record, Bar):
            for split in splits:
                if split.security_id == record.security_id and record.session < split.ex_date:
                    r = split.ratio_or_amount
                    record = replace(
                        record,
                        open=record.open / r,
                        high=record.high / r,
                        low=record.low / r,
                        close=record.close / r,
                        volume=round(record.volume * r),
                    )
        out.append(Ingested(record, item.ingested_at))
    return out


#: When the adjusted re-fetch runs: after every fixture `known_at`.
_REFETCH_AT = datetime(2026, 2, 2, 21, 0, tzinfo=UTC)


def _pre_adjusted_on_refetch(history: list[Ingested]) -> list[Ingested]:
    adjusted = _pre_adjusted_prices(history)
    revisions = [
        Ingested(replace(new.record, known_at=_REFETCH_AT), _REFETCH_AT)
        for old, new in zip(history, adjusted, strict=True)
        if new.record != old.record
    ]
    return history + revisions


def _known_at_after_ingested_at(history: list[Ingested]) -> list[Ingested]:
    last = max(
        (i for i in history if isinstance(i.record, Bar) and i.record.security_id == _EARLY_ID),
        key=lambda i: i.record.known_at,
    )
    assert isinstance(last.record, Bar)
    fetched = session_close(last.record.session) - timedelta(hours=1)
    return [Ingested(i.record, fetched) if i is last else i for i in history]


def _back_dated_revision(history: list[Ingested]) -> list[Ingested]:
    bars = [i for i in history if isinstance(i.record, Bar)]
    revised = [
        i
        for i in bars
        if i.record.security_id == _REVISED_BAR_ID
        and sum(1 for j in bars if j.record.key == i.record.key) > 1
    ]
    original, revision = sorted(revised, key=lambda i: i.ingested_at)
    back_dated = Ingested(
        replace(revision.record, known_at=original.record.known_at), revision.ingested_at
    )
    return [back_dated if i is revision else i for i in history]


_INJECT: dict[Violation, Callable[[list[Ingested]], list[Ingested]]] = {
    Violation.EARLY_KNOWN_AT: _early_known_at,
    Violation.PRE_ADJUSTED_PRICES: _pre_adjusted_prices,
    Violation.PRE_ADJUSTED_ON_REFETCH: _pre_adjusted_on_refetch,
    Violation.TICKER_RESOLUTION: lambda history: history,
    Violation.KNOWN_AT_AFTER_INGESTED_AT: _known_at_after_ingested_at,
    Violation.BACK_DATED_REVISION: _back_dated_revision,
}


@cache
def _clean_history(universe_dir: Path) -> tuple[Ingested, ...]:
    """The fixture's ingest history, loaded once per directory: building
    the fixture adapter validates every row against the calendar."""
    source = FixturePriceSource(universe_dir)
    return tuple(fixture_history(source, security_ids(universe_dir), universe_dir))


class BrokenPriceSource(PriceSource):
    """The fixture universe with one `Violation` injected; see the module
    docstring for what each one does."""

    def __init__(self, violation: Violation, universe_dir: Path = UNIVERSE_DIR) -> None:
        self.violation = violation
        self._known_ids = frozenset(security_ids(universe_dir))
        self._tickers = tickers(universe_dir)
        self._history = _INJECT[violation](list(_clean_history(universe_dir)))

    def history(self) -> list[Ingested]:
        """The ingest history with the violation injected."""
        return list(self._history)

    def _resolve(self, security_ids: Sequence[str], start: date, end: date) -> set[str]:
        resolved = set()
        for sid in check_request(security_ids, start, end):
            if sid in self._known_ids:
                resolved.add(sid)
            elif self.violation is Violation.TICKER_RESOLUTION and sid in self._tickers:
                resolved.add(self._tickers[sid])
            else:
                raise UnknownSecurityIdError(f"unknown security_id {sid!r}")
        return resolved

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        """Bars from the injected history, sorted like the fixture adapter's."""
        ids = self._resolve(security_ids, start, end)
        return sorted(
            (
                i.record
                for i in self._history
                if isinstance(i.record, Bar)
                and i.record.security_id in ids
                and start <= i.record.session <= end
            ),
            key=lambda b: (b.key, b.known_at),
        )

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        """Actions from the injected history, sorted like the fixture adapter's."""
        ids = self._resolve(security_ids, start, end)
        return sorted(
            (
                i.record
                for i in self._history
                if isinstance(i.record, CorporateAction)
                and i.record.security_id in ids
                and start <= i.record.ex_date <= end
            ),
            key=lambda a: (a.key, a.known_at),
        )
