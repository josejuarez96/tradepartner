"""Tests for `cli_record`'s missing/blank-secret handling and filing-document
helpers (T2 review round 2, safety-reviewer MUST FIX).

No network, no `.env`: `main()` must refuse before ever calling
`adapters.alpaca_raw`/`.edgar_raw` when any secret is missing or blank,
and must never leak a configured secret's value into its own error
message.
"""

from __future__ import annotations

from pathlib import Path

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


# --- T3 size trimming (#84) ---------------------------------------------------


def test_trim_company_facts_keeps_dei_and_share_concepts_only() -> None:
    payload = {
        "cik": 1,
        "entityName": "X",
        "facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {}}, "EntityPublicFloat": {}},
            "us-gaap": {
                "CommonStockSharesOutstanding": {"units": {}},
                "WeightedAverageNumberOfSharesOutstandingBasic": {},
                "Revenues": {"units": {}},
                "Assets": {},
            },
            "ffd": {"Something": {}},
        },
    }
    out = cli_record.trim_company_facts(payload)
    assert out["cik"] == 1 and out["entityName"] == "X"
    assert out["facts"]["dei"] == payload["facts"]["dei"]
    assert set(out["facts"]["us-gaap"]) == {
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    }
    assert "ffd" not in out["facts"]
    assert payload["facts"]["us-gaap"].get("Revenues")  # input untouched
    assert cli_record.trim_company_facts({"no": "facts"}) == {"no": "facts"}


def test_trim_company_tickers_keeps_sample_and_recorded_rows() -> None:
    rows = [
        [i, f"C{i}", f"T{i}", "NYSE"] for i in range(cli_record.COMPANY_TICKERS_SAMPLE_ROWS + 50)
    ]
    rows.append([320193, "Apple", "AAPL", "Nasdaq"])
    rows.append([999, "Coke", "KO", "NYSE"])
    rows.append([998, "Other", "ZZZ", "NYSE"])
    payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}
    out = cli_record.trim_company_tickers(payload, ciks=["0000320193"], symbols=["ko"])
    kept = out["data"]
    assert len(kept) == cli_record.COMPANY_TICKERS_SAMPLE_ROWS + 2
    assert kept[-2][2] == "AAPL" and kept[-1][2] == "KO"
    assert out["fields"] == payload["fields"]
    assert cli_record.trim_company_tickers(
        {"fields": ["x"], "data": [[1]]}, ciks=[], symbols=[]
    ) == {
        "fields": ["x"],
        "data": [[1]],
    }


def test_accessions_in_company_facts_collects_every_accn() -> None:
    payload = {
        "facts": {
            "dei": {"A": {"units": {"shares": [{"accn": "1-1", "val": 1}, {"accn": "1-2"}]}}},
            "us-gaap": {"B": {"units": {"USD": [{"accn": "1-1"}, {"val": 3}]}}},
        }
    }
    assert cli_record.accessions_in_company_facts(payload) == {"1-1", "1-2"}
    assert cli_record.accessions_in_company_facts({"facts": "nope"}) == set()


def test_trim_submissions_page_keeps_wanted_rows_across_every_column() -> None:
    page = {
        "accessionNumber": ["a", "b", "c"],
        "acceptanceDateTime": ["ta", "tb", "tc"],
        "form": ["10-K", "8-K", "10-Q"],
        "filingCount": 3,
    }
    out = cli_record.trim_submissions_page(page, accessions=["c", "a", "zzz"])
    assert out == {
        "accessionNumber": ["a", "c"],
        "acceptanceDateTime": ["ta", "tc"],
        "form": ["10-K", "10-Q"],
        "filingCount": 3,
    }
    assert cli_record.trim_submissions_page({"x": 1}, accessions=["a"]) == {"x": 1}


def test_recorded_at_notes_utc_time_per_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_record, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(cli_record, "_recorded_at", {})
    target = tmp_path / "alpaca" / "x.json"
    target.parent.mkdir()
    cli_record._write_json(target, {"k": "v"}, secrets=[])
    assert list(cli_record._recorded_at) == ["alpaca/x.json"]
    assert cli_record._recorded_at["alpaca/x.json"].endswith("+00:00")
