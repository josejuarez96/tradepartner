"""The T11h failure policy: skip-never-raise, quarantine, `record_failures()`
and `check_failures()` (plan T11h, split from T11f by #261, spike notes on
#224).

Reuses `test_edgar_source_cik.py`'s FSN router/settings/seeded-stamps
shortcut (`_router`, `_settings`, `_source`, `_seed_stamps`, `_record`) so
`cover_pages`/`filing_headers` exercise the real per-document fetch/parse
path, and `test_edgar_fsn.py`'s zip-building helpers to drive `_ensure_fsn`
for real over a synthetic FSN period with an accession whose rows fail to
parse.

`check_failures()`'s threshold rules are exercised two ways: by setting the
adapter's internal state directly (`_pending_failures`,
`_per_document_attempted`, `_failed_filings_cache`, FSN manifests) for the
pure-arithmetic cases, already covered end-to-end by the skip/quarantine
tests above them, and by driving the cross-day pair rule through several
simulated days of real `record_failures()` calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from edgar_transport import edgar_settings, index_header
from test_edgar_fsn import (
    _fsn_zip_bytes,
    _fsn_zip_url,
    _num,
    _router_with_fsn,
    _source_ready,
    _sub,
)
from test_edgar_source_cik import APPLE, INSIDE_LAG, _record, _router, _seed_stamps, _settings

from tradepartner.adapters.edgar_source import (
    FAILURES_VERSION,
    EdgarFilingSource,
    FilingFailuresError,
)
from tradepartner.adapters.prices import PriceSource
from tradepartner.config import Settings
from tradepartner.ingest import FAILED, ingest_session

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tradepartner.adapters.prices import Bar, CorporateAction

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
    """A cover-page collision is forged by stubbing the per-document fetch,
    since `_guarded` treats any `ValueError` alike; `facts()`'s own
    key-level collision handling has its dedicated tests in
    `test_edgar_source_cik.py`."""
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


def test_a_commit_after_eastern_midnight_counts_on_the_commit_day(tmp_path: Path) -> None:
    """The counted day is read from the clock when `record_failures()` runs
    (commit time), not when the failure happened."""
    clock, clock_fn = _mutable_clock(datetime(2026, 6, 1, 23, 30, tzinfo=UTC))  # 19:30 Eastern
    source, _router = _garbage_source(tmp_path, clock_fn)
    source.cover_pages(APPLE)
    clock[0] = datetime(2026, 6, 2, 4, 30, tzinfo=UTC)  # past Eastern midnight: 2026-06-02
    source.record_failures()
    assert source._failure_store()[APPLE_ACCESSION]["last_counted_day"] == "2026-06-02"


def test_a_dry_run_and_a_failed_chunk_advance_nothing(tmp_path: Path) -> None:
    """`record_failures` is only ever called by `_run_source`'s `after_commit`
    hook for a committed `ok`, non-dry run (`ingest.py`); called directly it
    always advances, so "a dry run advances nothing" means `after_commit`
    (and so `record_failures`) is simply never invoked in those cases -- the
    adapter's own state is untouched."""
    _clock, clock_fn = _mutable_clock(INSIDE_LAG)
    source, _router = _garbage_source(tmp_path, clock_fn)
    source.cover_pages(APPLE)
    assert source._pending_failures  # the failure happened
    assert source._failure_store() == {}  # but nothing is written until record_failures()


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


# --- FSN extraction failures: counted, never quarantined, sticky -----------


def _fsn_settings(tmp_path: Path, **edgar: object) -> Settings:
    return edgar_settings(tmp_path, fsn_first_year=2015, min_failed_filings=1, **edgar)


def _one_bad_period_zip(accession: str, cik: str) -> bytes:
    """A single 10-K accession whose share fact does not parse (T11c's
    `parse_fsn` fails closed on a non-numeric value)."""
    return _fsn_zip_bytes(
        [_sub(accession, cik, "10-K")],
        [_num(accession, "EntityCommonStockSharesOutstanding", "not-a-number", "20150131")],
        [],
        [],
    )


def test_an_fsn_extraction_failure_fails_the_chunk_again_next_run_with_no_download(
    tmp_path: Path,
) -> None:
    """T11c's zip is deleted after extraction and the period's manifest, once
    it exists, is never re-extracted: a period that failed `check_failures()`
    must fail it again on the next run until the parser is fixed or its
    failures are `accepted`, without a second download."""
    accession, cik = "0000000041-15-000001", "0000000041"
    settings = _fsn_settings(tmp_path)
    zip_bytes = _one_bad_period_zip(accession, cik)
    methods: list[str] = []

    def zip_route(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(200, content=zip_bytes, headers={"ETag": '"v1"'})

    router = _router_with_fsn("2015q1")
    router.add(_fsn_zip_url("2015q1"), zip_route)
    source = _source_ready(settings, router)
    source._ensure_fsn()
    assert methods == ["GET"]
    with pytest.raises(FilingFailuresError, match="FSN"):
        source.check_failures()

    # a fresh instance, same on-disk store: the manifest already exists, so
    # no second GET (only a HEAD, to check for a re-issue), and it fails again.
    source2 = _source_ready(settings, router)
    source2._ensure_fsn()
    assert methods == ["GET", "HEAD"]
    with pytest.raises(FilingFailuresError, match="FSN"):
        source2.check_failures()


def test_fsn_extraction_failures_are_never_quarantined(tmp_path: Path) -> None:
    """Plan T11h: an FSN accession whose rows failed extraction is "never
    quarantined and retried only when FSN_VERSION changes" -- unlike a
    per-document failure, it never gains a `failed_filings.json` entry or a
    consecutive-day count at all, over as many simulated days as it fails."""
    accession, cik = "0000000042-15-000001", "0000000042"
    settings = _fsn_settings(tmp_path)
    router = _router_with_fsn("2015q1", zips={"2015q1": _one_bad_period_zip(accession, cik)})
    for _day in range(5):
        # a fresh instance each simulated day, same on-disk store, never
        # committed: `record_failures()` is never called for this source, so
        # `check_failures()` keeps failing on the FSN group every day.
        source = _source_ready(settings, router)
        source._ensure_fsn()
        assert source._is_quarantined(accession) is False
        assert source.quarantined == 0
        with pytest.raises(FilingFailuresError, match="FSN"):
            source.check_failures()
    # still failing, still never quarantined, on the sixth day
    final = _source_ready(settings, router)
    final._ensure_fsn()
    assert final._is_quarantined(accession) is False


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


def test_an_accepted_pending_failure_is_excluded_from_the_per_document_share(
    tmp_path: Path,
) -> None:
    """`accepted: true` entries leave the numerator (owner ruling 2) but stay
    in the denominator: 6 attempted, 1 accepted -> 5 non-accepted still
    fails; accepting a second drops the numerator to 4 (below
    `min_failed_filings`) while the denominator, still 6, is untouched."""
    source = _bare_source(tmp_path)
    accessions = [f"000032019326-000{n:03d}" for n in range(6)]
    source._pending_failures = {a: ("ValueError", "10-K", "x") for a in accessions}
    source._per_document_attempted = set(accessions)
    source._failed_filings_cache = {
        a: {
            "error_class": "ValueError",
            "base_form": "10-K",
            "message_hash": "h",
            "count": 1,
            "last_counted_day": "2026-06-01",
            "accepted": a == accessions[0],
        }
        for a in accessions
    }
    with pytest.raises(FilingFailuresError, match="per-document"):
        source.check_failures()
    source._failed_filings_cache = {
        a: {**source._failed_filings_cache[a], "accepted": True} for a in accessions[:2]
    }
    source.check_failures()  # 4 non-accepted of 6 attempted: below min_failed_filings (5)


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
            "version": 1,
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


def test_cross_day_pair_rule_driven_through_several_simulated_days(tmp_path: Path) -> None:
    """Rather than building `_failed_filings_cache` by hand: five different
    accessions of the same (error class, base form) pair, each failing
    identically on its own single simulated day (never reaching this
    accession's own quarantine, which needs `max_filing_failures` consecutive
    days of the *same* accession), still trip the cross-day rule once the
    fifth is recorded."""
    settings = _settings(tmp_path)
    clock, clock_fn = _mutable_clock(datetime(2026, 6, 1, 12, tzinfo=UTC))
    router = _router()
    for n, filename in enumerate(f"garbage-{i}.htm" for i in range(5)):
        router.add(
            f"https://www.sec.gov/Archives/edgar/data/320193/00003201932600010{n}/{filename}",
            b"<html>not a cover page</html>",
        )
    source = EdgarFilingSource(settings, client=router.client(), clock=clock_fn)
    source._filing_index_ran = True

    for n, filename in enumerate(f"garbage-{i}.htm" for i in range(5)):
        accession = f"0000320193-26-0001{n:02d}"
        clock[0] = datetime(2026, 6, 1 + n, 12, tzinfo=UTC)  # a new Eastern day each time
        _seed_stamps(
            source,
            APPLE,
            {accession: _record(accession, "10-K", clock[0], primary_document=filename)},
        )
        source.cover_pages(APPLE)
        if n < 4:
            source.record_failures()  # commits this run's failure; check_failures not yet at 5
            source.check_failures()  # still below min_failed_filings (5): no raise
        else:
            with pytest.raises(FilingFailuresError, match="ValueError/10-K"):
                source.check_failures()


# --- end to end: ingest_session halts when check_failures raises -----------


@dataclass
class _NoPrices(PriceSource):
    """A `PriceSource` never actually called: `source="edgar"` in
    `ingest_session` only runs the edgar chunk."""

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return []

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        return []


def test_ingest_session_halts_when_check_failures_raises(tmp_path: Path) -> None:
    """A real `EdgarFilingSource` over an empty universe (no issuer CIKs, so
    no FSN/company-facts calls at all) with five pre-recorded failures of one
    (error class, base form) pair on disk: `_prefetch`'s `check_failures()`
    call raises before the store is ever opened for write, and the chunk's
    only trace is a `failed` run row."""
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    settings = edgar_settings(tmp_path / "edgar", index_first_year=2026, fsn_first_year=2026)
    router = _router()
    router.add_index(2026, 1, index_header())
    router.add_index(2026, 2, index_header())
    Path(settings.edgar.cache_dir).mkdir(parents=True, exist_ok=True)
    entries = {
        f"acc-{n}": {
            "error_class": "ValueError",
            "base_form": "10-K",
            "message_hash": "h",
            "count": 1,
            "last_counted_day": "2026-05-01",
            "accepted": False,
        }
        for n in range(5)
    }
    (Path(settings.edgar.cache_dir) / "failed_filings.json").write_text(
        json.dumps({"version": FAILURES_VERSION, "entries": entries})
    )
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: now)

    store_settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        edgar=settings.edgar.model_dump(),
        sec_edgar_user_agent=settings.sec_edgar_user_agent,
    )
    result = ingest_session(
        store_settings,
        prices=_NoPrices(),
        filings=source,
        source="edgar",
        clock=lambda: now,
    )
    [run] = result.runs
    assert run.status == FAILED
    assert "ValueError/10-K" in run.message
    assert run.rows_added == 0
    # nothing but the failed run row itself: check_failures() raised in
    # _prefetch, before the write transaction that would hold master rows.
    import duckdb

    with duckdb.connect(store_settings.store.path, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM securities").fetchone() == (0,)
        rows = conn.execute("SELECT status FROM ingestion_runs").fetchall()
        assert rows == [(FAILED,)]


# --- safety-reviewer fixes (#275) --------------------------------------------


@pytest.mark.parametrize("content", ["{truncated", "[]", '{"version": 1, "entries": []}'])
def test_a_corrupt_failed_filings_file_fails_loudly_and_is_not_overwritten(
    tmp_path: Path, content: str
) -> None:
    """The owner edits this file by hand: a typo must never silently lift every
    quarantine and drop every `accepted` flag, nor be overwritten."""
    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG)
    path = Path(source._settings.edgar.cache_dir) / "failed_filings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    with pytest.raises(ValueError, match=r"failed_filings\.json"):
        source.cover_pages(APPLE)
    assert path.read_text() == content


def test_another_failures_version_starts_fresh(tmp_path: Path) -> None:
    """A valid file with another `version` is the documented way to retry
    everything: it reads as empty, not as an error."""
    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG)
    path = Path(source._settings.edgar.cache_dir) / "failed_filings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": FAILURES_VERSION + 1, "entries": {}}))
    assert source.cover_pages(APPLE) == []
    assert source.failed_filings == 1


def test_a_hand_edit_during_the_run_survives_record_failures(tmp_path: Path) -> None:
    """`record_failures()` re-reads the file before writing, so an entry the
    owner marks `accepted` (or deletes) while a run is in flight is kept."""
    other = "0000320193-26-000200"
    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG)
    path = Path(source._settings.edgar.cache_dir) / "failed_filings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "error_class": "ValueError",
        "base_form": "10-K",
        "message_hash": "x",
        "count": 2,
        "last_counted_day": "2026-05-01",
        "accepted": False,
    }
    path.write_text(json.dumps({"version": FAILURES_VERSION, "entries": {other: entry}}))
    source.cover_pages(APPLE)  # loads the file, records this run's failure

    # The owner accepts `other` by hand while the run is in flight.
    path.write_text(
        json.dumps({"version": FAILURES_VERSION, "entries": {other: {**entry, "accepted": True}}})
    )
    source.record_failures()
    entries = json.loads(path.read_text())["entries"]
    assert entries[other]["accepted"] is True
    assert APPLE_ACCESSION in entries


def test_an_unsafe_primary_document_propagates_never_a_quiet_skip(tmp_path: Path) -> None:
    """The path-safety guard (`InvalidFilingReferenceError`) is not a filing
    failure: a traversal attempt must fail the chunk, not be quarantined."""
    from tradepartner.adapters.edgar_raw import InvalidFilingReferenceError

    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG, filename="../escape.htm")
    with pytest.raises(InvalidFilingReferenceError):
        source.cover_pages(APPLE)
    assert source.failed_filings == 0


# --- quant-auditor fixes (#275) ----------------------------------------------


def _write_store(source: EdgarFilingSource, entries: dict[str, dict[str, object]]) -> Path:
    path = Path(source._settings.edgar.cache_dir) / "failed_filings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": FAILURES_VERSION, "entries": entries}))
    source._failed_filings_cache = None
    return path


def _entry(count: int, *, kind: str = "fetch", accepted: bool = False) -> dict[str, object]:
    return {
        "error_class": "ValueError",
        "base_form": "10-K",
        "message_hash": "h1",
        "count": count,
        "last_counted_day": "2026-05-01",
        "accepted": accepted,
        "kind": kind,
    }


def test_a_collision_never_quarantines_its_accession(tmp_path: Path) -> None:
    """Collisions are re-detected from cached data every run; they count toward
    the (error class, base form) rule only, never toward quarantine, so a
    parser fix and version bump can still re-parse the accession."""
    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG)
    _write_store(source, {"A": _entry(9, kind="collision"), "B": _entry(9)})
    assert not source._is_quarantined("A")
    assert source._is_quarantined("B")


def test_a_recorded_collision_is_stored_as_a_collision(tmp_path: Path) -> None:
    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG)
    source._record_failure(APPLE_ACCESSION, "ValueError", "10-K", "collides", collision=True)
    source.record_failures()
    path = Path(source._settings.edgar.cache_dir) / "failed_filings.json"
    assert json.loads(path.read_text())["entries"][APPLE_ACCESSION]["kind"] == "collision"


def test_a_changed_error_clears_accepted(tmp_path: Path) -> None:
    """`accepted` was given for one error; a different one must be reviewed
    again, never hidden behind the old acceptance."""
    source, _router = _garbage_source(tmp_path, lambda: INSIDE_LAG)
    path = _write_store(source, {APPLE_ACCESSION: _entry(2, accepted=True)})
    source._record_failure(APPLE_ACCESSION, "ValueError", "10-K", "a different message")
    source.record_failures()
    entry = json.loads(path.read_text())["entries"][APPLE_ACCESSION]
    assert (entry["accepted"], entry["count"]) == (False, 1)


def test_an_accession_that_now_succeeds_is_pruned(tmp_path: Path) -> None:
    """After a parser fix, a formerly failing accession that parses this run
    leaves the store, so stale entries stop failing every nightly chunk."""
    from test_edgar_source_cik import _COVER_DOCUMENT

    settings = _settings(tmp_path)
    router = _router()
    router.add(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/fixed.htm",
        _COVER_DOCUMENT,
    )
    source = EdgarFilingSource(settings, client=router.client(), clock=lambda: INSIDE_LAG)
    source._filing_index_ran = True
    record = _record(APPLE_ACCESSION, "10-K", INSIDE_LAG, primary_document="fixed.htm")
    _seed_stamps(source, APPLE, {APPLE_ACCESSION: record})
    path = _write_store(source, {APPLE_ACCESSION: _entry(2), "OTHER": _entry(2)})
    assert [p.accession for p in source.cover_pages(APPLE)] == [APPLE_ACCESSION]
    source.record_failures()
    entries = json.loads(path.read_text())["entries"]
    assert APPLE_ACCESSION not in entries and "OTHER" in entries
