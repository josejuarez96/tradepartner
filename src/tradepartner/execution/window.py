"""Window start, stop, abandon, kill, the override writer and the owner
settlement writer (Phase 4 spec req 14 "Entry gate, start and stop", req 5,
req 9, req 17 and open question 13; ADR 0009 point 3; plans T64, T64b and
T84b).

`start(settings, connect, broker, clock, slug)` is `paper start --hypothesis
<slug>`. First it refuses a family paper trading cannot run
(`family_not_runnable`) and a hypothesis whose cadence, read through
`backtest.frozen.frozen_values`, is not `month_end` (`refused_cadence`,
strategy-lab spec req 11; a pre-lab registration, stored without `schedule.*`
keys, reads as `month_end`). Then, in order, it refuses, before any write:

1. **No `gap_signoff`.** `owner_decisions` must hold a row of kind
   `gap_signoff` for the hypothesis, referencing a trial that is `ok`,
   non-synthetic and `kind = in_sample` (ADR 0009 point 3). The decision
   command (`tradepartner decision gap-signoff`) does not itself check the
   referenced trial's kind or synthetic flag, so this is re-checked here,
   against the trial as it is *now* (a trial could in principle be amended
   only by a fresh row, never updated, but the check is cheap and the gate
   matters).
2. **`holdout.end` not a completed month-end.** The hypothesis's frozen
   `holdout_end` must be the last XNYS session of its calendar month, and
   its close must already be in the past at the clock's reading.
3. **A window is already open.**
4. **`account()` fails** on the paper endpoint (whatever the broker raises
   propagates as the refusal's cause).
5. **The account is not flat**: `open_orders()` is non-empty, or a
   position exists that is not explained by the previous window's listed
   residues (see "Flatness" below).
6. **The live costs differ from the hypothesis's registered costs**
   (`costs_drift`, #534): every `FROZEN_COSTS_KEYS` value must equal the
   registration's, which planning sizes with, so the frozen costs the wrapper
   reads are the plan's.
7. **The live fill price differs from the hypothesis's registered one**
   (`execution_drift`, #526): every `FROZEN_EXECUTION_KEYS` value must equal
   the registration's, which the tracking trial fills at, so `paper report`
   prices paper fills against the trial's own convention.

Once accepted, `start` appends the `paper_windows` row (`T_0`, the first
rebalance session strictly after both `holdout_end` and today, per the spec
Definitions' "Paper window"; `starting_cash` and `starting_equity` from
`account()`; `code_version`; `frozen_json`/`frozen_sha256`, the canonicalised
and hashed `risk.*` section plus `FROZEN_PAPER_KEYS`, `FROZEN_COSTS_KEYS` and
`FROZEN_EXECUTION_KEYS`, exactly as `registry.canonical_params_json`/
`params_sha256` do for hypothesis parameters), the `carried_residue`
adjustments copied from the previous window's listed residues (quantity and
origin unchanged, dated at the stop row's own session: the ledger itself
split-adjusts an adjustment from its `session` through any later one,
`execution.ledger`'s documented convention), and any `spinoff_receipt`
adjustments a spin-off explained (see below). It takes the run lock (T59) for
its writes, and migrates a store version 4 has left behind (`init_schema` on
the write connection) before writing.

**Flatness.** With no previous window, or the latest one `abandoned`, the
account must hold no position at all: an `abandoned` window carries no
residue forward, by spec req 14 ("after an `abandoned` window the account
must be strictly flat"), whatever its own `residues_json` still lists (that
list is the owner's record of what was *not* resolved, not a residue to
carry). With the latest window `closed`, every one of its `residues_json`
entries must be matched, in symbol and in split-adjusted quantity within the
frozen `risk.reconcile_quantity_tolerance`, against a broker position (its
removal is accepted when its security is delisted as of now instead); and
every broker position must be explained by one of those residues or by a
spin-off of one. The quantity each residue implies today is
`residue.quantity` times the product of the ratios of `split` corporate
actions on its security with an ex-date after the stop row's own session
(its `at`, in New York) and on or before now; the match compares that
derived quantity, never the stored one, to the live position.

**`residues_json` (settled here; T64b produces it).** A JSON object keyed by
`security_id`, each value `{"quantity": <float>, "origin": "dust" |
"untradable"}` (spec req 14, Data section: "per name, quantity and
origin"); `_parse_residues` refuses any other origin, including `null`
(#522 item 4).

**Spin-off children (open question, flagged in the PR).** The store has no
column linking a spin-off's child security to its parent (ADR 0009: no
adapter persists a `spinoff` action at all), so this module reads a
`corporate_actions` row of `action_type = "spinoff"` as: `security_id` is
the *child*, `ratio_or_amount` the child shares received per parent share,
and `source_action_id` the *parent's* `security_id` -- a convention this
task introduces for lack of a dedicated column, used only by test fixtures
until a real source populates it. Only a spin-off whose `ex_date` falls
strictly after the residue's own stated session and on or before now
explains anything (the same bound `reconcile.py` applies to its own
explanations); its ratio applies to the parent residue's own holding
*at the ex-date* (split-adjusted from the residue's session), and the
result is projected to today by the *child's* own splits since the
ex-date before it is compared to the live position, so neither the
parent's nor the child's splits are ever applied twice. A matching broker
position is accepted and journaled `spinoff_receipt` at the ex-date's own
share basis (the ledger split-adjusts it forward like any other
adjustment); the owner should confirm this convention (or replace it with
a schema change) before it is relied on for a real spin-off.

**The other commands (T64b).** Each raises `WindowCommandRefused(reason,
message)` for a refusal, whose `reason` is one of the codes below and whose
message names everything that is missing. A blank `--reason` is refused
(`reason`) before the lock, the clock or the broker is touched; every stored
reason is the trimmed text. Every command reads the open window through
`_window_of`, so a journal that predates schema version 17 (the eight expanded
tables lack `book_id`, `store.journal.require_journal`) is refused
`schema_version`, carrying that error's "open it for writing once" message, not
a false `no_window` (#1261, T132).

- **`stop(settings, connect, broker, clock, reason)`** is `paper stop`. It
  takes the run lock (`LockHeld` at once while a run holds it) and refuses
  `no_window`. It refuses `kill_switch` while the window's derived switch is
  engaged (`switch.derive` as a lock holder) or an `engage_kill_switch`
  override of the window is unconsumed (`derive` alone ignores those). With
  no `requested` row it appends `state = requested` and stops there. A later
  call refuses, in this order and writing nothing for the first two:
  `kill_switch` as above; `not_ready`, naming every order of the window that
  is not terminal and every terminal one without an outcome row of each kind
  it earns (`execution.outcomes`: a buy with fills owes `position_return`, a
  sell with fills `realised_pnl`, and a terminal status other than `filled`
  also `not_executed`); `reconciliation`, when its own `reconcile_now` for
  the clock's session (`reconcile_run.command_session`) with the frozen risk
  section raises a mismatch (the row is written and the switch engaged with
  source `fault`, as `paper reconcile` does) or ends in any status but `ok`;
  `not_flat`, naming each short holding (the system is long-only) and each
  name the ledger holds above its residue
  (`plan.residue`) by more than the frozen
  `risk.reconcile_quantity_tolerance`. Otherwise it appends `state = closed`
  with that reconciliation's id and `residues_json` in the shape above, each
  name's quantity being the ledger's holding. Its origin is `untradable` when
  any counted part is untradable (an untradable skip, or a carried row of
  that origin), else `dust`, as spec req 14 has it ("untradable wins when
  parts mix"). The parts are told apart by calling `plan.residue` on subsets
  of its own inputs, so the residue rule lives in one function. The switch is
  checked again inside the chunk that appends either row, so a `paper kill`
  or an override written meanwhile is never missed.
- **`abandon(settings, connect, broker, clock, reason)`** is the owner-only
  `paper abandon` (#247 Q13). It takes the run lock, refuses `no_window`,
  and does not look at the switch (a window that cannot be released can only
  end this way). It refuses `open_orders`, naming each one, while any order
  of the window has no terminal event in the journal (the read `stop`'s
  `not_ready` makes, so an order the broker filled but the journal has not
  collected still refuses); it never cancels one (#542: the owner cancels or
  waits, runs `paper resume` so the journal collects the order, then
  abandons). Every refusal comes before any broker call or write,
  and the run lock it holds keeps any run from placing an order meanwhile.
  It runs a final `reconcile_now`, keeping its row whatever
  its status (a mismatch is the expected case and engages nothing: the
  window ends here; but when anything after a mismatch fails, so the window
  stays open, it engages the switch with source `fault` before re-raising),
  then appends `state = abandoned` with the owner's note
  as `reason` (#247 Q13's "ADR-style note", also written on #247 or its
  successor), that reconciliation's id and `residues_json` = `{"positions":
  {symbol: quantity per broker.positions()}, "mismatches": [...], "lagging":
  [...], "pending": [...]}` from that reconciliation's `mismatches_json`.
  `start` never parses it (after an abandoned window the account must be
  strictly flat). It releases nothing.
- **`kill(settings, connect, clock, reason)`** is `paper kill`. It takes no
  run lock, so the owner can engage while a run holds it, refuses
  `no_window` and `multiple_open_windows` (spec req 14: only one window may
  ever be open; nothing is written for either refusal) and appends an
  `engaged` row with source `owner` (`switch.engage`), returning its
  `event_id`. A row that cannot be written, for any reason (the store, or
  the clock it is stamped with), raises `KillWriteFailed`.
- **`override(settings, clock, kind, rebalance_session, security_id,
  reason)`** is the one writer the override page (T69b) and the CLI (T67)
  share. It first refuses `override` for kind `settle_order` (#571, spec req
  17: that kind is `paper settle`'s alone, whose gate needs a fresh broker
  read the page and `paper override` do not make), before the clock, the
  store or any other check. It then reads the clock, opens one short-lived
  `open_for_write`
  (`StoreLockedError` propagates: "store busy"), refuses `no_window`, refuses
  `override` for a kind outside the schema's set or fields the kind does not
  take (`engage_kill_switch` takes neither a session nor a name;
  `exclude_name` and `keep_name` take both, the session a rebalance session
  no earlier than the window's T_0), refuses `reason` when the trimmed reason
  is shorter than the window's frozen `paper.min_override_reason_chars`, and
  returns the new `override_id`.
- **`settle_order(settings, connect, broker, clock, client_order_id,
  reason)`** is the owner-only `paper settle --order --reason` (T84b, spec
  req 17, #571), its one writer: under the run lock, the journal refusals in
  req 17's order before any broker call, one read-only broker read (`account`,
  `get_order`, `open_orders`, `fills`, `positions`, and a `get_order` per other
  open order on the name), the three-part gate with the reset exception, then
  the `overrides` row (`settle_order`) and the `cancelled` /
  `owner_settled_unknown` event in one transaction, stamped after the reads.
  It never calls `submit` or `cancel`. Its own docstring has the details.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from dateutil.relativedelta import relativedelta

from tradepartner.adapters.broker import TERMINAL_STATUSES, Broker, Order, UnknownOrderError
from tradepartner.backtest.frozen import frozen_values
from tradepartner.calendar import last_session_of_month, previous_session, session_close
from tradepartner.config import (
    FROZEN_COSTS_KEYS,
    FROZEN_EXECUTION_KEYS,
    FROZEN_PAPER_KEYS,
    PAPER_FAMILIES,
    RiskConfig,
    Settings,
)
from tradepartner.errors import ClockError, ReconciliationError
from tradepartner.execution import plan as plan_rules
from tradepartner.execution import switch
from tradepartner.execution.ledger import from_journal
from tradepartner.execution.lock import run_lock
from tradepartner.execution.outcomes import NOT_EXECUTED, POSITION_RETURN, REALISED_PNL
from tradepartner.execution.plan import current_listings
from tradepartner.execution.reconcile import OK
from tradepartner.execution.reconcile_run import (
    command_session,
    explanations_as_of,
    frozen_risk,
    reconcile_now,
)
from tradepartner.store import registry
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import open_for_write
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.journal import (
    CLOSING_STOP_STATES,
    TERMINAL_ORDER_STATUSES,
    AdjustmentRow,
    JournalIntegrityError,
    JournalNotInitialised,
    OrderEventRow,
    OrderRow,
    OverrideRow,
    PaperWindowRow,
    PaperWindowStopRow,
    adjustments_for,
    all_fill_ids,
    append,
    decisions_for,
    fills_for,
    kill_switch_events_for,
    latest_window,
    non_terminal_orders,
    open_window,
    order_events_for,
    orders_for,
    outcomes_for,
    pending_orders,
    positions_daily_for,
    reconciliations_for,
    runs_for,
    unconsumed_kill_switch_overrides,
    window_stops_for,
)
from tradepartner.store.schema import (
    ENGAGE_KILL_SWITCH_KIND,
    JOURNAL_ENUMS,
    SETTLE_ORDER_KIND,
    SchemaVersionError,
    init_schema,
)
from tradepartner.timeutil import ensure_tz_aware_utc

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

_NEW_YORK = ZoneInfo("America/New_York")
_RISK_PREFIX = "risk."
_PAPER_PREFIX = "paper."
_COSTS_PREFIX = "costs."
_EXECUTION_PREFIX = "execution."
#: The one cadence a paper window accepts (strategy-lab spec req 11; ADR 0005).
_CADENCE_KEY = "schedule.rebalance_cadence"
_PAPER_CADENCE = "month_end"
_ABANDONED = "abandoned"
_DUST = "dust"
_UNTRADABLE = "untradable"
_CARRIED_RESIDUE = "carried_residue"
_SPINOFF_RECEIPT = "spinoff_receipt"
_SPLIT = "split"
_SPINOFF = "spinoff"
_GAP_SIGNOFF = "gap_signoff"


class StartRefusedError(RuntimeError):
    """`paper start` refused before any write. `reason` is a short
    machine-readable code (the CLI, T67, maps it to its own message and
    exit code); `message` (this exception's `str()`) is for a human."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class StartResult:
    """What `start` returns on success."""

    window: PaperWindowRow
    abandoned_note: str | None


@dataclass(frozen=True)
class _Residue:
    security_id: str
    quantity: float
    origin: str  # "dust" or "untradable"; `_parse_residues` refuses anything else


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    reading = clock()
    if not isinstance(reading, datetime):
        raise TypeError(f"clock returned {type(reading).__name__}, not datetime")
    return ensure_tz_aware_utc(reading, field_name="clock")


def _ny_date(instant: datetime) -> date:
    return instant.astimezone(_NEW_YORK).date()


def _first_rebalance_session(holdout_end: date, today: date) -> date:
    """T_0 (spec Definitions "Paper window"): the first rebalance session
    (last XNYS session of a calendar month) strictly after both
    `holdout_end` and `today`."""
    lower = max(holdout_end, today)
    month = lower.replace(day=1)
    while True:
        candidate = last_session_of_month(month.year, month.month)
        if candidate > lower:
            return candidate
        month += relativedelta(months=1)


def _holdout_end_completed(holdout_end: date, now: datetime) -> bool:
    if holdout_end != last_session_of_month(holdout_end.year, holdout_end.month):
        return False
    return session_close(holdout_end) <= now


def _cost_drifted(registered_value: Any, live_value: float) -> bool:
    """Whether a registered cost value differs from the live one, true also
    for a registered value `float()` cannot parse (a string or null): that is
    drift too, refused the same way as a numeric mismatch (`costs_drift`,
    #580 item 1), never a plain `ValueError`/`TypeError` escaping to the
    caller."""
    try:
        return float(registered_value) != float(live_value)
    except (TypeError, ValueError):
        return True


def _frozen_params(settings: Settings, registered: Mapping[str, Any]) -> dict[str, Any]:
    """The flat dict `frozen_json` canonicalises: every `risk.*` key plus
    `FROZEN_PAPER_KEYS` under `paper.*`, `FROZEN_COSTS_KEYS` under `costs.*` and
    `FROZEN_EXECUTION_KEYS` under `execution.*` (spec req 14; the costs #534; the
    fill price #366 Q20, #526, which `paper report` reads back).

    The frozen costs must equal the hypothesis's `registered` parameters,
    which planning sizes the decisions with, so the plan and the wrapper share
    one cost model: a live `costs.*` value that differs from it, or a key the
    registration lacks, refuses the start (`costs_drift`). Likewise the frozen
    `execution.*` keys must equal the registered ones, which the tracking trial
    fills at, so `paper report` compares paper fills against the trial's own
    convention (`execution_drift`)."""
    risk = settings.risk.model_dump()
    paper = settings.paper.model_dump()
    costs = settings.costs.model_dump()
    execution = settings.execution.model_dump()
    drift = [
        f"{_COSTS_PREFIX}{k} live {costs[k]!r} vs registered "
        f"{registered.get(f'{_COSTS_PREFIX}{k}')!r}"
        for k in FROZEN_COSTS_KEYS
        if f"{_COSTS_PREFIX}{k}" not in registered
        or _cost_drifted(registered[f"{_COSTS_PREFIX}{k}"], costs[k])
    ]
    if drift:
        raise StartRefusedError(
            "costs_drift",
            "the live costs differ from the hypothesis's registered costs: " + "; ".join(drift),
        )
    execution_drift = [
        f"{_EXECUTION_PREFIX}{k} live {execution[k]!r} vs registered "
        f"{registered.get(f'{_EXECUTION_PREFIX}{k}')!r}"
        for k in FROZEN_EXECUTION_KEYS
        if registered.get(f"{_EXECUTION_PREFIX}{k}") != execution[k]
    ]
    if execution_drift:
        raise StartRefusedError(
            "execution_drift",
            "the live execution keys differ from the hypothesis's registered ones: "
            + "; ".join(execution_drift),
        )
    params: dict[str, Any] = {f"{_RISK_PREFIX}{k}": v for k, v in risk.items()}
    params.update({f"{_PAPER_PREFIX}{k}": paper[k] for k in FROZEN_PAPER_KEYS})
    params.update({f"{_COSTS_PREFIX}{k}": costs[k] for k in FROZEN_COSTS_KEYS})
    params.update({f"{_EXECUTION_PREFIX}{k}": execution[k] for k in FROZEN_EXECUTION_KEYS})
    return params


def _gap_signoff_ok(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> bool:
    """A `gap_signoff` row for `hypothesis_id` referencing an `ok`,
    non-synthetic, `in_sample` trial of that hypothesis (ADR 0009 point 3)."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM owner_decisions od
        JOIN trials t ON t.trial_id = od.trial_id
        JOIN trial_results r ON r.trial_id = t.trial_id
        WHERE od.kind = ?
          AND od.hypothesis_id = ?
          AND t.hypothesis_id = ?
          AND r.status = 'ok'
          AND NOT t.synthetic
          AND t.kind = 'in_sample'
        """,
        [_GAP_SIGNOFF, hypothesis_id, hypothesis_id],
    ).fetchone()
    return row is not None and bool(row[0])


def _previous_stop(conn: duckdb.DuckDBPyConnection, previous: PaperWindowRow | None) -> Any:
    """The previous window's closing stop row: any `abandoned` row wins over
    a `closed` one whatever their order (a window closes or is abandoned
    once; this does not rely on row order to tell which)."""
    if previous is None or previous.window_id is None:
        return None
    stops = [
        s for s in window_stops_for(conn, previous.window_id) if s.state in CLOSING_STOP_STATES
    ]
    if not stops:
        return None
    abandoned = [s for s in stops if s.state == _ABANDONED]
    return abandoned[-1] if abandoned else stops[-1]


def _parse_residues(residues_json: str | None) -> dict[str, _Residue]:
    if not residues_json:
        return {}
    try:
        parsed = json.loads(residues_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"residues_json is not JSON: {residues_json!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("residues_json must be an object keyed by security_id")
    residues: dict[str, _Residue] = {}
    for security_id, entry in parsed.items():
        if not isinstance(entry, dict) or "quantity" not in entry:
            raise ValueError(f"residues_json entry for {security_id!r} is malformed")
        origin = entry.get("origin")
        if origin not in (_DUST, _UNTRADABLE):
            raise ValueError(
                f"residues_json origin for {security_id!r} is {origin!r}, "
                f"must be {_DUST!r} or {_UNTRADABLE!r}"
            )
        quantity = float(entry["quantity"])
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError(f"residues_json quantity for {security_id!r} is {quantity!r}")
        residues[security_id] = _Residue(
            security_id=security_id,
            quantity=quantity,
            origin=origin,
        )
    return residues


def _split_factor(actions: pl.DataFrame, security_id: str, after: date, through: date) -> float:
    """The product of the ratios of `split` actions on `security_id` with
    `after < ex_date <= through`."""
    factor = 1.0
    rows = actions.filter(
        (pl.col("security_id") == security_id) & (pl.col("action_type") == _SPLIT)
    )
    for row in rows.iter_rows(named=True):
        ex_date = row["ex_date"]
        if after < ex_date <= through:
            factor *= float(row["ratio_or_amount"])
    return factor


def _spinoffs_of(
    actions: pl.DataFrame, parent_security_id: str, after: date, through: date
) -> list[dict[str, Any]]:
    """`spinoff` actions this module's convention attributes to
    `parent_security_id` (module docstring), with `after < ex_date <=
    through` (the same "explained only since the last known state" bound
    `reconcile.py` applies to its own explanations): a spin-off known but not
    yet effective, or one from before the residue was last stated, explains
    nothing here."""
    rows = actions.filter(
        (pl.col("action_type") == _SPINOFF)
        & (pl.col("source_action_id") == parent_security_id)
        & (pl.col("ex_date") > after)
        & (pl.col("ex_date") <= through)
    )
    return rows.to_dicts()


@dataclass(frozen=True)
class _FlatnessPlan:
    """What `start` journals once the account is accepted as flat."""

    carried: list[_Residue]
    carried_session: date | None
    spinoff_receipts: list[tuple[str, float, date]]  # (security_id, quantity, session)


def _check_flat(
    *,
    open_orders: list[Any],
    positions: dict[str, Any],
    previous_stop: Any,
    listings: pl.DataFrame,
    actions: pl.DataFrame,
    tolerance: float,
    now: date,
) -> _FlatnessPlan:
    if open_orders:
        raise StartRefusedError("open_orders", "the paper account has an open order")

    strictly_flat = previous_stop is None or previous_stop.state == _ABANDONED
    residues = {} if strictly_flat else _parse_residues(previous_stop.residues_json)
    stated_on = None if previous_stop is None else _ny_date(previous_stop.at)
    current = current_listings(listings, now)

    unmatched_symbols = {s: p.quantity for s, p in positions.items() if p.quantity != 0}
    carried: list[_Residue] = []
    for security_id, residue in residues.items():
        ticker = current.get(security_id, {}).get("ticker")
        expected = residue.quantity * (
            _split_factor(actions, security_id, stated_on, now) if stated_on else 1.0
        )
        quantity = unmatched_symbols.get(ticker) if ticker is not None else None
        if ticker is not None and quantity is not None and abs(quantity - expected) <= tolerance:
            del unmatched_symbols[ticker]
            carried.append(residue)
            continue
        delisted = current.get(security_id, {}).get("status") == DELISTED
        if quantity is None and delisted:
            continue
        raise StartRefusedError(
            "not_flat",
            f"residue of {security_id} ({expected} shares expected) is not matched "
            "by a broker position within the frozen quantity tolerance",
        )

    spinoff_receipts: list[tuple[str, float, date]] = []
    if carried and stated_on is not None:
        for residue in carried:
            for spinoff in _spinoffs_of(actions, residue.security_id, stated_on, now):
                child = spinoff["security_id"]
                ex_date = spinoff["ex_date"]
                ratio = float(spinoff["ratio_or_amount"])
                child_ticker = current.get(child, {}).get("ticker")
                if child_ticker is None or child_ticker not in unmatched_symbols:
                    continue
                # The parent's own holding at the ex-date (its splits between
                # the residue's stated session and the ex-date), times the
                # ratio: the child quantity received, in the child's
                # ex-date share basis.
                parent_at_ex_date = residue.quantity * _split_factor(
                    actions, residue.security_id, stated_on, ex_date
                )
                expected_at_ex_date = parent_at_ex_date * ratio
                # Projected to today by the child's own splits since the
                # ex-date, to compare against the live position (never
                # double counted: the stored adjustment below keeps the
                # ex-date basis and lets the ledger apply this same factor,
                # `execution.ledger`'s documented convention).
                expected_today = expected_at_ex_date * _split_factor(actions, child, ex_date, now)
                received = unmatched_symbols[child_ticker]
                if abs(received - expected_today) <= tolerance:
                    del unmatched_symbols[child_ticker]
                    spinoff_receipts.append((child, expected_at_ex_date, ex_date))

    if unmatched_symbols:
        raise StartRefusedError(
            "not_flat",
            f"the paper account holds unexplained positions: {sorted(unmatched_symbols)}",
        )

    return _FlatnessPlan(
        carried=carried, carried_session=stated_on, spinoff_receipts=spinoff_receipts
    )


def start(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    slug: str,
) -> StartResult:
    """`paper start --hypothesis <slug>` (module docstring). Raises
    `LockHeld` while another process holds the run lock,
    `registry.UnknownHypothesis` for an unregistered slug, and
    `StartRefusedError` for every other refusal, all before any write."""
    with run_lock(settings):
        with connect() as conn:
            hyp = registry.get_hypothesis(conn, slug)
            if hyp.family not in PAPER_FAMILIES:
                raise StartRefusedError(
                    "family_not_runnable",
                    f"{slug!r} is in family {hyp.family!r}, which paper trading cannot run "
                    f"yet (paper families: {', '.join(PAPER_FAMILIES)})",
                )
            registered = frozen_values(hyp)
            cadence = registered.get(_CADENCE_KEY)
            if cadence != _PAPER_CADENCE:
                raise StartRefusedError(
                    "refused_cadence",
                    f"{slug!r} rebalances at cadence {cadence!r}; a paper window "
                    f"accepts only {_PAPER_CADENCE!r} (strategy-lab spec req 11)",
                )
            if not _gap_signoff_ok(conn, hyp.hypothesis_id):
                raise StartRefusedError(
                    "gap_signoff",
                    f"no gap_signoff for {slug!r} references an ok, non-synthetic, "
                    "in_sample trial of this hypothesis",
                )
            now = _read_clock(clock)
            if not _holdout_end_completed(hyp.holdout_end, now):
                raise StartRefusedError(
                    "holdout_not_complete",
                    f"{slug!r}'s frozen holdout.end ({hyp.holdout_end}) is not a "
                    "completed month-end",
                )
            try:
                if open_window(conn) is not None:
                    raise StartRefusedError("window_open", "a paper window is already open")
                previous = latest_window(conn)
                previous_stop = _previous_stop(conn, previous)
            except JournalNotInitialised:
                previous = None
                previous_stop = None
            except SchemaVersionError as exc:
                raise StartRefusedError(SCHEMA_VERSION, str(exc)) from exc
            today = _ny_date(now)
            listings = listing_ends_as_of(conn, now, settings)
            actions = live_actions_as_of(conn, now)

        try:
            account = broker.account()
        except Exception as exc:
            raise StartRefusedError(
                "account_unavailable",
                f"account() failed on the paper endpoint ({type(exc).__name__})",
            ) from exc
        open_orders = broker.open_orders()
        positions = broker.positions()
        tolerance = settings.risk.reconcile_quantity_tolerance

        plan = _check_flat(
            open_orders=open_orders,
            positions=positions,
            previous_stop=previous_stop,
            listings=listings,
            actions=actions,
            tolerance=tolerance,
            now=today,
        )

        t_0 = _first_rebalance_session(hyp.holdout_end, today)
        commit, _dirty = registry.code_version()
        params = _frozen_params(settings, registered)
        frozen_json = registry.canonical_params_json(params)
        frozen_sha256 = registry.params_sha256(params)

        row = PaperWindowRow(
            hypothesis_id=hyp.hypothesis_id,
            first_rebalance_session=t_0,
            account_id=account.account_id,
            starting_cash=account.cash,
            starting_equity=account.equity,
            code_version=commit,
            started_at=now,
            frozen_json=frozen_json,
            frozen_sha256=frozen_sha256,
            book_id=settings.paper.book_id,
            known_at=now,
            ingested_at=now,
        )

        with open_for_write(settings) as write_conn:
            init_schema(write_conn)
            if open_window(write_conn) is not None:
                raise StartRefusedError("window_open", "a paper window is already open")
            window_id = append(write_conn, row)
            assert window_id is not None
            session = plan.carried_session or t_0
            for residue in plan.carried:
                append(
                    write_conn,
                    AdjustmentRow(
                        window_id=window_id,
                        run_id=None,
                        session=session,
                        kind=_CARRIED_RESIDUE,
                        origin=residue.origin,
                        security_id=residue.security_id,
                        quantity=residue.quantity,
                        cash=None,
                        book_id=row.book_id,
                        known_at=now,
                        ingested_at=now,
                    ),
                )
            for security_id, quantity, ex_date in plan.spinoff_receipts:
                append(
                    write_conn,
                    AdjustmentRow(
                        window_id=window_id,
                        run_id=None,
                        session=ex_date,
                        kind=_SPINOFF_RECEIPT,
                        origin=None,
                        security_id=security_id,
                        quantity=quantity,
                        cash=None,
                        book_id=row.book_id,
                        known_at=now,
                        ingested_at=now,
                    ),
                )

        result_window = replace(row, window_id=window_id)
        abandoned_note = (
            previous_stop.reason
            if previous_stop is not None and previous_stop.state == _ABANDONED
            else None
        )
        return StartResult(window=result_window, abandoned_note=abandoned_note)


# --- stop, abandon, kill and the override writer (T64b) -------------------------

#: `WindowCommandRefused.reason` codes (module docstring).
NO_WINDOW = "no_window"
#: A journal that predates schema version 17: the eight expanded tables lack
#: `book_id`, so `require_journal` raises. Distinct from `no_window` so the
#: command says "migrate first" rather than a false "no window" (#1261, T132).
SCHEMA_VERSION = "schema_version"
REASON = "reason"
KILL_SWITCH = "kill_switch"
NOT_READY = "not_ready"
RECONCILIATION = "reconciliation"
NOT_FLAT = "not_flat"
OVERRIDE = "override"
MULTIPLE_OPEN_WINDOWS = "multiple_open_windows"
OPEN_ORDERS = "open_orders"

REQUESTED = "requested"
CLOSED = "closed"
ABANDONED = _ABANDONED
_ENGAGE_KILL_SWITCH = ENGAGE_KILL_SWITCH_KIND
_OWNER = "owner"
_FAULT = "fault"
_FILLED = "filled"
_BUY = "buy"
_SELL = "sell"
_MIN_OVERRIDE_REASON_KEY = f"{_PAPER_PREFIX}min_override_reason_chars"


class WindowCommandRefused(RuntimeError):
    """`paper stop`, `abandon`, `kill` or an override refused. `reason` is a
    short machine-readable code (module docstring; the CLI, T67, and the page,
    T69b, map it to their own message and exit code); `message` (this
    exception's `str()`) names what is missing, for a human."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class KillWriteFailed(RuntimeError):
    """`paper kill` could not write its `engaged` row, so the switch is NOT
    engaged by it."""


@dataclass(frozen=True)
class StopResult:
    """What `stop` appended: `requested`, or `closed` with the reconciliation
    that passed and the `residues_json` it listed."""

    state: str
    reconciliation_id: int | None
    residues_json: str | None


@dataclass(frozen=True)
class AbandonResult:
    """What `abandon` appended: the final reconciliation's id and status, and
    the `residues_json` it listed."""

    reconciliation_id: int
    reconciliation_status: str
    residues_json: str


def _command_clock(clock: Callable[[], datetime]) -> datetime:
    """One clock reading, tz-aware UTC, or `ClockError` (ADR 0007 point 4)."""
    try:
        return _read_clock(clock)
    except Exception as exc:
        raise ClockError(f"clock failed: {type(exc).__name__}") from exc


def _note(reason: str, command: str) -> str:
    """The trimmed reason, refused when blank."""
    note = reason.strip()
    if not note:
        raise WindowCommandRefused(REASON, f"{command} needs a non-blank --reason")
    return note


def _window_of(conn: duckdb.DuckDBPyConnection) -> tuple[PaperWindowRow, int]:
    """The open window and its id, or a `no_window` refusal (a store whose
    journal no write has migrated has no window either), or a
    `multiple_open_windows` refusal (spec req 14: only one window may ever be
    open) when the journal has more than one open window, or a
    `schema_version` refusal when the journal predates schema version 17 (the
    eight expanded tables lack `book_id`; `require_journal`'s message names
    the fix). Every command that calls this (`stop`, `abandon`, `kill`,
    `override`) refuses the same way. `stop`, `kill` and `override` call it
    before writing anything; `abandon`'s re-check (after its reconciliation
    row) still refuses the same way, but by then the reconciliation row is
    already written."""
    try:
        window = open_window(conn)
    except JournalNotInitialised:
        window = None
    except SchemaVersionError as exc:
        raise WindowCommandRefused(SCHEMA_VERSION, str(exc)) from exc
    except JournalIntegrityError as exc:
        raise WindowCommandRefused(MULTIPLE_OPEN_WINDOWS, str(exc)) from exc
    if window is None or window.window_id is None:
        raise WindowCommandRefused(NO_WINDOW, "no_window: no paper window is open")
    return window, window.window_id


def _engaged_causes(conn: duckdb.DuckDBPyConnection, window: PaperWindowRow) -> list[str]:
    """Why the window's switch counts as engaged for a lock holder: the derived
    state's causes, then each unconsumed `engage_kill_switch` override."""
    window_id = window.window_id
    assert window_id is not None
    runs = runs_for(conn, window_id)
    state = switch.derive(
        window,
        kill_switch_events_for(conn, window_id),
        [r.run for r in runs],
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )
    causes = list(state.causes)
    causes += [
        f"override {o.override_id} engage_kill_switch is not yet consumed"
        for o in unconsumed_kill_switch_overrides(conn, window_id)
    ]
    return causes


def _refuse_if_engaged(conn: duckdb.DuckDBPyConnection, window: PaperWindowRow) -> None:
    causes = _engaged_causes(conn, window)
    if causes:
        raise WindowCommandRefused(KILL_SWITCH, "the kill switch is engaged: " + "; ".join(causes))


def _open_orders(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[str]:
    """Every order of the window with no terminal event in the journal
    (`pending` ones included), whatever the broker says: the same
    `non_terminal_orders` read `_not_ready` names for `stop` (#542)."""
    return [
        f"order {o.client_order_id} ({o.symbol}) is not terminal"
        for o in sorted(
            non_terminal_orders(conn, window_id=window_id), key=lambda o: o.client_order_id
        )
    ]


def _not_ready(conn: duckdb.DuckDBPyConnection, window_id: int) -> list[str]:
    """Every order of the window that is not terminal, every outcome kind a
    terminal one earns (`execution.outcomes`) with no row yet, and (#571, spec
    req 17 and req 15 (3)) every order with a live fill (`superseded_by` null,
    what `fills_for` returns) journaled after its first terminal event: a fill
    journaled with that event shares its stamp, and a feed fill after req 8's
    synthetic fill is superseded, so neither is listed."""
    orders = orders_for(conn, window_id=window_id)
    open_ids = {o.client_order_id for o in non_terminal_orders(conn, window_id=window_id)}
    terminal: dict[str, str] = {}
    terminal_at: dict[str, datetime] = {}
    for event in sorted(
        order_events_for(conn, window_id=window_id), key=lambda e: (e.known_at, e.ingested_at)
    ):
        if event.status in TERMINAL_ORDER_STATUSES:
            terminal.setdefault(event.client_order_id, event.status)
            terminal_at.setdefault(event.client_order_id, event.known_at)
    filled: dict[str, float] = {}
    late: set[str] = set()
    for item in fills_for(conn, window_id=window_id):
        coid = item.fill.client_order_id
        filled[coid] = filled.get(coid, 0.0) + item.fill.quantity
        if coid in terminal_at and item.fill.known_at > terminal_at[coid]:
            late.add(coid)
    written = {(o.client_order_id, o.kind) for o in outcomes_for(conn, window_id)}

    missing: list[str] = []
    for order in sorted(orders, key=lambda o: o.client_order_id):
        coid = order.client_order_id
        if coid in late:
            missing.append(
                f"order {coid} ({order.symbol}) has a live fill journaled after its terminal event"
            )
        if coid in open_ids or coid not in terminal:
            missing.append(f"order {coid} ({order.symbol}) is not terminal")
            continue
        earned: list[str] = []
        if filled.get(coid, 0.0) > 0 and order.side == _BUY:
            earned.append(POSITION_RETURN)
        if filled.get(coid, 0.0) > 0 and order.side == _SELL:
            earned.append(REALISED_PNL)
        if terminal[coid] != _FILLED:
            earned.append(NOT_EXECUTED)
        missing += [
            f"order {coid} ({order.symbol}) has no {kind} outcome"
            for kind in earned
            if (coid, kind) not in written
        ]
    return missing


def _origin(parts: dict[str, float], security_id: str) -> str:
    """`untradable` when any counted part is untradable, else `dust` (spec req
    14). Raises `ValueError` when no part is counted at all."""
    if parts["untradable"] > 0 or parts["carried_untradable"] > 0:
        return _UNTRADABLE
    if parts["dust"] > 0 or parts["carried_dust"] > 0:
        return _DUST
    raise ValueError(f"the residue of {security_id} has no part with an origin")


def _residues(
    connect: Connect, window: PaperWindowRow, session: date, frozen: RiskConfig
) -> dict[str, dict[str, Any]]:
    """The `closed` row's residues, keyed by `security_id`, or a `not_flat`
    refusal naming each name held above its residue (module docstring)."""
    window_id = window.window_id
    assert window_id is not None
    tolerance = frozen.reconcile_quantity_tolerance
    with connect() as conn:
        fills = fills_for(conn, window_id=window_id)
        orders = orders_for(conn, window_id=window_id)
        adjustments = adjustments_for(conn, window_id)
        ok_rows = [r for r in reconciliations_for(conn, window_id) if r.status == OK]
        decided = decisions_for(conn, window_id)
        marks = positions_daily_for(conn, window_id)
        runs = [r.run for r in runs_for(conn, window_id)]
        actions = live_actions_as_of(conn, session_close(previous_session(session)))
    stated = [r for r in ok_rows if _ny_date(r.at) <= session]
    ledger = from_journal(
        fills,
        orders,
        adjustments,
        actions,
        stated[-1] if stated else None,
        window.starting_cash,
        session,
        window_id=window_id,
        quantity_tolerance=tolerance,
    )
    decisions = [d.decision for d in decided]
    events = [e for d in decided for e in d.events]
    carried = [a for a in adjustments if a.kind == _CARRIED_RESIDUE]

    def part(
        security_id: str,
        rows: list[AdjustmentRow],
        *,
        with_decisions: bool = True,
        with_marks: bool = True,
    ) -> float:
        return plan_rules.residue(
            security_id,
            rows,
            decisions if with_decisions else [],
            events if with_decisions else [],
            marks if with_marks else [],
            ledger,
            actions,
            window_id=window_id,
            runs=runs,
        )

    listed: dict[str, dict[str, Any]] = {}
    offenders: list[str] = []
    for security_id, held in sorted(ledger.positions.items()):
        if held < -tolerance:
            # Long-only: a short the broker agrees with is never "flat".
            offenders.append(f"{security_id} holds {held:g}, a short position")
            continue
        if held <= tolerance:
            continue
        quantity = part(security_id, adjustments)
        if held > quantity + tolerance:
            offenders.append(f"{security_id} holds {held:g} against a residue of {quantity:g}")
            continue
        # The parts, each through `plan.residue` itself: without the carried
        # rows (dust + untradable), without the marks too (dust alone: an
        # untradable part needs a `tradable = false` mark), and the carried
        # rows of each origin alone.
        uncarried = part(security_id, [])
        dust = part(security_id, [], with_marks=False)
        parts = {
            "untradable": uncarried - dust,
            "dust": dust,
            "carried_untradable": part(
                security_id,
                [a for a in carried if a.origin == _UNTRADABLE],
                with_decisions=False,
            ),
            "carried_dust": part(
                security_id, [a for a in carried if a.origin == _DUST], with_decisions=False
            ),
        }
        listed[security_id] = {
            "quantity": held,
            "origin": _origin(parts, security_id),
        }
    if offenders:
        raise WindowCommandRefused(
            NOT_FLAT,
            "the account is not flat apart from residues: " + "; ".join(offenders),
        )
    return listed


def _engage_fault(
    settings: Settings, clock: Callable[[], datetime], window_id: int, exc: Exception
) -> str | None:
    """Engage the switch for a reconciliation fault, as `paper reconcile` does;
    the error text when the row could not be written."""
    try:
        engaged = switch.engage(
            settings,
            clock,
            window_id=window_id,
            source=_FAULT,
            fault_type=ReconciliationError.__name__,
            reason=str(exc),
        )
    except Exception as engage_error:  # a bad clock reading, say
        engaged = switch.WriteFailed(f"{type(engage_error).__name__}: {engage_error}")
    return engaged.error if isinstance(engaged, switch.WriteFailed) else None


def _latest_reconciliation(conn: duckdb.DuckDBPyConnection, window_id: int) -> Any:
    rows = reconciliations_for(conn, window_id)
    if not rows:
        raise RuntimeError(f"window {window_id} has no reconciliation row")
    return max(rows, key=lambda r: r.reconciliation_id or 0)


def stop(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    reason: str,
) -> StopResult:
    """`paper stop --reason` (module docstring). `connect` opens a write chunk
    (`lambda: store.db.open_for_write(settings)`). Raises `WindowCommandRefused`
    for every refusal, `LockHeld` while another process holds the run lock,
    `ClockError` for a bad clock reading, and whatever the broker or the store
    raises."""
    note = _note(reason, "paper stop")
    with run_lock(settings):
        with connect() as conn:
            window, window_id = _window_of(conn)
            _refuse_if_engaged(conn, window)
            requested = any(s.state == REQUESTED for s in window_stops_for(conn, window_id))
        now = _command_clock(clock)

        if not requested:
            with connect() as conn:
                _refuse_if_engaged(conn, window)
                append(
                    conn,
                    PaperWindowStopRow(
                        window_id=window_id,
                        at=now,
                        state=REQUESTED,
                        reason=note,
                        known_at=now,
                        ingested_at=now,
                    ),
                )
            return StopResult(REQUESTED, None, None)

        with connect() as conn:
            missing = _not_ready(conn, window_id)
        if missing:
            raise WindowCommandRefused(
                NOT_READY, "the window cannot close yet: " + "; ".join(missing)
            )

        frozen = frozen_risk(window)
        session = command_session(now)
        try:
            result = reconcile_now(
                settings,
                connect,
                broker,
                window,
                session,
                clock,
                connect,
                frozen=frozen,
                as_of=_command_clock(clock),  # every row journaled so far (#488)
            )
        except ReconciliationError as exc:
            message = f"reconciliation failed: {exc}"
            error = _engage_fault(settings, clock, window_id, exc)
            if error is not None:
                message += (
                    f"; the kill switch row could not be written, so it is NOT engaged: {error}"
                )
            raise WindowCommandRefused(RECONCILIATION, message) from exc
        with connect() as conn:
            reconciliation = _latest_reconciliation(conn, window_id)
        if result.status != OK:
            waiting = ", ".join((*result.lagging_ids, *result.pending_ids)) or "none listed"
            raise WindowCommandRefused(
                RECONCILIATION,
                f"reconciliation {reconciliation.reconciliation_id} is {result.status}, "
                f"not ok (orders: {waiting})",
            )

        residues = _residues(connect, window, session, frozen)
        residues_json = json.dumps(residues, sort_keys=True, allow_nan=False)
        stamp = _command_clock(clock)
        if stamp < reconciliation.at:
            raise ClockError(
                f"clock went back from {reconciliation.at.isoformat()} to {stamp.isoformat()}"
            )
        with connect() as conn:
            _refuse_if_engaged(conn, window)
            latest = _latest_reconciliation(conn, window_id)
            if latest.reconciliation_id != reconciliation.reconciliation_id:
                raise WindowCommandRefused(
                    RECONCILIATION,
                    f"reconciliation {latest.reconciliation_id} was written after "
                    f"{reconciliation.reconciliation_id}: run paper stop again",
                )
            append(
                conn,
                PaperWindowStopRow(
                    window_id=window_id,
                    at=stamp,
                    state=CLOSED,
                    reason=note,
                    reconciliation_id=reconciliation.reconciliation_id,
                    residues_json=residues_json,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
        return StopResult(CLOSED, reconciliation.reconciliation_id, residues_json)


def abandon(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    reason: str,
) -> AbandonResult:
    """`paper abandon --reason`, owner-only (module docstring; #247 Q13).
    Raises `WindowCommandRefused` for a blank reason, no open window or an
    open order of the window (before any broker call or write),
    `LockHeld` while another process holds the run lock, `ClockError` for a
    bad clock reading, and whatever the broker or the store raises (nothing
    but the reconciliation row is then written, plus a `fault` engagement
    after a mismatch; a note on the error says when that engagement could
    not be written, so the switch is NOT engaged)."""
    note = _note(reason, "paper abandon")
    with run_lock(settings):
        with connect() as conn:
            window, window_id = _window_of(conn)
            still_open = _open_orders(conn, window_id)
        if still_open:
            raise WindowCommandRefused(
                OPEN_ORDERS,
                "cancel or wait for the window's own open orders, run paper resume so "
                "the journal collects them, then abandon: " + "; ".join(still_open),
            )
        frozen = frozen_risk(window)
        now = _command_clock(clock)
        mismatch: ReconciliationError | None = None
        try:
            reconcile_now(
                settings,
                connect,
                broker,
                window,
                command_session(now),
                clock,
                connect,
                frozen=frozen,
                as_of=_command_clock(clock),  # every row journaled so far (#488)
            )
        except ReconciliationError as exc:
            mismatch = exc  # its row is written before the error: abandon lists it
        try:
            return _abandon_row(connect, broker, clock, window_id, note)
        except Exception as exc:
            # The window stays open over a mismatch row: engage, as `stop` does,
            # so it is never left unwatched, and say so when that fails too.
            if mismatch is not None:
                error = _engage_fault(settings, clock, window_id, mismatch)
                if error is not None:
                    exc.add_note(
                        "the kill switch row could not be written, so it is NOT engaged: " + error
                    )
            raise


def _abandon_row(
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    window_id: int,
    note: str,
) -> AbandonResult:
    """`abandon`'s positions read and its `abandoned` row, after the final
    reconciliation."""
    positions = broker.positions()
    with connect() as conn:
        reconciliation = _latest_reconciliation(conn, window_id)
    recorded = json.loads(reconciliation.mismatches_json or "{}")
    residues_json = json.dumps(
        {
            "positions": {
                symbol: position.quantity
                for symbol, position in sorted(positions.items())
                if position.quantity != 0
            },
            "mismatches": recorded.get("mismatches", []),
            "lagging": recorded.get("lagging", []),
            "pending": recorded.get("pending", []),
        },
        sort_keys=True,
        allow_nan=False,
    )
    stamp = _command_clock(clock)
    if stamp < reconciliation.at:
        raise ClockError(
            f"clock went back from {reconciliation.at.isoformat()} to {stamp.isoformat()}"
        )
    with connect() as conn:
        _window_of(conn)
        append(
            conn,
            PaperWindowStopRow(
                window_id=window_id,
                at=stamp,
                state=ABANDONED,
                reason=note,
                reconciliation_id=reconciliation.reconciliation_id,
                residues_json=residues_json,
                known_at=stamp,
                ingested_at=stamp,
            ),
        )
    return AbandonResult(reconciliation.reconciliation_id, reconciliation.status, residues_json)


def kill(
    settings: Settings,
    connect: Connect,
    clock: Callable[[], datetime],
    reason: str,
) -> int:
    """`paper kill --reason` (module docstring): the `engaged` row's
    `event_id`. Takes no run lock. Raises `WindowCommandRefused` for a blank
    reason, no open window, or more than one open window (only one window
    may ever be open, spec req 14; nothing is written for any of these) and
    `KillWriteFailed` when the row cannot be written."""
    note = _note(reason, "paper kill")
    with connect() as conn:
        _, window_id = _window_of(conn)
    try:
        engaged = switch.engage(settings, clock, window_id=window_id, source=_OWNER, reason=note)
    except Exception as exc:  # a bad clock reading, say: still not engaged
        engaged = switch.WriteFailed(f"{type(exc).__name__}: {exc}")
    if isinstance(engaged, switch.WriteFailed):
        raise KillWriteFailed(
            f"the kill switch row could not be written, so it is NOT engaged: {engaged.error}"
        )
    return engaged


def _min_override_reason_chars(window: PaperWindowRow) -> int:
    """The window's frozen `paper.min_override_reason_chars`; `ValueError`
    when `frozen_json` lacks it or it is not a positive integer."""
    try:
        frozen = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    value = frozen.get(_MIN_OVERRIDE_REASON_KEY) if isinstance(frozen, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"window {window.window_id} frozen_json has no usable "
            f"{_MIN_OVERRIDE_REASON_KEY}: {value!r}"
        )
    return value


def _override_fields(
    window: PaperWindowRow, kind: str, rebalance_session: date | None, security_id: str | None
) -> None:
    """Refuse `override` for a kind outside the schema's set or fields it does
    not take (module docstring)."""
    kinds = JOURNAL_ENUMS[("overrides", "kind")]
    if kind not in kinds:
        raise WindowCommandRefused(OVERRIDE, f"override kind {kind!r} is not one of {kinds}")
    if kind == _ENGAGE_KILL_SWITCH:
        if rebalance_session is not None or security_id is not None:
            raise WindowCommandRefused(OVERRIDE, f"{kind} takes no rebalance session and no name")
        return
    if rebalance_session is None or not security_id:
        raise WindowCommandRefused(OVERRIDE, f"{kind} needs a rebalance session and a name")
    if isinstance(rebalance_session, datetime) or rebalance_session != last_session_of_month(
        rebalance_session.year, rebalance_session.month
    ):
        raise WindowCommandRefused(
            OVERRIDE, f"{rebalance_session} is not a rebalance session (a month's last session)"
        )
    if rebalance_session < window.first_rebalance_session:
        raise WindowCommandRefused(
            OVERRIDE,
            f"{rebalance_session} is before the window's first rebalance "
            f"{window.first_rebalance_session}",
        )


def override(
    settings: Settings,
    clock: Callable[[], datetime],
    kind: str,
    rebalance_session: date | None,
    security_id: str | None,
    reason: str,
) -> int:
    """Append one `overrides` row to the open window and return its
    `override_id` (module docstring; spec req 9). Raises
    `WindowCommandRefused` for every refusal, nothing written, and
    `StoreLockedError` when the store stays locked ("store busy"). Kind
    `settle_order` is refused first (spec req 17, #571)."""
    if kind == SETTLE_ORDER_KIND:
        raise WindowCommandRefused(
            OVERRIDE,
            f"override kind {kind!r} is written only by `paper settle --order --reason`, "
            "whose gate reads the broker first",
        )
    now = _command_clock(clock)
    note = reason.strip()
    with open_for_write(settings) as conn:
        window, window_id = _window_of(conn)
        _override_fields(window, kind, rebalance_session, security_id)
        minimum = _min_override_reason_chars(window)
        if len(note) < minimum:
            raise WindowCommandRefused(
                REASON,
                f"the override reason is {len(note)} characters once trimmed; "
                f"the window's frozen minimum is {minimum}",
            )
        override_id = append(
            conn,
            OverrideRow(
                window_id=window_id,
                made_at=now,
                rebalance_session=rebalance_session,
                security_id=security_id,
                kind=kind,
                reason=note,
                known_at=now,
                ingested_at=now,
            ),
        )
    assert override_id is not None
    return override_id


# --- `paper settle` (T84b, spec req 17, #571) -----------------------------------------

UNKNOWN_ORDER = "unknown_order"
ALREADY_TERMINAL = "already_terminal"
PENDING_ORDER = "pending_order"
NOT_ENGAGED = "not_engaged"
ACCOUNT_MISMATCH = "account_mismatch"
BROKER_OPEN = "broker_open"
UNJOURNALED_FILL = "unjournaled_fill"
UNEXPLAINED_POSITION = "unexplained_position"
OTHER_OPEN_ORDER = "other_open_order"
OWNER_SETTLED_UNKNOWN = "owner_settled_unknown"
_CANCELLED = "cancelled"
_PENDING = "pending"
_ACKNOWLEDGING = ("accepted", "replay")
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SettleResult:
    """What `settle_order` wrote: the `overrides` row's id, the settled order,
    the one `known_at` both rows carry, and whether the reset exception of
    gate (3) applied."""

    override_id: int
    client_order_id: str
    known_at: datetime
    reset: bool


@dataclass(frozen=True)
class _SettleTarget:
    """The journal facts the gate read for the named order."""

    window: PaperWindowRow
    window_id: int
    order: OrderRow
    note: str
    pending_at: datetime
    broker_order_id: str | None
    rebalance_session: date | None
    others: tuple[OrderRow, ...]


def _settle_journal_gate(
    conn: duckdb.DuckDBPyConnection, client_order_id: str, reason: str
) -> _SettleTarget:
    """Req 17's journal refusals, in its order: `no_window`, `unknown_order`,
    `already_terminal`, `pending_order`, `reason`, `not_engaged`."""
    window, window_id = _window_of(conn)
    order = next(
        (o for o in orders_for(conn, window_id=window_id) if o.client_order_id == client_order_id),
        None,
    )
    if order is None:
        raise WindowCommandRefused(
            UNKNOWN_ORDER, f"{client_order_id!r} is not an order of the open window {window_id}"
        )
    events = sorted(
        order_events_for(conn, window_id=window_id, client_order_ids=[client_order_id]),
        key=lambda e: (e.known_at, e.ingested_at),
    )
    if any(e.status in TERMINAL_ORDER_STATUSES for e in events):
        raise WindowCommandRefused(
            ALREADY_TERMINAL, f"order {client_order_id} already has a terminal event"
        )
    if any(o.client_order_id == client_order_id for o in pending_orders(conn, window_id=window_id)):
        raise WindowCommandRefused(
            PENDING_ORDER,
            f"order {client_order_id} is pending (never acknowledged): paper resume settles it",
        )
    note = reason.strip()
    minimum = _min_override_reason_chars(window)
    if len(note) < minimum:
        raise WindowCommandRefused(
            REASON,
            f"the settle reason is {len(note)} characters once trimmed; "
            f"the window's frozen minimum is {minimum}",
        )
    runs = runs_for(conn, window_id)
    state = switch.derive(
        window,
        kill_switch_events_for(conn, window_id),
        [r.run for r in runs],
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )
    if not state.engaged:
        raise WindowCommandRefused(
            NOT_ENGAGED,
            "the kill switch is not engaged: engage it with paper kill --reason first, "
            "so no settlement runs beside a trading run",
        )
    pending_at = [e.known_at for e in events if e.status == _PENDING]
    if not pending_at:
        raise JournalIntegrityError(f"order {client_order_id} has no pending event")
    acknowledged = [e for e in events if e.status in _ACKNOWLEDGING and e.broker_order_id]
    accepted = [e for e in acknowledged if e.status == _ACKNOWLEDGING[0]]
    ack = (accepted or acknowledged)[0] if acknowledged else None
    decision = next(
        (
            d.decision
            for d in decisions_for(conn, window_id)
            if d.decision.decision_id == order.decision_id
        ),
        None,
    )
    if decision is None:
        raise JournalIntegrityError(f"order {client_order_id} cites no decision of the window")
    others = tuple(
        sorted(
            (
                o
                for o in non_terminal_orders(conn, window_id=window_id)
                if o.security_id == order.security_id and o.client_order_id != client_order_id
            ),
            key=lambda o: o.client_order_id,
        )
    )
    return _SettleTarget(
        window=window,
        window_id=window_id,
        order=order,
        note=note,
        pending_at=min(pending_at),
        broker_order_id=None if ack is None else ack.broker_order_id,
        rebalance_session=decision.rebalance_session,
        others=others,
    )


def _ledger_view(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    target: _SettleTarget,
    as_of: datetime,
    broker_symbols: list[str],
    tolerance: float,
) -> tuple[float, str | None]:
    """The ledger's quantity in the order's name (the window's live fills and
    adjustments known at `as_of`, split-adjusted through the clock's session S
    by the actions known at close(S-1), the view `reconcile.compare` takes)
    and the broker symbol reconciliation maps the name to (None: unmapped).
    A journal row stamped after `as_of` means the clock went back since it
    was written: `ClockError`, never a cut that silently drops it."""
    window, window_id = target.window, target.window_id
    session = command_session(as_of)
    fills = fills_for(conn, window_id=window_id)
    orders = orders_for(conn, window_id=window_id)
    adjustments = adjustments_for(conn, window_id)
    reconciliations = reconciliations_for(conn, window_id)
    stamps = (
        [f.fill.known_at for f in fills]
        + [o.known_at for o in orders]
        + [a.known_at for a in adjustments]
        + [r.known_at for r in reconciliations]
    )
    if stamps and max(stamps) > as_of:
        raise ClockError(
            f"the clock reading {as_of.isoformat()} is before journal row stamped "
            f"{max(stamps).isoformat()}"
        )
    stated = [r for r in reconciliations if r.status == OK and _ny_date(r.at) <= session]
    ledger = from_journal(
        fills,
        orders,
        adjustments,
        live_actions_as_of(conn, session_close(previous_session(session))),
        stated[-1] if stated else None,
        window.starting_cash,
        session,
        window_id=window_id,
        quantity_tolerance=tolerance,
    )
    symbols = explanations_as_of(
        conn,
        window,
        session,
        as_of=as_of,
        settings=settings,
        broker_symbols=broker_symbols,
        quantity_tolerance=tolerance,
    ).symbols
    return ledger.positions.get(target.order.security_id, 0.0), symbols.get(
        target.order.security_id
    )


def _reading_json(reading: Order | None) -> Any:
    if reading is None:
        return _UNKNOWN
    return {
        "status": reading.status.value,
        "filled_quantity": reading.filled_quantity,
        "filled_avg_price": reading.filled_avg_price,
        "filled_at": None if reading.filled_at is None else reading.filled_at.isoformat(),
    }


def settle_order(
    settings: Settings,
    connect: Connect,
    broker: Broker,
    clock: Callable[[], datetime],
    client_order_id: str,
    reason: str,
) -> SettleResult:
    """`paper settle --order <client_order_id> --reason` (spec req 17, #571):
    journal one acknowledged order of the open window terminal (`cancelled`,
    reason `owner_settled_unknown`, no fill) beside an `overrides` row of kind
    `settle_order`, on the evidence of one fresh read-only broker read.

    In req 17's order: the run lock (`LockHeld` propagates: `locked`); the
    journal refusals `no_window`, `unknown_order`, `already_terminal`,
    `pending_order`, `reason` (the trimmed note under the window's frozen
    `paper.min_override_reason_chars`) and `not_engaged` (the derived switch),
    each before any broker call or write; one clock reading; then `account`
    (`account_mismatch` when its id is not the window's), `get_order`,
    `open_orders`, `fills(since)` (`since` = the order's `pending` event's
    `known_at` minus `paper.fill_read_overlap_seconds`) and `positions`, and a
    `get_order` per other non-terminal order of the window on the same
    `security_id`. It refuses `broker_open` (a non-terminal reading, or the id
    listed open), `unjournaled_fill` (a fill of the order in the stream whose
    `broker_fill_id` the journal lacks), `unexplained_position` (the broker's
    quantity in the name beyond the ledger's by more than the frozen
    `risk.reconcile_quantity_tolerance` in the direction the order's fill
    would move it: above for a buy, below for a sell; a name reconciliation
    cannot map refuses too) and `other_open_order` (another non-terminal order
    on the name the broker still knows). Neither of the last two applies under
    the reset exception: `get_order` raised `UnknownOrderError` and
    `positions()` holds nothing. Any broker error but that one
    `UnknownOrderError` propagates, nothing written.

    Then one clock reading, which must be later than the gate's reading and
    than every journal row of the order (else `ClockError`), stamps both rows
    (`known_at = ingested_at`) in one `connect()` transaction, which re-reads
    the order's events and refuses `already_terminal` if one appeared. No other
    row is written and no `submit` or `cancel` is ever called. The only caller
    is the owner's CLI (#542 item 5)."""
    with run_lock(settings):
        with connect() as conn:
            target = _settle_journal_gate(conn, client_order_id, reason)
        window = target.window
        tolerance = frozen_risk(window).reconcile_quantity_tolerance
        now = _command_clock(clock)

        account = broker.account()
        if account.account_id != window.account_id:
            raise WindowCommandRefused(
                ACCOUNT_MISMATCH,
                f"the broker's account {account.account_id!r} is not window "
                f"{target.window_id}'s {window.account_id!r}",
            )
        try:
            reading: Order | None = broker.get_order(client_order_id)
        except UnknownOrderError:
            reading = None
        open_ids = sorted(o.client_order_id for o in broker.open_orders())
        since = target.pending_at - timedelta(seconds=settings.paper.fill_read_overlap_seconds)
        stream = broker.fills(since)
        positions = broker.positions()
        others: list[tuple[str, Order | None]] = []
        for other in target.others:
            try:
                others.append((other.client_order_id, broker.get_order(other.client_order_id)))
            except UnknownOrderError:
                others.append((other.client_order_id, None))

        with connect() as conn:
            journaled = all_fill_ids(conn)
            ledger_quantity, symbol = _ledger_view(
                conn, settings, target, now, sorted(positions), tolerance
            )
        held = positions.get(symbol) if symbol is not None else None
        broker_quantity = 0.0 if held is None else held.quantity
        reset = reading is None and not any(p.quantity != 0 for p in positions.values())

        if reading is not None and reading.status not in TERMINAL_STATUSES:
            raise WindowCommandRefused(
                BROKER_OPEN,
                f"the broker reports order {client_order_id} {reading.status.value}: "
                "it may yet fill; cancel it or wait",
            )
        if client_order_id in open_ids:
            raise WindowCommandRefused(
                BROKER_OPEN, f"the broker lists order {client_order_id} among its open orders"
            )
        missing = sorted(
            f.broker_fill_id
            for f in stream
            if f.client_order_id == client_order_id and f.broker_fill_id not in journaled
        )
        if missing:
            raise WindowCommandRefused(
                UNJOURNALED_FILL,
                f"the fill stream holds fills of {client_order_id} the journal lacks: "
                + ", ".join(missing),
            )
        if not reset:
            if symbol is None:
                raise WindowCommandRefused(
                    UNEXPLAINED_POSITION,
                    f"{target.order.security_id} maps to no broker symbol, so its "
                    "position cannot be read",
                )
            excess = broker_quantity - ledger_quantity
            if target.order.side == _SELL:
                excess = -excess
            if excess > tolerance:
                raise WindowCommandRefused(
                    UNEXPLAINED_POSITION,
                    f"the broker holds {broker_quantity:g} {symbol} against the ledger's "
                    f"{ledger_quantity:g}: the {target.order.side}'s fill may exist",
                )
            known = [coid for coid, other in others if other is not None]
            if known:
                raise WindowCommandRefused(
                    OTHER_OPEN_ORDER,
                    f"other open orders of the window on {target.order.security_id} the "
                    "broker still knows could net its quantity: " + ", ".join(known),
                )

        stamp = _command_clock(clock)
        if stamp <= now:
            raise ClockError(
                f"the settle stamp {stamp.isoformat()} is not after the gate's reading "
                f"{now.isoformat()}"
            )
        evidence: dict[str, Any] = {
            "account_id": account.account_id,
            "get_order": _reading_json(reading),
            "open_order_ids": open_ids,
            "symbol": symbol,
            "broker_quantity": broker_quantity,
            "ledger_quantity": ledger_quantity,
            "fill_ids": [f.broker_fill_id for f in stream],
            "other_orders": [
                {"client_order_id": coid, "get_order": _reading_json(other)}
                for coid, other in others
            ],
            "reset": reset,
        }
        with connect() as conn:
            events = order_events_for(conn, window_id=None, client_order_ids=[client_order_id])
            if any(e.status in TERMINAL_ORDER_STATUSES for e in events):
                raise WindowCommandRefused(
                    ALREADY_TERMINAL,
                    f"order {client_order_id} got a terminal event after the gate",
                )
            rows = [e.known_at for e in events] + [
                f.fill.known_at for f in fills_for(conn, client_order_ids=[client_order_id])
            ]
            if rows and stamp <= max(rows):
                raise ClockError(
                    f"the settle stamp {stamp.isoformat()} is not after the order's latest "
                    f"journal row {max(rows).isoformat()}"
                )
            override_id = append(
                conn,
                OverrideRow(
                    window_id=target.window_id,
                    made_at=stamp,
                    rebalance_session=target.rebalance_session,
                    security_id=target.order.security_id,
                    client_order_id=client_order_id,
                    kind=SETTLE_ORDER_KIND,
                    reason=target.note,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
            assert override_id is not None
            append(
                conn,
                OrderEventRow(
                    client_order_id=client_order_id,
                    status=_CANCELLED,
                    reason=OWNER_SETTLED_UNKNOWN,
                    broker_order_id=target.broker_order_id,
                    raw_json=json.dumps(
                        {"override_id": override_id, **evidence}, sort_keys=True, allow_nan=False
                    ),
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
    return SettleResult(override_id, client_order_id, stamp, reset)
