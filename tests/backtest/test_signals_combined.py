"""Tests for `signals_combined.combined_rank` (hypothesis backlog B4; ADR 0014 point 6,
T130). Hand-built pairs of sub-signals; no store, no provider."""

from __future__ import annotations

import ast
from pathlib import Path

from tradepartner.backtest import signals_combined
from tradepartner.backtest.signals import MomentumSignal, ProfitabilitySignal
from tradepartner.backtest.signals_combined import CombinedSignal, combined_rank


def _profitability(
    scores: dict[str, float],
    excluded: dict[str, tuple[str, ...]] | None = None,
    derived: tuple[str, ...] = (),
) -> ProfitabilitySignal:
    """A `ProfitabilitySignal` with every reason present, as the real signal returns."""
    reasons = ("sector", "no_facts", "stale_facts", "malformed")
    given = excluded or {}
    return ProfitabilitySignal(
        scores=scores,
        excluded={reason: given.get(reason, ()) for reason in reasons},  # type: ignore[dict-item]
        derived=derived,
    )


def _momentum(scores: dict[str, float], excluded: tuple[str, ...] = ()) -> MomentumSignal:
    return MomentumSignal(scores=scores, excluded=excluded)


def test_scores_are_the_mean_of_the_two_signals_ranks() -> None:
    """Over the common names each sub-signal ranks best first (largest rank), and the
    combined score is the equal-weight mean of the two ranks."""
    mom = _momentum({"A": 0.3, "B": 0.2, "C": 0.1})  # ranks A=3, B=2, C=1
    prof = _profitability({"B": 0.9, "C": 0.2, "A": 0.1})  # ranks B=3, C=2, A=1
    signal = combined_rank(mom, prof)
    assert signal.scores == {"A": 2.0, "B": 2.5, "C": 1.5}


def test_only_the_names_both_signals_score_are_scored() -> None:
    mom = _momentum({"A": 0.3, "B": 0.2})
    prof = _profitability({"A": 0.1, "C": 0.4})
    signal = combined_rank(mom, prof)
    assert set(signal.scores) == {"A"}
    assert signal.scores["A"] == 1.0  # one common name: both ranks are 1


def test_ties_are_broken_by_security_id() -> None:
    """Equal scores in a sub-signal rank by `security_id` ascending, so the smaller id
    takes the larger rank. The combination is deterministic for a tied pair."""
    mom = _momentum({"A": 0.1, "B": 0.1})  # A=2, B=1
    prof = _profitability({"A": 0.1, "B": 0.1})  # A=2, B=1
    signal = combined_rank(mom, prof)
    assert signal.scores == {"A": 2.0, "B": 1.0}


def test_precedence_momentum_then_profitability_then_one_signal_only() -> None:
    mom = _momentum({"A": 0.3}, excluded=("B",))
    prof = _profitability(
        {"A": 0.1, "C": 0.4},
        {"sector": ("D",), "no_facts": (), "stale_facts": (), "malformed": ()},
    )
    signal = combined_rank(mom, prof)
    assert signal.scores == {"A": 1.0}
    assert signal.excluded["no_history"] == ("B",)
    assert signal.excluded["sector"] == ("D",)
    assert signal.excluded["one_signal_only"] == ("C",)
    assert signal.excluded["no_facts"] == ()
    assert signal.excluded["stale_facts"] == ()
    assert signal.excluded["malformed"] == ()


def test_momentum_precedence_wins_when_both_signals_exclude_a_name() -> None:
    mom = _momentum({}, excluded=("B",))
    prof = _profitability({}, {"no_facts": ("B",)})
    signal = combined_rank(mom, prof)
    assert signal.excluded["no_history"] == ("B",)
    assert signal.excluded["no_facts"] == ()


def test_every_profitability_reason_is_carried_in_its_own_bucket() -> None:
    mom = _momentum({}, excluded=())
    prof = _profitability(
        {},
        {
            "sector": ("S",),
            "no_facts": ("N",),
            "stale_facts": ("G",),
            "malformed": ("M",),
        },
    )
    signal = combined_rank(mom, prof)
    assert signal.excluded["sector"] == ("S",)
    assert signal.excluded["no_facts"] == ("N",)
    assert signal.excluded["stale_facts"] == ("G",)
    assert signal.excluded["malformed"] == ("M",)


def test_exclusions_are_pairwise_disjoint_and_scores_disjoint() -> None:
    mom = _momentum({"A": 0.3}, excluded=("B",))
    prof = _profitability(
        {"A": 0.1, "C": 0.4},
        {"sector": ("D",), "stale_facts": ("B",)},
    )
    signal = combined_rank(mom, prof)
    buckets = [set(ids) for ids in signal.excluded.values()] + [set(signal.scores)]
    for i, left in enumerate(buckets):
        for right in buckets[i + 1 :]:
            assert left.isdisjoint(right)
    # Every reason the combination declares is present, even when empty.
    assert set(signal.excluded) == {
        "no_history",
        "sector",
        "no_facts",
        "stale_facts",
        "malformed",
        "one_signal_only",
    }


def test_counts_are_both_signals_counts_plus_n_combined() -> None:
    mom = _momentum({"A": 0.3, "B": 0.2}, excluded=("C",))
    prof = _profitability(
        {"A": 0.1, "B": 0.4},
        {"sector": ("D",), "malformed": ("E",)},
    )
    signal = combined_rank(mom, prof)
    assert signal.counts == {
        "n_excluded_no_history": 1,
        "n_ranked": 2,
        "n_excluded_no_facts": 0,
        "n_excluded_stale_facts": 0,
        "n_excluded_sector": 1,
        "n_excluded_malformed": 1,
        "n_derived": 0,
        "n_combined": 2,
    }


def test_derived_names_are_counted_not_special() -> None:
    mom = _momentum({"A": 0.3})
    prof = _profitability({"A": 0.1}, derived=("A",))
    signal = combined_rank(mom, prof)
    assert signal.counts["n_derived"] == 1
    assert signal.scores == {"A": 1.0}


def test_module_is_pure() -> None:
    """The signal reads the two sub-signal results only: no provider and no polars."""
    tree = ast.parse(Path(signals_combined.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported & {"polars", "tradepartner.backtest.provider"} == set()
    assert isinstance(combined_rank(_momentum({}), _profitability({})), CombinedSignal)
