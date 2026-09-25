"""Truncation invariance for `universe_as_of` (spec "Look-ahead"; plan T13).

`universe_as_of(T)` on the full fixture store equals `universe_as_of(T)` on
the store truncated to `known_at <= T`, at a probe just before and just
after every distinct `known_at` in the fixture. Members and exclusions are
compared whole, so a rule that read a row from after T (a later split, a
restated fact, a delisting filed later) shows up as a difference.
`top_n_by_cap=1` so the rule-8 cut runs at every probe (the fixture has
far fewer than 1,000 companies).
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.config import Settings
from tradepartner.universe import universe_as_of


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_universe_as_of_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    settings = Settings(_env_file=None, universe={"top_n_by_cap": 1})
    members_seen = 0
    for t in probe_timestamps(fixture_store):
        full = universe_as_of(fixture_store, t, settings)
        cut = universe_as_of(truncated.at(t), t, settings)
        assert full.members.equals(cut.members), f"members disagree at T={t!r}"
        assert full.exclusions.equals(cut.exclusions), f"exclusions disagree at T={t!r}"
        members_seen += full.members.height
    # Not vacuous: the universe is non-empty at many probes.
    assert members_seen > 0
