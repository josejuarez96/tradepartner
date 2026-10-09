"""Tests for `tradepartner.research.labeling.job` (plan T123; research-labeling spec
req 6 as C2, C4, C5 and C8 amend it, and #1121).

Every run here is synthetic, on a scratch DuckDB file that is not `store.path`, with
`research.data_dir` in a temporary directory, and every model call goes to the
scripted double (`tests/research/fake_model_client.py`): no real client is built and
nothing is spent.
"""

from __future__ import annotations

import dataclasses
import io
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from pydantic import SecretStr

from research.fake_model_client import Answer, ScriptedModelClient, Step, Timeout
from tradepartner.config import Settings
from tradepartner.research import DatasetChanged, RunHandle, datafiles
from tradepartner.research.experiment import ParsedExperiment
from tradepartner.research.gates import Flags, Reasons
from tradepartner.research.labeling import job, questions
from tradepartner.research.labeling.questions import DEFAULT_OPTION_SET
from tradepartner.research.models import ModelRequest, ModelResponse
from tradepartner.store import research, schema

MODEL = "jev-1.13.0"
MERGER = "merger_or_acquisition"
BANKRUPTCY = "bankruptcy"
UNRESOLVED = "unresolved"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
C8_FIELDS = {
    "record_id",
    "run_id",
    "listing_end_id",
    "passage_kind",
    "document_ids",
    "passage_sha256",
    "option_set_version",
    "option_set_hash",
    "model_id_requested",
    "model_id_returned",
    "client_version",
    "attempts",
    "raw_request",
    "raw_response",
    "selected_option",
    "probabilities",
    "vendor_confidence",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "price_snapshot",
    "http_status",
    "latency_ms",
    "reason",
    "known_at",
}


# --- fixtures ---------------------------------------------------------------------------


def _documents(lid: str, filed: date, *, eightk: bool) -> str:
    return json.dumps(
        {
            "listing_end_id": lid,
            "cik": "0000001000",
            "issuer": f"Issuer {lid}",
            "exchange": "NYSE",
            "class_title": "Common Stock",
            "form25_filed_on": filed.isoformat(),
            "effective_on": (filed + timedelta(days=10)).isoformat(),
            "rule_provision": "12d2-2(a)(3)",
            "rule_provision_raw": "17 CFR 240.12d2-2(a)(3)",
            "amendments": [],
            "exhibit": {"status": "text", "text": f"Notice {lid}: the merger closed."},
            "eightk": (
                {
                    "form": "8-K",
                    "accession": f"8K-{lid}",
                    "filed_on": filed.isoformat(),
                    "items": {"2.01": "Completion of the merger."},
                    "body_head": None,
                }
                if eightk
                else None
            ),
        },
        sort_keys=True,
    )


def _row(lid: str, accepted: datetime, *, eightk: bool) -> dict[str, Any]:
    return {
        "listing_end_id": lid,
        "cik": "0000001000",
        "exchange": "NYSE",
        "form25_accepted_at": accepted,
        "form25_filed_on": accepted.date(),
        "documents": _documents(lid, accepted.date(), eightk=eightk),
        "rule_status": "delisted",
        "rule_relisted": False,
        "rule_successor_id": None,
        "rule_form15_in_window": False,
        "rule_row": 5,
        "rule_as_of": datetime(2026, 9, 1, tzinfo=UTC),
        "rule_code_version": "abc",
    }


def _write(tmp_path: Path, name: str, rows: Sequence[Mapping[str, Any]]) -> tuple[Path, str]:
    buffer = io.BytesIO()
    pl.DataFrame(list(rows), infer_schema_length=None).write_parquet(buffer)
    path = tmp_path / f"{name}.parquet"
    path.write_bytes(buffer.getvalue())
    return path, sha256(buffer.getvalue()).hexdigest()


def _experiment(
    slug: str,
    *,
    kind: str,
    dataset: str,
    splits: tuple[str, ...],
    metric: str,
    direction: str,
    threshold: float | None,
    seed: int = 11,
) -> ParsedExperiment:
    return ParsedExperiment(
        path=Path(f"docs/experiments/{slug}.md"),
        slug=slug,
        kind=kind,
        stage=3,
        title=slug,
        confirmatory=False,
        provenance="model_historical",
        touches_returns=False,
        family=None,
        claims=(),
        hypothesis_ref=None,
        dataset_name=dataset,
        dataset_sha256_pin=None,
        window_start=date(2015, 1, 1),
        window_end=date(2026, 12, 31),
        splits=splits,
        primary_metric=metric,
        primary_direction=direction,
        primary_threshold=threshold,
        primary_ci_level=0.95,
        primary_min_clusters=1,
        primary_inference="fixture",
        secondary=(),
        comparison_set="fixture",
        multiplicity_method="none",
        multiplicity_family_id=None,
        multiplicity_family_size=None,
        budget_runs=50,
        budget_configurations=2,
        stop_rule="fixture",
        expected_effect="fixture",
        seed=seed,
        amends_sha256=None,
        doc_path=f"docs/experiments/{slug}.md",
        doc_sha256="d" * 64,
        params_json="{}",
        params_sha256=sha256(slug.encode()).hexdigest(),
    )


class World:
    """A scratch registry with the three registrations, a frame and a gold export."""

    def __init__(self, tmp_path: Path, settings: Settings) -> None:
        self.tmp = tmp_path
        self.settings = settings
        self.conn = duckdb.connect(str(tmp_path / "scratch.duckdb"))
        schema.init_schema(self.conn)
        for parsed in (
            _experiment(
                "departure-reason-batches",
                kind="benchmark",
                dataset="departure-reason-frame",
                splits=("full",),
                metric="yield",
                direction="greater",
                threshold=None,
            ),
            _experiment(
                "departure-reason-pilot",
                kind="benchmark",
                dataset="departure-reason-gold",
                splits=("dev", "pilot"),
                metric="class_accuracy",
                direction="greater",
                threshold=0.80,
            ),
            _experiment(
                "departure-reason-drift",
                kind="robustness",
                dataset="departure-reason-gold",
                splits=("dev",),
                metric="flip_rate",
                direction="less",
                threshold=0.10,
            ),
        ):
            research.register_experiment(self.conn, parsed, "owner")

    def frame(self, rows: Sequence[Mapping[str, Any]]) -> int:
        path, digest = _write(self.tmp, f"frame-{len(rows)}", rows)
        days = [r["form25_accepted_at"].date() for r in rows]
        return research.register_dataset(
            self.conn,
            name="departure-reason-frame",
            version=digest[:12],
            path=str(path),
            sha256=digest,
            event_start=min(days),
            event_end=max(days),
            n_rows=len(rows),
            event_column="form25_accepted_at",
        ).dataset_id

    def gold(self, dev: Sequence[Mapping[str, Any]], pilot: Sequence[Mapping[str, Any]]) -> int:
        rows = [*dev, *pilot]
        path, digest = _write(self.tmp, "gold", rows)
        splits = self.tmp / "gold.splits.json"
        splits.write_text(json.dumps({"splits": ["dev"] * len(dev) + ["pilot"] * len(pilot)}))
        dev_days = [r["form25_accepted_at"].date() for r in dev]
        pilot_days = [r["form25_accepted_at"].date() for r in pilot]
        pilot_span = (min(pilot_days), max(pilot_days))
        return research.register_dataset(
            self.conn,
            name="departure-reason-gold",
            version=digest[:12],
            path=str(path),
            sha256=digest,
            event_start=min(dev_days),
            event_end=max(pilot_days),
            n_rows=len(rows),
            event_column="form25_accepted_at",
            split_path=str(splits),
            split_sha256=sha256(splits.read_bytes()).hexdigest(),
            split_spans={"dev": (min(dev_days), max(dev_days)), "pilot": pilot_span},
            sealed_splits=("pilot",),
            sealed_periods=(pilot_span,),
            locked=True,
            seed=11,
        ).dataset_id

    def run(
        self,
        client: ScriptedModelClient | Callable[[Settings, RunHandle], Any],
        dataset_id: int,
        split: str,
        *,
        slug: str = "departure-reason-pilot",
        settings: Settings | None = None,
        **kwargs: Any,
    ) -> job.BatchResult:
        factory = (
            client
            if callable(client) and not isinstance(client, ScriptedModelClient)
            else (lambda _s, _h: client)
        )
        return job.run_batch(
            self.conn,
            settings or self.settings,
            factory,
            slug,
            dataset_id,
            split,
            model=MODEL,
            synthetic=True,
            clock=lambda: NOW,
            sleep=lambda _s: None,
            **kwargs,
        )

    def result(self, run_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT outcome, message, verdict, primary_value, exploratory_json "
            "FROM research_results WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if row is None:
            return {"outcome": "unfinished"}
        return dict(
            zip(("outcome", "message", "verdict", "primary_value", "exploratory"), row, strict=True)
        )


@pytest.fixture
def jsettings(settings: Settings, tmp_path: Path) -> Settings:
    labeling = settings.research.labeling.model_copy(update={"requests_per_second": 1000.0})
    research_config = settings.research.model_copy(
        update={
            "data_dir": str(tmp_path / "research"),
            "spend_ceiling_usd_month": 40.0,
            "spend_ceiling_usd_total": 40.0,
            "labeling": labeling,
        }
    )
    return settings.model_copy(update={"research": research_config})


@pytest.fixture
def world(tmp_path: Path, jsettings: Settings) -> Iterator[World]:
    built = World(tmp_path, jsettings)
    yield built
    built.conn.close()


def _gold_row(lid: str, accepted: datetime, label: str | None, *, eightk: bool = False) -> dict:
    return {
        **_row(lid, accepted, eightk=eightk),
        "gold_label": label,
        "text_states": None,
        "gold_class": None,
        "seed_case": False,
        "in_random_draw": True,
        "unlabelled": label is None,
    }


def _dev(n: int, *, eightk: bool = False) -> list[dict[str, Any]]:
    start = datetime(2016, 3, 1, 15, tzinfo=UTC)
    return [
        _gold_row(f"D{i:02d}", start + timedelta(days=i), MERGER, eightk=eightk) for i in range(n)
    ]


def _pilot(n: int) -> list[dict[str, Any]]:
    start = datetime(2018, 3, 1, 15, tzinfo=UTC)
    return [_gold_row(f"P{i:02d}", start + timedelta(days=i), MERGER) for i in range(n)]


def _records(settings: Settings, run_id: int) -> list[dict[str, Any]]:
    path = datafiles.inference_path(settings, run_id)
    return datafiles.read_jsonl(path) if path.is_file() else []


# --- the handle and the holdout ---------------------------------------------------------


def test_pilot_refused_without_flags_reads_nothing_and_spends_with_them(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold_id = world.gold(_dev(2), _pilot(3))
    reads: list[object] = []
    real_load = job.load_dataset
    monkeypatch.setattr(job, "load_dataset", lambda h: reads.append(h) or real_load(h))
    built: list[object] = []

    def factory(settings: Settings, handle: RunHandle) -> ScriptedModelClient:
        built.append(handle)
        return ScriptedModelClient([Answer(MERGER)] * 3)

    refused = world.run(factory, gold_id, "pilot")
    assert refused.outcome.startswith("refused")
    assert reads == [] and built == []
    assert _records(world.settings, refused.run_id) == []

    spent = world.run(
        factory,
        gold_id,
        "pilot",
        flags=Flags(spend_holdout=True),
        reasons=Reasons(holdout_reason="the one pilot score"),
    )
    assert spent.outcome == "ok"
    assert len(reads) == 1 and len(built) == 1
    holdout = world.conn.execute(
        "SELECT holdout_spent FROM research_runs WHERE run_id = ?", [spent.run_id]
    ).fetchone()
    assert holdout == (True,)
    assert world.result(spent.run_id)["primary_value"] == pytest.approx(1.0)


def test_a_frame_batch_without_a_drift_probe_opens_nothing(world: World) -> None:
    frame_id = world.frame([_row("F1", datetime(2019, 1, 2, 15, tzinfo=UTC), eightk=False)])
    with pytest.raises(ValueError, match="drift"):
        world.run(ScriptedModelClient([]), frame_id, "full", slug="departure-reason-batches")
    assert world.conn.execute("SELECT count(*) FROM research_runs").fetchone() == (0,)


# --- spend (C4) ----------------------------------------------------------------------------


def _spend(cost: float, when: datetime) -> dict[str, Any]:
    return {"cost_usd": cost, "known_at": when.isoformat()}


def test_spend_check_refuses_over_the_total_and_the_month(jsettings: Settings) -> None:
    last_month = datetime(2026, 9, 30, tzinfo=UTC)
    with pytest.raises(job.SpendRefused, match=r"spend ceiling: 0\.020000"):
        job.spend_check([_spend(39.99, last_month)], 0.02, jsettings, now=NOW)
    kept = job.spend_check([_spend(39.97, last_month)], 0.02, jsettings, now=NOW)
    assert kept.cumulative_usd == pytest.approx(39.97)
    monthly = jsettings.model_copy(
        update={
            "research": jsettings.research.model_copy(update={"spend_ceiling_usd_total": 100.0})
        }
    )
    with pytest.raises(job.SpendRefused):
        job.spend_check([_spend(39.99, NOW - timedelta(days=1))], 0.02, monthly, now=NOW)
    sums = job.spend_check([_spend(39.99, last_month)], 0.02, monthly, now=NOW)
    assert sums.month_to_date_usd == 0.0 and sums.cumulative_usd == pytest.approx(39.99)


def test_spend_check_refuses_at_the_zero_default(settings: Settings) -> None:
    with pytest.raises(job.SpendRefused):
        job.spend_check([], 1e-9, settings, now=NOW)


def test_the_estimate_counts_the_packet_instructions_and_criteria(jsettings: Settings) -> None:
    request = ModelRequest(
        state="x" * 100,
        model=MODEL,
        question=job.QUESTION,
        instructions="y" * 50,
        criteria={"aa": "b" * 8, "c": "d"},
    )
    assert job.request_chars(request) == 100 + 50 + (2 + 8) + (1 + 1)
    labeling = jsettings.research.labeling
    expected = 162 / 2.5 * 0.042 / 1_000_000
    assert labeling.chars_per_token == 2.5
    assert job.estimate_usd(request, labeling) == pytest.approx(expected)
    doubled = labeling.model_copy(update={"chars_per_token": 5.0})
    assert job.estimate_usd(request, doubled) == pytest.approx(expected / 2)


def test_the_mid_batch_stop_fires_after_the_record_that_crosses(
    world: World, tmp_path: Path
) -> None:
    gold_id = world.gold(_dev(4), _pilot(1))
    # 15,000 tokens a call at $0.042/M is $0.00063; the second record crosses $0.001.
    tight = world.settings.model_copy(
        update={
            "research": world.settings.research.model_copy(
                update={"spend_ceiling_usd_total": 0.001, "spend_ceiling_usd_month": 0.001}
            )
        }
    )
    client = ScriptedModelClient([Answer(MERGER, input_tokens=15_000)] * 4)
    result = world.run(client, gold_id, "dev", settings=tight)
    assert (result.outcome, result.message) == ("failed", "spend ceiling")
    records = _records(tight, result.run_id)
    assert len(records) == 2 and len(client.requests) == 2
    assert sum(r["cost_usd"] for r in records) > 0.001
    assert world.result(result.run_id)["message"] == "spend ceiling"


def test_the_pre_batch_check_refuses_before_any_call(world: World) -> None:
    gold_id = world.gold(_dev(3), _pilot(1))
    broke = world.settings.model_copy(
        update={
            "research": world.settings.research.model_copy(update={"spend_ceiling_usd_total": 1e-9})
        }
    )
    client = ScriptedModelClient([Answer(MERGER)] * 3)
    result = world.run(client, gold_id, "dev", settings=broke)
    assert result.outcome == "failed" and result.message is not None
    assert result.message.startswith("spend ceiling: ")
    assert client.requests == []


def test_every_records_file_is_summed_and_a_stray_name_is_refused(
    world: World, jsettings: Settings
) -> None:
    folder = datafiles.inference_path(jsettings, 7).parent
    datafiles.append_jsonl(datafiles.inference_path(jsettings, 7), [_spend(1.0, NOW)])
    datafiles.append_jsonl(datafiles.inference_path(jsettings, 12), [_spend(2.0, NOW)])
    assert job.spend_sums(job.spend_records(jsettings), NOW).cumulative_usd == pytest.approx(3.0)
    (folder / "notes.jsonl").write_text(json.dumps(_spend(5.0, NOW)) + "\n", encoding="utf-8")
    stray = r"unexpected inference file: notes\.jsonl"
    with pytest.raises(job.UnexpectedInferenceFile, match=stray):
        job.spend_records(jsettings)
    gold_id = world.gold(_dev(1), _pilot(1))
    client = ScriptedModelClient([Answer(MERGER)])
    result = world.run(client, gold_id, "dev")
    assert (result.outcome, result.message) == ("failed", "unexpected inference file: notes.jsonl")
    assert client.requests == []


def test_records_append_only_across_two_runs(world: World) -> None:
    gold_id = world.gold(_dev(2), _pilot(1))
    first = world.run(ScriptedModelClient([Answer(MERGER)] * 2), gold_id, "dev")
    first_path = datafiles.inference_path(world.settings, first.run_id)
    before = first_path.read_bytes()
    second = world.run(ScriptedModelClient([Answer(MERGER)] * 2), gold_id, "dev")
    assert first_path.read_bytes() == before
    assert len(_records(world.settings, second.run_id)) == 2
    sums = job.spend_sums(job.spend_records(world.settings), NOW)
    assert sums.cumulative_usd == pytest.approx(4 * 1000 * 0.042 / 1_000_000)


def test_dry_run_reports_and_opens_nothing(world: World, tmp_path: Path) -> None:
    rows = [_row("F1", datetime(2019, 1, 2, 15, tzinfo=UTC), eightk=True)]
    frame_id = world.frame(rows)
    datafiles.append_jsonl(datafiles.inference_path(world.settings, 3), [_spend(1.5, NOW)])
    report = job.dry_run(
        world.conn, world.settings, frame_id, tmp_path / "frame-1.parquet", now=NOW
    )
    assert (report.n_rows, report.n_requests) == (1, 1)
    assert report.estimate_usd > 0
    assert report.cumulative_usd == report.month_to_date_usd == pytest.approx(1.5)
    assert report.headroom_total_usd == pytest.approx(38.5)
    assert any("headroom" in line for line in report.lines())
    assert world.conn.execute("SELECT count(*) FROM research_runs").fetchone() == (0,)
    (tmp_path / "other.parquet").write_bytes(b"not the export")
    with pytest.raises(DatasetChanged):
        job.dry_run(world.conn, world.settings, frame_id, tmp_path / "other.parquet")


# --- the calls ------------------------------------------------------------------------------


def test_a_model_id_mismatch_stops_after_writing_its_record(world: World) -> None:
    gold_id = world.gold(_dev(3), _pilot(1))
    client = ScriptedModelClient([Answer(MERGER, model="jev-1.14.0"), Answer(MERGER)])
    result = world.run(client, gold_id, "dev")
    assert (result.outcome, result.message) == ("failed", "model id mismatch")
    records = _records(world.settings, result.run_id)
    assert [r["model_id_returned"] for r in records] == ["jev-1.14.0"]
    assert len(client.requests) == 1


def test_a_429_retry_is_recorded_with_two_attempts(world: World) -> None:
    gold_id = world.gold(_dev(1), _pilot(1))
    result = world.run(ScriptedModelClient([Answer(MERGER, rate_limits=1)]), gold_id, "dev")
    assert result.outcome == "ok"
    (record,) = _records(world.settings, result.run_id)
    assert record["attempts"] == 2 and record["reason"] == "ok"


def test_a_timeout_after_send_is_recorded_and_not_resent(world: World) -> None:
    gold_id = world.gold(_dev(2), _pilot(1))
    client = ScriptedModelClient([Timeout(), Answer(MERGER)])
    result = world.run(client, gold_id, "dev")
    records = _records(world.settings, result.run_id)
    assert [r["listing_end_id"] for r in records] == ["D00", "D01"]
    timeout = records[0]
    assert (timeout["reason"], timeout["selected_option"]) == ("timeout", UNRESOLVED)
    assert timeout["probabilities"] == {} and timeout["http_status"] is None
    assert timeout["cost_usd"] > 0  # sent, maybe billed: it keeps its estimate
    assert len(client.requests) == 2
    assert json.loads(world.result(result.run_id)["exploratory"])["unresolved_share"] == 0.5


def test_call_order_with_and_without_an_eight_k(world: World) -> None:
    start = datetime(2016, 3, 1, 15, tzinfo=UTC)
    dev = [
        _gold_row("D00", start, MERGER, eightk=True),
        _gold_row("D01", start + timedelta(days=1), BANKRUPTCY, eightk=True),
        _gold_row("D02", start + timedelta(days=2), MERGER, eightk=False),
    ]
    gold_id = world.gold(dev, _pilot(1))
    client = ScriptedModelClient(
        [Answer(MERGER), Answer(UNRESOLVED), Answer(BANKRUPTCY), Answer(MERGER)]
    )
    result = world.run(client, gold_id, "dev")
    records = _records(world.settings, result.run_id)
    assert [(r["listing_end_id"], r["passage_kind"]) for r in records] == [
        ("D00", "B"),
        ("D01", "B"),
        ("D01", "A"),
        ("D02", "A"),
    ]
    assert records[0]["document_ids"] == ["D00", "8K-D00"]
    labels = {lid: r["selected_option"] for lid, r in job.final_records(records).items()}
    assert labels == {"D00": MERGER, "D01": BANKRUPTCY, "D02": MERGER}
    assert world.result(result.run_id)["primary_value"] == pytest.approx(1.0)


def test_every_record_carries_c8s_fields(world: World) -> None:
    gold_id = world.gold(_dev(2, eightk=True), _pilot(1))
    probabilities = {name: 0.0 for name in DEFAULT_OPTION_SET.criteria}
    probabilities.update({MERGER: 0.7, BANKRUPTCY: 0.3})
    client = ScriptedModelClient([Answer(MERGER, probabilities=probabilities), Answer(MERGER)])
    result = world.run(client, gold_id, "dev")
    records = _records(world.settings, result.run_id)
    assert len(records) == 2
    for record in records:
        assert set(record) == C8_FIELDS
        known_at = datetime.fromisoformat(record["known_at"])
        assert known_at.tzinfo is not None and known_at.utcoffset() == timedelta(0)
        assert sum(record["probabilities"].values()) == pytest.approx(1.0, abs=1e-6)
        assert "Authorization" not in record["raw_request"]
        assert record["model_id_requested"] == record["model_id_returned"] == MODEL
        assert record["option_set_hash"] == DEFAULT_OPTION_SET.hash
        assert record["cost_usd"] == pytest.approx(1000 * 0.042 / 1_000_000)
        body = json.loads(record["raw_request"])
        assert record["passage_sha256"] == sha256(body["state"].encode()).hexdigest()
    assert records[0]["record_id"] != records[1]["record_id"]


class _EchoingClient:
    """A scripted stub whose request and response both carry `secret` and whose
    response is longer than `ingest.max_message_chars` (synthetic)."""

    def __init__(self, secret: str, padding: int) -> None:
        self._inner = ScriptedModelClient([Answer(MERGER)])
        self._secret = secret
        self._padding = padding

    def label(self, request: ModelRequest) -> ModelResponse:
        response = self._inner.label(request)
        return dataclasses.replace(
            response,
            raw_request=response.raw_request.replace("FILING TEXT", f"FILING {self._secret} TEXT"),
            raw_response=(
                f"{response.raw_response}\t\x01{self._secret}"
                f"{json.dumps(self._secret)[1:-1]}{'z' * self._padding}"
            ),
        )


def test_a_planted_key_is_redacted_at_full_length(world: World) -> None:
    key = 'planted"' + "-key" * 4  # a synthetic value with a JSON-escaped form
    keyed = world.settings.model_copy(update={"typesafe_api_key": SecretStr(key)})
    gold_id = world.gold(_dev(1), _pilot(1))
    padding = keyed.ingest.max_message_chars * 2
    client = _EchoingClient(key, padding)
    result = world.run(lambda _s, _h: client, gold_id, "dev", settings=keyed)
    (record,) = _records(keyed, result.run_id)
    assert key not in record["raw_request"] and key not in record["raw_response"]
    assert "FILING [redacted] TEXT" in record["raw_request"]
    assert record["raw_response"].endswith("\t\x01[redacted][redacted]" + "z" * padding)
    assert len(record["raw_response"]) > keyed.ingest.max_message_chars
    assert record["selected_option"] == MERGER and record["reason"] == "ok"


# --- the drift probe and the frame batch (C5) ------------------------------------------------


def _drift_world(world: World) -> tuple[int, int, int]:
    """A gold export with 20 `dev` rows, a baseline `dev` run that answered `merger`
    on every one, and a three-row frame; returns (gold id, baseline run, frame id)."""
    gold_id = world.gold(_dev(20), _pilot(1))
    baseline = world.run(ScriptedModelClient([Answer(MERGER)] * 20), gold_id, "dev")
    assert baseline.outcome == "ok"
    start = datetime(2019, 1, 2, 15, tzinfo=UTC)
    frame_id = world.frame(
        [
            _row("F1", start, eightk=False),
            _row("F2", start + timedelta(days=1), eightk=False),
            _row("F3", start + timedelta(days=2), eightk=False),
        ]
    )
    return gold_id, baseline.run_id, frame_id


def _factory(
    drift: ScriptedModelClient, batch: ScriptedModelClient
) -> Callable[[Settings, RunHandle], ScriptedModelClient]:
    return lambda _s, handle: drift if handle.slug == job.DRIFT_SLUG else batch


def _drift_answers(flips: int) -> list[Step]:
    return [Answer(BANKRUPTCY)] * flips + [Answer(MERGER)] * (20 - flips)


def test_two_drift_flips_close_the_batch_before_its_first_call(world: World) -> None:
    gold_id, baseline_run, frame_id = _drift_world(world)
    drift_client = ScriptedModelClient(_drift_answers(2))
    batch_client = ScriptedModelClient([Answer(MERGER)] * 3)
    result = world.run(
        _factory(drift_client, batch_client),
        frame_id,
        "full",
        slug="departure-reason-batches",
        drift=job.DriftProbe(dataset_id=gold_id, baseline_run_id=baseline_run),
    )
    assert (result.outcome, result.message) == ("failed", "drift probe not passed")
    assert batch_client.requests == [] and len(drift_client.requests) == 20
    assert _records(world.settings, result.run_id) == []
    drift_run = world.conn.execute(
        "SELECT max(run_id) FROM research_runs r JOIN research_registrations g "
        "USING (registration_id) WHERE g.slug = 'departure-reason-drift'"
    ).fetchone()
    assert drift_run is not None
    drift = world.result(drift_run[0])
    assert (drift["outcome"], drift["verdict"]) == ("ok", "fail")
    assert drift["primary_value"] == pytest.approx(0.10)


def test_one_drift_flip_passes_and_the_batch_is_left_unfinished(world: World) -> None:
    gold_id, baseline_run, frame_id = _drift_world(world)
    drift_client = ScriptedModelClient(_drift_answers(1))
    batch_client = ScriptedModelClient([Answer(MERGER), Answer(UNRESOLVED), Answer(BANKRUPTCY)])
    result = world.run(
        _factory(drift_client, batch_client),
        frame_id,
        "full",
        slug="departure-reason-batches",
        drift=job.DriftProbe(dataset_id=gold_id, baseline_run_id=baseline_run),
    )
    assert result.outcome == "unfinished" and result.message is None
    assert world.result(result.run_id) == {"outcome": "unfinished"}
    assert len(batch_client.requests) == 3
    # every drift packet is its baseline record's passage, re-sent
    baseline = job.final_records(_records(world.settings, baseline_run))
    sent = {sha256(r.state.encode()).hexdigest() for r in drift_client.requests}
    assert sent == {r["passage_sha256"] for r in baseline.values()}
    assert result.shortlist is not None
    strata = {item.listing_end_id: item.stratum for item in result.shortlist.items}
    assert strata["F2"] == "unresolved"
    written = json.loads(datafiles.shortlist_path(world.settings, result.run_id).read_text())
    assert written["items"] == [dataclasses.asdict(i) for i in result.shortlist.items]
    assert (written["seed"], written["inferences_dataset_id"]) == (11, result.inferences_dataset_id)
    dataset = research.get_dataset(world.conn, result.inferences_dataset_id or 0)
    assert dataset.name == job.INFERENCES_DATASET
    assert (
        dataset.sha256
        == sha256(datafiles.inference_path(world.settings, result.run_id).read_bytes()).hexdigest()
    )


def test_reversed_dev_pass_reports_flip_and_unresolved_against_baseline(world: World) -> None:
    gold_id = world.gold(_dev(2), _pilot(1))
    baseline = world.run(ScriptedModelClient([Answer(MERGER)] * 2), gold_id, "dev")
    client = ScriptedModelClient([Answer(BANKRUPTCY), Answer(UNRESOLVED)])

    result = world.run(
        client,
        gold_id,
        "dev",
        slug=job.DRIFT_SLUG,
        option_set=questions.REVERSED_OPTION_SET,
        reversed_baseline_run_id=baseline.run_id,
    )

    assert len(client.requests) == 2
    assert list(client.requests[0].criteria) == [
        name for name, _ in questions.REVERSED_OPTION_SET.options
    ]
    assert result.outcome == "ok"
    recorded = world.result(result.run_id)
    assert recorded["primary_value"] == 1.0
    assert json.loads(recorded["exploratory"])["unresolved_share"] == 0.5
    records = _records(world.settings, result.run_id)
    assert all(
        record["option_set_hash"] == questions.REVERSED_OPTION_SET.hash for record in records
    )


def test_reversed_pass_skips_non_ok_baseline_before_open(world: World) -> None:
    gold_id = world.gold(_dev(2), _pilot(1))
    baseline = world.run(ScriptedModelClient([Answer(MERGER), Timeout()]), gold_id, "dev")
    client = ScriptedModelClient([Answer(MERGER)])
    result = world.run(
        client,
        gold_id,
        "dev",
        slug=job.DRIFT_SLUG,
        option_set=questions.REVERSED_OPTION_SET,
        reversed_baseline_run_id=baseline.run_id,
    )
    assert result.outcome == "ok"
    assert len(client.requests) == 1
    assert json.loads(world.result(result.run_id)["exploratory"])["skipped_baseline"] == ["D01"]


def test_reversed_pass_uses_only_limited_baseline_items(world: World) -> None:
    gold_id = world.gold(_dev(2), _pilot(1))
    baseline = world.run(ScriptedModelClient([Answer(MERGER)]), gold_id, "dev", limit=1)
    client = ScriptedModelClient([Answer(MERGER)])
    result = world.run(
        client,
        gold_id,
        "dev",
        slug=job.DRIFT_SLUG,
        option_set=questions.REVERSED_OPTION_SET,
        reversed_baseline_run_id=baseline.run_id,
    )
    assert result.outcome == "ok"
    assert len(client.requests) == 1
    assert json.loads(world.result(result.run_id)["exploratory"])["skipped_baseline"] == ["D01"]


def test_reversed_pass_with_no_ok_baseline_opens_nothing(world: World) -> None:
    gold_id = world.gold(_dev(1), _pilot(1))
    baseline = world.run(ScriptedModelClient([Timeout()]), gold_id, "dev")
    before = world.conn.execute("SELECT count(*) FROM research_runs").fetchone()
    with pytest.raises(ValueError, match="no comparable baseline"):
        world.run(
            ScriptedModelClient([]),
            gold_id,
            "dev",
            slug=job.DRIFT_SLUG,
            option_set=questions.REVERSED_OPTION_SET,
            reversed_baseline_run_id=baseline.run_id,
        )
    assert world.conn.execute("SELECT count(*) FROM research_runs").fetchone() == before


# --- review fixes: the run lock, the month rollover, billed refusals, the estimate --------


def test_a_second_run_is_refused_while_one_holds_the_lock(world: World) -> None:
    import fcntl

    gold_id = world.gold(_dev(1), _pilot(1))
    path = datafiles.inference_lock_path(world.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(job.RunInProgress):
            world.run(ScriptedModelClient([Answer(MERGER)]), gold_id, "dev")
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    assert world.conn.execute("SELECT count(*) FROM research_runs").fetchone() == (0,)
    assert world.run(ScriptedModelClient([Answer(MERGER)]), gold_id, "dev").outcome == "ok"
    assert datafiles.inference_paths(world.settings) == [
        datafiles.inference_path(world.settings, 1)
    ]


def test_a_record_in_a_new_month_starts_that_months_sum() -> None:
    october = job.SpendSums(5.0, 2.0, (2026, 10))
    november = october.plus(0.5, datetime(2026, 11, 1, 0, 1, tzinfo=UTC))
    assert (november.cumulative_usd, november.month_to_date_usd) == (5.5, 0.5)
    assert november.plus(0.25, datetime(2026, 11, 2, tzinfo=UTC)).month_to_date_usd == 0.75
    late = november.plus(1.0, datetime(2026, 10, 31, 23, tzinfo=UTC))
    assert (late.cumulative_usd, late.month_to_date_usd) == (6.5, 0.5)


def _response(**changes: Any) -> ModelResponse:
    client = ScriptedModelClient([Answer(MERGER)])
    request = job.model_request(dataclasses.replace(_A_PACKET, text="x"), MODEL, DEFAULT_OPTION_SET)
    return dataclasses.replace(client.label(request), **changes)


def test_a_billed_reply_without_usage_keeps_its_estimate() -> None:
    no_usage = {"input_tokens": None, "output_tokens": None, "selected_option": None}
    malformed = _response(reason="refused", http_status=200, **no_usage)
    assert job.record_cost(malformed, 0.5, 0.042) == 0.5
    timeout = _response(reason="timeout", http_status=None, **no_usage)
    assert job.record_cost(timeout, 0.5, 0.042) == 0.5
    rate_limited = _response(reason="refused", http_status=429, **no_usage)
    assert job.record_cost(rate_limited, 0.5, 0.042) == 0.0
    assert job.record_cost(_response(input_tokens=2_000_000), 0.5, 0.042) == pytest.approx(0.084)


_A_PACKET = job.Packet(
    kind="A",
    text="",
    sha256="0" * 64,
    document_ids=(),
    option_set_version=DEFAULT_OPTION_SET.version,
    option_set_hash=DEFAULT_OPTION_SET.hash,
)


def test_the_estimate_counts_the_fallback_when_the_first_packet_is_over_the_cap(
    jsettings: Settings,
) -> None:
    row = job._parsed(_row("F1", datetime(2019, 1, 2, 15, tzinfo=UTC), eightk=True))
    row["documents"]["eightk"]["items"] = {"2.01": "Completion of the merger. " * 400}
    labeling = jsettings.research.labeling
    a_packet = job.build_packet(row, DEFAULT_OPTION_SET, job.packet_limits(labeling), "A")
    cap = int(len(a_packet.text) / labeling.chars_per_token) + 1
    limits = dataclasses.replace(job.packet_limits(labeling), max_packet_tokens=cap)
    packets = job._first_packets([row], DEFAULT_OPTION_SET, limits)
    assert [(lid, p.kind) for lid, p in packets] == [("F1", "A")]
