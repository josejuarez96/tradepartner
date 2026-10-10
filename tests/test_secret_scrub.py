"""Secret scrubbing added in #334: the paper recorder's stderr error and the
SMTP AUTH base64 forms of the alert credentials (the ingest run-message
redaction test lives in `test_ingest.py`)."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from test_cli_record import (
    IN_HOURS,
    NON_FRACTIONABLE,
    _paper_settings,
    _raw_on,
    _redirect_fixtures,
    _ScriptedPaperClient,
)

from tradepartner import cli_record
from tradepartner.config import Settings


def test_paper_script_error_on_stderr_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _redirect_fixtures(tmp_path, monkeypatch)
    settings = _paper_settings()
    assert settings.alpaca_paper_api_key is not None
    key = settings.alpaca_paper_api_key.get_secret_value()

    def leaky(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise cli_record.PaperRecordingError(f"broker echoed {key} in its refusal")

    monkeypatch.setattr(cli_record, "_record_paper", leaky)
    client = _ScriptedPaperClient()
    assert cli_record._run_paper(_raw_on(client), settings, NON_FRACTIONABLE, IN_HOURS) == 1
    err = capsys.readouterr().err
    assert "broker echoed" in err
    assert key not in err


def test_smtp_login_forms_are_configured_and_scrubbed() -> None:
    user, password = "relay-login-gamma", "correct horse battery staple 99"
    settings = Settings(_env_file=None, alert_smtp_user=user, alert_smtp_password=password)
    plain = base64.b64encode(f"\0{user}\0{password}".encode()).decode()
    login_user = base64.b64encode(user.encode()).decode()
    login_password = base64.b64encode(password.encode()).decode()
    secrets = cli_record._configured_secrets(settings)
    for form in (plain, login_user, login_password):
        assert form in secrets
    transcript = f"send: 'AUTH PLAIN {plain}'\nsend: '{login_user}'\nsend: '{login_password}'\n"
    scrubbed, _ = cli_record.scrub_text(transcript, secrets=secrets)
    for form in (plain, login_user, login_password):
        assert form not in scrubbed


def test_a_failed_flatten_prints_a_scrubbed_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _paper_settings()
    assert settings.alpaca_paper_api_key is not None
    key = settings.alpaca_paper_api_key.get_secret_value()

    def leaky(*_args: object, **_kwargs: object) -> list[object]:
        raise cli_record.PaperRecordingError(f"flatten refused, broker echoed {key}")

    monkeypatch.setattr(cli_record, "_paper_flatten", leaky)
    client = _ScriptedPaperClient()
    with pytest.raises(cli_record.PaperRecordingError):
        cli_record._paper_finish(_raw_on(client), settings, iter(["x"]), {})
    err = capsys.readouterr().err
    assert "NOT FLAT?" in err and "broker echoed" in err
    assert key not in err


@pytest.mark.parametrize("source", ["settings", "environment"])
def test_a_non_main_books_pair_is_scrubbed_raw_and_as_basic_auth(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1405: a book other than `main` (T153's `ALPACA_PAPER_BOOKS__<TOKEN>__*`)
    has its pair's raw halves and its HTTP Basic `base64("key:secret")` form
    scrubbed from a recording, under a field name that is not itself sensitive."""
    key, secret = "fake-daily-book-key-0001", "fake-daily-book-secret-0002"
    if source == "environment":
        monkeypatch.setenv("ALPACA_PAPER_BOOKS__DAILY__API_KEY", key)
        monkeypatch.setenv("ALPACA_PAPER_BOOKS__DAILY__API_SECRET", secret)
        settings = Settings(_env_file=None)
    else:
        settings = Settings(
            _env_file=None,
            alpaca_paper_books={"daily": {"api_key": key, "api_secret": secret}},
        )
    assert "daily" in settings.alpaca_paper_books
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    secrets = cli_record._configured_secrets(settings)
    assert basic in secrets
    payload = {"echo": f"Basic {basic}", "note": f"key {key} secret {secret}"}
    scrubbed, _ = cli_record.scrub_json(payload, secrets=secrets)
    text = str(scrubbed)
    for value in (key, secret, basic):
        assert value not in text
