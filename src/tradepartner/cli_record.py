"""Owner-run fixture recorder: `python -m tradepartner.cli_record`.

Fetches a small, fixed list of real raw payloads from Alpaca and SEC EDGAR
through `adapters/alpaca_raw.py` and `adapters/edgar_raw.py`, **scrubs**
every payload (secrets, email addresses, key-shaped tokens, `User-Agent`/
`Authorization`/Alpaca-key headers), and writes the result as JSON or text
under `tests/fixtures/{alpaca,edgar}/`. T11/T12's parsers are tested
against these recordings; CI never calls this module or the network (spec
"Users & usage": "Once, at the start, the owner runs a recording script
with their own keys").

Run by the owner (plan task T3) with real `ALPACA_API_KEY` /
`ALPACA_API_SECRET` / `SEC_EDGAR_USER_AGENT` set. Exits non-zero with a
message naming every missing (or blank) secret, before any network call,
so the owner sees exactly what to set (spec req: "recorder must exit
non-zero with a clear message when secrets are missing").
"""

from __future__ import annotations

import base64
import gzip
import io
import itertools
import json
import re
import sys
import zipfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest, OrderRequest
from pydantic import SecretStr

from tradepartner import calendar
from tradepartner.adapters import alpaca_raw, alpaca_trading_raw, edgar_raw
from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingError, AlpacaTradingRaw
from tradepartner.config import Settings, get_settings, secret_values

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
ALPACA_FIXTURES_DIR = FIXTURES_ROOT / "alpaca"
#: T48: paper trading responses, recorded by the owner with paper keys (T48b).
ALPACA_PAPER_FIXTURES_DIR = ALPACA_FIXTURES_DIR / "paper"
EDGAR_FIXTURES_DIR = FIXTURES_ROOT / "edgar"
#: T11c: the FSN data-set page and the two recorded periods' trimmed members.
EDGAR_FSN_FIXTURES_DIR = EDGAR_FIXTURES_DIR / "fsn"

SCRUBBED = "<scrubbed>"
RECORDED_AT_FILE = FIXTURES_ROOT / "recorded_at.json"
# `{relative fixture path: UTC ISO time}` written once at the end of a run. The time is
# taken when the payload is written, seconds after its fetch, so it is an upper bound on
# the fetch time: the right `known_at` for snapshot-provenance records (spec line 70,
# "recorded fetch time"), never earlier than the truth (quant-auditor, #87).
_recorded_at: dict[str, str] = {}

# --- scrub patterns (also imported by tests/test_fixture_scrub.py, so the
# walking test and this module's own scrubbing stay in lockstep) -----------

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Alpaca key IDs start "PK" (paper) or "AK" (live) followed by a long
# alnum run (ADR 0003 "Verify"/T3: exact format confirmed against the
# owner's real keys, but the prefix convention is documented by Alpaca).
ALPACA_KEY_PREFIX_PATTERN = re.compile(r"\b(?:PK|AK)[A-Za-z0-9]{16,}\b")
# A real label ("api_key", "secret_key", "secret", "token" -- word-bounded,
# so "KeyManagementPersonnelCompensation" -- a real us-gaap XBRL concept
# name that showed up in a real company-facts payload -- never matches),
# a separator, then a 20+ char token. Narrower than an earlier version that
# matched any "key"/"secret" substring immediately followed by 20+ alnum
# chars, which also matched inside ordinary camelCase identifiers and
# base64 image data (T2 review round 2, safety-reviewer MUST FIX).
KEY_SHAPED_PATTERN = re.compile(
    r"\b(?:api[_-]?key|secret[_-]?key|secret|token)[\"'=:\s]{1,5}[A-Za-z0-9/+_=-]{20,}",
    re.IGNORECASE,
)
# A `User-Agent` header left un-scrubbed in serialized JSON.
USER_AGENT_HEADER_PATTERN = re.compile(r'(?i)"user-agent"\s*:\s*"(?!<scrubbed>)[^"]*"')
# A `User-Agent:`/`Authorization:`/`APCA-API-KEY-ID:`/`APCA-API-SECRET-KEY:`
# line (`:` or `=`) in a plain-text payload -- the same field names
# `_SENSITIVE_HEADER_KEYS` below scrubs wholly in JSON, for a payload where
# they show up as request/response header text instead (round 3,
# safety-reviewer).
SENSITIVE_HEADER_LINE_PATTERN = re.compile(
    r"(?im)^\s*(?:user-agent|authorization|apca-api-(?:key-id|secret-key))\s*[:=].*"
)

# A paper account number ("PA" then letters and digits, at least one digit, so an
# upper-case word such as "PARTNERSHIP" never matches). Checked only over the paper
# recordings by the walk test; the recorder scrubs account ids by field name and by
# the recorded account's own values (T48).
PAPER_ACCOUNT_NUMBER_PATTERN = re.compile(r"\bPA(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")
_ACCOUNT_ID_KEYS = frozenset({"account_number", "account_id"})

# Field names whose *entire* value is inherently secret, regardless of its
# shape -- unlike the content patterns above, which scrub only the matched
# span within an otherwise-kept string.
_SENSITIVE_HEADER_KEYS = frozenset(
    {"authorization", "apca-api-key-id", "apca-api-secret-key", "user-agent"}
)
_KEY_LIKE_FIELD_NAME = re.compile(r"(?i)key|secret")
_TOKEN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9/+_=-]{20,}$")

_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    EMAIL_PATTERN,
    ALPACA_KEY_PREFIX_PATTERN,
    KEY_SHAPED_PATTERN,
)


def _secrets_pattern(secrets: Iterable[str]) -> re.Pattern[str] | None:
    """One alternation matching any of `secrets` as a substring, longest first.

    Longest-first so a secret that happens to be a prefix of another
    configured secret doesn't get matched (and only partially scrubbed)
    before the longer one is tried.
    """
    values = sorted({s for s in secrets if s}, key=len, reverse=True)
    if not values:
        return None
    return re.compile("|".join(re.escape(v) for v in values))


def _scrub_content(value: str, secrets_pattern: re.Pattern[str] | None) -> tuple[str, int]:
    """Replace only the matched span of every secret/email/key-shaped/Alpaca-prefix
    hit in `value` (never the whole string) with `"<scrubbed>"`; return `(result, count)`.
    """
    count = 0
    if secrets_pattern is not None:
        value, n = secrets_pattern.subn(SCRUBBED, value)
        count += n
    for pattern in _CONTENT_PATTERNS:
        value, n = pattern.subn(SCRUBBED, value)
        count += n
    return value, count


def scrub_json(value: Any, *, secrets: Iterable[str]) -> tuple[Any, int]:
    """Recursively scrub a JSON-like structure; returns `(scrubbed, replacement_count)`.

    A string under a sensitive header key (`Authorization`,
    `APCA-API-KEY-ID`, `APCA-API-SECRET-KEY`, `User-Agent`,
    case-insensitive) or a `key`/`secret`-named field holding a bare token
    is replaced *wholly*, since by construction the entire value is
    secret. Every other string only has the matched span of each
    configured secret, email address, Alpaca-style key prefix, or
    key-shaped token replaced -- surrounding text is preserved. Dicts and
    lists are walked; every other type (numbers, bools, `None`) passes
    through unchanged.
    """
    secrets_pattern = _secrets_pattern(secrets)
    return _scrub_json_value(value, secrets_pattern, key=None)


def _scrub_whole_if_changed(value: str) -> tuple[str, int]:
    return (value, 0) if value == SCRUBBED else (SCRUBBED, 1)


def _scrub_json_value(
    value: Any, secrets_pattern: re.Pattern[str] | None, *, key: str | None
) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        count = 0
        result: dict[Any, Any] = {}
        for k, v in value.items():
            result[k], n = _scrub_json_value(v, secrets_pattern, key=k)
            count += n
        return result, count
    if isinstance(value, list):
        count = 0
        result_list = []
        for v in value:
            scrubbed_v, n = _scrub_json_value(v, secrets_pattern, key=key)
            result_list.append(scrubbed_v)
            count += n
        return result_list, count
    if isinstance(value, str):
        if key is not None and key.strip().lower() in _SENSITIVE_HEADER_KEYS | _ACCOUNT_ID_KEYS:
            return _scrub_whole_if_changed(value)
        if (
            key is not None
            and _KEY_LIKE_FIELD_NAME.search(key)
            and _TOKEN_VALUE_PATTERN.match(value)
        ):
            return _scrub_whole_if_changed(value)
        return _scrub_content(value, secrets_pattern)
    return value, 0


def scrub_text(text: str, *, secrets: Iterable[str]) -> tuple[str, int]:
    """Line-by-line text scrub for non-JSON payloads (SGML header, full-index).

    A `User-Agent:`/`Authorization:`/`APCA-API-KEY-ID:`/
    `APCA-API-SECRET-KEY:` line (`:` or `=`, any leading whitespace) is
    replaced wholly, matching `scrub_json`'s `_SENSITIVE_HEADER_KEYS` set.
    Every other line only has the matched span of each configured secret,
    email address, Alpaca-style key prefix, or key-shaped token replaced.
    Returns `(scrubbed, replacement_count)`.
    """
    secrets_pattern = _secrets_pattern(secrets)
    total = 0
    out_lines = []
    for line in text.splitlines(keepends=True):
        scrubbed_line, n = _scrub_text_line(line, secrets_pattern)
        out_lines.append(scrubbed_line)
        total += n
    return "".join(out_lines), total


def _scrub_text_line(line: str, secrets_pattern: re.Pattern[str] | None) -> tuple[str, int]:
    scrubbed, n = SENSITIVE_HEADER_LINE_PATTERN.subn(SCRUBBED, line)
    if n:
        return scrubbed, n
    return _scrub_content(line, secrets_pattern)


def _non_blank_secret(secret: SecretStr | None) -> str | None:
    """`secret`'s value, or `None` if it's unset or blank/whitespace-only."""
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value if value.strip() else None


def _configured_secrets(settings: Settings) -> list[str]:
    """Every real secret value to scrub as a substring, plus derived forms.

    Starts from `config.secret_values`, every `SecretStr` field found by type,
    so a secret added to `Settings` later is scrubbed from CLI output and
    fixtures without editing this function (#342), as `ingest._clean` does
    for run rows (#334). An SMTP password is neither email- nor key-shaped,
    so no pattern would catch it otherwise (#320).

    On top: the HTTP Basic `base64("key:secret")` form of each Alpaca
    credential pair (the data pair, `main`'s paper pair and every other
    book's pair in `alpaca_paper_books`, read from settings, #1405), in case
    a payload ever carries an `Authorization: Basic ...` value built from them
    (T2 review round 2, safety-reviewer MUST FIX), on top of the
    `Authorization`-header-name scrub in `scrub_json`, which catches it
    regardless of content; and the SMTP AUTH forms.
    """
    values = secret_values(settings)
    pairs = [
        (settings.alpaca_api_key, settings.alpaca_api_secret),
        (settings.alpaca_paper_api_key, settings.alpaca_paper_api_secret),
    ]
    pairs += [(pair.api_key, pair.api_secret) for pair in settings.alpaca_paper_books.values()]
    for key_field, secret_field in pairs:
        key = _non_blank_secret(key_field)
        secret = _non_blank_secret(secret_field)
        if key is not None and secret is not None:
            values.append(base64.b64encode(f"{key}:{secret}".encode()).decode())
    values.extend(_smtp_login_forms(settings))
    return values


def _smtp_login_forms(settings: Settings) -> list[str]:
    """The base64 forms SMTP AUTH sends for the alert credentials (#334):
    `AUTH PLAIN` carries base64("\\0user\\0password"), and `AUTH LOGIN` sends
    base64(user) and base64(password) on their own. A raw-value scrub would
    miss them in a transcript or an exception, so they are scrubbed too. The
    alert delivery path must never enable `smtplib` debug output regardless, and
    must pass `smtplib.login` the same raw (unstripped) values these forms are
    built from. CRAM-MD5 (tried first when a server offers it) sends
    base64("user hmac"), which exposes the user name only and is not covered."""
    user = _non_blank_secret(settings.alert_smtp_user)
    password = _non_blank_secret(settings.alert_smtp_password)
    forms = [base64.b64encode(v.encode()).decode() for v in (user, password) if v]
    if user is not None and password is not None:
        forms.append(base64.b64encode(f"\0{user}\0{password}".encode()).decode())
    return forms


def _missing_secret_names(settings: Settings) -> list[str]:
    missing = []
    if _non_blank_secret(settings.alpaca_api_key) is None:
        missing.append("ALPACA_API_KEY")
    if _non_blank_secret(settings.alpaca_api_secret) is None:
        missing.append("ALPACA_API_SECRET")
    if _non_blank_secret(settings.sec_edgar_user_agent) is None:
        missing.append("SEC_EDGAR_USER_AGENT")
    return missing


def _note_recorded(path: Path) -> None:
    _recorded_at[str(path.relative_to(FIXTURES_ROOT))] = (
        datetime.now(UTC).replace(microsecond=0).isoformat()
    )


def _write_json(path: Path, payload: Any, *, secrets: Iterable[str]) -> None:
    scrubbed, count = scrub_json(payload, secrets=secrets)
    path.write_text(json.dumps(scrubbed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _note_recorded(path)
    print(f"cli_record: scrubbed {count} value(s) in {path.name}")


def _write_text(path: Path, text: str, *, secrets: Iterable[str]) -> None:
    scrubbed, count = scrub_text(text, secrets=secrets)
    path.write_text(scrubbed, encoding="utf-8")
    _note_recorded(path)
    print(f"cli_record: scrubbed {count} value(s) in {path.name}")


def _write_text_gz(path: Path, text: str, *, secrets: Iterable[str]) -> None:
    """Scrub, then write gzip-compressed (`path` already ends in `.gz`)."""
    scrubbed, count = scrub_text(text, secrets=secrets)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(scrubbed)
    _note_recorded(path)
    print(f"cli_record: scrubbed {count} value(s) in {path.name} (gzip)")


def accessions_in_company_facts(payload: Any) -> set[str]:
    """Every `accn` referenced by any fact in a (trimmed) company-facts payload. Pure."""
    out: set[str] = set()
    facts = payload.get("facts", {}) if isinstance(payload, dict) else {}
    if not isinstance(facts, dict):
        return out
    for concepts in facts.values():
        if not isinstance(concepts, dict):
            continue
        for concept in concepts.values():
            units = concept.get("units", {}) if isinstance(concept, dict) else {}
            for entries in units.values():
                for entry in entries if isinstance(entries, list) else []:
                    accn = entry.get("accn") if isinstance(entry, dict) else None
                    if accn:
                        out.add(str(accn))
    return out


def trim_submissions_page(page: Any, *, accessions: Iterable[str]) -> Any:
    """Keep only the rows (column-array index positions) whose accession is wanted. Pure.

    A submissions page is `{"accessionNumber": [...], "acceptanceDateTime": [...], ...}`
    with one entry per filing in every column; rows are kept in their original order.
    """
    if not isinstance(page, dict) or not isinstance(page.get("accessionNumber"), list):
        return page
    wanted = set(accessions)
    keep = [i for i, accn in enumerate(page["accessionNumber"]) if accn in wanted]
    return {
        key: [col[i] for i in keep if i < len(col)] if isinstance(col, list) else col
        for key, col in page.items()
    }


def trim_company_facts(payload: Any, statement_tags: Iterable[str] = ()) -> Any:
    """Keep `dei` whole, share-count concepts and the `taxonomy:tag` names in
    `statement_tags` (#660: `edgar.statement_tags`' fallbacks) elsewhere;
    other keys untouched.

    Pure. Drops nothing the master/universe code or the statement-facts
    parser reads (spec master table, amendment 2026-10-03); the full
    payload is 2-8 MB per filer, the trimmed one under ~200 KB.
    """
    keep_tags = frozenset(statement_tags)
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        return payload
    facts: dict[str, Any] = {}
    for namespace, concepts in payload["facts"].items():
        if not isinstance(concepts, dict):
            continue
        if namespace in COMPANY_FACTS_KEEP_NAMESPACES:
            facts[namespace] = concepts
            continue
        kept = {
            name: value
            for name, value in concepts.items()
            if any(s in name for s in COMPANY_FACTS_KEEP_CONCEPT_SUBSTRINGS)
            or f"{namespace}:{name}" in keep_tags
        }
        if kept:
            facts[namespace] = kept
    return {**payload, "facts": facts}


def trim_company_tickers(payload: Any, *, ciks: Iterable[str], symbols: Iterable[str]) -> Any:
    """Keep rows for the recorded CIKs and symbols plus the first sample rows.

    Pure. The snapshot is `{"fields": [...], "data": [[...], ...]}`; the original
    row order is kept so the sample is the same on every run.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return payload
    fields = payload.get("fields") or []
    try:
        cik_i, ticker_i = fields.index("cik"), fields.index("ticker")
    except ValueError:
        return payload
    want_ciks = {int(c) for c in ciks}
    want_symbols = {s.upper() for s in symbols}
    rows = payload["data"]
    kept = [
        row
        for i, row in enumerate(rows)
        if i < COMPANY_TICKERS_SAMPLE_ROWS
        or (
            len(row) > max(cik_i, ticker_i)
            and (row[cik_i] in want_ciks or str(row[ticker_i]).upper() in want_symbols)
        )
    ]
    return {**payload, "data": kept}


# --- the fixed recording plan ------------------------------------------------

# Chosen so T11's parsers see: a plain issuer, a dual-class issuer (cover
# page carries multiple `dei:Security12bTitle`/ticker pairs), and a real
# Form 25-NSE delisting notice. CIKs confirmed against live EDGAR
# (https://www.sec.gov/cgi-bin/browse-edgar) on 2026-09-24; see
# tests/fixtures/README.md if any no longer resolve.
EDGAR_CIKS: dict[str, str] = {
    "plain_issuer": "0000320193",  # Apple Inc.
    "dual_class": "0001652044",  # Alphabet Inc. (GOOGL / GOOG)
    "delisted_25nse": "0001738827",  # KLX Energy Services Holdings, Inc.
}
# Per-label form preference for picking one filing to fetch the SGML header
# and a document from: the first form in the list that appears in the
# CIK's `submissions()` "recent" filings wins.
_FORM_PREFERENCE: dict[str, tuple[str, ...]] = {
    "plain_issuer": ("10-K", "10-Q", "8-K"),
    "dual_class": ("10-K", "10-Q", "8-K"),
    "delisted_25nse": ("25-NSE", "25", "10-K", "10-Q", "8-K"),
}
EDGAR_FILING_INDEX_QUARTER = (2024, 1)

# Short enough to include a real split and dividend: AAPL's 4:1 split was
# 2020-08-31, inside this window, alongside SPY/MTUM's regular quarterly
# dividends.
ALPACA_SYMBOLS = ["SPY", "MTUM", "AAPL", "MSFT", "KO"]
ALPACA_START = date(2020, 8, 1)
ALPACA_END = date(2020, 9, 30)

# Fixture size cap (pre-commit `check-added-large-files`, 500 KB). The raw EDGAR
# payloads are far bigger: a company-facts file lists every XBRL concept a filer
# ever reported (2-8 MB), the companies snapshot lists ~10k companies (900 KB),
# and a 10-K primary document is 1.5-2.6 MB with `dei:` cover tags spread across
# the whole file (so it cannot be truncated). T3 recorded them at those sizes.
# Rules, applied before scrubbing and writing:
# - company facts keep the `dei` namespace whole plus the share-count concepts
#   the master and universe need (spec master table: shares from company facts
#   with `as_of_date` and class dimension); other concepts are dropped;
# - the companies snapshot keeps every row for a recorded CIK or symbol plus
#   the first `COMPANY_TICKERS_SAMPLE_ROWS` rows for shape;
# - filing documents are written gzip-compressed (`.gz`), whole.
COMPANY_FACTS_KEEP_NAMESPACES: tuple[str, ...] = ("dei",)
COMPANY_FACTS_KEEP_CONCEPT_SUBSTRINGS: tuple[str, ...] = ("SharesOutstanding", "SharesIssued")
COMPANY_TICKERS_SAMPLE_ROWS = 200

# Cap on the recorded full-index excerpt: the real per-quarter `form.idx`
# lists every SEC filing that quarter (tens of thousands of lines); only a
# small header + the lines naming our own recorded CIKs are kept.
_FILING_INDEX_HEADER_LINES = 12


def _pick_filing(
    submissions_payload: Any, form_preference: tuple[str, ...]
) -> tuple[str, str] | None:
    """The `(accession, primary_document)` of the first filing matching `form_preference`."""
    recent = submissions_payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    documents = recent.get("primaryDocument", [])
    for wanted_form in form_preference:
        for form, accession, document in zip(forms, accessions, documents, strict=False):
            if form == wanted_form:
                return accession, document
    return None


def _prefer_root_document(document: str) -> str:
    """Prefer the raw filing-root document over an XSL-rendered view.

    EDGAR's `primaryDocument` for XBRL-only forms (e.g. 25-NSE) often
    points at a viewer path like `xslF25X02/primary_doc.xml`; the same
    file also exists as raw XML directly at the filing root. Using the
    root copy avoids downloading (and needing to flatten) a nested path
    (T2 review round 2, safety-reviewer MUST FIX).
    """
    if "/" in document:
        return document.rsplit("/", 1)[-1]
    return document


def _flatten_fixture_filename(document: str) -> str:
    """`document`, with any path separator replaced so it's safe as a single
    filename under `tests/fixtures/edgar/` (defense in depth alongside
    `_prefer_root_document`, in case some other form's primary document is
    ever nested)."""
    return document.replace("/", "__")


def _truncate_filing_index(text: str, ciks: Iterable[str]) -> str:
    """Header lines plus any line naming one of `ciks` (unpadded, as `form.idx` shows it)."""
    wanted = {str(int(cik)) for cik in ciks}
    lines = text.splitlines(keepends=True)
    header = lines[:_FILING_INDEX_HEADER_LINES]
    matched = [line for line in lines[_FILING_INDEX_HEADER_LINES:] if _line_has_cik(line, wanted)]
    return "".join(header + matched)


def _line_has_cik(line: str, wanted: set[str]) -> bool:
    fields = line.split()
    return any(field in wanted for field in fields)


def _record_edgar(settings: Settings, secrets: list[str]) -> None:
    year, qtr = EDGAR_FILING_INDEX_QUARTER

    tickers = trim_company_tickers(
        edgar_raw.company_tickers(settings=settings),
        ciks=EDGAR_CIKS.values(),
        symbols=ALPACA_SYMBOLS,
    )
    _write_json(EDGAR_FIXTURES_DIR / "company_tickers.json", tickers, secrets=secrets)

    index_text = edgar_raw.filing_index_quarter(year, qtr, settings=settings)
    excerpt = _truncate_filing_index(index_text, EDGAR_CIKS.values())
    _write_text(EDGAR_FIXTURES_DIR / f"filing_index_{year}_qtr{qtr}.txt", excerpt, secrets=secrets)

    for label, cik in EDGAR_CIKS.items():
        submissions = edgar_raw.submissions(cik, settings=settings)
        _write_json(EDGAR_FIXTURES_DIR / f"submissions_{label}.json", submissions, secrets=secrets)

        statement_tags = itertools.chain.from_iterable(settings.edgar.statement_tags.values())
        facts = trim_company_facts(edgar_raw.company_facts(cik, settings=settings), statement_tags)
        _write_json(EDGAR_FIXTURES_DIR / f"company_facts_{label}.json", facts, secrets=secrets)

        # Older filings (and their acceptance times) live in paged files; keep only the
        # rows the trimmed facts reference, so T11 never has to fall back to `filed`.
        wanted = accessions_in_company_facts(facts)
        for page_ref in submissions.get("filings", {}).get("files", []):
            name = str(page_ref.get("name", ""))
            page = trim_submissions_page(
                edgar_raw.submissions_page(name, settings=settings), accessions=wanted
            )
            suffix = name.rsplit("-", 1)[-1].removesuffix(".json")
            _write_json(
                EDGAR_FIXTURES_DIR / f"submissions_{label}_{suffix}.json", page, secrets=secrets
            )

        picked = _pick_filing(submissions, _FORM_PREFERENCE[label])
        if picked is None:
            print(
                f"cli_record: no matching filing found for {label} ({cik}); skipping",
                file=sys.stderr,
            )
            continue
        accession, primary_document = picked
        root_document = _prefer_root_document(primary_document)

        header = edgar_raw.filing_sgml_header(cik, accession, settings=settings)
        _write_text(EDGAR_FIXTURES_DIR / f"sgml_header_{label}.txt", header, secrets=secrets)

        downloaded = edgar_raw.download_filing_file(
            cik, accession, root_document, settings=settings
        )
        fixture_name = _flatten_fixture_filename(root_document)
        for stale in EDGAR_FIXTURES_DIR.glob(f"filing_{label}_*"):
            stale.unlink()  # the document name changes per filing; keep exactly one per label
        _write_text_gz(
            EDGAR_FIXTURES_DIR / f"filing_{label}_{fixture_name}.gz",
            downloaded.read_text(encoding="utf-8", errors="replace"),
            secrets=secrets,
        )


# --- FSN data sets (T11c: `python -m tradepartner.cli_record fsn`) ---------

# The recorded periods (spec) and the one fixture accession to trim each
# down to: Apple's plain-issuer 10-K in `2025_10`, Alphabet's dual-class 10-K
# in `2026_02` (same accessions T3/T11's cover-page fixtures use).
FSN_RECORD_PERIODS: dict[str, str] = {
    "2025_10": "0000320193-25-000079",
    "2026_02": "0001652044-26-000018",
}
# The four `dei` cover-page tags kept in the trimmed `num.tsv`/`txt.tsv`.
FSN_RECORD_TAGS: tuple[str, ...] = (
    "Security12bTitle",
    "TradingSymbol",
    "SecurityExchangeName",
    "EntityCommonStockSharesOutstanding",
)
_FSN_MEMBERS = ("sub.tsv", "num.tsv", "txt.tsv", "dim.tsv")


def _column(header: str, name: str) -> int:
    return header.rstrip("\r\n").split("\t").index(name)


def _field(line: str, index: int) -> str | None:
    fields = line.rstrip("\r\n").split("\t")
    return fields[index] if index < len(fields) else None


def trim_fsn_member(
    lines: Iterable[str], *, accessions: Iterable[str], tags: Iterable[str] | None = None
) -> Iterator[str]:
    """The header and the `sub`/`num`/`txt.tsv` lines whose `adsh` is in
    `accessions` (and whose `tag` is in `tags`, when given), each exactly as
    read. Pure and streaming. FSN members are unquoted TSV, so a line is split
    on tabs only: a value FSN cut off with an unclosed `"` swallows nothing,
    and a quoted title is never rewritten."""
    lines = iter(lines)
    header = next(lines, None)
    if header is None:
        return
    yield header
    adsh = _column(header, "adsh")
    tag = _column(header, "tag") if tags is not None else None
    wanted, tag_set = set(accessions), set(tags or ())
    for line in lines:
        if _field(line, adsh) in wanted and (tag is None or _field(line, tag) in tag_set):
            yield line


def trim_fsn_dim(lines: Iterable[str], *, dimhashes: Iterable[str]) -> Iterator[str]:
    """The header and the `dim.tsv` lines whose `dimhash` a kept `num`/`txt`
    line references, each exactly as read. Pure and streaming."""
    lines = iter(lines)
    header = next(lines, None)
    if header is None:
        return
    yield header
    column, wanted = _column(header, "dimhash"), set(dimhashes)
    for line in lines:
        if _field(line, column) in wanted:
            yield line


def _member_lines(archive: zipfile.ZipFile, member: str) -> Iterator[str]:
    """`member`'s lines, streamed from the zip; `newline=""` keeps each line's
    own ending, so a kept line is written back byte for byte."""
    names = {name.lower(): name for name in archive.namelist()}
    with archive.open(names[member]) as raw:
        yield from io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")


def _record_fsn(settings: Settings, secrets: list[str]) -> None:
    """`python -m tradepartner.cli_record fsn`: the data-set page plus the
    two recorded periods, each trimmed to one fixture accession. Needs only
    `SEC_EDGAR_USER_AGENT` (no Alpaca keys). Each member is streamed and
    trimmed line by line, so memory stays small although an FSN member
    decompresses to gigabytes."""
    EDGAR_FSN_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    page_html = edgar_raw.fsn_page_html(settings=settings)
    _write_text(EDGAR_FSN_FIXTURES_DIR / "page.html", page_html, secrets=secrets)

    for period, accession in FSN_RECORD_PERIODS.items():
        zip_path, _headers = edgar_raw.fsn_zip(period, settings=settings)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                kept = {
                    "sub.tsv": list(
                        trim_fsn_member(_member_lines(archive, "sub.tsv"), accessions=[accession])
                    )
                }
                for member in ("num.tsv", "txt.tsv"):
                    kept[member] = list(
                        trim_fsn_member(
                            _member_lines(archive, member),
                            accessions=[accession],
                            tags=FSN_RECORD_TAGS,
                        )
                    )
                dimhashes = {
                    value
                    for member in ("num.tsv", "txt.tsv")
                    for line in kept[member][1:]
                    if (value := _field(line, _column(kept[member][0], "dimh")))
                }
                kept["dim.tsv"] = list(
                    trim_fsn_dim(_member_lines(archive, "dim.tsv"), dimhashes=dimhashes)
                )
        finally:
            zip_path.unlink(missing_ok=True)

        period_dir = EDGAR_FSN_FIXTURES_DIR / period
        period_dir.mkdir(parents=True, exist_ok=True)
        for member in _FSN_MEMBERS:
            _write_text(period_dir / member, "".join(kept[member]), secrets=secrets)


def _record_alpaca(settings: Settings, secrets: list[str]) -> None:
    bars = alpaca_raw.daily_bars(ALPACA_SYMBOLS, ALPACA_START, ALPACA_END, settings=settings)
    _write_json(ALPACA_FIXTURES_DIR / "daily_bars.json", bars, secrets=secrets)

    actions = alpaca_raw.corporate_actions(
        ALPACA_SYMBOLS, ALPACA_START, ALPACA_END, settings=settings
    )
    _write_json(ALPACA_FIXTURES_DIR / "corporate_actions.json", actions, secrets=secrets)

    # `assets_snapshot` uses `TradingClient(paper=True)`: a live-only key
    # pair will fail this call even though `daily_bars`/`corporate_actions`
    # succeed (see tests/fixtures/README.md).
    assets = alpaca_raw.assets_snapshot(ALPACA_SYMBOLS, settings=settings)
    _write_json(ALPACA_FIXTURES_DIR / "assets_snapshot.json", assets, secrets=secrets)


# --- paper trading responses (T48: `python -m tradepartner.cli_record paper SYMBOL`) ---

# The one order path outside the risk-gated wrapper (T50 fences its import): tiny
# market orders on one liquid, fractionable name, plus one whole share of a
# non-fractionable name the owner names on the command line. Run by the owner only
# (T48b), during regular hours, on a flat paper account; the script ends flat.
PAPER_SYMBOL = "KO"
PAPER_BUY_NOTIONAL = 5.0  # dollars, above Alpaca's $1 fractional minimum
PAPER_FRACTIONAL_QTY = 0.5
PAPER_RESTING_LIMIT_FRACTION = 0.9  # a buy limit this far under the fill price rests
# The script starts only inside regular hours with at least this long before the close,
# so no market DAY order (the flattening sells included) queues overnight.
PAPER_MIN_MINUTES_BEFORE_CLOSE = 30
_PAPER_TERMINAL = frozenset({"filled", "canceled", "expired", "rejected"})
_PAPER_RESTING = frozenset({"new", "accepted"})


class PaperRecordingError(RuntimeError):
    """The paper script refused to start, or could not finish flat."""


def _paper_await(
    raw: AlpacaTradingRaw, client_order_id: str, settings: Settings, until: frozenset[str]
) -> list[Any]:
    """Poll `get_order_by_client_id` every `paper.poll_interval_seconds` until the
    status is in `until`; every change of status is kept. Gives up after
    `paper.sell_wait_seconds`."""
    deadline = raw.clock.monotonic() + settings.paper.sell_wait_seconds
    seen: list[Any] = []
    while True:
        order = raw.get_order_by_client_id(client_order_id)
        if not seen or order.get("status") != seen[-1].get("status"):
            seen.append(order)
        if order.get("status") in until:
            return seen
        if raw.clock.monotonic() >= deadline:
            raise PaperRecordingError(f"order {client_order_id} still {order.get('status')!r}")
        raw.clock.sleep(settings.paper.poll_interval_seconds)


def _paper_submit(
    raw: AlpacaTradingRaw,
    request: OrderRequest,
    settings: Settings,
    until: frozenset[str] = _PAPER_TERMINAL,
) -> dict[str, Any]:
    """`{"submit": response, "polls": [...]}`, or `{"error": ...}` when refused."""
    try:
        submitted = raw.submit_order(request)
    except AlpacaTradingError as error:
        return {"error": {"status_code": error.status_code, "body": error.body}}
    cid = str(request.client_order_id)
    return {"submit": submitted, "polls": _paper_await(raw, cid, settings, until)}


def _market(
    symbol: str,
    side: OrderSide,
    cid: str,
    *,
    qty: float | None = None,
    notional: float | None = None,
) -> MarketOrderRequest:
    return MarketOrderRequest(
        symbol=symbol,
        side=side,
        time_in_force=TimeInForce.DAY,
        client_order_id=cid,
        qty=qty,
        notional=notional,
    )


def _held_quantity(positions: list[Any], symbol: str) -> float:
    return sum(float(p["qty"]) for p in positions if p.get("symbol") == symbol)


def _paper_flatten(raw: AlpacaTradingRaw, settings: Settings, ids: Iterator[str]) -> list[Any]:
    """Cancel every open order, then sell every position whole. A refused sell is
    kept as its step; the flatness check after it reports what is left."""
    steps: list[Any] = []
    for order in raw.list_open_orders():
        raw.cancel_order(str(order["id"]))
        cid = str(order["client_order_id"])
        steps.append({"cancel": cid, "polls": _paper_await(raw, cid, settings, _PAPER_TERMINAL)})
    for position in raw.list_positions():
        qty = float(position["qty"])
        if qty <= 0:
            raise PaperRecordingError(f"position {position.get('symbol')} is not long: {qty}")
        request = _market(str(position["symbol"]), OrderSide.SELL, next(ids), qty=qty)
        steps.append(_paper_submit(raw, request, settings))
    return steps


def _paper_finish(
    raw: AlpacaTradingRaw, settings: Settings, ids: Iterator[str], out: dict[str, Any]
) -> list[str]:
    """Flatten, then re-read positions and open orders into `out`; return what is
    left (symbols and order ids), printing a NOT FLAT line when anything is."""
    try:
        out["flatten"] = _paper_flatten(raw, settings, ids)
        out["positions_after"] = raw.list_positions()
        out["open_orders_after"] = raw.list_open_orders()
    except (AlpacaTradingError, PaperRecordingError) as error:
        message = scrub_text(str(error), secrets=_configured_secrets(settings))[0]
        print(f"cli_record: NOT FLAT? flattening failed ({message}); check it", file=sys.stderr)
        raise
    residue = [str(p.get("symbol")) for p in out["positions_after"]] + [
        str(o.get("client_order_id")) for o in out["open_orders_after"]
    ]
    if residue:
        print(f"cli_record: NOT FLAT: {', '.join(residue)}; check it", file=sys.stderr)
    return residue


def _require_regular_hours(now: datetime) -> None:
    """Refuse outside regular hours or within `PAPER_MIN_MINUTES_BEFORE_CLOSE` of the
    close (XNYS calendar; during regular hours the UTC date is the session date)."""
    day = now.date()
    if not calendar.is_session(day):
        raise PaperRecordingError(f"{day} is not a trading session")
    latest = calendar.session_close(day) - timedelta(minutes=PAPER_MIN_MINUTES_BEFORE_CLOSE)
    if not calendar.session_open(day) <= now <= latest:
        raise PaperRecordingError(
            f"run between the open and {PAPER_MIN_MINUTES_BEFORE_CLOSE} minutes before the close"
        )


def _record_paper(
    raw: AlpacaTradingRaw, settings: Settings, non_fractionable: str, now: datetime
) -> dict[str, Any]:
    """Run the fixed paper script; return `{fixture name: raw response}`. Refuses,
    before any order, a non-flat account or a symbol pair that is not (tradable and
    fractionable, tradable and not fractionable). Always tries to end flat, and raises
    if it did not."""
    started = now
    _require_regular_hours(now)
    out: dict[str, Any] = {"account_before": raw.get_account()}
    if raw.list_positions() or raw.list_open_orders():
        raise PaperRecordingError("the paper account holds positions or open orders; flatten it")
    out["assets"] = raw.get_assets([PAPER_SYMBOL, non_fractionable])
    liquid, other = out["assets"]
    if not (liquid.get("tradable") and liquid.get("fractionable")):
        raise PaperRecordingError(f"{PAPER_SYMBOL} is not tradable and fractionable")
    if not other.get("tradable") or other.get("fractionable"):
        raise PaperRecordingError(f"{non_fractionable} must be tradable and not fractionable")

    ids = (f"rec{started:%Y%m%d%H%M%S}-{n}" for n in itertools.count(1))
    first = next(ids)
    buy, sell = OrderSide.BUY, OrderSide.SELL
    try:
        out["buy_fractional"] = _paper_submit(
            raw, _market(PAPER_SYMBOL, buy, first, notional=PAPER_BUY_NOTIONAL), settings
        )
        out["buy_whole"] = _paper_submit(
            raw, _market(PAPER_SYMBOL, buy, next(ids), qty=1), settings
        )
        out["sell_fractional"] = _paper_submit(
            raw, _market(PAPER_SYMBOL, sell, next(ids), qty=PAPER_FRACTIONAL_QTY), settings
        )
        out["positions_held"] = raw.list_positions()
        above = round(_held_quantity(out["positions_held"], PAPER_SYMBOL) + PAPER_FRACTIONAL_QTY, 9)
        out["sell_above_held"] = _paper_submit(
            raw, _market(PAPER_SYMBOL, sell, next(ids), qty=above), settings
        )
        whole = out["buy_whole"].get("polls", [{}])[-1]
        if whole.get("status") != "filled":
            raise PaperRecordingError(f"the whole-share buy ended {whole.get('status')!r}")
        price = float(whole["filled_avg_price"])
        resting_id = next(ids)
        resting = LimitOrderRequest(
            symbol=PAPER_SYMBOL,
            side=buy,
            time_in_force=TimeInForce.DAY,
            client_order_id=resting_id,
            qty=1,
            limit_price=round(price * PAPER_RESTING_LIMIT_FRACTION, 2),
        )
        out["resting"] = _paper_submit(raw, resting, settings, _PAPER_RESTING | _PAPER_TERMINAL)
        raw.cancel_order(str(out["resting"]["submit"]["id"]))
        out["resting_cancelled"] = _paper_await(raw, resting_id, settings, _PAPER_TERMINAL)
        out["duplicate_client_order_id"] = _paper_submit(
            raw, _market(PAPER_SYMBOL, buy, first, notional=PAPER_BUY_NOTIONAL), settings
        )
        out["non_fractionable_buy"] = _paper_submit(
            raw, _market(non_fractionable, buy, next(ids), qty=1), settings
        )
        out["non_fractionable_sell_fractional"] = _paper_submit(
            raw, _market(non_fractionable, sell, next(ids), qty=PAPER_FRACTIONAL_QTY), settings
        )
    finally:
        residue = _paper_finish(raw, settings, ids, out)
    if residue:
        raise PaperRecordingError("the paper account is not flat after the script")
    out["account_after"] = raw.get_account()
    out["fill_activities"] = raw.list_fill_activities(started)
    return out


def _run_paper(
    raw: AlpacaTradingRaw, settings: Settings, non_fractionable: str, now: datetime | None = None
) -> int:
    """Record, then scrub (keys, emails, the account's own ids) and write every
    response under `ALPACA_PAPER_FIXTURES_DIR`, merging `recorded_at.json`."""
    try:
        recordings = _record_paper(raw, settings, non_fractionable, now or datetime.now(UTC))
    except (PaperRecordingError, AlpacaTradingError) as error:
        # An adapter error can echo a response body; scrub it like a fixture (#334).
        message = scrub_text(str(error), secrets=_configured_secrets(settings))[0]
        print(f"cli_record: {message}", file=sys.stderr)
        return 1
    account = recordings["account_before"]
    secrets = _configured_secrets(settings) + [
        str(account[k]) for k in ("id", "account_number") if account.get(k)
    ]
    ALPACA_PAPER_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in recordings.items():
        _write_json(ALPACA_PAPER_FIXTURES_DIR / f"{name}.json", payload, secrets=secrets)
    previous = json.loads(RECORDED_AT_FILE.read_text()) if RECORDED_AT_FILE.exists() else {}
    RECORDED_AT_FILE.write_text(
        json.dumps({**previous, **_recorded_at}, indent=2, sort_keys=True) + "\n"
    )
    print(f"cli_record: wrote paper fixtures under {ALPACA_PAPER_FIXTURES_DIR}")
    return 0


def main(argv: Sequence[str] = ()) -> int:
    """`python -m tradepartner.cli_record [fsn | paper SYMBOL]`. With no target,
    records every fixture (Alpaca and EDGAR) as before. `fsn` (T11c) records only
    the FSN data-set page and its two recorded periods, and needs only
    `SEC_EDGAR_USER_AGENT`. `paper SYMBOL` (T48) places the paper script's orders
    and needs only the paper keys; `SYMBOL` is a tradable, non-fractionable name."""
    target = argv[0] if argv else "all"
    if target == "paper" and len(argv) != 2:
        print("cli_record: usage: paper NON_FRACTIONABLE_SYMBOL", file=sys.stderr)
        return 2
    if target not in ("all", "fsn", "paper") or (target != "paper" and len(argv) > 1):
        # A typo must not fall through to the full recorder (Alpaca keys too).
        print(
            f"cli_record: unknown target {target!r}; use 'fsn', 'paper SYMBOL' or no target",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()

    if target == "paper":
        try:
            raw = AlpacaTradingRaw(settings)
        except (
            alpaca_trading_raw.AlpacaPaperCredentialsError,
            alpaca_trading_raw.AlpacaPaperGuardError,
        ) as error:
            # Fixed text today; scrubbed anyway so every recorder error has one rule (#342).
            message = scrub_text(str(error), secrets=_configured_secrets(settings))[0]
            print(f"cli_record: {message}", file=sys.stderr)
            return 1
        return _run_paper(raw, settings, argv[1].upper())

    if target == "fsn":
        if _non_blank_secret(settings.sec_edgar_user_agent) is None:
            print(
                "cli_record: missing required secret(s): SEC_EDGAR_USER_AGENT. Set it in "
                ".env (see .env.example) before running the recorder.",
                file=sys.stderr,
            )
            return 1
        _record_fsn(settings, _configured_secrets(settings))
        print(f"cli_record: wrote FSN fixtures under {EDGAR_FSN_FIXTURES_DIR}")
        return 0

    missing = _missing_secret_names(settings)
    if missing:
        print(
            "cli_record: missing required secret(s): "
            + ", ".join(missing)
            + ". Set them in .env (see .env.example) before running the recorder.",
            file=sys.stderr,
        )
        return 1

    ALPACA_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    EDGAR_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    secrets = _configured_secrets(settings)
    _record_edgar(settings, secrets)
    _record_alpaca(settings, secrets)
    RECORDED_AT_FILE.write_text(json.dumps(_recorded_at, indent=2, sort_keys=True) + "\n")

    print(f"cli_record: wrote fixtures under {FIXTURES_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
