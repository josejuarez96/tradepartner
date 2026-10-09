"""Tests for `execution.window.start` (Phase 4 spec req 14 "Entry gate, start
and stop"; ADR 0009 point 3; plan T64).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.adapters.broker import Account, OrderRequest, Position, Side
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.config import FROZEN_COSTS_KEYS, FROZEN_EXECUTION_KEYS, Settings
from tradepartner.execution import report, window
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.store import registry, schema
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
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
) -> None:
    """Open a trial for `hyp`, close it `ok`, and sign off its gap.

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
) -> int:
    with open_for_write(settings) as conn:
        row_id = append(
            conn,
            PaperWindowRow(
                hypothesis_id=hyp.hypothesis_id,
                first_rebalance_session=date(2026, 8, 31),
                account_id=ACCOUNT_ID,
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


def test_refuses_a_family_paper_cannot_run(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """A `profitability` hypothesis remains outside `PAPER_FAMILIES`, before any
    broker call or write, although the backtest engine can now run it."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "b3", HOLDOUT_END_PAST, family="profitability")
        _sign_off(conn, journal_settings, hyp, tmp_path)
    broker = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "b3")
    assert exc.value.reason == "family_not_runnable"
    assert "profitability" in str(exc.value)
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn) is None


@pytest.mark.parametrize("cadence", ["week_end", "daily"])
def test_refuses_a_non_monthly_cadence_before_any_broker_call(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, cadence: str
) -> None:
    """Strategy-lab spec req 11 (T100): a hypothesis whose `frozen_values` cadence
    is not `month_end` is refused `refused_cadence`, before any broker call or write."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(
            conn,
            journal_settings,
            "h1",
            HOLDOUT_END_PAST,
            params=_params(**{"schedule.rebalance_cadence": cadence}),
        )
        _sign_off(conn, journal_settings, hyp, tmp_path)
    broker = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert exc.value.reason == "refused_cadence"
    assert cadence in str(exc.value)
    assert broker.calls == ()
    with open_for_write(journal_settings) as conn:
        assert latest_window(conn) is None


@pytest.mark.parametrize("stored", [{"schedule.rebalance_cadence": "month_end"}, {}])
def test_accepts_a_month_end_and_a_pre_lab_registration(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path, stored: dict[str, Any]
) -> None:
    """A `month_end` registration and one without `schedule.*` keys (a pre-lab
    registration, read as `month_end` through `frozen_values`) both start."""
    with open_for_write(journal_settings) as conn:
        hyp = _register(conn, journal_settings, "h1", HOLDOUT_END_PAST, params=_params(**stored))
        _sign_off(conn, journal_settings, hyp, tmp_path)
    assert ("schedule.rebalance_cadence" in hyp.params) == bool(stored)
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
    )
    assert result.window.window_id is not None


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
    known_at = stop_at + timedelta(days=5)
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
        known_at=stop_at + timedelta(days=5),
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
        window._window_of(conn)
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
        conn.execute(
            "UPDATE hypotheses SET registered_at = ? WHERE family = ?", [first_registered, family]
        )
        assert registry.family_registered_on(conn, family) == first_registered.date()
    return hyp


def test_forward_holdout_starts_inside_the_holdout(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    """`holdout.end` is a year away (today's rule refuses `holdout_not_complete`), but
    the holdout is forward and its start has passed: the window opens, its T_0 the
    first month-end on or after `holdout.start` and after today, inside the holdout."""
    hyp = _register_forward(journal_settings, tmp_path)
    result = window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
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
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
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


def test_forward_holdout_still_needs_a_paper_family(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path, family="profitability")
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(
            journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
        )
    assert exc.value.reason == "family_not_runnable"


def test_forward_holdout_still_needs_month_end_cadence(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(
        journal_settings,
        tmp_path,
        params=_params(**{"schedule.rebalance_cadence": "week_end", GAP_KEY: 0.05}),
    )
    broker = _fake(fixed_clock)
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), broker, fixed_clock, "h1")
    assert exc.value.reason == "refused_cadence"
    assert broker.calls == ()


def test_forward_holdout_still_allows_one_open_window(
    journal_settings: Settings, fixed_clock: FixedClock, tmp_path: Path
) -> None:
    _register_forward(journal_settings, tmp_path)
    window.start(
        journal_settings, _connect(journal_settings), _fake(fixed_clock), fixed_clock, "h1"
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
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    assert exc.value.reason == "open_orders"
    held = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    held.extra_quantity["SPY"] = 1.0
    with pytest.raises(window.StartRefusedError) as exc:
        window.start(journal_settings, _connect(journal_settings), held, fixed_clock, "h1")
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
