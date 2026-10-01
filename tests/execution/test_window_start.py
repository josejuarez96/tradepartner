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
from tradepartner.config import Settings
from tradepartner.execution import window
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lock import LockHeld, run_lock
from tradepartner.store import registry
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
    return {"costs.per_side_bps": 15.0, "strategy.top_fraction": 0.1, **extra}


def _register(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    slug: str,
    holdout_end: date,
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params=_params(),
        in_sample_start=date(2016, 1, 29),
        holdout_start=date(2023, 1, 3),
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
        residues={SPY: (5.0, None)},
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
        residues={SPY: (5.0, None)},
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
    assert result.abandoned_note is None

    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    carried = {a.security_id: a for a in adjustments if a.kind == "carried_residue"}
    assert carried[SPY].quantity == 5.0
    assert carried[SPY].origin == "dust"
    assert carried[SPY].session == stop_at.date()
    assert carried[MTUM].quantity == 2.0
    assert carried[MTUM].origin == "untradable"


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
        residues={SPY: (5.0, None)},
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
    stop_at = fixed_clock() - timedelta(days=10)
    _write_closed_window(
        journal_settings,
        ready_hypothesis,
        at=stop_at,
        residues={SPY: (10.0, None)},
    )
    # The module's documented convention: a `spinoff` corporate_actions row
    # keyed by the child, with `source_action_id` naming the parent.
    ex_date = (stop_at + timedelta(days=2)).date()
    known_at = stop_at + timedelta(days=5)
    with open_for_write(journal_settings) as conn:
        conn.execute(
            "INSERT INTO corporate_actions "
            "(security_id, action_type, ex_date, ratio_or_amount, source_action_id, "
            "known_at, ingested_at, source, provenance) "
            "VALUES (?, 'spinoff', ?, 0.2, ?, ?, ?, 'test', 'action')",
            [CHILD, ex_date, SPY, known_at, known_at],
        )
    fake = BookedFake(clock=fixed_clock, price_of=lambda _s: REFERENCE_PRICE, account_id=ACCOUNT_ID)
    fake.extra_quantity["SPY"] = 10.0
    fake.extra_quantity["SPLT"] = 2.0  # 10 * 0.2

    result = window.start(journal_settings, _connect(journal_settings), fake, fixed_clock, "h1")
    window_id = result.window.window_id
    assert window_id is not None

    with open_read_only(journal_settings) as conn:
        adjustments = adjustments_for(conn, window_id=window_id)
    receipts = [a for a in adjustments if a.kind == "spinoff_receipt"]
    assert len(receipts) == 1
    assert receipts[0].security_id == CHILD
    assert receipts[0].quantity == pytest.approx(2.0)
    assert receipts[0].origin is None


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
