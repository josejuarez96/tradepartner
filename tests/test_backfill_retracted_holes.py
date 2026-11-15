"""fill-holes refetches sessions a repair retracted (#3431): a session whose bar
sits in `retracted_rows` of the open release and that no id now holds is a hole,
past the backfill cursor and inside a month the receiver already has bars in.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_backfill import (
    JUNE_WINDOW,
    LATER,
    SINCE,
    _alpaca_runs,
    _backfill,
    _History,
    _read,
    _sessions,
)
from test_ingest import ACME, SPY

from tradepartner.backfill import FILLED, fill_holes
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import open_for_write
from tradepartner.store.retracted_rows import retract

RELEASE = "repair-3431"
THEN = datetime(2019, 6, 29, 12, 0, tzinfo=UTC)  # before LATER


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _retract(settings: Settings, days: list[date], *, at: datetime = THEN) -> None:
    with open_for_write(settings) as conn:
        retract(
            conn, "prices_daily", [(ACME, d) for d in days], "misattributed", RELEASE, "r", at=at
        )


def _open_release(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        registry.open_data_release(conn, conn, name=RELEASE, backup_path="b.duckdb", reason="#3431")


def _acme_sessions(settings: Settings, window: tuple[date, date]) -> list[date]:
    rows: list[Any] = _read(
        settings,
        f"SELECT DISTINCT session FROM prices_daily WHERE security_id = '{ACME}' "
        f"AND session BETWEEN DATE '{window[0]}' AND DATE '{window[1]}' ORDER BY session",
    )
    return [r[0] for r in rows]


def _holes(found: Any) -> list[tuple[str, tuple[date, date]]]:
    return [(h.security_id, h.window) for h in found.holes]


def test_a_retracted_tail_of_a_month_the_receiver_has_bars_in_is_refetched(
    settings: Settings,
) -> None:
    assert _backfill(settings, _History()).ok
    tail = _sessions(date(2019, 6, 17), JUNE_WINDOW[1])
    _retract(settings, tail)
    _open_release(settings)
    assert _acme_sessions(settings, JUNE_WINDOW) == _sessions(JUNE_WINDOW[0], date(2019, 6, 14))
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]
    result = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER)
    assert [r.status for r in result.runs] == [FILLED]
    assert _acme_sessions(settings, JUNE_WINDOW) == _sessions(*JUNE_WINDOW)
    # Nothing is left to fetch.
    again = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert again.holes == ()


def test_a_retracted_month_past_the_backfill_cursor_is_refetched(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    # The cursor stops at May: June came from daily runs.
    with duckdb.connect(settings.store.path) as conn:
        conn.execute("DELETE FROM ingestion_runs WHERE chunk_cursor LIKE '%through=2019-06-%'")
    assert not any("2019-06" in str(r[2]) for r in _alpaca_runs(settings))
    _retract(settings, _sessions(*JUNE_WINDOW))
    _open_release(settings)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]
    prices = _History()
    result = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER)
    assert [r.status for r in result.runs] == [FILLED]
    # Only the retracted id (and the reference) is fetched in the tail month.
    assert prices.fetched == {date(2019, 6, 1): {ACME, SPY}}
    assert _acme_sessions(settings, JUNE_WINDOW) == _sessions(*JUNE_WINDOW)


def test_a_tail_month_is_fetched_for_its_retracted_ids_only(settings: Settings) -> None:
    """Another name with no bar in a month past the cursor is no hole there."""
    assert _backfill(settings, _History()).ok
    with duckdb.connect(settings.store.path) as conn:
        conn.execute("DELETE FROM ingestion_runs WHERE chunk_cursor LIKE '%through=2019-06-%'")
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id <> ? AND security_id <> ? "
            "AND session >= DATE '2019-06-01'",
            [ACME, SPY],
        )
    _retract(settings, _sessions(*JUNE_WINDOW))
    _open_release(settings)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]


def test_a_session_another_bar_already_holds_is_no_hole(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    days = _sessions(date(2019, 6, 17), JUNE_WINDOW[1])
    _retract(settings, days)
    _open_release(settings)
    # The receiver is refilled (by a restore or an earlier fill): no hole is left.
    assert fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER).exit_code == 0
    again = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert again.holes == ()


def test_without_an_open_release_a_retraction_is_no_hole(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    _retract(settings, _sessions(date(2019, 6, 17), JUNE_WINDOW[1]))
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert found.holes == ()


def test_a_retraction_made_after_the_clock_is_no_hole_yet(settings: Settings) -> None:
    """No look-ahead: a fill as of `t` sees only retractions made by `t`."""
    assert _backfill(settings, _History()).ok
    _retract(
        settings,
        _sessions(date(2019, 6, 17), JUNE_WINDOW[1]),
        at=LATER + timedelta(days=1),
    )
    _open_release(settings)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert found.holes == ()


def test_a_refetched_bar_is_stamped_no_earlier_than_its_session_close(settings: Settings) -> None:
    assert _backfill(settings, _History()).ok
    _retract(settings, _sessions(date(2019, 6, 17), JUNE_WINDOW[1]))
    _open_release(settings)
    assert fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER).exit_code == 0
    late = _read(
        settings,
        f"SELECT count(*) FROM prices_daily WHERE security_id = '{ACME}' "
        "AND session >= DATE '2019-06-17' AND known_at < CAST(session AS TIMESTAMPTZ)",
    )
    assert late == [(0,)]
