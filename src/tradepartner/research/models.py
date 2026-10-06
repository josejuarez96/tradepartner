"""The model client: plain `httpx`, a pinned model id, zero spend by default.

Research-labeling spec req 7 (the call), req 8 and ADR 0013 point 3 (e) (the zero
default), req 17 (the key), as the amendment of 2026-10-06 reads them (C4, C8, C9).
This is the one module that names the vendor's host (through
`research.labeling.api_base_url`) and reads `Settings.typesafe_api_key`
(`TYPESAFE_API_KEY`), and only `tradepartner.research.labeling.job` imports it
(ADR 0013 point 3 (b) and (c); `tests/test_llm_boundary.py`).

- **`build_client(settings, handle)`** refuses without an open `RunHandle`
  (`NoRunHandle`, "RunHandle required") before it reads anything else, and returns
  `DisabledModelClient` (every call raises `ModelDisabled`, nothing is constructed)
  unless both `research.spend_ceiling_usd_month` and
  `research.spend_ceiling_usd_total` exceed zero and the key is set. Their defaults
  are `0.0`, `0.0` and absent, so a fresh checkout cannot open a connection.
- **`HttpModelClient.label(request)`** checks the model id first (a versioned
  `jev-X.Y.Z`; the aliases `jev`, `jev-latest` and `jev-preview` are refused), then
  sends one `POST {base}/systemone` with `Authorization: Bearer <key>` and the body
  `{"state", "model", "questions": {<name>: {"type": "choice", "instructions",
  "criteria"}}}`. It retries only HTTP 429 and 529 and a connection error before any
  byte was sent (`ConnectError`, `ConnectTimeout`), at most
  `research.labeling.max_attempts` sends, honouring a numeric `Retry-After` (capped at
  the request timeout) or else backing off 1 s, 2 s, 4 s. Anything that fails after
  the request was sent (a read or write timeout, a dropped connection) is never
  re-sent, because it may have been billed: it comes back as `reason = "timeout"`
  with no answer. A final non-200 comes back as `reason = "refused"` with its status;
  401 and 403 raise `ModelAuthRejected` (a bad key fails every call, so the job
  stops); a 200 whose body is not the documented shape raises
  `ModelResponseInvalid`, which carries the call's `refused` record (it may have
  been billed, so the job records it); a key that cannot go in a header raises
  `ModelKeyInvalid` at construction; connection errors on every attempt raise
  `ModelUnreachable` (nothing was sent, so nothing was billed).
- **`ModelResponse`** is frozen and carries what the inference record (C8) needs from
  the call: `raw_request` is the body exactly as sent and holds no header, so the
  key is in no record. A returned model id that differs from the requested one is
  reported, not refused: stopping the batch on it is the job's rule (req 6 step 4).

The key is a `SecretStr`; its value is read only when the header is built, and no
message, `repr` or record here contains it.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx

from tradepartner.research import NoRunHandle, require_handle

if TYPE_CHECKING:
    from pydantic import SecretStr

    from tradepartner.config import Settings

#: This module's version, recorded on every inference record (`client_version`, C8).
#: Bump it with any change to the request, the retry rule or the parse.
CLIENT_VERSION = "1.0.0"

#: A versioned model id (spec req 7). Anything else, an alias included, is refused.
PINNED_MODEL_ID = re.compile(r"jev-\d+\.\d+\.\d+")
#: The vendor's moving aliases (Models page, read 2026-10-05), named in the refusal.
MODEL_ALIASES = frozenset({"jev", "jev-latest", "jev-preview"})
#: The only statuses retried (req 7); every other status is final.
RETRY_STATUSES = frozenset({429, 529})
#: Statuses that mean the key was refused.
AUTH_STATUSES = frozenset({401, 403})
ENDPOINT = "systemone"
#: What a bearer token may hold: printable ASCII, no space (RFC 6750 `b64token` and more).
_HEADER_TOKEN = re.compile(r"[\x21-\x7e]+")
#: The first backoff when no usable `Retry-After` came back; doubled per attempt.
BACKOFF_BASE_SECONDS = 1.0

Reason = Literal["ok", "timeout", "refused"]


class ModelDisabled(RuntimeError):
    """The zero default: no ceiling or no key, so no model call is possible."""


class ModelIdRefused(ValueError):
    """The requested model id is an alias or not a versioned `jev-X.Y.Z`."""


class ModelAuthRejected(RuntimeError):
    """The API refused the key (401 or 403)."""


class ModelUnreachable(RuntimeError):
    """No connection could be made on any attempt; nothing was sent."""


class ModelKeyInvalid(ValueError):
    """The configured key cannot be sent in an HTTP header."""


class ModelResponseInvalid(RuntimeError):
    """A 200 response whose body is not the documented shape.

    The call may have been billed, so `response` keeps what the record needs:
    `reason = "refused"`, `http_status = 200`, the raw request and body as received,
    and `usage.input_tokens` / `output_tokens` when the body still holds them, so the
    job can record it before it stops."""

    def __init__(self, message: str, response: ModelResponse) -> None:
        super().__init__(message)
        self.response = response


@dataclass(frozen=True)
class ModelRequest:
    """One `choice` question over one packet. `criteria` maps each option to its
    description, in the order sent (the vendor leans toward the first option)."""

    state: str
    model: str
    question: str
    instructions: str
    criteria: Mapping[str, str]


@dataclass(frozen=True)
class ModelResponse:
    """The outcome of one `label` call, attempts included (C8's call-side fields).

    `reason` is `ok` (an answer), `timeout` (sent, maybe billed, no answer) or
    `refused` (a final non-200: `http_status` says which). Without an answer,
    `selected_option`, `vendor_confidence` and the token counts are `None` and
    `probabilities` is empty. `known_at` is the completion time, tz-aware UTC.
    """

    model_id_requested: str
    model_id_returned: str | None
    client_version: str
    attempts: int
    raw_request: str
    raw_response: str
    http_status: int | None
    reason: Reason
    selected_option: str | None
    probabilities: Mapping[str, float]
    vendor_confidence: float | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    known_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ModelClient(Protocol):
    """What the labeling job calls: one question over one packet per call."""

    def label(self, request: ModelRequest) -> ModelResponse:
        """Ask `request`'s question and return the outcome."""
        ...


def check_model_id(model: str) -> str:
    """Return `model` if it is a versioned id, else raise `ModelIdRefused`."""
    if model in MODEL_ALIASES:
        raise ModelIdRefused(f"model {model!r} is an alias that moves; pin a jev-X.Y.Z id")
    if not isinstance(model, str) or not PINNED_MODEL_ID.fullmatch(model):
        raise ModelIdRefused(f"model {model!r} is not a versioned jev-X.Y.Z id")
    return model


def request_body(request: ModelRequest) -> dict[str, Any]:
    """The JSON body of req 7 for `request` (no header: the key is never in it)."""
    return {
        "state": request.state,
        "model": request.model,
        "questions": {
            request.question: {
                "type": "choice",
                "instructions": request.instructions,
                "criteria": dict(request.criteria),
            }
        },
    }


def encode_request(request: ModelRequest) -> str:
    """`request_body` as the exact JSON text sent and recorded as `raw_request`."""
    return json.dumps(request_body(request), ensure_ascii=False)


class _Malformed(Exception):
    """Why a 200 body is not the documented shape (internal to `make_response`)."""


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _usage_counts(data: object) -> tuple[int | None, int | None]:
    """`usage.input_tokens` and `output_tokens` from a parsed body, each `None` when
    absent or not a count (best effort, for a malformed body's record)."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None, None
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    return (
        input_tokens if _count(input_tokens) else None,
        output_tokens if _count(output_tokens) else None,
    )


def _parse_answer(request: ModelRequest, data: object) -> dict[str, Any]:
    """The answer fields of a 200 body, or `_Malformed` naming what is wrong. The
    choice and every probability key must be options that were sent, and the choice
    must carry a probability."""
    if not isinstance(data, dict):
        raise _Malformed("not a JSON object")
    model = data.get("model")
    if not isinstance(model, str) or not model:
        raise _Malformed("no model id")
    answers = data.get("answers")
    answer = answers.get(request.question) if isinstance(answers, dict) else None
    if not isinstance(answer, dict):
        raise _Malformed(f"no answer to {request.question!r}")
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in request.criteria:
        raise _Malformed("the choice is not one of the options sent")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or not all(
        isinstance(k, str) and _number(v) for k, v in probabilities.items()
    ):
        raise _Malformed("no probability map")
    if not set(probabilities) <= set(request.criteria):
        raise _Malformed("a probability for an option that was not sent")
    if choice not in probabilities:
        raise _Malformed("no probability for the choice")
    confidence = answer.get("confidence")
    if confidence is not None and not _number(confidence):
        raise _Malformed("a non-numeric confidence")
    input_tokens, output_tokens = _usage_counts(data)
    if input_tokens is None or output_tokens is None:
        raise _Malformed("usage lacks input_tokens or output_tokens")
    return {
        "model_id_returned": model,
        "selected_option": choice,
        "probabilities": MappingProxyType({k: float(v) for k, v in probabilities.items()}),
        "vendor_confidence": None if confidence is None else float(confidence),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def make_response(
    request: ModelRequest,
    *,
    raw_request: str,
    http_status: int | None,
    raw_response: str,
    attempts: int,
    latency_ms: int,
) -> ModelResponse:
    """The `ModelResponse` for a finished call: parsed when `http_status` is 200,
    `timeout` when nothing came back, `refused` for any other status. A 200 body that
    is not the documented shape raises `ModelResponseInvalid` carrying its `refused`
    record. Shared with the test double so both produce records through one parse."""
    common: dict[str, Any] = {
        "model_id_requested": request.model,
        "client_version": CLIENT_VERSION,
        "attempts": attempts,
        "raw_request": raw_request,
        "raw_response": raw_response,
        "http_status": http_status,
        "latency_ms": latency_ms,
    }
    no_answer: dict[str, Any] = {
        "model_id_returned": None,
        "selected_option": None,
        "probabilities": MappingProxyType({}),
        "vendor_confidence": None,
        "input_tokens": None,
        "output_tokens": None,
    }
    if http_status != 200:
        reason: Reason = "timeout" if http_status is None else "refused"
        return ModelResponse(**common, **no_answer, reason=reason)
    data: object = None
    try:
        data = json.loads(raw_response)
        return ModelResponse(**common, **_parse_answer(request, data), reason="ok")
    except (ValueError, _Malformed) as exc:
        why = str(exc) if isinstance(exc, _Malformed) else "not JSON"
        input_tokens, output_tokens = _usage_counts(data)
        record = ModelResponse(
            **common,
            **{**no_answer, "input_tokens": input_tokens, "output_tokens": output_tokens},
            reason="refused",
        )
        raise ModelResponseInvalid(
            f"the model API's 200 response is not the documented shape: {why}", record
        ) from None


class DisabledModelClient:
    """The client at the zero default: every call raises `ModelDisabled`."""

    def label(self, request: ModelRequest) -> ModelResponse:
        """Refuse: no ceiling or no key is configured."""
        raise ModelDisabled(
            "model calls are disabled: research.spend_ceiling_usd_month and "
            "research.spend_ceiling_usd_total must both exceed 0 and the key must be "
            "set in .env (ADR 0013 point 3 (e))"
        )


class HttpModelClient:
    """The real client: one `POST {base}/systemone` per call over `httpx`.

    The `httpx.Client` is built on the first call, not here, so constructing this
    client opens nothing. `transport` and `sleep` exist for tests (a
    `MockTransport` stub and a recorded backoff); production passes neither.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        timeout_seconds: float,
        max_attempts: int,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not _HEADER_TOKEN.fullmatch(api_key.get_secret_value().strip()):
            # Checked here, once, so a bad key never reaches httpx (whose encoding
            # error would carry the value); the message names the variable only.
            raise ModelKeyInvalid(
                "TYPESAFE_API_KEY is not a header-safe token (printable ASCII, no "
                "spaces or control characters); fix it in .env"
            )
        self._url = f"{base_url.rstrip('/')}/{ENDPOINT}"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._transport = transport
        self._sleep = sleep
        self._http: httpx.Client | None = None

    def __repr__(self) -> str:
        return f"HttpModelClient(url={self._url!r}, max_attempts={self._max_attempts})"

    def close(self) -> None:
        """Close the HTTP connection pool, if one was opened."""
        if self._http is not None:
            self._http.close()
            self._http = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout_seconds, transport=self._transport)
        return self._http

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        """Seconds to wait before send `attempt + 1`: a numeric `Retry-After` when
        the server sent one, else exponential; never over the request timeout."""
        wait: float = BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1)
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = math.nan
            if math.isfinite(seconds) and seconds >= 0:
                wait = seconds
        return min(wait, self._timeout_seconds)

    def label(self, request: ModelRequest) -> ModelResponse:
        """Send `request` under the retry rule of req 7 (see the module docstring)."""
        check_model_id(request.model)
        raw_request = encode_request(request)
        content = raw_request.encode("utf-8")
        for attempt in range(1, self._max_attempts + 1):
            started = time.monotonic()
            try:
                response = self._client().post(
                    self._url,
                    content=content,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value().strip()}",
                        "Content-Type": "application/json",
                    },
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Before any byte was sent: nothing was billed, so it may be retried.
                if attempt < self._max_attempts:
                    self._sleep(self._backoff(attempt, None))
                    continue
                raise ModelUnreachable(
                    f"no connection to the model API after {attempt} attempt(s) "
                    f"({type(exc).__name__})"
                ) from None
            except httpx.TransportError:
                # After the send began: maybe billed, so never re-sent.
                return make_response(
                    request,
                    raw_request=raw_request,
                    http_status=None,
                    raw_response="",
                    attempts=attempt,
                    latency_ms=_elapsed_ms(started),
                )
            latency_ms = _elapsed_ms(started)
            status = response.status_code
            if status in AUTH_STATUSES:
                raise ModelAuthRejected(f"the model API refused the key (HTTP {status})")
            if status in RETRY_STATUSES and attempt < self._max_attempts:
                self._sleep(self._backoff(attempt, response.headers.get("Retry-After")))
                continue
            return make_response(
                request,
                raw_request=raw_request,
                http_status=status,
                raw_response=response.text,
                attempts=attempt,
                latency_ms=latency_ms,
            )
        raise AssertionError("unreachable: the last attempt always returns or raises")


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def build_client(settings: Settings, handle: object) -> ModelClient:
    """The client for a run: the real one only with both ceilings above zero and the
    key set, else `DisabledModelClient`. Raises `NoRunHandle` ("RunHandle required")
    without an open handle, before anything else is read (spec req 16)."""
    try:
        require_handle(handle)
    except NoRunHandle as exc:
        raise NoRunHandle(f"RunHandle required: {exc}") from None
    research = settings.research
    key = settings.typesafe_api_key
    if (
        research.spend_ceiling_usd_month > 0
        and research.spend_ceiling_usd_total > 0
        and key is not None
        and key.get_secret_value().strip()
    ):
        labeling = research.labeling
        return HttpModelClient(
            base_url=labeling.api_base_url,
            api_key=key,
            timeout_seconds=labeling.request_timeout_seconds,
            max_attempts=labeling.max_attempts,
        )
    return DisabledModelClient()
