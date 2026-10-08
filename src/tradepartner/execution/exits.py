"""Forced and stop exits, pure (Phase 4 spec req 7 step 7 and req 14; plan T63e).

Three functions decide which names a run sells outside the plan; the run
(T63d, T63f) journals what they return and hands it to the wrapper's sells
phase. No broker, no journal write, no clock.

- `forced_exits`: a held name whose listing ended at close(S-1) is sold whole
  (`forced_exit`, reason `delisted`); one the `assets` read says is not
  tradable gets the same decision closed at once, returned with its `skipped`
  event (reason `untradable`) for the caller to write, and is re-evaluated on
  the next session; a held `spinoff_receipt` child is sold whole (reason
  `untargeted_receipt`).
- `reattempt_exits`: the open forced exits of any reason, which the phase
  re-attempts for their remainder (`plan.decision_state`, T52).

**One hand-off per exit.** The caller computes `reattempt_exits` and
`forced_exits` (or `stop_exits`) over the same `decisions` and `states`, read
before this run journals any new exit, and hands each list to the sells phase
once. The two never name the same security, since an open forced exit blocks
a new one. A new exit journaled by this run is ordered from its own list and
never re-read through `reattempt_exits` in the same run: that would put two
sells for one name in one phase.
- `stop_exits`: on a `stop` run, a `window_stop` exit for each held name, for
  the holding minus its residue (`plan.residue`, T52b), floored to whole shares
  where the name is not fractionable.

**Blocking.** No new forced exit is made for a name that has an open or in-flight
decision among `decisions` that is a forced exit or a plan decision of the
pending rebalance `pending_rebalance` (that decision handles the name, or is
re-attempted for its remainder), or a non-terminal own sell from any session
(`open_sells`). A plan decision of any other rebalance (one executed or lapsed
to `missed`) blocks nothing. A `window_stop` exit is blocked by an open or
in-flight `forced_exit` decision, a non-terminal own sell, or a same-run
`untradable` exit (`untradable_this_run`); a plan decision does not block it
(a `stop` run plans nothing). A settled or closed decision, however recent,
blocks nothing, except for `forced_exits` alone: no new forced exit is made
for a name whose latest `forced_exit` decision (any reason, by `known_at`
then decision id) is closed by a `decision_events` `skipped` row with reason
`dust` while the holding is not above that decision's `planned_quantity`, or
settled while the holding is not above its state's remainder quantity (`None`
counts as 0). The remainder below the trading minimum is a dust residue, held
and reported, never re-decided each session (owner decision 2026-10-01,
#505). A holding that grew past that quantity (a split, a new receipt) gets a
new decision, as any other case does. `stop_exits` is unaffected: a dust- or
settled-closed name still gets its `window_stop` exit, so the residue is
counted (quantity floored to 0 when `whole_share`).

**Inputs.** `held` is the ledger's quantity per `security_id` on S (names at
zero are ignored, a negative or non-finite one raises). `decisions` are every
decision of the open window known to the run, settled and closed ones
included (a spin-off receipt is spent only by a decision present here), read
before this run journals a new exit, except that `stop_exits` also takes this
run's journaled forced exits; `states` maps every one of their ids to its
`plan.decision_state` (a decision with no state raises, so a missing
derivation never lets a second sell through). `listings_at` maps a name to its
listing's last session as read at close(S-1), `None` when the end is unknown;
a name present with an end on or before S-1, or `None`, has ended
(`plan.decisions_from` reads it the same way), and a name absent from it is
listed. `assets` is this run's `assets` read keyed by `security_id`; a name
that would be decided and is missing from it raises.

**Spin-off receipts.** `adjustments` are the open window's rows; a
`spinoff_receipt` row dated on or before S makes its name a candidate until it
is spent. A receipt is spent by a decision of its name made at or after the
receipt's `known_at` that is either a plan decision (any kind and state: the
plan has taken the name in hand) or an `untargeted_receipt` forced exit that is
not closed (open, in flight or settled). Shares the plan buys after the
receipt was sold or planned are therefore never sold as a receipt, and a
receipt exit closed as `untradable` is re-evaluated on the next session. A
name that is both delisted and a receipt gets one decision, `delisted`.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from tradepartner.calendar import previous_session
from tradepartner.execution.plan import DecisionState, State
from tradepartner.execution.risk import OpenSell
from tradepartner.store.journal import AdjustmentRow, DecisionEventRow, DecisionRow
from tradepartner.store.schema import (
    DELISTED_REASON,
    UNTARGETED_RECEIPT_REASON,
    WINDOW_STOP_REASON,
)

_NEW_YORK = ZoneInfo("America/New_York")
_FORCED_EXIT = "forced_exit"
_SELL = "sell"
_SKIPPED = "skipped"
_UNTRADABLE = "untradable"
_SPINOFF_RECEIPT = "spinoff_receipt"
_ACTIVE = frozenset({State.OPEN, State.IN_FLIGHT})
_DUST = "dust"


class ExitAsset(Protocol):
    """What the exits read from the broker's `assets` answer for a name
    (`adapters.broker.Asset` fits; this module imports no adapter)."""

    @property
    def tradable(self) -> bool:
        """Whether the broker accepts orders in the name now."""
        ...

    @property
    def fractionable(self) -> bool:
        """Whether the broker trades the name in fractional shares."""
        ...


@dataclass(frozen=True)
class ExitDecision:
    """One new `forced_exit` decision before the run journals it.

    `planned_quantity` is in shares on S = `session`, post-split. A forced
    exit has no rebalance session, so `plan.remainder` reads the quantity as
    stated for the New York date of its `known_at`; `row` therefore refuses a
    `known_at` on any other New York date, which would apply a split on S
    twice. `skipped_reason` is set (`untradable`) when the decision is
    closed at once: the caller writes `event`'s row with it, and nothing is
    ordered for it this run.
    """

    security_id: str
    reason: str
    planned_quantity: float
    whole_share: bool
    session: date
    skipped_reason: str | None = None

    def row(
        self,
        *,
        run_id: int,
        known_at: datetime,
        ingested_at: datetime,
        book_id: str,
    ) -> DecisionRow:
        """The `decisions` row for run `run_id`; its id is assigned on insert.
        A `known_at` whose New York date is not `session` raises. `book_id` is
        the window's book (ADR 0015 seam 1, plan T133)."""
        if known_at.tzinfo is None or known_at.astimezone(_NEW_YORK).date() != self.session:
            raise ValueError(
                f"exit of {self.security_id} is stated for {self.session}; known_at "
                f"{known_at} is not on that New York date"
            )
        return DecisionRow(
            run_id=run_id,
            rebalance_session=None,
            security_id=self.security_id,
            side=_SELL,
            planned_quantity=self.planned_quantity,
            whole_share=self.whole_share,
            decision=_FORCED_EXIT,
            reason=self.reason,
            book_id=book_id,
            known_at=known_at,
            ingested_at=ingested_at,
        )

    def event(
        self, *, decision_id: int, run_id: int, known_at: datetime, ingested_at: datetime
    ) -> DecisionEventRow | None:
        """The `decision_events` row that closes the decision once journaled as
        `decision_id`, or `None` when it is to be ordered."""
        if self.skipped_reason is None:
            return None
        return DecisionEventRow(
            decision_id=decision_id,
            run_id=run_id,
            status=_SKIPPED,
            reason=self.skipped_reason,
            known_at=known_at,
            ingested_at=ingested_at,
        )


def _check_session(session: date) -> None:
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")


def _held(held: Mapping[str, float]) -> dict[str, float]:
    """The names held, by `security_id`; a negative or non-finite one raises."""
    out: dict[str, float] = {}
    for security_id, quantity in held.items():
        if not math.isfinite(quantity):
            raise ValueError(f"holding of {security_id} is {quantity}, not a finite number")
        if quantity < 0:
            raise ValueError(f"holding of {security_id} is {quantity}: the ledger is short")
        if quantity > 0:
            out[security_id] = quantity
    return out


def _state(decision: DecisionRow, states: Mapping[int, DecisionState]) -> DecisionState:
    if decision.decision_id is None:
        raise ValueError(f"decision of {decision.security_id} has no decision_id")
    if decision.decision_id not in states:
        raise ValueError(f"decision {decision.decision_id} has no derived state")
    return states[decision.decision_id]


class MissingAssetRefused(ValueError):
    """A held name that would be decided is missing from this run's `assets`
    read: the exit is refused and nothing is journaled or submitted for it. A
    `ValueError`, so callers that catch that keep working; the run still ends
    `failed` (fail closed), not the `SystemFaultError` halt path."""


def _asset(security_id: str, assets: Mapping[str, ExitAsset]) -> ExitAsset:
    if security_id not in assets:
        raise MissingAssetRefused(f"{security_id} is missing from the assets read")
    return assets[security_id]


def _active(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    *,
    pending_rebalance: date | None,
    forced_only: bool,
) -> set[str]:
    """Names with an open or in-flight forced exit, or (unless `forced_only`)
    an open or in-flight plan decision of `pending_rebalance`."""
    names: set[str] = set()
    for decision in decisions:
        state = _state(decision, states)
        if decision.decision != _FORCED_EXIT and (
            forced_only
            or decision.rebalance_session is None
            or decision.rebalance_session != pending_rebalance
        ):
            continue
        if state.state in _ACTIVE:
            names.add(decision.security_id)
    return names


def _open_sell_names(open_sells: Sequence[OpenSell]) -> set[str]:
    return {sell.security_id for sell in open_sells}


def _ended(security_id: str, listings_at: Mapping[str, date | None], previous: date) -> bool:
    if security_id not in listings_at:
        return False
    end = listings_at[security_id]
    return end is None or end <= previous


def _unspent_receipts(
    adjustments: Sequence[AdjustmentRow],
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    session: date,
) -> set[str]:
    """Names with a `spinoff_receipt` dated on or before S not yet spent."""
    receipts = [a for a in adjustments if a.kind == _SPINOFF_RECEIPT and a.session <= session]
    windows = {a.window_id for a in adjustments}
    if len(windows) > 1:
        raise ValueError(f"adjustments of more than one window given: {sorted(windows)}")
    spending = [
        d
        for d in decisions
        if d.rebalance_session is not None
        or (
            d.decision == _FORCED_EXIT
            and d.reason == UNTARGETED_RECEIPT_REASON
            and _state(d, states).state is not State.CLOSED
        )
    ]
    names: set[str] = set()
    for receipt in receipts:
        if receipt.security_id is None:
            raise ValueError(f"spinoff_receipt {receipt.adjustment_id} names no security")
        spent = any(
            d.security_id == receipt.security_id and d.known_at >= receipt.known_at
            for d in spending
        )
        if not spent:
            names.add(receipt.security_id)
    return names


def _latest_forced_exit(security_id: str, decisions: Sequence[DecisionRow]) -> DecisionRow | None:
    """The held name's own latest `forced_exit` decision (any reason), by
    `known_at` then decision id, or `None` if it has none."""
    mine = [d for d in decisions if d.decision == _FORCED_EXIT and d.security_id == security_id]
    if not mine:
        return None
    return max(mine, key=lambda d: (d.known_at, d.decision_id or 0))


def _dust_blocked(
    security_id: str,
    quantity: float,
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
) -> bool:
    """Whether `security_id`'s holding `quantity` is a dust residue of its
    latest `forced_exit` decision: that decision is closed by a
    `decision_events` `skipped` row with reason `dust` and the holding is not
    above its `planned_quantity`, or it is settled and the holding is not
    above its state's remainder quantity (`None` counts as 0). A holding that
    grew past that quantity is not blocked."""
    latest = _latest_forced_exit(security_id, decisions)
    if latest is None:
        return False
    state = _state(latest, states)
    if state.state is State.CLOSED and state.event_reason == _DUST:
        return quantity <= (latest.planned_quantity or 0.0)
    if state.state is State.SETTLED:
        remainder_quantity = state.remainder.quantity if state.remainder else 0.0
        return quantity <= remainder_quantity
    return False


def forced_exits(
    held: Mapping[str, float],
    listings_at: Mapping[str, date | None],
    assets: Mapping[str, ExitAsset],
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    open_sells: Sequence[OpenSell],
    adjustments: Sequence[AdjustmentRow],
    *,
    session: date,
    pending_rebalance: date | None,
) -> list[ExitDecision]:
    """The new forced exits of the run on session S = `session` (module
    docstring; spec req 7 step 7), by `security_id`: `delisted` for a held name
    whose listing ended at close(S-1), `untargeted_receipt` for a held,
    unspent spin-off receipt, each for the whole holding, `whole_share` from
    `assets`; one not tradable now is returned closed (`skipped_reason`
    `untradable`). None for a name with an open or in-flight decision, a
    non-terminal own sell, or a dust residue of its own latest `forced_exit`
    decision (module docstring **Blocking**; owner decision 2026-10-01,
    #505). `pending_rebalance` is the window's pending rebalance session T_i,
    or `None` when none is pending.
    """
    _check_session(session)
    holding = _held(held)
    previous = previous_session(session)
    blocked = _active(
        decisions, states, pending_rebalance=pending_rebalance, forced_only=False
    ) | _open_sell_names(open_sells)
    receipts = _unspent_receipts(adjustments, decisions, states, session)
    exits: list[ExitDecision] = []
    for security_id in sorted(holding):
        if security_id in blocked:
            continue
        if _dust_blocked(security_id, holding[security_id], decisions, states):
            continue
        if _ended(security_id, listings_at, previous):
            reason = DELISTED_REASON
        elif security_id in receipts:
            reason = UNTARGETED_RECEIPT_REASON
        else:
            continue
        asset = _asset(security_id, assets)
        exits.append(
            ExitDecision(
                security_id=security_id,
                reason=reason,
                planned_quantity=holding[security_id],
                whole_share=not asset.fractionable,
                session=session,
                skipped_reason=None if asset.tradable else _UNTRADABLE,
            )
        )
    return exits


def reattempt_exits(
    decisions: Sequence[DecisionRow], states: Mapping[int, DecisionState]
) -> list[DecisionRow]:
    """The open `forced_exit` decisions of any reason (`delisted`,
    `untargeted_receipt`, `window_stop`), by `security_id` then decision id:
    the phase re-attempts each for its remainder (`states[id].remainder`). An
    in-flight one waits for its order, a settled or closed one is done; any
    other decision is not an exit. A decision with no state raises, and so do
    a repeated decision id and two open forced exits for one name (never two
    sells for one name).
    """
    open_: list[DecisionRow] = []
    seen: set[int] = set()
    for decision in decisions:
        state = _state(decision, states)
        decision_id = decision.decision_id or 0
        if decision_id in seen:
            raise ValueError(f"decision {decision_id} is given twice")
        seen.add(decision_id)
        if decision.decision == _FORCED_EXIT and state.state is State.OPEN:
            open_.append(decision)
    names = [d.security_id for d in open_]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"two open forced exits for {twice}")
    return sorted(open_, key=lambda d: (d.security_id, d.decision_id or 0))


def stop_exits(
    held: Mapping[str, float],
    residues: Mapping[str, float],
    assets: Mapping[str, ExitAsset],
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    open_sells: Sequence[OpenSell],
    untradable_this_run: Collection[str],
    *,
    session: date,
    quantity_decimals: int,
) -> list[ExitDecision]:
    """The `window_stop` exits of a `stop` run on session S = `session` (spec
    req 14), by `security_id`: one per held name with no open or in-flight
    `forced_exit`, no non-terminal own sell and no same-run `untradable` exit,
    for the holding minus `residues` (`plan.residue` per name, at most the
    holding), floored to whole shares when `assets` says the name is not
    fractionable (`whole_share`). A name whose whole holding is its residue gets
    none; a flagged excess below one share is returned with quantity 0, so the
    sells phase journals it `dust` and `plan.residue` reads it as a residue.
    Tradability is the phase's to check, as for any sell. The excess is rounded
    to `quantity_decimals` (the broker's quantity precision, `alpaca.*`) before
    the floor, so float error never turns a whole share into dust.
    """
    _check_session(session)
    if isinstance(untradable_this_run, str):
        raise ValueError("untradable_this_run must be a collection of names, not a str")
    if isinstance(quantity_decimals, bool) or not (
        isinstance(quantity_decimals, int) and quantity_decimals >= 0
    ):
        raise ValueError(f"quantity_decimals is {quantity_decimals!r}")
    holding = _held(held)
    for security_id, quantity in residues.items():
        if not (math.isfinite(quantity) and quantity >= 0):
            raise ValueError(f"residue of {security_id} is {quantity}")
        if quantity > holding.get(security_id, 0.0) and quantity > 0:
            raise ValueError(
                f"residue of {security_id} is {quantity}, above the holding "
                f"{holding.get(security_id, 0.0)}"
            )
    blocked = (
        _active(decisions, states, pending_rebalance=None, forced_only=True)
        | _open_sell_names(open_sells)
        | set(untradable_this_run)
    )
    exits: list[ExitDecision] = []
    for security_id in sorted(holding):
        if security_id in blocked:
            continue
        excess = holding[security_id] - residues.get(security_id, 0.0)
        if excess <= 0:
            continue
        whole_share = not _asset(security_id, assets).fractionable
        exits.append(
            ExitDecision(
                security_id=security_id,
                reason=WINDOW_STOP_REASON,
                planned_quantity=(
                    float(math.floor(round(excess, quantity_decimals))) if whole_share else excess
                ),
                whole_share=whole_share,
                session=session,
            )
        )
    return exits
