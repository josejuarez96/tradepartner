"""Step 6 triggers for the incremental EDGAR touched set."""

from dataclasses import replace

from tradepartner.store.touched import FailureState, TouchedInputs, touched_ciks

A = "0000000001"
B = "0000000002"
C = "0000000003"
ACCESSION = "0000000001-26-000001"


def test_no_triggers_touch_nothing() -> None:
    assert touched_ciks(TouchedInputs()) == set()


def test_row_inserted_tonight_daily_reconcile_or_new_issuer_walk() -> None:
    assert touched_ciks(TouchedInputs(inserted_ciks=frozenset({A}))) == {A}


def test_companyfacts_or_statement_cache_incomplete_stays_touched_until_complete() -> None:
    inputs = TouchedInputs(companyfacts_complete={A: False, B: True}, statement_complete={C: False})
    assert touched_ciks(inputs) == {A, C}
    assert touched_ciks(inputs) == {A, C}  # no one-night suppression
    assert touched_ciks(replace(inputs, companyfacts_complete={A: True, B: True})) == {C}
    assert touched_ciks(replace(inputs, statement_complete={C: True})) == {A}


def test_non_quarantined_failed_filings_entry() -> None:
    inputs = TouchedInputs(
        previous_failures={
            "a": FailureState(A),
            "b": FailureState(B, quarantined=True),
            "c": FailureState(C, accepted=True),
        },
        current_failures={
            "a": FailureState(A),
            "b": FailureState(B, quarantined=True),
            "c": FailureState(C, accepted=True),
        },
    )
    assert touched_ciks(inputs) == {A, C}


def test_failure_store_state_changed_owner_cleared_quarantine_or_accepted_filing() -> None:
    before = {
        "a": FailureState(A, quarantined=True),
        "b": FailureState(B),
    }
    after = {"b": FailureState(B, accepted=True)}
    assert touched_ciks(TouchedInputs(previous_failures=before, current_failures=after)) == {A, B}
    assert touched_ciks(TouchedInputs(previous_failures=before, current_failures=before)) == {B}


def test_shared_accession_failure_touches_every_listing_cik() -> None:
    before = {ACCESSION: FailureState(A, quarantined=True)}
    after = {ACCESSION: FailureState(A)}
    inputs = TouchedInputs(
        previous_failures=before,
        current_failures=after,
        accession_ciks={ACCESSION: frozenset({A, B})},
    )
    assert touched_ciks(inputs) == {A, B}


def test_filename_incomplete_ciks() -> None:
    assert touched_ciks(TouchedInputs(filename_incomplete_ciks=frozenset({A}))) == {A}


def test_every_cik_listing_new_fsn_period_accession_including_co_registrants() -> None:
    inputs = TouchedInputs(
        new_fsn_accessions=frozenset({ACCESSION}),
        accession_ciks={ACCESSION: frozenset({A, B}), "old": frozenset({C})},
    )
    assert touched_ciks(inputs) == {A, B}


def test_companies_snapshot_entry_appeared_changed_or_disappeared() -> None:
    before = {A: frozenset({("AAA", "NYSE")}), B: frozenset({("BBB", "NYSE")})}
    after = {A: frozenset({("AAA", "NASDAQ")}), C: frozenset({("CCC", "NYSE")})}
    assert touched_ciks(TouchedInputs(previous_snapshot=before, current_snapshot=after)) == {
        A,
        B,
        C,
    }
    assert touched_ciks(TouchedInputs(previous_snapshot=before, current_snapshot=before)) == set()


def test_per_name_list_edit_diffed_against_last_run_value() -> None:
    before = {"master.keep_successors": {"old": frozenset({A})}}
    after = {"master.keep_successors": {"new": frozenset({B})}}
    assert touched_ciks(
        TouchedInputs(previous_per_name_lists=before, current_per_name_lists=after)
    ) == {A, B}
    assert (
        touched_ciks(TouchedInputs(previous_per_name_lists=before, current_per_name_lists=before))
        == set()
    )


def test_master_chains_entry_ciks_every_night() -> None:
    assert touched_ciks(TouchedInputs(master_chains_ciks=frozenset({A, B}))) == {A, B}


def test_settled_quarter_clock_case() -> None:
    assert touched_ciks(TouchedInputs(settled_ciks=frozenset({A}))) == {A}
    assert touched_ciks(TouchedInputs()) == set()


def test_lag_window_start_clock_case() -> None:
    assert touched_ciks(TouchedInputs(lag_window_start_ciks=frozenset({A}))) == {A}
    assert touched_ciks(TouchedInputs()) == set()


def test_unread_run_reached_clock_case() -> None:
    assert touched_ciks(TouchedInputs(unread_run_reached_ciks=frozenset({A}))) == {A}
    assert touched_ciks(TouchedInputs()) == set()


def test_not_yet_known_acceptance_crosses_run_clock() -> None:
    assert touched_ciks(TouchedInputs(newly_known_ciks=frozenset({A}))) == {A}
    assert touched_ciks(TouchedInputs()) == set()


def test_stamp_changed_cik_is_always_left_out() -> None:
    inputs = TouchedInputs(
        inserted_ciks=frozenset({A, B}),
        companyfacts_complete={A: False},
        settled_ciks=frozenset({A}),
        stamp_changed_ciks=frozenset({A, C}),
    )
    assert touched_ciks(inputs) == {B}
