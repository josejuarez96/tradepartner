"""Window start (Phase 4 spec req 14 "Entry gate, start and stop"; ADR 0009
point 3; plan T64).

`start(settings, connect, broker, clock, slug)` is `paper start --hypothesis
<slug>`. In order it refuses, before any write:

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

Once accepted, `start` appends the `paper_windows` row (`T_0`, the first
rebalance session strictly after both `holdout_end` and today, per the spec
Definitions' "Paper window"; `starting_cash` and `starting_equity` from
`account()`; `code_version`; `frozen_json`/`frozen_sha256`, the canonicalised
and hashed `risk.*` section plus `FROZEN_PAPER_KEYS`, exactly as
`registry.canonical_params_json`/`params_sha256` do for hypothesis
parameters), the `carried_residue` adjustments copied from the previous
window's listed residues (quantity and origin unchanged, dated at the stop
row's own session: the ledger itself split-adjusts an adjustment from its
`session` through any later one, `execution.ledger`'s documented
convention), and any `spinoff_receipt` adjustments a spin-off explained (see
below). It takes the run lock (T59) for its writes, and migrates a store
version 4 has left behind (`init_schema` on the write connection) before
writing.

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
"untradable" | null}` (spec req 14, Data section: "per name, quantity and
origin").

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
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
from dateutil.relativedelta import relativedelta

from tradepartner.adapters.broker import Broker
from tradepartner.calendar import last_session_of_month, session_close
from tradepartner.config import FROZEN_PAPER_KEYS, Settings
from tradepartner.execution.lock import run_lock
from tradepartner.store import registry
from tradepartner.store.asof import live_actions_as_of
from tradepartner.store.db import open_for_write
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.journal import (
    CLOSING_STOP_STATES,
    AdjustmentRow,
    JournalNotInitialised,
    PaperWindowRow,
    append,
    latest_window,
    open_window,
    window_stops_for,
)
from tradepartner.store.schema import init_schema
from tradepartner.timeutil import ensure_tz_aware_utc

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

_NEW_YORK = ZoneInfo("America/New_York")
_RISK_PREFIX = "risk."
_PAPER_PREFIX = "paper."
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
    origin: str | None


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


def _frozen_params(settings: Settings) -> dict[str, Any]:
    """The flat dict `frozen_json` canonicalises: every `risk.*` key plus
    `FROZEN_PAPER_KEYS` under `paper.*` (spec req 14)."""
    risk = settings.risk.model_dump()
    paper = settings.paper.model_dump()
    params: dict[str, Any] = {f"{_RISK_PREFIX}{k}": v for k, v in risk.items()}
    params.update({f"{_PAPER_PREFIX}{k}": paper[k] for k in FROZEN_PAPER_KEYS})
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
        if origin not in (None, _DUST, _UNTRADABLE):
            raise ValueError(f"residues_json origin for {security_id!r} is {origin!r}")
        quantity = float(entry["quantity"])
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError(f"residues_json quantity for {security_id!r} is {quantity!r}")
        residues[security_id] = _Residue(
            security_id=security_id,
            quantity=quantity,
            origin=origin,
        )
    return residues


def _current_tickers(listings: pl.DataFrame, as_of: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from <= as_of`."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        if row["valid_from"] > as_of:
            continue
        held = current.get(row["security_id"])
        if held is None or row["valid_from"] > held["valid_from"]:
            current[row["security_id"]] = row
    return current


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
    current = _current_tickers(listings, now)

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
        params = _frozen_params(settings)
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
