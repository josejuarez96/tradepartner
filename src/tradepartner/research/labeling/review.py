"""The disagreement review: the session, the decisions, the undo, `finish`.

Research-labeling spec req 9 and C10 (review mode) as the 2026-10-07 amendment (#1121)
edits them, and req 10's batch metrics (moved here from T121b by #1068); plan task
T123b. No Streamlit import: the review page (T123c) draws only what this module hands
it and writes only through it, and nothing else writes the review file.

**The session** (`build_review_session`). It attaches the unfinished frame batch run
through `store.research.attach_run` on the caller's connection, reads the shortlist the
labeling job wrote once to `datafiles.shortlist_path` and the run's inference records
(refused unless their SHA-256 is the one the shortlist recorded), and the bound frame
rows through `research.load_dataset`. Its items are the shortlist's items that are not
`deferred`, in the shortlist's acceptance order. Rebuilding it is the resume: the
state lives in the review file alone.

**What the page draws** (`item_view`). Per item the Form 25 fields, the notice and the
chosen 8-K's items inline, the EDGAR index link, and two answers `a` and `b` as class
descriptions in the same form: the model's class, or the fixed phrase for a model
`unresolved` (and for a call that ended without an answer); the rule's class or class
set, or the fixed phrase for crosswalk rows 6 and 7. Which side is `a` is drawn per
item from a seed derived from the run id and the listing end id, so it is stable on
re-display and varies across items. No option name, probability, confidence, stratum
or attribution is in the view.

**The review file** (`record_decision`, `record_undo`). Each appends one line to
`reviews/departure-reason/<run_id>.jsonl` through `datafiles.append_jsonl`; the last
line per listing end wins. `record_decision` needs a reason, attributes after the save
(`decided_for` is computed from the item's hidden sides and written in the same
record; the caller learns it only from the return value), and refuses an item whose
final line is a decision, with no bypass parameter (#1029 F5). `record_undo` appends
an undo line that reopens the item, so the next decision is accepted and wins.

**`finish`** refuses a partial session, registers the review file under
`departure-reason-reviews`, computes the batch metrics (`batch_metrics`) and writes
the run's result, all on the caller's connection through `store.research`. A
`finishing` marker written before the registry writes freezes the review file (a rerun
after an interruption registers the same hash and reads back the closed run's outcome),
and a `finished` marker after them returns the stored result. Every write takes a file
lock beside the review file around its check and its append. This module opens no
connection itself and reads no table outside the research registry.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import random
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from tradepartner.research import RunHandle, datafiles, load_dataset
from tradepartner.research.labeling import crosswalk
from tradepartner.research.labeling.gold import EDGAR_INDEX_URL, EightKView
from tradepartner.research.labeling.scoring import clopper_pearson_lower_bound
from tradepartner.store.research import (
    RunAlreadyClosed,
    attach_run,
    get_registration,
    list_runs,
    register_dataset,
    write_result,
)

if TYPE_CHECKING:
    import duckdb

    from tradepartner.config import Settings

#: The registered name of a finished review file (C7).
DATASET_NAME: Final = "departure-reason-reviews"
#: Splits that bind a frame: only a frame batch has a shortlist to review.
FRAME_SPLITS: Final = frozenset({"full", "prospective"})
#: C10: a model `unresolved` (#965 F8), and the rule's rows 6 and 7 (req 9).
UNRESOLVED_PHRASE: Final = "the filings shown do not state the reason"
RULE_PHRASES: Final[Mapping[int, str]] = {
    6: "the rule kept the line listed",
    7: "the rule placed no filing",
}
#: One description per class (req 5), in `crosswalk.CLASSES` order.
CLASS_DESCRIPTIONS: Final[Mapping[str, str]] = {
    "transfer": "the class moved to another exchange and kept trading",
    "continuity": "the same business continued under a new holding company, domicile, "
    "legal form or a separation",
    "insolvency": "an insolvency proceeding of the issuer ended trading in the class",
    "terminal": "the company was acquired or taken private, or the instrument was retired",
    "removal": "the exchange removed the class, or the issuer withdrew it",
}
#: C10's preset reasons; any other non-empty text is accepted as the reason too.
REASON_PRESETS: Final = (
    "filing states it",
    "rule misread the filing",
    "filing silent",
    "other",
)
DECISIONS: Final = frozenset({"a", "b", "both_wrong", "unresolved"})
_ITEM_REF = re.compile(r"8-K item \d+\.\d{2}")
#: Req 10: the agreement stratum's estimates are `underpowered` below this many.
MIN_AGREEMENTS: Final = 20

Side = Literal["rule", "model"]
DecidedFor = Literal["rule", "model", "both_wrong", "unresolved"]
Connect = Callable[[], AbstractContextManager["duckdb.DuckDBPyConnection"]]


class ReviewRefused(ValueError):
    """A review step refused (the message says why)."""


class AlreadyDecided(ReviewRefused):
    """The item's final review line is a decision; `record_undo` reopens it."""


class SessionPartial(ReviewRefused):
    """An item has no final decision; a partial session is never finished."""


class SessionFinished(ReviewRefused):
    """The session is finished; it only displays."""


@dataclass(frozen=True)
class ReviewItem:
    """One item to review: its position and listing end, nothing that attributes."""

    index: int
    listing_end_id: str


@dataclass(frozen=True)
class ItemSides:
    """An item's hidden side: never in a view, used for the attribution and metrics."""

    stratum: crosswalk.Stratum
    sampling_rate: float
    model_class: str | None
    rule_row: int
    a_side: Side
    accepted_at: datetime


@dataclass(frozen=True)
class ReviewSession:
    """An open review session over one unfinished frame batch run."""

    settings: Settings
    handle: RunHandle
    connect: Connect = field(repr=False)
    code_version: str
    items: tuple[ReviewItem, ...]
    n_rows: int
    n_deferred: int
    n_unresolved: int
    n_disagreements: int
    cost_usd: float
    entries: Mapping[str, ItemSides] = field(repr=False)
    rows: Mapping[str, Mapping[str, Any]] = field(repr=False)

    @property
    def run_id(self) -> int:
        """The batch run under review."""
        return self.handle.run_id

    @property
    def review_path(self) -> Path:
        """The review file (append-only JSONL)."""
        return datafiles.review_path(self.settings, self.run_id)

    @property
    def finished_path(self) -> Path:
        """Written once `finish` has written the run's result."""
        return self.review_path.with_name(f"{self.run_id}.finished.json")

    @property
    def finishing_path(self) -> Path:
        """Written before `finish`'s registry writes: from then on the review file
        never changes, even if `finish` is interrupted and run again."""
        return self.review_path.with_name(f"{self.run_id}.finishing")

    @property
    def lock_path(self) -> Path:
        """Held while a write checks the file and appends, so two pages on one run
        cannot both pass the already-decided check."""
        return self.review_path.with_name(f"{self.run_id}.lock")


@dataclass(frozen=True)
class ItemView:
    """What the page draws for one item (module docstring): never an option name, a
    probability, the stratum or which answer is whose."""

    index: int
    total: int
    listing_end_id: str
    issuer: str | None
    exchange: str | None
    class_title: str | None
    filed_on: str | None
    effective_on: str | None
    notice: str | None
    eightk: EightKView | None
    index_url: str
    a: str
    b: str


@dataclass(frozen=True)
class Decision:
    """What `record_decision` saved: the line's id and, after the save, its attribution."""

    review_id: str
    decided_for: DecidedFor


@dataclass(frozen=True)
class FinishResult:
    """`finish`'s outcome: the result row's outcome, the review dataset and metrics."""

    outcome: str
    dataset_id: int
    sha256: str
    metrics: Mapping[str, Any]


# --- the session ------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def a_side(run_id: int, listing_end_id: str) -> Side:
    """Which side is answer `a` for this item: drawn from a seed derived from the run
    id and the listing end id (stable per item, varying across items)."""
    digest = hashlib.sha256(f"{run_id}:{listing_end_id}".encode()).digest()
    return "model" if random.Random(int.from_bytes(digest[:8], "big")).random() < 0.5 else "rule"


def _rule_answer(row: Mapping[str, Any]) -> crosswalk.RuleAnswer:
    return crosswalk.RuleAnswer(
        status=row["rule_status"],
        relisted=bool(row["rule_relisted"]),
        successor_id=row["rule_successor_id"],
        form15_in_window=bool(row["rule_form15_in_window"]),
    )


def _rule_classes(rule_row: int) -> list[str]:
    options = crosswalk.ROW_CONSISTENT_OPTIONS[rule_row]
    return [c for c, members in crosswalk.CLASSES.items() if members & options]


def _utc(lid: str, value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ReviewRefused(f"{lid}: form25_accepted_at {value!r} is not timezone-aware")
    return parsed


def build_review_session(
    run_id: int, *, settings: Settings, connect: Connect, code_version: str
) -> ReviewSession:
    """The review session of the unfinished frame batch run `run_id` (module
    docstring). `connect` opens the registry connection (`attach_run` here, the
    registry writes in `finish`); `code_version` is written on every review line."""
    with connect() as conn:
        handle = attach_run(conn, run_id, settings)
    if handle.split not in FRAME_SPLITS:
        raise ReviewRefused(f"run {run_id} binds split {handle.split!r}; only a batch is reviewed")
    shortlist_file = datafiles.shortlist_path(settings, run_id)
    if not shortlist_file.is_file():
        raise ReviewRefused(f"run {run_id} has no shortlist at {shortlist_file}")
    listed = json.loads(shortlist_file.read_text(encoding="utf-8"))
    records_file = datafiles.inference_path(settings, run_id)
    present = records_file.is_file()
    # A batch whose every packet was refused writes no records file and an empty hash.
    if (_sha256(records_file) if present else "") != listed["records_sha256"]:
        raise ReviewRefused(f"run {run_id}'s inference records differ from its shortlist's")
    finals: dict[str, Mapping[str, Any]] = {}
    cost = 0.0
    for record in datafiles.read_jsonl(records_file) if present else []:
        finals[str(record["listing_end_id"])] = record
        cost += float(record.get("cost_usd") or 0.0)
    rows = {str(r["listing_end_id"]): r for r in load_dataset(handle).iter_rows(named=True)}
    entries: dict[str, ItemSides] = {}
    items: list[ReviewItem] = []
    for raw in listed["items"]:
        lid = str(raw["listing_end_id"])
        if raw["deferred"]:
            continue
        if lid not in rows:
            raise ReviewRefused(f"shortlist item {lid} is not a row of run {run_id}'s frame")
        final = finals.get(lid)
        option = final["selected_option"] if final and final["reason"] == "ok" else None
        accepted = rows[lid]["form25_accepted_at"]
        entries[lid] = ItemSides(
            stratum=raw["stratum"],
            sampling_rate=float(raw["sampling_rate"]),
            model_class=None if option is None else crosswalk.class_of(option),
            rule_row=crosswalk.crosswalk_row(_rule_answer(rows[lid])),
            a_side=a_side(run_id, lid),
            accepted_at=_utc(lid, accepted),
        )
        items.append(ReviewItem(len(items), lid))
    return ReviewSession(
        settings=settings,
        handle=handle,
        connect=connect,
        code_version=code_version,
        items=tuple(items),
        n_rows=int(listed["n_rows"]),
        n_deferred=int(listed["n_deferred"]),
        n_unresolved=sum(1 for raw in listed["items"] if raw["stratum"] == "unresolved"),
        n_disagreements=sum(1 for raw in listed["items"] if raw["stratum"] == "disagreement"),
        cost_usd=cost,
        entries=entries,
        rows={lid: rows[lid] for lid in entries},
    )


# --- what the page draws ----------------------------------------------------------


def _describe(classes: Sequence[str]) -> str:
    texts = [CLASS_DESCRIPTIONS[c] for c in classes]
    return texts[0] if len(texts) == 1 else "one of: " + "; or ".join(texts)


def _answer_text(entry: ItemSides, side: Side) -> str:
    if side == "model":
        return UNRESOLVED_PHRASE if entry.model_class is None else _describe([entry.model_class])
    if entry.rule_row in RULE_PHRASES:
        return RULE_PHRASES[entry.rule_row]
    return _describe(_rule_classes(entry.rule_row))


def item_view(session: ReviewSession, i: int) -> ItemView:
    """Item `i` as the page draws it (module docstring)."""
    item = session.items[i]
    entry = session.entries[item.listing_end_id]
    raw_docs = session.rows[item.listing_end_id]["documents"]
    docs = raw_docs if isinstance(raw_docs, dict) else json.loads(raw_docs)
    exhibit = docs.get("exhibit") or {}
    raw_8k = docs.get("eightk")
    order = list(session.settings.research.labeling.eightk_items)
    eightk = None
    if raw_8k:
        found = raw_8k.get("items") or {}
        ranked = [k for k in order if k in found] + [k for k in found if k not in order]
        eightk = EightKView(
            form=raw_8k.get("form") or "8-K",
            accession=raw_8k.get("accession") or "",
            filed_on=raw_8k.get("filed_on"),
            items=tuple((k, found[k]) for k in ranked),
            body_head=raw_8k.get("body_head"),
        )
    filed_on = str(docs["form25_filed_on"])
    until = date.fromisoformat(filed_on) + timedelta(
        days=session.settings.research.labeling.context_after_days
    )
    other: Side = "rule" if entry.a_side == "model" else "model"
    return ItemView(
        index=i,
        total=len(session.items),
        listing_end_id=item.listing_end_id,
        issuer=docs.get("issuer"),
        exchange=docs.get("exchange_name") or docs.get("exchange"),
        class_title=docs.get("class_title"),
        filed_on=filed_on,
        effective_on=docs.get("effective_on"),
        notice=exhibit.get("text") if exhibit.get("status") == "text" else None,
        eightk=eightk,
        index_url=EDGAR_INDEX_URL.format(cik=docs["cik"], dateb=f"{until:%Y%m%d}"),
        a=_answer_text(entry, entry.a_side),
        b=_answer_text(entry, other),
    )


# --- the review file --------------------------------------------------------------


def _lines(session: ReviewSession) -> list[dict[str, Any]]:
    path = session.review_path
    return datafiles.read_jsonl(path) if path.exists() else []


def final_lines(session: ReviewSession) -> dict[str, dict[str, Any] | None]:
    """The final decision line per item, `None` for an item that is open (no line, or
    an undo as its last line)."""
    finals: dict[str, dict[str, Any] | None] = {i.listing_end_id: None for i in session.items}
    for line in _lines(session):
        lid = line.get("listing_end_id")
        if lid not in finals:
            raise ReviewRefused(f"review file names {lid!r}, which is not an item of this session")
        finals[lid] = None if line.get("undo") else line
    return finals


@contextmanager
def _locked(session: ReviewSession) -> Iterator[None]:
    session.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with session.lock_path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _writable(session: ReviewSession, item: ReviewItem | str) -> tuple[str, dict[str, Any] | None]:
    if session.finished_path.exists() or session.finishing_path.exists():
        raise SessionFinished(f"the review of run {session.run_id} is finished")
    lid = item.listing_end_id if isinstance(item, ReviewItem) else item
    finals = final_lines(session)
    if lid not in finals:
        raise ReviewRefused(f"{lid!r} is not an item of this session")
    return lid, finals[lid]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _review_id(session: ReviewSession) -> str:
    return f"{session.run_id}-{len(_lines(session)) + 1}"


def _attribute(entry: ItemSides, decision: str) -> DecidedFor:
    if decision == "a":
        return entry.a_side
    if decision == "b":
        return "rule" if entry.a_side == "model" else "model"
    return "both_wrong" if decision == "both_wrong" else "unresolved"


def record_decision(
    session: ReviewSession,
    item: ReviewItem | str,
    *,
    decision: str,
    reason: str,
    relied_on: str,
    passage_ref: str | None = None,
) -> Decision:
    """Append one decision line and return its attribution (module docstring).
    Refuses an item whose final line is a decision (`AlreadyDecided`), a decision
    outside `a`, `b`, `both_wrong`, `unresolved`, an empty reason, a `relied_on`
    other than `notice`, `8-K item N.NN` or `outside`, and `outside` without
    `passage_ref`."""
    with _locked(session):
        return _record_decision(session, item, decision, reason, relied_on, passage_ref)


def _record_decision(
    session: ReviewSession,
    item: ReviewItem | str,
    decision: str,
    reason: str,
    relied_on: str,
    passage_ref: str | None,
) -> Decision:
    lid, final = _writable(session, item)
    if final is not None:
        raise AlreadyDecided(f"{lid} is already decided; undo it first")
    if decision not in DECISIONS:
        raise ReviewRefused(f"{lid}: decision {decision!r} is not one of {sorted(DECISIONS)}")
    if not reason.strip():
        raise ReviewRefused(f"{lid}: a decision needs a reason")
    if relied_on not in ("notice", "outside") and not _ITEM_REF.fullmatch(relied_on):
        raise ReviewRefused(f"{lid}: relied_on {relied_on!r} is not notice, 8-K item or outside")
    if relied_on == "outside" and not (passage_ref or "").strip():
        raise ReviewRefused(f"{lid}: an `outside` decision needs the filing's accession or URL")
    entry = session.entries[lid]
    decided_for = _attribute(entry, decision)
    review_id = _review_id(session)
    datafiles.append_jsonl(
        session.review_path,
        [
            {
                "review_id": review_id,
                "run_id": session.run_id,
                "listing_end_id": lid,
                "stratum": entry.stratum,
                "decision": decision,
                "decided_for": decided_for,
                "reason": reason.strip(),
                "relied_on": relied_on,
                "passage_ref": passage_ref,
                "reviewer": "owner",
                "code_version": session.code_version,
                "known_at": _now(),
            }
        ],
    )
    return Decision(review_id, decided_for)


def record_undo(session: ReviewSession, item: ReviewItem | str) -> None:
    """Append an undo line that reopens a decided item; the next `record_decision`
    is accepted and, as the last line, wins at `finish`."""
    with _locked(session):
        lid, final = _writable(session, item)
        if final is None:
            raise ReviewRefused(f"{lid} has no decision to undo")
        datafiles.append_jsonl(
            session.review_path,
            [
                {
                    "review_id": _review_id(session),
                    "run_id": session.run_id,
                    "listing_end_id": lid,
                    "undo": True,
                    "known_at": _now(),
                }
            ],
        )


def next_item(session: ReviewSession) -> int | None:
    """The first item with no final decision, `None` when every item is decided."""
    finals = final_lines(session)
    return next((i.index for i in session.items if finals[i.listing_end_id] is None), None)


# --- the batch metrics and finish -------------------------------------------------


def _correct(entry: ItemSides, decided_for: str) -> tuple[bool, bool] | None:
    """`(model right, rule right)` for one decided item, or `None` when the item says
    nothing about either side's accuracy: an owner `unresolved`, or a `model` decision
    on a model `unresolved` (the filings do not state the reason: not a rule error for
    yield, but no confirmation of the rule's class either; QA pass 1). Otherwise the
    chosen side is right, and the other is right too when the chosen answer implies
    it: the model's class inside the rule's class set, or the rule's set that one
    class itself. `both_wrong` makes both wrong."""
    if decided_for == "unresolved":
        return None
    if decided_for == "both_wrong":
        return False, False
    rule_classes = _rule_classes(entry.rule_row)
    if decided_for == "model":
        if entry.model_class is None:
            return None
        return True, entry.model_class in rule_classes
    return rule_classes == [entry.model_class], True


def batch_metrics(
    entries: Mapping[str, ItemSides],
    decided: Mapping[str, DecidedFor],
    *,
    n_rows: int,
    n_deferred: int,
    n_unresolved: int,
    n_disagreements: int,
) -> dict[str, Any]:
    """Req 10's batch metrics over the reviewed items (`decided`: the final
    `decided_for` per listing end).

    `yield`: over reviewed `disagreement` and `unresolved` items, the share on which
    the rule was wrong (`both_wrong`, or `model` on a `disagreement`; never `model` on
    an `unresolved` item). Model and rule accuracy: post-stratified, each scored item
    weighted `N_h / r_h` (the stratum's rows in the batch over its scored items), which
    is the inverse of the stratum's sampling rate when nothing is deferred and stays
    unbiased when `max_items` defers part of a stratum (QA pass 1); a stratum with rows
    but no scored item is listed under `strata_unscored`. `N_h` is the shortlist's
    count for `disagreement` and `unresolved` (deferred included) and the remaining
    rows for the agreements. The agreement stratum's Clopper-Pearson lower bounds are
    reported beside them, flagged `underpowered` below `MIN_AGREEMENTS`."""
    errors = considered = 0
    scored: dict[str, list[tuple[bool, bool]]] = {
        "disagreement": [],
        "unresolved": [],
        "agreement_sample": [],
    }
    for lid, decided_for in decided.items():
        entry = entries[lid]
        if entry.stratum in ("disagreement", "unresolved"):
            considered += 1
            errors += decided_for == "both_wrong" or (
                decided_for == "model" and entry.stratum == "disagreement"
            )
        result = _correct(entry, decided_for)
        if result is not None:
            scored[entry.stratum].append(result)
    population = {
        "disagreement": n_disagreements,
        "unresolved": n_unresolved,
        "agreement_sample": max(n_rows - n_disagreements - n_unresolved, 0),
    }
    weight = model_w = rule_w = 0.0
    for stratum, results in scored.items():
        if results:
            w = population[stratum] / len(results)
            weight += w * len(results)
            model_w += w * sum(m for m, _ in results)
            rule_w += w * sum(r for _, r in results)
    agreements = scored["agreement_sample"]
    return {
        "yield": errors / considered if considered else 0.0,
        "yield_lower_bound": clopper_pearson_lower_bound(errors, considered),
        "yield_n": considered,
        "model_accuracy": model_w / weight if weight else None,
        "rule_accuracy": rule_w / weight if weight else None,
        "strata_unscored": sorted(h for h, n in population.items() if n and not scored[h]),
        "agreement_n": len(agreements),
        "agreement_model_accuracy_lower_bound": clopper_pearson_lower_bound(
            sum(m for m, _ in agreements), len(agreements)
        ),
        "agreement_rule_accuracy_lower_bound": clopper_pearson_lower_bound(
            sum(r for _, r in agreements), len(agreements)
        ),
        "underpowered": len(agreements) < MIN_AGREEMENTS,
        "unresolved_share": n_unresolved / n_rows if n_rows else 0.0,
        "n_reviewed": len(decided),
        "n_deferred": n_deferred,
    }


def finish(session: ReviewSession) -> FinishResult:
    """Finish a complete session (module docstring). A finished session returns its
    result again; `SessionPartial` when an item has no final decision."""
    if session.finished_path.exists():
        done = json.loads(session.finished_path.read_text(encoding="utf-8"))
        return FinishResult(done["outcome"], done["dataset_id"], done["sha256"], done["metrics"])
    with _locked(session):  # the check and the freeze are one step: no write between
        finals = final_lines(session)
        open_items = [lid for lid, line in finals.items() if line is None]
        if open_items:
            raise SessionPartial(f"{len(open_items)} item(s) have no decision: {open_items[:5]}")
        session.finishing_path.touch()  # the review file is frozen from here on
    decided: dict[str, DecidedFor] = {
        lid: line["decided_for"] for lid, line in finals.items() if line is not None
    }
    metrics = batch_metrics(
        session.entries,
        decided,
        n_rows=session.n_rows,
        n_deferred=session.n_deferred,
        n_unresolved=session.n_unresolved,
        n_disagreements=session.n_disagreements,
    )
    metrics["cost_usd"] = session.cost_usd
    path = session.review_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()  # a session with no item still registers its (empty) review file
    sha = _sha256(path)
    days = [e.accepted_at.astimezone(UTC).date() for e in session.entries.values()] or [
        session.handle.dataset.event_start,
        session.handle.dataset.event_end,
    ]
    with session.connect() as conn:
        record = register_dataset(
            conn,
            name=DATASET_NAME,
            version=f"run-{session.run_id}",
            path=str(path),
            sha256=sha,
            event_start=min(days),
            event_end=max(days),
            n_rows=len(_lines(session)),
            note=f"review records of research run {session.run_id}",
        )
        declared = set(get_registration(conn, session.handle.slug).secondary)
        try:
            outcome = _write_result(conn, session, metrics, sha, declared, record.dataset_id)
        except RunAlreadyClosed:
            # an earlier `finish` wrote the result and stopped before its marker
            outcome = _closed_outcome(conn, session)
    done = {"outcome": outcome, "dataset_id": record.dataset_id, "sha256": sha, "metrics": metrics}
    session.finished_path.write_text(json.dumps(done, indent=2) + "\n", encoding="utf-8")
    return FinishResult(outcome, record.dataset_id, sha, metrics)


def _closed_outcome(conn: duckdb.DuckDBPyConnection, session: ReviewSession) -> str:
    for run in list_runs(conn, session.handle.slug, include_synthetic=True):
        if run.run_id == session.run_id:
            return run.outcome
    raise ReviewRefused(f"run {session.run_id} is closed but not listed")


def _write_result(
    conn: duckdb.DuckDBPyConnection,
    session: ReviewSession,
    metrics: Mapping[str, Any],
    sha: str,
    declared: set[str],
    dataset_id: int,
) -> str:
    path = session.review_path
    return write_result(
        conn,
        session.handle,
        primary_value=metrics["yield"],
        primary_ci_low=metrics["yield_lower_bound"],
        primary_ci_high=1.0,
        n_observations=metrics["n_reviewed"],
        n_clusters=metrics["yield_n"],
        n_configurations=1,
        artifact_sha256=sha,
        artifact_path=str(path),
        secondary={k: v for k, v in metrics.items() if k in declared},
        exploratory={
            "reviews_dataset_id": dataset_id,
            **{k: v for k, v in metrics.items() if k not in declared},
        },
    )
