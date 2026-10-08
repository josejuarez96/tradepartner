"""The expansion-seam write-through, end to end (ADR 0015 seam 1, plan T133).

Shared by the T133 -> T134 -> T135b chain. One `paper run` on `test_run_trade`'s
scripted fake, with `paper.book_id = "fx"` (never the DDL default `"main"`), then
one assertion per table the run fills that every row carries `"fx"` and that
every `orders.client_order_id` has `fx` as its second token. A writer that
leaves the DDL default, or that reads a live key instead of the window's book,
fails here.

Two of the eight expanded tables are not filled by this run and are pinned in
their writers' own tests instead, as the plan's T133 line says:

- `adjustments` is written only on a corporate action: `test_window_start.py`
  (the carried residue and spin-off receipts) and `test_reconcile_run.py` (the
  reconciliation's own adjustments).
- `disposals` is written only after a sell: `test_outcomes.py`, whose writer
  test also pins `lots`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution.test_run_trade import F_0, Clock, Env, at, bought
from tradepartner.config import Settings
from tradepartner.execution.lots import LedgerAccount
from tradepartner.execution.outcomes import write_outcomes_and_lots
from tradepartner.store.db import open_for_write

#: The book the window is started with: not `schema.DEFAULT_BOOK_ID`.
BOOK = "fx"

#: Every table the fill-session scripted run fills. `adjustments` and
#: `disposals` are pinned in their writers' tests (module docstring).
RUN_TABLES = (
    "paper_windows",
    "decisions",
    "orders",
    "positions_daily",
    "lots",
    "reconciliations",
)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`test_run_trade.py`'s own autouse fixture, repeated here: importing its
    `env`/`window` fixtures does not import its autouse ones, and the run path
    (`StoreProvider` -> `get_settings()`) reads the real `.env` without this."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.delenv("TRADEPARTNER_INVOKED_BY", raising=False)


@pytest.fixture
def fx_env(fixture_store_path: Path) -> Env:
    """The scripted fake's `Env` with a non-default window book."""
    settings = Settings(
        _env_file=None,
        store={"path": str(fixture_store_path)},
        alpaca={"quantity_decimals": 6, "client_order_id_max_length": 64},
        paper={"book_id": BOOK},
    )
    return Env(settings, Clock(at(F_0)))


def test_a_run_writes_its_windows_book_through_every_table_it_fills(
    fx_env: Env, tmp_path: Path
) -> None:
    window = fx_env.open_window(tmp_path=tmp_path)
    bought(fx_env)
    # The fill-session run alone leaves the ledger to step 9: rebuild it here
    # (`run._outcomes`' own write), over the fills the run journaled.
    assert window.window_id is not None
    with open_for_write(fx_env.settings) as conn:
        write_outcomes_and_lots(
            conn,
            window.window_id,
            LedgerAccount(window.account_id, "paper", "self"),
            {},
            lambda _security_id, _session: None,
            F_0,
            fx_env.clock,
            on_lot_error=lambda message: pytest.fail(message),
        )

    for table in RUN_TABLES:
        assert fx_env.count(table) >= 1, table
        assert fx_env.query(f"SELECT DISTINCT book_id FROM {table}") == [(BOOK,)], table

    ids = [row[0] for row in fx_env.query("SELECT client_order_id FROM orders ORDER BY known_at")]
    assert ids
    assert all(coid.split("-")[1] == BOOK for coid in ids), ids
