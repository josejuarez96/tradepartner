"""Frozen-key defaults, `frozen_values` and the fingerprint (strategy-lab spec
Definitions "Frozen-key defaults", "Fingerprint"; backtest spec decision 13).

A leaf module: it imports `config` only and nothing under `store/`, so the store
modules can import it without a cycle (`backtest/hypothesis.py` imports
`store.registry`).

**The defaults table** `FROZEN_KEY_DEFAULTS` lists every frozen key added after the
lab's baseline (`LAB_BASELINE_FROZEN_KEYS`, the frozen set as it stood before the lab)
with the default that reproduces the old behaviour and the schema version current when
it landed. It is append-only and immutable: an edited default would move every pre-lab
run and every stored fingerprint. `frozen_values(record)` overlays those defaults on a
registration's stored `params` for every key the stored set lacks, at the stored hash,
so a registration is never re-registered when a key lands.

**The canonical frozen set** leaves out every table key whose value equals its default,
except the keys of the family's own signal section, which are always kept; **the
fingerprint** hashes the keys that decide what a run computes through that set, so a
table entry added later changes neither.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from typing import Any, Final, Protocol

from tradepartner.config import HypothesisFamily

#: (key, behaviour-preserving default in JSON form, schema version when it landed).
#: Append-only; every entry is pinned by value in `tests/backtest/test_frozen_defaults.py`.
FROZEN_KEY_DEFAULTS: Final[tuple[tuple[str, Any, int], ...]] = (
    ("schedule.rebalance_cadence", "month_end", 12),
    ("schedule.signal_anchor", "month_end", 12),
    # The `profitability` family (backtest spec amendment #720, T85), at B3's values.
    ("profitability.basis", "gross", 12),
    ("profitability.annual_period_days", [350, 380], 12),
    ("profitability.max_fact_age_days", 548, 12),
    ("profitability.exclude_sic_ranges", [[6000, 6999]], 12),
    ("profitability.include_derived", True, 12),
    ("profitability.top_fraction", 0.10, 12),
    ("profitability.weighting", "equal", 12),
)

#: The frozen key set before the lab (`frozen_keys()` on 2026-10-05, schema version 12).
#: Every frozen key outside it needs a `FROZEN_KEY_DEFAULTS` entry.
LAB_BASELINE_FROZEN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "strategy.formation_months",
        "strategy.skip_months",
        "strategy.top_fraction",
        "strategy.weighting",
        "strategy.signal_total_return",
        "universe.security_types",
        "universe.exchanges",
        "universe.exclude_sic_ranges",
        "universe.min_price",
        "universe.liquidity_rule_enabled",
        "universe.min_median_dollar_volume",
        "universe.liquidity_window",
        "universe.min_history_months",
        "universe.max_shares_age_days",
        "universe.max_shares_ratio",
        "universe.accepted_shares_facts",
        "universe.top_n_by_cap",
        "universe.max_jump_ratio",
        "universe.min_jump_ratio",
        "universe.accepted_price_jumps",
        "universe.accepted_same_day_pairs",
        "costs.per_side_bps",
        "costs.commission_per_share",
        "costs.commission_per_order",
        "costs.sensitivity_per_side_bps",
        "backtest.initial_capital",
        "backtest.cash_rate",
        "backtest.delisting_exit",
        "backtest.stale_exit_sessions",
        "adjust.max_prior_close_gap_sessions",
        "adjust.max_dividend_to_prior_close",
        "master.transfer_window_sessions",
        "master.reorganisation_window_sessions",
        "master.snapshot_relisting_lag_days",
        "master.issuer_forms",
        "master.static_columns",
        "master.keep_successors",
        "gap.missing_tail_sessions",
        "gap.count_share_threshold",
        "holdout.start",
        "holdout.end",
        "metrics.risk_free_rate",
        "metrics.red_flag_excess_cagr_pp",
        "execution.fill_price",
        "benchmarks",
        "alpaca.historical_feed",
    }
)

#: Each family's own signal section: kept whole in the canonical set and the
#: fingerprint, never left out as default-valued (backtest spec decision 13).
FAMILY_SIGNAL_SECTIONS: Final[dict[HypothesisFamily, str]] = {
    "momentum": "strategy",
    "profitability": "profitability",
}

#: Single keys the fingerprint reads besides the sections below (spec "Fingerprint").
_FINGERPRINT_KEYS: Final = (
    "execution.fill_price",
    "costs.per_side_bps",
    "holdout.start",
    "holdout.end",
)
_FINGERPRINT_SECTIONS: Final = ("strategy", "schedule", "universe")


class _HasParams(Protocol):
    @property
    def params(self) -> Mapping[str, Any]: ...

    @property
    def family(self) -> str: ...


def _defaults() -> dict[str, Any]:
    return {key: default for key, default, _version in FROZEN_KEY_DEFAULTS}


def frozen_values(record: _HasParams) -> dict[str, Any]:
    """A registration's stored `params` with each table default overlaid for every key
    the stored set lacks, outside the family's inert sections. The one accessor every
    reader of frozen values goes through."""
    return _overlay_defaults(record.params, record.family)


def _overlay_defaults(params: Mapping[str, Any], family: str) -> dict[str, Any]:
    inert = inert_sections(family)
    values = dict(params)
    for key, default in _defaults().items():
        if _section(key) not in inert:
            values.setdefault(key, default)
    return values


def _section(key: str) -> str:
    return key.partition(".")[0]


def _signal_section(family: str) -> str | None:
    return next((s for f, s in FAMILY_SIGNAL_SECTIONS.items() if f == family), None)


def inert_sections(family: str) -> frozenset[str]:
    """The other families' signal sections: never stored, overlaid, hashed or
    fingerprinted for a `family` registration. A family without a signal section of its
    own (`oracle`) reads momentum's."""
    own = _signal_section(family) or FAMILY_SIGNAL_SECTIONS["momentum"]
    return frozenset(FAMILY_SIGNAL_SECTIONS.values()) - {own}


def canonical_frozen_set(params: Mapping[str, Any], family: str) -> dict[str, Any]:
    """`params` read through the defaults, with every table key at its default left out,
    except the keys of `family`'s signal section, which are always kept, and with the
    other families' signal sections left out whole."""
    signal = _signal_section(family)
    inert = inert_sections(family)
    defaults = _defaults()
    return {
        key: value
        for key, value in _overlay_defaults(params, family).items()
        if _section(key) not in inert
        and not (key in defaults and value == defaults[key] and _section(key) != signal)
    }


def fingerprint(family: str, params: Mapping[str, Any], in_sample_start: date) -> str:
    """SHA-256 of the canonical JSON of the keys that decide what a run computes:
    `family`, its signal section in full, `strategy.*`, `schedule.*`, `universe.*`,
    `execution.fill_price`, `costs.per_side_bps`, `in_sample_start` (record metadata,
    not a frozen param), `holdout.start` and `holdout.end`, through the canonical set."""
    canonical = canonical_frozen_set(params, family)
    sections = set(_FINGERPRINT_SECTIONS)
    signal = _signal_section(family)
    if signal is not None:
        sections.add(signal)
    chosen = {
        key: value
        for key, value in canonical.items()
        if key in _FINGERPRINT_KEYS or _section(key) in sections
    }
    payload = {"family": family, "in_sample_start": in_sample_start.isoformat(), **chosen}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(text.encode()).hexdigest()
