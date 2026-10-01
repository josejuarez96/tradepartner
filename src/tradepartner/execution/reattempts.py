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
start. Forced exits carry no rebalance session and never enter the test:
their proceeds are cash until the next rebalance. `decisions` must therefore
hold the rebalance's sells as well as its buys, or a phase would look last when
it is not.

**One rebalance, each decision once.** Both functions take one pending
rebalance's decisions plus the open forced exits, and raise `ValueError` when a
decision id repeats (it would get two attempts, each with its own id) or when
the decisions other than forced exits carry more than one rebalance session or
none (an open sell of a `missed` rebalance would otherwise be ordered again).

**`write_offs`** is the end of a buys phase: one `WrittenOff` per buy whose
derived state is written off but has no `written_off` row yet (an order of the
phase that ended short, the row collection appends when it sees the order
first), and, when the phase was the last and **completed** (it sized its buys
and ran to its end), one per buy its sizing **deferred** (`deferred`, the ids
`phases.buy_orders` deferred; a deferred buy has no order of its own, so this
row is its only write-off), each with its remainder's notional as the unfunded
amount. Only a funding shortfall is written off: a phase that did not complete
(the halt path, a kill switch read before a submit, a clock or limit stop, any
exception) writes off only the derived ones, its unreached buys staying open
for the run after, and `decision_state` never writes off a buy whose order
carries a halt cancel or ended `cancelled` with reason `not_received`, so such
a buy stays open and the next in-window run retries it, sized from cash like
any other; if a completed last phase defers that retry, it is written off.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from tradepartner.execution.plan import FORCED_EXIT, DecisionState, Remainder, State
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


def _check_decisions(decisions: Sequence[DecisionRow]) -> None:
    """Each decision once, and one rebalance among those that are not forced exits."""
    seen: set[int] = set()
    for decision in decisions:
        decision_id = _id(decision)
        if decision_id in seen:
            raise ValueError(f"decision {decision_id} is passed twice")
        seen.add(decision_id)
    planned = [d for d in decisions if d.decision != FORCED_EXIT]
    if any(d.rebalance_session is None for d in planned):
        raise ValueError("a decision that is not a forced exit has no rebalance session")
    rebalances = sorted({str(d.rebalance_session) for d in planned})
    if len(rebalances) > 1:
        raise ValueError(f"decisions of more than one rebalance in one phase: {rebalances}")


def _last(decisions: Sequence[DecisionRow], states: Mapping[int, DecisionState]) -> bool:
    """True when no sell of the rebalance is open or in flight."""
    return not any(
        d.side == _SELL and d.decision != FORCED_EXIT and _state(d, states).state in _IN_PLAY
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
    without an id or a state, a repeated decision, decisions of more than one
    rebalance, and an open decision without a remainder.
    """
    _check_phase(phase)
    _check_decisions(decisions)
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
    completed: bool,
    deferred: Collection[int],
) -> list[WrittenOff]:
    """The `written_off` rows at the end of a phase, in decision-id order
    (module docstring).

    `decisions` are those `attempt_scope` took; `states` their
    `plan.decision_state` after the phase's collection; `last` the phase's
    `AttemptScope.last`, decided when it began (a sell that ends during the buys
    phase does not make it last); `completed` true only when the phase sized
    its buys and ran to its end; and `deferred` the ids of the buys its sizing
    deferred (empty when it sized none). A sells phase writes nothing off.
    Raises `ValueError` for an unknown phase, a decision without an id or a
    state, a repeated decision, decisions of more than one rebalance, a deferred
    id that is not an open buy among `decisions`, and a buy to write off without
    a remainder.
    """
    _check_phase(phase)
    _check_decisions(decisions)
    buys = {_id(d): d for d in decisions if d.side == _BUY}
    for decision_id in deferred:
        if decision_id not in buys or _state(buys[decision_id], states).state != State.OPEN:
            raise ValueError(f"deferred decision {decision_id} is not an open buy of the phase")
    if phase != _BUY:
        return []
    unfunded = set(deferred) if last and completed else set()
    written: list[WrittenOff] = []
    for decision_id in sorted(buys):
        state = _state(buys[decision_id], states)
        if not (state.written_off or decision_id in unfunded):
            continue
        if state.remainder is None:
            raise ValueError(f"buy decision {decision_id} has no remainder to write off")
        written.append(WrittenOff(decision_id, state.remainder.notional))
    return written
