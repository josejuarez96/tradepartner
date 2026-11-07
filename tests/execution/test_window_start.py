"""Tests for `execution.window.start` (Phase 4 spec req 14 "Entry gate, start
and stop"; ADR 0009 point 3; plan T64).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.adapters.alpaca_prices import parse_corporate_actions
from tradepartner.adapters.broker import (
    Account,
    CashActivity,
    Fill,
    Order,
    OrderRequest,
    Position,
    Side,
)
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.adapters.prices import announcement_date_known_at
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import FROZEN_COSTS_KEYS, FROZEN_EXECUTION_KEYS, Settings
from tradepartner.execution import exam as exam_rules
from tradepartner.execution import exam_ledger, lock, report, window
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.execution.reconcile_run import frozen_risk
from tradepartner.ingest import _action_row
from tradepartner.store import registry, schema
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import insert_row, open_for_write, open_read_only
from tradepartner.store.journal import (
    AdjustmentRow,
    PaperWindowRow,
    PaperWindowStopRow,
    adjustments_for,
    append,
    fills_for,
    latest_window,
    orders_for,
)


class FixedClock(Protocol):
    """What `start` needs from the `fixed_clock` fixture: a callable clock."""

    def __call__(self) -> datetime: ...


ACCOUNT_ID = "PA1"
REFERENCE_PRICE = 100.0

#: The fixture universe lists SEC_SPY/SPY and SEC_MTUM/MTUM from 2018-01-02
#: with no delisting (tests/fixtures/universe); SEC_SPLIT_PLAIN/SPLT also has
#: no delisting and is reused here as a stand-in spin-off child.
SPY = "SEC_SPY"
MTUM = "SEC_MTUM"
CHILD = "SEC_SPLIT_PLAIN"
HOLDOUT_END_PAST = date(2026, 9, 30)  # a completed month-end at the conftest clock's start
HOLDOUT_END_FUTURE = date(2026, 12, 31)  # not yet completed there


def _connect(
    settings: Settings,
) -> Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]:
    return lambda: open_read_only(settings)


def _params(**extra: Any) -> dict[str, Any]:
    return {
        "costs.per_side_bps": 15.0,
        "costs.commission_per_share": 0.0,
        "costs.commission_per_order": 0.0,
        "execution.fill_price": "close",
        "strategy.top_fraction": 0.1,
        **extra,
    }


def _register(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    slug: str,
    holdout_end: date,
    params: dict[str, Any] | None = None,
    family: str = "momentum",
    holdout_start: date = date(2023, 1, 3),
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family=family,
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params=_params() if params is None else params,
        in_sample_start=date(2016, 1, 29),
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        registered_by="owner",
        settings=settings,
    )


def _sign_off(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    hyp: registry.HypothesisRecord,
    tmp_path: Path,
    *,
    kind: registry.TrialKind = "in_sample",
    synthetic: bool = False,
    real: bool = True,
) -> int:
    """Open a trial for `hyp`, close it `ok`, sign off its gap and return the
    trial's id.

    `real=False` opens the trial under a `settings` whose `store.path`
    differs from `conn`'s file, so `open_trial`'s synthetic-on-the-real-store
    refusal does not apply (the trick `tests/store/test_registry.py` uses).
    """
    trial_settings = settings if real else Settings(_env_file=None, store={"path": "elsewhere"})
    handle = registry.open_trial(
        conn,
        hypothesis_id=hyp.hypothesis_id,
        kind=kind,
        start_session=date(2018, 1, 31),
        end_session=date(2022, 12, 30),
        data_cutoff=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        synthetic=synthetic,
        run_by="test",
        settings=trial_settings,
        repo_dir=tmp_path,
    )
    assert registry.write_result(conn, handle, registry.ResultStatistics()) == "ok"
    registry.record_decision(
        conn,
        kind="gap_signoff",
        reason="accepted for the test",
        values={},
        hypothesis_id=hyp.hypothesis_id,
        trial_id=handle.trial_id,
    )
    return handle.trial_id


@pytest.fixture
def ready_hypothesis(journal_settings: Settings, tmp_path: Path) -> registry.HypothesisRecord:
    """A hypothesis with a gap_signoff on an ok, non-synthetic, in_sample
    trial and a completed `holdout.end`: the baseline every refusal test
    starts from and mutates one thing."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path)
    return hyp


def _fake(clock: FixedClock, **kwargs: Any) -> FakeBroker:
    kwargs.setdefault("assets", FakeBroker.active_assets("SPY", "MTUM", "SPLT", "SSP"))
    return FakeBroker(
        clock=clock,
        price_of=lambda _s: REFERENCE_PRICE,
        auto_fill=False,
        account_id=ACCOUNT_ID,
        **kwargs,
    )


class BookedFake(FakeBroker):
    """A fake whose positions a test can move directly (`test_reconcile_run`'s
    pattern), since seeding a residual holding through real fills would need
    a previous window's whole order flow."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.extra_quantity: dict[str, float] = {}

    def positions(self) -> dict[str, Position]:
        held = {s: p.quantity for s, p in super().positions().items()}
        for symbol, delta in self.extra_quantity.items():
            held[symbol] = held.get(symbol, 0.0) + delta
        return {s: Position(symbol=s, quantity=q) for s, q in held.items() if q != 0}


class CallCountingFake(FakeBroker):
    """A fake that records every broker call, so a refusal can show it made none."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.broker_calls: list[str] = []

    def account(self) -> Account:
        self.broker_calls.append("account")
        return super().account()

    def positions(self) -> dict[str, Position]:
        self.broker_calls.append("positions")
        return super().positions()

    def open_orders(self) -> list[Order]:
        self.broker_calls.append("open_orders")
        return super().open_orders()

    def fills(self, since: datetime | None = None) -> list[Fill]:
        self.broker_calls.append("fills")
        return super().fills(since)

    def submit(self, request: OrderRequest) -> Order:
        self.broker_calls.append("submit")
        return super().submit(request)

    def cancel(self, client_order_id: str) -> None:
        self.broker_calls.append("cancel")
        super().cancel(client_order_id)

    def get_order(self, client_order_id: str) -> Order:
        self.broker_calls.append("get_order")
        return super().get_order(client_order_id)


class FailingAccountFake(FakeBroker):
    def account(self) -> Account:
        raise RuntimeError("paper endpoint unreachable")


def _residues_json(entries: dict[str, tuple[float, str | None]]) -> str:
    return json.dumps(
        {name: {"quantity": qty, "origin": origin} for name, (qty, origin) in entries.items()}
    )


def _insert_action(
    settings: Settings,
    *,
    security_id: str,
    action_type: str,
    ex_date: date,
    ratio_or_amount: float,
    known_at: datetime,
    source_action_id: str = "",
) -> None:
    with open_for_write(settings) as conn:
        conn.execute(
            "INSERT INTO corporate_actions "
            "(security_id, action_type, ex_date, ratio_or_amount, source_action_id, "
            "known_at, ingested_at, source, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'test', 'action')",
            [
                security_id,
                action_type,
                ex_date,
                ratio_or_amount,
                source_action_id,
                known_at,
                known_at,
            ],
        )


def _write_closed_window(
    settings: Settings,
    hyp: registry.HypothesisRecord,
    *,
    at: datetime,
    residues: dict[str, tuple[float, str | None]],
    state: str = "closed",
    reason: str | None = None,
    book_id: str | None = None,
    account_id: str = ACCOUNT_ID,
) -> int:
    """A previous window of `book_id` (default `settings.paper.book_id`, the book
    `start` reads) on `account_id`, closed (or `state`) with `residues`."""
    with open_for_write(settings) as conn:
        row_id = append(
            conn,
            PaperWindowRow(
                hypothesis_id=hyp.hypothesis_id,
                first_rebalance_session=date(2026, 8, 31),
                account_id=account_id,
                book_id=settings.paper.book_id if book_id is None else book_id,
                starting_cash=100_000.0,
                starting_equity=100_000.0,
                code_version="prev",
                started_at=at - timedelta(days=60),
                frozen_json="{}",
                frozen_sha256="0" * 64,
                known_at=at - timedelta(days=60),
                ingested_at=at - timedelta(days=60),
            ),
        )
        assert row_id is not None
        append(
            conn,
            PaperWindowStopRow(
                window_id=row_id,
                at=at,
                state=state,
                reason=reason,
                residues_json=_residues_json(residues),
                known_at=at,
                ingested_at=at,
            ),
        )
    return row_id


# --- refusals ---------------------------------------------------------------


def test_refuses_sweep_variant_before_broker_call(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    from tradepartner.store import lab_schema

    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "variant", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path)
        lab_schema.apply_lab_schema(conn)
        conn.execute(
            "INSERT INTO sweep_variants VALUES (1, 1, ?, 'fingerprint', '{}')",
            [hyp.hypothesis_id],
        )
    broker = CallCountingFake(
        clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, auto_fill=False
    )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "variant")
    assert exc.value.reason == "sweep_variant"
    assert broker.broker_calls == []
    with open_read_only(journal_settings) as conn:
        assert conn.execute("SELECT count(*) FROM paper_windows").fetchone() == (0,)


def test_refuses_without_gap_signoff(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    with open_for_write(journal_settings) as conn:
        _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "gap_signoff"


def test_refuses_a_family_outside_paper_families(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate itself, with `PAPER_FAMILIES` narrowed back to momentum's pair: a
    family outside it is refused `family_not_runnable`, before any write."""
    monkeypatch.setattr(window, "PAPER_FAMILIES", ("momentum", "oracle"))
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "b3", HOLDOUT_END_PAST, family="profitability")
        _sign_off(conn, journal_settings, hyp, tmp_path)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "b3"
        )
    assert exc.value.reason == "family_not_runnable"
    assert "profitability" in str(exc.value)
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn) is None


@pytest.mark.parametrize("family", ["profitability", "combined"])
def test_accepts_a_family_paper_now_runs(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, family: str
) -> None:
    """`profitability` and `combined` are in `PAPER_FAMILIES` (ADR 0017 part D, T152):
    a signed-off hypothesis of either starts a window on its own registration."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "b3", HOLDOUT_END_PAST, family=family)
        _sign_off(conn, journal_settings, hyp, tmp_path)
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "b3"
    )
    assert result.window.hypothesis_id == hyp.hypothesis_id
    with open_for_write(journal_settings) as conn:
        opened = latest_window(conn)
        assert opened is not None and opened.window_id == result.window.window_id


def test_paper_start_records_the_seen_activity_ids(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """#1583, T183, spec item 1: `paper start` reads `cash_activities` after
    `account()` and writes `activities_seen_json` with the sorted ids it read (the
    booked-`FEE` seen-set); it is a DB column, not part of `frozen_sha256`."""
    activities = [
        CashActivity(
            activity_id="a-2",
            activity_type="FEE",
            date=date(2026, 9, 30),
            status="executed",
            net_amount=-0.01,
        ),
        CashActivity(
            activity_id="a-1",
            activity_type="SSP",
            date=date(2026, 9, 29),
            status="executed",
            net_amount=None,
        ),
    ]
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "b3", HOLDOUT_END_PAST, family="profitability")
        _sign_off(conn, journal_settings, hyp, tmp_path)
    result = window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock, activities=activities),
        fixed_clock,
        "b3",
    )

    seen = json.dumps(["a-1", "a-2"])
    assert result.window.activities_seen_json == seen
    with open_read_only(journal_settings) as conn:
        opened = latest_window(conn)
        assert opened is not None and opened.activities_seen_json == seen


def test_paper_start_reads_account_before_cash_activities(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """The read order is fixed at `paper start` too (#1583, item 1): the account
    is read before the activity list, so a fee inside `starting_cash` the activity
    read can see is in the seen-set."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "b3", HOLDOUT_END_PAST, family="profitability")
        _sign_off(conn, journal_settings, hyp, tmp_path)
    fake = _fake(fixed_clock)

    window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "b3")

    methods = [call.method for call in fake.calls]
    assert methods.index("account") < methods.index("cash_activities")


def test_paper_start_wraps_a_cash_activities_failure_as_start_refused(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """#1583, Q16: a `cash_activities()` failure is wrapped as `StartRefusedError`
    like an `account()` failure, so the endpoint name never reaches the owner."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "b3", HOLDOUT_END_PAST, family="profitability")
        _sign_off(conn, journal_settings, hyp, tmp_path)

    class FailingActivitiesFake(FakeBroker):
        def cash_activities(self, since: date) -> list[CashActivity]:  # type: ignore[override]
            raise ValueError("boom")

    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings,
            _connect(journal_settings),
            FailingActivitiesFake(
                clock=fixed_clock,
                price_of=lambda _s: REFERENCE_PRICE,
                auto_fill=False,
                account_id=ACCOUNT_ID,
            ),
            fixed_clock,
            "b3",
        )
    assert exc.value.reason == "activities_unavailable"


#: The conftest clock (2026-10-01, a Thursday) is mid-week: the first week-end and the
#: first session strictly after it are both Friday 2026-10-02.
NEXT_WEEK_END = date(2026, 10, 2)
#: A completed week-end at the conftest clock (Friday 2026-09-25; 2026-09-30 is not one).
WEEK_END_PAST = date(2026, 9, 25)


@pytest.mark.parametrize(
    ("cadence", "holdout_end", "t_0", "min_rebalances"),
    [
        pytest.param("week_end", WEEK_END_PAST, NEXT_WEEK_END, 13, id="week_end"),
        pytest.param("daily", HOLDOUT_END_PAST, date(2026, 10, 2), 63, id="daily"),
    ],
)
def test_accepts_a_week_end_and_a_daily_registration(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    cadence: str,
    holdout_end: date,
    t_0: date,
    min_rebalances: int,
) -> None:
    """ADR 0017 part C (strategy-lab spec req 11 as amended 2026-10-09): a `week_end`
    and a `daily` hypothesis start; T_0 is the first rebalance session at that cadence
    strictly after both `holdout.end` and today, and the window freezes that cadence's
    `paper.min_rebalances` as a scalar under the same key."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(
            conn,
            journal_settings,
            "h1",
            holdout_end,
            params=_params(**{"schedule.rebalance_cadence": cadence}),
        )
        _sign_off(conn, journal_settings, hyp, tmp_path)
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.window_id is not None
    assert result.window.first_rebalance_session == t_0
    frozen = json.loads(result.window.frozen_json)
    assert frozen["paper.min_rebalances"] == min_rebalances
    # One k at every cadence (ADR 0017 open question 6).
    assert frozen["paper.tracking_k"] == 2.0


def test_sandbox_now_starts_from_the_previous_stored_close(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lock, "_temp_roots", lambda: {tmp_path.resolve()})
    lock.sandbox_marker(journal_settings).write_text("")
    with open_for_write(journal_settings) as conn:
        hyp = _register(
            conn,
            journal_settings,
            "now",
            date(2026, 8, 31),
            params=_params(**{"schedule.rebalance_cadence": "daily"}),
        )
        _sign_off(conn, journal_settings, hyp, tmp_path)
        conn.execute(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, "
            "volume, known_at, ingested_at, source, provenance) "
            "VALUES ('SEC_SPY', '2026-09-30', 100, 100, 100, 100, 1000, ?, ?, 'test', 'bar')",
            [datetime(2026, 9, 30, 20, tzinfo=UTC)] * 2,
        )
    result = window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock),
        fixed_clock,
        "now",
        book_id="sandbox",
        now=True,
    )
    assert result.window.first_rebalance_session == date(2026, 9, 30)
    assert fill_session(result.window.first_rebalance_session, "daily") == date(2026, 10, 1)


@pytest.mark.parametrize("case", ["wrong_cadence", "missing_close", "late_close", "post_close"])
def test_sandbox_now_refuses_without_a_known_daily_prior_close(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    monkeypatch.setattr(lock, "_temp_roots", lambda: {tmp_path.resolve()})
    lock.sandbox_marker(journal_settings).write_text("")
    cadence = "month_end" if case == "wrong_cadence" else "daily"
    with open_for_write(journal_settings) as conn:
        hyp = _register(
            conn,
            journal_settings,
            "now-refused",
            date(2026, 8, 31),
            params=_params(**{"schedule.rebalance_cadence": cadence}),
        )
        _sign_off(conn, journal_settings, hyp, tmp_path)
        if case in ("late_close", "post_close"):
            stamp = (
                fixed_clock() + timedelta(days=1)
                if case == "late_close"
                else datetime(2026, 9, 30, 21, tzinfo=UTC)
            )
            conn.execute(
                "INSERT INTO prices_daily (security_id, session, open, high, low, close, "
                "volume, known_at, ingested_at, source, provenance) "
                "VALUES ('SEC_SPY', '2026-09-30', 100, 100, 100, 100, 1000, ?, ?, 'test', 'bar')",
                [stamp, stamp],
            )
    reason = "now_cadence" if case == "wrong_cadence" else "now_data"
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings,
            _connect(journal_settings),
            _fake(fixed_clock),
            fixed_clock,
            "now-refused",
            book_id="sandbox",
            now=True,
        )
    assert exc.value.reason == reason
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn, "sandbox") is None


@pytest.mark.parametrize("book", ["main", "daily"])
def test_now_refuses_real_books_before_a_write(
    journal_settings: Settings, fixed_clock: FixedClock, book: str
) -> None:
    with pytest.raises(ValueError, match="sandbox"):
        window.start(
            journal_settings,
            _connect(journal_settings),
            _fake(fixed_clock),
            fixed_clock,
            "missing",
            book_id=book,
            now=True,
        )
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn, book) is None


def test_now_refuses_unmarked_store_before_a_write(
    journal_settings: Settings, fixed_clock: FixedClock
) -> None:
    with pytest.raises(ValueError, match="temp copy"):
        window.start(
            journal_settings,
            _connect(journal_settings),
            _fake(fixed_clock),
            fixed_clock,
            "missing",
            book_id="sandbox",
            now=True,
        )
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn, "sandbox") is None


def test_accepts_an_operations_book_after_a_signoff_on_its_own_trial(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """Paper plan T160b: on a lab store, a `daily` operations file (an
    `operations_book` decision names it; no promotion row) starts after a gap sign-off
    on its own `ok` in-sample trial, T_0 and `paper.min_rebalances` at `daily`; and
    H1's `month_end` window on book `main`, started on the same store, freezes exactly
    what it freezes on a store without the operations row."""
    from tradepartner.store import lab_registry, lab_schema

    def h1_frozen() -> tuple[date, str, str]:
        result = window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
        opened = result.window
        with open_for_write(journal_settings) as conn:
            conn.execute("DELETE FROM paper_windows")
        return opened.first_rebalance_session, opened.book_id, opened.frozen_json

    with open_for_write(journal_settings) as conn:
        h1 = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, h1, tmp_path)
    before = h1_frozen()
    with open_for_write(journal_settings) as conn:
        lab_schema.apply_lab_schema(conn)
        ops = _register(
            conn,
            journal_settings,
            "ops-book",
            HOLDOUT_END_PAST,
            params=_params(**{"schedule.rebalance_cadence": "daily"}),
        )
        registry.record_decision(
            conn,
            kind="operations_book",
            reason="machine test",
            values={"variant_hypothesis_id": 99},
            hypothesis_id=ops.hypothesis_id,
        )
        assert lab_registry.promotion_for(conn, ops.hypothesis_id) is None
        _sign_off(conn, journal_settings, ops, tmp_path)
    assert h1_frozen() == before
    assert before[1] == "main"
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "ops-book"
    )
    assert result.window.hypothesis_id == ops.hypothesis_id
    assert result.window.first_rebalance_session == date(2026, 10, 2)
    assert json.loads(result.window.frozen_json)["paper.min_rebalances"] == 63


def test_refuses_a_week_end_registration_whose_holdout_end_is_not_a_week_end(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """The holdout-end check runs at the hypothesis's cadence: Wednesday 2026-09-30 is a
    completed month-end but not a week-end, so a `week_end` registration ending there is
    refused `holdout_not_complete`, before any broker call."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(
            conn,
            journal_settings,
            "h1",
            HOLDOUT_END_PAST,
            params=_params(**{"schedule.rebalance_cadence": "week_end"}),
        )
        _sign_off(conn, journal_settings, hyp, tmp_path)
    broker = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert exc.value.reason == "holdout_not_complete"
    assert str(exc.value) == ("'h1''s frozen holdout.end (2026-09-30) is not a completed week-end")
    assert broker.calls == ()


@pytest.mark.parametrize("stored", [{"schedule.rebalance_cadence": "month_end"}, {}])
def test_accepts_a_month_end_and_a_pre_lab_registration(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, stored: dict[str, Any]
) -> None:
    """A `month_end` registration and one without `schedule.*` keys (a pre-lab
    registration, read as `month_end` through `frozen_values`) both start, at the
    first month-end, freezing `paper.min_rebalances` 6 as before ADR 0017."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST, params=_params(**stored))
        _sign_off(conn, journal_settings, hyp, tmp_path)
    assert ("schedule.rebalance_cadence" in hyp.params) == bool(stored)
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.window_id is not None
    assert result.window.first_rebalance_session == date(2026, 10, 30)
    frozen = json.loads(result.window.frozen_json)
    assert frozen["paper.min_rebalances"] == 6
    assert frozen["paper.tracking_k"] == 2.0


def test_refuses_on_synthetic_signoff_trial(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path, synthetic=True, real=False)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "gap_signoff"


def test_refuses_on_holdout_trial_signoff(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path, kind="holdout")
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "gap_signoff"


def _close_release(
    conn: duckdb.DuckDBPyConnection,
    name: str = "repair-a",
    sessions_from: date = date(2018, 1, 2),
    sessions_to: date = date(2022, 12, 30),
) -> None:
    """Open and close a named data release (the store stands in for its own
    backup, as `tests/store/test_registry.py` does)."""
    registry.open_data_release(
        conn, conn, name=name, backup_path=f"data/tradepartner.{name}.duckdb", reason="#1 fix"
    )
    registry.close_data_release(
        conn, name=name, sessions_from=sessions_from, sessions_to=sessions_to
    )


def test_refuses_a_signoff_trial_from_before_the_latest_release(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """#3178 (a): a sign-off on a trial that read the store before the latest
    closed release reached its window is refused, naming the release."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path)
        _close_release(conn, "repair-monday")
    broker = CallCountingFake(
        clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, auto_fill=False
    )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert exc.value.reason == "gap_signoff"
    assert "repair-monday" in str(exc.value)
    assert broker.broker_calls == []


def test_accepts_a_signoff_trial_run_after_the_latest_release(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """#3178 (b): a sign-off on a trial opened after the release closed passes,
    beside an older sign-off on a pre-release trial."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path)
        _close_release(conn, "repair-monday")
        _sign_off(conn, journal_settings, hyp, tmp_path)
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.window_id is not None


def test_accepts_a_pre_release_trial_the_release_did_not_reach(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A release whose touched sessions all start after the trial's data cutoff
    leaves the trial's read unchanged (`data_vintage`'s release rule), so its
    sign-off still passes."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, journal_settings, hyp, tmp_path)  # cutoff 2026-09-25
        _close_release(
            conn, "repair-later", sessions_from=date(2026, 10, 1), sessions_to=date(2026, 10, 2)
        )
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.window_id is not None


def test_a_store_without_a_release_keeps_the_old_rule_even_without_stamps(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """#3178 (c): no closed release, no new check: a sign-off trial with no
    recorded data vintage still passes, as before."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        trial_id = _sign_off(conn, journal_settings, hyp, tmp_path)
        conn.execute("UPDATE trials SET data_vintage = NULL WHERE trial_id = ?", [trial_id])
        assert registry.data_releases(conn) == []
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.window_id is not None


@pytest.mark.parametrize("column", ["data_vintage", "data_cutoff"])
def test_refuses_a_signoff_trial_missing_its_stamp_once_a_release_exists(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, column: str
) -> None:
    """#3178 (d): with a closed release on the store, a sign-off trial with no
    recorded `data_vintage` or `data_cutoff` cannot show it read the released
    store, so it fails closed, even when opened after the release."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST)
        _close_release(conn, "repair-monday")
        trial_id = _sign_off(conn, journal_settings, hyp, tmp_path)
        conn.execute(f"UPDATE trials SET {column} = NULL WHERE trial_id = ?", [trial_id])
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "gap_signoff"
    assert "repair-monday" in str(exc.value)


def test_refuses_incomplete_holdout_end(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_FUTURE)
        _sign_off(conn, journal_settings, hyp, tmp_path)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "holdout_not_complete"


def test_refuses_account_unavailable(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    fake = FailingAccountFake(
        clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID
    )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "account_unavailable"


def test_refuses_while_window_open(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    now = fixed_clock()
    with open_for_write(journal_settings) as conn:
        append(
            conn,
            PaperWindowRow(
                hypothesis_id=ready_hypothesis.hypothesis_id,
                first_rebalance_session=date(2026, 10, 30),
                account_id=ACCOUNT_ID,
                starting_cash=1.0,
                starting_equity=1.0,
                code_version="x",
                started_at=now,
                frozen_json="{}",
                frozen_sha256="0" * 64,
                known_at=now,
                ingested_at=now,
            ),
        )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "window_open"


def test_refuses_open_orders(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    fake = _fake(fixed_clock)
    fake.submit(OrderRequest(client_order_id="o1", symbol="SPY", side=Side.BUY, quantity=1))
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "open_orders"


def test_refuses_residue_quantity_mismatch(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={SPY: (5.0, "dust")},
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 50.0  # far outside the frozen tolerance
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_unexplained_symbol(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={SPY: (5.0, "dust")},
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 5.0
    fake.extra_quantity["MTUM"] = 3.0  # held, never listed as a residue
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_non_flat_after_abandoned_window(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={SPY: (5.0, "dust")},
        state="abandoned",
        reason="owner reset the account after a fault",
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 5.0  # exactly the abandoned row's own listing: still refused
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_while_run_holds_lock(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    with run_lock(journal_settings), pytest.raises(LockHeld):
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )


# --- acceptance --------------------------------------------------------------


def test_accepts_with_residues_carried_and_records_starting_values(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    stop_at = fixed_clock() - timedelta(days=1)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (5.0, "dust"), MTUM: (2.0, "untradable")},
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 5.0
    fake.extra_quantity["MTUM"] = 2.0
    account = fake.account()

    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None

    assert result.window.starting_cash == account.cash
    assert result.window.starting_equity == account.equity
    assert result.window.account_id == ACCOUNT_ID
    assert result.window.book_id == "main"
    assert result.abandoned_note is None

    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    carried = {a.security_id: a for a in adjustments if a.kind == "carried_residue"}
    assert carried[SPY].quantity == 5.0
    assert carried[SPY].origin == "dust"
    assert carried[SPY].session == stop_at.date()
    assert carried[MTUM].quantity == 2.0
    assert carried[MTUM].origin == "untradable"
    assert {a.book_id for a in adjustments} == {"main"}


def test_a_non_default_book_id_flows_to_the_window_and_its_adjustments(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """ADR 0015 seam 1 (T133): `paper start` reads `settings.paper.book_id`
    once into the window row, and the window's adjustments carry it. The live
    key is not frozen (`frozen_json`/`frozen_sha256` unchanged)."""
    settings = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        paper={"book_id": "fx"},
    )
    stop_at = fixed_clock() - timedelta(days=1)
    _write_closed_window(
        settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (5.0, "dust")},
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 5.0

    result = window.start(settings, _connect(settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None
    assert result.window.book_id == "fx"
    assert "paper.book_id" not in result.window.frozen_json

    with open_read_only(settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    carried = [a for a in adjustments if a.kind == "carried_residue"]
    assert carried and {a.book_id for a in carried} == {"fx"}


def test_accepted_window_s_first_reconciliation_passes(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """The ledger the new window implies (fills: none; adjustments: the
    carried residue) matches the live broker within the frozen tolerance,
    which is what `reconcile_now`'s first call would need to pass."""
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={SPY: (5.0, "dust")},
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 5.0
    account_before = fake.account()

    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None

    with open_read_only(journal_settings) as conn:
        fills = fills_for(conn, window_id=window_id)
        orders = orders_for(conn, window_id=window_id)
        adjustments = adjustments_for(conn, window_id=window_id)
        actions = live_actions_as_of(conn, fixed_clock())
    ledger = from_journal(
        fills,
        orders,
        adjustments,
        actions,
        None,
        result.window.starting_cash,
        result.window.first_rebalance_session,
        window_id=window_id,
        quantity_tolerance=journal_settings.risk.reconcile_quantity_tolerance,
    )
    assert ledger.cash == account_before.cash
    assert ledger.positions.get(SPY, 0.0) == pytest.approx(5.0)
    assert fake.positions()["SPY"].quantity == pytest.approx(ledger.positions[SPY])


def test_accepts_spinoff_child_of_a_residue(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """ADR 0015 seam 1 (T133): the spin-off receipt `AdjustmentRow` carries the
    window's book. A non-default book makes a missing `book_id` fail."""
    settings = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        paper={"book_id": "fx"},
    )
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (10.0, "dust")},
    )
    # The module's documented convention: a `spinoff` corporate_actions row
    # keyed by the child, with `source_action_id` naming the parent.
    ex_date = (stop_at + timedelta(days=2)).date()
    process_date = (stop_at + timedelta(days=4)).date()
    known_at = announcement_date_known_at(process_date)
    _insert_action(
        settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=ex_date,
        ratio_or_amount=0.2,
        known_at=known_at,
        source_action_id=SPY,
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0  # 10 * 0.2

    result = window.start(settings, _connect(settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None

    with open_read_only(settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    receipts = [a for a in adjustments if a.kind == "spinoff_receipt"]
    assert len(receipts) == 1
    assert receipts[0].security_id == CHILD
    assert receipts[0].quantity == pytest.approx(2.0)
    assert receipts[0].origin is None
    assert receipts[0].book_id == "fx"


def _start_with_spinoff_child(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
    *,
    allocation_offset: timedelta | None,
) -> tuple[AdjustmentRow, date]:
    """`start` over a 10 `SPY` residue whose 0.2 `SPLT` child sits at the broker.
    An allocation of 0.2 is stored with `known_at` = close(ex_date - 1) plus
    `allocation_offset`, when it is not None. Returns the receipt and the
    ex-date."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings, ready_hypothesis, at=stop_at, residues={SPY: (10.0, "dust")}
    )
    ex_date = (stop_at + timedelta(days=2)).date()
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=ex_date,
        ratio_or_amount=0.2,
        known_at=announcement_date_known_at((stop_at + timedelta(days=4)).date()),
        source_action_id=SPY,
    )
    if allocation_offset is not None:
        known = session_close(previous_session(ex_date)) + allocation_offset
        with open_for_write(journal_settings) as conn:
            conn.execute(
                "INSERT INTO spinoff_allocations VALUES (?, ?, ?, 0.2, ?, ?, 'owner', 'owner')",
                [SPY, CHILD, ex_date, known, known],
            )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0
    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None
    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    [receipt] = [a for a in adjustments if a.kind == "spinoff_receipt"]
    return receipt, ex_date


def test_the_residue_spinoff_receipt_names_its_parent_and_a_stored_allocation(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """T255 (#1976 item 2): the same keys `reconcile_run` writes; the allocation
    stored with `known_at` = close(ex_date - 1) is read."""
    receipt, ex_date = _start_with_spinoff_child(
        journal_settings, fixed_clock, ready_hypothesis, allocation_offset=timedelta(0)
    )

    terms = json.loads(receipt.explanation_json or "{}")
    assert terms["parent_security_id"] == SPY
    assert terms["basis_allocation"] == 0.2
    assert terms["ex_date"] == ex_date.isoformat()


def test_the_residue_spinoff_receipt_without_an_allocation_names_the_parent_alone(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """No allocation stored: the receipt names the parent only (zero basis)."""
    receipt, _ = _start_with_spinoff_child(
        journal_settings, fixed_clock, ready_hypothesis, allocation_offset=None
    )
    assert json.loads(receipt.explanation_json or "{}")["parent_security_id"] == SPY
    assert "basis_allocation" not in json.loads(receipt.explanation_json or "{}")


def test_the_residue_spinoff_receipt_ignores_an_allocation_known_after_the_cutoff(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    receipt, _ = _start_with_spinoff_child(
        journal_settings, fixed_clock, ready_hypothesis, allocation_offset=timedelta(minutes=1)
    )
    assert "basis_allocation" not in json.loads(receipt.explanation_json or "{}")


def test_frozen_hash_stable_across_an_environment_override(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake(fixed_clock)
    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    frozen_sha_before = result.window.frozen_sha256

    monkeypatch.setenv("RISK__MAX_DRAWDOWN", "0.01")
    changed = Settings(_env_file=None, store={"path": journal_settings.store.path})
    assert changed.risk.max_drawdown == 0.01  # the override took effect live

    # The window on disk is untouched by the live override: re-read it.
    with open_read_only(journal_settings) as conn:
        stored = latest_window(conn)
    assert stored is not None
    assert stored.frozen_sha256 == frozen_sha_before


def test_frozen_json_carries_the_registered_cost_keys_the_wrapper_reads(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """`paper start` freezes `FROZEN_COSTS_KEYS` under `costs.` (#534), equal to
    the hypothesis's registered costs, and no other `costs.*` key."""
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )

    frozen = json.loads(result.window.frozen_json)
    costs = {k: v for k, v in frozen.items() if k.startswith("costs.")}
    assert costs == {k: v for k, v in ready_hypothesis.params.items() if k.startswith("costs.")}
    assert {k.removeprefix("costs.") for k in costs} == set(FROZEN_COSTS_KEYS)


@pytest.mark.parametrize("fill_price", ["close", "open"])
def test_frozen_json_carries_the_live_execution_fill_price(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    fill_price: str,
) -> None:
    """`paper start` freezes `FROZEN_EXECUTION_KEYS` under `execution.` with the
    live values, equal to the registered ones (#526, #366 Q20), and no other
    `execution.*` key."""
    settings = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        execution={"fill_price": fill_price},
    )
    with open_for_write(settings) as conn:
        params = _params(**{"execution.fill_price": fill_price})
        hyp = _register(conn, settings, "h1", HOLDOUT_END_PAST, params)
        _sign_off(conn, settings, hyp, tmp_path)

    result = window.start(settings, _connect(settings), _fake(fixed_clock), fixed_clock, "h1")

    frozen = json.loads(result.window.frozen_json)
    execution = {k: v for k, v in frozen.items() if k.startswith("execution.")}
    assert execution == {"execution.fill_price": fill_price}
    assert {k.removeprefix("execution.") for k in execution} == set(FROZEN_EXECUTION_KEYS)


def test_report_reads_the_fill_price_a_started_window_froze(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """`paper report`'s `_frozen_fill_price` reads the value `paper start` wrote,
    from the stored row, so a real window no longer raises `ValueError` (#526)."""
    window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    with open_read_only(journal_settings) as conn:
        stored = latest_window(conn)
    assert stored is not None

    assert report._frozen_fill_price(stored) == journal_settings.execution.fill_price


@pytest.mark.parametrize(
    "live",
    [
        {"per_side_bps": 7.5},
        {"commission_per_share": 0.01},
        {"commission_per_order": 1.0},
    ],
)
def test_refuses_when_live_costs_differ_from_the_registered_costs(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
    live: dict[str, float],
) -> None:
    """Planning sizes with the registered costs and the wrapper with the
    frozen ones, so a live `costs.*` edit before `paper start` refuses the
    start (`costs_drift`) and writes no window (#534)."""
    settings = Settings(_env_file=None, store={"path": journal_settings.store.path}, costs=live)
    fake = _fake(fixed_clock)

    with pytest.raises(window.StartRefusedError) as refused:
        window.start(settings, _connect(settings), fake, fixed_clock, "h1")

    assert refused.value.reason == "costs_drift"
    assert f"costs.{next(iter(live))}" in str(refused.value)
    with open_read_only(journal_settings) as conn:
        assert latest_window(conn) is None


def test_refuses_a_registration_without_the_cost_keys(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A registration that names only `costs.per_side_bps` cannot vouch for
    the commissions the wrapper reads: refused, never filled from live."""
    with open_for_write(journal_settings) as conn:
        params = {"costs.per_side_bps": 15.0, "strategy.top_fraction": 0.1}
        hyp = _register(conn, journal_settings, "h2", HOLDOUT_END_PAST, params)
        _sign_off(conn, journal_settings, hyp, tmp_path)

    with pytest.raises(window.StartRefusedError) as refused:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h2"
        )

    assert refused.value.reason == "costs_drift"
    assert "costs.commission_per_share" in str(refused.value)
    with open_read_only(journal_settings) as conn:
        assert latest_window(conn) is None


@pytest.mark.parametrize("bad", ["not-a-number", None], ids=["string", "null"])
def test_refuses_when_a_registered_cost_value_is_not_a_number(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    bad: object,
) -> None:
    """A registered cost value `float()` cannot parse (a string or null)
    refuses the start the same way a numeric drift does (`costs_drift`),
    never a plain `ValueError`/`TypeError` escaping from `_frozen_params`,
    and writes no window either way (#580 item 1)."""
    with open_for_write(journal_settings) as conn:
        params = _params(**{"costs.commission_per_share": bad})
        hyp = _register(conn, journal_settings, "h3", HOLDOUT_END_PAST, params)
        _sign_off(conn, journal_settings, hyp, tmp_path)

    with pytest.raises(window.StartRefusedError) as refused:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h3"
        )

    assert refused.value.reason == "costs_drift"
    assert "costs.commission_per_share" in str(refused.value)
    with open_read_only(journal_settings) as conn:
        assert latest_window(conn) is None


def test_refuses_when_live_fill_price_differs_from_the_registered_one(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """The tracking trial fills at the registered `execution.fill_price` and
    `paper report` prices the fill-timing term off the frozen one, so a live
    edit before `paper start` refuses the start (`execution_drift`) and writes
    no window (#526)."""
    settings = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        execution={"fill_price": "open"},
    )

    with pytest.raises(window.StartRefusedError) as refused:
        window.start(settings, _connect(settings), _fake(fixed_clock), fixed_clock, "h1")

    assert refused.value.reason == "execution_drift"
    assert "execution.fill_price live 'open' vs registered 'close'" in str(refused.value)
    with open_read_only(journal_settings) as conn:
        assert latest_window(conn) is None


def test_refuses_a_registration_without_the_fill_price(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A registration that lacks `execution.fill_price` cannot vouch for the
    convention `paper report` compares against: refused, never filled from live."""
    with open_for_write(journal_settings) as conn:
        params = {k: v for k, v in _params().items() if k != "execution.fill_price"}
        hyp = _register(conn, journal_settings, "h2", HOLDOUT_END_PAST, params)
        _sign_off(conn, journal_settings, hyp, tmp_path)

    with pytest.raises(window.StartRefusedError) as refused:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h2"
        )

    assert refused.value.reason == "execution_drift"
    assert "execution.fill_price" in str(refused.value)


def test_succeeds_on_a_version_4_store(tmp_path: Path, fixed_clock: FixedClock) -> None:
    path = version_4_store(tmp_path / "v4.duckdb")
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        hyp = _register(conn, settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, settings, hyp, tmp_path)
    fake = _fake(fixed_clock)

    result = window.start(settings, _connect(settings), fake, fixed_clock, "h1")

    assert result.window.window_id is not None
    assert result.abandoned_note is None


def test_prints_the_abandoned_note(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={},
        state="abandoned",
        reason="the account was reset by hand at Alpaca",
    )
    fake = _fake(fixed_clock)  # strictly flat: no positions seeded
    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert result.abandoned_note == "the account was reset by hand at Alpaca"


# --- split-adjustment, delisting and the spin-off ex-date bound -------------


def test_accepts_split_adjusted_residue_match(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """A 2-for-1 split between the residue's stated session and now doubles
    the quantity `start` must find at the broker; the stored adjustment
    keeps the original, pre-split quantity (the ledger split-adjusts it)."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (5.0, "dust")},
    )
    _insert_action(
        journal_settings,
        security_id=SPY,
        action_type="split",
        ex_date=(stop_at + timedelta(days=2)).date(),
        ratio_or_amount=2.0,
        known_at=stop_at + timedelta(days=1),
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0  # 5 pre-split shares, doubled

    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None

    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    carried = {a.security_id: a for a in adjustments if a.kind == "carried_residue"}
    assert carried[SPY].quantity == 5.0  # unadjusted: the ledger applies the split itself


def test_refuses_residue_not_held_and_not_delisted(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={SPY: (5.0, "dust")},
    )
    fake = _fake(fixed_clock)  # holds nothing, and SPY is not delisted
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_accepts_a_delisted_residue_s_removal(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """`tests/fixtures/universe` delists SEC_TRUNC_DELIST in 2018 with no
    re-listing: a residue in it is explained away when the broker no longer
    holds it, never refused."""
    delisted = "SEC_TRUNC_DELIST"
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={delisted: (3.0, "untradable")},
    )
    fake = _fake(fixed_clock)  # holds nothing: the delisting explains it

    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")

    window_id = result.window.window_id
    assert window_id is not None
    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    assert [a for a in adjustments if a.security_id == delisted] == []


def _real_shaped_spinoff(settings: Settings, stop_at: datetime) -> date:
    """Write the spin-off row exactly as the writers do (#1506, T172c): an
    Alpaca-shaped `spin_offs` payload through `parse_corporate_actions`,
    stored by `ingest._action_row` (child as `security_id`, the parent in
    `source_action_id`, `announced_at` NULL, source `alpaca`, stamped at the
    close after `process_date`). One parent share gives 0.2 child shares."""
    ex_date = (stop_at + timedelta(days=2)).date()
    process_date = (stop_at + timedelta(days=4)).date()
    tickers = {"SPY": SPY, "SPLT": CHILD}
    payload = {
        "spin_offs": [
            {
                "id": "alpaca-spin",
                "source_symbol": "SPY",
                "source_cusip": "78462F103",
                "source_rate": 5,
                "new_symbol": "SPLT",
                "new_cusip": "000000000",
                "new_rate": 1,
                "process_date": process_date.isoformat(),
                "ex_date": ex_date.isoformat(),
            }
        ]
    }
    parsed = parse_corporate_actions(payload, lambda symbol, _day: tickers.get(symbol))
    (action,) = parsed.actions
    assert action.known_at == announcement_date_known_at(process_date)
    row = _action_row(action, action.known_at)
    assert (row["security_id"], row["source_action_id"], row["announced_at"]) == (CHILD, SPY, None)
    assert row["ratio_or_amount"] == pytest.approx(0.2)
    with open_for_write(settings) as conn:
        insert_row(conn, "corporate_actions", row)
    return ex_date


def test_accepts_a_residue_explained_by_a_real_shaped_spinoff_row(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """T172b: the row the writers store explains the child position as the
    convention's hand-written rows do, and journals one receipt."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings, ready_hypothesis, at=stop_at, residues={SPY: (10.0, "dust")}
    )
    ex_date = _real_shaped_spinoff(journal_settings, stop_at)
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0  # 10 * 0.2
    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert result.window.window_id is not None
    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=result.window.window_id)
    receipts = [a for a in adjustments if a.kind == "spinoff_receipt"]
    assert [(r.security_id, r.quantity, r.session) for r in receipts] == [(CHILD, 2.0, ex_date)]


def test_refuses_a_child_position_below_the_expected_quantity(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """T172b: a child position below parent x ratio (the fraction paid as cash
    in lieu) is not explained by the spin-off: refused, never accepted."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings, ready_hypothesis, at=stop_at, residues={SPY: (11.0, "dust")}
    )
    _real_shaped_spinoff(journal_settings, stop_at)
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 11.0
    fake.extra_quantity["SPLT"] = 2.0  # 11 * 0.2 = 2.2: 0.2 paid as cash in lieu
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_spinoff_quantity_mismatch(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (10.0, "dust")},
    )
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=(stop_at + timedelta(days=2)).date(),
        ratio_or_amount=0.2,
        known_at=announcement_date_known_at((stop_at + timedelta(days=4)).date()),
        source_action_id=SPY,
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 99.0  # not 10 * 0.2: an unexplained position
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_a_spinoff_whose_ex_date_is_not_yet_effective(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """A no-look-ahead guard on the spin-off bound: an ex-date after `now`
    does not explain a position yet, however the corporate action is
    already known (a corporate action announced ahead of its ex-date)."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (10.0, "dust")},
    )
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=(fixed_clock() + timedelta(days=5)).date(),  # not yet effective
        ratio_or_amount=0.2,
        known_at=stop_at + timedelta(days=5),
        source_action_id=SPY,
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0  # held already, but the spin-off has no effect yet
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_a_spinoff_not_yet_known(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """No-look-ahead on `known_at`, not just `ex_date`: a spin-off this
    process could not yet have read explains nothing, even with a past
    ex-date (a fixture could otherwise smuggle look-ahead past the ex-date
    bound alone)."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (10.0, "dust")},
    )
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=(stop_at + timedelta(days=2)).date(),
        ratio_or_amount=0.2,
        known_at=fixed_clock() + timedelta(days=1),  # known only after "now"
        source_action_id=SPY,
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_refuses_a_spinoff_ex_dated_on_the_stop_session(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """The bound is `stated_on < ex_date`, strict: a spin-off ex-dated on the
    stop session itself (already reflected in the residue quantity the stop
    row listed) does not explain a position a second time."""
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (10.0, "dust")},
    )
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=stop_at.date(),  # on the stop session, not after it
        ratio_or_amount=0.2,
        known_at=stop_at - timedelta(days=1),
        source_action_id=SPY,
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "not_flat"


def test_accepts_spinoff_with_a_parent_split_before_and_a_child_split_after(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """Exercises both split-adjustment legs so the fix cannot regress to the
    unadjusted math: a 2-for-1 parent split between the stop session and the
    spin-off's ex-date, then a 3-for-1 child split between the ex-date and
    now. The stored adjustment keeps the ex-date's own basis (5.0 = 10.0
    post-parent-split shares x 0.5 ratio), letting the ledger apply the
    child's own 3-for-1 split itself; the live broker position the quantity
    both splits would actually produce (15.0) is what `start` must accept."""
    stop_at = fixed_clock() - timedelta(days=20)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (5.0, "dust")},
    )
    ex_date = (stop_at + timedelta(days=5)).date()
    _insert_action(
        journal_settings,
        security_id=SPY,
        action_type="split",
        ex_date=(stop_at + timedelta(days=2)).date(),  # between the stop session and the ex-date
        ratio_or_amount=2.0,
        known_at=stop_at + timedelta(days=1),
    )
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="spinoff",
        ex_date=ex_date,
        ratio_or_amount=0.5,
        known_at=stop_at + timedelta(days=6),
        source_action_id=SPY,
    )
    _insert_action(
        journal_settings,
        security_id=CHILD,
        action_type="split",
        ex_date=(fixed_clock() - timedelta(days=1)).date(),  # between the ex-date and now
        ratio_or_amount=3.0,
        known_at=fixed_clock() - timedelta(days=2),
    )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0  # 5 pre-split shares, doubled
    fake.extra_quantity["SPLT"] = 15.0  # (10 * 0.5) post-parent-split child shares, tripled

    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None

    with open_read_only(journal_settings) as conn:
        fills = fills_for(conn, window_id=window_id)
        orders = orders_for(conn, window_id=window_id)
        adjustments = adjustments_for(conn, window_id=window_id)
        actions = live_actions_as_of(conn, fixed_clock())
    receipts = [a for a in adjustments if a.kind == "spinoff_receipt"]
    assert len(receipts) == 1
    assert receipts[0].security_id == CHILD
    assert receipts[0].session == ex_date
    assert receipts[0].quantity == pytest.approx(5.0)  # the ex-date's own basis, unadjusted

    ledger = from_journal(
        fills,
        orders,
        adjustments,
        actions,
        None,
        result.window.starting_cash,
        result.window.first_rebalance_session,
        window_id=window_id,
        quantity_tolerance=journal_settings.risk.reconcile_quantity_tolerance,
    )
    # The ledger applies the child's own split itself (its documented
    # convention): 5.0 stored x 3.0 (the child's split since the ex-date) =
    # 15.0, matching what the live broker actually holds.
    assert ledger.positions[CHILD] == pytest.approx(15.0)
    assert fake.positions()["SPLT"].quantity == pytest.approx(ledger.positions[CHILD])


def test_window_of_refuses_schema_version_on_a_version_16_store(tmp_path: Path) -> None:
    """#1261: `_window_of` refuses `schema_version` (not a false `no_window`)
    over a version-16 journal, the eight expanded tables lacking `book_id`, with
    the fix in the message."""
    path = tmp_path / "v16.duckdb"
    conn = duckdb.connect(str(path))
    try:
        schema.init_schema(conn)
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")
    finally:
        conn.close()
    with (
        duckdb.connect(str(path), read_only=True) as conn,
        pytest.raises(window.WindowCommandRefused) as refused,
    ):
        window._window_of(conn, "main")
    assert refused.value.reason == window.SCHEMA_VERSION
    assert "open it for writing once" in str(refused.value)


def test_start_refuses_schema_version_on_a_version_16_store(
    tmp_path: Path, fixed_clock: FixedClock
) -> None:
    """#1261: `start` refuses `schema_version`, naming the fix, rather than
    treating the store as having no journal and then refusing `not_flat`."""
    path = tmp_path / "v16.duckdb"
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        hyp = _register(conn, settings, "h1", HOLDOUT_END_PAST)
        _sign_off(conn, settings, hyp, tmp_path)
    with duckdb.connect(str(path)) as conn:
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")

    fake = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as refused:
        window.start(settings, _connect(settings), fake, fixed_clock, "h1")

    assert refused.value.reason == window.SCHEMA_VERSION
    assert "open it for writing once" in str(refused.value)


# --- a forward holdout (ADR 0016 point 4, plan T142c) -------------------------

#: A forward holdout: it starts after the family's first registration day, so the
#: paper book inside it is the exam of record. At the conftest clock (2026-10-01,
#: 10:00 ET) `holdout.start` has passed and `holdout.end` lies a year ahead.
FORWARD_START = date(2026, 9, 1)
FORWARD_END = date(2027, 8, 31)
FIRST_REGISTERED = datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
#: The tracking rule's frozen gap threshold, which `holdout.Frozen` requires.
GAP_KEY = "gap.count_share_threshold"
#: The first month-end session strictly after the clock's date (2026-10-31 is a Saturday).
NEXT_MONTH_END = date(2026, 10, 30)


def _register_forward(
    settings: Settings,
    tmp_path: Path,
    *,
    holdout_start: date = FORWARD_START,
    first_registered: datetime = FIRST_REGISTERED,
    family: str = "momentum",
    params: dict[str, Any] | None = None,
    sign_off: bool = True,
    excess: tuple[float, ...] = (0.01, -0.005, 0.02, -0.01, 0.0, 0.015),
) -> registry.HypothesisRecord:
    """Register `h1` with a holdout of [`holdout_start`, `FORWARD_END`] and date the
    family's every registration at `first_registered` (`registered_at` is the wall
    clock at registration, which a test cannot choose)."""
    with open_for_write(settings) as conn:
        hyp = _register(
            conn,
            settings,
            "h1",
            FORWARD_END,
            params=_params(**{GAP_KEY: 0.05}) if params is None else params,
            family=family,
            holdout_start=holdout_start,
        )
        if sign_off:
            _sign_off(conn, settings, hyp, tmp_path)
            _write_insample_equity(conn, hyp, excess)
        conn.execute(
            "UPDATE hypotheses SET registered_at = ? WHERE family = ?", [first_registered, family]
        )
        assert registry.family_registered_on(conn, family) == first_registered.date()
    return hyp


#: The in-sample trial's span (`_sign_off`) and the SPY series's monthly return.
_INSAMPLE_SPAN = (date(2018, 1, 31), date(2022, 12, 30))
_SPY_MONTHLY = 0.005
#: A crash month among small gains: a skew far below the -0.85 guard.
CRASH_EXCESS = (0.01,) * 11 + (-0.2,)


def _write_insample_equity(
    conn: duckdb.DuckDBPyConnection, hyp: registry.HypothesisRecord, excess: tuple[float, ...]
) -> None:
    """The signed-off in-sample trial's base-cost strategy and SPY equity at each
    month-end, the strategy beating SPY by `excess` (cycled) each month."""
    (trial_id,) = conn.execute(
        "SELECT MAX(trial_id) FROM trials WHERE hypothesis_id = ?", [hyp.hypothesis_id]
    ).fetchone() or (None,)
    ends = rebalance_sessions(*_INSAMPLE_SPAN, "month_end")
    strategy, spy = 100.0, 100.0
    for i, session in enumerate(ends):
        if i:
            spy *= 1 + _SPY_MONTHLY
            strategy *= 1 + _SPY_MONTHLY + excess[(i - 1) % len(excess)]
        for series, level, bps in (("strategy", strategy, 15.0), ("SPY", spy, 0.0)):
            conn.execute(
                "INSERT INTO trial_equity VALUES (?, ?, ?, ?, ?, ?)",
                [trial_id, series, bps, session, level, None],
            )


KILL_LINE = "https://github.com/josejuarez96/tradepartner/issues/1#issuecomment-1"
_LEDGER_HEAD = 'budget = {budget}\nbudget_adr = "docs/decisions/0016.md#rule-5"\n'


def _ledger_entry(
    seq: int,
    *,
    fingerprint: str,
    book: str = "main",
    start: date = NEXT_MONTH_END,
    window_id: int = 0,
    outcome: str = "pending",
    kill_line: str = KILL_LINE,
    campaign_k: int = -1,
) -> str:
    outcome_on = start if outcome in ("pending", "running") else start + timedelta(days=400)
    return (
        f'\n[[exam]]\nseq = {seq}\nhypothesis = "h{seq}"\n'
        f'hypothesis_fingerprint = "{fingerprint}"\nfamily = "momentum"\n'
        f'book_id = "{book}"\nwindow_id = {window_id}\nstart_date = {start.isoformat()}\n'
        f'kill_line = "{kill_line}"\ncampaign_k = {campaign_k}\noutcome = "{outcome}"\n'
        f"outcome_on = {outcome_on.isoformat()}\n"
    )


def _ledger(*entries: str, budget: int = 4) -> str:
    return _LEDGER_HEAD.format(budget=budget) + "".join(entries)


def _fingerprint(settings: Settings, slug: str = "h1") -> str:
    with open_read_only(settings) as conn:
        return window._fingerprint_of(registry.get_hypothesis(conn, slug))


def _texts(text: str | None, committed: str | None = "same") -> Callable[[], window.LedgerTexts]:
    """A ledger reader whose two copies are `text` (and `committed`, default equal)."""
    return lambda: window.LedgerTexts(text, text if committed == "same" else committed)


def _pending(
    settings: Settings,
    *,
    slug: str = "h1",
    book: str = "main",
    t_0: date = NEXT_MONTH_END,
    before: tuple[str, ...] = (),
) -> Callable[[], window.LedgerTexts]:
    """The reviewed ledger with `before` and one `pending` entry naming this start;
    built lazily, so the fingerprint is read from the store at the start."""

    def read() -> window.LedgerTexts:
        entry = _ledger_entry(
            len(before) + 1, fingerprint=_fingerprint(settings, slug), book=book, start=t_0
        )
        text = _ledger(*before, entry)
        return window.LedgerTexts(text, text)

    return read


def test_forward_holdout_starts_inside_the_holdout(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """`holdout.end` is a year away (today's rule refuses `holdout_not_complete`), but
    the holdout is forward and its start has passed: the window opens, its T_0 the
    first month-end on or after `holdout.start` and after today, inside the holdout."""
    hyp = _register_forward(journal_settings, tmp_path)
    result = window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock),
        fixed_clock,
        "h1",
        ledger_texts=_pending(journal_settings),
    )
    assert result.window.window_id is not None
    assert result.window.hypothesis_id == hyp.hypothesis_id
    assert result.window.first_rebalance_session == NEXT_MONTH_END
    assert FORWARD_START <= NEXT_MONTH_END < FORWARD_END


def test_forward_holdout_starting_today_is_accepted(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """On `holdout.start`'s own day (New York) the start has passed; T_0 is the first
    month-end on or after it."""
    _register_forward(journal_settings, tmp_path, holdout_start=date(2026, 10, 1))
    result = window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock),
        fixed_clock,
        "h1",
        ledger_texts=_pending(journal_settings),
    )
    assert result.window.first_rebalance_session == NEXT_MONTH_END


def test_forward_holdout_is_refused_before_its_start(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A forward holdout that starts tomorrow is refused `holdout_not_complete`, before
    any broker call or write."""
    _register_forward(journal_settings, tmp_path, holdout_start=date(2026, 10, 2))
    broker = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert exc.value.reason == "holdout_not_complete"
    assert "forward holdout.start (2026-10-02) has not passed" in str(exc.value)
    assert broker.calls == ()
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn) is None


def test_a_holdout_registered_after_its_start_keeps_the_holdout_end_rule(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """The same dates with the family first registered after `holdout.start` is a
    historical holdout: refused on the incomplete `holdout.end`, as before T142c."""
    _register_forward(
        journal_settings, tmp_path, first_registered=datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
    )
    broker = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert exc.value.reason == "holdout_not_complete"
    assert str(exc.value) == ("'h1''s frozen holdout.end (2027-08-31) is not a completed month-end")
    assert broker.calls == ()


def test_a_historical_holdout_s_first_session_is_unchanged(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """A historical holdout (registered long after its start) still starts at the first
    month-end strictly after both `holdout.end` and today."""
    with open_for_write(journal_settings) as conn:
        first = registry.family_registered_on(conn, "momentum")
    assert first is not None and first > ready_hypothesis.holdout_start
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.first_rebalance_session == NEXT_MONTH_END


def test_forward_holdout_still_needs_a_gap_signoff(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path, sign_off=False)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "gap_signoff"


@pytest.mark.parametrize("family", ["profitability", "combined"])
def test_forward_holdout_starts_for_a_paper_family(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, family: str
) -> None:
    """B4 (`combined`) is a forward exam (ADR 0016 point 5): a forward holdout of either
    newly paper-ready family opens inside its holdout, as momentum's does (T152)."""
    hyp = _register_forward(journal_settings, tmp_path, family=family)
    result = window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock),
        fixed_clock,
        "h1",
        ledger_texts=_pending(journal_settings),
    )
    assert result.window.hypothesis_id == hyp.hypothesis_id
    assert result.window.first_rebalance_session == NEXT_MONTH_END


def test_forward_holdout_at_week_end_starts_at_the_first_week_end(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """ADR 0017 part C: a forward `week_end` holdout opens at the first week-end on or
    after `holdout.start` and after today, freezing `paper.min_rebalances` 13."""
    _register_forward(
        journal_settings,
        tmp_path,
        params=_params(**{"schedule.rebalance_cadence": "week_end", GAP_KEY: 0.05}),
    )
    result = window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock),
        fixed_clock,
        "h1",
        ledger_texts=_pending(journal_settings, t_0=NEXT_WEEK_END),
    )
    assert result.window.first_rebalance_session == NEXT_WEEK_END
    assert json.loads(result.window.frozen_json)["paper.min_rebalances"] == 13


def test_forward_holdout_still_allows_one_open_window(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    window.start(
        journal_settings,
        _connect(journal_settings),
        _fake(fixed_clock),
        fixed_clock,
        "h1",
        ledger_texts=_pending(journal_settings),
    )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "window_open"


def test_forward_holdout_still_needs_a_flat_account(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    fake = _fake(fixed_clock)
    fake.submit(OrderRequest(client_order_id="o1", symbol="SPY", side=Side.BUY, quantity=1))
    pending = _pending(journal_settings)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings,
            _connect(journal_settings),
            fake,
            fixed_clock,
            "h1",
            ledger_texts=pending,
        )
    assert exc.value.reason == "open_orders"
    held = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    held.extra_quantity["SPY"] = 1.0
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings,
            _connect(journal_settings),
            held,
            fixed_clock,
            "h1",
            ledger_texts=pending,
        )
    assert exc.value.reason == "not_flat"
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn) is None


def test_forward_holdout_without_a_frozen_gap_threshold_raises_before_any_call(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A forward registration the daily run's tracking rule could not read is stopped
    at `paper start`, before any broker call or write."""
    _register_forward(journal_settings, tmp_path, params=_params())
    broker = _fake(fixed_clock)
    with pytest.raises(ValueError, match=r"gap\.count_share_threshold"):
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert broker.calls == ()
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn) is None


# --- books (ADR 0017 B.3 to B.5, plan T155) ------------------------------------------

B_ACCOUNT_ID = "PB1"


def _fake_b(clock: FixedClock) -> BookedFake:
    """Book `b`'s own paper account: another account id than `main`'s."""
    return BookedFake(
        clock=clock, price_of=lambda _s: REFERENCE_PRICE, auto_fill=False, account_id=B_ACCOUNT_ID
    )


def _windows(settings: Settings) -> list[tuple[int | None, str, str]]:
    with open_read_only(settings) as conn:
        rows = conn.execute(
            "SELECT window_id, book_id, account_id FROM paper_windows ORDER BY window_id"
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def test_a_second_book_starts_beside_an_open_main_window_and_a_second_main_is_refused(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """Spec acceptance (ADR 0017): `paper start --book b` beside an open `main`
    window is accepted, a second `paper start --book main` is refused
    `window_open`, and so is a second `b`."""
    connect = _connect(journal_settings)
    main = window.start(journal_settings, connect, _fake(fixed_clock), fixed_clock, "h1")
    assert main.window.book_id == "main"

    b = window.start(
        journal_settings, connect, _fake_b(fixed_clock), fixed_clock, "h1", book_id="b"
    )
    assert b.window.book_id == "b"
    assert b.window.account_id == B_ACCOUNT_ID

    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, connect, _fake(fixed_clock), fixed_clock, "h1")
    assert exc.value.reason == "window_open"
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, connect, _fake_b(fixed_clock), fixed_clock, "h1", book_id="b"
        )
    assert exc.value.reason == "window_open"
    assert _windows(journal_settings) == [
        (main.window.window_id, "main", ACCOUNT_ID),
        (b.window.window_id, "b", B_ACCOUNT_ID),
    ]


def test_b_s_first_start_is_not_excused_by_main_s_closed_window_residues(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """The fail-open trap: `main`'s closed window lists SPY as a residue, and
    `b`'s account holds exactly that position. `b` has no previous window, so its
    account must be strictly flat: refused `not_flat`, nothing written."""
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=fixed_clock() - timedelta(days=1),
        residues={SPY: (5.0, "dust")},
        book_id="main",
    )
    fake = _fake_b(fixed_clock)
    fake.extra_quantity["SPY"] = 5.0
    before = _windows(journal_settings)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), fake, fixed_clock, "h1", book_id="b"
        )
    assert exc.value.reason == "not_flat"
    assert _windows(journal_settings) == before


def test_b_carries_its_own_closed_window_s_residues_not_main_s(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """`b`'s previous window is `b`'s, even when `main`'s closed later: its
    residue is carried, and `main`'s (MTUM) explains nothing on `b`'s account."""
    stop_at = fixed_clock() - timedelta(days=2)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (5.0, "dust")},
        book_id="b",
        account_id=B_ACCOUNT_ID,
    )
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at + timedelta(days=1),
        residues={MTUM: (2.0, "dust")},
        book_id="main",
    )
    fake = _fake_b(fixed_clock)
    fake.extra_quantity["SPY"] = 5.0
    fake.extra_quantity["MTUM"] = 2.0  # main's residue, on b's account: unexplained
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), fake, fixed_clock, "h1", book_id="b"
        )
    assert exc.value.reason == "not_flat"

    del fake.extra_quantity["MTUM"]
    result = window.start(
        journal_settings, _connect(journal_settings), fake, fixed_clock, "h1", book_id="b"
    )
    window_id = result.window.window_id
    assert window_id is not None
    with open_read_only(journal_settings) as conn:
        carried = [a for a in adjustments_for(conn, window_id) if a.kind == "carried_residue"]
    assert [(a.security_id, a.quantity, a.book_id) for a in carried] == [(SPY, 5.0, "b")]


@pytest.mark.parametrize("state", ["open", "closed", "abandoned"])
def test_a_book_is_refused_another_book_s_account(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
    state: str,
) -> None:
    """ADR 0017 B.1 and B.4: one account per book. `b`'s broker reporting the
    account `main`'s window (open, closed or abandoned) was opened on is refused
    `account_in_use` before any write, the message naming no secret."""
    connect = _connect(journal_settings)
    if state == "open":
        window.start(journal_settings, connect, _fake(fixed_clock), fixed_clock, "h1")
    else:
        _write_closed_window(
            journal_settings,
            ready_hypothesis,
            at=fixed_clock() - timedelta(days=1),
            residues={},
            state=state,
            reason="test",
            book_id="main",
        )
    before = _windows(journal_settings)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, connect, _fake(fixed_clock), fixed_clock, "h1", book_id="b")
    assert exc.value.reason == window.ACCOUNT_IN_USE
    assert "'main'" in str(exc.value)
    assert _windows(journal_settings) == before


def test_main_s_own_closed_account_is_not_another_book_s(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """The identity check is per book: `main` reopening on its own previous
    account (H1's case) is accepted, with `b` open on another account."""
    _write_closed_window(
        journal_settings, ready_hypothesis, at=fixed_clock() - timedelta(days=1), residues={}
    )
    connect = _connect(journal_settings)
    window.start(journal_settings, connect, _fake_b(fixed_clock), fixed_clock, "h1", book_id="b")
    result = window.start(journal_settings, connect, _fake(fixed_clock), fixed_clock, "h1")
    assert (result.window.book_id, result.window.account_id) == ("main", ACCOUNT_ID)


def test_the_default_book_is_paper_book_id_and_main_is_unchanged(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
    tmp_path: Path,
) -> None:
    """H1 pinned: with no book, `start` is `paper.book_id`'s (`main`) and writes
    the same row as `book_id="main"`, field for field but the id."""
    default = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    other = tmp_path / "other.duckdb"
    other.write_bytes(Path(journal_settings.store.path).read_bytes())
    settings = Settings(_env_file=None, store={"path": str(other)})
    with open_for_write(settings) as conn:
        conn.execute("DELETE FROM paper_windows")
    named = window.start(
        settings, _connect(settings), _fake(fixed_clock), fixed_clock, "h1", book_id="main"
    )
    assert replace(default.window, window_id=None) == replace(named.window, window_id=None)


def test_a_book_outside_the_token_grammar_is_refused_before_anything(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    fake = _fake(fixed_clock)
    with pytest.raises(ValueError, match="book_id"):
        window.start(
            journal_settings, _connect(journal_settings), fake, fixed_clock, "h1", book_id="../x"
        )
    assert fake.calls == ()
    assert _windows(journal_settings) == []


# --- the start feasibility rule (spec amendment 2026-10-18 item 8; ADR 0023 D6; T206) ---
# `paper start` refuses (`refused_small_account`) a window whose plan cannot fit:
# n x `risk.min_order_notional` x (1 + `risk.whole_share_price_buffer`) <= `account().cash`.
# n is the hypothesis's target name count from its frozen set; for momentum the most
# names `strategy.top_fraction` selects from `universe.top_n_by_cap`. At the defaults
# ($1.00 x 1.02 per name) and $100 of cash: 100 names need $102.00 (refused), 25 need
# $25.50 (accepted).

SMALL_ACCOUNT_CASH = 100.0
#: H1's default count: 0.1 of a 1,000-name universe.
HUNDRED_NAMES = {"strategy.top_fraction": 0.1, "universe.top_n_by_cap": 1000}
#: 0.1 of a 250-name universe.
TWENTY_FIVE_NAMES = {"strategy.top_fraction": 0.1, "universe.top_n_by_cap": 250}


def _small_account_settings(settings: Settings, *, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": settings.store.path},
        risk={"small_account": {"enabled": enabled}},
    )


def _register_names(
    settings: Settings, tmp_path: Path, names: dict[str, Any]
) -> registry.HypothesisRecord:
    with open_for_write(settings) as conn:
        hyp = _register(conn, settings, "h1", HOLDOUT_END_PAST, params=_params(**names))
        _sign_off(conn, settings, hyp, tmp_path)
    return hyp


def test_start_refuses_a_100_name_registration_at_100_before_any_write(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """Section on: 100 x $1.00 x 1.02 = $102.00 of buys cannot fit $100 of cash."""
    settings = _small_account_settings(journal_settings, enabled=True)
    _register_names(settings, tmp_path, HUNDRED_NAMES)
    fake = _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(settings, _connect(settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "refused_small_account"
    assert _windows(settings) == []


def test_start_accepts_the_25_name_registration_at_100_and_freezes_the_section(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """Section on: 25 x $1.00 x 1.02 = $25.50 fits $100; the window opens on the
    account's $100 and freezes `risk.small_account` on, read back by `frozen_risk`."""
    settings = _small_account_settings(journal_settings, enabled=True)
    _register_names(settings, tmp_path, TWENTY_FIVE_NAMES)
    fake = _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH)
    result = window.start(settings, _connect(settings), fake, fixed_clock, "h1")
    assert result.window.window_id is not None
    assert result.window.starting_cash == SMALL_ACCOUNT_CASH
    assert frozen_risk(result.window).small_account.enabled is True
    assert [w[0] for w in _windows(settings)] == [result.window.window_id]


def test_the_feasibility_rule_holds_for_every_window_with_the_section_off(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """The spec's "the feasibility rule, every window": with the section off the
    100-name registration at $100 is refused the same way, before any write."""
    settings = _small_account_settings(journal_settings, enabled=False)
    _register_names(settings, tmp_path, HUNDRED_NAMES)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            settings,
            _connect(settings),
            _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH),
            fixed_clock,
            "h1",
        )
    assert exc.value.reason == "refused_small_account"
    assert _windows(settings) == []


def test_the_section_frozen_off_by_default(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    """A start with live defaults freezes `risk.small_account` off."""
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert frozen_risk(result.window).small_account.enabled is False


# --- review findings on #1807 (T206): the frozen limits and the family name counts ---
# The rule reads "within the frozen limits": a window whose names cannot each hold
# the minimum order inside ADR 0010's limits is refused at start, as one whose buys
# cannot fit the cash is. With the 5 % weight limit an equal-weight book needs at
# least 1 / 0.05 = 20 names; and one minimum order with its buffer, $1.00 x 1.02 =
# $1.02, must fit the per-order limit at the start cash. (The position-weight form of
# the second clause is implied by the first and the cash clause together:
# n >= 1/w and n x m x (1 + b) <= cash give m x (1 + b) <= cash x w; so the order
# limit is the one a test can isolate.)

#: 0.1 of a 150-name universe: 15 names. 15 x $1.02 = $15.30 fits $100, but 15 < 20.
FIFTEEN_NAMES = {"strategy.top_fraction": 0.1, "universe.top_n_by_cap": 150}
#: 0.1 of a 200-name universe: 20 names, exactly 1 / 0.05.
TWENTY_NAMES = {"strategy.top_fraction": 0.1, "universe.top_n_by_cap": 200}


def test_start_refuses_fewer_names_than_the_frozen_weight_limit_allows(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """Section on: 15 names at $100 fit the cash ($15.30) but each would hold 1/15 =
    6.7 % of equity, over the frozen 5 % `risk.max_position_weight`: fewer than
    1 / 0.05 = 20 names is `refused_small_account`, before any write."""
    settings = _small_account_settings(journal_settings, enabled=True)
    assert settings.risk.max_position_weight == 0.05
    _register_names(settings, tmp_path, FIFTEEN_NAMES)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            settings,
            _connect(settings),
            _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH),
            fixed_clock,
            "h1",
        )
    assert exc.value.reason == "refused_small_account"
    assert _windows(settings) == []


def test_start_accepts_exactly_the_names_the_weight_limit_needs(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """Section on: 20 names = 1 / 0.05, $20.40 of buys at $100: accepted (the
    boundary of the weight clause is inclusive)."""
    settings = _small_account_settings(journal_settings, enabled=True)
    _register_names(settings, tmp_path, TWENTY_NAMES)
    result = window.start(
        settings,
        _connect(settings),
        _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH),
        fixed_clock,
        "h1",
    )
    assert [w[0] for w in _windows(settings)] == [result.window.window_id]


def test_start_refuses_a_minimum_order_over_the_frozen_order_limit(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """Section on, `risk.max_order_notional_fraction` frozen at 1 %: at $100 one
    order may be $1.00, but the minimum order with its buffer is $1.00 x 1.02 =
    $1.02. The 25-name registration fits the cash ($25.50) and the weight clause
    (25 >= 20), and is still `refused_small_account`, before any write."""
    settings = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        risk={"small_account": {"enabled": True}, "max_order_notional_fraction": 0.01},
    )
    _register_names(settings, tmp_path, TWENTY_FIVE_NAMES)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            settings,
            _connect(settings),
            _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH),
            fixed_clock,
            "h1",
        )
    assert exc.value.reason == "refused_small_account"
    assert _windows(settings) == []


# The name count n per family, from the key that family registers: momentum
# `strategy.top_fraction`, profitability `profitability.top_fraction`, combined
# `combined.top_fraction` (config.py `ProfitabilityConfig`, `CombinedConfig`), each a
# share of `universe.top_n_by_cap` (250 here). A momentum key in another family's set
# is not its count: each case carries `strategy.top_fraction` at the other count, so
# reading the momentum key gives the opposite answer.

FAMILY_COUNT_CASES = [
    # (family, its fraction, the stray momentum fraction, refused)
    pytest.param("profitability", 0.1, 0.4, False, id="profitability-25-accepted"),
    pytest.param("profitability", 0.4, 0.1, True, id="profitability-100-refused"),
    pytest.param("combined", 0.1, 0.4, False, id="combined-25-accepted"),
    pytest.param("combined", 0.4, 0.1, True, id="combined-100-refused"),
]


@pytest.mark.parametrize(("family", "fraction", "momentum_fraction", "refused"), FAMILY_COUNT_CASES)
def test_the_name_count_is_the_family_own_top_fraction(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    family: str,
    fraction: float,
    momentum_fraction: float,
    refused: bool,
) -> None:
    """Section on, $100: the family's own `top_fraction` x 250 is n. 0.1 gives 25
    names ($25.50, accepted); 0.4 gives 100 names ($102.00, refused
    `refused_small_account` before any write)."""
    settings = _small_account_settings(journal_settings, enabled=True)
    params = _params(
        **{
            f"{family}.top_fraction": fraction,
            "strategy.top_fraction": momentum_fraction,
            "universe.top_n_by_cap": 250,
        }
    )
    with open_for_write(settings) as conn:
        hyp = _register(conn, settings, "b3", HOLDOUT_END_PAST, params=params, family=family)
        _sign_off(conn, settings, hyp, tmp_path)
    fake = _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH)
    if refused:
        with pytest.raises(window.StartRefusedError) as exc:
            window.start(settings, _connect(settings), fake, fixed_clock, "b3")
        assert exc.value.reason == "refused_small_account"
        assert _windows(settings) == []
    else:
        result = window.start(settings, _connect(settings), fake, fixed_clock, "b3")
        assert result.window.hypothesis_id == hyp.hypothesis_id


# `strategy.hold_target_names` is the name count only under `strategy.weighting =
# hold`; under the default `equal` it is inert (config.py `StrategyConfig`), so a stray
# value never counts. 0.1 x 250 = 25 names ($25.50 at $100, accepted); 150 hold names
# need $153.00 (refused).


@pytest.mark.parametrize(
    ("weighting", "refused"),
    [
        pytest.param("equal", False, id="equal-ignores-hold-names-accepted"),
        pytest.param("hold", True, id="hold-counts-hold-names-refused"),
    ],
)
def test_hold_target_names_count_only_under_hold_weighting(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    weighting: str,
    refused: bool,
) -> None:
    """Section on, $100, the same 25-name `top_fraction` registration with
    `strategy.hold_target_names` = 150: under `equal` the start rule counts 25 names
    and opens; under `hold` it counts 150 and refuses `refused_small_account` before
    any write."""
    settings = _small_account_settings(journal_settings, enabled=True)
    _register_names(
        settings,
        tmp_path,
        {
            **TWENTY_FIVE_NAMES,
            "strategy.weighting": weighting,
            "strategy.hold_target_names": 150,
        },
    )
    fake = _fake(fixed_clock, cash=SMALL_ACCOUNT_CASH)
    if refused:
        with pytest.raises(window.StartRefusedError) as exc:
            window.start(settings, _connect(settings), fake, fixed_clock, "h1")
        assert exc.value.reason == "refused_small_account"
        assert _windows(settings) == []
    else:
        result = window.start(settings, _connect(settings), fake, fixed_clock, "h1")
        assert [w[0] for w in _windows(settings)] == [result.window.window_id]


# --- the forward return exam at `paper start` (plan T381a; ADR 0016 #3143, #3146) ----


@pytest.fixture(autouse=True)
def min_exam_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    """`paper.min_exam_fills` is not in config yet (#3214); these tests stand in 60."""
    monkeypatch.setattr(window, "_min_exam_fills", lambda _settings, _cadence: 60)


EXAM_KEYS = {
    "paper.min_exam_fills": 60,
    "paper.exam_looks_months": [12, 24, 36],
    "paper.exam_bounds": [4.762, 3.367, 2.75],
    "paper.exam_futility_months": 6,
    "paper.exam_max_count": 4,
    "paper.exam_min_insample_skew": -0.85,
    "exam.benchmark": "SPY",
}
_PAST = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def _store_window(
    settings: Settings,
    book: str,
    *,
    exam_class: str | None = "exam",
    started_at: datetime = _PAST,
    first: date = date(2026, 1, 30),
    slug: str = "h1",
) -> int:
    """Append a window of `slug` on `book`'s own account, frozen with `exam_class`
    (None: a window started before T381a)."""
    frozen: dict[str, Any] = {} if exam_class is None else {"exam_class": exam_class}
    with open_for_write(settings) as conn:
        hyp = registry.get_hypothesis(conn, slug)
        window_id = append(
            conn,
            PaperWindowRow(
                hypothesis_id=hyp.hypothesis_id,
                first_rebalance_session=first,
                account_id=f"PA-{book}",
                starting_cash=1000.0,
                starting_equity=1000.0,
                code_version="test",
                started_at=started_at,
                frozen_json=json.dumps(frozen),
                frozen_sha256="0" * 64,
                book_id=book,
                known_at=started_at,
                ingested_at=started_at,
            ),
        )
    assert window_id is not None
    return window_id


def _paper_windows(settings: Settings) -> list[Any]:
    with open_read_only(settings) as conn:
        return conn.execute("SELECT * FROM paper_windows ORDER BY window_id").fetchall()


def _refused(
    settings: Settings, clock: FixedClock, texts: Callable[[], window.LedgerTexts]
) -> window.StartRefusedError:
    """Start `h1` on `main`, expect a refusal before any broker call or write."""
    broker = CallCountingFake(
        clock=clock, price_of=lambda _s: REFERENCE_PRICE, auto_fill=False, account_id=ACCOUNT_ID
    )
    before = _paper_windows(settings)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(settings, _connect(settings), broker, clock, "h1", ledger_texts=texts)
    assert broker.broker_calls == []
    assert _paper_windows(settings) == before
    return exc.value


def _start_h1(
    settings: Settings, clock: FixedClock, texts: Callable[[], window.LedgerTexts]
) -> window.StartResult:
    return window.start(settings, _connect(settings), _fake(clock), clock, "h1", ledger_texts=texts)


def test_an_exam_window_freezes_the_exam_line(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """The five exam keys, the benchmark, the in-sample skew and `exam_class`."""
    _register_forward(journal_settings, tmp_path)
    frozen = json.loads(
        _start_h1(journal_settings, fixed_clock, _pending(journal_settings)).window.frozen_json
    )
    assert {k: frozen[k] for k in EXAM_KEYS} == EXAM_KEYS
    assert frozen["exam_class"] == "exam"
    with open_read_only(journal_settings) as conn:
        series = window.insample_monthly_excess(conn, registry.get_hypothesis(conn, "h1"))
    assert len(series) == 59
    assert frozen["exam.insample_skew"] == pytest.approx(exam_rules.insample_skew(series))
    assert frozen["exam.insample_skew"] > -0.85


def test_a_tracking_window_freezes_only_its_class_and_reads_no_ledger(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    ready_hypothesis: registry.HypothesisRecord,
) -> None:
    def no_ledger() -> window.LedgerTexts:
        raise AssertionError("a tracking window reads no ledger")

    result = _start_h1(journal_settings, fixed_clock, no_ledger)
    frozen = json.loads(result.window.frozen_json)
    assert frozen["exam_class"] == "tracking"
    assert not [k for k in frozen if k.startswith("exam.") or k.startswith("paper.exam_")]


def test_a_skew_below_the_guard_is_refused(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path, excess=CRASH_EXCESS)
    refusal = _refused(journal_settings, fixed_clock, _pending(journal_settings))
    assert refusal.reason == "exam_skew_out_of_range"
    assert "below the guard -0.85" in str(refusal)


def test_a_spent_forward_holdout_is_refused(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """#3149 item 1 (orchestrator, fail-closed): no exam on a spent forward holdout,
    so the backfill `unexamined` entry stays for windows started before T381a."""
    _register_forward(journal_settings, tmp_path)
    with open_for_write(journal_settings) as conn:
        hyp = registry.get_hypothesis(conn, "h1")
        conn.execute(
            "INSERT INTO trials SELECT * REPLACE (trial_id + 1000 AS trial_id, "
            "'holdout' AS kind) FROM trials WHERE hypothesis_id = ?",
            [hyp.hypothesis_id],
        )
    assert _refused(journal_settings, fixed_clock, _pending(journal_settings)).reason == (
        "exam_holdout_spent"
    )


def test_the_fifth_exam_is_refused_after_four_whatever_their_outcomes(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    fp = _fingerprint(journal_settings)
    entries = []
    for seq, outcome in enumerate(("passed", "failed", "failed_futility", "failed_void"), 1):
        book = f"b{seq}"
        first = date(2026, seq, 28) if seq != 2 else date(2026, 2, 27)
        wid = _store_window(
            journal_settings, book, started_at=_PAST + timedelta(days=seq), first=first
        )
        entries.append(
            _ledger_entry(
                seq, fingerprint=fp, book=book, start=first, window_id=wid, outcome=outcome
            )
        )
    refusal = _refused(
        journal_settings, fixed_clock, _pending(journal_settings, before=tuple(entries))
    )
    assert refusal.reason == "exam_budget_spent"
    # a ledger budget of 5 with the config at 4 still refuses the fifth
    five = _ledger(*entries, _ledger_entry(5, fingerprint=fp), budget=5)
    assert _refused(journal_settings, fixed_clock, _texts(five)).reason == "exam_budget_spent"
    # paper.exam_max_count above the ledger's budget is a mismatch
    three = _ledger(*entries, _ledger_entry(5, fingerprint=fp), budget=3)
    assert _refused(journal_settings, fixed_clock, _texts(three)).reason == "exam_ledger_mismatch"


def test_a_not_started_entry_frees_its_slot(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    fp = _fingerprint(journal_settings)
    entries = [_ledger_entry(1, fingerprint=fp, outcome="not_started")]
    for seq in (2, 3, 4):
        book = f"b{seq}"
        first = date(2026, 3, 31) if seq == 2 else date(2026, seq + 1, 30)
        wid = _store_window(
            journal_settings, book, started_at=_PAST + timedelta(days=seq), first=first
        )
        entries.append(
            _ledger_entry(
                seq, fingerprint=fp, book=book, start=first, window_id=wid, outcome="failed"
            )
        )
    result = _start_h1(
        journal_settings, fixed_clock, _pending(journal_settings, before=tuple(entries))
    )
    assert json.loads(result.window.frozen_json)["exam_class"] == "exam"


def test_a_pre_t381a_window_counts_and_needs_its_backfill_entry(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A window with no `exam_class` on a forward holdout is an exam window: with no
    ledger entry the next exam start is refused, with its `unexamined` backfill it
    starts."""
    _register_forward(journal_settings, tmp_path)
    old = _store_window(journal_settings, "old", exam_class=None)
    with open_read_only(journal_settings) as conn:
        assert [k.window_id for _r, k in window.store_exam_windows(conn)] == [old]
    refusal = _refused(journal_settings, fixed_clock, _pending(journal_settings))
    assert refusal.reason == "exam_ledger_mismatch"
    backfill = _ledger_entry(
        1,
        fingerprint=_fingerprint(journal_settings),
        book="old",
        start=date(2026, 1, 30),
        window_id=old,
        outcome="unexamined",
        kill_line="none",
    )
    assert _start_h1(journal_settings, fixed_clock, _pending(journal_settings, before=(backfill,)))


def test_the_stored_class_holds_and_a_pre_t381a_window_that_flips_is_refused(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """An older registration imported later moves the family's first day back: a
    window whose stored class is `tracking` stays tracking, a window started before
    T381a re-derives as an exam window and the start is refused (fail-closed)."""
    _register_forward(
        journal_settings, tmp_path, first_registered=datetime(2026, 9, 2, 15, 0, tzinfo=UTC)
    )
    _store_window(journal_settings, "kept", exam_class="tracking")
    _store_window(journal_settings, "old", exam_class=None, started_at=_PAST + timedelta(days=1))
    with open_read_only(journal_settings) as conn:
        assert window.store_exam_windows(conn) == []
    with open_for_write(journal_settings) as conn:
        older = _register(
            conn, journal_settings, "h0", FORWARD_END, params=_params(**{GAP_KEY: 0.05})
        )
        conn.execute(
            "UPDATE hypotheses SET registered_at = ? WHERE hypothesis_id = ?",
            [FIRST_REGISTERED, older.hypothesis_id],
        )
    with open_read_only(journal_settings) as conn:
        assert [r.book_id for r, _k in window.store_exam_windows(conn)] == ["old"]
    assert _refused(journal_settings, fixed_clock, _pending(journal_settings)).reason == (
        "exam_ledger_mismatch"
    )


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing", "exam_ledger_invalid"),
        ("unparsable", "exam_ledger_invalid"),
        ("local_extra_pending", "exam_ledger_unreviewed"),
        ("lagging", "exam_ledger_unreviewed"),
        ("no_git", "exam_ledger_unreviewed"),
        ("no_pending", "exam_ledger_mismatch"),
        ("two_pending", "exam_ledger_mismatch"),
        ("other_book", "exam_ledger_mismatch"),
        ("other_t0", "exam_ledger_mismatch"),
        ("other_hypothesis", "exam_ledger_mismatch"),
        ("entry_without_window", "exam_ledger_mismatch"),
    ],
)
def test_each_ledger_refusal_comes_before_any_broker_call_or_write(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, case: str, reason: str
) -> None:
    _register_forward(journal_settings, tmp_path)
    fp = _fingerprint(journal_settings)
    pending = _ledger_entry(1, fingerprint=fp)
    good = _ledger(pending)
    texts = {
        "missing": _texts(None, good),
        "unparsable": _texts("budget = [", good),
        "local_extra_pending": _texts(good, _ledger()),
        "lagging": _texts(
            _ledger(
                _ledger_entry(1, fingerprint=fp, outcome="not_started"),
                _ledger_entry(2, fingerprint=fp),
            ),
            _ledger(
                _ledger_entry(1, fingerprint=fp, outcome="not_started"),
                _ledger_entry(2, fingerprint=fp),
                "# newer\n",
            ),
        ),
        "no_git": _texts(good, None),
        "no_pending": _texts(_ledger()),
        "two_pending": _texts(_ledger(pending, _ledger_entry(2, fingerprint=fp))),
        "other_book": _texts(_ledger(_ledger_entry(1, fingerprint=fp, book="b"))),
        "other_t0": _texts(_ledger(_ledger_entry(1, fingerprint=fp, start=date(2026, 11, 30)))),
        "other_hypothesis": _texts(_ledger(_ledger_entry(1, fingerprint="f" * 64))),
        "entry_without_window": _texts(
            _ledger(
                _ledger_entry(1, fingerprint=fp, book="gone", window_id=7, outcome="running"),
                _ledger_entry(2, fingerprint=fp),
            )
        ),
    }[case]
    assert _refused(journal_settings, fixed_clock, texts).reason == reason


def test_a_store_window_out_of_ledger_order_is_a_mismatch(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    fp = _fingerprint(journal_settings)
    a = _store_window(journal_settings, "a", first=date(2026, 1, 30))
    b = _store_window(
        journal_settings, "b", started_at=_PAST + timedelta(days=1), first=date(2026, 2, 27)
    )
    swapped = (
        _ledger_entry(
            1, fingerprint=fp, book="b", start=date(2026, 2, 27), window_id=b, outcome="running"
        ),
        _ledger_entry(
            2, fingerprint=fp, book="a", start=date(2026, 1, 30), window_id=a, outcome="running"
        ),
    )
    refusal = _refused(journal_settings, fixed_clock, _pending(journal_settings, before=swapped))
    assert refusal.reason == "exam_ledger_mismatch"


def test_a_forward_file_called_a_shakedown_counts_like_any_other(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """The class comes from the stored holdout and the family's first registration
    day, never from a name: a forward `shakedown` slug needs its ledger entry."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(
            conn,
            journal_settings,
            "shakedown",
            FORWARD_END,
            params=_params(**{GAP_KEY: 0.05}),
            holdout_start=FORWARD_START,
        )
        _sign_off(conn, journal_settings, hyp, tmp_path)
        _write_insample_equity(conn, hyp, (0.01, -0.005, 0.02, -0.01, 0.0, 0.015))
        conn.execute("UPDATE hypotheses SET registered_at = ?", [FIRST_REGISTERED])
    broker = CallCountingFake(
        clock=fixed_clock,
        price_of=lambda _s: REFERENCE_PRICE,
        auto_fill=False,
        account_id=ACCOUNT_ID,
    )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings,
            _connect(journal_settings),
            broker,
            fixed_clock,
            "shakedown",
            ledger_texts=_texts(_ledger()),
        )
    assert exc.value.reason == "exam_ledger_mismatch"
    assert broker.broker_calls == []


def test_the_committed_ledger_parses_empty_with_budget_4() -> None:
    root = window.repository_root()
    assert root is not None
    ledger = exam_ledger.load_ledger((root / window.EXAM_LEDGER_PATH).read_text())
    assert ledger.entries == () and ledger.budget == 4


def test_the_ledger_is_read_from_the_repository_root_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = window.repository_root()
    assert root is not None
    monkeypatch.chdir(tmp_path)
    texts = window.read_ledger_texts(fetch=False)
    assert texts.local == (root / window.EXAM_LEDGER_PATH).read_text()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_read_ledger_texts_compares_with_origin_main_and_fails_closed_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local copy against `origin/main`'s after a fetch; a checkout git cannot
    read gives no committed copy (refused `exam_ledger_unreviewed`)."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    origin, clone = tmp_path / "origin.git", tmp_path / "clone"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "t")
    path = clone / window.EXAM_LEDGER_PATH
    path.parent.mkdir(parents=True)
    path.write_text(_ledger())
    _git(clone, "add", ".")
    _git(clone, "commit", "-m", "ledger")
    _git(clone, "push", "origin", "main")
    texts = window.read_ledger_texts(clone)
    assert texts.local == texts.committed == _ledger()
    path.write_text(_ledger() + "# local\n")
    texts = window.read_ledger_texts(clone)
    assert texts.local != texts.committed
    plain = tmp_path / "plain"
    (plain / "docs" / "research").mkdir(parents=True)
    (plain / window.EXAM_LEDGER_PATH).write_text(_ledger())
    no_git = window.read_ledger_texts(plain, fetch=False)
    assert no_git.local == _ledger() and no_git.committed is None
    assert window.read_ledger_texts(tmp_path / "nowhere", fetch=False).local is None


def test_an_exam_start_is_refused_while_config_has_no_min_exam_fills(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the key (#3214) an exam could only end `failed_void` and would spend a
    budget slot: refused `exam_config` before any broker call or write."""
    monkeypatch.undo()
    assert window._min_exam_fills(journal_settings, "month_end") is None
    _register_forward(journal_settings, tmp_path)
    refusal = _refused(journal_settings, fixed_clock, _pending(journal_settings))
    assert refusal.reason == "exam_config"
    assert "paper.min_exam_fills" in str(refusal)


class _RacingFake(CallCountingFake):
    """Another exam window is written to the store while `start` reads the broker."""

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    def account(self) -> Account:
        if not self.broker_calls:
            _store_window(self.settings, "racer", started_at=_PAST)
        return super().account()


def test_the_ledger_is_checked_again_under_the_write(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """An exam window that lands between the read chunk and the write (another book's
    start) makes the store disagree with the ledger: refused, and no row is added."""
    _register_forward(journal_settings, tmp_path)
    broker = _RacingFake(
        journal_settings,
        clock=fixed_clock,
        price_of=lambda _s: REFERENCE_PRICE,
        auto_fill=False,
        account_id=ACCOUNT_ID,
    )
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings,
            _connect(journal_settings),
            broker,
            fixed_clock,
            "h1",
            ledger_texts=_pending(journal_settings),
        )
    assert exc.value.reason == "exam_ledger_mismatch"
    assert "account" in broker.broker_calls
    assert [row[0] for row in _windows(journal_settings)] == [1]
    assert [row[1] for row in _windows(journal_settings)] == ["racer"]


def test_the_in_sample_skew_refuses_a_missing_month_end(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    with open_for_write(journal_settings) as conn:
        conn.execute("DELETE FROM trial_equity WHERE session = ?", [date(2020, 6, 30)])
    refusal = _refused(journal_settings, fixed_clock, _pending(journal_settings))
    assert refusal.reason == "exam_skew_out_of_range"
    assert "2020-06-30" in str(refusal)
