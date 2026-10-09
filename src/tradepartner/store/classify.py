"""Security-type classification (spec "Master column sources", plan T9).

EDGAR has no point-in-time security type (ADR 0006, "Verify"), so each
class's type is derived from evidence that does carry a timestamp: its
issuer's filing forms and SGML-header SIC, the class's cover-page title,
and, before cover pages, the ticker of its snapshot listing.
`build_classifications` is pure (a `FilingSource` and a `MasterBuild` in,
rows out); `write_classifications` inserts them; `classifications_as_of`
reads the latest row per `security_id` known at T.

**Rules**, first match wins, evaluated for each class over the evidence
known at one instant:

1. `benchmark_config`: a seeded benchmark is an `etf` (source `config`).
2. `fund_form`: the issuer has filed an investment-company form
   (N-CSR, N-CSRS, N-PORT / NPORT-P, 485BPOS, N-2): `fund`.
3. `f6_depositary`: the CIK has filed an F-6 (depositary receipts):
   `depositary`.
4. `foreign_form`: the issuer's latest status form (below) is foreign:
   `foreign`.
5. `sic_6770`: the latest filing header's SIC is 6770 (blank checks):
   `spac`.
6. The class's latest cover-page title, up to the first comma (the
   master's `_norm_title`): a depositary, preferred, warrant, unit, right
   or debt word gives `title_<type>`; else a common-equity title (the
   master's `_is_common`) gives `title_common`; anything else is
   `unclassifiable` by `title_unrecognized` ("Shares of Beneficial
   Interest" is a trust, not common stock).
7. A class with no titled listing: its latest snapshot ticker's suffix
   (`suffix_<type>`, `provenance = snapshot_static`): after a separator,
   `W`/`WS`/`WT` warrant, `U`/`UN`/`UT` unit, `R`/`RT`/`RI` right, `P`,
   `P?` or `PR?` preferred; a five-letter ticker with no separator ending
   in `W`, `U`, `R` (Nasdaq fifth-letter codes) or `Y` (ADR). A class
   letter (`BRK.B`) is no suffix. This is ADR 0006's documented pre-2019
   heuristic.
8. `common_default`: the issuer's latest status form is domestic: `common`.
9. `no_rule_matched`: `unclassifiable`.

Status forms decide domestic vs foreign: `10-K`, `10-KT`, `10-Q`, `S-1`
are domestic; `20-F`, `40-F`, `F-1`, `6-K` foreign; the latest known wins,
so a foreign issuer that starts filing 10-Ks becomes domestic then. An
amendment (`/A`) counts as its base form. Filings accepted at one instant
are ordered by accession. Funds and F-6 are sticky: once
filed, they stay.

**Timing.** The type is recomputed at the security's `known_at` and at
every later instant some evidence for it became known (a filing's
acceptance, a listing row's `known_at`); a row is written only when
(`security_type`, `rule`, `sic`) changes. So each row's `known_at` is the
instant its classification first became knowable, never earlier than any
evidence it used, and nothing is back-dated. Evidence accepted before the
security's own `known_at` is applied at that `known_at`. `sic` is the
latest header SIC known then (`None` before any header); a header without
a SIC is skipped, so it never resets a SPAC.

Commodity and grantor-trust ETFs that file 10-Ks under an ordinary SIC
need price-source asset metadata (ADR 0006); `FilingSource` has none, so
they classify by their title or fall to `common_default`. MLP "Common
Units" titles are `unit` from their first cover page, while the same issuer
is `common_default` before it. Both are open owner questions on PR #122.

**Snapshot tickers and #35.** A class listed only by a `snapshot_static`
row is `common_default` until that row's fetch, and only then can a suffix
make it a warrant or unit. That is safe because #35 keeps the listing
itself invisible before its `known_at`; any change letting such listings
apply earlier must apply the suffix rows at the same time.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb
import polars as pl

from tradepartner.adapters.filings import FilingSource
from tradepartner.config import Settings
from tradepartner.store.asof import _latest_as_of, _validate_t
from tradepartner.store.db import insert_row
from tradepartner.store.master import MasterBuild, _is_common, _norm_title
from tradepartner.timeutil import ensure_tz_aware_utc

COMMON = "common"
UNCLASSIFIABLE = "unclassifiable"

SPAC_SIC = 6770
FUND_FORMS = frozenset({"N-CSR", "N-CSRS", "N-PORT", "NPORT-P", "485BPOS", "N-2"})
F6_FORMS = frozenset({"F-6", "F-6EF"})
DOMESTIC_FORMS = frozenset({"10-K", "10-KT", "10-K405", "10-KSB", "10-Q", "10-QSB", "S-1"})
FOREIGN_FORMS = frozenset({"20-F", "40-F", "F-1", "6-K"})
_STATUS_FORMS = DOMESTIC_FORMS | FOREIGN_FORMS

#: Title words, checked in order before the common-equity words.
_TITLE_TYPES: tuple[tuple[str, str], ...] = (
    ("depositary", "depositary"),
    ("preferred", "preferred"),
    ("preference", "preferred"),
    ("warrant", "warrant"),
    ("unit", "unit"),
    ("right", "right"),
    ("note", "debt"),
    ("debenture", "debt"),
)

_SEPARATED_SUFFIX = re.compile(r"^[A-Z0-9]+[.\-/ ^+=](?P<suffix>[A-Z]+)$")
_SUFFIX_TYPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"W|WS|WT"), "warrant"),
    (re.compile(r"U|UN|UT"), "unit"),
    (re.compile(r"R|RT|RI"), "right"),
    (re.compile(r"P|P[A-Z]|PR[A-Z]?"), "preferred"),
)
_FIFTH_LETTER = {"W": "warrant", "U": "unit", "R": "right", "Y": "depositary"}

_EDGAR = "edgar"

Row = dict[str, Any]


@dataclass(frozen=True)
class ClassificationBuild:
    """`classifications` rows, oldest first per security."""

    classifications: tuple[Row, ...]


@dataclass(frozen=True)
class _Evidence:
    """Everything known about one CIK, as (instant, value) pairs."""

    forms: tuple[tuple[datetime, str], ...]
    sics: tuple[tuple[datetime, int], ...]


def _base_form(form: str) -> str:
    return form.removesuffix("/A")


def _title_type(title: str) -> tuple[str, str]:
    norm = _norm_title(title)
    for word, security_type in _TITLE_TYPES:
        if re.search(rf"\b{word}", norm):
            return security_type, f"title_{security_type}"
    if _is_common(title):
        return COMMON, "title_common"
    return UNCLASSIFIABLE, "title_unrecognized"


def ticker_suffix_type(ticker: str) -> str | None:
    """The security type a ticker's suffix implies, or `None` for none."""
    ticker = ticker.upper()
    match = _SEPARATED_SUFFIX.match(ticker)
    if match is not None:
        suffix = match.group("suffix")
        return next((t for pattern, t in _SUFFIX_TYPES if pattern.fullmatch(suffix)), None)
    if len(ticker) == 5 and ticker.isalpha():
        return _FIFTH_LETTER.get(ticker[-1])
    return None


EQUITY = "equity"

#: Non-equity words in a listing's title head, checked in this order.
_LISTING_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"prefer(red|ence)"), "preferred"),  # also "CumulativePreferred"
    (re.compile(r"\b(notes?|debentures?|bonds?)\b"), "debt"),
    (re.compile(r"\bwarrants?\b"), "warrant"),
    (re.compile(r"\brights?\b"), "right"),
)
_INCLUDED_IN_UNITS = re.compile(r"\s+included\b.*\bunits?\b.*$")
#: A common or ordinary class at the start of a head, and a shareholder
#: rights plan attached to it after that ("Common Shares (including Rights
#: under Shareholder Rights Plan)", "Common Stock ... Preferred Share
#: Purchase Rights"): the rights trade with the shares, so the row is the
#: common's. Only a plan-shaped clause is stripped ("including",
#: "together with", "associated" ... rights; "... stock purchase rights"),
#: never one naming a warrant or unit ("Class A Common Stock and one Right"
#: stays a right). A head going on with "purchase", "rights" or "warrants"
#: right after the class ("Common Stock Purchase Rights") is the
#: instrument itself.
_COMMON_HEAD = re.compile(
    r"^((class|series) [a-z0-9] )?(common|ordinary|capital) (stock|shares?)\b"
)
_INSTRUMENT = re.compile(r"^\s*(purchase|rights?|warrants?)\b")
_RIGHTS_PLAN = re.compile(
    r"\(?\s*\b(including|together with|and associated|associated)\b[^()]*\brights?\b.*$"
    r"|\b(preferred|preference|common) (stock|shares?) purchase rights?\b.*$"
)
_OWN_INSTRUMENT = re.compile(r"\b(warrants?|units?)\b")
_DEPOSITARY_SHARE = re.compile(r"\bdepos[a-z]*ry ?shares?\b")
_ADS = re.compile(r"\b(american|global)\b")
_UNIT = re.compile(r"\bunits?\b")
_EQUITY_UNIT = re.compile(r"\b(common|depositary) units?\b|\bpartner")
_NON_EQUITY_SUFFIXES = frozenset({"warrant", "unit", "right", "preferred"})
#: The kinds `listing_kind` returns for a non-equity row.
NON_EQUITY_KINDS = ("coupon", "preferred", "debt", "warrant", "right", "unit")


def _listing_head(class_title: str) -> str:
    """The master's `_norm_title` head, less a trailing "included as part of
    the units" clause (it names the shares or warrants inside a unit, not
    the unit) and a rights plan attached to a common class."""
    head = _INCLUDED_IN_UNITS.sub("", _norm_title(class_title))
    common = _COMMON_HEAD.match(head)
    if common is None:
        return head
    rest = head[common.end() :]
    plan = _RIGHTS_PLAN.search(rest)
    if _INSTRUMENT.match(rest) or plan is None or _OWN_INSTRUMENT.search(plan.group()):
        return head
    return head[: common.end()] + rest[: plan.start()]


def listing_kind(ticker: str, class_title: str | None) -> str:
    """`EQUITY`, or why one `listings` row is not an equity class (one of
    `NON_EQUITY_KINDS`), for the price resolver (spec, amendment #735).
    Read from the row alone, on its title head (`_listing_head`: up to the
    first comma, less an "included as part of the units" clause and a
    rights plan attached to a common class):

    - a `%` in the head, or anywhere in a title whose head is not a common
      class, is a coupon instrument (`coupon`: notes, preferred);
    - else a head naming preferred, debt (note, debenture, bond), a warrant
      or a right is that kind;
    - else a depositary *share* that is not American or Global is read as a
      bank's preferred depositary share (`preferred`), so a foreign issuer's
      ADS titled only "Depositary Shares" is left out too;
    - else a head naming a unit is a `unit`, unless it names common or
      depositary units or partner interests (an MLP's "Common Units");
    - an untitled row (`None` or blank: a snapshot) takes its ticker suffix
      (`ticker_suffix_type`): warrant, unit, right or preferred.

    Everything else is `EQUITY`: common and ordinary shares, ADSs, and
    titles no rule recognises ("Shares", "Class A")."""
    if class_title is None or not class_title.strip():
        suffix = ticker_suffix_type(ticker)
        return suffix if suffix in _NON_EQUITY_SUFFIXES else EQUITY
    head = _listing_head(class_title)
    # A coupon anywhere in the title, unless the head is a common class (a
    # `%` after its first comma belongs to another class listed with it).
    if "%" in head or ("%" in class_title and _COMMON_HEAD.match(head) is None):
        return "coupon"
    for pattern, kind in _LISTING_WORDS:
        if pattern.search(head):
            return kind
    if _DEPOSITARY_SHARE.search(head) and not _ADS.search(head):
        return "preferred"
    if _UNIT.search(head) and not _EQUITY_UNIT.search(head):
        return "unit"
    return EQUITY


@dataclass(frozen=True)
class _State:
    """What a CIK's filings say at one instant: the inputs rules 2-5 and 8 read."""

    fund: bool = False
    f6: bool = False
    domestic: bool | None = None  # the latest status form's side; `None` before any
    sic: int | None = None


_NO_STATE = _State()


@dataclass(frozen=True)
class _Timeline:
    """A CIK's `_State` after all evidence at each instant, kept only where it
    changes (`stamps` ascending), and its latest evidence instant."""

    stamps: tuple[datetime, ...]
    states: tuple[_State, ...]
    last: datetime | None

    def at(self, t: datetime) -> _State:
        """The state from evidence known at `t` (`known_at <= t`)."""
        i = bisect_right(self.stamps, t)
        return self.states[i - 1] if i else _NO_STATE


_NO_TIMELINE = _Timeline((), (), None)


def _timeline(evidence: _Evidence) -> _Timeline:
    """One ordered pass over a CIK's evidence. Within one instant forms and
    SICs apply in `_evidence`'s (instant, accession) order, so the latest
    status form and SIC there are the last in that order."""
    forms, sics = evidence.forms, evidence.sics
    instants = sorted({k for k, _ in forms} | {k for k, _ in sics})
    stamps: list[datetime] = []
    states: list[_State] = []
    fund = f6 = False
    domestic: bool | None = None
    sic: int | None = None
    i = j = 0
    for t in instants:
        while i < len(forms) and forms[i][0] <= t:
            form = forms[i][1]
            fund = fund or form in FUND_FORMS
            f6 = f6 or form in F6_FORMS
            if form in _STATUS_FORMS:
                domestic = form in DOMESTIC_FORMS
            i += 1
        while j < len(sics) and sics[j][0] <= t:
            sic = sics[j][1]
            j += 1
        state = _State(fund, f6, domestic, sic)
        if not states or state != states[-1]:
            stamps.append(t)
            states.append(state)
    return _Timeline(tuple(stamps), tuple(states), instants[-1] if instants else None)


def _classify(
    state: _State,
    titled: Row | None,
    known: Row | None,
    benchmark: Row | None,
) -> tuple[str, str, int | None, str]:
    """(security_type, rule, sic, provenance) from the issuer's `state` and the
    class's latest titled and latest listing rows, all known at one instant."""
    sic = state.sic
    if benchmark is not None:
        return "etf", "benchmark_config", sic, benchmark["provenance"]
    if state.fund:
        return "fund", "fund_form", sic, "filing"
    if state.f6:
        return "depositary", "f6_depositary", sic, "filing"
    if state.domestic is False:
        return "foreign", "foreign_form", sic, "filing"
    if sic == SPAC_SIC:
        return "spac", "sic_6770", sic, "filing"
    if titled is not None:
        security_type, rule = _title_type(titled["class_title"])
        return security_type, rule, sic, "filing"
    if known is not None:
        suffix_type = ticker_suffix_type(known["ticker"])
        if suffix_type is not None:
            return suffix_type, f"suffix_{suffix_type}", sic, "snapshot_static"
    if state.domestic:
        return COMMON, "common_default", sic, "filing"
    return UNCLASSIFIABLE, "no_rule_matched", sic, "filing"


def _listing_timeline(
    rows: Sequence[Row],
) -> tuple[list[datetime], list[tuple[Row | None, Row | None]]]:
    """A class's (latest titled, latest) listing row after all rows at each
    instant; `rows` are in `known_at` order (ties in master order)."""
    stamps: list[datetime] = []
    states: list[tuple[Row | None, Row | None]] = []
    titled: Row | None = None
    for row in rows:
        if row["class_title"] is not None:
            titled = row
        if stamps and stamps[-1] == row["known_at"]:
            states[-1] = (titled, row)
        else:
            stamps.append(row["known_at"])
            states.append((titled, row))
    return stamps, states


def _evidence(
    source: FilingSource, ciks: Iterable[str], settings: Settings
) -> dict[str, _Evidence]:
    wanted = set(ciks)
    forms: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    for entry in source.filing_index():  # full history, as the master reads it
        if entry.cik in wanted:
            forms[entry.cik].append((entry.accepted_at, entry.accession, _base_form(entry.form)))
    out: dict[str, _Evidence] = {}
    for cik in wanted:
        headers = source.filing_headers(cik, settings.edgar.header_forms)
        # A header without a SIC never erases a known one (a SPAC stays a SPAC).
        sics = sorted((h.accepted_at, h.accession, h.sic) for h in headers if h.sic is not None)
        out[cik] = _Evidence(
            forms=tuple((stamp, form) for stamp, _, form in sorted(forms[cik])),
            sics=tuple((stamp, sic) for stamp, _, sic in sics),
        )
    return out


def build_classifications(
    source: FilingSource,
    master: MasterBuild,
    settings: Settings,
    *,
    ingested_at: datetime,
) -> ClassificationBuild:
    """`classifications` rows for every security in `master`.

    Pure apart from calling `source`. Raises `ValueError` if any evidence
    is later than `ingested_at` (spec: `known_at <= ingested_at`).

    One ordered pass per CIK and per class (#564): the classification is
    evaluated at the security's `known_at` and at each later instant its
    issuer's `_State` or its listings change. Any other evidence instant
    leaves every input of `_classify` as it was, so it could only repeat the
    previous row, which is never written.
    """
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    securities = list(master.securities)
    issuers = {row["cik"] for row in securities if not row["benchmark"]}
    timelines = {
        cik: _timeline(facts) for cik, facts in _evidence(source, issuers, settings).items()
    }
    listings: dict[str, list[Row]] = defaultdict(list)
    for row in sorted(master.listings, key=lambda r: r["known_at"]):
        listings[row["security_id"]].append(row)

    rows: list[Row] = []
    for security in sorted(securities, key=lambda r: r["security_id"]):
        security_id = security["security_id"]
        benchmark = security if security["benchmark"] else None
        issuer = timelines.get(security["cik"], _NO_TIMELINE)
        if benchmark is not None:
            issuer = _NO_TIMELINE
        listing_stamps, listing_states = _listing_timeline(listings[security_id])
        latest = max(
            (stamp for stamp in (issuer.last, *listing_stamps[-1:]) if stamp is not None),
            default=None,
        )
        if latest is not None and latest > ingested_at:
            raise ValueError(
                f"{security_id}: evidence at {latest.isoformat()} is after "
                f"ingested_at {ingested_at.isoformat()}"
            )
        start = security["known_at"]
        changes = {stamp for stamp in issuer.stamps if stamp > start}
        changes |= {stamp for stamp in listing_stamps if stamp > start}
        previous: tuple[str, str, int | None] | None = None
        for t in sorted({start} | changes):
            i = bisect_right(listing_stamps, t)
            titled, known = listing_states[i - 1] if i else (None, None)
            security_type, rule, sic, provenance = _classify(issuer.at(t), titled, known, benchmark)
            if (security_type, rule, sic) == previous:
                continue
            previous = (security_type, rule, sic)
            rows.append(
                {
                    "security_id": security_id,
                    "sic": sic,
                    "security_type": security_type,
                    "rule": rule,
                    "known_at": t,
                    "ingested_at": ingested_at,
                    "source": security["source"] if benchmark is not None else _EDGAR,
                    "provenance": provenance,
                }
            )
    return ClassificationBuild(classifications=tuple(rows))


def write_classifications(conn: duckdb.DuckDBPyConnection, build: ClassificationBuild) -> int:
    """Insert every row of `build`; return the number inserted."""
    for row in build.classifications:
        insert_row(conn, "classifications", row)
    return len(build.classifications)


def classifications_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """The latest `classifications` row per `security_id` known by `t`,
    sorted by `security_id`. A bare date raises `TypeError`, a naive
    datetime `ValueError` (same rules as `store.asof`)."""
    return _latest_as_of(conn, "classifications", ("security_id",), _validate_t(t), security_ids)
