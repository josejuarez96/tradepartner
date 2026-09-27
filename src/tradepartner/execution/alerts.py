"""Alerts: the `alerts` journal row first, then delivery (Phase 4 spec req 11; plan T57).

The `alerts` table is the source of truth. `Alerter.write` appends the row
**before** any delivery is attempted, so an alert exists even if every channel
fails, then delivers to each channel in `alerts.channels`, journaling one
`alert_deliveries` row per attempt. A channel failure never raises: it is a
failed delivery row, and the caller's status is whatever it was. The emitter
writes the alert before its exception propagates; the row runs in the caller's
transaction (`store.journal.append`), so the caller commits it.

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
from the `ALERT_SMTP_*` settings, over STARTTLS; skipped, as a failed row saying
so, unless all four are set). `ALERT_SMTP_HOST` may carry a port
(`smtp.example.com:587`), which `smtplib` parses. No secret value is ever logged
or journaled: every delivery error has the configured secrets masked.

The clock stamps `at`, `known_at` and `ingested_at`. If it raises or returns a
naive value, the stamp falls back to `store.db.utc_now()`, as the halt path's
rows do after a `ClockError` (spec Definitions): the alert for a clock fault
must still be written.
"""

from __future__ import annotations

import smtplib
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from typing import Any

import duckdb

from tradepartner.config import Settings
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

#: Upper bound on one `osascript` or SMTP attempt, so a stuck channel cannot hold
#: the run. Not a trading limit; T47 left no config key for it (an open question on issue #310).
DELIVERY_TIMEOUT_SECONDS = 30.0

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

    def __repr__(self) -> str:
        return f"Alerter(channels={self._channels!r})"

    def write(self, kind: str, run_id: int | None, session: date, message: str) -> int:
        """Append the alert (unless its dedupe key already has one) and deliver it;
        return its `alert_id`, the existing one for a duplicate, which is not
        delivered again. Raises `ValueError` for an unknown kind, a
        `kill_switch_write_failed` (use `deliver_without_store`), a run-scoped kind
        without a run or a session-scoped kind with one; store errors propagate
        (the caller then falls back to `deliver_without_store`). Never raises for
        a delivery failure."""
        _check_kind(kind)
        if kind in NON_STORE_KINDS:
            raise ValueError(f"{kind} never touches the store: use deliver_without_store")
        if kind in SESSION_SCOPED_KINDS and run_id is not None:
            raise ValueError(f"{kind} has no run; got run {run_id}")
        if kind not in SESSION_SCOPED_KINDS and run_id is None:
            raise ValueError(f"{kind} is run-scoped and needs a run id")
        existing = self._existing(kind, run_id, session)
        if existing is not None:
            return existing
        now = self._now()
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
            stamp = self._now()
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

    def _now(self) -> datetime:
        try:
            now = self._clock()
        except Exception:
            return utc_now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return utc_now()
        return now

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
            return Delivery(channel, False, self._scrub(f"{type(exc).__name__}: {exc}"))
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
            timeout=DELIVERY_TIMEOUT_SECONDS,
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
        with self._smtp(s.alert_smtp_host, timeout=DELIVERY_TIMEOUT_SECONDS) as client:
            client.starttls()
            client.login(user, s.alert_smtp_password.get_secret_value())
            client.send_message(email)

    def _secrets(self) -> Sequence[str]:
        s = self._settings
        values = (s.alert_smtp_user, s.alert_smtp_password, s.alert_email_to)
        return [v.get_secret_value() for v in values if v is not None and v.get_secret_value()]

    def _scrub(self, text: str) -> str:
        for secret in sorted(self._secrets(), key=len, reverse=True):
            text = text.replace(secret, _MASK)
        return text


def _check_kind(kind: str) -> None:
    if kind not in ALERT_KINDS:
        raise ValueError(f"unknown alert kind {kind!r}; expected one of {ALERT_KINDS}")


def _applescript(text: str) -> str:
    """`text` as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
