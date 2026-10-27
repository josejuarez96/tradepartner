"""Tests for the Risk card's read (plan T260, amendment 2026-10-20 #2096 item 3).

Hand values: frozen limits 0.05 cash, 1.0 gross, 0.50 position and 0.30
drawdown; the last mark holds cash 4,000, A 56,000 and B 40,000, so equity is
100,000; the window started with 110,000.

- cash 4,000 / 100,000 = 4.0 %, 5.0 - 4.0 = 1.0 pp left;
- gross (56,000 + 40,000) / 100,000 = 0.96, 100 - 96 = 4.0 pp left;
- largest A, 56,000 / 100,000 = 0.56, 50 - 56 = -6.0 pp (over by 6.0 pp);
- drawdown (110,000 - 100,000) / 110,000 = 9.0909 %, 30 - 9.0909 = 20.9091 pp
  left, and 100,000 - 110,000 x 0.70 = 23,000.00 dollars before the switch;
- after a release with peak_equity 120,000: 20,000 / 120,000 = 16.6667 %, and
  100,000 - 120,000 x 0.70 = 16,000.00 dollars left.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import headroom
from tradepartner.execution.marks import UnreadableMarkError
from tradepartner.store import journal, registry, schema
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import (
    KillSwitchRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    append,
)

_SESSION = date(2026, 10, 16)
_EARLIER = date(2026, 10, 15)
_AT = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
_STAMP: dict[str, Any] = {"known_at": _AT, "ingested_at": _AT}
_LIMITS = {
    "max_unspent_cash_fraction": 0.05,
    "max_gross_exposure": 1.0,
    "max_position_weight": 0.50,
    "max_drawdown": 0.30,
}
_RISK = RiskConfig(**_LIMITS)  # type: ignore[arg-type]


def _frozen_json(risk: RiskConfig = _RISK) -> str:
    return json.dumps(
        {f"risk.{k}": v for k, v in risk.model_dump(mode="json").items()}, sort_keys=True
    )


def _window(window_id: int | None = 7, hypothesis_id: int = 1) -> PaperWindowRow:
    return PaperWindowRow(
        window_id=window_id,
        hypothesis_id=hypothesis_id,
        first_rebalance_session=_EARLIER,
        account_id="PA1",
        starting_cash=110_000.0,
        starting_equity=110_000.0,
        code_version="test",
        started_at=_AT - timedelta(days=5),
        frozen_json=_frozen_json(),
        frozen_sha256="0" * 64,
        **_STAMP,
    )


def _mark(
    security_id: str | None,
    value: float | None,
    *,
    cash: float | None = 4_000.0,
    session: date = _SESSION,
    run_id: int = 1,
    side: str = "long",
) -> PositionDailyRow:
    return PositionDailyRow(
        run_id=run_id,
        session=session,
        security_id=security_id,
        quantity=0.0 if value is None else value / 100.0,
        mark_price=None if value is None else 100.0,
        value=value,
        cash=cash,
        position_side=side,
        **_STAMP,
    )


_MARKS = (_mark("A", 56_000.0), _mark("B", 40_000.0))


def _release(event_id: int, peak: float, window_id: int = 7) -> KillSwitchRow:
    return KillSwitchRow(
        event_id=event_id,
        window_id=window_id,
        at=_AT - timedelta(days=1),
        state="released",
        source="owner",
        reason="resumed",
        peak_equity=peak,
        **_STAMP,
    )


def _engaged(event_id: int, source: str = "drawdown", window_id: int = 7) -> KillSwitchRow:
    return KillSwitchRow(
        event_id=event_id,
        window_id=window_id,
        at=_AT - timedelta(days=2),
        state="engaged",
        source=source,
        reason="drawdown",
        **_STAMP,
    )


def _row(card: headroom.Headroom, key: str) -> headroom.LimitRow:
    (row,) = [r for r in card.rows if r.key == key]
    return row


# --- the pure function -------------------------------------------------------


def test_four_rows_in_order_with_what_each_limit_does() -> None:
    card = headroom.compute(_window(), _MARKS, (), _RISK)
    assert [r.key for r in card.rows] == ["cash", "gross", "position", "drawdown"]
    assert "unspent_cash" in _row(card, "cash").does
    assert "halts" in _row(card, "gross").does
    assert "drift alone never halts" in _row(card, "position").does
    assert "kill switch engages" in _row(card, "drawdown").does
    assert card.session == _SESSION
    assert card.equity == pytest.approx(100_000.0)


def test_hand_values_against_the_frozen_limits() -> None:
    card = headroom.compute(_window(), _MARKS, (), _RISK)
    cash, gross, position, drawdown = card.rows
    assert (cash.value, cash.limit) == (pytest.approx(0.04), 0.05)
    assert cash.headroom_pp == pytest.approx(1.0)
    assert cash.status == "within"
    assert (gross.value, gross.limit) == (pytest.approx(0.96), 1.0)
    assert gross.headroom_pp == pytest.approx(4.0)
    assert gross.status == "within"
    assert (position.value, position.limit) == (pytest.approx(0.56), 0.50)
    assert position.headroom_pp == pytest.approx(-6.0)
    assert position.status == "over"
    assert card.largest_security_id == "A"
    assert drawdown.value == pytest.approx(10_000.0 / 110_000.0)
    assert round(drawdown.value * 100, 2) == 9.09
    assert drawdown.headroom_pp == pytest.approx(30.0 - 1_000_000.0 / 110_000.0)
    assert round(drawdown.headroom_pp, 2) == 20.91
    assert drawdown.status == "within"
    assert card.peak == 110_000.0
    assert card.dollars_before_switch == pytest.approx(23_000.0)


def test_the_largest_position_takes_its_ticker_when_known() -> None:
    card = headroom.compute(_window(), _MARKS, (), _RISK, tickers={"A": "AAA"})
    assert card.largest_ticker == "AAA"
    assert headroom.compute(_window(), _MARKS, (), _RISK).largest_ticker is None


def test_a_release_moves_the_peak_to_its_peak_equity() -> None:
    card = headroom.compute(_window(), _MARKS, (_engaged(1), _release(2, 120_000.0)), _RISK)
    drawdown = _row(card, "drawdown")
    assert round(drawdown.value * 100, 2) == 16.67
    assert drawdown.value == pytest.approx(20_000.0 / 120_000.0)
    assert card.peak == 120_000.0
    assert card.dollars_before_switch == pytest.approx(16_000.0)
    assert drawdown.status == "within"
    assert not card.drawdown_engaged


def test_drawdown_reads_engaged_until_the_next_release() -> None:
    card = headroom.compute(_window(), _MARKS, (_engaged(1),), _RISK)
    assert card.drawdown_engaged
    assert _row(card, "drawdown").status == "engaged"
    # An owner engagement is not the drawdown trigger: the row stays within.
    owner = headroom.compute(_window(), _MARKS, (_engaged(1, source="owner"),), _RISK)
    assert not owner.drawdown_engaged
    assert _row(owner, "drawdown").status == "within"


def test_another_windows_kill_switch_rows_are_ignored() -> None:
    rows = (_engaged(1, window_id=8), _release(2, 500_000.0, window_id=8))
    card = headroom.compute(_window(), _MARKS, rows, _RISK)
    assert card.peak == 110_000.0
    assert not card.drawdown_engaged


def test_at_the_limit_is_not_within() -> None:
    marks = (_mark("A", 50_000.0, cash=50_000.0),)
    position = _row(headroom.compute(_window(), marks, (), _RISK), "position")
    assert position.headroom_pp == pytest.approx(0.0)
    assert position.status == "over"


def test_held_shorts_leave_the_cash_row_and_count_gross_by_absolute_value() -> None:
    # Cash 30,000 includes 20,000 of short proceeds; long A 90,000, short B -20,000.
    marks = (
        _mark("A", 90_000.0, cash=30_000.0),
        _mark("B", -20_000.0, cash=30_000.0, side="short"),
    )
    card = headroom.compute(_window(), marks, (), _RISK)
    assert card.equity == pytest.approx(100_000.0)
    assert _row(card, "cash").value == pytest.approx(0.10)
    assert _row(card, "gross").value == pytest.approx(1.10)
    assert _row(card, "position").value == pytest.approx(0.90)


def test_cash_is_floored_at_zero_when_shorts_exceed_it() -> None:
    marks = (
        _mark("A", 105_000.0, cash=5_000.0),
        _mark("B", -10_000.0, cash=5_000.0, side="short"),
    )
    assert _row(headroom.compute(_window(), marks, (), _RISK), "cash").value == 0.0


def test_before_the_first_mark_shows_the_starting_cash_and_zero_exposure() -> None:
    card = headroom.compute(_window(), (), (), _RISK)
    assert card.session is None
    assert card.equity == 110_000.0
    assert card.cash == 110_000.0
    assert _row(card, "cash").value == pytest.approx(1.0)
    assert _row(card, "gross").value == 0.0
    assert _row(card, "position").value == 0.0
    assert card.largest_security_id is None
    assert _row(card, "drawdown").value == 0.0
    assert card.dollars_before_switch == pytest.approx(110_000.0 - 77_000.0)


def test_only_the_last_marked_session_is_read() -> None:
    older = (_mark("C", 99_000.0, cash=1_000.0, session=_EARLIER),)
    card = headroom.compute(_window(), older + _MARKS, (), _RISK)
    assert card.session == _SESSION
    assert card.largest_security_id == "A"


def test_an_unreadable_mark_raises() -> None:
    with pytest.raises(UnreadableMarkError):
        headroom.compute(_window(), (_mark("A", None),), (), _RISK)


def test_a_long_short_window_is_flagged() -> None:
    assert headroom.compute(_window(), _MARKS, (), _RISK, side="long_short").long_short
    assert not headroom.compute(_window(), _MARKS, (), _RISK).long_short


# --- the read ----------------------------------------------------------------

_HOLDOUT_START, _HOLDOUT_END = date(2018, 7, 2), date(2019, 3, 29)


def _store(tmp_path: Path) -> Settings:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "h.duckdb")})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
    return settings


def _register(conn: duckdb.DuckDBPyConnection) -> int:
    params = Settings(
        _env_file=None,
        holdout={"start": _HOLDOUT_START.isoformat(), "end": _HOLDOUT_END.isoformat()},
    )
    return registry.register_hypothesis(
        conn,
        slug="h-headroom",
        family="momentum",
        title="headroom test",
        doc_path="docs/hypotheses/h-headroom.md",
        doc_sha256="0" * 64,
        params=frozen_params_of(params, family="momentum"),
        in_sample_start=date(2017, 1, 3),
        holdout_start=_HOLDOUT_START,
        holdout_end=_HOLDOUT_END,
        registered_by="test",
        settings=params,
    ).hypothesis_id


def _seed(settings: Settings, *, frozen_json: str | None = None, marks: bool = True) -> int:
    with open_for_write(settings) as conn:
        hypothesis_id = _register(conn)
        window = _window(window_id=None, hypothesis_id=hypothesis_id)
        if frozen_json is not None:
            window = replace(window, frozen_json=frozen_json)
        window_id = append(conn, window)
        assert window_id is not None
        run_id = append(
            conn,
            PaperRunRow(
                window_id=window_id,
                session=_SESSION,
                kind="mark",
                started_at=_AT,
                invoked_by="scheduler",
                code_version="test",
                **_STAMP,
            ),
        )
        assert run_id is not None
        if marks:
            for row in _MARKS:
                append(conn, replace(row, run_id=run_id))
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, valid_from, known_at, "
            "ingested_at, source, provenance) VALUES ('A', 'AAA', 'NYSE', ?, ?, ?, "
            "'fixture', 'filing')",
            [_EARLIER, _AT - timedelta(days=1), _AT - timedelta(days=1)],
        )
        return window_id


def _open_window(conn: duckdb.DuckDBPyConnection, window_id: int) -> PaperWindowRow:
    window = journal.open_window(conn, "main")
    assert window is not None
    assert window.window_id == window_id
    return window


def _counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = [
        str(r[0])
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
    ]
    counts: dict[str, int] = {}
    for table in tables:
        row = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()
        assert row is not None
        counts[table] = int(row[0])
    return counts


def test_read_gives_the_hand_values_and_the_ticker_and_writes_nothing(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    window_id = _seed(settings)
    with open_for_write(settings) as conn:
        before = _counts(conn)
        card = headroom.read(conn, _open_window(conn, window_id))
        assert _counts(conn) == before
    assert card.session == _SESSION
    assert card.largest_ticker == "AAA"
    assert [round(r.headroom_pp, 2) for r in card.rows] == [1.0, 4.0, -6.0, 20.91]
    assert card.dollars_before_switch == pytest.approx(23_000.0)
    assert not card.long_short


def test_read_uses_the_frozen_limits_not_live_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RISK__MAX_POSITION_WEIGHT", "0.90")
    monkeypatch.setenv("RISK__MAX_DRAWDOWN", "0.10")
    settings = _store(tmp_path)
    window_id = _seed(settings)
    with open_for_write(settings) as conn:
        card = headroom.read(conn, _open_window(conn, window_id))
    assert _row(card, "position").limit == 0.50
    assert _row(card, "drawdown").limit == 0.30


def test_read_before_the_first_mark(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    window_id = _seed(settings, marks=False)
    with open_for_write(settings) as conn:
        card = headroom.read(conn, _open_window(conn, window_id))
    assert card.session is None
    assert card.equity == 110_000.0
    assert _row(card, "gross").value == 0.0


def test_read_raises_on_an_unreadable_frozen_json(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    window_id = _seed(settings, frozen_json="{not json")
    with open_for_write(settings) as conn, pytest.raises(ValueError, match="frozen_json"):
        headroom.read(conn, _open_window(conn, window_id))
