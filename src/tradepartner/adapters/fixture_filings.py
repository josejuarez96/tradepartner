"""In-memory `FilingSource` for tests (spec req 6: fixture adapter).

Built from record lists instead of recorded payloads, so a test states the
exact filings a scenario needs. `known_by(t)` returns a copy holding only
the records knowable at `t` (`accepted_at <= t` or `fetched_at <= t`): a
store built from `known_by(t)` must agree with a store built from the full
source at every as-of read at `t`, which is how the master's write path is
tested for look-ahead.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
    FilingSource,
)
from tradepartner.timeutil import ensure_tz_aware_utc


class FixtureFilingSource(FilingSource):
    """A `FilingSource` over records held in memory."""

    def __init__(
        self,
        *,
        index: Iterable[FilingIndexEntry] = (),
        snapshot: Iterable[CompanySnapshotEntry] = (),
        facts: Iterable[FactRecord] = (),
        headers: Iterable[FilingHeader] = (),
        cover_pages: Iterable[CoverPage] = (),
        delistings: Iterable[DelistingFiling] = (),
    ) -> None:
        self._index = sorted(index, key=lambda e: (e.accepted_at, e.accession, e.cik))
        self._snapshot = sorted(snapshot, key=lambda e: (e.fetched_at, e.cik, e.ticker))
        self._facts = sorted(facts, key=lambda e: (e.accepted_at, e.accession, e.fact_name))
        self._headers = sorted(headers, key=lambda e: (e.accepted_at, e.accession))
        self._cover_pages = sorted(cover_pages, key=lambda e: (e.accepted_at, e.accession))
        self._delistings = sorted(delistings, key=lambda e: (e.accepted_at, e.accession))

    def known_by(self, t: datetime) -> FixtureFilingSource:
        """A copy with only the records knowable at `t`."""
        t = ensure_tz_aware_utc(t, field_name="t")
        return FixtureFilingSource(
            index=[e for e in self._index if e.accepted_at <= t],
            snapshot=[e for e in self._snapshot if e.fetched_at <= t],
            facts=[e for e in self._facts if e.accepted_at <= t],
            headers=[e for e in self._headers if e.accepted_at <= t],
            cover_pages=[e for e in self._cover_pages if e.accepted_at <= t],
            delistings=[e for e in self._delistings if e.accepted_at <= t],
        )

    def known_ats(self) -> list[datetime]:
        """Every distinct record timestamp, sorted (probe points for tests)."""
        stamps = {e.accepted_at for e in self._index}
        stamps |= {e.fetched_at for e in self._snapshot}
        stamps |= {e.accepted_at for e in self._facts}
        stamps |= {e.accepted_at for e in self._headers}
        stamps |= {e.accepted_at for e in self._cover_pages}
        stamps |= {e.accepted_at for e in self._delistings}
        return sorted(stamps)

    def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
        return [e for e in self._index if since is None or e.accepted_at >= since]

    def companies_snapshot(self) -> list[CompanySnapshotEntry]:
        return list(self._snapshot)

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        return [e for e in self._facts if e.cik == cik and e.fact_name in names]

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        return [e for e in self._headers if e.cik == cik and e.form in forms]

    def cover_pages(self, cik: str) -> list[CoverPage]:
        return [e for e in self._cover_pages if e.cik == cik]

    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        return [e for e in self._delistings if since is None or e.accepted_at >= since]
