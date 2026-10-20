"""No look-ahead in the index check (backtest spec amendment #1598, T245).

Month m is read at t_m = close(T_m). A bar revision, a dividend and a listing end,
each planted with `known_at` after t_m, change nothing for month m: the report of a
store with them equals the report of the store without. The control plants the same
rows known before t_m and the report moves, so the comparison can fail.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from backtest.test_index_check_store import (
    JULY,
    NOW,
    action,
    build_store,
    run,
    settings_for,
    writer,
)

from tradepartner.backtest import index_check_store as ics
from tradepartner.config import Settings
from tradepartner.store.db import insert_row

AFTER = datetime(2019, 8, 2, 20, tzinfo=UTC)  # after close(2019-07-31)
BEFORE = datetime(2019, 7, 30, 20, 30, tzinfo=UTC)  # before it


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def plant(settings: Settings, known: datetime) -> None:
    """A revised bar, a dividend and a Form 25 for members of July 2019, all `known`."""
    with writer(settings) as conn:
        conn.execute(
            "INSERT INTO prices_daily SELECT * REPLACE (close * 2 AS close, high * 2 AS high, "
            "? AS known_at, ? AS ingested_at) FROM prices_daily "
            "WHERE security_id = 'SEC_SPLIT_BETWEEN' AND session = '2019-07-31'",
            [known, known],
        )
        insert_row(
            conn,
            "corporate_actions",
            action("SEC_DUAL_A", "dividend", date(2019, 7, 15), 0.25, "late", known),
        )
        insert_row(
            conn,
            "delistings",
            {
                "security_id": "SEC_DUAL_B",
                "form": "25",
                "class_title": "Class B Common Stock",
                "exchange": "NYSE",
                "filed_at": known,
                "effective_on": date(2019, 8, 9),
                "known_at": known,
                "ingested_at": known,
                "source": "edgar",
                "provenance": "filing",
            },
        )


def report_of(settings: Settings) -> dict[str, Any]:
    payload = ics.report_dict(run(settings, JULY, JULY))
    payload["header"].pop("store_path")  # the two stores live at different paths
    return payload


@pytest.fixture
def pair(tmp_path: Path) -> tuple[Settings, Settings]:
    base = build_store(tmp_path / "base.duckdb", boundary=date(2019, 12, 31))
    with writer(base) as conn:  # DUALB stops trading, so a Form 25 for it ends its listing
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = 'SEC_DUAL_B' AND session > '2019-07-19'"
        )
    copy = tmp_path / "planted.duckdb"
    shutil.copy(base.store.path, copy)
    return base, settings_for(copy)


def test_rows_known_after_t_m_change_nothing_for_month_m(pair: tuple[Settings, Settings]) -> None:
    base, planted = pair
    plant(planted, AFTER)
    assert report_of(planted) == report_of(base)


def test_the_same_rows_known_before_t_m_do_change_it(pair: tuple[Settings, Settings]) -> None:
    base, planted = pair
    plant(planted, BEFORE)
    assert report_of(planted) != report_of(base)
    names = {n["ticker"]: n for w in report_of(planted)["worst"]["equal"] for n in w["names"]}
    assert "dividend=0.25" in names["DUALA"]["flags"]
    assert "delisted" in names["DUALB"]["flags"]


def test_the_data_vintage_at_close_of_the_end_month_ignores_later_rows(
    pair: tuple[Settings, Settings],
) -> None:
    base, planted = pair
    plant(planted, AFTER)
    assert run(planted, JULY, JULY).header.data_vintage == run(base, JULY, JULY).header.data_vintage
    assert NOW > AFTER
