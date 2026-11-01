"""Network smoke test for `adapters.edgar_raw` against the real SEC EDGAR API.

Skipped unless `RUN_NETWORK_TESTS=1` is set alongside a real
`SEC_EDGAR_USER_AGENT` in the environment/`.env`. Not a parser test (T11
owns those on recorded fixtures) — it only proves the raw client talks to
the real API, respects the throttle, and returns plain JSON/text, never a
parsed record. For offline coverage of the throttle/retry/credential/
path-safety behavior (mocked transport, no real network), see
`test_edgar_raw_offline.py`.
"""

from __future__ import annotations

import json
import os

import pytest

from tradepartner.adapters import edgar_raw

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("RUN_NETWORK_TESTS") != "1",
        reason="network test; set RUN_NETWORK_TESTS=1 to run",
    ),
]

_APPLE_CIK = "0000320193"


def test_company_tickers_returns_json_serializable_payload() -> None:
    payload = edgar_raw.company_tickers()
    json.dumps(payload)


def test_submissions_returns_payload_for_a_known_cik() -> None:
    payload = edgar_raw.submissions(_APPLE_CIK)

    assert payload["cik"] == str(int(_APPLE_CIK))
    json.dumps(payload)


def test_company_facts_returns_json_serializable_payload() -> None:
    payload = edgar_raw.company_facts(_APPLE_CIK)
    json.dumps(payload)


def test_filing_index_quarter_returns_text() -> None:
    text = edgar_raw.filing_index_quarter(2024, 1)

    assert isinstance(text, str)
    assert len(text) > 0


def test_submissions_page_rejects_a_name_that_is_not_a_page() -> None:
    """The page name flows into a URL, so only the documented shape is accepted (T3)."""
    for bad in ["../secret.json", "CIK0000320193.json", "CIK0000320193-submissions-1.json", ""]:
        with pytest.raises(edgar_raw.InvalidFilingReferenceError):
            edgar_raw.submissions_page(bad)


def test_insider_bulk_zip_reads_a_published_quarter_and_a_future_one_as_unpublished() -> None:
    """T305b (#2498): a published DERA quarter streams; a quarter far ahead is a 404."""
    published = edgar_raw.insider_bulk_zip("2025q4")
    assert published is not None and published[0].is_file()
    assert edgar_raw.insider_bulk_zip("2099q1") is None
