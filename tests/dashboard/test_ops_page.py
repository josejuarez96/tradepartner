"""Tests for the operations page (Phase 4 T69, spec req 12; ADR 0011).

Driven headless through `streamlit.testing.v1.AppTest` on the shell, as
`test_trials_page.py` does, over a temp store seeded only through
`store.journal`. `execution.ops.page_data` is T66b's own module and has its
own tests (`tests/execution/test_ops.py`); here the page is exercised as the
thin read-only draw over it.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import version_4_store
from streamlit.testing.v1 import AppTest

from tradepartner.calendar import next_session
from tradepartner.config import Settings
from tradepartner.dashboard import ops_page, theme
from tradepartner.execution import ops
from tradepartner.store import schema
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.journal import (
    AlertRow,
    DecisionRow,
    FillRow,
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


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    # Freeze the clock the page reads to the instant S-1 was derived from, so a
    # slow shard whose wall clock crosses a session boundary between import and
    # render cannot advance the required session past the fixture's run (#1263).
    monkeypatch.setattr(ops, "utc_now", lambda: _NOW)
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
        "Override",
    ]


def test_render_journal_not_initialised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = version_4_store(tmp_path / "v4.duckdb")
    at = _app(monkeypatch, store_path)
    assert not at.exception
    text = _text(at).lower()
    assert "journal not initialised" in text
    assert "version 4" not in text


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
    rows = _dataframe_with_columns(at, "security_id", "selected", "signal_reason")
    assert set(rows["security_id"].to_list()) == {"BBB", "CCC"}
    by_security = rows.set_index("security_id")
    assert bool(by_security.loc["BBB", "selected"]) is True
    assert bool(by_security.loc["CCC", "selected"]) is False


def test_render_fills_card(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    fills = _dataframe_with_columns(at, "broker_fill_id", "quantity", "price")
    assert fills["broker_fill_id"].to_list() == ["brk-1"]


def test_render_chain_detail_view(monkeypatch: pytest.MonkeyPatch, seeded_store: Path) -> None:
    at = _app(monkeypatch, seeded_store)
    assert not at.exception
    [picker] = at.selectbox
    assert picker.options == ["tp-buy-1"]
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
