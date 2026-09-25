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
import json
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from tradepartner.adapters import alpaca_raw, edgar_raw
from tradepartner.config import Settings, get_settings

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
ALPACA_FIXTURES_DIR = FIXTURES_ROOT / "alpaca"
EDGAR_FIXTURES_DIR = FIXTURES_ROOT / "edgar"

SCRUBBED = "<scrubbed>"

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
        if key is not None and key.strip().lower() in _SENSITIVE_HEADER_KEYS:
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

    Includes the HTTP Basic `base64("key:secret")` form of the Alpaca
    credential pair, in case a payload ever carries an `Authorization:
    Basic ...` value built from them (T2 review round 2, safety-reviewer
    MUST FIX) -- on top of the `Authorization`-header-name scrub in
    `scrub_json`, which catches it regardless of content.
    """
    api_key = _non_blank_secret(settings.alpaca_api_key)
    api_secret = _non_blank_secret(settings.alpaca_api_secret)
    user_agent = _non_blank_secret(settings.sec_edgar_user_agent)

    values = [v for v in (api_key, api_secret, user_agent) if v is not None]
    if api_key is not None and api_secret is not None:
        basic = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        values.append(basic)
    return values


def _missing_secret_names(settings: Settings) -> list[str]:
    missing = []
    if _non_blank_secret(settings.alpaca_api_key) is None:
        missing.append("ALPACA_API_KEY")
    if _non_blank_secret(settings.alpaca_api_secret) is None:
        missing.append("ALPACA_API_SECRET")
    if _non_blank_secret(settings.sec_edgar_user_agent) is None:
        missing.append("SEC_EDGAR_USER_AGENT")
    return missing


def _write_json(path: Path, payload: Any, *, secrets: Iterable[str]) -> None:
    scrubbed, count = scrub_json(payload, secrets=secrets)
    path.write_text(json.dumps(scrubbed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"cli_record: scrubbed {count} value(s) in {path.name}")


def _write_text(path: Path, text: str, *, secrets: Iterable[str]) -> None:
    scrubbed, count = scrub_text(text, secrets=secrets)
    path.write_text(scrubbed, encoding="utf-8")
    print(f"cli_record: scrubbed {count} value(s) in {path.name}")


def _write_text_gz(path: Path, text: str, *, secrets: Iterable[str]) -> None:
    """Scrub, then write gzip-compressed (`path` already ends in `.gz`)."""
    scrubbed, count = scrub_text(text, secrets=secrets)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(scrubbed)
    print(f"cli_record: scrubbed {count} value(s) in {path.name} (gzip)")


def trim_company_facts(payload: Any) -> Any:
    """Keep `dei` whole and only share-count concepts elsewhere; other keys untouched.

    Pure. Drops nothing the master/universe code reads (spec master table); the full
    payload is 2-8 MB per filer, the trimmed one under ~200 KB.
    """
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

        facts = trim_company_facts(edgar_raw.company_facts(cik, settings=settings))
        _write_json(EDGAR_FIXTURES_DIR / f"company_facts_{label}.json", facts, secrets=secrets)

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


def main() -> int:
    settings = get_settings()
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

    print(f"cli_record: wrote fixtures under {FIXTURES_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
