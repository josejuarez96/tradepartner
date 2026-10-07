"""The labeling job: handle first, spend check, drift run, calls, records, shortlist.

Research-labeling spec req 6 as the amendment of 2026-10-06 amends it (C2 the call
order, C4 the spend check, C5 the drift run, C8 the per-call record) and the
amendment of 2026-10-07 (#1121: full-length redaction, every records file summed).
This is the one module that imports `tradepartner.research.models` (ADR 0013 point 3
(b) (ii); `tests/test_llm_boundary.py`).

**`run_batch`** opens the run through `store.research.open_run` before it reads
anything (a `pilot` split bound to the sealed period opens only with the holdout
flags, and is then a spend); a refused open reads nothing and returns. On a frame
batch (`full`, `prospective`) it first runs the **drift probe** of C5 under its own
`robustness` run (`departure-reason-drift`) and closes the batch `failed` with
`drift probe not passed` before its first call unless that run's verdict is `pass`.
It then loads the bound rows through `research.load_dataset`, builds every first
packet, and runs **`spend_check`** over every inference record in the research store
before the first call; before every call it checks the spend recorded so far plus
that call's estimate again and stops the run (`failed`, `spend ceiling`) when it
would cross either ceiling. Calls are paced at `research.labeling.requests_per_second`
and follow `packets.next_kind` (`B` first where the row has an 8-K, `A` after a `B`
that came back `unresolved` or where none exists); the listing end's label is the
last call's answer. One record per call (C8) is appended through `datafiles` as it
comes back, its `raw_request` and `raw_response` passed through the secret
substitution of `config.clean_message` at full length (never its truncation or its
control-character pass). The batch stops on the first reply whose model id differs
from the one requested (`failed`, `model id mismatch`), after writing that reply's
record. A timeout after send is recorded `unresolved` / `timeout` and never re-sent.
At the end the records are registered as a `departure-reason-inferences` version; a
gold split (`dev`, `pilot`) is scored (`scoring`) and its result written, and a frame
batch computes the shortlist (`crosswalk.shortlist`) and is left unfinished for the
review to finish.

**Spend** (C4) is computed from records, never configured: `spend_records` reads every
`.jsonl` `datafiles.inference_paths` lists and refuses, naming it, a file whose stem
is not a run id (`unexpected inference file: <name>`), since a skipped records file
is spend the check cannot see. A call's estimate is the characters of the packet,
the instructions and the criteria divided by `chars_per_token`, at the price
snapshot; each record carries the returned `usage.input_tokens` instead (a timeout,
which may have been billed, keeps its estimate; a refusal with no usage costs 0).
Within a run the sums are carried forward in memory from the files read before the
first call plus every record this run writes. The two ceilings are `0.0` by default,
so with the defaults every call is refused here as well as by the client.

**`dry_run`** reads a registered frame or gold export by the path it is given,
refuses it unless its SHA-256 is the dataset row's, and reports the estimate over
every row of the export (an upper bound for a split), the two spend sums and the
headroom; it opens no run and calls nothing.

No batch is ever scheduled (ADR 0013 point 8): the job runs when a person runs it.
"""

from __future__ import annotations

import dataclasses
import io
import json
import random
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import duckdb
import polars as pl

from tradepartner.config import ResearchLabelingConfig, Settings, secret_values
from tradepartner.research import DatasetChanged, RunHandle, datafiles, load_dataset
from tradepartner.research.gates import Flags, Reasons
from tradepartner.research.labeling import crosswalk, scoring
from tradepartner.research.labeling.packets import (
    Answer,
    Kind,
    Packet,
    PacketLimits,
    PacketTooLarge,
    build_packet,
    next_kind,
)
from tradepartner.research.labeling.questions import DEFAULT_OPTION_SET, UNRESOLVED, OptionSet
from tradepartner.research.models import (
    ModelAuthRejected,
    ModelClient,
    ModelDisabled,
    ModelIdRefused,
    ModelKeyInvalid,
    ModelRequest,
    ModelResponse,
    ModelResponseInvalid,
    ModelUnreachable,
    build_client,
    check_model_id,
)
from tradepartner.store.research import (
    close_run,
    get_dataset,
    get_registration,
    open_run,
    register_dataset,
    write_result,
)

#: The question name sent to the vendor (req 7's body).
QUESTION: Final = "departure_reason"
#: The dataset name the records of every labeling run are registered under (req 6).
INFERENCES_DATASET: Final = "departure-reason-inferences"
#: C5's drift registration.
DRIFT_SLUG: Final = "departure-reason-drift"
#: C5: the probe set's size.
DRIFT_PROBE_SIZE: Final = 20
#: Splits that bind a frame (a batch); every other split is a gold split, scored.
FRAME_SPLITS: Final = frozenset({"full", "prospective"})
GOLD_SPLITS: Final = frozenset({"dev", "pilot"})
REDACTED: Final = "[redacted]"

SPEND_CEILING: Final = "spend ceiling"
MODEL_ID_MISMATCH: Final = "model id mismatch"
DRIFT_NOT_PASSED: Final = "drift probe not passed"
UNEXPECTED_FILE: Final = "unexpected inference file"

ClientFactory = Callable[[Settings, RunHandle], ModelClient]
#: The production client factory: the real client only with both ceilings and a key.
CLIENT_FACTORY: Final[ClientFactory] = build_client
Clock = Callable[[], datetime]


class SpendRefused(RuntimeError):
    """`spend_check` refused: the estimate would cross a ceiling."""


class UnexpectedInferenceFile(RuntimeError):
    """A `.jsonl` under the records directory that is not a run's records file, or a
    record the spend sum cannot read."""


class _Stop(Exception):
    """Ends a run `failed` with `message` (internal)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- spend (C4) ------------------------------------------------------------------------


@dataclass(frozen=True)
class SpendSums:
    """Cumulative and month-to-date spend in USD over inference records."""

    cumulative_usd: float
    month_to_date_usd: float

    def plus(self, cost_usd: float, known_at: datetime, now: datetime) -> SpendSums:
        """These sums with one more record of `cost_usd` known at `known_at`."""
        same_month = _month(known_at) == _month(now)
        return SpendSums(
            self.cumulative_usd + cost_usd,
            self.month_to_date_usd + (cost_usd if same_month else 0.0),
        )


def _month(value: datetime) -> tuple[int, int]:
    utc = value.astimezone(UTC)
    return utc.year, utc.month


def _known_at(value: object) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"known_at {value!r} is not a tz-aware ISO timestamp")
    return parsed


def spend_records(settings: Settings) -> list[dict[str, Any]]:
    """Every inference record in the research store (C4), every run's file in run-id
    order. Raises `UnexpectedInferenceFile` naming the first `.jsonl` whose stem is
    not a run id, or a record with no readable `cost_usd` and `known_at`."""
    records: list[dict[str, Any]] = []
    for path in datafiles.inference_paths(settings):
        if not datafiles.is_run_id_stem(path.stem):
            raise UnexpectedInferenceFile(f"{UNEXPECTED_FILE}: {path.name}")
        for number, record in enumerate(datafiles.read_jsonl(path), start=1):
            cost = record.get("cost_usd")
            try:
                _known_at(record.get("known_at"))
            except ValueError:
                cost = None
            if not isinstance(cost, int | float) or isinstance(cost, bool) or cost < 0:
                raise UnexpectedInferenceFile(
                    f"{UNEXPECTED_FILE}: {path.name} line {number} has no readable "
                    "cost_usd and known_at"
                )
            records.append(record)
    return records


def spend_sums(records: Iterable[Mapping[str, Any]], now: datetime) -> SpendSums:
    """Cumulative and month-to-date (the UTC month of `now`) spend over `records`."""
    sums = SpendSums(0.0, 0.0)
    for record in records:
        sums = sums.plus(float(record["cost_usd"]), _known_at(record["known_at"]), now)
    return sums


def _check_sums(sums: SpendSums, estimate_usd: float, settings: Settings) -> None:
    research = settings.research
    if (
        sums.cumulative_usd + estimate_usd > research.spend_ceiling_usd_total
        or sums.month_to_date_usd + estimate_usd > research.spend_ceiling_usd_month
    ):
        raise SpendRefused(f"{SPEND_CEILING}: {estimate_usd:.6f}")


def spend_check(
    records: Iterable[Mapping[str, Any]],
    estimate_usd: float,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> SpendSums:
    """Refuse (`SpendRefused`, `spend ceiling: <estimate>`) when cumulative spend over
    `records` plus `estimate_usd` exceeds `research.spend_ceiling_usd_total`, or the
    month-to-date spend plus it exceeds `research.spend_ceiling_usd_month` (C4).
    Returns the sums it checked."""
    sums = spend_sums(records, now or datetime.now(UTC))
    _check_sums(sums, estimate_usd, settings)
    return sums


def request_chars(request: ModelRequest) -> int:
    """The characters a call's estimate counts: the packet, the instructions and
    every option name and description sent (C4)."""
    criteria = sum(len(name) + len(text) for name, text in request.criteria.items())
    return len(request.state) + len(request.instructions) + criteria


def estimate_usd(request: ModelRequest, labeling: ResearchLabelingConfig) -> float:
    """The pre-call cost estimate: characters over `chars_per_token`, at the price
    snapshot (C4)."""
    tokens = request_chars(request) / labeling.chars_per_token
    return tokens * labeling.price_usd_per_million_input_tokens / 1_000_000


def record_cost(response: ModelResponse, estimate: float, price: float) -> float:
    """A record's `cost_usd`: the returned input tokens at the price snapshot; a
    timeout (sent, maybe billed, no usage) keeps its estimate; a refusal with no usage
    costs nothing."""
    if response.input_tokens is not None:
        return response.input_tokens * price / 1_000_000
    return estimate if response.reason == "timeout" else 0.0


# --- records (C8) ----------------------------------------------------------------------


def redact(text: str, settings: Settings) -> str:
    """`text` with every configured secret replaced by `[redacted]`, at full length:
    `config.clean_message`'s substitution without its cut or control-character pass,
    because a record is evidence (C8, #1121)."""
    for value in secret_values(settings):
        text = text.replace(value, REDACTED)
    return text


def build_record(
    *,
    run_id: int,
    sequence: int,
    listing_end_id: str,
    packet: Packet,
    response: ModelResponse,
    cost_usd: float,
    settings: Settings,
) -> dict[str, Any]:
    """C8's per-call record for one finished call. A timeout's `selected_option` is
    `unresolved` (req 6 step 4); every other unanswered call's is null."""
    if response.known_at.tzinfo is None:
        raise ValueError("a model response's known_at must be tz-aware")
    selected = UNRESOLVED if response.reason == "timeout" else response.selected_option
    return {
        "record_id": f"{run_id}-{sequence}",
        "run_id": run_id,
        "listing_end_id": listing_end_id,
        "passage_kind": packet.kind,
        "document_ids": list(packet.document_ids),
        "passage_sha256": packet.sha256,
        "option_set_version": packet.option_set_version,
        "option_set_hash": packet.option_set_hash,
        "model_id_requested": response.model_id_requested,
        "model_id_returned": response.model_id_returned,
        "client_version": response.client_version,
        "attempts": response.attempts,
        "raw_request": redact(response.raw_request, settings),
        "raw_response": redact(response.raw_response, settings),
        "selected_option": selected,
        "probabilities": dict(response.probabilities),
        "vendor_confidence": response.vendor_confidence,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cost_usd": cost_usd,
        "price_snapshot": settings.research.labeling.price_usd_per_million_input_tokens,
        "http_status": response.http_status,
        "latency_ms": response.latency_ms,
        "reason": response.reason,
        "known_at": response.known_at.astimezone(UTC).isoformat(),
    }


# --- rows, packets and outcomes ---------------------------------------------------------


def packet_limits(labeling: ResearchLabelingConfig) -> PacketLimits:
    """The packet caps of `research.labeling` (C2)."""
    return PacketLimits(
        exhibit_max_chars=labeling.exhibit_max_chars,
        item_max_chars=labeling.item_max_chars,
        eightk_max_chars=labeling.eightk_max_chars,
        max_packet_tokens=labeling.max_packet_tokens,
        chars_per_token=labeling.chars_per_token,
    )


def _parsed(row: Mapping[str, Any]) -> dict[str, Any]:
    """`row` with its `documents` JSON text parsed (the frame stores it as text)."""
    documents = row["documents"]
    return {**row, "documents": json.loads(documents) if isinstance(documents, str) else documents}


def _accepted_at(row: Mapping[str, Any]) -> datetime:
    value = row["form25_accepted_at"]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{row.get('listing_end_id')}: form25_accepted_at is not tz-aware")
    return value


def select_rows(
    frame: pl.DataFrame,
    *,
    accepted_from: date | None = None,
    accepted_to: date | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The rows to label, in acceptance order (ties by `listing_end_id`): those whose
    UTC acceptance day is inside `[accepted_from, accepted_to]`, then the first
    `limit`."""
    rows = sorted(
        (_parsed(r) for r in frame.to_dicts()),
        key=lambda r: (_accepted_at(r), str(r["listing_end_id"])),
    )
    kept = [
        r
        for r in rows
        if (accepted_from is None or _accepted_at(r).astimezone(UTC).date() >= accepted_from)
        and (accepted_to is None or _accepted_at(r).astimezone(UTC).date() <= accepted_to)
    ]
    return kept if limit is None else kept[:limit]


def model_request(packet: Packet, model: str, option_set: OptionSet) -> ModelRequest:
    """The one `choice` question over `packet` (req 7)."""
    return ModelRequest(
        state=packet.text,
        model=model,
        question=QUESTION,
        instructions=option_set.instructions,
        criteria=option_set.criteria,
    )


def _first_packets(
    rows: Sequence[Mapping[str, Any]], option_set: OptionSet, limits: PacketLimits
) -> list[tuple[str, Packet]]:
    """Every row's first packet (C2's call order); a packet over the cap is not a
    call, so it is left out."""
    packets: list[tuple[str, Packet]] = []
    for row in rows:
        kind = next_kind(row, None)
        assert kind is not None
        try:
            packets.append(
                (str(row["listing_end_id"]), build_packet(row, option_set, limits, kind))
            )
        except PacketTooLarge:
            continue
    return packets


def _packets_estimate(
    packets: Iterable[tuple[str, Packet]], option_set: OptionSet, labeling: ResearchLabelingConfig
) -> float:
    """The estimate of sending `packets` (the model id does not change it)."""
    return sum(estimate_usd(model_request(p, "", option_set), labeling) for _, p in packets)


def final_records(records: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """The last record per listing end, in file order: the label is the last call's
    answer (C2)."""
    last: dict[str, Mapping[str, Any]] = {}
    for record in records:
        last[str(record["listing_end_id"])] = record
    return last


def _final_answer(record: Mapping[str, Any] | None) -> tuple[str | None, scoring.Reason]:
    """A listing end's final `(selected_option, reason)`: no answer unless the last
    call was `ok`; a listing end with no call at all (every packet over the cap) is
    `refused`."""
    if record is None:
        return None, "refused"
    reason: scoring.Reason = record["reason"]
    return (record["selected_option"] if reason == "ok" else None), reason


def batch_shortlist(
    rows: Sequence[Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
    *,
    seed: int,
    agreement_sample_size: int = 30,
    max_items: int = 200,
) -> crosswalk.Shortlist:
    """The shortlist of a frame batch (req 9) from its bound rows and its records:
    each row's final answer against its rule answer (the `rule_*` columns)."""
    last = final_records(records)
    outcomes = []
    for row in rows:
        selected, reason = _final_answer(last.get(str(row["listing_end_id"])))
        outcomes.append(
            crosswalk.InferenceOutcome(
                listing_end_id=str(row["listing_end_id"]),
                accepted_at=_accepted_at(row),
                rule_answer=crosswalk.RuleAnswer(
                    status=row["rule_status"],
                    relisted=bool(row["rule_relisted"]),
                    successor_id=row["rule_successor_id"],
                    form15_in_window=bool(row["rule_form15_in_window"]),
                ),
                selected_option=selected,
                reason=reason,
            )
        )
    return crosswalk.shortlist(
        outcomes, seed=seed, agreement_sample_size=agreement_sample_size, max_items=max_items
    )


def scored_items(
    rows: Sequence[Mapping[str, Any]], records: Iterable[Mapping[str, Any]]
) -> list[scoring.ScoredItem]:
    """One `ScoredItem` per gold row: the owner's labels against the final answer."""
    last = final_records(records)
    items = []
    for row in rows:
        record = last.get(str(row["listing_end_id"]))
        selected, reason = _final_answer(record)
        is_seed = bool(row.get("seed_case") or False)
        in_draw = row.get("in_random_draw")
        items.append(
            scoring.ScoredItem(
                listing_end_id=str(row["listing_end_id"]),
                gold_label=row.get("gold_label"),
                predicted_option=selected,
                reason=reason,
                rule_provision=row["documents"].get("rule_provision"),
                text_states=row.get("text_states"),
                probabilities=(record["probabilities"] or None)
                if record and reason == "ok"
                else None,
                is_seed=is_seed,
                in_random_draw=True if in_draw is None else bool(in_draw),
            )
        )
    return items


def gold_metrics(
    items: Sequence[scoring.ScoredItem],
) -> tuple[scoring.AccuracyResult, dict[str, Any]]:
    """The primary bar (`class_accuracy` against `gold_label`) and the reported
    metrics of req 10 as C6 amends them."""
    primary = scoring.class_accuracy(items)
    usable = [
        item for item in items if item.in_random_draw and item.gold_label not in (None, UNRESOLVED)
    ]
    calibration = [
        scoring.CalibrationItem(item.probabilities, str(crosswalk.class_of(item.gold_label or "")))
        for item in usable
        if item.probabilities
    ]
    agreement = [
        (
            max(item.probabilities.values()),
            item.reason == "ok"
            and item.predicted_option not in (None, UNRESOLVED)
            and crosswalk.class_of(item.predicted_option or "")
            == crosswalk.class_of(item.gold_label or ""),
        )
        for item in usable
        if item.probabilities
    ]
    arm = scoring.rule_provision_arm_score(items)
    metrics: dict[str, Any] = {
        "class_accuracy_text_states": dataclasses.asdict(
            scoring.class_accuracy(items, against="text_states")
        ),
        "worst_case_lower_bound": scoring.worst_case_lower_bound(items),
        "unresolved_share": scoring.unresolved_share(items),
        "unlabelled_share": scoring.unlabelled_share(items),
        "seeded_recall": scoring.seeded_recall(items),
        "per_option": {
            name: dataclasses.asdict(value)
            for name, value in scoring.per_option_precision_recall(items).items()
        },
        "arm_coverage": arm.coverage,
        "arm_class_accuracy": dataclasses.asdict(arm.class_accuracy),
        "ece": scoring.ece(calibration),
        "multiclass_brier": scoring.multiclass_brier(calibration),
        "classwise_reliability": scoring.classwise_reliability(calibration),
        "share_p_at_or_above_0_999": scoring.share_at_or_above(calibration),
        "agreement_by_probability_bin": [
            dataclasses.asdict(row) for row in scoring.agreement_by_probability_bin(agreement)
        ],
    }
    return primary, metrics


# --- the calls ----------------------------------------------------------------------------


class _Pacer:
    """Spaces call starts at least `1 / requests_per_second` apart."""

    def __init__(
        self, requests_per_second: float, sleep: Callable[[float], None], clock: Callable[[], float]
    ) -> None:
        self._interval = 1.0 / requests_per_second
        self._sleep = sleep
        self._clock = clock
        self._next: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval


@dataclass
class _Caller:
    """One run's calls: the spend carried forward, the pacing, the records file."""

    settings: Settings
    handle: RunHandle
    client: ModelClient
    model: str
    option_set: OptionSet
    pacer: _Pacer
    sums: SpendSums
    now: datetime
    sequence: int = 0

    @property
    def path(self) -> Path:
        return datafiles.inference_path(self.settings, self.handle.run_id)

    def _write(
        self, listing_end_id: str, packet: Packet, response: ModelResponse, estimate: float
    ) -> None:
        cost = record_cost(
            response, estimate, self.settings.research.labeling.price_usd_per_million_input_tokens
        )
        self.sequence += 1
        record = build_record(
            run_id=self.handle.run_id,
            sequence=self.sequence,
            listing_end_id=listing_end_id,
            packet=packet,
            response=response,
            cost_usd=cost,
            settings=self.settings,
        )
        datafiles.append_jsonl(self.path, [record])
        self.sums = self.sums.plus(cost, response.known_at, self.now)

    def call(self, listing_end_id: str, packet: Packet) -> ModelResponse:
        """Send `packet` after the mid-batch spend check, write its record, and stop
        the run on a refused key, an invalid reply or a model id mismatch."""
        request = model_request(packet, self.model, self.option_set)
        estimate = estimate_usd(request, self.settings.research.labeling)
        try:
            _check_sums(self.sums, estimate, self.settings)
        except SpendRefused:
            raise _Stop(SPEND_CEILING) from None
        self.pacer.wait()
        try:
            response = self.client.label(request)
        except ModelResponseInvalid as exc:
            self._write(listing_end_id, packet, exc.response, estimate)
            raise _Stop(redact(str(exc), self.settings)) from None
        except (
            ModelDisabled,
            ModelAuthRejected,
            ModelUnreachable,
            ModelIdRefused,
            ModelKeyInvalid,
        ) as exc:
            raise _Stop(f"{type(exc).__name__}: {redact(str(exc), self.settings)}") from None
        self._write(listing_end_id, packet, response, estimate)
        if response.model_id_returned is not None and response.model_id_returned != request.model:
            raise _Stop(MODEL_ID_MISMATCH)
        return response


def _label_row(caller: _Caller, row: Mapping[str, Any], limits: PacketLimits) -> list[str]:
    """Send `row`'s calls in C2's order; return the kinds whose packet was over the
    cap (refused, never sent; the next kind follows as after an `unresolved`)."""
    refused: list[str] = []
    listing_end_id = str(row["listing_end_id"])
    kind: Kind | None = next_kind(row, None)
    while kind is not None:
        try:
            packet = build_packet(row, caller.option_set, limits, kind)
        except PacketTooLarge:
            refused.append(kind)
            kind = next_kind(row, Answer(kind, UNRESOLVED))
            continue
        response = caller.call(listing_end_id, packet)
        answered = response.selected_option if response.reason == "ok" else None
        kind = next_kind(row, Answer(kind, answered or UNRESOLVED))
    return refused


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


# --- the drift probe (C5) ----------------------------------------------------------------


@dataclass(frozen=True)
class DriftProbe:
    """C5's probe: the `departure-reason-gold` version whose `dev` split holds the
    probe set, and the frozen configuration's `dev` run under `departure-reason-pilot`
    whose records are the baseline (that run's result row holds the records file's
    SHA-256, so the baseline cannot change unnoticed)."""

    dataset_id: int
    baseline_run_id: int
    size: int = DRIFT_PROBE_SIZE


def _baseline_sha256(conn: duckdb.DuckDBPyConnection, run_id: int) -> str:
    row = conn.execute(
        "SELECT artifact_sha256 FROM research_results WHERE run_id = ? AND outcome = 'ok'",
        [run_id],
    ).fetchone()
    if row is None or not row[0]:
        raise _Stop(f"drift baseline run {run_id} has no ok result")
    return str(row[0])


def _run_drift(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    client_factory: ClientFactory,
    probe: DriftProbe,
    caller_args: Mapping[str, Any],
    *,
    run_by: str,
    synthetic: bool,
) -> str | None:
    """Open, run and score the drift run; its verdict (`None` when it did not finish
    `ok`)."""
    model: str = caller_args["model"]
    option_set: OptionSet = caller_args["option_set"]
    config = {
        "model": model,
        "option_set_version": option_set.version,
        "option_set_hash": option_set.hash,
        "baseline_run_id": probe.baseline_run_id,
        "probe_size": probe.size,
    }
    handle = open_run(
        conn,
        DRIFT_SLUG,
        probe.dataset_id,
        "dev",
        config,
        run_by,
        synthetic=synthetic,
        settings=settings,
    )
    if handle.refusal is not None:
        return None
    try:
        expected = _baseline_sha256(conn, probe.baseline_run_id)
        baseline_path = datafiles.inference_path(settings, probe.baseline_run_id)
        if not baseline_path.is_file() or _file_sha256(baseline_path) != expected:
            raise _Stop(f"drift baseline run {probe.baseline_run_id} records changed")
        rows = {str(r["listing_end_id"]): r for r in select_rows(load_dataset(handle))}
        baseline = {
            lid: record
            for lid, record in final_records(datafiles.read_jsonl(baseline_path)).items()
            if lid in rows and record["reason"] == "ok"
        }
        seed = get_registration(conn, DRIFT_SLUG).seed
        chosen = sorted(
            random.Random(seed).sample(sorted(baseline), min(probe.size, len(baseline)))
        )
        limits = packet_limits(settings.research.labeling)
        packets: list[tuple[str, Packet]] = []
        for lid in chosen:
            record = baseline[lid]
            packet = build_packet(rows[lid], option_set, limits, record["passage_kind"])
            if (
                packet.sha256 != record["passage_sha256"]
                or packet.option_set_hash != record["option_set_hash"]
                or record["model_id_requested"] != model
            ):
                raise _Stop(f"drift packet {lid} differs from its baseline record")
            packets.append((lid, packet))
        caller = _start_caller(settings, handle, client_factory, caller_args, packets)
        comparisons = []
        for lid, packet in packets:
            response = caller.call(lid, packet)
            comparisons.append(
                scoring.DriftComparison(
                    listing_end_id=lid,
                    option=response.selected_option if response.reason == "ok" else None,
                    baseline_option=baseline[lid]["selected_option"],
                    probabilities=dict(response.probabilities) or None,
                    baseline_probabilities=baseline[lid]["probabilities"] or None,
                )
            )
        rate = scoring.flip_rate(comparisons)
        path = caller.path
        outcome = write_result(
            conn,
            handle,
            primary_value=rate,
            primary_ci_low=rate,
            primary_ci_high=rate,
            n_observations=len(comparisons),
            n_clusters=len(comparisons),
            n_configurations=1,
            artifact_sha256=_file_sha256(path) if path.is_file() else "",
            artifact_path=str(path),
            exploratory={
                "baseline_run_id": probe.baseline_run_id,
                "baseline_sha256": expected,
                "flips": [c.listing_end_id for c in comparisons if c.option != c.baseline_option],
                "mean_abs_probability_shift": scoring.mean_abs_probability_shift(comparisons),
            },
        )
    except _Stop as stop:
        close_run(conn, handle, "failed", stop.message)
        return None
    except Exception as exc:
        close_run(conn, handle, "failed", f"{type(exc).__name__}: {redact(str(exc), settings)}")
        raise
    if outcome != "ok":
        return None
    verdict = conn.execute(
        "SELECT verdict FROM research_results WHERE run_id = ?", [handle.run_id]
    ).fetchone()
    return None if verdict is None else verdict[0]


def _start_caller(
    settings: Settings,
    handle: RunHandle,
    client_factory: ClientFactory,
    caller_args: Mapping[str, Any],
    packets: Sequence[tuple[str, Packet]],
) -> _Caller:
    """The run's guard (`spend_check` over every records file plus the estimate of
    `packets`), then its client and its caller."""
    model: str = caller_args["model"]
    option_set: OptionSet = caller_args["option_set"]
    now: datetime = caller_args["clock"]()
    labeling = settings.research.labeling
    estimate = _packets_estimate(packets, option_set, labeling)
    try:
        records = spend_records(settings)
        sums = spend_check(records, estimate, settings, now=now)
    except (SpendRefused, UnexpectedInferenceFile) as exc:
        raise _Stop(str(exc)) from None
    return _Caller(
        settings=settings,
        handle=handle,
        client=client_factory(settings, handle),
        model=model,
        option_set=option_set,
        pacer=_Pacer(labeling.requests_per_second, caller_args["sleep"], caller_args["monotonic"]),
        sums=sums,
        now=now,
    )


# --- the batch -----------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchResult:
    """What `run_batch` did: the run id, its outcome (`ok`, `failed`, a refusal at
    open, or `unfinished` for a frame batch awaiting review), the message of a
    failure, the records' dataset id, and the shortlist of a frame batch."""

    run_id: int
    outcome: str
    message: str | None = None
    inferences_dataset_id: int | None = None
    shortlist: crosswalk.Shortlist | None = None
    packets_refused: tuple[str, ...] = ()


def run_batch(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    client_factory: ClientFactory,
    slug: str,
    dataset_id: int,
    split: str,
    *,
    model: str,
    option_set: OptionSet = DEFAULT_OPTION_SET,
    drift: DriftProbe | None = None,
    flags: Flags | None = None,
    reasons: Reasons | None = None,
    configurations: int = 1,
    accepted_from: date | None = None,
    accepted_to: date | None = None,
    limit: int | None = None,
    agreement_sample_size: int = 30,
    max_items: int = 200,
    run_by: str = "owner",
    synthetic: bool = False,
    clock: Clock = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> BatchResult:
    """Run one labeling batch (module docstring). `client_factory(settings, handle)`
    builds the run's client (`CLIENT_FACTORY`, `models.build_client`, in production;
    a scripted double in tests). A frame batch needs `drift`; it is refused before
    anything opens without one."""
    check_model_id(model)
    if split in FRAME_SPLITS and drift is None:
        raise ValueError(
            f"a {split!r} batch runs the drift probe first: pass drift=DriftProbe(...)"
        )
    config = {
        "model": model,
        "option_set_version": option_set.version,
        "option_set_hash": option_set.hash,
        "packet_limits": dataclasses.asdict(packet_limits(settings.research.labeling)),
        "question": QUESTION,
        "accepted_from": accepted_from.isoformat() if accepted_from else None,
        "accepted_to": accepted_to.isoformat() if accepted_to else None,
        "limit": limit,
        "agreement_sample_size": agreement_sample_size,
        "max_items": max_items,
        "drift": dataclasses.asdict(drift) if drift else None,
    }
    handle = open_run(
        conn,
        slug,
        dataset_id,
        split,
        config,
        run_by,
        synthetic=synthetic,
        flags=flags,
        reasons=reasons,
        configurations=configurations,
        settings=settings,
    )
    if handle.refusal is not None:
        return BatchResult(handle.run_id, handle.refusal, handle.message)
    caller_args = {
        "model": model,
        "option_set": option_set,
        "clock": clock,
        "sleep": sleep,
        "monotonic": monotonic,
    }
    try:
        if drift is not None:
            verdict = _run_drift(
                conn,
                settings,
                client_factory,
                drift,
                caller_args,
                run_by=run_by,
                synthetic=synthetic,
            )
            if verdict != "pass":
                raise _Stop(DRIFT_NOT_PASSED)
        return _label_batch(
            conn,
            settings,
            client_factory,
            handle,
            caller_args,
            rows=select_rows(
                load_dataset(handle),
                accepted_from=accepted_from,
                accepted_to=accepted_to,
                limit=limit,
            ),
            configurations=configurations,
            agreement_sample_size=agreement_sample_size,
            max_items=max_items,
        )
    except _Stop as stop:
        close_run(conn, handle, "failed", stop.message)
        return BatchResult(handle.run_id, "failed", stop.message)
    except Exception as exc:
        close_run(conn, handle, "failed", f"{type(exc).__name__}: {redact(str(exc), settings)}")
        raise


def _label_batch(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    client_factory: ClientFactory,
    handle: RunHandle,
    caller_args: Mapping[str, Any],
    *,
    rows: list[dict[str, Any]],
    configurations: int,
    agreement_sample_size: int,
    max_items: int,
) -> BatchResult:
    model: str = caller_args["model"]
    option_set: OptionSet = caller_args["option_set"]
    limits = packet_limits(settings.research.labeling)
    caller = _start_caller(
        settings, handle, client_factory, caller_args, _first_packets(rows, option_set, limits)
    )
    refused: list[str] = []
    for row in rows:
        refused += [f"{row['listing_end_id']}:{k}" for k in _label_row(caller, row, limits)]
    path = caller.path
    records = datafiles.read_jsonl(path) if path.is_file() else []
    inferences_id = _register_records(conn, handle, path, rows, len(records)) if records else None
    artifact_sha = _file_sha256(path) if records else ""
    exploratory: dict[str, Any] = {
        "inferences_dataset_id": inferences_id,
        "model": model,
        "n_rows": len(rows),
        "n_calls": len(records),
        "packets_refused": refused,
    }
    if handle.split in FRAME_SPLITS:
        seed = get_registration(conn, handle.slug).seed
        listed = batch_shortlist(
            rows,
            records,
            seed=seed,
            agreement_sample_size=agreement_sample_size,
            max_items=max_items,
        )
        return BatchResult(handle.run_id, "unfinished", None, inferences_id, listed, tuple(refused))
    primary, metrics = gold_metrics(scored_items(rows, records))
    declared = set(get_registration(conn, handle.slug).secondary)
    outcome = write_result(
        conn,
        handle,
        primary_value=primary.point_estimate,
        primary_ci_low=primary.lower_bound,
        primary_ci_high=1.0,
        n_observations=len(rows),
        n_clusters=primary.n,
        n_configurations=configurations,
        artifact_sha256=artifact_sha,
        artifact_path=str(path),
        secondary={k: v for k, v in metrics.items() if k in declared},
        exploratory={**exploratory, **{k: v for k, v in metrics.items() if k not in declared}},
    )
    return BatchResult(handle.run_id, outcome, None, inferences_id, None, tuple(refused))


def _register_records(
    conn: duckdb.DuckDBPyConnection,
    handle: RunHandle,
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    n_records: int,
) -> int:
    """Register run `handle`'s records file as a `departure-reason-inferences`
    version over the acceptance span of its rows (req 6 step 5)."""
    days = [_accepted_at(r).astimezone(UTC).date() for r in rows]
    record = register_dataset(
        conn,
        name=INFERENCES_DATASET,
        version=f"run-{handle.run_id}",
        path=str(path),
        sha256=_file_sha256(path),
        event_start=min(days),
        event_end=max(days),
        n_rows=n_records,
        note=f"inference records of research run {handle.run_id}",
    )
    return record.dataset_id


# --- the dry run --------------------------------------------------------------------------


@dataclass(frozen=True)
class DryRun:
    """`dry_run`'s report: the estimate over the export's first calls, the two spend
    sums over every records file, and the headroom under each ceiling."""

    n_rows: int
    n_requests: int
    estimate_usd: float
    cumulative_usd: float
    month_to_date_usd: float
    headroom_total_usd: float
    headroom_month_usd: float

    def lines(self) -> list[str]:
        """The report as printable lines."""
        return [
            f"rows: {self.n_rows}, first calls: {self.n_requests}",
            f"estimate: ${self.estimate_usd:.6f}",
            f"spend to date: ${self.cumulative_usd:.6f}, this month: ${self.month_to_date_usd:.6f}",
            f"headroom: total ${self.headroom_total_usd:.6f}, month ${self.headroom_month_usd:.6f}",
        ]


def dry_run(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    dataset_id: int,
    export: Path,
    *,
    option_set: OptionSet = DEFAULT_OPTION_SET,
    accepted_from: date | None = None,
    accepted_to: date | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> DryRun:
    """The estimate, the spend sums and the headroom (C4's `--dry-run`). Reads the
    export at `export` (the path the caller was given), refused with `DatasetChanged`
    unless its SHA-256 is dataset `dataset_id`'s; opens no run and calls nothing. The
    estimate covers every row of the export, an upper bound for a split."""
    record = get_dataset(conn, dataset_id)
    data = export.read_bytes()
    if sha256(data).hexdigest() != record.sha256:
        raise DatasetChanged(f"export {export} is not dataset {dataset_id}'s file")
    rows = select_rows(
        pl.read_parquet(io.BytesIO(data)),
        accepted_from=accepted_from,
        accepted_to=accepted_to,
        limit=limit,
    )
    labeling = settings.research.labeling
    packets = _first_packets(rows, option_set, packet_limits(labeling))
    estimate = _packets_estimate(packets, option_set, labeling)
    sums = spend_sums(spend_records(settings), now or datetime.now(UTC))
    research = settings.research
    return DryRun(
        n_rows=len(rows),
        n_requests=len(packets),
        estimate_usd=estimate,
        cumulative_usd=sums.cumulative_usd,
        month_to_date_usd=sums.month_to_date_usd,
        headroom_total_usd=research.spend_ceiling_usd_total - sums.cumulative_usd,
        headroom_month_usd=research.spend_ceiling_usd_month - sums.month_to_date_usd,
    )
