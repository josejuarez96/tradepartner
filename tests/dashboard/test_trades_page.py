"""Headless acceptance tests for the read-only Trades page (T259)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard import trades_page
from tradepartner.execution import blotter
from tradepartner.execution.slippage import SideSlippage, Slippage
from tradepartner.store import schema
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import PaperWindowRow, append

_APP = str(Path(__file__).resolve().parents[2] / "src/tradepartner/dashboard/app.py")
_NOW = datetime(2026, 10, 21, 17, tzinfo=UTC)
_OLD = date(2026, 10, 19)
_NEW = date(2026, 10, 20)


def _store(path: Path, books: tuple[str, ...] = ("main",)) -> Path:
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        for i, book in enumerate(books):
            append(
                conn,
                PaperWindowRow(
                    hypothesis_id=1,
                    first_rebalance_session=_OLD,
                    account_id=f"PA{i}",
                    starting_cash=100_000.0,
                    starting_equity=100_000.0,
                    code_version="test",
                    started_at=_NOW,
                    frozen_json="{}",
                    frozen_sha256="0" * 64,
                    book_id=book,
                    known_at=_NOW,
                    ingested_at=_NOW,
                ),
            )
    return path


def _row(symbol: str, slip: float | None, note: str | None = None) -> blotter.BlotterRow:
    return blotter.BlotterRow(
        client_order_id=f"order-{symbol}",
        symbol=symbol,
        side="buy",
        quantity=10.0,
        notional=None,
        filled_quantity=10.0 if slip is not None else 0.0,
        fill_price=100.0 if slip is not None else None,
        status="filled" if slip is not None else "open",
        decision="buy",
        reason="signal reason",
        signal_rank=2,
        slippage_bp=slip,
        slippage_note=note,
    )


def _data(*rows: blotter.BlotterRow) -> blotter.SessionBlotter:
    group = SideSlippage(1, 12.5, 12.5, 12.5)
    empty = SideSlippage(0, None, None, None)
    return blotter.SessionBlotter(tuple(rows), Slippage(group, empty, group, 0, 0, 0), False)


def _app(monkeypatch: pytest.MonkeyPatch, path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(path.parent / "missing.env"))
    at = AppTest.from_file(_APP).run()
    at.sidebar.radio[0].set_value("Trades").run(timeout=30)
    return at


def test_default_book_and_newest_session_and_kpis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _store(tmp_path / "trades.duckdb", ("main", "alpha"))
    calls: list[tuple[int, date]] = []
    monkeypatch.setattr(blotter, "sessions", lambda conn, wid: (_NEW, _OLD))

    def read(
        conn: object, wid: int, session: date, now: datetime, limit: int
    ) -> blotter.SessionBlotter:
        calls.append((wid, session))
        return _data(_row("AAPL", 12.5), _row("MSFT", -20.0))

    monkeypatch.setattr(blotter, "session_blotter", read)
    at = _app(monkeypatch, path)
    assert not at.exception
    assert calls[-1][1] == _NEW
    assert at.selectbox[0].value == "main"
    assert any(box.value == _NEW for box in at.selectbox)
    metrics = {m.label: str(m.value) for m in at.metric}
    assert metrics == {
        "Orders": "2",
        "Filled": "2",
        "Notional traded": "2,000.00",
        "Slippage (bp)": "+12.50",
    }
    assert len(at.get("vega_lite_chart")) == 1
    chart = str(at.get("vega_lite_chart")[0].proto.spec)
    assert "signal rank" in chart and "highlight" in chart
    assert "as of" in " ".join(c.value for c in at.caption)
    assert "last updated" in " ".join(c.value for c in at.caption)

    selector = next(box for box in at.selectbox if box.key == trades_page.BOOK_KEY)
    selector.set_value("alpha").run(timeout=30)
    assert not at.exception
    assert calls[-1][0] != calls[0][0]


def test_older_session_changes_rows_and_missing_slippage_is_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _store(tmp_path / "trades.duckdb")
    monkeypatch.setattr(blotter, "sessions", lambda conn, wid: (_NEW, _OLD))
    monkeypatch.setattr(
        blotter,
        "session_blotter",
        lambda conn, wid, session, now, limit: (
            _data(_row("NEW", 12.5))
            if session == _NEW
            else _data(_row("PENDING", None, "pending"), _row("NOBAR", None, "no bar"))
        ),
    )
    at = _app(monkeypatch, path)
    selector = next(box for box in at.selectbox if box.key == trades_page.SESSION_KEY)
    selector.set_value(_OLD).run(timeout=30)
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table.columns) == [
        "ticker",
        "side",
        "shares",
        "fill price",
        "status",
        "slippage (bp)",
        "reason",
    ]
    assert list(table["ticker"]) == ["PENDING", "NOBAR"]
    assert list(table["slippage (bp)"]) == ["pending", "no bar"]
    assert not at.get("vega_lite_chart")


def test_empty_book_shows_panel_without_chart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _store(tmp_path / "trades.duckdb")
    monkeypatch.setattr(blotter, "sessions", lambda conn, wid: ())
    at = _app(monkeypatch, path)
    assert not at.exception
    assert not at.get("vega_lite_chart")
    assert any("no orders" in m.value.lower() for m in at.info)


def test_unreadable_blotter_uses_panel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _store(tmp_path / "trades.duckdb")
    monkeypatch.setattr(blotter, "sessions", lambda conn, wid: (_NEW,))

    def bad(*args: object) -> blotter.SessionBlotter:
        raise ValueError("malformed sell decision")

    monkeypatch.setattr(blotter, "session_blotter", bad)
    at = _app(monkeypatch, path)
    assert not at.exception
    assert any("could not be read" in m.value.lower() for m in at.error)


def test_no_colour_literal_in_page_code() -> None:
    source = Path(trades_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


def test_page_writes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _store(tmp_path / "trades.duckdb")
    monkeypatch.setattr(blotter, "sessions", lambda conn, wid: (_NEW,))
    monkeypatch.setattr(
        blotter, "session_blotter", lambda conn, wid, session, now, limit: _data(_row("AAPL", 12.5))
    )
    with duckdb.connect(str(path), read_only=True) as conn:
        before = conn.execute("SELECT count(*) FROM paper_windows").fetchone()
    at = _app(monkeypatch, path)
    assert not at.exception
    with duckdb.connect(str(path), read_only=True) as conn:
        after = conn.execute("SELECT count(*) FROM paper_windows").fetchone()
    assert before == after
