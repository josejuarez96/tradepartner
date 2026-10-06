"""`ScriptedModelClient`: the one test double for the model client (spec C9).

Synthetic: every answer here is written by the test that scripts it, never copied from
the vendor. The owner's real recordings live under `tests/fixtures/typesafe/` and are
read only by the contract test in `tests/test_llm_boundary.py`.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from tradepartner.research.models import (
    ModelRequest,
    ModelResponse,
    check_model_id,
    encode_request,
    make_response,
)


@dataclass(frozen=True)
class Answer:
    """A scripted answer. `model` is the id the response claims (default: the one
    requested; set it to script a mismatch). `probabilities` defaults to 1.0 on
    `choice` and 0.0 on every other option sent. `rate_limits` is how many 429s come
    before it: below the client's `max_attempts` they become extra attempts on the
    answer, at or above it the call ends `refused` with HTTP 429."""

    choice: str
    model: str | None = None
    probabilities: Mapping[str, float] | None = None
    confidence: float | None = 1.0
    input_tokens: int = 1000
    output_tokens: int = 3
    rate_limits: int = 0


@dataclass(frozen=True)
class Timeout:
    """A scripted timeout after send: no answer, never re-sent."""


Step = Answer | Timeout


class ScriptedModelClient:
    """A synthetic stand-in for `HttpModelClient`, not a recording.

    Each `label` call refuses an alias exactly as the real client does, keeps the
    request in `requests`, pops the next scripted step and builds the response
    through `models.make_response`, the same parse the real client uses, from a body
    in the vendor's documented shape. An exhausted script fails the test.
    """

    def __init__(self, steps: Iterable[Step], *, max_attempts: int = 3) -> None:
        self._steps: deque[Step] = deque(steps)
        self._max_attempts = max_attempts
        self.requests: list[ModelRequest] = []

    def label(self, request: ModelRequest) -> ModelResponse:
        """Return the next scripted outcome for `request`."""
        check_model_id(request.model)
        self.requests.append(request)
        if not self._steps:
            raise AssertionError("script exhausted: more calls than scripted steps")
        step = self._steps.popleft()
        raw_request = encode_request(request)
        if isinstance(step, Timeout):
            return make_response(
                request,
                raw_request=raw_request,
                http_status=None,
                raw_response="",
                attempts=1,
                latency_ms=0,
            )
        if step.rate_limits >= self._max_attempts:
            return make_response(
                request,
                raw_request=raw_request,
                http_status=429,
                raw_response="",
                attempts=self._max_attempts,
                latency_ms=0,
            )
        probabilities = step.probabilities or {
            option: 1.0 if option == step.choice else 0.0 for option in request.criteria
        }
        body = {
            "answers": {
                request.question: {
                    "type": "choice",
                    "choice": step.choice,
                    "probabilities": dict(probabilities),
                    "confidence": step.confidence,
                }
            },
            "model": step.model or request.model,
            "usage": {"input_tokens": step.input_tokens, "output_tokens": step.output_tokens},
        }
        return make_response(
            request,
            raw_request=raw_request,
            http_status=200,
            raw_response=json.dumps(body, sort_keys=True),
            attempts=step.rate_limits + 1,
            latency_ms=0,
        )
