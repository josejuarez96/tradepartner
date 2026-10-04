"""#610: the EDGAR source's fixes from the 2026-10-02 backfill failure.

- X1: an FSN share (4 decimal places) agrees with a company-facts value
  that rounds to it.
- X2: when capping `as_of` at acceptance makes two company values collide,
  the value dated after acceptance is dropped (no look-ahead).
- Policy 1: the per-document denominator counts cache hits.
- Policy 2: a failed `check_failures()` records its pending failures, so
  they can be quarantined and `accepted` without a committed run;
  acceptance holds only for the same error class and message.
- Policy 3: FSN manifest failures are judged only by the FSN share rule,
  never pooled into the cross-day pair rule.
- P4: failure messages are persisted (failed_filings.json entries and FSN
  manifests), redacted and capped.

All offline: the routers serve fixtures, never the network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import duckdb
import pytest
from edgar_transport import USER_AGENT, edgar_settings, index_header
from test_edgar_failures import _fsn_settings, _one_bad_period_zip
from test_edgar_fsn import (
    _fsn_zip_bytes,
    _fsn_zip_url,
    _num,
    _router_with_fsn,
    _source_ready,
    _sub,
    _txt,
)
from test_edgar_source_cik import (
    APPLE,
    APPLE_ACCEPTED,
    APPLE_ACCESSION,
    BLANK_SIC_ACCESSION,
    INSIDE_LAG,
    SHARES,
    _apple_entries,
    _entry,
    _facts_router,
    _record,
    _router,
    _seed_apple,
    _seed_stamps,
    _settings,
    _shares,
    _source,
)

from tradepartner.adapters.edgar_source import (
    COVER_VERSION,
    FAILURES_VERSION,
    FSN_VERSION,
    HEADER_VERSION,
    EdgarFilingSource,
    FilingFailuresError,
)
from tradepartner.adapters.prices import PriceSource
from tradepartner.config import Settings
from tradepartner.ingest import FAILED, OK, ingest_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tradepartner.adapters.prices import Bar, CorporateAction

GARBAGE = b"<html>not a cover page</html>"


def _garbage_accession(n: int) -> str:
    return f"0000320193-26-0001{n:02d}"


def _document_url(accession: str, filename: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/320193/{accession.replace('-', '')}/{filename}"


def _garbage_source(
    settings: Settings, n: int, clock: datetime = INSIDE_LAG, body: bytes = GARBAGE
) -> EdgarFilingSource:
    """A source whose Apple stamps list `n` lag-window 10-Ks, each with a
    primary document that `parse_cover_page` rejects for real."""
    router = _router()
    records = {}
    for i in range(n):
        accession, filename = _garbage_accession(i), f"garbage-{i}.htm"
        router.add(_document_url(accession, filename), body)
        records[accession] = _record(accession, "10-K", INSIDE_LAG, primary_document=filename)
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: clock)
    source._filing_index_ran = True
    _seed_stamps(source, APPLE, records)
    return source


def _check(source: EdgarFilingSource) -> None:
    """`check_failures()` as `ingest._prefetch` runs it on a non-dry run: a
    raise records the run's failures first (policy 2)."""
    try:
        source.check_failures()
    except FilingFailuresError:
        source.record_failed_check()
        raise


def _store_path(settings: Settings) -> Path:
    return Path(settings.edgar.cache_dir) / "failed_filings.json"


def _read_store(settings: Settings) -> dict[str, dict[str, object]]:
    data = json.loads(_store_path(settings).read_text())
    assert data["version"] == FAILURES_VERSION
    entries: dict[str, dict[str, object]] = data["entries"]
    return entries


def _accept_all(settings: Settings) -> None:
    """What the owner does by hand: `"accepted": true` on each entry."""
    entries = _read_store(settings)
    for entry in entries.values():
        entry["accepted"] = True
    _store_path(settings).write_text(json.dumps({"version": FAILURES_VERSION, "entries": entries}))


def _manifest(failed: list[str], *, committed: bool = False) -> dict[str, object]:
    return {
        "version": FSN_VERSION,
        "period": "2025_10",
        "content_hash": "x",
        "validators": {},
        "accessions_extracted": [],
        "accessions_served": [],
        "incomplete_listings": 0,
        "accessions_failed": [
            {"accession": a, "error_class": "ValueError", "base_form": "10-K", "accepted": False}
            for a in failed
        ],
        "committed": committed,
    }


# --- X1: FSN's 4-decimal rounding is not a collision -------------------------

X1_ACCESSION = "0000320193-26-000300"
X1_ACCEPTED = datetime(2026, 3, 21, 0, 30, tzinfo=UTC)  # 2026-03-20 in New York


def _x1_zip(fsn_value: str | None) -> bytes:
    """The synthetic newest period with an FSN 10-Q for Apple whose share
    count FSN rounded to 4 decimal places (0000916457-18-000149's shape);
    `None` lists the 10-Q with no share row (#749)."""
    return _fsn_zip_bytes(
        [_sub(BLANK_SIC_ACCESSION, "320193", "8-K", sic=""), _sub(X1_ACCESSION, "320193", "10-Q")],
        [] if fsn_value is None else [_num(X1_ACCESSION, SHARES, fsn_value, "20260331")],
        [
            _txt(BLANK_SIC_ACCESSION, "Security12bTitle", "Common Stock"),
            _txt(BLANK_SIC_ACCESSION, "TradingSymbol", "AAPL"),
            _txt(BLANK_SIC_ACCESSION, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
            _txt(X1_ACCESSION, "Security12bTitle", "Common Stock"),
            _txt(X1_ACCESSION, "TradingSymbol", "AAPL"),
            _txt(X1_ACCESSION, "SecurityExchangeName", "Nasdaq Stock Market LLC"),
        ],
        [],
    )


def _x1_source(tmp_path: Path, company_value: float) -> EdgarFilingSource:
    payload = _apple_entries(_entry(X1_ACCESSION, "2026-03-13", company_value))
    router = _facts_router(**{APPLE: payload})
    router.add(_fsn_zip_url("2026_03"), _x1_zip("105.1597"))
    source = _source(_settings(tmp_path), router)
    _seed_apple(source, _record(X1_ACCESSION, "10-Q", X1_ACCEPTED))
    return source


def test_x1_an_fsn_value_rounded_to_4_places_agrees_with_company_facts(tmp_path: Path) -> None:
    source = _x1_source(tmp_path, 105.159666)
    records = [f for f in _shares(source, APPLE) if f.accession == X1_ACCESSION]
    # company facts win over FSN and keep their full precision
    assert [(f.as_of_date, f.value) for f in records] == [(date(2026, 3, 13), 105.159666)]
    assert source.failed_filings == 0


def test_x1_a_real_difference_from_fsn_is_still_a_collision(tmp_path: Path) -> None:
    source = _x1_source(tmp_path, 105.1604)  # 4-place rounding gives 105.1604, not 105.1597
    assert [f for f in _shares(source, APPLE) if f.accession == X1_ACCESSION] == []
    assert source.failed_filings == 1
    [(_class, _form, message)] = source._pending_failures.values()
    assert "differs between company and fsn" in message


# --- X2: a capped date never lets a later-dated value collide ----------------


def test_x2_a_value_dated_after_acceptance_is_dropped_when_capping_collides(
    tmp_path: Path,
) -> None:
    """CIK 1402328's shape: the filing reports a count on its acceptance date
    and a typo'd 0 dated after it; both cap to the acceptance date. The
    after-acceptance value is dropped and the filing's own value is served.
    No look-ahead: nothing is dated after the Eastern acceptance date."""
    payload = _apple_entries(
        _entry(APPLE_ACCESSION, "2025-10-31", 14_776_353_000),
        _entry(APPLE_ACCESSION, "2025-11-06", 0),
    )
    source = _source(_settings(tmp_path), _facts_router(**{APPLE: payload}))
    _seed_apple(source)
    records = [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION]
    assert [(f.as_of_date, f.value) for f in records] == [(date(2025, 10, 31), 14_776_353_000.0)]
    assert source.failed_filings == 0
    assert len(source.facts_capped_dropped) == 1
    accepted_eastern = APPLE_ACCEPTED.astimezone(ZoneInfo("America/New_York")).date()
    assert all(f.as_of_date <= accepted_eastern for f in records)
    assert all(f.as_of_date <= f.accepted_at.date() for f in records)


def test_x2_two_after_acceptance_values_still_collide(tmp_path: Path) -> None:
    """Fail closed: with no value dated on or before acceptance there is
    nothing the filing reports to keep, so the clash is still a collision."""
    payload = _apple_entries(
        _entry(APPLE_ACCESSION, "2025-11-05", 1),
        _entry(APPLE_ACCESSION, "2025-11-06", 2),
    )
    source = _source(_settings(tmp_path), _facts_router(**{APPLE: payload}))
    _seed_apple(source)
    assert [f for f in _shares(source, APPLE) if f.accession == APPLE_ACCESSION] == []
    assert source.failed_filings == 1


# --- Policy 1: cache hits count in the per-document denominator --------------


def _write_cover_cache(settings: Settings, accession: str) -> None:
    path = Path(settings.edgar.cache_dir) / "cover" / f"v{COVER_VERSION}" / f"{accession}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": COVER_VERSION,
        "accession": accession,
        "cik": APPLE,
        "entity_cik": APPLE,
        "listings": [],
        "facts": [],
    }
    path.write_text(json.dumps(data))


def _write_header_cache(settings: Settings, accession: str) -> None:
    path = (
        Path(settings.edgar.cache_dir)
        / "header"
        / f"v{HEADER_VERSION}"
        / str(int(APPLE))
        / f"{accession}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": HEADER_VERSION, "accession": accession, "cik": APPLE, "sic": 3571})
    )


def test_policy1_cover_cache_hits_count_in_the_per_document_denominator(tmp_path: Path) -> None:
    """5 failures among 5 fetched would be 100%; with the 6 cached covers the
    run read, 5 of 11 (45%) is under a 50% ceiling and the check passes."""
    settings = _settings(tmp_path, max_failed_filing_share=0.5)
    source = _garbage_source(settings, 5)
    stamps = source._load_stamps(APPLE)
    for n in range(6):
        accession = f"0000320193-26-0002{n:02d}"
        _write_cover_cache(settings, accession)
        stamps[accession] = _record(accession, "10-K", INSIDE_LAG)
    _seed_stamps(source, APPLE, stamps)
    source.cover_pages(APPLE)
    assert len(source._pending_failures) == 5
    assert len(source._per_document_attempted) == 11
    reasons: list[str] = []
    source._check_per_document_group(reasons)
    assert reasons == []


def test_policy1_header_cache_hits_count_in_the_per_document_denominator(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _garbage_source(settings, 0)
    accession = "0000320193-26-000300"
    _write_header_cache(settings, accession)
    _seed_stamps(source, APPLE, {accession: _record(accession, "8-K", INSIDE_LAG)})
    [header] = source.filing_headers(APPLE, ["8-K"])
    assert header.sic == 3571
    assert source._per_document_attempted == {accession}


@pytest.mark.parametrize("accepted", [False, True])
def test_a_quarantined_accession_counts_in_the_share_unless_accepted(
    tmp_path: Path, accepted: bool
) -> None:
    """quant-auditor (#610): a failed check can now quarantine, so a
    quarantined accession stays in the per-document share: in the
    denominator always, in the numerator unless its entry is accepted."""
    settings = _settings(tmp_path, min_failed_filings=1)
    source = _garbage_source(settings, 1)
    entry = {
        "error_class": "ValueError",
        "base_form": "10-K",
        "message_hash": "h",
        "count": settings.edgar.max_filing_failures,
        "last_counted_day": "2026-05-01",
        "accepted": accepted,
        "kind": "fetch",
    }
    _store_path(settings).parent.mkdir(parents=True, exist_ok=True)
    _store_path(settings).write_text(
        json.dumps({"version": FAILURES_VERSION, "entries": {_garbage_accession(0): entry}})
    )
    source.cover_pages(APPLE)
    assert source.quarantined == 1
    assert source._per_document_attempted == set()  # no request was made
    reasons: list[str] = []
    source._check_per_document_group(reasons)
    assert reasons == (
        [] if accepted else ["per-document: 1 failures of 1 attempted (100.0%, over 1.0%)"]
    )


def test_a_quarantined_cover_page_still_counts_after_its_header_cache_hit(tmp_path: Path) -> None:
    """/code-review (#610): a cached SGML header puts the accession in the
    attempted set, but its quarantined, un-accepted cover page is still a
    failure: the header hit must not lift it out of the numerator."""
    settings = _settings(tmp_path, min_failed_filings=1)
    source = _garbage_source(settings, 1)
    accession = _garbage_accession(0)
    entry = {
        "error_class": "ValueError",
        "base_form": "10-K",
        "message_hash": "h",
        "count": settings.edgar.max_filing_failures,
        "last_counted_day": "2026-05-01",
        "accepted": False,
        "kind": "fetch",
    }
    _store_path(settings).parent.mkdir(parents=True, exist_ok=True)
    _store_path(settings).write_text(
        json.dumps({"version": FAILURES_VERSION, "entries": {accession: entry}})
    )
    _write_header_cache(settings, accession)
    source.filing_headers(APPLE, ["10-K"])
    source.cover_pages(APPLE)
    assert source.quarantined == 1
    assert source._per_document_attempted == {accession}  # the header cache hit
    reasons: list[str] = []
    source._check_per_document_group(reasons)
    assert reasons == ["per-document: 1 failures of 1 attempted (100.0%, over 1.0%)"]


# --- Policy 2: a failed check records its failures ---------------------------


def test_policy2_a_failed_check_records_its_failures(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _garbage_source(settings, 5)
    source.cover_pages(APPLE)
    assert not _store_path(settings).exists()
    with pytest.raises(FilingFailuresError, match=r"per-document.*failed_filings\.json"):
        _check(source)
    entries = _read_store(settings)
    assert set(entries) == {_garbage_accession(n) for n in range(5)}
    for entry in entries.values():
        assert entry["count"] == 1
        assert entry["accepted"] is False
        assert entry["kind"] == "fetch"
        assert entry["error_class"] == "ValueError"
        assert isinstance(entry["message"], str) and entry["message"]  # P4


def test_policy2_a_failed_check_never_commits_the_fsn_manifests(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _garbage_source(settings, 5)
    source.cover_pages(APPLE)
    with pytest.raises(FilingFailuresError):
        _check(source)
    periods = source._cached_fsn_periods()
    assert periods
    for period in periods:
        manifest = source._load_fsn_manifest(period)
        assert manifest is not None and manifest["committed"] is False


def test_policy2_accepted_failures_then_pass_the_check(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = _garbage_source(settings, 5)
    first.cover_pages(APPLE)
    with pytest.raises(FilingFailuresError):
        _check(first)
    _accept_all(settings)

    rerun = _garbage_source(settings, 5)
    rerun.cover_pages(APPLE)
    assert len(rerun._pending_failures) == 5  # still failing, each accepted
    _check(rerun)


def test_policy2_an_accepted_entry_does_not_excuse_a_different_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: `accepted` covers the error class and message hash it was
    given for; the same accession failing differently is judged again."""
    settings = _settings(tmp_path)
    first = _garbage_source(settings, 5)
    first.cover_pages(APPLE)
    with pytest.raises(FilingFailuresError):
        _check(first)
    _accept_all(settings)

    changed = _garbage_source(settings, 5)
    monkeypatch.setattr(changed, "_fetch_cover_page", _raise_other)
    changed.cover_pages(APPLE)
    with pytest.raises(FilingFailuresError, match="per-document"):
        _check(changed)


def _raise_other(*args: object, **kwargs: object) -> None:
    raise ValueError("a different failure than the one accepted")


def test_policy2_failed_checks_quarantine_without_a_committed_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    max_days = settings.edgar.max_filing_failures
    for day in range(max_days):
        source = _garbage_source(settings, 5, clock=datetime(2026, 6, 2 + day, 16, tzinfo=UTC))
        source.cover_pages(APPLE)
        with pytest.raises(FilingFailuresError):
            _check(source)
    final = _garbage_source(settings, 5, clock=datetime(2026, 6, 2 + max_days, 16, tzinfo=UTC))
    assert all(final._is_quarantined(_garbage_accession(n)) for n in range(5))
    final.cover_pages(APPLE)
    assert final.quarantined == 5
    assert final._per_document_attempted == set()
    # quarantine never excuses on its own (quant-auditor, #610): the five
    # quarantined, un-accepted accessions still fail the per-document share
    with pytest.raises(FilingFailuresError, match="per-document: 5 failures of 5"):
        _check(final)
    _accept_all(settings)
    accepted = _garbage_source(settings, 5, clock=datetime(2026, 6, 3 + max_days, 16, tzinfo=UTC))
    accepted.cover_pages(APPLE)
    assert accepted.quarantined == 5
    _check(accepted)  # accepted and quarantined: passes, with no request


def test_policy2_a_second_failed_check_the_same_day_counts_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    for _ in range(2):
        source = _garbage_source(settings, 5)
        source.cover_pages(APPLE)
        with pytest.raises(FilingFailuresError):
            _check(source)
    assert {e["count"] for e in _read_store(settings).values()} == {1}


def test_policy2_a_failed_check_keeps_an_owner_hand_edit(tmp_path: Path) -> None:
    """The record on a failed check re-reads the file under the lock, as
    `record_failures` does: an acceptance made mid-run survives it."""
    settings = _settings(tmp_path)
    other = "0000320193-26-000999"
    entry = {
        "error_class": "ValueError",
        "base_form": "10-K",
        "message_hash": "x",
        "count": 1,
        "last_counted_day": "2026-05-01",
        "accepted": False,
        "kind": "fetch",
    }
    _store_path(settings).parent.mkdir(parents=True, exist_ok=True)
    _store_path(settings).write_text(
        json.dumps({"version": FAILURES_VERSION, "entries": {other: entry}})
    )
    source = _garbage_source(settings, 5)
    source.cover_pages(APPLE)
    _store_path(settings).write_text(
        json.dumps({"version": FAILURES_VERSION, "entries": {other: {**entry, "accepted": True}}})
    )
    with pytest.raises(FilingFailuresError):
        _check(source)
    entries = _read_store(settings)
    assert entries[other]["accepted"] is True
    assert len(entries) == 6


# --- end to end: the thresholds, acceptance and fail-closed together ---------


def test_check_failures_end_to_end(tmp_path: Path) -> None:
    """1 un-accepted failure (under min_failed_filings) passes; 6 failures
    all accepted pass; 6 un-accepted failures (over both thresholds) raise."""
    one = _garbage_source(_settings(tmp_path / "one"), 1)
    one.cover_pages(APPLE)
    assert len(one._pending_failures) == 1
    _check(one)

    settings = _settings(tmp_path / "six")
    six = _garbage_source(settings, 6)
    six.cover_pages(APPLE)
    with pytest.raises(FilingFailuresError, match="per-document: 6 failures of 6"):
        _check(six)
    _accept_all(settings)
    accepted = _garbage_source(settings, 6)
    accepted.cover_pages(APPLE)
    _check(accepted)

    unaccepted = _garbage_source(_settings(tmp_path / "again"), 6)
    unaccepted.cover_pages(APPLE)
    with pytest.raises(FilingFailuresError):
        _check(unaccepted)


@dataclass
class _NoPrices(PriceSource):
    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return []

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        return []


def _ingest_with_store(tmp_path: Path, *, accepted: bool) -> str:
    """`ingest_session` over an empty universe with five recorded failures
    of one (error class, base form) pair on disk."""
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    settings = edgar_settings(tmp_path / "edgar", index_first_year=2026, fsn_first_year=2026)
    router = _router()
    router.add_index(2026, 1, index_header())
    router.add_index(2026, 2, index_header())
    Path(settings.edgar.cache_dir).mkdir(parents=True, exist_ok=True)
    entries = {
        f"acc-{n}": {
            "error_class": "ValueError",
            "base_form": "25-NSE",
            "message_hash": "h",
            "count": 1,
            "last_counted_day": "2026-05-01",
            "accepted": accepted,
        }
        for n in range(5)
    }
    _store_path(settings).write_text(json.dumps({"version": FAILURES_VERSION, "entries": entries}))
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: now)
    store_settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        edgar=settings.edgar.model_dump(),
        sec_edgar_user_agent=settings.sec_edgar_user_agent,
    )
    [run] = ingest_session(
        store_settings, prices=_NoPrices(), filings=source, source="edgar", clock=lambda: now
    ).runs
    with duckdb.connect(store_settings.store.path, read_only=True) as conn:
        [(status,)] = conn.execute("SELECT status FROM ingestion_runs").fetchall()
    assert status == run.status
    return str(run.status)


def test_ingest_session_passes_once_the_recorded_failures_are_accepted(tmp_path: Path) -> None:
    assert _ingest_with_store(tmp_path / "open", accepted=False) == FAILED
    assert _ingest_with_store(tmp_path / "accepted", accepted=True) == OK


# --- Policy 3: FSN failures are judged by the FSN rule only ------------------


def test_policy3_fsn_manifest_failures_are_not_pooled_into_the_pair_rule(
    tmp_path: Path,
) -> None:
    """3 per-document entries plus 2 FSN failures of the same pair: 5 pooled
    would trip the pair rule; FSN is judged by its own share rule only."""
    source = _garbage_source(_settings(tmp_path), 0)
    source._failed_filings_cache = {
        f"a-{n}": {
            "error_class": "ValueError",
            "base_form": "10-K",
            "message_hash": "h",
            "count": 1,
            "last_counted_day": "2026-06-01",
            "accepted": False,
        }
        for n in range(3)
    }
    source._save_fsn_manifest("2025_10", _manifest(["fsn-0", "fsn-1"], committed=True))
    source.check_failures()


def test_policy3_many_fsn_failures_under_the_fsn_share_pass(tmp_path: Path) -> None:
    """255 failures of ~700k tripped the pooled pair rule on their own; here
    10 failures among 10,000 served (0.1%, under the 1% ceiling) pass."""
    source = _garbage_source(_settings(tmp_path), 0)
    manifest = _manifest([f"fsn-{n}" for n in range(10)])
    manifest["accessions_served"] = [f"served-{n}" for n in range(10_000)]
    source._save_fsn_manifest("2025_10", manifest)
    source.check_failures()


# --- P4: failure messages on disk --------------------------------------------


def test_p4_fsn_manifest_failures_carry_their_message(tmp_path: Path) -> None:
    accession, cik = "0000000043-15-000001", "0000000043"
    settings = _fsn_settings(tmp_path)
    router = _router_with_fsn("2015q1", zips={"2015q1": _one_bad_period_zip(accession, cik)})
    source = _source_ready(settings, router)
    source._ensure_fsn()
    manifest = source._load_fsn_manifest("2015q1")
    assert manifest is not None
    [failure] = manifest["accessions_failed"]
    assert failure["accession"] == accession
    assert "malformed payload" in failure["message"]
    assert failure["accepted"] is False


def test_p4_stored_messages_are_redacted_cleaned_and_capped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = _garbage_source(settings, 0)
    long = f"bad {USER_AGENT}\x1b[31m " + "x" * 10_000
    source._record_failure("0000320193-26-000001", "ValueError", "10-K", long)
    source.record_failures()
    [entry] = _read_store(settings).values()
    message = str(entry["message"])
    assert USER_AGENT not in message and "[redacted]" in message
    assert "\x1b" not in message
    assert len(message) <= settings.ingest.max_message_chars
    # the hash is still of the raw message, so acceptance matches the next run
    assert entry["message_hash"] == hashlib.sha256(long.encode("utf-8")).hexdigest()


def test_p4_stored_messages_and_run_rows_share_one_redaction(tmp_path: Path) -> None:
    """#629: `failed_filings.json` and the run row clean a message with one
    helper, so the two paths cannot drift."""
    from tradepartner import ingest
    from tradepartner.config import clean_message

    settings = _settings(tmp_path)
    source = _garbage_source(settings, 0)
    message = f"bad {USER_AGENT}\x00\x1b[31m " + "x" * 10_000
    assert ingest._clean is clean_message
    assert source._stored_message(message) == clean_message(message, settings)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (float("nan"), 1.0, False),
        (1.0, float("nan"), False),
        (float("nan"), float("nan"), False),
        (float("inf"), float("inf"), False),
        (float("inf"), float("-inf"), False),
        (float("inf"), 1.0, False),
    ],
)
def test_a_non_finite_value_compares_without_raising(a: float, b: float, same: bool) -> None:
    """#629: a NaN or infinite value against FSN raised
    `decimal.InvalidOperation`, failing the whole source; a non-finite value
    now never agrees, from any source, so the key is withheld."""
    from tradepartner.adapters.edgar_source import _same_value

    assert _same_value("fsn", a, "company", b) is same
    assert _same_value("company", a, "fsn", b) is same
    assert _same_value("company", a, "document", b) is same


# --- #749: a non-finite value from a single source is never served ------------

NON_FINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("value", NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_non_finite_company_value_from_one_source_is_withheld(
    tmp_path: Path, value: float
) -> None:
    """#749: a key only company facts supply is never compared, so a NaN or
    infinity was served as a fact. It is withheld and recorded like a
    collision; the CIK's other keys are still served."""
    payload = _apple_entries(
        _entry(APPLE_ACCESSION, "2025-10-31", 14_776_353_000),
        _entry(X1_ACCESSION, "2026-03-13", value),
    )
    # raw bytes: `json.loads` accepts the `NaN`/`Infinity` tokens httpx won't encode
    router = _facts_router(**{APPLE: json.dumps(payload).encode("utf-8")})
    router.add(_fsn_zip_url("2026_03"), _x1_zip(None))  # FSN covers it, no share row
    source = _source(_settings(tmp_path), router)
    _seed_apple(source, _record(X1_ACCESSION, "10-Q", X1_ACCEPTED))
    records = _shares(source, APPLE)
    assert [f for f in records if f.accession == X1_ACCESSION] == []
    assert [(f.accession, f.value) for f in records if f.accession == APPLE_ACCESSION] == [
        (APPLE_ACCESSION, 14_776_353_000.0)
    ]
    assert source.failed_filings == 1
    [(error_class, base_form, message)] = source._pending_failures.values()
    assert (error_class, base_form) == ("ValueError", "10-Q")
    assert "not finite" in message
    assert source._collision_failures == {X1_ACCESSION}


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity"])
def test_a_non_finite_fsn_value_from_one_source_is_withheld(tmp_path: Path, raw: str) -> None:
    """#749: the same for a key only FSN supplies (no company-facts entry
    for the accession): withheld and recorded like a collision. (An FSN
    `NaN` already fails its accession's extraction: `parse_fsn`'s two-values
    check sees `NaN != NaN`.)"""
    payload = _apple_entries(_entry(APPLE_ACCESSION, "2025-10-31", 14_776_353_000))
    router = _facts_router(**{APPLE: payload})
    router.add(_fsn_zip_url("2026_03"), _x1_zip(raw))
    source = _source(_settings(tmp_path), router)
    _seed_apple(source, _record(X1_ACCESSION, "10-Q", X1_ACCEPTED))
    records = _shares(source, APPLE)
    assert [f for f in records if f.accession == X1_ACCESSION] == []
    assert [f.value for f in records if f.accession == APPLE_ACCESSION] == [14_776_353_000.0]
    [(error_class, base_form, message)] = source._pending_failures.values()
    assert (error_class, base_form) == ("ValueError", "10-Q")
    assert "not finite" in message
    assert source._collision_failures == {X1_ACCESSION}
