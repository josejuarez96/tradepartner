"""A hole fill's cost per month follows what it writes (#3234).

The 11-09 rehearsal measured `--fill-holes` at ~75 s per month of the
`--since` window whatever it wrote: every month rebuilt the store's
`ListingResolver`, whose symbol-change check (`ListingResolver._takes`)
walked each day to the run's day with `next_session` (~1M calendar calls).
A month with no hole now reuses the resolver read for the month before it
when nothing the resolver reads can have changed, the walk is one slice of
the session list, and each month prints one progress line.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from test_backfill import JUNE_WINDOW, LATER, MAY_WINDOW, SINCE, _backfill, _History, _ticking

from tradepartner import backfill as backfill_module
from tradepartner.adapters.alpaca_prices import ListingResolver
from tradepartner.backfill import FILLED, _Resolvers, fill_holes
from tradepartner.config import Settings
from tradepartner.ingest import _read
from tradepartner.repair import store_resolver


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _drop_bars(settings: Settings, sid: str, window: tuple[date, date]) -> None:
    with duckdb.connect(settings.store.path) as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session BETWEEN ? AND ?",
            [sid, *window],
        )


@pytest.fixture
def builds() -> Iterator[list[Any]]:
    """Every `store_resolver` build the fill makes, by its `at`."""
    seen: list[Any] = []

    def counting(conn: duckdb.DuckDBPyConnection, at: Any, s: Settings) -> ListingResolver:
        seen.append(at)
        return store_resolver(conn, at, s)

    with patch.object(backfill_module, "store_resolver", counting):
        yield seen


def _rows(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT security_id, session, close, volume, known_at FROM prices_daily ORDER BY ALL"
        ).fetchall()


def test_months_with_no_hole_reuse_one_resolver(settings: Settings, builds: list[Any]) -> None:
    from test_ingest import DUAL

    assert _backfill(settings, _History()).ok
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    builds.clear()
    result = fill_holes(
        settings, prices=_History(), since=SINCE, clock=_ticking(LATER, timedelta(minutes=1))
    )
    assert [r.status for r in result.runs] == [FILLED]
    # April and May have no hole and write nothing: one build serves all three.
    assert len(builds) == 1


def test_a_month_that_wrote_makes_the_next_month_read_the_store_again(
    settings: Settings, builds: list[Any]
) -> None:
    from test_ingest import ACME, DUAL

    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    builds.clear()
    result = fill_holes(
        settings, prices=_History(), since=SINCE, clock=_ticking(LATER, timedelta(minutes=1))
    )
    assert [r.status for r in result.runs] == [FILLED, FILLED]
    # April's build serves May; May's write (bars the resolver reads) forces June's.
    assert len(builds) == 2


def test_reuse_writes_the_same_rows_as_a_build_every_month(
    settings: Settings, tmp_path: Path
) -> None:
    from test_ingest import ACME, DUAL

    assert _backfill(settings, _History()).ok
    _drop_bars(settings, ACME, MAY_WINDOW)
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    other = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "copy.duckdb"), "lock_retry_seconds": 1},
    )
    Path(other.store.path).write_bytes(Path(settings.store.path).read_bytes())
    fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER)
    with patch.object(_Resolvers, "_reusable", lambda self, conn, at: False):
        fill_holes(other, prices=_History(), since=SINCE, clock=lambda: LATER)
    assert _rows(settings) == _rows(other)


def test_one_progress_line_per_month(settings: Settings) -> None:
    from test_ingest import DUAL

    assert _backfill(settings, _History()).ok
    _drop_bars(settings, DUAL, JUNE_WINDOW)
    lines: list[str] = []
    fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, progress=lines.append)
    assert len(lines) == 3
    assert lines[0].startswith("alpaca holes 2019-04 (1/3): no hole")
    assert lines[2].startswith("alpaca holes 2019-06 (3/3): filled, ")


def test_a_dry_run_prints_no_progress(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    lines: list[str] = []
    fill_holes(
        settings,
        prices=_History(),
        since=SINCE,
        clock=lambda: LATER,
        dry_run=True,
        progress=lines.append,
    )
    assert lines == []


# --- the reuse rule, point in time -----------------------------------------


def _resolver_at(settings: Settings, cache: _Resolvers, at: Any) -> ListingResolver:
    with _read(settings) as conn:
        return cache.get(conn, at)


def test_the_same_store_and_day_reuse_the_resolver(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    cache = _Resolvers(settings)
    first = _resolver_at(settings, cache, LATER)
    assert _resolver_at(settings, cache, LATER + timedelta(minutes=5)) is first
    assert cache.builds == 1


def test_a_row_known_between_two_reads_is_seen_by_the_later_one(settings: Settings) -> None:
    # No look-ahead and no stale read: a row already stored but known only after
    # the first read (a writer whose clock ran ahead) is invisible to it and
    # must be in the second.
    assert _backfill(settings, _History()).ok
    with duckdb.connect(settings.store.path) as conn:
        conn.execute(
            "INSERT INTO listings SELECT * REPLACE "
            "(CAST(? AS TIMESTAMPTZ) AS known_at, CAST(? AS TIMESTAMPTZ) AS ingested_at) "
            "FROM listings LIMIT 1",
            [LATER + timedelta(minutes=2)] * 2,
        )
    cache = _Resolvers(settings)
    first = _resolver_at(settings, cache, LATER)
    again = _resolver_at(settings, cache, LATER + timedelta(minutes=1))
    assert again is first  # the row is not known yet
    later = _resolver_at(settings, cache, LATER + timedelta(minutes=3))
    assert later is not first and cache.builds == 2


def test_a_write_after_the_read_makes_the_store_read_again(settings: Settings) -> None:
    # A row known long ago but written after the read (another process, a
    # late restatement): the store file changed, so the resolver is rebuilt.
    assert _backfill(settings, _History()).ok
    cache = _Resolvers(settings)
    first = _resolver_at(settings, cache, LATER)
    with duckdb.connect(settings.store.path) as conn:
        conn.execute(
            "INSERT INTO listings SELECT * REPLACE (CAST(? AS TIMESTAMPTZ) AS known_at) "
            "FROM listings LIMIT 1",
            [LATER - timedelta(days=30)],
        )
    assert _resolver_at(settings, cache, LATER + timedelta(minutes=1)) is not first


def test_a_new_day_reads_the_store_again(settings: Settings) -> None:
    # The resolver's evidence and run day are the read's day.
    assert _backfill(settings, _History()).ok
    cache = _Resolvers(settings)
    first = _resolver_at(settings, cache, LATER)
    assert _resolver_at(settings, cache, LATER + timedelta(days=1)) is not first


def test_drop_forces_a_build(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    cache = _Resolvers(settings)
    first = _resolver_at(settings, cache, LATER)
    cache.drop()
    assert _resolver_at(settings, cache, LATER) is not first
