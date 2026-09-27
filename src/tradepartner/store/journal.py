"""The paper-trading journal: row types, the one writer and the fills accessor
(Phase 4 spec "Data / interfaces"; plan T49b).

The journal tables (`schema.JOURNAL_TABLE_NAMES`, schema version 5) are append-only:
this module inserts and reads, and never updates or deletes, as `store.registry`
does for the registry. A later fact (a fill superseded by a real one, an order's
next state) is a new row, never an edit.

- **Row types**: one frozen dataclass per journal table, named after it, whose
  fields are the table's columns in order (a test holds them to the schema). A
  column that may be NULL defaults to None; every other field must be given,
  `known_at` and `ingested_at` included, so no timestamp is ever filled in here.
- **`append(conn, row)`**: the one writer, keyed by the row's `TABLE`, through
  `store.db.insert_row` (which checks every `TIMESTAMPTZ` is tz-aware and every
  `DATE` a date). A table with its own id (`ID_COLUMN`) gets the next one, the
  registry's `_next_id` pattern, when the row leaves it None. Before the insert,
  `known_at` and `ingested_at` must both be tz-aware and `known_at <=
  ingested_at`. `known_at` is the caller's reading of the shared clock at the
  moment the system learned or decided the fact (spec "Definitions"): this module
  never derives it from a broker field such as `filled_at` or `event_at`.
  `ingested_at` must come from the same clock or later, or the check refuses the
  row.
- **`fills_for(conn, ...)`**: the **single** reader of `fills`. It hides every
  superseded row (`superseded_by IS NULL`) and joins each fill to its `orders` row
  for `side` and `security_id` (`fills.quantity` is unsigned). A live fill with no
  order row raises `JournalIntegrityError` rather than drop out of a ledger.
  `all_fill_ids(conn)` returns every `broker_fill_id`, superseded rows included, for
  the collectors' duplicate check. Nothing else here selects from `fills`.
- **`JournalNotInitialised`**: raised by `append`, `fills_for` and `all_fill_ids`
  when the journal tables are absent (a version-4 store no write has migrated;
  read-only connections never migrate), for the pages' "not initialised" state.

Column `at` is a DuckDB keyword: SQL naming it must quote it (`"at"`);
`insert_row` quotes every column.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any, ClassVar, Protocol

import duckdb

from tradepartner.store.db import ensure_tz_aware, insert_row
from tradepartner.store.schema import JOURNAL_TABLE_NAMES


class JournalNotInitialised(RuntimeError):
    """The store has no journal tables: a version-4 store that no write connection
    has migrated to version 5 yet. Any writing command's `init_schema` migrates it."""


class JournalIntegrityError(RuntimeError):
    """The journal breaks one of its own invariants (a live fill with no order)."""


class JournalRow(Protocol):
    """What every row type has: its table and, when it has one, its own id column."""

    TABLE: ClassVar[str]
    ID_COLUMN: ClassVar[str | None]
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PaperWindowRow:
    """One `paper_windows` row."""

    TABLE: ClassVar[str] = "paper_windows"
    ID_COLUMN: ClassVar[str | None] = "window_id"

    window_id: int | None = None
    hypothesis_id: int
    first_rebalance_session: date
    account_id: str
    starting_cash: float
    starting_equity: float
    code_version: str
    started_at: datetime
    frozen_json: str
    frozen_sha256: str
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PaperWindowStopRow:
    """One `paper_window_stops` row."""

    TABLE: ClassVar[str] = "paper_window_stops"
    ID_COLUMN: ClassVar[str | None] = None

    window_id: int
    at: datetime
    state: str
    reason: str | None = None
    reconciliation_id: int | None = None
    residues_json: str | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PaperRunRow:
    """One `paper_runs` row."""

    TABLE: ClassVar[str] = "paper_runs"
    ID_COLUMN: ClassVar[str | None] = "run_id"

    run_id: int | None = None
    window_id: int
    session: date | None = None
    kind: str | None = None
    started_at: datetime
    invoked_by: str
    code_version: str
    code_dirty: bool | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PaperRunResultRow:
    """One `paper_run_results` row."""

    TABLE: ClassVar[str] = "paper_run_results"
    ID_COLUMN: ClassVar[str | None] = None

    run_id: int
    finished_at: datetime
    status: str
    fault_type: str | None = None
    message: str | None = None
    clock_fault: bool
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PaperPlanRow:
    """One `paper_plans` row."""

    TABLE: ClassVar[str] = "paper_plans"
    ID_COLUMN: ClassVar[str | None] = None

    run_id: int
    plan_trial_id: int
    rebalance_session: date
    store_max_ingested_at: datetime | None = None
    n_universe: int
    n_targets: int
    n_orders_below_min_at_live_capital: int
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class RebalanceEventRow:
    """One `rebalance_events` row."""

    TABLE: ClassVar[str] = "rebalance_events"
    ID_COLUMN: ClassVar[str | None] = None

    rebalance_session: date
    run_id: int
    status: str
    reason: str | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PaperReportRow:
    """One `paper_reports` row."""

    TABLE: ClassVar[str] = "paper_reports"
    ID_COLUMN: ClassVar[str | None] = None

    window_id: int
    trial_id: int
    through_session: date
    run_at: datetime
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class SignalRow:
    """One `signals` row."""

    TABLE: ClassVar[str] = "signals"
    ID_COLUMN: ClassVar[str | None] = None

    run_id: int
    rebalance_session: date
    security_id: str
    score: float | None = None
    rank: int | None = None
    reason: str
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class DecisionRow:
    """One `decisions` row."""

    TABLE: ClassVar[str] = "decisions"
    ID_COLUMN: ClassVar[str | None] = "decision_id"

    decision_id: int | None = None
    run_id: int
    rebalance_session: date | None = None
    security_id: str
    target_weight: float | None = None
    drifted_weight: float | None = None
    side: str | None = None
    planned_notional: float | None = None
    planned_quantity: float | None = None
    target_notional: float | None = None
    whole_share: bool
    decision: str
    reason: str | None = None
    override_id: int | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class DecisionEventRow:
    """One `decision_events` row."""

    TABLE: ClassVar[str] = "decision_events"
    ID_COLUMN: ClassVar[str | None] = None

    decision_id: int
    run_id: int
    status: str
    reason: str | None = None
    unfunded_notional: float | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class OrderRow:
    """One `orders` row."""

    TABLE: ClassVar[str] = "orders"
    ID_COLUMN: ClassVar[str | None] = None

    client_order_id: str
    decision_id: int
    run_id: int
    session: date
    attempt: int
    phase: str
    security_id: str
    symbol: str
    side: str
    notional: float | None = None
    quantity: float | None = None
    sells_in_flight_at_submit: bool
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class OrderEventRow:
    """One `order_events` row."""

    TABLE: ClassVar[str] = "order_events"
    ID_COLUMN: ClassVar[str | None] = None

    client_order_id: str
    event_at: datetime | None = None
    status: str
    reason: str | None = None
    broker_order_id: str | None = None
    filled_quantity: float | None = None
    filled_avg_price: float | None = None
    raw_json: str | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class FillRow:
    """One `fills` row."""

    TABLE: ClassVar[str] = "fills"
    ID_COLUMN: ClassVar[str | None] = "fill_id"

    fill_id: int | None = None
    client_order_id: str
    filled_at: datetime
    quantity: float
    price: float
    price_implied: bool
    broker_fill_id: str
    source: str
    superseded_by: int | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class FillCursorRow:
    """One `fill_cursors` row."""

    TABLE: ClassVar[str] = "fill_cursors"
    ID_COLUMN: ClassVar[str | None] = None

    writer_kind: str
    writer_id: int
    collected_through: datetime
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class ResumeInvocationRow:
    """One `resume_invocations` row."""

    TABLE: ClassVar[str] = "resume_invocations"
    ID_COLUMN: ClassVar[str | None] = "resume_id"

    resume_id: int | None = None
    at: datetime
    reason: str
    accept_broker_fills: bool
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class OutcomeRow:
    """One `outcomes` row."""

    TABLE: ClassVar[str] = "outcomes"
    ID_COLUMN: ClassVar[str | None] = None

    client_order_id: str
    through_session: date
    kind: str
    value: float | None = None
    contribution: float | None = None
    mark_price: float | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class PositionDailyRow:
    """One `positions_daily` row."""

    TABLE: ClassVar[str] = "positions_daily"
    ID_COLUMN: ClassVar[str | None] = None

    run_id: int
    session: date
    security_id: str | None = None
    quantity: float
    mark_price: float | None = None
    value: float | None = None
    cash: float | None = None
    tradable: bool | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class AdjustmentRow:
    """One `adjustments` row."""

    TABLE: ClassVar[str] = "adjustments"
    ID_COLUMN: ClassVar[str | None] = "adjustment_id"

    adjustment_id: int | None = None
    window_id: int
    run_id: int | None = None
    session: date
    kind: str
    origin: str | None = None
    security_id: str | None = None
    quantity: float | None = None
    cash: float | None = None
    explanation_json: str | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class ReconciliationRow:
    """One `reconciliations` row."""

    TABLE: ClassVar[str] = "reconciliations"
    ID_COLUMN: ClassVar[str | None] = "reconciliation_id"

    reconciliation_id: int | None = None
    window_id: int
    run_id: int | None = None
    at: datetime
    status: str
    broker_cash: float | None = None
    mismatches_json: str | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class KillSwitchRow:
    """One `kill_switch` row."""

    TABLE: ClassVar[str] = "kill_switch"
    ID_COLUMN: ClassVar[str | None] = "event_id"

    event_id: int | None = None
    window_id: int
    at: datetime
    state: str
    source: str
    fault_type: str | None = None
    reason: str | None = None
    run_id: int | None = None
    override_id: int | None = None
    resume_id: int | None = None
    reconciliation_id: int | None = None
    peak_equity: float | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class OverrideRow:
    """One `overrides` row."""

    TABLE: ClassVar[str] = "overrides"
    ID_COLUMN: ClassVar[str | None] = "override_id"

    override_id: int | None = None
    window_id: int
    made_at: datetime
    rebalance_session: date | None = None
    security_id: str | None = None
    kind: str
    reason: str
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class AlertRow:
    """One `alerts` row."""

    TABLE: ClassVar[str] = "alerts"
    ID_COLUMN: ClassVar[str | None] = "alert_id"

    alert_id: int | None = None
    run_id: int | None = None
    session: date
    kind: str
    message: str
    at: datetime
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class AlertDeliveryRow:
    """One `alert_deliveries` row."""

    TABLE: ClassVar[str] = "alert_deliveries"
    ID_COLUMN: ClassVar[str | None] = None

    alert_id: int
    channel: str
    at: datetime
    ok: bool
    error: str | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class LotRow:
    """One `lots` row."""

    TABLE: ClassVar[str] = "lots"
    ID_COLUMN: ClassVar[str | None] = "lot_id"

    lot_id: int | None = None
    account_id: str
    account_type: str
    account_owner: str
    security_id: str
    symbol: str
    cusip: str | None = None
    trade_at: datetime
    trade_date_local: date
    quantity: float
    cost_basis: float
    fill_id: int | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class DisposalRow:
    """One `disposals` row."""

    TABLE: ClassVar[str] = "disposals"
    ID_COLUMN: ClassVar[str | None] = "disposal_id"

    disposal_id: int | None = None
    lot_id: int
    account_id: str
    trade_at: datetime
    trade_date_local: date
    quantity: float
    proceeds: float
    realised_pnl: float
    tax_year: int
    fill_id: int | None = None
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class WashSaleFlagRow:
    """One `wash_sale_flags` row."""

    TABLE: ClassVar[str] = "wash_sale_flags"
    ID_COLUMN: ClassVar[str | None] = "flag_id"

    flag_id: int | None = None
    disposal_id: int
    replacement_lot_id: int
    matched_quantity: float
    disallowed_amount: float
    scanned_at: datetime
    known_at: datetime
    ingested_at: datetime


#: Every row type, by table, in `JOURNAL_TABLE_NAMES` order.
ROW_TYPES: dict[str, type[Any]] = {
    row_type.TABLE: row_type
    for row_type in (
        PaperWindowRow,
        PaperWindowStopRow,
        PaperRunRow,
        PaperRunResultRow,
        PaperPlanRow,
        RebalanceEventRow,
        PaperReportRow,
        SignalRow,
        DecisionRow,
        DecisionEventRow,
        OrderRow,
        OrderEventRow,
        FillRow,
        FillCursorRow,
        ResumeInvocationRow,
        OutcomeRow,
        PositionDailyRow,
        AdjustmentRow,
        ReconciliationRow,
        KillSwitchRow,
        OverrideRow,
        AlertRow,
        AlertDeliveryRow,
        LotRow,
        DisposalRow,
        WashSaleFlagRow,
    )
}


@dataclass(frozen=True)
class OrderedFill:
    """A live fill (not superseded) with its order's side, security and symbol."""

    fill: FillRow
    side: str
    security_id: str
    symbol: str
    run_id: int


def require_journal(conn: duckdb.DuckDBPyConnection) -> None:
    """Raise `JournalNotInitialised` unless every journal table exists."""
    (present,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name IN "
        f"({', '.join('?' for _ in JOURNAL_TABLE_NAMES)})",
        list(JOURNAL_TABLE_NAMES),
    ).fetchone()
    if present != len(JOURNAL_TABLE_NAMES):
        raise JournalNotInitialised(
            "the store has no paper-trading journal (schema version 4); any writing "
            "command migrates it to version 5"
        )


def _next_id(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def append(conn: duckdb.DuckDBPyConnection, row: JournalRow) -> int | None:
    """Insert `row` into its table and return its own id (assigned when None), or
    None for a table without one. Raises `ValueError` for a naive timestamp or
    `known_at` after `ingested_at`, `JournalNotInitialised` on a store without the
    journal, and DuckDB's constraint errors for anything the schema refuses. Runs in
    the caller's transaction."""
    if type(row) not in ROW_TYPES.values():
        raise TypeError(f"not a journal row type: {type(row).__name__}")
    known_at = ensure_tz_aware(row.known_at, field=f"{row.TABLE}.known_at")
    ingested_at = ensure_tz_aware(row.ingested_at, field=f"{row.TABLE}.ingested_at")
    if known_at > ingested_at:
        raise ValueError(
            f"{row.TABLE}: known_at {known_at.isoformat()} is after ingested_at "
            f"{ingested_at.isoformat()}"
        )
    require_journal(conn)
    values = {f.name: getattr(row, f.name) for f in fields(row)}  # type: ignore[arg-type]
    row_id = None
    if row.ID_COLUMN is not None:
        row_id = values[row.ID_COLUMN]
        if row_id is None:
            row_id = values[row.ID_COLUMN] = _next_id(conn, row.TABLE, row.ID_COLUMN)
    insert_row(conn, row.TABLE, values)
    return row_id


_FILL_COLUMNS = tuple(f.name for f in fields(FillRow))


def fills_for(
    conn: duckdb.DuckDBPyConnection,
    *,
    window_id: int | None = None,
    client_order_ids: Iterable[str] | None = None,
) -> list[OrderedFill]:
    """Every live fill (superseded rows hidden), oldest `fill_id` first, each with
    its order's side and security; optionally only a window's (through the order's
    run) or only some orders'. The only reader of `fills`: every consumer (ledger,
    lots, outcomes, reports, pages) goes through it."""
    require_journal(conn)
    ids = None if client_order_ids is None else sorted(set(client_order_ids))
    selected = ", ".join(f"f.{name}" for name in _FILL_COLUMNS)
    rows = conn.execute(
        f"SELECT {selected}, o.side, o.security_id, o.symbol, o.run_id, r.window_id "
        "FROM fills f LEFT JOIN orders o USING (client_order_id) "
        "LEFT JOIN paper_runs r ON r.run_id = o.run_id "
        "WHERE f.superseded_by IS NULL "
        "AND (? IS NULL OR r.window_id = ?) "
        "AND (? IS NULL OR list_contains(?, f.client_order_id)) "
        "ORDER BY f.fill_id",
        [window_id, window_id, ids, ids],
    ).fetchall()
    width = len(_FILL_COLUMNS)
    result: list[OrderedFill] = []
    for row in rows:
        fill = FillRow(**dict(zip(_FILL_COLUMNS, row[:width], strict=True)))
        side, security_id, symbol, run_id, _ = row[width:]
        if side is None:
            raise JournalIntegrityError(
                f"fill {fill.fill_id} ({fill.broker_fill_id}) has no orders row "
                f"for {fill.client_order_id!r}"
            )
        result.append(OrderedFill(fill, side, security_id, symbol, run_id))
    return result


def all_fill_ids(conn: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Every journaled `broker_fill_id`, superseded rows included: the collectors'
    insert-or-ignore check, so a fill is never journaled twice."""
    require_journal(conn)
    return frozenset(row[0] for row in conn.execute("SELECT broker_fill_id FROM fills").fetchall())
