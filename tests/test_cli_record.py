"""Tests for `cli_record`'s missing/blank-secret handling and filing-document
helpers (T2 review round 2, safety-reviewer MUST FIX).

No network, no `.env`: `main()` must refuse before ever calling
`adapters.alpaca_raw`/`.edgar_raw` when any secret is missing or blank,
and must never leak a configured secret's value into its own error
message.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alpaca.common.exceptions import APIError

from tradepartner import cli_record
from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingRaw
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


# --- the `paper` target (T48) -------------------------------------------------

PAPER_KEY = "PKPAPERFAKE1234567890"  # gitleaks:allow
PAPER_SECRET = "paperSecretFake1234567890abcdefghij"  # gitleaks:allow
ACCOUNT_ID = "0b6f3c1e-1111-4222-8333-944455556666"
ACCOUNT_NUMBER = "PA3FAKE12345"
NON_FRACTIONABLE = "NFX"
IN_HOURS = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)  # a Monday session, 11:00 New York


def _paper_settings(**overrides: str | None) -> Settings:
    values: dict[str, str | None] = {
        "alpaca_paper_api_key": PAPER_KEY,
        "alpaca_paper_api_secret": PAPER_SECRET,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _redirect_fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli_record, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(cli_record, "ALPACA_PAPER_FIXTURES_DIR", tmp_path / "alpaca" / "paper")
    monkeypatch.setattr(cli_record, "RECORDED_AT_FILE", tmp_path / "recorded_at.json")
    monkeypatch.setattr(cli_record, "_recorded_at", {})
    return tmp_path / "alpaca" / "paper"


@pytest.mark.parametrize("missing", ["alpaca_paper_api_key", "alpaca_paper_api_secret"])
def test_paper_target_refused_without_paper_keys_writing_nothing(
    missing: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paper_dir = _redirect_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(cli_record, "get_settings", lambda: _paper_settings(**{missing: None}))

    assert cli_record.main(["paper", NON_FRACTIONABLE]) == 1

    err = capsys.readouterr().err
    assert missing.upper() in err
    assert PAPER_KEY not in err and PAPER_SECRET not in err
    assert not paper_dir.exists() and list(tmp_path.iterdir()) == []


def test_paper_target_refused_when_the_paper_guard_was_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redirect_fixtures(tmp_path, monkeypatch)
    settings = _paper_settings()
    bypassed = settings.model_copy(
        update={"alpaca": settings.alpaca.model_copy(update={"paper": False})}
    )
    monkeypatch.setattr(cli_record, "get_settings", lambda: bypassed)

    assert cli_record.main(["paper", NON_FRACTIONABLE]) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("argv", [["papr"], ["paper"], ["paper", "A", "B"], ["fsn", "x"]])
def test_unknown_target_or_bad_paper_arguments_exit_2(argv: list[str]) -> None:
    assert cli_record.main(argv) == 2


class _ScriptedPaperClient:
    """A paper account in memory: market orders fill at once at 10, limit orders
    rest, a sell above the held quantity or a reused `client_order_id` is refused."""

    def __init__(self, positions: dict[str, float] | None = None) -> None:
        self.positions = dict(positions or {})
        self.orders: dict[str, dict[str, Any]] = {}
        self.submitted: list[str] = []

    @staticmethod
    def _refuse(status: int, message: str) -> APIError:
        http_error = SimpleNamespace(response=SimpleNamespace(status_code=status))
        return APIError(json.dumps({"code": status, "message": message}), http_error)

    def submit_order(self, order_data: Any) -> Any:
        cid, symbol = order_data.client_order_id, order_data.symbol
        self.submitted.append(cid)
        if cid in self.orders:
            raise self._refuse(422, "client_order_id must be unique")
        qty = order_data.qty if order_data.qty is not None else order_data.notional / 10
        if order_data.side.value == "sell" and qty > self.positions.get(symbol, 0) + 1e-9:
            raise self._refuse(403, "insufficient qty available for order")
        order = {"id": f"id-{cid}", "client_order_id": cid, "symbol": symbol, "status": "new"}
        if order_data.type.value == "market":
            sign = 1 if order_data.side.value == "buy" else -1
            self.positions[symbol] = self.positions.get(symbol, 0) + sign * qty
            if abs(self.positions[symbol]) < 1e-9:
                del self.positions[symbol]
            order.update(status="filled", filled_qty=str(qty), filled_avg_price="10")
        self.orders[cid] = order
        return dict(order)

    def cancel_order_by_id(self, order_id: str) -> None:
        for order in self.orders.values():
            if order["id"] == order_id and order["status"] == "new":
                order["status"] = "canceled"

    def get_order_by_client_id(self, client_id: str) -> Any:
        return dict(self.orders[client_id])

    def get_orders(self, filter: Any = None) -> Any:
        return [dict(o) for o in self.orders.values() if o["status"] == "new"]

    def get_all_positions(self) -> Any:
        return [
            {"symbol": s, "qty": str(q), "account_id": ACCOUNT_ID}
            for s, q in self.positions.items()
        ]

    def get_account(self) -> Any:
        return {"id": ACCOUNT_ID, "account_number": ACCOUNT_NUMBER, "cash": "100000"}

    def get_asset(self, symbol_or_asset_id: str) -> Any:
        fractionable = symbol_or_asset_id != NON_FRACTIONABLE
        return {"symbol": symbol_or_asset_id, "tradable": True, "fractionable": fractionable}

    def get(self, path: str, data: Any = None) -> Any:
        return [{"id": "act-1", "activity_type": "FILL", "note": f"acct {ACCOUNT_ID}"}]


class _FakeClock:
    """Advances only when slept on, so pacing and polling cost no real time."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _raw_on(client: _ScriptedPaperClient) -> AlpacaTradingRaw:
    return AlpacaTradingRaw(_paper_settings(), client=client, clock=_FakeClock())


def test_paper_script_ends_flat_and_writes_scrubbed_recordings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_dir = _redirect_fixtures(tmp_path, monkeypatch)
    (tmp_path / "recorded_at.json").write_text('{"alpaca/daily_bars.json": "2026-09-25"}')
    client = _ScriptedPaperClient()

    assert (
        cli_record._run_paper(_raw_on(client), _paper_settings(), NON_FRACTIONABLE, IN_HOURS) == 0
    )

    assert client.positions == {} and client.get_orders() == []
    written = {path.stem: path.read_text() for path in paper_dir.glob("*.json")}
    assert {"buy_fractional", "sell_above_held", "duplicate_client_order_id", "flatten"} <= set(
        written
    )
    assert json.loads(written["sell_above_held"])["error"]["status_code"] == 403
    assert json.loads(written["duplicate_client_order_id"])["error"]["status_code"] == 422
    assert json.loads(written["resting_cancelled"])[-1]["status"] == "canceled"
    for text in written.values():
        for secret in (ACCOUNT_ID, ACCOUNT_NUMBER, PAPER_KEY, PAPER_SECRET):
            assert secret not in text
    recorded_at = json.loads((tmp_path / "recorded_at.json").read_text())
    assert "alpaca/daily_bars.json" in recorded_at and "alpaca/paper/flatten.json" in recorded_at


def test_paper_script_refuses_a_non_flat_account_before_any_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paper_dir = _redirect_fixtures(tmp_path, monkeypatch)
    client = _ScriptedPaperClient(positions={"KO": 2.0})

    assert (
        cli_record._run_paper(_raw_on(client), _paper_settings(), NON_FRACTIONABLE, IN_HOURS) == 1
    )
    assert client.submitted == [] and not paper_dir.exists()


def test_paper_script_refuses_a_fractionable_symbol_as_non_fractionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redirect_fixtures(tmp_path, monkeypatch)
    client = _ScriptedPaperClient()

    assert cli_record._run_paper(_raw_on(client), _paper_settings(), "SPY", IN_HOURS) == 1
    assert client.submitted == []


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 9, 28, 12, 0, tzinfo=UTC),  # before the open
        datetime(2026, 9, 28, 19, 45, tzinfo=UTC),  # 15 minutes before the close
        datetime(2026, 9, 27, 15, 0, tzinfo=UTC),  # a Sunday
    ],
)
def test_paper_script_refuses_outside_regular_hours(
    now: datetime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _redirect_fixtures(tmp_path, monkeypatch)
    client = _ScriptedPaperClient()

    assert cli_record._run_paper(_raw_on(client), _paper_settings(), NON_FRACTIONABLE, now) == 1
    assert client.submitted == []


def test_paper_script_reports_not_flat_when_a_flattening_sell_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paper_dir = _redirect_fixtures(tmp_path, monkeypatch)
    client = _ScriptedPaperClient()
    submit = client.submit_order

    def refuse_non_fractionable_sells(order_data: Any) -> Any:
        if order_data.symbol == NON_FRACTIONABLE and order_data.side.value == "sell":
            raise client._refuse(403, "fractional orders not supported")
        return submit(order_data)

    client.submit_order = refuse_non_fractionable_sells  # type: ignore[method-assign]

    assert (
        cli_record._run_paper(_raw_on(client), _paper_settings(), NON_FRACTIONABLE, IN_HOURS) == 1
    )
    assert f"NOT FLAT: {NON_FRACTIONABLE}" in capsys.readouterr().err
    assert not paper_dir.exists()


# --- #320: the Phase 4 alert secrets are scrubbed too ------------------------

_ALERT_SECRETS = {
    "alert_smtp_user": "relay-login-gamma",
    "alert_smtp_password": "correct horse battery staple 99",
    "alert_email_to": "owner.alerts@example.org",
}


@pytest.mark.parametrize("field", sorted(_ALERT_SECRETS))
def test_alert_secrets_are_configured_and_scrubbed(field: str) -> None:
    from tradepartner import cli

    value = _ALERT_SECRETS[field]
    settings = Settings(_env_file=None, **{field: value})
    assert value in cli_record._configured_secrets(settings)

    message = f"sent alert via {value} ok"
    try:
        raise RuntimeError(f"SMTP login failed for {value!r}: 535 rejected")
    except RuntimeError as exc:
        exception_text = f"{type(exc).__name__}: {exc}"
    for text in (message, exception_text):
        scrubbed, count = cli_record.scrub_text(
            text, secrets=cli_record._configured_secrets(settings)
        )
        assert value not in scrubbed
        assert count >= 1
        assert value not in cli._scrubbed(text, settings)


def test_blank_alert_secrets_are_not_configured() -> None:
    settings = Settings(
        _env_file=None,
        alert_smtp_user="  ",
        alert_smtp_password="",
        alert_email_to=None,
        alpaca_api_key=None,
        alpaca_api_secret=None,
        alpaca_paper_api_key=None,
        alpaca_paper_api_secret=None,
        sec_edgar_user_agent=None,
    )
    assert cli_record._configured_secrets(settings) == []
