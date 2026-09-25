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
uses (`NYSE`, `NASDAQ`, `NYSE_AMERICAN`), plus `NYSE_ARCA`, `CBOE`, `OTC`,
and an upper-case slug for anything else, so a cover page's
`SecurityExchangeName`, the tickers snapshot's `Nasdaq` and a Form 25's
"Nasdaq Stock Market LLC" agree.

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

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import lxml.html  # type: ignore[import-untyped]
from edgar.documents.strategies.xbrl_extraction import XBRLExtractor

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


def _cik(value: object) -> str:
    return f"{int(str(value)):010d}"


def _parse_utc(text: str) -> datetime:
    return ensure_tz_aware_utc(
        datetime.fromisoformat(text.replace("Z", "+00:00")), field_name="acceptanceDateTime"
    )


# --- exchanges -----------------------------------------------------------

#: Checked in order: "NYSE Arca" and "NYSE American" before plain NYSE.
_EXCHANGE_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ARCA",), "NYSE_ARCA"),
    (
        ("NYSEAMER", "NYSE AMERICAN", "NYSE MKT", "NYSEMKT", "AMEX", "AMERICAN STOCK"),
        "NYSE_AMERICAN",
    ),
    (("NASDAQ",), "NASDAQ"),
    (("NYSE", "NEW YORK STOCK EXCHANGE"), "NYSE"),
    (("CBOE", "BATS"), "CBOE"),
    (("OTC",), "OTC"),
)


def normalize_exchange(raw: str) -> str:
    """The exchange code config uses for `raw` (see the module docstring)."""
    text = " ".join(raw.upper().replace(",", " ").replace(".", " ").split())
    for needles, code in _EXCHANGE_RULES:
        if any(needle in text for needle in needles):
            return code
    return re.sub(r"[^A-Z0-9]+", "_", text).strip("_")


# --- submissions and the filing index -------------------------------------


def _filing_columns(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The column lists of a submissions payload or of one older page."""
    filings = payload.get("filings")
    if isinstance(filings, Mapping):
        recent = filings["recent"]
        assert isinstance(recent, Mapping)
        return recent
    return payload


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


def parse_filing_index(text: str, acceptance: Mapping[str, datetime]) -> FilingIndexParse:
    """Rows of a quarterly `form.idx`, stamped from `acceptance`
    (`acceptance_times`); rows with no acceptance time go to `unstamped`."""
    entries: list[FilingIndexEntry] = []
    unstamped: list[UnstampedFiling] = []
    for line in text.splitlines():
        match = _INDEX_ROW.match(line)
        if match is None:
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


def parse_sgml_header(text: str) -> FilingHeader:
    """A filing's SGML header: form, accession, acceptance (Eastern time in
    the header, returned as UTC) and the issuer's CIK and SIC. The issuer is
    the SUBJECT COMPANY when there is one (a 25-NSE is filed by the
    exchange), else the FILER. A missing SIC is `None`."""
    stamp = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", text)
    if stamp is None:
        raise ValueError("SGML header has no ACCEPTANCE-DATETIME")
    naive = datetime.strptime(stamp.group(1), "%Y%m%d%H%M%S")  # noqa: DTZ007
    accepted_at = naive.replace(tzinfo=_EASTERN).astimezone(UTC)

    fields: dict[str, str] = {}
    blocks: dict[str, dict[str, str]] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("<") or not line.strip():
            continue
        if not line.startswith((" ", "\t")) and line.rstrip().endswith(":"):
            section = line.strip().rstrip(":")
            continue
        match = _HEADER_FIELD.match(line)
        if match is None:
            continue
        key, value = match["key"], match["value"]
        if section and line.startswith((" ", "\t")):
            blocks.setdefault(section, {}).setdefault(key, value)
        else:
            fields.setdefault(key, value)
    issuer = blocks.get("SUBJECT COMPANY") or blocks.get("FILER") or {}
    cik = issuer.get("CENTRAL INDEX KEY")
    if cik is None:
        raise ValueError("SGML header names no CENTRAL INDEX KEY")
    sic_match = _SIC.search(issuer.get("STANDARD INDUSTRIAL CLASSIFICATION", ""))
    return FilingHeader(
        cik=_cik(cik),
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


def _dei_facts(document: bytes) -> list[tuple[str, str, dict[str, Any]]]:
    """(local name, value, context) of every `dei:` fact, in document order."""
    tree = lxml.html.fromstring(document)
    extractor = XBRLExtractor()  # type: ignore[no-untyped-call]
    out: list[tuple[str, str, dict[str, Any]]] = []
    for element in tree.iter():
        fact = extractor.extract_fact(element)
        if fact is None or not fact.concept.startswith("dei:"):
            continue
        out.append((fact.concept.removeprefix("dei:"), fact.value, fact.context or {}))
    return out


def parse_cover_page(document: bytes, *, accession: str, accepted_at: datetime) -> CoverPageParse:
    """The listed classes (`Security12bTitle`, `TradingSymbol`,
    `SecurityExchangeName`, grouped by context) and the
    `EntityCommonStockSharesOutstanding` facts of one periodic filing's
    primary iXBRL document, all known at `accepted_at` (the caller's
    acceptance time for `accession`)."""
    accepted_at = ensure_tz_aware_utc(accepted_at, field_name="accepted_at")
    facts = _dei_facts(document)
    ciks = {context["entity"] for _, _, context in facts if context.get("entity")}
    if len(ciks) != 1:
        raise ValueError(f"{accession}: cover page names {len(ciks)} entities")
    cik = _cik(ciks.pop())

    classes: dict[str, dict[str, list[str]]] = {}
    shares: list[FactRecord] = []
    for name, value, context in facts:
        if name in ("Security12bTitle", "TradingSymbol", "SecurityExchangeName"):
            group = classes.setdefault(context.get("id", ""), {})
            group.setdefault(name, []).append(value)
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
    for group in classes.values():
        titles, symbols = group.get("Security12bTitle", []), group.get("TradingSymbol", [])
        if not titles or not symbols:
            continue  # no trading symbol (notes), or a symbol with no class
        for exchange in group.get("SecurityExchangeName", []):
            listings.append(CoverListing(titles[0], symbols[0], normalize_exchange(exchange)))
    return CoverPageParse(CoverPage(cik, accession, accepted_at, tuple(listings)), tuple(shares))


# --- Forms 25 and 25-NSE ------------------------------------------------------------


def parse_delisting(
    xml_text: str, *, form: str, accession: str, accepted_at: datetime
) -> DelistingFiling:
    """A Form 25 or 25-NSE primary document (`notificationOfRemoval`): the
    issuer, the class description and the exchange removing it, known at
    `accepted_at`. `effective_on` is left to the store's Rule 12d2-2
    default; the notice itself states no effective date."""
    if form.removesuffix("/A") not in _DELISTING_FORMS:
        raise ValueError(f"{accession}: not a delisting form: {form!r}")
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
