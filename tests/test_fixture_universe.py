"""Tests for the fixture universe generator and its output (T5).

Covers spec req 13's required cases and the acceptance criteria listed for
T5 in `docs/plans/data-foundation.md`: deterministic, content-equal
regeneration; every CSV loads via `fixture_store`; every fact table is
non-empty; one structural assertion per required case, queried from the
loaded store; every bar sits on a real XNYS session with `known_at` equal
to that session's calendar close; no bar on the holiday; the half day's
bar `known_at` is the early close.

Each case-specific test queries `fixture_store` directly rather than
hand-computing "the right answer" a second time in Python — the fixture
*is* the answer; these tests check its shape, not derive it.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType

import duckdb
import pytest

from tradepartner import calendar as tp_calendar
from tradepartner.config import get_settings

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "universe"
GENERATOR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "make_fixture_universe.py"

# Confirmed by the generator run: 2019-07-04 (Independence Day) has no
# session in range; 2018-11-23 (day after Thanksgiving) is a half day.
HOLIDAY = date(2019, 7, 4)
HALF_DAY = date(2018, 11, 23)


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("make_fixture_universe", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _q(conn: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None) -> list:
    return conn.execute(sql, params or []).fetchall()


# --- (a) deterministic, content-equal regeneration -------------------------


def test_regeneration_is_content_equal_to_committed_csvs(tmp_path: Path) -> None:
    module = _load_generator()
    module.generate(tmp_path)
    committed = sorted(FIXTURES_DIR.glob("*.csv"))
    assert committed, "no committed fixture CSVs to compare against"
    for committed_path in committed:
        regenerated_path = tmp_path / committed_path.name
        assert regenerated_path.exists(), f"generator did not write {committed_path.name}"
        assert regenerated_path.read_bytes() == committed_path.read_bytes(), (
            f"{committed_path.name} differs between the committed fixture and a fresh "
            "regeneration; the generator must be fully deterministic"
        )


# --- (b) every CSV loads; every fact table is non-empty --------------------


FACT_TABLES: tuple[str, ...] = (
    "securities",
    "listings",
    "classifications",
    "delistings",
    "prices_daily",
    "corporate_actions",
    "facts",
)


@pytest.mark.parametrize("table", FACT_TABLES)
def test_every_fact_table_has_rows(fixture_store: duckdb.DuckDBPyConnection, table: str) -> None:
    (count,) = fixture_store.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # type: ignore[misc]
    assert count > 0, f"{table} has no rows in the fixture universe"


# --- (c) one assertion per required case (spec req 13) ---------------------


def test_delisting_with_truncated_history_exceeds_gap_threshold(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    last_bar = _q(
        fixture_store, "SELECT MAX(session) FROM prices_daily WHERE security_id = 'TRUNC1'"
    )[0][0]
    (filed_at,) = _q(fixture_store, "SELECT filed_at FROM delistings WHERE security_id = 'TRUNC1'")[
        0
    ]
    reference_session = tp_calendar.previous_session(filed_at.date())
    gap = 0
    cursor = last_bar
    while cursor < reference_session:
        cursor = tp_calendar.next_session(cursor)
        gap += 1
    assert gap > get_settings().gap.missing_tail_sessions


def test_delisting_within_gap_threshold_is_not_missing(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    last_bar = _q(
        fixture_store, "SELECT MAX(session) FROM prices_daily WHERE security_id = 'NEARN1'"
    )[0][0]
    (filed_at,) = _q(fixture_store, "SELECT filed_at FROM delistings WHERE security_id = 'NEARN1'")[
        0
    ]
    reference_session = tp_calendar.previous_session(filed_at.date())
    gap = 0
    cursor = last_bar
    while cursor < reference_session:
        cursor = tp_calendar.next_session(cursor)
        gap += 1
    assert 0 < gap <= get_settings().gap.missing_tail_sessions


def test_clean_merger_last_bar_is_session_before_filing(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    last_bar = _q(
        fixture_store, "SELECT MAX(session) FROM prices_daily WHERE security_id = 'MRGR1'"
    )[0][0]
    (filed_at,) = _q(fixture_store, "SELECT filed_at FROM delistings WHERE security_id = 'MRGR1'")[
        0
    ]
    assert last_bar == tp_calendar.previous_session(filed_at.date())


def test_form_25_nse_present(fixture_store: duckdb.DuckDBPyConnection) -> None:
    rows = _q(fixture_store, "SELECT security_id FROM delistings WHERE form = '25-NSE'")
    assert rows == [("NSE1",)]


def test_form_25_on_non_common_class_leaves_common_listed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    (pfd_type,) = _q(
        fixture_store, "SELECT security_type FROM classifications WHERE security_id = 'ZETA_PFD'"
    )[0]
    assert pfd_type != "common"
    delisted = _q(
        fixture_store, "SELECT class_title FROM delistings WHERE security_id = 'ZETA_PFD'"
    )
    assert delisted == [("6% Cumulative Preferred Stock",)]
    common_delisted = _q(
        fixture_store, "SELECT COUNT(*) FROM delistings WHERE security_id = 'ZETA_COM'"
    )[0][0]
    assert common_delisted == 0
    (last_common_bar,) = _q(
        fixture_store, "SELECT MAX(session) FROM prices_daily WHERE security_id = 'ZETA_COM'"
    )[0]
    (pfd_filed_at,) = _q(
        fixture_store, "SELECT filed_at FROM delistings WHERE security_id = 'ZETA_PFD'"
    )[0]
    # The common has bars well past the preferred's Form 25 — it stays listed.
    assert last_common_bar > pfd_filed_at.date()


def test_exchange_transfer_new_listing_within_window_known_later(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    listings = _q(
        fixture_store,
        "SELECT exchange, valid_from, known_at FROM listings WHERE security_id = 'XFER1' "
        "ORDER BY valid_from",
    )
    assert len(listings) == 2
    (old_exchange, _, _), (new_exchange, new_valid_from, new_known_at) = listings
    assert old_exchange != new_exchange
    (filed_at,) = _q(fixture_store, "SELECT filed_at FROM delistings WHERE security_id = 'XFER1'")[
        0
    ]
    window = get_settings().master.transfer_window_sessions
    gap = 0
    cursor = filed_at.date()
    while cursor < new_valid_from:
        cursor = tp_calendar.next_session(cursor)
        gap += 1
    assert 0 < gap <= window
    # The new listing becomes known strictly after the Form 25, so a probe
    # T in between (T7/T8b will exercise this) would see "delisted".
    assert new_known_at > filed_at


def test_same_company_ticker_change(fixture_store: duckdb.DuckDBPyConnection) -> None:
    listings = _q(
        fixture_store,
        "SELECT DISTINCT ticker, exchange FROM listings WHERE security_id = 'TIKR1'",
    )
    assert len(listings) == 2
    exchanges = {exchange for _, exchange in listings}
    tickers = {ticker for ticker, _ in listings}
    assert len(exchanges) == 1
    assert len(tickers) == 2


def test_ticker_reused_by_different_company(fixture_store: duckdb.DuckDBPyConnection) -> None:
    rows = _q(
        fixture_store,
        "SELECT s.security_id, s.cik FROM securities s "
        "JOIN listings l ON l.security_id = s.security_id "
        "WHERE l.ticker = 'DUPL'",
    )
    security_ids = {r[0] for r in rows}
    ciks = {r[1] for r in rows}
    assert security_ids == {"REUSE_OLD", "REUSE_NEW"}
    assert len(ciks) == 2
    (old_last_bar,) = _q(
        fixture_store, "SELECT MAX(session) FROM prices_daily WHERE security_id = 'REUSE_OLD'"
    )[0]
    (new_valid_from,) = _q(
        fixture_store, "SELECT valid_from FROM listings WHERE security_id = 'REUSE_NEW'"
    )[0]
    assert old_last_bar < new_valid_from


def test_dual_class_company_shares_one_cik(fixture_store: duckdb.DuckDBPyConnection) -> None:
    rows = _q(
        fixture_store,
        "SELECT sec.cik FROM securities sec "
        "JOIN classifications cl ON cl.security_id = sec.security_id "
        "WHERE cl.security_type = 'common' "
        "GROUP BY sec.cik HAVING COUNT(DISTINCT sec.security_id) = 2",
    )
    assert rows == [("0001000012",)]
    facts = _q(
        fixture_store,
        "SELECT security_id, class_member FROM facts WHERE security_id IN ('KAPPA_A', 'KAPPA_B') "
        "ORDER BY security_id",
    )
    assert facts == [("KAPPA_A", "ClassA"), ("KAPPA_B", "ClassB")]


def test_split_is_visible_as_a_raw_price_step(fixture_store: duckdb.DuckDBPyConnection) -> None:
    (ex_date, ratio, known_at) = _q(
        fixture_store,
        "SELECT ex_date, ratio_or_amount, known_at FROM corporate_actions "
        "WHERE security_id = 'SPLIT1' AND action_type = 'split'",
    )[0]
    assert known_at == tp_calendar.session_close(tp_calendar.previous_session(ex_date))
    (before,) = _q(
        fixture_store,
        "SELECT close FROM prices_daily WHERE security_id = 'SPLIT1' AND session = ?",
        [tp_calendar.previous_session(ex_date)],
    )[0]
    (after,) = _q(
        fixture_store,
        "SELECT close FROM prices_daily WHERE security_id = 'SPLIT1' AND session = ?",
        [ex_date],
    )[0]
    assert after < before / ratio * 1.2
    assert after > before / ratio * 0.8


def test_split_between_shares_filing_and_month_end_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    (fact_known_at,) = _q(fixture_store, "SELECT known_at FROM facts WHERE security_id = 'SPLIT2'")[
        0
    ]
    (ex_date,) = _q(
        fixture_store,
        "SELECT ex_date FROM corporate_actions WHERE security_id = 'SPLIT2' AND "
        "action_type = 'split'",
    )[0]
    month_end_t = tp_calendar.last_session_of_month(2019, 6)
    assert fact_known_at.date() < ex_date < month_end_t


def test_split_known_before_t_with_ex_date_after_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    (ex_date, known_at) = _q(
        fixture_store,
        "SELECT ex_date, known_at FROM corporate_actions WHERE security_id = 'SPLIT3' AND "
        "action_type = 'split'",
    )[0]
    month_end_t = tp_calendar.last_session_of_month(2020, 3)
    assert known_at.date() < month_end_t < ex_date


def test_revised_dividend_second_row_known_at_is_ingested_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = _q(
        fixture_store,
        "SELECT ratio_or_amount, known_at, ingested_at FROM corporate_actions "
        "WHERE security_id = 'DIVR1' AND action_type = 'dividend' ORDER BY known_at",
    )
    assert len(rows) == 2
    (first_amount, first_known_at, _), (second_amount, second_known_at, second_ingested_at) = rows
    assert first_amount != second_amount
    assert second_known_at > first_known_at
    assert second_known_at == second_ingested_at


def test_restated_shares_fact_same_as_of_date_two_known_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = _q(
        fixture_store,
        "SELECT as_of_date, value, known_at FROM facts WHERE security_id = 'REST1' "
        "ORDER BY known_at",
    )
    assert len(rows) == 2
    (as_of_1, value_1, known_at_1), (as_of_2, value_2, known_at_2) = rows
    assert as_of_1 == as_of_2
    assert known_at_1 != known_at_2
    assert value_1 != value_2


def test_stale_shares_fact_exceeds_max_age_at_a_later_month_end(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = _q(fixture_store, "SELECT known_at FROM facts WHERE security_id = 'STALE1'")
    assert len(rows) == 1
    (known_at,) = rows[0]
    month_end_t = tp_calendar.last_session_of_month(2019, 8)
    age_days = (month_end_t - known_at.date()).days
    assert age_days > get_settings().universe.max_shares_age_days


def test_unclassifiable_name(fixture_store: duckdb.DuckDBPyConnection) -> None:
    rows = _q(
        fixture_store, "SELECT security_id FROM classifications WHERE rule = 'unclassifiable'"
    )
    assert rows == [("UNCL1",)]


def test_snapshot_static_only_pre_2019_listing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    (valid_from, known_at, provenance) = _q(
        fixture_store,
        "SELECT valid_from, known_at, provenance FROM listings WHERE security_id = 'STAT1'",
    )[0]
    assert provenance == "snapshot_static"
    assert valid_from < date(2019, 1, 1)
    assert known_at.date() >= date(2019, 1, 1)


def test_benchmarks_seeded_with_bars_and_dividends(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    for security_id in ("SPY", "MTUM"):
        (benchmark,) = _q(
            fixture_store, "SELECT benchmark FROM securities WHERE security_id = ?", [security_id]
        )[0]
        assert benchmark is True
        (bar_count,) = _q(
            fixture_store, "SELECT COUNT(*) FROM prices_daily WHERE security_id = ?", [security_id]
        )[0]
        assert bar_count > 0
        (dividend_count,) = _q(
            fixture_store,
            "SELECT COUNT(*) FROM corporate_actions WHERE security_id = ? AND action_type = "
            "'dividend'",
            [security_id],
        )[0]
        assert dividend_count > 0


# --- (d) bars sit only on real sessions, known_at is the calendar close ----


def test_every_bar_session_is_an_xnys_session_with_correct_known_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = _q(fixture_store, "SELECT DISTINCT session, known_at FROM prices_daily")
    assert rows
    for session, known_at in rows:
        assert tp_calendar.is_session(session), f"{session} is not an XNYS session"
        assert known_at == tp_calendar.session_close(session)


# --- (e) holiday has no bar; the half day's bar known_at is the early close


def test_no_bar_on_the_holiday(fixture_store: duckdb.DuckDBPyConnection) -> None:
    assert tp_calendar.is_session(HOLIDAY) is False
    (count,) = _q(fixture_store, "SELECT COUNT(*) FROM prices_daily WHERE session = ?", [HOLIDAY])[
        0
    ]
    assert count == 0


def test_half_day_bar_known_at_is_the_early_close(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert tp_calendar.is_half_day(HALF_DAY) is True
    known_ats = _q(
        fixture_store, "SELECT DISTINCT known_at FROM prices_daily WHERE session = ?", [HALF_DAY]
    )
    assert known_ats
    expected = tp_calendar.session_close(HALF_DAY)
    assert expected != datetime(HALF_DAY.year, HALF_DAY.month, HALF_DAY.day, 21, 0, tzinfo=UTC)
    for (known_at,) in known_ats:
        assert known_at == expected


# --- timing/store baseline (every row obeys known_at <= ingested_at) -------


def test_every_row_has_known_at_le_ingested_at(fixture_store: duckdb.DuckDBPyConnection) -> None:
    for table in FACT_TABLES:
        (bad,) = _q(fixture_store, f"SELECT COUNT(*) FROM {table} WHERE known_at > ingested_at")[0]
        assert bad == 0, f"{table} has {bad} row(s) with known_at > ingested_at"
