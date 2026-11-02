"""Pins for the incremental EDGAR rule fingerprint."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from tradepartner.adapters import edgar_source
from tradepartner.config import Settings
from tradepartner.config_edgar import PRE_2826_STATEMENT_FACTS, EdgarConfig, MasterConfig
from tradepartner.store import build_fingerprint as module
from tradepartner.store import schema


def _changed(settings: Settings, key: str) -> Settings:
    parts = key.split(".")
    parent: Any = settings if len(parts) == 1 else getattr(settings, parts[0])
    old = getattr(parent, parts[-1])
    if isinstance(old, bool):
        new = not old
    elif isinstance(old, list):
        new = [*old, "TEST"]
    elif isinstance(old, dict):
        new = {**old, "TEST": "TEST"}
    elif isinstance(old, date):
        new = old + timedelta(days=1)
    elif isinstance(old, str):
        new = old + "-test"
    elif old is None:
        new = 1994
    else:
        new = old + 1
    if len(parts) == 1:
        return settings.model_copy(update={key: new})
    return settings.model_copy(update={parts[0]: parent.model_copy(update={parts[1]: new})})


@pytest.mark.parametrize("key", module.HASHED_SETTINGS_KEYS)
def test_every_listed_setting_changes_hash(key: str) -> None:
    settings = Settings(_env_file=None)
    assert (
        module.build_fingerprint(_changed(settings, key)).hash
        != module.build_fingerprint(settings).hash
    )


# Pinned from main (before #2826 added `edgar.statement_fact_units`).
MAIN_DEFAULT_HASH = "753a81937c7bfaee20dcb1f7da7398bb128f4e3d9ef96e60434fc10b6699f342"


def test_hash_at_defaults_equals_the_hex_pinned_from_main() -> None:
    assert module.build_fingerprint(Settings(_env_file=None)).hash == MAIN_DEFAULT_HASH


def test_statement_fact_units_is_classified_and_hashed_only_when_non_empty() -> None:
    settings = Settings(_env_file=None)
    assert "edgar.statement_fact_units" in module.HASHED_SETTINGS_KEYS
    edgar = settings.edgar.model_copy(update={"statement_fact_units": {}})
    assert module.build_fingerprint(settings.model_copy(update={"edgar": edgar})).hash == (
        MAIN_DEFAULT_HASH
    )
    edgar = settings.edgar.model_copy(update={"statement_fact_units": {"revenue": ["EUR"]}})
    assert module.build_fingerprint(settings.model_copy(update={"edgar": edgar})).hash != (
        MAIN_DEFAULT_HASH
    )


@pytest.mark.parametrize("name", module.CACHE_VERSION_NAMES)
def test_each_cache_version_changes_hash(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    settings = Settings(_env_file=None)
    original = module.build_fingerprint(settings).hash
    monkeypatch.setattr(edgar_source, name, getattr(edgar_source, name) + 1)
    assert module.build_fingerprint(settings).hash != original


def test_build_rules_and_schema_versions_change_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)
    original = module.build_fingerprint(settings).hash
    monkeypatch.setattr(module, "BUILD_RULES_VERSION", module.BUILD_RULES_VERSION + 1)
    assert module.build_fingerprint(settings).hash != original
    monkeypatch.undo()
    monkeypatch.setattr(schema, "CURRENT_SCHEMA_VERSION", schema.CURRENT_SCHEMA_VERSION + 1)
    assert module.build_fingerprint(settings).hash != original


@pytest.mark.parametrize(
    "key",
    (
        "edgar.requests_per_second",
        "edgar.request_timeout_seconds",
        "edgar.retry_backoff_seconds",
        "edgar.cache_dir",
        "edgar.stamp_header_bytes",
        "edgar.incremental_index",
        "master.chains",
        "master.chains_dismissed",
        "master.keep_successors",
        "edgar.class_member_overrides",
        "edgar.filename_listings_refused",
    ),
)
def test_transport_new_and_per_name_keys_do_not_change_hash(key: str) -> None:
    settings = Settings(_env_file=None)
    if key == "edgar.incremental_index":
        changed = settings.model_copy(
            update={"edgar": settings.edgar.model_copy(update={"incremental_index": True})}
        )
    else:
        changed = _changed(settings, key)
    before = module.build_fingerprint(settings)
    after = module.build_fingerprint(changed)
    assert after.hash == before.hash
    if key in module.PER_NAME_KEYS:
        assert after.per_name[key] != before.per_name[key]


def test_every_master_and_edgar_key_is_classified() -> None:
    classified = (
        set(module.HASHED_SETTINGS_KEYS)
        | set(module.PER_NAME_KEYS)
        | set(module.EXCLUDED_SETTINGS_KEYS)
    )
    actual = {f"master.{key}" for key in MasterConfig.model_fields} | {
        f"edgar.{key}" for key in EdgarConfig.model_fields
    }
    assert actual <= classified
    assert "calendar.start" in classified
    assert "calendar.end" in classified
    assert "benchmarks" in classified
    assert "events.sec_forms" in classified
    assert classified - actual == {
        "benchmarks",
        "calendar.start",
        "calendar.end",
        "events.sec_forms",
        "events.sec_forms_enabled",
        "events.sec_forms_backfill_start",
        "events.form4_xml_price_below",
        "events.form4_zero_price_xml_codes",
    }


# --- additive_statement_change (T365a, #2826) ---------------------------------


def _with_edgar(settings: Settings, **update: Any) -> Settings:
    return settings.model_copy(update={"edgar": settings.edgar.model_copy(update=update)})


def _row(settings: Settings, *, payload: bool = True) -> dict[str, Any]:
    fingerprint = module.build_fingerprint(settings)
    per_name = dict(fingerprint.per_name)
    if not payload:
        per_name = {k: v for k, v in per_name.items() if k not in module.STORED_STATEMENT_KEYS}
    return {"hash": fingerprint.hash, "per_name": per_name}


def _pre_2826() -> Settings:
    base = Settings(_env_file=None)
    tags = {n: base.edgar.statement_tags[n] for n in PRE_2826_STATEMENT_FACTS}
    return _with_edgar(base, statement_tags=tags, statement_fact_units={})


def _added(settings: Settings) -> Settings:
    tags = {**settings.edgar.statement_tags, "net_income": ["us-gaap:NetIncomeLoss"]}
    units = {"eps_diluted": ["USD/shares"]}
    tags["eps_diluted"] = ["us-gaap:EarningsPerShareDiluted"]
    return _with_edgar(settings, statement_tags=tags, statement_fact_units=units)


def test_the_run_row_payload_holds_both_statement_keys() -> None:
    settings = Settings(_env_file=None)
    per_name = module.build_fingerprint(settings).per_name
    assert per_name["edgar.statement_tags"] == settings.edgar.statement_tags
    assert per_name["edgar.statement_fact_units"] == settings.edgar.statement_fact_units


def test_added_names_are_additive_against_a_row_with_the_payload() -> None:
    row = _row(_pre_2826())
    result = module.additive_statement_change(_added(_pre_2826()), row)
    assert result.ok
    assert result.added == ("net_income", "eps_diluted")


def test_a_row_without_the_payload_reads_against_pre_2826_names() -> None:
    row = _row(_pre_2826(), payload=False)
    result = module.additive_statement_change(_added(_pre_2826()), row)
    assert result.ok
    assert result.added == ("net_income", "eps_diluted")


def test_a_row_without_the_payload_refuses_when_the_base_is_not_the_eight() -> None:
    wider = _added(_pre_2826())
    assert not module.additive_statement_change(wider, _row(wider, payload=False)).ok


def test_a_chained_addition_uses_the_stored_names_as_base() -> None:
    first = _added(_pre_2826())
    tags = {**first.edgar.statement_tags, "cash": ["us-gaap:CashAndCashEquivalentsAtCarryingValue"]}
    second = _with_edgar(first, statement_tags=tags)
    result = module.additive_statement_change(second, _row(first))
    assert result.added == ("cash",)


def test_no_change_is_ok_with_nothing_added() -> None:
    settings = _pre_2826()
    result = module.additive_statement_change(settings, _row(settings))
    assert result.ok
    assert result.added == ()


def _refused(change: Settings, row: dict[str, Any]) -> bool:
    return not module.additive_statement_change(change, row).ok


def test_a_changed_list_unit_or_form_is_not_additive() -> None:
    base = _pre_2826()
    row = _row(base)
    tags = {**base.edgar.statement_tags, "revenue": ["us-gaap:Revenues", "us-gaap:Other"]}
    assert _refused(_with_edgar(base, statement_tags=tags), row)
    assert _refused(_with_edgar(base, statement_fact_units={"revenue": ["EUR"]}), row)
    assert _refused(_with_edgar(base, statement_forms=[*base.edgar.statement_forms, "8-K"]), row)
    assert _refused(_with_edgar(base, statement_units=["USD", "EUR"]), row)


def test_a_removed_base_name_is_not_additive() -> None:
    base = _pre_2826()
    tags = dict(base.edgar.statement_tags)
    del tags["revenue"]
    assert _refused(_with_edgar(base, statement_tags=tags), _row(base))


def test_a_changed_unit_on_a_base_name_alongside_an_addition_is_not_additive() -> None:
    base = _pre_2826()
    row = _row(base)
    changed = _added(base)
    changed = _with_edgar(changed, statement_fact_units={"revenue": ["EUR"]})
    assert _refused(changed, row)


def test_any_other_hashed_key_is_not_additive() -> None:
    base = _pre_2826()
    row = _row(base)
    for key in module.HASHED_SETTINGS_KEYS:
        if key in module.STORED_STATEMENT_KEYS:
            continue
        assert _refused(_added(_changed(base, key)), row), key


def test_statement_version_cache_versions_rules_and_schema_are_not_additive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _pre_2826()
    row = _row(base)
    change = _added(base)
    for name in module.CACHE_VERSION_NAMES:
        with monkeypatch.context() as patch:
            patch.setattr(edgar_source, name, getattr(edgar_source, name) + 1)
            assert _refused(change, row), name
    with monkeypatch.context() as patch:
        patch.setattr(module, "BUILD_RULES_VERSION", module.BUILD_RULES_VERSION + 1)
        assert _refused(change, row)
    with monkeypatch.context() as patch:
        patch.setattr(schema, "CURRENT_SCHEMA_VERSION", schema.CURRENT_SCHEMA_VERSION + 1)
        assert _refused(change, row)
