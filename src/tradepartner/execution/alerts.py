"""Alerts: the `alerts` journal row first, then delivery (Phase 4 spec req 11; plan T57).

The `alerts` table is the source of truth. `Alerter.write` appends the row
**before** any delivery is attempted, so an alert exists even if every channel
fails, then delivers to each channel in `alerts.channels`, journaling one
`alert_deliveries` row per attempt. A channel failure never raises: it is a
failed delivery row, and the caller's status is whatever it was. The emitter
writes the alert before its exception propagates; the row runs in the caller's
transaction (`store.journal.append`), so the caller commits it, and must do so
before re-raising: channels are called before that commit, so a rollback would
leave a delivered alert with no row (and no dedupe record).

Kinds (`ALERT_KINDS`, pinned to the spec's list) and who emits them:

- `run_failed`, `halted`, `stale_data`, `kill_switch`, `reconciliation`,
  `rejection_cap`, `skip_cap`, `missed_run`, `missed_rebalance`, `drawdown`,
  `unspent_cash`: the tracking run and the risk-gated wrapper (T60 to T63f).
  Run-scoped: one alert per (kind, run), `alerts.session` = the run's S.
- `locked`, `no_window`: the run's entry (T63), before any run row exists.
  No run id; one alert per (kind, session), `session` being the calendar session
  containing the instant, or the next one on a non-session day (the caller's).
- `kill_switch_write_failed`: the halt path when the `kill_switch` row itself
  could not be written, so the store cannot be trusted. It never touches the
  store: `deliver_without_store` sends it through the other channels only and
  returns the outcomes instead of journaling them.

Channels: `store` (always; its delivery row records that the alert is in the
table), `macos` (an `osascript` notification), `email` (SMTP to `ALERT_EMAIL_TO`
from the `ALERT_SMTP_*` settings, over STARTTLS; each attempt bounded by
`alerts.delivery_timeout_seconds`; skipped, as a failed row saying
so, unless all four are set). `ALERT_SMTP_HOST` may carry a port
(`smtp.example.com:587`), which `smtplib` parses. No secret value is ever logged
or journaled (see "Secrets" below).

The clock stamps `at`, `known_at` and `ingested_at` (converted to UTC). On the
halt path after a `ClockError` the caller passes `clock_fault=True`: the clock
is never read and every row is stamped with `store.db.utc_now()` (spec
Definitions). If the clock raises or returns a naive value anyway, the stamp
falls back the same way: the alert for a clock fault must still be written.

Secrets: every `SecretStr` value in `Settings` (the Alpaca keys and the
`ALERT_*` values, as `config.secret_values` lists them: stripped, blanks left
out, #417) is masked in the alert message before it is journaled or sent,
and in every delivery error, whatever its case or repr escaping, since an
emitter may pass an exception's text. A failed `osascript` records its stderr,
which carries the cause, masked the same way (#402). SMTP uses STARTTLS with a verifying
context (certificate and hostname) before any credential is sent.
"""

from __future__ import annotations

import re
import smtplib
import ssl
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.message import EmailMessage
from typing import Any

import duckdb

from tradepartner.config import Settings, secret_values
from tradepartner.store import journal
from tradepartner.store.db import utc_now
from tradepartner.store.journal import AlertDeliveryRow, AlertRow

__all__ = [
    "ALERT_KINDS",
    "NON_STORE_KINDS",
    "SESSION_SCOPED_KINDS",
    "Alerter",
    "Delivery",
]

#: Every alert kind, exactly the spec's req 11 list (pinned by a test).
ALERT_KINDS: tuple[str, ...] = (
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
#: Kinds with no run, deduped on (kind, session).
SESSION_SCOPED_KINDS: tuple[str, ...] = ("locked", "no_window")
#: Kinds that never touch the store.
NON_STORE_KINDS: tuple[str, ...] = ("kill_switch_write_failed",)

_MASK = "***"


@dataclass(frozen=True)
class Delivery:
    """One delivery attempt's outcome: journaled as an `alert_deliveries` row by
    `write`, returned as is by `deliver_without_store`."""

    channel: str
    ok: bool
    error: str | None = None


class Alerter:
    """Writes alerts to the journal and delivers them (see the module docstring)."""

    def __init__(
        self,
        settings: Settings,
        conn: duckdb.DuckDBPyConnection,
        clock: Callable[[], datetime],
        *,
        runner: Callable[..., object] = subprocess.run,
        smtp: Callable[..., Any] = smtplib.SMTP,
    ) -> None:
        self._channels: tuple[str, ...] = tuple(settings.alerts.channels)
        self._settings = settings
        self._conn = conn
        self._clock = clock
        self._runner = runner
        self._smtp = smtp
        # Settings are fixed for the Alerter's life, so the masked forms are too.
        self._secret_forms = sorted(
            {form for secret in secret_values(settings) for form in (secret, repr(secret)[1:-1])},
            key=len,
            reverse=True,
        )

    def __repr__(self) -> str:
        return f"Alerter(channels={self._channels!r})"

    def write(
        self,
        kind: str,
        run_id: int | None,
        session: date,
        message: str,
        *,
        clock_fault: bool = False,
    ) -> int:
        """Append the alert (unless its dedupe key already has one) and deliver it;
        return its `alert_id`, the existing one for a duplicate, which is not
        delivered again. Raises `ValueError` for an unknown kind, a
        `kill_switch_write_failed` (use `deliver_without_store`), a run-scoped kind
        without a run or a session-scoped kind with one; store errors propagate
        (the caller then falls back to `deliver_without_store`). Never raises for
        a delivery failure. `clock_fault=True` (the halt path after a
        `ClockError`) stamps every row with `store.db.utc_now()` and never reads
        the clock, which may be returning implausible but well-formed values."""
        _check_kind(kind)
        if kind in NON_STORE_KINDS:
            raise ValueError(f"{kind} never touches the store: use deliver_without_store")
        if kind in SESSION_SCOPED_KINDS and run_id is not None:
            raise ValueError(f"{kind} has no run; got run {run_id}")
        if kind not in SESSION_SCOPED_KINDS and run_id is None:
            raise ValueError(f"{kind} is run-scoped and needs a run id")
        message = self._scrub(message)
        existing = self._existing(kind, run_id, session)
        if existing is not None:
            return existing
        now = self._now(clock_fault)
        alert_id = journal.append(
            self._conn,
            AlertRow(
                run_id=run_id,
                session=session,
                kind=kind,
                message=message,
                at=now,
                known_at=now,
                ingested_at=now,
            ),
        )
        assert alert_id is not None
        for channel in self._channels:
            if channel == "store":
                outcome = Delivery("store", True)
            else:
                outcome = self._deliver(channel, kind, message)
            stamp = self._now(clock_fault)
            journal.append(
                self._conn,
                AlertDeliveryRow(
                    alert_id=alert_id,
                    channel=outcome.channel,
                    at=stamp,
                    ok=outcome.ok,
                    error=outcome.error,
                    known_at=stamp,
                    ingested_at=stamp,
                ),
            )
        return alert_id

    def deliver_without_store(self, kind: str, message: str) -> list[Delivery]:
        """Deliver a `kill_switch_write_failed` alert through every channel but
        `store`, writing nothing to the store, and return the outcomes. Never raises
        for a delivery failure; `ValueError` for any other kind."""
        _check_kind(kind)
        if kind not in NON_STORE_KINDS:
            raise ValueError(f"only {NON_STORE_KINDS} bypass the store; got {kind}")
        message = self._scrub(message)
        return [self._deliver(c, kind, message) for c in self._channels if c != "store"]

    # --- internals ---------------------------------------------------------------------

    def _existing(self, kind: str, run_id: int | None, session: date) -> int | None:
        if run_id is None:
            rows = journal.alerts_for(self._conn, kind=kind, session=session)
            return rows[0].alert_id if rows else None
        journal.require_journal(self._conn)
        row = self._conn.execute(
            "SELECT MIN(alert_id) FROM alerts WHERE kind = ? AND run_id = ?", [kind, run_id]
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def _now(self, clock_fault: bool) -> datetime:
        if clock_fault:
            return utc_now()
        try:
            now = self._clock()
            if not isinstance(now, datetime) or now.utcoffset() is None:
                return utc_now()
            return now.astimezone(UTC)
        except Exception:
            return utc_now()

    def _deliver(self, channel: str, kind: str, message: str) -> Delivery:
        try:
            if channel == "macos":
                self._macos(kind, message)
            elif channel == "email":
                if not self._email_configured():
                    return Delivery(channel, False, "email channel not configured (ALERT_* unset)")
                self._email(kind, message)
            else:
                return Delivery(channel, False, f"unknown channel {channel!r}")
        except Exception as exc:
            return Delivery(channel, False, self._scrub(_describe(exc)))
        return Delivery(channel, True)

    def _macos(self, kind: str, message: str) -> None:
        script = (
            f"display notification {_applescript(message)} "
            f"with title {_applescript(f'TradePartner: {kind}')}"
        )
        self._runner(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            timeout=self._settings.alerts.delivery_timeout_seconds,
        )

    def _email_configured(self) -> bool:
        s = self._settings
        return all((s.alert_smtp_host, s.alert_smtp_user, s.alert_smtp_password, s.alert_email_to))

    def _email(self, kind: str, message: str) -> None:
        s = self._settings
        assert s.alert_smtp_host and s.alert_smtp_user and s.alert_smtp_password
        assert s.alert_email_to
        user = s.alert_smtp_user.get_secret_value()
        email = EmailMessage()
        email["Subject"] = f"TradePartner alert: {kind}"
        email["From"] = user
        email["To"] = s.alert_email_to.get_secret_value()
        email.set_content(message)
        with self._smtp(
            s.alert_smtp_host, timeout=self._settings.alerts.delivery_timeout_seconds
        ) as client:
            client.starttls(context=ssl.create_default_context())
            client.login(user, s.alert_smtp_password.get_secret_value())
            client.send_message(email)

    def _scrub(self, text: str) -> str:
        """`text` with every secret masked, in any case and in its repr-escaped form."""
        for form in self._secret_forms:
            text = re.sub(re.escape(form), _MASK, text, flags=re.IGNORECASE)
        return text


def _check_kind(kind: str) -> None:
    if kind not in ALERT_KINDS:
        raise ValueError(f"unknown alert kind {kind!r}; expected one of {ALERT_KINDS}")


def _describe(exc: Exception) -> str:
    """An exception as a delivery error: its type and text, plus a failed
    subprocess's stderr, which `CalledProcessError`'s text leaves out (#402).
    The caller scrubs it."""
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        text = f"{text} stderr: {str(stderr).strip()}"
    return text


def _applescript(text: str) -> str:
    """`text` as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
