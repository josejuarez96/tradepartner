"""The family registry drives `backtest/strategies.py` (ADR 0014 point 2, T128).

`signal_for`'s table has the same keys as `config.FAMILIES`; each `Strategy` record's
`exclusion_reasons`, `count_names` and `section` come from the family's spec. A new
family that lands as a `FAMILIES` entry therefore also needs a `strategies._FAMILY_IO`
entry — the registry is the single list to walk.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from backtest import fake_provider
from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine, strategies
from tradepartner.backtest.signals import formation_sessions
from tradepartner.calendar import session_close
from tradepartner.config import FAMILIES, FamilySpec, Settings


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


# --- the momentum dispatch of B10's turnover screen (#1358, T165c) -------------------

T_SESSION = date(2024, 6, 28)
SCREEN_IDS = ["A", "B", "C", "D", "E"]
#: Per name, its volume a session: C first, then A, B and D; E lacks one formation bar.
SCREEN_VOLUMES = {"A": 10.0, "B": 9.0, "C": 11.0, "D": 7.0, "E": 50.0}


def _screen_provider() -> FakeProvider:
    """June 2024 bars plus the May 31 anchor (none for C), and a shares fact per name."""
    known = session_close(date(2024, 5, 31))
    sessions = [date(2024, 5, 31), *formation_sessions(T_SESSION, "month_end")]
    rows = [
        (sid, s, 10.0 + i + (s == T_SESSION) * (5 - i), SCREEN_VOLUMES[sid], known)
        for i, sid in enumerate(SCREEN_IDS)
        for s in sessions
        if (sid, s) not in {("C", date(2024, 5, 31)), ("E", date(2024, 6, 12))}
    ]
    prices = pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "close": pl.Float64,
            "volume": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )
    shares = pl.DataFrame(
        [(sid, date(2024, 5, 31), 100.0, known) for sid in SCREEN_IDS],
        schema=fake_provider._SHARES_SCHEMA,
        orient="row",
    )
    return FakeProvider(
        prices=prices, members={T_SESSION: SCREEN_IDS}, benchmarks={}, shares_rows=shares
    )


def _screen_params(fraction: float) -> Settings:
    strategy = {
        "formation_months": 1,
        "skip_months": 0,
        "top_fraction": 0.5,
        "turnover_top_fraction": fraction,
    }
    return Settings(_env_file=None, strategy=strategy)


def test_at_the_default_fraction_the_momentum_plan_reads_and_reports_nothing_new() -> None:
    provider = _screen_provider()
    plan = engine.plan(provider, _screen_params(1.0), T_SESSION, "momentum")
    assert "turnover_inputs" not in {call.method for call in provider.calls}
    assert plan.counts == {"n_excluded_no_history": 1}
    assert plan.exclusions == {"no_history": ("C",), "no_turnover": ()}
    assert sorted(plan.scores) == ["A", "B", "D", "E"]


def test_below_one_the_plan_screens_before_the_rank_and_reports_the_counts() -> None:
    provider = _screen_provider()
    plan = engine.plan(provider, _screen_params(0.5), T_SESSION, "momentum")
    [read] = [call for call in provider.calls if call.method == "turnover_inputs"]
    assert (read.t, read.ids, read.sessions_from) == (
        session_close(T_SESSION),
        tuple(SCREEN_IDS),
        date(2024, 6, 3),
    )
    # E has no usable turnover; of the four usable, ceil(0.5 * 4) = 2 are kept (C, A);
    # C lacks its formation anchor, so it is `no_history`, never both.
    assert plan.counts == {
        "n_excluded_no_history": 1,
        "n_screened": 2,
        "n_excluded_no_turnover": 1,
    }
    assert plan.exclusions == {"no_history": ("C",), "no_turnover": ("B", "D", "E")}
    assert list(plan.scores) == ["A"]
    assert set(plan.targets) == {"A"}
