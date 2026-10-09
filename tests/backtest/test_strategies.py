"""The family registry drives `backtest/strategies.py` (ADR 0014 point 2, T128).

`signal_for`'s table has the same keys as `config.FAMILIES`; each `Strategy` record's
`exclusion_reasons`, `count_names` and `section` come from the family's spec. A new
family that lands as a `FAMILIES` entry therefore also needs a `strategies._FAMILY_IO`
entry — the registry is the single list to walk.
"""

from __future__ import annotations

import pytest

from tradepartner.backtest import strategies
from tradepartner.config import FAMILIES, FamilySpec


def test_signal_for_keys_equal_the_family_registry() -> None:
    assert set(strategies._SIGNALS) == set(FAMILIES)


def test_oracle_resolves_to_momentum_s_section() -> None:
    """`oracle` reads momentum's `strategy` section (as today), so its registry entry
    has `sections=("strategy",)` and the signal record's section is `strategy`."""
    assert strategies.signal_for("oracle").section == "strategy"
    assert FAMILIES["oracle"].sections == ("strategy",)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_strategy_record_reasons_counts_and_section_come_from_the_spec(family: str) -> None:
    spec: FamilySpec = FAMILIES[family]  # type: ignore[assignment]
    strategy = strategies.signal_for(family)  # type: ignore[arg-type]
    assert strategy.exclusion_reasons == spec.exclusion_reasons
    assert strategy.count_names == spec.count_names
    assert strategy.section == spec.sections[0]


def test_signal_for_raises_for_an_unknown_family() -> None:
    with pytest.raises(KeyError):
        strategies.signal_for("nosuch")  # type: ignore[arg-type]
