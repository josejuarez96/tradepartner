"""Offline tests for the #106 pre-open paper-fill probe (spike code, never merged)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_open_fills.py"
    spec = importlib.util.spec_from_file_location("probe_open_fills", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["probe_open_fills"] = mod
    spec.loader.exec_module(mod)
    return mod


po = _load()

D = date(2026, 9, 28)  # a Monday, full session; 09:30 ET = 13:30Z (EDT)


def _utc(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 9, 28, h, m, s, tzinfo=UTC)


def test_order_plan_ids_are_deterministic_and_unique() -> None:
    plan = po.order_plan(D, include_opg=True)
    ids = [o.client_order_id for o in plan]
    assert len(ids) == len(set(ids)) == 6
    assert "probe106-20260928-KO-F" in ids
    f = next(o for o in plan if o.kind == "F" and o.symbol == "AAPL")
    assert (f.notional, f.qty, f.tif) == (5, None, "day")
    o = next(o for o in plan if o.kind == "O" and o.symbol == "KO")
    assert (o.qty, o.tif) == (1, "opg")
    assert len(po.order_plan(D, include_opg=False)) == 4


@pytest.mark.parametrize(
    ("now", "ok"),
    [
        (_utc(13, 0), True),  # 09:00 ET
        (_utc(13, 15), True),  # 09:15 ET
        (_utc(12, 59), False),
        (_utc(13, 16), False),
        (datetime(2026, 9, 27, 13, 5, tzinfo=UTC), False),  # Sunday
        (datetime(2026, 11, 27, 14, 5, tzinfo=UTC), False),  # day after Thanksgiving, half day
    ],
)
def test_submit_window(now: datetime, ok: bool) -> None:
    assert (po.submit_window_error(now) is None) is ok


def test_live_base_url_refused() -> None:
    class Fake:
        _base_url = "https://api.alpaca.markets"

    with pytest.raises(SystemExit):
        po.require_paper(Fake())


def test_paper_base_url_accepted() -> None:
    class Fake:
        _base_url = "https://paper-api.alpaca.markets"

    po.require_paper(Fake())


def test_parse_ts_nanoseconds() -> None:
    t = po.parse_ts("2026-09-28T13:30:00.123456789Z")
    assert t == datetime(2026, 9, 28, 13, 30, 0, 123456, tzinfo=UTC)


def test_official_open_takes_listing_exchange_opening_print() -> None:
    trades = [
        {"t": "2026-09-28T13:30:00.001Z", "x": "V", "p": 60.10, "c": ["@"]},
        {"t": "2026-09-28T13:30:00.500Z", "x": "N", "p": 60.00, "c": ["O"]},
        {"t": "2026-09-28T13:30:01.000Z", "x": "N", "p": 60.00, "c": ["Q"]},
    ]
    assert po.official_open(trades, "N") == pytest.approx(60.00)
    assert po.official_open(trades, "Q") is None


def test_official_open_falls_back_to_q_duplicate() -> None:
    trades = [{"t": "2026-09-28T13:30:02Z", "x": "Q", "p": 230.5, "c": ["@", "Q"]}]
    assert po.official_open(trades, "Q") == pytest.approx(230.5)


def test_nbbo_at_takes_last_quote_not_after_t() -> None:
    quotes = [
        {"t": "2026-09-28T13:05:00Z", "bp": 59.9, "ap": 60.1},
        {"t": "2026-09-28T13:05:02Z", "bp": 59.95, "ap": 60.05},
        {"t": "2026-09-28T13:05:05Z", "bp": 1, "ap": 2},
    ]
    assert po.nbbo_at(quotes, _utc(13, 5, 3)) == (59.95, 60.05)
    assert po.nbbo_at(quotes, _utc(13, 4, 0)) is None


def test_order_metrics_and_flags() -> None:
    order = {
        "client_order_id": "probe106-20260928-KO-W",
        "symbol": "KO",
        "status": "filled",
        "submitted_at": "2026-09-28T13:05:00Z",
        "filled_at": "2026-09-28T13:30:00.400Z",
        "filled_avg_price": "60.06",
        "filled_qty": "1",
    }
    m = po.order_metrics(order, D, bar_open=60.03, official=60.00, pre_ask=60.10)
    assert m.kind == "W"
    assert m.latency_s == pytest.approx(0.4)
    assert m.gap_official_bps == pytest.approx(10.0)
    assert m.gap_bar_bps == pytest.approx(10_000 * 0.03 / 60.03)
    assert not m.late and not m.at_official and not m.at_pre_ask


def test_unfilled_order_is_late_with_no_gap() -> None:
    order = {"client_order_id": "probe106-20260928-KO-F", "symbol": "KO", "status": "new"}
    m = po.order_metrics(order, D, bar_open=60.0, official=60.0, pre_ask=None)
    assert m.late and m.gap_bar_bps is None


def test_summary_median_and_worst() -> None:
    rows = [
        po.Metrics("x", "KO", "F", "filled", 0.2, 5.0, 4.0, False, False, False),
        po.Metrics("y", "KO", "F", "filled", 0.4, -3.0, 1.0, False, False, False),
        po.Metrics("z", "KO", "F", "filled", 0.9, 12.0, 8.0, False, False, False),
    ]
    s = po.summarize(rows)["F"]
    assert s["n"] == 3
    assert s["gap_official_bps"] == (pytest.approx(5.0), pytest.approx(12.0))
    assert s["latency_s"] == (pytest.approx(0.4), pytest.approx(0.9))


def test_analyze_end_to_end(tmp_path: Path) -> None:
    day = tmp_path / D.isoformat()
    day.mkdir()
    orders = [
        {
            "client_order_id": "probe106-20260928-KO-F",
            "symbol": "KO",
            "status": "filled",
            "submitted_at": "2026-09-28T13:05:00Z",
            "filled_at": "2026-09-28T13:30:00.300Z",
            "filled_avg_price": "60.10",
            "filled_qty": "0.083",
        }
    ]
    (day / "orders_final.json").write_text(json.dumps(orders))
    (day / "bars.json").write_text(
        json.dumps({"feed": "sip", "bars": {"KO": [{"t": "2026-09-28T04:00:00Z", "o": 60.05}]}})
    )
    (day / "trades_KO.json").write_text(
        json.dumps({"KO": [{"t": "2026-09-28T13:30:00.2Z", "x": "N", "p": 60.0, "c": ["O"]}]})
    )
    (day / "quotes_probe106-20260928-KO-F_submit.json").write_text(
        json.dumps({"KO": [{"t": "2026-09-28T13:04:59Z", "bp": 60.0, "ap": 60.10}]})
    )
    report = po.analyze(tmp_path)
    assert "probe106-20260928-KO-F" in report
    assert "at pre-open ask: 1" in report
