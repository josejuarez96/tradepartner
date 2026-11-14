"""A Form 25 that ends one exchange's line while another listing of the same
security stays live is a line withdrawal, not a delisting (#3269, T388).

Fixtures (`tests/fixtures/form25_withdrawals`) are the rows of the 11-09
sitting copy for Apache (0000006769, NYSE and NYSE Chicago Form 25s filed
2020-06-08 for 2020-06-18, the Nasdaq line to its own 25-NSE effective
2021-03-14) and Marriott (0001048286, NYSE Chicago Form 25 filed 2020-09-10
for 2020-09-20, Nasdaq to today), with the bars the sitting held around those
days. Step 9's EDGAR write added the 2020 Form 25s; read as delistings they
made `registrant_evidence` cut both spans and `repair-resolution` delete bars
no id receives (#3264).
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.alpaca_prices import registrant_evidence
from tradepartner.backtest import engine
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.execution import planning
from tradepartner.health import _current_from
from tradepartner.ingest import _price_names
from tradepartner.repair import misattributed, store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.delistings import (
    DELISTED,
    LISTED,
    TRANSFERRED,
    derive_listing_ends,
    listing_ends_as_of,
)
from tradepartner.store.schema import init_schema
from tradepartner.universe import _current_listings, _is_live

FIXTURES = Path(__file__).parent.parent / "fixtures" / "form25_withdrawals"
T = datetime(2026, 11, 10, 12, tzinfo=UTC)
APA, MAR = "0000006769", "0001048286"


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("+0000", "+00:00"))


def _csv(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date, close: float = 1.0) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": session_close(session),
            "ingested_at": session_close(session),
            "source": "alpaca_sip",
            "provenance": "bar",
        },
    )


def _load(conn: duckdb.DuckDBPyConnection) -> None:
    init_schema(conn)
    for r in _csv("listings.csv"):
        insert_row(
            conn,
            "listings",
            {
                **{k: r[k] for k in ("security_id", "ticker", "exchange", "source", "provenance")},
                "class_title": r["class_title"] or None,
                "valid_from": date.fromisoformat(r["valid_from"]),
                "known_at": _utc(r["known_at"]),
                "ingested_at": _utc(r["ingested_at"]),
                "retracted": r["retracted"] == "True",
            },
        )
    for r in _csv("delistings.csv"):
        insert_row(
            conn,
            "delistings",
            {
                **{k: r[k] for k in ("security_id", "form", "class_title", "exchange")},
                **{k: r[k] for k in ("source", "provenance")},
                "filed_at": _utc(r["filed_at"]),
                "effective_on": date.fromisoformat(r["effective_on"]),
                "known_at": _utc(r["known_at"]),
                "ingested_at": _utc(r["ingested_at"]),
            },
        )
    for r in _csv("bars.csv"):
        _bar(conn, r["security_id"], date.fromisoformat(r["session"]), float(r["close"]))


@pytest.fixture
def store_settings(tmp_path: Path) -> Settings:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "w.duckdb")})
    with open_for_write(settings) as conn:
        _load(conn)
    return settings


@pytest.fixture
def conn(store_settings: Settings) -> Any:
    with duckdb.connect(store_settings.store.path, read_only=True) as handle:
        yield handle


def _row(ends: pl.DataFrame, sid: str, exchange: str, ticker: str | None = None) -> dict[str, Any]:
    wanted = (pl.col("security_id") == sid) & (pl.col("exchange") == exchange)
    if ticker is not None:
        wanted &= pl.col("ticker") == ticker
    rows = ends.filter(wanted & (pl.col("valid_from") >= date(2019, 1, 1))).to_dicts()
    [row] = [r for r in rows if r["class_title"] is not None]
    return row


# -- the sitting's two securities -------------------------------------------------


def test_marriotts_nyse_chicago_line_is_withdrawn_while_nasdaq_stays_listed(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    ends = listing_ends_as_of(conn, T, store_settings)
    # A later NASDAQ row (2020-11-06, retracted in 2026) is no "next listing"
    # for a line that ran beside Nasdaq: the end is the same at T 2020-12-02.
    during = listing_ends_as_of(conn, datetime(2020, 12, 2, 12, tzinfo=UTC), store_settings)
    assert _row(during, MAR, "NYSE_CHICAGO")["end_session"] == date(2020, 9, 18)
    chicago = _row(ends, MAR, "NYSE_CHICAGO")
    # Effective Sunday 2020-09-20: the line's last session is Friday 09-18.
    assert (chicago["status"], chicago["end_session"]) == (TRANSFERRED, date(2020, 9, 18))
    assert chicago["delisting_form"] == "25"
    assert _row(ends, MAR, "NASDAQ")["status"] == LISTED


def test_apaches_nyse_lines_are_withdrawn_and_its_span_ends_at_the_25_nse(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    ends = listing_ends_as_of(conn, T, store_settings)
    for exchange in ("NYSE", "NYSE_CHICAGO"):
        line = _row(ends, APA, exchange)
        assert (line["status"], line["end_session"]) == (TRANSFERRED, date(2020, 6, 17))
    nasdaq = _row(ends, APA, "NASDAQ")
    assert (nasdaq["status"], nasdaq["end_session"]) == (DELISTED, date(2021, 3, 12))
    assert nasdaq["delisting_form"] == "25-NSE"


def test_registrant_evidence_has_no_delisted_listing_for_a_withdrawn_line(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    ends = listing_ends_as_of(conn, T, store_settings)
    evidence = registrant_evidence(
        [],
        ends.iter_rows(named=True),
        as_of=T.date(),
        quiet_after_days=90,
        transfer_window_sessions=store_settings.master.transfer_window_sessions,
    )
    assert MAR not in evidence.delisted_listings
    # APA: only the Nasdaq line's 25-NSE (effective 2021-03-14) is left.
    assert evidence.delisted_listings[APA] == ((date(2019, 8, 1), "APA", date(2021, 3, 14)),)


def test_repair_resolution_no_longer_deletes_the_withdrawn_lines_bars(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    from tradepartner.adapters.alpaca_prices import BAR_SOURCES
    from tradepartner.repair import _Counted

    resolver = store_resolver(conn, T, store_settings)
    bars = _Counted(conn, "prices_daily", "session", sorted(BAR_SOURCES))
    found = misattributed(resolver, bars, ()).bars
    assert [k for k in found if k[0] == MAR] == []
    assert [k for k in found if k[0] == APA and k[1] <= date(2021, 3, 12)] == []


def test_apa_and_mar_stay_in_the_universe_after_the_withdrawal(
    conn: duckdb.DuckDBPyConnection, store_settings: Settings
) -> None:
    # A read just after both withdrawals, when APA's Nasdaq line (its 25-NSE
    # comes in March 2021) is still the live one that ties the withdrawn NYSE
    # row on `valid_from`.
    at = datetime(2020, 12, 2, 12, tzinfo=UTC)
    session = date(2020, 12, 1)
    current = _current_listings(conn, at, session, store_settings)
    for sid in (APA, MAR):
        assert _is_live(current[sid], session), sid
        assert current[sid]["on_listed_exchange"], sid
    # `ingest.py`'s live count and `health._current_from` take the first row
    # of the latest `valid_from` in frame order; here that is the live one.
    first: dict[str, dict[str, Any]] = {}
    for row in listing_ends_as_of(conn, at, store_settings).iter_rows(named=True):
        held = first.get(row["security_id"])
        if row["valid_from"] <= session and (
            held is None or row["valid_from"] > held["valid_from"]
        ):
            first[row["security_id"]] = row
    for sid in (APA, MAR):
        assert first[sid]["status"] == LISTED, sid


# -- synthetic stores: the rule's edges -------------------------------------------


def _listing(
    sid: str, ticker: str, exchange: str, valid_from: date, title: str | None = "Common Stock"
) -> dict[str, Any]:
    known = session_close(valid_from)
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": exchange,
        "class_title": title,
        "valid_from": valid_from,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
    }


def _f25(
    sid: str, exchange: str, filed: datetime, effective_on: date, title: str = "Common Stock"
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "form": "25",
        "class_title": title,
        "exchange": exchange,
        "filed_at": filed,
        "effective_on": effective_on,
        "known_at": filed,
        "ingested_at": filed,
        "source": "edgar",
        "provenance": "filing",
    }


FILED1 = datetime(2018, 3, 1, 21, tzinfo=UTC)
FILED2 = datetime(2018, 3, 2, 21, tzinfo=UTC)
FILED3 = datetime(2018, 3, 5, 21, tzinfo=UTC)
D0 = date(2018, 1, 2)
#: Effective days of the staggered cases: Mon 03-12, Wed 03-14.
E1, E2 = date(2018, 3, 12), date(2018, 3, 14)
SEC = "SEC_W"


def _make(
    tmp_path: Path,
    listings: list[dict[str, Any]],
    delistings: list[dict[str, Any]],
    bars: list[date],
    name: str = "s.duckdb",
) -> Settings:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / name)})
    with open_for_write(settings) as writer:
        init_schema(writer)
        for row in listings:
            insert_row(writer, "listings", row)
        for row in delistings:
            insert_row(writer, "delistings", row)
        for session in bars:
            _bar(writer, SEC, session)
    return settings


def _read(settings: Settings, at: datetime = T) -> pl.DataFrame:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return listing_ends_as_of(conn, at, settings)


def _status(ends: pl.DataFrame, exchange: str) -> tuple[str, date | None]:
    [row] = ends.filter(pl.col("exchange") == exchange).to_dicts()
    return row["status"], row["end_session"]


#: Bars across the effective days: Thu 03-08 .. Fri 03-16.
BARS = [date(2018, 3, d) for d in (8, 9, 12, 13, 14, 15, 16)]


def _dual() -> list[dict[str, Any]]:
    return [_listing(SEC, "AAA", "NYSE", D0), _listing(SEC, "AAA", "NASDAQ", D0)]


def test_same_day_nasdaq_withdrawal_keeps_nyse_current_at_each_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    filed = datetime(2020, 3, 2, 21, tzinfo=UTC)
    session = date(2020, 3, 13)
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NASDAQ", filed, date(2020, 3, 10))],
        [date(2020, 3, 9), date(2020, 3, 10), session],
    )
    at = session_close(session)
    with open_for_write(settings) as writer:
        insert_row(
            writer,
            "classifications",
            {
                "security_id": SEC,
                "security_type": "common",
                "rule": "common_default",
                "known_at": session_close(D0),
                "ingested_at": session_close(D0),
                "source": "edgar",
                "provenance": "filing",
            },
        )
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        before = listing_ends_as_of(conn, filed - timedelta(minutes=1), settings)
        assert _status(before, "NASDAQ") == (LISTED, None)
        assert engine._ended(before, date(2020, 3, 2)) == {}
        frame = listing_ends_as_of(conn, at, settings)
        assert _status(frame, "NASDAQ") == (TRANSFERRED, date(2020, 3, 9))
        assert _status(frame, "NYSE") == (LISTED, None)
        for ordered in (frame, frame.reverse()):
            monkeypatch.setattr(
                "tradepartner.universe.listing_ends_as_of",
                lambda _conn, _at, _settings, ordered=ordered: ordered,
            )
            monkeypatch.setattr(
                "tradepartner.ingest.listing_ends_as_of",
                lambda _conn, _at, _settings, ordered=ordered: ordered,
            )
            assert _current_listings(conn, at, session, settings)[SEC]["exchange"] == "NYSE"
            assert _current_from(ordered, session)[SEC]["exchange"] == "NYSE"
            assert planning.current_listings(ordered, session)[SEC]["exchange"] == "NYSE"
            assert engine._ended(ordered, session) == {}
            fetch, listed, *_ = _price_names(conn, at, session, settings)
            assert SEC in fetch
            assert SEC in listed


def test_same_day_withdrawal_then_real_delisting_exits_at_later_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nyse_end = date(2022, 6, 14)
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NASDAQ", datetime(2020, 3, 2, 21, tzinfo=UTC), date(2020, 3, 10))],
        [date(2020, 3, 9), date(2020, 3, 10), date(2020, 3, 13)],
    )
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        frame = listing_ends_as_of(conn, session_close(date(2020, 3, 13)), settings)
        rows = frame.to_dicts()
        for row in rows:
            if row["exchange"] == "NYSE":
                row["status"] = DELISTED
                row["end_session"] = nyse_end
        frame = pl.DataFrame(rows, schema=frame.schema)
        assert _status(frame, "NASDAQ") == (TRANSFERRED, date(2020, 3, 9))
        assert _status(frame, "NYSE") == (DELISTED, nyse_end)
        assert engine._ended(frame, nyse_end) == {SEC: nyse_end}
        monkeypatch.setattr(
            planning, "listing_ends_as_of", lambda _conn, _at, _settings, _ids: frame
        )
        assert planning._ended(conn, date(2022, 6, 16), [SEC], settings) == {SEC: nyse_end}


@pytest.mark.parametrize("filed_exchange", ["NASDAQ", "NYSE"])
def test_real_delisting_beats_stale_listed_sibling_in_exit_readers(
    tmp_path: Path, filed_exchange: str
) -> None:
    filed = datetime(2020, 3, 2, 21, tzinfo=UTC)
    ended = date(2020, 3, 9)
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, filed_exchange, filed, date(2020, 3, 10))],
        [ended],
    )
    session = date(2020, 3, 13)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        frame = listing_ends_as_of(conn, session_close(session), settings)
        assert _status(frame, filed_exchange) == (DELISTED, ended)
        assert engine._ended(frame, session) == {SEC: ended}
        assert planning._ended(conn, date(2020, 3, 16), [SEC], settings) == {SEC: ended}


def test_a_single_exchange_delisting_still_reads_delisted(tmp_path: Path) -> None:
    settings = _make(
        tmp_path, [_listing(SEC, "AAA", "NYSE", D0)], [_f25(SEC, "NYSE", FILED1, E1)], BARS
    )
    # Bars follow the effective day, but no other row exists to move to.
    assert _status(_read(settings), "NYSE") == (DELISTED, date(2018, 3, 16))


def test_a_form_25_on_one_of_two_lines_with_a_bar_after_it_is_a_withdrawal(
    tmp_path: Path,
) -> None:
    settings = _make(tmp_path, _dual(), [_f25(SEC, "NYSE", FILED1, E1)], BARS)
    ends = _read(settings)
    assert _status(ends, "NYSE") == (TRANSFERRED, date(2018, 3, 9))
    assert _status(ends, "NASDAQ") == (LISTED, None)


def test_a_stale_sibling_with_no_bar_after_the_effective_day_reads_delisted(
    tmp_path: Path,
) -> None:
    # The sibling row has no Form 25 of its own, but the security stopped
    # trading on the primary's last day: nothing shows the sibling carried on.
    settings = _make(
        tmp_path, _dual(), [_f25(SEC, "NYSE", FILED1, E1)], [date(2018, 3, 8), date(2018, 3, 9)]
    )
    assert _status(_read(settings), "NYSE") == (DELISTED, date(2018, 3, 9))


def test_a_bar_far_after_the_effective_day_is_no_withdrawal(tmp_path: Path) -> None:
    # A lone bar months later is a reused ticker's, not the sibling line's.
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NYSE", FILED1, E1)],
        [date(2018, 3, 8), date(2018, 3, 9), date(2018, 9, 4)],
    )
    assert _status(_read(settings), "NYSE") == (DELISTED, date(2018, 9, 4))


@pytest.mark.parametrize("swap", [False, True])
def test_two_lines_ended_the_same_day_with_no_third_row_both_read_delisted(
    tmp_path: Path, swap: bool
) -> None:
    first, second = (FILED2, FILED1) if swap else (FILED1, FILED2)
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NYSE", first, E1), _f25(SEC, "NASDAQ", second, E1)],
        BARS,
        f"same{swap}.duckdb",
    )
    ends = _read(settings)
    assert _status(ends, "NYSE")[0] == DELISTED
    assert _status(ends, "NASDAQ")[0] == DELISTED


@pytest.mark.parametrize("swap", [False, True])
def test_two_lines_ended_with_one_live_third_row_are_both_withdrawals(
    tmp_path: Path, swap: bool
) -> None:
    first, second = (FILED2, FILED1) if swap else (FILED1, FILED2)
    settings = _make(
        tmp_path,
        [*_dual(), _listing(SEC, "AAA", "NYSE_AMERICAN", D0)],
        [_f25(SEC, "NYSE", first, E1), _f25(SEC, "NASDAQ", second, E1)],
        BARS,
        f"third{swap}.duckdb",
    )
    ends = _read(settings)
    assert _status(ends, "NYSE") == (TRANSFERRED, date(2018, 3, 9))
    assert _status(ends, "NASDAQ") == (TRANSFERRED, date(2018, 3, 9))
    assert _status(ends, "NYSE_AMERICAN") == (LISTED, None)


@pytest.mark.parametrize("swap", [False, True])
def test_a_staggered_pair_withdraws_the_first_line_and_delists_the_second(
    tmp_path: Path, swap: bool
) -> None:
    first, second = (FILED3, FILED1) if swap else (FILED1, FILED3)
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NYSE", first, E1), _f25(SEC, "NASDAQ", second, E2)],
        BARS,
        f"stag{swap}.duckdb",
    )
    ends = _read(settings)
    assert _status(ends, "NYSE") == (TRANSFERRED, date(2018, 3, 9))
    assert _status(ends, "NASDAQ") == (DELISTED, date(2018, 3, 16))


def test_a_staggered_pair_with_no_bar_after_the_first_day_delists_the_first(
    tmp_path: Path,
) -> None:
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NYSE", FILED1, E1), _f25(SEC, "NASDAQ", FILED3, E2)],
        [date(2018, 3, 8), date(2018, 3, 9), date(2018, 3, 12)],
    )
    # No session after 03-12 has a bar (03-12 itself is the effective day).
    ends = _read(settings)
    assert _status(ends, "NYSE")[0] == DELISTED
    assert _status(ends, "NASDAQ")[0] == DELISTED


def test_an_otc_sibling_row_is_no_withdrawal(tmp_path: Path) -> None:
    # #3252: OTC bars after a Form 25 are a security that left the exchange.
    settings = _make(
        tmp_path,
        [_listing(SEC, "AAA", "NYSE", D0), _listing(SEC, "AAAQ", "OTC", D0)],
        [_f25(SEC, "NYSE", FILED1, E1)],
        BARS,
    )
    assert _status(_read(settings), "NYSE")[0] == DELISTED


def test_a_sibling_of_another_class_is_no_withdrawal(tmp_path: Path) -> None:
    settings = _make(
        tmp_path,
        [
            _listing(SEC, "AAA", "NYSE", D0, "Class A Common Stock"),
            _listing(SEC, "AAA", "NASDAQ", D0, "Class B Common Stock"),
        ],
        [_f25(SEC, "NYSE", FILED1, E1, "Class A Common Stock")],
        BARS,
    )
    assert _status(_read(settings), "NYSE")[0] == DELISTED


def test_an_untitled_sibling_row_counts(tmp_path: Path) -> None:
    settings = _make(
        tmp_path,
        [_listing(SEC, "AAA", "NYSE", D0), _listing(SEC, "AAA", "NASDAQ", D0, None)],
        [_f25(SEC, "NYSE", FILED1, E1)],
        BARS,
    )
    assert _status(_read(settings), "NYSE")[0] == TRANSFERRED


def test_a_sibling_that_starts_after_the_effective_day_is_not_live_then(tmp_path: Path) -> None:
    # A row that starts the day after the filing's effective day is a
    # successor's, not a concurrent line: the existing transfer rules read it.
    settings = _make(
        tmp_path,
        [_listing(SEC, "AAA", "NYSE", D0), _listing(SEC, "AAA", "NASDAQ", date(2019, 6, 3))],
        [_f25(SEC, "NYSE", FILED1, E1)],
        BARS,
    )
    assert _status(_read(settings), "NYSE")[0] == DELISTED


def test_the_withdrawal_reads_only_what_is_known_at_t(tmp_path: Path) -> None:
    """No look-ahead. The sibling row is first known well after the filing: a
    read before it sees a lone line (delisted); the same store read after sees
    a withdrawal. And the reading at T equals the reading on a store holding
    only what is known by T."""
    known_late = datetime(2018, 6, 1, 21, tzinfo=UTC)
    sibling = {
        **_listing(SEC, "AAA", "NASDAQ", D0),
        "known_at": known_late,
        "ingested_at": known_late,
    }
    listings = [_listing(SEC, "AAA", "NYSE", D0), sibling]
    settings = _make(tmp_path, listings, [_f25(SEC, "NYSE", FILED1, E1)], BARS)
    before = _read(settings, datetime(2018, 5, 31, 21, tzinfo=UTC))
    after = _read(settings, known_late)
    assert _status(before, "NYSE") == (DELISTED, date(2018, 3, 16))
    assert _status(after, "NYSE") == (TRANSFERRED, date(2018, 3, 9))
    truncated = _make(
        tmp_path, [listings[0]], [_f25(SEC, "NYSE", FILED1, E1)], BARS, "trunc.duckdb"
    )
    assert _status(before, "NYSE") == _status(_read(truncated, known_late), "NYSE")


def test_a_form_25_known_after_t_does_not_end_the_sibling(tmp_path: Path) -> None:
    # The sibling's own Form 25 is filed after the bars: at a T before it is
    # known the sibling is live and the first line is withdrawn; once it is
    # known (same effective day) neither line is live and both are delisted.
    filed_late = datetime(2018, 3, 20, 21, tzinfo=UTC)
    settings = _make(
        tmp_path,
        _dual(),
        [_f25(SEC, "NYSE", FILED1, E1), _f25(SEC, "NASDAQ", filed_late, E1)],
        BARS,
    )
    early = _read(settings, datetime(2018, 3, 16, 21, tzinfo=UTC))
    late = _read(settings, filed_late)
    assert _status(early, "NYSE") == (TRANSFERRED, date(2018, 3, 9))
    assert _status(early, "NASDAQ") == (LISTED, None)
    assert _status(late, "NYSE")[0] == DELISTED
    assert _status(late, "NASDAQ")[0] == DELISTED


def test_derive_with_no_bars_reads_a_form_25_as_a_delisting() -> None:
    listings = pl.DataFrame(_dual())
    delistings = pl.DataFrame([_f25(SEC, "NYSE", FILED1, E1)])
    none = pl.DataFrame([], schema={"security_id": pl.Utf8(), "session": pl.Date()})
    ends = derive_listing_ends(listings, delistings, none, transfer_window_sessions=5)
    assert _status(ends, "NYSE")[0] == DELISTED


def test_an_older_row_left_behind_by_a_move_is_no_live_sibling(tmp_path: Path) -> None:
    # NASDAQ 2010, replaced by NYSE in 2015 with no Form 25 for the old line;
    # a real NYSE Form 25 and two tail bars: still a delisting.
    settings = _make(
        tmp_path,
        [_listing(SEC, "AAA", "NASDAQ", date(2010, 1, 4)), _listing(SEC, "AAA", "NYSE", D0)],
        [_f25(SEC, "NYSE", FILED1, E1)],
        BARS,
    )
    assert _status(_read(settings), "NYSE")[0] == DELISTED
