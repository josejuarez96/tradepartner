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
- `stop_exits`: on a `stop` run, a `window_stop` exit for each held name, for
  the holding minus its residue (`plan.residue`, T52b), floored to whole shares
  where the name is not fractionable.

**Blocking.** No new forced exit is made for a name that has an open or in-flight
decision among `decisions` (a plan decision of the pending rebalance, or a
forced exit: that decision handles the name, or is re-attempted for its
remainder), or a non-terminal own sell from any session (`open_sells`). A
`window_stop` exit is blocked by an open or in-flight `forced_exit` decision, a
non-terminal own sell, or a same-run `untradable` exit (`untradable_this_run`);
a plan decision does not block it (a `stop` run plans nothing). A settled or
closed decision, however recent, blocks nothing.

**Inputs.** `held` is the ledger's quantity per `security_id` on S (names at
zero are ignored, a negative or non-finite one raises). `decisions` are the
decisions that can still act on S: the pending rebalance's and the window's
forced exits, this run's journaled ones included; `states` maps every one of
their ids to its `plan.decision_state` (a decision with no state raises, so a
missing derivation never lets a second sell through). A plan decision of a
rebalance that is no longer pending must not be passed: it would block.
`listings_at` maps a name to its listing's last session as read at
close(S-1), `None` when the end is unknown; a name present with an end on or
before S-1, or `None`, has ended (`plan.decisions_from` reads it the same way),
and a name absent from it is listed. `assets` is this run's `assets` read keyed
by `security_id`; a name that would be decided and is missing from it raises.

**Spin-off receipts.** `adjustments` are the open window's rows; a
`spinoff_receipt` row dated on or before S makes its name a candidate until it
is spent: a receipt is spent by an `untargeted_receipt` forced exit of its name
decided at or after the receipt's `known_at` that is not closed (open, in flight
or settled), so shares the plan buys after the receipt was sold are never sold
as a receipt, and a receipt exit closed as `untradable` is re-evaluated. A
name that is both delisted and a receipt gets one decision, `delisted`.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from tradepartner.calendar import previous_session
from tradepartner.execution.plan import DecisionState, State
from tradepartner.execution.risk import OpenSell
from tradepartner.store.journal import AdjustmentRow, DecisionEventRow, DecisionRow
from tradepartner.store.schema import (
    DELISTED_REASON,
    UNTARGETED_RECEIPT_REASON,
    WINDOW_STOP_REASON,
)

_FORCED_EXIT = "forced_exit"
_SELL = "sell"
_SKIPPED = "skipped"
_UNTRADABLE = "untradable"
_SPINOFF_RECEIPT = "spinoff_receipt"
_ACTIVE = frozenset({State.OPEN, State.IN_FLIGHT})


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

    `planned_quantity` is in shares on S (a forced exit has no rebalance
    session, so `plan.remainder` states it for the New York date of its
    `known_at`). `skipped_reason` is set (`untradable`) when the decision is
    closed at once: the caller writes `event`'s row with it, and nothing is
    ordered for it this run.
    """

    security_id: str
    reason: str
    planned_quantity: float
    whole_share: bool
    skipped_reason: str | None = None

    def row(self, *, run_id: int, known_at: datetime, ingested_at: datetime) -> DecisionRow:
        """The `decisions` row for run `run_id`; its id is assigned on insert."""
        return DecisionRow(
            run_id=run_id,
            rebalance_session=None,
            security_id=self.security_id,
            side=_SELL,
            planned_quantity=self.planned_quantity,
            whole_share=self.whole_share,
            decision=_FORCED_EXIT,
            reason=self.reason,
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


def _asset(security_id: str, assets: Mapping[str, ExitAsset]) -> ExitAsset:
    if security_id not in assets:
        raise ValueError(f"{security_id} is missing from the assets read")
    return assets[security_id]


def _active(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    *,
    forced_only: bool,
) -> set[str]:
    """Names with an open or in-flight decision (a forced exit only, if asked)."""
    names: set[str] = set()
    for decision in decisions:
        state = _state(decision, states)
        if forced_only and decision.decision != _FORCED_EXIT:
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
        if d.decision == _FORCED_EXIT
        and d.reason == UNTARGETED_RECEIPT_REASON
        and _state(d, states).state is not State.CLOSED
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
) -> list[ExitDecision]:
    """The new forced exits of the run on session S = `session` (module
    docstring; spec req 7 step 7), by `security_id`: `delisted` for a held name
    whose listing ended at close(S-1), `untargeted_receipt` for a held,
    unspent spin-off receipt, each for the whole holding, `whole_share` from
    `assets`; one not tradable now is returned closed (`skipped_reason`
    `untradable`). None for a name with an open or in-flight decision or a
    non-terminal own sell.
    """
    _check_session(session)
    holding = _held(held)
    previous = previous_session(session)
    blocked = _active(decisions, states, forced_only=False) | _open_sell_names(open_sells)
    receipts = _unspent_receipts(adjustments, decisions, states, session)
    exits: list[ExitDecision] = []
    for security_id in sorted(holding):
        if security_id in blocked:
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
    other decision is not an exit. A decision with no state raises.
    """
    open_: list[DecisionRow] = []
    for decision in decisions:
        state = _state(decision, states)
        if decision.decision == _FORCED_EXIT and state.state is State.OPEN:
            open_.append(decision)
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
) -> list[ExitDecision]:
    """The `window_stop` exits of a `stop` run on session S = `session` (spec
    req 14), by `security_id`: one per held name with no open or in-flight
    `forced_exit`, no non-terminal own sell and no same-run `untradable` exit,
    for the holding minus `residues` (`plan.residue` per name, at most the
    holding), floored to whole shares when `assets` says the name is not
    fractionable (`whole_share`). A name whose whole holding is its residue gets
    none; a flagged excess below one share is returned with quantity 0, so the
    sells phase journals it `dust` and `plan.residue` reads it as a residue.
    Tradability is the phase's to check, as for any sell.
    """
    _check_session(session)
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
        _active(decisions, states, forced_only=True)
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
                planned_quantity=float(math.floor(excess)) if whole_share else excess,
                whole_share=whole_share,
            )
        )
    return exits
