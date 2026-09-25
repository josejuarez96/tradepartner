"""Deterministic generator for the fixture universe (T5).

Writes one CSV per store fact table into `tests/fixtures/universe/` (or an
override directory given as the first CLI argument, so tests can regenerate
into a tmp dir): `securities.csv`, `listings.csv`, `classifications.csv`,
`delistings.csv`, `prices_daily.csv`, `corporate_actions.csv`, `facts.csv`,
plus `README.md` mapping every spec req 13 case to the rows that exercise
it.

Determinism (spec req 13, acceptance criterion "content-equal
regeneration"): every timestamp is built from a fixed calendar date plus a
fixed hour/minute, `ingested_at` values are synthetic offsets from
`known_at` (never `datetime.now()`), and `random.Random` is seeded
explicitly per security. Config is loaded with `_env_file=None` so a
developer's local `.env` can never change the thresholds baked into this
data (`gap.missing_tail_sessions`, `master.transfer_window_sessions`,
`universe.max_shares_age_days`) and drift the committed CSVs out from under
CI. Rows are sorted before writing so row order never depends on
dict/set iteration order or the order cases happen to run in.

This module intentionally does not import anything from `tradepartner`
except the calendar wrapper and `Settings` — it builds *master-table*-level
rows (as if already parsed by T7-T9's adapters), not raw EDGAR/Alpaca
payloads, so it owns no adapter or schema logic itself.
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tradepartner.calendar import (
    is_half_day,
    is_session,
    next_session,
    previous_session,
    session_close,
)
from tradepartner.config import Settings

# ---------------------------------------------------------------------------
# Config thresholds this generator's cases are built around (never
# hardcoded here, per CLAUDE.md's "thresholds come from config"; loaded
# with no `.env` so the committed CSVs never depend on a developer's local
# environment).
# ---------------------------------------------------------------------------

_SETTINGS = Settings(_env_file=None)
_GAP_THRESHOLD = _SETTINGS.gap.missing_tail_sessions
_TRANSFER_WINDOW = _SETTINGS.master.transfer_window_sessions
_MAX_SHARES_AGE_DAYS = _SETTINGS.universe.max_shares_age_days

_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "universe"

_SOURCE_EDGAR = "edgar"
_SOURCE_ALPACA = "alpaca"
_SOURCE_CONFIG = "config"

# `facts.class_member` is `NOT NULL DEFAULT ''` (schema.py: '' is the
# sentinel for an undimensioned fact). The CSV fixture loader
# (`tests/conftest.py::load_universe_fixtures`) inserts via DuckDB's
# `read_csv(..., all_varchar=true)`, which treats *both* an unquoted and a
# quoted empty cell as NULL (confirmed by hand) -- so an explicit '' cannot
# round-trip through this CSV path and would trip the NOT NULL constraint.
# "NONE" is used here as a placeholder instead; see
# https://github.com/josejuarez96/tradepartner/issues/28 for the loader gap
# and tests/fixtures/universe/README.md for the note to later readers.
_UNDIMENSIONED_CLASS_MEMBER = "NONE"

_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "securities": (
        "security_id",
        "cik",
        "name",
        "benchmark",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
    "listings": (
        "security_id",
        "ticker",
        "exchange",
        "class_title",
        "valid_from",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
    "classifications": (
        "security_id",
        "sic",
        "security_type",
        "rule",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
    "delistings": (
        "security_id",
        "form",
        "class_title",
        "exchange",
        "filed_at",
        "effective_on",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
    "prices_daily": (
        "security_id",
        "session",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
    "corporate_actions": (
        "security_id",
        "action_type",
        "ex_date",
        "ratio_or_amount",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
    "facts": (
        "security_id",
        "fact_name",
        "as_of_date",
        "class_member",
        "value",
        "filing_accession",
        "known_at",
        "ingested_at",
        "source",
        "provenance",
    ),
}


# ---------------------------------------------------------------------------
# Session and timestamp helpers.
# ---------------------------------------------------------------------------


def _session_on_or_after(day: date) -> date:
    return day if is_session(day) else next_session(day)


def session_range(start: date, count: int) -> list[date]:
    """`count` consecutive XNYS sessions starting on or after `start`."""
    sessions = [_session_on_or_after(start)]
    while len(sessions) < count:
        sessions.append(next_session(sessions[-1]))
    return sessions


def nth_session_after(day: date, n: int) -> date:
    d = day
    for _ in range(n):
        d = next_session(d)
    return d


def nth_session_before(day: date, n: int) -> date:
    d = day
    for _ in range(n):
        d = previous_session(d)
    return d


def _dt(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=UTC)


def _filing_acceptance(day: date) -> datetime:
    """A plausible SEC EDGAR filing-acceptance instant on `day`."""
    return _dt(day, 20, 30)


def _thanksgiving(year: int) -> date:
    """The fourth Thursday of November `year` (an XNYS holiday, never a session)."""
    day = date(year, 11, 1)
    while day.weekday() != 3:  # Thursday
        day += timedelta(days=1)
    return day + timedelta(weeks=3)


def _holiday_and_half_day(year: int) -> tuple[date, date]:
    """Thanksgiving (a holiday: not a session) and the day after (a half day),
    verified against the real XNYS calendar rather than assumed."""
    thanksgiving = _thanksgiving(year)
    if is_session(thanksgiving):
        raise AssertionError(f"expected {thanksgiving} (Thanksgiving) to not be an XNYS session")
    day_after = thanksgiving + timedelta(days=1)
    day_after = _session_on_or_after(day_after)
    if not is_half_day(day_after):
        raise AssertionError(
            f"expected {day_after} (day after Thanksgiving) to be an XNYS half day"
        )
    return thanksgiving, day_after


# ---------------------------------------------------------------------------
# Row accumulators.
# ---------------------------------------------------------------------------


@dataclass
class Rows:
    securities: list[dict[str, object]] = field(default_factory=list)
    listings: list[dict[str, object]] = field(default_factory=list)
    classifications: list[dict[str, object]] = field(default_factory=list)
    delistings: list[dict[str, object]] = field(default_factory=list)
    prices_daily: list[dict[str, object]] = field(default_factory=list)
    corporate_actions: list[dict[str, object]] = field(default_factory=list)
    facts: list[dict[str, object]] = field(default_factory=list)
    readme_cases: list[dict[str, str]] = field(default_factory=list)

    def security(
        self,
        security_id: str,
        cik: str,
        name: str,
        known_at: datetime,
        *,
        benchmark: bool = False,
        source: str = _SOURCE_EDGAR,
        provenance: str = "filing",
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        self.securities.append(
            {
                "security_id": security_id,
                "cik": cik,
                "name": name,
                "benchmark": benchmark,
                "known_at": known_at,
                "ingested_at": known_at + ingested_delay,
                "source": source,
                "provenance": provenance,
            }
        )

    def listing(
        self,
        security_id: str,
        ticker: str,
        exchange: str,
        valid_from: date,
        known_at: datetime,
        *,
        class_title: str | None = "Common Stock",
        source: str = _SOURCE_EDGAR,
        provenance: str = "filing",
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        self.listings.append(
            {
                "security_id": security_id,
                "ticker": ticker,
                "exchange": exchange,
                "class_title": class_title,
                "valid_from": valid_from,
                "known_at": known_at,
                "ingested_at": known_at + ingested_delay,
                "source": source,
                "provenance": provenance,
            }
        )

    def classification(
        self,
        security_id: str,
        security_type: str,
        rule: str,
        known_at: datetime,
        *,
        sic: int | None = 7372,
        source: str = _SOURCE_EDGAR,
        provenance: str = "filing",
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        self.classifications.append(
            {
                "security_id": security_id,
                "sic": sic,
                "security_type": security_type,
                "rule": rule,
                "known_at": known_at,
                "ingested_at": known_at + ingested_delay,
                "source": source,
                "provenance": provenance,
            }
        )

    def delisting(
        self,
        security_id: str,
        form: str,
        class_title: str,
        exchange: str,
        filed_at: datetime,
        *,
        effective_days: int = 10,
        source: str = _SOURCE_EDGAR,
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        self.delistings.append(
            {
                "security_id": security_id,
                "form": form,
                "class_title": class_title,
                "exchange": exchange,
                "filed_at": filed_at,
                "effective_on": filed_at.date() + timedelta(days=effective_days),
                "known_at": filed_at,
                "ingested_at": filed_at + ingested_delay,
                "source": source,
                "provenance": "filing",
            }
        )

    def bars(
        self,
        security_id: str,
        sessions: list[date],
        *,
        start_price: float,
        seed: int,
        source: str = _SOURCE_ALPACA,
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        rng = random.Random(seed)
        price = start_price
        for session in sessions:
            open_price = price
            daily_return = rng.uniform(-0.015, 0.015)
            close_price = round(open_price * (1 + daily_return), 2)
            high_price = round(max(open_price, close_price) * 1.004, 2)
            low_price = round(min(open_price, close_price) * 0.996, 2)
            volume = rng.randint(100_000, 2_000_000)
            known_at = session_close(session)
            self.prices_daily.append(
                {
                    "security_id": security_id,
                    "session": session,
                    "open": round(open_price, 2),
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "volume": volume,
                    "known_at": known_at,
                    "ingested_at": known_at + ingested_delay,
                    "source": source,
                    "provenance": "bar",
                }
            )
            price = close_price

    def action(
        self,
        security_id: str,
        action_type: str,
        ex_date: date,
        ratio_or_amount: float,
        *,
        known_at: datetime,
        source: str = _SOURCE_ALPACA,
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        self.corporate_actions.append(
            {
                "security_id": security_id,
                "action_type": action_type,
                "ex_date": ex_date,
                "ratio_or_amount": ratio_or_amount,
                "known_at": known_at,
                "ingested_at": known_at + ingested_delay,
                "source": source,
                "provenance": "action",
            }
        )

    def fact(
        self,
        security_id: str,
        fact_name: str,
        as_of_date: date,
        value: float,
        known_at: datetime,
        *,
        class_member: str = _UNDIMENSIONED_CLASS_MEMBER,
        filing_accession: str | None = None,
        source: str = _SOURCE_EDGAR,
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        self.facts.append(
            {
                "security_id": security_id,
                "fact_name": fact_name,
                "as_of_date": as_of_date,
                "class_member": class_member,
                "value": value,
                "filing_accession": filing_accession,
                "known_at": known_at,
                "ingested_at": known_at + ingested_delay,
                "source": source,
                "provenance": "filing",
            }
        )

    def case(self, case: str, security_ids: str, tickers: str, dates: str, probe: str) -> None:
        self.readme_cases.append(
            {
                "case": case,
                "security_ids": security_ids,
                "tickers": tickers,
                "dates": dates,
                "probe": probe,
            }
        )


# ---------------------------------------------------------------------------
# Case builders. Each function adds one or two securities and every row
# needed to exercise its spec req 13 case, and records a README row.
# ---------------------------------------------------------------------------


def _truncated_delisting(rows: Rows) -> None:
    sessions = session_range(date(2018, 1, 2), 160)
    last_bar = sessions[100]
    # Comfortably more than `gap.missing_tail_sessions` sessions of gap.
    filing_session = sessions[100 + _GAP_THRESHOLD + 8]
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_TRUNC_DELIST", "CIK0001000001", "TRHX"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Truncated History Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions[: sessions.index(last_bar) + 1], start_price=40.0, seed=1)
    rows.delisting(security_id, "25", "Common Stock", "NYSE", filed_at)
    last_session_before_filing = previous_session(filing_session)
    gap = (
        sessions.index(last_session_before_filing) - sessions.index(last_bar)
        if last_session_before_filing in sessions
        else None
    )
    rows.case(
        "Truncated-history delisting (req 13)",
        security_id,
        ticker,
        f"last bar {last_bar}, Form 25 filed {filed_at.date()} "
        f"(last session before filing {last_session_before_filing}, gap {gap} sessions "
        f"> gap.missing_tail_sessions={_GAP_THRESHOLD})",
        "n/a",
    )


def _within_window_delisting(rows: Rows) -> None:
    sessions = session_range(date(2018, 2, 1), 130)
    last_bar_index = 100
    last_bar = sessions[last_bar_index]
    gap_sessions = max(_GAP_THRESHOLD - 2, 1)
    filing_session = sessions[last_bar_index + gap_sessions + 1]
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_WINDOW_DELIST", "CIK0001000002", "WNDX"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Window History Corp", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions[: last_bar_index + 1], start_price=22.0, seed=2)
    rows.delisting(security_id, "25", "Common Stock", "NASDAQ", filed_at)
    last_session_before_filing = previous_session(filing_session)
    rows.case(
        "Delisting within the gap window (req 13, contrast with truncated)",
        security_id,
        ticker,
        f"last bar {last_bar}, Form 25 filed {filed_at.date()} "
        f"(last session before filing {last_session_before_filing}, "
        f"gap <= gap.missing_tail_sessions={_GAP_THRESHOLD})",
        "n/a",
    )


def _clean_merger_delisting(rows: Rows) -> None:
    sessions = session_range(date(2018, 3, 1), 105)
    filing_session = sessions[-1]
    last_bar = previous_session(filing_session)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_CLEAN_MERGER", "CIK0001000003", "CLNM"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    bar_sessions = [s for s in sessions if s <= last_bar]
    rows.security(security_id, cik, "Clean Merger Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=60.0, seed=3)
    rows.delisting(security_id, "25", "Common Stock", "NYSE", filed_at)
    rows.case(
        "Clean merger delisting (req 13: last bar the session before filing, expected NOT missing)",
        security_id,
        ticker,
        f"last bar {last_bar} = session before Form 25 filed {filed_at.date()}",
        "n/a",
    )


def _form_25nse(rows: Rows) -> None:
    sessions = session_range(date(2018, 4, 2), 105)
    filing_session = sessions[-1]
    last_bar = previous_session(filing_session)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_25NSE", "CIK0001000004", "NSEX"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    bar_sessions = [s for s in sessions if s <= last_bar]
    rows.security(security_id, cik, "NSE Delist Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE_AMERICAN", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=15.0, seed=4)
    rows.delisting(security_id, "25-NSE", "Common Stock", "NYSE_AMERICAN", filed_at)
    rows.case(
        "Form 25-NSE delisting (req 13)",
        security_id,
        ticker,
        f"Form 25-NSE filed {filed_at.date()}",
        "n/a",
    )


def _dual_class(rows: Rows) -> None:
    """Dual-class company (req 13): two `security_id`s share one `cik`. Also
    covers "Form 25 on a non-common class, common survives" (req 13): the
    preferred class DUALB is delisted, the common class DUALA is not."""
    sessions = session_range(date(2018, 5, 1), 160)
    cik = "CIK0001000005"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    common_id, common_ticker = "SEC_DUAL_A", "DUALA"
    rows.security(common_id, cik, "Dual Class Holdings", filing_known)
    rows.listing(
        common_id,
        common_ticker,
        "NYSE",
        sessions[0],
        filing_known,
        class_title="Class A Common Stock",
    )
    rows.classification(common_id, "common", "common_default", filing_known)
    rows.bars(common_id, sessions, start_price=80.0, seed=5)

    preferred_id, preferred_ticker = "SEC_DUAL_B", "DUALB"
    preferred_bar_count = 100
    preferred_bar_sessions = sessions[:preferred_bar_count]
    filing_session = sessions[preferred_bar_count]
    filed_at = _filing_acceptance(filing_session)
    rows.security(preferred_id, cik, "Dual Class Holdings", filing_known)
    rows.listing(
        preferred_id,
        preferred_ticker,
        "NYSE",
        sessions[0],
        filing_known,
        class_title="6% Preferred Stock",
    )
    rows.classification(preferred_id, "preferred", "preferred_class_suffix", filing_known)
    rows.bars(preferred_id, preferred_bar_sessions, start_price=25.0, seed=6)
    rows.delisting(preferred_id, "25", "6% Preferred Stock", "NYSE", filed_at)

    rows.case(
        "Dual-class company, two security_ids sharing one cik (req 13)",
        f"{common_id}, {preferred_id}",
        f"{common_ticker}, {preferred_ticker}",
        f"cik {cik}",
        "n/a",
    )
    rows.case(
        "Form 25 on a non-common class; common class survives (req 13)",
        preferred_id,
        preferred_ticker,
        f"Form 25 filed {filed_at.date()} for the preferred class only; "
        f"{common_id} ({common_ticker}) has bars through {sessions[-1]} and no delisting row",
        "n/a",
    )


def _exchange_transfer(rows: Rows) -> None:
    sessions = session_range(date(2018, 6, 1), 160)
    filing_index = 100
    filing_session = sessions[filing_index]
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_TRANSFER", "CIK0001000006", "TRNSF"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    new_valid_from = sessions[filing_index + min(_TRANSFER_WINDOW - 2, 3)]
    new_known_session = sessions[filing_index + _TRANSFER_WINDOW + 2]
    new_known_at = session_close(new_known_session)
    probe_session = sessions[filing_index + 2]
    probe_t = session_close(probe_session)

    rows.security(security_id, cik, "Transfer Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=30.0, seed=7)
    rows.delisting(security_id, "25", "Common Stock", "NYSE", filed_at)
    rows.listing(
        security_id,
        ticker,
        "NASDAQ",
        new_valid_from,
        new_known_at,
        source=_SOURCE_EDGAR,
        provenance="filing",
    )
    rows.case(
        "Exchange transfer: Form 25 + new listing within master.transfer_window_sessions (req 13)",
        security_id,
        ticker,
        f"Form 25 filed {filed_at.date()} (known_at {filed_at.isoformat()}); "
        f"new NASDAQ listing valid_from {new_valid_from}, known_at {new_known_at.isoformat()}",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): strictly between the "
        "filing's known_at and the new listing's known_at, so `listings_as_of`/`universe_as_of` "
        "at T see it delisted, not yet transferred",
    )


def _same_company_ticker_change(rows: Rows) -> None:
    sessions = session_range(date(2018, 7, 2), 160)
    change_index = 90
    change_session = sessions[change_index]
    security_id, cik = "SEC_TICKCHANGE", "CIK0001000007"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))
    rename_known = _filing_acceptance(previous_session(change_session))

    rows.security(security_id, cik, "Ticker Change Co", filing_known)
    rows.listing(security_id, "TCKA", "NYSE", sessions[0], filing_known)
    rows.listing(security_id, "TCKB", "NYSE", change_session, rename_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=18.0, seed=8)
    rows.case(
        "Same-company ticker change: one security_id, two listing ranges (req 13)",
        security_id,
        "TCKA -> TCKB",
        f"TCKA from {sessions[0]}, TCKB from {change_session}",
        "n/a",
    )


def _reused_ticker(rows: Rows) -> None:
    sessions_1 = session_range(date(2018, 8, 1), 100)
    filing_session = sessions_1[-1]
    filed_at = _filing_acceptance(filing_session)
    security_id_1, cik_1 = "SEC_REUSE_1", "CIK0001000008"
    filing_known_1 = _filing_acceptance(nth_session_before(sessions_1[0], 20))

    rows.security(security_id_1, cik_1, "First Reuse Corp", filing_known_1)
    rows.listing(security_id_1, "REUSE", "NYSE", sessions_1[0], filing_known_1)
    rows.classification(security_id_1, "common", "common_default", filing_known_1)
    rows.bars(security_id_1, sessions_1, start_price=12.0, seed=9)
    rows.delisting(security_id_1, "25", "Common Stock", "NYSE", filed_at)

    sessions_2 = session_range(date(2019, 8, 1), 100)
    security_id_2, cik_2 = "SEC_REUSE_2", "CIK0001000009"
    filing_known_2 = _filing_acceptance(nth_session_before(sessions_2[0], 20))

    rows.security(security_id_2, cik_2, "Second Reuse Corp", filing_known_2)
    rows.listing(security_id_2, "REUSE", "NASDAQ", sessions_2[0], filing_known_2)
    rows.classification(security_id_2, "common", "common_default", filing_known_2)
    rows.bars(security_id_2, sessions_2, start_price=9.0, seed=10)
    rows.case(
        "Ticker reused by a different company: two security_ids, "
        "non-overlapping listings, same ticker (req 13)",
        f"{security_id_1}, {security_id_2}",
        "REUSE (both)",
        f"{security_id_1} delisted {filed_at.date()}; {security_id_2} listed from {sessions_2[0]}",
        "n/a",
    )


def _plain_split(rows: Rows) -> None:
    sessions = session_range(date(2018, 9, 4), 140)
    ex_index = 70
    ex_date = sessions[ex_index]
    known_at = session_close(
        sessions[ex_index - 1]
    )  # first-seen, no announcement: close before ex-date
    security_id, cik, ticker = "SEC_SPLIT_PLAIN", "CIK0001000010", "SPLT"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Plain Split Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=100.0, seed=11)
    rows.action(security_id, "split", ex_date, 2.0, known_at=known_at)
    rows.case(
        "Plain 2-for-1 split (req 13)",
        security_id,
        ticker,
        f"ex_date {ex_date}, known_at {known_at.isoformat()} "
        "(close before ex-date, no announcement)",
        "n/a",
    )


def _split_between_filing_and_t(rows: Rows) -> None:
    sessions = session_range(date(2018, 10, 1), 140)
    filing_session = sessions[20]
    ex_index = 70
    ex_date = sessions[ex_index]
    split_known_at = session_close(sessions[ex_index - 1])
    probe_session = sessions[ex_index + 10]
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_SPLIT_BETWEEN", "CIK0001000011", "SPBT"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))
    shares_known_at = _filing_acceptance(filing_session)

    rows.security(security_id, cik, "Split Between Filing Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=50.0, seed=12)
    rows.fact(security_id, "shares_outstanding", filing_session, 5_000_000, shares_known_at)
    rows.action(security_id, "split", ex_date, 3.0, known_at=split_known_at)
    rows.case(
        "Split between a shares filing and a documented T (req 13)",
        security_id,
        ticker,
        f"shares fact as_of {filing_session} (known_at {shares_known_at.isoformat()}); "
        f"3-for-1 split ex_date {ex_date} (known_at {split_known_at.isoformat()})",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): after both the shares "
        "filing and the split's ex-date, so both are known and the split is effective at T",
    )


def _split_known_before_t_ex_after_t(rows: Rows) -> None:
    sessions = session_range(date(2018, 11, 1), 140)
    announce_index = 10
    ex_index = 70
    ex_date = sessions[ex_index]
    announced_at = session_close(
        sessions[announce_index]
    )  # announcement time is present -> known_at = announcement
    probe_session = sessions[announce_index + 5]
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_SPLIT_FUTURE", "CIK0001000012", "SPFT"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Split Future Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=70.0, seed=13)
    rows.action(security_id, "split", ex_date, 4.0, known_at=announced_at)
    holiday, half_day = _holiday_and_half_day(2018)
    rows.case(
        "Split known before a documented T with ex-date after T (req 13)",
        security_id,
        ticker,
        f"split announced {announced_at.isoformat()}, ex_date {ex_date} (after probe T)",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): split is known but not yet "
        "effective (ex-date > T); rule 4 and universe_as_of must use the raw close and "
        "unadjusted shares at T",
    )
    rows.case(
        "Holiday inside a bar range: no bar exists on the holiday (req 13)",
        security_id,
        ticker,
        f"{holiday.isoformat()} (Thanksgiving) falls inside {security_id}'s bar range "
        f"{sessions[0]}..{sessions[-1]} and has no prices_daily row",
        "n/a",
    )
    rows.case(
        "Half day inside a bar range: known_at is the early close (req 13)",
        security_id,
        ticker,
        f"{half_day.isoformat()} (day after Thanksgiving) falls inside {security_id}'s bar range; "
        "its prices_daily.known_at is calendar.session_close(half_day), the early close",
        "n/a",
    )


def _revised_dividend(rows: Rows) -> None:
    sessions = session_range(date(2018, 12, 3), 120)
    ex_index = 60
    ex_date = sessions[ex_index]
    first_known_at = session_close(sessions[ex_index - 1])
    revision_known_at = session_close(sessions[ex_index + 20])
    security_id, cik, ticker = "SEC_DIV_REVISED", "CIK0001000013", "DIVR"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Revised Dividend Co", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=35.0, seed=14)
    rows.action(security_id, "dividend", ex_date, 0.10, known_at=first_known_at)
    rows.action(security_id, "dividend", ex_date, 0.12, known_at=revision_known_at)
    rows.case(
        "Revised dividend: two rows, same ex_date, second known_at = "
        "ingested_at later than the first (req 13)",
        security_id,
        ticker,
        f"ex_date {ex_date}: first-seen known_at {first_known_at.isoformat()} amount 0.10; "
        f"revision known_at {revision_known_at.isoformat()} amount 0.12",
        "n/a",
    )


def _restated_shares_fact(rows: Rows) -> None:
    sessions = session_range(date(2019, 1, 2), 100)
    as_of_date = sessions[10]
    first_known_at = _filing_acceptance(sessions[15])
    second_known_at = _filing_acceptance(sessions[60])
    security_id, cik, ticker = "SEC_FACTS_RESTATED", "CIK0001000014", "FRST"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Restated Shares Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=45.0, seed=15)
    rows.fact(security_id, "shares_outstanding", as_of_date, 1_000_000, first_known_at)
    rows.fact(security_id, "shares_outstanding", as_of_date, 1_050_000, second_known_at)
    rows.case(
        "Restated shares fact: same as_of_date, two known_ats (req 13)",
        security_id,
        ticker,
        f"as_of_date {as_of_date}: known_at {first_known_at.isoformat()} value 1,000,000; "
        f"known_at {second_known_at.isoformat()} value 1,050,000",
        "n/a",
    )


def _stale_shares_fact(rows: Rows) -> None:
    sessions = session_range(date(2019, 2, 1), 100)
    as_of_date = sessions[0]
    known_at = _filing_acceptance(sessions[5])
    probe_day = as_of_date + timedelta(days=_MAX_SHARES_AGE_DAYS + 30)
    probe_session = _session_on_or_after(probe_day)
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_FACTS_STALE", "CIK0001000015", "STLF"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    bar_sessions = session_range(date(2019, 2, 1), 260)
    rows.security(security_id, cik, "Stale Shares Co", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", sessions[0], filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=28.0, seed=16)
    rows.fact(security_id, "shares_outstanding", as_of_date, 2_000_000, known_at)
    rows.case(
        "Stale shares fact: older than universe.max_shares_age_days at a documented T (req 13)",
        security_id,
        ticker,
        f"shares fact as_of_date {as_of_date}, known_at {known_at.isoformat()}",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): "
        f"T - as_of_date > universe.max_shares_age_days={_MAX_SHARES_AGE_DAYS}",
    )


def _unclassifiable_name(rows: Rows) -> None:
    sessions = session_range(date(2019, 3, 1), 80)
    security_id, cik, ticker = "SEC_UNCLASSIFIABLE", "CIK0001000016", "UNCX"
    filing_known = _filing_acceptance(nth_session_before(sessions[0], 20))

    rows.security(security_id, cik, "Consolidated Enterprises Number Twelve LLC", filing_known)
    rows.listing(security_id, ticker, "NYSE_AMERICAN", sessions[0], filing_known)
    rows.classification(security_id, "unclassifiable", "no_rule_matched", filing_known)
    rows.bars(security_id, sessions, start_price=8.0, seed=17)
    rows.case(
        "Unclassifiable name (req 13)",
        security_id,
        ticker,
        "classifications.security_type = 'unclassifiable', rule = 'no_rule_matched'",
        "n/a",
    )


def _static_pre_2019_listing(rows: Rows) -> None:
    valid_from = date(2017, 1, 3)
    sessions = session_range(valid_from, 260)
    security_id, cik, ticker = "SEC_STATIC_PRE2019", "CIK0001000017", "PRE9"
    first_filing_session = date(2019, 4, 15)
    filing_known = _filing_acceptance(first_filing_session)
    static_fetch_at = _dt(date(2020, 1, 15), 14, 0)

    rows.security(security_id, cik, "Pre-2019 Static Listing Co", filing_known)
    rows.listing(
        security_id,
        ticker,
        "NYSE",
        valid_from,
        static_fetch_at,
        source=_SOURCE_ALPACA,
        provenance="snapshot_static",
    )
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions, start_price=20.0, seed=18)
    rows.case(
        "snapshot_static-only pre-2019 listing (req 13)",
        security_id,
        ticker,
        f"listing valid_from {valid_from} (pre-2019), provenance snapshot_static, "
        f"known_at {static_fetch_at.isoformat()} (the fetch time, later than valid_from); "
        f"securities.known_at {filing_known.isoformat()} is the earliest issuer filing",
        "n/a",
    )


def _benchmarks(rows: Rows) -> None:
    sessions = session_range(date(2018, 1, 2), 500)
    seed_known_at = session_close(sessions[0])

    for security_id, ticker, cik, name, seed, start_price, div_amount in (
        ("SEC_SPY", "SPY", "CIK0001000900", "SPDR S&P 500 ETF Trust", 90, 250.0, 1.35),
        (
            "SEC_MTUM",
            "MTUM",
            "CIK0001000901",
            "iShares MSCI USA Momentum Factor ETF",
            91,
            90.0,
            0.30,
        ),
    ):
        rows.security(
            security_id,
            cik,
            name,
            seed_known_at,
            benchmark=True,
            source=_SOURCE_CONFIG,
            provenance="snapshot_static",
        )
        rows.listing(
            security_id,
            ticker,
            "NYSE",
            sessions[0],
            seed_known_at,
            source=_SOURCE_CONFIG,
            provenance="snapshot_static",
        )
        rows.classification(
            security_id,
            "etf",
            "benchmark_config",
            seed_known_at,
            sic=None,
            source=_SOURCE_CONFIG,
            provenance="snapshot_static",
        )
        rows.bars(security_id, sessions, start_price=start_price, seed=seed)
        ex_date = sessions[120]
        div_known_at = session_close(sessions[119])
        rows.action(security_id, "dividend", ex_date, div_amount, known_at=div_known_at)
        rows.case(
            f"Benchmark seeded from config with a dividend (req 13): {ticker}",
            security_id,
            ticker,
            f"benchmark=TRUE; dividend ex_date {ex_date} amount {div_amount}",
            "n/a",
        )


# ---------------------------------------------------------------------------
# CSV / README writing.
# ---------------------------------------------------------------------------


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _write_csv(
    path: Path, table: str, records: list[dict[str, object]], sort_key: tuple[str, ...]
) -> None:
    columns = _TABLE_COLUMNS[table]
    ordered = sorted(records, key=lambda r: tuple(_format_cell(r[k]) for k in sort_key))
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for record in ordered:
            writer.writerow(_format_cell(record[c]) for c in columns)


def _write_readme(path: Path, cases: list[dict[str, str]]) -> None:
    ordered = sorted(cases, key=lambda c: c["case"])
    lines = [
        "# Fixture universe (T5)",
        "",
        "Generated by `scripts/make_fixture_universe.py` — do not hand-edit; regenerate instead.",
        "Maps every spec req 13 case to the `security_id`(s), tickers, dates and probe",
        "timestamps that exercise it, for T6-T15 authors.",
        "",
        "| Case | security_id(s) | Ticker(s) | Dates | Probe T |",
        "|---|---|---|---|---|",
    ]
    for c in ordered:
        lines.append(
            f"| {c['case']} | {c['security_ids']} | {c['tickers']} | {c['dates']} | {c['probe']} |"
        )
    lines.extend(
        [
            "",
            "## Notes for readers",
            "",
            '- `facts.class_member` uses the placeholder `"NONE"` for every '
            "undimensioned fact here, not the production `''` sentinel documented in "
            "`schema.py` — DuckDB's CSV loader (`tests/conftest.py::load_universe_fixtures`) "
            "turns a quoted-empty cell into `NULL` regardless, which would trip the "
            "`NOT NULL` constraint. See "
            "[issue #28](https://github.com/josejuarez96/tradepartner/issues/28).",
            "- Every `security_id`, listing and classification not called out in a case "
            "row above still exists to carry that case's bars/actions/facts; this table "
            "lists only the rows that make each req 13 case identifiable.",
            "- SPY and MTUM are both seeded on exchange `NYSE` for simplicity; the real "
            "listings are NYSE Arca, which is not in `universe.exchanges`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n")


def build_rows() -> Rows:
    rows = Rows()
    _truncated_delisting(rows)
    _within_window_delisting(rows)
    _clean_merger_delisting(rows)
    _form_25nse(rows)
    _dual_class(rows)
    _exchange_transfer(rows)
    _same_company_ticker_change(rows)
    _reused_ticker(rows)
    _plain_split(rows)
    _split_between_filing_and_t(rows)
    _split_known_before_t_ex_after_t(rows)
    _revised_dividend(rows)
    _restated_shares_fact(rows)
    _stale_shares_fact(rows)
    _unclassifiable_name(rows)
    _static_pre_2019_listing(rows)
    _benchmarks(rows)
    return rows


def write_fixtures(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    _write_csv(
        output_dir / "securities.csv", "securities", rows.securities, ("security_id", "known_at")
    )
    _write_csv(
        output_dir / "listings.csv",
        "listings",
        rows.listings,
        ("security_id", "valid_from", "known_at"),
    )
    _write_csv(
        output_dir / "classifications.csv",
        "classifications",
        rows.classifications,
        ("security_id", "rule", "known_at"),
    )
    _write_csv(
        output_dir / "delistings.csv",
        "delistings",
        rows.delistings,
        ("security_id", "form", "filed_at"),
    )
    _write_csv(
        output_dir / "prices_daily.csv",
        "prices_daily",
        rows.prices_daily,
        ("security_id", "session", "known_at"),
    )
    _write_csv(
        output_dir / "corporate_actions.csv",
        "corporate_actions",
        rows.corporate_actions,
        ("security_id", "action_type", "ex_date", "known_at"),
    )
    _write_csv(
        output_dir / "facts.csv",
        "facts",
        rows.facts,
        ("security_id", "fact_name", "as_of_date", "class_member", "known_at"),
    )
    _write_readme(output_dir / "README.md", rows.readme_cases)


def main(argv: list[str]) -> int:
    output_dir = Path(argv[1]) if len(argv) > 1 else _DEFAULT_OUTPUT_DIR
    write_fixtures(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
