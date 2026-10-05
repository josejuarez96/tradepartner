"""Alpaca `PriceSource`: pure parsers over raw `alpaca_raw` payloads, plus a
thin adapter wiring them to the raw client (spec req 6, plan T12).

**Resolution through the master.** Alpaca keys everything by symbol; the
store keys by `security_id` (spec req 3: adapters never resolve by bare
ticker). `ListingResolver` maps `(ticker, session)` to the security whose
listing carried that ticker on that session, from the master's `listings`
rows (`security_id`, `ticker`, `valid_from`, `class_title`):

- a security holds a ticker from a listing's `valid_from` until its next
  listing with a different ticker (a ticker change), so one security has
  one span per ticker it traded under;
- only equity classes hold tickers (#735). A span with a listing under a
  placeholder ticker (`is_placeholder_ticker`: '', 'N/A', 'None', '-',
  ...) or of a non-equity class (`store.classify.listing_kind`: notes,
  preferred, warrants, rights, units, from the row's own class title, else
  its ticker suffix) holds nothing; its first row still ends the security's
  previous span. A non-equity span still **shadows** another company's
  older span from its own start (the ticker resolves to nothing rather than
  back to the older company), but never its own company's, so a company's
  notes listed under its common ticker never take or hide it;
- an equity row whose ticker is no symbol even in Alpaca's form
  (`alpaca_symbol`: `New York Stock Exchange`, `F&G`, `PTN1`) never ends
  its security's span (#844): it is dropped on a day that also lists a
  readable equity ticker of the security, else read as the ticker of the
  security's last row before it when that is a readable equity row
  (Vulcan's 10-Qs naming the exchange for `VMC`), and a delisting of its
  listing ends that span under the rule below. As its first row, or after
  a placeholder or non-equity row, it stays as written. A footnote marker
  or bracketed exchange after a symbol (`VAL*`, `BAX (NYSE)`) is that
  symbol (`alpaca_symbol`), never a bracketed class (`HEI (A)`);
- a security listing two different tickers on equity rows of one day,
  one of them the ticker of its row just before that day (FutureFuel's
  cover page naming Ford's `F` beside its own `FF`), keeps that ticker:
  the other rows of that day are a typo and are dropped (#819). With no
  such ticker it holds neither, nor any later one, from that day: its rows
  are not assigned, and the tickers resolve as if it had never listed them;
- a span ends at its own delisting (#819): the effective day of a
  delisted (not transferred) equity listing under its ticker that is the
  span's last row (`RegistrantEvidence.delisted_listings`), never before
  the day after the Form 25's acceptance. A later row of the same
  ticker (a late cover page, a relisting, a reorganized security's new
  row) means the security went on trading, and the span runs on, unless
  the line had stopped (#847): a last bar before the delisting's effective
  day and more than `master.transfer_window_sessions` sessions without a
  bar between it and that row (a cover page filed for debt after going
  private) ends the span as if the listing were its last row. A last bar
  on or after the effective day means the line traded through the
  delisting, so a later gap is a hole in the store, never a stop (#943,
  Seagate's 2010 redomicile). An id on `alpaca.accepted_relistings` (#943,
  the owner's genuine long-gap relistings) is never cut at a stopped line.
  From
  that day the ticker resolves to nothing until another span starts: it
  never falls back to an older company's span, and a reused ticker never
  prices the delisted security. Only a span that still holds the ticker
  after the class and registrant rules below shadows anything by ending;
- a ticker reused by another company belongs to whichever span started
  most recently on or before the session, so an old company's bars stop
  resolving once the new company's listing starts;
- within one company (one CIK: ids `<cik>` and `<cik>:<class>`) the
  class that held a ticker first keeps it: a span starting while another
  class of the company already holds the ticker never holds it, not even
  after that class moves on (a Class B listed under the Class A's ticker);
  it shadows other companies like a non-equity span;
- two spans of one ticker starting on the same day, of two securities,
  are **ambiguous**: the ticker resolves to nothing while they are the
  latest, and the run goes on. Not so a repair successor (`<cik>@<date>`)
  and its own company's span (#874): that is the predecessor's copy of the
  successor's row, so the predecessor's span is dropped before rules 3
  and 6 (the contested rule still sees it) and the successor holds the
  ticker, also over an older successor of the company;
- a span is **contested** when its ticker is later taken by another
  security that arrived at it through a rename (Roundhill's `META` ETF,
  then Facebook's `FB` -> `META`). Alpaca serves a renamed company's
  history under its new symbol as well (#104), so rows under that ticker
  on the old holder's dates may be the renamed company's. They resolve to
  nothing and are reported, never assigned; `contested_spans` lists them;
- across companies, a span that starts while another company's span of
  the ticker is live (the holder) is a **claim** unless the holder has
  left by the claimant's start (#793). `RegistrantEvidence` (from the
  store at the run) says when a holder left: the effective day of a
  delisted, not transferred, equity listing of its security, or the day
  after its company's last cover-page share count once it has filed none
  for `alpaca.registrant_quiet_days` by the run. A claim is a
  **co-registrant** when the holder has not left and the claimant
  reported the holder's share count for one date at most that many days
  before its start (a combined filing: AEP Texas listing AEP's stock): the
  holder keeps the ticker and the claimant never holds it. Any other claim
  is **disputed**: it shadows the holder until the earlier of the holder's
  span end and its leaving, then holds the ticker from that day (unless
  another span starts that same day) (MG&E listing MGE Energy's `MGEE`).
  With no evidence on the holder the newer span wins as above, and a
  holding-company successor (Xerox Holdings, NorthWestern
  Energy Group) takes the ticker from its start, its predecessor having
  left before.

**Rename lead (#843).** A span's `valid_from` is the first cover page
showing its ticker, often weeks after a ticker change, while Alpaca
serves the renamed company's bars only under the new symbol from the
change on (and copies its history onto it, #104). So when a security's
equity span under a new ticker directly follows its own assigned span
under another (a rename, neither cut by rule 7, the new span not
contested), the new ticker also *leads* to the security on sessions from
`alpaca.rename_lead_days` before the new span's start (never before the
old span's start) up to it, while `resolve` gives the ticker to no one
(`ListingResolver.lead`) and the old ticker to no other security (a reuse
the master knows of is left to #869). `parse_bars` assigns such a row
only when the security has no row (bar or placeholder) that session from
the rest of the payload: the hole closes at the old symbol's last bar,
and no session is stored twice. The mapping is the master's at the run,
as for every row; a bar's `known_at` stays its session's close.
Corporate actions take no lead, nor does `holds(..., lead=False)`, the
repair's action check.

`ListingResolver.report` counts every listing and span left out by these
rules; `AlpacaPriceSource.resolution_summary` puts it on the run row.

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

**Extended hours.** Extended-hours prints do not move a daily bar's open
or close (free-data-terms research, row 2e), but SIP daily volume includes
them, while a bar is stamped at the 16:00 close per the spec: its volume
can hold up to four hours of later trades. Open owner question on PR #139.

**Actions window.** Alpaca filters corporate actions on `process_date`,
which can trail the ex-date by weeks (#101). `AlpacaPriceSource` asks for
actions processed up to `alpaca.actions_process_lag_days` after the end
of the ex-date window, and from the same lag before its start (in case an
action is processed before its ex-date), for the symbols held from the
session before its start (an action resolves on that session), then
filters on ex-date. An action not yet processed when its ex-date is first
queried is not returned then, so incremental ingest (T16) must re-query
ex-dates in `[today - lag, today]` on every run; an unchanged action is a
no-op under `prices.revision_of`.

**Raw closes.** `alpaca_raw.daily_bars` always asks for `adjustment=raw`
(ADR 0003 rule 1); prices here are the payload's values, unadjusted.

**Source per feed.** A bar's `source` is `alpaca_<feed>` from the payload's
`feed` key (`alpaca_sip`, `alpaca_iex`); a payload without a known feed
raises, so ingest can never mix feeds for a security unnoticed
(`tests/fixtures/README.md`). Actions are `alpaca`.

**Symbols sent (#737).** alpaca-py comma-joins every symbol into the one
`symbols=` parameter of one request and raises `APIError` for the whole
call on any error status, so one invalid symbol is taken to fail the whole
chunk (whether Alpaca's server rejects it or drops it is not established).
A master ticker is therefore sent only in Alpaca's form (`alpaca_symbol`):
trimmed of spaces and quotes, less one trailing footnote marker or
bracketed exchange (`VAL*`, `BAX (NYSE)` -> `BAX`, #844), upper-cased, and
a one-letter class suffix after `-` or `/` written with `.` (`CRD-A` ->
`CRD.A`). Anything else (`C/28`, `F&G`) is not sent, so its security gets no rows
under it, never a guessed mapping's; `last_excluded_symbols` and
`symbol_summary` name those tickers, last on `resolution_summary`'s line.
`ListingResolver` keys every listing by that form (an invalid ticker or a
placeholder as written), so the spellings of one symbol
(`META ` and `META`, `CRD-A` and `CRD.A`) are one ticker to all of its
rules, and the payload's symbols resolve as they are.

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
import re
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from tradepartner.calendar import is_session, next_session, previous_session
from tradepartner.config import Settings, get_settings
from tradepartner.store.classify import EQUITY, listing_kind
from tradepartner.store.delistings import DELISTED
from tradepartner.timeutil import ensure_tz_aware_utc
from tradepartner.universe import SHARES_FACT

_NEW_YORK = ZoneInfo("America/New_York")
_FEEDS = frozenset({"sip", "iex"})
_ACTIONS_SOURCE = "alpaca"
_SPLIT_CATEGORIES = frozenset({"forward_splits", "reverse_splits"})
_DIVIDEND_CATEGORIES = frozenset({"cash_dividends"})
#: The `source` of every bar and action this module returns (#819: the
#: rows a resolution repair may judge).
BAR_SOURCES = frozenset(f"alpaca_{feed}" for feed in _FEEDS)
ACTIONS_SOURCE = _ACTIONS_SOURCE

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


#: The US-equity symbols sent to Alpaca: letters only, with at most one
#: `.`-separated suffix (`BRK.B`). A ticker with a digit is a note or other
#: non-equity line (Citi's `C27C`, P&G's `PG25`); Alpaca rejected all 510
#: such tickers seen from 2019-08, one by one, and had bars for none (#792).
_ALPACA_SYMBOL = re.compile(r"[A-Z]+(\.[A-Z]+)?")
#: A one-letter share-class suffix written with `-` or `/` (`CRD-A`, `BRK/B`).
_CLASS_SUFFIX = re.compile(r"([A-Z]+)[-/]([A-Z])")
#: A filer's footnote marker or bracketed exchange after the symbol (#844):
#: trailing asterisks (`VAL*`), or one trailing bracketed footnote number
#: of one or two digits (`UPH(1)`, `LCINQ (1)`; never a year) or exchange
#: name (`BAX (NYSE)`), with the spaces before it. Any other bracketed
#: group (`HEI (A)`, `GOOG (Class C)`, `BAC (Series L)`) names a class or
#: series and is left alone, so it stays no symbol rather than another
#: class's.
_FOOTNOTE = re.compile(
    r"(.*?[^\s*(])\s*(?:\*+|\(\s*(?:\d{1,2}|NYSE(?:\s+(?:AMERICAN|MKT|ARCA))?|NASDAQ(?:\s*[GC][SM])?"
    r"|AMEX|CBOE|BATS)\s*\))",
    re.IGNORECASE,
)


def _strip_footnote(ticker: str) -> str:
    """`ticker` less one trailing footnote marker (`_FOOTNOTE`), trimmed of
    spaces and quotes at both ends; a field that is only a marker stays."""
    trimmed = ticker.strip().strip("\"'").strip()
    match = _FOOTNOTE.fullmatch(trimmed)
    return match[1] if match else trimmed


def alpaca_symbol(ticker: str) -> str | None:
    """`ticker` in Alpaca's symbol form, or `None` when it has none.

    Spaces and quotes at both ends are trimmed, one trailing footnote
    marker or bracketed exchange dropped (`VAL*`, `BAX (NYSE)`: #844),
    letters upper-cased and a one-letter class suffix after `-` or `/`
    written with `.`; a non-ASCII ticker, or whatever then falls outside
    `_ALPACA_SYMBOL`, is `None`, never a guessed symbol."""
    trimmed = _strip_footnote(ticker)
    if not trimmed.isascii():  # upper() folds some letters into ASCII ('\ufb01' -> 'FI')
        return None
    norm = trimmed.upper()
    if match := _CLASS_SUFFIX.fullmatch(norm):
        norm = f"{match[1]}.{match[2]}"
    return norm if _ALPACA_SYMBOL.fullmatch(norm) else None


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


#: Ticker fields that name no ticker, compared upper-cased after trimming
#: spaces, quotes and brackets at both ends; a field with no letter is one
#: too ('-', '0'). `NA` is on the list although Nano Labs trades as `NA`:
#: filers also write it for "not applicable", and a later such row would
#: take Nano Labs' bars (#736 review), so `NA` is left out and counted.
PLACEHOLDER_TICKERS = frozenset({"", "N/A", "NA", "NONE", "NOT APPLICABLE", "TRADING SYMBOL"})
#: Words that are placeholders unless written in capitals: XBRL booleans
#: and filers' words come in lower or mixed case ('true', 'No'), while a
#: real symbol is upper-case (TrueCar's `TRUE`).
PLACEHOLDER_WORDS = frozenset({"TRUE", "FALSE", "YES", "NO"})


def is_placeholder_ticker(ticker: str) -> bool:
    """True for a ticker field that names no ticker (`PLACEHOLDER_TICKERS`,
    `PLACEHOLDER_WORDS` not in capitals, or no letter at all)."""
    trimmed = ticker.strip().strip("\"'()").strip()
    norm = trimmed.upper()
    if norm in PLACEHOLDER_WORDS:
        return trimmed != norm
    return norm in PLACEHOLDER_TICKERS or not any("A" <= c <= "Z" for c in norm)


@dataclass(frozen=True)
class ResolverReport:
    """What `ListingResolver` leaves unassigned, for the run row: listing
    rows under a placeholder ticker, of a non-equity class (by kind, see
    `store.classify.listing_kind`), of a security listing two tickers on
    one day (from that day), and spans that are later-class (another class
    of the company already held the ticker), co-registrant or disputed (a
    claim on a live holder's ticker, #793), ambiguous (another security's
    span of the ticker starts the same day) or contested; and (#819) the
    spans ended at their own delisting, those kept through a delisting by a
    later row of their ticker, and the same-day typo rows dropped; (#844)
    the equity rows whose ticker is no symbol that were ignored; (#847) the
    spans ended at a stopped line; and (#943) the spans of an owner-accepted
    relisting kept through one."""

    placeholder: int = 0
    non_equity: Mapping[str, int] = field(default_factory=dict)
    same_day_securities: int = 0
    same_day_listings: int = 0
    later_class_spans: int = 0
    ambiguous_spans: int = 0
    contested_spans: int = 0
    co_registrant_spans: int = 0
    disputed_spans: int = 0
    ended_spans: int = 0
    same_day_typos: int = 0
    kept_spans: int = 0
    successor_duplicates: int = 0
    unreadable: int = 0
    stopped_spans: int = 0
    accepted_relistings: int = 0

    def summary(self) -> str:
        """One line for an `ingestion_runs` message."""
        kinds = ", ".join(f"{n} {kind}" for kind, n in sorted(self.non_equity.items()))
        return (
            f"resolver left out {self.placeholder} placeholder-ticker and "
            f"{sum(self.non_equity.values())} non-equity listings ({kinds or 'none'}), "
            f"{self.same_day_listings} listings of {self.same_day_securities} securities "
            f"listing two tickers on one day; {self.later_class_spans} later-class, "
            f"{self.ambiguous_spans} ambiguous and "
            f"{self.contested_spans} contested spans unassigned; "
            f"{self.co_registrant_spans} co-registrant and {self.disputed_spans} disputed "
            f"claims on another company's ticker; {self.ended_spans} spans ended at their "
            f"own delisting, {self.kept_spans} kept through one by a later row of the "
            f"ticker; {self.same_day_typos} same-day typo listings dropped; "
            f"{self.successor_duplicates} predecessor rows duplicated by a successor; "
            f"{self.unreadable} unreadable-ticker listings ignored; "
            f"{self.stopped_spans} spans ended at a stopped line despite a later row, "
            f"{self.accepted_relistings} kept through a stopped line by "
            "alpaca.accepted_relistings"
        )


@dataclass(frozen=True)
class RegistrantEvidence:
    """What the store says, at one run, about whether a ticker's holder
    still trades under it (#793): per company (the CIK of `<cik>` and
    `<cik>:<class>` ids), the day it last filed a cover-page share count
    and every `(as_of_date, value)` share count it reported; per security,
    the effective days of its delisted (not transferred) equity listings,
    and each such listing as `(valid_from, ticker, effective day)` (#819:
    which span it ends; the ticker as `ListingResolver` keys it); the run's
    day, and how many days without a share count make a company quiet.
    (#847) Each such listing's last bar before its security's next row,
    keyed `(security_id, valid_from, ticker)`, and how many sessions
    without a bar before that next row mean the line had stopped trading
    (`master.transfer_window_sessions`; `None`: never judged)."""

    last_filed: Mapping[str, date]
    share_counts: Mapping[str, frozenset[tuple[date, float]]]
    delisted_on: Mapping[str, tuple[date, ...]]
    as_of: date
    quiet_after_days: int
    delisted_listings: Mapping[str, tuple[tuple[date, str, date], ...]] = field(
        default_factory=dict
    )
    last_bars: Mapping[tuple[str, date, str], date] = field(default_factory=dict)
    stop_after_sessions: int | None = None

    def stopped_before(
        self, security_id: str, valid_from: date, ticker: str, row: date, effective: date
    ) -> bool:
        """True when the delisted listing `(security_id, valid_from, ticker)`,
        whose delisting takes effect on `effective`, had stopped trading
        before a later row of its span on `row` (#847): its last bar is
        before `effective` and more than `stop_after_sessions` sessions lie
        strictly between that bar and `row`. False without a last bar or a
        window, and (#943) when the last bar is on or after `effective`:
        the line went on trading through that delisting (Seagate's 2010
        redomicile), so a later gap in the stored bars is a hole, not a stop."""
        last_bar = self.last_bars.get((security_id, valid_from, ticker))
        if last_bar is None or self.stop_after_sessions is None or last_bar >= effective:
            return False
        return _sessions_between(last_bar, row, self.stop_after_sessions + 1) > (
            self.stop_after_sessions
        )

    def left_on(self, security_id: str, since: date) -> date | None:
        """The first day `security_id` no longer trades, as far as the
        evidence shows: the earliest effective day on or after `since` of a
        delisted equity listing, or the day after its company's last share
        count once that is more than `quiet_after_days` before `as_of`;
        `None` while neither applies."""
        days = [day for day in self.delisted_on.get(security_id, ()) if day >= since]
        last = self.last_filed.get(_company(security_id))
        if last is not None and (self.as_of - last).days > self.quiet_after_days:
            days.append(last + timedelta(days=1))
        return min(days, default=None)

    def knows(self, security_id: str) -> bool:
        """True when the evidence has a share count or a delisting for it."""
        return _company(security_id) in self.last_filed or security_id in self.delisted_on

    def same_count(self, one: str, other: str, around: date) -> bool:
        """True when the two companies reported one share count for one
        date no more than `quiet_after_days` before `around` or later (the
        combined filing behind a claimant's listing, not a coincidence)."""
        empty: frozenset[tuple[date, float]] = frozenset()
        mine = self.share_counts.get(_company(one), empty)
        shared = mine & self.share_counts.get(_company(other), empty)
        since = around - timedelta(days=self.quiet_after_days)
        return any(day >= since for day, _ in shared)


def registrant_evidence(
    facts: Iterable[Mapping[str, Any]],
    listing_ends: Iterable[Mapping[str, Any]],
    *,
    as_of: date,
    quiet_after_days: int,
    transfer_window_sessions: int,
) -> RegistrantEvidence:
    """`RegistrantEvidence` from `facts` rows (`shares_outstanding` only;
    the UTC day of `known_at` is the filing day) and `listing_ends` rows
    (`store.delistings.listing_ends_as_of`: only `status == "delisted"` of
    an equity listing by `store.classify.listing_kind`, on its
    `effective_on`, else the day after its `end_session`, never before the
    day after the Form 25's acceptance (`delisting_filed_at`, when given),
    with the listing's `valid_from` and ticker; a transfer is not
    leaving), with each such listing's `end_session` as its last bar and
    `transfer_window_sessions` as the stop window (#847)."""
    last: dict[str, date] = {}
    counts: dict[str, set[tuple[date, float]]] = defaultdict(set)
    for row in facts:
        if row["fact_name"] != SHARES_FACT:
            continue
        company = _company(str(row["security_id"]))
        filed = ensure_tz_aware_utc(row["known_at"], field_name="known_at").date()
        last[company] = max(last.get(company, filed), filed)
        counts[company].add((row["as_of_date"], float(row["value"])))
    delisted: dict[str, set[date]] = defaultdict(set)
    ended: dict[str, set[tuple[date, str, date]]] = defaultdict(set)
    last_bars: dict[tuple[str, date, str], date] = {}
    for row in listing_ends:
        if (
            row["status"] != DELISTED
            or listing_kind(str(row["ticker"]), row["class_title"]) != EQUITY
        ):
            continue
        day = row["effective_on"] or (
            row["end_session"] + timedelta(days=1) if row["end_session"] else None
        )
        accepted = row.get("delisting_filed_at")
        if day is not None and accepted is not None:  # never before the Form 25 is known
            known = ensure_tz_aware_utc(accepted, field_name="delisting_filed_at").date()
            day = max(day, known + timedelta(days=1))
        if day is not None:
            ticker = str(row["ticker"])
            delisted[str(row["security_id"])].add(day)
            keyed = alpaca_symbol(ticker) or ticker
            ended[str(row["security_id"])].add((row["valid_from"], keyed, day))
            if row["end_session"] is not None:
                last_bars[(str(row["security_id"]), row["valid_from"], keyed)] = row["end_session"]
    return RegistrantEvidence(
        last_filed=last,
        share_counts={company: frozenset(rows) for company, rows in counts.items()},
        delisted_on={sid: tuple(sorted(days)) for sid, days in delisted.items()},
        as_of=as_of,
        quiet_after_days=quiet_after_days,
        delisted_listings={sid: tuple(sorted(rows)) for sid, rows in ended.items()},
        last_bars=last_bars,
        stop_after_sessions=transfer_window_sessions,
    )


def _sessions_between(first: date, last: date, cap: int) -> int:
    """XNYS sessions strictly between `first` and `last`, counted up to
    `cap` (enough to tell a gap above a window)."""
    count, day = 0, next_session(first)
    while day < last and count < cap:
        count, day = count + 1, next_session(day)
    return count


@dataclass(frozen=True)
class RenameLead:
    """A rename's lead window (#843): `security_id` traded as `old` and
    then as `new` from `end` (its listing's `valid_from`); on sessions in
    `[start, end)`, rows under `new` may be its own (see `lead`)."""

    security_id: str
    old: str
    new: str
    start: date
    end: date


@dataclass(frozen=True)
class _Row:
    day: date
    ticker: str
    kind: str  # EQUITY or the non-equity kind; "placeholder" for no ticker
    written: str | None = None  # an unreadable ticker read as `ticker` (#844)


_PLACEHOLDER = "placeholder"
_CO_REGISTRANT = "co-registrant"
_DISPUTED = "disputed"


class ListingResolver:
    """`(ticker, session) -> security_id` from master `listings` rows; see
    the module docstring for the rules. A row's `class_title` is optional
    (an untitled row is judged by its ticker suffix). `accepted_relistings`
    (`alpaca.accepted_relistings`, #943) are security ids whose spans rule
    7's stopped-line cut (#847) never ends."""

    def __init__(
        self,
        listings: Iterable[Mapping[str, Any]],
        evidence: RegistrantEvidence | None = None,
        *,
        rename_lead_days: int = 0,
        accepted_relistings: Collection[str] = (),
    ) -> None:
        self._evidence = evidence
        self._accepted = frozenset(accepted_relistings)
        by_security: dict[str, list[_Row]] = defaultdict(list)
        for row in listings:
            ticker = str(row["ticker"])
            kind = (
                _PLACEHOLDER
                if is_placeholder_ticker(ticker)
                else listing_kind(ticker, row.get("class_title"))
            )
            if kind != _PLACEHOLDER:  # one ticker per Alpaca symbol (#737)
                ticker = alpaca_symbol(ticker) or ticker
            by_security[str(row["security_id"])].append(_Row(row["valid_from"], ticker, kind))
        self._by_ticker: dict[str, list[TickerSpan]] = defaultdict(list)
        self._by_security: dict[str, list[TickerSpan]] = defaultdict(list)
        self._history: dict[str, list[TickerSpan]] = defaultdict(list)  # no placeholders
        # Spans that hold no ticker but shadow other companies' older spans.
        self._blockers: dict[str, list[TickerSpan]] = defaultdict(list)
        # An assigned span ended at its own delisting (#819): from its end on,
        # with no end, it shadows the other companies' spans it had
        # superseded (started before it), never a later one.
        self._vacated: dict[str, list[TickerSpan]] = defaultdict(list)
        cut: set[TickerSpan] = set()  # spans ended at their own delisting
        placeholder = same_day_listings = same_day_typos = kept_spans = unreadable = 0
        stopped_spans = accepted_spans = 0
        same_day_securities: set[str] = set()
        non_equity: dict[str, int] = defaultdict(int)
        for security_id, listed in by_security.items():
            listed.sort(key=_row_order)
            rows, pair_day, typos, ignored = _clean_rows(listed)
            same_day_typos += typos
            unreadable += ignored
            index = 0
            while index < len(rows):
                first = index
                start, ticker = rows[index].day, rows[index].ticker
                index += 1
                while index < len(rows) and rows[index].ticker == ticker:
                    index += 1  # the same ticker again (a second exchange): one span
                end = rows[index].day if index < len(rows) else None
                kinds = {r.kind for r in rows[first:index]}
                left, kept, stopped, accepted = None, False, False, False
                if kinds == {EQUITY}:
                    days = [r.day for r in rows[first:index]]
                    aliases = {(r.day, r.written) for r in rows[first:index] if r.written}
                    left, kept, stopped, accepted = self._own_delisting(
                        security_id, ticker, days, end, aliases
                    )
                span = TickerSpan(security_id, ticker, start, end if left is None else left)
                if left is not None:
                    cut.add(span)
                kept_spans += kept
                stopped_spans += stopped
                accepted_spans += accepted
                self._by_security[security_id].append(span)
                if _PLACEHOLDER in kinds:
                    placeholder += index - first
                    continue
                self._history[security_id].append(span)
                if kinds != {EQUITY}:
                    kind = min(kinds - {EQUITY})
                    non_equity[kind] += index - first
                    self._blockers[ticker].append(span)
                elif pair_day is not None and start >= pair_day:
                    same_day_securities.add(security_id)
                    same_day_listings += index - first
                else:
                    self._by_ticker[ticker].append(span)
        # A repair successor's row duplicating its predecessor's (#874) holds
        # the ticker alone: the predecessor's span is dropped before any other
        # rule, so it is never a tie (rule 4) nor a rival claim (rule 6). The
        # contested rule still sees it, so a rename into the ticker counts.
        duplicated = {
            span
            for spans in self._by_ticker.values()
            for span in spans
            if any(
                o.start == span.start and _succeeds(o.security_id, span.security_id) for o in spans
            )
        }
        for spans in self._by_ticker.values():
            spans[:] = [span for span in spans if span not in duplicated]
        later_class = {
            span
            for spans in self._by_ticker.values()
            for span in spans
            if any(_holds_before(other, span) for other in spans)
        }
        for spans in self._by_ticker.values():
            spans[:] = [span for span in spans if span not in later_class]
        for span in later_class:
            self._blockers[span.ticker].append(span)
        claims = {
            span: claim
            for spans in self._by_ticker.values()
            for span in spans
            if (claim := self._claim(span, spans)) is not None
        }
        for spans in self._by_ticker.values():
            spans[:] = [span for span in spans if span not in claims]
        for span, (verdict, until) in claims.items():
            if verdict == _DISPUTED:  # shadows the holder until the claim may hold
                stop = until if span.end is None or (until and until < span.end) else span.end
                self._blockers[span.ticker].append(replace(span, end=stop))
            rivals = self._by_ticker[span.ticker]
            if (
                verdict == _DISPUTED  # a co-registrant never traded the ticker
                and until is not None
                and (span.end is None or until < span.end)
                and not any(other.start == until for other in rivals)  # no rule-4 tie
            ):
                held = replace(span, start=until)  # the claim waits for the holder
                rivals.append(held)
                own = self._by_security[span.security_id]
                own[own.index(span)] = held
        self._contested = frozenset(
            span
            for spans in self._by_ticker.values()
            for span in spans
            if any(
                other.security_id != span.security_id
                and other.start > span.start
                and self._renamed_into(other)
                for other in (*spans, *(d for d in duplicated if d.ticker == span.ticker))
            )
        )
        ambiguous = {
            span
            for spans in self._by_ticker.values()
            for span in spans
            if any(o.security_id != span.security_id and o.start == span.start for o in spans)
        }
        self._assigned_spans = frozenset(s for spans in self._by_ticker.values() for s in spans)
        # Only a span that still holds the ticker after rules 3 and 6 (a
        # claim held from its wait's end included) vacates it.
        cut_ends = {(span.security_id, span.ticker, span.end) for span in cut}
        ended = [
            span
            for span in self._assigned_spans
            if (span.security_id, span.ticker, span.end) in cut_ends
        ]
        for span in ended:
            self._vacated[span.ticker].append(span)
        self._leads: dict[str, list[RenameLead]] = defaultdict(list)  # by new ticker
        self._leads_of: dict[str, list[RenameLead]] = defaultdict(list)  # by security
        if rename_lead_days > 0:
            for security_id, own in self._by_security.items():
                for old, new in itertools.pairwise(own):
                    if (
                        old.ticker != new.ticker
                        and old.end == new.start
                        and old.start < new.start
                        and old in self._assigned_spans
                        and new in self._assigned_spans
                        and new not in self._contested
                    ):
                        start = max(old.start, new.start - timedelta(days=rename_lead_days))
                        lead = RenameLead(security_id, old.ticker, new.ticker, start, new.start)
                        self._leads[new.ticker].append(lead)
                        self._leads_of[security_id].append(lead)
        self.report = ResolverReport(
            placeholder=placeholder,
            non_equity=dict(non_equity),
            same_day_securities=len(same_day_securities),
            same_day_listings=same_day_listings,
            later_class_spans=len(later_class),
            ambiguous_spans=len(ambiguous),
            contested_spans=len(self._contested),
            co_registrant_spans=sum(v == _CO_REGISTRANT for v, _ in claims.values()),
            disputed_spans=sum(v == _DISPUTED for v, _ in claims.values()),
            ended_spans=len(ended),
            same_day_typos=same_day_typos,
            kept_spans=kept_spans,
            successor_duplicates=len(duplicated),
            unreadable=unreadable,
            stopped_spans=stopped_spans,
            accepted_relistings=accepted_spans,
        )

    def _own_delisting(
        self,
        security_id: str,
        ticker: str,
        days: Sequence[date],
        end: date | None,
        aliases: Collection[tuple[date, str]] = (),
    ) -> tuple[date | None, bool, bool, bool]:
        """`(left, kept, stopped, accepted)` for an equity span of `security_id` under `ticker`
        whose rows are on `days` (sorted), until `end` (#819). `aliases`
        are `(day, ticker)` of the span's unreadable rows read as `ticker`
        (#844): a delisted listing under one of them is a listing of the
        span too.

        `left` is the day the span stops holding its ticker because its own
        listing was delisted: the earliest effective day, inside the span,
        of a delisted equity listing of that ticker that is the span's last
        row, or (#847) whose line had stopped trading before the span's
        next row (`RegistrantEvidence.stopped_before`: a cover page filed
        after going private); else `None`. A delisted listing followed by
        another row of the span while the line still traded (a late cover
        page, a relisting, a transfer outside the transfer window, a
        reorganized security's new row) never ends it: the security went on
        trading under the ticker. `kept` is true when such a listing is
        what the span ran on through, `stopped` when a stopped line is
        what ends it. (#943) For an id on `accepted_relistings` a stopped
        line never ends the span (the later row keeps it, as before #847);
        `accepted` is true when that is what kept it."""
        if self._evidence is None:
            return None, False, False, False
        start, last = days[0], days[-1]
        evidence = self._evidence
        accepted = False

        def ends(valid_from: date, listed: str, day: date) -> bool:
            nonlocal accepted
            if valid_from == last:
                return True
            following = min((d for d in days if d > valid_from), default=None)
            if following is None:
                return True
            if not evidence.stopped_before(security_id, valid_from, listed, following, day):
                return False
            if security_id in self._accepted:  # the owner's accepted relisting (#943)
                accepted = True
                return False
            return True

        inside = [
            (day, ends(valid_from, listed, day), valid_from < last)
            for valid_from, listed, day in evidence.delisted_listings.get(security_id, ())
            if (listed == ticker or (valid_from, listed) in aliases)
            and start <= valid_from
            and start < day
            and (end is None or day < end)
        ]
        left = min((day for day, cut, _ in inside if cut), default=None)
        kept = any(not cut for _, cut, _ in inside)
        stopped = any(cut and day == left and later for day, cut, later in inside)
        return left, kept, stopped, accepted

    def _claim(
        self, span: TickerSpan, spans: Sequence[TickerSpan]
    ) -> tuple[str, date | None] | None:
        """`(verdict, until)` when `span` starts while another company's
        span in `spans` holds the ticker and that holder has not left it
        (see the module docstring); `None` when there is no claim.
        `_DISPUTED` if any such holder makes it so, else `_CO_REGISTRANT`;
        `until` is the last day any such holder's wait ends (the earlier of
        its span's end and its leaving), `None` while one has no end."""
        waits = [
            wait
            for holder in spans
            if _company(holder.security_id) != _company(span.security_id)
            and holder.start < span.start
            and holder.covers(span.start)
            and (wait := self._wait(holder, span)) is not None
        ]
        if not waits:
            return None
        ends = [until for _, until in waits]
        until = None if None in ends else max(end for end in ends if end is not None)
        verdicts = {verdict for verdict, _ in waits}
        return (_DISPUTED if _DISPUTED in verdicts else _CO_REGISTRANT), until

    def _wait(self, holder: TickerSpan, claimant: TickerSpan) -> tuple[str, date | None] | None:
        """The claim `claimant` makes on `holder`'s ticker, or `None` when
        the evidence is silent on the holder or shows it left by the
        claimant's start."""
        evidence = self._evidence
        if evidence is None or not evidence.knows(holder.security_id):
            return None  # no evidence: the newer span wins, as under #735
        left = evidence.left_on(holder.security_id, holder.start)
        if left is not None and left <= claimant.start:
            return None  # a successor or a reuse
        ends = [day for day in (holder.end, left) if day is not None]
        until = min(ends, default=None)
        if left is None and evidence.same_count(
            holder.security_id, claimant.security_id, claimant.start
        ):
            return _CO_REGISTRANT, until
        return _DISPUTED, until

    def _renamed_into(self, span: TickerSpan) -> bool:
        """True if `span`'s security traded under another ticker before it."""
        return any(
            earlier.start < span.start and earlier.ticker != span.ticker
            for earlier in self._history[span.security_id]
        )

    @property
    def contested_spans(self) -> tuple[TickerSpan, ...]:
        """Spans whose rows are not assigned (see the module docstring)."""
        return tuple(sorted(self._contested, key=lambda s: (s.ticker, s.start)))

    def resolve(self, ticker: str, session: date) -> str | None:
        """The security trading under `ticker` on `session`, or `None` when
        none does; when the span holding it is contested or shares its start
        with another security's span (ambiguous); or when another company's
        span that holds no ticker (non-equity or later-class) started on or
        after it and is live: an older company never gets the bars of a
        newer listing just because that listing is left out."""
        live = [span for span in self._by_ticker.get(ticker, []) if span.covers(session)]
        if not live:
            return None
        latest = max(span.start for span in live)
        winners = [span for span in live if span.start == latest]
        owners = {span.security_id for span in winners}
        if len(owners) > 1 or any(span in self._contested for span in winners):
            return None
        owner = owners.pop()
        if any(
            block.start >= latest
            and block.covers(session)
            and _company(block.security_id) != _company(owner)
            for block in self._blockers.get(ticker, [])
        ) or any(
            latest < gone.start
            and gone.end is not None
            and gone.end <= session
            and _company(gone.security_id) != _company(owner)
            for gone in self._vacated.get(ticker, [])
        ):
            return None
        return owner

    def lead(self, ticker: str, session: date) -> str | None:
        """The renamed security whose rename lead (#843) covers `ticker` on
        `session`, or `None`: only while `resolve` gives the ticker to no
        one and the old ticker to no other security (a reuse the master
        knows of is #869's), and only one security's lead. A caller assigns
        such a row only where the security has no row under its own symbols
        that session (`parse_bars`)."""
        owners = {
            lead.security_id
            for lead in self._leads.get(ticker, [])
            if lead.start <= session < lead.end
            and self.resolve(lead.old, session) in (None, lead.security_id)
        }
        if len(owners) != 1 or self.resolve(ticker, session) is not None:
            return None
        return owners.pop()

    def holds(self, security_id: str, session: date, *, lead: bool = True) -> bool:
        """True when some ticker resolves to `security_id` on `session`, or
        (with `lead`, for bars only: actions take no lead) leads to it
        (#843): a row of it on that session is one this resolver would
        assign."""
        return any(
            span.covers(session)
            and self._assigned(span)
            and self.resolve(span.ticker, session) == security_id
            for span in self._by_security.get(security_id, [])
        ) or (
            lead
            and any(
                rename.start <= session < rename.end
                and self.lead(rename.new, session) == security_id
                for rename in self._leads_of.get(security_id, [])
            )
        )

    def symbols(self, security_id: str, start: date, end: date) -> list[str]:
        """Every ticker `security_id` traded under at some day in
        `[start, end]`, in span order; `[]` for an unknown id."""
        out: list[str] = []
        for span in self._by_security.get(security_id, []):
            overlaps = span.start <= end and (span.end is None or start < span.end)
            if overlaps and span.ticker not in out and self._assigned(span):
                out.append(span.ticker)
        for lead in self._leads_of.get(security_id, []):  # #843: the new symbol too
            if lead.start <= end and start < lead.end and lead.new not in out:
                out.append(lead.new)
        return out

    def _assigned(self, span: TickerSpan) -> bool:
        return span in self._assigned_spans

    def knows(self, security_id: str) -> bool:
        """True for any security with a listing row, assigned or not."""
        return security_id in self._by_security


def _company(security_id: str) -> str:
    """The CIK of a `<cik>` or `<cik>:<class>` id; any other id is its own."""
    head, sep, _ = security_id.partition(":")
    return head if sep and head.isdigit() else security_id


#: A repair successor's id (#820, `store.master`): `<cik>@<valid_from>`,
#: with `-<n>` when that id was taken.
_SUCCESSOR_ID = re.compile(r"(\d+)@(\d{4}-\d{2}-\d{2})(?:-(\d+))?")


def _succeeds(successor: str, security_id: str) -> bool:
    """True when `successor` is a repair successor id of `security_id`'s
    company (#874): `<cik>@<date>` for `<cik>`, `<cik>:<class>`, or an
    older successor `<cik>@<earlier date>` (a second relisting; on one
    date, the `-<n>` id made later)."""
    match = _SUCCESSOR_ID.fullmatch(successor)
    if match is None or successor == security_id:
        return False
    older = _SUCCESSOR_ID.fullmatch(security_id)
    if older is None:
        return _company(security_id) == match[1]
    mine = (match[2], int(match[3] or 1))
    return older[1] == match[1] and (older[2], int(older[3] or 1)) < mine


def _holds_before(holder: TickerSpan, span: TickerSpan) -> bool:
    """True if `holder`, another class of `span`'s company, already held the
    ticker when `span` started: `span` never holds it (a later class of one
    company never takes over its ticker, not even once `holder` ends)."""
    return (
        holder.security_id != span.security_id
        and _company(holder.security_id) == _company(span.security_id)
        and holder.start < span.start
        and holder.covers(span.start)
    )


def _same_day_pair(rows: Sequence[_Row]) -> date | None:
    """The first day a security's sorted rows list two different tickers on
    equity rows (placeholder and non-equity rows aside), or `None`."""
    for row, following in itertools.pairwise(r for r in rows if r.kind == EQUITY):
        if row.day == following.day and row.ticker != following.ticker:
            return row.day
    return None


def is_same_day_typo(held: str | None, tickers: Collection[str]) -> bool:
    """True when `held` (a security's ticker just before a same-day,
    two-ticker listing day) is one of `tickers` (the tickers tied that
    day): the pair is a cover page's typo (#819; spec req 11 amendment
    #822 part 2), and the security keeps `held`, never a genuine
    same-start listing. Shared with `health._overlapping_listings`
    (#846), so the two rules can never drift apart."""
    return held is not None and held in tickers


_INTERNAL_WHITESPACE = re.compile(r"\s+")


def same_alpaca_symbol(one: str, other: str) -> bool:
    """True when `one` and `other` are the one Alpaca symbol under two
    filer spellings (#846): `alpaca_symbol` already folds a `-` or `/`
    one-letter class suffix into `.` (`CRD-A`, `CRD.A`); a run of spaces
    before such a suffix is the same filer slip (`MOTV U`, `MOTV.U`) and
    is folded the same way here, for comparison only -- `alpaca_symbol`
    itself, and what it sends Alpaca, is unchanged."""

    def folded(ticker: str) -> str | None:
        return alpaca_symbol(_INTERNAL_WHITESPACE.sub("-", _strip_footnote(ticker)))

    left, right = folded(one), folded(other)
    return left is not None and left == right


def _unreadable(row: _Row) -> bool:
    """An equity row whose ticker is no symbol even after `alpaca_symbol`'s
    folds (an exchange name, `F&G`, a digit): a cover page's slip (#844)."""
    return row.kind == EQUITY and alpaca_symbol(row.ticker) is None


def _row_order(row: _Row) -> tuple[date, bool, str]:
    """A placeholder or non-equity row sorts first on its day, so it never
    ends the span of an equity ticker listed that same day."""
    return row.day, row.kind == EQUITY, row.ticker


def _clean_rows(rows: Sequence[_Row]) -> tuple[list[_Row], date | None, int, int]:
    """A security's sorted rows less each same-day typo (#819) and with
    each unreadable equity row (#844) ignored; the first same-day pair day
    left, or `None`; and how many typo and unreadable rows there were.

    An unreadable row never ends the security's span: on a day that also
    lists a readable equity ticker it is dropped, and otherwise it is read
    as the ticker of the security's last row before that day, once that
    day's typos are dropped, when that row is a readable equity row
    (Vulcan's 10-Qs naming `New York Stock Exchange` for `VMC`). With no
    such row (its first row, or after a placeholder or non-equity row) it
    is kept as written, a span that holds a ticker Alpaca is never sent,
    as before. The typo rule runs on the readable rows first, so a typo's
    ticker is never carried on."""
    readable = [r for r in rows if not _unreadable(r)]
    out, pair_day = _drop_same_day_typos(readable)
    typos = len(readable) - len(out)
    ignored = 0
    for row in (r for r in rows if _unreadable(r)):
        if any(r.day == row.day and r.kind == EQUITY for r in readable):
            ignored += 1
            continue
        before = [r for r in out if r.day < row.day]
        if before and before[-1].kind == EQUITY and not _unreadable(before[-1]):
            row = replace(row, ticker=before[-1].ticker, written=row.ticker)
            ignored += 1
        out.append(row)
        out.sort(key=_row_order)
    return out, pair_day, typos, ignored


def _drop_same_day_typos(rows: Sequence[_Row]) -> tuple[list[_Row], date | None]:
    """A security's sorted rows less each same-day typo (#819), and the
    first same-day pair day left, or `None`.

    On a day whose equity rows list two tickers, one of them the ticker of
    the security's row just before that day (an equity row), the others
    are a cover page's slip (FutureFuel naming Ford's `F` beside its own
    `FF`): their rows are dropped and the security keeps its ticker. A pair
    with no such ticker stays a pair."""
    kept = list(rows)
    while (day := _same_day_pair(kept)) is not None:
        before = [r for r in kept if r.day < day]
        held = before[-1].ticker if before and before[-1].kind == EQUITY else None
        tickers = {r.ticker for r in kept if r.day == day and r.kind == EQUITY}
        if not is_same_day_typo(held, tickers):
            return kept, day
        kept = [r for r in kept if not (r.day == day and r.kind == EQUITY and r.ticker != held)]
    return kept, None


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


def _bar(
    symbol: str, session: date, row: Mapping[str, Any], security_id: str, source: str
) -> Bar | None:
    """The `Bar` of one payload row, or `None` for a zero-volume placeholder."""
    trades = row["n"]
    if isinstance(trades, bool) or not isinstance(trades, int) or trades < 0:
        raise ValueError(f"{symbol} {session}: trade count must be an int, got {trades!r}")
    if _is_int_zero(row["v"]) and trades == 0:
        return None
    return Bar(
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


@_fail_closed
def parse_bars(
    payload: Mapping[str, Any], resolve: Resolve, lead: Resolve | None = None
) -> BarsParse:
    """`Bar`s from an `alpaca_raw.daily_bars` payload, each resolved with
    `resolve(symbol, session)` and known at its session's close.

    With `lead` (`ListingResolver.lead`, #843), a row `resolve` leaves out
    goes to `lead(symbol, session)` when that security has no row (a bar
    or a zero-volume placeholder) that session from the rest of the
    payload (a renamed company's new symbol on the sessions after its old
    symbol's last bar); otherwise it stays unresolved."""
    source = feed_source(payload)
    bars: list[Bar] = []
    unresolved: list[tuple[str, date]] = []
    placeholders: list[tuple[str, date]] = []
    pending: list[tuple[str, date, Mapping[str, Any]]] = []
    for symbol, rows in payload["bars"].items():
        for row in rows:
            session = _session_of(row["t"])
            security_id = resolve(symbol, session)
            if security_id is None:
                pending.append((symbol, session, row))
                continue
            bar = _bar(symbol, session, row, security_id, source)
            if bar is None:
                placeholders.append((security_id, session))
            else:
                bars.append(bar)
    have = {bar.key for bar in bars} | set(placeholders)  # any own row stops the lead
    for symbol, session, row in pending:
        security_id = lead(symbol, session) if lead is not None else None
        if security_id is None or (security_id, session) in have:
            unresolved.append((symbol, session))
            continue
        key = (security_id, session)
        bar = _bar(symbol, session, row, security_id, source)
        have.add(key)
        if bar is None:
            placeholders.append(key)
        else:
            bars.append(bar)
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
    each ex-date. Alpaca's stable `id` becomes `source_action_id`, so a
    re-dated action is a revision of one event (#108, #181); a row without
    one has none."""
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
                    source_action_id=row.get("id"),
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
        self.last_excluded_symbols: tuple[str, ...] = ()

    def _plan(
        self, security_ids: Sequence[str], start: date, end: date, *, symbols_from: date
    ) -> tuple[set[str], list[str]]:
        self.last_excluded_symbols = ()
        ids = set(check_request(security_ids, start, end))
        unknown = sorted(i for i in ids if not self._resolver.knows(i))
        if unknown:
            raise UnknownSecurityIdError(
                f"unknown security_id(s) {unknown}; resolve tickers through the security master"
            )
        tickers = {t for i in ids for t in self._resolver.symbols(i, symbols_from, end)}
        symbols = {t: alpaca_symbol(t) for t in tickers}
        self.last_excluded_symbols = tuple(sorted(t for t, s in symbols.items() if s is None))
        return ids, sorted({s for s in symbols.values() if s is not None})

    def symbol_summary(self) -> str:
        """The latest call's master tickers not sent to Alpaca, as one line
        for the run row's message; `""` when there are none."""
        if not self.last_excluded_symbols:
            return ""
        named = ", ".join(repr(t) for t in self.last_excluded_symbols)
        return (
            f"{len(self.last_excluded_symbols)} master ticker(s) not sent to Alpaca, "
            f"not Alpaca symbols: {named}"
        )

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        self.last_bars_report = None
        ids, symbols = self._plan(security_ids, start, end, symbols_from=start)
        if not symbols:
            return []
        parsed = parse_bars(
            self._fetch_bars(symbols, start, end), self._resolver.resolve, self._resolver.lead
        )
        self.last_bars_report = parsed
        return [b for b in parsed.bars if b.security_id in ids and start <= b.session <= end]

    def resolution_summary(self) -> str:
        """The resolver's `ResolverReport` line, plus the rows of the latest
        `bars` and `corporate_actions` calls that resolved to no security,
        and last (the run row's length cut takes it first) `symbol_summary`."""
        line = self._resolver.report.summary()
        if self.last_bars_report is not None:
            line += f"; {len(self.last_bars_report.unresolved)} bar rows unresolved"
        if self.last_actions_report is not None:
            line += f"; {len(self.last_actions_report.unresolved)} action rows unresolved"
        if symbols := self.symbol_summary():
            line += f"; {symbols}"
        return line

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        self.last_actions_report = None
        check_request(security_ids, start, end)
        symbols_from = previous_session(start)  # an action resolves on the session before it
        ids, symbols = self._plan(security_ids, start, end, symbols_from=symbols_from)
        if not symbols:
            return []
        # Alpaca's window is on process_date, which trails the ex-date.
        payload = self._fetch_actions(symbols, start - self._lag, end + self._lag)
        parsed = parse_corporate_actions(payload, self._resolver.resolve)
        self.last_actions_report = parsed
        return [a for a in parsed.actions if a.security_id in ids and start <= a.ex_date <= end]
