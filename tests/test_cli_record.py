"""Tests for `cli_record`'s missing/blank-secret handling and filing-document
helpers (T2 review round 2, safety-reviewer MUST FIX).

No network, no `.env`: `main()` must refuse before ever calling
`adapters.alpaca_raw`/`.edgar_raw` when any secret is missing or blank,
and must never leak a configured secret's value into its own error
message.
"""

from __future__ import annotations

import pytest

from tradepartner import cli_record
from tradepartner.config import Settings


def _settings(**overrides: str | None) -> Settings:
    values: dict[str, str | None] = {
        "alpaca_api_key": None,
        "alpaca_api_secret": None,
        "sec_edgar_user_agent": None,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_main_exits_1_with_only_alpaca_api_key_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_key = "PKREALSECRETVALUE1234567890"  # gitleaks:allow
    settings = _settings(alpaca_api_key=real_key)
    monkeypatch.setattr(cli_record, "get_settings", lambda: settings)

    exit_code = cli_record.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ALPACA_API_SECRET" in captured.err
    assert "SEC_EDGAR_USER_AGENT" in captured.err
    assert "ALPACA_API_KEY" not in captured.err  # that one *was* set
    assert real_key not in captured.err
    assert real_key not in captured.out


def test_main_exits_1_when_a_secret_is_blank(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(
        alpaca_api_key="PKFAKE1234567890ABCD",
        alpaca_api_secret="   ",  # blank, not missing
        sec_edgar_user_agent="TradePartner test test@example.com",
    )
    monkeypatch.setattr(cli_record, "get_settings", lambda: settings)

    exit_code = cli_record.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ALPACA_API_SECRET" in captured.err


def test_missing_secret_names_reports_all_three_when_none_set() -> None:
    assert cli_record._missing_secret_names(_settings()) == [
        "ALPACA_API_KEY",
        "ALPACA_API_SECRET",
        "SEC_EDGAR_USER_AGENT",
    ]


def test_configured_secrets_includes_basic_auth_form() -> None:
    settings = _settings(
        alpaca_api_key="PKFAKE1234567890ABCD",
        alpaca_api_secret="SKFAKE1234567890ABCD",
    )
    secrets = cli_record._configured_secrets(settings)

    assert "PKFAKE1234567890ABCD" in secrets
    assert "SKFAKE1234567890ABCD" in secrets
    # base64("PKFAKE1234567890ABCD:SKFAKE1234567890ABCD")
    other = {"PKFAKE1234567890ABCD", "SKFAKE1234567890ABCD"}
    assert any(len(s) > 20 and s not in other for s in secrets)


def test_configured_secrets_excludes_blank_values() -> None:
    settings = _settings(alpaca_api_key="   ", sec_edgar_user_agent="TradePartner test@example.com")
    secrets = cli_record._configured_secrets(settings)
    assert secrets == ["TradePartner test@example.com"]


# --- filing-document helpers (MUST FIX #2) ----------------------------------


def test_prefer_root_document_strips_xsl_rendered_view_directory() -> None:
    assert cli_record._prefer_root_document("xslF25X02/primary_doc.xml") == "primary_doc.xml"


def test_prefer_root_document_leaves_a_root_level_document_unchanged() -> None:
    assert cli_record._prefer_root_document("primary_doc.xml") == "primary_doc.xml"


def test_flatten_fixture_filename_replaces_every_slash() -> None:
    assert cli_record._flatten_fixture_filename("a/b/c.xml") == "a__b__c.xml"
    assert cli_record._flatten_fixture_filename("primary_doc.xml") == "primary_doc.xml"
