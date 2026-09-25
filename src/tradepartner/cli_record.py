"""Owner-run fixture recorder: `python -m tradepartner.cli_record`.

Fetches a small, fixed list of real raw payloads from Alpaca and SEC EDGAR
through `adapters/alpaca_raw.py` and `adapters/edgar_raw.py`, **scrubs**
every payload (secrets, email addresses, key-shaped tokens, `User-Agent`
headers), and writes the result as JSON or text under
`tests/fixtures/{alpaca,edgar}/`. T11/T12's parsers are tested against
these recordings; CI never calls this module or the network (spec
"Users & usage": "Once, at the start, the owner runs a recording script
with their own keys").

Run by the owner (plan task T3) with real `ALPACA_API_KEY` /
`ALPACA_API_SECRET` / `SEC_EDGAR_USER_AGENT` set. Exits non-zero with a
message naming every missing secret, before any network call, so the owner
sees exactly what to set (spec req: "recorder must exit non-zero with a
clear message when secrets are missing").
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

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
# "key" or "secret" (any case), a little punctuation, then a 20+ char token
# -- catches `"api_key": "..."`, `secret=...`, etc. without needing to know
# the surrounding structure.
KEY_SHAPED_PATTERN = re.compile(r"(?i)(?:key|secret)[^A-Za-z0-9]{0,5}[A-Za-z0-9/+_=-]{20,}")
# A `User-Agent` header left un-scrubbed in serialized JSON.
USER_AGENT_HEADER_PATTERN = re.compile(r'(?i)"user-agent"\s*:\s*"(?!<scrubbed>)[^"]*"')
# A dict key naming itself a key/secret, e.g. `alpaca_api_key`, `secret_token`.
_KEY_LIKE_FIELD_NAME = re.compile(r"(?i)key|secret")
# A bare token value (no separator needed) long enough to plausibly be one.
_TOKEN_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9/+_=-]{20,}$")


def _looks_secret(value: str) -> bool:
    return bool(
        EMAIL_PATTERN.search(value)
        or ALPACA_KEY_PREFIX_PATTERN.search(value)
        or KEY_SHAPED_PATTERN.search(value)
    )


def scrub_json(value: Any, *, secrets: Iterable[str]) -> Any:
    """Recursively replace secret-shaped values in a JSON-like structure.

    Any string is replaced with `"<scrubbed>"` if it exactly equals one of
    `secrets`; sits under a `User-Agent` (case-insensitive) key; sits under
    a key naming itself `key`/`secret` (e.g. `alpaca_api_key`) and looks
    like a bare token; or itself contains an email address, an
    Alpaca-style key prefix, or a `key`/`secret`-adjacent token. Dicts and
    lists are walked; every other type (numbers, bools, `None`) passes
    through unchanged.
    """
    secret_values = {s for s in secrets if s}
    return _scrub_json_value(value, secret_values, key=None)


def _scrub_json_value(value: Any, secret_values: set[str], *, key: str | None) -> Any:
    if isinstance(value, Mapping):
        return {k: _scrub_json_value(v, secret_values, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_json_value(v, secret_values, key=key) for v in value]
    if isinstance(value, str):
        if key is not None and key.strip().lower() == "user-agent":
            return SCRUBBED
        if (
            key is not None
            and _KEY_LIKE_FIELD_NAME.search(key)
            and _TOKEN_VALUE_PATTERN.match(value)
        ):
            return SCRUBBED
        if value in secret_values or _looks_secret(value):
            return SCRUBBED
        return value
    return value


def scrub_text(text: str, *, secrets: Iterable[str]) -> str:
    """Line-by-line text scrub for non-JSON payloads (SGML header, full-index).

    Each line is replaced wholesale with `"<scrubbed>"` if it equals a
    secret verbatim or matches one of the same patterns `scrub_json` uses;
    every other line is left untouched.
    """
    secret_values = {s for s in secrets if s}
    lines = text.splitlines(keepends=True)
    return "".join(_scrub_line(line, secret_values) for line in lines)


def _scrub_line(line: str, secret_values: set[str]) -> str:
    ending = line[len(line.rstrip("\r\n")) :]
    stripped = line.rstrip("\r\n")
    if stripped in secret_values or _looks_secret(stripped):
        return f"{SCRUBBED}{ending}"
    return line


def _configured_secrets(settings: Settings) -> list[str]:
    values = [settings.alpaca_api_key, settings.alpaca_api_secret, settings.sec_edgar_user_agent]
    return [v.get_secret_value() for v in values if v is not None]


def _missing_secret_names(settings: Settings) -> list[str]:
    missing = []
    if settings.alpaca_api_key is None:
        missing.append("ALPACA_API_KEY")
    if settings.alpaca_api_secret is None:
        missing.append("ALPACA_API_SECRET")
    if settings.sec_edgar_user_agent is None:
        missing.append("SEC_EDGAR_USER_AGENT")
    return missing


def _write_json(path: Path, payload: Any, *, secrets: Iterable[str]) -> None:
    scrubbed = scrub_json(payload, secrets=secrets)
    path.write_text(json.dumps(scrubbed, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str, *, secrets: Iterable[str]) -> None:
    path.write_text(scrub_text(text, secrets=secrets), encoding="utf-8")


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

    tickers = edgar_raw.company_tickers(settings=settings)
    _write_json(EDGAR_FIXTURES_DIR / "company_tickers.json", tickers, secrets=secrets)

    index_text = edgar_raw.filing_index_quarter(year, qtr, settings=settings)
    excerpt = _truncate_filing_index(index_text, EDGAR_CIKS.values())
    _write_text(EDGAR_FIXTURES_DIR / f"filing_index_{year}_qtr{qtr}.txt", excerpt, secrets=secrets)

    for label, cik in EDGAR_CIKS.items():
        submissions = edgar_raw.submissions(cik, settings=settings)
        _write_json(EDGAR_FIXTURES_DIR / f"submissions_{label}.json", submissions, secrets=secrets)

        facts = edgar_raw.company_facts(cik, settings=settings)
        _write_json(EDGAR_FIXTURES_DIR / f"company_facts_{label}.json", facts, secrets=secrets)

        picked = _pick_filing(submissions, _FORM_PREFERENCE[label])
        if picked is None:
            print(
                f"cli_record: no matching filing found for {label} ({cik}); skipping",
                file=sys.stderr,
            )
            continue
        accession, primary_document = picked

        header = edgar_raw.filing_sgml_header(cik, accession, settings=settings)
        _write_text(EDGAR_FIXTURES_DIR / f"sgml_header_{label}.txt", header, secrets=secrets)

        downloaded = edgar_raw.download_filing_file(
            cik, accession, primary_document, settings=settings
        )
        _write_text(
            EDGAR_FIXTURES_DIR / f"filing_{label}_{primary_document}",
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
