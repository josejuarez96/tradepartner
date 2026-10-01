"""Re-attempt scope and end-of-phase write-offs (Phase 4 spec Definitions >
Decision and req 3 "Re-runs and catch-ups"; plan T60d).

Both functions are pure over the journal's decisions and their derived states
(`plan.decision_state`, which the caller computes on session S from the
journal's decision events, orders, order events and fills): no broker, no
clock, no store. The remainders and the write-off rule live in those states,
so this module reads no order or fill row itself.

**`attempt_scope`** is what one phase may order. Re-runs and catch-ups trade
only **open** decisions, for their remainder (T52): a decision **in flight**
is left to collection, a **settled** one is done, and a **closed** one (a
skip, `dust`, `keep_name`, a `skipped` or `written_off` event, or a buy the
write-off rule applies to) is never ordered again. The sells phase takes the
open sells, forced exits included; the buys phase takes the open buys,
deferred ones included, so `risk.size_buys` rescales a deferred buy against the
remaining buys rather than every planned one. A buy is re-attempted only if a
sell of its rebalance was open or in flight at its previous attempt: that is
the write-off rule `decision_state` applies from the earlier `orders` row's
`sells_in_flight_at_submit`, so any buy it has not closed is open.

**The last-phase rule.** A buys phase that runs when no sell of its rebalance
is open or in flight is the rebalance's **last** (`AttemptScope.last`), since
no more cash can arrive for it; the phase's buy orders therefore journal
`sells_in_flight_at_submit = not last` when the states are read at the phase's
start. The rebalance is that of the buys in play (open or in flight); buys of
two rebalances in play in one phase raise. Forced exits carry no rebalance
session and never enter the test: their proceeds are cash until the next
rebalance. `decisions` must therefore hold the rebalance's sells as well as
its buys, or a phase would look last when it is not.

**`write_offs`** is the end of a buys phase: one `WrittenOff` per buy whose
derived state is written off but has no `written_off` row yet (an order of the
phase that ended short, the row collection appends when it sees the order
first), and, when the phase was the last and was not halted, one per buy still
open, which in such a phase is a buy it deferred (a deferred buy has no order
of its own, so this row is its only write-off), each with its remainder's
notional as the unfunded amount. A halt is not a funding shortfall: a halted
phase writes off only the derived ones, and `decision_state` never writes off
a buy whose order carries a halt cancel or ended `cancelled` with reason
`not_received`, so such a buy stays open and the next in-window run retries it,
sized from cash like any other.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from tradepartner.execution.plan import DecisionState, Remainder, State
from tradepartner.store.journal import DecisionRow

__all__ = ["PHASES", "Attempt", "AttemptScope", "WrittenOff", "attempt_scope", "write_offs"]

PHASES = ("sell", "buy")
_SELL, _BUY = PHASES
_IN_PLAY = frozenset({State.OPEN, State.IN_FLIGHT})


@dataclass(frozen=True)
class Attempt:
    """One decision to order in this phase, for its remainder."""

    decision: DecisionRow
    remainder: Remainder


@dataclass(frozen=True)
class AttemptScope:
    """What a phase may order (`attempts`, by decision id), the decisions of its
    side left to collection (`in_flight`, by decision id), and, for a buys
    phase, whether it is the rebalance's last (no sell of its rebalance open or
    in flight; always false for a sells phase)."""

    attempts: tuple[Attempt, ...]
    in_flight: tuple[int, ...]
    last: bool


@dataclass(frozen=True)
class WrittenOff:
    """A buy's `written_off` decision event: its unfunded remainder."""

    decision_id: int
    unfunded_notional: float


def _id(decision: DecisionRow) -> int:
    if decision.decision_id is None:
        raise ValueError("a decisions row without a decision_id: pass rows read from the journal")
    return decision.decision_id


def _state(decision: DecisionRow, states: Mapping[int, DecisionState]) -> DecisionState:
    decision_id = _id(decision)
    if decision_id not in states:
        raise ValueError(f"no derived state for decision {decision_id}")
    return states[decision_id]


def _check_phase(phase: str) -> None:
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")


def _last(decisions: Sequence[DecisionRow], states: Mapping[int, DecisionState]) -> bool:
    """True when no sell of the in-play buys' rebalance is open or in flight."""
    rebalances: set[date | None] = {
        d.rebalance_session
        for d in decisions
        if d.side == _BUY and _state(d, states).state in _IN_PLAY
    }
    if len(rebalances) > 1:
        shown = sorted(str(r) for r in rebalances)
        raise ValueError(f"buys of more than one rebalance in one phase: {shown}")
    return not any(
        d.side == _SELL
        and d.rebalance_session is not None
        and d.rebalance_session in rebalances
        and _state(d, states).state in _IN_PLAY
        for d in decisions
    )


def attempt_scope(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    *,
    phase: str,
) -> AttemptScope:
    """The phase's attempts, its side's in-flight decisions and whether a buys
    phase is the last (module docstring).

    `decisions` are the pending rebalance's decisions, both sides, plus the
    open forced exits; `states` maps every one's id to its `plan.decision_state`
    on S; `phase` is `"sell"` or `"buy"`. Attempts and in-flight ids are in
    decision-id order. Raises `ValueError` for an unknown phase, a decision
    without an id or a state, an open decision without a remainder, and a buys
    phase with buys of two rebalances in play.
    """
    _check_phase(phase)
    attempts: list[Attempt] = []
    in_flight: list[int] = []
    for decision in sorted(decisions, key=_id):
        if decision.side != phase:
            continue
        state = _state(decision, states)
        if state.state == State.IN_FLIGHT:
            in_flight.append(_id(decision))
        elif state.state == State.OPEN:
            if state.remainder is None:
                raise ValueError(f"open decision {_id(decision)} has no remainder")
            attempts.append(Attempt(decision, state.remainder))
    last = phase == _BUY and _last(decisions, states)
    return AttemptScope(tuple(attempts), tuple(in_flight), last)


def write_offs(
    decisions: Sequence[DecisionRow],
    states: Mapping[int, DecisionState],
    *,
    phase: str,
    last: bool,
    halted: bool,
) -> list[WrittenOff]:
    """The `written_off` rows at the end of a phase, in decision-id order
    (module docstring).

    `states` are the decisions' `plan.decision_state` after the phase's
    collection; `last` is the phase's `AttemptScope.last`, decided when it
    began (a sell that ends during the buys phase does not make it last); and
    `halted` is true when the phase ended on the halt path. A sells phase
    writes nothing off. Raises `ValueError` for an unknown phase, a decision
    without an id or a state, and a buy to write off without a remainder.
    """
    _check_phase(phase)
    if phase != _BUY:
        return []
    written: list[WrittenOff] = []
    for decision in sorted(decisions, key=_id):
        if decision.side != _BUY:
            continue
        state = _state(decision, states)
        deferred = last and not halted and state.state == State.OPEN
        if not (state.written_off or deferred):
            continue
        if state.remainder is None:
            raise ValueError(f"buy decision {_id(decision)} has no remainder to write off")
        written.append(WrittenOff(_id(decision), state.remainder.notional))
    return written
