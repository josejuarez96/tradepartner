"""Pins for the incremental EDGAR rule fingerprint."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from tradepartner.adapters import edgar_source
from tradepartner.config import Settings
from tradepartner.config_edgar import EdgarConfig, MasterConfig
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
