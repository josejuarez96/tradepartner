"""Truncation invariance for `survivorship_gap` (spec "Look-ahead"; plan T15).

`survivorship_gap(T)` on the full fixture store equals `survivorship_gap(T)`
on the store truncated to `known_at <= T`, at a probe just before and just
after every distinct `known_at` in the fixture. L, M (with its evidence),
both shares and the side categories are compared whole, so a Form 25, a
bar or a shares fact read from after T shows up as a difference.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import duckdb
import pytest
from conftest import (
    PROBE_CHUNKS,
    assert_chunks_are_the_probe_set,
    failing_chunks,
    probe_chunk_indices,
)

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


def _assert_gap_invariant_at(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore, i: int, t: datetime
) -> tuple[int, int]:
    """One probe `t`, the `i`th of the whole probe list (it picks the stale key); returns the
    (missing, stale) row counts it saw."""
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
    return full.missing.height, full.stale_listings.height


#: `probe_timestamps` on the fixture store, pinned (T216): the chunks walk exactly these.
PROBE_COUNT = 1942
PROBE_DIGEST = "f8ccd42c8faaea4b04835e4161798c90701298e1a924f450b147b3058563ccef"


def test_gap_chunks_are_the_pinned_probe_set(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """No probe dropped: the chunks' union is today's probe set, by count and hash."""
    assert_chunks_are_the_probe_set(probe_timestamps(fixture_store), PROBE_COUNT, PROBE_DIGEST)


def test_a_planted_lookahead_fails_exactly_one_gap_chunk(
    fixture_store: duckdb.DuckDBPyConnection,
    truncated: TruncatedStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `survivorship_gap` that reads 400 days past T on the full store at one probe
    fails the chunk holding that probe and no other."""
    probes = probe_timestamps(fixture_store)
    planted = probes[900]
    real = survivorship_gap

    def leaky(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> Any:
        read_at = t + timedelta(days=400) if conn is fixture_store and t == planted else t
        return real(conn, read_at, settings)

    monkeypatch.setitem(globals(), "survivorship_gap", leaky)
    window = probes[880:920]
    failed = failing_chunks(
        window,
        lambda t: _assert_gap_invariant_at(fixture_store, truncated, probes.index(t), t),
    )
    assert failed == [window.index(planted) % PROBE_CHUNKS]


@pytest.mark.parametrize("chunk", range(PROBE_CHUNKS))
def test_survivorship_gap_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore, chunk: int
) -> None:
    """One strided chunk of the probes (see `conftest.PROBE_CHUNKS`); the stale key still
    alternates by the probe's position in the whole list, as it did in one loop."""
    probes = probe_timestamps(fixture_store)
    missing_seen = stale_seen = 0
    for i in probe_chunk_indices(len(probes), chunk):
        missing, stale = _assert_gap_invariant_at(fixture_store, truncated, i, probes[i])
        missing_seen += missing
        stale_seen += stale
    # Not vacuous: some probe of the chunk finds a missing name, and some a stale one.
    assert missing_seen > 0
    assert stale_seen > 0
