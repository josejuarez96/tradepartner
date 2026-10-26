"""Off-Mac heartbeat: a dead-man's-switch ping when a scheduled job finishes (#2155).

A Mac that is off, asleep or off the network cannot send its own alerts, so on
2026-10-21..25 both books sat halted for three days with no one told. A check on
an outside service (healthchecks.io convention) closes that gap: `paper run` and
`ingest` ping the check's URL when they end, the plain URL on success and
`<url>/fail` on a failure, and the service mails the owner when a ping is late
or failed. Missing pings are what the service alerts on, so this module only
ever sends.

The ping is best effort and must never change the job: it has a short timeout
(`alerts.heartbeat_timeout_seconds`), swallows every error, and a failed ping
logs one warning naming the job and the error type. The URL is the credential,
so it is never logged and httpx's exception text (which can carry the URL) is
never used. No URL configured means no request at all.
"""

from __future__ import annotations

import logging
import threading
from typing import Literal

import httpx

from tradepartner.config import Settings

LOG = logging.getLogger(__name__)

Job = Literal["paper", "ingest"]


def _get(url: str, timeout: float) -> httpx.Response:
    """The one HTTP call (a seam for tests; no redirects, so no hop to another host)."""
    return httpx.get(url, timeout=timeout, follow_redirects=False)


class _PingRefused(Exception):
    """A local refusal or bad answer; its text is ours and safe to log."""


def _bounded_get(url: str, timeout: float) -> httpx.Response:
    """`_get` under one overall deadline (httpx's timeout is per phase and DNS is
    unbounded): a daemon thread, abandoned if it outlives `timeout`."""
    box: list[httpx.Response | BaseException] = []

    def work() -> None:
        try:
            box.append(_get(url, timeout))
        except BaseException as exc:
            box.append(exc)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout)
    if not box:
        raise TimeoutError("overall deadline")
    if isinstance(box[0], BaseException):
        raise box[0]
    return box[0]


def heartbeat_url(settings: Settings, job: Job) -> str | None:
    """The job's configured check URL, stripped, or None when unset or blank."""
    secret = (
        settings.alert_heartbeat_url_paper
        if job == "paper"
        else settings.alert_heartbeat_url_ingest
    )
    if secret is None:
        return None
    return secret.get_secret_value().strip() or None


def ping(settings: Settings, job: Job, *, ok: bool) -> bool:
    """Ping the job's check: the URL when `ok`, `<url>/fail` otherwise.

    Returns True when the service answered 2xx, False on no URL or any failure.
    Never raises and never logs the URL.
    """
    url = heartbeat_url(settings, job)
    if url is None:
        return False
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        if not target.lower().startswith("https://"):
            raise _PingRefused("not an https URL")  # the URL is the credential
        response = _bounded_get(target, settings.alerts.heartbeat_timeout_seconds)
        if not response.is_success:
            raise _PingRefused(f"status {response.status_code}")
    except Exception as exc:
        # Only our own text and the exception type are safe; other text may hold the URL.
        detail = str(exc) if isinstance(exc, _PingRefused) else type(exc).__name__
        LOG.warning("heartbeat ping for %s failed (%s); the run is unaffected", job, detail)
        return False
    return True
