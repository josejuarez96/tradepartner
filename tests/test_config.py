"""Tests for tradepartner.config.

Every default asserted here mirrors the "Config keys" lists in
docs/specs/data-foundation.md and docs/specs/backtest.md (Phase 3, T30).
`store.path` and `edgar.cache_dir` are not given explicit defaults in the
spec; T1 picked conservative ones under the gitignored `data/` directory.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from tradepartner.config import (
    _DEFAULT_SWEEPABLE_KEYS,
    ALLOWED_AXIS_PREFIXES,
    ENGINE_FAMILIES,
    FAMILIES,
    FAMILY_PARENTS,
    FAMILY_SIGNAL_SECTIONS,
    FORBIDDEN_AXIS_PREFIXES,
    FROZEN_EXECUTION_KEYS,
    FROZEN_PAPER_KEYS,
    MAIN_BOOK_ID,
    PAPER_FAMILIES,
    AlpacaConfig,
    CombinedConfig,
    CostsConfig,
    EdgarConfig,
    ExecutionConfig,
    HypothesisFamily,
    PaperConfig,
    ProfitabilityConfig,
    RiskConfig,
    Settings,
    StrategyConfig,
    _default_env_file,
    _settings_has_key,
    clean_message,
    paper_key_variable_names,
    render_validation_errors,
    secret_values,
)
from tradepartner.store.schema import DEFAULT_BOOK_ID


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the real shell environment.

    Every variable `Settings` reads is cleared, found from its fields, so a new
    key or a nested one such as `STORE__PATH` is covered without a list edit (#360).
    """
    fields = {name.upper() for name in Settings.model_fields}
    nested = tuple(f"{name}__" for name in fields)
    for key in list(os.environ):
        upper = key.upper()
        if upper in fields or upper.startswith(nested) or upper == "TRADEPARTNER_ENV_FILE":
            monkeypatch.delenv(key)


def _settings() -> Settings:
    # Ignore any real .env on disk so defaults are what's actually asserted;
    # the "no .env present" acceptance criterion has its own dedicated test.
    return Settings(_env_file=None)


def test_settings_construct_with_no_env_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Import and load succeed with no `.env` present (acceptance criterion).

    The loader reads `.env` from the project root, not the CWD, so a checkout
    with a real `.env` needs the documented override to have none (#360).
    """
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.chdir(tmp_path)
    assert not _default_env_file().exists()
    settings = Settings()
    assert settings is not None
    assert settings.store.path == "data/tradepartner.duckdb"


def test_store_defaults() -> None:
    s = _settings()
    assert s.store.path == "data/tradepartner.duckdb"
    assert s.store.lock_retry_seconds == 60


def test_calendar_defaults() -> None:
    s = _settings()
    assert s.calendar.start == date(1990, 1, 1)
    assert s.calendar.end == date(2035, 12, 31)


def test_ingest_defaults() -> None:
    s = _settings()
    assert s.ingest.settle_delay_minutes == 60
    assert s.ingest.reference_symbol == "SPY"
    assert s.ingest.max_missing_share == pytest.approx(0.05)
    assert s.ingest.max_dark_share == pytest.approx(1.0)  # #796: off until measured


@pytest.mark.parametrize("value", [-0.1, 1.5, float("nan")])
def test_max_dark_share_must_be_a_share(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ingest={"max_dark_share": value})


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("ingest", "max_missing_share"),
        ("universe", "min_price"),
        ("store", "lock_retry_initial_delay_seconds"),
        ("store", "lock_retry_max_delay_seconds"),
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_early_config_sections_refuse_non_finite_floats(
    section: str, field: str, value: float
) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(_env_file=None, **{section: {field: value}})


@pytest.mark.parametrize(
    "section",
    ["store", "ingest", "master", "universe", "adjust", "gap", "execution", "calendar"],
)
def test_early_config_sections_refuse_unknown_keys_without_echoing_values(section: str) -> None:
    with pytest.raises(ValidationError, match="mistyped_key") as excinfo:
        Settings(_env_file=None, **{section: {"mistyped_key": "MARKER-VALUE"}})
    assert "MARKER-VALUE" not in str(excinfo.value)


def test_edgar_defaults() -> None:
    s = _settings()
    # Anchored to the project root (T2 review round 2), not a bare relative
    # path: must be absolute and end with data/edgar_cache regardless of CWD.
    assert Path(s.edgar.cache_dir).is_absolute()
    assert Path(s.edgar.cache_dir) == Path(__file__).resolve().parents[1] / "data" / "edgar_cache"
    assert s.edgar.requests_per_second == pytest.approx(9.0)
    assert s.edgar.retry_backoff_seconds == pytest.approx(1.0)
    assert s.edgar.request_timeout_seconds == pytest.approx(30.0)
    assert s.edgar.header_bytes == 4096
    assert s.edgar.max_retry_after_seconds == pytest.approx(120.0)
    assert s.edgar.retry_max_attempts == 5
    assert s.edgar.retry_backoff_cap_seconds == pytest.approx(60.0)
    assert s.edgar.rate_limit_wait_seconds == pytest.approx(600.0)


def test_edgar_filing_source_defaults() -> None:
    """T11b's keys: index scan start, quarter settle days, bulk-stamp threshold
    and the periodic forms whose cover pages are read."""
    s = _settings()
    assert s.edgar.index_first_year == 1993
    assert s.edgar.index_settle_days == 3
    assert s.edgar.bulk_stamp_threshold_ciks == 500
    assert s.edgar.cover_page_forms == ["10-K", "10-Q", "20-F", "40-F"]


def test_edgar_fsn_first_year_default() -> None:
    """T11c (#216): one year before the 2016 price start."""
    assert _settings().edgar.fsn_first_year == 2015


def test_edgar_header_forms_defaults() -> None:
    """T11d's keys, added by T11c: registration forms, 8-K and the periodic
    forms; `header_first_year` follows `fsn_first_year` unless set."""
    s = _settings()
    assert s.edgar.header_forms == ["S-1", "F-1", "10-12B", "8-K", "10-K", "10-Q", "20-F", "40-F"]
    assert s.edgar.header_first_year is None
    assert s.edgar.header_start_year == 2015  # follows fsn_first_year
    s = Settings(_env_file=None, edgar={"fsn_first_year": 2019})
    assert s.edgar.header_start_year == 2019
    s = Settings(_env_file=None, edgar={"header_first_year": 2012})
    assert s.edgar.header_first_year == 2012
    assert s.edgar.header_start_year == 2012


@pytest.mark.parametrize("field", ["cover_page_forms", "header_forms"])
@pytest.mark.parametrize("value", [[], ["10-K", ""], [" ", "10-K"]])
def test_edgar_filing_form_lists_refuse_empty_or_blank_items(field: str, value: list[str]) -> None:
    with pytest.raises(ValidationError, match=field):
        Settings(_env_file=None, edgar={field: value})


def test_edgar_fsn_first_year_override() -> None:
    s = Settings(_env_file=None, edgar={"fsn_first_year": 2010})
    assert s.edgar.fsn_first_year == 2010


def test_edgar_statement_facts_defaults() -> None:
    """T77 (#660): the tag, form and unit lists are the spec amendment's
    defaults, in its precedence order; T78 (#1127) turned the switch on."""
    s = Settings(_env_file=None)
    assert s.edgar.statement_facts_enabled is True
    assert s.edgar.statement_tags == {
        "revenue": [
            "us-gaap:Revenues",
            "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap:SalesRevenueNet",
            "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
            "us-gaap:SalesRevenueGoodsNet",
            "us-gaap:SalesRevenueServicesNet",
        ],
        "cost_of_revenue": [
            "us-gaap:CostOfRevenue",
            "us-gaap:CostOfGoodsAndServicesSold",
            "us-gaap:CostOfGoodsSold",
            "us-gaap:CostOfServices",
        ],
        "gross_profit": ["us-gaap:GrossProfit"],
        "total_assets": ["us-gaap:Assets"],
        "operating_cash_flow": [
            "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
    }
    assert s.edgar.statement_forms == ["10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"]
    assert s.edgar.statement_units == ["USD"]


def test_edgar_statement_tags_default_is_not_shared() -> None:
    """Mutating one instance's default lists never leaks into the next."""
    first = Settings(_env_file=None)
    first.edgar.statement_tags["revenue"].append("us-gaap:Other")
    assert "us-gaap:Other" not in Settings(_env_file=None).edgar.statement_tags["revenue"]


@pytest.mark.parametrize(
    "tag", ["Revenues", "us-gaap:", ":Revenues", "us-gaap:Revenues:Net", "us gaap:Revenues"]
)
def test_edgar_statement_tag_must_be_taxonomy_qualified(tag: str) -> None:
    with pytest.raises(ValidationError, match="taxonomy:tag"):
        Settings(_env_file=None, edgar={"statement_tags": {"revenue": [tag]}})


def test_edgar_statement_keys_override() -> None:
    s = Settings(
        _env_file=None,
        edgar={
            "statement_facts_enabled": True,
            "statement_tags": {"revenue": ["ifrs-full:Revenue"]},
            "statement_forms": ["10-K"],
            "statement_units": ["USD", "EUR"],
        },
    )
    assert s.edgar.statement_facts_enabled is True
    assert s.edgar.statement_tags == {"revenue": ["ifrs-full:Revenue"]}
    assert s.edgar.statement_forms == ["10-K"]
    assert s.edgar.statement_units == ["USD", "EUR"]


@pytest.mark.parametrize(
    "edgar",
    [
        {"statement_tags": {}},
        {"statement_tags": {"revenue": []}},
        {"statement_forms": []},
        {"statement_units": []},
    ],
    ids=["tags-empty", "tag-list-empty", "forms-empty", "units-empty"],
)
def test_edgar_statement_lists_must_not_be_empty(edgar: dict[str, object]) -> None:
    """#1037: an empty list would silently read no statement facts at all."""
    with pytest.raises(ValidationError, match="at least 1"):
        Settings(_env_file=None, edgar=edgar)


def test_edgar_class_member_overrides_default_names_exactly_the_three_owner_cases() -> None:
    """#1169 (owner decision, option 3): DKS, TR and VMEO, nothing else."""
    assert Settings(_env_file=None).edgar.class_member_overrides == {
        "0001089063": "CommonClassA",
        "0000098677": "CommonClassA",
        "0001837686": "CommonClassA",
    }


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"1089063": "CommonClassA"}, "10-digit CIK"),
        ({"DKS": "CommonClassA"}, "10-digit CIK"),
        ({"0001089063": "ClassA"}, "CommonClass<A-Z>"),
        ({"0001089063": "us-gaap:CommonClassAMember"}, "CommonClass<A-Z>"),
        ({"0001089063": "CommonClassAB"}, "CommonClass<A-Z>"),
        ({"0001089063": "CommonClassa"}, "CommonClass<A-Z>"),
    ],
)
def test_edgar_class_member_overrides_refuse_malformed_entries(
    overrides: dict[str, str], match: str
) -> None:
    with pytest.raises(ValidationError, match=match):
        Settings(_env_file=None, edgar={"class_member_overrides": overrides})


def test_edgar_class_member_overrides_may_be_emptied() -> None:
    s = Settings(_env_file=None, edgar={"class_member_overrides": {}})
    assert s.edgar.class_member_overrides == {}


def test_edgar_unknown_key_is_refused() -> None:
    """#1037: a mistyped nested key fails instead of being silently ignored."""
    with pytest.raises(ValidationError, match="statement_unit"):
        Settings(_env_file=None, edgar={"statement_unit": ["USD"]})


def test_edgar_unknown_env_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGAR__REQUESTS_PER_SECONDS", "5")
    with pytest.raises(ValidationError, match="requests_per_seconds"):
        Settings(_env_file=None)


def test_edgar_unknown_dotenv_key_is_refused_without_echoing_its_value(
    tmp_path: Path,
) -> None:
    """#1037: a secret under a mistyped `EDGAR__` name in `.env` is refused by name,
    and its value never reaches the error text."""
    env_file = tmp_path / ".env"
    env_file.write_text("EDGAR__USER_AGENT=Owner owner-secret@example.com\n")
    with pytest.raises(ValidationError, match="user_agent") as excinfo:
        Settings(_env_file=env_file)
    assert "owner-secret" not in str(excinfo.value)


@pytest.mark.parametrize("field", ["REQUESTS_PER_SECOND", "RETRY_BACKOFF_CAP_SECONDS"])
@pytest.mark.parametrize("value", ["inf", "-inf", "nan"])
def test_edgar_refuses_non_finite_floats(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    """#1093: `inf` passed `gt=0`, so `requests_per_second=inf` meant no SEC throttle."""
    monkeypatch.setenv(f"EDGAR__{field}", value)
    with pytest.raises(ValidationError, match=field.lower()):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (EdgarConfig, {"user_agent": "MARKER-VALUE"}),
        (CostsConfig, {"per_side_bps": "MARKER-VALUE"}),
        (RiskConfig, {"max_orders_per_run": "MARKER-VALUE"}),
        (AlpacaConfig, {"symbols_per_request": "MARKER-VALUE"}),
    ],
    ids=["edgar-extra", "costs", "risk", "alpaca"],
)
def test_section_validated_on_its_own_hides_input_values(
    model: type[BaseModel], data: dict[str, object]
) -> None:
    """#1093: a section validated outside `Settings` (`CostsConfig` and `RiskConfig`
    in `execution/`) names the field and rule, never the value."""
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(data)
    assert "MARKER-VALUE" not in str(excinfo.value)


def _config_models() -> list[type[BaseModel]]:
    import tradepartner.config as config_module

    return [
        obj
        for obj in vars(config_module).values()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel
    ]


def test_no_config_validator_reads_a_secret_field() -> None:
    """#1093: custom validators format their value into their own message, which
    `hide_input_in_errors` does not hide; so none may validate a `SecretStr` field,
    and no model holding one has a `@model_validator` (its error carries the whole
    input). `Settings`' own checks run after construction and name env vars only.
    Not covered: a validator on a plain field reading a secret via `ValidationInfo.data`
    (reviewers read diffs for that)."""
    holders = {}
    for model in _config_models():
        secret_fields = {
            name for name, info in model.model_fields.items() if "SecretStr" in str(info.annotation)
        }
        decorators = model.__pydantic_decorators__
        for validator in decorators.field_validators.values():
            checked = set(validator.info.fields)
            reaches_secret = "*" in checked or secret_fields & checked
            assert not (secret_fields and reaches_secret), (model, validator)
        if secret_fields:
            holders[model] = secret_fields
            assert not decorators.model_validators, model
            for name in secret_fields:  # `Annotated[SecretStr, AfterValidator(...)]` too
                metadata = model.model_fields[name].metadata
                assert not [m for m in metadata if type(m).__name__.endswith("Validator")], name
    # Not vacuous: the secrets are found where they live.
    assert {"alpaca_api_secret", "sec_edgar_user_agent"} <= holders.get(Settings, set())


def test_render_validation_errors_shows_input_only_where_allowed() -> None:
    with pytest.raises(ValidationError) as excinfo:
        CostsConfig.model_validate({"per_side_bps": -1.0, "sensitivity_per_side_bps": [5.0, -2.0]})
    shown = render_validation_errors(excinfo.value, show_input=lambda _key: True)
    assert "per_side_bps = -1.0:" in shown
    assert "sensitivity_per_side_bps.1 = -2.0:" in shown
    hidden = render_validation_errors(excinfo.value, show_input=lambda _key: False)
    assert "-1.0" not in hidden and "-2.0" not in hidden


@pytest.mark.parametrize("value", ["10.5", "1e12"])
def test_edgar_requests_per_second_is_capped_at_secs_ceiling(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """#1108: a large finite rate would shrink `edgar_raw`'s interval toward 0."""
    monkeypatch.setenv("EDGAR__REQUESTS_PER_SECOND", value)
    with pytest.raises(ValidationError, match="requests_per_second"):
        Settings(_env_file=None)


def test_edgar_requests_per_second_accepts_secs_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGAR__REQUESTS_PER_SECOND", "10")
    assert Settings(_env_file=None).edgar.requests_per_second == 10.0


def test_edgar_failure_policy_defaults() -> None:
    """T11h's keys: quarantine after 3 consecutive counted days, and
    `check_failures()`'s count floor and share ceiling."""
    s = Settings(_env_file=None)
    assert s.edgar.max_filing_failures == 3
    assert s.edgar.min_failed_filings == 5
    assert s.edgar.max_failed_filing_share == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("index_first_year", 1992),
        ("index_settle_days", -1),
        ("bulk_stamp_threshold_ciks", 0),
        ("fsn_first_year", 2008),
        ("header_first_year", 1992),
        ("max_filing_failures", 0),
        ("min_failed_filings", 0),
        ("max_failed_filing_share", 0),
        ("max_failed_filing_share", 1.5),
    ],
)
def test_edgar_filing_source_keys_reject_nonsense(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, edgar={field: value})


def test_edgar_cache_dir_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert Path(_settings().edgar.cache_dir).is_absolute()


@pytest.mark.parametrize(
    "field",
    [
        "requests_per_second",
        "retry_backoff_seconds",
        "request_timeout_seconds",
        "header_bytes",
        "max_retry_after_seconds",
        "retry_max_attempts",
        "retry_backoff_cap_seconds",
        "rate_limit_wait_seconds",
    ],
)
def test_edgar_thresholds_reject_zero(field: str) -> None:
    """Every `edgar.*` throttle/timeout/backoff threshold is `gt=0`: zero or
    negative would either hang or hot-loop `adapters.edgar_raw`."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, edgar={field: 0})


def test_master_defaults() -> None:
    s = _settings()
    assert s.master.transfer_window_sessions == 5
    assert s.master.issuer_forms == [
        "10-K",
        "10-Q",
        "8-K",
        "20-F",
        "40-F",
        "S-1",
        "F-1",
        "10-12B",
        "25",
        "25-NSE",
    ]
    assert s.master.static_columns == ["name", "ticker", "exchange"]


def test_master_keep_successors_defaults_empty() -> None:
    """#922: no owner-accepted successor is kept unless the owner lists it."""
    assert _settings().master.keep_successors == []


def test_master_keep_successors_reads_the_owner_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `.env` line the PR body gives the owner (MTCH, #828)."""
    monkeypatch.setenv("MASTER__KEEP_SUCCESSORS", '["0000891103@2020-08-10"]')
    assert _settings().master.keep_successors == ["0000891103@2020-08-10"]


@pytest.mark.parametrize(
    "entry",
    [
        "0000891103",  # a primary id, not a successor
        "0000891103:class-b",  # a class id, not a successor
        "MTCH",
        "891103@2020-08-10",  # CIK not ten digits
        "0000891103@2020-13-01",  # no such day
        "0000891103@2020-8-10",
        "0000891103@2020-08-10-x",
        " 0000891103@2020-08-10",
        "",
    ],
)
def test_master_keep_successors_fails_closed_on_a_malformed_id(entry: str) -> None:
    """#922: a malformed id refuses the config, never keeps nothing silently."""
    with pytest.raises(ValidationError, match="keep_successors"):
        Settings(_env_file=None, master={"keep_successors": [entry]})


def test_master_keep_successors_accepts_a_numbered_successor() -> None:
    """`store.master._successor` numbers a second successor of one day `-2`."""
    s = Settings(_env_file=None, master={"keep_successors": ["0000891103@2020-08-10-2"]})
    assert s.master.keep_successors == ["0000891103@2020-08-10-2"]


def test_alpaca_accepted_relistings_defaults_empty() -> None:
    """#943: every span stays under the #847 stopped-line cut unless the owner lists it."""
    assert _settings().alpaca.accepted_relistings == []


def test_alpaca_accepted_relistings_reads_the_owner_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """The `.env` line the PR body gives the owner (MiMedx, #943)."""
    monkeypatch.setenv("ALPACA__ACCEPTED_RELISTINGS", '["0001376339"]')
    assert _settings().alpaca.accepted_relistings == ["0001376339"]


@pytest.mark.parametrize(
    "entry",
    ["0001376339", "0000314808:common-shares", "0000891103@2020-08-10", "0000891103@2020-08-10-2"],
)
def test_alpaca_accepted_relistings_takes_every_security_id_shape(entry: str) -> None:
    """A primary, a class and a successor id (#820) are all security ids."""
    s = Settings(_env_file=None, alpaca={"accepted_relistings": [entry]})
    assert s.alpaca.accepted_relistings == [entry]


@pytest.mark.parametrize(
    "entry",
    [
        "MDXG",  # a ticker, not a security id
        "1376339",  # CIK not ten digits
        " 0001376339",
        "0001376339 ",
        "0001376339:",
        "0001376339:Common Stock",
        "0001376339@2020-13-01",  # no such day
        "0001376339@2020-11-4",
        "BENCH:SPY",
        "",
    ],
)
def test_alpaca_accepted_relistings_fails_closed_on_a_malformed_id(entry: str) -> None:
    """#943: a malformed id refuses the config, never keeps nothing silently."""
    with pytest.raises(ValidationError, match="accepted_relistings"):
        Settings(_env_file=None, alpaca={"accepted_relistings": [entry]})


def test_alpaca_first_span_lead_defaults_on() -> None:
    """#974 (owner decision on #979): on by default; `false` is the kill switch."""
    assert _settings().alpaca.first_span_lead is True
    assert (
        Settings(_env_file=None, alpaca={"first_span_lead": False}).alpaca.first_span_lead is False
    )


def test_alpaca_first_span_lead_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA__FIRST_SPAN_LEAD", "false")
    assert _settings().alpaca.first_span_lead is False


def test_benchmarks_default() -> None:
    assert _settings().benchmarks == ["SPY", "MTUM"]


def test_execution_fill_price_default_close() -> None:
    assert _settings().execution.fill_price == "close"


def test_execution_fill_price_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, execution={"fill_price": "colse"})


def test_universe_defaults() -> None:
    u = _settings().universe
    assert u.security_types == ["common"]
    assert u.exchanges == ["NYSE", "NASDAQ", "NYSE_AMERICAN"]
    assert u.min_price == 5
    assert u.liquidity_rule_enabled is True
    assert u.min_median_dollar_volume == 5_000_000
    assert u.liquidity_window == 20
    assert u.min_history_months == 12
    assert u.max_shares_age_days == 400
    assert u.top_n_by_cap == 1000


def test_guarded_sic_exclusion_pinned_to_charter_range() -> None:
    """Guarded per ADR 0006: full SIC 4900-4999 division, no carve-outs.

    Changing this default is a charter amendment, not a routine code change.
    """
    assert _settings().universe.exclude_sic_ranges == ((4900, 4999),)


def test_guarded_sic_exclusion_rejects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A env override can't silently loosen the guarded SIC exclusion."""
    monkeypatch.setenv("UNIVERSE__EXCLUDE_SIC_RANGES", "[]")
    with pytest.raises(ValidationError, match="guarded setting; change only by charter amendment"):
        Settings(_env_file=None)


def test_liquidity_rule_enabled_since_t3() -> None:
    """T3 (#84) turned it on: SIP history makes dollar volume a real measure."""
    assert _settings().universe.liquidity_rule_enabled is True


def test_alpaca_historical_feed_defaults_to_sip_and_rejects_others() -> None:
    assert _settings().alpaca.historical_feed == "sip"
    assert (
        Settings(_env_file=None, alpaca={"historical_feed": "iex"}).alpaca.historical_feed == "iex"
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpaca={"historical_feed": "otc"})


def test_gap_defaults() -> None:
    s = _settings()
    assert s.gap.missing_tail_sessions == 5
    assert s.gap.count_share_threshold == pytest.approx(0.05)
    assert s.gap.stale_listing_sessions == 63


def test_adjust_defaults() -> None:
    assert _settings().adjust.max_prior_close_gap_sessions == 5


@pytest.mark.parametrize("value", [0, -1])
def test_adjust_max_prior_close_gap_sessions_rejects_non_positive(value: int) -> None:
    """Zero sessions would reject every prior close, even the session right
    before the ex-date, silently dropping every dividend (#72)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, adjust={"max_prior_close_gap_sessions": value})


def test_secrets_default_to_none() -> None:
    s = _settings()
    assert s.alpaca_api_key is None
    assert s.alpaca_api_secret is None
    assert s.sec_edgar_user_agent is None


def test_secrets_absent_from_repr_and_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "sk-live-abc123")
    monkeypatch.setenv("ALPACA_API_SECRET", "sk-live-secret456")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Jose Juarez jose@example.com")
    s = _settings()

    assert s.alpaca_api_key is not None
    assert s.alpaca_api_key.get_secret_value() == "sk-live-abc123"

    for blob in (repr(s), str(s)):
        assert "sk-live-abc123" not in blob
        assert "sk-live-secret456" not in blob
        assert "jose@example.com" not in blob


# --- .env resolution: anchored to the project root, not the CWD -----------


def test_env_file_anchored_to_project_root_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A decoy `.env` in an unrelated CWD (e.g. `$HOME` for a launchd job)
    must never be picked up.
    """
    decoy = tmp_path / ".env"
    decoy.write_text("ALPACA_API_KEY=decoy-should-not-load\n")
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.alpaca_api_key is None or (
        settings.alpaca_api_key.get_secret_value() != "decoy-should-not-load"
    )


def test_env_file_override_via_tradepartner_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_env = tmp_path / "custom.env"
    custom_env.write_text("ALPACA_API_KEY=custom-key-999\n")
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(custom_env))

    settings = Settings()

    assert settings.alpaca_api_key is not None
    assert settings.alpaca_api_key.get_secret_value() == "custom-key-999"


# --- Phase 3 keys (docs/specs/backtest.md "Config keys", T30) ---


def test_hypotheses_families_default() -> None:
    assert _settings().hypotheses.families == ["momentum", "oracle", "profitability", "combined"]


def test_hypotheses_family_outside_the_list_rejected() -> None:
    """Families are enumerated so N cannot be reset by renaming a family."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, hypotheses={"families": ["momentum", "value"]})


def test_strategy_defaults() -> None:
    s = _settings().strategy
    assert s.formation_months == 12
    assert s.skip_months == 1
    assert s.top_fraction == pytest.approx(0.10)
    assert s.weighting == "equal"
    assert s.signal_total_return is True


@pytest.mark.parametrize(
    "override",
    [
        {"weighting": "cap"},
        {"top_fraction": 0.0},
        {"top_fraction": 1.5},
        {"skip_months": -1},
        {"formation_months": 1, "skip_months": 1},
    ],
)
def test_strategy_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, strategy=override)


def test_costs_defaults() -> None:
    c = _settings().costs
    assert c.per_side_bps == pytest.approx(15.0)
    assert c.commission_per_share == pytest.approx(0.0)
    assert c.commission_per_order == pytest.approx(0.0)
    assert c.sensitivity_per_side_bps == [0.0, 30.0, 60.0, 100.0]


def test_costs_negative_sensitivity_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={"sensitivity_per_side_bps": [0, -5, 30]})


@pytest.mark.parametrize("field", ["per_side_bps", "commission_per_share", "commission_per_order"])
def test_costs_negative_component_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={field: -1})


def test_holdout_null_by_default() -> None:
    """No defaults: every hypothesis file names both dates (spec open question 2)."""
    h = _settings().holdout
    assert h.start is None
    assert h.end is None


def test_holdout_end_before_start_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            holdout={"start": date(2024, 1, 1), "end": date(2023, 12, 31)},
        )


def test_holdout_accepts_ordered_window() -> None:
    s = Settings(_env_file=None, holdout={"start": date(2024, 1, 1), "end": date(2026, 9, 30)})
    assert s.holdout.start == date(2024, 1, 1)
    assert s.holdout.end == date(2026, 9, 30)


def test_backtest_defaults() -> None:
    b = _settings().backtest
    assert b.initial_capital == pytest.approx(100_000.0)
    assert b.cash_rate == pytest.approx(0.0)
    assert b.delisting_exit == "last_close"
    assert b.stale_exit_sessions == 5


@pytest.mark.parametrize(
    "override",
    [
        {"initial_capital": 0},
        {"delisting_exit": "zero"},
        {"stale_exit_sessions": 0},
    ],
)
def test_backtest_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, backtest=override)


def test_metrics_defaults() -> None:
    m = _settings().metrics
    assert m.risk_free_rate == pytest.approx(0.0)
    assert m.red_flag_excess_cagr_pp == pytest.approx(3.0)


def test_metrics_has_no_periods_per_year() -> None:
    """`MONTHS_PER_YEAR = 12` is a derived constant (ADR 0006), not a key."""
    assert "periods_per_year" not in type(_settings().metrics).model_fields


def test_env_example_names_no_holdout_key() -> None:
    """`holdout.*` is never listed in `.env.example` (spec, Config keys)."""
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    assert "HOLDOUT__" not in env_example.read_text(encoding="utf-8").upper()


# --- Strategy lab: `schedule` and `lab` sections (docs/specs/strategy-lab.md, T93) ---


def test_env_example_names_schedule_and_lab_keys() -> None:
    """`.env.example` gains the `schedule.*` and `lab.*` lines (spec, Config keys)."""
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8").upper()
    assert "SCHEDULE__" in text
    assert "LAB__" in text


def test_schedule_defaults() -> None:
    s = _settings().schedule
    assert s.rebalance_cadence == "month_end"
    assert s.signal_anchor == "month_end"


@pytest.mark.parametrize(
    "override",
    [
        {"rebalance_cadence": "quarterly"},
        {"signal_anchor": "weekly"},
    ],
)
def test_schedule_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, schedule=override)


def test_lab_defaults() -> None:
    lab = _settings().lab
    assert lab.sweepable_keys == [
        "strategy.formation_months",
        "strategy.skip_months",
        "strategy.top_fraction",
        "strategy.weighting",
        "strategy.signal_total_return",
        "strategy.turnover_top_fraction",
        "schedule.rebalance_cadence",
        "schedule.signal_anchor",
    ]
    assert lab.max_variants_per_sweep == 100
    assert lab.max_promotions_per_sweep == 1
    assert lab.max_family_promotions == 2
    assert lab.promotion_min_dsr_excess == pytest.approx(0.5)
    assert lab.min_sharpe_variance_annual == pytest.approx(0.04)
    assert lab.max_failures_per_variant == 2
    assert lab.max_family_holdout_spends == 3
    assert lab.sweep_time_budget_minutes == pytest.approx(480.0)
    assert lab.sweep_detail_level == "summary"
    assert lab.axis_lattice == {"strategy.top_fraction": 0.01}
    assert lab.quiet_intervals == [("16:00", "21:00")]
    assert lab.quiet_weekdays == [0, 1, 2, 3, 4]
    assert lab.quiet_timezone == "America/New_York"
    assert lab.paper_run_lead_minutes == 30
    assert lab.registry_size_warn_gb == pytest.approx(20.0)
    assert lab.registry_size_refuse_gb == pytest.approx(50.0)
    assert lab.seconds_per_variant_default == {
        "month_end": 1.0,
        "week_end": 4.0,
        "daily": 20.0,
    }


def test_forbidden_axis_prefixes_pinned() -> None:
    """Pinned by value (strategy-lab spec req 1)."""
    assert FORBIDDEN_AXIS_PREFIXES == (
        "costs.",
        "universe.",
        "holdout.",
        "gap.",
        "adjust.",
        "master.",
        "metrics.",
        "backtest.",
        "benchmarks",
        "alpaca.",
        "execution.",
    )


def test_allowed_axis_prefixes_pinned() -> None:
    """Pinned by value (Amendment 2026-10-05 (#952, owner): allow-listed to
    `strategy.*` and `schedule.*`, closing #955's `risk.*`/`paper.*`/`alerts.*` gap)."""
    assert ALLOWED_AXIS_PREFIXES == ("strategy.", "schedule.")


@pytest.mark.parametrize("prefix", FORBIDDEN_AXIS_PREFIXES)
def test_sweepable_keys_entry_under_a_forbidden_prefix_rejected(prefix: str) -> None:
    key = prefix if prefix.endswith(".") else f"{prefix}."
    with pytest.raises(ValueError, match="forbidden prefix"):
        Settings(_env_file=None, lab={"sweepable_keys": [f"{key}bogus"], "axis_lattice": {}})


@pytest.mark.parametrize(
    "key",
    [
        "risk.max_drawdown",
        "paper.tracking_k",
        "alerts.delivery_timeout_seconds",
        "lab.max_variants_per_sweep",
        "store.path",
    ],
)
def test_sweepable_keys_entry_outside_the_allowed_prefixes_rejected(key: str) -> None:
    """#955: a deny-list alone let `risk.*`, `paper.*` and `alerts.*` through; the
    allow-list (Amendment 2026-10-05 (#952, owner)) refuses any section but
    `strategy.*`/`schedule.*`, whether or not `Settings` actually has the key."""
    with pytest.raises(ValueError, match="not under an allowed prefix"):
        Settings(_env_file=None, lab={"sweepable_keys": [key], "axis_lattice": {}})


def test_sweepable_keys_entry_settings_lacks_rejected() -> None:
    with pytest.raises(ValueError, match="names a key Settings lacks"):
        Settings(
            _env_file=None,
            lab={"sweepable_keys": ["strategy.not_a_real_key"], "axis_lattice": {}},
        )


def test_sweepable_keys_entry_unknown_field_in_an_allowed_section_rejected() -> None:
    """A key under an allowed prefix but naming no real field still fails the
    `Settings`-existence check, reached only once it clears `ALLOWED_AXIS_PREFIXES`."""
    with pytest.raises(ValueError, match="names a key Settings lacks"):
        Settings(
            _env_file=None,
            lab={"sweepable_keys": ["schedule.not_a_real_field"], "axis_lattice": {}},
        )


@pytest.mark.parametrize(
    "bare_key",
    ["universe", "costs", "holdout", "alpaca_api_key", "not_a_section"],
)
def test_sweepable_keys_entry_without_a_dot_rejected(bare_key: str) -> None:
    """A bare top-level name is refused, under `ALLOWED_AXIS_PREFIXES` (Amendment
    2026-10-05 (#952, owner)): `universe`/`costs`/`holdout` name a whole section and
    `alpaca_api_key` a secret scalar, and none is one `strategy.*`/`schedule.*`-shaped
    field a grid can vary. `_settings_has_key` on its own also refuses a bare name
    outright (closing the gap where it slipped past the old `FORBIDDEN_AXIS_PREFIXES`
    `startswith` check, since a bare name never matches a dotted prefix; #952 reviewer
    findings), exercised directly below."""
    with pytest.raises(ValueError, match="not under an allowed prefix"):
        Settings(_env_file=None, lab={"sweepable_keys": [bare_key], "axis_lattice": {}})


@pytest.mark.parametrize(
    "key",
    ["universe", "costs", "alpaca_api_key", "not_a_section", "strategy", "strategy."],
)
def test_settings_has_key_refuses_every_bare_or_dotless_name(key: str) -> None:
    """Unit test on `_settings_has_key` itself, independent of the allow-list: it
    requires a non-empty `section.field` shape, so a bare name or a trailing-dot-only
    string is never treated as a real key, whether or not `Settings` has a field or a
    section by that name."""
    assert _settings_has_key(key) is False


def test_settings_has_key_accepts_a_real_dotted_field() -> None:
    assert _settings_has_key("strategy.formation_months") is True
    assert _settings_has_key("schedule.rebalance_cadence") is True
    assert _settings_has_key("strategy.not_a_real_field") is False
    assert _settings_has_key("not_a_section.key") is False


def test_sweepable_keys_entry_bare_exact_forbidden_name_rejected() -> None:
    """`benchmarks` (no trailing dot: the one `FORBIDDEN_AXIS_PREFIXES` entry that is
    an exact field name, not a prefix) is still refused, by the prefix check itself."""
    with pytest.raises(ValueError, match="forbidden prefix"):
        Settings(_env_file=None, lab={"sweepable_keys": ["benchmarks"], "axis_lattice": {}})


# --- `profitability` family (backtest spec amendment #720, accepted 2026-10-06; T85) ---


def test_profitability_is_a_root_family() -> None:
    assert "profitability" in get_args(HypothesisFamily)
    assert FAMILY_PARENTS["profitability"] is None


def test_profitability_defaults() -> None:
    p = _settings().profitability
    assert p.basis == "gross"
    assert p.annual_period_days == (350, 380)
    assert p.max_fact_age_days == 548
    assert p.exclude_sic_ranges == ((6000, 6999),)
    assert p.include_derived is True
    assert p.top_fraction == pytest.approx(0.10)
    assert p.weighting == "equal"


def test_profitability_basis_is_gross_only() -> None:
    """`cash` joins the literal only when its pre-declared variant registers."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, profitability={"basis": "cash"})


@pytest.mark.parametrize(
    "override",
    [
        {"unknown_key": 1},
        {"top_fraction": float("nan")},
        {"top_fraction": float("inf")},
        {"top_fraction": 0.0},
        {"top_fraction": 1.5},
        {"weighting": "cap"},
        {"max_fact_age_days": 0},
        {"annual_period_days": [380, 350]},
        {"annual_period_days": [0, 380]},
        {"exclude_sic_ranges": [[6999, 6000]]},
    ],
)
def test_profitability_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, profitability=override)


def test_profitability_keys_are_not_sweepable_by_default() -> None:
    """`lab.sweepable_keys` is unchanged by T85 (spec open question 12 is a later,
    reviewed config change)."""
    assert not any(k.startswith("profitability.") for k in _settings().lab.sweepable_keys)


def test_env_example_names_profitability_keys() -> None:
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    for name in Settings.model_fields["profitability"].annotation.model_fields:  # type: ignore[union-attr]
        assert f"PROFITABILITY__{name.upper()}=" in text, name


def test_every_non_oracle_family_has_a_family_parents_entry() -> None:
    """Every family in `HypothesisFamily` except `oracle` needs a lineage entry
    (strategy-lab spec open question 11)."""
    non_oracle = {family for family in get_args(HypothesisFamily) if family != "oracle"}
    assert non_oracle <= set(FAMILY_PARENTS)
    assert FAMILY_PARENTS["momentum"] is None


# --- The family registry (ADR 0014 point 2, T128) ---


def test_hypothesis_family_literal_equals_the_registry_keys() -> None:
    """The `HypothesisFamily` `Literal` and `FAMILIES` must enumerate the same names:
    the literal stays explicit for mypy strict, and a test pins the equality."""
    assert tuple(get_args(HypothesisFamily)) == tuple(FAMILIES)


def test_family_parents_derives_without_oracle_at_today_s_values() -> None:
    assert FAMILY_PARENTS == {"momentum": None, "profitability": None, "combined": "momentum"}


def test_family_signal_sections_derives_without_oracle_at_today_s_values() -> None:
    assert FAMILY_SIGNAL_SECTIONS == {
        "momentum": "strategy",
        "profitability": "profitability",
        "combined": "combined",
    }


def test_engine_families_derives_at_today_s_value() -> None:
    assert ENGINE_FAMILIES == ("momentum", "oracle", "profitability", "combined")


def test_paper_families_derives_at_today_s_value() -> None:
    assert PAPER_FAMILIES == ("momentum", "oracle", "profitability", "combined")


def test_default_sweepable_keys_derives_at_today_s_value() -> None:
    assert _DEFAULT_SWEEPABLE_KEYS == (
        "strategy.formation_months",
        "strategy.skip_months",
        "strategy.top_fraction",
        "strategy.weighting",
        "strategy.signal_total_return",
        "strategy.turnover_top_fraction",
        "schedule.rebalance_cadence",
        "schedule.signal_anchor",
    )


def test_allowed_axis_prefixes_derives_at_today_s_value() -> None:
    assert ALLOWED_AXIS_PREFIXES == ("strategy.", "schedule.")


def test_momentum_family_spec() -> None:
    spec = FAMILIES["momentum"]
    assert spec.sections == ("strategy",)
    assert spec.params_model is StrategyConfig
    assert spec.parent is None
    assert spec.engine_ready is True
    assert spec.paper_ready is True
    # B10's screen (#1358): its reason and counts, reported only below 1.0.
    assert spec.exclusion_reasons == ("no_history", "no_turnover")
    assert spec.count_names == ("n_excluded_no_history", "n_screened", "n_excluded_no_turnover")
    assert spec.benchmark == "MTUM"
    assert spec.sweepable_keys == (
        "strategy.formation_months",
        "strategy.skip_months",
        "strategy.top_fraction",
        "strategy.weighting",
        "strategy.signal_total_return",
        "strategy.turnover_top_fraction",
    )


def test_turnover_top_fraction_defaults_to_no_screen() -> None:
    """B10's key (backtest spec amendment #1358): 1.0 by default, in (0, 1]."""
    assert StrategyConfig().turnover_top_fraction == 1.0
    assert StrategyConfig(turnover_top_fraction=0.2).turnover_top_fraction == 0.2
    for bad in (0, 1.5, -0.1):
        with pytest.raises(ValidationError):
            StrategyConfig(turnover_top_fraction=bad)


def test_oracle_family_spec_reads_momentum_s_section() -> None:
    """`oracle` is a test-only entry that reads momentum's `strategy` section."""
    spec = FAMILIES["oracle"]
    assert spec.sections == ("strategy",)
    assert spec.params_model is StrategyConfig
    assert spec.engine_ready is True
    assert spec.paper_ready is True
    assert spec.sweepable_keys == ()


def test_profitability_family_spec() -> None:
    spec = FAMILIES["profitability"]
    assert spec.sections == ("profitability",)
    assert spec.params_model is ProfitabilityConfig
    assert spec.parent is None
    assert spec.engine_ready is True
    assert spec.paper_ready is True
    assert spec.exclusion_reasons == ("sector", "no_facts", "stale_facts", "malformed")
    assert spec.count_names == (
        "n_ranked",
        "n_excluded_no_facts",
        "n_excluded_stale_facts",
        "n_excluded_sector",
        "n_excluded_malformed",
        "n_derived",
    )
    assert spec.benchmark == "MTUM"
    assert spec.sweepable_keys == ()


# --- The `combined` family (hypothesis backlog B4; ADR 0014 point 6, T130) -----


def test_combined_is_a_child_of_momentum() -> None:
    """Owner decision 2026-10-06 on #1074 (ADR 0014 open question 2)."""
    assert "combined" in get_args(HypothesisFamily)
    assert FAMILY_PARENTS["combined"] == "momentum"


def test_combined_defaults() -> None:
    c = _settings().combined
    assert c.top_fraction == pytest.approx(0.10)
    assert c.weighting == "equal"


@pytest.mark.parametrize(
    "override",
    [
        {"unknown_key": 1},
        {"top_fraction": float("nan")},
        {"top_fraction": float("inf")},
        {"top_fraction": 0.0},
        {"top_fraction": 1.5},
        {"weighting": "cap"},
    ],
)
def test_combined_rejects_invalid_values(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, combined=override)


def test_combined_keys_are_not_sweepable_by_default() -> None:
    assert not any(k.startswith("combined.") for k in _settings().lab.sweepable_keys)


def test_env_example_names_combined_keys() -> None:
    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    for name in Settings.model_fields["combined"].annotation.model_fields:  # type: ignore[union-attr]
        assert f"COMBINED__{name.upper()}=" in text, name


def test_combined_family_spec() -> None:
    spec = FAMILIES["combined"]
    assert spec.sections == ("combined", "strategy", "profitability")
    assert spec.params_model is CombinedConfig
    assert spec.parent == "momentum"
    assert spec.engine_ready is True
    assert spec.paper_ready is True
    assert spec.exclusion_reasons == (
        "no_history",
        "sector",
        "no_facts",
        "stale_facts",
        "malformed",
        "one_signal_only",
    )
    assert spec.count_names == (
        "n_excluded_no_history",
        "n_ranked",
        "n_excluded_no_facts",
        "n_excluded_stale_facts",
        "n_excluded_sector",
        "n_excluded_malformed",
        "n_derived",
        "n_combined",
    )
    assert spec.benchmark == "MTUM"
    assert spec.sweepable_keys == ()


@pytest.mark.parametrize(
    "timezone",
    ["America/New_York", "UTC", "Europe/London"],
)
def test_quiet_timezone_accepts_valid_iana_zones(timezone: str) -> None:
    settings = Settings(_env_file=None, lab={"quiet_timezone": timezone})
    assert settings.lab.quiet_timezone == timezone


@pytest.mark.parametrize("timezone", ["Not/AZone", "EST5EDT9", ""])
def test_quiet_timezone_rejects_an_invalid_iana_zone(timezone: str) -> None:
    with pytest.raises(ValidationError, match="not a valid IANA timezone"):
        Settings(_env_file=None, lab={"quiet_timezone": timezone})


def test_axis_lattice_key_outside_sweepable_keys_rejected() -> None:
    with pytest.raises(ValidationError, match=r"not in lab\.sweepable_keys"):
        Settings(
            _env_file=None,
            lab={
                "sweepable_keys": ["strategy.top_fraction"],
                "axis_lattice": {"strategy.formation_months": 1.0},
            },
        )


def test_axis_lattice_key_within_sweepable_keys_accepted() -> None:
    settings = Settings(
        _env_file=None,
        lab={
            "sweepable_keys": ["strategy.top_fraction", "strategy.formation_months"],
            "axis_lattice": {"strategy.top_fraction": 0.01, "strategy.formation_months": 1.0},
        },
    )
    assert settings.lab.axis_lattice == {
        "strategy.top_fraction": 0.01,
        "strategy.formation_months": 1.0,
    }


@pytest.mark.parametrize("weekday", [-1, 7])
def test_quiet_weekdays_out_of_range_rejected(weekday: int) -> None:
    with pytest.raises(ValidationError, match="0-6"):
        Settings(_env_file=None, lab={"quiet_weekdays": [weekday]})


def test_seconds_per_variant_default_missing_a_cadence_rejected() -> None:
    with pytest.raises(ValidationError, match="missing an entry"):
        Settings(_env_file=None, lab={"seconds_per_variant_default": {"month_end": 1.0}})


@pytest.mark.parametrize(
    "value",
    [
        [("16:00", "21:00")],
        [("09:00", "17:00")],
        [("00:00", "23:59")],
        [("09:00", "12:00"), ("13:00", "17:00")],
    ],
    ids=["default", "business-hours", "full-day", "two-intervals"],
)
def test_quiet_intervals_accepts_valid_lists(value: list[tuple[str, str]]) -> None:
    settings = Settings(_env_file=None, lab={"quiet_intervals": value})
    assert settings.lab.quiet_intervals == value


@pytest.mark.parametrize(
    "value",
    [
        [("1600", "21:00")],
        [("4:00", "21:00")],
        [("24:00", "21:00")],
        [("16:00", "16:60")],
    ],
    ids=["no-colon", "one-digit-hour", "24-hour", "60-minutes"],
)
def test_quiet_intervals_rejects_non_hh_mm_times(value: list[tuple[str, str]]) -> None:
    with pytest.raises(ValidationError, match="is not HH:MM"):
        Settings(_env_file=None, lab={"quiet_intervals": value})


@pytest.mark.parametrize(
    "value",
    [
        [("16:00", "16:00")],
        [("21:00", "16:00")],
    ],
    ids=["equal", "end-before-start"],
)
def test_quiet_intervals_rejects_end_not_after_start(value: list[tuple[str, str]]) -> None:
    with pytest.raises(ValidationError, match="must end after it starts"):
        Settings(_env_file=None, lab={"quiet_intervals": value})


def test_quiet_intervals_rejects_non_hh_mm_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAB__QUIET_INTERVALS", '[["1600", "21:00"]]')
    with pytest.raises(ValidationError, match="is not HH:MM"):
        Settings(_env_file=None)


def test_quiet_intervals_rejects_equal_pair_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAB__QUIET_INTERVALS", '[["16:00", "16:00"]]')
    with pytest.raises(ValidationError, match="must end after it starts"):
        Settings(_env_file=None)


@pytest.mark.parametrize("level", [float("nan"), float("inf")])
def test_costs_non_finite_sensitivity_rejected(level: float) -> None:
    """A NaN level would silently turn that level's results into NaN."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={"sensitivity_per_side_bps": [0, level]})


@pytest.mark.parametrize("field", ["per_side_bps", "commission_per_share", "commission_per_order"])
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_costs_non_finite_component_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, costs={field: value})


@pytest.mark.parametrize("families", [[], ["momentum", "momentum"]])
def test_hypotheses_families_empty_or_duplicate_rejected(families: list[str]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, hypotheses={"families": families})


@pytest.mark.parametrize(
    ("section", "override"),
    [
        ("hypotheses", {"familes": ["momentum"]}),
        ("strategy", {"top_fracton": 0.2}),
        ("costs", {"per_side_bp": 0}),
        ("holdout", {"stat": "2024-01-01"}),
        ("backtest", {"initial_capitol": 1.0}),
        ("metrics", {"red_flag": 1.0}),
        ("schedule", {"rebalance_cadenc": "month_end"}),
        ("lab", {"max_variants_per_swee": 10}),
    ],
)
def test_phase3_sections_reject_unknown_keys(section: str, override: dict[str, object]) -> None:
    """A typo in a pinned key must fail, not fall back to the default (spec req 10)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{section: override})


@pytest.mark.parametrize(
    ("section", "override"),
    [
        ("metrics", {"red_flag_excess_cagr_pp": -1.0}),
        ("metrics", {"red_flag_excess_cagr_pp": float("nan")}),
        ("metrics", {"risk_free_rate": float("nan")}),
        ("backtest", {"cash_rate": float("inf")}),
        ("backtest", {"initial_capital": float("inf")}),
        ("strategy", {"top_fraction": float("nan")}),
    ],
)
def test_phase3_rates_and_thresholds_reject_bad_values(
    section: str, override: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{section: override})


def test_misspelt_phase3_env_var_fails_loudly_naming_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`COSTS__PER_SIDE_BP` (typo) must fail closed with the key in the error, not load
    the 15 bp default. Loading `Settings` then fails for every job, secrets included,
    so the message has to point straight at the bad key."""
    monkeypatch.setenv("COSTS__PER_SIDE_BP", "0")
    with pytest.raises(ValidationError, match="per_side_bp"):
        Settings(_env_file=None)


# --- Phase 4 keys (docs/specs/paper-trading.md "Config keys", ADR 0010 point 1, T47) ---


def test_alpaca_paper_guarded_default_true() -> None:
    """`alpaca.paper` is guarded: Phase 6 changes it by ADR, never by config."""
    assert _settings().alpaca.paper is True


def test_alpaca_paper_rejects_false_in_code() -> None:
    with pytest.raises(ValidationError, match="guarded setting"):
        Settings(_env_file=None, alpaca={"paper": False})


def test_alpaca_paper_rejects_false_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA__PAPER", "false")
    with pytest.raises(ValidationError, match="guarded setting"):
        Settings(_env_file=None)


def test_alpaca_paper_cannot_be_assigned_after_construction() -> None:
    """The guard runs at construction, so the section is frozen: no later assignment can
    point the order path at the live endpoint (safety-reviewer on T47)."""
    s = _settings()
    with pytest.raises(ValidationError):
        s.alpaca.paper = False  # type: ignore[misc]
    assert s.alpaca.paper is True


def test_alpaca_rejects_unknown_keys() -> None:
    """A misspelt broker fact must fail loudly, not leave the real one unset."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpaca={"quantity_decimal": 4})


def test_alpaca_trading_defaults() -> None:
    a = _settings().alpaca
    assert a.trading_requests_per_minute == pytest.approx(150.0)
    assert a.trading_request_timeout_seconds == pytest.approx(30.0)
    assert a.trading_max_retries == 3
    # Broker facts the recording task (T48b) set: 9 decimals, accepted and filled
    # on paper (tests/fixtures/alpaca/paper/buy_fractional.json, flatten.json);
    # 128 characters, Alpaca's documented `client_order_id` limit.
    assert a.quantity_decimals == 9
    assert a.client_order_id_max_length == 128


PAPER_FIXTURES = Path(__file__).parent / "fixtures" / "alpaca" / "paper"


def test_quantity_decimals_matches_the_recorded_paper_fills() -> None:
    """The default precision is the finest quantity paper filled (T48b's
    recording): every fill fits it, and at least one fill needs all of it."""
    fills = json.loads((PAPER_FIXTURES / "fill_activities.json").read_text())
    places = [
        -Decimal(f["qty"]).normalize().as_tuple().exponent
        for f in fills
        if f["activity_type"] == "FILL"
    ]
    assert max(places) == _settings().alpaca.quantity_decimals


def test_alpaca_broker_facts_may_still_be_unset() -> None:
    """`None` stays valid, so the adapter's refusal to construct without the
    recorded facts (T48c) can still be exercised."""
    a = Settings(
        _env_file=None, alpaca={"quantity_decimals": None, "client_order_id_max_length": None}
    ).alpaca
    assert a.quantity_decimals is None
    assert a.client_order_id_max_length is None


@pytest.mark.parametrize(
    "override",
    [
        {"trading_requests_per_minute": 0},
        {"trading_request_timeout_seconds": 0},
        {"trading_max_retries": -1},
        {"quantity_decimals": -1},
        {"client_order_id_max_length": 0},
        {"trading_request_timeout_seconds": float("inf")},
        {"trading_requests_per_minute": float("inf")},
    ],
)
def test_alpaca_trading_keys_reject_nonsense(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpaca=override)


def test_risk_defaults_match_adr_0010() -> None:
    r = _settings().risk
    assert r.max_position_weight == pytest.approx(0.05)
    assert r.max_order_notional_fraction == pytest.approx(0.05)
    assert r.max_gross_exposure == pytest.approx(1.0)
    assert r.max_orders_per_run == 250
    assert r.max_skips_per_run == 10
    assert r.max_rejections_per_run == 5
    assert r.max_drawdown == pytest.approx(0.30)
    assert r.min_order_notional == pytest.approx(1.0)
    assert r.whole_share_price_buffer == pytest.approx(0.02)
    assert r.max_unspent_cash_fraction == pytest.approx(0.05)
    assert r.max_fill_lag_sessions == 1
    assert r.clock_max_sessions_late == 1
    assert r.max_broker_clock_skew_seconds == pytest.approx(60.0)
    assert r.reconcile_quantity_tolerance == pytest.approx(1e-6)
    assert r.reconcile_cash_tolerance == pytest.approx(0.01)


@pytest.mark.parametrize(
    "field",
    [
        "max_position_weight",
        "max_order_notional_fraction",
        "max_gross_exposure",
        "max_drawdown",
        "whole_share_price_buffer",
        "max_unspent_cash_fraction",
    ],
)
@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan")])
def test_risk_fractions_outside_unit_interval_rejected(field: str, value: float) -> None:
    """Every `risk.*` fraction is a share in [0, 1]; above 1 would be leverage."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, risk={field: value})


@pytest.mark.parametrize("value", [0, -1])
def test_risk_max_fill_lag_sessions_at_least_one(value: int) -> None:
    """Validated >= 1 (spec): the bound is measured in whole sessions past the anchor."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, risk={"max_fill_lag_sessions": value})


@pytest.mark.parametrize(
    "override",
    [
        {"max_orders_per_run": 0},
        {"max_skips_per_run": -1},
        {"max_rejections_per_run": -1},
        {"clock_max_sessions_late": -1},
        {"min_order_notional": -1.0},
        {"max_broker_clock_skew_seconds": -1.0},
        {"reconcile_quantity_tolerance": -1e-6},
        {"reconcile_cash_tolerance": float("inf")},
        {"max_position_weigth": 0.05},
    ],
)
def test_risk_rejects_nonsense_and_unknown_keys(override: dict[str, object]) -> None:
    """The section is frozen into the window by name (req 14): a typo must fail, never
    fall back to a default."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, risk=override)


def test_paper_defaults() -> None:
    p = _settings().paper
    # ADR 0017 part C, open question 2: six months, a quarter of weeks, a quarter of
    # sessions; `paper start` freezes the window's cadence's entry as a scalar.
    assert p.min_rebalances == {"month_end": 6, "week_end": 13, "daily": 63}
    # One k at every cadence (ADR 0017 open question 6): a scalar, never a table.
    assert p.tracking_k == pytest.approx(2.0)
    assert p.tracking_rule == "residual"  # T70, ADR 0005 amendment 2026-10-09 (#247 Q4)
    assert p.max_catch_up_sessions == 5
    assert p.submit_window_before_open_minutes == 90
    assert p.submit_window_after_open_minutes == 30
    # T70, ADR 0006 amendment 2026-10-09: checked against Probe 3's measured paper fill
    # latency (worst whole-share fill 118.5 s after the open) and kept.
    assert p.sell_wait_seconds == pytest.approx(900.0)
    assert p.poll_interval_seconds == pytest.approx(15.0)
    assert p.accept_wait_seconds == pytest.approx(30.0)
    assert p.fill_read_overlap_seconds == pytest.approx(60.0)
    assert p.order_id_prefix == "tp"
    assert p.book_id == DEFAULT_BOOK_ID == "main"
    assert p.live_capital_reference == pytest.approx(100.0)
    assert p.min_override_reason_chars == 20


def test_paper_tracking_rule_residual_accepted_and_others_rejected() -> None:
    assert Settings(_env_file=None, paper={"tracking_rule": "raw"}).paper.tracking_rule == "raw"
    assert Settings(_env_file=None, paper={"tracking_rule": "residual"}).paper.tracking_rule == (
        "residual"
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, paper={"tracking_rule": "net"})


def test_paper_poll_interval_never_above_accept_wait() -> None:
    """Spec req 3(f): `Settings` rejects a poll interval above the accept wait."""
    with pytest.raises(ValidationError, match="poll_interval_seconds"):
        Settings(_env_file=None, paper={"poll_interval_seconds": 31.0, "accept_wait_seconds": 30.0})
    equal = {"poll_interval_seconds": 30.0, "accept_wait_seconds": 30.0}
    assert Settings(_env_file=None, paper=equal).paper.poll_interval_seconds == pytest.approx(30.0)


@pytest.mark.parametrize(
    "override",
    [
        {"min_rebalances": 0},
        {"min_rebalances": 6},
        {"min_rebalances": {"month_end": 6, "week_end": 13}},
        {"min_rebalances": {"month_end": 6, "week_end": 13, "daily": 0}},
        {"min_rebalances": {"month_end": 6, "week_end": 13, "daily": 63, "quarter_end": 2}},
        {"tracking_k": -1.0},
        {"max_catch_up_sessions": -1},
        {"submit_window_before_open_minutes": -1},
        {"submit_window_after_open_minutes": -1},
        {"sell_wait_seconds": -1.0},
        {"poll_interval_seconds": 0.0},
        {"accept_wait_seconds": 0.0},
        {"fill_read_overlap_seconds": -1.0},
        {"order_id_prefix": ""},
        {"order_id_prefix": "t p"},
        {"book_id": ""},
        {"book_id": "a-b"},
        {"book_id": "a:b"},
        {"live_capital_reference": 0.0},
        {"min_override_reason_chars": 0},
        {"tracking_k": float("nan")},
        {"min_rebalance": 6},
    ],
)
def test_paper_rejects_nonsense_and_unknown_keys(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, paper=override)


def test_paper_min_rebalances_table_overrides_one_cadence() -> None:
    """The table is the config key (ADR 0017 part C); an override restates every
    cadence, and the validator names a missing one."""
    table = {"month_end": 6, "week_end": 10, "daily": 63}
    assert Settings(_env_file=None, paper={"min_rebalances": table}).paper.min_rebalances == table
    with pytest.raises(ValidationError, match="missing an entry for"):
        Settings(_env_file=None, paper={"min_rebalances": {"month_end": 6, "week_end": 13}})


def test_frozen_paper_keys_are_the_five_req_14_names() -> None:
    assert FROZEN_PAPER_KEYS == (
        "tracking_k",
        "min_rebalances",
        "tracking_rule",
        "max_catch_up_sessions",
        "min_override_reason_chars",
    )
    assert set(FROZEN_PAPER_KEYS) <= set(PaperConfig.model_fields)


def test_frozen_execution_keys_cover_fill_price() -> None:
    """#366 Q20 (owner): `execution.fill_price` freezes into the paper window
    beside the `paper.*` keys, read from `frozen_json`, never live `Settings`."""
    assert FROZEN_EXECUTION_KEYS == ("fill_price",)
    assert set(FROZEN_EXECUTION_KEYS) <= set(ExecutionConfig.model_fields)


def test_dashboard_page_row_limit_defaults_to_500_and_must_be_positive() -> None:
    """ADR 0011, #273: bounds every per-row read a page makes over the journal."""
    assert _settings().dashboard.page_row_limit == 500
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, dashboard={"page_row_limit": bad})


def test_research_experiments_dir_defaults_to_docs_experiments() -> None:
    """docs/specs/research-registry.md req 2, req 14: the one key `experiment
    register`/`dataset register` read from; no env var (spec, Config keys)."""
    assert _settings().research.experiments_dir == "docs/experiments"
    overridden = Settings(
        _env_file=None, research={"experiments_dir": "tests/fixtures/experiments"}
    )
    assert overridden.research.experiments_dir == "tests/fixtures/experiments"


def test_research_labeling_defaults() -> None:
    """Research-labeling spec, amendment 2026-10-06's config table (plan T119): every
    key and default; the two ceilings and the key are zero/absent in code."""
    research = _settings().research
    assert research.spend_ceiling_usd_month == 0.0
    assert research.spend_ceiling_usd_total == 0.0
    labeling = research.labeling
    assert labeling.api_base_url == "https://api.typesafe.ai/v1"
    assert labeling.price_usd_per_million_input_tokens == 0.042
    assert labeling.chars_per_token == 2.5
    assert labeling.max_packet_tokens == 8000
    assert labeling.exhibit_max_chars == 4000
    assert labeling.item_max_chars == 4000
    assert labeling.eightk_max_chars == 12000
    assert labeling.context_before_days == 45
    assert labeling.context_after_days == 20
    assert labeling.marker_after_days == 400
    # Owner decision 2026-10-06 question 5 (E14), not the body's list.
    assert labeling.eightk_items == ["3.01", "2.01", "1.03", "5.01", "3.03", "1.01", "8.01"]
    assert labeling.requests_per_second == 2.0
    assert labeling.request_timeout_seconds == 60.0
    assert labeling.max_attempts == 3
    # The amendment removed these two keys.
    assert not hasattr(labeling, "estimate_margin")
    assert not hasattr(labeling, "batch_max_usd")
    assert _settings().typesafe_api_key is None


def test_research_data_dir_is_under_the_repo_and_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    data_dir = Path(_settings().research.data_dir)
    assert data_dir.is_absolute()
    assert data_dir == Path(__file__).resolve().parents[1] / "data" / "research"


def test_research_ceilings_and_key_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The three variables the spec names (req 8, req 17) and nothing else."""
    monkeypatch.setenv("RESEARCH__SPEND_CEILING_USD_MONTH", "40")
    monkeypatch.setenv("RESEARCH__SPEND_CEILING_USD_TOTAL", "40")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key-abc123")
    monkeypatch.setenv("RESEARCH__DATA_DIR", "/tmp/research-x")
    s = _settings()
    assert s.research.spend_ceiling_usd_month == 40.0
    assert s.research.spend_ceiling_usd_total == 40.0
    assert s.research.data_dir == "/tmp/research-x"
    assert s.typesafe_api_key is not None
    assert s.typesafe_api_key.get_secret_value() == "ts-key-abc123"


@pytest.mark.parametrize(
    ("section", "values"),
    [
        ("research", {"spend_ceiling_usd_month": -1.0}),
        ("research", {"spend_ceiling_usd_total": -0.01}),
        ("research", {"spend_ceiling_usd_total": float("inf")}),
        ("labeling", {"api_base_url": "http://api.typesafe.ai/v1"}),
        ("labeling", {"chars_per_token": 0}),
        ("labeling", {"max_packet_tokens": 0}),
        ("labeling", {"exhibit_max_chars": 0}),
        ("labeling", {"item_max_chars": 0}),
        ("labeling", {"eightk_max_chars": 0}),
        ("labeling", {"context_before_days": -1}),
        ("labeling", {"requests_per_second": 0}),
        ("labeling", {"request_timeout_seconds": 0}),
        ("labeling", {"max_attempts": 0}),
        ("labeling", {"price_usd_per_million_input_tokens": 0}),
        ("labeling", {"eightk_items": []}),
        ("labeling", {"eightk_items": ["3.01", "3.01"]}),
        ("labeling", {"eightk_items": ["Item 3.01"]}),
        ("labeling", {"estimate_margin": 0.25}),
    ],
)
def test_research_labeling_rejects_nonsense(section: str, values: dict[str, object]) -> None:
    research: dict[str, object] = dict(values) if section == "research" else {"labeling": values}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, research=research)


@pytest.mark.parametrize("value", ["inf", "nan", "-1"])
def test_research_ceilings_from_the_environment_reject_nonsense(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RESEARCH__SPEND_CEILING_USD_TOTAL", value)
    with pytest.raises(ValidationError):
        _settings()


def test_typesafe_key_is_redacted_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec req 17: a `SecretStr` in `secret_values`, so `clean_message` (and
    `cli_record`, which uses the same set) redacts it; absent from repr/str."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-key-abc123")
    s = _settings()
    for blob in (repr(s), str(s), repr(s.typesafe_api_key)):
        assert "ts-key-abc123" not in blob
    cleaned = clean_message("401 for Bearer ts-key-abc123 on systemone", s)
    assert "ts-key-abc123" not in cleaned
    assert "[redacted]" in cleaned


def test_alerts_channels_default_store_and_macos() -> None:
    """#247 Q2: `[store, macos]`; `email` only when the owner sets the `ALERT_*` variables."""
    assert _settings().alerts.channels == ["store", "macos"]
    with_email = ["store", "macos", "email"]
    assert Settings(_env_file=None, alerts={"channels": with_email}).alerts.channels == with_email


def test_alerts_delivery_timeout_defaults_to_ten_seconds_and_must_be_positive() -> None:
    """T57: bounds one `osascript` call or SMTP socket operation; the spec names no value."""
    assert _settings().alerts.delivery_timeout_seconds == 10.0
    for bad in (0, -1.0):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, alerts={"delivery_timeout_seconds": bad})


@pytest.mark.parametrize(
    "channels",
    [["macos"], [], ["store", "store"], ["store", "push"]],
)
def test_alerts_channels_require_store_once_and_known_names(channels: list[str]) -> None:
    """`store` is always a channel (spec req 11); a paid push service needs a budget change."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alerts={"channels": channels})


def test_alerts_channels_require_a_non_store_channel() -> None:
    """#366 Q22 (iii), #515/#416: `store`-only would make `deliver_without_store`
    (the halt path's `kill_switch_write_failed` alert, which never touches the
    store) deliver through nothing at all."""
    with pytest.raises(ValidationError, match="non-store"):
        Settings(_env_file=None, alerts={"channels": ["store"]})


_ALL_EMAIL_SETTINGS: dict[str, str] = {
    "alert_smtp_host": "smtp.example.com",
    "alert_smtp_user": "alerts-user",
    "alert_smtp_password": "hunter2",
    "alert_email_to": "owner@example.com",
}


def test_alerts_email_channel_with_nothing_set_refuses() -> None:
    """#544: `channels=[store, email]` with every `ALERT_*` variable unset passed
    #543's "at least one non-store channel is listed" check but left
    `kill_switch_write_failed` reaching nobody, since `email` can never actually
    deliver. The owner's #366 Q22 (iii) answer requires a *usable* channel.

    This refuses with a plain `ValueError`, not a `ValidationError`: a whole-model
    pydantic validator's `ValidationError` embeds the raw constructor input (every
    field, including any other secret passed alongside `alerts=...`) in its
    `input_value`, which `_validate_alert_channel_is_usable` is deliberately
    structured to avoid (see its docstring in `config.py`)."""
    with pytest.raises(ValueError, match="ALERT_SMTP_HOST"):
        Settings(_env_file=None, alerts={"channels": ["store", "email"]})


def test_alerts_email_channel_with_every_setting_loads() -> None:
    s = Settings(_env_file=None, alerts={"channels": ["store", "email"]}, **_ALL_EMAIL_SETTINGS)
    assert s.alerts.channels == ["store", "email"]


def test_alerts_email_channel_names_the_single_missing_variable() -> None:
    """Only the actually-missing variable is named; nothing else, and never a value."""
    partial = {k: v for k, v in _ALL_EMAIL_SETTINGS.items() if k != "alert_smtp_password"}
    with pytest.raises(ValueError) as exc_info:
        Settings(_env_file=None, alerts={"channels": ["store", "email"]}, **partial)
    message = str(exc_info.value)
    assert "ALERT_SMTP_PASSWORD" in message
    for other in ("ALERT_SMTP_HOST", "ALERT_SMTP_USER", "ALERT_EMAIL_TO"):
        assert other not in message
    for secret in _ALL_EMAIL_SETTINGS.values():
        assert secret not in message


def test_alerts_email_alongside_macos_needs_no_email_config() -> None:
    """`macos` alone is a usable non-store channel, so a config that also lists
    `email` (e.g. belt-and-suspenders) is not forced to configure it too."""
    s = Settings(_env_file=None, alerts={"channels": ["store", "macos", "email"]})
    assert s.alerts.channels == ["store", "macos", "email"]


def test_paper_and_alert_secrets_default_to_none() -> None:
    s = _settings()
    assert s.alpaca_paper_api_key is None
    assert s.alpaca_paper_api_secret is None
    assert s.alert_smtp_host is None
    assert s.alert_smtp_user is None
    assert s.alert_smtp_password is None
    assert s.alert_email_to is None
    assert s.alert_email_from is None


def test_paper_and_alert_secrets_absent_from_repr_and_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "pk-paper-abc123")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "ps-paper-secret456")
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_SMTP_USER", "alerts-user")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "smtp-pass-789")
    monkeypatch.setenv("ALERT_EMAIL_TO", "jose@example.com")
    monkeypatch.setenv("ALERT_EMAIL_FROM", "alerts-sender@example.com")
    s = _settings()
    assert s.alpaca_paper_api_key is not None
    assert s.alpaca_paper_api_key.get_secret_value() == "pk-paper-abc123"
    assert s.alert_smtp_host == "smtp.example.com"
    for blob in (repr(s), str(s)):
        for secret in ("pk-paper-abc123", "ps-paper-secret456", "alerts-user", "smtp-pass-789"):
            assert secret not in blob
        assert "jose@example.com" not in blob
        assert "alerts-sender@example.com" not in blob


def test_paper_keys_are_separate_from_data_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live-capable data key never reaches the order path: the paper keys are their
    own variables and do not fall back to `ALPACA_API_KEY`/`ALPACA_API_SECRET`."""
    monkeypatch.setenv("ALPACA_API_KEY", "sk-live-abc123")
    monkeypatch.setenv("ALPACA_API_SECRET", "sk-live-secret456")
    s = _settings()
    assert s.alpaca_paper_api_key is None
    assert s.alpaca_paper_api_secret is None


def test_env_example_lists_phase_4_variables_but_never_alpaca_paper() -> None:
    """`alpaca.paper` is never listed as overridable (spec, Config keys); the paper keys,
    the invoker marker and the four `ALERT_*` variables are."""
    text = Path(__file__).resolve().parents[1] / ".env.example"
    upper = text.read_text(encoding="utf-8").upper()
    assert "ALPACA__PAPER" not in upper
    assert "ALPACA_PAPER=" not in upper
    for name in (
        "ALPACA_PAPER_API_KEY",
        "ALPACA_PAPER_API_SECRET",
        "TRADEPARTNER_INVOKED_BY",
        "ALERT_SMTP_HOST",
        "ALERT_SMTP_USER",
        "ALERT_SMTP_PASSWORD",
        "ALERT_EMAIL_TO",
    ):
        assert name in upper


# --- one paper key pair per book (ADR 0017 B.1, plan T153) -------------------


def test_main_keeps_todays_paper_variables_and_other_books_are_named_by_token() -> None:
    assert MAIN_BOOK_ID == DEFAULT_BOOK_ID == PaperConfig().book_id
    assert paper_key_variable_names("main") == ("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_API_SECRET")
    assert paper_key_variable_names("daily1") == (
        "ALPACA_PAPER_BOOKS__DAILY1__API_KEY",
        "ALPACA_PAPER_BOOKS__DAILY1__API_SECRET",
    )


def test_a_books_pair_loads_from_the_environment_and_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The names `paper_key_variable_names` gives are the names `Settings` reads, from the
    shell and from `.env`; a half-set or mistyped pair still loads (the adapter refuses
    that book alone) and `main`'s variables are untouched."""
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "pk-main-111")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "ps-main-222")
    for name, value in zip(paper_key_variable_names("b"), ("pk-b-333", "ps-b-444"), strict=True):
        monkeypatch.setenv(name, value)
    env_file = tmp_path / "books.env"
    key_c, _secret_c = paper_key_variable_names("c")
    env_file.write_text(
        f"{key_c}=pk-c-555\nALPACA_PAPER_BOOKS__C__API_SECRT=typo-666\n", encoding="utf-8"
    )
    s = Settings(_env_file=env_file)
    assert s.alpaca_paper_api_key is not None and s.alpaca_paper_api_secret is not None
    assert s.alpaca_paper_api_key.get_secret_value() == "pk-main-111"
    assert s.alpaca_paper_api_secret.get_secret_value() == "ps-main-222"
    assert sorted(s.alpaca_paper_books) == ["b", "c"]
    b, c = s.alpaca_paper_books["b"], s.alpaca_paper_books["c"]
    assert b.api_key is not None and b.api_key.get_secret_value() == "pk-b-333"
    assert b.api_secret is not None and b.api_secret.get_secret_value() == "ps-b-444"
    assert c.api_key is not None and c.api_key.get_secret_value() == "pk-c-555"
    assert c.api_secret is None


def test_book_pairs_default_empty_and_never_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "pk-main-111")
    monkeypatch.setenv("ALPACA_API_KEY", "sk-data-abc123")
    assert _settings().alpaca_paper_books == {}


def test_book_pairs_are_redacted_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every book's key and secret are in `secret_values` (so `clean_message` and the
    recorder's scrub redact them) and absent from repr/str."""
    for name, value in zip(
        paper_key_variable_names("b"), ("pk-book-b-777", "ps-book-b-888"), strict=True
    ):
        monkeypatch.setenv(name, value)
    s = _settings()
    assert {"pk-book-b-777", "ps-book-b-888"} <= set(secret_values(s))
    for blob in (repr(s), str(s), repr(s.alpaca_paper_books)):
        assert "pk-book-b-777" not in blob and "ps-book-b-888" not in blob
    cleaned = clean_message("401 for pk-book-b-777:ps-book-b-888", s)
    assert "pk-book-b-777" not in cleaned and "ps-book-b-888" not in cleaned


def test_env_example_names_the_per_book_pair_pattern() -> None:
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    for name in paper_key_variable_names("daily"):
        assert f"# {name}=" in text
    assert "ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY" in text
    assert "ALPACA_PAPER_BOOKS__<TOKEN>__API_SECRET" in text


@pytest.mark.parametrize("pair", [{"BFB": "BFB"}, {"bfb": "BF.B"}, {"BFB": "BF-B"}, {"B1": "B.B"}])
def test_alpaca_class_symbols_fails_closed_on_a_malformed_pair(pair: dict[str, str]) -> None:
    """#1219: a pair is an undotted ticker and a dotted class symbol, or the config refuses."""
    with pytest.raises(ValidationError, match="class_symbols"):
        Settings(_env_file=None, alpaca={"class_symbols": pair})
