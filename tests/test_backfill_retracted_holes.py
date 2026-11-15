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

from tradepartner.adapters.prices import bar_known_at
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


def test_a_retracted_session_whose_receiver_holds_a_different_bar_is_a_hole(
    settings: Settings,
) -> None:
    """ALTI: the receiver trades a foreign line every session, so no month has a gap."""
    assert _backfill(settings, _History()).ok
    days = _sessions(*JUNE_WINDOW)
    keys = [(ACME, d) for d in days]
    with open_for_write(settings) as conn:
        retract(conn, "prices_daily", keys, "foreign", RELEASE, "r", at=THEN)
    _open_release(settings)
    # Refill ACME with the foreign line: restore the rows, then change them.
    with open_for_write(settings) as conn:
        for d in days:
            conn.execute(
                "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
                "known_at, ingested_at, source, provenance) "
                "SELECT ?, ?, 3.0, 4.0, 2.0, 3.0, 1000000, ?, ?, source, provenance "
                "FROM prices_daily WHERE security_id = ? LIMIT 1",
                [ACME, d, bar_known_at(d), bar_known_at(d) + timedelta(hours=1), SPY],
            )
    # The vendor serves exactly the planted bar again: the refetch writes nothing, yet
    # the month is answered and the next dry run lists no hole.
    same = _History(close=3.0)
    found = fill_holes(settings, prices=same, since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]
    assert fill_holes(settings, prices=same, since=SINCE, clock=lambda: LATER).exit_code == 0
    # Answered: a fill finished after the retraction, so no hole is left.
    again = fill_holes(settings, prices=same, since=SINCE, clock=lambda: LATER, dry_run=True)
    assert again.holes == ()


def test_a_retracted_session_after_the_receivers_last_bar_is_a_hole(settings: Settings) -> None:
    """APPH: not a delisting tail, the bar was real."""
    assert _backfill(settings, _History()).ok
    last = _sessions(*JUNE_WINDOW)[-1]
    _retract(settings, [last])
    _open_release(settings)
    assert _acme_sessions(settings, JUNE_WINDOW)[-1] < last
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]


def test_a_retracted_session_inside_a_month_the_receiver_lacks_is_a_hole(
    settings: Settings,
) -> None:
    """NHWK: an interior gap of whole months, only a few sessions retracted."""
    assert _backfill(settings, _History()).ok
    with duckdb.connect(settings.store.path) as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session BETWEEN ? AND ?",
            [ACME, date(2019, 5, 1), date(2019, 5, 20)],
        )
    _retract(settings, _sessions(date(2019, 5, 21), date(2019, 5, 24)))
    _open_release(settings)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert (ACME, (date(2019, 5, 1), date(2019, 5, 31))) in _holes(found)


# --- #3454: the receiver of a hole whose lost id's listings were master-retracted ---

LOST = "0000000099"


def _lost_id_store(settings: Settings, *, listing_known: datetime = THEN) -> None:
    """ACME is the receiver: it holds no June bar (the backfill cursor stops at
    May). LOST held ACME's ticker, its June bars are retracted and its listing is
    a retraction tombstone, as after step 9's master-retract (CRC, PG, DBD)."""
    assert _backfill(settings, _History()).ok
    days = _sessions(*JUNE_WINDOW)
    # Both listing rows are known after the clock when the tombstone is (no look-ahead).
    live_gap = timedelta(days=400) if listing_known <= THEN else timedelta(hours=1)
    with duckdb.connect(settings.store.path) as conn:
        conn.execute("DELETE FROM ingestion_runs WHERE chunk_cursor LIKE '%through=2019-06-%'")
        conn.execute(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
            "known_at, ingested_at, source, provenance) "
            "SELECT ?, session, open, high, low, close, volume, known_at, ingested_at, "
            "source, provenance FROM prices_daily WHERE security_id = ? "
            "AND session BETWEEN ? AND ?",
            [LOST, ACME, *JUNE_WINDOW],
        )
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session BETWEEN ? AND ?",
            [ACME, *JUNE_WINDOW],
        )
        for retracted in (False, True):
            conn.execute(
                "INSERT INTO listings (security_id, ticker, exchange, class_title, valid_from, "
                "retracted, known_at, ingested_at, source, provenance) "
                "SELECT ?, ticker, exchange, class_title, valid_from, ?, ?, ?, source, provenance "
                "FROM listings WHERE security_id = ? ORDER BY known_at LIMIT 1",
                [
                    LOST,
                    retracted,
                    listing_known - (timedelta(0) if retracted else live_gap),
                    listing_known,
                    ACME,
                ],
            )
    with open_for_write(settings) as conn:
        retract(
            conn, "prices_daily", [(LOST, d) for d in days], "misattributed", RELEASE, "r", at=THEN
        )
    _open_release(settings)


def test_a_hole_whose_lost_ids_listings_were_retracted_is_refetched_for_its_receiver(
    settings: Settings,
) -> None:
    _lost_id_store(settings)
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert _holes(found) == [(ACME, JUNE_WINDOW)]
    prices = _History()
    result = fill_holes(settings, prices=prices, since=SINCE, clock=lambda: LATER)
    assert [r.status for r in result.runs] == [FILLED]
    assert prices.fetched == {date(2019, 6, 1): {ACME, SPY}}
    assert _acme_sessions(settings, JUNE_WINDOW) == _sessions(*JUNE_WINDOW)


def test_a_lost_ids_retracted_listing_made_after_the_clock_names_no_receiver(
    settings: Settings,
) -> None:
    """No look-ahead: the tickers the lost id held are those known at `t`."""
    _lost_id_store(settings, listing_known=LATER + timedelta(days=1))
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert found.holes == ()


def test_an_owner_holding_its_own_bar_is_no_receiver_of_the_lost_ids_hole(
    settings: Settings,
) -> None:
    """#3455: the receiver already holds its own, non-foreign bars there, so a
    refetch could not land the retracted bar and none is asked for."""
    _lost_id_store(settings)
    with duckdb.connect(settings.store.path) as conn:
        for d in _sessions(*JUNE_WINDOW):  # the same line the retracted bar carried
            conn.execute(
                "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
                "known_at, ingested_at, source, provenance) "
                "SELECT ?, ?, 20.0, 21.0, 19.0, 20.0, 1000000, ?, ?, source, provenance "
                "FROM prices_daily WHERE security_id = ? LIMIT 1",
                [ACME, d, bar_known_at(d), bar_known_at(d) + timedelta(hours=1), SPY],
            )
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert found.holes == ()
