"""Deterministic generator for the fixture universe (T5; #660/T76).

Writes one CSV per store fact table into `tests/fixtures/universe/` (or an
override directory given as the first CLI argument, so tests can regenerate
into a tmp dir): `securities.csv`, `listings.csv`, `classifications.csv`,
`delistings.csv`, `prices_daily.csv`, `corporate_actions.csv`, `facts.csv`,
`statement_facts.csv`, plus `README.md` mapping every spec req 13 case,
and every statement-facts case (#660), to the rows that exercise it.

`statement_facts.csv` rows are **DB-table-level** rows, like every other
CSV here (as if the as-yet-unbuilt T77/T77a/T77b parser and ingest had
already run on these scenarios) — this generator owns no EDGAR parsing or
derivation logic, so a "derived" gross-profit row or a restated fact's
one surviving vintage is authored directly, not computed from inputs.
Unlike every other table, it is keyed by **cik**, not `security_id` (the
spec's decision (c)); most of its cases reuse a `securities.csv` cik from
an existing req 13 case (so later as-of/join tests have one to join
against) rather than inventing a bare one, since this task's file list
does not include `securities.csv` itself.

Determinism (spec req 13, acceptance criterion "content-equal
regeneration"): every timestamp is built from a fixed calendar date plus a
fixed hour/minute, `ingested_at` values are synthetic offsets from
`known_at` (never `datetime.now()`), and `random.Random` is seeded
explicitly per security. Thresholds are read from each `*Config` model's
own field default (`GapConfig()`, `MasterConfig()`, `UniverseConfig()`)
rather than a full `Settings()` load, so a developer's local `.env` can
never change the values baked into this data and drift the committed CSVs
out from under CI.

**Known determinism gap (documented, not fixed here):** `calendar.py`
itself still resolves the XNYS calendar bounds via `get_settings().calendar`
(a full env-reading `Settings()` load) rather than a fixed value, so an
`.env` that overrides `calendar.start`/`calendar.end` could in principle
change which sessions exist. `calendar.py` is a `src/` file this task does
not own, so this is not fixed here.

This module intentionally does not import anything from `tradepartner`
except the calendar wrapper and the three `*Config` models — it builds
*master-table*-level rows (as if already parsed by T7-T9's adapters), not
raw EDGAR/Alpaca payloads, so it owns no adapter or schema logic itself.
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tradepartner.calendar import (
    is_half_day,
    is_session,
    next_session,
    previous_session,
    session_close,
)
from tradepartner.config import GapConfig, MasterConfig, UniverseConfig

# ---------------------------------------------------------------------------
# Config thresholds this generator's cases are built around (never
# hardcoded here, per CLAUDE.md's "thresholds come from config"). Each
# `*Config` model is constructed directly from its own field defaults
# rather than through a full `Settings()`/`.env` load, so these values can
# never drift with a developer's local environment (review round 3).
# ---------------------------------------------------------------------------

_GAP_THRESHOLD = GapConfig().missing_tail_sessions
_TRANSFER_WINDOW = MasterConfig().transfer_window_sessions
_MAX_SHARES_AGE_DAYS = UniverseConfig().max_shares_age_days
_MIN_PRICE = UniverseConfig().min_price

_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "universe"

_SOURCE_EDGAR = "edgar"
_SOURCE_ALPACA = "alpaca"
_SOURCE_CONFIG = "config"

# A single shared bar-history start for every security whose case needs to
# demonstrate passing universe rule 6 (min_history_months) and/or rule 7
# (fresh shares fact) at a documented probe T in 2018+: comfortably more
# than 12 months before any such probe (review round 3, MUST FIX 4).
_GLOBAL_START = date(2017, 1, 2)


def _session_on_or_before(day: date) -> date:
    return day if is_session(day) else previous_session(day)


# A single shared bar-history end for every surviving (non-delisted)
# security, so no two survivors quietly stop on different dates and
# pollute a future survivorship-gap computation (review round 3, SHOULD
# FIX 7).
_FIXTURE_END = _session_on_or_before(date(2020, 6, 30))

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
        "announced_at",
        "source_action_id",
        "cancelled",
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
    "statement_facts": (
        "cik",
        "fact_name",
        "xbrl_tag",
        "period_start",
        "period_end",
        "period_days",
        "value",
        "unit",
        "form",
        "filing_accession",
        "basis",
        "comparative",
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


# The first real XNYS session on or after `_GLOBAL_START` (2017-01-02 is the
# New Year's Day holiday observance, not a session): used for every listing
# `valid_from` that shares `_GLOBAL_START` as its bar-history start, so no
# security's listing predates its own first bar session (review round 4,
# nit 4).
_GLOBAL_START_SESSION = _session_on_or_after(_GLOBAL_START)


def sessions_between(start: date, end: date) -> list[date]:
    """Every XNYS session `s` with `start <= s <= end` (inclusive), by
    calendar date rather than a fixed count -- used so several entities can
    share a bar-history start (`_GLOBAL_START`) or end (`_FIXTURE_END`)
    while still landing their own filings/ex-dates/probes on the specific
    calendar dates each case is built around."""
    sessions = [_session_on_or_after(start)]
    while True:
        nxt = next_session(sessions[-1])
        if nxt > end:
            break
        sessions.append(nxt)
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


def _sessions_gap(start: date, end: date) -> int:
    """Number of sessions strictly after `start` up to and including `end`
    -- the same gap definition
    `tests/test_fixture_universe.py::test_boundary_delisting_gap_equals_threshold_exactly`
    uses -- so a README gap count is always computed from the data instead
    of hardcoded (review round 4, nit 3)."""
    gap = 0
    day = start
    while day < end:
        day = next_session(day)
        gap += 1
    return gap


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
    statement_facts: list[dict[str, object]] = field(default_factory=list)
    readme_cases: list[dict[str, str]] = field(default_factory=list)
    statement_readme_cases: list[dict[str, str]] = field(default_factory=list)

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
        floor: float | None = None,
        ceiling: float | None = None,
        source: str = _SOURCE_ALPACA,
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        """A bounded random-walk daily bar series.

        Reflected at `floor`/`ceiling` (defaults `0.4x`/`2.5x` of
        `start_price`) rather than left as an unbounded multiplicative walk,
        so a multi-year series (several of these now run `_GLOBAL_START` to
        `_FIXTURE_END`, review round 3) cannot drift below
        `universe.min_price` or to an implausible level. Callers with a
        split (whose raw price must still clear `universe.min_price` *after*
        `apply_split` divides by the ratio) pass an explicit `floor`.
        """
        rng = random.Random(seed)
        floor_price = floor if floor is not None else start_price * 0.4
        ceiling_price = ceiling if ceiling is not None else start_price * 2.5
        price = start_price
        for session in sessions:
            open_price = price
            daily_return = rng.uniform(-0.015, 0.015)
            close_price = open_price * (1 + daily_return)
            close_price = min(max(close_price, floor_price), ceiling_price)
            close_price = round(close_price, 2)
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

    def apply_split(self, security_id: str, ex_date: date, ratio: float) -> None:
        """Divide every OHLC field of `security_id`'s already-generated bars
        on or after `ex_date` by `ratio`, so the *raw* series itself shows
        the split's price jump (review round 3, MUST FIX 1) -- `bars()`
        generates a continuous walk with no knowledge of splits, so this
        must run after both `bars()` and `action()` for the same case.
        """
        for row in self.prices_daily:
            if row["security_id"] == security_id and row["session"] >= ex_date:  # type: ignore[operator]
                for column in ("open", "high", "low", "close"):
                    row[column] = round(row[column] / ratio, 2)  # type: ignore[operator]

    def bar_revision(
        self,
        security_id: str,
        session: date,
        new_close: float,
        revised_at: datetime,
        *,
        source: str = _SOURCE_ALPACA,
    ) -> None:
        """A second `prices_daily` row for a session that already has a bar:
        a re-fetch that returned a different close. Per the spec's
        "Master column sources" table, a bar revision's `known_at` is the
        `ingested_at` of the re-fetch, not the session close -- both equal
        `revised_at` here."""
        open_price = round(new_close * 0.995, 2)
        high_price = round(max(open_price, new_close) * 1.004, 2)
        low_price = round(min(open_price, new_close) * 0.996, 2)
        self.prices_daily.append(
            {
                "security_id": security_id,
                "session": session,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": round(new_close, 2),
                "volume": 500_000,
                "known_at": revised_at,
                "ingested_at": revised_at,
                "source": source,
                "provenance": "bar",
            }
        )

    def action(
        self,
        security_id: str,
        action_type: str,
        ex_date: date,
        ratio_or_amount: float,
        *,
        known_at: datetime,
        announced_at: datetime | None = None,
        source: str = _SOURCE_ALPACA,
        ingested_delay: timedelta = timedelta(minutes=10),
        source_action_id: str = "",
        cancelled: bool = False,
    ) -> None:
        self.corporate_actions.append(
            {
                "security_id": security_id,
                "action_type": action_type,
                "ex_date": ex_date,
                "ratio_or_amount": ratio_or_amount,
                "announced_at": announced_at,
                "source_action_id": source_action_id,
                "cancelled": cancelled,
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
        class_member: str = "",
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

    def statement_fact(
        self,
        cik: str,
        fact_name: str,
        xbrl_tag: str,
        period_end: date,
        value: float,
        known_at: datetime,
        filing_accession: str,
        *,
        period_start: date | None = None,
        unit: str = "USD",
        form: str = "10-K",
        basis: str = "reported",
        comparative: bool = False,
        source: str = _SOURCE_EDGAR,
        ingested_delay: timedelta = timedelta(minutes=10),
    ) -> None:
        """One `statement_facts` row (#660): `period_days` is derived from
        `period_start`/`period_end` here (never a caller-supplied number),
        matching `StatementFactRecord.period_days` (`adapters/filings.py`)
        — `0` for an instant fact (`period_start=None`), else the
        duration in days."""
        period_days = 0 if period_start is None else (period_end - period_start).days
        self.statement_facts.append(
            {
                "cik": cik,
                "fact_name": fact_name,
                "xbrl_tag": xbrl_tag,
                "period_start": period_start,
                "period_end": period_end,
                "period_days": period_days,
                "value": value,
                "unit": unit,
                "form": form,
                "filing_accession": filing_accession,
                "basis": basis,
                "comparative": comparative,
                "known_at": known_at,
                "ingested_at": known_at + ingested_delay,
                "source": source,
                "provenance": "filing",
            }
        )

    def statement_case(self, case: str, cik: str, facts: str, dates: str, notes: str) -> None:
        self.statement_readme_cases.append(
            {"case": case, "cik": cik, "facts": facts, "dates": dates, "notes": notes}
        )


# ---------------------------------------------------------------------------
# Case builders. Each function adds one or more securities and every row
# needed to exercise its spec req 13 case, and records a README row.
# ---------------------------------------------------------------------------


def _truncated_delisting(rows: Rows) -> None:
    start = _session_on_or_after(date(2018, 1, 2))
    last_bar = nth_session_after(start, 100)
    filing_session = nth_session_after(last_bar, _GAP_THRESHOLD + 8)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_TRUNC_DELIST", "CIK0001000001", "TRHX"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, last_bar)
    rows.security(security_id, cik, "Truncated History Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=40.0, seed=1)
    rows.delisting(security_id, "25", "Common Stock", "NYSE", filed_at)
    last_session_before_filing = previous_session(filing_session)
    gap = _sessions_gap(last_bar, last_session_before_filing)
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
    """The "delisted name with a fresh, universe-rule-passing history"
    case (review round 3, MUST FIX 4's "at least one delisted name"): bars
    run from `_GLOBAL_START`, and a shares fact is fresh as of a documented
    probe T at the last bar, before this name is later delisted."""
    start = _GLOBAL_START
    last_bar = nth_session_after(_session_on_or_after(date(2018, 1, 2)), 370)
    gap_sessions = max(_GAP_THRESHOLD - 2, 1)
    filing_session = nth_session_after(last_bar, gap_sessions + 1)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_WINDOW_DELIST", "CIK0001000002", "WNDX"
    # The listing's valid_from must equal the first bar session (`start`,
    # i.e. `_GLOBAL_START_SESSION`), not an unrelated later date -- no
    # security may trade before it is listed (review round 4, SHOULD FIX 2).
    listing_start = _GLOBAL_START_SESSION
    filing_known = _filing_acceptance(nth_session_before(start, 5))

    bar_sessions = sessions_between(start, last_bar)
    rows.security(security_id, cik, "Window History Corp", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", listing_start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=22.0, seed=2)
    rows.delisting(security_id, "25", "Common Stock", "NASDAQ", filed_at)

    shares_as_of = nth_session_before(last_bar, 110)
    shares_known = nth_session_after(shares_as_of, 5)
    rows.fact(
        security_id,
        "shares_outstanding",
        shares_as_of,
        3_000_000,
        _filing_acceptance(shares_known),
    )
    probe_t = session_close(last_bar)
    last_session_before_filing = previous_session(filing_session)
    rows.case(
        "Delisting within the gap window (req 13, contrast with truncated); "
        "also T5's one delisted name with a universe-rule-passing history (review round 3)",
        security_id,
        ticker,
        f"bars from {listing_start} (>= universe.min_history_months before probe T); "
        f"last bar {last_bar}, Form 25 filed {filed_at.date()} "
        f"(last session before filing {last_session_before_filing}, "
        f"gap <= gap.missing_tail_sessions={_GAP_THRESHOLD}); "
        f"shares fact as_of {shares_as_of}, "
        f"known_at {_filing_acceptance(shares_known).isoformat()}",
        f"probe T = {probe_t.isoformat()} (close of {last_bar}, its last trading session): "
        "expected to pass universe rules 1 (common/listed exchange), 6 (>= "
        "min_history_months of contiguous history) and 7 (shares fact fresh, well "
        "under max_shares_age_days) -- still listed at T, delisted only after it",
    )


def _clean_merger_delisting(rows: Rows) -> None:
    start = _session_on_or_after(date(2018, 3, 1))
    last_bar = nth_session_after(start, 100)
    filing_session = nth_session_after(last_bar, 1)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_CLEAN_MERGER", "CIK0001000003", "CLNM"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, last_bar)
    rows.security(security_id, cik, "Clean Merger Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
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
    start = _session_on_or_after(date(2018, 4, 2))
    last_bar = nth_session_after(start, 100)
    filing_session = nth_session_after(last_bar, 1)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_25NSE", "CIK0001000004", "NSEX"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, last_bar)
    rows.security(security_id, cik, "NSE Delist Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE_AMERICAN", start, filing_known)
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


def _boundary_delisting(rows: Rows) -> None:
    """A delisting whose gap is *exactly* `gap.missing_tail_sessions`
    sessions -- neither > nor <, so an off-by-one in T15's survivorship-gap
    truncation rule is caught (review round 3, nit 12)."""
    start = _session_on_or_after(date(2018, 5, 1))
    last_bar = nth_session_after(start, 100)
    filing_session = nth_session_after(last_bar, _GAP_THRESHOLD + 1)
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_BOUNDARY_DELIST", "CIK0001000005", "BNDX"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, last_bar)
    rows.security(security_id, cik, "Boundary Gap Corp", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=18.0, seed=19)
    rows.delisting(security_id, "25", "Common Stock", "NYSE", filed_at)
    last_session_before_filing = previous_session(filing_session)
    rows.case(
        "Boundary delisting: gap exactly gap.missing_tail_sessions "
        "(req 13/T15 off-by-one, review round 3 nit)",
        security_id,
        ticker,
        f"last bar {last_bar}, Form 25 filed {filed_at.date()} "
        f"(last session before filing {last_session_before_filing}, "
        f"gap == gap.missing_tail_sessions={_GAP_THRESHOLD} exactly -- NOT truncated)",
        "n/a",
    )


def _dual_class(rows: Rows) -> None:
    """Dual-class company (req 13): three `security_id`s share one `cik` --
    two common classes (Class A, Class B) and one preferred class. Also
    covers "Form 25 on a non-common class, common survives" (req 13): only
    the preferred class DUAL_PFD is delisted. Review round 3 (MUST FIX 5)
    added the second common class so universe rule 1 ("cap summed over
    classes, both listed classes admitted") has two common classes to sum,
    each with its own per-class `shares_outstanding` fact."""
    cik = "CIK0001000006"
    filing_known = _filing_acceptance(nth_session_before(_GLOBAL_START, 5))
    common_sessions = sessions_between(_GLOBAL_START, _FIXTURE_END)
    probe_t = session_close(_session_on_or_after(date(2018, 12, 17)))

    class_a_id, class_a_ticker = "SEC_DUAL_A", "DUALA"
    rows.security(class_a_id, cik, "Dual Class Holdings", filing_known)
    rows.listing(
        class_a_id,
        class_a_ticker,
        "NYSE",
        _GLOBAL_START_SESSION,
        filing_known,
        class_title="Class A Common Stock",
    )
    rows.classification(class_a_id, "common", "common_default", filing_known)
    rows.bars(class_a_id, common_sessions, start_price=80.0, seed=5)

    class_b_id, class_b_ticker = "SEC_DUAL_B", "DUALB"
    rows.security(class_b_id, cik, "Dual Class Holdings", filing_known)
    rows.listing(
        class_b_id,
        class_b_ticker,
        "NYSE",
        _GLOBAL_START_SESSION,
        filing_known,
        class_title="Class B Common Stock",
    )
    rows.classification(class_b_id, "common", "common_default", filing_known)
    rows.bars(class_b_id, common_sessions, start_price=30.0, seed=6)

    shares_as_of = _session_on_or_after(date(2018, 6, 1))
    shares_known = _filing_acceptance(_session_on_or_after(date(2018, 6, 8)))
    rows.fact(
        class_a_id,
        "shares_outstanding",
        shares_as_of,
        10_000_000,
        shares_known,
        class_member="ClassA",
    )
    rows.fact(
        class_b_id,
        "shares_outstanding",
        shares_as_of,
        4_000_000,
        shares_known,
        class_member="ClassB",
    )

    preferred_id, preferred_ticker = "SEC_DUAL_PFD", "DUALP"
    preferred_start = _session_on_or_after(date(2018, 5, 1))
    preferred_last_bar = nth_session_after(preferred_start, 100)
    preferred_filing_session = nth_session_after(preferred_last_bar, 1)
    filed_at = _filing_acceptance(preferred_filing_session)
    preferred_sessions = sessions_between(preferred_start, preferred_last_bar)
    rows.security(preferred_id, cik, "Dual Class Holdings", filing_known)
    rows.listing(
        preferred_id,
        preferred_ticker,
        "NYSE",
        preferred_start,
        filing_known,
        class_title="6% Preferred Stock",
    )
    rows.classification(preferred_id, "preferred", "preferred_class_suffix", filing_known)
    rows.bars(preferred_id, preferred_sessions, start_price=25.0, seed=7)
    rows.delisting(preferred_id, "25", "6% Preferred Stock", "NYSE", filed_at)

    rows.case(
        "Dual-class company, three security_ids sharing one cik: two common "
        "classes plus one preferred (req 13; review round 3 MUST FIX 5)",
        f"{class_a_id}, {class_b_id}, {preferred_id}",
        f"{class_a_ticker}, {class_b_ticker}, {preferred_ticker}",
        f"cik {cik}; bars from {_GLOBAL_START_SESSION} through {_FIXTURE_END} "
        "for both common classes",
        f"probe T = {probe_t.isoformat()} (close of {_session_on_or_after(date(2018, 12, 17))}): "
        "expected to pass universe rule 1 with both common classes admitted and cap "
        f"summed over {class_a_id} (10,000,000 sh, class_member=ClassA) and "
        f"{class_b_id} (4,000,000 sh, class_member=ClassB), both shares facts known "
        f"{shares_known.isoformat()}",
    )
    rows.case(
        "Form 25 on a non-common class; common classes survive (req 13)",
        preferred_id,
        preferred_ticker,
        f"Form 25 filed {filed_at.date()} for the preferred class only; "
        f"{class_a_id} ({class_a_ticker}) and {class_b_id} ({class_b_ticker}) have "
        f"bars through {_FIXTURE_END} and no delisting row",
        "n/a",
    )


def _exchange_transfer(rows: Rows) -> None:
    filing_session = _session_on_or_after(date(2018, 10, 23))
    filed_at = _filing_acceptance(filing_session)
    security_id, cik, ticker = "SEC_TRANSFER", "CIK0001000007", "TRNS"
    filing_known = _filing_acceptance(nth_session_before(_GLOBAL_START, 5))

    new_valid_from = nth_session_after(filing_session, min(_TRANSFER_WINDOW - 2, 3))
    new_known_session = nth_session_after(filing_session, _TRANSFER_WINDOW + 2)
    # Review round 3 (item 11): a listing's known_at is a filing-acceptance
    # instant, not a bar's session close.
    new_known_at = _filing_acceptance(new_known_session)
    probe_session = nth_session_after(filing_session, 2)
    probe_t = session_close(probe_session)

    bar_sessions = sessions_between(_GLOBAL_START, _FIXTURE_END)
    rows.security(security_id, cik, "Transfer Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", _GLOBAL_START_SESSION, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=30.0, seed=8)
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
    shares_as_of = _session_on_or_after(date(2018, 4, 1))
    shares_known = _filing_acceptance(_session_on_or_after(date(2018, 4, 10)))
    rows.fact(security_id, "shares_outstanding", shares_as_of, 6_000_000, shares_known)
    rows.case(
        "Exchange transfer: Form 25 + new listing within master.transfer_window_sessions (req 13)",
        security_id,
        ticker,
        f"bars from {_GLOBAL_START_SESSION} through {_FIXTURE_END}; "
        f"Form 25 filed {filed_at.date()} "
        f"(known_at {filed_at.isoformat()}); new NASDAQ listing valid_from {new_valid_from}, "
        f"known_at {new_known_at.isoformat()} (a filing-acceptance instant, review round 3 "
        "item 11); shares fact as_of "
        f"{shares_as_of}, known_at {shares_known.isoformat()}",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): strictly between the "
        "filing's known_at and the new listing's known_at, so `listings_as_of`/`universe_as_of` "
        "at T see it delisted, not yet transferred; also expected to pass universe rules 6 "
        "(>= min_history_months of history from _GLOBAL_START) and 7 (shares fact fresh)",
    )


def _same_company_ticker_change(rows: Rows) -> None:
    start = _session_on_or_after(date(2018, 7, 2))
    change_session = _session_on_or_after(date(2018, 11, 7))
    security_id, cik = "SEC_TICKCHANGE", "CIK0001000008"
    filing_known = _filing_acceptance(nth_session_before(start, 20))
    rename_known = _filing_acceptance(previous_session(change_session))

    bar_sessions = sessions_between(start, _FIXTURE_END)
    rows.security(security_id, cik, "Ticker Change Co", filing_known)
    rows.listing(security_id, "TCKA", "NYSE", start, filing_known)
    rows.listing(security_id, "TCKB", "NYSE", change_session, rename_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=18.0, seed=9)
    rows.case(
        "Same-company ticker change: one security_id, two listing ranges (req 13)",
        security_id,
        "TCKA -> TCKB",
        f"TCKA from {start}, TCKB from {change_session}; bars through {_FIXTURE_END}",
        "n/a",
    )


def _reused_ticker(rows: Rows) -> None:
    start_1 = _session_on_or_after(date(2018, 8, 1))
    last_bar_1 = nth_session_after(start_1, 100)
    filing_session = nth_session_after(last_bar_1, 1)
    filed_at = _filing_acceptance(filing_session)
    security_id_1, cik_1 = "SEC_REUSE_1", "CIK0001000009"
    filing_known_1 = _filing_acceptance(nth_session_before(start_1, 20))

    bar_sessions_1 = sessions_between(start_1, last_bar_1)
    rows.security(security_id_1, cik_1, "First Reuse Corp", filing_known_1)
    rows.listing(security_id_1, "REUSE", "NYSE", start_1, filing_known_1)
    rows.classification(security_id_1, "common", "common_default", filing_known_1)
    rows.bars(security_id_1, bar_sessions_1, start_price=12.0, seed=10)
    rows.delisting(security_id_1, "25", "Common Stock", "NYSE", filed_at)

    start_2 = _session_on_or_after(date(2019, 8, 1))
    security_id_2, cik_2 = "SEC_REUSE_2", "CIK0001000010"
    filing_known_2 = _filing_acceptance(nth_session_before(start_2, 20))

    bar_sessions_2 = sessions_between(start_2, _FIXTURE_END)
    rows.security(security_id_2, cik_2, "Second Reuse Corp", filing_known_2)
    rows.listing(security_id_2, "REUSE", "NASDAQ", start_2, filing_known_2)
    rows.classification(security_id_2, "common", "common_default", filing_known_2)
    rows.bars(security_id_2, bar_sessions_2, start_price=9.0, seed=11)
    rows.case(
        "Ticker reused by a different company: two security_ids, "
        "non-overlapping listings, same ticker (req 13)",
        f"{security_id_1}, {security_id_2}",
        "REUSE (both)",
        f"{security_id_1} delisted {filed_at.date()}; {security_id_2} listed from {start_2}",
        "n/a",
    )


def _plain_split(rows: Rows) -> None:
    start = _session_on_or_after(date(2018, 9, 4))
    ex_date = _session_on_or_after(date(2018, 12, 13))
    known_at = session_close(previous_session(ex_date))  # first-seen, no announcement
    security_id, cik, ticker = "SEC_SPLIT_PLAIN", "CIK0001000011", "SPLT"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, _FIXTURE_END)
    rows.security(security_id, cik, "Plain Split Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    # floor is set so that after apply_split divides by 2, the raw close
    # still comfortably clears universe.min_price (review round 3, MUST FIX 1).
    rows.bars(security_id, bar_sessions, start_price=100.0, seed=12, floor=_MIN_PRICE * 2 * 3)
    rows.action(security_id, "split", ex_date, 2.0, known_at=known_at)
    rows.apply_split(security_id, ex_date, 2.0)
    rows.case(
        "Plain 2-for-1 split (req 13); raw close visibly drops by the ratio "
        "on ex-date (review round 3 MUST FIX 1)",
        security_id,
        ticker,
        f"ex_date {ex_date}, known_at {known_at.isoformat()} "
        "(close before ex-date, no announcement); bars through "
        f"{_FIXTURE_END}",
        "n/a",
    )


def _split_between_filing_and_t(rows: Rows) -> None:
    filing_session = _session_on_or_after(date(2018, 10, 29))
    ex_date = _session_on_or_after(date(2019, 1, 11))
    split_known_at = session_close(previous_session(ex_date))
    probe_session = _session_on_or_after(date(2019, 1, 28))
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_SPLIT_BETWEEN", "CIK0001000012", "SPBT"
    filing_known = _filing_acceptance(nth_session_before(_GLOBAL_START, 5))
    shares_known_at = _filing_acceptance(filing_session)

    bar_sessions = sessions_between(_GLOBAL_START, _FIXTURE_END)
    rows.security(security_id, cik, "Split Between Filing Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", _GLOBAL_START_SESSION, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=60.0, seed=13, floor=_MIN_PRICE * 3 * 2)
    rows.fact(security_id, "shares_outstanding", filing_session, 5_000_000, shares_known_at)
    rows.action(security_id, "split", ex_date, 3.0, known_at=split_known_at)
    rows.apply_split(security_id, ex_date, 3.0)
    rows.case(
        "Split between a shares filing and a documented T (req 13); "
        "also passes universe rules 6/7 (review round 3 MUST FIX 4)",
        security_id,
        ticker,
        f"bars from {_GLOBAL_START_SESSION} through {_FIXTURE_END}; "
        f"shares fact as_of {filing_session} "
        f"(known_at {shares_known_at.isoformat()}); 3-for-1 split ex_date {ex_date} "
        f"(known_at {split_known_at.isoformat()}), raw close drops by 3x on ex-date",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): after both the shares "
        "filing and the split's ex-date, so both are known and the split is effective at T; "
        "also expected to pass universe rules 6 (history from _GLOBAL_START) and 7 (shares "
        "fact fresh, ~3 months old at T)",
    )


def _split_known_before_t_ex_after_t(rows: Rows) -> None:
    announced_at = session_close(_session_on_or_after(date(2018, 11, 15)))
    ex_date = _session_on_or_after(date(2019, 2, 14))
    probe_session = _session_on_or_after(date(2018, 11, 23))  # the half day, below
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_SPLIT_FUTURE", "CIK0001000013", "SPFT"
    filing_known = _filing_acceptance(nth_session_before(_GLOBAL_START, 5))

    bar_sessions = sessions_between(_GLOBAL_START, _FIXTURE_END)
    rows.security(security_id, cik, "Split Future Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", _GLOBAL_START_SESSION, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=90.0, seed=14, floor=_MIN_PRICE * 4 * 2)
    rows.action(
        security_id, "split", ex_date, 4.0, known_at=announced_at, announced_at=announced_at
    )
    rows.apply_split(security_id, ex_date, 4.0)

    shares_as_of = _session_on_or_after(date(2018, 5, 1))
    shares_known = _filing_acceptance(_session_on_or_after(date(2018, 5, 8)))
    rows.fact(security_id, "shares_outstanding", shares_as_of, 7_000_000, shares_known)

    holiday, half_day = _holiday_and_half_day(2018)
    rows.case(
        "Split known before a documented T with ex-date after T (req 13); "
        "also passes universe rules 6/7 (review round 3 MUST FIX 4)",
        security_id,
        ticker,
        f"bars from {_GLOBAL_START_SESSION} through {_FIXTURE_END}; split announced "
        f"{announced_at.isoformat()}, ex_date {ex_date} (after probe T), raw close "
        "drops by 4x on ex-date; shares fact as_of "
        f"{shares_as_of}, known_at {shares_known.isoformat()}",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): split is known but not "
        "yet effective (ex-date > T); rule 4 and universe_as_of must use the raw close and "
        "unadjusted shares at T; also expected to pass universe rules 6 (history from "
        "_GLOBAL_START) and 7 (shares fact fresh, ~6 months old at T)",
    )
    rows.case(
        "Holiday inside a bar range: no bar exists on the holiday (req 13)",
        security_id,
        ticker,
        f"{holiday.isoformat()} (Thanksgiving) falls inside {security_id}'s bar range "
        f"{_GLOBAL_START_SESSION}..{_FIXTURE_END} and has no prices_daily row",
        "n/a",
    )
    rows.case(
        "Half day inside a bar range: known_at is the early close (req 13)",
        security_id,
        ticker,
        f"{half_day.isoformat()} (day after Thanksgiving) falls inside {security_id}'s bar "
        "range; its prices_daily.known_at is calendar.session_close(half_day), the early close",
        "n/a",
    )


def _split_backfilled_and_bar_revision(rows: Rows) -> None:
    """The two T6 acceptance cases the spec names explicitly (review round
    3, SHOULD FIX 10): a split whose ex-date is 2018 but which is only
    *ingested* in 2026 -- a backfill run discovers it very late (a 2026
    `ingested_at`), but its `known_at` still follows the ordinary
    first-seen rule (req 5: the close of the session before ex-date,
    regardless of when it was ingested), so the late discovery shows up
    only in `ingested_at`, never `known_at` (review round 4, MUST FIX 1;
    was previously known_at also in 2026, which violated req 5). "Backfilled
    2018 split in 2026: adjusted_prices_as_of(2019-01-31 close) adjusts 2017
    prices", and a re-fetched bar revision (a second row for the same
    session with a different close, `known_at = ingested_at`, well after
    the original bar's `known_at`)."""
    ex_date = _session_on_or_after(date(2018, 3, 15))
    probe_session = _session_on_or_after(date(2019, 1, 31))
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_SPLIT_BACKFILLED", "CIK0001000014", "BKFL"
    filing_known = _filing_acceptance(nth_session_before(_GLOBAL_START, 5))
    # First-seen action, no announcement: known_at = close of the session
    # before ex-date (req 5), regardless of how late it was ingested.
    known_at = session_close(previous_session(ex_date))
    ingested_at = _dt(date(2026, 1, 20), 9, 0)  # a late 2026 backfill discovery

    bar_sessions = sessions_between(_GLOBAL_START, _FIXTURE_END)
    rows.security(security_id, cik, "Backfilled Split Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", _GLOBAL_START_SESSION, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=60.0, seed=15, floor=_MIN_PRICE * 2 * 3)
    rows.corporate_actions.append(
        {
            "security_id": security_id,
            "action_type": "split",
            "ex_date": ex_date,
            "ratio_or_amount": 2.0,
            "announced_at": None,
            "source_action_id": "",
            "cancelled": False,
            "known_at": known_at,
            "ingested_at": ingested_at,
            "source": _SOURCE_ALPACA,
            "provenance": "action",
        }
    )
    rows.apply_split(security_id, ex_date, 2.0)
    rows.case(
        "Backfilled 2018 split: known_at at the close before ex-date (2018, "
        "the ordinary first-seen rule), ingested_at years later in a 2026 "
        "backfill run (spec T6 acceptance criterion, review round 3 SHOULD "
        "FIX 10, review round 4 MUST FIX 1)",
        security_id,
        ticker,
        f"bars from {_GLOBAL_START_SESSION} through {_FIXTURE_END}; split ex_date {ex_date}, "
        f"known_at {known_at.isoformat()} (close of the session before ex-date), "
        f"ingested_at {ingested_at.isoformat()} (a late 2026 backfill discovery -- "
        "the late discovery shows only in ingested_at, never known_at) -- raw close "
        "drops by 2x on ex-date regardless of when it became known",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}, a 2019 rebalance date): "
        "known_at is well before T, so adjusted_prices_as_of(T, include_dividends=False) "
        "should adjust the 2017/2018 raw closes for this split even though it was only "
        "ingested in 2026",
    )

    revised_session = _session_on_or_after(date(2018, 6, 1))
    original = next(
        r
        for r in rows.prices_daily
        if r["security_id"] == security_id and r["session"] == revised_session
    )
    original_close = float(original["close"])  # type: ignore[arg-type]
    revised_at = session_close(revised_session) + timedelta(days=14)
    new_close = round(original_close * 1.05, 2)
    rows.bar_revision(security_id, revised_session, new_close, revised_at)
    rows.case(
        "Re-fetched bar revision: second row for the same session, different "
        "close, known_at = ingested_at later (spec T6 acceptance criterion, "
        "review round 3 SHOULD FIX 10)",
        security_id,
        ticker,
        f"session {revised_session}: original close {original_close} "
        f"(known_at {original['known_at']}), revision close {new_close} "
        f"(known_at = ingested_at = {revised_at.isoformat()})",
        "n/a",
    )


def _revised_dividend(rows: Rows) -> None:
    start = _session_on_or_after(date(2018, 12, 3))
    ex_date = _session_on_or_after(date(2019, 2, 25))
    first_known_at = session_close(previous_session(ex_date))
    revision_known_at = session_close(nth_session_after(ex_date, 20)) + timedelta(hours=1)
    security_id, cik, ticker = "SEC_DIV_REVISED", "CIK0001000015", "DIVR"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, _FIXTURE_END)
    rows.security(security_id, cik, "Revised Dividend Co", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=35.0, seed=16)
    rows.action(security_id, "dividend", ex_date, 0.10, known_at=first_known_at)
    # Spec req 5: a revision's known_at = ingested_at (never back-dated).
    rows.action(
        security_id,
        "dividend",
        ex_date,
        0.12,
        known_at=revision_known_at,
        ingested_delay=timedelta(0),
    )
    rows.case(
        "Revised dividend: two rows, same ex_date, second known_at = "
        "ingested_at (equal), later than the first (req 13)",
        security_id,
        ticker,
        f"ex_date {ex_date}: first-seen known_at {first_known_at.isoformat()} amount 0.10; "
        f"revision known_at = ingested_at = {revision_known_at.isoformat()} amount 0.12",
        "n/a",
    )


def _redated_split(rows: Rows) -> None:
    """#108: the source moves a split to a later ex-date after the first
    one has passed. Both rows carry the source's id, so the second is a
    revision of the same event (`known_at = ingested_at`), and the split
    applies once: at the old ex-date until the re-date is known, at the new
    one after. The raw bars jump on the true (new) ex-date."""
    start = _session_on_or_after(date(2019, 5, 1))
    first_ex_date = _session_on_or_after(date(2019, 6, 10))
    new_ex_date = _session_on_or_after(date(2019, 6, 17))
    first_known_at = session_close(previous_session(first_ex_date))
    redated_at = session_close(nth_session_after(first_ex_date, 2)) + timedelta(hours=1)
    source_action_id = "fixture-redated-split"
    security_id, cik, ticker = "SEC_SPLIT_REDATED", "CIK0001000020", "RDAT"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    rows.security(security_id, cik, "Redated Split Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions_between(start, _FIXTURE_END), start_price=80.0, seed=23)
    rows.action(
        security_id,
        "split",
        first_ex_date,
        2.0,
        known_at=first_known_at,
        source_action_id=source_action_id,
    )
    rows.action(
        security_id,
        "split",
        new_ex_date,
        2.0,
        known_at=redated_at,
        ingested_delay=timedelta(0),
        source_action_id=source_action_id,
    )
    rows.apply_split(security_id, new_ex_date, 2.0)
    rows.case(
        "Re-dated split: same source id, ex-date moved after the first one "
        "passed; applies once (#108)",
        security_id,
        ticker,
        f"first ex_date {first_ex_date} known_at {first_known_at.isoformat()}; "
        f"re-dated to {new_ex_date} at known_at = ingested_at = {redated_at.isoformat()}",
        f"{first_ex_date} close (old ex-date applies), {new_ex_date} close (new ex-date "
        "applies, old one retired)",
    )


def _redated_split_without_id(rows: Rows) -> None:
    """#108 audit: an id-less split re-dated to an *earlier* ex-date after
    the first was known. The ingest that sees it writes a cancel for the old
    key and a replacement row for the new one, both stamped at that ingest:
    before it nothing knew the earlier date, and the split never applies
    twice. The raw bars jump on the true (new) ex-date."""
    start = _session_on_or_after(date(2019, 9, 3))
    first_ex_date = _session_on_or_after(date(2019, 10, 21))
    new_ex_date = _session_on_or_after(date(2019, 10, 14))
    first_known_at = session_close(previous_session(first_ex_date))
    # Re-dated the evening the first ex-date became known: the new ex-date
    # is already past, the old one not yet effective.
    redated_at = first_known_at + timedelta(hours=2)
    security_id, cik, ticker = "SEC_SPLIT_REDATED_NOID", "CIK0001000022", "RDNI"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    rows.security(security_id, cik, "Redated Split No Id Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions_between(start, _FIXTURE_END), start_price=90.0, seed=25)
    rows.action(security_id, "split", first_ex_date, 2.0, known_at=first_known_at)
    for ex_date, cancelled in ((first_ex_date, True), (new_ex_date, False)):
        rows.action(
            security_id,
            "split",
            ex_date,
            2.0,
            known_at=redated_at,
            ingested_delay=timedelta(0),
            cancelled=cancelled,
        )
    rows.apply_split(security_id, new_ex_date, 2.0)
    rows.case(
        "Re-dated split, no source id: moved to an earlier ex-date; cancel of the "
        "old key plus a replacement, both at the re-dating ingest (#108)",
        security_id,
        ticker,
        f"first ex_date {first_ex_date} known_at {first_known_at.isoformat()}; "
        f"cancelled and replaced by ex_date {new_ex_date} at known_at = ingested_at = "
        f"{redated_at.isoformat()}",
        f"just before and after {redated_at.isoformat()}",
    )


def _cancelled_dividend(rows: Rows) -> None:
    """#108: a dividend with no source id is withdrawn after its ex-date. A
    `cancelled` revision of the same key removes it from its `known_at` on
    (the same row retires the old key of an id-less re-date)."""
    start = _session_on_or_after(date(2019, 8, 1))
    ex_date = _session_on_or_after(date(2019, 9, 16))
    first_known_at = session_close(previous_session(ex_date))
    cancelled_at = session_close(nth_session_after(ex_date, 4)) + timedelta(hours=1)
    security_id, cik, ticker = "SEC_DIV_CANCELLED", "CIK0001000021", "DCAN"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    rows.security(security_id, cik, "Cancelled Dividend Co", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, sessions_between(start, _FIXTURE_END), start_price=40.0, seed=24)
    rows.action(security_id, "dividend", ex_date, 0.40, known_at=first_known_at)
    rows.action(
        security_id,
        "dividend",
        ex_date,
        0.40,
        known_at=cancelled_at,
        ingested_delay=timedelta(0),
        cancelled=True,
    )
    rows.case(
        "Cancelled dividend: no source id, withdrawn after its ex-date by a "
        "cancelled revision (#108)",
        security_id,
        ticker,
        f"ex_date {ex_date}: first-seen known_at {first_known_at.isoformat()} amount 0.40; "
        f"cancelled at known_at = ingested_at = {cancelled_at.isoformat()}",
        f"just before and after {cancelled_at.isoformat()}",
    )


def _restated_shares_fact(rows: Rows) -> None:
    start = _session_on_or_after(date(2019, 1, 2))
    as_of_date = nth_session_after(start, 10)
    first_known_at = _filing_acceptance(nth_session_after(start, 15))
    second_known_at = _filing_acceptance(nth_session_after(start, 60))
    security_id, cik, ticker = "SEC_FACTS_RESTATED", "CIK0001000016", "FRST"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, _FIXTURE_END)
    rows.security(security_id, cik, "Restated Shares Co", filing_known)
    rows.listing(security_id, ticker, "NYSE", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=45.0, seed=17)
    rows.fact(
        security_id,
        "shares_outstanding",
        as_of_date,
        1_000_000,
        first_known_at,
        filing_accession="0001000016-19-000001",
    )
    rows.fact(
        security_id,
        "shares_outstanding",
        as_of_date,
        1_050_000,
        second_known_at,
        filing_accession="0001000016-19-000002",
    )
    rows.case(
        "Restated shares fact: same as_of_date, two known_ats, two distinct "
        "filing_accessions (req 13)",
        security_id,
        ticker,
        f"as_of_date {as_of_date}: known_at {first_known_at.isoformat()} value 1,000,000 "
        "(accession 0001000016-19-000001); known_at "
        f"{second_known_at.isoformat()} value 1,050,000 (accession 0001000016-19-000002)",
        "n/a",
    )


def _stale_shares_fact(rows: Rows) -> None:
    start = _session_on_or_after(date(2019, 2, 1))
    as_of_date = start
    known_at = _filing_acceptance(nth_session_after(start, 5))
    probe_day = as_of_date + timedelta(days=_MAX_SHARES_AGE_DAYS + 30)
    probe_session = _session_on_or_after(probe_day)
    probe_t = session_close(probe_session)
    security_id, cik, ticker = "SEC_FACTS_STALE", "CIK0001000017", "STLF"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    # Bars run through _FIXTURE_END (a survivor): review round 3 MUST FIX 3
    # -- they must not stop before the probe session, or the name looks
    # "listed with no bar" at T for reasons unrelated to the stale-shares
    # case this fixture exists to demonstrate. _FIXTURE_END is well past
    # probe_t, and start (2019-02-01) is already >= min_history_months
    # before probe_t (~14 months), so no _GLOBAL_START push is needed here.
    bar_sessions = sessions_between(start, _FIXTURE_END)
    rows.security(security_id, cik, "Stale Shares Co", filing_known)
    rows.listing(security_id, ticker, "NASDAQ", start, filing_known)
    rows.classification(security_id, "common", "common_default", filing_known)
    rows.bars(security_id, bar_sessions, start_price=28.0, seed=18)
    rows.fact(security_id, "shares_outstanding", as_of_date, 2_000_000, known_at)
    age_days = (probe_session - as_of_date).days
    rows.case(
        "Stale shares fact: older than universe.max_shares_age_days at a "
        "documented T (req 13); bars run through the common fixture end so "
        "only rule 7 fails, not rule 6 (review round 3 MUST FIX 3)",
        security_id,
        ticker,
        f"shares fact as_of_date {as_of_date}, known_at {known_at.isoformat()}; "
        f"bars from {start} through {_FIXTURE_END} (contiguous through and past probe T)",
        f"probe T = {probe_t.isoformat()} (close of {probe_session}): age at T is "
        f"{age_days} days > universe.max_shares_age_days={_MAX_SHARES_AGE_DAYS}; "
        "expected to pass rule 6 (history) but fail rule 7 (stale shares) in isolation",
    )


def _unclassifiable_name(rows: Rows) -> None:
    start = _session_on_or_after(date(2019, 3, 1))
    security_id, cik, ticker = "SEC_UNCLASSIFIABLE", "CIK0001000018", "UNCX"
    filing_known = _filing_acceptance(nth_session_before(start, 20))

    bar_sessions = sessions_between(start, _FIXTURE_END)
    rows.security(security_id, cik, "Consolidated Enterprises Number Twelve LLC", filing_known)
    rows.listing(security_id, ticker, "NYSE_AMERICAN", start, filing_known)
    rows.classification(security_id, "unclassifiable", "no_rule_matched", filing_known)
    rows.bars(security_id, bar_sessions, start_price=8.0, seed=20)
    rows.case(
        "Unclassifiable name (req 13)",
        security_id,
        ticker,
        "classifications.security_type = 'unclassifiable', rule = 'no_rule_matched'",
        "n/a",
    )


def _static_pre_2019_listing(rows: Rows) -> None:
    valid_from = date(2017, 1, 3)
    first_bar = _session_on_or_after(valid_from)
    bar_sessions = sessions_between(valid_from, _FIXTURE_END)
    security_id, cik, ticker = "SEC_STATIC_PRE2019", "CIK0001000019", "PRE9"
    # Review round 3 (MUST FIX 6): the earliest filing must precede the
    # first bar like every other case, or securities_as_of returns nothing
    # for this name at any pre-2019 T -- the whole point of the case.
    filing_known = _filing_acceptance(nth_session_before(first_bar, 20))
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
    rows.bars(security_id, bar_sessions, start_price=20.0, seed=21)
    rows.case(
        "snapshot_static-only pre-2019 listing (req 13)",
        security_id,
        ticker,
        f"listing valid_from {valid_from} (pre-2019), provenance snapshot_static, "
        f"known_at {static_fetch_at.isoformat()} (the fetch time, later than valid_from); "
        f"securities.known_at {filing_known.isoformat()} precedes the first bar "
        f"(review round 3 MUST FIX 6); bars through {_FIXTURE_END}",
        "n/a",
    )


def _benchmarks(rows: Rows) -> None:
    sessions = sessions_between(date(2018, 1, 2), _FIXTURE_END)
    seed_known_at = session_close(sessions[0])
    ex_date = _session_on_or_after(date(2018, 6, 25))
    div_known_at = session_close(previous_session(ex_date))

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
            # Not "Common Stock": an ETF's shares are not common equity
            # (review round 3, nit 15).
            class_title="Beneficial Interest Shares",
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
        rows.action(security_id, "dividend", ex_date, div_amount, known_at=div_known_at)
        rows.case(
            f"Benchmark seeded from config with a dividend (req 13): {ticker}",
            security_id,
            ticker,
            f"benchmark=TRUE; dividend ex_date {ex_date} amount {div_amount}; "
            f"bars through {_FIXTURE_END}",
            "n/a",
        )


def _statement_accession(cik: str, year: int, seq: int) -> str:
    """A plausible EDGAR accession number: the filer's 10-digit cik (the
    `CIK` prefix of this generator's `securities.cik` convention stripped),
    the 2-digit acceptance year, and a 6-digit sequence — matching the
    convention `facts.csv`'s own `filing_accession` cells already use."""
    digits = cik.removeprefix("CIK")
    return f"{digits}-{year % 100:02d}-{seq:06d}"


def _statement_facts_plain_issuer(rows: Rows) -> None:
    """A plain issuer with one 10-K (FY plus two comparative years, the
    comparatives flagged) and three 10-Qs (quarter durations, two of them
    also carrying a `operating_cash_flow` year-to-date duration row —
    spec "Statement facts" > Periods: a 10-Q's cash-flow statement is
    year-to-date, never calendarised here). Reuses `SEC_WINDOW_DELIST`'s
    cik (`CIK0001000002`) rather than inventing a bare one."""
    cik = "CIK0001000002"
    accession = _statement_accession(cik, 2020, 1)
    known_at = _filing_acceptance(_session_on_or_after(date(2020, 2, 15)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 12, 31),
        900_000.0,
        known_at,
        accession,
        period_start=date(2019, 1, 1),
    )
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2018, 12, 31),
        850_000.0,
        known_at,
        accession,
        period_start=date(2018, 1, 1),
        comparative=True,
    )
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2017, 12, 31),
        800_000.0,
        known_at,
        accession,
        period_start=date(2017, 1, 1),
        comparative=True,
    )

    q1_accession = _statement_accession(cik, 2020, 2)
    q1_known_at = _filing_acceptance(_session_on_or_after(date(2020, 5, 10)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2020, 3, 31),
        230_000.0,
        q1_known_at,
        q1_accession,
        period_start=date(2020, 1, 1),
        form="10-Q",
    )

    q2_accession = _statement_accession(cik, 2020, 3)
    q2_known_at = _filing_acceptance(_session_on_or_after(date(2020, 8, 10)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2020, 6, 30),
        245_000.0,
        q2_known_at,
        q2_accession,
        period_start=date(2020, 4, 1),
        form="10-Q",
    )
    rows.statement_fact(
        cik,
        "operating_cash_flow",
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        date(2020, 6, 30),
        300_000.0,
        q2_known_at,
        q2_accession,
        period_start=date(2020, 1, 1),
        form="10-Q",
    )

    q3_accession = _statement_accession(cik, 2020, 4)
    q3_known_at = _filing_acceptance(_session_on_or_after(date(2020, 11, 10)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2020, 9, 30),
        260_000.0,
        q3_known_at,
        q3_accession,
        period_start=date(2020, 7, 1),
        form="10-Q",
    )
    rows.statement_fact(
        cik,
        "operating_cash_flow",
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        date(2020, 9, 30),
        480_000.0,
        q3_known_at,
        q3_accession,
        period_start=date(2020, 1, 1),
        form="10-Q",
    )
    rows.statement_case(
        "Plain issuer: one 10-K (FY plus two flagged comparatives) and three 10-Qs "
        "(quarter revenue; two also carry a year-to-date operating_cash_flow row)",
        cik,
        "revenue (FY2019/18/17, Q1-Q3 2020), operating_cash_flow (H1 and 9mo 2020 YTD)",
        f"10-K known_at {known_at.isoformat()} ({accession}); 10-Qs known_at "
        f"{q1_known_at.isoformat()}, {q2_known_at.isoformat()}, {q3_known_at.isoformat()}",
        "n/a",
    )


def _statement_facts_derived_gross_profit(rows: Rows) -> None:
    """An issuer with no filed `GrossProfit` whose stored `revenue` and
    `cost_of_revenue` rows from one accession carry a `derived`
    `gross_profit` row equal to their difference (spec "Derived gross
    profit"). Reuses `SEC_CLEAN_MERGER`'s cik (`CIK0001000003`)."""
    cik = "CIK0001000003"
    accession = _statement_accession(cik, 2020, 1)
    known_at = _filing_acceptance(_session_on_or_after(date(2020, 2, 20)))
    period_end = date(2019, 12, 31)
    period_start = date(2019, 1, 1)
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        period_end,
        1_000_000.0,
        known_at,
        accession,
        period_start=period_start,
    )
    rows.statement_fact(
        cik,
        "cost_of_revenue",
        "us-gaap:CostOfRevenue",
        period_end,
        600_000.0,
        known_at,
        accession,
        period_start=period_start,
    )
    rows.statement_fact(
        cik,
        "gross_profit",
        "us-gaap:Revenues-us-gaap:CostOfRevenue",
        period_end,
        400_000.0,
        known_at,
        accession,
        period_start=period_start,
        basis="derived",
    )
    rows.statement_case(
        "Derived gross profit: no filed GrossProfit; revenue - cost_of_revenue "
        "from one accession stored as a basis='derived' row",
        cik,
        "revenue, cost_of_revenue, gross_profit (derived) — all FY2019",
        f"10-K known_at {known_at.isoformat()} ({accession})",
        "gross_profit.value (400,000.0) equals revenue - cost_of_revenue exactly, "
        "the health --check rule T77c adds",
    )


def _statement_facts_restated_revenue(rows: Rows) -> None:
    """An issuer whose FY2018 revenue is restated in its FY2019 10-K: the
    first-accepted (FY2018 10-K) row is the only one stored, per the
    first-vintage rule — the FY2019 10-K's differing FY2018 comparative
    column (520,000.0, vs. the original 500,000.0) is never written, so it
    is deliberately absent from this fixture rather than represented as a
    row. Reuses `SEC_BOUNDARY_DELIST`'s cik (`CIK0001000005`)."""
    cik = "CIK0001000005"
    accession = _statement_accession(cik, 2019, 1)
    known_at = _filing_acceptance(_session_on_or_after(date(2019, 2, 10)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2018, 12, 31),
        500_000.0,
        known_at,
        accession,
        period_start=date(2018, 1, 1),
    )
    rows.statement_case(
        "Restated revenue: FY2018's original 10-K value is the only row; the FY2019 "
        "10-K's differing comparative (520,000.0) is never stored",
        cik,
        "revenue (FY2018)",
        f"10-K known_at {known_at.isoformat()} ({accession})",
        "no second row for this key exists anywhere in this fixture -- the absence "
        "itself is the case",
    )


def _statement_facts_10ka_first_carrier(rows: Rows) -> None:
    """A 10-K/A that is the first carrier of a key (`total_assets`, an
    instant fact: `period_start=None`, `period_days=0`); the vintage's
    `form` is kept verbatim. Reuses `SEC_BOUNDARY_DELIST`'s cik
    (`CIK0001000005`), a second accession distinct from the restated-
    revenue case above."""
    cik = "CIK0001000005"
    accession = _statement_accession(cik, 2020, 2)
    known_at = _filing_acceptance(_session_on_or_after(date(2020, 3, 1)))
    rows.statement_fact(
        cik,
        "total_assets",
        "us-gaap:Assets",
        date(2019, 12, 31),
        2_000_000.0,
        known_at,
        accession,
        form="10-K/A",
    )
    rows.statement_case(
        "10-K/A first carrier: an amendment is the first (and only) filing to carry "
        "this key; form stays '10-K/A', never normalized to '10-K'",
        cik,
        "total_assets (FY2019, instant: period_start=NULL, period_days=0)",
        f"10-K/A known_at {known_at.isoformat()} ({accession})",
        "n/a",
    )


def _statement_facts_dual_class(rows: Rows) -> None:
    """A dual-class CIK: one row set, never duplicated per class, since
    `statement_facts` is keyed by cik, not security_id (spec decision
    (c)). Reuses the dual-class case's cik (`CIK0001000006`,
    `SEC_DUAL_A`/`SEC_DUAL_B`/`SEC_DUAL_PFD`)."""
    cik = "CIK0001000006"
    accession = _statement_accession(cik, 2020, 1)
    known_at = _filing_acceptance(_session_on_or_after(date(2020, 2, 1)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 12, 31),
        1_200_000.0,
        known_at,
        accession,
        period_start=date(2019, 1, 1),
    )
    rows.statement_case(
        "Dual-class cik: one row set, not duplicated per security_id/class",
        cik,
        "revenue (FY2019)",
        f"10-K known_at {known_at.isoformat()} ({accession})",
        "the join through securities_as_of (T76b) is expected to surface this one "
        "row once per class (SEC_DUAL_A, SEC_DUAL_B, SEC_DUAL_PFD)",
    )


def _statement_facts_no_securities_row_yet(rows: Rows) -> None:
    """A CIK with no securities row until after its first filing: this
    statement filing's known_at (2017-06-15) is strictly before
    `SEC_TRUNC_DELIST`'s `securities.known_at` (2017-12-01T20:30:00+00:00,
    `securities.csv`) for the same cik (`CIK0001000001`) -- so a T76b
    as-of read between the two sees the fact but no securities row to
    join it through, and should return nothing."""
    cik = "CIK0001000001"
    accession = _statement_accession(cik, 2017, 1)
    known_at = _filing_acceptance(_session_on_or_after(date(2017, 6, 15)))
    rows.statement_fact(
        cik,
        "revenue",
        "us-gaap:Revenues",
        date(2016, 12, 31),
        300_000.0,
        known_at,
        accession,
        period_start=date(2016, 1, 1),
    )
    rows.statement_case(
        "No securities row yet: this fact's known_at precedes CIK0001000001's own "
        "securities.known_at (2017-12-01T20:30:00+00:00)",
        cik,
        "revenue (FY2016)",
        f"10-K known_at {known_at.isoformat()} ({accession})",
        "a T76b as-of read at a T between the two known_ats is expected to return "
        "nothing for this cik (no securities row to join through yet)",
    )


# ---------------------------------------------------------------------------
# Profitability statement facts (backtest spec amendment #720, plan T85c):
# a scored baseline on existing issuers plus the point-in-time cases.
# ---------------------------------------------------------------------------

_NEW_YORK = ZoneInfo("America/New_York")
_GROSS_PROFIT_TAG = "us-gaap:GrossProfit"
_ASSETS_TAG = "us-gaap:Assets"

#: Every non-benchmark issuer in `securities.csv`, with the month its fiscal
#: year ends (12 unless a case needs a filing in another month). The index
#: in this tuple sets the issuer's baseline values.
_PROFITABILITY_ISSUERS: tuple[tuple[str, int], ...] = tuple(
    (f"CIK{1000000 + n:010d}", {12: 8, 16: 9}.get(n, 12)) for n in range(1, 23)
)
#: The baseline's fiscal years: FY2015's filings make every fixture
#: rebalance from 2017-01-31 on see a fresh annual pair.
_PROFITABILITY_YEARS = (2015, 2016, 2017, 2018, 2019)

#: Acceptance stamps (spec acceptance, amendment #720): three on an ordinary
#: month-end and two on the fixture window's half-day month-end.
_STAMP_MONTH_END = date(2019, 1, 31)
_STAMP_HALF_DAY = date(2019, 11, 29)
#: The rebalance whose close the late 10-K crosses (T_i).
_CROSSING_SESSION = date(2019, 2, 28)

_CROSSING_CIK = "CIK0001000007"  # SEC_TRANSFER, a universe member 2018-04 to 2019-04
_RESTATED_CIK = "CIK0001000005"  # the T76 restated-FY issuer
_ASSETS_LATER_CIK = "CIK0001000016"  # SEC_FACTS_RESTATED, a member 2019-12 and 2020-01
_DUAL_CIK = "CIK0001000006"
_HALF_DAY_CIK = "CIK0001000012"  # SEC_SPLIT_BETWEEN, 2019-11-29's only universe member
_LOW_BP, _HIGH_BP = 500, 9500  # the crossing issuer's GP/A before and after, basis points


def _new_york(day: date, hour: int, minute: int = 0) -> datetime:
    """`hour:minute` New York time on `day`, as a UTC instant."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=_NEW_YORK).astimezone(UTC)


def _fiscal_period(fiscal_year: int, end_month: int) -> tuple[date, date]:
    """`(period_start, period_end)` of the fiscal year ending in `end_month` of
    `fiscal_year`: from the day after the previous year's end to the month's last day."""
    end = date(fiscal_year + (end_month == 12), end_month % 12 + 1, 1) - timedelta(days=1)
    start = date(fiscal_year - 1 + (end_month == 12), end_month % 12 + 1, 1)
    return start, end


def _default_acceptance(fiscal_year: int, end_month: int, index: int) -> datetime:
    """A baseline 10-K's acceptance: about six weeks after the fiscal year ends, on
    one of four sessions (so the store gains few distinct `known_at`s), mid-month."""
    first = {12: date(fiscal_year + 1, 2, 12), 8: date(fiscal_year, 11, 12)}.get(
        end_month, date(fiscal_year, 12, 10)
    )
    return _filing_acceptance(nth_session_after(_session_on_or_after(first), index % 4))


def _profitability_row(
    rows: Rows,
    cik: str,
    fact_name: str,
    period: tuple[date, date],
    value: float,
    known_at: datetime,
    accession: str,
    form: str = "10-K",
) -> None:
    start, end = period
    if fact_name == "gross_profit":
        rows.statement_fact(
            cik,
            fact_name,
            _GROSS_PROFIT_TAG,
            end,
            value,
            known_at,
            accession,
            period_start=start,
            form=form,
        )
    else:
        rows.statement_fact(cik, fact_name, _ASSETS_TAG, end, value, known_at, accession, form=form)


def _statement_facts_profitability(rows: Rows) -> None:
    """The `profitability` family's fixture (plan T85c): annual `gross_profit`
    (duration) and `total_assets` (instant, same `period_end`) pairs for every
    non-benchmark issuer, FY2015 to FY2019, then the point-in-time cases on
    existing issuers (no new security, so no momentum fixture result moves).

    Values are `assets` and `assets * bp / 10000` with integer basis points, so
    every value is exact. Where an earlier case already owns a filing (the plain
    issuer's FY2019 10-K, the derived-gross-profit 10-K, the restated issuer's
    FY2018 10-K, the no-securities-row-yet 10-K) the pair joins that filing at
    its `known_at`; where a case already stores a key (CIK0001000003's derived
    FY2019 `gross_profit`, CIK0001000005's 10-K/A FY2019 `total_assets`) the
    baseline does not repeat it.
    """
    overrides: dict[tuple[str, int], tuple[datetime, str]] = {
        # The 10-K accepted after close(T_i): crosses the cut at T_i.
        (_CROSSING_CIK, 2018): (
            _new_york(_CROSSING_SESSION, 16, 30),
            _statement_accession(_CROSSING_CIK, 2019, 101),
        ),
        # Ordinary month-end stamps: 15:00, 16:00 (= session_close), 17:30 New York.
        ("CIK0001000002", 2018): (
            _new_york(_STAMP_MONTH_END, 15),
            _statement_accession("CIK0001000002", 2019, 101),
        ),
        ("CIK0001000013", 2018): (
            session_close(_STAMP_MONTH_END),
            _statement_accession("CIK0001000013", 2019, 101),
        ),
        (_DUAL_CIK, 2018): (
            _new_york(_STAMP_MONTH_END, 17, 30),
            _statement_accession(_DUAL_CIK, 2019, 101),
        ),
        # Half-day month-end stamp: the 10-K at 12:30 New York (close 13:00); its
        # total_assets comes in a 10-K/A at 14:00 (below).
        (_HALF_DAY_CIK, 2019): (
            _new_york(_STAMP_HALF_DAY, 12, 30),
            _statement_accession(_HALF_DAY_CIK, 2019, 101),
        ),
        # Filings an earlier case already owns.
        ("CIK0001000001", 2016): (
            _filing_acceptance(_session_on_or_after(date(2017, 6, 15))),
            _statement_accession("CIK0001000001", 2017, 1),
        ),
        ("CIK0001000002", 2019): (
            _filing_acceptance(_session_on_or_after(date(2020, 2, 15))),
            _statement_accession("CIK0001000002", 2020, 1),
        ),
        ("CIK0001000003", 2019): (
            _filing_acceptance(_session_on_or_after(date(2020, 2, 20))),
            _statement_accession("CIK0001000003", 2020, 1),
        ),
        (_RESTATED_CIK, 2018): (
            _filing_acceptance(_session_on_or_after(date(2019, 2, 10))),
            _statement_accession(_RESTATED_CIK, 2019, 1),
        ),
        (_RESTATED_CIK, 2019): (
            _filing_acceptance(_session_on_or_after(date(2020, 2, 10))),
            _statement_accession(_RESTATED_CIK, 2020, 1),
        ),
    }
    # Keys an earlier case already stores, or a case leaves out on purpose.
    skipped = {
        ("CIK0001000003", 2019, "gross_profit"),  # the derived row
        (_RESTATED_CIK, 2019, "total_assets"),  # first carried by the 10-K/A
        (_ASSETS_LATER_CIK, 2019, "total_assets"),  # carried by the later 10-K/A below
        (_HALF_DAY_CIK, 2019, "total_assets"),  # carried by the 14:00 10-K/A below
    }
    stamps: dict[str, datetime] = {}
    for index, (cik, end_month) in enumerate(_PROFITABILITY_ISSUERS):
        for fiscal_year in _PROFITABILITY_YEARS:
            # The dual-class issuer files from FY2018 only: an earlier pair would
            # be fresh at the health tests' 2018-12-17 probe of this cik.
            if cik == _DUAL_CIK and fiscal_year != 2018:
                continue
            period = _fiscal_period(fiscal_year, end_month)
            known_at, accession = overrides.get(
                (cik, fiscal_year),
                (
                    _default_acceptance(fiscal_year, end_month, index),
                    _statement_accession(cik, fiscal_year + (end_month == 12), 100 + index),
                ),
            )
            assets = 1_000_000 + 50_000 * index + 10_000 * (fiscal_year - 2015)
            bp = 2_000 + 200 * index + 50 * (fiscal_year - 2015)
            if cik == _CROSSING_CIK:
                bp = _LOW_BP if fiscal_year < 2018 else _HIGH_BP
            values = {"gross_profit": assets * bp / 10_000, "total_assets": float(assets)}
            for fact_name, value in values.items():
                if (cik, fiscal_year, fact_name) not in skipped:
                    _profitability_row(rows, cik, fact_name, period, value, known_at, accession)
            stamps[f"{cik} FY{fiscal_year}"] = known_at

    # total_assets accepted after its gross-profit filing, twice: the FY2019
    # 10-K carries gross_profit only and a 10-K/A carries the assets, in the
    # next month (the case) and an hour and a half later, after the half-day
    # close (the 14:00 stamp).
    ciks = [c for c, _ in _PROFITABILITY_ISSUERS]
    later_known = _filing_acceptance(_session_on_or_after(date(2020, 1, 15)))
    later_accession = _statement_accession(_ASSETS_LATER_CIK, 2020, 102)
    half_day_known = _new_york(_STAMP_HALF_DAY, 14)
    for cik, end_month, known_at, accession in (
        (_ASSETS_LATER_CIK, 9, later_known, later_accession),
        (_HALF_DAY_CIK, 8, half_day_known, _statement_accession(_HALF_DAY_CIK, 2019, 102)),
    ):
        _profitability_row(
            rows,
            cik,
            "total_assets",
            _fiscal_period(2019, end_month),
            float(1_000_000 + 50_000 * ciks.index(cik) + 10_000 * (2019 - 2015)),
            known_at,
            accession,
            form="10-K/A",
        )

    rows.statement_case(
        "Profitability baseline: an annual gross_profit (duration) and total_assets "
        "(instant, same period_end) pair per fiscal year for every non-benchmark issuer",
        "CIK0001000001 to CIK0001000022 (22 issuers)",
        "gross_profit, total_assets: FY2015-FY2019 (CIK0001000006: FY2018 only); fiscal "
        "years end in December except CIK0001000012 (August) and CIK0001000016 "
        "(September)",
        "each 10-K about six weeks after its fiscal year end, on one of four sessions, "
        "unless a case below sets it",
        "at least 15 issuers have a fresh pair (annual_period_days, max_fact_age_days "
        "of ProfitabilityConfig) known at every month-end close 2017-01-31 to 2020-06-30, "
        "by cik; the engine scores only universe members (at most six names on this "
        "fixture), so a fixture book is one or two names",
    )
    rows.statement_case(
        "10-K accepted after close(T_i): its annual row moves the issuer across the cut at T_i",
        _CROSSING_CIK,
        f"gross_profit/total_assets {_LOW_BP / 100:.0f}% through FY2017, "
        f"{_HIGH_BP / 100:.0f}% from FY2018 (the lowest, then the highest, of any issuer)",
        f"FY2018 10-K known_at {stamps[f'{_CROSSING_CIK} FY2018'].isoformat()} (16:30 New "
        f"York), after close({_CROSSING_SESSION.isoformat()}) "
        f"{session_close(_CROSSING_SESSION).isoformat()}",
        "SEC_TRANSFER is a universe member at T_i and T_i+1 (2019-03-29): invisible at "
        "T_i, so run(end=T_i) is unchanged and run(end=T_i+1) is not",
    )
    rows.statement_case(
        "Restated FY (profitability): FY2018's original 10-K pair is the only one stored; "
        "the FY2019 10-K's restated FY2018 comparative is never stored",
        _RESTATED_CIK,
        "gross_profit, total_assets FY2018 (original); gross_profit FY2019; total_assets "
        "FY2019 from the 10-K/A",
        f"FY2018 10-K known_at {stamps[f'{_RESTATED_CIK} FY2018'].isoformat()}; FY2019 "
        f"10-K (the restating filing) known_at {stamps[f'{_RESTATED_CIK} FY2019'].isoformat()}",
        "the T76 restated-revenue issuer; a read at any T sees the original FY2018 values",
    )
    rows.statement_case(
        "total_assets accepted after its gross-profit filing: scored only from the later "
        "acceptance",
        _ASSETS_LATER_CIK,
        "gross_profit FY2019 (10-K), total_assets FY2019 (10-K/A); fiscal year ends 2019-09-30",
        f"10-K known_at {stamps[f'{_ASSETS_LATER_CIK} FY2019'].isoformat()}; 10-K/A "
        f"known_at {later_known.isoformat()} ({later_accession})",
        "SEC_FACTS_RESTATED is a universe member at 2019-12-31 (gross_profit without its "
        "total_assets: no_facts) and 2020-01-31 (scored)",
    )
    month_end_close = session_close(_STAMP_MONTH_END)
    rows.statement_case(
        f"Acceptance stamps on an ordinary month-end ({_STAMP_MONTH_END.isoformat()}, "
        "close 16:00 New York): 15:00, 16:00 and 17:30",
        "CIK0001000002, CIK0001000013, CIK0001000006",
        "gross_profit, total_assets FY2018",
        f"15:00 {stamps['CIK0001000002 FY2018'].isoformat()} (CIK0001000002); 16:00 "
        f"{stamps['CIK0001000013 FY2018'].isoformat()} = session_close "
        f"{month_end_close.isoformat()} (CIK0001000013); 17:30 "
        f"{stamps[f'{_DUAL_CIK} FY2018'].isoformat()} (CIK0001000006)",
        "all three are universe members at that close: the 15:00 and 16:00 filings are "
        "used at it, the 17:30 one first at 2019-02-28",
    )
    rows.statement_case(
        f"Acceptance stamps on the half-day month-end ({_STAMP_HALF_DAY.isoformat()}, "
        "close 13:00 New York): 12:30 and 14:00",
        _HALF_DAY_CIK,
        "gross_profit FY2019 (10-K), total_assets FY2019 (10-K/A); fiscal year ends 2019-08-31",
        f"12:30 10-K {stamps[f'{_HALF_DAY_CIK} FY2019'].isoformat()}; 14:00 10-K/A "
        f"{half_day_known.isoformat()}; session_close "
        f"{session_close(_STAMP_HALF_DAY).isoformat()}",
        "SEC_SPLIT_BETWEEN is that month-end's only universe member: at the close its "
        "FY2019 gross_profit is visible and its total_assets is not (no_facts); a cut "
        "after 14:00 would score it, one before 12:30 would score FY2018",
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


def _write_readme(
    path: Path, cases: list[dict[str, str]], statement_cases: list[dict[str, str]]
) -> None:
    ordered = sorted(cases, key=lambda c: c["case"])
    statement_ordered = sorted(statement_cases, key=lambda c: c["case"])
    lines = [
        "# Fixture universe (T5)",
        "",
        "Generated by `scripts/make_fixture_universe.py` — do not hand-edit; regenerate instead.",
        "Maps every spec req 13 case to the `security_id`(s), tickers, dates and probe",
        "timestamps that exercise it, for T6-T15 authors.",
        "",
        f"Shared conventions: securities needing a universe-rule-passing history run bars "
        f"from `{_GLOBAL_START_SESSION.isoformat()}`; "
        "every surviving (non-delisted) name's bars run "
        f"through `{_FIXTURE_END.isoformat()}` (the common fixture end session), so no two "
        "survivors quietly stop on different dates.",
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
            "- Every `security_id`, listing and classification not called out in a case "
            "row above still exists to carry that case's bars/actions/facts; this table "
            "lists only the rows that make each req 13 case identifiable.",
            "- SPY and MTUM are both seeded on exchange `NYSE` for simplicity; the real "
            "listings are NYSE Arca, which is not in `universe.exchanges`.",
            "- Most 2018-dated listings here use `provenance=filing` for ticker/exchange "
            "even though the spec's master column-sources table calls pre-~2019 ticker/"
            "exchange `snapshot_static`; this is a deliberate simplification (only "
            "`SEC_STATIC_PRE2019` exercises the `snapshot_static` case on purpose). Per "
            "the owner's decision on "
            "[issue #35](https://github.com/josejuarez96/tradepartner/issues/35), a "
            "`snapshot_static` row is invisible to as-of reads before its own `known_at`, "
            "so PRE9 is unlisted at any T before 2020-01-15.",
            "",
            "## Statement facts (#660)",
            "",
            "Maps every statement-facts fixture case (`statement_facts.csv`) to its cik, "
            "the facts/periods it carries, the filing dates and any case-specific note, "
            "for T76b-T78 authors. Rows are DB-table-level, authored directly rather than "
            "parsed or derived (this generator owns no EDGAR parsing or derivation logic) "
            "— see `make_fixture_universe.py`'s module docstring.",
            "",
            "| Case | cik | Fact(s) | Dates | Notes |",
            "|---|---|---|---|---|",
        ]
    )
    for c in statement_ordered:
        lines.append(f"| {c['case']} | {c['cik']} | {c['facts']} | {c['dates']} | {c['notes']} |")
    lines.extend(
        [
            "",
            "### Notes for readers",
            "",
            "- `statement_facts` is keyed by **cik**, not `security_id` (spec decision "
            "(c)): most cases above reuse an existing req 13 case's cik from "
            "`securities.csv` rather than inventing a bare one, so a later as-of/join "
            "test (T76b) has a security to join against.",
            "- `basis='derived'` and a restated key's single surviving row are authored "
            "directly as the (future) ingest's expected output, not computed by this "
            "generator or verified against a parser here (T77/T77b's job).",
        ]
    )
    path.write_text("\n".join(lines) + "\n")


def build_rows() -> Rows:
    rows = Rows()
    _truncated_delisting(rows)
    _within_window_delisting(rows)
    _clean_merger_delisting(rows)
    _form_25nse(rows)
    _boundary_delisting(rows)
    _dual_class(rows)
    _exchange_transfer(rows)
    _same_company_ticker_change(rows)
    _reused_ticker(rows)
    _plain_split(rows)
    _split_between_filing_and_t(rows)
    _split_known_before_t_ex_after_t(rows)
    _split_backfilled_and_bar_revision(rows)
    _revised_dividend(rows)
    _redated_split(rows)
    _redated_split_without_id(rows)
    _cancelled_dividend(rows)
    _restated_shares_fact(rows)
    _stale_shares_fact(rows)
    _unclassifiable_name(rows)
    _static_pre_2019_listing(rows)
    _benchmarks(rows)
    _statement_facts_plain_issuer(rows)
    _statement_facts_derived_gross_profit(rows)
    _statement_facts_restated_revenue(rows)
    _statement_facts_10ka_first_carrier(rows)
    _statement_facts_dual_class(rows)
    _statement_facts_no_securities_row_yet(rows)
    _statement_facts_profitability(rows)
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
        ("security_id", "action_type", "ex_date", "source_action_id", "known_at"),
    )
    _write_csv(
        output_dir / "facts.csv",
        "facts",
        rows.facts,
        ("security_id", "fact_name", "as_of_date", "class_member", "known_at"),
    )
    _write_csv(
        output_dir / "statement_facts.csv",
        "statement_facts",
        rows.statement_facts,
        ("cik", "fact_name", "period_end", "period_days"),
    )
    _write_readme(output_dir / "README.md", rows.readme_cases, rows.statement_readme_cases)


def main(argv: list[str]) -> int:
    output_dir = Path(argv[1]) if len(argv) > 1 else _DEFAULT_OUTPUT_DIR
    write_fixtures(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
