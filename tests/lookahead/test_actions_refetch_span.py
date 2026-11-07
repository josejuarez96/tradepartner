"""#3074: a dividend the span refetch recovers years after its ex-date is
stamped by the first-seen proxy of its ex-date, so an as-of read is the same
as if the backfill had stored it: invisible before the close of the session
before its ex-date, visible from it; never visible earlier because the
refetch ran late, and never stamped at the source's (late) process date."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb
import pytest
from test_backfill import SINCE
from test_backfill_refetch_actions import EX, _committed, _open_release, _refetch
from test_ingest import ACME

from tradepartner.adapters.prices import action_first_seen_known_at
from tradepartner.config import Settings
from tradepartner.store.asof import live_actions_as_of


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def test_a_span_recovered_dividend_is_known_only_from_its_first_seen_proxy(
    settings: Settings,
) -> None:
    prices = _committed(settings)
    _open_release(settings)
    result = _refetch(
        settings, prices, since=SINCE, through=EX + timedelta(days=30), span=True, securities=[ACME]
    )
    assert result.ok and result.added == (("dividend", 1),)
    known = action_first_seen_known_at(EX)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        before = live_actions_as_of(conn, known - timedelta(microseconds=1), [ACME])
        at = live_actions_as_of(conn, known, [ACME])
    assert before.height == 0
    assert at["ex_date"].to_list() == [EX]
