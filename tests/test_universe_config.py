"""Every `universe.*` key, overridden one at a time, changes membership as
expected (spec req 14 acceptance; plan T14).

Each case compares `universe_as_of` at `T_LATE` under a base `Settings` with
the same base plus one key changed, and pins the exact names that enter or
leave and, for those that leave, the rule and reason they now fail. Values
are chosen from the fixture universe so an override separates members
rather than emptying the universe (e.g. `min_price=20` sits between
SEC_SPLIT_BETWEEN's 17.70 close and SEC_DUAL_B's 27.08).

Overrides go through both routes a caller has: an explicit `Settings`
(the per-trial route, #102) and `UNIVERSE__*` environment variables read by
`get_settings()` when `settings` is omitted. `exclude_sic_ranges` is
guarded (ADR 0006): every override is refused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from tradepartner.config import Settings, UniverseConfig, get_settings
from tradepartner.store.db import insert_row
from tradepartner.universe import Universe, universe_as_of

T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)

#: SEC_SPLIT_BETWEEN's 5M (2018-10-29) is 15M after its 3-for-1 split; a fact
#: of 15.3M as of 2019-01-31 is x1.02 over it. With a 200-day age limit the
#: new fact is fresh at T_LATE (148 days) and the old one stale (242).
SHARES_MOVE = (
    ("SEC_SPLIT_BETWEEN", date(2019, 1, 31), 15_300_000.0, datetime(2019, 2, 7, 21, 0, tzinfo=UTC)),
)

#: Default members at T_LATE: SEC_DUAL_A/B (one company, rank 1, NYSE),
#: SEC_SPLIT_BETWEEN (rank 2, NYSE).
DEFAULT_MEMBERS = {"SEC_DUAL_A", "SEC_DUAL_B", "SEC_SPLIT_BETWEEN"}

GUARDED_KEYS = {"exclude_sic_ranges"}


@dataclass(frozen=True)
class Case:
    """One key overridden on top of `base`: who enters, who leaves and why."""

    key: str
    value: Any
    admitted: set[str] = field(default_factory=set)
    removed: dict[str, tuple[int, str]] = field(default_factory=dict)
    base: dict[str, Any] = field(default_factory=dict)
    #: Extra shares facts `(security_id, as_of_date, value, known_at)` the
    #: store-backed test inserts first; the fixture has one fact per name.
    facts: tuple[tuple[str, date, float, datetime], ...] = ()

    @property
    def id(self) -> str:
        return f"{self.key}={self.value}"


CASES = [
    Case(
        "security_types",
        ["preferred"],
        removed={sid: (1, "common") for sid in DEFAULT_MEMBERS},
    ),
    Case(
        "exchanges",
        ["NASDAQ"],
        removed={sid: (2, "exchange:NYSE") for sid in DEFAULT_MEMBERS},
    ),
    # 17.70 < 20 <= 27.08, 56.66.
    Case("min_price", 20, removed={"SEC_SPLIT_BETWEEN": (4, "min_price")}),
    # SEC_DUAL_A's last-session dollar volume (~6.1M) is below 20M, its
    # 20-session median (~55M) above; the other members clear 20M either way.
    Case(
        "liquidity_window",
        1,
        base={"min_median_dollar_volume": 20_000_000},
        removed={"SEC_DUAL_A": (5, "min_median_dollar_volume")},
    ),
    Case(
        "liquidity_rule_enabled",
        False,
        base={"min_median_dollar_volume": 20_000_000, "liquidity_window": 1},
        admitted={"SEC_DUAL_A"},
    ),
    # SEC_SPLIT_BETWEEN's 20-session median (~21.8M) is under 25M.
    Case(
        "min_median_dollar_volume",
        25_000_000,
        removed={"SEC_SPLIT_BETWEEN": (5, "min_median_dollar_volume")},
    ),
    # Both have bars only from 2019-02 (under 12 months at T_LATE).
    Case("min_history_months", 3, admitted={"SEC_FACTS_RESTATED", "SEC_FACTS_STALE"}),
    # Shares facts from 2018-04/05, over 400 days old at T_LATE.
    Case("max_shares_age_days", 10_000, admitted={"SEC_SPLIT_FUTURE", "SEC_TRANSFER"}),
    Case(
        "max_shares_age_days",
        1,
        removed={sid: (7, "stale_shares") for sid in DEFAULT_MEMBERS},
    ),
    # The dual-class company ranks first; both its classes stay.
    Case("top_n_by_cap", 1, removed={"SEC_SPLIT_BETWEEN": (8, "top_n_by_cap")}),
    # Price-quality gate (#787). SEC_SPLIT_BETWEEN's largest one-day rise in the
    # window is x1.01506 (2019-04-05); the dual classes' stay under x1.015.
    Case("max_jump_ratio", 1.015, removed={"SEC_SPLIT_BETWEEN": (6, "price_jump")}),
    # SEC_DUAL_A falls x0.98501 on 2018-12-27, SEC_SPLIT_BETWEEN x0.98501 on
    # 2019-01-30 (its 3:1 split day is explained); SEC_DUAL_B stays above.
    Case(
        "min_jump_ratio",
        0.9851,
        removed={sid: (6, "price_jump") for sid in ("SEC_DUAL_A", "SEC_SPLIT_BETWEEN")},
    ),
    # Shares plausibility (#845): x1.02 is out of line at 1.01, so the fallback
    # to the stale 2018 fact fails rule 7.
    Case(
        "max_shares_ratio",
        1.01,
        base={"max_shares_age_days": 200},
        facts=SHARES_MOVE,
        removed={"SEC_SPLIT_BETWEEN": (7, "stale_shares")},
    ),
    Case(
        "accepted_shares_facts",
        ["SEC_SPLIT_BETWEEN@2019-01-31"],
        base={"max_shares_age_days": 200, "max_shares_ratio": 1.01},
        facts=SHARES_MOVE,
        admitted={"SEC_SPLIT_BETWEEN"},
    ),
    Case(
        "accepted_price_jumps",
        ["SEC_SPLIT_BETWEEN@2019-04-05"],
        base={"max_jump_ratio": 1.015},
        admitted={"SEC_SPLIT_BETWEEN"},
    ),
]


def _settings(**universe: Any) -> Settings:
    return Settings(_env_file=None, universe=universe)


def _members(u: Universe) -> set[str]:
    return set(u.members["security_id"].to_list())


def _excluded(u: Universe) -> dict[str, tuple[int, str]]:
    return {r["security_id"]: (r["rule"], r["reason"]) for r in u.exclusions.iter_rows(named=True)}


def _assert_case(before: Universe, after: Universe, case: Case) -> None:
    assert case.admitted or case.removed, "a case must expect a change"
    assert _members(after) - _members(before) == case.admitted
    removed = _members(before) - _members(after)
    assert {sid: _excluded(after)[sid] for sid in removed} == case.removed


@pytest.fixture
def _no_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No `.env` and no stray `UNIVERSE__*` variable reach `get_settings()`."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "absent.env"))
    for key in UniverseConfig.model_fields:
        monkeypatch.delenv(f"UNIVERSE__{key.upper()}", raising=False)


#: `universe` keys only `health` reads, which never change membership:
#: `accepted_same_day_pairs` (#855) tolerates a listing pair in the
#: `non_overlapping_listings` rule (tests/test_health.py).
HEALTH_ONLY_KEYS = {"accepted_same_day_pairs"}


def test_every_universe_key_has_an_override_case() -> None:
    covered = {case.key for case in CASES} | GUARDED_KEYS | HEALTH_ONLY_KEYS
    assert covered == set(UniverseConfig.model_fields)


def test_default_members_at_t_late(fixture_store: duckdb.DuckDBPyConnection) -> None:
    assert _members(universe_as_of(fixture_store, T_LATE, _settings())) == DEFAULT_MEMBERS


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_override_via_settings_changes_membership_as_expected(
    fixture_store: duckdb.DuckDBPyConnection, case: Case
) -> None:
    for security_id, as_of, value, known in case.facts:
        insert_row(
            fixture_store,
            "facts",
            {
                "security_id": security_id,
                "fact_name": "shares_outstanding",
                "as_of_date": as_of,
                "class_member": "",
                "value": value,
                "filing_accession": f"case-{security_id}-{as_of.isoformat()}",
                "known_at": known,
                "ingested_at": known,
                "source": "edgar",
                "provenance": "filing",
            },
        )
    before = universe_as_of(fixture_store, T_LATE, _settings(**case.base))
    after = universe_as_of(fixture_store, T_LATE, _settings(**case.base, **{case.key: case.value}))
    assert after.settings["universe"][case.key] == case.value
    _assert_case(before, after, case)


@pytest.mark.usefixtures("_no_env")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_override_via_environment_is_the_loaded_default(
    monkeypatch: pytest.MonkeyPatch, case: Case
) -> None:
    for key, value in {**case.base, case.key: case.value}.items():
        monkeypatch.setenv(f"UNIVERSE__{key.upper()}", json.dumps(value))
    explicit = _settings(**case.base, **{case.key: case.value})
    assert get_settings().universe == explicit.universe


@pytest.mark.usefixtures("_no_env")
def test_universe_as_of_without_settings_reads_the_environment(
    fixture_store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = next(c for c in CASES if c.key == "top_n_by_cap")
    before = universe_as_of(fixture_store, T_LATE)
    monkeypatch.setenv("UNIVERSE__TOP_N_BY_CAP", json.dumps(case.value))
    after = universe_as_of(fixture_store, T_LATE)
    _assert_case(before, after, case)


@pytest.mark.parametrize("value", [[], [[4900, 4949]], [[4900, 4999], [6000, 6099]]])
def test_guarded_sic_ranges_refuse_every_override(value: list[list[int]]) -> None:
    with pytest.raises(ValidationError, match="guarded"):
        _settings(exclude_sic_ranges=value)


@pytest.mark.usefixtures("_no_env")
def test_guarded_sic_ranges_refuse_an_environment_carve_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIVERSE__EXCLUDE_SIC_RANGES", "[[4900, 4949]]")
    with pytest.raises(ValidationError, match="guarded"):
        get_settings()
