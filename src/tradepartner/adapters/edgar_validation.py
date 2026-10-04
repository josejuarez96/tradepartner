"""Collect-and-continue validation of the EDGAR fetch pass (#578, owner
decision (B) 2026-10-04; research #575 pitfall 4).

A parse failure inside the fetch pass is recorded on a `ValidationFailures`
collector as (input, key, error) and the input is treated as absent, so the
pass goes on and every failure of the run is seen in one pass, not one per
rerun. `ingest._prefetch` then calls `raise_if_any` before any store
connection opens: if anything was recorded, the run fails with one
`InputValidationError` whose message is bounded and redacted and names a
JSON file holding the full list. The gate cannot be skipped (a dry run runs
it too), and nothing is quarantined: the owner reads the list and decides
case by case (owner decision, pitfalls 2-3).

A truly empty `{}` payload that the source already treats as "no facts" or
"lists nothing" (#567, #576) is counted on the source, never recorded here;
`raise_if_any` copies those counts into the message and the file so a
failing run shows them next to the failures.

The cost of continuing (owner-accepted): a bad payload is absent for the
rest of the pass, so a failure that depends on it may only show on the
next run.

This module holds the collector and the gate only. The parser call sites
that record onto it are wired separately: bulk and index inputs, then the
per-CIK and per-document inputs (#578 parts 2 and 3).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from tradepartner.adapters.edgar_raw import InvalidFilingReferenceError, write_atomic
from tradepartner.config import Settings, clean_message
from tradepartner.timeutil import ensure_tz_aware_utc

#: Bumped when the list file's layout changes.
LIST_VERSION = 1

#: What a parser raises on a malformed payload: `ValueError` (the parsers'
#: own refusals and JSON decode errors), `KeyError`/`TypeError` (a missing
#: or mistyped field reached unwrapped, #599) and `ArithmeticError`
#: (`decimal.InvalidOperation`, #609 C2). Network and I/O errors are not
#: parse failures and still propagate.
PARSE_ERRORS: tuple[type[Exception], ...] = (ValueError, KeyError, TypeError, ArithmeticError)

_T = TypeVar("_T")


class InputValidationError(RuntimeError):
    """Raised by `ValidationFailures.raise_if_any` when the fetch pass
    recorded at least one input that failed to parse."""


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """One input that failed to parse: what kind of input (`input`, e.g.
    "companyfacts.zip member"), which one (`key`: a CIK, accession, period or
    path) and the error (`ExceptionClass: text`, cleaned)."""

    input: str
    key: str
    error: str


class ValidationFailures:
    """The fetch pass's parse failures, in the order they were recorded.
    `directory` is where `raise_if_any` writes the full list; `clock` names
    the file and stamps it."""

    def __init__(self, directory: Path, clock: Callable[[], datetime]) -> None:
        self._directory = directory
        self._clock = clock
        self._failures: list[ValidationFailure] = []

    def __len__(self) -> int:
        return len(self._failures)

    def __iter__(self) -> Iterator[ValidationFailure]:
        return iter(self._failures)

    def record(self, input: str, key: str, error: BaseException) -> None:
        """Record that `input` `key` failed to parse with `error`. A tripped
        path-safety guard (`InvalidFilingReferenceError`, a `ValueError`) is
        never a parse failure: it is re-raised, never recorded (#275)."""
        if isinstance(error, InvalidFilingReferenceError):
            raise error
        self._failures.append(ValidationFailure(input, key, f"{type(error).__name__}: {error}"))

    def collect(self, input: str, key: str, parse: Callable[[], _T]) -> _T | None:
        """`parse()`, or `None` (the input is absent) after recording a
        `PARSE_ERRORS` failure. A tripped path-safety guard is never a parse
        failure and propagates (#275), as does any other exception."""
        try:
            return parse()
        except PARSE_ERRORS as error:  # `record` re-raises a path-safety trip
            self.record(input, key, error)
            return None

    def raise_if_any(self, settings: Settings, counted: Mapping[str, int] | None = None) -> None:
        """Do nothing when no failure was recorded. Otherwise write the full
        list (and `counted`, the source's not-failed counts such as empty
        `{}` payloads) to a new JSON file under the directory, and raise
        `InputValidationError` naming it. The message puts the file's path
        and the per-input totals first, so `ingest.max_message_chars` never
        cuts them, then the first `edgar.max_validation_listed` failures.
        Every error text is cleaned with `config.clean_message` (secrets
        redacted, control characters replaced, cut to
        `ingest.max_message_chars`), in the file and in the message. A file
        that cannot be written is named as such in the message, which still
        fails the run with every failure counted."""
        if not self._failures:
            return
        failures = [
            ValidationFailure(
                clean_message(f.input, settings),
                clean_message(f.key, settings),
                clean_message(f.error, settings),
            )
            for f in self._failures
        ]
        counts = {name: n for name, n in (counted or {}).items() if n}
        try:
            where = str(self._write(failures, counts))
        except OSError as error:  # the failures still fail the run, unlisted on disk
            where = clean_message(f"not written ({type(error).__name__}: {error})", settings)
        by_input = Counter(f.input for f in failures)
        limit = settings.edgar.max_validation_listed
        parts = [
            f"EDGAR input validation: {len(failures)} input(s) failed to parse, "
            "nothing was written to the store",
            f"full list: {where}",
            "by input: " + ", ".join(f"{name} {n}" for name, n in sorted(by_input.items())),
        ]
        if counts:
            parts.append(
                "counted, not failed: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
            )
        parts.append(
            f"first {min(limit, len(failures))}: "
            + "; ".join(f"{f.input} {f.key}: {f.error}" for f in failures[:limit])
        )
        raise InputValidationError(" | ".join(parts))

    def _write(self, failures: list[ValidationFailure], counted: Mapping[str, int]) -> Path:
        now = ensure_tz_aware_utc(self._clock(), field_name="clock()")
        path = self._directory / f"failures-{now.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        payload = {
            "version": LIST_VERSION,
            "written_at": now.isoformat(),
            "failures": [asdict(f) for f in failures],
            "counted": dict(counted),
        }
        write_atomic(path, json.dumps(payload, indent=1).encode())
        return path.resolve()
