"""The per-CIK and per-document inputs (#578 part 3, owner option (a) on
#808): a per-CIK submissions API payload or older page that does not parse
is recorded on the validation collector and lists nothing for the run;
per-document failures keep the one failure policy (`failed_filings.json`,
its allowance and `accepted`), and `check_failures` lists the run's
unaccepted ones in its pre-write message. The companyfacts API cases are in
`test_edgar_source_cik.py`, next to the rest of the company-facts path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from edgar_transport import edgar_settings
from test_edgar_failures import _bare_source
from test_edgar_source import (
    ALPHABET,
    APPLE,
    MISSING,
    SUBMISSIONS_URL,
    _router,
    _source,
    _submission_urls,
)

from tradepartner.adapters.edgar_source import EdgarFilingSource, FilingFailuresError
from tradepartner.config import Settings

#: A submissions page whose columns do not line up (`zip(strict=True)`).
BAD_COLUMNS = {
    "accessionNumber": ["0000320193-20-000001"],
    "form": [],
    "primaryDocument": [],
    "isInlineXBRL": [],
    "acceptanceDateTime": [],
}


def test_a_submissions_payload_that_does_not_parse_lists_nothing(tmp_path: Path) -> None:
    """Alphabet's payload is recorded; its rows stay unstamped and nothing
    is cached as unstampable for it, so a later run with the real payload
    stamps them exactly as a clean run does."""
    settings = edgar_settings(tmp_path / "cache")
    router = _router()
    router.add(f"{SUBMISSIONS_URL}CIK{ALPHABET}.json", {"cik": ALPHABET, "filings": []})
    source = _source(settings, router)
    entries = source.filing_index()
    [failure] = source.validation_failures
    assert (failure.input, failure.key) == ("submissions API", f"CIK{ALPHABET}.json")
    assert failure.error.startswith("ValueError: ") and "malformed" in failure.error
    assert not any(e.cik == ALPHABET for e in entries)
    assert ALPHABET in {u.cik for u in source.unstamped_filings}
    assert source.submissions_api_empty == 0
    full = _source(edgar_settings(tmp_path / "clean"), _router()).filing_index()
    later = _source(settings, _router())
    assert later.filing_index() == full
    assert len(later.validation_failures) == 0


def test_an_older_page_that_does_not_parse_stops_paging(tmp_path: Path) -> None:
    """Apple's older page is recorded and paging stops; the accession only
    a later page could stamp stays unstamped and is never cached as
    unstampable."""
    settings = edgar_settings(tmp_path / "cache")
    router = _router()
    page = f"CIK{APPLE}-submissions-001.json"
    router.add(f"{SUBMISSIONS_URL}{page}", BAD_COLUMNS)
    source = _source(settings, router)
    source.filing_index()
    assert [(f.input, f.key) for f in source.validation_failures] == [("submissions API", page)]
    assert page in _submission_urls(router)
    assert MISSING in {u.accession for u in source.unstamped_filings}
    again_router = _router()
    again = _source(settings, again_router)
    again.filing_index()
    assert page in _submission_urls(again_router)  # asked again, never cached away
    assert MISSING in {u.accession for u in again.unstamped_filings}
    assert len(again.validation_failures) == 0


@pytest.mark.parametrize("which", ["payload", "page"])
def test_a_submissions_body_that_is_not_json_is_recorded(tmp_path: Path, which: str) -> None:
    """quant-auditor on #881: a non-JSON body (an HTML error page served as
    200) is recorded like a malformed payload, never a crash, and lists
    nothing."""
    settings = edgar_settings(tmp_path / "cache")
    router = _router()
    name = f"CIK{APPLE}.json" if which == "payload" else f"CIK{APPLE}-submissions-001.json"
    router.add(f"{SUBMISSIONS_URL}{name}", b"<html>busy</html>")
    source = _source(settings, router)
    source.filing_index()
    [failure] = source.validation_failures
    assert (failure.input, failure.key) == ("submissions API", name)
    assert failure.error.startswith("JSONDecodeError")
    assert MISSING in {u.accession for u in source.unstamped_filings}


# --- per-document failures: one policy, listed in the check's message ---------


def test_the_check_lists_the_unaccepted_per_document_failures(tmp_path: Path) -> None:
    source = _bare_source(tmp_path)
    accessions = [f"0000320193-26-000{k:03d}" for k in range(7)]
    source._pending_failures = {a: ("ValueError", "10-K", f"bad {a}") for a in accessions}
    source._per_document_attempted = set(accessions)
    accepted = accessions[0]
    source._failed_filings_cache = {
        accepted: {
            "error_class": "ValueError",
            "base_form": "10-K",
            "message_hash": hashlib.sha256(f"bad {accepted}".encode()).hexdigest(),
            "count": 1,
            "last_counted_day": "2026-06-01",
            "accepted": True,
        }
    }
    with pytest.raises(FilingFailuresError) as raised:
        source.check_failures()
    message = str(raised.value)
    assert "unaccepted filing failures (per-document and fact collisions): 6, first 6: " in message
    assert f"{accessions[1]} ValueError/10-K: bad {accessions[1]}" in message
    assert f"{accepted} ValueError" not in message
    assert message.index("failed_filings.json") < message.index(f"{accessions[1]} ValueError")


def test_the_listing_is_bounded_and_redacted(tmp_path: Path) -> None:
    secret = "sk-sentinel-808"
    base = edgar_settings(tmp_path / "cache", max_validation_listed=2)
    settings = Settings(
        _env_file=None,
        edgar=base.edgar.model_dump(),
        sec_edgar_user_agent=base.sec_edgar_user_agent,
        alpaca_api_secret=secret,
    )
    source = EdgarFilingSource(settings, client=_router().client())
    accessions = [f"0000320193-26-000{k:03d}" for k in range(5)]
    source._pending_failures = {a: ("ValueError", "10-K", f"token {secret}") for a in accessions}
    source._per_document_attempted = set(accessions)
    with pytest.raises(FilingFailuresError) as raised:
        source.check_failures()
    message = str(raised.value)
    assert "fact collisions): 5, first 2: " in message
    assert accessions[1] in message and accessions[2] not in message
    assert secret not in message


def test_the_full_listing_is_written_to_disk_and_named_first(tmp_path: Path) -> None:
    """#884: past `edgar.max_validation_listed`, the message names a JSON file
    under `edgar.cache_dir/validation/` holding every unaccepted failure
    (cleaned and redacted, like the message); the path opens the message,
    so the run row's `ingest.max_message_chars` cut keeps it. A dry
    run writes no `failed_filings.json`, so this file is its only full list."""
    secret = "sk-sentinel-884"
    base = edgar_settings(tmp_path / "cache", max_validation_listed=2)
    settings = Settings(
        _env_file=None,
        edgar=base.edgar.model_dump(),
        sec_edgar_user_agent=base.sec_edgar_user_agent,
        alpaca_api_secret=secret,
    )
    source = EdgarFilingSource(settings, client=_router().client())
    accessions = [f"0000320193-26-000{k:03d}" for k in range(5)]
    source._pending_failures = {a: ("ValueError", "10-K", f"bad {a} {secret}") for a in accessions}
    source._per_document_attempted = set(accessions)
    with pytest.raises(FilingFailuresError) as raised:
        source.check_failures()
    message = str(raised.value)

    [written] = (Path(settings.edgar.cache_dir) / "validation").glob("filing-failures-*.json")
    assert message.startswith(
        f"full list of this run's 5 unaccepted filing failures: {written.resolve()}; "
    )
    assert "fact collisions): 5, first 2: " in message
    assert not (Path(settings.edgar.cache_dir) / "failed_filings.json").exists()
    data = json.loads(written.read_text())
    assert [entry["accession"] for entry in data["failures"]] == accessions
    assert data["failures"][4]["error_class"] == "ValueError"
    assert data["failures"][4]["base_form"] == "10-K"
    assert data["failures"][4]["message"].startswith(f"bad {accessions[4]} ")
    assert secret not in written.read_text()


def test_a_listing_file_that_cannot_be_written_still_fails_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full_disk(path: Path, data: bytes) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr("tradepartner.adapters.edgar_source.edgar_raw.write_atomic", full_disk)
    source = _bare_source(tmp_path)
    accessions = [f"0000320193-26-000{k:03d}" for k in range(6)]
    source._pending_failures = {a: ("ValueError", "10-K", "x") for a in accessions}
    source._per_document_attempted = set(accessions)
    with pytest.raises(FilingFailuresError) as raised:
        source.check_failures()
    assert str(raised.value).startswith(
        "full list of this run's 6 unaccepted filing failures: "
        "not written (OSError: No space left on device); "
    )


def test_a_run_under_the_allowance_does_not_fail(tmp_path: Path) -> None:
    """Option (a): the per-document failures fail the run only where the
    existing rules fire; 4 is under `min_failed_filings` (5)."""
    source = _bare_source(tmp_path)
    accessions = [f"0000320193-26-000{k:03d}" for k in range(4)]
    source._pending_failures = {a: ("ValueError", "10-K", "x") for a in accessions}
    source._per_document_attempted = set(accessions)
    source.check_failures()
