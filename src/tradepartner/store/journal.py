"""The paper-trading journal: row types, the one writer and the fills accessor
(Phase 4 spec "Data / interfaces"; plan T49b).

The journal tables (`schema.JOURNAL_TABLE_NAMES`, schema version 5 on) are append-only:
this module inserts and reads, and never updates or deletes, as `store.registry`
does for the registry. A later fact is a new row, never an edit: an order's next
state is an `order_events` row, and a real fill arriving after the synthetic
`broker_status` residual that completed its order is journaled with `superseded_by`
= the synthetic's `fill_id` and hidden (spec req 8; only the new row can carry the
pointer).

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
  row. A `fills` row must also be stamped strictly after every `reconciliations`
  row's `known_at` (any window): `execution.ledger` treats a fill tied with its
  base reconciliation as inside that reconciliation's `broker_cash`, so a later
  fill on the same clock reading would silently drop out of the ledger's cash
  (#650). A tie needs a frozen or coarse clock; the writer refuses it.
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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, ClassVar, Protocol

import duckdb

from tradepartner.store.db import ensure_tz_aware, insert_row
from tradepartner.store.schema import JOURNAL_TABLE_NAMES, LATER_JOURNAL_TABLE_NAMES


class JournalNotInitialised(RuntimeError):
    """The store has no journal tables: a version-4 store that no write connection
    has migrated to the current version yet. Any writing command's `init_schema` migrates it."""


class JournalIntegrityError(RuntimeError):
    """The journal breaks one of its own invariants (a live fill with no order)."""


class JournalRow(Protocol):
    """What every row type has: its table and, when it has one, its own id column."""

    TABLE: ClassVar[str]
    ID_COLUMN: ClassVar[str | None]

    # Read-only members, so a frozen row type satisfies the protocol.
    @property
    def known_at(self) -> datetime: ...

    @property
    def ingested_at(self) -> datetime: ...


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
    accept_rejections: bool
    known_at: datetime
    ingested_at: datetime


@dataclass(frozen=True, kw_only=True)
class ResumeAcceptanceRow:
    """One `resume_acceptances` row: the rejection-cap verdicts a resume given
    `--accept-rejections` accepted, as a JSON list (`[]` for none; #472). Not a
    release: that is the `kill_switch` `released` row citing the `resume_id`."""

    TABLE: ClassVar[str] = "resume_acceptances"
    ID_COLUMN: ClassVar[str | None] = None

    resume_id: int
    accepted_json: str
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
ROW_TYPES: Mapping[str, type[Any]] = MappingProxyType(
    {
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
            ResumeAcceptanceRow,
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
)


@dataclass(frozen=True)
class OrderedFill:
    """A live fill (not superseded) with its order's side, security and symbol."""

    fill: FillRow
    side: str
    security_id: str
    symbol: str
    run_id: int
    window_id: int


#: The tables `require_journal` asks for: every journal table but those a
#: read-only connection to an older journal store may lack (#472).
_REQUIRED_TABLES = tuple(t for t in JOURNAL_TABLE_NAMES if t not in LATER_JOURNAL_TABLE_NAMES)


def require_journal(conn: duckdb.DuckDBPyConnection) -> None:
    """Raise `JournalNotInitialised` unless every journal table exists, those in
    `schema.LATER_JOURNAL_TABLE_NAMES` aside (a read-only connection to a
    version-7 store lacks them; a write connection has migrated)."""
    (present,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name IN "
        f"({', '.join('?' for _ in _REQUIRED_TABLES)})",
        list(_REQUIRED_TABLES),
    ).fetchone()
    if present != len(_REQUIRED_TABLES):
        raise JournalNotInitialised(
            "the store has no paper-trading journal (a journal table is missing); "
            "any writing command migrates it to the current version"
        )


def _next_id(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _require_after_reconciliations(conn: duckdb.DuckDBPyConnection, known_at: datetime) -> None:
    """Refuse a fill stamped at or before the latest reconciliation (#650): a
    ledger counts a fill's cash only when its `known_at` is strictly after its
    base reconciliation's, so a tied stamp would drop the fill from every later
    ledger's cash."""
    row = conn.execute("SELECT MAX(known_at) FROM reconciliations").fetchone()
    floor = None if row is None else row[0]
    if floor is not None and known_at <= floor:
        raise ValueError(
            f"fills: known_at {known_at.isoformat()} is not after the latest reconciliation's "
            f"{floor.isoformat()}; the clock did not advance past it"
        )


def append(conn: duckdb.DuckDBPyConnection, row: JournalRow) -> int | None:
    """Insert `row` into its table and return its own id (assigned when None), or
    None for a table without one. Raises `ValueError` for a naive timestamp or
    `known_at` after `ingested_at` or a fill not stamped after every reconciliation,
    `JournalNotInitialised` on a store without the
    journal, and DuckDB's constraint errors for anything the schema refuses. Runs in
    the caller's transaction."""
    if type(row) not in ROW_TYPES.values():
        raise TypeError(f"not a journal row type: {type(row).__name__}")
    table, id_column = type(row).TABLE, type(row).ID_COLUMN
    known_at = ensure_tz_aware(row.known_at, field=f"{table}.known_at")
    ingested_at = ensure_tz_aware(row.ingested_at, field=f"{table}.ingested_at")
    if known_at > ingested_at:
        raise ValueError(
            f"{table}: known_at {known_at.isoformat()} is after ingested_at "
            f"{ingested_at.isoformat()}"
        )
    require_journal(conn)
    if isinstance(row, FillRow):
        _require_after_reconciliations(conn, known_at)
    values = {f.name: getattr(row, f.name) for f in fields(row)}  # type: ignore[arg-type]
    row_id = None
    if id_column is not None:
        row_id = values[id_column]
        if row_id is None:
            row_id = values[id_column] = _next_id(conn, table, id_column)
    insert_row(conn, table, values)
    return row_id


_FILL_COLUMNS = tuple(f.name for f in fields(FillRow))


def _ids(client_order_ids: Iterable[str] | None) -> list[str] | None:
    """`client_order_ids` as a sorted SQL list parameter, None for "every order"."""
    if isinstance(client_order_ids, str):
        raise TypeError("client_order_ids must be a collection of ids, not one string")
    return None if client_order_ids is None else sorted(set(client_order_ids))


def _check_limit(limit: int | None) -> None:
    if limit is not None and limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")


def _orphan_fill_error(
    fill_id: int | None, broker_fill_id: str, client_order_id: str, side: str | None, run_id: Any
) -> JournalIntegrityError:
    missing = "orders row" if side is None else f"paper_runs row for run {run_id}"
    return JournalIntegrityError(
        f"fill {fill_id} ({broker_fill_id}) of {client_order_id!r} has no {missing}"
    )


def fills_for(
    conn: duckdb.DuckDBPyConnection,
    *,
    window_id: int | None = None,
    client_order_ids: Iterable[str] | None = None,
    limit: int | None = None,
) -> list[OrderedFill]:
    """Every live fill (superseded rows hidden), in `fill_id` order, each with
    its order's side and security; optionally only a window's (through the order's
    run) or only some orders'. The only reader of `fills`: every consumer (ledger,
    lots, outcomes, reports, pages) goes through it. `fill_id` order is collection
    order, not trade order (a lagging fill collected later has a higher id and an
    earlier `filled_at`): anything order-sensitive, such as FIFO lots, sorts by
    `(filled.filled_at, fill_id)` itself.

    `limit` (#435, for a page's bounded read; every other caller leaves it None
    and reads everything) keeps only the `limit` newest live fills in scope, by
    `fill_id`, bounded in SQL, and returns them newest first (`fill_id` descending).

    Fails closed: raises `JournalIntegrityError` when any superseded row points at
    something other than a live `broker_status` fill of its own order (so no fill is
    hidden by a bad pointer), and when a live fill in scope has no `orders` row or
    its order no `paper_runs` row (a fill whose window cannot be told is never
    filtered out of a window's ledger). Both checks cover the whole scope, not
    only the `limit` rows returned."""
    require_journal(conn)
    ids = _ids(client_order_ids)
    _check_limit(limit)
    bad = conn.execute(
        "SELECT f.fill_id, f.superseded_by FROM fills f "
        "LEFT JOIN fills s ON s.fill_id = f.superseded_by "
        "WHERE f.superseded_by IS NOT NULL AND (s.fill_id IS NULL "
        "OR s.source <> 'broker_status' OR s.client_order_id <> f.client_order_id "
        "OR s.superseded_by IS NOT NULL) ORDER BY f.fill_id"
    ).fetchall()
    if bad:
        pairs = ", ".join(f"{fill} -> {target}" for fill, target in bad)
        raise JournalIntegrityError(
            f"fills superseded by something other than a live broker_status fill of "
            f"their own order: {pairs}"
        )
    in_scope = (
        "FROM fills f LEFT JOIN orders o USING (client_order_id) "
        "LEFT JOIN paper_runs r ON r.run_id = o.run_id "
        "WHERE f.superseded_by IS NULL "
        "AND (? IS NULL OR r.run_id IS NULL OR r.window_id = ?) "
        "AND (? IS NULL OR list_contains(?, f.client_order_id)) "
    )
    scope = [window_id, window_id, ids, ids]
    if limit is not None:
        # The rows past the limit are never fetched, so check them in SQL first.
        orphan = conn.execute(
            "SELECT f.fill_id, f.broker_fill_id, f.client_order_id, o.side, o.run_id "
            f"{in_scope}AND (o.side IS NULL OR r.run_id IS NULL) "
            "ORDER BY f.fill_id LIMIT 1",
            scope,
        ).fetchone()
        if orphan is not None:
            raise _orphan_fill_error(*orphan)
    selected = ", ".join(f"f.{name}" for name in _FILL_COLUMNS)
    order = "ORDER BY f.fill_id" if limit is None else "ORDER BY f.fill_id DESC LIMIT ?"
    rows = conn.execute(
        f"SELECT {selected}, o.side, o.security_id, o.symbol, o.run_id, r.run_id, "
        f"r.window_id {in_scope}{order}",
        scope if limit is None else [*scope, limit],
    ).fetchall()
    width = len(_FILL_COLUMNS)
    result: list[OrderedFill] = []
    for row in rows:
        fill = FillRow(**dict(zip(_FILL_COLUMNS, row[:width], strict=True)))
        side, security_id, symbol, run_id, known_run, fill_window = row[width:]
        if side is None or known_run is None:
            raise _orphan_fill_error(
                fill.fill_id, fill.broker_fill_id, fill.client_order_id, side, run_id
            )
        result.append(OrderedFill(fill, side, security_id, symbol, run_id, fill_window))
    return result


def all_fill_ids(conn: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Every journaled `broker_fill_id`, superseded rows included: the collectors'
    insert-or-ignore check, so a fill is never journaled twice."""
    require_journal(conn)
    return frozenset(row[0] for row in conn.execute("SELECT broker_fill_id FROM fills").fetchall())


# --- Readers (plan T49c) -------------------------------------------------------------
#
# Reads only. Every reader calls `require_journal` first, so a version-4 store
# raises `JournalNotInitialised`. A row tied to a window through a column
# (`window_id`) or through its run (`run_id -> paper_runs.window_id`, or an order's
# run for `order_events` and `outcomes`) is read per window; later tasks never add a
# reader here, they query in their own module. Rows come back in `known_at` order,
# ties broken by `ingested_at` and then insertion order (DuckDB's `rowid`), or in id
# order for a table with its own id; "latest" always means latest by that order,
# never by a broker instant. No reader applies a `known_at` cutoff: a caller that
# needs state as of an instant (the ledger at close(S-1), say) filters itself.
# The one amendment (#435, ADR 0011's bounded page reads): `orders_for` takes an
# optional `limit` and `order_events_for` and `outcomes_for` an optional
# `client_order_ids`, so a page reads its capped rows in SQL; left at their
# defaults, every reader reads everything as before.

#: `order_events` statuses that end an order. Terminal is absorbing: an order with
#: any of these rows is terminal whatever rows follow it in `known_at` order.
TERMINAL_ORDER_STATUSES: tuple[str, ...] = ("filled", "expired", "rejected", "cancelled")
#: Statuses that show the broker has the order; an order with none of them and no
#: journaled fill is `pending` (spec req 4, "Unknown state"), whatever `cancel_*`
#: rows follow.
ACKNOWLEDGED_ORDER_STATUSES: tuple[str, ...] = ("accepted", "replay", *TERMINAL_ORDER_STATUSES)
#: `paper_window_stops` states that close a window (`requested` leaves it open).
CLOSING_STOP_STATES: tuple[str, ...] = ("closed", "abandoned")

_ORDER = "t.known_at, t.ingested_at, t.rowid"
_RUN_IN_WINDOW = "t.run_id IN (SELECT run_id FROM paper_runs WHERE window_id = ?)"
_ORDER_IN_WINDOW = (
    "(? IS NULL OR t.client_order_id IN (SELECT o.client_order_id FROM orders o "
    "JOIN paper_runs r ON r.run_id = o.run_id WHERE r.window_id = ?))"
)
_ORDER_IN_LIST = "(? IS NULL OR list_contains(?, t.client_order_id))"
#: `orders_for(limit=...)`'s order: newest first, ties by `client_order_id`.
_NEWEST_ORDER_FIRST = "t.known_at DESC, t.client_order_id DESC"


@dataclass(frozen=True)
class RunWithResult:
    """A `paper_runs` row and its `paper_run_results` row, None while unfinished."""

    run: PaperRunRow
    result: PaperRunResultRow | None


@dataclass(frozen=True)
class DecisionWithEvents:
    """A decision and its `decision_events` rows in `known_at` order."""

    decision: DecisionRow
    events: tuple[DecisionEventRow, ...]


@dataclass(frozen=True)
class OverrideWithConsumption:
    """An override and what consumed it: the decisions citing it (`exclude_name`,
    `keep_name`) or the `engaged` `kill_switch` rows citing it (`engage_kill_switch`)."""

    override: OverrideRow
    decision_ids: tuple[int, ...]
    kill_switch_event_ids: tuple[int, ...]

    @property
    def consumed(self) -> bool:
        """True once what its kind consumes cites it: an `engaged` `kill_switch` row
        for `engage_kill_switch`, a decision for the name kinds."""
        if self.override.kind == "engage_kill_switch":
            return bool(self.kill_switch_event_ids)
        return bool(self.decision_ids)


def _select[R](
    conn: duckdb.DuckDBPyConnection,
    row_type: type[R],
    where: str = "TRUE",
    params: Iterable[Any] = (),
    order: str = _ORDER,
    limit: int | None = None,
) -> list[R]:
    """Rows of `row_type`'s table (aliased `t`) matching `where`, as row objects,
    at most `limit` of them (in `order`) when given. Never `fills`: that table is
    read only through `fills_for`."""
    if row_type is FillRow:
        raise TypeError("fills is read only through fills_for")
    require_journal(conn)
    _check_limit(limit)
    names = [f.name for f in fields(row_type)]  # type: ignore[arg-type]
    columns = ", ".join(f't."{name}"' for name in names)
    table = row_type.TABLE  # type: ignore[attr-defined]
    sql = f"SELECT {columns} FROM {table} t WHERE {where} ORDER BY {order}"
    values = list(params)
    if limit is not None:
        sql += " LIMIT ?"
        values.append(limit)
    rows = conn.execute(sql, values).fetchall()
    return [row_type(**dict(zip(names, row, strict=True))) for row in rows]


def _open_windows(conn: duckdb.DuckDBPyConnection) -> list[PaperWindowRow]:
    return _select(
        conn,
        PaperWindowRow,
        "t.window_id NOT IN (SELECT window_id FROM paper_window_stops "
        "WHERE list_contains(?, state))",
        [list(CLOSING_STOP_STATES)],
        order="t.window_id",
    )


def open_window(conn: duckdb.DuckDBPyConnection) -> PaperWindowRow | None:
    """The open window (no `closed` or `abandoned` stop row), or None. Fails closed
    with `JournalIntegrityError` when more than one window is open."""
    windows = _open_windows(conn)
    if len(windows) > 1:
        ids = ", ".join(str(w.window_id) for w in windows)
        raise JournalIntegrityError(f"more than one open paper window: {ids}")
    return windows[0] if windows else None


def latest_window(conn: duckdb.DuckDBPyConnection) -> PaperWindowRow | None:
    """The window with the highest id, open or closed (what `paper check` and
    `paper report` target), or None before the first `paper start`."""
    windows = _select(conn, PaperWindowRow, order="t.window_id DESC")
    return windows[0] if windows else None


def window_stops_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[PaperWindowStopRow]:
    """The window's `paper_window_stops` rows."""
    return _select(conn, PaperWindowStopRow, "t.window_id = ?", [window_id])


def runs_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[RunWithResult]:
    """The window's runs in `run_id` order, each with its result (None while
    unfinished)."""
    runs = _select(conn, PaperRunRow, "t.window_id = ?", [window_id], order="t.run_id")
    results = {
        r.run_id: r
        for r in _select(
            conn,
            PaperRunResultRow,
            "t.run_id IN (SELECT run_id FROM paper_runs WHERE window_id = ?)",
            [window_id],
        )
    }
    return [RunWithResult(run, results.get(run.run_id)) for run in runs]  # type: ignore[arg-type]


def _require_orders_have_runs(conn: duckdb.DuckDBPyConnection) -> None:
    """Fail closed, as `fills_for` does: an order whose run has no `paper_runs` row
    would drop out of every per-window order read (and so out of the halt path's
    and reconciliation's view) instead of being seen."""
    require_journal(conn)
    orphans = conn.execute(
        "SELECT o.client_order_id FROM orders o LEFT JOIN paper_runs r "
        "ON r.run_id = o.run_id WHERE r.run_id IS NULL ORDER BY o.client_order_id"
    ).fetchall()
    if orphans:
        ids = ", ".join(repr(row[0]) for row in orphans)
        raise JournalIntegrityError(f"orders with no paper_runs row: {ids}")


def orders_for(
    conn: duckdb.DuckDBPyConnection, *, window_id: int | None, limit: int | None = None
) -> list[OrderRow]:
    """The window's orders (through their run), or every order when `window_id` is
    None (the collectors read own orders account-wide). Raises
    `JournalIntegrityError` when any order has no run (every order reader does).

    `limit` (#435, for a page's bounded read; every other caller leaves it None)
    keeps only the `limit` newest orders, bounded in SQL, and returns them newest
    first: `known_at` descending, ties by `client_order_id` descending."""
    _require_orders_have_runs(conn)
    if limit is None:
        return _select(conn, OrderRow, _ORDER_IN_WINDOW, [window_id, window_id])
    return _select(
        conn,
        OrderRow,
        _ORDER_IN_WINDOW,
        [window_id, window_id],
        order=_NEWEST_ORDER_FIRST,
        limit=limit,
    )


def order_events_for(
    conn: duckdb.DuckDBPyConnection,
    *,
    window_id: int | None,
    client_order_ids: Iterable[str] | None = None,
) -> list[OrderEventRow]:
    """Every `order_events` row of the window's orders (or of every order), or,
    given `client_order_ids` (#435), only those orders' rows within that scope."""
    _require_orders_have_runs(conn)
    ids = _ids(client_order_ids)
    return _select(
        conn,
        OrderEventRow,
        f"{_ORDER_IN_WINDOW} AND {_ORDER_IN_LIST}",
        [window_id, window_id, ids, ids],
    )


def latest_order_events(
    conn: duckdb.DuckDBPyConnection, *, window_id: int | None
) -> dict[str, OrderEventRow]:
    """Each order's latest event by `known_at` (not insertion order, not the
    broker's `event_at`), keyed by `client_order_id`; an order with no event is
    absent. Not an order's state: a `cancel_noop` journaled after `expired` is the
    latest row of a terminal order. State comes from `non_terminal_orders` and
    `pending_orders`."""
    return {e.client_order_id: e for e in order_events_for(conn, window_id=window_id)}


def _orders_where(
    conn: duckdb.DuckDBPyConnection, window_id: int | None, statuses: tuple[str, ...]
) -> list[OrderRow]:
    _require_orders_have_runs(conn)
    return _select(
        conn,
        OrderRow,
        f"{_ORDER_IN_WINDOW} AND t.client_order_id NOT IN (SELECT client_order_id "
        "FROM order_events WHERE list_contains(?, status))",
        [window_id, window_id, list(statuses)],
    )


def non_terminal_orders(
    conn: duckdb.DuckDBPyConnection, *, window_id: int | None
) -> list[OrderRow]:
    """Orders with no terminal event (`pending` ones included), for the window or,
    with None, account-wide."""
    return _orders_where(conn, window_id, TERMINAL_ORDER_STATUSES)


def pending_orders(conn: duckdb.DuckDBPyConnection, *, window_id: int | None) -> list[OrderRow]:
    """Orders with no broker-acknowledged event (`accepted`, `replay` or terminal)
    and no journaled fill (read through `fills_for`), whatever `cancel_*` rows
    follow: settled only by `paper resume`."""
    candidates = _orders_where(conn, window_id, ACKNOWLEDGED_ORDER_STATUSES)
    if not candidates:
        return []
    filled = {
        f.fill.client_order_id
        for f in fills_for(conn, client_order_ids=[o.client_order_id for o in candidates])
    }
    return [o for o in candidates if o.client_order_id not in filled]


def orders_on_session(
    conn: duckdb.DuckDBPyConnection, session: date, security_id: str, side: str
) -> list[OrderRow]:
    """Every order on `session` for (`security_id`, `side`), across decisions and
    windows: the attempt count behind `client_order_id`. Not filtered by window,
    because ids are unique store-wide and a window filter could re-issue one."""
    return _select(
        conn,
        OrderRow,
        "t.session = ? AND t.security_id = ? AND t.side = ?",
        [session, security_id, side],
    )


def decisions_for(
    conn: duckdb.DuckDBPyConnection, window_id: int, *, rebalance_session: date | None = None
) -> list[DecisionWithEvents]:
    """The window's decisions in `decision_id` order, each with its events; only
    one rebalance's when `rebalance_session` is given (forced exits, which have
    none, are then left out)."""
    where = _RUN_IN_WINDOW
    params: list[Any] = [window_id]
    if rebalance_session is not None:
        where += " AND t.rebalance_session = ?"
        params.append(rebalance_session)
    decisions = _select(conn, DecisionRow, where, params, order="t.decision_id")
    events: dict[int, list[DecisionEventRow]] = {}
    for event in _select(
        conn,
        DecisionEventRow,
        f"t.decision_id IN (SELECT decision_id FROM decisions t WHERE {_RUN_IN_WINDOW})",
        [window_id],
    ):
        events.setdefault(event.decision_id, []).append(event)
    return [
        DecisionWithEvents(d, tuple(events.get(d.decision_id, ())))  # type: ignore[arg-type]
        for d in decisions
    ]


def fill_cursors(conn: duckdb.DuckDBPyConnection) -> list[FillCursorRow]:
    """Every `fill_cursors` row (account-wide: collectors read `fills(since)` for
    the whole account)."""
    return _select(conn, FillCursorRow)


def latest_collected_through(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    """The latest `collected_through` across every `fill_cursors` row, or None."""
    through = [c.collected_through for c in fill_cursors(conn)]
    return max(through) if through else None


def kill_switch_events_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[KillSwitchRow]:
    """The window's `kill_switch` rows in `known_at` order (then insertion order).
    Rows written on the halt path after a `ClockError` carry `utc_now()` stamps,
    so the caller deriving the state must not trust `known_at` order alone there
    (`event_id` is write order)."""
    return _select(conn, KillSwitchRow, "t.window_id = ?", [window_id])


def positions_daily_for(
    conn: duckdb.DuckDBPyConnection, window_id: int, *, after: date | None = None
) -> list[PositionDailyRow]:
    """The window's marks (through their run) in session order, only those after
    `after` when given."""
    return _select(
        conn,
        PositionDailyRow,
        f"{_RUN_IN_WINDOW} AND (? IS NULL OR t.session > ?)",
        [window_id, after, after],
        order=f"t.session, {_ORDER}",
    )


def last_marked_session(conn: duckdb.DuckDBPyConnection, window_id: int) -> date | None:
    """The latest session the window has a mark for, or None: the mark step
    writes every session after it through S-1."""
    marks = positions_daily_for(conn, window_id)
    return marks[-1].session if marks else None


def adjustments_for(
    conn: duckdb.DuckDBPyConnection, window_id: int, *, through_session: date | None = None
) -> list[AdjustmentRow]:
    """The window's `adjustments` rows, only those on or before `through_session`
    when given."""
    return _select(
        conn,
        AdjustmentRow,
        "t.window_id = ? AND (? IS NULL OR t.session <= ?)",
        [window_id, through_session, through_session],
    )


def reconciliations_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[ReconciliationRow]:
    """The window's `reconciliations` rows."""
    return _select(conn, ReconciliationRow, "t.window_id = ?", [window_id])


def last_ok_reconciliation(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> ReconciliationRow | None:
    """The window's latest `ok` reconciliation (the ledger's cash base), or None."""
    rows = _select(conn, ReconciliationRow, "t.window_id = ? AND t.status = 'ok'", [window_id])
    return rows[-1] if rows else None


def rebalance_events_for(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> list[RebalanceEventRow]:
    """The window's `rebalance_events` rows (through their run)."""
    return _select(conn, RebalanceEventRow, _RUN_IN_WINDOW, [window_id])


def plans_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[PaperPlanRow]:
    """The window's `paper_plans` rows (through their run)."""
    return _select(conn, PaperPlanRow, _RUN_IN_WINDOW, [window_id])


def signals_for(conn: duckdb.DuckDBPyConnection, run_id: int) -> list[SignalRow]:
    """One planning run's `signals` rows."""
    return _select(conn, SignalRow, "t.run_id = ?", [run_id])


def overrides_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[OverrideWithConsumption]:
    """The window's overrides in `override_id` order, each with the decisions and
    `engaged` `kill_switch` rows citing it (consumption is visible only through
    those; a `released` row citing it consumes nothing)."""
    overrides = _select(conn, OverrideRow, "t.window_id = ?", [window_id], order="t.override_id")
    cited = "t.override_id IN (SELECT override_id FROM overrides WHERE window_id = ?)"
    decisions: dict[int, list[int]] = {}
    for d in _select(conn, DecisionRow, cited, [window_id], order="t.decision_id"):
        decisions.setdefault(d.override_id, []).append(d.decision_id)  # type: ignore[arg-type]
    engaged: dict[int, list[int]] = {}
    engaged_rows = f"{cited} AND t.state = 'engaged' AND t.window_id = ?"
    for k in _select(conn, KillSwitchRow, engaged_rows, [window_id, window_id], order="t.event_id"):
        engaged.setdefault(k.override_id, []).append(k.event_id)  # type: ignore[arg-type]
    return [
        OverrideWithConsumption(
            o,
            tuple(decisions.get(o.override_id, ())),  # type: ignore[arg-type]
            tuple(engaged.get(o.override_id, ())),  # type: ignore[arg-type]
        )
        for o in overrides
    ]


def unconsumed_kill_switch_overrides(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> list[OverrideRow]:
    """The window's `engage_kill_switch` overrides no `kill_switch` row cites yet:
    they count as engaged until a run or phase boundary appends that row."""
    return [
        o.override
        for o in overrides_for(conn, window_id)
        if o.override.kind == "engage_kill_switch" and not o.kill_switch_event_ids
    ]


def paper_reports_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[PaperReportRow]:
    """The window's `paper_reports` rows."""
    return _select(conn, PaperReportRow, "t.window_id = ?", [window_id])


def resume_invocations(conn: duckdb.DuckDBPyConnection) -> list[ResumeInvocationRow]:
    """Every `resume_invocations` row in `resume_id` order (the table has no
    window; its outcome is the `kill_switch` row carrying the `resume_id`)."""
    return _select(conn, ResumeInvocationRow, order="t.resume_id")


def resume_acceptances(conn: duckdb.DuckDBPyConnection) -> list[ResumeAcceptanceRow]:
    """Every `resume_acceptances` row in `resume_id` order (#472)."""
    return _select(conn, ResumeAcceptanceRow, order="t.resume_id")


def alerts_for(conn: duckdb.DuckDBPyConnection, *, kind: str, session: date) -> list[AlertRow]:
    """The alerts under one dedupe key (`kind`, `alerts.session`)."""
    return _select(
        conn, AlertRow, "t.kind = ? AND t.session = ?", [kind, session], order="t.alert_id"
    )


def outcomes_for(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    *,
    client_order_ids: Iterable[str] | None = None,
) -> list[OutcomeRow]:
    """The window's `outcomes` rows (through their order's run), or, given
    `client_order_ids` (#435), only those orders' rows of the window."""
    ids = _ids(client_order_ids)
    return _select(
        conn,
        OutcomeRow,
        f"{_ORDER_IN_WINDOW} AND {_ORDER_IN_LIST}",
        [window_id, window_id, ids, ids],
    )
