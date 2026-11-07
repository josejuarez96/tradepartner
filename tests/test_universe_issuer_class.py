"""Universe rule 1's issuer classes (#3076, #3053 item 6, decision c).

`universe.exclude_issuer_classes` (live default `["bdc", "foreign", "mlp"]`)
excludes a class whose latest classification carries one of those
`issuer_class` tags, whatever its `security_type`; `[]` (the frozen-key table
default) is the rule before #3076, so a registration stored without the key
runs as it always did.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import pydantic
import pytest

from tradepartner.backtest import frozen
from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import universe_as_of

T = datetime(2019, 1, 28, 21, 0, tzinfo=UTC)  # the fixture's T_SPLIT_BETWEEN probe
NAME = "SEC_SPLIT_BETWEEN"  # a member at T under the old rule
KEY = "universe.exclude_issuer_classes"


def _settings(**universe: Any) -> Settings:
    return Settings(_env_file=None, universe=universe)


def _tag(conn: duckdb.DuckDBPyConnection, tag: str | None, known: datetime) -> None:
    insert_row(
        conn,
        "classifications",
        {
            "security_id": NAME,
            "sic": 4922,
            "security_type": "common",
            "rule": "common_default",
            "issuer_class": tag,
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _excluded(conn: duckdb.DuckDBPyConnection, settings: Settings) -> dict[str, tuple[Any, ...]]:
    u = universe_as_of(conn, T, settings)
    return {
        r["security_id"]: (r["rule"], r["rule_name"], r["reason"])
        for r in u.exclusions.iter_rows(named=True)
    }


def _members(conn: duckdb.DuckDBPyConnection, settings: Settings) -> set[str]:
    return set(universe_as_of(conn, T, settings).members["security_id"].to_list())


@pytest.mark.parametrize("tag", ["mlp", "bdc", "foreign"])
def test_a_tagged_common_class_fails_rule_1_by_default(
    fixture_store: duckdb.DuckDBPyConnection, tag: str
) -> None:
    assert NAME in _members(fixture_store, _settings())
    _tag(fixture_store, tag, datetime(2018, 6, 1, 21, 0, tzinfo=UTC))
    assert _excluded(fixture_store, _settings())[NAME] == (1, "security_type", f"issuer:{tag}")


def test_an_empty_list_is_the_old_rule(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """The old behaviour stays reproducible: `[]` admits the tagged class."""
    _tag(fixture_store, "mlp", datetime(2018, 6, 1, 21, 0, tzinfo=UTC))
    assert NAME in _members(fixture_store, _settings(exclude_issuer_classes=[]))


def test_an_untagged_row_passes(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _tag(fixture_store, None, datetime(2018, 6, 1, 21, 0, tzinfo=UTC))
    assert NAME in _members(fixture_store, _settings())


def test_a_tag_known_after_t_does_not_apply_at_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """No look-ahead: the tag is read from the classification known at T."""
    _tag(fixture_store, "mlp", T + timedelta(seconds=1))
    assert NAME in _members(fixture_store, _settings())


def test_a_later_untagged_row_readmits_the_class(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A conversion (an LP's first filing as a corporation) clears the tag from then on."""
    _tag(fixture_store, "mlp", datetime(2018, 6, 1, 21, 0, tzinfo=UTC))
    insert_row(
        fixture_store,
        "classifications",
        {
            "security_id": NAME,
            "sic": 4922,
            "security_type": "common",
            "rule": "title_common",
            "issuer_class": None,
            "known_at": datetime(2018, 9, 4, 21, 0, tzinfo=UTC),
            "ingested_at": datetime(2018, 9, 4, 21, 0, tzinfo=UTC),
            "source": "edgar",
            "provenance": "filing",
        },
    )
    assert NAME in _members(fixture_store, _settings())


def test_the_setting_is_stored_sorted_and_refuses_an_unknown_class() -> None:
    assert Settings(_env_file=None).universe.exclude_issuer_classes == ["bdc", "foreign", "mlp"]
    assert _settings(
        exclude_issuer_classes=["mlp", "bdc", "mlp"]
    ).universe.exclude_issuer_classes == [
        "bdc",
        "mlp",
    ]
    with pytest.raises(pydantic.ValidationError):
        _settings(exclude_issuer_classes=["reit"])


def test_the_table_entry_is_last_and_reproduces_the_old_rule() -> None:
    assert frozen.is_default(frozen.FROZEN_KEY_DEFAULTS[-1:], ((KEY, [], 48),))
    assert KEY not in frozen.POST_REGISTRATION_OWN_KEYS  # universe is no family's own


def test_a_stored_set_without_the_key_keeps_its_fingerprint_and_runs_the_old_rule() -> None:
    """Every registration on the owner's store predates the key: `frozen_values`
    overlays `[]` and the fingerprint is the one stored. A new registration that
    leaves the key out freezes the live exclusion, a new fingerprint."""
    start = date(2017, 1, 31)
    params = {"universe.security_types": ["common"], "universe.min_price": 5.0}
    old = frozen.fingerprint("momentum", params, start)
    assert frozen.fingerprint("momentum", {**params, KEY: []}, start) == old
    assert frozen.canonical_frozen_set({**params, KEY: []}, "momentum") == (
        frozen.canonical_frozen_set(params, "momentum")
    )
    assert frozen._overlay_defaults(params, "momentum")[KEY] == []
    live = Settings(_env_file=None).universe.exclude_issuer_classes
    assert frozen.fingerprint("momentum", {**params, KEY: live}, start) != old
