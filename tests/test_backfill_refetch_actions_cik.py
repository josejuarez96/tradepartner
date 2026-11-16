"""#3304: `refetch_actions`' collision check also catches a new event that
repeats a live event on another security id of the same CIK (same ex-date,
type and amount or ratio): the re-keyed split or dividend would apply twice
across ids. A reviewed `distinct` group or an allow entry covers it; an
unrelated CIK never collides. Same store and `_History` stub as
`test_backfill_refetch_actions`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from test_backfill import SINCE
from test_backfill_refetch_actions import (
    EX,
    LATER,
    _committed,
    _dividend,
    _open_release,
    _refetch,
    _rows,
)

from tradepartner.adapters.prices import ActionType, CorporateAction, action_first_seen_known_at
from tradepartner.config import Settings
from tradepartner.ingest import FAILED
from tradepartner.store import dividend_reviews as dr

OLD = "0000000002"  # the first id of CIK 0000000002
NEW = "0000000002:class-b-common-stock"  # a second id of the same CIK
OTHER_CIK = "0000000001"
STAMP = action_first_seen_known_at(EX).isoformat()
AGAIN = LATER + timedelta(days=1)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _split(security: str, ratio: float = 2.0, action_id: str = "s1") -> CorporateAction:
    return CorporateAction(
        security,
        ActionType.SPLIT,
        EX,
        ratio,
        action_first_seen_known_at(EX),
        "alpaca",
        source_action_id=action_id,
    )


def _div(security: str, amount: float = 0.25, action_id: str = "d1") -> CorporateAction:
    return replace(_dividend(amount=amount, action_id=action_id), security_id=security)


def _then(settings: Settings, first: list[Any], second: list[Any], **kwargs: Any) -> Any:
    """`first` stored by a real run; the source then answers `second`."""
    prices = _committed(settings)
    _open_release(settings)
    prices.actions = first
    _refetch(settings, prices, since=SINCE, securities=[OLD, NEW])
    prices.actions = second
    return _refetch(
        settings, prices, since=SINCE, securities=[OLD, NEW], clock=lambda: AGAIN, **kwargs
    )


def _count(settings: Settings) -> int:
    return int(_rows(settings, "SELECT count(*) FROM corporate_actions")[0][0])


def test_a_dividend_repeating_a_live_one_on_another_id_of_the_cik_is_refused(
    settings: Settings,
) -> None:
    result = _then(settings, [_div(OLD)], [_div(NEW, action_id="d2")], dry_run=True)
    line = f"collision {NEW} dividend {EX} 0.25 known_at={STAMP}"
    assert result.refused == (line,) and result.rows == ()


def test_a_real_run_with_the_cross_id_dividend_writes_nothing(settings: Settings) -> None:
    result = _then(settings, [_div(OLD)], [_div(NEW, action_id="d2")])
    assert not result.ok and result.runs[-1].status == FAILED
    assert "1 collision row(s)" in result.runs[-1].message
    assert _count(settings) == 1


def test_a_split_repeating_a_live_one_on_another_id_of_the_cik_is_refused(
    settings: Settings,
) -> None:
    result = _then(settings, [_split(OLD)], [_split(NEW, action_id="s2")])
    assert not result.ok
    assert result.refused == (f"collision {NEW} split {EX} 2.0 known_at={STAMP}",)
    assert _count(settings) == 1


def test_a_different_amount_or_ratio_on_another_id_is_not_a_collision(settings: Settings) -> None:
    result = _then(settings, [_div(OLD)], [_div(NEW, amount=0.5, action_id="d2")])
    assert result.ok and result.refused == ()
    assert _count(settings) == 2


def test_an_unrelated_cik_never_collides(settings: Settings) -> None:
    prices = _committed(settings)
    _open_release(settings)
    prices.actions = [_div(OLD)]
    _refetch(settings, prices, since=SINCE, securities=[OLD])
    prices.actions = [_div(OTHER_CIK, action_id="d2")]
    result = _refetch(settings, prices, since=SINCE, securities=[OTHER_CIK], clock=lambda: AGAIN)
    assert result.ok and result.refused == ()
    assert _count(settings) == 2


def test_a_reviewed_distinct_group_covers_the_cross_id_event(settings: Settings) -> None:
    group = dr.ReviewedGroup(NEW, EX, 0.25, "distinct", "", "ACME", "regular and special")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dr, "reviewed_groups", lambda: (group,))
        result = _then(settings, [_div(OLD)], [_div(NEW, action_id="d2")])
    assert result.ok and result.refused == ()
    assert _count(settings) == 2


def test_an_allow_entry_covers_the_cross_id_event(settings: Settings) -> None:
    result = _then(settings, [_div(OLD)], [_div(NEW, action_id="d2")], allow_collisions=[(NEW, EX)])
    assert result.ok and result.refused == ()
    assert _count(settings) == 2


def test_two_ids_of_one_cik_arriving_together_do_not_collide(settings: Settings) -> None:
    """A dual-class issuer's classes land in one write: neither is a live
    event stored before it."""
    prices = _committed(settings)
    _open_release(settings)
    prices.actions = [_div(OLD), _div(NEW, action_id="d2")]
    result = _refetch(settings, prices, since=SINCE, securities=[OLD, NEW])
    assert result.ok and result.refused == ()
    assert _count(settings) == 2


def test_a_cancelled_live_event_on_another_id_does_not_collide(settings: Settings) -> None:
    gone = replace(_div(OLD), cancelled=True)
    result = _then(settings, [gone], [_div(NEW, action_id="d2")])
    assert result.ok and result.refused == ()


def test_a_class_added_later_with_an_identical_event_is_refused_unless_allowed(
    settings: Settings,
) -> None:
    """Known fail-closed limit (#3304): a second share class of one CIK whose
    identical event arrives in a later write looks like a re-key. The
    caller's allow entry for the class covers it."""
    refused = _then(settings, [_div(OLD)], [_div(NEW, action_id="d2")])
    assert not refused.ok


def _collision_and_new(settings: Settings, **kwargs: Any) -> Any:
    """OLD holds 0.25 live; the source then answers a duplicate under NEW and a new OLD split."""
    first = [_div(OLD)]
    second = [_div(NEW, action_id="d2"), _split(OLD)]
    return _then(settings, first, second, **kwargs)


def test_without_skip_collisions_one_collision_refuses_the_whole_run(settings: Settings) -> None:
    result = _collision_and_new(settings)  # existing behaviour (#3246), kept (#3484)
    assert not result.ok and result.skipped == ()
    assert _count(settings) == 1


def test_skip_collisions_writes_the_new_row_and_leaves_the_collision(settings: Settings) -> None:
    result = _collision_and_new(settings, skip_collisions=True)
    line = f"collision {NEW} dividend {EX} 0.25 known_at={STAMP}"
    assert result.ok and result.skipped == (line,) and result.refused == ()
    assert [r.split()[:2] for r in result.rows] == [["new", OLD]]
    assert "1 collision(s) skipped" in result.summary()
    assert _rows(
        settings, "SELECT security_id, action_type FROM corporate_actions ORDER BY 1, 2"
    ) == [
        (OLD, "dividend"),
        (OLD, "split"),
    ]


def test_skip_collisions_leaves_the_stored_row_and_its_known_at(settings: Settings) -> None:
    prices = _committed(settings)
    _open_release(settings)
    prices.actions = [_div(OLD)]
    _refetch(settings, prices, since=SINCE, securities=[OLD, NEW])
    sql = "SELECT security_id, ratio_or_amount, known_at, ingested_at FROM corporate_actions"
    before = _rows(settings, sql)
    prices.actions = [_div(NEW, action_id="d2")]
    result = _refetch(
        settings,
        prices,
        since=SINCE,
        securities=[OLD, NEW],
        clock=lambda: AGAIN,
        skip_collisions=True,
    )
    assert result.ok and len(result.skipped) == 1 and result.added == ()
    assert _rows(settings, sql) == before


def test_a_dry_run_with_skip_collisions_lists_what_it_would_skip(settings: Settings) -> None:
    result = _collision_and_new(settings, skip_collisions=True, dry_run=True)
    assert len(result.skipped) == 1 and result.refused == ()
    assert result.added == (("split", 1),)
    assert _count(settings) == 1


def test_skip_collisions_with_an_allow_entry_writes_the_covered_collision(
    settings: Settings,
) -> None:
    result = _collision_and_new(settings, skip_collisions=True, allow_collisions=[(NEW, EX)])
    assert result.ok and result.skipped == ()
    assert _count(settings) == 3


def test_a_skipped_collision_leaves_no_cancel_of_a_stored_dividend(settings: Settings) -> None:
    """quant-auditor on #3485: a new id on a stored id's date, paired as the re-date of an
    id-less key, would cancel that key; skipping the collision must skip that cancel too."""
    prices = _committed(settings)
    _open_release(settings)
    day2 = EX + timedelta(days=1)
    idless = replace(_div(OLD), source_action_id=None)
    d1 = replace(_div(OLD, amount=0.30, action_id="d1"), ex_date=day2)
    prices.actions = [idless, d1]
    _refetch(settings, prices, since=SINCE, securities=[OLD])
    sql = (
        "SELECT source_action_id, ex_date, ratio_or_amount, cancelled FROM corporate_actions "
        "QUALIFY row_number() OVER (PARTITION BY security_id, coalesce(nullif(source_action_id,''),"
        " action_type||'|'||CAST(ex_date AS VARCHAR)) ORDER BY ingested_at DESC, known_at DESC)=1 "
        "ORDER BY 2, 1"
    )
    before = _rows(settings, sql)
    d2 = replace(_div(OLD, amount=0.40, action_id="d2"), ex_date=day2)
    prices.actions = [d1, d2]
    result = _refetch(
        settings,
        prices,
        since=SINCE,
        securities=[OLD],
        clock=lambda: AGAIN,
        skip_collisions=True,
    )
    assert result.ok
    assert _rows(settings, sql) == before
