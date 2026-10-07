"""Truncation invariance for `survivorship_gap` (spec "Look-ahead"; plan T15).

`survivorship_gap(T)` on the full fixture store equals `survivorship_gap(T)`
on the store truncated to `known_at <= T`, at a probe just before and just
after every distinct `known_at` in the fixture. L, M (with its evidence),
both shares and the side categories are compared whole, so a Form 25, a
bar or a shares fact read from after T shows up as a difference.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.config import Settings
from tradepartner.gap import survivorship_gap

#: The fixture sees no name dark 63 sessions; 0 makes the stale-listing rule
#: (#1199) fire wherever a live name's last bar is before W. The probes take the
#: two keys in turn, so the test costs what it did before the rule.
STALE_KEYS = (63, 0)


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_survivorship_gap_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    missing_seen = stale_seen = 0
    for i, t in enumerate(probe_timestamps(fixture_store)):
        stale_listing_sessions = STALE_KEYS[i % len(STALE_KEYS)]
        settings = Settings(_env_file=None, gap={"stale_listing_sessions": stale_listing_sessions})
        full = survivorship_gap(fixture_store, t, settings)
        cut = survivorship_gap(truncated.at(t), t, settings)
        assert full.listed == cut.listed, f"L disagrees at T={t!r}"
        assert full.missing.equals(cut.missing), f"M disagrees at T={t!r}"
        assert (full.count_share, full.size_share) == (cut.count_share, cut.size_share)
        assert (full.unclassifiable, full.truncated_history, full.stale_shares) == (
            cut.unclassifiable,
            cut.truncated_history,
            cut.stale_shares,
        ), f"side categories disagree at T={t!r}"
        assert full.stale_listings.equals(cut.stale_listings), f"stale disagrees at T={t!r}"
        missing_seen += full.missing.height
        stale_seen += full.stale_listings.height
    # Not vacuous: some probe finds a missing name, and some a stale one.
    assert missing_seen > 0
    assert stale_seen > 0
