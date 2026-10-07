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

**The canonical frozen set** leaves out every table key whose value is its default (in
the same JSON form, so `True`, `1` and `1.0` differ), except the keys of the family's
own signal section, which are always kept; **the fingerprint** hashes the keys that
decide what a run computes through that set, so a table entry added later changes
neither.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from typing import Any, Final, Protocol

from tradepartner.config import FAMILIES, HypothesisFamily
from tradepartner.config import FAMILY_SIGNAL_SECTIONS as _CONFIG_FAMILY_SIGNAL_SECTIONS

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
    # The stale-listing rule (ADR 0003 amendment #1199): a `gap.*` key that measures the
    # store, not the strategy, enters at its real default, so H1 reads 63 too (owner,
    # 2026-10-07; strategy-lab spec Definitions, "Frozen-key defaults").
    ("gap.stale_listing_sessions", 63, 16),
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
#: fingerprint, never left out as default-valued (backtest spec decision 13). Re-exported
#: from `config.FAMILY_SIGNAL_SECTIONS` (ADR 0014 point 2, T128) so callers that already
#: import it from here keep working; `inert_sections` and `fingerprint` below use the
#: full `FAMILIES[family].sections` tuple instead, which is `(signal_section,)` for every
#: family today.
FAMILY_SIGNAL_SECTIONS: Final[dict[HypothesisFamily, str]] = dict(_CONFIG_FAMILY_SIGNAL_SECTIONS)

#: Single keys the fingerprint reads besides the sections below (spec "Fingerprint").
_FINGERPRINT_KEYS: Final = (
    "execution.fill_price",
    "costs.per_side_bps",
    "holdout.start",
    "holdout.end",
)
_FINGERPRINT_SECTIONS: Final = ("schedule", "universe")


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


def _own_sections(family: str) -> frozenset[str]:
    """The sections `family` lists in `FAMILIES` (ADR 0014 point 2): its own signal
    section first, then any other family's section its signal reads. Raises `KeyError`
    for an unlisted family instead of inheriting momentum's identity."""
    return frozenset(FAMILIES[family].sections)  # type: ignore[index]


def _all_sections() -> frozenset[str]:
    """Every section listed by any family in `FAMILIES`."""
    return frozenset(section for spec in FAMILIES.values() for section in spec.sections)


def inert_sections(family: str) -> frozenset[str]:
    """The sections `family` does not list: never stored, overlaid, hashed or
    fingerprinted for a `family` registration. Raises `KeyError` for an unlisted family
    (ADR 0014 point 2: the momentum fallback goes)."""
    return _all_sections() - _own_sections(family)


def canonical_frozen_set(params: Mapping[str, Any], family: str) -> dict[str, Any]:
    """`params` read through the defaults, with every table key at its default left out,
    except the keys of `family`'s own listed sections, which are always kept, and with
    the sections `family` does not list left out whole."""
    own = _own_sections(family)
    inert = _all_sections() - own
    defaults = _defaults()
    return {
        key: value
        for key, value in _overlay_defaults(params, family).items()
        if _section(key) not in inert
        and not (key in defaults and _section(key) not in own and is_default(value, defaults[key]))
    }


def is_default(value: Any, default: Any) -> bool:
    """`value` is the table `default` in the same JSON form the fingerprint hashes
    (#1022): Python's `True == 1 == 1.0` would call a different value the default.
    Every comparison of a frozen value with its table default goes through this.
    Raises `ValueError` on a NaN or infinite value, as the fingerprint does."""
    return _canonical_json(value) == _canonical_json(default)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(family: str, params: Mapping[str, Any], in_sample_start: date) -> str:
    """SHA-256 of the canonical JSON of the keys that decide what a run computes:
    `family`, every section it lists in full, `schedule.*`, `universe.*`,
    `execution.fill_price`, `costs.per_side_bps`, `in_sample_start` (record metadata,
    not a frozen param), `holdout.start` and `holdout.end`, through the canonical set."""
    canonical = canonical_frozen_set(params, family)
    sections = set(_FINGERPRINT_SECTIONS) | set(_own_sections(family))
    chosen = {
        key: value
        for key, value in canonical.items()
        if key in _FINGERPRINT_KEYS or _section(key) in sections
    }
    payload = {"family": family, "in_sample_start": in_sample_start.isoformat(), **chosen}
    return sha256(_canonical_json(payload).encode()).hexdigest()
