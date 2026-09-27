"""The T11f failure policy: skip-never-raise, quarantine, `record_failures()`
and `check_failures()` (plan amendment #216, owner decision (2)).

Reuses `test_edgar_source_cik.py`'s FSN router/settings/seeded-stamps
shortcut (`_router`, `_settings`, `_source`, `_seed_stamps`, `_record`) so
`cover_pages`/`filing_headers` exercise the real per-document fetch/parse
path; a forged fact collision is the one failure kind stubbed directly
(monkeypatching `_fetch_cover_page`), since `facts()` (T11e) is not this
task's to implement (see the module docstring of `edgar_source.py` and
CLAUDE.md's task scope).

`check_failures()`'s threshold rules are exercised by setting the adapter's
internal state directly (`_pending_failures`, `_per_document_attempted`,
`_failed_filings_cache`, FSN manifests) rather than driving thousands of
fetches through the router: the rules are pure arithmetic over those,
already covered end-to-end by the skip/quarantine tests above them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_edgar_source_cik import APPLE, INSIDE_LAG, _record, _router, _seed_stamps, _settings

from tradepartner.adapters.edgar_source import (
    FAILURES_VERSION,
    EdgarFilingSource,
    FilingFailuresError,
)

APPLE_ACCESSION = "0000320193-26-000100"


def _mutable_clock(start: datetime) -> tuple[list[datetime], object]:
    box = [start]
    return box, (lambda: box[0])


def _garbage_source(
    tmp_path: Path, clock_fn: object, *, filename: str = "garbage.htm"
) -> tuple[EdgarFilingSource, object]:
    """A source with one lag-window Apple accession whose primary document is
    not real iXBRL: `parse_cover_page` raises `ValueError` for real."""
    settings = _settings(tmp_path)
    router = _router()
    router.add(
        f"https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/{filename}",
        b"<html>not a cover page</html>",
    )
    source = EdgarFilingSource(settings, client=router.client(), clock=clock_fn)
    source._filing_index_ran = True
    _seed_stamps(
        source,
        APPLE,
        {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", INSIDE_LAG, primary_document=filename)},
    )
    return source, router


# --- skip, never raise -------------------------------------------------------


def test_a_parser_valueerror_is_skipped_and_recorded(tmp_path: Path) -> None:
    _clock, clock_fn = _mutable_clock(INSIDE_LAG)
    source, router = _garbage_source(tmp_path, clock_fn)
    assert source.cover_pages(APPLE) == []
    assert source.failed_filings == 1
    assert len(router.urls) > 0  # the document was actually requested


def test_a_forged_fact_collision_is_skipped_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`facts()` (T11e) is not implemented here; a collision is forged by
    stubbing the per-document fetch, since `_guarded` treats any
    `ValueError` alike (module docstring)."""
    settings = _settings(tmp_path)
    router = _router()
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: INSIDE_LAG)
    source._filing_index_ran = True
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: _record(APPLE_ACCESSION, "10-K", INSIDE_LAG)})

    def _forged_collision(*args: object, **kwargs: object) -> None:
        raise ValueError(f"{APPLE_ACCESSION}: EntityCommonStockSharesOutstanding collides")

    monkeypatch.setattr(source, "_fetch_cover_page", _forged_collision)
    assert source.cover_pages(APPLE) == []
    assert source.failed_filings == 1


def test_a_404_for_the_document_is_skipped_and_recorded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    router.add("https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/missing.htm", 404)
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: INSIDE_LAG)
    source._filing_index_ran = True
    _seed_stamps(
        source,
        APPLE,
        {
            APPLE_ACCESSION: _record(
                APPLE_ACCESSION, "10-K", INSIDE_LAG, primary_document="missing.htm"
            )
        },
    )
    assert source.cover_pages(APPLE) == []
    assert source.failed_filings == 1


def test_a_500_still_fails_the_chunk(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    router = _router()
    router.add("https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/boom.htm", 500)
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: INSIDE_LAG)
    source._filing_index_ran = True
    _seed_stamps(
        source,
        APPLE,
        {
            APPLE_ACCESSION: _record(
                APPLE_ACCESSION, "10-K", INSIDE_LAG, primary_document="boom.htm"
            )
        },
    )
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        source.cover_pages(APPLE)


# --- record_failures(): Eastern-day counting, idempotency, resets ----------


def test_two_runs_one_eastern_day_advance_the_count_once(tmp_path: Path) -> None:
    _clock, clock_fn = _mutable_clock(INSIDE_LAG)
    source, _router = _garbage_source(tmp_path, clock_fn)
    source.cover_pages(APPLE)
    source.record_failures()
    source.cover_pages(APPLE)
    source.record_failures()  # a second writer, same Eastern day: idempotent
    entry = source._failure_store()[APPLE_ACCESSION]
    assert entry["count"] == 1


def test_a_different_error_message_resets_the_count(tmp_path: Path) -> None:
    """Driven directly at `_record_failure`/`record_failures()` (not through
    a real fetch): two genuinely malformed documents can easily raise the
    *same* `parse_cover_page` message (e.g. "names 0 entities"), which would
    not exercise the reset at all."""
    clock, clock_fn = _mutable_clock(datetime(2026, 6, 1, 12, tzinfo=UTC))
    source, _router = _garbage_source(tmp_path, clock_fn)
    source._record_failure(APPLE_ACCESSION, "ValueError", "10-K", "first message")
    source.record_failures()
    clock[0] = datetime(2026, 6, 2, 12, tzinfo=UTC)
    source._record_failure(APPLE_ACCESSION, "ValueError", "10-K", "first message")
    source.record_failures()
    assert source._failure_store()[APPLE_ACCESSION]["count"] == 2

    clock[0] = datetime(2026, 6, 3, 12, tzinfo=UTC)
    source._record_failure(APPLE_ACCESSION, "ValueError", "10-K", "a different message entirely")
    source.record_failures()
    assert source._failure_store()[APPLE_ACCESSION]["count"] == 1


def test_quarantine_after_max_filing_failures_consecutive_days(tmp_path: Path) -> None:
    clock, clock_fn = _mutable_clock(datetime(2026, 6, 1, 12, tzinfo=UTC))
    source, router = _garbage_source(tmp_path, clock_fn)
    max_days = source._settings.edgar.max_filing_failures
    for day in range(max_days):
        clock[0] = datetime(2026, 6, 1 + day, 12, tzinfo=UTC)
        assert source.cover_pages(APPLE) == []
        source.record_failures()
    assert source._is_quarantined(APPLE_ACCESSION)

    before = len(router.urls)
    clock[0] = datetime(2026, 6, 1 + max_days, 12, tzinfo=UTC)
    assert source.cover_pages(APPLE) == []
    assert source.quarantined == 1
    assert router.urls[before:] == []  # no request at all: quarantined


def test_a_failures_version_bump_retries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, clock_fn = _mutable_clock(datetime(2026, 6, 1, 12, tzinfo=UTC))
    source, _router = _garbage_source(tmp_path, clock_fn)
    max_days = source._settings.edgar.max_filing_failures
    for day in range(max_days):
        clock[0] = datetime(2026, 6, 1 + day, 12, tzinfo=UTC)
        source.cover_pages(APPLE)
        source.record_failures()
    assert source._is_quarantined(APPLE_ACCESSION)

    import tradepartner.adapters.edgar_source as edgar_source_module

    monkeypatch.setattr(edgar_source_module, "FAILURES_VERSION", FAILURES_VERSION + 1)
    fresh, _router2 = _garbage_source(tmp_path, lambda: clock[0])
    assert not fresh._is_quarantined(APPLE_ACCESSION)


# --- check_failures(): the three rules --------------------------------------


def _bare_source(tmp_path: Path) -> EdgarFilingSource:
    settings = _settings(tmp_path)
    source = EdgarFilingSource(settings, client=_router().client(), clock=lambda: INSIDE_LAG)
    source._filing_index_ran = True
    return source


def test_per_document_share_below_min_failed_filings_does_not_fail(tmp_path: Path) -> None:
    source = _bare_source(tmp_path)
    source._pending_failures = {
        f"000032019326-000{n:03d}": ("ValueError", "10-K", "x") for n in range(4)
    }
    source._per_document_attempted = {f"000032019326-000{n:03d}" for n in range(4)}
    source.check_failures()  # 4 < min_failed_filings (5): no raise


def test_per_document_share_at_min_and_above_max_share_fails(tmp_path: Path) -> None:
    source = _bare_source(tmp_path)
    source._pending_failures = {
        f"000032019326-000{n:03d}": ("ValueError", "10-K", "x") for n in range(5)
    }
    source._per_document_attempted = {f"000032019326-000{n:03d}" for n in range(5)}
    with pytest.raises(FilingFailuresError, match="per-document"):
        source.check_failures()


def test_thousands_of_clean_fsn_accessions_do_not_dilute_the_per_document_check(
    tmp_path: Path,
) -> None:
    """The two groups have separate denominators (module docstring):
    thousands of clean FSN accessions never enter the per-document group's
    denominator, so five failures out of five attempted still fails."""
    source = _bare_source(tmp_path)
    source._pending_failures = {
        f"000032019326-000{n:03d}": ("ValueError", "10-K", "x") for n in range(5)
    }
    source._per_document_attempted = {f"000032019326-000{n:03d}" for n in range(5)}
    source._save_fsn_manifest(
        "2025_10",
        {
            "version": (source._settings.edgar.fsn_first_year and 1) or 1,
            "period": "2025_10",
            "content_hash": "x",
            "validators": {},
            "accessions_extracted": [],
            "accessions_served": [f"clean-{n}" for n in range(5000)],
            "incomplete_listings": 0,
            "accessions_failed": [],
            "committed": False,
        },
    )
    with pytest.raises(FilingFailuresError, match="per-document"):
        source.check_failures()


def test_fsn_group_threshold(tmp_path: Path) -> None:
    source = _bare_source(tmp_path)
    manifest = {
        "version": 1,
        "period": "2025_10",
        "content_hash": "x",
        "validators": {},
        "accessions_extracted": [],
        "accessions_served": [f"served-{n}" for n in range(5)],
        "incomplete_listings": 0,
        "accessions_failed": [
            {
                "accession": f"failed-{n}",
                "error_class": "ValueError",
                "base_form": "10-K",
                "accepted": False,
            }
            for n in range(5)
        ],
        "committed": False,
    }
    source._save_fsn_manifest("2025_10", manifest)
    with pytest.raises(FilingFailuresError, match="FSN"):
        source.check_failures()


def test_a_committed_fsn_period_does_not_count_toward_the_fsn_threshold(tmp_path: Path) -> None:
    """`_check_fsn_group` (isolated from `check_failures()`'s other two
    rules, which are already covered by their own tests below) skips a
    committed period entirely."""
    source = _bare_source(tmp_path)
    manifest = {
        "version": 1,
        "period": "2025_10",
        "content_hash": "x",
        "validators": {},
        "accessions_extracted": [],
        "accessions_served": [f"served-{n}" for n in range(5)],
        "incomplete_listings": 0,
        "accessions_failed": [
            {
                "accession": f"failed-{n}",
                "error_class": "ValueError",
                "base_form": "10-K",
                "accepted": False,
            }
            for n in range(5)
        ],
        "committed": True,  # already committed: outside the FSN group's scan
    }
    source._save_fsn_manifest("2025_10", manifest)
    reasons: list[str] = []
    source._check_fsn_group(reasons)
    assert reasons == []


def test_cross_day_pair_rule_fails_and_accepted_lifts_it(tmp_path: Path) -> None:
    source = _bare_source(tmp_path)
    store = {
        f"a-{n}": {
            "error_class": "ValueError",
            "base_form": "10-K",
            "message_hash": "h",
            "count": 1,
            "last_counted_day": "2026-06-01",
            "accepted": False,
        }
        for n in range(5)
    }
    source._failed_filings_cache = store
    with pytest.raises(FilingFailuresError, match="ValueError/10-K"):
        source.check_failures()

    store["a-0"]["accepted"] = True
    source._failed_filings_cache = dict(store)
    source.check_failures()  # 4 distinct non-accepted accessions: below min_failed_filings


def test_fsn_manifest_failures_count_toward_the_cross_day_pair_rule(tmp_path: Path) -> None:
    source = _bare_source(tmp_path)
    store = {
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
    source._failed_filings_cache = store
    manifest = {
        "version": 1,
        "period": "2025_10",
        "content_hash": "x",
        "validators": {},
        "accessions_extracted": [],
        "accessions_served": [],
        "incomplete_listings": 0,
        "accessions_failed": [
            {
                "accession": f"fsn-{n}",
                "error_class": "ValueError",
                "base_form": "10-K",
                "accepted": False,
            }
            for n in range(2)
        ],
        "committed": True,
    }
    source._save_fsn_manifest("2025_10", manifest)
    with pytest.raises(FilingFailuresError, match="ValueError/10-K"):
        source.check_failures()
