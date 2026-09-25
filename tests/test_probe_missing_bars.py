"""Offline tests for the #106 missing-bar probe (spike code, never merged)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from tradepartner import calendar


def _load() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_missing_bars.py"
    spec = importlib.util.spec_from_file_location("probe_missing_bars", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["probe_missing_bars"] = mod
    spec.loader.exec_module(mod)
    return mod


pm = _load()

S = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)]


def _bar(day: date, v: int = 100, n: int = 10) -> dict[str, object]:
    # Alpaca stamps a daily bar at midnight New York time (04:00Z in summer).
    return {"t": f"{day.isoformat()}T04:00:00Z", "o": 1, "c": 1, "v": v, "n": n}


def test_bar_session_is_new_york_date() -> None:
    assert pm.bar_session({"t": "2026-01-05T05:00:00Z"}) == date(2026, 1, 5)


def test_placeholder_bars_count_as_missing() -> None:
    bars = {"X": [_bar(S[0]), _bar(S[1], v=0, n=0)]}
    assert pm.present_sessions(bars) == {"X": {S[0]}}


def test_share_per_session_with_ipo_and_never_traded() -> None:
    present = {
        "A": set(S),
        "B": {S[0], S[2], S[3]},  # missing S[1]
        "IPO": {S[2], S[3]},  # first bar after the window start
        # "DEAD" has no bars at all
    }
    stats = pm.missing_stats(["A", "B", "IPO", "DEAD"], present, S)
    assert stats.never_traded == ["DEAD"]
    assert stats.probable_ipo == ["IPO"]
    by_day = {r.session: r for r in stats.rows}
    assert (by_day[S[0]].expected, by_day[S[0]].missing) == (2, 0)
    assert (by_day[S[1]].expected, by_day[S[1]].missing) == (2, 1)
    assert (by_day[S[2]].expected, by_day[S[2]].missing) == (3, 0)
    assert stats.max_share == pytest.approx(0.5)
    assert stats.max_session == S[1]
    assert stats.median_share == pytest.approx(0.0)
    assert stats.top_missing == [("B", 1)]


def test_ipo_gap_after_first_bar_is_missing() -> None:
    present = {"A": set(S), "IPO": {S[1], S[3]}}
    stats = pm.missing_stats(["A", "IPO"], present, S)
    assert {r.session: r.missing for r in stats.rows}[S[2]] == 1
    assert {r.session: r.expected for r in stats.rows}[S[0]] == 1


def test_window_uses_calendar_and_gap() -> None:
    today = date(2026, 9, 25)
    window = pm.window_sessions(today, n=20, gap=5)
    assert len(window) == 20
    assert all(calendar.is_session(d) for d in window)
    end = today
    for _ in range(5):
        end = calendar.previous_session(end)
    assert window[-1] == end
    assert window == sorted(window)


@pytest.mark.parametrize(
    ("max_share", "live", "current", "value", "tighten"),
    [
        (0.004, 0.006, 0.05, 0.015, True),  # max(0.008, 0.011) -> 0.015
        (0.012, 0.001, 0.05, 0.025, True),  # max(0.024, 0.006) -> 0.025
        (0.010, 0.000, 0.05, 0.020, True),  # exact multiple stays put
        (0.030, 0.010, 0.05, 0.05, False),  # 0.06 >= current: keep 0.05
    ],
)
def test_tightening_rule(
    max_share: float, live: float, current: float, value: float, tighten: bool
) -> None:
    rec = pm.tightening(max_share, live, current)
    assert rec.value == pytest.approx(value)
    assert rec.tighten is tighten


def test_tightening_never_below_observed() -> None:
    rec = pm.tightening(0.0, 0.0, 0.05)
    assert rec.value >= 0.005


def test_tightening_without_live_case_is_provisional() -> None:
    rec = pm.tightening(0.004, None, 0.05)
    assert rec.provisional
    assert rec.value == pytest.approx(0.01)


def test_listed_filter() -> None:
    assets = [
        {
            "symbol": "A",
            "exchange": "NYSE",
            "status": "active",
            "tradable": True,
            "class": "us_equity",
        },
        {
            "symbol": "B",
            "exchange": "ARCA",
            "status": "active",
            "tradable": True,
            "class": "us_equity",
        },
        {
            "symbol": "C",
            "exchange": "NASDAQ",
            "status": "active",
            "tradable": False,
            "class": "us_equity",
        },
        {
            "symbol": "D",
            "exchange": "AMEX",
            "status": "active",
            "tradable": True,
            "class": "us_equity",
        },
        {
            "symbol": "E",
            "exchange": "OTC",
            "status": "active",
            "tradable": True,
            "class": "us_equity",
        },
    ]
    assert pm.listed_symbols(assets) == ["A", "D"]


def test_out_dir_inside_repo_refused() -> None:
    with pytest.raises(SystemExit):
        pm.check_out_dir(pm.REPO_ROOT / "data" / "x")


def test_analyze_end_to_end(tmp_path: Path) -> None:
    import json

    assets = [
        {
            "symbol": s,
            "exchange": "NYSE",
            "status": "active",
            "tradable": True,
            "class": "us_equity",
        }
        for s in ("A", "B")
    ]
    (tmp_path / "assets.json").write_text(json.dumps(assets))
    (tmp_path / "window.json").write_text(json.dumps([d.isoformat() for d in S]))
    (tmp_path / "bars").mkdir()
    payload = {
        "feed": "sip",
        "bars": {"A": [_bar(d) for d in S], "B": [_bar(S[0]), _bar(S[1], v=0, n=0)]},
    }
    (tmp_path / "bars" / "batch_0000.json").write_text(json.dumps(payload))
    report = pm.analyze(tmp_path)
    assert "Max share: 0.5000" in report
    assert "Zero-volume placeholder rows: 1" in report
    assert "provisional" in report


def test_expected_from_first_row_of_any_kind() -> None:
    # "LATE" has a placeholder on S[0] and first trades on S[2]: listed, not an IPO.
    present = {"A": set(S), "LATE": {S[2], S[3]}, "NEW": {S[2], S[3]}}
    rows = {"A": set(S), "LATE": set(S), "NEW": {S[2], S[3]}}
    stats = pm.missing_stats(["A", "LATE", "NEW"], present, S, first_seen=rows)
    assert stats.probable_ipo == ["NEW"]
    by_day = {r.session: r for r in stats.rows}
    assert (by_day[S[0]].expected, by_day[S[0]].missing) == (2, 1)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Inflection Point Acquisition Corp. VII Units", "unit"),
        ("JAB Acquisition Corp I Warrants", "warrant"),
        ("Foo Corp Rights", "right"),
        ("Bar Bank 6.5% Series A Preferred Stock", "preferred"),
        ("Baz 5.25% Senior Notes due 2031", "notes"),
        ("Apple Inc. Common Stock", "common-like"),
        ("Taiwan Semiconductor American Depositary Shares", "common-like"),
    ],
)
def test_instrument_kind(name: str, kind: str) -> None:
    assert pm.instrument_kind(name) == kind
