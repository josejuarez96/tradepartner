"""The model client, its zero default and the research store's files (plan T119).

Research-labeling spec req 7 (the client), req 8 (the zero default), req 16 (test (e)'s
doubles) and req 17 (the key), as the 2026-10-06 amendment reads them (C4, C8, C9).
Every HTTP test runs against an `httpx.MockTransport` stub: nothing here reaches the
network, and the stub bodies are synthetic in the vendor's documented shape (E8).
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from research.fake_model_client import Answer, ScriptedModelClient, Timeout
from tradepartner.config import Settings
from tradepartner.research import NoRunHandle, RunHandle, _issue_run_handle, datafiles, models

KEY = "not-a-real-key"
BASE = "https://api.typesafe.ai/v1"
MODEL = "jev-1.13.0"
CRITERIA = {
    "bankruptcy": "The issuer is in bankruptcy.",
    "merger_or_acquisition": "The issuer was acquired.",
    "unresolved": "The text does not say.",
}


def _request(model: str = MODEL) -> models.ModelRequest:
    return models.ModelRequest(
        state="=== FILING TEXT START ===\nForm 25 for KLX\n=== FILING TEXT END ===",
        model=model,
        question="departure_reason",
        instructions="Why did the listing end?",
        criteria=CRITERIA,
    )


def _body(**overrides: Any) -> dict[str, Any]:
    """A synthetic response in the shape of E8 (the spike's `raw_response`)."""
    body: dict[str, Any] = {
        "answers": {
            "departure_reason": {
                "choice": "bankruptcy",
                "confidence": 0.98,
                "probabilities": {
                    "bankruptcy": 0.99,
                    "merger_or_acquisition": 0.01,
                    "unresolved": 0.0,
                },
                "type": "choice",
            }
        },
        "model": MODEL,
        "usage": {"input_tokens": 1351, "output_tokens": 116},
    }
    body.update(overrides)
    return body


Handler = Callable[[httpx.Request], httpx.Response]


class Stub:
    """A scripted `MockTransport`: each send pops the next step (a response or an
    exception to raise) and keeps the request it saw."""

    def __init__(self, steps: list[httpx.Response | Exception]) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _client(
    stub: Stub, *, max_attempts: int = 3, timeout: float = 60.0
) -> tuple[models.HttpModelClient, list[float]]:
    sleeps: list[float] = []
    client = models.HttpModelClient(
        base_url=BASE,
        api_key=SecretStr(KEY),
        timeout_seconds=timeout,
        max_attempts=max_attempts,
        transport=httpx.MockTransport(stub),
        sleep=sleeps.append,
    )
    return client, sleeps


def _ok(body: Mapping[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(200, json=body if body is not None else _body())


def _handle(refusal: str | None = None) -> RunHandle:
    return _issue_run_handle(
        run_id=7,
        registration_id=1,
        slug="departure-reason-batches",
        kind="benchmark",
        family=None,
        touches_returns=False,
        dataset=None,
        split="full",
        n_configurations_declared=1,
        synthetic=True,
        known_at=datetime(2026, 10, 6, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
        refusal=refusal,
        message=None,
    )


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TYPESAFE_API_KEY",
        "RESEARCH__SPEND_CEILING_USD_MONTH",
        "RESEARCH__SPEND_CEILING_USD_TOTAL",
        "RESEARCH__DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def _settings(**research: Any) -> Settings:
    return Settings(_env_file=None, research=research)


# --- the pinned model id ---------------------------------------------------------------


def test_a_versioned_id_passes() -> None:
    assert models.check_model_id("jev-1.13.0") == "jev-1.13.0"
    assert models.check_model_id("jev-2.0.10") == "jev-2.0.10"


@pytest.mark.parametrize(
    "model",
    [
        "jev",
        "jev-latest",
        "jev-preview",
        "jev-1.13",
        "JEV-1.13.0",
        "jev-1.13.0-rc1",
        "",
        " jev-1.13.0",
    ],
)
def test_aliases_and_unpinned_ids_are_refused(model: str) -> None:
    with pytest.raises(models.ModelIdRefused):
        models.check_model_id(model)


@pytest.mark.parametrize("alias", ["jev", "jev-latest", "jev-preview"])
def test_an_alias_raises_before_any_send(alias: str) -> None:
    stub = Stub([])
    client, _ = _client(stub)
    with pytest.raises(models.ModelIdRefused):
        client.label(_request(alias))
    assert stub.requests == []


# --- build_client: the handle, the zero default, the real type ---------------------------


@pytest.mark.usefixtures("clean_env")
def test_build_client_without_a_handle_raises_runhandle_required() -> None:
    with pytest.raises(NoRunHandle, match="RunHandle required"):
        models.build_client(_settings(), None)
    with pytest.raises(NoRunHandle, match="RunHandle required"):
        models.build_client(_settings(), 7)


@pytest.mark.usefixtures("clean_env")
def test_build_client_refuses_a_refused_run() -> None:
    with pytest.raises(NoRunHandle, match="RunHandle required"):
        models.build_client(_settings(), _handle(refusal="holdout"))


def _refuse_httpx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        pytest.fail("httpx.Client was constructed")

    monkeypatch.setattr(httpx, "Client", refuse)


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    "research",
    [
        {},
        {"spend_ceiling_usd_month": 40.0},
        {"spend_ceiling_usd_total": 40.0},
        {"spend_ceiling_usd_month": 40.0, "spend_ceiling_usd_total": 40.0},
    ],
)
def test_zero_default_or_no_key_gives_the_disabled_client(
    monkeypatch: pytest.MonkeyPatch, research: dict[str, float]
) -> None:
    _refuse_httpx_client(monkeypatch)
    client = models.build_client(_settings(**research), _handle())
    assert isinstance(client, models.DisabledModelClient)
    with pytest.raises(models.ModelDisabled):
        client.label(_request())


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    "research",
    [{"spend_ceiling_usd_month": 40.0}, {"spend_ceiling_usd_total": 40.0}, {}],
)
def test_a_key_without_both_ceilings_gives_the_disabled_client(
    monkeypatch: pytest.MonkeyPatch, research: dict[str, float]
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    _refuse_httpx_client(monkeypatch)
    client = models.build_client(_settings(**research), _handle())
    assert isinstance(client, models.DisabledModelClient)


@pytest.mark.usefixtures("clean_env")
def test_a_blank_key_gives_the_disabled_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    _refuse_httpx_client(monkeypatch)
    settings = _settings(spend_ceiling_usd_month=40.0, spend_ceiling_usd_total=40.0)
    assert isinstance(models.build_client(settings, _handle()), models.DisabledModelClient)


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize("bad", ["k\u00e9y-not-ascii", "key\nwith-newline", "key with space"])
def test_a_key_that_cannot_go_in_a_header_is_refused_without_naming_it(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", bad)
    _refuse_httpx_client(monkeypatch)
    settings = _settings(spend_ceiling_usd_month=40.0, spend_ceiling_usd_total=40.0)
    with pytest.raises(models.ModelKeyInvalid) as caught:
        models.build_client(settings, _handle())
    assert "TYPESAFE_API_KEY" in str(caught.value)
    for blob in (str(caught.value), repr(caught.value), repr(caught.value.args)):
        assert bad.strip() not in blob
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.usefixtures("clean_env")
def test_both_ceilings_and_a_key_give_the_real_client_without_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    _refuse_httpx_client(monkeypatch)  # built lazily, on the first call
    settings = _settings(spend_ceiling_usd_month=40.0, spend_ceiling_usd_total=40.0)
    client = models.build_client(settings, _handle())
    assert isinstance(client, models.HttpModelClient)
    assert KEY not in repr(client)


def test_the_disabled_client_refuses_every_protocol_call() -> None:
    client = models.DisabledModelClient()
    for name, _ in inspect.getmembers(models.ModelClient, inspect.isfunction):
        if not name.startswith("_"):
            with pytest.raises(models.ModelDisabled):
                getattr(client, name)(None)


# --- the request (req 7) ----------------------------------------------------------------


def test_one_post_to_systemone_with_the_req_7_body() -> None:
    stub = Stub([_ok()])
    client, _ = _client(stub)
    response = client.label(_request())
    (sent,) = stub.requests
    assert sent.method == "POST"
    assert str(sent.url) == f"{BASE}/systemone"
    assert sent.headers["Authorization"] == f"Bearer {KEY}"
    assert sent.headers["Content-Type"] == "application/json"
    body = json.loads(sent.content)
    assert body == {
        "state": _request().state,
        "model": MODEL,
        "questions": {
            "departure_reason": {
                "type": "choice",
                "instructions": "Why did the listing end?",
                "criteria": CRITERIA,
            }
        },
    }
    # The option order is the request's (the vendor leans to the first option).
    assert list(body["questions"]["departure_reason"]["criteria"]) == list(CRITERIA)
    assert response.raw_request == sent.content.decode("utf-8")


def test_a_trailing_slash_on_the_base_url_does_not_double() -> None:
    stub = Stub([_ok()])
    client = models.HttpModelClient(
        base_url=BASE + "/",
        api_key=SecretStr(KEY),
        timeout_seconds=60.0,
        max_attempts=3,
        transport=httpx.MockTransport(stub),
        sleep=lambda _s: None,
    )
    client.label(_request())
    assert str(stub.requests[0].url) == f"{BASE}/systemone"


def test_raw_request_holds_no_header_and_no_key() -> None:
    client, _ = _client(Stub([_ok()]))
    response = client.label(_request())
    assert "Authorization" not in response.raw_request
    assert "Bearer" not in response.raw_request
    assert KEY not in response.raw_request
    assert KEY not in response.raw_response
    assert KEY not in repr(response)


def test_the_key_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    stub = Stub([httpx.Response(429, headers={"Retry-After": "1"}), _ok()])
    client, _ = _client(stub)
    client.label(_request())
    assert KEY not in caplog.text


# --- the response parse (E8's shape) ----------------------------------------------------


def test_the_response_parses_into_a_frozen_record() -> None:
    body = _body()
    client, _ = _client(Stub([_ok(body)]))
    before = datetime.now(UTC)
    response = client.label(_request())
    assert response.reason == "ok"
    assert response.http_status == 200
    assert response.attempts == 1
    assert response.model_id_requested == MODEL
    assert response.model_id_returned == MODEL
    assert response.selected_option == "bankruptcy"
    assert dict(response.probabilities) == body["answers"]["departure_reason"]["probabilities"]
    assert response.vendor_confidence == 0.98
    assert response.input_tokens == 1351
    assert response.output_tokens == 116
    assert response.client_version == models.CLIENT_VERSION
    assert json.loads(response.raw_response) == body
    assert response.latency_ms >= 0
    assert response.known_at.tzinfo is UTC
    assert before <= response.known_at <= datetime.now(UTC)
    with pytest.raises(dataclasses.FrozenInstanceError):
        response.selected_option = "unresolved"  # type: ignore[misc]
    with pytest.raises(TypeError):
        response.probabilities["bankruptcy"] = 0.0  # type: ignore[index]


def test_a_mismatched_model_id_is_reported_not_hidden() -> None:
    client, _ = _client(Stub([_ok(_body(model="jev-1.14.0"))]))
    response = client.label(_request())
    assert response.model_id_requested == MODEL
    assert response.model_id_returned == "jev-1.14.0"


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps([1, 2]),
        json.dumps(_body(model=None)),
        json.dumps(_body(answers={})),
        json.dumps(_body(usage={"output_tokens": 3})),
        json.dumps(_body(answers={"departure_reason": {"choice": "bankruptcy", "type": "choice"}})),
        json.dumps(
            _body(
                answers={
                    "departure_reason": {
                        "choice": "not_an_option",
                        "probabilities": {"not_an_option": 1.0},
                        "type": "choice",
                    }
                }
            )
        ),
    ],
)
def test_a_malformed_200_raises(body: str) -> None:
    client, _ = _client(Stub([httpx.Response(200, text=body)]))
    with pytest.raises(models.ModelResponseInvalid) as caught:
        client.label(_request())
    record = caught.value.response
    assert (record.reason, record.http_status, record.attempts) == ("refused", 200, 1)
    assert record.raw_response == body
    assert record.selected_option is None


def test_a_malformed_200_keeps_its_usage_for_the_spend_sum() -> None:
    """It may have been billed: the record the job writes keeps the token counts."""
    body = _body(answers={"departure_reason": {"choice": "Bankruptcy", "type": "choice"}})
    client, _ = _client(Stub([_ok(body)]))
    with pytest.raises(models.ModelResponseInvalid) as caught:
        client.label(_request())
    assert caught.value.response.input_tokens == 1351
    assert caught.value.response.output_tokens == 116


@pytest.mark.parametrize(
    "probabilities",
    [
        {"bankruptcy": 0.9, "going_private": 0.1},  # an option that was not sent
        {"merger_or_acquisition": 1.0},  # nothing on the choice
    ],
)
def test_probabilities_must_be_over_the_options_sent(probabilities: dict[str, float]) -> None:
    answer = {"choice": "bankruptcy", "probabilities": probabilities, "type": "choice"}
    client, _ = _client(Stub([_ok(_body(answers={"departure_reason": answer}))]))
    with pytest.raises(models.ModelResponseInvalid):
        client.label(_request())


# --- retries: 429 and 529 only, Retry-After honoured ------------------------------------


@pytest.mark.parametrize("status", [429, 529])
def test_429_and_529_are_retried_honouring_retry_after(status: int) -> None:
    stub = Stub([httpx.Response(status, headers={"Retry-After": "3"}), _ok()])
    client, sleeps = _client(stub)
    response = client.label(_request())
    assert response.reason == "ok"
    assert response.attempts == 2
    assert len(stub.requests) == 2
    assert sleeps == [3.0]


def test_without_retry_after_the_backoff_is_exponential() -> None:
    stub = Stub([httpx.Response(429), httpx.Response(529), _ok()])
    client, sleeps = _client(stub)
    assert client.label(_request()).attempts == 3
    assert sleeps == [1.0, 2.0]


def test_retry_after_is_capped_at_the_request_timeout() -> None:
    stub = Stub([httpx.Response(429, headers={"Retry-After": "600"}), _ok()])
    client, sleeps = _client(stub, timeout=30.0)
    client.label(_request())
    assert sleeps == [30.0]


def test_an_unparseable_retry_after_falls_back_to_the_backoff() -> None:
    stub = Stub([httpx.Response(429, headers={"Retry-After": "soon"}), _ok()])
    client, sleeps = _client(stub)
    client.label(_request())
    assert sleeps == [1.0]


def test_429_on_every_attempt_ends_refused_after_max_attempts() -> None:
    stub = Stub([httpx.Response(429), httpx.Response(429), httpx.Response(429)])
    client, sleeps = _client(stub, max_attempts=3)
    response = client.label(_request())
    assert response.reason == "refused"
    assert response.http_status == 429
    assert response.attempts == 3
    assert len(stub.requests) == 3
    assert len(sleeps) == 2
    assert response.selected_option is None
    assert dict(response.probabilities) == {}
    assert response.input_tokens is None


@pytest.mark.parametrize("status", [408, 422, 500, 502, 503, 504])
def test_other_statuses_are_never_retried(status: int) -> None:
    stub = Stub([httpx.Response(status, text="no")])
    client, sleeps = _client(stub)
    response = client.label(_request())
    assert response.reason == "refused"
    assert response.http_status == status
    assert response.attempts == 1
    assert response.raw_response == "no"
    assert len(stub.requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_raises_without_naming_it(status: int) -> None:
    stub = Stub([httpx.Response(status)])
    client, _ = _client(stub)
    with pytest.raises(models.ModelAuthRejected) as caught:
        client.label(_request())
    assert KEY not in str(caught.value)
    assert len(stub.requests) == 1


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow connect")],
)
def test_a_connection_error_before_send_is_retried(error: Exception) -> None:
    stub = Stub([error, _ok()])
    client, sleeps = _client(stub)
    response = client.label(_request())
    assert response.reason == "ok"
    assert response.attempts == 2
    assert sleeps == [1.0]


def test_connection_errors_on_every_attempt_raise_unreachable() -> None:
    stub = Stub([httpx.ConnectError("x"), httpx.ConnectError("x"), httpx.ConnectError("x")])
    client, _ = _client(stub, max_attempts=3)
    with pytest.raises(models.ModelUnreachable):
        client.label(_request())
    assert len(stub.requests) == 3


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("read"),
        httpx.WriteTimeout("write"),
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("closed"),
    ],
)
def test_a_failure_after_send_is_never_retried(error: Exception) -> None:
    """It may have been billed: recorded as `timeout`, no answer, never re-sent."""
    stub = Stub([error, _ok()])
    client, sleeps = _client(stub)
    response = client.label(_request())
    assert response.reason == "timeout"
    assert response.http_status is None
    assert response.attempts == 1
    assert response.selected_option is None
    assert dict(response.probabilities) == {}
    assert len(stub.requests) == 1
    assert sleeps == []


def test_max_attempts_one_never_retries() -> None:
    stub = Stub([httpx.Response(429, headers={"Retry-After": "1"})])
    client, sleeps = _client(stub, max_attempts=1)
    response = client.label(_request())
    assert (response.reason, response.attempts, sleeps) == ("refused", 1, [])


def test_the_http_client_is_built_once_and_closed() -> None:
    stub = Stub([_ok(), _ok()])
    client, _ = _client(stub)
    client.label(_request())
    client.label(_request())
    assert len(stub.requests) == 2
    client.close()
    client.close()  # idempotent


# --- the scripted double (tests/research/fake_model_client.py) --------------------------


def test_the_double_is_labelled_synthetic() -> None:
    doc = (ScriptedModelClient.__doc__ or "").lower()
    assert "synthetic" in doc
    assert "not a recording" in doc


def test_the_double_answers_in_the_real_shape() -> None:
    double = ScriptedModelClient([Answer("merger_or_acquisition")])
    response = double.label(_request())
    assert response.reason == "ok"
    assert response.selected_option == "merger_or_acquisition"
    assert response.model_id_returned == MODEL
    assert sum(response.probabilities.values()) == pytest.approx(1.0)
    assert set(response.probabilities) == set(CRITERIA)
    assert "Authorization" not in response.raw_request
    assert double.requests == [_request()]


def test_the_double_scripts_a_429_retry_a_mismatch_and_a_timeout() -> None:
    double = ScriptedModelClient(
        [
            Answer("bankruptcy", rate_limits=1),
            Answer("bankruptcy", model="jev-1.14.0"),
            Timeout(),
            Answer("bankruptcy", rate_limits=3),
        ]
    )
    retried = double.label(_request())
    assert (retried.reason, retried.attempts) == ("ok", 2)
    assert double.label(_request()).model_id_returned == "jev-1.14.0"
    timed_out = double.label(_request())
    assert (timed_out.reason, timed_out.selected_option) == ("timeout", None)
    assert dict(timed_out.probabilities) == {}
    exhausted = double.label(_request())
    assert (exhausted.reason, exhausted.http_status, exhausted.attempts) == ("refused", 429, 3)


def test_the_double_refuses_an_alias_and_an_empty_script() -> None:
    double = ScriptedModelClient([Answer("bankruptcy")])
    with pytest.raises(models.ModelIdRefused):
        double.label(_request("jev-latest"))
    double.label(_request())
    with pytest.raises(AssertionError, match="script exhausted"):
        double.label(_request())


# --- datafiles: the one resolver of the research store ----------------------------------

SHA = "a" * 64


def test_every_path_is_under_the_configured_directory(tmp_path: Path) -> None:
    settings = _settings(data_dir=str(tmp_path / "research"))
    root = tmp_path / "research"
    assert datafiles.data_dir(settings) == root
    assert datafiles.frame_path(settings, SHA) == (
        root / "frames" / "departure-reason" / SHA / "frame.parquet"
    )
    assert datafiles.frame_counts_path(settings, SHA) == (
        root / "frames" / "departure-reason" / SHA / "counts.json"
    )
    assert datafiles.gold_working_path(settings) == (
        root / "gold" / "departure-reason" / "working.jsonl"
    )
    assert datafiles.gold_path(settings, SHA) == (
        root / "gold" / "departure-reason" / SHA / "gold.parquet"
    )
    assert datafiles.gold_splits_path(settings, SHA) == (
        root / "gold" / "departure-reason" / SHA / "splits.json"
    )
    assert datafiles.inference_path(settings, 12) == (
        root / "inferences" / "departure-reason" / "12.jsonl"
    )
    assert datafiles.review_path(settings, 12) == (
        root / "reviews" / "departure-reason" / "12.jsonl"
    )


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "../" + "a" * 61, ""])
def test_a_content_address_must_be_a_sha256(tmp_path: Path, bad: str) -> None:
    settings = _settings(data_dir=str(tmp_path))
    with pytest.raises(ValueError, match="SHA-256"):
        datafiles.frame_path(settings, bad)


@pytest.mark.parametrize("bad", [0, -1, True])
def test_a_run_id_must_be_a_positive_integer(tmp_path: Path, bad: int) -> None:
    settings = _settings(data_dir=str(tmp_path))
    with pytest.raises(ValueError, match="run id"):
        datafiles.inference_path(settings, bad)


def test_jsonl_writes_append_and_never_rewrite(tmp_path: Path) -> None:
    path = datafiles.inference_path(_settings(data_dir=str(tmp_path)), 1)
    assert datafiles.append_jsonl(path, [{"record_id": "a", "n": 1}]) == 1
    first = path.read_bytes()
    assert datafiles.append_jsonl(path, [{"record_id": "b"}, {"record_id": "c"}]) == 2
    assert path.read_bytes().startswith(first)
    assert [r["record_id"] for r in datafiles.read_jsonl(path)] == ["a", "b", "c"]


def test_jsonl_refuses_a_torn_file(tmp_path: Path) -> None:
    path = tmp_path / "torn.jsonl"
    path.write_text('{"record_id": "a"}\n{"record_id": ', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="newline"):
        datafiles.append_jsonl(path, [{"record_id": "b"}])
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="line 2"):
        datafiles.read_jsonl(path)


def test_jsonl_refuses_nan_and_non_json(tmp_path: Path) -> None:
    path = tmp_path / "x.jsonl"
    with pytest.raises(ValueError):
        datafiles.append_jsonl(path, [{"p": float("nan")}])
    with pytest.raises(TypeError):
        datafiles.append_jsonl(path, [{"when": datetime.now(UTC)}])
    assert not path.exists() or path.read_bytes() == b""


def test_inference_paths_lists_every_records_file(tmp_path: Path) -> None:
    settings = _settings(data_dir=str(tmp_path))
    assert datafiles.inference_paths(settings) == []
    for run_id in (3, 1, 20):
        datafiles.append_jsonl(datafiles.inference_path(settings, run_id), [{"r": run_id}])
    names = [p.name for p in datafiles.inference_paths(settings)]
    assert names == ["1.jsonl", "3.jsonl", "20.jsonl"]
