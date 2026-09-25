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


@_fail_closed
def parse_filing_index(text: str, acceptance: Mapping[str, datetime]) -> FilingIndexParse:
    """Rows of a quarterly `form.idx`, stamped from `acceptance`
    (`acceptance_times`); rows with no acceptance time go to `unstamped`.
    A data row (one naming `edgar/data/`) that does not parse raises."""
    entries: list[FilingIndexEntry] = []
    unstamped: list[UnstampedFiling] = []
    for line in text.splitlines():
        match = _INDEX_ROW.match(line)
        if match is None:
            if "edgar/data/" in line:
                raise ValueError(f"form.idx row does not parse: {line.strip()!r}")
            continue
        cik, accession = _cik(match["cik"]), match["accession"]
        form, name = match["form"], match["name"]
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


def _dei_facts(document: bytes, accession: str) -> list[tuple[str, str, dict[str, Any]]]:
    """(local name, value, context) of each cover-page `dei:` fact this
    module reads, in document order. A repeated fact (same concept, context
    and value) is kept once; the same concept and context with two values,
    or a value whose iXBRL format did not apply, raises: either would give
    a silently wrong record."""
    # Imported here: loading `edgar` pulls in the whole package, which only
    # cover-page parsing needs.
    from edgar.documents.strategies.xbrl_extraction import XBRLExtractor

    tree = lxml.html.fromstring(document)
    extractor = XBRLExtractor()  # type: ignore[no-untyped-call]
    seen: dict[tuple[str, str], str] = {}
    out: list[tuple[str, str, dict[str, Any]]] = []
    for element in tree.iter():
        fact = extractor.extract_fact(element)
        if fact is None or not fact.concept.startswith("dei:"):
            continue
        name = fact.concept.removeprefix("dei:")
        if name not in _COVER_CONCEPTS:
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
    return out


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
    listing; a symbol with no title or no exchange, or a fact with no
    context, raises. A context dimensioned by any axis other than the class
    axis (a co-registrant in a combined filing) is skipped and returned in
    `other_contexts`: its shares and listings are not the filer's."""
    accepted_at = ensure_tz_aware_utc(accepted_at, field_name="accepted_at")
    facts = _dei_facts(document, accession)
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
                    member,
                    float(Decimal(value)),
                    accession,
                    accepted_at,
                )
            )

    listings: list[CoverListing] = []
    for context_id, group in classes.items():
        title, symbol = group.get("Security12bTitle"), group.get("TradingSymbol")
        exchange = group.get("SecurityExchangeName")
        if symbol is None:
            continue  # notes and other classes with no trading symbol
        if title is None or exchange is None:
            raise ValueError(
                f"{accession}: symbol {symbol!r} ({context_id}) lacks a title or exchange"
            )
        listings.append(CoverListing(title, symbol, normalize_exchange(exchange)))
    return CoverPageParse(
        CoverPage(cik, accession, accepted_at, tuple(listings)), tuple(shares), tuple(sorted(other))
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

    return DelistingFiling(
        cik=_cik(text("issuer/cik")),
        form=form,
        class_title=text("descriptionClassSecurity"),
        exchange=normalize_exchange(text("exchange/entityName")),
        accession=accession,
        accepted_at=accepted_at,
    )
