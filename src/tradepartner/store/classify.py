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


def _classify(
    t: datetime,
    evidence: _Evidence,
    listings: Sequence[Row],
    benchmark: Row | None,
) -> tuple[str, str, int | None, str]:
    """(security_type, rule, sic, provenance) from what is known at `t`."""
    forms = [form for known_at, form in evidence.forms if known_at <= t]
    sics = [sic for known_at, sic in evidence.sics if known_at <= t]
    sic = sics[-1] if sics else None
    if benchmark is not None:
        return "etf", "benchmark_config", sic, benchmark["provenance"]
    if any(form in FUND_FORMS for form in forms):
        return "fund", "fund_form", sic, "filing"
    if any(form in F6_FORMS for form in forms):
        return "depositary", "f6_depositary", sic, "filing"
    status = [form for form in forms if form in DOMESTIC_FORMS | FOREIGN_FORMS]
    domestic = bool(status) and status[-1] in DOMESTIC_FORMS
    if status and not domestic:
        return "foreign", "foreign_form", sic, "filing"
    if sic == SPAC_SIC:
        return "spac", "sic_6770", sic, "filing"
    known = [row for row in listings if row["known_at"] <= t]
    titled = [row for row in known if row["class_title"] is not None]
    if titled:
        security_type, rule = _title_type(titled[-1]["class_title"])
        return security_type, rule, sic, "filing"
    if known:
        suffix_type = ticker_suffix_type(known[-1]["ticker"])
        if suffix_type is not None:
            return suffix_type, f"suffix_{suffix_type}", sic, "snapshot_static"
    if domestic:
        return COMMON, "common_default", sic, "filing"
    return UNCLASSIFIABLE, "no_rule_matched", sic, "filing"


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
        headers = source.filing_headers(cik, settings.master.issuer_forms)
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
    """
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    securities = list(master.securities)
    issuers = {row["cik"] for row in securities if not row["benchmark"]}
    evidence = _evidence(source, issuers, settings)
    listings: dict[str, list[Row]] = defaultdict(list)
    for row in sorted(master.listings, key=lambda r: r["known_at"]):
        listings[row["security_id"]].append(row)

    rows: list[Row] = []
    for security in sorted(securities, key=lambda r: r["security_id"]):
        security_id = security["security_id"]
        benchmark = security if security["benchmark"] else None
        facts = evidence.get(security["cik"], _Evidence((), ()))
        if benchmark is not None:
            facts = _Evidence((), ())
        stamps = {k for k, _ in facts.forms} | {k for k, _ in facts.sics}
        stamps |= {row["known_at"] for row in listings[security_id]}
        late = [stamp for stamp in stamps if stamp > ingested_at]
        if late:
            raise ValueError(
                f"{security_id}: evidence at {max(late).isoformat()} is after "
                f"ingested_at {ingested_at.isoformat()}"
            )
        start = security["known_at"]
        previous: tuple[str, str, int | None] | None = None
        for t in sorted({start} | {stamp for stamp in stamps if stamp > start}):
            security_type, rule, sic, provenance = _classify(
                t, facts, listings[security_id], benchmark
            )
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
