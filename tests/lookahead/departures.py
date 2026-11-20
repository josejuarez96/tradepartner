"""Reads that knowingly depart from truncation invariance (#3530)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The only allowed directions. A departure that reveals a later value is never allowed.
DIRECTIONS = ("hides_data", "corrects_history")

Direction = Literal["hides_data", "corrects_history"]


@dataclass(frozen=True)
class Departure:
    name: str
    condition: str
    direction: Direction
    citation: str


_CHAINED_PRICES = Departure(
    name="",
    condition=(
        "T before a successor_chains row's known_at, with the successor's own bars on the "
        "full store: the full store hides those bars (the successor is named by a chain row "
        "whatever its known_at, asof.py _pre_listing_at) while the truncated store has no row "
        "and shows them"
    ),
    direction="hides_data",
    citation=(
        "#821 (successor chains), #2103; asof.py _pre_listing_at; "
        "tests/lookahead/test_successor_chains.py "
        "test_the_chain_reads_are_truncation_invariant_from_the_known_at"
    ),
)

#: Reads known to depart on the chain fixture store, keyed by function name. Reads that depart
#: only on data the fixtures do not carry (chain moves applying to all T, #2103; the facts
#: newest-ingest rule, asof.py facts_as_of; delisting class resolution over later master rows,
#: delistings.py) stay documented where they are: an entry here must be provable on the fixture,
#: or `test_every_allowed_departure_still_departs` fails it as stale.
ALLOWED: dict[str, Departure] = {
    name: Departure(
        name=name,
        condition=_CHAINED_PRICES.condition,
        direction=_CHAINED_PRICES.direction,
        citation=_CHAINED_PRICES.citation,
    )
    for name in ("prices_as_of", "adjusted_prices_as_of")
}
