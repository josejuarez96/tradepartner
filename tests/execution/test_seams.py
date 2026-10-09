"""The expansion-seam write-through, end to end (ADR 0015 seams 1 to 3, plans
T133, T134 and T135b).

Shared by the T133 -> T134 -> T135b chain. One `paper run` on `test_run_trade`'s
scripted fake, with the window's book `"fx"` (never the DDL default `"main"`),
then one assertion per table the run fills that every row carries `"fx"` and
that every `orders.client_order_id` has `fx` as its second token. A writer that
leaves the DDL default fails here.

The window is opened with `"fx"` and the *live* `paper.book_id` is then set to
`"zz"` before the run, so a writer that reads `settings.paper.book_id` instead
of the window row's `book_id` writes `"zz"` and fails the `{"fx"}` assertions.
That is the "every run reads the row and never the live key" rule.

Two of the eight expanded tables are not filled by this run and are pinned in
their writers' own tests instead:

- `adjustments` is written only on a corporate action: `test_window_start.py`
  (the carried residue and spin-off receipts) and `test_reconcile_run.py` (the
  reconciliation's own adjustments).
- `disposals` is written only after a sell: `test_outcomes.py`, whose writer
  test also pins `lots`.
- Three further writers are filled only by other runs and are pinned there with
  a non-default book: the spin-off receipt `AdjustmentRow` (`test_window_start.py`),
  the lag-bound `MISMATCH` `ReconciliationRow` (`test_run_core.py`), and the exit
  `DecisionRow` (`test_run_exits.py`).

Seam 2 (T134): after the same run every row of the five tables with a
`position_side` column reads `long`. Four are filled here (`decisions`,
`orders`, `positions_daily`, `lots`); `disposals` is covered as above, and its
side cannot differ: `Disposal` carries no side, so `DisposalRow` is written
with the DDL default, and `lots.rebuild` refuses any order that is not long
(`test_lots.py`).

Seam 3 (T135b): after the same run every `orders` row reads the default shape,
`schema.ORDER_SHAPE_DEFAULTS` with no limit price, stop price or parent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution.test_run_trade import F_0, Clock, Env, at, bought
from tradepartner.config import Settings
from tradepartner.execution.lots import LedgerAccount
from tradepartner.execution.outcomes import write_outcomes_and_lots
from tradepartner.store.db import open_for_write
from tradepartner.store.schema import LONG, ORDER_SHAPE_DEFAULTS

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

#: The tables with a `position_side` column the run fills (module docstring).
SIDE_TABLES = ("decisions", "orders", "positions_daily", "lots")


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


def _fixture_run(fx_env: Env, tmp_path: Path) -> None:
    """The window with book `"fx"`, the live key moved off it, the scripted
    fill-session run, and the lot-ledger write after it."""
    window = fx_env.open_window(tmp_path=tmp_path)
    # Move the live key off the window's book: every writer must read the row.
    fx_env.settings = fx_env.settings.model_copy(
        update={"paper": fx_env.settings.paper.model_copy(update={"book_id": "zz"})}
    )
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
            cadence="month_end",
        )


def test_a_run_writes_its_windows_book_through_every_table_it_fills(
    fx_env: Env, tmp_path: Path
) -> None:
    _fixture_run(fx_env, tmp_path)

    for table in RUN_TABLES:
        assert fx_env.count(table) >= 1, table
        assert fx_env.query(f"SELECT DISTINCT book_id FROM {table}") == [(BOOK,)], table

    ids = [row[0] for row in fx_env.query("SELECT client_order_id FROM orders ORDER BY known_at")]
    assert ids
    assert all(coid.split("-")[1] == BOOK for coid in ids), ids


def test_every_row_the_run_writes_is_long(fx_env: Env, tmp_path: Path) -> None:
    """ADR 0015 seam 2 (T134): each table filled, and every row `long`."""
    _fixture_run(fx_env, tmp_path)

    for table in SIDE_TABLES:
        assert fx_env.count(table) >= 1, table
        assert fx_env.query(f"SELECT DISTINCT position_side FROM {table}") == [(LONG,)], table


def test_every_orders_row_the_run_writes_has_the_default_shape(fx_env: Env, tmp_path: Path) -> None:
    """ADR 0015 seam 3 (T135b): the market day order, one share class."""
    _fixture_run(fx_env, tmp_path)

    assert fx_env.count("orders") >= 1
    shapes = fx_env.query(
        "SELECT DISTINCT order_type, time_in_force, asset_class, order_class, multiplier, "
        "limit_price, stop_price, parent_order_id FROM orders"
    )
    defaults = ORDER_SHAPE_DEFAULTS
    assert shapes == [
        (
            defaults["order_type"],
            defaults["time_in_force"],
            defaults["asset_class"],
            defaults["order_class"],
            defaults["multiplier"],
            None,
            None,
            None,
        )
    ]
