"""Shared fixtures for the Phase 4 execution tests (plan T58 owns this file;
later tasks keep their helpers local and never edit it).

- `fixed_clock`: a settable UTC clock that only moves when told.
- `scripted_fake`: a `FakeBroker` on that clock that never fills by itself
  (`auto_fill=False`), so a test scripts every outcome (T46c).
- `journal_settings`: `Settings` pointed at a temp-file copy of the fixture
  store (`fixture_store_path`), whose schema includes the journal, with no
  `.env` loaded.
- `open_window`: one open `paper_windows` row seeded in that store.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.config import Settings
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import PaperWindowRow, append

#: 10:00 ET on a regular session, inside a paper run's submit window.
CLOCK_START = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
WINDOW_FIRST_REBALANCE = date(2026, 9, 30)
ACCOUNT_ID = "PA1"
REFERENCE_PRICE = 100.0


class FixedClock:
    """A clock that returns `now` until a test moves it."""

    def __init__(self, now: datetime = CLOCK_START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> datetime:
        """Move forward by `timedelta(**delta)` and return the new time."""
        self.now += timedelta(**delta)
        return self.now


@pytest.fixture
def fixed_clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def scripted_fake(fixed_clock: FixedClock) -> FakeBroker:
    return FakeBroker(
        clock=fixed_clock,
        price_of=lambda _symbol: REFERENCE_PRICE,
        auto_fill=False,
        account_id=ACCOUNT_ID,
    )


@pytest.fixture
def journal_settings(fixture_store_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(fixture_store_path)})


@pytest.fixture
def open_window(journal_settings: Settings) -> PaperWindowRow:
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=WINDOW_FIRST_REBALANCE,
        account_id=ACCOUNT_ID,
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=CLOCK_START - timedelta(days=2),
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=CLOCK_START - timedelta(days=2),
        ingested_at=CLOCK_START - timedelta(days=2),
    )
    with open_for_write(journal_settings) as conn:
        window_id = append(conn, row)  # type: ignore[arg-type]
    return replace(row, window_id=window_id)
