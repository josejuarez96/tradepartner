"""Tests for the operations page (Phase 4 T69, spec req 12; ADR 0011).

Driven headless through `streamlit.testing.v1.AppTest` on the shell, as
`test_trials_page.py` does, over a temp store seeded only through
`store.journal`. `execution.ops.page_data` is T66b's own module and has its
own tests (`tests/execution/test_ops.py`); here the page is exercised as the
thin read-only draw over it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import version_4_store
from streamlit.testing.v1 import AppTest

from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.calendar import last_completed_session, next_session, previous_session
from tradepartner.config import RiskConfig, Settings
from tradepartner.dashboard import ops_page, theme
from tradepartner.execution import headroom, ops
from tradepartner.store import journal, registry, schema
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.journal import (
    AlertRow,
    DecisionRow,
    FillRow,
    KillSwitchRow,
    OrderEventRow,
    OrderRow,
    PaperPlanRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    ReconciliationRow,
    SignalRow,
    append,
)

_ROOT = Path(__file__).resolve().parents[2]
_APP_PATH = str(_ROOT / "src" / "tradepartner" / "dashboard" / "app.py")
_PAGE = "Operations"
_ACCOUNT_ID = "PA1"

# The fixture's seeding and the page's own `_required_run_session(utc_now())`
# at render must read the same instant, or a shard that imports before a
# session boundary and renders after it sees the finished run on "S-1 at
# import" as stale against "S-1 at render" (#1263). Capture the clock once
# here, derive S-1 from it, and freeze the page's clock to it in `_app`; import
# and render then cannot straddle a boundary.
_NOW = utc_now()
_S_MINUS_1 = ops._required_run_session(_NOW)
#: The session after S-1: the one a boundary crossing would advance the page
#: to, so the regression test's `_AFTER_BOUNDARY` clock is the session past it.
_S = next_session(_S_MINUS_1)
_AFTER_BOUNDARY = datetime.combine(next_session(_S), datetime.min.time(), tzinfo=UTC) + timedelta(
    hours=14
)
_T0 = datetime.combine(_S_MINUS_1, datetime.min.time(), tzinfo=UTC) + timedelta(hours=14)


_RECON_JSON = json.dumps(
    {
        "activities": [],
        "explained": [
            {
                "kind": "dividend_cash",
                "security_id": "BBB",
                "quantity": None,
                "cash": 12.5,
                "explanation": "cash dividend",
            }
        ],
        "mismatches": [],
        "symbol_changes": [{"security_id": "BBB", "old_symbol": "BBB", "new_symbol": "BBX"}],
    }
)


def _at(minutes: int = 0) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _stamp(minutes: int = 0) -> dict[str, datetime]:
    return {"known_at": _at(minutes), "ingested_at": _at(minutes)}


def _seed(store_path: Path) -> None:
    """A window with one finished run on S-1, a plan with a ranking, one
    filled order with its chain, a position mark, a reconciliation and an
    alert -- everything the page draws."""
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        window = PaperWindowRow(
            hypothesis_id=1,
            first_rebalance_session=_S_MINUS_1,
            account_id=_ACCOUNT_ID,
            starting_cash=100_000.0,
            starting_equity=100_000.0,
            code_version="test",
            started_at=_at(-60),
            frozen_json="{}",
            frozen_sha256="0" * 64,
            known_at=_at(-60),
            ingested_at=_at(-60),
        )
        window_id = append(conn, window)
        assert window_id is not None

        run_id = append(
            conn,
            PaperRunRow(
                window_id=window_id,
                session=_S_MINUS_1,
                kind="rebalance",
                started_at=_at(0),
                invoked_by="scheduler",
                code_version="abc",
                **_stamp(0),
            ),
        )
        assert run_id is not None
        append(
            conn,
            PaperRunResultRow(
                run_id=run_id, finished_at=_at(5), status="ok", clock_fault=False, **_stamp(5)
            ),
        )

        decision_id = append(
            conn,
            DecisionRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="BBB",
                side="buy",
                whole_share=True,
                decision="trade",
                **_stamp(1),
            ),
        )
        assert decision_id is not None

        append(
            conn,
            PaperPlanRow(
                run_id=run_id,
                plan_trial_id=1,
                rebalance_session=_S_MINUS_1,
                n_universe=10,
                n_targets=1,
                n_orders_below_min_at_live_capital=0,
                **_stamp(2),
            ),
        )
        append(
            conn,
            SignalRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="BBB",
                score=1.0,
                rank=1,
                reason="selected",
                **_stamp(2),
            ),
        )
        append(
            conn,
            SignalRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="CCC",
                score=0.5,
                rank=2,
                reason="below_cut",
                **_stamp(2),
            ),
        )
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, valid_from, known_at, "
            "ingested_at, source, provenance) VALUES ('BBB', 'BBB', 'NYSE', ?, ?, ?, "
            "'fixture', 'filing')",
            [_S_MINUS_1, _at(1), _at(1)],
        )

        order = OrderRow(
            client_order_id="tp-buy-1",
            decision_id=decision_id,
            run_id=run_id,
            session=_S_MINUS_1,
            attempt=1,
            phase="buy",
            security_id="BBB",
            symbol="BBB",
            side="buy",
            quantity=5.0,
            sells_in_flight_at_submit=False,
            **_stamp(3),
        )
        append(conn, order)
        append(conn, OrderEventRow(client_order_id="tp-buy-1", status="accepted", **_stamp(4)))
        append(
            conn,
            OrderEventRow(
                client_order_id="tp-buy-1",
                status="filled",
                filled_quantity=5.0,
                filled_avg_price=20.0,
                **_stamp(5),
            ),
        )
        append(
            conn,
            FillRow(
                client_order_id="tp-buy-1",
                filled_at=_at(5),
                quantity=5.0,
                price=20.0,
                price_implied=True,
                broker_fill_id="brk-1",
                source="broker_status",
                **_stamp(5),
            ),
        )
        append(
            conn,
            PositionDailyRow(
                run_id=run_id,
                session=_S_MINUS_1,
                security_id="BBB",
                quantity=5.0,
                mark_price=20.0,
                value=100.0,
                **_stamp(6),
            ),
        )
        append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=run_id,
                at=_at(7),
                status="ok",
                broker_cash=1000.0,
                mismatches_json=_RECON_JSON,
                **_stamp(7),
            ),
        )
        append(
            conn,
            AlertRow(
                run_id=run_id,
                session=_S_MINUS_1,
                kind="stale_data",
                message="SPY has no bar for S",
                at=_at(8),
                **_stamp(8),
            ),
        )


@pytest.fixture
def seeded_store(tmp_path: Path) -> Path:
    store_path = tmp_path / "ops.duckdb"
    _seed(store_path)
    return store_path


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path, now: datetime = _NOW) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    # Freeze the clock the page reads to the instant S-1 was derived from, so a
    # slow shard whose wall clock crosses a session boundary between import and
    # render cannot advance the required session past the fixture's run (#1263).
    monkeypatch.setattr(ops, "utc_now", lambda: now)
    monkeypatch.setattr(ops_page, "utc_now", lambda: now)
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run()
    return at


def _text(at: AppTest) -> str:
    parts: list[Any] = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.header]
    parts += [h.value for h in at.subheader]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


def _dataframe_with_columns(at: AppTest, *columns: str) -> Any:
    for df in at.dataframe:
        if set(columns) <= set(df.value.columns):
            return df.value
    available = [list(df.value.columns) for df in at.dataframe]
    raise AssertionError(f"no dataframe has columns {columns}; got {available}")


def _overdue_delisting(store_path: Path, *, paid: bool = False) -> None:
    """BBB removed on S-1 with no cash and flagged overdue by a reconciliation (#411)."""
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        append(
            conn,
            journal.AdjustmentRow(
                window_id=1,
                session=_S_MINUS_1,
                kind="corporate_action_cash",
                security_id="BBB",
                quantity=-5.0,
                cash=0.0,
                explanation_json=json.dumps({"explanation": "listing ended"}),
                **_stamp(9),
            ),
        )
        if paid:
            append(
                conn,
                journal.AdjustmentRow(
                    window_id=1,
                    session=_S_MINUS_1,
                    kind="corporate_action_cash",
                    security_id="BBB",
                    cash=100.0,
                    explanation_json=json.dumps({"delisting_cash": True}),
                    **_stamp(9),
                ),
            )
        allowed = [{"reason": "overdue delisting cash", "difference": 5.0, "security_id": "BBB"}]
        append(
            conn,
            ReconciliationRow(
                window_id=1,
                at=_at(10),
                status="ok",
                broker_cash=1000.0,
                mismatches_json=json.dumps({"mismatches": [], "allowed": allowed}),
                **_stamp(10),
            ),
        )


def test_page_shows_a_delisting_whose_cash_is_overdue(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _overdue_delisting(seeded_store)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert f"BBB: delisting cash overdue since {_S_MINUS_1} \u2014 check Alpaca's activity" in (
        _text(at)
    )


def test_page_drops_the_overdue_line_once_the_cash_is_booked(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _overdue_delisting(seeded_store, paid=True)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert "delisting cash overdue" not in _text(at)


def _edgar_run(store_path: Path, finished: datetime, *, status: str = "ok") -> None:
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, started_at, finished_at, status, source, mode) "
            "VALUES (?, ?, ?, ?, 'edgar', 'session')",
            [f"r-{finished.isoformat()}-{status}", finished, finished, status],
        )


def _sessions_back(count: int) -> datetime:
    """After the close of the session `count` sessions before the page's last closed one."""
    session = last_completed_session(_NOW)
    for _ in range(count):
        session = previous_session(session)
    return datetime.combine(session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)


def test_page_shows_edgar_data_three_sessions_old(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _edgar_run(seeded_store, _sessions_back(3))
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert "EDGAR data 3 sessions old" in _text(at)


def test_page_is_quiet_two_sessions_in_and_after_an_ok_run(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _edgar_run(seeded_store, _sessions_back(2))
    assert "EDGAR data" not in _text(_app(monkeypatch, seeded_store))


# --- headless render --------------------------------------------------------


def test_page_is_in_the_shell_navigation(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert at.sidebar.radio[0].options == [
        "Data health",
        "Backtest",
        "Trial registry",
        "Research",
        _PAGE,
        "Trades",
        "Performance",
        "Override",
    ]


def test_render_journal_not_initialised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = version_4_store(tmp_path / "v4.duckdb")
    at = _app(monkeypatch, store_path)
    assert not at.exception
    text = _text(at).lower()
    assert "journal not initialised" in text
    assert "version 4" not in text


def test_render_journal_outdated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """#1261: a version-16 journal shows the migrate-first message, not a false
    "no journal"."""
    store_path = tmp_path / "v16.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")
    at = _app(monkeypatch, store_path)
    assert not at.exception
    text = _text(at)
    assert "journal outdated" in text.lower()
    assert "open it for writing once" in text


def test_render_no_window_yet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "empty.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "no paper window yet" in _text(at).lower()


def test_header_shows_as_of_last_updated_and_stale_chip(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    text = _text(at)
    assert "as of" in text and "last updated" in text
    # S-1 has a finished run in the fixture, so the page is not stale.
    assert "stale" not in text.lower()


def test_stale_badge_says_which_to_believe(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    """P-opsstale (#2206): the stale badge explains itself and says it is expected
    before the morning run."""
    at = _app(monkeypatch, seeded_store, _AFTER_BOUNDARY + timedelta(days=10))
    assert not at.exception
    text = _text(at)
    assert "stale: no run yet" in text
    assert "Normal before today's morning run" in text


def test_reconciliation_lists_events_not_raw_json(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    """O-recon (#2206): events on holdings read as a list; raw JSON is in an expander."""
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    text = _text(at)
    assert "Dividend on BBB, cash +12.50: cash dividend" in text
    assert "Ticker change on BBB: BBB is now BBX" in text
    assert '{"activities"' not in text
    assert any("Raw reconciliation record" in e.label for e in at.expander)


def test_reconciliation_events_helper_survives_bad_json() -> None:
    assert ops_page._reconciliation_events("not json") == []
    assert ops_page._reconciliation_events("[]") == []
    lines = ops_page._reconciliation_events(
        '{"mismatches": [{"kind": "cash", "detail": "off by 3", "security_id": "AAA"}]}'
    )
    assert lines == ["Needs a look, cash on AAA: off by 3"]


def test_stale_chip_survives_a_session_boundary(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    """Regression for #1263: a shard that imports before a session boundary and
    renders after it must not paint `stale` for the fixture's finished run.
    Simulates the ambient clock one session past S-1; a page that reads that
    clock (instead of the fixture's own frozen instant) then requires a later
    session the fixture has no run for."""
    monkeypatch.setattr(ops, "utc_now", lambda: _AFTER_BOUNDARY)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert "stale" not in _text(at).lower()


def test_render_kpis_and_switch_chip(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    metrics = {m.label: str(m.value) for m in at.metric}
    assert metrics["Positions"] == "1"
    assert metrics["Open orders"] == "0"
    assert metrics["Today's signals"] == "1"
    assert "kill switch: ok" in _text(at).lower()


def test_render_ranking_hero_and_table(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    rows = _dataframe_with_columns(at, "ticker", "selected", "signal_reason")
    assert set(rows["ticker"].to_list()) == {"BBB", "Ticker unavailable (CCC)"}
    by_security = rows.set_index("ticker")
    assert bool(by_security.loc["BBB", "selected"]) is True
    assert bool(by_security.loc["Ticker unavailable (CCC)", "selected"]) is False
    assert "No separate reason recorded" in by_security.loc["BBB", "decision_reason"]


def test_ranking_uses_known_ticker_for_security_id(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    with duckdb.connect(str(seeded_store)) as conn:
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, valid_from, known_at, "
            "ingested_at, source, provenance) VALUES ('CCC', 'CEE', 'NYSE', ?, ?, ?, "
            "'fixture', 'filing')",
            [_S_MINUS_1, _at(-1), _at(-1)],
        )
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    ranking = _dataframe_with_columns(at, "ticker", "selected")
    assert set(ranking["ticker"].to_list()) == {"BBB", "CEE"}
    assert "security_id" not in ranking.columns


def test_ranking_uses_ticker_known_at_plan_time(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    with duckdb.connect(str(seeded_store)) as conn:
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, valid_from, known_at, "
            "ingested_at, source, provenance) VALUES "
            "('CCC', 'OLD', 'NYSE', ?, ?, ?, 'fixture', 'filing'), "
            "('CCC', 'NEW', 'NYSE', ?, ?, ?, 'fixture', 'filing')",
            [ops.previous_session(_S_MINUS_1), _at(1), _at(1), _S_MINUS_1, _at(6), _at(6)],
        )
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    ranking = _dataframe_with_columns(at, "ticker", "selected")
    assert set(ranking["ticker"].to_list()) == {"BBB", "OLD"}


def _add_order_for_unlisted_ranked_name(store_path: Path, *, order_minute: int) -> None:
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        (run_id,) = conn.execute("SELECT run_id FROM paper_plans").fetchone()
        decision_id = append(
            conn,
            DecisionRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="CCC",
                side="buy",
                whole_share=True,
                decision="trade",
                **_stamp(0),
            ),
        )
        assert decision_id is not None
        append(
            conn,
            OrderRow(
                client_order_id="tp-ccc-buy-1",
                decision_id=decision_id,
                run_id=run_id,
                session=_S_MINUS_1,
                attempt=1,
                phase="buy",
                security_id="CCC",
                symbol="CEE",
                side="buy",
                quantity=1.0,
                sells_in_flight_at_submit=False,
                **_stamp(order_minute),
            ),
        )


def test_ranking_does_not_use_order_symbol_recorded_after_plan(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _add_order_for_unlisted_ranked_name(seeded_store, order_minute=4)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    ranking = _dataframe_with_columns(at, "ticker", "selected")
    assert set(ranking["ticker"].to_list()) == {"BBB", "Ticker unavailable (CCC)"}


def test_ranking_uses_order_symbol_known_before_plan(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _add_order_for_unlisted_ranked_name(seeded_store, order_minute=1)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    ranking = _dataframe_with_columns(at, "ticker", "selected")
    assert set(ranking["ticker"].to_list()) == {"BBB", "CEE"}


def test_render_fills_card(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    fills = _dataframe_with_columns(at, "broker_fill_id", "quantity", "price")
    assert fills["broker_fill_id"].to_list() == ["brk-1"]


def test_open_rebalance_order_explains_fill_timing(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(seeded_store)})
    with open_for_write(settings) as conn:
        decision_id = append(
            conn,
            DecisionRow(
                run_id=1,
                rebalance_session=_S_MINUS_1,
                security_id="0002023554",
                side="buy",
                whole_share=False,
                decision="trade",
                **_stamp(8),
            ),
        )
        assert decision_id is not None
        append(
            conn,
            OrderRow(
                client_order_id="tp-buy-pending",
                decision_id=decision_id,
                run_id=1,
                session=_S_MINUS_1,
                attempt=1,
                phase="buy",
                security_id="0002023554",
                symbol="AAPL",
                side="buy",
                notional=494.0,
                sells_in_flight_at_submit=False,
                **_stamp(9),
            ),
        )
        append(
            conn, OrderEventRow(client_order_id="tp-buy-pending", status="accepted", **_stamp(10))
        )
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert "Fills are recorded at the next scheduled run" in _text(at)
    assert "08:05 ET" not in _text(at)
    [picker] = at.selectbox
    assert any(option.startswith("AAPL") for option in picker.options)
    assert any("tp-buy-pending" in option for option in picker.options)
    assert all("0002023554" not in option for option in picker.options)


def test_render_chain_detail_view(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    [picker] = at.selectbox
    assert picker.options == ["BBB #1 (tp-buy-1)"]
    chain = _dataframe_with_columns(at, "kind", "detail")
    assert set(chain["kind"].to_list()) >= {"order", "order_event", "fill"}


def test_render_alerts_card(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    alerts = _dataframe_with_columns(at, "kind", "message")
    assert "SPY has no bar for S" in alerts["message"].to_list()


def test_render_reconciliation_status(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert "ok" in _text(at).lower()


@pytest.mark.parametrize(
    ("status", "colour"),
    [
        ("ok", "green"),
        ("pending_unresolved", "orange"),
        ("fills_lagging", "orange"),
        ("mismatch", "red"),
    ],
)
def test_reconciliation_status_severity(status: str, colour: str) -> None:
    """A transient, allowance-governed status (`pending_unresolved`,
    `fills_lagging`) reads as a warning, never the same critical red as a real
    `mismatch` (code review on PR #496: both painted the same badge before this
    mapping existed)."""
    assert theme.STATUS_COLOR[ops_page._RECONCILIATION_STATUS.get(status, "critical")] == colour


def test_render_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> Any:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    at = _app(monkeypatch, seeded_store)
    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    at.run()
    assert not at.exception
    assert opened == [True]


@pytest.fixture
def holder(seeded_store: Path) -> Iterator[subprocess.Popen[str]]:
    """Another process holding the paper run lock until its stdin closes
    (mirrors `tests/execution/test_ops.py::holder`)."""
    script = textwrap.dedent(
        f"""
        import sys
        from tradepartner.config import Settings
        from tradepartner.execution.lock import run_lock

        settings = Settings(_env_file=None, store={{"path": {str(seeded_store)!r}}})
        with run_lock(settings):
            print("held", flush=True)
            sys.stdin.read()
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_ROOT,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        yield proc
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def test_run_in_progress_while_another_process_holds_the_lock(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path, holder: subprocess.Popen[str]
) -> None:
    # An unfinished run of this window, as the holder's own run would leave
    # one, so the derived state is `run_in_progress`, not `engaged`.
    settings = Settings(_env_file=None, store={"path": str(seeded_store)})
    with open_for_write(settings) as conn:
        window_id = conn.execute("SELECT window_id FROM paper_windows LIMIT 1").fetchone()
        assert window_id is not None
        append(
            conn,
            PaperRunRow(
                window_id=window_id[0],
                session=next_session(_S_MINUS_1),
                kind="rebalance",
                started_at=_at(100),
                invoked_by="scheduler",
                code_version="abc",
                **_stamp(100),
            ),
        )

    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    assert "run in progress" in _text(at).lower()


# --- theme -------------------------------------------------------------------


def test_no_colour_literal_in_page_code() -> None:
    source = Path(ops_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


# --- books (ADR 0017 B.7; plan T156) ----------------------------------------------


def _add_book_b(store_path: Path) -> None:
    """A second open book beside the seeded `main` one: its own window, one
    finished run on S-1 and two marked positions, and no fill, order or alert."""
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        window_id = append(
            conn,
            PaperWindowRow(
                hypothesis_id=1,
                first_rebalance_session=_S_MINUS_1,
                account_id="PB1",
                starting_cash=50_000.0,
                starting_equity=50_000.0,
                code_version="test",
                started_at=_at(-30),
                frozen_json="{}",
                frozen_sha256="0" * 64,
                book_id="b",
                known_at=_at(-30),
                ingested_at=_at(-30),
            ),
        )
        assert window_id is not None
        run_id = append(
            conn,
            PaperRunRow(
                window_id=window_id,
                session=_S_MINUS_1,
                kind="rebalance",
                started_at=_at(10),
                invoked_by="scheduler",
                code_version="abc",
                **_stamp(10),
            ),
        )
        assert run_id is not None
        append(
            conn,
            PaperRunResultRow(
                run_id=run_id, finished_at=_at(12), status="ok", clock_fault=False, **_stamp(12)
            ),
        )
        for security_id in ("DDD", "EEE"):
            append(
                conn,
                PositionDailyRow(
                    run_id=run_id,
                    session=_S_MINUS_1,
                    security_id=security_id,
                    quantity=1.0,
                    mark_price=10.0,
                    value=10.0,
                    **_stamp(13),
                ),
            )


def test_one_book_shows_its_summary_row_and_no_selector(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    summary = _dataframe_with_columns(at, "book", "next_rebalance", "kill_switch")
    assert summary["book"].to_list() == ["main"]
    assert all(sb.key != ops_page.BOOK_KEY for sb in at.selectbox)


def test_summary_names_traded_rebalance_without_marking_next_daily_one_done(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(seeded_store)})
    with duckdb.connect(str(seeded_store), read_only=True) as conn:
        (row,) = ops.book_summaries(conn, settings)
    today = datetime(2026, 10, 30, 14, tzinfo=UTC)
    monkeypatch.setattr(ops, "utc_now", lambda: today)
    row = replace(
        row,
        last_run_kind="rebalance",
        last_run_status="ok",
        last_run_session=today.date(),
        next_rebalance_session=today.date(),
    )
    (label,) = ops_page._summary_table((row,))["next_rebalance"].to_list()
    assert label == "2026-10-30; rebalance 2026-10-29 traded today"


def test_summary_names_rebalance_run_with_no_orders(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _add_book_b(seeded_store)
    monkeypatch.setattr(ops, "utc_now", lambda: _T0)
    settings = Settings(_env_file=None, store={"path": str(seeded_store)})
    with duckdb.connect(str(seeded_store), read_only=True) as conn:
        b = next(row for row in ops.book_summaries(conn, settings) if row.book_id == "b")
    (label,) = ops_page._summary_table((b,))["next_rebalance"].to_list()
    assert f"rebalance {ops.previous_session(_S_MINUS_1)} ran today with no orders" in label
    assert "traded today" not in label


def test_two_books_list_two_summary_rows_and_the_selector_switches_the_sections(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _add_book_b(seeded_store)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    summary = _dataframe_with_columns(at, "book", "positions", "last_run")
    assert summary["book"].to_list() == ["b", "main"]
    assert summary["positions"].to_list() == [2, 1]
    assert summary["last_run"].to_list() == ["ok", "ok"]
    [selector] = [sb for sb in at.selectbox if sb.key == ops_page.BOOK_KEY]
    assert selector.options == ["b", "main"]
    assert selector.value == "main"  # `paper.book_id`, not the first token
    assert {m.label: str(m.value) for m in at.metric}["Positions"] == "1"
    alerts = _dataframe_with_columns(at, "kind", "message")
    assert alerts["message"].to_list() == ["SPY has no bar for S"]

    selector.set_value("b").run(timeout=30)
    assert not at.exception
    assert {m.label: str(m.value) for m in at.metric}["Positions"] == "2"
    assert "No alerts in this window." in _text(at)
    assert "No fills yet in this window." in _text(at)


def test_waiting_card_empty_before_books(monkeypatch: pytest.MonkeyPatch, risk_store: Path) -> None:
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    labels = [h.value for h in at.subheader]
    assert labels.index("Waiting on you") < labels.index("Books")
    assert re.search(r":green-badge\[[^\]]*Nothing is waiting on you", _text(at))


def test_waiting_card_covers_every_book_after_selection_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _add_book_b(seeded_store)
    settings = Settings(_env_file=None, store={"path": str(seeded_store)})
    with open_for_write(settings) as conn:
        window = journal.open_window(conn, "main")
        assert window is not None and window.window_id is not None
        append(
            conn,
            KillSwitchRow(
                window_id=window.window_id,
                at=_at(-20),
                state="engaged",
                source="owner",
                reason="test",
                **_stamp(-20),
            ),
        )
    before = seeded_store.read_bytes()
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    labels = [h.value for h in at.subheader]
    assert labels.index("Waiting on you") < labels.index("Books")
    text = _text(at)
    assert re.search(r":red-badge\[[^\]]*main: kill_switch_engaged", text)
    assert re.search(r":orange-badge\[[^\]]*b: unreadable", text)
    assert any("tradepartner paper resume --book main" in code.value for code in at.code)
    [selector] = [sb for sb in at.selectbox if sb.key == ops_page.BOOK_KEY]
    selector.set_value("b").run(timeout=30)
    assert not at.exception
    assert "main: kill_switch_engaged" in _text(at)
    assert "b: unreadable" in _text(at)
    assert seeded_store.read_bytes() == before


def test_waiting_card_shows_a_secret_in_the_kill_reason_scrubbed(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    secret = "PKREALSECRETVALUE1234567890"  # gitleaks:allow
    configured = "correct horse battery staple 99"  # no pattern catches this one
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", configured)
    settings = Settings(_env_file=None, store={"path": str(seeded_store)})
    with open_for_write(settings) as conn:
        window = journal.open_window(conn, "main")
        assert window is not None and window.window_id is not None
        append(
            conn,
            KillSwitchRow(
                window_id=window.window_id,
                at=_at(-20),
                state="engaged",
                source="owner",
                reason=f"stop key {secret} pw {configured}",
                **_stamp(-20),
            ),
        )
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    text = _text(at)
    assert "reason given: stop key" in text
    assert secret not in text and configured not in text


# --- the Risk card (plan T260, amendment 2026-10-20 #2096 item 3) -----------------

#: Frozen limits 0.05 cash, 1.0 gross, 0.50 position, 0.30 drawdown.
_RISK_LIMITS: dict[str, float] = {
    "max_unspent_cash_fraction": 0.05,
    "max_gross_exposure": 1.0,
    "max_position_weight": 0.50,
    "max_drawdown": 0.30,
}


def _risk_frozen_json() -> str:
    risk = RiskConfig(**_RISK_LIMITS)  # type: ignore[arg-type]
    return json.dumps(
        {f"risk.{k}": v for k, v in risk.model_dump(mode="json").items()}, sort_keys=True
    )


def _register_hypothesis(conn: duckdb.DuckDBPyConnection) -> int:
    holdout_start, holdout_end = date(2018, 7, 2), date(2019, 3, 29)
    params = Settings(
        _env_file=None,
        holdout={"start": holdout_start.isoformat(), "end": holdout_end.isoformat()},
    )
    return registry.register_hypothesis(
        conn,
        slug="h-ops-risk",
        family="momentum",
        title="ops risk card test",
        doc_path="docs/hypotheses/h-ops-risk.md",
        doc_sha256="0" * 64,
        params=frozen_params_of(params, family="momentum"),
        in_sample_start=date(2017, 1, 3),
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        registered_by="test",
        settings=params,
    ).hypothesis_id


def _make_risk_readable(store_path: Path, *, marks: bool = True) -> None:
    """The seeded window with a readable frozen `risk.*`, a registered hypothesis,
    starting equity 110,000 and, when `marks`, S-1 marked at cash 4,000, A 56,000
    and B 40,000 (equity 100,000) in place of the seed's cash-less mark. With
    `marks` false, the seed's cash-less mark is left (an unreadable mark)."""
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        hypothesis_id = _register_hypothesis(conn)
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ?, hypothesis_id = ?, "
            "starting_cash = 110000, starting_equity = 110000 WHERE book_id = 'main'",
            [_risk_frozen_json(), hypothesis_id],
        )
        if not marks:
            return
        row = conn.execute("SELECT run_id FROM positions_daily").fetchone()
        assert row is not None
        conn.execute("DELETE FROM positions_daily")
        for security_id, value in (("AAA1", 56_000.0), ("BBB", 40_000.0)):
            append(
                conn,
                PositionDailyRow(
                    run_id=int(row[0]),
                    session=_S_MINUS_1,
                    security_id=security_id,
                    quantity=value / 100.0,
                    mark_price=100.0,
                    value=value,
                    cash=4_000.0,
                    **_stamp(6),
                ),
            )
        conn.execute(
            "INSERT INTO listings (security_id, ticker, exchange, valid_from, known_at, "
            "ingested_at, source, provenance) VALUES ('AAA1', 'AAA', 'NYSE', ?, ?, ?, "
            "'fixture', 'filing')",
            [_S_MINUS_1, _at(1), _at(1)],
        )


@pytest.fixture
def risk_store(seeded_store: Path) -> Path:
    _make_risk_readable(seeded_store)
    return seeded_store


def _risk_table(at: AppTest) -> Any:
    return _dataframe_with_columns(at, "limit", "value", "frozen limit", "headroom", "status")


def test_risk_card_shows_the_hand_values_against_the_frozen_limits(
    monkeypatch: pytest.MonkeyPatch, risk_store: Path
) -> None:
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    table = _risk_table(at)
    assert table["limit"].to_list() == ["Cash", "Gross exposure", "Largest position", "Drawdown"]
    assert table["value"].to_list() == ["4.00%", "0.96", "AAA 0.56", "9.09%"]
    assert table["frozen limit"].to_list() == ["5.00%", "1.00", "0.50", "30.00%"]
    assert table["headroom"].to_list() == [
        "1.00 pp left",
        "4.00 pp left",
        "over by 6.00 pp",
        "20.91 pp left",
    ]
    assert table["status"].to_list() == ["within", "within", "at or over", "within"]
    assert "drift alone never halts" in table["what the limit does"].to_list()[2]
    text = _text(at)
    assert f"as of the close of {_S_MINUS_1.isoformat()}" in text
    assert "$23,000.00" in text
    assert "ledger's cash" in text
    # The over-the-limit position is a warning chip, not critical; never colour alone.
    assert re.search(r":orange-badge\[[^\]]*Largest position: at or over", text)
    assert not re.search(r":red-badge\[[^\]]*Largest position", text)
    assert re.search(r":green-badge\[[^\]]*Drawdown: within", text)


def test_risk_card_after_a_release_reads_the_releases_peak(
    monkeypatch: pytest.MonkeyPatch, risk_store: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(risk_store)})
    with open_for_write(settings) as conn:
        window = journal.open_window(conn, "main")
        assert window is not None and window.window_id is not None
        append(
            conn,
            KillSwitchRow(
                window_id=window.window_id,
                at=_at(-50),
                state="released",
                source="owner",
                reason="resumed",
                peak_equity=120_000.0,
                **_stamp(-50),
            ),
        )
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    assert _risk_table(at)["value"].to_list()[3] == "16.67%"
    assert "$16,000.00" in _text(at)


def test_risk_card_shows_the_frozen_limits_when_live_settings_differ(
    monkeypatch: pytest.MonkeyPatch, risk_store: Path
) -> None:
    monkeypatch.setenv("RISK__MAX_POSITION_WEIGHT", "0.90")
    monkeypatch.setenv("RISK__MAX_DRAWDOWN", "0.10")
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    assert _risk_table(at)["frozen limit"].to_list() == ["5.00%", "1.00", "0.50", "30.00%"]


def test_risk_card_before_the_first_mark(monkeypatch: pytest.MonkeyPatch, risk_store: Path) -> None:
    settings = Settings(_env_file=None, store={"path": str(risk_store)})
    with open_for_write(settings) as conn:
        conn.execute("DELETE FROM positions_daily")
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    table = _risk_table(at)
    assert table["value"].to_list() == ["100.00%", "0.00", "none", "0.00%"]
    text = _text(at)
    assert "Before the first mark" in text
    assert "110,000.00" in text


def test_risk_card_shows_an_unreadable_mark_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    _make_risk_readable(seeded_store, marks=False)
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    text = _text(at)
    assert "Risk limits unreadable" in text
    assert "has no cash" in text


def test_risk_card_shows_an_unreadable_frozen_json_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, seeded_store: Path
) -> None:
    # The seed's window freezes `{}`: `frozen_risk` refuses it.
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    text = _text(at)
    assert "Risk limits unreadable" in text
    assert "frozen_json lacks risk keys" in text
    # The rest of the page still draws.
    assert "Signal ranking" in text


def test_risk_card_flags_a_long_short_window(
    monkeypatch: pytest.MonkeyPatch, risk_store: Path
) -> None:
    monkeypatch.setattr(headroom, "_side", lambda _conn, _window: "long_short")
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    assert re.search(r":orange-badge\[[^\]]*short limits not shown yet", _text(at))


def test_risk_card_shows_an_engaged_drawdown_as_critical(
    monkeypatch: pytest.MonkeyPatch, risk_store: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(risk_store)})
    with open_for_write(settings) as conn:
        window = journal.open_window(conn, "main")
        assert window is not None and window.window_id is not None
        append(
            conn,
            KillSwitchRow(
                window_id=window.window_id,
                at=_at(-50),
                state="engaged",
                source="drawdown",
                reason="drawdown",
                **_stamp(-50),
            ),
        )
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    assert _risk_table(at)["status"].to_list()[3] == "engaged"
    assert re.search(r":red-badge\[[^\]]*Drawdown: engaged", _text(at))


def test_risk_card_render_writes_nothing(monkeypatch: pytest.MonkeyPatch, risk_store: Path) -> None:
    def counts() -> dict[str, int]:
        with duckdb.connect(str(risk_store), read_only=True) as conn:
            tables = [
                str(r[0])
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                ).fetchall()
            ]
            return {
                t: int(conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0])  # type: ignore[index]
                for t in tables
            }

    before = counts()
    at = _app(monkeypatch, risk_store)
    assert not at.exception
    assert _risk_table(at) is not None
    assert counts() == before
