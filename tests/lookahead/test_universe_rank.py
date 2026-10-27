"""Look-ahead cases for `universe.rank_by = dollar_volume` (ADR 0023 D4; backtest spec
amendment 2026-10-18; data-foundation plan T212).

Everything the rank and the intersection read must be known at `t`:

- Truncation invariance: `universe_as_of(T)` on the full fixture store equals it on the
  store truncated to `known_at <= T` (bars, listings and the assets snapshot included), at
  a probe just before and just after every distinct `known_at`, members, exclusions and the
  intersection's record compared whole.
- A bar revision known after `t` (a volume that would put a name first) does not change
  membership or rank at `t`, and does from its `known_at`: the case is not vacuous.
- An assets snapshot known after `t` (a name turning untradable) leaves membership at `t`
  alone, and removes the name from its `known_at`.
- Before the first snapshot the intersection is skipped (never today's list read
  backwards), and the skip is recorded.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
from conftest import PROBE_CHUNKS, assert_chunks_are_the_probe_set, probe_chunk

from lookahead.harness import PROBE_EPSILON, TruncatedStore, probe_timestamps
from tradepartner.calendar import last_completed_session, session_close
from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import Universe, universe_as_of

_SETTINGS = Settings(_env_file=None, universe={"rank_by": "dollar_volume", "top_n_by_cap": 3})  # type: ignore[call-arg]
_ALL = Settings(_env_file=None, universe={"rank_by": "dollar_volume"})  # type: ignore[call-arg]

#: Inside the fixture's history for the names below, before and after its first snapshot
#: (fetched 2020-06-26 21:00 UTC).
_T_BEFORE = datetime(2020, 6, 25, 21, 0, tzinfo=UTC)
_T_AFTER = datetime(2020, 6, 30, 21, 0, tzinfo=UTC)


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def _ids(u: Universe) -> list[str]:
    return u.members.sort("company_rank", "security_id")["security_id"].to_list()


#: `probe_timestamps` on the fixture store, pinned: the nine chunks cover exactly these.
PROBE_COUNT = 1942
PROBE_DIGEST = "f8ccd42c8faaea4b04835e4161798c90701298e1a924f450b147b3058563ccef"


def test_dollar_volume_chunks_are_the_pinned_probe_set(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """No probe dropped or repeated: the chunks' union is the original probe set."""
    assert_chunks_are_the_probe_set(probe_timestamps(fixture_store), PROBE_COUNT, PROBE_DIGEST)


@pytest.mark.parametrize("chunk", range(PROBE_CHUNKS))
def test_dollar_volume_universe_is_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore, chunk: int
) -> None:
    """One strided ninth of the pinned full probe set, with the original comparisons."""
    seen_members = 0
    skipped: set[bool] = set()
    for t in probe_chunk(probe_timestamps(fixture_store), chunk):
        full = universe_as_of(fixture_store, t, _SETTINGS)
        cut = universe_as_of(truncated.at(t), t, _SETTINGS)
        assert full.members.equals(cut.members), f"members disagree at T={t!r}"
        assert full.exclusions.equals(cut.exclusions), f"exclusions disagree at T={t!r}"
        assert (full.n_assets_intersected, full.assets_intersection_skipped) == (
            cut.n_assets_intersected,
            cut.assets_intersection_skipped,
        ), f"intersection disagrees at T={t!r}"
        seen_members += full.members.height
        skipped.add(full.assets_intersection_skipped)
    # Not vacuous: members at many probes, and probes on both sides of the first snapshot.
    assert seen_members > 0
    assert skipped == {True, False}


def _revise_volume(conn: duckdb.DuckDBPyConnection, security_id: str, known_at: datetime) -> None:
    """A re-fetched bar for every stored session of `security_id`, volume 1000x, known at
    `known_at` (a vendor revision)."""
    rows = conn.execute(
        "SELECT * FROM prices_daily WHERE security_id = ?", [security_id]
    ).fetchall()
    cols = [d[0] for d in conn.description]
    for row in rows:
        bar = dict(zip(cols, row, strict=True))
        bar["volume"] = bar["volume"] * 1000
        bar["known_at"] = known_at
        bar["ingested_at"] = known_at
        insert_row(conn, "prices_daily", bar)


def test_a_volume_revision_known_after_t_does_not_change_membership_at_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    t = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)
    revised_at = datetime(2019, 7, 1, 12, 0, tzinfo=UTC)
    baseline = universe_as_of(fixture_store, t, _ALL)
    last = _ids(baseline)[-1]
    assert last == "SEC_SPLIT_BETWEEN"
    _revise_volume(fixture_store, last, revised_at)
    # At `t`, and a microsecond before the revision, nothing moved.
    for probe in (t, revised_at - PROBE_EPSILON):
        again = universe_as_of(fixture_store, probe, _ALL)
        assert again.members.equals(baseline.members)
    # From its `known_at` the revised volume ranks (the case is not vacuous): the revision
    # carries sessions up to the store's end, so read it at a session it covers.
    t_after = session_close(last_completed_session(revised_at + timedelta(days=3)))
    assert _ids(universe_as_of(fixture_store, t_after, _ALL))[0] == last


def test_an_assets_revision_known_after_t_does_not_change_membership_at_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    before = universe_as_of(fixture_store, _T_AFTER, _ALL)
    victim = before.members["ticker"].to_list()[0]
    revised_at = datetime(2020, 6, 30, 22, 0, tzinfo=UTC)  # after the fixture's last close
    row = fixture_store.execute(
        "SELECT * FROM borrow_status WHERE symbol = ? ORDER BY known_at DESC LIMIT 1", [victim]
    ).fetchone()
    cols = [d[0] for d in fixture_store.description]
    assert row is not None
    flipped = dict(zip(cols, row, strict=True))
    flipped.update(tradable=False, known_at=revised_at, ingested_at=revised_at)
    insert_row(fixture_store, "borrow_status", flipped)
    just_before = universe_as_of(fixture_store, revised_at - PROBE_EPSILON, _ALL)
    assert victim in just_before.members["ticker"].to_list()
    assert just_before.members.equals(before.members)
    at_after = universe_as_of(fixture_store, revised_at + PROBE_EPSILON, _ALL)
    assert victim not in at_after.members["ticker"].to_list()


def test_before_the_first_snapshot_nothing_is_intersected_and_the_skip_is_recorded(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    u = universe_as_of(fixture_store, _T_BEFORE, _ALL)
    assert u.assets_intersection_skipped and u.n_assets_intersected == 0
    after = universe_as_of(fixture_store, _T_AFTER, _ALL)
    assert not after.assets_intersection_skipped
    # The later (smaller) tradable list never shrinks the earlier universe.
    assert u.members.height > after.members.height
    assert set(after.members["security_id"]) <= set(u.members["security_id"])
