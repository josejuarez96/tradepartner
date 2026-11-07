"""The exam ledger's loader, store check and append-only check (plan T381b; ADR 0016,
amendment 2026-11-07, #3146, rules 3 and 4; #3149 item 2)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tradepartner.execution.exam_ledger import (
    EXAM_BUDGET_SPENT,
    EXAM_LEDGER_INVALID,
    EXAM_LEDGER_MISMATCH,
    EXAM_LEDGER_NOT_APPEND_ONLY,
    EXAM_LEDGER_UNREVIEWED,
    ExamLedgerRefused,
    ExamStart,
    StoreExamWindow,
    check_append_only,
    check_exam_start,
    load_ledger,
    passed_window_ids,
)

_ROOT = Path(__file__).resolve().parents[2]
_URL = "https://github.com/josejuarez96/tradepartner/issues/9#issuecomment-1"
_HEAD = 'budget = 4\nbudget_adr = "docs/decisions/0016.md#amendment"\n'

_DEFAULTS: dict[str, object] = {
    "hypothesis": "h",
    "hypothesis_fingerprint": "fp",
    "family": "momentum",
    "book_id": "book",
    "window_id": 0,
    "start_date": dt.date(2027, 1, 4),
    "kill_line": _URL,
    "campaign_k": -1,
    "outcome": "pending",
}


def _value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str):
        return f'"{value}"'
    raise TypeError(value)


def _entry(seq: int, **fields: object) -> dict[str, object]:
    entry: dict[str, object] = {"seq": seq, **_DEFAULTS, **fields}
    if "outcome_on" not in fields:
        entry["outcome_on"] = entry["start_date"]
    return entry


def _text(*entries: dict[str, object], head: str = _HEAD, drop: str = "") -> str:
    parts = [head]
    for entry in entries:
        parts.append("\n[[exam]]\n")
        for key, value in entry.items():
            if key != drop:
                parts.append(f"{key} = {_value(value)}\n")
    return "".join(parts)


def _refused(reason: str) -> pytest.RaisesExc[ExamLedgerRefused]:
    return pytest.raises(ExamLedgerRefused, match=rf"^{reason}: ")


def _ran(seq: int, window_id: int, outcome: str, day: int) -> dict[str, object]:
    return _entry(
        seq,
        hypothesis=f"h{seq}",
        hypothesis_fingerprint=f"fp{seq}",
        book_id=f"book{seq}",
        window_id=window_id,
        start_date=dt.date(2027, 1, day),
        outcome=outcome,
        outcome_on=dt.date(2027, 6, day) if outcome != "running" else dt.date(2027, 1, day),
    )


def _window(seq: int, window_id: int, day: int) -> StoreExamWindow:
    return StoreExamWindow(
        window_id=window_id,
        hypothesis_fingerprint=f"fp{seq}",
        book_id=f"book{seq}",
        start_date=dt.date(2027, 1, day),
    )


_START = ExamStart(hypothesis_fingerprint="fpN", book_id="bookN", start_date=dt.date(2027, 3, 1))


def _pending(seq: int) -> dict[str, object]:
    return _entry(
        seq,
        hypothesis="hN",
        hypothesis_fingerprint="fpN",
        book_id="bookN",
        start_date=dt.date(2027, 3, 1),
    )


def _start(text: str, windows: list[StoreExamWindow], *, max_count: int = 4) -> None:
    check_exam_start(load_ledger(text), text, windows, _START, max_count=max_count)


# --- load_ledger --------------------------------------------------------------------


def test_committed_ledger_loads_empty_with_budget_four() -> None:
    ledger = load_ledger((_ROOT / "docs/research/exam-ledger.toml").read_text())
    assert ledger.entries == ()
    assert ledger.budget == 4
    assert ledger.budget_adr.startswith("docs/decisions/0016-")
    assert passed_window_ids(ledger) == frozenset()


def test_a_valid_entry_loads() -> None:
    ledger = load_ledger(_text(_entry(1), _ran(2, 7, "passed", 5)))
    assert [e.seq for e in ledger.entries] == [1, 2]
    assert ledger.entries[1].window_id == 7
    assert ledger.entries[1].start_date == dt.date(2027, 1, 5)
    assert passed_window_ids(ledger) == frozenset({7})


def test_unparsable_text_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger("budget = = 4")


@pytest.mark.parametrize("field", ["seq", "hypothesis", "window_id", "kill_line", "outcome_on"])
def test_a_missing_entry_field_is_invalid(field: str) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1), drop=field))


@pytest.mark.parametrize(
    "head",
    ['budget_adr = "x"\n', "budget = 4\n", 'budget = 4\nbudget_adr = ""\n'],
)
def test_a_missing_or_empty_top_level_field_is_invalid(head: str) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(head=head))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seq", "1"),
        ("seq", True),
        ("window_id", 1.0),
        ("hypothesis", 3),
        ("hypothesis_fingerprint", ""),
        ("start_date", "2027-01-04"),
        ("campaign_k", "7"),
        ("outcome_on", 20270104),
    ],
)
def test_an_ill_typed_entry_field_is_invalid(field: str, value: object) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1) | {field: value}))


def test_a_datetime_start_date_is_invalid() -> None:
    text = _text(_entry(1)).replace("start_date = 2027-01-04", "start_date = 2027-01-04T00:00:00Z")
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(text)


def test_an_unknown_entry_field_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, note="x")))


def test_an_unknown_top_level_field_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(head=_HEAD + "extra = 1\n"))


def test_exam_not_an_array_of_tables_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_HEAD + "exam = 3\n")


@pytest.mark.parametrize("seqs", [[2], [1, 3], [1, 1], [2, 1]])
def test_a_gap_in_seq_is_invalid(seqs: list[int]) -> None:
    entries = [_ran(s, 10 + i, "failed", 1 + i) for i, s in enumerate(seqs)]
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(*entries))


def test_a_repeated_non_zero_window_id_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_ran(1, 7, "failed", 4), _ran(2, 7, "running", 5)))


def test_repeated_zero_window_ids_load() -> None:
    ledger = load_ledger(_text(_entry(1, outcome="not_started"), _entry(2)))
    assert [e.window_id for e in ledger.entries] == [0, 0]


def test_an_unknown_outcome_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, outcome="abandoned")))


@pytest.mark.parametrize("outcome", ["pending", "not_started"])
def test_pending_or_not_started_with_a_window_id_is_invalid(outcome: str) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, outcome=outcome, window_id=3)))


@pytest.mark.parametrize(
    "outcome",
    ["running", "passed", "failed", "failed_futility", "failed_void", "unexamined"],
)
def test_running_terminal_or_unexamined_with_window_id_zero_is_invalid(outcome: str) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, outcome=outcome, window_id=0)))


def test_a_negative_window_id_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, outcome="running", window_id=-1)))


@pytest.mark.parametrize("outcome", ["pending", "running", "passed", "not_started"])
def test_kill_line_none_outside_unexamined_is_invalid(outcome: str) -> None:
    window_id = 0 if outcome in ("pending", "not_started") else 3
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, outcome=outcome, window_id=window_id, kill_line="none")))


def test_kill_line_none_on_unexamined_loads() -> None:
    entry = _ran(1, 3, "unexamined", 4)
    entry["kill_line"] = "none"
    assert load_ledger(_text(entry)).entries[0].kill_line == "none"


@pytest.mark.parametrize("kill_line", ["issue 9", "", "https://", "ftp://x/y", "https://a b"])
def test_a_kill_line_that_is_not_a_url_is_invalid(kill_line: str) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, kill_line=kill_line)))


def test_campaign_k_below_minus_one_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, campaign_k=-2)))


def test_campaign_k_minus_one_and_seven_load() -> None:
    ledger = load_ledger(
        _text(_entry(1, campaign_k=-1, outcome="not_started"), _entry(2, campaign_k=7))
    )
    assert [e.campaign_k for e in ledger.entries] == [-1, 7]


@pytest.mark.parametrize("budget", ["0", "-1", "4.0", '"4"', "true"])
def test_budget_below_one_or_not_an_integer_is_invalid(budget: str) -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(f'budget = {budget}\nbudget_adr = "x"\n')


@pytest.mark.parametrize("outcome", ["pending", "running"])
def test_outcome_on_not_the_start_date_while_pending_or_running_is_invalid(outcome: str) -> None:
    window_id = 0 if outcome == "pending" else 3
    entry = _entry(1, outcome=outcome, window_id=window_id, outcome_on=dt.date(2027, 2, 1))
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(entry))


# --- check_exam_start ---------------------------------------------------------------


def test_the_first_pending_entry_on_an_empty_store_is_accepted() -> None:
    _start(_text(_pending(1)), [])


def test_text_unequal_to_the_committed_copy_is_unreviewed() -> None:
    text = _text(_pending(1))
    with _refused(EXAM_LEDGER_UNREVIEWED):
        check_exam_start(load_ledger(text), _HEAD, [], _START, max_count=4)


def test_a_lagging_checkout_is_unreviewed() -> None:
    local = _text(_ran(1, 7, "running", 4), _pending(2))
    committed = _text(_ran(1, 7, "failed", 4), _pending(2))
    with _refused(EXAM_LEDGER_UNREVIEWED):
        check_exam_start(load_ledger(local), committed, [_window(1, 7, 4)], _START, max_count=4)


def test_git_unreadable_is_unreviewed() -> None:
    text = _text(_pending(1))
    with _refused(EXAM_LEDGER_UNREVIEWED):
        check_exam_start(load_ledger(text), None, [], _START, max_count=4)


def test_store_windows_match_one_to_one_in_order_unexamined_included() -> None:
    unexamined = _ran(2, 8, "unexamined", 5)
    unexamined["kill_line"] = "none"
    text = _text(
        _ran(1, 7, "failed", 4),
        _entry(2, outcome="not_started") | {"seq": 2},
        unexamined | {"seq": 3},
        _pending(4),
    )
    _start(text, [_window(1, 7, 4), _window(2, 8, 5)])


@pytest.mark.parametrize(
    "windows",
    [
        pytest.param([], id="a ledger entry with no store window (rebuilt store)"),
        pytest.param(
            [_window(1, 7, 4), _window(2, 8, 5), _window(3, 9, 6)],
            id="a store window with no entry",
        ),
        pytest.param([_window(2, 8, 5), _window(1, 7, 4)], id="out of start order"),
        pytest.param([_window(1, 70, 4), _window(2, 8, 5)], id="window_id"),
        pytest.param(
            [StoreExamWindow(7, "other", "book1", dt.date(2027, 1, 4)), _window(2, 8, 5)],
            id="fingerprint",
        ),
        pytest.param(
            [StoreExamWindow(7, "fp1", "other", dt.date(2027, 1, 4)), _window(2, 8, 5)], id="book"
        ),
        pytest.param([_window(1, 7, 9), _window(2, 8, 5)], id="start_date"),
    ],
)
def test_each_store_mismatch_kind_is_refused(windows: list[StoreExamWindow]) -> None:
    text = _text(_ran(1, 7, "failed", 4), _ran(2, 8, "running", 5), _pending(3))
    with _refused(EXAM_LEDGER_MISMATCH):
        _start(text, windows)


def test_no_pending_entry_is_a_mismatch() -> None:
    with _refused(EXAM_LEDGER_MISMATCH):
        _start(_text(_ran(1, 7, "failed", 4)), [_window(1, 7, 4)])


def test_two_pending_entries_are_a_mismatch() -> None:
    with _refused(EXAM_LEDGER_MISMATCH):
        _start(_text(_pending(1), _pending(2)), [])


@pytest.mark.parametrize(
    "field",
    [
        ("hypothesis_fingerprint", "other"),
        ("book_id", "other"),
        ("start_date", dt.date(2027, 3, 2)),
    ],
)
def test_a_pending_entry_naming_another_start_is_a_mismatch(field: tuple[str, object]) -> None:
    key, value = field
    entry = _pending(1) | {key: value}
    if key == "start_date":
        entry["outcome_on"] = value
    with _refused(EXAM_LEDGER_MISMATCH):
        _start(_text(entry), [])


def test_max_count_above_budget_is_a_mismatch() -> None:
    head = 'budget = 3\nbudget_adr = "x"\n'
    with _refused(EXAM_LEDGER_MISMATCH):
        _start(_text(_pending(1), head=head), [], max_count=4)


_FOUR = [
    _ran(1, 7, "passed", 4),
    _ran(2, 8, "failed", 5),
    _ran(3, 9, "failed_futility", 6),
    _ran(4, 10, "failed_void", 7),
]
_FOUR_WINDOWS = [_window(1, 7, 4), _window(2, 8, 5), _window(3, 9, 6), _window(4, 10, 7)]


def test_four_counted_plus_a_fifth_pending_is_budget_spent() -> None:
    with _refused(EXAM_BUDGET_SPENT):
        _start(_text(*_FOUR, _pending(5)), _FOUR_WINDOWS)


def test_a_budget_of_five_with_max_count_four_still_refuses_the_fifth() -> None:
    head = 'budget = 5\nbudget_adr = "x"\n'
    with _refused(EXAM_BUDGET_SPENT):
        _start(_text(*_FOUR, _pending(5), head=head), _FOUR_WINDOWS, max_count=4)


def test_the_fourth_pending_is_accepted() -> None:
    _start(_text(*_FOUR[:3], _pending(4)), _FOUR_WINDOWS[:3])


def test_a_not_started_entry_frees_its_slot() -> None:
    not_started = _entry(4, outcome="not_started")
    _start(_text(*_FOUR[:3], not_started, _pending(5)), _FOUR_WINDOWS[:3])


def test_an_unexamined_entry_takes_a_slot() -> None:
    unexamined = _ran(4, 10, "unexamined", 7)
    unexamined["kill_line"] = "none"
    with _refused(EXAM_BUDGET_SPENT):
        _start(_text(*_FOUR[:3], unexamined, _pending(5)), _FOUR_WINDOWS)


# --- check_append_only --------------------------------------------------------------


def _ok(old: str, new: str) -> None:
    check_append_only(old, new)


def _bad(old: str, new: str) -> None:
    with _refused(EXAM_LEDGER_NOT_APPEND_ONLY):
        check_append_only(old, new)


def test_an_unchanged_ledger_and_a_comment_edit_are_accepted() -> None:
    old = _text(_pending(1))
    _ok(old, old)
    _ok(old, "# a comment\n" + old)


def test_appending_a_pending_entry_is_accepted() -> None:
    _ok(_HEAD, _text(_entry(1)))
    _ok(_text(_ran(1, 7, "failed", 4)), _text(_ran(1, 7, "failed", 4), _pending(2)))


def test_appending_an_unexamined_entry_is_accepted() -> None:
    unexamined = _ran(1, 7, "unexamined", 4)
    unexamined["kill_line"] = "none"
    _ok(_HEAD, _text(unexamined))


@pytest.mark.parametrize(
    "outcome", ["running", "passed", "failed", "failed_futility", "failed_void", "not_started"]
)
def test_appending_an_entry_not_pending_or_unexamined_is_refused(outcome: str) -> None:
    window_id = 0 if outcome == "not_started" else 7
    _bad(_HEAD, _text(_ran(1, window_id, outcome, 4)))


def test_pending_to_running_with_window_id_set_is_accepted() -> None:
    _ok(_text(_pending(1)), _text(_pending(1) | {"outcome": "running", "window_id": 7}))


def test_pending_to_not_started_is_accepted() -> None:
    _ok(
        _text(_pending(1)),
        _text(_pending(1) | {"outcome": "not_started", "outcome_on": dt.date(2027, 3, 2)}),
    )


@pytest.mark.parametrize("terminal", ["passed", "failed", "failed_futility", "failed_void"])
def test_running_to_each_terminal_state_is_accepted(terminal: str) -> None:
    old = _ran(1, 7, "running", 4)
    _ok(_text(old), _text(old | {"outcome": terminal, "outcome_on": dt.date(2028, 1, 4)}))


def test_a_deleted_entry_is_refused() -> None:
    _bad(_text(_ran(1, 7, "failed", 4), _pending(2)), _text(_ran(1, 7, "failed", 4)))


def test_a_reorder_is_refused() -> None:
    first, second = _ran(1, 7, "failed", 4), _ran(2, 8, "running", 5)
    _bad(_text(first, second), _text(second | {"seq": 1}, first | {"seq": 2}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kill_line", "https://github.com/x/y/issues/1#issuecomment-2"),
        ("hypothesis", "other"),
        ("hypothesis_fingerprint", "other"),
        ("family", "other"),
        ("book_id", "other"),
        ("campaign_k", 3),
    ],
)
def test_an_edited_fixed_field_is_refused(field: str, value: object) -> None:
    _bad(_text(_pending(1)), _text(_pending(1) | {field: value}))


def test_an_edited_start_date_is_refused() -> None:
    day = dt.date(2027, 3, 2)
    _bad(_text(_pending(1)), _text(_pending(1) | {"start_date": day, "outcome_on": day}))


def test_running_to_not_started_with_window_id_reset_is_refused() -> None:
    old = _ran(1, 7, "running", 4)
    _bad(_text(old), _text(old | {"outcome": "not_started", "window_id": 0}))


def test_a_second_window_id_change_is_refused() -> None:
    old = _ran(1, 7, "running", 4)
    _bad(_text(old), _text(old | {"window_id": 8}))


@pytest.mark.parametrize("new", ["failed", "passed", "running", "unexamined"])
def test_a_terminal_outcome_changed_is_refused(new: str) -> None:
    old = _ran(1, 7, "failed_futility", 4)
    entry = old | {"outcome": new}
    if new == "running":
        entry["outcome_on"] = entry["start_date"]
    _bad(_text(old), _text(entry))


def test_a_terminal_outcome_on_rewritten_is_refused() -> None:
    old = _ran(1, 7, "failed", 4)
    _bad(_text(old), _text(old | {"outcome_on": dt.date(2027, 7, 1)}))


def test_pending_straight_to_a_terminal_state_is_refused() -> None:
    _bad(_text(_pending(1)), _text(_pending(1) | {"outcome": "passed", "window_id": 7}))


def test_not_started_to_running_is_refused() -> None:
    old = _entry(1, outcome="not_started")
    _bad(_text(old), _text(old | {"outcome": "running", "window_id": 7}))


def test_budget_changed_without_budget_adr_is_refused() -> None:
    _bad(_HEAD, _HEAD.replace("budget = 4", "budget = 5"))


def test_budget_changed_with_budget_adr_is_accepted() -> None:
    new = 'budget = 5\nbudget_adr = "docs/decisions/0016.md#amendment-2027"\n'
    _ok(_HEAD, new)


def test_an_invalid_new_copy_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        check_append_only(_HEAD, "budget = = 4")


def test_a_deleted_file_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        check_append_only(_HEAD, "")


def test_refusals_carry_a_reason() -> None:
    with pytest.raises(ExamLedgerRefused) as caught:
        load_ledger("x")
    assert caught.value.reason == EXAM_LEDGER_INVALID


def test_a_retried_start_after_not_started_is_append_only() -> None:
    first = _pending(1) | {"outcome": "not_started"}
    retry = _pending(2)
    old = _text(first, retry)
    _ok(old, old)
    _ok(old, _text(first, retry | {"outcome": "running", "window_id": 7}))


def test_swapping_a_retried_pair_is_still_refused() -> None:
    first = _pending(1) | {"outcome": "not_started"}
    retry = _pending(2)
    _bad(_text(first, retry), _text(retry | {"seq": 1}, first | {"seq": 2}))


def test_a_whitespace_only_string_is_invalid() -> None:
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(_entry(1, book_id="  ")))


@pytest.mark.parametrize("outcome", ["failed", "unexamined"])
def test_a_terminal_outcome_on_before_start_date_is_invalid(outcome: str) -> None:
    entry = _ran(1, 7, outcome, 4) | {"outcome_on": dt.date(2027, 1, 3), "kill_line": "none"}
    if outcome != "unexamined":
        entry["kill_line"] = _URL
    with _refused(EXAM_LEDGER_INVALID):
        load_ledger(_text(entry))
