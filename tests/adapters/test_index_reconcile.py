"""T319b: the settled-quarter reconcile, pure (#2629, #2703, owner answers on #2659)."""

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradepartner.adapters import index_reconcile
from tradepartner.adapters.form4_route import Quarter
from tradepartner.adapters.index_reconcile import (
    FileRow,
    StoredRow,
    etag_changed_quarters,
    quarterly_listed_on,
    reconcile_quarter,
    select_quarters,
)

Q = Quarter(2026, 1)
KEPT = frozenset({"25", "25-NSE"})
ISSUER_FORMS = frozenset({"10-K", "10-Q", "8-K"})


def _file(cik="0000000001", acc="0000000001-26-000001", form="10-K", name="ACME", d=(2026, 2, 3)):
    return FileRow(cik, acc, form, name, date(*d))


def _stored(
    cik="0000000001",
    acc="0000000001-26-000001",
    form="10-K",
    name="ACME",
    d=(2026, 2, 3),
    listed=(2026, 2, 3),
):
    return StoredRow(cik, acc, form, name, date(*d), date(*listed))


def _run(file_rows, stored=(), issuers=("0000000001",), listed=(2026, 4, 20)):
    return reconcile_quarter(
        file_rows,
        quarter=Q,
        listed_on=date(*listed),
        stored=stored,
        issuer_ciks=frozenset(issuers),
        issuer_forms=ISSUER_FORMS,
        kept_forms=KEPT,
    )


def test_row_only_in_file_is_returned_to_add() -> None:
    result = _run([_file()])
    assert [r.accession for r in result.to_add] == ["0000000001-26-000001"]
    assert result.absent == 0
    assert not result.refused and not result.revisions


def test_only_kept_rows_are_compared() -> None:
    rows = [
        _file(cik="0000000009", acc="0000000009-26-000001", form="4"),  # not an issuer, not kept
        _file(cik="0000000009", acc="0000000009-26-000002", form="25"),  # kept form
        _file(cik="0000000001", acc="0000000001-26-000003", form="4"),  # issuer CIK
    ]
    result = _run(rows)
    assert {r.accession for r in result.to_add} == {
        "0000000009-26-000002",
        "0000000001-26-000003",
    }


def test_stored_row_absent_from_file_is_counted_and_kept() -> None:
    stored = [_stored(), _stored(acc="0000000001-26-000002")]
    result = _run([_file()], stored)
    assert result.absent == 1
    assert not result.to_add and not result.refused and not result.revisions


def test_form_or_name_difference_is_refused() -> None:
    stored = [_stored(), _stored(acc="0000000001-26-000002")]
    rows = [_file(form="10-K/A"), _file(acc="0000000001-26-000002", name="OTHER")]
    result = _run(rows, stored)
    assert result.refused_count == 2
    assert not result.to_add and not result.revisions


def test_filed_on_difference_becomes_one_revision() -> None:
    result = _run([_file(d=(2026, 2, 5))], [_stored(listed=(2026, 2, 3))])
    assert len(result.revisions) == 1
    revision = result.revisions[0]
    assert revision.filed_on == date(2026, 2, 5)
    assert revision.listed_on == date(2026, 4, 20)
    assert not result.refused and not result.to_add


def test_equal_filed_on_writes_nothing() -> None:
    result = _run([_file()], [_stored()])
    assert result == _run([_file()], [_stored()])
    assert not (result.to_add or result.revisions or result.refused or result.stale)


def test_row_whose_latest_revision_sits_in_another_quarter_is_matched() -> None:
    """The file still lists the first date; the latest revision moved to Q2."""
    stored = [_stored(d=(2026, 4, 2), listed=(2026, 4, 3))]
    result = _run([_file(d=(2026, 3, 30))], stored, listed=(2026, 4, 20))
    assert not result.to_add
    assert result.absent == 0
    # the file's listing (4-20) is after the revision's (4-3), but the date differs: a revision
    assert len(result.revisions) == 1


def test_row_stored_in_this_quarter_but_moved_out_is_not_absent() -> None:
    """A stored row whose latest revision is in Q2 is not 'held by' Q1, so never absent."""
    stored = [_stored(d=(2026, 4, 2), listed=(2026, 4, 3))]
    result = _run([], stored)
    assert result.absent == 0


def test_file_older_than_a_daily_relisting_returns_no_revision_back() -> None:
    """#2703: a quarterly file whose Last-Modified predates a daily re-listing."""
    stored = [_stored(d=(2026, 2, 5), listed=(2026, 4, 10))]  # daily re-listing on 4-10
    last_modified = datetime(2026, 4, 10, 7, 30, tzinfo=UTC)  # file covers up to 4-09
    listed = quarterly_listed_on(last_modified)
    result = _run([_file(d=(2026, 2, 3))], stored, listed=(listed.year, listed.month, listed.day))
    assert not result.revisions
    assert result.stale == 1


def test_equal_listed_on_never_revises_from_a_quarterly_file() -> None:
    result = _run([_file(d=(2026, 2, 5))], [_stored(listed=(2026, 4, 20))])
    assert not result.revisions
    assert result.stale == 1


def test_quarterly_listed_on_is_day_before_eastern_date_of_last_modified() -> None:
    assert quarterly_listed_on(datetime(2026, 10, 31, 7, 28, tzinfo=UTC)) == date(2026, 10, 30)
    # 02:30 UTC on the 1st is the 31st in New York: the day before is the 30th
    assert quarterly_listed_on(datetime(2026, 11, 1, 2, 30, tzinfo=UTC)) == date(2026, 10, 30)
    with pytest.raises(ValueError):
        quarterly_listed_on(datetime(2026, 10, 31, 7, 28))  # noqa: DTZ001


def test_new_issuer_cik_is_reported_for_the_walk() -> None:
    rows = [
        _file(cik="0000000007", acc="0000000007-26-000001", form="10-K"),
        _file(cik="0000000007", acc="0000000007-26-000002", form="4"),
        _file(cik="0000000008", acc="0000000008-26-000001", form="4"),
    ]
    result = _run(rows)
    assert result.new_issuer_ciks == ("0000000007",)
    assert not result.to_add  # the walk takes the whole CIK


def test_exact_duplicate_lines_are_one_row_and_conflicting_ones_are_refused() -> None:
    result = _run([_file(), _file()])
    assert len(result.to_add) == 1
    result = _run([_file(), _file(form="10-K/A")])
    assert result.refused_count == 1
    assert not result.to_add


def test_same_accession_under_two_ciks_are_two_keys() -> None:
    rows = [
        _file(cik="0000000001", form="25"),
        _file(cik="0000000002", form="25"),
    ]
    assert len(_run(rows).to_add) == 2


def test_no_look_ahead_the_function_reads_no_clock() -> None:
    """The result depends only on its arguments; the module holds no clock call."""
    source = Path(index_reconcile.__file__).read_text()
    calls = {
        ast.unparse(n.func)
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert not calls & {"datetime.now", "datetime.utcnow", "date.today"}
    assert _run([_file(d=(2026, 2, 5))], [_stored()]) == _run([_file(d=(2026, 2, 5))], [_stored()])


def test_revision_listed_on_is_the_files_not_a_later_date() -> None:
    """A revision carries the file's listed_on, so a later run cannot place it in the future."""
    result = _run([_file(d=(2026, 2, 5))], [_stored(listed=(2026, 2, 3))], listed=(2026, 4, 1))
    assert result.revisions[0].listed_on == date(2026, 4, 1)


# --- which quarters to reconcile -----------------------------------------------------------


def _select(now, last_ok=None, changed=(), recent=8, settle_days=3):
    return select_quarters(
        now=now,
        last_ok_run_at=last_ok,
        changed=frozenset(changed),
        settle_days=settle_days,
        first_year=1993,
        recent_quarters=recent,
    )


WEEKDAY = datetime(2026, 11, 3, 23, 0, tzinfo=UTC)  # a Tuesday
SATURDAY = datetime(2026, 10, 31, 22, 30, tzinfo=UTC)  # 18:30 EDT Saturday


def test_quarter_settled_during_a_key_off_gap_is_selected() -> None:
    last_ok = datetime(2026, 9, 28, 22, 0, tzinfo=UTC)  # Q3 settles 2026-10-04 (Eastern)
    result = _select(datetime(2026, 10, 12, 22, 0, tzinfo=UTC), last_ok)
    assert result.quarters == (Quarter(2026, 3),)
    assert result.reasons[Quarter(2026, 3)] == ("settled_since_last_ok",)


def test_quarter_settled_before_last_ok_run_is_not_selected() -> None:
    last_ok = datetime(2026, 10, 10, 22, 0, tzinfo=UTC)
    assert _select(datetime(2026, 10, 12, 22, 0, tzinfo=UTC), last_ok).quarters == ()


def test_unsettled_quarter_is_never_selected() -> None:
    result = _select(datetime(2026, 10, 2, 22, 0, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC))
    assert result.quarters == ()


def test_changed_etag_selects_a_settled_quarter_only() -> None:
    result = _select(WEEKDAY, changed=[Quarter(2025, 4), Quarter(2026, 4)])
    assert result.quarters == (Quarter(2025, 4),)  # 2026 Q4 is open
    assert result.reasons[Quarter(2025, 4)] == ("etag_changed",)


def test_saturday_selects_last_eight_settled_quarters_beside_the_etag_test() -> None:
    """Owner, 2026-10-31 (#2659): every Saturday, whatever the HEAD tests say."""
    result = _select(SATURDAY, changed=[Quarter(2024, 1)])
    assert len(result.quarters) == 9  # eight recent plus the changed older one
    assert Quarter(2026, 3) in result.quarters and Quarter(2024, 4) in result.quarters
    assert Quarter(2024, 3) not in result.quarters
    assert result.reasons[Quarter(2024, 1)] == ("etag_changed",)
    assert result.reasons[Quarter(2026, 3)] == ("saturday",)
    assert result.quarters == tuple(sorted(result.quarters))


def test_saturday_uses_the_eastern_date() -> None:
    friday_night_eastern = datetime(2026, 11, 1, 2, 0, tzinfo=UTC)  # Fri 22:00 EDT? Oct 31 is Sat
    assert not _select(datetime(2026, 10, 31, 2, 0, tzinfo=UTC)).quarters  # Fri in New York
    assert _select(friday_night_eastern).quarters  # Sat in New York (2026-10-31 22:00 EDT)


def test_saturday_count_comes_from_the_argument() -> None:
    assert len(_select(SATURDAY, recent=2).quarters) == 2


def test_a_reason_list_joins_when_several_apply() -> None:
    last_ok = datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
    now = datetime(2026, 10, 10, 22, 0, tzinfo=UTC)  # Saturday in New York
    result = _select(now, last_ok, changed=[Quarter(2026, 3)])
    assert result.reasons[Quarter(2026, 3)] == (
        "settled_since_last_ok",
        "etag_changed",
        "saturday",
    )


def test_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError):
        _select(datetime(2026, 11, 3, 23, 0))  # noqa: DTZ001


def test_etag_compare_ignores_quotes_and_the_gzip_suffix() -> None:
    stored = {Quarter(2026, 1): "abc-gzip", Quarter(2026, 2): '"def"', Quarter(2025, 4): "old"}
    head = {
        Quarter(2026, 1): '"abc"',
        Quarter(2026, 2): "def-gzip",
        Quarter(2025, 4): "new",
        Quarter(2025, 3): "never-seen",
    }
    assert etag_changed_quarters(stored, head) == frozenset({Quarter(2025, 4), Quarter(2025, 3)})
