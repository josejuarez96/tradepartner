"""Tests for `execution.alerts` (Phase 4 plan T57, spec req 11).

The `alerts` row exists before any delivery call (inspected from a fake channel);
both dedupe rules; a raising channel leaves a failed delivery row and the caller's
status unchanged; `osascript` and SMTP receive the message through fakes; no secret
in a delivery error; the non-store path writes nothing to the store; every kind in
the spec's list is accepted and any other refused.
"""

from __future__ import annotations

import re
import ssl
import subprocess
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any, ClassVar

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.execution import alerts
from tradepartner.execution.alerts import ALERT_KINDS, Alerter
from tradepartner.store import schema

_NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
_SESSION = date(2026, 10, 1)
_SECRETS = {
    "alert_smtp_host": "smtp.example.test:587",
    "alert_smtp_user": "owner-login@example.test",
    "alert_smtp_password": "hunter2-very-secret",
    "alert_email_to": "owner-inbox@example.test",
}

#: The spec's req 11 list, typed out again so a change to either side fails here.
SPEC_KINDS = (
    "run_failed",
    "halted",
    "stale_data",
    "kill_switch",
    "kill_switch_write_failed",
    "reconciliation",
    "rejection_cap",
    "skip_cap",
    "missed_run",
    "missed_rebalance",
    "drawdown",
    "unspent_cash",
    "locked",
    "no_window",
)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


_TIMEOUT = 7.5


def _settings(channels: Sequence[str] = ("store",), **secrets: str) -> Settings:
    alerts_config = {"channels": list(channels), "delivery_timeout_seconds": _TIMEOUT}
    return Settings(_env_file=None, alerts=alerts_config, **secrets)  # type: ignore[call-arg]


class FakeRunner:
    """Stands in for `subprocess.run`: records each call, optionally raises, and can
    look at the store when called."""

    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.alerts_seen: list[int] = []
        self.conn, self.fail = conn, fail

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(args)
        if self.conn is not None:
            (count,) = self.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()  # type: ignore[misc]
            self.alerts_seen.append(count)
        if self.fail:
            raise subprocess.CalledProcessError(1, args, stderr=b"osascript: boom")
        assert kwargs.get("check") is True and kwargs.get("timeout") == _TIMEOUT
        return subprocess.CompletedProcess(args, 0)


class FakeSMTP:
    """Stands in for `smtplib.SMTP`; records what was sent."""

    instances: ClassVar[list[FakeSMTP]] = []
    fail_login_with: ClassVar[Exception | None] = None

    def __init__(self, host: str, timeout: float) -> None:
        self.host, self.timeout = host, timeout
        self.tls = False
        self.logins: list[tuple[str, str]] = []
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self, *, context: ssl.SSLContext | None = None) -> None:
        assert context is not None, "STARTTLS must use a verifying context"
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        self.tls = True

    def login(self, user: str, password: str) -> None:
        assert self.tls, "credentials must never be sent before STARTTLS"
        if FakeSMTP.fail_login_with is not None:
            raise FakeSMTP.fail_login_with
        self.logins.append((user, password))

    def send_message(self, message: EmailMessage) -> None:
        assert self.tls, "credentials must never be sent before STARTTLS"
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_smtp() -> Iterator[None]:
    FakeSMTP.instances = []
    FakeSMTP.fail_login_with = None
    yield


def _alerter(conn: duckdb.DuckDBPyConnection, settings: Settings, **kwargs: Any) -> Alerter:
    kwargs.setdefault("runner", FakeRunner())
    kwargs.setdefault("smtp", FakeSMTP)
    return Alerter(settings, conn, lambda: _NOW, **kwargs)


def _deliveries(conn: duckdb.DuckDBPyConnection) -> list[tuple[int, str, bool, str | None]]:
    return conn.execute(
        "SELECT alert_id, channel, ok, error FROM alert_deliveries ORDER BY alert_id, channel"
    ).fetchall()


# --- kinds ---------------------------------------------------------------------------------


def test_the_kind_set_is_the_spec_list() -> None:
    assert ALERT_KINDS == SPEC_KINDS


@pytest.mark.parametrize("kind", SPEC_KINDS)
def test_every_spec_kind_is_accepted_by_its_path(
    conn: duckdb.DuckDBPyConnection, kind: str
) -> None:
    alerter = _alerter(conn, _settings())
    if kind == "kill_switch_write_failed":
        alerter.deliver_without_store(kind, "the switch row could not be written")
    elif kind in ("locked", "no_window"):
        assert alerter.write(kind, None, _SESSION, "m") is not None
    else:
        assert alerter.write(kind, 7, _SESSION, "m") is not None


@pytest.mark.parametrize("kind", ["run_failure", "", "HALTED", "email"])
def test_any_other_kind_is_refused(conn: duckdb.DuckDBPyConnection, kind: str) -> None:
    alerter = _alerter(conn, _settings())
    with pytest.raises(ValueError, match="kind"):
        alerter.write(kind, 7, _SESSION, "m")
    with pytest.raises(ValueError, match="kind"):
        alerter.deliver_without_store(kind, "m")
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone() == (0,)


def test_each_path_refuses_the_other_paths_kinds(conn: duckdb.DuckDBPyConnection) -> None:
    alerter = _alerter(conn, _settings())
    with pytest.raises(ValueError, match="deliver_without_store"):
        alerter.write("kill_switch_write_failed", 7, _SESSION, "m")
    with pytest.raises(ValueError, match="kill_switch_write_failed"):
        alerter.deliver_without_store("halted", "m")
    with pytest.raises(ValueError, match="run"):
        alerter.write("halted", None, _SESSION, "m")
    with pytest.raises(ValueError, match="run"):
        alerter.write("no_window", 7, _SESSION, "m")


# --- ordering and dedupe ---------------------------------------------------------------------


def test_the_row_exists_before_any_delivery_call(conn: duckdb.DuckDBPyConnection) -> None:
    runner = FakeRunner(conn)
    alerter = _alerter(conn, _settings(["store", "macos"]), runner=runner)
    alerter.write("halted", 7, _SESSION, "run 7 halted")
    assert runner.alerts_seen == [1]
    ((alert_id, kind, run_id, session, message, at, known_at),) = conn.execute(
        'SELECT alert_id, kind, run_id, session, message, "at", known_at FROM alerts'
    ).fetchall()
    assert (kind, run_id, session, message) == ("halted", 7, _SESSION, "run 7 halted")
    assert at == known_at == _NOW
    assert _deliveries(conn) == [(alert_id, "macos", True, None), (alert_id, "store", True, None)]


def test_run_scoped_kinds_dedupe_on_kind_and_run(conn: duckdb.DuckDBPyConnection) -> None:
    runner = FakeRunner()
    alerter = _alerter(conn, _settings(["store", "macos"]), runner=runner)
    first = alerter.write("halted", 7, _SESSION, "a")
    assert alerter.write("halted", 7, _SESSION, "again") == first
    assert alerter.write("halted", 8, _SESSION, "b") != first
    assert alerter.write("drawdown", 7, _SESSION, "c") != first
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone() == (3,)
    assert len(runner.calls) == 3  # the duplicate is not delivered again


def test_no_window_and_locked_dedupe_on_kind_and_session(conn: duckdb.DuckDBPyConnection) -> None:
    alerter = _alerter(conn, _settings())
    first = alerter.write("no_window", None, _SESSION, "a")
    assert alerter.write("no_window", None, _SESSION, "b") == first
    assert alerter.write("no_window", None, date(2026, 10, 2), "c") != first
    assert alerter.write("locked", None, _SESSION, "d") != first
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone() == (3,)


# --- channels ------------------------------------------------------------------------------


def test_a_raising_channel_leaves_a_failed_row_and_never_raises(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    alerter = _alerter(conn, _settings(["store", "macos"]), runner=FakeRunner(fail=True))
    alert_id = alerter.write("halted", 7, _SESSION, "m")
    ((_, channel, ok, error), store_row) = [d for d in _deliveries(conn) if d[1] == "macos"] + [
        d for d in _deliveries(conn) if d[1] == "store"
    ]
    assert (channel, ok) == ("macos", False)
    assert error is not None and "CalledProcessError" in error
    assert store_row == (alert_id, "store", True, None)


def test_osascript_receives_the_message_escaped(conn: duckdb.DuckDBPyConnection) -> None:
    runner = FakeRunner()
    alerter = _alerter(conn, _settings(["store", "macos"]), runner=runner)
    alerter.write("halted", 7, _SESSION, 'run 7 said "stop" \\ now')
    ((program, flag, script),) = runner.calls
    assert (program, flag) == ("osascript", "-e")
    assert 'display notification "run 7 said \\"stop\\" \\\\ now"' in script
    assert "halted" in script


def test_email_is_sent_over_starttls_to_the_configured_address(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    alerter = _alerter(conn, _settings(["store", "email"], **_SECRETS))
    alert_id = alerter.write("drawdown", 7, _SESSION, "equity below the peak")
    (smtp,) = FakeSMTP.instances
    assert smtp.host == "smtp.example.test:587" and smtp.tls and smtp.timeout == _TIMEOUT
    assert smtp.logins == [("owner-login@example.test", "hunter2-very-secret")]
    (message,) = smtp.sent
    assert message["To"] == "owner-inbox@example.test"
    assert "drawdown" in message["Subject"]
    assert "equity below the peak" in message.get_content()
    assert (alert_id, "email", True, None) in _deliveries(conn)


def test_email_is_skipped_when_unset(conn: duckdb.DuckDBPyConnection) -> None:
    partial = {k: v for k, v in _SECRETS.items() if k != "alert_smtp_password"}
    alerter = _alerter(conn, _settings(["store", "email"], **partial))
    alert_id = alerter.write("drawdown", 7, _SESSION, "m")
    assert FakeSMTP.instances == []
    ((_, _, ok, error),) = [d for d in _deliveries(conn) if d[1] == "email"]
    assert not ok and error is not None and "not configured" in error
    assert (alert_id, "store", True, None) in _deliveries(conn)


def test_no_secret_in_a_delivery_error(conn: duckdb.DuckDBPyConnection) -> None:
    FakeSMTP.fail_login_with = RuntimeError(
        f"535 auth failed for {_SECRETS['alert_smtp_user']} with {_SECRETS['alert_smtp_password']} "
        f"to {_SECRETS['alert_email_to']}"
    )
    alerter = _alerter(conn, _settings(["store", "email"], **_SECRETS))
    alerter.write("drawdown", 7, _SESSION, "m")
    ((_, _, ok, error),) = [d for d in _deliveries(conn) if d[1] == "email"]
    assert not ok and error is not None and "535 auth failed" in error
    for secret in ("owner-login@example.test", "hunter2-very-secret", "owner-inbox@example.test"):
        assert secret not in error
    assert "hunter2" not in repr(alerter)


# --- the non-store path ------------------------------------------------------------------------


def test_deliver_without_store_writes_nothing_to_the_store(conn: duckdb.DuckDBPyConnection) -> None:
    runner = FakeRunner()
    alerter = _alerter(conn, _settings(["store", "macos", "email"], **_SECRETS), runner=runner)
    outcomes = alerter.deliver_without_store("kill_switch_write_failed", "the switch row failed")
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone() == (0,)
    assert len(runner.calls) == 1 and len(FakeSMTP.instances) == 1
    assert [(o.channel, o.ok) for o in outcomes] == [("macos", True), ("email", True)]


def test_deliver_without_store_never_raises_and_works_on_a_closed_store() -> None:
    conn = duckdb.connect(":memory:")
    conn.close()
    alerter = Alerter(
        _settings(["store", "macos"]),
        conn,
        lambda: _NOW,
        runner=FakeRunner(fail=True),
        smtp=FakeSMTP,
    )
    (outcome,) = alerter.deliver_without_store("kill_switch_write_failed", "m")
    assert outcome.channel == "macos" and not outcome.ok


def test_a_raising_clock_still_writes_the_alert(conn: duckdb.DuckDBPyConnection) -> None:
    """The alert for a `ClockError` halt must still land: the stamp falls back to
    `store.db.utc_now()`, as the halt path's rows do (spec Definitions)."""

    def broken() -> datetime:
        raise OSError("clock gone")

    alerter = Alerter(_settings(), conn, broken, runner=FakeRunner(), smtp=FakeSMTP)
    assert alerter.write("halted", 7, _SESSION, "m") is not None


def test_the_module_has_no_secret_logging() -> None:
    """No `print` or logging call in the module takes a settings secret."""
    text = alerts.__loader__.get_source(alerts.__name__)  # type: ignore[union-attr]
    assert text is not None
    assert "get_secret_value()" in text  # secrets are unwrapped only where used
    for line in text.splitlines():
        if "get_secret_value" in line:
            assert not re.search(r"\b(logging|logger|log\.|print)\b|\blog\(", line)


# --- stamps --------------------------------------------------------------------------------


def _stamps(conn: duckdb.DuckDBPyConnection) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    alert_rows = conn.execute('SELECT "at", known_at, ingested_at FROM alerts').fetchall()
    delivery_rows = conn.execute(
        'SELECT "at", known_at, ingested_at FROM alert_deliveries'
    ).fetchall()
    return alert_rows, delivery_rows


def test_rows_are_stamped_with_the_clock_reading_in_utc(conn: duckdb.DuckDBPyConnection) -> None:
    eastern = timezone(timedelta(hours=-4))
    alerter = Alerter(
        _settings(["store", "macos"]),
        conn,
        lambda: _NOW.astimezone(eastern),
        runner=FakeRunner(),
        smtp=FakeSMTP,
    )
    alerter.write("halted", 7, _SESSION, "m")
    alert_rows, delivery_rows = _stamps(conn)
    assert alert_rows == [(_NOW, _NOW, _NOW)]
    assert delivery_rows == [(_NOW, _NOW, _NOW)] * 2


def test_a_clock_fault_never_reads_the_clock(conn: duckdb.DuckDBPyConnection) -> None:
    """After a `ClockError` the clock may return well-formed nonsense (a year
    ahead, say): with `clock_fault=True` it is never called and every stamp is
    `utc_now()`."""
    calls: list[datetime] = []
    ahead = _NOW + timedelta(days=365)

    def implausible() -> datetime:
        calls.append(ahead)
        return ahead

    alerter = Alerter(
        _settings(["store", "macos"]), conn, implausible, runner=FakeRunner(), smtp=FakeSMTP
    )
    before = datetime.now(UTC)
    alerter.write("halted", 7, _SESSION, "m", clock_fault=True)
    after = datetime.now(UTC)
    assert calls == []
    alert_rows, delivery_rows = _stamps(conn)
    for row in alert_rows + delivery_rows:
        assert all(before <= stamp <= after for stamp in row)


@pytest.mark.parametrize("reading", ["raises", "naive"])
def test_a_broken_clock_falls_back_to_utc_now(
    conn: duckdb.DuckDBPyConnection, reading: str
) -> None:
    def clock() -> datetime:
        if reading == "raises":
            raise OSError("clock gone")
        return datetime(2026, 10, 1, 21, 0)  # noqa: DTZ001 (a naive reading on purpose)

    before = datetime.now(UTC)
    Alerter(_settings(), conn, clock, runner=FakeRunner(), smtp=FakeSMTP).write(
        "halted", 7, _SESSION, "m"
    )
    ((at, known_at, ingested_at),), _ = _stamps(conn)
    assert at.tzinfo is not None and before <= at == known_at == ingested_at <= datetime.now(UTC)


# --- secrets in messages ---------------------------------------------------------------------


def test_secrets_are_masked_in_the_message_before_it_is_stored_or_sent(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """An emitter may pass an exception's text; a key in it never reaches the
    store, the notification or the email."""
    runner = FakeRunner()
    alerter = _alerter(
        conn,
        _settings(["store", "macos", "email"], alpaca_paper_api_key="PKFAKEKEY123", **_SECRETS),
        runner=runner,
    )
    alerter.write("run_failed", 7, _SESSION, "401 for key pkfakekey123 and HUNTER2-VERY-SECRET")
    ((message,),) = conn.execute("SELECT message FROM alerts").fetchall()
    ((_, _, script),) = runner.calls
    (smtp,) = FakeSMTP.instances
    for text in (message, script, smtp.sent[0].get_content()):
        assert "pkfakekey123" not in text.lower() and "hunter2" not in text.lower()
        assert "401 for key ***" in text
    outcomes = alerter.deliver_without_store("kill_switch_write_failed", "key PKFAKEKEY123")
    assert all(o.ok for o in outcomes)
    assert "PKFAKEKEY123" not in runner.calls[-1][2]


def test_a_secret_is_masked_in_errors_in_any_case_or_repr_form(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    secrets = {**_SECRETS, "alert_smtp_password": "pa'ss\\word"}
    FakeSMTP.fail_login_with = RuntimeError(
        f"refused {secrets['alert_email_to'].upper()} {secrets['alert_smtp_password']!r}"
    )
    alerter = _alerter(conn, _settings(["store", "email"], **secrets))
    alerter.write("drawdown", 7, _SESSION, "m")
    ((_, _, ok, error),) = [d for d in _deliveries(conn) if d[1] == "email"]
    assert not ok and error is not None and "refused" in error
    assert "OWNER-INBOX" not in error and "word" not in error
