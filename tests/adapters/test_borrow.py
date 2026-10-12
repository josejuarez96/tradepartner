"""Tests for `adapters.borrow`, plan task T171 (#1506): the borrow-status
record, the parser over a payload in the recorder's shape and the pure diff
with its vanished-share guard.

The data-foundation spec's "Borrow status: the table" and "Amendment
2026-10-11 (#1506)" acceptance lines, and ADR 0021's decision points 2 and 3.
Every payload here is **constructed by hand** in the shape of the recorder's
`assets-20261011T080954Z.json.gz` (the asset field names of
`GET /v2/assets`); nothing is recorded from a live endpoint and no real
recorder file is read.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from tradepartner.adapters.borrow import (
    US_EQUITY_CLASS,
    AssetSnapshot,
    BorrowStatus,
    TruncatedSnapshotError,
    parse_assets,
    snapshot_rows,
)

#: Three snapshots a day apart, each fetched one hour after that session's
#: close, so an as-of read at `session_close(S)` sees that day's snapshot.
_FIRST = datetime(2020, 6, 26, 21, 0, tzinfo=UTC)
_SECOND = datetime(2020, 6, 29, 21, 0, tzinfo=UTC)
_THIRD = datetime(2020, 6, 30, 21, 0, tzinfo=UTC)

#: The default `borrow.max_vanished_share` the guard tests are built around.
_MAX_VANISHED_SHARE = 0.05


def _asset(
    asset_id: str,
    symbol: str,
    *,
    asset_class: str = US_EQUITY_CLASS,
    exchange: str = "NYSE",
    status: str = "active",
    tradable: bool = True,
    marginable: bool = True,
    shortable: bool = True,
    easy_to_borrow: bool = True,
    borrow_status: str = "easy_to_borrow",
    fractionable: bool = True,
) -> dict[str, Any]:
    """One entry of `GET /v2/assets` in the recorder's shape, carrying the
    payload's non-stored fields (`name`, `attributes`, the margin
    requirements) so the parser is shown ignoring them."""
    return {
        "id": asset_id,
        "class": asset_class,
        "symbol": symbol,
        "exchange": exchange,
        "status": status,
        "tradable": tradable,
        "marginable": marginable,
        "shortable": shortable,
        "easy_to_borrow": easy_to_borrow,
        "borrow_status": borrow_status,
        "fractionable": fractionable,
        "name": f"{symbol} Holdings Corp",
        "attributes": ["fractional_eh_enabled", "has_options"],
        "maintenance_margin_requirement": 30,
        "margin_requirement_long": "30",
        "margin_requirement_short": "30",
    }


def _record(
    asset_id: str = "b0rr0w000-0000-4000-8000-00000000000a",
    symbol: str = "BNDX",
    *,
    easy_to_borrow: bool = True,
    borrow_status: str = "easy_to_borrow",
    present: bool = True,
    known_at: datetime = _FIRST,
    fetched_at: datetime = _FIRST,
) -> BorrowStatus:
    return BorrowStatus(
        asset_id=asset_id,
        symbol=symbol,
        exchange="NYSE",
        status="active",
        tradable=True,
        marginable=True,
        shortable=True,
        easy_to_borrow=easy_to_borrow,
        borrow_status=borrow_status,
        fractionable=True,
        present=present,
        known_at=known_at,
        fetched_at=fetched_at,
    )


# --- the record ------------------------------------------------------------


def test_the_record_keeps_its_eleven_payload_fields_and_the_two_stamps() -> None:
    record = _record()
    assert record.asset_id == "b0rr0w000-0000-4000-8000-00000000000a"
    assert record.symbol == "BNDX"
    assert (record.exchange, record.status) == ("NYSE", "active")
    assert (record.tradable, record.marginable, record.shortable) == (True, True, True)
    assert (record.easy_to_borrow, record.fractionable) == (True, True)
    assert record.borrow_status == "easy_to_borrow"
    assert record.present is True
    assert (record.known_at, record.fetched_at) == (_FIRST, _FIRST)


def test_the_record_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        _record().present = False  # type: ignore[misc]


@pytest.mark.parametrize("field_name", ["known_at", "fetched_at"])
@pytest.mark.parametrize(
    "bad",
    [
        datetime(2020, 6, 26, 21, 0),  # noqa: DTZ001 - the naive datetime refused
        "2020-06-26T21:00:00+00:00",
        1593205200.0,
        None,
    ],
    ids=["naive", "string", "epoch-float", "none"],
)
def test_the_record_refuses_a_stamp_that_is_not_tz_aware(field_name: str, bad: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        _record(**{field_name: bad})


def test_the_record_refuses_a_known_at_before_its_fetched_at() -> None:
    with pytest.raises(ValueError, match="before"):
        _record(known_at=_FIRST - timedelta(hours=1))
    # The fetch time itself is a legal stamp: `known_at` is the fetch time.
    assert _record(known_at=_FIRST).known_at == _FIRST
    # A later stamp is legal too (an import stamps the import run's clock).
    assert _record(known_at=_FIRST + timedelta(minutes=5)).fetched_at == _FIRST


def test_the_record_refuses_a_stamp_written_as_a_bare_date() -> None:
    with pytest.raises(ValueError, match="known_at"):
        _record(known_at=date(2020, 6, 26))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name",
    ["tradable", "marginable", "shortable", "easy_to_borrow", "fractionable"],
)
def test_the_record_refuses_a_flag_that_is_not_a_boolean(field_name: str) -> None:
    with pytest.raises(TypeError, match=field_name):
        _record(**{field_name: 1})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=field_name):
        _record(**{field_name: "true"})  # type: ignore[arg-type]


def test_the_record_refuses_a_present_that_is_not_a_boolean() -> None:
    with pytest.raises(TypeError, match="present"):
        _record(present=1)  # type: ignore[arg-type]


def test_the_record_refuses_a_non_string_text_field() -> None:
    with pytest.raises(TypeError, match="borrow_status"):
        _record(borrow_status=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="symbol"):
        _record(symbol=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("field_name", ["asset_id", "symbol"])
def test_the_record_refuses_an_empty_id_or_symbol(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        _record(**{field_name: ""})


def test_an_empty_borrow_status_string_is_a_value_not_a_null() -> None:
    """The payload's field absent reads `''`, never NULL (spec "Borrow
    status: the table")."""
    assert _record(borrow_status="").borrow_status == ""


# --- the parser ------------------------------------------------------------


def test_the_parser_keeps_us_equity_only_and_counts_the_rest() -> None:
    payload = [
        _asset("id-a", "AAA"),
        _asset("id-b", "BBB", asset_class="crypto"),
        _asset("id-c", "CCC", asset_class="us_option"),
        _asset("id-d", "DDD"),
        _asset("id-e", "EEE", asset_class="us_option"),
    ]
    snapshot = parse_assets(payload, _FIRST)
    assert isinstance(snapshot, AssetSnapshot)
    assert [record.symbol for record in snapshot.assets] == ["AAA", "DDD"]
    assert snapshot.total == 5
    assert snapshot.us_equity == 2
    assert snapshot.other_class == 3


def test_the_parser_counts_the_other_classes_without_keeping_a_row() -> None:
    snapshot = parse_assets([_asset("id-a", "AAA", asset_class="crypto")], _FIRST)
    assert snapshot.assets == ()
    assert snapshot.other_class == 1
    assert snapshot.us_equity == 0
    assert snapshot.total == 1


def test_the_parser_reads_every_stored_field_verbatim() -> None:
    payload = [
        _asset(
            "id-a",
            "AAA",
            exchange="NASDAQ",
            status="inactive",
            tradable=False,
            marginable=False,
            shortable=False,
            easy_to_borrow=False,
            borrow_status="hard_to_borrow",
            fractionable=False,
        )
    ]
    (record,) = parse_assets(payload, _FIRST).assets
    assert record.asset_id == "id-a"
    assert record.symbol == "AAA"
    assert record.exchange == "NASDAQ"
    assert record.status == "inactive"
    assert (record.tradable, record.marginable) == (False, False)
    assert (record.shortable, record.easy_to_borrow) == (False, False)
    assert record.fractionable is False
    assert record.borrow_status == "hard_to_borrow"


def test_the_parser_stamps_every_row_at_the_fetch_time() -> None:
    """`known_at` is the fetch time, tz-aware, never the request start and
    never a date (ADR 0021 point 2)."""
    records = parse_assets([_asset("id-a", "AAA"), _asset("id-b", "BBB")], _SECOND).assets
    assert all(record.known_at == _SECOND for record in records)
    assert all(record.known_at.tzinfo is not None for record in records)
    assert all(record.present is True for record in records)


def test_the_parser_refuses_a_naive_fetch_time() -> None:
    with pytest.raises(ValueError, match="fetched_at"):
        parse_assets([_asset("id-a", "AAA")], datetime(2020, 6, 26, 21, 0))  # noqa: DTZ001


def test_the_parser_reads_borrow_status_verbatim_and_the_empty_string_when_absent() -> None:
    present = _asset("id-a", "AAA", borrow_status="restricted")
    absent = _asset("id-b", "BBB")
    del absent["borrow_status"]
    nulled = _asset("id-c", "CCC", borrow_status=None)  # type: ignore[arg-type]
    records = parse_assets([present, absent, nulled], _FIRST).assets
    assert [record.borrow_status for record in records] == ["restricted", "", ""]


def test_an_empty_payload_is_a_snapshot_with_nothing() -> None:
    snapshot = parse_assets([], _FIRST)
    assert snapshot.assets == ()
    assert (snapshot.total, snapshot.us_equity, snapshot.other_class) == (0, 0, 0)


def test_a_payload_entry_that_is_not_a_mapping_raises() -> None:
    with pytest.raises(TypeError, match="mapping"):
        parse_assets(["AAA"], _FIRST)  # type: ignore[list-item]


_REQUIRED_PAYLOAD_FIELDS = (
    "id",
    "class",
    "symbol",
    "exchange",
    "status",
    "tradable",
    "marginable",
    "shortable",
    "easy_to_borrow",
    "fractionable",
)


@pytest.mark.parametrize("field_name", _REQUIRED_PAYLOAD_FIELDS)
def test_the_parser_raises_on_a_missing_field(field_name: str) -> None:
    asset = _asset("id-a", "AAA")
    del asset[field_name]
    with pytest.raises(ValueError, match=field_name):
        parse_assets([asset], _FIRST)


@pytest.mark.parametrize("field_name", ["tradable", "marginable", "shortable", "easy_to_borrow"])
@pytest.mark.parametrize("bad", ["true", 1, 0, None], ids=["string", "one", "zero", "none"])
def test_the_parser_raises_on_a_non_boolean_flag(field_name: str, bad: object) -> None:
    asset = _asset("id-a", "AAA", **{field_name: bad})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match=field_name):
        parse_assets([asset], _FIRST)


def test_the_parser_raises_on_a_non_boolean_fractionable() -> None:
    asset = _asset("id-a", "AAA", fractionable="false")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="fractionable"):
        parse_assets([asset], _FIRST)


def test_the_parser_raises_on_a_non_string_borrow_status() -> None:
    asset = _asset("id-a", "AAA", borrow_status=1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="borrow_status"):
        parse_assets([asset], _FIRST)


def test_the_parser_names_the_asset_that_failed() -> None:
    asset = _asset("id-a", "AAA")
    del asset["shortable"]
    with pytest.raises(ValueError, match="AAA"):
        parse_assets([_asset("id-0", "ZZZ"), asset], _FIRST)


# --- the diff --------------------------------------------------------------


def test_nothing_is_written_for_an_unchanged_asset() -> None:
    stored = {"id-a": _record("id-a", "AAA")}
    rows = snapshot_rows(
        stored,
        parse_assets([_asset("id-a", "AAA")], _SECOND).assets,
        _SECOND,
        max_vanished_share=_MAX_VANISHED_SHARE,
    )
    assert rows == []


def test_a_new_asset_writes_one_row() -> None:
    rows = snapshot_rows(
        {},
        parse_assets([_asset("id-a", "AAA")], _FIRST).assets,
        _FIRST,
        max_vanished_share=_MAX_VANISHED_SHARE,
    )
    (row,) = rows
    assert row.asset_id == "id-a"
    assert row.symbol == "AAA"
    assert row.present is True
    assert (row.known_at, row.fetched_at) == (_FIRST, _FIRST)


def test_a_changed_flag_writes_one_row_with_the_new_values() -> None:
    stored = {
        "id-a": _record(
            "id-a", "AAA", easy_to_borrow=True, borrow_status="easy_to_borrow", known_at=_FIRST
        )
    }
    (row,) = snapshot_rows(
        stored,
        parse_assets(
            [_asset("id-a", "AAA", easy_to_borrow=False, borrow_status="hard_to_borrow")], _SECOND
        ).assets,
        _SECOND,
        max_vanished_share=_MAX_VANISHED_SHARE,
    )
    assert row.easy_to_borrow is False
    assert row.borrow_status == "hard_to_borrow"
    assert row.present is True
    assert row.known_at == _SECOND


def test_a_renamed_asset_writes_one_row_under_its_new_symbol() -> None:
    """`symbol` is in the compared tuple (spec "rows record change")."""
    stored = {"id-a": _record("id-a", "TCKA", known_at=_FIRST)}
    (row,) = snapshot_rows(
        stored,
        parse_assets([_asset("id-a", "TCKB")], _SECOND).assets,
        _SECOND,
        max_vanished_share=_MAX_VANISHED_SHARE,
    )
    assert row.symbol == "TCKB"
    assert row.asset_id == "id-a"
    assert row.known_at == _SECOND


def test_a_vanished_asset_writes_one_present_false_row_carrying_the_last_values() -> None:
    last = _record("id-a", "AAA", known_at=_SECOND)
    (row,) = snapshot_rows(
        {"id-a": last},
        (),
        _THIRD,
        max_vanished_share=1.0,
    )
    assert row.asset_id == "id-a"
    assert row.present is False
    assert row.symbol == "AAA"
    assert row.exchange == "NYSE"
    assert row.status == "active"
    assert row.tradable is True
    assert row.marginable is True
    assert row.shortable is True
    assert row.easy_to_borrow is True
    assert row.borrow_status == "easy_to_borrow"
    assert row.fractionable is True
    assert row.known_at == _THIRD
    assert row.fetched_at == _THIRD


def test_a_vanished_asset_stays_vanished_and_writes_nothing_more() -> None:
    """The stored row of a vanished asset carries `present = FALSE`, so the
    next snapshot that still lacks it is unchanged for it."""
    stored = {"id-a": _record("id-a", "AAA", present=False, known_at=_SECOND)}
    rows = snapshot_rows(
        stored,
        parse_assets([_asset("id-b", "BBB")], _THIRD).assets,
        _THIRD,
        max_vanished_share=_MAX_VANISHED_SHARE,
    )
    assert [(row.asset_id, row.present) for row in rows] == [("id-b", True)]


def test_a_reappeared_asset_writes_a_row_even_with_its_old_values() -> None:
    """`present` is in the compared tuple, so a vanished asset that comes
    back with unchanged values gets its reappearance row rather than staying
    vanished for ever (ADR 0021 point 3)."""
    stored = {"id-a": _record("id-a", "AAA", present=False, known_at=_SECOND)}
    (row,) = snapshot_rows(
        stored,
        parse_assets([_asset("id-a", "AAA")], _THIRD).assets,
        _THIRD,
        max_vanished_share=_MAX_VANISHED_SHARE,
    )
    assert row.present is True
    assert row.symbol == "AAA"
    assert row.known_at == _THIRD


def test_the_rows_are_ordered_payload_first_then_the_vanished() -> None:
    stored = {
        "id-a": _record("id-a", "AAA", known_at=_SECOND),
        "id-c": _record("id-c", "CCC", known_at=_SECOND),
    }
    assets = parse_assets(
        [_asset("id-b", "BBB"), _asset("id-a", "AAA", shortable=False)], _THIRD
    ).assets
    rows = snapshot_rows(stored, assets, _THIRD, max_vanished_share=1.0)
    assert [(row.asset_id, row.present) for row in rows] == [
        ("id-b", True),
        ("id-a", True),
        ("id-c", False),
    ]


def test_the_diff_over_three_snapshots_yields_exactly_the_six_cases() -> None:
    """The fixture's six cases (unchanged, ETB to HTB, vanished, new,
    renamed, vanished then reappeared) over three snapshots a day apart: the
    first snapshot writes one row per asset, and every later row is earned by
    a change, an absence or a return. The rows are the snapshot's, in payload
    order, then the vanished ones.

    The guard is wide open here (its own tests below use the default 5%
    threshold): the second snapshot loses two of five stored assets, a 40%
    vanished share, which the guard exists to refuse.
    """
    unchanged = ("AAAA000-0000-4000-8000-000000000001", "BNDX")
    flipped = ("AAAA000-0000-4000-8000-000000000002", "SPLT")
    vanished = ("AAAA000-0000-4000-8000-000000000003", "DUALA")
    new = ("AAAA000-0000-4000-8000-000000000004", "TRNS")
    renamed = ("AAAA000-0000-4000-8000-000000000005", "TCKA")
    reappeared = ("AAAA000-0000-4000-8000-000000000006", "RDAT")
    # One payload per snapshot: what `GET /v2/assets` answered that day.
    first_payload = [_asset(*case) for case in (unchanged, flipped, vanished, renamed, reappeared)]
    second_payload = [
        _asset(*unchanged),
        _asset(*flipped, easy_to_borrow=False, borrow_status="hard_to_borrow"),
        _asset(renamed[0], "TCKB"),
        _asset(*new),
    ]
    third_payload = [
        _asset(*unchanged),
        _asset(*flipped, easy_to_borrow=False, borrow_status="hard_to_borrow"),
        _asset(renamed[0], "TCKB"),
        _asset(*new),
        _asset(*reappeared),
    ]

    first_rows = snapshot_rows(
        {}, parse_assets(first_payload, _FIRST).assets, _FIRST, max_vanished_share=1.0
    )
    second_rows = snapshot_rows(
        {row.asset_id: row for row in first_rows},
        parse_assets(second_payload, _SECOND).assets,
        _SECOND,
        max_vanished_share=1.0,
    )
    latest = {row.asset_id: row for row in (*first_rows, *second_rows)}
    third_rows = snapshot_rows(
        latest,
        parse_assets(third_payload, _THIRD).assets,
        _THIRD,
        max_vanished_share=1.0,
    )

    assert [(row.asset_id, row.symbol, row.present) for row in first_rows] == [
        (unchanged[0], "BNDX", True),
        (flipped[0], "SPLT", True),
        (vanished[0], "DUALA", True),
        (renamed[0], "TCKA", True),
        (reappeared[0], "RDAT", True),
    ]
    assert [(row.asset_id, row.symbol, row.present) for row in second_rows] == [
        (flipped[0], "SPLT", True),  # ETB to HTB
        (renamed[0], "TCKB", True),  # renamed
        (new[0], "TRNS", True),  # new
        (vanished[0], "DUALA", False),  # vanished, carrying its last values
        (reappeared[0], "RDAT", False),  # vanished, and back next snapshot
    ]
    assert [(row.asset_id, row.symbol, row.present) for row in third_rows] == [
        (reappeared[0], "RDAT", True),  # back, with its old values
    ]


# --- the vanished-share guard ----------------------------------------------


def _stored(count: int) -> dict[str, BorrowStatus]:
    return {f"id-{index}": _record(f"id-{index}", f"AAA{index}") for index in range(count)}


@pytest.mark.parametrize(
    "stored,missing,threshold,refused",
    [
        (20, 1, 0.05, False),  # a twentieth vanished: exactly the threshold, allowed
        (20, 2, 0.05, True),  # a tenth: refused
        (10, 1, 0.05, True),  # a tenth of a smaller store: refused
        (10, 0, 0.0, False),  # a zero threshold with nothing vanished: allowed
        (10, 0, 1.0, False),  # a wide threshold with nothing vanished: allowed
    ],
    ids=["at-threshold", "over", "small-store", "zero-with-none-vanished", "wide"],
)
def test_the_guard_refuses_a_snapshot_over_the_threshold(
    stored: int, missing: int, threshold: float, refused: bool
) -> None:
    latest = _stored(stored)
    kept = [f"id-{index}" for index in range(stored - missing)]
    assets = parse_assets(
        [_asset(asset_id, f"AAA{index}") for index, asset_id in enumerate(kept)], _SECOND
    ).assets
    if refused:
        with pytest.raises(TruncatedSnapshotError, match="vanished"):
            snapshot_rows(latest, assets, _SECOND, max_vanished_share=threshold)
    else:
        rows = snapshot_rows(latest, assets, _SECOND, max_vanished_share=threshold)
        # Allowed: one `present = FALSE` row per vanished asset, and nothing
        # else (the kept assets are unchanged).
        assert [(row.asset_id, row.present) for row in rows] == [
            (f"id-{index}", False) for index in range(stored - missing, stored)
        ]


def test_the_guard_refuses_every_asset_vanishing_at_all() -> None:
    with pytest.raises(TruncatedSnapshotError, match="vanished"):
        snapshot_rows(_stored(4), (), _SECOND, max_vanished_share=_MAX_VANISHED_SHARE)


def test_the_guard_ignores_assets_already_recorded_vanished() -> None:
    """The share is over the stored **present** assets: an asset whose latest
    row is `present = FALSE` is out of both numerator and denominator, so a
    store with many vanished rows is not refused as truncated."""
    latest = _stored(2)
    latest["id-gone"] = _record("id-gone", "ZZZ", present=False)
    assets = parse_assets([_asset("id-0", "AAA0"), _asset("id-1", "AAA1")], _SECOND).assets
    assert snapshot_rows(latest, assets, _SECOND, max_vanished_share=0.0) == []


def test_the_guard_is_not_tripped_by_a_store_with_nothing_present() -> None:
    """The first snapshot on an empty store vanishes nothing, whatever the
    threshold."""
    latest = {"id-gone": _record("id-gone", "ZZZ", present=False)}
    rows = snapshot_rows(
        latest,
        parse_assets([_asset("id-a", "AAA")], _FIRST).assets,
        _FIRST,
        max_vanished_share=0.0,
    )
    assert [(row.asset_id, row.present) for row in rows] == [("id-a", True)]


@pytest.mark.parametrize("threshold", [-0.1, 1.5, True, "0.05", None])
def test_the_guard_refuses_a_threshold_outside_zero_and_one(threshold: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        snapshot_rows({}, (), _FIRST, max_vanished_share=threshold)  # type: ignore[arg-type]
