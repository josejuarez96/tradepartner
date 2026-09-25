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
    settings = Settings(_env_file=None)
    missing_seen = 0
    for t in probe_timestamps(fixture_store):
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
        missing_seen += full.missing.height
    # Not vacuous: some probe finds a missing name.
    assert missing_seen > 0
