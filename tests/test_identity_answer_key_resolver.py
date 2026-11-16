"""The resolver is judged by the identity answer key (#3275, ADR 0029, T395).

Each history in `store/identity_answer_key.toml` is turned into a fixture store
from its **input events** and **bar sessions** only (never from its expected
outcomes), the store's resolver (`repair.store_resolver`) and line ends
(`store.delistings.listing_ends_as_of`) are run on it, and `match` compares
what they say with what the cited sources say. The test is parametrized over
whatever histories the file holds, so a history T394b adds is judged without
an edit here. A history the resolver does not yet pass is listed in `XFAIL`
with the issue that fixes it, as `xfail(strict=True)`: when the fix lands the
test fails until the entry is removed, so the list cannot go stale.

How an input event becomes rows (fixture assumptions, none of them a key fact):

- `form25` and `25-nse` -> one `delistings` row (form `25` or `25-NSE`): filed
  on the event date at 16:00 UTC, effective on the date the notice states or,
  when the key gives none, ten days later (Rule 12d2-2(d)(1)), plus a
  `listings` row for the line it ends (unless a `listing` input already gives
  that line), listed from the earliest `listing` input the history gives or,
  with none, a year before its first dated fact. So concurrent lines of one
  history share their `valid_from`.
- `listing` -> a `listings` row from the event date.
- `8-k12b` -> a `listings` row for the successor from the event date and a
  `successor_chains` row from `predecessor_cik`.
- Every bar span -> a bar for each session of it (an open span runs to `T`),
  on the security id equal to the CIK.

Rows are stamped with the event's date (a bar with its session close, a
Form 25's baseline listing with the baseline date), none after `T`.
Runs with no network and no `.env`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.alpaca_prices import BAR_SOURCES
from tradepartner.calendar import all_sessions, last_completed_session, session_close
from tradepartner.config import Settings
from tradepartner.repair import store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.store.identity_answer_key import (
    History,
    InputEvent,
    Observed,
    Span,
    load_answer_key,
    match,
)
from tradepartner.store.schema import init_schema

#: The clock every history is read at, and the key's check date.
T = datetime(2026, 11, 10, 12, tzinfo=UTC)
CHECK = T.date()
#: Form 25 takes effect ten days after filing (Rule 12d2-2(d)(1)).
_EFFECTIVE_DAYS = 10
_CLASS = "Common Stock"

#: History id -> the issue that fixes the resolver for it. Empty means every
#: history passes. Strict: a history that starts passing fails until removed.
XFAIL: dict[str, str] = {
    "apa-apache-holdco-2021": "#3422, #3423",
    "batra-batrk-split-off-2023": "#3423",
    "llyva-llyvk-split-off-2025": "#3423",
    "ua-uaa-ticker-swap-2016": "#3421",
    "alphabet-google-holdco-2015": "#3424",
    "swx-holdco-2017": "#3424",
    "chk-chapter-11-new-equity-2021": "#3425",
    "htz-chapter-11-otc-then-nasdaq-2021": "#3425",
}

HISTORIES = load_answer_key(CHECK)
CHECK_SESSION = last_completed_session(T)


def _exchange(name: str) -> str:
    """The key's exchange name in the store's form ("NYSE Chicago" -> NYSE_CHICAGO)."""
    return name.strip().upper().replace(" ", "_")


def _stamp(day: date, hour: int = 20) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _listing(
    sid: str, ticker: str, exchange: str, valid_from: date, known: datetime
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": _exchange(exchange),
        "class_title": _CLASS,
        "valid_from": valid_from,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
        "retracted": False,
    }


def _bar(sid: str, session: date) -> dict[str, Any]:
    return {
        "security_id": sid,
        "session": session,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "volume": 1000,
        "known_at": session_close(session),
        "ingested_at": session_close(session),
        "source": sorted(BAR_SOURCES)[0],
        "provenance": "bar",
    }


def _baseline(history: History) -> date:
    """When a Form 25's line is taken to have been listed: with the lines the
    history's `listing` inputs give before its first removal, the earliest of
    them (concurrent rows share a `valid_from`), else a year before its first
    dated fact."""
    removals = [i.date for i in history.inputs if i.kind in ("form25", "25-nse")]
    first_removal = min(removals) if removals else None
    listed = [
        i.date
        for i in history.inputs
        if i.kind == "listing" and first_removal is not None and i.date < first_removal
    ]
    return min(listed) if listed else _first_fact(history) - timedelta(days=365)


def _first_fact(history: History) -> date:
    """The earliest date the history's inputs and bars give (never an expectation)."""
    return min([i.date for i in history.inputs] + [b.first for b in history.bars])


def build_store(conn: duckdb.DuckDBPyConnection, history: History) -> None:
    """Write the fixture store for `history` from its inputs and bar spans."""
    init_schema(conn)
    since = _baseline(history)
    listed: set[tuple[str, str, str]] = set()

    def line(event: InputEvent, valid_from: date, known: datetime) -> None:
        key = (event.cik, event.ticker, _exchange(event.exchange))
        if key not in listed:
            listed.add(key)
            insert_row(
                conn,
                "listings",
                _listing(event.cik, event.ticker, event.exchange, valid_from, known),
            )

    for event in history.inputs:
        known = _stamp(event.date)
        if event.kind in ("form25", "25-nse"):
            line(event, since, _stamp(since))
            filed = datetime(event.date.year, event.date.month, event.date.day, 16, tzinfo=UTC)
            insert_row(
                conn,
                "delistings",
                {
                    "security_id": event.cik,
                    "form": "25-NSE" if event.kind == "25-nse" else "25",
                    "class_title": _CLASS,
                    "exchange": _exchange(event.exchange),
                    "filed_at": filed,
                    "effective_on": event.effective or event.date + timedelta(days=_EFFECTIVE_DAYS),
                    "known_at": filed,
                    "ingested_at": filed,
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        else:
            line(event, event.date, known)
            if event.kind == "8-k12b" and event.predecessor_cik is not None:
                insert_row(
                    conn,
                    "successor_chains",
                    {
                        "predecessor_id": event.predecessor_cik,
                        "successor_id": event.cik,
                        "evidence_accession": event.citation.ref,
                        "evidence_form": "8-K12B",
                        "retracted": False,
                        "withdrawn_known_at": None,
                        "known_at": known,
                        "ingested_at": known,
                        "source": "edgar",
                        "provenance": "filing",
                    },
                )
    sessions = all_sessions()
    for span in history.bars:
        last = span.last or CHECK_SESSION
        for session in sessions:
            if span.first <= session <= last:
                insert_row(conn, "prices_daily", _bar(span.cik, session))


def _runs(days: list[date]) -> list[tuple[date, date]]:
    """Consecutive calendar days joined into `(first, last)` runs."""
    runs: list[tuple[date, date]] = []
    for day in days:
        if runs and day - runs[-1][1] == timedelta(days=1):
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def observe(conn: duckdb.DuckDBPyConnection, settings: Settings, history: History) -> Observed:
    """What the store's resolver and line ends say about `history`, in outside
    identifiers: who holds each ticker on each day, which stored bars the
    resolver keeps for the CIK, and where each line ends."""
    resolver = store_resolver(conn, T, settings)
    pairs = sorted(
        {(i.cik, i.ticker) for i in history.inputs} | {(b.cik, b.ticker) for b in history.bars}
    )
    start = _first_fact(history) - timedelta(days=30)
    days = [start + timedelta(days=n) for n in range((CHECK - start).days + 1)]
    holds: list[Span] = []
    for cik, ticker in pairs:
        held = [d for d in days if resolver.resolve(ticker, d) == cik]
        for first, last in _runs(held):
            holds.append(Span(cik, ticker, first, None if last == CHECK else last))
    stored = conn.execute("SELECT security_id, session FROM prices_daily ORDER BY 1, 2").fetchall()
    bars: list[Span] = []
    sessions = all_sessions()
    index = {s: n for n, s in enumerate(sessions)}
    for cik, ticker in pairs:
        kept = {s for sid, s in stored if sid == cik and resolver.resolve(ticker, s) == cik}
        ordered = sorted(kept)
        run: list[date] = []
        for s in ordered:
            if run and index[s] != index[run[-1]] + 1:
                bars.append(Span(cik, ticker, run[0], run[-1]))
                run = []
            run.append(s)
        if run:
            last_session = run[-1]
            bars.append(
                Span(cik, ticker, run[0], None if last_session >= CHECK_SESSION else last_session)
            )
    ends = listing_ends_as_of(conn, T, settings)
    line_ends: dict[tuple[str, str], date | None] = {}
    names = {_exchange(i.exchange): i.exchange for i in history.inputs}
    for row in ends.sort("valid_from").iter_rows(named=True):
        key = (row["security_id"], names.get(row["exchange"], row["exchange"]))
        line_ends[key] = row["end_session"]
    return Observed(holds=tuple(holds), bars=tuple(bars), line_ends=line_ends)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(tmp_path / "key.duckdb")})


def _params() -> list[Any]:
    out = []
    for history in HISTORIES:
        marks = []
        if history.id in XFAIL:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    raises=AssertionError,
                    reason=f"{XFAIL[history.id]}: the resolver does not yet meet the key",
                )
            )
        out.append(pytest.param(history, id=history.id, marks=marks))
    return out


@pytest.mark.parametrize("history", _params())
def test_the_resolver_agrees_with_the_answer_key(history: History, settings: Settings) -> None:
    with open_for_write(settings) as conn:
        build_store(conn, history)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        observed = observe(conn, settings, history)
    misses = match(history, observed)
    assert not misses, "\n".join(str(m) for m in misses)


def test_the_xfail_list_names_only_histories_in_the_key() -> None:
    assert set(XFAIL) <= {h.id for h in HISTORIES}
    assert all(issue.startswith("#") for issue in XFAIL.values())
    assert "mar-nyse-chicago-2020" not in XFAIL


# -- positive controls: the harness itself, on histories built here --------------


def _control(*, form25: bool) -> History:
    """A one-line security: listed on Nasdaq, bars from the session after, and
    (with `form25`) a Form 25 on 2024-03-04 effective 2024-03-14."""
    from tradepartner.store.identity_answer_key import BarSpan, Citation, Expected

    cite = Citation("accession", "0000000001-24-000001", date(2024, 3, 4))
    cik, listed = "0000000123", date(2023, 1, 3)
    inputs = [InputEvent("listing", cik, listed, "Nasdaq", "ZZZ", None, cite)]
    last = date(2024, 3, 13) if form25 else None
    if form25:
        inputs.append(InputEvent("form25", cik, date(2024, 3, 4), "Nasdaq", "ZZZ", None, cite))
    return History(
        id="control",
        title="control",
        inputs=tuple(inputs),
        bars=(BarSpan(cik, "ZZZ", date(2023, 1, 4), last, cite),),
        expected=(
            Expected("line_end", cik, None, last, None, "Nasdaq", cite),
            Expected("bars", cik, date(2023, 1, 4), last, "ZZZ", None, cite),
        ),
    )


@pytest.mark.parametrize("form25", [False, True], ids=["still listed", "delisted"])
def test_the_harness_passes_a_plain_history_and_reads_a_wrong_answer(
    form25: bool, settings: Settings
) -> None:
    history = _control(form25=form25)
    with open_for_write(settings) as conn:
        build_store(conn, history)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        observed = observe(conn, settings, history)
    assert match(history, observed) == []
    wrong = Observed(
        holds=observed.holds,
        bars=observed.bars,
        line_ends={k: date(2023, 6, 1) for k in observed.line_ends},
    )
    assert any("line_end" in m.why for m in match(history, wrong))
