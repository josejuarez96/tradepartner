"""Pure parsers over raw EDGAR payloads (spec req 6, plan T11).

`edgar_raw` fetches; this module turns what it returned into the records
`adapters.filings` defines. Nothing here touches the network: every
function takes the payload (parsed JSON, text or bytes) and returns
records, so the tests run on T3's recordings.

**`known_at` per the master table.** Every filing record carries the SEC
acceptance instant, from one of two sources:

- the submissions payload's `acceptanceDateTime` (UTC), via
  `acceptance_times`, which also reads the older paged files;
- the SGML header's `ACCEPTANCE-DATETIME`, which is **Eastern time** with no
  zone, converted here with the `America/New_York` rules (EST or EDT).

A filing **date** is never turned into an acceptance time. The index dates
a filing accepted after 17:30 ET on the next business day (Apple's 10-Q
accepted 18:03 ET on 2024-02-01 is dated 2024-02-02), and a company-facts
entry's `filed` is a date too. A record whose accession has no known
acceptance time is returned in `unstamped`, never guessed; ingest fetches
the submissions for that CIK or reports the gap. Snapshot records
(`company_tickers`) carry the caller's fetch time.

**Exchanges** are normalized by `normalize_exchange` to the codes config
uses (`NYSE`, `NASDAQ`, `NYSE_AMERICAN`), plus codes for other venues
(`NYSE_ARCA`, `NYSE_CHICAGO`, `NASDAQ_PHLX`, ...) and an upper-case slug for
anything unknown, so a cover page's
`SecurityExchangeName`, the tickers snapshot's `Nasdaq` and a Form 25's
"Nasdaq Stock Market LLC" agree. Names are matched exactly, never by
substring: a delisting from NYSE Chicago must not end an NYSE listing.

**Cover pages** are parsed with `edgartools`' inline-XBRL extractor (spec:
`edgartools` only for cover-page iXBRL), which resolves each fact's context
and class dimension and applies the iXBRL transforms (so "Nasdaq Stock
Market LLC" with `ixt-sec:exchnameen` reads as `NASDAQ`). A registered class
with `NoTradingSymbolFlag` (Apple's and Alphabet's notes) has no ticker and
is not a listing. `EntityCommonStockSharesOutstanding` facts come back per
class member (`us-gaap:CommonClassAMember`), or with `class_member = ""`
when undimensioned: the company-facts API drops dimensioned facts, so a
dual-class filer's per-class shares exist only here.

Fact names are the XBRL concept's local name
(`EntityCommonStockSharesOutstanding`); mapping them to the store's
`fact_name` is ingest's job (T16).
"""

from __future__ import annotations

import functools
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import lxml.etree  # type: ignore[import-untyped]
import lxml.html  # type: ignore[import-untyped]

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.timeutil import ensure_tz_aware_utc

_EASTERN = ZoneInfo("America/New_York")
_DELISTING_FORMS = frozenset({"25", "25-NSE"})
_SHARES_CONCEPT = "EntityCommonStockSharesOutstanding"
_CLASS_AXIS = "us-gaap:StatementClassOfStockAxis"
_CIK_SCHEME = "http://www.sec.gov/CIK"
_CIK_CONCEPT = "EntityCentralIndexKey"
_COVER_CONCEPTS = frozenset(
    {"Security12bTitle", "TradingSymbol", "SecurityExchangeName", _SHARES_CONCEPT}
)


def _fail_closed[**P, R](parse: Callable[P, R]) -> Callable[P, R]:
    """Re-raise a malformed payload's `KeyError`, `IndexError`, `TypeError`,
    `AttributeError`, `decimal.InvalidOperation` or XML/HTML parse error as
    `ValueError`, so every parser fails
    with one exception type and never returns a partial record."""

    @functools.wraps(parse)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return parse(*args, **kwargs)
        except (
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            InvalidOperation,
            ET.ParseError,
            lxml.etree.LxmlError,
        ) as error:
            raise ValueError(f"{parse.__name__}: malformed payload: {error!r}") from error

    return wrapper


def _cik(value: object) -> str:
    return f"{int(str(value)):010d}"


def normalize_class_member(raw: str) -> str:
    """A class-axis dimension value with any `prefix:` and a trailing
    `Member` dropped, so `us-gaap:CommonClassAMember` (cover-page iXBRL) and
    `CommonClassA` (an FSN `dim.segments` member, whose namespace and
    `Member` are already stripped) are one name. Pure; `""` stays `""`."""
    value = raw.split(":", 1)[-1] if ":" in raw else raw
    return value.removesuffix("Member")


def _parse_utc(text: str) -> datetime:
    return ensure_tz_aware_utc(
        datetime.fromisoformat(text.replace("Z", "+00:00")), field_name="acceptanceDateTime"
    )


# --- exchanges -----------------------------------------------------------

#: Exact names (upper case, punctuation dropped) of the venues config
#: names, as the dei `SecurityExchangeName` codes, the tickers snapshot and
#: Form 25 notices spell them. Exact, not substring: "NYSE National" or
#: "Nasdaq PHLX" is another venue, not NYSE or Nasdaq.
_EXCHANGES: dict[str, str] = {
    name: code
    for code, names in {
        "NYSE": (
            "NYSE",
            "NEW YORK STOCK EXCHANGE",
            "NEW YORK STOCK EXCHANGE LLC",
            "NEW YORK STOCK EXCHANGE INC",
        ),
        "NASDAQ": (
            "NASDAQ",
            "NASDAQ STOCK MARKET",
            "NASDAQ STOCK MARKET LLC",
            "THE NASDAQ STOCK MARKET LLC",
        ),
        "NYSE_AMERICAN": (
            "NYSEAMER",
            "NYSE AMERICAN",
            "NYSE AMERICAN LLC",
            "NYSE MKT",
            "NYSE MKT LLC",
            "NYSEMKT",
            "NYSE AMEX",
            "NYSE AMEX LLC",
            "AMEX",
            "AMERICAN STOCK EXCHANGE",
            "AMERICAN STOCK EXCHANGE LLC",
            "NYSE ALTERNEXT US",
            "NYSE ALTERNEXT US LLC",
        ),
        "NYSE_ARCA": ("NYSEARCA", "NYSE ARCA", "NYSE ARCA INC", "NYSE ARCA LLC"),
        "CBOE_BZX": (
            "CBOEBZX",
            "BZX",
            "CBOE BZX EXCHANGE",
            "CBOE BZX EXCHANGE INC",
            "BATS",
            "BATS BZX EXCHANGE INC",
        ),
        "NYSE_CHICAGO": ("CHX", "NYSE CHICAGO", "NYSE CHICAGO INC", "CHICAGO STOCK EXCHANGE INC"),
        "NYSE_NATIONAL": ("NYSENAT", "NYSE NATIONAL", "NYSE NATIONAL INC"),
        "NASDAQ_BX": ("BX", "NASDAQ BX INC", "NASDAQ OMX BX INC"),
        "NASDAQ_PHLX": ("PHLX", "NASDAQ PHLX LLC", "NASDAQ OMX PHLX LLC"),
        "CBOE": ("CBOE",),
        "OTC": ("OTC",),
    }.items()
    for name in names
}


def normalize_exchange(raw: str) -> str:
    """The exchange code config uses for `raw`, from an exact-name table;
    any other name becomes its upper-case slug ("Nasdaq Global Select
    Market" -> `NASDAQ_GLOBAL_SELECT_MARKET`), which matches no listing, so
    ingest should report codes outside the table."""
    text = " ".join(re.sub(r"[.,]", " ", raw.upper()).split())
    return _EXCHANGES.get(text) or re.sub(r"[^A-Z0-9]+", "_", text).strip("_")


# --- submissions and the filing index -------------------------------------


def _filing_columns(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The column lists of a submissions payload or of one older page."""
    filings = payload.get("filings")
    if isinstance(filings, Mapping):
        recent = filings["recent"]
        if not isinstance(recent, Mapping):
            raise ValueError("submissions payload: filings.recent is not an object")
        return recent
    return payload


@_fail_closed
def acceptance_times(*payloads: Mapping[str, Any]) -> dict[str, datetime]:
    """Accession -> acceptance instant (UTC) from submissions payloads and
    their older pages (`submissions_page`), in any mix."""
    times: dict[str, datetime] = {}
    for payload in payloads:
        columns = _filing_columns(payload)
        for accession, stamp in zip(
            columns["accessionNumber"], columns["acceptanceDateTime"], strict=True
        ):
            times[accession] = _parse_utc(stamp)
    return times


@_fail_closed
def parse_submissions(
    payload: Mapping[str, Any], pages: Sequence[Mapping[str, Any]] = ()
) -> list[FilingIndexEntry]:
    """Every filing in a submissions payload and its older pages, as index
    entries with exact acceptance times, sorted by acceptance."""
    cik = _cik(payload["cik"])
    name = str(payload["name"])
    entries: list[FilingIndexEntry] = []
    for part in (payload, *pages):
        columns = _filing_columns(part)
        for form, accession, stamp in zip(
            columns["form"], columns["accessionNumber"], columns["acceptanceDateTime"], strict=True
        ):
            entries.append(FilingIndexEntry(cik, name, form, accession, _parse_utc(stamp)))
    return sorted(entries, key=lambda e: (e.accepted_at, e.accession))


@dataclass(frozen=True)
class UnstampedFiling:
    """An index row whose acceptance time is unknown: reported, not stamped."""

    cik: str
    company_name: str
    form: str
    accession: str
    filed_on: date


@dataclass(frozen=True)
class FilingIndexParse:
    entries: tuple[FilingIndexEntry, ...]
    unstamped: tuple[UnstampedFiling, ...]


_INDEX_ROW = re.compile(
    r"^(?P<form>\S(?:.*?\S)?)\s{2,}(?P<name>\S.*?)\s+(?P<cik>\d+)\s+"
    r"(?P<filed>\d{4}-\d{2}-\d{2})\s+edgar/data/\d+/(?P<accession>\d{10}-\d{2}-\d{6})\.txt\s*$"
)
# A row whose company-name column is blank (EDGAR has such rows, e.g. a 1997 SC 13D). Tried
# only when `_INDEX_ROW` fails, so a one-character form can never absorb the name. The form
# is capped at 16 characters (the form column is 17 wide, the name column 62) and the gap
# must be at least 40 spaces, so a form and a name separated by one space, whatever the form
# length, never pass as a long form with a blank name (#358).
_INDEX_ROW_BLANK_NAME = re.compile(
    r"^(?P<form>\S(?:.{0,14}\S)?)\s{40,}(?P<cik>\d+)\s+"
    r"(?P<filed>\d{4}-\d{2}-\d{2})\s+edgar/data/\d+/(?P<accession>\d{10}-\d{2}-\d{6})\.txt\s*$"
)


@_fail_closed
def parse_filing_index(text: str, acceptance: Mapping[str, datetime]) -> FilingIndexParse:
    """Rows of a quarterly `form.idx`, stamped from `acceptance`
    (`acceptance_times`); rows with no acceptance time go to `unstamped`.
    A data row (one naming `edgar/data/`) that does not parse raises. A row whose
    company-name column is blank (EDGAR has such rows, e.g. a 1997 SC 13D) parses with
    an empty name (#358)."""
    entries: list[FilingIndexEntry] = []
    unstamped: list[UnstampedFiling] = []
    for line in text.splitlines():
        match = _INDEX_ROW.match(line) or _INDEX_ROW_BLANK_NAME.match(line)
        if match is None:
            if "edgar/data/" in line:
                raise ValueError(f"form.idx row does not parse: {line.strip()!r}")
            continue
        cik, accession = _cik(match["cik"]), match["accession"]
        form, name = match["form"], match.groupdict().get("name") or ""
        accepted_at = acceptance.get(accession)
        if accepted_at is None:
            filed_on = date.fromisoformat(match["filed"])
            unstamped.append(UnstampedFiling(cik, name, form, accession, filed_on))
        else:
            entries.append(FilingIndexEntry(cik, name, form, accession, accepted_at))
    entries.sort(key=lambda e: (e.accepted_at, e.accession, e.cik))
    return FilingIndexParse(tuple(entries), tuple(unstamped))


# --- companies snapshot ----------------------------------------------------


@_fail_closed
def parse_company_tickers(
    payload: Mapping[str, Any], fetched_at: datetime
) -> list[CompanySnapshotEntry]:
    """`company_tickers_exchange.json` rows, each known at `fetched_at`.
    A row with no ticker or no exchange is skipped."""
    fetched_at = ensure_tz_aware_utc(fetched_at, field_name="fetched_at")
    fields = list(payload["fields"])
    at = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    entries: list[CompanySnapshotEntry] = []
    for row in payload["data"]:
        ticker, exchange = row[at["ticker"]], row[at["exchange"]]
        if not ticker or not exchange:
            continue
        entries.append(
            CompanySnapshotEntry(
                _cik(row[at["cik"]]),
                str(row[at["name"]]),
                str(ticker),
                normalize_exchange(str(exchange)),
                fetched_at,
            )
        )
    return entries


# --- company facts ----------------------------------------------------------


@dataclass(frozen=True)
class UnstampedFact:
    """A company-facts entry whose accession has no known acceptance time."""

    cik: str
    fact_name: str
    accession: str
    filed_on: date


@dataclass(frozen=True)
class CompanyFactsParse:
    facts: tuple[FactRecord, ...]
    unstamped: tuple[UnstampedFact, ...]


@_fail_closed
def parse_company_facts(
    payload: Mapping[str, Any], names: Iterable[str], acceptance: Mapping[str, datetime]
) -> CompanyFactsParse:
    """Share-count entries named in `names` (any taxonomy, any unit) from a
    company-facts payload, undimensioned (`class_member = ""`), stamped
    from `acceptance`; entries it has no time for go to `unstamped`."""
    cik = _cik(payload["cik"])
    wanted = set(names)
    facts: list[FactRecord] = []
    unstamped: list[UnstampedFact] = []
    for concepts in payload["facts"].values():
        for fact_name, concept in concepts.items():
            if fact_name not in wanted:
                continue
            for entries in concept["units"].values():
                for entry in entries:
                    accepted_at = acceptance.get(entry["accn"])
                    if accepted_at is None:
                        filed_on = date.fromisoformat(entry["filed"])
                        unstamped.append(UnstampedFact(cik, fact_name, entry["accn"], filed_on))
                        continue
                    facts.append(
                        FactRecord(
                            cik,
                            fact_name,
                            date.fromisoformat(entry["end"]),
                            "",
                            float(entry["val"]),
                            entry["accn"],
                            accepted_at,
                        )
                    )
    facts.sort(key=lambda f: (f.accepted_at, f.accession, f.fact_name, f.as_of_date))
    return CompanyFactsParse(tuple(facts), tuple(unstamped))


# --- SGML header ---------------------------------------------------------------

_HEADER_FIELD = re.compile(r"^\s*(?P<key>[A-Z][A-Z0-9 -]*?):\s*(?P<value>.*?)\s*$")
_SIC = re.compile(r"\[(\d{4})\]")


@_fail_closed
def parse_sgml_header(text: str, cik: str | None = None) -> FilingHeader:
    """A filing's SGML header: form, accession, acceptance (Eastern time in
    the header, returned as UTC) and the issuer's CIK and SIC. The issuer is
    the SUBJECT COMPANY when there is one (a 25-NSE is filed by the
    exchange), else the FILER. A header naming several issuers (a combined
    10-K has one FILER block per co-registrant) raises unless `cik` picks
    one. A missing SIC is `None`.

    Only the text before `</SEC-HEADER>` is read: the fetched range goes on
    into the filer's own documents, whose text must never be taken for a
    header field. A range cut before `</SEC-HEADER>` raises."""
    header, closed, _ = text.partition("</SEC-HEADER>")
    if not closed:
        raise ValueError("SGML header is truncated: no </SEC-HEADER>")
    text = header
    stamp = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", text)
    if stamp is None:
        raise ValueError("SGML header has no ACCEPTANCE-DATETIME")
    naive = datetime.strptime(stamp.group(1), "%Y%m%d%H%M%S")  # noqa: DTZ007
    # fold=1: in the repeated hour when clocks go back, take the later
    # (EST) instant, never the earlier one.
    accepted_at = naive.replace(tzinfo=_EASTERN, fold=1).astimezone(UTC)

    fields: dict[str, str] = {}
    blocks: dict[str, list[dict[str, str]]] = {}
    block: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("<") or not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            block = {}
            blocks.setdefault(line.strip().rstrip(":"), []).append(block)
            continue
        match = _HEADER_FIELD.match(line)
        if match is None:
            continue
        key, value = match["key"], match["value"]
        if block is not None and line.startswith((" ", "\t")):
            block.setdefault(key, value)
        else:
            fields.setdefault(key, value)
    issuers = blocks.get("SUBJECT COMPANY") or blocks.get("FILER") or []
    by_cik = {_cik(b["CENTRAL INDEX KEY"]): b for b in issuers if b.get("CENTRAL INDEX KEY")}
    if not by_cik:
        raise ValueError("SGML header names no CENTRAL INDEX KEY")
    if cik is not None:
        if _cik(cik) not in by_cik:
            raise ValueError(f"SGML header names no issuer {cik!r}")
        chosen = _cik(cik)
    elif len(by_cik) == 1:
        chosen = next(iter(by_cik))
    else:
        raise ValueError(f"SGML header names {len(by_cik)} issuers; pass cik")
    issuer = by_cik[chosen]
    sic_match = _SIC.search(issuer.get("STANDARD INDUSTRIAL CLASSIFICATION", ""))
    return FilingHeader(
        cik=chosen,
        accession=fields["ACCESSION NUMBER"],
        form=fields["CONFORMED SUBMISSION TYPE"],
        sic=int(sic_match.group(1)) if sic_match else None,
        accepted_at=accepted_at,
    )


# --- cover page (iXBRL) -----------------------------------------------------------


@dataclass(frozen=True)
class CoverPageParse:
    """A cover page's registered classes, and its share counts per class."""

    cover: CoverPage
    facts: tuple[FactRecord, ...]
    #: Contexts dimensioned by anything but the class axis (a co-registrant's
    #: `dei:LegalEntityAxis`): reported, never read as the filer's own.
    other_contexts: tuple[str, ...] = ()
    #: Listings with a trading symbol but no title or exchange, skipped and
    #: counted (owner decision #224, as `FsnFiling.incomplete_listings`;
    #: #609 C1): the filing's shares and complete listings are kept.
    incomplete_listings: int = 0


def _is_nil(element: Any) -> bool:
    """Whether an iXBRL fact element carries `xsi:nil="true"` (any prefix:
    filers write `xs:nil` too): a fact that reports no value."""
    return any(
        str(key).rsplit(":", 1)[-1].lower() == "nil" and str(value).strip().lower() == "true"
        for key, value in element.attrib.items()
    )


@dataclass(frozen=True)
class _DeiFacts:
    #: (local name, value, context) of each cover concept, in document order.
    cover: list[tuple[str, str, dict[str, Any]]]
    #: The `dei:EntityCentralIndexKey` values, read only to name the filer
    #: of a cover that carries no cover concept (#609 C3).
    ciks: set[str]


def _dei_facts(document: bytes, accession: str) -> _DeiFacts:
    """(local name, value, context) of each cover-page `dei:` fact this
    module reads, in document order. A repeated fact (same concept, context
    and value) is kept once; the same concept and context with two values,
    or a value whose iXBRL format did not apply, raises: either would give
    a silently wrong record. A nil fact (`xsi:nil="true"`, #609 C2) reports
    nothing and is skipped."""
    # Imported here: loading `edgar` pulls in the whole package, which only
    # cover-page parsing needs.
    from edgar.documents.strategies.xbrl_extraction import XBRLExtractor

    tree = lxml.html.fromstring(document)
    extractor = XBRLExtractor()  # type: ignore[no-untyped-call]
    seen: dict[tuple[str, str], str] = {}
    out: list[tuple[str, str, dict[str, Any]]] = []
    ciks: set[str] = set()
    for element in tree.iter():
        fact = extractor.extract_fact(element)
        if fact is None or not fact.concept.startswith("dei:"):
            continue
        name = fact.concept.removeprefix("dei:")
        if name == _CIK_CONCEPT:
            ciks.add(str(fact.value).strip())
            continue
        if name not in _COVER_CONCEPTS or _is_nil(element):
            continue
        issue = (fact.metadata or {}).get("format_issue")
        if issue:
            raise ValueError(f"{accession}: dei:{name}: {issue}")
        context = fact.context or {}
        key = (name, str(context.get("id", "")))
        if key in seen:
            if seen[key] != fact.value:
                raise ValueError(f"{accession}: dei:{name} has two values in context {key[1]!r}")
            continue
        seen[key] = fact.value
        out.append((name, fact.value, context))
    return _DeiFacts(out, ciks)


@_fail_closed
def parse_cover_page(document: bytes, *, accession: str, accepted_at: datetime) -> CoverPageParse:
    """The listed classes (`Security12bTitle`, `TradingSymbol`,
    `SecurityExchangeName`, grouped by context) and the
    `EntityCommonStockSharesOutstanding` facts of one periodic filing's
    primary iXBRL document, all known at `accepted_at` (the caller's
    acceptance time for `accession`).

    Each class is one context holding one title, one symbol and one
    exchange; a class listed on a second exchange is a second context. A
    title with no symbol (notes with `NoTradingSymbolFlag`) is not a
    listing; a symbol with no title or no exchange is skipped and counted
    in `incomplete_listings` (owner decision #224, as the FSN path); a fact
    with no context raises. A nil fact (`xsi:nil="true"`) is skipped. A
    context dimensioned by any axis other than the class axis (a
    co-registrant in a combined filing) is skipped and returned in
    `other_contexts`: its shares and listings are not the filer's.

    A cover with no listing, shares or title fact at all (a registrant
    with no listed class that reports no share count) is an empty parse for
    the one CIK its `dei:EntityCentralIndexKey` names; with no such CIK it
    raises."""
    accepted_at = ensure_tz_aware_utc(accepted_at, field_name="accepted_at")
    dei = _dei_facts(document, accession)
    facts = dei.cover
    if not facts and len(dei.ciks) == 1:  # nothing to list or count (#609 C3)
        [raw_cik] = dei.ciks
        if not re.fullmatch(r"\d{1,10}", raw_cik):
            raise ValueError(f"{accession}: cover-page EntityCentralIndexKey is not a CIK")
        return CoverPageParse(CoverPage(_cik(raw_cik), accession, accepted_at, ()), ())
    entities = {(context.get("scheme"), context.get("entity")) for _, _, context in facts}
    if len(entities) != 1:
        raise ValueError(f"{accession}: cover page names {len(entities)} entities")
    scheme, entity = entities.pop()
    if scheme != _CIK_SCHEME or not entity:
        raise ValueError(f"{accession}: cover-page entity is not a CIK: {scheme!r} {entity!r}")
    cik = _cik(entity)

    classes: dict[str, dict[str, str]] = {}
    shares: list[FactRecord] = []
    other: set[str] = set()
    for name, value, context in facts:
        if not context.get("id"):
            raise ValueError(f"{accession}: dei:{name} has no context")
        if set(context.get("dimensions") or {}) - {_CLASS_AXIS}:
            other.add(context["id"])
            continue
        if name != _SHARES_CONCEPT:
            classes.setdefault(context["id"], {})[name] = value
        elif name == _SHARES_CONCEPT:
            member = str(context.get("dimensions", {}).get(_CLASS_AXIS, ""))
            shares.append(
                FactRecord(
                    cik,
                    _SHARES_CONCEPT,
                    date.fromisoformat(context["instant"]),
                    normalize_class_member(member),
                    float(Decimal(value)),
                    accession,
                    accepted_at,
                )
            )

    listings: list[CoverListing] = []
    incomplete = 0
    for group in classes.values():
        title, symbol = group.get("Security12bTitle"), group.get("TradingSymbol")
        exchange = group.get("SecurityExchangeName")
        if symbol is None:
            continue  # notes and other classes with no trading symbol
        if title is None or exchange is None:
            incomplete += 1  # skipped and counted, not a failure (owner, #224; #609 C1)
            continue
        listings.append(CoverListing(title, symbol, normalize_exchange(exchange)))
    return CoverPageParse(
        CoverPage(cik, accession, accepted_at, tuple(listings)),
        tuple(shares),
        tuple(sorted(other)),
        incomplete,
    )


# --- Forms 25 and 25-NSE ------------------------------------------------------------


@_fail_closed
def parse_delisting(
    xml_text: str, *, form: str, accession: str, accepted_at: datetime
) -> DelistingFiling:
    """A Form 25 or 25-NSE primary document (`notificationOfRemoval`): the
    issuer, the class description and the exchange removing it, known at
    `accepted_at`. `effective_on` is left to the store's Rule 12d2-2
    default; the notice itself states no effective date."""
    if form.removesuffix("/A") not in _DELISTING_FORMS:
        raise ValueError(f"{accession}: not a delisting form: {form!r}")
    accepted_at = ensure_tz_aware_utc(accepted_at, field_name="accepted_at")
    # Stdlib expat: no external entities or DTD fetching, and entity
    # expansion is bounded (expat >= 2.4 billion-laughs protection).
    root = ET.fromstring(xml_text)

    def text(path: str) -> str:
        found = root.findtext(path)
        if found is None or not found.strip():
            raise ValueError(f"{accession}: {form} has no {path}")
        return found.strip()

    class_title = (root.findtext("descriptionClassSecurity") or "").strip()
    if not class_title:
        # Owner 2026-10-02 (#609 D1): which class is removed is never
        # guessed (ACCO Brands' 25-NSE names none while the issuer stays
        # listed), so the notice stays a failure for the owner to accept.
        raise ValueError(
            f"{accession}: {form} names no class of security (descriptionClassSecurity is "
            "missing or blank); the class is not guessed, so the notice is not recorded"
        )

    return DelistingFiling(
        cik=_cik(text("issuer/cik")),
        form=form,
        class_title=class_title,
        exchange=normalize_exchange(text("exchange/entityName")),
        accession=accession,
        accepted_at=accepted_at,
    )


# --- SEC Financial Statement and Notes data sets (T11c) ---------------------
#
# `sub.tsv`, `num.tsv`, `txt.tsv` and `dim.tsv` of one FSN period zip, read
# by the caller with DuckDB `read_csv(..., delim='\t', header=true,
# all_varchar=true, quote='')` and passed here as row mappings already
# filtered to the four `dei` cover-page tags. Records carry **no**
# timestamp: `sub.accepted`, `filed` and `period` are never read (module
# docstring "known_at"); T11d stamps these at read time from the
# submissions acceptance cache.

_FSN_LISTING_TAGS = frozenset({"Security12bTitle", "TradingSymbol", "SecurityExchangeName"})
_FSN_CLASS_AXIS = "ClassOfStock"


@dataclass(frozen=True)
class FsnShare:
    """One `EntityCommonStockSharesOutstanding` fact from an FSN `num.tsv` row.

    `as_of_date` is FSN's `ddate`, which FSN **rounds to the nearest month
    end**: Alphabet's cover date 2026-01-28 arrives as 2026-01-31, Apple's
    2025-10-17 as 2025-10-31 (recorded fixtures, #224). It is not the cover's
    own date, it can fall after the filing's acceptance, and it is never a
    `known_at`; T11e decides how FSN shares are dated and de-duplicated.
    When a filing reports a member's count at several ddates, only the
    latest is kept (#609 F1); `facts()` still caps it at the acceptance
    date."""

    class_member: str
    value: float
    as_of_date: date


@dataclass(frozen=True)
class FsnFiling:
    """One accession's cover facts and SIC from the FSN data sets, unstamped."""

    accession: str
    cik: str
    form: str
    sic: int | None
    listings: tuple[CoverListing, ...]
    shares: tuple[FsnShare, ...]
    #: Listings with a trading symbol but no title or exchange, skipped
    #: (owner decision 2026-09-26, #224): the filing's SIC, shares and complete
    #: listings are kept rather than failing the whole accession.
    incomplete_listings: int = 0


@dataclass(frozen=True)
class FsnFailure:
    """An accession whose FSN rows did not parse.

    `error_class` is the underlying exception's type name (`KeyError`,
    `ValueError`, ...): `_fail_closed` chains the original exception as
    `__cause__`, so a malformed-payload error (e.g. a missing column) keeps
    its own class name rather than collapsing to `ValueError` (T11f's
    per-`(error class, base form)` failure-policy grouping needs it)."""

    accession: str
    error: str
    error_class: str


@dataclass(frozen=True)
class FsnParse:
    records: tuple[FsnFiling, ...]
    failures: tuple[FsnFailure, ...]


_CLASS_LETTER_NO_SPACE = re.compile(r"\bClass([A-Z])\b")


def restore_class_letter_space(title: str) -> str:
    """`title` with the space put back between `Class` and a single class
    letter. FSN drops the filing's non-breaking space, so Alphabet's
    `Class&#160;A Common Stock` arrives as `ClassA Common Stock` (recorded
    2026_02, #224), which ingest's class-letter rule would not match. Pure;
    other lost NBSPs are left as FSN gives them."""
    return _CLASS_LETTER_NO_SPACE.sub(r"Class \1", title)


def _fsn_title_key(title: str) -> str:
    """`title` with the class-letter space restored and all whitespace
    removed: two FSN copies of one title that differ only by a dropped
    non-breaking space ("ClassA" / "Class A", "No ParValue" / "No Par
    Value", #609 F4) have the same key."""
    return "".join(restore_class_letter_space(title).split())


def _fsn_spaced_title(first: str, second: str) -> str:
    """Of two titles with the same `_fsn_title_key`, the one that kept its
    spaces (the longer once whitespace runs are collapsed), whatever their
    order; a tie keeps the larger string, so the result never depends on
    row order."""
    candidates = (" ".join(restore_class_letter_space(t).split()) for t in (first, second))
    return max(candidates, key=lambda t: (len(t), t))


def _fsn_segments(raw: str) -> dict[str, str] | None:
    """`dim.segments` (e.g. `"ClassOfStock=CommonClassA;"`) to `{axis:
    member}`, or `None` if it names any axis other than `ClassOfStock`."""
    pairs: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        axis, _, member = part.partition("=")
        pairs[axis.strip()] = member.strip()
    if set(pairs) - {_FSN_CLASS_AXIS}:
        return None
    return pairs


def _fsn_ddate(value: str) -> date:
    text = value.strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"malformed ddate: {value!r}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _fsn_is_coreg(row: Mapping[str, str]) -> bool:
    return bool((row.get("coreg") or "").strip())


def _fsn_class_member(row: Mapping[str, str], dim_segments: Mapping[str, str]) -> str | None:
    """The row's class member: `""` when `dimn` is `0` (undimensioned), or
    `None` when the row should be excluded (a non-`ClassOfStock` axis, or a
    `dimh` with no matching `dim.tsv` row)."""
    dimn = (row.get("dimn") or "0").strip()
    if dimn in ("", "0"):
        return ""
    segments_raw = dim_segments.get((row.get("dimh") or "").strip())
    if segments_raw is None:
        return None
    # An invalid byte or a tab `_fsn_rows` replaced (#498), anywhere in a
    # ClassOfStock segment: beside the axis key it would read as another
    # axis and drop the class silently, so the accession fails instead.
    if "\ufffd" in segments_raw and _FSN_CLASS_AXIS in segments_raw:
        raise ValueError(
            f"{row.get('adsh', '')}: undecodable byte or embedded tab in "
            f"segments {segments_raw[:60]!r}"
        )
    segments = _fsn_segments(segments_raw)
    if segments is None:
        return None
    return normalize_class_member(segments.get(_FSN_CLASS_AXIS, ""))


@_fail_closed
def _parse_one_fsn_filing(
    accession: str,
    sub_row: Mapping[str, str],
    num_rows: Sequence[Mapping[str, str]],
    txt_rows: Sequence[Mapping[str, str]],
    dim_segments: Mapping[str, str],
) -> FsnFiling:
    raw_cik = str(sub_row["cik"]).strip()
    if not re.fullmatch(r"\d{1,10}", raw_cik):  # it names a cache file downstream
        raise ValueError(f"{accession}: malformed CIK {raw_cik[:20]!r}")
    cik = _cik(raw_cik)
    form = str(sub_row["form"])
    sic_raw = (sub_row.get("sic") or "").strip()
    sic = int(sic_raw) if sic_raw else None

    groups: dict[str, dict[str, str]] = {}
    for row in txt_rows:
        tag = row.get("tag")
        if tag not in _FSN_LISTING_TAGS or _fsn_is_coreg(row):
            continue
        member = _fsn_class_member(row, dim_segments)
        if member is None:
            continue
        group, value = groups.setdefault(member, {}), row.get("value") or ""
        if "\ufffd" in value:  # an invalid byte or a tab `_fsn_rows` replaced (#455, #498)
            raise ValueError(
                f"{accession}: undecodable byte or embedded tab in {tag} ({member or 'no class'})"
            )
        held = group.get(tag, value)
        if held != value:
            if tag != "Security12bTitle" or _fsn_title_key(held) != _fsn_title_key(value):
                # fail closed, as parse_cover_page does
                raise ValueError(f"{accession}: two values for {tag} ({member or 'no class'})")
            value = _fsn_spaced_title(held, value)  # FSN dropped a space in one copy (#609 F4)
        group[tag] = value

    listings: list[CoverListing] = []
    incomplete = 0
    for group in groups.values():
        title, symbol = group.get("Security12bTitle"), group.get("TradingSymbol")
        exchange = group.get("SecurityExchangeName")
        if symbol is None:
            continue  # notes and other classes with no trading symbol
        if title is None or exchange is None:
            incomplete += 1  # skipped and counted, not a failure (owner, #224)
            continue
        listings.append(
            CoverListing(restore_class_letter_space(title), symbol, normalize_exchange(exchange))
        )

    # member -> ddate -> value. A filing may report its share count at several
    # ddates (a prior year-end beside the cover date, #609 F1): every ddate is
    # checked, and the latest is kept per member.
    dated: dict[str, dict[date, float]] = {}
    for row in num_rows:
        if row.get("tag") != _SHARES_CONCEPT or _fsn_is_coreg(row):
            continue
        member = _fsn_class_member(row, dim_segments)
        if member is None:
            continue
        raw_value = row.get("value")
        if raw_value is None or not str(raw_value).strip():
            continue  # FSN's NULL: an `xsi:nil` fact reports no count (#609 F3)
        ddate, count = _fsn_ddate(row["ddate"]), float(Decimal(raw_value))
        # Fail closed: nothing in the rows read picks one of two counts (#609 F2).
        if dated.setdefault(member, {}).setdefault(ddate, count) != count:
            raise ValueError(
                f"{accession}: two share values for {member or 'no class'} on {ddate.isoformat()}"
            )
    shares = tuple(FsnShare(m, values[max(values)], max(values)) for m, values in dated.items())

    return FsnFiling(accession, cik, form, sic, tuple(listings), shares, incomplete)


def parse_fsn(
    sub: Iterable[Mapping[str, str]],
    num: Iterable[Mapping[str, str]],
    txt: Iterable[Mapping[str, str]],
    dim: Iterable[Mapping[str, str]],
) -> FsnParse:
    """One unstamped record per accession in `sub`, from one FSN period's
    members. A `num`/`txt` row carrying a non-null `coreg`, or naming any
    axis other than `ClassOfStock`, is excluded exactly as `parse_cover_page`
    skips other axes. An accession whose rows do not parse yields one
    failure; every other accession still parses (never raises for one bad
    accession)."""
    dim_segments = {str(row["dimhash"]): str(row["segments"]) for row in dim}
    num_by_adsh: dict[str, list[Mapping[str, str]]] = {}
    for row in num:
        num_by_adsh.setdefault(str(row["adsh"]), []).append(row)
    txt_by_adsh: dict[str, list[Mapping[str, str]]] = {}
    for row in txt:
        txt_by_adsh.setdefault(str(row["adsh"]), []).append(row)

    records: list[FsnFiling] = []
    failures: list[FsnFailure] = []
    for sub_row in sub:
        accession = str(sub_row["adsh"])
        try:
            records.append(
                _parse_one_fsn_filing(
                    accession,
                    sub_row,
                    num_by_adsh.get(accession, []),
                    txt_by_adsh.get(accession, []),
                    dim_segments,
                )
            )
        except ValueError as error:
            error_class = (
                type(error.__cause__).__name__ if error.__cause__ else type(error).__name__
            )
            failures.append(FsnFailure(accession, str(error), error_class))
    return FsnParse(tuple(records), tuple(failures))
