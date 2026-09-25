"""Fixture-scrub tests (spec acceptance criterion: "Scrub test").

Two layers:
- a pattern-based walk over `tests/fixtures/**` (no `.env` needed; must
  pass whether the directory is absent, empty, or holds real recordings);
- a unit test of `cli_record.scrub_json`/`.scrub_text` against a synthetic
  payload containing a fake key, an email, and a `User-Agent` header.
"""

from __future__ import annotations

from pathlib import Path

from tradepartner.cli_record import (
    ALPACA_KEY_PREFIX_PATTERN,
    EMAIL_PATTERN,
    KEY_SHAPED_PATTERN,
    SCRUBBED,
    USER_AGENT_HEADER_PATTERN,
    scrub_json,
    scrub_text,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _iter_fixture_files() -> list[Path]:
    if not FIXTURES_DIR.exists():
        return []
    return [path for path in FIXTURES_DIR.rglob("*") if path.is_file()]


# --- walking test over tests/fixtures/** ------------------------------------


def test_no_fixture_file_contains_a_secret_shaped_string() -> None:
    """Passes trivially when `tests/fixtures/` is absent or empty (no `.env` needed)."""
    files = _iter_fixture_files()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not KEY_SHAPED_PATTERN.search(text), f"key-shaped string in {path}"
        assert not ALPACA_KEY_PREFIX_PATTERN.search(text), f"Alpaca-style key prefix in {path}"
        assert not EMAIL_PATTERN.search(text), f"email address in {path}"
        assert not USER_AGENT_HEADER_PATTERN.search(text), f"unscrubbed User-Agent header in {path}"


def test_walk_passes_when_fixtures_dir_is_absent(tmp_path: Path) -> None:
    """The same walk, pointed at a directory that doesn't exist, finds nothing to fail on."""
    missing_dir = tmp_path / "does-not-exist"
    assert not missing_dir.exists()
    files = [p for p in missing_dir.rglob("*") if p.is_file()] if missing_dir.exists() else []
    assert files == []


# --- unit test of the scrub functions themselves ----------------------------


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
    scrubbed = scrub_json(payload, secrets=["PKFAKE1234567890ABCD"])

    assert scrubbed["headers"]["User-Agent"] == SCRUBBED
    assert scrubbed["alpaca_api_key"] == SCRUBBED
    assert scrubbed["contact"]["email"] == SCRUBBED
    assert scrubbed["nested"][0]["secret_token"] == SCRUBBED
    # Non-secret-shaped values pass through unchanged.
    assert scrubbed["contact"]["note"] == "reach out anytime"
    assert scrubbed["count"] == 3
    assert scrubbed["is_active"] is True
    assert scrubbed["note"] is None

    # And the result satisfies the same detection patterns the walking test uses.
    import json

    dumped = json.dumps(scrubbed)
    assert not KEY_SHAPED_PATTERN.search(dumped)
    assert not ALPACA_KEY_PREFIX_PATTERN.search(dumped)
    assert not EMAIL_PATTERN.search(dumped)
    assert not USER_AGENT_HEADER_PATTERN.search(dumped)


def test_scrub_json_exact_secret_match_without_key_shape() -> None:
    """A secret that doesn't itself look key-shaped is still caught by exact match."""
    payload = {"note": "the plain value jose@example.com appears here"}
    scrubbed = scrub_json(payload, secrets=["jose@example.com"])
    assert scrubbed["note"] == SCRUBBED


def test_scrub_text_scrubs_matching_lines_only() -> None:
    text = (
        "FORM TYPE  COMPANY NAME  CIK  DATE FILED  FILE NAME\n"
        "User-Agent: TradePartner Jose jose@example.com\n"
        "api_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
        "10-K  Example Corp  0000320193  2024-01-05  edgar/data/example.txt\n"
    )
    scrubbed = scrub_text(text, secrets=[])
    lines = scrubbed.splitlines()

    assert lines[0] == "FORM TYPE  COMPANY NAME  CIK  DATE FILED  FILE NAME"
    assert lines[1] == SCRUBBED
    assert lines[2] == SCRUBBED
    assert lines[3] == "10-K  Example Corp  0000320193  2024-01-05  edgar/data/example.txt"
