"""Fixture-scrub tests (spec acceptance criterion: "Scrub test").

Two layers:
- a pattern-based walk over `tests/fixtures/**` (no `.env` needed; must
  pass whether the directory is absent, empty, or holds real recordings).
  For a JSON file, the "key-shaped"/Alpaca-key-prefix content checks only
  look at parsed string *values* (not the raw serialized text); for a
  non-JSON (plain-text) file, they run over the whole file text instead,
  since `scrub_text` scrubs whole lines rather than JSON values and there
  is no narrower "value" to scope to (round 3, safety-reviewer -- round 2
  had left non-JSON files unchecked for these two patterns). The email/
  User-Agent/Authorization/Alpaca-header-line checks scan the whole file
  text regardless of format either way;
- unit tests of `cli_record.scrub_json`/`.scrub_text` against synthetic
  payloads: a fake key, an email, a User-Agent header, an Authorization
  header, a secret embedded mid-string, and a real us-gaap XBRL concept
  name (`KeyManagementPersonnelCompensation`) that a looser pattern used to
  false-positive on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tradepartner.cli_record import (
    ALPACA_KEY_PREFIX_PATTERN,
    EMAIL_PATTERN,
    KEY_SHAPED_PATTERN,
    SCRUBBED,
    SENSITIVE_HEADER_LINE_PATTERN,
    USER_AGENT_HEADER_PATTERN,
    scrub_json,
    scrub_text,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _iter_fixture_files(root: Path = FIXTURES_DIR) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def _iter_json_strings(value: Any) -> list[str]:
    """Every string found anywhere in a parsed JSON structure (values only, not keys)."""
    strings: list[str] = []
    if isinstance(value, dict):
        for v in value.values():
            strings.extend(_iter_json_strings(v))
    elif isinstance(value, list):
        for v in value:
            strings.extend(_iter_json_strings(v))
    elif isinstance(value, str):
        strings.append(value)
    return strings


def _parsed_json_or_none(text: str) -> Any | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _assert_file_is_scrubbed(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="ignore")

    # Whole-file-text checks: email addresses and sensitive-header lines
    # can appear in either JSON or plain-text payloads, and matching them
    # against raw text carries no real false-positive risk.
    assert not EMAIL_PATTERN.search(text), f"email address in {path}"
    assert not USER_AGENT_HEADER_PATTERN.search(text), f"unscrubbed User-Agent header in {path}"
    assert not SENSITIVE_HEADER_LINE_PATTERN.search(text), (
        f"unscrubbed User-Agent/Authorization/Alpaca-key header line in {path}"
    )

    parsed = _parsed_json_or_none(text)
    if parsed is not None:
        # JSON-string-value-only: scanning raw serialized text for a
        # "key-shaped" token risks both false positives (e.g. a camelCase
        # XBRL concept name split across "key"/token-looking substrings)
        # and missing the real per-value boundary `scrub_json` uses.
        for value in _iter_json_strings(parsed):
            assert not KEY_SHAPED_PATTERN.search(value), f"key-shaped string in {path}: {value!r}"
            assert not ALPACA_KEY_PREFIX_PATTERN.search(value), (
                f"Alpaca-style key prefix in {path}: {value!r}"
            )
    else:
        # Non-JSON (plain text): `scrub_text` scrubs matched spans across
        # each whole line rather than a bounded JSON "value", so there's no
        # narrower scope to check against -- run both patterns over the
        # whole file text instead (round 3, safety-reviewer MUST FIX: round
        # 2 left text fixtures unchecked for these two).
        assert not KEY_SHAPED_PATTERN.search(text), f"key-shaped string in {path}"
        assert not ALPACA_KEY_PREFIX_PATTERN.search(text), f"Alpaca-style key prefix in {path}"


# --- walking test over tests/fixtures/** ------------------------------------


def test_no_fixture_file_contains_a_secret_shaped_string() -> None:
    """Passes trivially when `tests/fixtures/` is absent or empty (no `.env` needed)."""
    for path in _iter_fixture_files():
        _assert_file_is_scrubbed(path)


def test_iter_fixture_files_returns_empty_for_a_nonexistent_root(tmp_path: Path) -> None:
    missing_dir = tmp_path / "does-not-exist"
    assert not missing_dir.exists()
    assert _iter_fixture_files(root=missing_dir) == []


def test_walk_fails_on_an_unscrubbed_alpaca_key_prefix_in_a_text_fixture(tmp_path: Path) -> None:
    """Round 2 only ran the key-shaped/Alpaca-prefix checks against parsed JSON
    string values, so a leaked key in a *non*-JSON fixture (SGML header,
    full-index excerpt, ...) would have passed silently. `scrub_text` itself
    already scrubs this shape; the walk test now checks the same thing."""
    fixture_file = tmp_path / "sgml_header_example.txt"
    fixture_file.write_text("SEC-DOCUMENT\nOWNER-ID: PKTESTKEY0123456789AB\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="Alpaca-style key prefix"):
        _assert_file_is_scrubbed(fixture_file)


# --- unit tests of the scrub functions themselves ---------------------------


def _synthetic_payload() -> dict[str, object]:
    return {
        "headers": {"User-Agent": "TradePartner Jose jose.juarez@example.com"},
        "alpaca_api_key": "PKFAKE1234567890ABCD",
        "contact": {"email": "owner@example.com", "note": "reach out anytime"},
        "nested": [{"secret_token": "abcdefghijklmnopqrstuvwxyz012345"}],  # gitleaks:allow
        "count": 3,
        "is_active": True,
        "note": None,
    }


def test_scrub_json_removes_key_email_and_user_agent() -> None:
    payload = _synthetic_payload()
    scrubbed, count = scrub_json(payload, secrets=["PKFAKE1234567890ABCD"])

    assert scrubbed["headers"]["User-Agent"] == SCRUBBED
    assert scrubbed["alpaca_api_key"] == SCRUBBED
    assert scrubbed["contact"]["email"] == SCRUBBED
    assert scrubbed["nested"][0]["secret_token"] == SCRUBBED
    assert count == 4
    # Non-secret-shaped values pass through unchanged.
    assert scrubbed["contact"]["note"] == "reach out anytime"
    assert scrubbed["count"] == 3
    assert scrubbed["is_active"] is True
    assert scrubbed["note"] is None

    # And the result satisfies the same detection the walking test uses.
    dumped = json.dumps(scrubbed)
    assert not EMAIL_PATTERN.search(dumped)
    assert not USER_AGENT_HEADER_PATTERN.search(dumped)
    for value in _iter_json_strings(scrubbed):
        assert not KEY_SHAPED_PATTERN.search(value)
        assert not ALPACA_KEY_PREFIX_PATTERN.search(value)


def test_scrub_json_exact_secret_match_scrubs_just_that_span() -> None:
    """A configured secret is scrubbed as a substring, not the whole enclosing value."""
    payload = {"note": "the plain value jose@example.com appears here"}
    scrubbed, count = scrub_json(payload, secrets=["jose@example.com"])
    assert count == 1
    assert scrubbed["note"] == f"the plain value {SCRUBBED} appears here"


def test_scrub_json_secret_embedded_mid_string_is_scrubbed() -> None:
    """A non-email secret substring, not at a value's start or end, is still caught."""
    secret = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"  # gitleaks:allow
    payload = {"note": f"prefix-{secret}-suffix embedded in a longer string"}
    scrubbed, count = scrub_json(payload, secrets=[secret])
    assert count == 1
    assert secret not in scrubbed["note"]
    assert scrubbed["note"] == f"prefix-{SCRUBBED}-suffix embedded in a longer string"


def test_scrub_json_authorization_header_scrubbed_regardless_of_shape() -> None:
    """Sensitive header *names* scrub their whole value even if it doesn't look key-shaped."""
    payload = {
        "Authorization": "Bearer short-token",
        "APCA-API-KEY-ID": "short",
        "apca-api-secret-key": "also-short",
    }
    scrubbed, count = scrub_json(payload, secrets=[])
    assert scrubbed["Authorization"] == SCRUBBED
    assert scrubbed["APCA-API-KEY-ID"] == SCRUBBED
    assert scrubbed["apca-api-secret-key"] == SCRUBBED
    assert count == 3


def test_scrub_text_keeps_surrounding_text_and_only_scrubs_the_email() -> None:
    html = (
        "<html><body><p>Contact us at jose@example.com for details, or visit "
        "our site for more information about the product and pricing tiers "
        "available to new customers this quarter.</p></body></html>\n"
    )
    scrubbed, count = scrub_text(html, secrets=[])

    assert count == 1
    assert "jose@example.com" not in scrubbed
    assert SCRUBBED in scrubbed
    assert scrubbed.startswith("<html><body><p>Contact us at ")
    assert scrubbed.endswith("this quarter.</p></body></html>\n")


def test_scrub_text_scrubs_matching_lines_only() -> None:
    text = (
        "FORM TYPE  COMPANY NAME  CIK  DATE FILED  FILE NAME\n"
        "User-Agent: TradePartner Jose jose@example.com\n"
        "api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
        "10-K  Example Corp  0000320193  2024-01-05  edgar/data/example.txt\n"
    )
    scrubbed, count = scrub_text(text, secrets=[])
    lines = scrubbed.splitlines()

    assert lines[0] == "FORM TYPE  COMPANY NAME  CIK  DATE FILED  FILE NAME"
    assert lines[1] == SCRUBBED
    assert lines[2] == SCRUBBED
    assert lines[3] == "10-K  Example Corp  0000320193  2024-01-05  edgar/data/example.txt"
    assert count == 2


def test_scrub_text_scrubs_authorization_and_apca_header_lines() -> None:
    """`scrub_text`'s sensitive-header-line pattern matches the same field
    names as `scrub_json`'s `_SENSITIVE_HEADER_KEYS` (round 3,
    safety-reviewer): not just `User-Agent`."""
    text = (
        "Authorization: Bearer short-token\n"
        "APCA-API-KEY-ID: short\n"
        "apca-api-secret-key=also-short\n"
        "10-K  Example Corp  0000320193  2024-01-05  edgar/data/example.txt\n"
    )
    scrubbed, count = scrub_text(text, secrets=[])
    lines = scrubbed.splitlines()

    assert lines[0] == SCRUBBED
    assert lines[1] == SCRUBBED
    assert lines[2] == SCRUBBED
    assert lines[3] == "10-K  Example Corp  0000320193  2024-01-05  edgar/data/example.txt"
    assert count == 3


def test_key_management_personnel_compensation_does_not_trip_the_walk_test(
    tmp_path: Path,
) -> None:
    """A real us-gaap XBRL concept name; an earlier, looser KEY_SHAPED_PATTERN
    (bare "key"/"secret" substring, no label boundary) matched it."""
    payload = {
        "facts": {
            "us-gaap": {
                "KeyManagementPersonnelCompensation": {
                    "label": "Key Management Personnel Compensation",
                    "description": "Amount of key management personnel compensation.",
                }
            }
        }
    }
    fixture_file = tmp_path / "company_facts_example.json"
    fixture_file.write_text(json.dumps(payload), encoding="utf-8")

    _assert_file_is_scrubbed(fixture_file)  # raises AssertionError if it trips
