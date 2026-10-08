"""Tests for `tradepartner.research.labeling.review` (plan T123b; research-labeling spec
req 9 and C10's review mode as #1121 amends them, and req 10's batch metrics, #1068).

Every run is synthetic, on a scratch DuckDB file that is not `store.path`, with the
research store in a temporary directory; the batch under review is labelled by the
scripted double through `job.run_batch` (T123's World fixture), so nothing is spent.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from research.fake_model_client import Answer, ScriptedModelClient, Step, Timeout
from research.labeling.test_job import (
    MERGER,
    UNRESOLVED,
    World,
    _dev,
    _factory,
    _pilot,
    _row,
)
from tradepartner.config import Settings
from tradepartner.research import datafiles
from tradepartner.research.labeling import job, review
from tradepartner.research.labeling.questions import DEFAULT_OPTION_SET
from tradepartner.research.labeling.review import ItemSides
from tradepartner.store.schema import (
    JOURNAL_TABLE_NAMES,
    LATER_JOURNAL_TABLE_NAMES,
    MASTER_CHECK_TABLE_NAMES,
    REGISTRY_TABLE_NAMES,
    TABLE_NAMES,
)

TRANSFER = "exchange_transfer"
START = datetime(2019, 1, 2, 15, tzinfo=UTC)
#: listing end -> (rule status, scripted answer). F01..F04 agree (rule row 5, merger);
#: F05, F06 disagree; F07 is a model `unresolved`, F08 a timeout; F09 disagrees on
#: row 1 (transferred) and F10 on row 6 (still listed).
BATCH: dict[str, tuple[str, Step]] = {
    "F01": ("delisted", Answer(MERGER)),
    "F02": ("delisted", Answer(MERGER)),
    "F03": ("delisted", Answer(MERGER)),
    "F04": ("delisted", Answer(MERGER)),
    "F05": ("delisted", Answer(TRANSFER)),
    "F06": ("delisted", Answer(TRANSFER)),
    "F07": ("delisted", Answer(UNRESOLVED)),
    "F08": ("delisted", Timeout()),
    "F09": ("transferred", Answer(MERGER)),
    "F10": ("listed", Answer(MERGER)),
}
RUNTIME_TABLES = frozenset(
    TABLE_NAMES
    + JOURNAL_TABLE_NAMES
    + LATER_JOURNAL_TABLE_NAMES
    + MASTER_CHECK_TABLE_NAMES
    + REGISTRY_TABLE_NAMES
)


def _batch(world: World, *, max_items: int = 200) -> int:
    """A finished drift probe, then the ten-row frame batch above, left unfinished
    for review with two of its four agreements sampled; the run id."""
    gold_id = world.gold(_dev(1), _pilot(1))
    baseline = world.run(ScriptedModelClient([Answer(MERGER)]), gold_id, "dev")
    rows = []
    for i, (lid, (status, _)) in enumerate(BATCH.items()):
        rows.append({**_row(lid, START + timedelta(days=i), eightk=False), "rule_status": status})
    frame_id = world.frame(rows)
    result = world.run(
        _factory(
            ScriptedModelClient([Answer(MERGER)]),
            ScriptedModelClient([step for _, step in BATCH.values()]),
        ),
        frame_id,
        "full",
        slug="departure-reason-batches",
        drift=job.DriftProbe(dataset_id=gold_id, baseline_run_id=baseline.run_id),
        agreement_sample_size=2,
        max_items=max_items,
    )
    assert result.outcome == "unfinished", result
    return result.run_id


def _connect(world: World) -> review.Connect:
    return lambda: contextlib.nullcontext(world.conn)


def _session(world: World, run_id: int) -> review.ReviewSession:
    return review.build_review_session(
        run_id, settings=world.settings, connect=_connect(world), code_version="abc123"
    )


@pytest.fixture
def world(tmp_path: Path, settings: Settings) -> Iterator[World]:
    """T123's scratch registry, with the ceilings set so the scripted calls pass the
    spend check, the pace unthrottled and the research store in `tmp_path`."""
    labeling = settings.research.labeling.model_copy(update={"requests_per_second": 1000.0})
    research_config = settings.research.model_copy(
        update={
            "data_dir": str(tmp_path / "research"),
            "spend_ceiling_usd_month": 40.0,
            "spend_ceiling_usd_total": 40.0,
            "labeling": labeling,
        }
    )
    built = World(tmp_path, settings.model_copy(update={"research": research_config}))
    yield built
    built.conn.close()


@pytest.fixture
def batch(world: World) -> int:
    return _batch(world)


def _decide(session: review.ReviewSession, item: Any, decision: str = "a") -> review.Decision:
    return review.record_decision(
        session, item, decision=decision, reason="filing states it", relied_on="notice"
    )


# --- the session and what the page draws ------------------------------------------


def test_the_session_holds_the_shortlist_items_not_deferred(world: World) -> None:
    run_id = _batch(world, max_items=5)
    session = _session(world, run_id)
    listed = json.loads(datafiles.shortlist_path(world.settings, run_id).read_text())
    active = [i["listing_end_id"] for i in listed["items"] if not i["deferred"]]
    assert [i.listing_end_id for i in session.items] == active
    assert len(active) == 5 and session.n_deferred == listed["n_deferred"] == 3


def test_a_b_order_is_stable_per_item_and_varies_across_items(world: World, batch: int) -> None:
    first, second = _session(world, batch), _session(world, batch)
    views = [review.item_view(first, i.index) for i in first.items]
    assert views == [review.item_view(second, i.index) for i in second.items]
    sides = {review.a_side(batch, i.listing_end_id) for i in first.items}
    assert sides == {"model", "rule"}


def test_the_view_shows_no_option_name_probability_or_stratum(world: World, batch: int) -> None:
    session = _session(world, batch)
    allowed = {
        *review.CLASS_DESCRIPTIONS.values(),
        *review.RULE_PHRASES.values(),
        review.UNRESOLVED_PHRASE,
    }
    options = [name for name, _ in DEFAULT_OPTION_SET.options]
    banned = re.compile(
        "|".join([*options, "disagreement", "agreement_sample", "stratum", "probab", "confiden"])
    )
    for item in session.items:
        view = review.item_view(session, item.index)
        for answer in (view.a, view.b):
            assert answer in allowed or answer.startswith("one of: "), answer
            assert not banned.search(answer)
        shown = json.dumps(dataclasses.asdict(view), default=str)
        assert not banned.search(shown.replace(view.notice or "", "")), shown
        assert {f.name for f in dataclasses.fields(view)}.isdisjoint(
            {"stratum", "decided_for", "model", "rule", "probabilities", "sampling_rate"}
        )


def test_a_model_unresolved_and_a_timeout_render_as_the_fixed_phrase(
    world: World, batch: int
) -> None:
    session = _session(world, batch)
    by_id = {i.listing_end_id: review.item_view(session, i.index) for i in session.items}
    for lid in ("F07", "F08"):
        assert review.UNRESOLVED_PHRASE in (by_id[lid].a, by_id[lid].b)
    assert review.RULE_PHRASES[6] in (by_id["F10"].a, by_id["F10"].b)
    f05 = by_id["F05"]
    assert review.CLASS_DESCRIPTIONS["transfer"] in (f05.a, f05.b)
    assert any(t.startswith("one of: ") for t in (f05.a, f05.b))


# --- the review file -----------------------------------------------------------------


def test_a_decision_needs_a_reason_and_a_valid_passage(world: World, batch: int) -> None:
    session = _session(world, batch)
    item = session.items[0]
    with pytest.raises(review.ReviewRefused, match="reason"):
        review.record_decision(session, item, decision="a", reason="  ", relied_on="notice")
    with pytest.raises(review.ReviewRefused, match="accession or URL"):
        review.record_decision(session, item, decision="a", reason="other", relied_on="outside")
    with pytest.raises(review.ReviewRefused, match="decision"):
        review.record_decision(session, item, decision="c", reason="other", relied_on="notice")
    assert not session.review_path.exists()


def test_the_attribution_comes_after_the_save(world: World, batch: int) -> None:
    session = _session(world, batch)
    by_id = {i.listing_end_id: i for i in session.items}
    item = by_id["F05"]
    side = review.a_side(batch, "F05")
    saved = _decide(session, item, "a")
    assert saved.decided_for == side
    (line,) = datafiles.read_jsonl(session.review_path)
    assert line["decided_for"] == side and line["review_id"] == saved.review_id
    assert line["stratum"] == "disagreement" and line["reviewer"] == "owner"
    assert line["code_version"] == "abc123" and datetime.fromisoformat(line["known_at"]).tzinfo
    assert _decide(session, by_id["F06"], "both_wrong").decided_for == "both_wrong"
    # a save that fails attributes nothing and writes nothing
    with session.review_path.open("ab") as handle:
        handle.write(b'{"torn": ')
    before = session.review_path.read_bytes()
    with pytest.raises(ValueError, match=r"not valid JSON|torn"):
        _decide(session, by_id["F07"], "b")
    assert session.review_path.read_bytes() == before


def test_a_second_decision_is_refused_until_undo_and_then_wins(world: World, batch: int) -> None:
    session = _session(world, batch)
    item = next(i for i in session.items if i.listing_end_id == "F05")
    first = _decide(session, item, "a")
    with pytest.raises(review.AlreadyDecided):
        _decide(session, item, "b")
    with pytest.raises(review.ReviewRefused, match=r"nothing|no decision"):
        review.record_undo(session, session.items[0])
    review.record_undo(session, item)
    assert review.next_item(session) == 0
    second = _decide(session, item, "b")
    assert second.decided_for != first.decided_for
    assert review.final_lines(session)["F05"]["decided_for"] == second.decided_for  # type: ignore[index]


def test_the_review_file_is_append_only(world: World, batch: int) -> None:
    session = _session(world, batch)
    seen = b""
    for step in range(3):
        item = session.items[0]
        if step == 1:
            review.record_undo(session, item)
        else:
            _decide(session, item, "a" if step == 0 else "b")
        now = session.review_path.read_bytes()
        assert now.startswith(seen) and len(now) > len(seen)
        seen = now
    assert len(datafiles.read_jsonl(session.review_path)) == 3


# --- finish and the batch metrics -------------------------------------------------------


def _sides(stratum: str, *, model: str | None, row: int, rate: float = 1.0) -> ItemSides:
    return ItemSides(
        stratum=stratum,  # type: ignore[arg-type]
        sampling_rate=rate,
        model_class=model,
        rule_row=row,
        a_side="model",
        accepted_at=START,
    )


def test_yield_counts_rule_errors_only() -> None:
    entries = {
        "d_model": _sides("disagreement", model="transfer", row=5),
        "d_rule": _sides("disagreement", model="transfer", row=5),
        "d_wrong": _sides("disagreement", model="transfer", row=5),
        "u_model": _sides("unresolved", model=None, row=5),
        "u_wrong": _sides("unresolved", model=None, row=5),
        "u_unsure": _sides("unresolved", model=None, row=5),
        "a_model": _sides("agreement_sample", model="terminal", row=5, rate=0.5),
        "a_rule": _sides("agreement_sample", model="terminal", row=5, rate=0.5),
    }
    decided: dict[str, Any] = {
        "d_model": "model",
        "d_rule": "rule",
        "d_wrong": "both_wrong",
        "u_model": "model",
        "u_wrong": "both_wrong",
        "u_unsure": "unresolved",
        "a_model": "model",
        "a_rule": "rule",
    }
    metrics = review.batch_metrics(
        entries, decided, n_rows=10, n_deferred=0, n_unresolved=3, n_disagreements=3
    )
    # rule errors: d_model, d_wrong, u_wrong (never u_model) over six reviewed items
    assert metrics["yield"] == pytest.approx(3 / 6)
    assert metrics["yield_n"] == 6
    # scored: d_* (3 of 3 rows, weight 1), u_wrong only (u_model and u_unsure say
    # nothing: 1 scored for 3 rows, weight 3), a_* (2 scored for 4 rows, weight 2).
    # model right: d_model, a_model; rule right: d_rule, a_model, a_rule.
    assert metrics["model_accuracy"] == pytest.approx((1 + 2) / 10)
    assert metrics["rule_accuracy"] == pytest.approx((1 + 2 + 2) / 10)
    assert metrics["strata_unscored"] == []
    assert metrics["agreement_n"] == 2 and metrics["underpowered"] is True
    assert (metrics["n_reviewed"], metrics["n_deferred"]) == (8, 0)
    assert metrics["unresolved_share"] == pytest.approx(3 / 10)
    assert 0.0 < metrics["yield_lower_bound"] < 0.5


def test_the_weights_are_post_stratified_when_items_are_deferred() -> None:
    # four disagreements, two reviewed (two deferred); six agreements, one reviewed
    entries = {
        "d1": _sides("disagreement", model="transfer", row=5),
        "d2": _sides("disagreement", model="transfer", row=5),
        "a1": _sides("agreement_sample", model="terminal", row=5, rate=0.5),
    }
    decided: dict[str, Any] = {"d1": "model", "d2": "rule", "a1": "model"}
    metrics = review.batch_metrics(
        entries, decided, n_rows=10, n_deferred=4, n_unresolved=0, n_disagreements=4
    )
    # weights 4/2 per disagreement and 6/1 for the agreement (1/rate would give 0.75)
    assert metrics["model_accuracy"] == pytest.approx((2 + 6) / 10)
    assert metrics["rule_accuracy"] == pytest.approx((2 + 6) / 10)
    only_disagreements = review.batch_metrics(
        {k: v for k, v in entries.items() if k != "a1"},
        {"d1": "model", "d2": "rule"},
        n_rows=10,
        n_deferred=6,
        n_unresolved=0,
        n_disagreements=4,
    )
    assert only_disagreements["strata_unscored"] == ["agreement_sample"]


def test_a_single_class_rule_answer_chosen_makes_the_matching_model_right() -> None:
    entries = {"x": _sides("agreement_sample", model="transfer", row=1)}
    metrics = review.batch_metrics(
        entries, {"x": "rule"}, n_rows=1, n_deferred=0, n_unresolved=0, n_disagreements=0
    )
    assert (metrics["model_accuracy"], metrics["rule_accuracy"]) == (1.0, 1.0)


def test_finish_refuses_a_partial_session_then_writes_the_result(world: World) -> None:
    run_id = _batch(world, max_items=7)
    session = _session(world, run_id)
    for item in session.items[:-1]:
        _decide(session, item, "a")
    with pytest.raises(review.SessionPartial):
        review.finish(session)
    assert world.result(run_id) == {"outcome": "unfinished"}
    _decide(session, session.items[-1], "both_wrong")
    done = review.finish(session)
    assert done.outcome == "ok"
    result = world.result(run_id)
    assert result["outcome"] == "ok" and result["verdict"] == "n/a"
    assert result["primary_value"] == pytest.approx(done.metrics["yield"])
    exploratory = json.loads(result["exploratory"])
    assert exploratory["n_reviewed"] == 7 and exploratory["n_deferred"] == 1
    assert exploratory["reviews_dataset_id"] == done.dataset_id
    dataset = world.conn.execute(
        "SELECT name, sha256 FROM research_datasets WHERE dataset_id = ?", [done.dataset_id]
    ).fetchone()
    assert dataset == (review.DATASET_NAME, done.sha256)
    with pytest.raises(review.SessionFinished):
        review.record_undo(session, session.items[0])
    assert review.finish(session) == done
    # a `finish` interrupted after the result and before its marker completes on rerun
    session.finished_path.unlink()
    again = review.finish(session)
    assert (again.outcome, again.dataset_id, again.sha256) == ("ok", done.dataset_id, done.sha256)
    with pytest.raises(review.SessionFinished):
        _decide(session, session.items[0], "b")


def test_writes_stop_once_finish_has_begun(world: World, batch: int) -> None:
    session = _session(world, batch)
    session.finishing_path.parent.mkdir(parents=True, exist_ok=True)
    session.finishing_path.touch()
    with pytest.raises(review.SessionFinished):
        _decide(session, session.items[0])


def test_a_batch_with_no_records_file_can_be_reviewed(world: World, batch: int) -> None:
    listed_path = datafiles.shortlist_path(world.settings, batch)
    listed = json.loads(listed_path.read_text())
    datafiles.inference_path(world.settings, batch).unlink()
    with pytest.raises(review.ReviewRefused, match="differ"):
        _session(world, batch)
    listed_path.write_text(json.dumps({**listed, "records_sha256": ""}))
    session = _session(world, batch)
    views = [review.item_view(session, i.index) for i in session.items]
    assert all(review.UNRESOLVED_PHRASE in (v.a, v.b) for v in views)


# --- no runtime-store connection ---------------------------------------------------------


class _RecordingConnection:
    """The caller's registry connection, recording every statement run on it."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self.statements: list[str] = []

    def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(query)
        return self._conn.execute(query, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


def test_the_module_opens_no_connection_and_reads_no_runtime_table(
    world: World, batch: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _RecordingConnection(world.conn)
    opened: list[int] = []

    @contextlib.contextmanager
    def connect() -> Iterator[Any]:
        opened.append(1)
        yield recorder

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the review module opened a connection")

    monkeypatch.setattr(duckdb, "connect", refuse)
    session = review.build_review_session(
        batch, settings=world.settings, connect=connect, code_version="abc123"
    )
    for item in session.items:
        _decide(session, item, "a")
    review.finish(session)
    assert len(opened) == 2  # the attach, then the registry writes of `finish`
    token = re.compile(r"\b(" + "|".join(sorted(RUNTIME_TABLES, key=len, reverse=True)) + r")\b")
    touched = {t for sql in recorder.statements for t in token.findall(sql)}
    assert touched == set(), touched
    assert any("research_results" in sql for sql in recorder.statements)


def test_a_run_without_its_shortlist_is_refused(world: World, batch: int) -> None:
    datafiles.shortlist_path(world.settings, batch).unlink()
    with pytest.raises(review.ReviewRefused, match="no shortlist"):
        _session(world, batch)
