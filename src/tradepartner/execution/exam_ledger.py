"""The exam ledger: its loader, its store check and its append-only check, all pure
(plan T381b; ADR 0016, amendment 2026-11-07, #3146, rules 3 and 4; #3149 item 2).

`docs/research/exam-ledger.toml` is the git-tracked, append-only record of every
forward return exam start, and it holds the programme budget. This module reads text
only: finding the file under the repository root, `git show origin/main:...` and the
store query are the caller's edge (T381a at `paper start`, `scripts/ready_pr.py` for
the append-only check, #3147).

- `load_ledger(text)` parses one copy and refuses `exam_ledger_invalid` on every
  rule-3 invariant one copy can show.
- `check_exam_start(ledger, committed_text, store_windows, start, *, max_count)` is
  rule 4: `exam_ledger_unreviewed` when the copy read is not the committed copy on
  `origin/main` (or git could not be read, `committed_text is None`);
  `exam_ledger_mismatch` when the store's exam windows and the ledger's counted
  entries disagree, when there is not exactly one `pending` entry naming this start,
  or when `max_count` is above the ledger's `budget`; `exam_budget_spent` when the
  entries that are not `not_started` (this start's `pending` one included) are more
  than `min(max_count, budget)`.
- `check_append_only(old_text, new_text)` compares the `origin/main` copy with a PR's
  copy and refuses `exam_ledger_not_append_only` for a removed or reordered entry, a
  change to any field but `window_id`, `outcome` and `outcome_on`, a second
  `window_id` change, a backward or illegal outcome move, an appended entry that is
  neither `pending` nor `unexamined`, and `budget` changed without `budget_adr`.
  Either copy failing `load_ledger` is `exam_ledger_invalid`.
- `passed_window_ids(ledger)` is the set of window ids whose entry is `passed`, the
  only route by which T163b's live switch reads a pass (rule 2(b)).

Every refusal is an `ExamLedgerRefused` whose `reason` is the code and whose message
starts with `<code>: `.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

EXAM_LEDGER_INVALID: Final = "exam_ledger_invalid"
EXAM_LEDGER_UNREVIEWED: Final = "exam_ledger_unreviewed"
EXAM_LEDGER_MISMATCH: Final = "exam_ledger_mismatch"
EXAM_BUDGET_SPENT: Final = "exam_budget_spent"
EXAM_LEDGER_NOT_APPEND_ONLY: Final = "exam_ledger_not_append_only"

PENDING: Final = "pending"
RUNNING: Final = "running"
UNEXAMINED: Final = "unexamined"
NOT_STARTED: Final = "not_started"
PASSED: Final = "passed"
#: The states `running` moves to once T381's verdict is final.
TERMINAL: Final = frozenset({PASSED, "failed", "failed_futility", "failed_void"})
OUTCOMES: Final = frozenset({PENDING, RUNNING, UNEXAMINED, NOT_STARTED} | TERMINAL)
#: Outcomes whose `window_id` is 0: the paper start has not succeeded.
_NO_WINDOW: Final = frozenset({PENDING, NOT_STARTED})
#: The forward moves rule 3 allows; any other change of `outcome` is refused.
_MOVES: Final = frozenset(
    {(PENDING, RUNNING), (PENDING, NOT_STARTED)} | {(RUNNING, t) for t in TERMINAL}
)
#: The outcomes an entry may be appended with (#3149 item 2).
_FIRST: Final = frozenset({PENDING, UNEXAMINED})
#: The only fields of an entry that ever change.
_MUTABLE: Final = ("window_id", "outcome", "outcome_on")
_NONE_KILL_LINE: Final = "none"

_STR_FIELDS: Final = (
    "hypothesis",
    "hypothesis_fingerprint",
    "family",
    "book_id",
    "kill_line",
    "outcome",
)
_INT_FIELDS: Final = ("seq", "window_id", "campaign_k")
_DATE_FIELDS: Final = ("start_date", "outcome_on")
_ENTRY_FIELDS: Final = frozenset(_STR_FIELDS + _INT_FIELDS + _DATE_FIELDS)
_TOP_FIELDS: Final = frozenset({"budget", "budget_adr", "exam"})


class ExamLedgerRefused(ValueError):
    """The exam ledger refused a load, an exam start or a PR's copy. `reason` is one
    of the module's codes; the message (this exception's `str()`) starts with it."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


@dataclass(frozen=True)
class ExamEntry:
    """One `[[exam]]` entry (rule 3's fields)."""

    seq: int
    hypothesis: str
    hypothesis_fingerprint: str
    family: str
    book_id: str
    window_id: int
    start_date: dt.date
    kill_line: str
    campaign_k: int
    outcome: str
    outcome_on: dt.date


@dataclass(frozen=True)
class ExamLedger:
    """One copy of the ledger: the budget of record, its entries in file order, and
    the text it was loaded from (compared byte for byte with `origin/main`'s copy)."""

    budget: int
    budget_adr: str
    entries: tuple[ExamEntry, ...]
    text: str


def passed_families(ledger: ExamLedger) -> frozenset[str]:
    """The families with a ledger entry marked `passed` (ADR 0016 #3146 rule 2(b))."""
    return frozenset(e.family for e in ledger.entries if e.outcome == PASSED)


def latest_campaign_ks(ledger: ExamLedger) -> dict[str, int]:
    """Each family's latest entry's `campaign_k`, leaving out a family whose latest
    entry records -1 (not recorded). A diagnostic only (#3146)."""
    latest = {e.family: e.campaign_k for e in ledger.entries}
    return {family: k for family, k in latest.items() if k != -1}


@dataclass(frozen=True)
class StoreExamWindow:
    """One exam window of the store (rule 1), as the edge reads it from
    `paper_windows`; the caller passes them in `started_at` order."""

    window_id: int
    hypothesis_fingerprint: str
    book_id: str
    start_date: dt.date


@dataclass(frozen=True)
class ExamStart:
    """The exam start being checked: its hypothesis fingerprint, book and `T_0`."""

    hypothesis_fingerprint: str
    book_id: str
    start_date: dt.date


def _invalid(detail: str) -> ExamLedgerRefused:
    return ExamLedgerRefused(EXAM_LEDGER_INVALID, detail)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_date(value: object) -> bool:
    return isinstance(value, dt.date) and not isinstance(value, dt.datetime)


def _is_url(value: str) -> bool:
    if any(c.isspace() for c in value):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.netloc)


def _entry(index: int, raw: object) -> ExamEntry:
    where = f"[[exam]] number {index + 1}"
    if not isinstance(raw, Mapping):
        raise _invalid(f"{where} is not a table")
    keys = set(raw)
    if missing := sorted(_ENTRY_FIELDS - keys):
        raise _invalid(f"{where} is missing {', '.join(missing)}")
    if unknown := sorted(keys - _ENTRY_FIELDS):
        raise _invalid(f"{where} has unknown field(s) {', '.join(unknown)}")
    for name in _STR_FIELDS:
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise _invalid(f"{where}: {name} must be a non-empty string")
    for name in _INT_FIELDS:
        if not _is_int(raw[name]):
            raise _invalid(f"{where}: {name} must be an integer")
    for name in _DATE_FIELDS:
        if not _is_date(raw[name]):
            raise _invalid(f"{where}: {name} must be a TOML date")
    entry = ExamEntry(**{name: raw[name] for name in _ENTRY_FIELDS})
    _check_entry(where, entry)
    return entry


def _check_entry(where: str, entry: ExamEntry) -> None:
    if entry.outcome not in OUTCOMES:
        raise _invalid(f"{where}: unknown outcome {entry.outcome!r}")
    if entry.window_id < 0:
        raise _invalid(f"{where}: window_id {entry.window_id} is negative")
    if entry.outcome in _NO_WINDOW and entry.window_id != 0:
        raise _invalid(f"{where}: a {entry.outcome} entry must have window_id 0")
    if entry.outcome not in _NO_WINDOW and entry.window_id == 0:
        raise _invalid(f"{where}: a {entry.outcome} entry needs its store window_id")
    if entry.kill_line == _NONE_KILL_LINE:
        if entry.outcome != UNEXAMINED:
            raise _invalid(f'{where}: kill_line "none" is for an unexamined entry only')
    elif not _is_url(entry.kill_line):
        raise _invalid(f"{where}: kill_line {entry.kill_line!r} is not an https URL")
    if entry.campaign_k < -1:
        raise _invalid(f"{where}: campaign_k {entry.campaign_k} is below -1")
    if entry.outcome in (PENDING, RUNNING) and entry.outcome_on != entry.start_date:
        raise _invalid(f"{where}: a {entry.outcome} entry's outcome_on must be its start_date")
    if entry.outcome in TERMINAL | {UNEXAMINED} and entry.outcome_on < entry.start_date:
        raise _invalid(f"{where}: a {entry.outcome} entry's outcome_on is before its start_date")


def load_ledger(text: str) -> ExamLedger:
    """Parse one copy of the exam ledger, refusing `exam_ledger_invalid` on every
    rule-3 invariant one copy can show (module docstring)."""
    try:
        raw: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise _invalid(f"the ledger does not parse ({error})") from None
    if unknown := sorted(set(raw) - _TOP_FIELDS):
        raise _invalid(f"unknown top-level field(s) {', '.join(unknown)}")
    budget = raw.get("budget")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        raise _invalid(f"budget must be an integer of at least 1, not {budget!r}")
    budget_adr = raw.get("budget_adr")
    if not isinstance(budget_adr, str) or not budget_adr.strip():
        raise _invalid("budget_adr must be a non-empty string")
    rows = raw.get("exam", [])
    if not isinstance(rows, list):
        raise _invalid("exam must be an array of [[exam]] tables")
    entries = tuple(_entry(i, row) for i, row in enumerate(rows))
    for index, entry in enumerate(entries):
        if entry.seq != index + 1:
            raise _invalid(f"seq is not contiguous from 1: entry {index + 1} has seq {entry.seq}")
    seen: set[int] = set()
    for entry in entries:
        if entry.window_id != 0 and entry.window_id in seen:
            raise _invalid(f"window_id {entry.window_id} is repeated")
        seen.add(entry.window_id)
    return ExamLedger(budget=budget, budget_adr=budget_adr, entries=entries, text=text)


def counted(ledger: ExamLedger) -> tuple[ExamEntry, ...]:
    """The entries that count toward the budget: every one that is not `not_started`
    (`pending` included, rule 4's `exam_budget_spent`)."""
    return tuple(e for e in ledger.entries if e.outcome != NOT_STARTED)


def _mismatch(detail: str) -> ExamLedgerRefused:
    return ExamLedgerRefused(EXAM_LEDGER_MISMATCH, detail)


def _key(window: StoreExamWindow | ExamEntry) -> tuple[int, str, str, dt.date]:
    return (window.window_id, window.hypothesis_fingerprint, window.book_id, window.start_date)


def check_exam_start(
    ledger: ExamLedger,
    committed_text: str | None,
    store_windows: Sequence[StoreExamWindow],
    start: ExamStart,
    *,
    max_count: int,
) -> None:
    """Rule 4 for one exam start, after `load_ledger` passed: refuse
    `exam_ledger_unreviewed`, `exam_ledger_mismatch` or `exam_budget_spent`, in that
    order (module docstring). `committed_text` is `origin/main`'s copy, `None` when
    git could not be read; `store_windows` are the store's exam windows in
    `started_at` order; `max_count` is `paper.exam_max_count`."""
    if committed_text is None:
        raise ExamLedgerRefused(
            EXAM_LEDGER_UNREVIEWED, "git could not be read for origin/main's copy"
        )
    if ledger.text != committed_text:
        raise ExamLedgerRefused(
            EXAM_LEDGER_UNREVIEWED, "the ledger read differs from the committed copy on origin/main"
        )
    if max_count > ledger.budget:
        raise _mismatch(
            f"paper.exam_max_count {max_count} is above the ledger's budget {ledger.budget}"
        )
    started = [e for e in ledger.entries if e.outcome not in _NO_WINDOW]
    if len(started) != len(store_windows):
        raise _mismatch(
            f"the store has {len(store_windows)} exam window(s), "
            f"the ledger {len(started)} started entries"
        )
    for entry, window in zip(started, store_windows, strict=True):
        if _key(entry) != _key(window):
            raise _mismatch(
                f"ledger entry seq {entry.seq} {_key(entry)} "
                f"does not match store window {_key(window)}"
            )
    pending = [e for e in ledger.entries if e.outcome == PENDING]
    if len(pending) != 1:
        raise _mismatch(f"exactly one pending entry must exist, found {len(pending)}")
    (entry,) = pending
    wanted = (start.hypothesis_fingerprint, start.book_id, start.start_date)
    if (entry.hypothesis_fingerprint, entry.book_id, entry.start_date) != wanted:
        raise _mismatch(f"the pending entry seq {entry.seq} does not name this start {wanted}")
    limit = min(max_count, ledger.budget)
    if (count := len(counted(ledger))) > limit:
        raise ExamLedgerRefused(EXAM_BUDGET_SPENT, f"{count} exams counted, budget {limit}")


def _not_append_only(detail: str) -> ExamLedgerRefused:
    return ExamLedgerRefused(EXAM_LEDGER_NOT_APPEND_ONLY, detail)


def _fixed(entry: ExamEntry) -> tuple[object, ...]:
    return tuple(getattr(entry, name) for name in sorted(_ENTRY_FIELDS - set(_MUTABLE)))


def _identity(entry: ExamEntry) -> tuple[object, ...]:
    """The fixed fields without `seq`: what follows an entry that was moved."""
    return tuple(getattr(entry, n) for n in sorted(_ENTRY_FIELDS - set(_MUTABLE) - {"seq"}))


def _check_kept(old: ExamEntry, new: ExamEntry) -> None:
    where = f"entry seq {old.seq}"
    if _fixed(old) != _fixed(new):
        changed = sorted(
            n for n in _ENTRY_FIELDS - set(_MUTABLE) if getattr(old, n) != getattr(new, n)
        )
        raise _not_append_only(
            f"{where}: {', '.join(changed)} changed (only {', '.join(_MUTABLE)} may)"
        )
    if old.window_id != new.window_id and old.window_id != 0:
        raise _not_append_only(
            f"{where}: window_id changed again ({old.window_id} -> {new.window_id})"
        )
    if old.outcome == new.outcome:
        if old.outcome_on != new.outcome_on:
            raise _not_append_only(f"{where}: outcome_on changed without an outcome change")
        return
    if (old.outcome, new.outcome) not in _MOVES:
        raise _not_append_only(f"{where}: outcome may not move {old.outcome} -> {new.outcome}")


def check_append_only(old_text: str, new_text: str) -> None:
    """Refuse a PR's copy of the ledger (`new_text`) that is not an append-only change
    of `origin/main`'s copy (`old_text`), rule 3 (module docstring). Comments and
    layout may change; the parsed values are compared."""
    old, new = load_ledger(old_text), load_ledger(new_text)
    if new.budget != old.budget and new.budget_adr == old.budget_adr:
        raise _not_append_only(f"budget changed {old.budget} -> {new.budget} without budget_adr")
    if len(new.entries) < len(old.entries):
        raise _not_append_only(f"{len(old.entries) - len(new.entries)} entry(ies) removed")
    old_seq: dict[tuple[object, ...], int] = {}
    for entry in old.entries:
        old_seq.setdefault(_identity(entry), entry.seq)
    for old_entry, new_entry in zip(old.entries, new.entries, strict=False):
        # A retried start repeats an entry's identity (a `not_started` one, then a
        # fresh `pending` one), so "moved" is read only where the identity changed.
        moved = (
            None
            if _identity(new_entry) == _identity(old_entry)
            else old_seq.get(_identity(new_entry))
        )
        if moved is not None:
            raise _not_append_only(f"entry seq {moved} was moved to seq {new_entry.seq}")
        _check_kept(old_entry, new_entry)
    for entry in new.entries[len(old.entries) :]:
        if entry.outcome not in _FIRST:
            raise _not_append_only(
                f"entry seq {entry.seq} is appended as {entry.outcome}; "
                "only pending or unexamined may be"
            )


def passed_window_ids(ledger: ExamLedger) -> frozenset[int]:
    """The window ids whose ledger entry is `passed` (rule 2(b), for T163b)."""
    return frozenset(e.window_id for e in ledger.entries if e.outcome == PASSED)
