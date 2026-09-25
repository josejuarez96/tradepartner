"""Deterministic generator for `tests/fixtures/universe/*.csv` (T5).

Writes one CSV per store table (`tradepartner.store.schema.TABLE_NAMES`,
minus `ingestion_runs`/`schema_version`, which are never fixture data) with
headers read straight from the live schema (`_schema_columns`), so a header
can never drift from `schema.py`'s real column names. Every timestamp is
written as ISO-8601 with an explicit `+00:00` offset; every date as bare
`YYYY-MM-DD` (`tests/conftest.py`'s loader rejects anything else).

**Determinism.** A single `random.Random(SEED)` instance, advanced in a
fixed order by a fixed sequence of calls, is the only source of randomness;
nothing here reads the wall clock. Re-running `generate()` therefore always
produces byte-identical CSVs (`tests/test_fixture_universe.py` checks this
directly).

**Sessions.** Every date that denotes a trading day (`valid_from` on a
"normal" listing, a bar's `session`, a split's `ex_date`, ...) comes from
`tradepartner.calendar`, addressed by *offset* into `_all_sessions()`
(2018-01 through 2021-12), never hand-typed: the calendar decides which
calendar days are sessions, so this generator never encodes a holiday or a
half day itself and never needs "weekday" logic (CLAUDE.md).

**Design choices, not pinned by the spec text:**
- `source` follows the real Phase-2 adapters: `alpaca` for bars and
  corporate actions, `edgar` for filing-derived rows, `config` for the two
  benchmarks (spec req 3: "seeded from config, not derived from EDGAR"),
  `alpaca` for a `snapshot_static` listing (master column-sources table:
  pre-2019 ticker/exchange comes from the Alpaca assets snapshot).
- Every `securities` row still has `provenance='filing'`
  (`store/schema.py`'s module docstring: `securities.name` is always
  filing-sourced) *except* the two benchmarks, which are config-seeded and
  use `provenance='snapshot_static'` — flagged for `quant-auditor` review
  since T8 (the security master that will actually seed benchmarks) does
  not exist yet.
- A dual-class company's two classes are two distinct `security_id`s
  (`store/schema.py`'s design decision), so `facts.class_member` is
  redundant with `security_id` for disambiguation; it is still populated
  with a readable per-class token ("ClassA"/"ClassB") to exercise the
  column, per the plan's "per-class shares facts via `class_member`".
- Every `facts` row here has a non-empty `class_member` — "ClassA"/
  "ClassB" for the dual-class case, "common" elsewhere — never the
  schema's own default `''`. `tests/conftest.py`'s `load_universe_fixtures`
  reads every CSV cell through `read_csv(..., all_varchar=true)` with no
  `nullstr` override, and DuckDB maps *any* empty cell (quoted or not) to
  `NULL` under that default regardless of column type, which
  `facts.class_member NOT NULL DEFAULT ''` then rejects — confirmed by
  hand; there is no read_csv option available to the fixed call in
  conftest.py to opt out. Filed as a follow-up (conftest.py is a T4 file,
  outside T5's scope) rather than fixed here; see the PR description.
- Corporate-action `ratio_or_amount` for a split is the "for" side of an
  N-for-1 split (2.0 = 2-for-1); the raw close is divided by that ratio at
  the ex-date session, which is what "raw" storage means for a real split
  print (ADR 0003 rule 1) — no read-time adjustment happens here.
- Dividends do not move the raw close (only a read-time total-return series
  would, and that is T6, not this fixture).
"""

from __future__ import annotations

import csv
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb

from tradepartner import calendar as tp_calendar
from tradepartner.store import schema as store_schema
from tradepartner.store.db import configure_connection

SEED = 20180102
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "universe"
_INGEST_LAG = timedelta(hours=2)
_FACT_TABLES: tuple[str, ...] = tuple(
    t for t in store_schema.TABLE_NAMES if t not in ("ingestion_runs", "schema_version")
)


def _all_sessions() -> list[date]:
    """Every XNYS session from 2018-01-01 through 2021-12-31, walked one
    session at a time via `tp_calendar.next_session` (never date math or
    "weekday" logic of our own). `sessions_in_month_window` is a *rolling*
    one-month-back window from its `end_session`, not a calendar-month
    filter, so it is not what "one row per session, no repeats" needs
    here."""
    sessions: list[date] = []
    cursor = date(2017, 12, 31)
    end = date(2021, 12, 31)
    while True:
        cursor = tp_calendar.next_session(cursor)
        if cursor > end:
            break
        sessions.append(cursor)
    return sessions


def _schema_columns() -> dict[str, list[str]]:
    """`{table: [column names in DDL order]}`, read from a fresh in-memory
    store instead of hand-copied from `schema.py`, so a header can never
    silently drift from the real columns."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    store_schema.init_schema(conn)
    try:
        columns = {}
        for table in _FACT_TABLES:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? AND table_catalog = current_database() "
                "AND table_schema = current_schema() ORDER BY ordinal_position",
                [table],
            ).fetchall()
            columns[table] = [r[0] for r in rows]
        return columns
    finally:
        conn.close()


def _fmt(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"naive datetime in fixture row: {value!r}")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


class Universe:
    """Accumulates fixture rows, table by table, via small typed helpers.

    Every helper fills in `known_at`/`ingested_at`/`source`/`provenance`
    consistently with the spec's "Data / interfaces" master column-sources
    table, so call sites only ever name the business columns plus the one
    or two timing values that make each required case what it is.
    """

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.rows: dict[str, list[dict[str, object]]] = {t: [] for t in _FACT_TABLES}

    def security(
        self,
        security_id: str,
        cik: str,
        name: str,
        *,
        known_at: datetime,
        benchmark: bool = False,
        source: str = "edgar",
        provenance: str = "filing",
    ) -> None:
        self.rows["securities"].append(
            dict(
                security_id=security_id,
                cik=cik,
                name=name,
                benchmark=benchmark,
                known_at=known_at,
                ingested_at=known_at + _INGEST_LAG,
                source=source,
                provenance=provenance,
            )
        )

    def listing(
        self,
        security_id: str,
        ticker: str,
        exchange: str,
        valid_from: date,
        *,
        known_at: datetime,
        class_title: str | None = None,
        source: str = "edgar",
        provenance: str = "filing",
    ) -> None:
        self.rows["listings"].append(
            dict(
                security_id=security_id,
                ticker=ticker,
                exchange=exchange,
                class_title=class_title,
                valid_from=valid_from,
                known_at=known_at,
                ingested_at=known_at + _INGEST_LAG,
                source=source,
                provenance=provenance,
            )
        )

    def classification(
        self,
        security_id: str,
        security_type: str,
        rule: str,
        *,
        known_at: datetime,
        sic: int | None = None,
        source: str = "edgar",
        provenance: str = "filing",
    ) -> None:
        self.rows["classifications"].append(
            dict(
                security_id=security_id,
                sic=sic,
                security_type=security_type,
                rule=rule,
                known_at=known_at,
                ingested_at=known_at + _INGEST_LAG,
                source=source,
                provenance=provenance,
            )
        )

    def delisting(
        self,
        security_id: str,
        form: str,
        class_title: str,
        exchange: str,
        filed_at: datetime,
        *,
        source: str = "edgar",
    ) -> None:
        self.rows["delistings"].append(
            dict(
                security_id=security_id,
                form=form,
                class_title=class_title,
                exchange=exchange,
                filed_at=filed_at,
                effective_on=filed_at.date() + timedelta(days=10),
                known_at=filed_at,
                ingested_at=filed_at + _INGEST_LAG,
                source=source,
                provenance="filing",
            )
        )

    def bars(
        self,
        security_id: str,
        sessions: list[date],
        base_price: float,
        *,
        splits: dict[date, float] | None = None,
        source: str = "alpaca",
    ) -> None:
        splits = splits or {}
        level = base_price
        for session in sessions:
            if session in splits:
                level = level / splits[session]
            level = max(2.0, level * (1 + self.rng.uniform(-0.015, 0.015)))
            open_ = level * (1 + self.rng.uniform(-0.004, 0.004))
            high = max(open_, level) * (1 + self.rng.uniform(0.0, 0.006))
            low = min(open_, level) * (1 - self.rng.uniform(0.0, 0.006))
            volume = self.rng.randint(100_000, 3_000_000)
            known_at = tp_calendar.session_close(session)
            self.rows["prices_daily"].append(
                dict(
                    security_id=security_id,
                    session=session,
                    open=round(open_, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(level, 2),
                    volume=volume,
                    known_at=known_at,
                    ingested_at=known_at + _INGEST_LAG,
                    source=source,
                    provenance="bar",
                )
            )

    def action(
        self,
        security_id: str,
        action_type: str,
        ex_date: date,
        ratio_or_amount: float,
        *,
        known_at: datetime | None = None,
        source: str = "alpaca",
    ) -> None:
        # First-seen rule (spec req 5): announcement time if given, else the
        # close of the session before ex-date.
        resolved_known_at = known_at or tp_calendar.session_close(
            tp_calendar.previous_session(ex_date)
        )
        self.rows["corporate_actions"].append(
            dict(
                security_id=security_id,
                action_type=action_type,
                ex_date=ex_date,
                ratio_or_amount=ratio_or_amount,
                known_at=resolved_known_at,
                ingested_at=resolved_known_at + _INGEST_LAG,
                source=source,
                provenance="action",
            )
        )

    def fact(
        self,
        security_id: str,
        fact_name: str,
        as_of_date: date,
        value: float,
        *,
        known_at: datetime,
        class_member: str,
        filing_accession: str | None = None,
        source: str = "edgar",
    ) -> None:
        self.rows["facts"].append(
            dict(
                security_id=security_id,
                fact_name=fact_name,
                as_of_date=as_of_date,
                class_member=class_member,
                value=value,
                filing_accession=filing_accession,
                known_at=known_at,
                ingested_at=known_at + _INGEST_LAG,
                source=source,
                provenance="filing",
            )
        )

    def write(self, out_dir: Path, columns: dict[str, list[str]]) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        for table in _FACT_TABLES:
            path = out_dir / f"{table}.csv"
            header = columns[table]
            with path.open("w", newline="") as fh:
                writer = csv.writer(fh, lineterminator="\n")
                writer.writerow(header)
                for row in self.rows[table]:
                    writer.writerow([_fmt(row.get(c)) for c in header])


def _dt(day: date, hour: int = 18, minute: int = 30) -> datetime:
    """An acceptance/fetch instant on `day`, tz-aware UTC.

    Defaults to 18:30 UTC — mid-afternoon US Eastern in both DST states
    (13:30/14:30 ET) and, crucially, always *before* that session's close
    (20:00 UTC in summer, 21:00 UTC in winter). A default of "the close" or
    later would make `tp_calendar.last_completed_session(filed_at)` treat
    the filing's own session as already completed in summer but not in
    winter — the exact bare-date-cast trap `store/schema.py`'s module
    docstring warns `delistings.py` (T8b) away from, just reached through
    the *time* component instead of a date cast. A safely pre-close instant
    makes `last_completed_session(filed_at) == previous_session(filed_at.date())`
    always, regardless of season.
    """
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def generate(out_dir: Path = FIXTURES_DIR, *, seed: int = SEED) -> None:
    """Build the full fixture universe and write it to `out_dir`."""
    rng = random.Random(seed)
    u = Universe(rng)
    s = _all_sessions()
    columns = _schema_columns()

    # Every security below gets a *bounded* bar window around whatever
    # session offset(s) its own case actually needs, not the full 2018-2021
    # range: the offsets and dates are unchanged from a first draft that
    # gave everyone `s[0:]` (~1008 sessions each, ~2MB of CSV), which the
    # repo's `check-added-large-files` pre-commit hook (500 KB) rejects.
    # Windows are still generous (tens to low hundreds of sessions) and
    # still land every date this file's comments and the fixture README
    # name; only the *volume* of otherwise-unused filler bars was cut.

    # --- The universe-rule control group: continuous bars (valid_from ==
    # first bar, no dangling "listed but no data" period) covering *every*
    # session in the 12 calendar months before, and several month-ends
    # after, idx252 — so `universe_as_of` rules 1-8 (min_history_months=12,
    # max_shares_age_days=400 kept fresh by two facts) can actually admit
    # these names at more than one T. Two plain commons plus CTRL1, which
    # later delists cleanly (proves a name can qualify, then leave the
    # universe, exactly as T13/T15 need). ---------------------------------
    for sid, cik, name, ticker, exch, sic, price in (
        ("PLAIN1", "0001000001", "Plain Consolidated Inc", "PLN1", "NASDAQ", 7372, 40.0),
        ("PLAIN2", "0001000002", "Plain Industries Corp", "PLN2", "NYSE", 3674, 65.0),
    ):
        u.security(sid, cik, name, known_at=_dt(s[0]))
        u.listing(sid, ticker, exch, s[0], known_at=_dt(s[0]))
        u.classification(sid, "common", "sic_default", known_at=_dt(s[0]), sic=sic)
        u.bars(sid, s[0:330], price)
        for fact_idx, value in ((0, 6_000_000), (260, 6_100_000)):
            u.fact(
                sid,
                "shares_outstanding",
                s[fact_idx] - timedelta(days=3),
                value,
                known_at=_dt(s[fact_idx]),
                class_member="common",
                filing_accession=f"{cik}-19-00000{fact_idx // 100 + 1}",
            )

    u.security("CTRL1", "0001000021", "Control Group Delisted Co", known_at=_dt(s[0]))
    u.listing("CTRL1", "CTRL", "NASDAQ", s[0], known_at=_dt(s[0]))
    u.classification("CTRL1", "common", "sic_default", known_at=_dt(s[0]), sic=2836)
    u.bars("CTRL1", s[0:330], 24.0)
    u.fact(
        "CTRL1",
        "shares_outstanding",
        s[200] - timedelta(days=3),
        5_500_000,
        known_at=_dt(s[200]),
        class_member="common",
        filing_accession="0001000021-19-000001",
    )
    u.delisting("CTRL1", "25", "Common Stock", "NASDAQ", _dt(s[330]))

    # --- Delisting with truncated history (last bar > gap.missing_tail_
    # sessions before the last session before the Form 25 filing). Bars
    # are a short window ending exactly at the last-bar index the gap
    # arithmetic below depends on (idx149) — no case here needs a longer
    # history, so valid_from matches the window's own start. -------------
    u.security("TRUNC1", "0001000003", "Truncated Holdings Inc", known_at=_dt(s[120]))
    u.listing("TRUNC1", "TRNC", "NASDAQ", s[120], known_at=_dt(s[120]))
    u.classification("TRUNC1", "common", "sic_default", known_at=_dt(s[120]), sic=2836)
    u.bars("TRUNC1", s[120:150], 22.0)
    u.delisting("TRUNC1", "25", "Common Stock", "NASDAQ", _dt(s[179]))

    # --- Delisting within N (not missing). -----------------------------
    u.security("NEARN1", "0001000004", "Near Merger Co", known_at=_dt(s[120]))
    u.listing("NEARN1", "NEAR", "NYSE", s[120], known_at=_dt(s[120]))
    u.classification("NEARN1", "common", "sic_default", known_at=_dt(s[120]), sic=2836)
    u.bars("NEARN1", s[120:150], 18.0)
    u.delisting("NEARN1", "25", "Common Stock", "NYSE", _dt(s[153]))

    # --- Clean merger: last bar the session before the filing (not
    # missing, per spec req 13). ----------------------------------------
    u.security("MRGR1", "0001000005", "Merger Target Inc", known_at=_dt(s[120]))
    u.listing("MRGR1", "MRGR", "NYSE", s[120], known_at=_dt(s[120]))
    u.classification("MRGR1", "common", "sic_default", known_at=_dt(s[120]), sic=2836)
    u.bars("MRGR1", s[120:150], 51.0)
    u.delisting("MRGR1", "25", "Common Stock", "NYSE", _dt(s[150]))

    # --- Form 25-NSE. -----------------------------------------------------
    u.security("NSE1", "0001000006", "NSE Exit Corp", known_at=_dt(s[170]))
    u.listing("NSE1", "NSEX", "NYSE_AMERICAN", s[170], known_at=_dt(s[170]))
    u.classification("NSE1", "common", "sic_default", known_at=_dt(s[170]), sic=3663)
    u.bars("NSE1", s[170:200], 12.0)
    u.delisting("NSE1", "25-NSE", "Common Stock", "NYSE_AMERICAN", _dt(s[200]))

    # --- Form 25 on a non-common class; the common survives. ------------
    u.security("ZETA_COM", "0001000007", "Zeta Capital Inc", known_at=_dt(s[0]))
    u.listing("ZETA_COM", "ZETA", "NASDAQ", s[0], known_at=_dt(s[0]), class_title="Common Stock")
    u.classification("ZETA_COM", "common", "sic_default", known_at=_dt(s[0]), sic=6022)
    # Extends well past ZETA_PFD's idx-300 delisting (proves the common
    # survives) and covers idx226 (2018-11-23, the half day). No Form 25 of
    # its own — ZETA_COM simply has no *recorded* bars past idx340 in this
    # fixture (a documented data-coverage boundary, not a delisting); it is
    # not part of the rule-6/7 control group (PLAIN1/PLAIN2/CTRL1 above),
    # so it should not be picked as a `universe_as_of` probe at a late T.
    u.bars("ZETA_COM", s[0:340], 30.0)
    u.security("ZETA_PFD", "0001000007", "Zeta Capital Inc", known_at=_dt(s[0]))
    u.listing(
        "ZETA_PFD",
        "ZETAP",
        "NASDAQ",
        s[0],
        known_at=_dt(s[0]),
        class_title="6% Cumulative Preferred Stock",
    )
    u.classification("ZETA_PFD", "preferred", "title_suffix", known_at=_dt(s[0]), sic=6022)
    u.bars("ZETA_PFD", s[0:300], 25.0)
    u.delisting("ZETA_PFD", "25", "6% Cumulative Preferred Stock", "NASDAQ", _dt(s[300]))

    # --- Exchange transfer: Form 25 on the old listing, then a new listing
    # within master.transfer_window_sessions, known several sessions later
    # (a probe T between the two known_at values sees "delisted"; T7/T8b
    # will exercise the derivation). Bars are continuous under one
    # security_id across the move. ---------------------------------------
    u.security("XFER1", "0001000008", "Transfer Systems Inc", known_at=_dt(s[350]))
    # valid_from matches the first recorded bar: no case here needs earlier
    # history, so the listing does not claim data this fixture does not have.
    u.listing("XFER1", "XFR", "NYSE", s[350], known_at=_dt(s[350]))
    u.classification("XFER1", "common", "sic_default", known_at=_dt(s[350]), sic=3577)
    u.bars("XFER1", s[350:430], 44.0)
    u.delisting("XFER1", "25", "Common Stock", "NYSE", _dt(s[400]))
    u.listing("XFER1", "XFR", "NASDAQ", s[403], known_at=tp_calendar.session_close(s[408]))

    # --- Same-company ticker change: one security_id, two listings on the
    # same exchange. --------------------------------------------------------
    u.security("TIKR1", "0001000009", "Ticker Change Co", known_at=_dt(s[470]))
    u.listing("TIKR1", "OLDT", "NASDAQ", s[470], known_at=_dt(s[470]))
    u.classification("TIKR1", "common", "sic_default", known_at=_dt(s[470]), sic=5961)
    u.bars("TIKR1", s[470:540], 27.0)
    u.listing("TIKR1", "NEWT", "NASDAQ", s[500], known_at=_dt(s[500]))

    # --- Ticker reused by a different company: different security_id and
    # cik, non-overlapping listing windows. --------------------------------
    u.security("REUSE_OLD", "0001000010", "Reuse Old Corp", known_at=_dt(s[90]))
    u.listing("REUSE_OLD", "DUPL", "NASDAQ", s[90], known_at=_dt(s[90]))
    u.classification("REUSE_OLD", "common", "sic_default", known_at=_dt(s[90]), sic=2860)
    u.bars("REUSE_OLD", s[90:120], 9.0)
    u.delisting("REUSE_OLD", "25", "Common Stock", "NASDAQ", _dt(s[120]))
    u.security("REUSE_NEW", "0001000011", "Reuse New Inc", known_at=_dt(s[600]))
    u.listing("REUSE_NEW", "DUPL", "NASDAQ", s[600], known_at=_dt(s[600]))
    u.classification("REUSE_NEW", "common", "sic_default", known_at=_dt(s[600]), sic=7370)
    u.bars("REUSE_NEW", s[600:660], 15.0)

    # --- Dual-class company: two security_ids, one cik, per-class shares
    # facts via class_member. ------------------------------------------
    u.security("KAPPA_A", "0001000012", "Kappa Media Inc", known_at=_dt(s[0]))
    u.listing(
        "KAPPA_A", "KAPA", "NASDAQ", s[0], known_at=_dt(s[0]), class_title="Class A Common Stock"
    )
    u.classification("KAPPA_A", "common", "sic_default", known_at=_dt(s[0]), sic=7812)
    # Extends past the facts' idx-275 known_at (12 months of trailing bars
    # are present by then too), so a combined-class market cap is
    # computable at a T in [275, 299] — not just structurally present.
    u.bars("KAPPA_A", s[0:300], 80.0)
    u.security("KAPPA_B", "0001000012", "Kappa Media Inc", known_at=_dt(s[0]))
    u.listing(
        "KAPPA_B", "KAPB", "NASDAQ", s[0], known_at=_dt(s[0]), class_title="Class B Common Stock"
    )
    u.classification("KAPPA_B", "common", "sic_default", known_at=_dt(s[0]), sic=7812)
    u.bars("KAPPA_B", s[0:300], 76.0)
    u.fact(
        "KAPPA_A",
        "shares_outstanding",
        s[275] - timedelta(days=3),
        5_000_000,
        known_at=_dt(s[275]),
        class_member="ClassA",
        filing_accession="0001000012-19-000010",
    )
    u.fact(
        "KAPPA_B",
        "shares_outstanding",
        s[275] - timedelta(days=3),
        20_000_000,
        known_at=_dt(s[275]),
        class_member="ClassB",
        filing_accession="0001000012-19-000010",
    )

    # --- A split, visible as a raw-price step at the ex-date. -------------
    u.security("SPLIT1", "0001000013", "Split Simple Corp", known_at=_dt(s[0]))
    u.listing("SPLIT1", "SPLT", "NYSE", s[0], known_at=_dt(s[0]))
    u.classification("SPLIT1", "common", "sic_default", known_at=_dt(s[0]), sic=3826)
    u.bars("SPLIT1", s[0:340], 100.0, splits={s[300]: 2.0})
    u.action("SPLIT1", "split", s[300], 2.0)

    # --- A split between a shares filing and a later month-end T. --------
    u.security("SPLIT2", "0001000014", "Split Mid Corp", known_at=_dt(s[100]))
    u.listing("SPLIT2", "MID2", "NASDAQ", s[100], known_at=_dt(s[100]))
    u.classification("SPLIT2", "common", "sic_default", known_at=_dt(s[100]), sic=3841)
    # s[100:390] covers every session in the 12 calendar months before
    # idx374 (2019-06-28, the T `tests/test_fixture_universe.py` and the
    # README use for this case: idx374-252=idx122 is inside this window)
    # through past both the split (idx350) and T itself.
    u.bars("SPLIT2", s[100:390], 60.0, splits={s[350]: 1.5})
    u.fact(
        "SPLIT2",
        "shares_outstanding",
        s[320] - timedelta(days=3),
        8_000_000,
        known_at=_dt(s[320]),
        class_member="common",
        filing_accession="0001000014-19-000005",
    )
    u.action("SPLIT2", "split", s[350], 1.5)

    # --- A split known before T with ex-date after T (explicit early
    # announcement time, not the close-before-ex-date default). ----------
    u.security("SPLIT3", "0001000015", "Split Future Corp", known_at=_dt(s[300]))
    u.listing("SPLIT3", "FUT3", "NYSE", s[300], known_at=_dt(s[300]))
    u.classification("SPLIT3", "common", "sic_default", known_at=_dt(s[300]), sic=2911)
    # s[300:630] covers every session in the 12 calendar months before
    # idx564 (2020-03-31, T) — idx564-252=idx312 is inside this window —
    # through past the ex-date (idx600).
    u.bars("SPLIT3", s[300:630], 88.0, splits={s[600]: 4.0})
    u.fact(
        "SPLIT3",
        "shares_outstanding",
        s[500] - timedelta(days=3),
        9_000_000,
        known_at=_dt(s[500]),
        class_member="common",
        filing_accession="0001000015-20-000002",
    )
    u.action("SPLIT3", "split", s[600], 4.0, known_at=_dt(s[550], hour=12, minute=30))

    # --- A revised dividend: second row's known_at is its ingested_at. ---
    u.security("DIVR1", "0001000016", "Dividend Revision Inc", known_at=_dt(s[200]))
    u.listing("DIVR1", "DIVR", "NASDAQ", s[200], known_at=_dt(s[200]))
    u.classification("DIVR1", "common", "sic_default", known_at=_dt(s[200]), sic=6021)
    u.bars("DIVR1", s[200:280], 33.0)
    original_known_at = tp_calendar.session_close(s[249])
    u.action("DIVR1", "dividend", s[250], 0.20, known_at=original_known_at)
    revised_known_at = original_known_at + timedelta(days=45)
    u.rows["corporate_actions"].append(
        dict(
            security_id="DIVR1",
            action_type="dividend",
            ex_date=s[250],
            ratio_or_amount=0.25,
            known_at=revised_known_at,
            ingested_at=revised_known_at,
            source="alpaca",
            provenance="action",
        )
    )

    # --- A restated shares fact: same as_of_date, two known_at values. ---
    u.security("REST1", "0001000017", "Restatement Corp", known_at=_dt(s[240]))
    u.listing("REST1", "REST", "NASDAQ", s[240], known_at=_dt(s[240]))
    u.classification("REST1", "common", "sic_default", known_at=_dt(s[240]), sic=3576)
    u.bars("REST1", s[240:340], 47.0)
    shares_as_of = s[270]
    u.fact(
        "REST1",
        "shares_outstanding",
        shares_as_of,
        12_000_000,
        known_at=_dt(s[275]),
        class_member="common",
        filing_accession="0001000017-19-000010",
    )
    u.fact(
        "REST1",
        "shares_outstanding",
        shares_as_of,
        11_750_000,
        known_at=_dt(s[310]),
        class_member="common",
        filing_accession="0001000017-19-000045",
    )

    # --- A stale shares fact: no newer fact, aged past
    # universe.max_shares_age_days by a later month-end T (2019-08-30,
    # 562 days after known_at — see README). -------------------------------
    u.security("STALE1", "0001000018", "Stale Data Holdings", known_at=_dt(s[0]))
    u.listing("STALE1", "STAL", "NYSE", s[0], known_at=_dt(s[0]))
    u.classification("STALE1", "common", "sic_default", known_at=_dt(s[0]), sic=3670)
    # Two windows, not one contiguous s[0:430]: bars near the start (context
    # for the stale fact itself, idx30) and s[160:430] (every session in the
    # 12 calendar months before idx418 = 2019-08-30, T, through T) — an
    # explicit, documented gap between them rather than ~100 filler rows
    # nothing here reads.
    u.bars("STALE1", s[0:60], 21.0)
    u.bars("STALE1", s[160:430], 21.0)
    u.fact(
        "STALE1",
        "shares_outstanding",
        s[25] - timedelta(days=3),
        6_000_000,
        known_at=_dt(s[30]),
        class_member="common",
        filing_accession="0001000018-18-000003",
    )

    # --- An unclassifiable name: no rule matches. -------------------------
    u.security("UNCL1", "0001000019", "Unclassifiable Ventures", known_at=_dt(s[0]))
    u.listing("UNCL1", "UNCL", "NASDAQ", s[0], known_at=_dt(s[0]))
    u.classification(
        "UNCL1",
        "unclassifiable",
        "unclassifiable",
        known_at=_dt(s[0]),
        provenance="snapshot_static",
    )
    u.bars("UNCL1", s[0:40], 17.0)

    # --- A snapshot_static-only pre-2019 listing. --------------------------
    pre_2019 = date(2017, 3, 1)
    u.security("STAT1", "0001000020", "Static Snapshot Co", known_at=_dt(s[0]))
    u.listing(
        "STAT1",
        "STAT",
        "NYSE_AMERICAN",
        pre_2019,
        known_at=_dt(s[450]),
        source="alpaca",
        provenance="snapshot_static",
    )
    u.classification("STAT1", "common", "sic_default", known_at=_dt(s[0]), sic=5411)
    u.bars("STAT1", s[0:40], 38.0)

    # --- Benchmarks: seeded from config, not derived from EDGAR. ----------
    for sid, cik, name, ticker, price in (
        ("SPY", "0000900001", "SPDR S&P 500 ETF Trust (fixture)", "SPY", 250.0),
        ("MTUM", "0000900002", "iShares MSCI USA Momentum Factor ETF (fixture)", "MTUM", 90.0),
    ):
        u.security(
            sid,
            cik,
            name,
            known_at=_dt(s[0]),
            benchmark=True,
            source="config",
            provenance="snapshot_static",
        )
        u.listing(
            sid,
            ticker,
            "NYSE_ARCA",
            s[0],
            known_at=_dt(s[0]),
            source="config",
            provenance="snapshot_static",
        )
        u.classification(
            sid,
            "fund",
            "benchmark_seed",
            known_at=_dt(s[0]),
            source="config",
            provenance="snapshot_static",
        )
        window = s[0:150]
        u.bars(sid, window, price)
        for i in range(60, len(window), 63):
            ex = window[i]
            u.action(sid, "dividend", ex, round(price * 0.005, 2))

    u.write(out_dir, columns)


if __name__ == "__main__":
    generate()
