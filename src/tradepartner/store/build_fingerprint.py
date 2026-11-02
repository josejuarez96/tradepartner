"""Pure rule identity for an incremental EDGAR build (#2629).

The per-name exceptions are returned separately: T323 compares them with the
last successful run and touches their CIKs without invalidating every issuer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from tradepartner.adapters import edgar_source
from tradepartner.config import Settings
from tradepartner.store import schema

# Bump when a builder changes its derivation from unchanged inputs. Cache and
# schema versions below are read from their owners at call time, never copied.
BUILD_RULES_VERSION = 2

HASHED_SETTINGS_KEYS = (
    "benchmarks",
    "calendar.start",
    "calendar.end",
    "master.transfer_window_sessions",
    "master.reorganisation_window_sessions",
    "master.snapshot_relisting_lag_days",
    "master.issuer_forms",
    "master.static_columns",
    "master.chain_window_sessions",
    "edgar.index_first_year",
    "edgar.index_settle_days",
    "edgar.cover_page_forms",
    "edgar.fsn_first_year",
    "edgar.header_forms",
    "edgar.header_first_year",
    "edgar.statement_facts_enabled",
    "edgar.statement_tags",
    "edgar.statement_forms",
    "edgar.statement_units",
    "edgar.statement_fact_units",
    "edgar.filing_events_enabled",
    "edgar.event_forms",
    "edgar.title_fallback",
    "edgar.exchange_axis_listings",
    "edgar.cover_split_contexts",
    "edgar.class_member_link",
    "edgar.dead_class_filings",
    "edgar.lettered_member_max_minor_share",
    "edgar.filename_listings",
    "edgar.filename_listings_since",
    "edgar.filename_listing_forms",
    "edgar.filename_document_max_bytes",
    "events.sec_forms",
    "events.sec_forms_enabled",
    "events.sec_forms_backfill_start",
    "events.form4_xml_price_below",
    "events.form4_zero_price_xml_codes",
)

PER_NAME_KEYS = (
    "master.chains",
    "master.chains_dismissed",
    "master.keep_successors",
    "edgar.class_member_overrides",
    "edgar.filename_listings_refused",
)

# Explicit decisions for the pin test. Transport, fetch policy, failure
# reporting, and health thresholds do not change a builder's derived rows.
EXCLUDED_SETTINGS_KEYS = (
    "master.chain_max_price_ratio",
    "edgar.cache_dir",
    "edgar.requests_per_second",
    "edgar.retry_backoff_seconds",
    "edgar.request_timeout_seconds",
    "edgar.header_bytes",
    "edgar.max_retry_after_seconds",
    "edgar.retry_max_attempts",
    "edgar.retry_backoff_cap_seconds",
    "edgar.rate_limit_wait_seconds",
    "edgar.bulk_stamp_threshold_ciks",
    "edgar.bulk_stamps_enabled",
    "edgar.stamp_sgml_check",
    "edgar.stamp_sgml_fallback_runs",
    "edgar.stamp_header_bytes",
    "edgar.max_filing_failures",
    "edgar.min_failed_filings",
    "edgar.max_failed_filing_share",
    "edgar.max_validation_listed",
    "edgar.incremental_index",  # T323 adds the gate's on/off switch.
    "edgar.reconcile_recent_quarters",  # T319b: which quarters to re-read, not a build rule.
    "edgar.verify_rotation_days",  # Health sampling window, not a derivation rule.
)

CACHE_VERSION_NAMES = (
    "PARSER_VERSION",
    "COVER_VERSION",
    "HEADER_VERSION",
    "DELISTING_VERSION",
    "FSN_VERSION",
    "STAMPS_LAYOUT",
    # Not in the amendment's list of six, but a bump re-parses cached entries, so
    # leaving them out would rebuild only touched CIKs (quant-auditor, #2641).
    "STATEMENT_VERSION",
    "FILENAME_VERSION",
)


@dataclass(frozen=True)
class BuildFingerprint:
    """The global hash and independently diffable per-name settings."""

    hash: str
    per_name: dict[str, Any]


def _setting(settings: Settings, key: str) -> Any:
    value: Any = settings
    for part in key.split("."):
        value = getattr(value, part)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


# Keys that enter the hashed payload only when non-empty, so adding the key
# leaves the hash at defaults byte-identical (#2826, as the statement cache key).
_HASHED_WHEN_NON_EMPTY = frozenset({"edgar.statement_fact_units"})


def build_fingerprint(settings: Settings) -> BuildFingerprint:
    """Hash global build rules and return per-name lists outside the hash."""
    hashed = {
        key: _setting(settings, key)
        for key in HASHED_SETTINGS_KEYS
        if key not in _HASHED_WHEN_NON_EMPTY or _setting(settings, key)
    }
    payload = {
        "build_rules": BUILD_RULES_VERSION,
        "cache_versions": {name: getattr(edgar_source, name) for name in CACHE_VERSION_NAMES},
        "schema_version": schema.CURRENT_SCHEMA_VERSION,
        "settings": hashed,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return BuildFingerprint(
        hash=hashlib.sha256(encoded).hexdigest(),
        per_name={key: _setting(settings, key) for key in PER_NAME_KEYS},
    )
