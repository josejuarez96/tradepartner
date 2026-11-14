"""#3312: a real `refetch_actions` run asks each month once. The dry pass
that refuses before writing (#3303) keeps its fetched answers and the write
uses them, so the run costs one request per month, not two."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_backfill_refetch_actions import (
    LATER,
    _committed,
    _dividend,
    _open_release,
    _refetch,
    _rows,
)

from tradepartner.config import Settings

JUNE_EX = date(2019, 6, 14)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _asks(prices: object) -> Counter[tuple[date, date]]:
    return Counter((s, e) for kind, s, e in prices.calls if kind == "actions")  # type: ignore[attr-defined]


def test_a_real_run_makes_one_request_per_month(settings: Settings) -> None:
    prices = _committed(settings)
    _open_release(settings)
    prices.actions = [_dividend(), _dividend(JUNE_EX, action_id="acme-q2-jun")]
    result = _refetch(settings, prices, since=date(2019, 5, 1), through=date(2019, 6, 28))
    assert result.ok and result.added == (("dividend", 2),)
    asks = _asks(prices)
    assert set(asks.values()) == {1} and len(asks) == 2  # May and June, once each
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(2,)]


def test_a_dry_run_still_asks_each_month_once(settings: Settings) -> None:
    prices = _committed(settings)
    _refetch(settings, prices, since=date(2019, 5, 1), through=date(2019, 6, 28), dry_run=True)
    assert set(_asks(prices).values()) == {1}


def test_a_refused_run_asks_each_month_once_and_writes_nothing(settings: Settings) -> None:
    prices = _committed(settings)
    _open_release(settings)
    prices.actions = [_dividend(JUNE_EX, action_id="acme-q2-jun")]
    _refetch(settings, prices, since=date(2019, 6, 1), through=date(2019, 6, 28))
    prices.actions = [_dividend(), _dividend(JUNE_EX, action_id="acme-q2-jun-reissued")]
    prices.calls.clear()
    again = LATER + timedelta(days=1)  # a later clock than the setup run's
    result = _refetch(
        settings, prices, since=date(2019, 5, 1), through=date(2019, 6, 28), clock=lambda: again
    )
    assert not result.ok and len(result.refused) == 1
    assert set(_asks(prices).values()) == {1}
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(1,)]
