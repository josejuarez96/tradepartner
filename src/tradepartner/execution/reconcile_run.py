"""Reconciliation against the store and the broker (Phase 4 spec req 6; plan T61).

`reconcile.compare` (T55) is pure. This module gathers its inputs, writes what
it decides and raises on a mismatch. A run's steps 4 and 8, `paper reconcile`,
`paper stop` and `paper resume` all use the same `reconcile_now`.

**`reconcile_now`**, in order:

1. Read the window's non-terminal orders from the journal, and which of them
   are `pending` (never acknowledged), with the live fills of each.
2. Read the broker: `positions()`, `open_orders()`, `account()`, and one
   `get_order` per acknowledged non-terminal order, the `lagging` input. A
   `pending` order is never read: only `paper resume` settles it (req 4). Read
   `cash_activities(since = the day before the window's New York start date)`
   after `account()` (#1583, T183, spec item 1), the booked-`FEE` rule's input.
3. Build the ledger stated through S, or through the clock's New York date
   while no session has opened since S (a weekend `paper reconcile`, so its
   own earlier `ok` row stays the cash base), from the journal rows known at
   the caller's `as_of` (below) with splits from
   `live_actions_as_of(close(S-1))`, and the explanations
   (`explanations_as_of`, at the same `as_of`), then call `compare`.
4. In one write chunk, append the `reconciliations` row and the `adjustments`
   rows an `ok` result's explanations imply. Each row is stamped with the
   same clock reading, so every adjustment is inside the reconciliation that
   re-bases the ledger's cash (`execution.ledger`: a row whose `known_at`
   equals the base's is counted in it, never twice).
5. On `mismatch`, raise `ReconciliationError` after the row is committed.
   Engaging the switch and alerting belong to the caller: the run's halt
   path (T60) for steps 4 and 8, `reconcile_command` for `paper reconcile`.

**The journal cut (#488).** Every fill, order, adjustment and `ok`
reconciliation the ledger and the explanations read is cut at one explicit
`as_of` instant: a row counts only when its `known_at <= as_of`. Store facts
(listings, delistings, actions, bars) are always cut at close(S-1), whatever
`as_of` is. So the result depends on (S, `as_of`) and on nothing journaled
after `as_of`, whenever it is called. The caller chooses `as_of` and always
passes it: `paper reconcile` and `paper resume` pass a clock reading taken
just before the call (every row they and earlier runs journaled), and a
run's post-trade read (step 8) passes its own clock reading, so its own
fills on S are explained. `as_of` may not be later than `reconcile_now`'s
clock reading. The open-orders and `lagging` inputs (step 1) are the
journal's current state, which the broker's current open orders are compared
with.

**`explanations_as_of`** is the read-only half (T63g's no-look-ahead suite
calls it on a truncated store). Every store fact it reads is from rows with
`known_at <= close(S-1)`, and every journal row from rows with `known_at <=
as_of`:

- `symbols`: each name the window's journal holds (fills, orders,
  adjustments) maps to the ticker of its current listing at S, meaning the
  listing with the latest `valid_from <= S`. A name with no listing falls
  back to its latest order's symbol. Each broker symbol no journal name
  claims maps to the one security whose current listing has that ticker and
  is not delisted, and stays unmapped (compare's `unknown_symbol`) when
  there is none or more than one. When two journal names share a ticker,
  only the one not delisted keeps it; when that does not settle it, neither
  does, and a held one becomes compare's `unknown_security`. A held name whose
  ticker changed in the store at close(S-1) maps to the broker's one new
  symbol (#1761), recorded on the row's `symbol_changes`.
- `ended`: journal names whose current listing is `delisted` at close(S-1)
  (`store.delistings.listing_ends_as_of`; a transfer is not an end).
- `dividends`: per held name, the cash due from `dividend` actions with an
  ex-date after the cash base's session and on or before S. An
  equal-amount group (one security, ex-date and amount under several live
  ids, #1613) is read through the reviewed list
  (`store.dividend_reviews`): a listed `duplicate` not yet repaired counts
  once, a listed `distinct` or `unresolved` pair as stored, and any other
  group on a held name is never summed but named in `dividend_findings`,
  which `compare` reports as an `unexplained_dividend_group` mismatch
  (#1625): a data finding, failing closed. The base is the
  window's latest `ok` reconciliation stated on or before S, or the window's
  start. The cash due is the amount per share times the ledger's holding at
  the session before the ex-date. A dividend the journal already holds as a
  `dividend_cash` row dated on or after its ex-date is never offered again.
- `spinoffs`: each stored `spinoff` action known at close(S-1) with an ex-date
  on or before S and whose parent the ledger held on the session before the
  ex-date (still held, unchanged, at the stated session), keyed by child
  `security_id` to `(parent security_id, shares per parent share)` (the
  `execution.window` convention). There is no lower ex-date bound: a spin-off's
  row is stamped on the close after Alpaca's date-only `process_date`, so it can
  arrive weeks after the ex-date and a later `ok` reconciliation must not hide
  it. A child the broker books is journaled a `spinoff_receipt`; a row known
  only after close(S-1), a wrong ratio, a child for a parent the ledger never
  held, or a parent moved since the ex-date stays an `unknown_symbol` or
  `broker_only_position` mismatch (never a guess).
- `reference_prices`: the raw close of the latest bar at or before S-1 per
  name, which bounds an ended name's proceeds and a pending order's reach.
- `mergers` (paper spec amendment #1762 item 1, T249): the open mergers, from
  `mergers_as_of(close(S-1))` for the journal's names. A merger is identified by
  its Alpaca id, which every `corporate_action_cash` or `merger_receipt` row names
  in `explanation_json` (`merger_id`). It is open at S when `effective_date <=
  S` and either (a) the ledger stated through S still holds its acquiree, long
  or short, or (b) the removal is journaled (its own row, or the ended-listing
  band's `corporate_action_cash` removal row for the acquiree, linked by
  `security_id` and stated no earlier than the session before
  `effective_date`) and the merger is not closed. It closes when the journaled cash
  is within the cash bound of the due, or once an `ok` reconciliation after its
  window (`payable_date`, else `effective_date`, plus the frozen
  `risk.reconcile_merger_lag_sessions` sessions) has run with the row known and
  the removal journaled; a row first known after its window's close opens
  nothing under (b). `final` marks the first completed (`ok` or `mismatch`)
  reconciliation after the window with the row known, unless an earlier one
  already ran with the removal journaled and no lagging or pending order (the
  check never runs beside one), or named the merger's due: that one checks the
  due once. A reconciliation row's session is `command_session` of
  its `at`. When one acquiree has several open ids, the latest
  `effective_date` (then id) is passed. The acquirers join the names mapped to
  broker symbols. Two ids for one acquiree and one `effective_date`
  (`merger_conflict`) are never passed: the first reconciliation that knows
  both, while either is open or has rows journaled, raises the pair in
  `merger_conflicts`; any reconciliation row that already raised it (any
  status) closes the pair for good.

**`reconcile_command`** is `paper reconcile --book <book>` (`book_id`, default
`paper.book_id`; ADR 0017 B.3 to B.5, plan T155). It takes the book's run lock
(T59), refuses with `NoWindowError` when no window is open for the book (no
broker call, no write; another book's window is never read), and runs one
`reconcile_now` for the clock's session (the latest XNYS session on or before
the clock's New York date), with the window's
frozen `risk` section. A mismatch is a system fault (spec Definitions), so it
appends the `engaged` row (source `fault`, fault type
`ReconciliationError`) before re-raising. It writes no alert: a
`reconciliation` alert is run-scoped (`execution.alerts`) and this command
has no run.

`settle_pending_unresolved` is the read-back of a `pending_unresolved` order
(T202b, paper spec amendment 2026-10-18 item 4 (c)): `reconcile_command` runs it
before `reconcile_now`, as the run's step 3 and `paper resume` do before their
collections.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from pydantic import ValidationError

from tradepartner.adapters.broker import Account, Broker, Order, Position, UnknownOrderError
from tradepartner.calendar import is_session, next_session, previous_session, session_close
from tradepartner.config import RiskConfig, Settings, render_validation_errors
from tradepartner.errors import ClockError, LimitBreachError, ReconciliationError
from tradepartner.execution import switch
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.lock import resolve_book, run_lock
from tradepartner.execution.plan import current_listings
from tradepartner.execution.reconcile import (
    MERGER_CONFLICT,
    MISMATCH,
    OK,
    OVERDUE_DELISTING_CASH,
    DelistedCash,
    Explanations,
    JournalOpenOrder,
    OpenMerger,
    Reconciliation,
    _cash_bound,
    compare,
    merger_due_detail,
)
from tradepartner.execution.risk import order_shape_violation
from tradepartner.execution.symbols import broker_symbol
from tradepartner.store import registry
from tradepartner.store.asof import (
    live_actions_as_of,
    mergers_as_of,
    prices_as_of,
    symbol_changes_as_of,
)
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.dividend_reviews import (
    ReviewedGroup,
    equal_amount_groups,
    reviewed_groups,
)
from tradepartner.store.journal import (
    AdjustmentRow,
    OrderedFill,
    OrderEventRow,
    OrderRow,
    PaperWindowRow,
    ReconciliationRow,
    adjustments_for,
    append,
    fills_for,
    non_terminal_orders,
    open_window,
    order_events_for,
    orders_for,
    pending_orders,
    reconciliations_for,
)
from tradepartner.timeutil import ensure_tz_aware_utc

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

_NEW_YORK = ZoneInfo("America/New_York")
_DIVIDEND = "dividend"
_DIVIDEND_CASH = "dividend_cash"
_SPLIT = "split"
_SPINOFF = "spinoff"
_RISK_PREFIX = "risk."
_CORPORATE_ACTION_CASH = "corporate_action_cash"
#: The reconciliation statuses that ran to the end: a merger's last check counts
#: only these, never a `fills_lagging` or `pending_unresolved` one (T249).
_COMPLETED = frozenset({OK, MISMATCH})
_DEFAULT_RISK = RiskConfig()
_FAULT_TYPE = ReconciliationError.__name__


class NoWindowError(RuntimeError):
    """`paper reconcile` with no open window (spec acceptance "`paper run`
    before any `paper start`"): refused, nothing written."""


@dataclass(frozen=True)
class _JournalState:
    """The window's journal rows a reconciliation reads."""

    fills: list[OrderedFill]
    orders: list[OrderRow]
    adjustments: list[AdjustmentRow]
    ok_rows: list[ReconciliationRow]
    #: Every reconciliation row known at the cut, any status (T249: a merger's
    #: last check and a conflicting pair's one halt are read from them).
    rows: list[ReconciliationRow] = field(default_factory=list)

    def base(self, through: date) -> ReconciliationRow | None:
        """The latest `ok` reconciliation stated on or before `through`: the
        ledger's cash base for that session. A later `ok` row (a weekend
        `paper reconcile` stated for Friday) cannot state `through`."""
        stated = [r for r in self.ok_rows if r.at.astimezone(_NEW_YORK).date() <= through]
        return stated[-1] if stated else None


def verify_frozen_sha256(window: PaperWindowRow) -> None:
    """Raise `LimitBreachError` unless `window.frozen_json` hashes to its
    `frozen_sha256` (#1984). The hash is `registry.params_sha256` of the parsed
    object, the canonical form `paper start` froze, so a whitespace-only change
    passes and any changed value, added key or unparseable text does not. The
    journal tables are append-only and nothing in `src` updates `paper_windows`:
    a mismatch means the row was edited in place, so the limits it states are not
    the ones the owner froze, and the run must not trade under them."""
    try:
        parsed = json.loads(window.frozen_json)
        actual = registry.params_sha256(parsed)
    except (ValueError, TypeError) as exc:
        raise LimitBreachError(
            f"window {window.window_id} frozen_json cannot be hashed ({type(exc).__name__}); "
            "it does not match frozen_sha256"
        ) from exc
    if actual != window.frozen_sha256:
        raise LimitBreachError(
            f"window {window.window_id} frozen_json hashes to {actual}, not its "
            f"frozen_sha256 {window.frozen_sha256}: the frozen limits were edited after "
            "`paper start`"
        )


def frozen_risk(window: PaperWindowRow) -> RiskConfig:
    """The window's frozen `risk` section from `frozen_json`.

    The spec has `paper start` canonicalise the frozen values "as the registry
    does for hypothesis parameters": one flat JSON object with dotted keys
    (`"risk.max_drawdown"`, `"paper.tracking_k"`, ...). Every `RiskConfig` field
    must be present under its `risk.` key, because the whole section is frozen.
    An unknown `risk.` key, a value the model refuses, or anything other than
    that object raises `ValueError`. Other keys are not read here."""
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"window {window.window_id} frozen_json is not an object")
    risk = {k[len(_RISK_PREFIX) :]: v for k, v in parsed.items() if k.startswith(_RISK_PREFIX)}
    # A window frozen before T202 has no `risk.max_skip_share`: read it as 0, so the
    # skip cap stays max(max_skips_per_run, 0) (paper spec amendment 2026-10-18 item 3).
    # It, the merger lag and the delisting-cash premium are the keys back-filled;
    # every other missing key still refuses.
    risk.setdefault("max_skip_share", 0.0)
    # A window frozen before T249 has no `risk.reconcile_merger_lag_sessions`: read
    # it at its default (paper spec amendment #1762 item 10).
    risk.setdefault("reconcile_merger_lag_sessions", _DEFAULT_RISK.reconcile_merger_lag_sessions)
    # A window frozen before #411 has no `risk.delisting_cash_max_premium`: same.
    risk.setdefault("delisting_cash_max_premium", _DEFAULT_RISK.delisting_cash_max_premium)
    missing = sorted(set(RiskConfig.model_fields) - set(risk))
    if missing:
        raise ValueError(f"window {window.window_id} frozen_json lacks risk keys {missing}")
    try:
        return RiskConfig.model_validate(risk)
    except ValidationError as exc:
        # Risk limits hold no secret: put the stored value back in the message (#1093).
        detail = render_validation_errors(exc, show_input=lambda _key: True)
        raise ValueError(f"window {window.window_id} frozen risk section: {detail}") from exc


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    """One clock reading, tz-aware UTC, or `ClockError` (ADR 0007 point 4)."""
    try:
        reading = clock()
        if not isinstance(reading, datetime):
            raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
        return ensure_tz_aware_utc(reading, field_name="clock")
    except Exception as exc:
        raise ClockError(f"clock failed: {type(exc).__name__}") from exc


def _window_id(window: PaperWindowRow) -> int:
    if window.window_id is None:
        raise ValueError("a paper_windows row without a window_id: pass a row read from the store")
    return window.window_id


def _cut(session: date) -> datetime:
    """close(S-1): the latest instant a fact may be known at to explain S."""
    if isinstance(session, datetime) or not is_session(session):
        raise ValueError(f"session must be an XNYS session date, got {session!r}")
    return session_close(previous_session(session))


def _stated_through(session: date, now: datetime) -> date:
    """The day the ledger is stated for: the clock's New York date while no
    session has opened since S (a weekend `paper reconcile` for Friday), else
    S. No fill, ex-date or split falls between them, so positions are the
    same; the difference is that an `ok` row written that weekend can still be
    the cash base of the next one."""
    day = now.astimezone(_NEW_YORK).date()
    return day if day > session and command_session(now) == session else session


def _journal_state(
    conn: duckdb.DuckDBPyConnection, window_id: int, as_of: datetime
) -> _JournalState:
    """The window's journal rows known at `as_of` (`known_at <= as_of`): the one
    place the ledger's and the explanations' journal input is cut (#488)."""
    rows = [r for r in reconciliations_for(conn, window_id) if r.known_at <= as_of]
    return _JournalState(
        fills=[f for f in fills_for(conn, window_id=window_id) if f.fill.known_at <= as_of],
        orders=[o for o in orders_for(conn, window_id=window_id) if o.known_at <= as_of],
        adjustments=[a for a in adjustments_for(conn, window_id) if a.known_at <= as_of],
        ok_rows=[r for r in rows if r.status == OK],
        rows=rows,
    )


def _ledger(
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    through: date,
    tolerance: float,
) -> Ledger:
    return from_journal(
        state.fills,
        state.orders,
        state.adjustments,
        actions,
        state.base(through),
        window.starting_cash,
        through,
        window_id=_window_id(window),
        quantity_tolerance=tolerance,
    )


def _applied_change(
    name: str,
    symbol: str,
    changes: Mapping[str, Sequence[tuple[str, str]]],
    broker_symbols: Collection[str],
) -> tuple[str, str] | None:
    """The one ticker change this reconciliation applies for `name` (#1761), or
    None: the name's current symbol is a change's old symbol and exactly one
    distinct new symbol is on the broker (`broker_symbols`). A name with no
    change, one the broker has not renamed yet, and one with two candidate new
    symbols apply nothing, so the position stays a mismatch, never a guess."""
    candidates = {new for old, new in changes.get(name, ()) if old == symbol}
    if len(candidates) != 1:
        return None
    (new,) = candidates
    return (symbol, new) if new in broker_symbols else None


def _symbol_change_candidates(
    conn: duckdb.DuckDBPyConnection, cut: datetime, names: Iterable[str]
) -> dict[str, list[tuple[str, str]]]:
    """Per name, the `(old symbol, new symbol)` ticker changes the store held at
    close(S-1) (`store.asof.symbol_changes_as_of`, #1761); the read is at `cut`
    like every store fact, so a change known only after it is invisible."""
    rows = symbol_changes_as_of(conn, cut, sorted(set(names)))
    changes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows.iter_rows(named=True):
        changes[row["security_id"]].append((row["old_symbol"], row["new_symbol"]))
    return changes


def _symbols(
    names: Iterable[str],
    order_symbols: Mapping[str, str],
    current: Mapping[str, Mapping[str, Any]],
    broker_symbols: Collection[str],
    class_symbols: Mapping[str, str] | None,
    changes: Mapping[str, Sequence[tuple[str, str]]],
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """`security_id` to broker symbol, one name per symbol, and the ticker
    changes applied to it (module docstring). A listing's ticker is read in its
    broker form (`broker_symbol`, #1729), so a `GEF.B` position matches the
    `GEF-B` listing. A held name whose stored change (at close(S-1)) puts it
    under a new symbol the broker holds maps to that symbol (#1761), so the
    ledger's old-symbol position matches, and the applied changes come back for
    the row's record."""
    claims: dict[str, list[str]] = defaultdict(list)
    applied: dict[str, tuple[str, str]] = {}
    for name in sorted(set(names)):
        listing = current.get(name)
        symbol = (
            broker_symbol(listing, class_symbols)
            if listing is not None
            else order_symbols.get(name)
        )
        change = (
            _applied_change(name, symbol, changes, broker_symbols) if symbol is not None else None
        )
        if change is not None:
            applied[name] = change
            symbol = change[1]
        if symbol is not None:
            claims[symbol].append(name)
    symbols: dict[str, str] = {}
    for symbol, claimants in claims.items():
        live = [n for n in claimants if current.get(n, {}).get("status") != DELISTED]
        chosen = claimants if len(claimants) == 1 else live
        if len(chosen) == 1:
            symbols[chosen[0]] = symbol
    claimed = set(claims)
    for symbol in sorted(set(broker_symbols) - claimed):
        matches = [
            name
            for name, row in current.items()
            if broker_symbol(row, class_symbols) == symbol and row["status"] != DELISTED
        ]
        if len(matches) == 1:
            symbols[matches[0]] = symbol
    # Only a change the final map kept is recorded: a later pass (the
    # unclaimed-symbol rule) can put the name back on its listing's symbol
    # when the broker holds both, and the row must not claim it was applied.
    return symbols, {
        name: change for name, change in applied.items() if symbols.get(name) == change[1]
    }


def _reference_prices(
    conn: duckdb.DuckDBPyConnection, cut: datetime, names: Sequence[str], before: date
) -> dict[str, float]:
    """The raw close of each name's latest bar on or before `before`, known at `cut`."""
    if not names:
        return {}
    bars = prices_as_of(conn, cut, list(names)).filter(pl.col("session") <= before)
    latest = bars.sort("session").group_by("security_id").last()
    return {row["security_id"]: float(row["close"]) for row in latest.iter_rows(named=True)}


def _dividends(
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    session: date,
    tolerance: float,
    through: date,
    reviewed: Sequence[ReviewedGroup],
) -> tuple[dict[str, float], dict[str, str]]:
    """Cash due per name for dividends with ex-date in (base session, S] (negative
    for a name held short before the ex-date), less
    any the journal already holds as `dividend_cash` on or after the ex-date,
    and the held names with an equal-amount dividend group the reviewed list
    does not explain (#1625, #1613), each with its detail.

    Each `(security_id, ex_date, amount)` held by more than one live identity
    is classed by `store.dividend_reviews.equal_amount_groups`: a listed
    `duplicate` not yet repaired (`pending`) counts once (its retract id's row
    is skipped); a listed `distinct` or `unresolved` pair counts as stored; any
    other group (`violation`) is never summed and, on a held name, is a
    finding, so the caller fails closed rather than guess one payment or two."""
    base = state.base(through)
    stated = base.at if base is not None else window.started_at
    after = stated.astimezone(_NEW_YORK).date()
    journaled: dict[str, list[date]] = defaultdict(list)
    for adjustment in state.adjustments:
        if adjustment.kind == _DIVIDEND_CASH and adjustment.security_id is not None:
            journaled[adjustment.security_id].append(adjustment.session)
    rows = actions.filter(
        (pl.col("action_type") == _DIVIDEND)
        & (pl.col("ex_date") > after)
        & (pl.col("ex_date") <= session)
    )
    skipped: set[tuple[str, str]] = set()
    unexplained: dict[tuple[str, date, float], str] = {}
    for group in equal_amount_groups(rows, reviewed):
        if group.group_class == "pending" and group.reviewed is not None:
            skipped.add((group.security_id, group.reviewed.retract_source_action_id))
        elif group.group_class == "violation":
            ids = ", ".join(
                f"{i.source_action_id} known {i.known_at.isoformat()}" for i in group.identities
            )
            unexplained[group.key] = (
                f"{len(group.identities)} live dividend records of {group.amount!r} with "
                f"ex-date {group.ex_date} ({ids}) that the reviewed list does not explain"
            )
    due: dict[str, float] = defaultdict(float)
    findings: dict[str, list[str]] = defaultdict(list)
    holdings: dict[date, Ledger] = {}
    for row in rows.iter_rows(named=True):
        if (row["security_id"], row["source_action_id"]) in skipped:
            continue
        if any(day >= row["ex_date"] for day in journaled.get(row["security_id"], ())):
            continue
        before = previous_session(row["ex_date"])
        if before not in holdings:
            holdings[before] = _ledger(state, actions, window, before, tolerance)
        held = holdings[before].positions.get(row["security_id"], 0.0)
        short = holdings[before].short_holdings.get(row["security_id"], 0.0)
        if held > tolerance or short > tolerance:
            key = (row["security_id"], row["ex_date"], float(row["ratio_or_amount"]))
            if key in unexplained:
                if unexplained[key] not in findings[row["security_id"]]:
                    findings[row["security_id"]].append(unexplained[key])
                continue
            # A short owes the dividend: -quantity x amount (#1928, item 10).
            due[row["security_id"]] += (
                held * float(row["ratio_or_amount"])
                if held > tolerance
                else -short * float(row["ratio_or_amount"])
            )
    # `compare` signs a due against the book at S: a short's due is negative only
    # while the name is still held short, a long's positive only while it is not.
    # A short covered (or a long flipped short) after the ex-date has a due of
    # the wrong sign there, which would raise; leave it unexplained instead, so
    # the cash it moved halts as a `cash` mismatch as before (#1928).
    short_now = _ledger(state, actions, window, through, tolerance).short_holdings
    return (
        {
            name: amount
            for name, amount in sorted(due.items())
            if (amount < 0 and short_now.get(name, 0.0) > tolerance)
            or (amount > 0 and short_now.get(name, 0.0) <= tolerance)
        },
        {name: "; ".join(details) for name, details in sorted(findings.items())},
    )


def _spinoffs(
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    session: date,
    tolerance: float,
    through: date,
) -> dict[str, tuple[str, float]]:
    """Each `spinoff` action (#1894, #1896) effective by `session` whose parent
    the ledger held, keyed by child `security_id` to `(parent security_id,
    shares per parent share)`: the store convention `execution.window` reads
    between windows (child is the row's `security_id`, parent its
    `source_action_id`, ratio its `ratio_or_amount`).

    The rows come from `live_actions_as_of(close(S-1))`, so only those known at
    the run's cutoff are offered: a row known later is invisible and its child
    stays a mismatch, never a guess. There is no lower ex-date bound (#1896): a
    spin-off's row is stamped on the close after Alpaca's date-only
    `process_date`, which can land weeks after the ex-date, and a later `ok`
    reconciliation must not hide it once the child appears.

    A row is offered only when the ledger held its parent on the session before
    the ex-date (the session the distribution was earned in), so a child for a
    parent this window never held explains nothing, and only when that holding
    still equals the ledger's stated holding, so `reconcile.compare`'s
    `parent held x ratio` bound is computed from the same basis and a
    since-moved parent fails closed. The ratio is read as stored: a wrong one
    leaves the broker's child outside that bound and `compare` fails closed."""
    rows = actions.filter(
        (pl.col("action_type") == _SPINOFF)
        & (pl.col("ex_date") <= session)
        & (pl.col("source_action_id") != "")
    )
    stated = _ledger(state, actions, window, through, tolerance).positions
    spinoffs: dict[str, tuple[str, float]] = {}
    holdings: dict[date, Ledger] = {}
    for row in rows.iter_rows(named=True):
        parent = row["source_action_id"]
        before = previous_session(row["ex_date"])
        if before not in holdings:
            holdings[before] = _ledger(state, actions, window, before, tolerance)
        held = holdings[before].positions.get(parent, 0.0)
        if held > tolerance and abs(held - stated.get(parent, 0.0)) <= tolerance:
            spinoffs[row["security_id"]] = (parent, float(row["ratio_or_amount"]))
    return spinoffs


def _stock_dividends(
    actions: pl.DataFrame, session: date, max_rate: float, max_lag_sessions: int
) -> dict[str, tuple[date, date, float]]:
    """`security_id`s with a stock dividend stored as a `SPLIT` action (#1627):
    a ratio in `(1, max_rate]` — the same `alpaca.stock_dividend_max_rate` guard
    `#1618` (T186-family) stores one by — each mapped to `(first_date, last_date,
    ratio)`: the name's latest `ex_date <= session`, that ex-date advanced up to
    `max_lag_sessions` XNYS sessions (the `alpaca.cash_in_lieu_max_lag_sessions`
    window Alpaca pays cash in lieu on, the payable date), clamped to `session`,
    and its ratio. A `CIL` cash-in-lieu activity is reconciled into a
    `cash_in_lieu` adjustment only beside a name here and dated in
    `[first_date, last_date]` (and a fractional remainder the broker credited as
    whole shares plus cash); otherwise it is `unexplained_activity`."""
    rows = actions.filter(
        (pl.col("action_type") == _SPLIT)
        & (pl.col("ratio_or_amount") > 1)
        & (pl.col("ratio_or_amount") <= max_rate)
        & (pl.col("ex_date") <= session)
    )
    latest: dict[str, tuple[date, float]] = {}
    for row in rows.iter_rows(named=True):
        ex_date = row["ex_date"]
        current = latest.get(row["security_id"])
        if current is None or ex_date > current[0]:
            latest[row["security_id"]] = (ex_date, float(row["ratio_or_amount"]))
    result: dict[str, tuple[date, date, float]] = {}
    for name, (ex_date, ratio) in sorted(latest.items()):
        last = ex_date
        for _ in range(max_lag_sessions):
            if last >= session:
                break
            last = next_session(last)
        result[name] = (ex_date, last, ratio)
    return result


def _explanations(
    conn: duckdb.DuckDBPyConnection,
    state: _JournalState,
    actions: pl.DataFrame,
    window: PaperWindowRow,
    session: date,
    settings: Settings,
    broker_symbols: Collection[str],
    tolerance: float,
    through: date,
    *,
    merger_lag_sessions: int,
    cash_tolerance: float,
    whole_share_price_buffer: float,
) -> Explanations:
    cut = _cut(session)
    names = (
        {f.security_id for f in state.fills}
        | {o.security_id for o in state.orders}
        | {a.security_id for a in state.adjustments if a.security_id is not None}
    )
    order_symbols = {o.security_id: o.symbol for o in state.orders}
    current = current_listings(listing_ends_as_of(conn, cut, settings), session)
    stated = _ledger(state, actions, window, through, tolerance)
    mergers, merger_conflicts, acquirers = _mergers(
        conn,
        state,
        names,
        session,
        stated,
        tolerance,
        merger_lag_sessions,
        cash_tolerance,
        whole_share_price_buffer,
    )
    # The acquirers join the mapped names before the map is built (#1762 item
    # 1), so a symbol an acquirer shares with its acquiree is resolved by
    # `_symbols`' one-name-per-symbol rule, never a crash.
    mapped = names | acquirers
    symbols, symbol_changes = _symbols(
        mapped,
        order_symbols,
        current,
        broker_symbols,
        settings.alpaca.class_symbols,
        _symbol_change_candidates(conn, cut, mapped),
    )
    ended = frozenset(n for n in names if current.get(n, {}).get("status") == DELISTED)
    priced = sorted(names | set(symbols))
    seen_ids, seen_before = _seen_activities(state.adjustments, state.base(through), window)
    dividends, findings = _dividends(
        state, actions, window, session, tolerance, through, reviewed_groups()
    )
    stock_dividends = _stock_dividends(
        actions,
        session,
        settings.alpaca.stock_dividend_max_rate,
        settings.alpaca.cash_in_lieu_max_lag_sessions,
    )
    return Explanations(
        symbols=symbols,
        ended=ended,
        spinoffs=_spinoffs(state, actions, window, session, tolerance, through),
        dividends=dividends,
        dividend_findings=findings,
        reference_prices=_reference_prices(conn, cut, priced, previous_session(session)),
        stock_dividends=stock_dividends,
        stock_dividend_references=_stock_dividend_references(conn, cut, priced, stock_dividends),
        seen_activity_ids=seen_ids,
        activity_seen_before=seen_before,
        symbol_changes=symbol_changes,
        mergers=mergers,
        merger_conflicts=merger_conflicts,
        delisted_cash=_delisted_cash(
            state,
            mergers,
            session,
            merger_lag_sessions,
            stated,
            tolerance,
        ),
    )


def _delisted_cash(
    state: _JournalState,
    mergers: Mapping[str, OpenMerger],
    session: date,
    lag_sessions: int,
    ledger: Ledger,
    tolerance: float,
) -> dict[str, DelistedCash]:
    """The removed delisted holdings whose cash may still arrive (#411): each name
    the ended-listing band removed (its latest `corporate_action_cash` removal row
    with no merger id) that has no merger journaled and no open merger, is not
    held again, and whose removal session plus the frozen
    `risk.reconcile_merger_lag_sessions` sessions has not passed (`open`), or has
    passed with no cash journaled and no completed reconciliation after it yet
    recording `overdue delisting cash` for the name (`overdue`)."""
    removals: dict[str, AdjustmentRow] = {}
    merger_names: set[str] = set()
    for adjustment in state.adjustments:
        name = adjustment.security_id
        if name is None or adjustment.kind != _CORPORATE_ACTION_CASH:
            continue
        if _merger_id_of(adjustment) is not None:
            merger_names.add(name)
        elif adjustment.quantity is not None and adjustment.quantity < 0:
            current = removals.get(name)
            if current is None or (adjustment.session, adjustment.adjustment_id or 0) > (
                current.session,
                current.adjustment_id or 0,
            ):
                removals[name] = adjustment
    found: dict[str, DelistedCash] = {}
    for name, removal in sorted(removals.items()):
        if (
            name in merger_names
            or name in mergers
            or ledger.positions.get(name, 0.0) > tolerance
            or "listing ended" not in (removal.explanation_json or "")
        ):
            continue
        late = [
            a
            for a in state.adjustments
            if a.security_id == name
            and a.kind == _CORPORATE_ACTION_CASH
            and a.quantity is None
            and a.session >= removal.session
            and is_delisting_cash(a)
        ]
        journaled = math.fsum([removal.cash or 0.0, *(a.cash or 0.0 for a in late)])
        end = removal.session
        for _ in range(lag_sessions):
            end = next_session(end)
        is_open = session <= end
        noted = any(
            m.get("security_id") == name and m.get("reason") == OVERDUE_DELISTING_CASH
            for row in state.rows
            for m in allowed_of(row.mismatches_json)
        )
        found[name] = DelistedCash(
            removed_quantity=-(removal.quantity or 0.0),
            since=removal.session,
            journaled_cash=journaled,
            open=is_open,
            overdue=not is_open and journaled <= 0 and not noted,
        )
    return found


def is_delisting_cash(adjustment: AdjustmentRow) -> bool:
    """Whether an adjustment's `explanation_json` marks it a delisting's late cash."""
    try:
        payload = json.loads(adjustment.explanation_json or "{}")
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("delisting_cash") is True


def allowed_of(mismatches_json: str | None) -> list[dict[str, Any]]:
    """The `allowed` entries a reconciliation row's `mismatches_json` recorded (empty
    when none or unreadable)."""
    if mismatches_json is None:
        return []
    try:
        payload = json.loads(mismatches_json)
    except (ValueError, TypeError):
        return []
    found = payload.get("allowed") if isinstance(payload, dict) else None
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


def _stock_dividend_references(
    conn: duckdb.DuckDBPyConnection,
    cut: datetime,
    priced: Sequence[str],
    stock_dividends: Mapping[str, tuple[date, date, float]],
) -> dict[str, float]:
    """Each stock dividend's pre-ex reference (#1627): the raw close of the
    name's latest bar on or before `previous_session(ex_date)`, known at `cut`.
    Read for the ex-date, not the reconciliation session, so a `CIL` posted on
    the payable date still divides the same pre-split close by the ratio; a name
    the bars do not reach is left out, and its `CIL` halts there."""
    references: dict[str, float] = {}
    for name, (ex_date, _last_date, _ratio) in stock_dividends.items():
        if name not in set(priced):
            continue
        references.update(_reference_prices(conn, cut, [name], previous_session(ex_date)))
    return references


def _recorded_activity_ids(base: ReconciliationRow | None) -> set[str] | None:
    """The cash-activity ids a reconciliation row recorded for a base (the
    `activities` of its `mismatches_json`), or None when the row is None, holds
    no JSON, or carries no recorded set (a pre-T183 `ok` row, or any hand-built
    row without the key): the read-only base of a null set."""
    if base is None or base.mismatches_json is None:
        return None
    try:
        payload = json.loads(base.mismatches_json)
    except (ValueError, TypeError):
        return None
    entries = payload.get("activities") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    return {
        str(entry["activity_id"])
        for entry in entries
        if isinstance(entry, dict) and "activity_id" in entry
    }


def _seen_activities(
    adjustments: Sequence[AdjustmentRow], base: ReconciliationRow | None, window: PaperWindowRow
) -> tuple[frozenset[str], date | None]:
    """The caller-applied seen-sets for the booked-`FEE` rule (#1583, T183): the
    ids every activity a reconciliation reads is treated as already inside the
    base, and the fallback date cutoff (None when a recorded set exists).

    The window's already-booked activity ids (`adjustments.broker_activity_id`)
    are always seen, so a fee is booked once by id. The base's recorded set (the
    last `ok` reconciliation's `activities`) takes precedence; before the first
    `ok`, the window row's `activities_seen_json` (what `paper start` read); when
    both are null, the fallback cutoff is the window's New York start date, so a
    fee dated before the window opened is seen and a fee dated on it is bookable."""
    booked = frozenset(
        a.broker_activity_id for a in adjustments if a.broker_activity_id is not None
    )
    recorded = _recorded_activity_ids(base)
    if recorded is not None:
        return booked | recorded, None
    if window.activities_seen_json is not None:
        try:
            ids = json.loads(window.activities_seen_json)
        except (ValueError, TypeError):
            ids = []
        return booked | frozenset(str(i) for i in ids if isinstance(i, str)), None
    return booked, window.started_at.astimezone(_NEW_YORK).date()


def _merger_id_of(adjustment: AdjustmentRow) -> str | None:
    """The merger id an adjustment's `explanation_json` names, else None."""
    if adjustment.explanation_json is None:
        return None
    try:
        payload = json.loads(adjustment.explanation_json)
    except (ValueError, TypeError):
        return None
    value = payload.get("merger_id") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def _row_mismatches(row: ReconciliationRow) -> list[dict[str, Any]]:
    """The mismatches a reconciliation row recorded (empty when none or unreadable)."""
    if row.mismatches_json is None:
        return []
    try:
        payload = json.loads(row.mismatches_json)
    except (ValueError, TypeError):
        return []
    found = payload.get("mismatches") if isinstance(payload, dict) else None
    return [m for m in found if isinstance(m, dict)] if isinstance(found, list) else []


def _conflict_detail(name: str, effective_date: date, ids: Sequence[str]) -> str:
    """The `merger_conflict` detail: every id quoted, so a read-back matches whole ids."""
    quoted = ", ".join(repr(i) for i in ids)
    return f"merger ids [{quoted}] for {name} effective {effective_date.isoformat()}"


def _raised_conflict(row: ReconciliationRow, name: str, ids: Sequence[str]) -> bool:
    """Whether `row` already raised the conflicting pair `ids` on `name`."""
    return any(
        m.get("kind") == MERGER_CONFLICT
        and m.get("security_id") == name
        and all(repr(i) in str(m.get("detail", "")) for i in ids)
        for m in _row_mismatches(row)
    )


def _deferred(row: ReconciliationRow) -> bool:
    """Whether `row` had a lagging or pending order: `compare` runs no merger due
    check beside one, so such a row never counts as the check (T249)."""
    if row.mismatches_json is None:
        return False
    try:
        payload = json.loads(row.mismatches_json)
    except (ValueError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("lagging")) or bool(payload.get("pending"))


def _named_due(row: ReconciliationRow, name: str, merger_id: str) -> bool:
    """Whether `row` recorded the `cash` mismatch of `merger_id`'s unmet due."""
    return any(
        m.get("kind") == "cash"
        and m.get("security_id") == name
        and str(m.get("detail", "")).startswith(merger_due_detail(merger_id))
        for m in _row_mismatches(row)
    )


def _merger_first_known(
    conn: duckdb.DuckDBPyConnection, cut: datetime, names: Sequence[str]
) -> dict[tuple[str, str], datetime]:
    """When each merger identity `(acquiree, id)` was first known, at or before
    `cut`: its earliest stored revision's `known_at` (a later revision does not
    move when the merger became known)."""
    rows = conn.execute(
        """
        SELECT security_id, source_action_id, min(known_at) FROM mergers
        WHERE known_at <= ? AND list_contains(?, security_id)
        GROUP BY security_id, source_action_id
        """,
        [cut, list(names)],
    ).fetchall()
    return {(str(r[0]), str(r[1])): ensure_tz_aware_utc(r[2], field_name="known_at") for r in rows}


def _window_end(row: Mapping[str, Any], lag_sessions: int) -> date:
    """A merger's window end: `payable_date` (else `effective_date`) plus
    `lag_sessions` XNYS sessions (#1762 item 4)."""
    end: date = row["payable_date"] if row["payable_date"] is not None else row["effective_date"]
    for _ in range(lag_sessions):
        end = next_session(end)
    return end


def _mergers(
    conn: duckdb.DuckDBPyConnection,
    state: _JournalState,
    names: Collection[str],
    session: date,
    ledger: Ledger,
    tolerance: float,
    lag_sessions: int,
    cash_tolerance: float,
    whole_share_price_buffer: float,
) -> tuple[dict[str, OpenMerger], dict[str, str], set[str]]:
    """The open mergers by acquiree, the conflicting pairs raised at this
    reconciliation, and the acquirers to map (module docstring, `mergers`).

    Every row is read from `mergers_as_of(close(S-1))`, so a row known later
    explains nothing at S. `ledger` is the ledger stated through S."""
    cut = _cut(session)
    frame = mergers_as_of(conn, cut, sorted(names))
    rows = [r for r in frame.iter_rows(named=True) if r["security_id"] in names]
    acquirers = {
        str(r["acquirer_security_id"]) for r in rows if r["acquirer_security_id"] is not None
    }
    if not rows:
        return {}, {}, acquirers
    first_known = _merger_first_known(conn, cut, sorted({r["security_id"] for r in rows}))
    # A merger's own rows by (acquiree, id), the table's identity.
    own: dict[tuple[str, str], list[AdjustmentRow]] = defaultdict(list)
    band: dict[str, list[AdjustmentRow]] = defaultdict(list)
    for adjustment in state.adjustments:
        merger_id = _merger_id_of(adjustment)
        if merger_id is not None:
            own[(adjustment.security_id or "", merger_id)].append(adjustment)
        elif (
            adjustment.kind == _CORPORATE_ACTION_CASH
            and adjustment.security_id is not None
            and adjustment.quantity is not None
            and adjustment.quantity < 0
        ):
            band[adjustment.security_id].append(adjustment)
    completed = [(row, command_session(row.at)) for row in state.rows if row.status in _COMPLETED]
    bound = _cash_bound(cash_tolerance, ledger.fills_since_base)

    # Per (acquiree, id): the merger, whether it is open, and whether it is
    # active for a conflict (open, or with rows journaled).
    found: dict[tuple[str, str], tuple[OpenMerger, bool, bool]] = {}
    for row in rows:
        name, merger_id = str(row["security_id"]), str(row["source_action_id"])
        end = _window_end(row, lag_sessions)
        known = first_known.get((name, merger_id), row["known_at"])
        own_rows = sorted(own.get((name, merger_id), []), key=lambda a: a.known_at)
        removal = next((a for a in own_rows if a.quantity is not None), None)
        # A band removal links only from the session before the effective date
        # on, so an older removal of the name is never read as this merger's.
        linked = [
            a for a in band.get(name, []) if a.session >= previous_session(row["effective_date"])
        ]
        from_band = removal is None and bool(linked)
        if from_band:
            removal = max(linked, key=lambda a: a.known_at)
        journaled = math.fsum(a.cash for a in own_rows if a.cash is not None)
        if from_band and removal is not None and removal.cash is not None:
            journaled += removal.cash
        held = (
            ledger.positions.get(name, 0.0) > tolerance
            or ledger.short_holdings.get(name, 0.0) > tolerance
        )
        effective = row["effective_date"] <= session

        def after_end(stated: date, end: date = end, known: datetime = known) -> bool:
            # A reconciliation stated after the window, with the row known at its cut.
            return stated > end and known <= _cut(stated)

        open_held = effective and held
        open_removed = False
        removed_quantity: float | None = None
        if removal is not None and removal.quantity is not None and effective and not held:
            removed_quantity = -removal.quantity
            rate = row["cash_per_share"] if row["cash_per_share"] is not None else 0.0
            upper_value = removed_quantity * float(rate)
            if not from_band and row["kind"] != "cash":
                try:
                    terms = json.loads(removal.explanation_json or "{}")
                except (ValueError, TypeError):
                    terms = {}
                if isinstance(terms, dict):
                    fractional = terms.get("fractional_shares")
                    price = terms.get("acquirer_price")
                    if (
                        isinstance(fractional, int | float)
                        and not isinstance(fractional, bool)
                        and math.isfinite(fractional)
                        and fractional > tolerance
                        and isinstance(price, int | float)
                        and not isinstance(price, bool)
                        and math.isfinite(price)
                        and price > 0
                    ):
                        upper_value += fractional * price * (1 + whole_share_price_buffer)
            upper = Decimal(str(upper_value))
            paid = Decimal(str(journaled)) >= upper - bound
            closed = any(
                r.status == OK and after_end(stated) and removal.known_at <= r.known_at
                for r, stated in completed
            )
            ended_when_known = known > session_close(end)
            open_removed = not (paid or closed or ended_when_known)
        is_open = open_held or open_removed
        checked = any(
            after_end(stated)
            and (
                (removal is not None and removal.known_at <= r.known_at and not _deferred(r))
                or _named_due(r, name, merger_id)
            )
            for r, stated in completed
        )
        merger = OpenMerger(
            merger_id=merger_id,
            kind=str(row["kind"]),
            acquirer=row["acquirer_security_id"],
            share_ratio=row["share_ratio"],
            cash_per_share=row["cash_per_share"],
            effective_date=row["effective_date"],
            payable_date=row["payable_date"],
            window_end=end,
            removed_quantity=None if open_held else removed_quantity,
            journaled_cash=journaled,
            final=is_open and session > end and not checked,
            cash_upper=upper_value if removed_quantity is not None else None,
        )
        found[(name, merger_id)] = (merger, is_open, is_open or bool(own_rows))

    by_deal: dict[tuple[str, date], list[str]] = defaultdict(list)
    for (name, merger_id), (merger, _open, _active) in found.items():
        by_deal[(name, merger.effective_date)].append(merger_id)
    conflicts: dict[str, str] = {}
    excluded: set[tuple[str, str]] = set()
    for (name, effective_date), ids in sorted(by_deal.items()):
        if len(ids) < 2:
            continue
        ids = sorted(ids)
        excluded |= {(name, i) for i in ids}
        if any(_raised_conflict(r, name, ids) for r in state.rows):
            continue
        if any(found[(name, i)][2] for i in ids):
            detail = _conflict_detail(name, effective_date, ids)
            conflicts[name] = f"{conflicts[name]}; {detail}" if name in conflicts else detail

    open_by_name: dict[str, OpenMerger] = {}
    for (name, merger_id), (merger, is_open, _active) in sorted(found.items()):
        if (name, merger_id) in excluded or not is_open:
            continue
        current = open_by_name.get(name)
        if current is None or (merger.effective_date, merger.merger_id) > (
            current.effective_date,
            current.merger_id,
        ):
            open_by_name[name] = merger
    return open_by_name, conflicts, acquirers


def explanations_as_of(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    session: date,
    *,
    as_of: datetime,
    settings: Settings,
    broker_symbols: Collection[str] = (),
    quantity_tolerance: float,
    merger_lag_sessions: int = _DEFAULT_RISK.reconcile_merger_lag_sessions,
    cash_tolerance: float = _DEFAULT_RISK.reconcile_cash_tolerance,
    whole_share_price_buffer: float = _DEFAULT_RISK.whole_share_price_buffer,
) -> Explanations:
    """What the store knew at close(S-1), and the journal at `as_of`, to explain
    the broker on S (module docstring). Read-only. `as_of` is the journal cut
    (tz-aware). `broker_symbols` are `positions()`' keys, so a symbol
    the journal never held can still be matched, and a held name whose ticker
    changed at close(S-1) maps to the broker's new symbol (#1761). `settings`
    gives `master.transfer_window_sessions` for the listing ends, and
    `quantity_tolerance` is the frozen `risk.reconcile_quantity_tolerance`;
    `merger_lag_sessions` and `cash_tolerance` are the frozen
    `risk.reconcile_merger_lag_sessions` and `risk.reconcile_cash_tolerance`
    (their defaults when not given), which an open merger's window and its
    paid-in-full test read (T249).
    Raises `ValueError` for a non-session `session` or a naive `as_of`."""
    cut = _cut(session)
    as_of = ensure_tz_aware_utc(as_of, field_name="as_of")
    state = _journal_state(conn, _window_id(window), as_of)
    actions = live_actions_as_of(conn, cut)
    return _explanations(
        conn,
        state,
        actions,
        window,
        session,
        settings,
        broker_symbols,
        quantity_tolerance,
        session,
        merger_lag_sessions=merger_lag_sessions,
        cash_tolerance=cash_tolerance,
        whole_share_price_buffer=whole_share_price_buffer,
    )


def _open_orders(
    conn: duckdb.DuckDBPyConnection, window_id: int
) -> tuple[list[OrderRow], set[str], dict[str, tuple[float, float]]]:
    """The window's non-terminal orders, the `pending` ids among them, and each
    order's live journaled (quantity, notional)."""
    orders = non_terminal_orders(conn, window_id=window_id)
    pending = {o.client_order_id for o in pending_orders(conn, window_id=window_id)}
    sums: dict[str, tuple[float, float]] = {}
    ids = [o.client_order_id for o in orders]
    if ids:
        for fill in fills_for(conn, client_order_ids=ids):
            quantity, notional = sums.get(fill.fill.client_order_id, (0.0, 0.0))
            sums[fill.fill.client_order_id] = (
                quantity + fill.fill.quantity,
                notional + fill.fill.quantity * fill.fill.price,
            )
    return orders, pending, sums


def _explanation_json(
    text: str,
    reconciliation_id: int | None,
    merger_id: str | None = None,
    merger_terms: Mapping[str, object] | None = None,
) -> str:
    """An adjustment's `explanation_json`: its text, the reconciliation that
    journaled it and, for a merger's row, the merger id (#1762, T249)."""
    payload: dict[str, object] = {"explanation": text, "reconciliation_id": reconciliation_id}
    if merger_id is not None:
        payload["merger_id"] = merger_id
    if merger_terms is not None:
        payload.update(merger_terms)
    return json.dumps(payload, sort_keys=True, allow_nan=False)


def reconcile_now(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    window: PaperWindowRow,
    session: date,
    clock: Callable[[], datetime],
    journal: Connect,
    run_id: int | None = None,
    *,
    frozen: RiskConfig,
    as_of: datetime,
) -> Reconciliation:
    """Reconcile window `window` on session `session` (module docstring) and
    return the compare result once its rows are committed.

    `connect` opens a connection for the reads, `journal` a write chunk for the
    `reconciliations` and `adjustments` rows (both `lambda:
    store.db.open_for_write(settings)` in a run). `frozen` is the window's
    frozen `risk` section and `settings` gives the run-time
    `paper.order_id_prefix` and `master.transfer_window_sessions`. `run_id`
    names the run, None outside one. `as_of` is the journal cut the ledger and
    the explanations read at (module docstring), never after the clock reading.

    Raises `ReconciliationError` on a mismatch (the row is written first),
    `ClockError` for a bad clock reading or an `as_of` after it, `ValueError`
    for a non-session `session`, a window without an id, or a naive `as_of`,
    and whatever the broker raises (every allowlist this reaches is empty,
    req 4)."""
    window_id = _window_id(window)
    cut = _cut(session)
    now = _read_clock(clock)
    as_of = ensure_tz_aware_utc(as_of, field_name="as_of")
    if as_of > now:
        # A clock that went back between the caller's reading and this one (or a
        # caller cutting in the future): the same fault as a stamp going back.
        raise ClockError(f"as_of {as_of.isoformat()} is after the clock reading {now.isoformat()}")
    through = _stated_through(session, now)
    tolerance = frozen.reconcile_quantity_tolerance

    with connect() as conn:
        orders, pending, sums = _open_orders(conn, window_id)

    positions: dict[str, Position] = broker.positions()
    open_orders: list[Order] = broker.open_orders()
    account: Account = broker.account()
    # The booked-`FEE` rule's input (#1583, T183, spec item 1): every non-trade
    # cash activity from the day before the window's New York start date, read
    # after `account()` so a fee inside `starting_cash` the read can see is seen.
    activities = broker.cash_activities(
        window.started_at.astimezone(_NEW_YORK).date() - timedelta(days=1)
    )
    lagging = [
        JournalOpenOrder(
            order=order,
            pending=order.client_order_id in pending,
            journaled_quantity=sums.get(order.client_order_id, (0.0, 0.0))[0],
            journaled_notional=sums.get(order.client_order_id, (0.0, 0.0))[1],
            reading=(
                None
                if order.client_order_id in pending
                else broker.get_order(order.client_order_id)
            ),
        )
        for order in orders
    ]

    with connect() as conn:
        state = _journal_state(conn, window_id, as_of)
        actions = live_actions_as_of(conn, cut)
        explanations = _explanations(
            conn,
            state,
            actions,
            window,
            session,
            settings,
            positions.keys(),
            tolerance,
            through,
            merger_lag_sessions=frozen.reconcile_merger_lag_sessions,
            cash_tolerance=frozen.reconcile_cash_tolerance,
            whole_share_price_buffer=frozen.whole_share_price_buffer,
        )
    # The read-only `_explanations` names the seen-sets and fallback; the broker's
    # cash activities are attached here (they are a broker input, never a journal
    # fact, so the look-ahead cut does not touch them).
    explanations = replace(explanations, activities=tuple(activities))
    ledger = _ledger(state, actions, window, through, tolerance)
    result = compare(
        ledger,
        positions,
        open_orders,
        lagging,
        account,
        explanations,
        window,
        frozen,
        order_id_prefix=settings.paper.order_id_prefix,
    )

    with journal() as conn:
        stamp = _read_clock(clock)
        if stamp < now:
            raise ClockError(f"clock went back from {now.isoformat()} to {stamp.isoformat()}")
        reconciliation_id = append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=run_id,
                at=stamp,
                status=result.status,
                broker_cash=result.broker_cash,
                mismatches_json=result.mismatches_json,
                book_id=window.book_id,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
        activity_sessions = {a.activity_id: a.date for a in activities}
        for adjustment in result.adjustments:
            # A row keyed by a broker activity (a booked `fee_cash`, a `cash_in_lieu`,
            # a delisting's late cash) is stated on the activity's own date, not the
            # reconciliation session, so the ledger applies it from the session it
            # belongs to (#1583, T183; #411).
            fee_date = (
                activity_sessions.get(adjustment.broker_activity_id)
                if adjustment.broker_activity_id is not None
                else None
            )
            append(
                conn,
                AdjustmentRow(
                    window_id=window_id,
                    run_id=run_id,
                    session=fee_date if fee_date is not None else session,
                    kind=adjustment.kind,
                    security_id=adjustment.security_id,
                    quantity=adjustment.quantity,
                    cash=adjustment.cash,
                    explanation_json=_explanation_json(
                        adjustment.explanation,
                        reconciliation_id,
                        adjustment.merger_id,
                        adjustment.merger_terms,
                    ),
                    book_id=window.book_id,
                    broker_activity_id=adjustment.broker_activity_id,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )

    if result.status == MISMATCH:
        kinds = ", ".join(sorted({m.kind for m in result.mismatches}))
        raise ReconciliationError(
            f"reconciliation {reconciliation_id} of window {window_id} on "
            f"{session.isoformat()}: {kinds}"
        )
    return result


#: The `Order` fields a read-back must match the journal row on (the replay's).
_READ_BACK_FIELDS = ("symbol", "side", "notional", "quantity")


def unresolved_orders(
    connect: Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]], window_id: int
) -> list[OrderRow]:
    """The window's orders left `pending_unresolved` and not yet settled (they
    have no acknowledged event, so `pending_orders` lists them)."""
    with connect() as conn:
        unsettled = pending_orders(conn, window_id=window_id)
        ids = [o.client_order_id for o in unsettled]
        marked = {
            e.client_order_id
            for e in order_events_for(conn, window_id=window_id, client_order_ids=ids)
            if e.status == "pending_unresolved"
        }
    return [o for o in unsettled if o.client_order_id in marked]


def settle_pending_unresolved(
    broker: Broker,
    connect: Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]],
    clock: Callable[[], datetime],
    orders: Sequence[OrderRow],
) -> tuple[str, ...]:
    """Read back, by `client_order_id`, each of `orders` (`unresolved_orders`);
    return the ids settled.

    The broker knows it: an `accepted` event with the broker's order id (the
    collection that follows journals its fills and terminal event). It does not
    (`UnknownOrderError`): `cancelled` with reason `not_received`, so the
    decision is open again. A read that fails otherwise, or a broker order whose
    `symbol`, `side`, `notional` or `quantity` differ from the journal row, raises
    `ReconciliationError` and writes nothing, leaving the order unsettled (a
    `ClockError` keeps its type)."""
    readings: dict[str, Order | None] = {}
    for order in orders:
        coid = order.client_order_id
        try:
            got = broker.get_order(coid)
        except UnknownOrderError:
            readings[coid] = None
            continue
        except ClockError:
            raise
        except Exception as exc:
            raise ReconciliationError(
                f"pending_unresolved order {coid} could not be read back ({type(exc).__name__})"
            ) from exc
        violation = order_shape_violation(got)
        if violation is not None:
            raise ReconciliationError(
                f"pending_unresolved order {coid}: {violation.rule}: the read-back "
                f"returned {violation.detail}"
            )
        differing = [n for n in _READ_BACK_FIELDS if getattr(got, n) != getattr(order, n)]
        if differing:
            raise ReconciliationError(
                f"pending_unresolved order {coid} differs from the broker's order in "
                f"{', '.join(differing)}"
            )
        readings[coid] = got
    if not readings:
        return ()
    stamp = _read_clock(clock)
    with connect() as conn:
        for coid, reading in readings.items():
            if reading is None:
                event = OrderEventRow(
                    client_order_id=coid,
                    status="cancelled",
                    reason="not_received",
                    known_at=stamp,
                    ingested_at=stamp,
                )
            else:
                event = OrderEventRow(
                    client_order_id=coid,
                    event_at=reading.submitted_at,
                    status="accepted",
                    broker_order_id=reading.broker_order_id,
                    raw_json=json.dumps(asdict(reading), default=str, sort_keys=True),
                    known_at=stamp,
                    ingested_at=stamp,
                )
            append(conn, event)
    return tuple(readings)


def command_session(now: datetime) -> date:
    """The session `paper reconcile` states the ledger for: the clock's New
    York date when it is a session, else the latest session before it."""
    day = ensure_tz_aware_utc(now, field_name="clock").astimezone(_NEW_YORK).date()
    return day if is_session(day) else previous_session(day)


def reconcile_command(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    *,
    book_id: str | None = None,
) -> Reconciliation:
    """`paper reconcile --book <book>` (module docstring; `book_id` defaults to
    `paper.book_id`, `ValueError` outside the token grammar). Raises `LockHeld`
    while another process holds the book's run lock and `NoWindowError` with no
    open window for the book, both before any broker call or write. A mismatch
    engages only the book's window (ADR 0017 B.5). On a mismatch, appends the `engaged` row
    and re-raises `ReconciliationError`; if that row cannot be written (the
    store, or the clock it is stamped with, failing), the error says so and
    still names the mismatch."""
    book = resolve_book(settings, book_id)
    with run_lock(settings, book):
        with connect() as conn:
            window = open_window(conn, book)
        if window is None:
            raise NoWindowError("no_window: no paper window is open")
        frozen = frozen_risk(window)
        try:
            # Before `settle_pending_unresolved` writes anything broker-derived
            # (#2187): another account's broker cannot answer for this window's orders.
            account = broker.account()
            if account.account_id != window.account_id:
                raise ReconciliationError(
                    f"account_id: the broker's account {account.account_id!r} is not window "
                    f"{_window_id(window)}'s {window.account_id!r}"
                )
            settle_pending_unresolved(
                broker, connect, clock, unresolved_orders(connect, _window_id(window))
            )
            now = _read_clock(clock)
            session = command_session(now)
            return reconcile_now(
                settings, connect, broker, window, session, clock, connect, frozen=frozen, as_of=now
            )
        except ReconciliationError as exc:
            try:
                engaged = switch.engage(
                    settings,
                    clock,
                    window_id=_window_id(window),
                    source="fault",
                    fault_type=_FAULT_TYPE,
                    reason=str(exc),
                )
            except Exception as engage_error:  # a bad clock reading, say
                engaged = switch.WriteFailed(f"{type(engage_error).__name__}: {engage_error}")
            if isinstance(engaged, switch.WriteFailed):
                raise ReconciliationError(
                    f"{exc}; the kill switch row could not be written: {engaged.error}"
                ) from exc
            raise
