"""Pure selection of issuer CIKs for the incremental EDGAR rebuild (#2629).

The caller reads and normalizes all state before calling :func:`touched_ciks`.
In particular, clock inputs name CIKs whose relevant source state crossed a
boundary since the last successful index run; this module does no I/O.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class FailureState:
    """One failed-filings entry at a run boundary."""

    cik: str
    quarantined: bool = False
    accepted: bool = False


@dataclass(frozen=True, slots=True)
class PerNameEntry:
    """One config entry's full value and every CIK the entry names."""

    value: str
    ciks: frozenset[str]


@dataclass(frozen=True, slots=True)
class TouchedInputs:
    """Explicit facts and state changes used to select tonight's CIKs.

    ``accession_ciks`` includes every index row listing the accession, not
    only its filer. It expands failures as well as FSN periods. Per-name
    entries hold both the full config value and every CIK they name. An
    edited key or value touches all CIKs it named before or after.
    Snapshot values omit fetch time: identical new fetches are not changes.
    Clock sets are calculated at the source boundary from the prior successful
    run and tonight's source state. ``newly_known_ciks`` covers an acceptance
    previously skipped as ``not_yet_known`` whose stamp is now at or before
    the run clock.
    """

    inserted_ciks: frozenset[str] = frozenset()
    companyfacts_complete: Mapping[str, bool] = field(default_factory=dict)
    statement_complete: Mapping[str, bool] = field(default_factory=dict)
    previous_failures: Mapping[str, FailureState] = field(default_factory=dict)
    current_failures: Mapping[str, FailureState] = field(default_factory=dict)
    filename_incomplete_ciks: frozenset[str] = frozenset()
    new_fsn_accessions: frozenset[str] = frozenset()
    accession_ciks: Mapping[str, frozenset[str]] = field(default_factory=dict)
    previous_snapshot: Mapping[str, frozenset[tuple[str, str]]] = field(default_factory=dict)
    current_snapshot: Mapping[str, frozenset[tuple[str, str]]] = field(default_factory=dict)
    previous_per_name_lists: Mapping[str, Mapping[str, PerNameEntry]] = field(default_factory=dict)
    current_per_name_lists: Mapping[str, Mapping[str, PerNameEntry]] = field(default_factory=dict)
    master_chains_ciks: frozenset[str] = frozenset()
    settled_ciks: frozenset[str] = frozenset()
    lag_window_start_ciks: frozenset[str] = frozenset()
    unread_run_reached_ciks: frozenset[str] = frozenset()
    newly_known_ciks: frozenset[str] = frozenset()
    stamp_changed_ciks: frozenset[str] = frozenset()


def touched_ciks(inputs: TouchedInputs) -> set[str]:
    """Return CIKs to rebuild tonight, excluding every stamp-changed CIK.

    No input is mutated. The caller supplies state from the same source
    snapshot, including all co-registrants for newly published FSN periods.
    """
    touched = set(inputs.inserted_ciks)
    touched.update(cik for cik, complete in inputs.companyfacts_complete.items() if not complete)
    touched.update(cik for cik, complete in inputs.statement_complete.items() if not complete)

    for accession, state in inputs.current_failures.items():
        if not state.quarantined:
            touched.update(inputs.accession_ciks.get(accession, (state.cik,)))
    for accession in inputs.previous_failures.keys() | inputs.current_failures.keys():
        before = inputs.previous_failures.get(accession)
        after = inputs.current_failures.get(accession)
        if before != after:
            touched.update(inputs.accession_ciks.get(accession, ()))
            if before is not None:
                touched.add(before.cik)
            if after is not None:
                touched.add(after.cik)

    touched.update(inputs.filename_incomplete_ciks)
    for accession in inputs.new_fsn_accessions:
        touched.update(inputs.accession_ciks.get(accession, ()))

    for cik in inputs.previous_snapshot.keys() | inputs.current_snapshot.keys():
        if inputs.previous_snapshot.get(cik) != inputs.current_snapshot.get(cik):
            touched.add(cik)

    for name in inputs.previous_per_name_lists.keys() | inputs.current_per_name_lists.keys():
        before_list = inputs.previous_per_name_lists.get(name, {})
        after_list = inputs.current_per_name_lists.get(name, {})
        for entry in before_list.keys() | after_list.keys():
            before_value = before_list.get(entry)
            after_value = after_list.get(entry)
            if before_value != after_value:
                if before_value is not None:
                    touched.update(before_value.ciks)
                if after_value is not None:
                    touched.update(after_value.ciks)

    touched.update(inputs.master_chains_ciks)
    touched.update(inputs.settled_ciks)
    touched.update(inputs.lag_window_start_ciks)
    touched.update(inputs.unread_run_reached_ciks)
    touched.update(inputs.newly_known_ciks)
    return touched - inputs.stamp_changed_ciks
