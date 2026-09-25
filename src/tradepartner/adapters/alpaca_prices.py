"""Alpaca `PriceSource`: pure parsers over raw `alpaca_raw` payloads, plus a
thin adapter wiring them to the raw client (spec req 6, plan T12).

**Resolution through the master.** Alpaca keys everything by symbol; the
store keys by `security_id` (spec req 3: adapters never resolve by bare
ticker). `ListingResolver` maps `(ticker, session)` to the security whose
listing carried that ticker on that session, from the master's `listings`
rows (`security_id`, `ticker`, `valid_from`):

- a security holds a ticker from a listing's `valid_from` until its next
  listing with a different ticker (a ticker change), so one security has
  one span per ticker it traded under;
- a ticker reused by another company belongs to whichever span started
  most recently on or before the session, so an old company's bars stop
  resolving once the new company's listing starts;
- two spans of one ticker starting on the same day are ambiguous and raise;
- a span is **contested** when its ticker is later taken by another
  security that arrived at it through a rename (Roundhill's `META` ETF,
  then Facebook's `FB` -> `META`). Alpaca serves a renamed company's history
  under its new symbol as well (#104), so rows under that ticker on the old
  holder's dates may be the renamed company's. They resolve to nothing and
  are reported, never assigned; `contested_spans` lists them.

The resolver is a key mapping built from the master, like the delisting
resolution in `store.delistings`; it never makes a record visible early,
because every record still carries its own `known_at`.

**Timing** comes only from `adapters.prices`:

- a bar for session S is known at S's close (`bar_known_at`), early close
  on a half day. The session is the New York date of the bar's timestamp,
  and a bar on a non-session raises;
- Alpaca's corporate actions carry no announcement time (#101), so every
  action is stamped at the first-seen proxy, the close of the last session
  before its ex-date (`action_first_seen_known_at`), with
  `announced_at = None`. An action resolves on that same session, the last
  one before its ex-date, so an action whose ex-date coincides with a
  ticker change belongs to the security under its old symbol.

**Extended hours.** Alpaca's SIP daily volume includes extended-hours
trades (free-data-terms research, S1), while a bar is stamped at the
16:00 close per the spec; the gap is an open owner question on PR #139.

**Actions window.** Alpaca filters corporate actions on `process_date`,
which can trail the ex-date by weeks (#101). `AlpacaPriceSource` asks for
actions processed up to `alpaca.actions_process_lag_days` after the end
of the ex-date window, and for the symbols held from the session before
its start (an action resolves on that session), then filters on ex-date.

**Raw closes.** `alpaca_raw.daily_bars` always asks for `adjustment=raw`
(ADR 0003 rule 1); prices here are the payload's values, unadjusted.

**Source per feed.** A bar's `source` is `alpaca_<feed>` from the payload's
`feed` key (`alpaca_sip`, `alpaca_iex`); a payload without a known feed
raises, so ingest can never mix feeds for a security unnoticed
(`tests/fixtures/README.md`). Actions are `alpaca`.

**Not returned as data, reported instead:**

- a zero-volume placeholder bar (`v == 0` and `n == 0`) that Alpaca can
  serve after a delisting (#104): it is not a trade and counts as missing;
- a symbol or row that resolves to no security;
- corporate-action categories this module does not store (spin-offs,
  mergers, stock dividends, name changes, ...): only splits (forward and
  reverse, ratio `new_rate / old_rate`) and cash dividends (`rate`) are
  `CorporateAction`s.

A malformed payload raises `ValueError`, never a partial record: a
missing field, a non-numeric, boolean, non-finite or non-positive rate, a
repeated key, or a placeholder whose zeros are not integers.
"""

from __future__ import annotations

import functools
import itertools
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    UnknownSecurityIdError,
    action_first_seen_known_at,
    bar_known_at,
    check_request,
)
from tradepartner.calendar import is_session, previous_session
from tradepartner.config import Settings, get_settings

_NEW_YORK = ZoneInfo("America/New_York")
_FEEDS = frozenset({"sip", "iex"})
_ACTIONS_SOURCE = "alpaca"
_SPLIT_CATEGORIES = frozenset({"forward_splits", "reverse_splits"})
_DIVIDEND_CATEGORIES = frozenset({"cash_dividends"})

Resolve = Callable[[str, date], str | None]


def _fail_closed[**P, R](parse: Callable[P, R]) -> Callable[P, R]:
    """Re-raise a malformed payload's `KeyError`, `IndexError`, `TypeError`,
    `AttributeError` or `ArithmeticError` as `ValueError` naming the parser."""

    @functools.wraps(parse)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return parse(*args, **kwargs)
        except (KeyError, IndexError, TypeError, AttributeError, ArithmeticError) as error:
            raise ValueError(f"{parse.__name__}: malformed payload: {error!r}") from error

    return wrapper


# --- resolution ----------------------------------------------------------


@dataclass(frozen=True)
class TickerSpan:
    """One security trading under one ticker from `start` until `end`
    (exclusive; `None` while no later listing of the security ends it)."""

    security_id: str
    ticker: str
    start: date
    end: date | None

    def covers(self, session: date) -> bool:
        return self.start <= session and (self.end is None or session < self.end)


class ListingResolver:
    """`(ticker, session) -> security_id` from master `listings` rows; see
    the module docstring for the rules."""

    def __init__(self, listings: Iterable[Mapping[str, Any]]) -> None:
        by_security: dict[str, list[tuple[date, str]]] = defaultdict(list)
        for row in listings:
            by_security[str(row["security_id"])].append((row["valid_from"], str(row["ticker"])))
        self._by_ticker: dict[str, list[TickerSpan]] = defaultdict(list)
        self._by_security: dict[str, list[TickerSpan]] = defaultdict(list)
        for security_id, rows in by_security.items():
            rows.sort()
            for (day, ticker), (next_day, next_ticker) in itertools.pairwise(rows):
                if day == next_day and ticker != next_ticker:
                    raise ValueError(
                        f"{security_id} lists {ticker!r} and {next_ticker!r} "
                        f"from the same day {day}"
                    )
            index = 0
            while index < len(rows):
                start, ticker = rows[index]
                index += 1
                while index < len(rows) and rows[index][1] == ticker:
                    index += 1  # the same ticker again (a second exchange): one span
                end = rows[index][0] if index < len(rows) else None
                span = TickerSpan(security_id, ticker, start, end)
                self._by_ticker[ticker].append(span)
                self._by_security[security_id].append(span)
        self._contested = frozenset(
            span
            for spans in self._by_ticker.values()
            for span in spans
            if any(
                other.security_id != span.security_id
                and other.start > span.start
                and self._renamed_into(other)
                for other in spans
            )
        )

    def _renamed_into(self, span: TickerSpan) -> bool:
        """True if `span`'s security traded under another ticker before it."""
        return any(
            earlier.start < span.start and earlier.ticker != span.ticker
            for earlier in self._by_security[span.security_id]
        )

    @property
    def contested_spans(self) -> tuple[TickerSpan, ...]:
        """Spans whose rows are not assigned (see the module docstring)."""
        return tuple(sorted(self._contested, key=lambda s: (s.ticker, s.start)))

    def resolve(self, ticker: str, session: date) -> str | None:
        """The security trading under `ticker` on `session`, or `None`."""
        live = [span for span in self._by_ticker.get(ticker, []) if span.covers(session)]
        if not live:
            return None
        latest = max(span.start for span in live)
        winners = [span for span in live if span.start == latest]
        owners = {span.security_id for span in winners}
        if len(owners) > 1:
            raise ValueError(f"ticker {ticker!r} on {session} is ambiguous: {sorted(owners)}")
        if any(span in self._contested for span in winners):
            return None
        return owners.pop()

    def symbols(self, security_id: str, start: date, end: date) -> list[str]:
        """Every ticker `security_id` traded under at some day in
        `[start, end]`, in span order; `[]` for an unknown id."""
        out: list[str] = []
        for span in self._by_security.get(security_id, []):
            overlaps = span.start <= end and (span.end is None or start < span.end)
            if overlaps and span.ticker not in out:
                out.append(span.ticker)
        return out

    def knows(self, security_id: str) -> bool:
        return security_id in self._by_security


# --- bars ------------------------------------------------------------------


def feed_source(payload: Mapping[str, Any]) -> str:
    """`alpaca_<feed>` for a bars payload, or `ValueError` if the payload
    names no known feed."""
    feed = payload.get("feed")
    if feed not in _FEEDS:
        raise ValueError(f"bars payload names no known feed: {feed!r}")
    return f"alpaca_{feed}"


def _session_of(stamp: str) -> date:
    instant = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError(f"bar timestamp {stamp!r} has no zone")
    session = instant.astimezone(_NEW_YORK).date()
    if not is_session(session):
        raise ValueError(f"bar timestamp {stamp!r} is not on an XNYS session")
    return session


def _is_int_zero(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _require_unique(keys: Sequence[tuple[Any, ...]], what: str) -> None:
    for key, following in itertools.pairwise(keys):
        if key == following:
            raise ValueError(f"payload repeats the {what} {key}")


def _rate(row: Mapping[str, Any], field: str, *, positive: bool) -> float:
    """A real, finite rate from `row` (not a bool or string); split rates
    must be positive, a dividend non-negative."""
    value = row[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be a number, got {value!r}")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field} must be finite, got {value!r}")
    if number < 0 or (positive and number == 0):
        raise ValueError(
            f"{field} must be {'positive' if positive else 'non-negative'}, got {value!r}"
        )
    return number


@dataclass(frozen=True)
class BarsParse:
    bars: tuple[Bar, ...]
    unresolved: tuple[tuple[str, date], ...]  # (symbol, session)
    placeholders: tuple[tuple[str, date], ...]  # (security_id, session)


@_fail_closed
def parse_bars(payload: Mapping[str, Any], resolve: Resolve) -> BarsParse:
    """`Bar`s from an `alpaca_raw.daily_bars` payload, each resolved with
    `resolve(symbol, session)` and known at its session's close."""
    source = feed_source(payload)
    bars: list[Bar] = []
    unresolved: list[tuple[str, date]] = []
    placeholders: list[tuple[str, date]] = []
    for symbol, rows in payload["bars"].items():
        for row in rows:
            session = _session_of(row["t"])
            security_id = resolve(symbol, session)
            if security_id is None:
                unresolved.append((symbol, session))
                continue
            if _is_int_zero(row["v"]) and _is_int_zero(row["n"]):
                placeholders.append((security_id, session))
                continue
            bars.append(
                Bar(
                    security_id=security_id,
                    session=session,
                    open=row["o"],
                    high=row["h"],
                    low=row["l"],
                    close=row["c"],
                    volume=row["v"],
                    known_at=bar_known_at(session),
                    source=source,
                )
            )
    bars.sort(key=lambda b: (b.security_id, b.session))
    _require_unique([b.key for b in bars], "bar")
    return BarsParse(tuple(bars), tuple(unresolved), tuple(placeholders))


# --- corporate actions ---------------------------------------------------------


@dataclass(frozen=True)
class ActionsParse:
    actions: tuple[CorporateAction, ...]
    unresolved: tuple[tuple[str, date], ...]  # (symbol, ex_date)
    unsupported: tuple[str, ...]  # categories present but not stored


@_fail_closed
def parse_corporate_actions(payload: Mapping[str, Any], resolve: Resolve) -> ActionsParse:
    """Splits and cash dividends from an `alpaca_raw.corporate_actions`
    payload, on the first-seen proxy, resolved on the last session before
    each ex-date."""
    actions: list[CorporateAction] = []
    unresolved: list[tuple[str, date]] = []
    unsupported: list[str] = []
    for category, rows in sorted(payload.items()):
        if category in _SPLIT_CATEGORIES:
            action_type = ActionType.SPLIT
        elif category in _DIVIDEND_CATEGORIES:
            action_type = ActionType.DIVIDEND
        else:
            if not isinstance(rows, list):
                raise ValueError(f"category {category!r} is not a list")
            if rows:
                unsupported.append(category)
            continue
        if not isinstance(rows, list):
            raise ValueError(f"category {category!r} is not a list")
        for row in rows:
            ex_date = date.fromisoformat(row["ex_date"])
            security_id = resolve(row["symbol"], previous_session(ex_date))
            if security_id is None:
                unresolved.append((row["symbol"], ex_date))
                continue
            if action_type is ActionType.SPLIT:
                value = _rate(row, "new_rate", positive=True) / _rate(
                    row, "old_rate", positive=True
                )
            else:
                value = _rate(row, "rate", positive=False)
            actions.append(
                CorporateAction(
                    security_id=security_id,
                    action_type=action_type,
                    ex_date=ex_date,
                    ratio_or_amount=value,
                    known_at=action_first_seen_known_at(ex_date),
                    source=_ACTIONS_SOURCE,
                )
            )
    actions.sort(key=lambda a: (a.security_id, a.action_type.value, a.ex_date))
    _require_unique([a.key for a in actions], "action")
    return ActionsParse(tuple(actions), tuple(unresolved), tuple(unsupported))


# --- the adapter -----------------------------------------------------------------

FetchBars = Callable[[list[str], date, date], Mapping[str, Any]]
FetchActions = Callable[[list[str], date, date], Mapping[str, Any]]


def _default_fetch_bars(symbols: list[str], start: date, end: date) -> Mapping[str, Any]:
    from tradepartner.adapters import alpaca_raw

    return alpaca_raw.daily_bars(symbols, start, end)


def _default_fetch_actions(symbols: list[str], start: date, end: date) -> Mapping[str, Any]:
    from tradepartner.adapters import alpaca_raw

    payload: Mapping[str, Any] = alpaca_raw.corporate_actions(symbols, start, end)
    return payload


class AlpacaPriceSource(PriceSource):
    """`PriceSource` over Alpaca: asks for every symbol the requested
    securities traded under in the range, parses, and keeps only the
    requested securities and range.

    A row served under a symbol outside that symbol's span (Alpaca serves
    a renamed company's history under both symbols, #104) resolves to no
    requested span and is left out; so is a placeholder bar. The parse
    reports of the latest call stay on `last_bars_report` and
    `last_actions_report` (unresolved rows, placeholders, unsupported
    categories such as spin-offs), for ingest to count or refuse. `fetch_bars` and
    `fetch_actions` default to `alpaca_raw` (network, owner's keys); tests
    pass recorded payloads.
    """

    def __init__(
        self,
        resolver: ListingResolver,
        *,
        fetch_bars: FetchBars = _default_fetch_bars,
        fetch_actions: FetchActions = _default_fetch_actions,
        settings: Settings | None = None,
    ) -> None:
        self._resolver = resolver
        self._fetch_bars = fetch_bars
        self._fetch_actions = fetch_actions
        self._lag = timedelta(days=(settings or get_settings()).alpaca.actions_process_lag_days)
        self.last_bars_report: BarsParse | None = None
        self.last_actions_report: ActionsParse | None = None

    def _plan(
        self, security_ids: Sequence[str], start: date, end: date, *, symbols_from: date
    ) -> tuple[set[str], list[str]]:
        ids = set(check_request(security_ids, start, end))
        unknown = sorted(i for i in ids if not self._resolver.knows(i))
        if unknown:
            raise UnknownSecurityIdError(
                f"unknown security_id(s) {unknown}; resolve tickers through the security master"
            )
        symbols = sorted({s for i in ids for s in self._resolver.symbols(i, symbols_from, end)})
        return ids, symbols

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        ids, symbols = self._plan(security_ids, start, end, symbols_from=start)
        if not symbols:
            return []
        parsed = parse_bars(self._fetch_bars(symbols, start, end), self._resolver.resolve)
        self.last_bars_report = parsed
        return [b for b in parsed.bars if b.security_id in ids and start <= b.session <= end]

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        check_request(security_ids, start, end)
        symbols_from = previous_session(start)  # an action resolves on the session before it
        ids, symbols = self._plan(security_ids, start, end, symbols_from=symbols_from)
        if not symbols:
            return []
        # Alpaca's window is on process_date, which trails the ex-date.
        payload = self._fetch_actions(symbols, start, end + self._lag)
        parsed = parse_corporate_actions(payload, self._resolver.resolve)
        self.last_actions_report = parsed
        return [a for a in parsed.actions if a.security_id in ids and start <= a.ex_date <= end]
