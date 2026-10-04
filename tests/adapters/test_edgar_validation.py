"""The fetch pass's input-validation collector (#578 part 1).

Fixtures reproduce the shapes of parse failures that crashed past
backfills: #599 (a companyfacts payload with `facts` but no `cik`,
`KeyError: 'cik'`), #609 C3 (a cover page naming 0 entities, `ValueError`),
#609 F3 (an FSN NULL share, `TypeError` from `Decimal(None)`) and #609 C2 (an
iXBRL nil share fact, `decimal.InvalidOperation`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.edgar_raw import InvalidFilingReferenceError
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.edgar_validation import (
    InputValidationError,
    ValidationFailures,
)
from tradepartner.config import Settings

NOW = datetime(2026, 10, 4, 12, 30, tzinfo=UTC)
SECRET = "sk-sentinel-4f2a"

#: #599: CIK 0001786835 (Star Mountain) served `entityName` and `facts`, no `cik`.
KEYLESS_FACTS: Mapping[str, Any] = {
    "entityName": "Star Mountain Lower Middle-Market Capital",
    "facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {}}}},
}
#: #609 C3: a TRIC 10-Q with no listing or shares fact.
EMPTY_COVER = "0002124122-26-000017"


def _settings(tmp_path: Path, **edgar: Any) -> Settings:
    return Settings(
        _env_file=None,
        edgar={"cache_dir": str(tmp_path / "edgar"), **edgar},
        alpaca_api_secret=SECRET,
    )


def _collector(tmp_path: Path) -> ValidationFailures:
    return ValidationFailures(tmp_path / "edgar" / "validation", lambda: NOW)


def _cover_names_no_entity() -> None:
    raise ValueError(f"{EMPTY_COVER}: cover page names 0 entities")


def _record_the_three_crashes(failures: ValidationFailures) -> None:
    member = "companyfacts.zip member"
    assert failures.collect(member, "0001786835", lambda: KEYLESS_FACTS["cik"]) is None
    assert failures.collect("cover page", EMPTY_COVER, _cover_names_no_entity) is None
    assert failures.collect("FSN period", "2026q1", lambda: Decimal(None)) is None  # type: ignore[arg-type]


def _list_file(tmp_path: Path) -> dict[str, Any]:
    (path,) = (tmp_path / "edgar" / "validation").glob("failures-*.json")
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def test_a_parse_that_succeeds_is_returned_and_records_nothing(tmp_path: Path) -> None:
    failures = _collector(tmp_path)
    assert failures.collect("form.idx", "2019q1", lambda: [1, 2]) == [1, 2]
    assert len(failures) == 0
    failures.raise_if_any(_settings(tmp_path), {"empty bulk facts": 62})
    assert not (tmp_path / "edgar" / "validation").exists()  # a clean pass writes no file


def test_three_different_bad_inputs_fail_once_listing_all_three(tmp_path: Path) -> None:
    failures = _collector(tmp_path)
    _record_the_three_crashes(failures)
    assert len(failures) == 3  # the pass went on past each one

    with pytest.raises(InputValidationError) as raised:
        failures.raise_if_any(_settings(tmp_path))
    message = str(raised.value)
    assert message.startswith("EDGAR input validation: 3 input(s) failed to parse")
    assert "by input: FSN period 1, companyfacts.zip member 1, cover page 1" in message
    assert "companyfacts.zip member 0001786835: KeyError: 'cik'" in message
    assert f"cover page {EMPTY_COVER}: ValueError: {EMPTY_COVER}: cover page names 0" in message
    assert "FSN period 2026q1: TypeError: conversion from NoneType to Decimal" in message

    listed = _list_file(tmp_path)
    assert f"full list: {(tmp_path / 'edgar' / 'validation').resolve()}" in message
    assert listed["version"] == 1 and listed["written_at"] == NOW.isoformat()
    assert [(f["input"], f["key"]) for f in listed["failures"]] == [
        ("companyfacts.zip member", "0001786835"),
        ("cover page", EMPTY_COVER),
        ("FSN period", "2026q1"),
    ]


def test_an_ixbrl_nil_share_fact_is_a_parse_failure(tmp_path: Path) -> None:
    """#609 C2: `Decimal("")` raises `InvalidOperation`, an `ArithmeticError`."""
    failures = _collector(tmp_path)
    assert failures.collect("cover page", "0001398344-26-014697", lambda: Decimal("")) is None
    assert next(iter(failures)).error.startswith("InvalidOperation")


@pytest.mark.parametrize(
    "error",
    [
        InvalidFilingReferenceError("path-safety guard tripped"),  # #275: never a parse failure
        OSError("disk full"),
        RuntimeError("network down"),
    ],
)
def test_errors_that_are_not_parse_failures_propagate(tmp_path: Path, error: Exception) -> None:
    failures = _collector(tmp_path)

    def parse() -> None:
        raise error

    with pytest.raises(type(error)):
        failures.collect("form.idx", "2019q1", parse)
    assert len(failures) == 0


def test_the_message_is_bounded_and_the_file_holds_every_failure(tmp_path: Path) -> None:
    failures = _collector(tmp_path)
    for n in range(5):
        failures.record("submissions.zip member", f"000000000{n}", ValueError(f"bad {n}"))

    with pytest.raises(InputValidationError) as raised:
        failures.raise_if_any(_settings(tmp_path, max_validation_listed=2))
    message = str(raised.value)
    assert "first 2: " in message and "bad 1" in message and "bad 2" not in message
    assert message.index("full list: ") < message.index("first 2: ")
    assert len(_list_file(tmp_path)["failures"]) == 5


def test_secrets_and_control_characters_are_cleaned_in_message_and_file(tmp_path: Path) -> None:
    failures = _collector(tmp_path)
    failures.record("cover page", f"key-{SECRET}", ValueError(f"echoed {SECRET}\x1b[31m"))

    with pytest.raises(InputValidationError) as raised:
        failures.raise_if_any(_settings(tmp_path))
    text = json.dumps(_list_file(tmp_path))
    for shown in (str(raised.value), text):
        assert SECRET not in shown and "\x1b" not in shown and "[redacted]" in shown


def test_empty_payload_counts_are_shown_not_failed(tmp_path: Path) -> None:
    """#566/#576: an empty `{}` is counted on the source, never a failure;
    a failing run shows the non-zero counts next to its list."""
    failures = _collector(tmp_path)
    failures.record("cover page", EMPTY_COVER, ValueError("names 0 entities"))
    counted = {"empty bulk facts": 62, "empty API facts": 0}

    with pytest.raises(InputValidationError) as raised:
        failures.raise_if_any(_settings(tmp_path), counted)
    assert "counted, not failed: empty bulk facts 62" in str(raised.value)
    assert "empty API facts" not in str(raised.value)
    assert _list_file(tmp_path)["counted"] == {"empty bulk facts": 62}


def test_the_edgar_source_collects_under_its_cache_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = EdgarFilingSource(settings, clock=lambda: NOW)
    source.validation_failures.record("form.idx", "2019q1", ValueError("bad row"))
    with pytest.raises(InputValidationError, match="full list: "):
        source.validation_failures.raise_if_any(settings)
    assert _list_file(tmp_path)["failures"][0]["key"] == "2019q1"
