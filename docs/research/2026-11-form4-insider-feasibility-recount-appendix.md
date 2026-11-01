# Form 4 insider purchases: T342 recount script (appendix)

**Appendix to:** [the T342 recount note](2026-11-form4-insider-feasibility-recount.md) (#2679) · **Date:** 2026-10-31 · **Status:** frozen copy of the script that produced the T342 numbers

This is the full source of `~/tradepartner-probes/t342/recount.py`, the script the orchestrator ran on 2026-10-31 on a store copy. The copy below is byte for byte. The original file's sha256 is `9dcb98de00428c2e6c22c47214a1222238186d16ec4868abaee5c746fef13c15`, and the script prints the same hash on its first output line. The fenced block, without its fence lines, reproduces that hash. It lives in a Markdown appendix rather than as a `.py` file so that the repository's formatter cannot rewrite it and change the hash. Run instructions are in the note's [What ran](2026-11-form4-insider-feasibility-recount.md#what-ran) section. It must be run only on a copy of the store, never the live file.

```text
"""T342 counts-only feasibility recount for the Form 4 insider-purchase study (#2679).

Usage (on a COPY of the store, never the live file):

    uv run --offline --no-sync --project <repo> python -I recount.py <store-copy.duckdb> \
        [--json out.json] [--continue-if-blocked]

What it reads, and nothing else (counts and coverage only):
- `sec_filing_events`: accession, form, accepted_at (quarter-completeness ratio).
- `sec_form4_transactions`: keys, roles, code, A/D, shares>0 and price>0 tests,
  transaction_date, accepted_at (classifier, coalescing, A4 exclusion).
- `securities` (cik mapping), `classifications` (security_type as of the entry cutoff),
  `delistings` (Form 25 rows), via the store's own as-of readers.
- `prices_daily`: ONLY security_id, session, close, volume, known_at of the latest
  revision at the window-end cut (the runner's `EventStudyReader.bars` cut, traded
  bars only). Close and volume are used only as close >= 1 (price floor) and the
  20-session median of close x volume (liquidity stratum and the A3 class choice).
  No open/high/low, no adjusted bar, no return, no shares fact is ever read.

Everything is pinned to the frozen pre-registration table (desk read 2026-10-31) and
the owner's T343 answers (PR #2680 comment "T343: owner's answers"). No threshold here
is a parameter.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl

from tradepartner.calendar import all_sessions, is_session, previous_session, session_close, session_open
from tradepartner.store.asof import _chain_id, _chain_join, _chain_own, _chains_registered
from tradepartner.store.classify import classifications_as_of
from tradepartner.store.db import configure_connection
from tradepartner.store.delistings import _filing_session, delistings_as_of
from tradepartner.store.master import securities_as_of

# ---------------------------------------------------------------------------
# Frozen values (desk read "Frozen pre-registration"; T343 answers). Not parameters.
# ---------------------------------------------------------------------------
ACC_START = datetime(2020, 1, 1, tzinfo=UTC)  # eligible acceptances from (UTC)
ACC_END_EXCL = datetime(2023, 12, 1, tzinfo=UTC)  # ... through 2023-11-30 UTC
WINDOW_START = date(2020, 1, 1)  # registered window start
WINDOW_LAST_SESSION = date(2023, 12, 29)  # registered window ends at close(2023-12-29)
CUTOFF = session_close(WINDOW_LAST_SESSION)  # no read after this instant
HISTORY_FROM = datetime(2017, 1, 1, tzinfo=UTC)  # event dataset history from 2017-01-01
HORIZON = 20  # entry session counted as 1
BUFFER = timedelta(minutes=15)  # next_open_after_instant_buffer_15m
PRICE_FLOOR = 1.0
VOLUME_LOOKBACK = 20
MIN_VOLUME_BARS = 10
BETA_LOOKBACK = 120  # only for the A7 stamp check (raw-bar stamps), never a beta
STRATA: tuple[tuple[str, float], ...] = (("200M", 200_000_000.0), ("all", 0.0))
FLOOR_N = 400
ENTRY_MISSING_MAX = 0.10
DELISTER_MIN = 0.90
RATIO = 0.8  # T343 ratio rule
RATIO_YEARS = tuple(range(2016, 2024))  # 2016Q1..2023Q4
FIRST_OBSERVED_YEAR = 2017  # T343 A1
A4_CLOSE_OFFSET = 21  # close(E-21) < accepted_at <= open(E) - 15 min
DELIST_FORMS = ("25", "25-NSE")
DELIST_AMENDMENTS = ("25/A", "25-NSE/A")
NS = 1_000_000_000


def sha256_self() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def pct(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{100.0 * num / den:.1f}%"


def frac(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def ns(t: datetime) -> int:
    return (int(t.timestamp()) * NS) + t.microsecond * 1000


class Out:
    """Collects printed lines and a JSON mirror."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def line(self, tag: str, text: str) -> None:
        print(f"[{tag}] {text}", flush=True)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
def build_calendar() -> pl.DataFrame:
    sessions = [d for d in all_sessions() if date(2015, 6, 1) <= d <= date(2024, 3, 29)]
    opens = [session_open(d) for d in sessions]
    closes = [session_close(d) for d in sessions]
    return pl.DataFrame(
        {
            "idx": list(range(len(sessions))),
            "session": sessions,
            "open_ns": [ns(o) for o in opens],
            "close_ns": [ns(c) for c in closes],
            "cut_ns": [ns(o - BUFFER) for o in opens],
        },
        schema={"idx": pl.Int64, "session": pl.Date, "open_ns": pl.Int64, "close_ns": pl.Int64, "cut_ns": pl.Int64},
    )


# ---------------------------------------------------------------------------
# Store reads (read-only connection, projected columns only)
# ---------------------------------------------------------------------------
def bars_cut(
    conn: duckdb.DuckDBPyConnection, t: datetime, sessions_from: date
) -> tuple[pl.DataFrame, int]:
    """`prices_as_of(conn, t, traded_only=True, sessions_from=...)` restricted to
    security_id, session, close, volume, known_at (the same SQL shape as
    `store.asof._latest_as_of`, chain-aware, but never selecting open/high/low)."""
    with _chains_registered(conn, t) as chains:
        hide = chains.hide_sql("t")
        if chains:
            sql = f"""
                SELECT security_id, session, close, volume, known_at FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY security_id, session ORDER BY _own, known_at DESC) AS _rn
                    FROM (
                        SELECT {_chain_id()} AS security_id, t.session, t.close, t.volume,
                               t.known_at, {_chain_own()} AS _own
                        FROM prices_daily t {_chain_join()}
                        WHERE t.known_at <= ? AND t.session >= ? {hide}
                    )
                ) WHERE _rn = 1 AND volume > 0
            """
        else:
            sql = f"""
                SELECT security_id, session, close, volume, known_at FROM (
                    SELECT t.security_id, t.session, t.close, t.volume, t.known_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY t.security_id, t.session ORDER BY t.known_at DESC) AS _rn
                    FROM prices_daily t
                    WHERE t.known_at <= ? AND t.session >= ? {hide}
                ) WHERE _rn = 1 AND volume > 0
            """
        frame = conn.execute(sql, [t, sessions_from]).pl()
        n_chains = len(chains.pairs)
    return frame, n_chains


def norm_cik(col: str) -> pl.Expr:
    return pl.col(col).str.replace_all(r"[^0-9]", "").str.zfill(10)


# ---------------------------------------------------------------------------
# Quarter-completeness ratio (T343, blocking) and the row series (non-blocking)
# ---------------------------------------------------------------------------
def quarter_tables(conn: duckdb.DuckDBPyConnection, out: Out) -> tuple[bool, set[tuple[int, int]], pl.DataFrame]:
    acc = conn.execute(
        "SELECT accession, MIN(accepted_at) AS accepted_at FROM sec_filing_events "
        "WHERE form = '4' AND accepted_at <= ? GROUP BY accession",
        [CUTOFF],
    ).pl()
    acc = acc.with_columns(pl.col("accepted_at").dt.convert_time_zone("America/New_York").alias("ny"))
    acc = acc.with_columns(pl.col("ny").dt.year().alias("y"), pl.col("ny").dt.quarter().alias("q"))
    acc_counts = {(r["y"], r["q"]): r["n"] for r in acc.group_by("y", "q").agg(pl.len().alias("n")).to_dicts()}

    qrows = conn.execute(
        """
        SELECT t.accession, t.accepted_at FROM sec_form4_transactions t
        WHERE t.transaction_code = 'P' AND t.acquired_disposed = 'A' AND t.shares > 0
          AND t.price IS NOT NULL AND t.price > 0 AND (t.is_officer OR t.is_director)
          AND t.accepted_at <= ? AND t.accepted_at >= ?
          AND t.accession IN (SELECT accession FROM sec_filing_events WHERE form = '4')
        """,
        [CUTOFF, datetime(2016, 1, 1, tzinfo=UTC)],
    ).pl()
    qrows = qrows.with_columns(pl.col("accepted_at").dt.convert_time_zone("America/New_York").alias("ny"))
    qrows = qrows.with_columns(pl.col("ny").dt.year().alias("y"), pl.col("ny").dt.quarter().alias("q"))
    row_counts = {(r["y"], r["q"]): r["n"] for r in qrows.group_by("y", "q").agg(pl.len().alias("n")).to_dicts()}
    qacc_counts = {
        (r["y"], r["q"]): r["n"]
        for r in qrows.group_by("y", "q").agg(pl.col("accession").n_unique().alias("n")).to_dicts()
    }

    quarters = [(y, q) for y in RATIO_YEARS for q in (1, 2, 3, 4)]
    failing: set[tuple[int, int]] = set()
    table = []
    zero_quarters = 0
    for y, q in quarters:
        n = acc_counts.get((y, q), 0)
        refs = [acc_counts.get((yy, q), 0) for yy in (y - 2, y - 1, y + 1, y + 2) if yy in RATIO_YEARS]
        ref = statistics.median(refs)
        ok = n >= RATIO * ref
        if not ok:
            failing.add((y, q))
        if n == 0:
            zero_quarters += 1
        # row series: median of two quarters on each side, inside 2016..2023 (non-blocking)
        k = quarters.index((y, q))
        side = [quarters[j] for j in (k - 2, k - 1, k + 1, k + 2) if 0 <= j < len(quarters)]
        rref = statistics.median([row_counts.get(s, 0) for s in side])
        rows_n = row_counts.get((y, q), 0)
        table.append(
            {
                "quarter": f"{y}Q{q}",
                "form4_accessions": n,
                "ref_median": ref,
                "ratio": None if ref == 0 else n / ref,
                "pass": ok,
                "qual_rows": rows_n,
                "qual_accessions": qacc_counts.get((y, q), 0),
                "rows_ref_median": rref,
                "rows_ratio": None if rref == 0 else rows_n / rref,
            }
        )
        rr = "n/a" if ref == 0 else f"{n / ref:.2f}"
        rw = "n/a" if rref == 0 else f"{rows_n / rref:.2f}"
        out.line(
            "ratio",
            f"{y}Q{q} form4_accessions={n} ref_median={ref:g} ratio={rr} {'PASS' if ok else 'FAIL'}"
            f" | qualifying rows={rows_n} accessions={qacc_counts.get((y, q), 0)}"
            f" rows_ref_median={rref:g} rows_ratio={rw} (non-blocking)",
        )
    out.put("quarters", table)
    complete = not failing
    out.line(
        "ratio",
        f"snapshot {'COMPLETE' if complete else 'INCOMPLETE'}: {len(failing)} of {len(quarters)} quarters below "
        f"{RATIO} x median (failing: {', '.join(f'{y}Q{q}' for y, q in sorted(failing)) or 'none'});"
        f" quarters with zero accessions={zero_quarters}",
    )
    out.put("snapshot_complete", complete)
    return complete, failing, acc


# ---------------------------------------------------------------------------
# Main recount
# ---------------------------------------------------------------------------
def run(store: Path, out: Out, continue_if_blocked: bool) -> str:
    t0 = time.time()
    conn = duckdb.connect(str(store), read_only=True)
    configure_connection(conn)
    work = duckdb.connect(":memory:")
    configure_connection(work)

    cal = build_calendar()
    work.register("cal", cal)
    cut_arr = cal["cut_ns"].to_numpy()
    open_arr = cal["open_ns"].to_numpy()
    close_arr = cal["close_ns"].to_numpy()
    sess_list = cal["session"].to_list()
    idx_of = {d: i for i, d in enumerate(sess_list)}
    last_idx = idx_of[WINDOW_LAST_SESSION]
    first_idx = next(i for i, d in enumerate(sess_list) if d >= WINDOW_START)

    # ---- 1. quarter ratio (blocking) --------------------------------------
    complete, failing, _ = quarter_tables(conn, out)
    observed = {
        y: (y >= FIRST_OBSERVED_YEAR and all((y, q) not in failing for q in (1, 2, 3, 4)))
        for y in RATIO_YEARS
    }
    out.line("A1", "fully observed years: " + ", ".join(f"{y}={'yes' if v else 'no'}" for y, v in observed.items()))
    out.put("observed_years", {str(k): v for k, v in observed.items()})
    if not complete and not continue_if_blocked:
        out.line("blocked", "the T343 ratio rule blocks the recount; nothing below was computed")
        verdict = "FAIL (incomplete snapshot: T343 ratio rule)"
        out.line("VERDICT", verdict)
        return verdict
    if not complete:
        out.line("blocked", "--continue-if-blocked: counts below are DIAGNOSTIC ONLY, not a T342 result")

    # ---- 2. qualifying lines, votes, classification ------------------------
    q = conn.execute(
        """
        SELECT t.accession, t.issuer_cik, t.filer_cik, t.transaction_date, t.accepted_at
        FROM sec_form4_transactions t
        WHERE t.transaction_code = 'P' AND t.acquired_disposed = 'A' AND t.shares > 0
          AND t.price IS NOT NULL AND t.price > 0 AND (t.is_officer OR t.is_director)
          AND t.accepted_at <= ? AND t.accepted_at >= ?
          AND t.accession IN (SELECT accession FROM sec_filing_events WHERE form = '4')
        """,
        [CUTOFF, HISTORY_FROM],
    ).pl()
    q = q.with_columns(norm_cik("issuer_cik").alias("issuer"), norm_cik("filer_cik").alias("filer"))
    q = q.with_columns(pl.col("accepted_at").dt.epoch("ns").alias("acc_ns"))
    a = q["acc_ns"].to_numpy()
    q = q.with_columns(
        pl.Series("entry_idx", np.searchsorted(cut_arr, a, side="left")),  # first E: a <= open(E)-15m
        pl.Series("base_idx", np.searchsorted(open_arr, a, side="left")),  # first E: a <= open(E)
        pl.Series("close_pos", np.searchsorted(close_arr, a, side="left")),  # closes[c-1] < a <= closes[c]
    )
    work.register("q", q)
    out.line("data", f"qualifying P/A officer/director Form 4 lines (accepted 2017-01-01..cutoff): {q.height}")

    work.execute("""
        CREATE TABLE votes AS
        SELECT accession, issuer, filer, MIN(accepted_at) accepted_at, MIN(entry_idx) entry_idx,
               MIN(base_idx) base_idx, MIN(transaction_date) td,
               year(MIN(transaction_date)) ty, month(MIN(transaction_date)) tm, COUNT(*) n_lines
        FROM q GROUP BY accession, issuer, filer
    """)
    work.execute("CREATE TABLE hist AS SELECT DISTINCT issuer, filer, accession, accepted_at, "
                 "year(transaction_date) y, month(transaction_date) m FROM q")
    obs = pl.DataFrame({"y": list(observed), "obs": list(observed.values())})
    work.register("obs", obs)
    work.execute(
        """
        CREATE TABLE wvotes AS SELECT * FROM votes
        WHERE accepted_at >= ? AND accepted_at < ?
        """,
        [ACC_START, ACC_END_EXCL],
    )
    work.execute("""
        CREATE TABLE vcl AS
        SELECT v.accession, v.issuer, v.filer, v.entry_idx, v.base_idx, v.ty, v.tm,
          COUNT(DISTINCT h.y) FILTER (WHERE h.y IN (v.ty - 1, v.ty - 2, v.ty - 3)) yrs_with_trade,
          COUNT(DISTINCT h.y) FILTER (WHERE h.y IN (v.ty - 1, v.ty - 2, v.ty - 3) AND h.m = v.tm) yrs_with_month
        FROM wvotes v LEFT JOIN hist h
          ON h.issuer = v.issuer AND h.filer = v.filer AND h.accepted_at < v.accepted_at
         AND h.accession <> v.accession
        GROUP BY ALL
    """)
    work.execute("""
        CREATE TABLE vlab AS
        SELECT vcl.*,
          (COALESCE(o1.obs, false) AND COALESCE(o2.obs, false) AND COALESCE(o3.obs, false)) years_observed,
          CASE
            WHEN NOT (COALESCE(o1.obs, false) AND COALESCE(o2.obs, false) AND COALESCE(o3.obs, false))
              THEN 'unclassifiable'
            WHEN yrs_with_trade < 3 THEN 'unclassifiable'
            WHEN yrs_with_month = 3 THEN 'routine'
            ELSE 'opportunistic' END lab,
          CASE
            WHEN NOT (COALESCE(o1.obs, false) AND COALESCE(o2.obs, false) AND COALESCE(o3.obs, false))
              THEN 'year_not_fully_observed'
            WHEN yrs_with_trade < 3 THEN 'year_without_trade' END unclass_reason
        FROM vcl
        LEFT JOIN obs o1 ON o1.y = vcl.ty - 1
        LEFT JOIN obs o2 ON o2.y = vcl.ty - 2
        LEFT JOIN obs o3 ON o3.y = vcl.ty - 3
    """)
    vt = work.execute("""
        SELECT COUNT(*) votes, COUNT(*) FILTER (WHERE lab <> 'unclassifiable') classifiable,
               COUNT(*) FILTER (WHERE lab = 'opportunistic') opp, COUNT(*) FILTER (WHERE lab = 'routine') rout,
               COUNT(*) FILTER (WHERE unclass_reason = 'year_not_fully_observed') u_obs,
               COUNT(*) FILTER (WHERE unclass_reason = 'year_without_trade') u_trade
        FROM vlab
    """).fetchone()
    out.line(
        "A1",
        f"window votes (eligible acceptances)={vt[0]} classifiable={vt[1]} ({pct(vt[1], vt[0])})"
        f" opportunistic={vt[2]} routine={vt[3]} | unclassifiable: year not fully observed={vt[4]},"
        f" year without a qualifying trade={vt[5]}",
    )
    out.put("votes", dict(zip(["votes", "classifiable", "opportunistic", "routine", "unclass_year_not_observed",
                               "unclass_year_without_trade"], vt, strict=True)))
    ym = work.execute("""
        SELECT ty, tm, COUNT(*) n, COUNT(*) FILTER (WHERE unclass_reason = 'year_not_fully_observed') n_obs,
               COUNT(*) FILTER (WHERE unclass_reason = 'year_without_trade') n_trade
        FROM vlab WHERE lab = 'unclassifiable' GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    out.line("A1", "unclassifiable votes by year-month (total/not-observed/no-trade): "
             + " ".join(f"{y}-{m:02d}:{n}/{o}/{t}" for y, m, n, o, t in ym))
    out.put("unclassifiable_by_year_month", [list(r) for r in ym])

    # ---- 3. coalesced issuer x entry-session events -------------------------
    work.execute("""
        CREATE TABLE ev AS
        SELECT issuer, entry_idx, COUNT(*) n_votes, COUNT(DISTINCT filer) n_insiders,
          bool_or(base_idx <> entry_idx) buffered,
          CASE WHEN bool_or(lab = 'unclassifiable') THEN 'unclassifiable'
               WHEN bool_and(lab = 'opportunistic') THEN 'opportunistic'
               WHEN bool_and(lab = 'routine') THEN 'routine' ELSE 'mixed' END kind
        FROM vlab GROUP BY issuer, entry_idx
    """)
    work.execute(f"""
        CREATE TABLE evw AS SELECT * FROM ev
        WHERE entry_idx >= {first_idx} AND entry_idx + {HORIZON - 1} <= {last_idx}
    """)
    kinds = dict(work.execute("SELECT kind, COUNT(*) FROM evw GROUP BY 1").fetchall())
    hz = work.execute("SELECT COUNT(*) FROM ev").fetchone()[0] - sum(kinds.values())
    buffered = work.execute("SELECT COUNT(*) FILTER (WHERE buffered) FROM evw").fetchone()[0]
    out.line("events", f"window issuer x entry-session events: {kinds} | buffered={buffered}"
             f" | outside window/horizon={hz}")
    out.put("event_kinds", kinds)
    out.put("buffered_events", buffered)

    # ---- 4. securities, classifications as of each entry cutoff (A3, A7) -----
    secs = securities_as_of(conn, CUTOFF).select("security_id", "cik")
    secs = secs.with_columns(norm_cik("cik").alias("cik10")).select("security_id", "cik10")
    work.register("secs", secs)
    sessions_needed = [r[0] for r in work.execute("SELECT DISTINCT entry_idx FROM evw ORDER BY 1").fetchall()]
    common_parts = []
    for e in sessions_needed:
        t_cut = session_open(sess_list[e]) - BUFFER
        c = classifications_as_of(conn, t_cut).filter(pl.col("security_type") == "common")
        common_parts.append(c.select("security_id").with_columns(pl.lit(e, pl.Int64).alias("E")))
    common = (pl.concat(common_parts) if common_parts else
              pl.DataFrame(schema={"security_id": pl.Utf8, "E": pl.Int64}))
    work.register("common_at", common)
    latest_common = classifications_as_of(conn, CUTOFF).filter(pl.col("security_type") == "common").select("security_id")
    work.register("latest_common", latest_common)
    ties = conn.execute(
        "SELECT COUNT(*) FROM (SELECT security_id, known_at FROM classifications GROUP BY 1, 2 "
        "HAVING COUNT(DISTINCT security_type) > 1)"
    ).fetchone()[0]
    out.line("A3", f"classification rows tied on (security_id, known_at) with different types: {ties}"
             " (as-of read picks one arbitrarily; expected 0)")

    # ---- 5. bars: the window-end cut, projected ------------------------------
    sessions_from = sess_list[max(0, first_idx - BETA_LOOKBACK - 5)]
    bars, n_chains = bars_cut(conn, CUTOFF, sessions_from)
    bars = bars.join(cal.select("idx", "session", "cut_ns"), on="session", how="inner")
    bars = bars.with_columns(
        (pl.col("close") * pl.col("volume").cast(pl.Float64)).alias("dv"),
        pl.col("known_at").dt.epoch("ns").alias("known_ns"),
    ).select("security_id", "idx", "close", "dv", "known_ns")
    work.register("bars_src", bars)
    work.execute("CREATE TABLE bars AS SELECT * FROM bars_src")
    out.line("data", f"window-cut traded bars read (sessions from {sessions_from}): {bars.height};"
             f" live successor chains at cut: {n_chains}")
    # stats for entry E = k + 1 from the bar at k (prior bar) and bars in [E-20, E-1]
    work.execute(f"""
        CREATE TABLE st AS
        SELECT security_id, idx + 1 AS E, close AS prior_close,
          COUNT(*) OVER w AS nb, MEDIAN(dv) OVER w AS adv
        FROM bars
        WINDOW w AS (PARTITION BY security_id ORDER BY idx RANGE BETWEEN {VOLUME_LOOKBACK - 1} PRECEDING
                     AND CURRENT ROW)
    """)

    # ---- 6. A7: pre-entry bar stamps (late bars) ------------------------------
    work.execute("""
        CREATE TABLE late AS
        SELECT b.security_id, b.idx, b.known_ns FROM bars b JOIN cal c ON c.idx = b.idx + 1
        WHERE b.known_ns > c.cut_ns
    """)
    n_late = work.execute("SELECT COUNT(*) FROM late").fetchone()[0]

    # ---- 7. treated events -> securities (A3) ---------------------------------
    work.execute("""
        CREATE TABLE cand AS
        SELECT e.issuer, e.entry_idx E, s.security_id
        FROM evw e JOIN secs s ON s.cik10 = e.issuer
        JOIN common_at c ON c.security_id = s.security_id AND c.E = e.entry_idx
    """)
    # pairs whose 20-session window holds a bar not known at the entry cutoff
    pairs_sql = """
        SELECT DISTINCT p.security_id, p.E FROM {pairs} p JOIN late l
          ON l.security_id = p.security_id AND l.idx BETWEEN p.E - {lb} AND p.E - 1
        JOIN cal c ON c.idx = p.E WHERE l.known_ns > c.cut_ns
    """
    a7_t20 = work.execute(f"SELECT COUNT(*) FROM ({pairs_sql.format(pairs='cand', lb=VOLUME_LOOKBACK)})").fetchone()[0]
    a7_t120 = work.execute(f"SELECT COUNT(*) FROM ({pairs_sql.format(pairs='cand', lb=BETA_LOOKBACK)})").fetchone()[0]
    work.execute("""
        CREATE TABLE cand_st AS
        SELECT c.*, st.prior_close, st.nb, st.adv FROM cand c
        LEFT JOIN st ON st.security_id = c.security_id AND st.E = c.E
    """)
    work.execute("""
        CREATE TABLE chosen AS
        SELECT issuer, E, security_id, n_common_classes FROM (
          SELECT issuer, E, security_id, COUNT(*) OVER (PARTITION BY issuer, E) n_common_classes,
                 ROW_NUMBER() OVER (PARTITION BY issuer, E ORDER BY (prior_close IS NULL), adv DESC NULLS LAST,
                                    security_id) rn
          FROM cand_st) WHERE rn = 1
    """)
    work.execute("""
        CREATE TABLE evf AS
        SELECT e.*, ch.security_id, ch.n_common_classes, (ch.security_id IS NOT NULL) has_sec,
               st.prior_close, st.nb, st.adv,
               EXISTS (SELECT 1 FROM bars b WHERE b.security_id = ch.security_id AND b.idx = e.entry_idx) has_entry_bar
        FROM evw e LEFT JOIN chosen ch ON ch.issuer = e.issuer AND ch.E = e.entry_idx
        LEFT JOIN st ON st.security_id = ch.security_id AND st.E = e.entry_idx
    """)
    nosec = work.execute("""
        SELECT COUNT(*) FILTER (WHERE NOT has_sec), COUNT(*) FILTER (WHERE NOT has_sec AND kind = 'opportunistic'),
               COUNT(DISTINCT issuer) FILTER (WHERE NOT has_sec),
               COUNT(*) FILTER (WHERE has_sec AND n_common_classes > 1)
        FROM evf
    """).fetchone()
    diag = work.execute("""
        SELECT COUNT(*) FROM evf e WHERE NOT e.has_sec AND EXISTS (
          SELECT 1 FROM secs s JOIN latest_common l USING (security_id) WHERE s.cik10 = e.issuer)
    """).fetchone()[0]
    out.line("A3", f"no_common_security events (window): all kinds={nosec[0]} opportunistic={nosec[1]}"
             f" distinct issuer CIKs={nosec[2]} | multi-class events (most liquid class chosen)={nosec[3]}"
             f" | diagnostic: of the no_common_security events, {diag} map to a common class under"
             " the classification known at the window-end cut (not decisive)")
    out.put("no_common_security", {"all": nosec[0], "opportunistic": nosec[1], "issuers": nosec[2],
                                   "multi_class_events": nosec[3], "window_cut_classification_would_map": diag})

    # ---- 8. delisters (A6) -----------------------------------------------------
    dl = delistings_as_of(conn, CUTOFF)
    delisters: dict[str, dict[str, Any]] = {}
    by_sid: dict[str, list[dict[str, Any]]] = {}
    for r in dl.to_dicts():
        if r["filed_at"] <= CUTOFF:
            by_sid.setdefault(r["security_id"], []).append(r)
    for sid, rows in by_sid.items():
        orig = [r for r in rows if r["form"] in DELIST_FORMS]
        pool = orig or [r for r in rows if r["form"] in DELIST_AMENDMENTS]
        if not pool:
            continue
        first = min(pool, key=lambda r: r["filed_at"])
        f_sess = _filing_session(first["filed_at"])
        last_listed = previous_session(f_sess)
        eff = first["effective_on"]
        eff_last = eff if is_session(eff) else previous_session(eff)
        delisters[sid] = {
            "security_id": sid,
            "L": idx_of.get(last_listed, -1),
            "L_eff": idx_of.get(eff_last, -1),
            "eff_idx_after": bisect.bisect_right(sess_list, eff),
            "from_amendment": not orig,
        }
    dlf = pl.DataFrame(
        list(delisters.values()),
        schema={"security_id": pl.Utf8, "L": pl.Int64, "L_eff": pl.Int64, "eff_idx_after": pl.Int64,
                "from_amendment": pl.Boolean},
    )
    work.register("dl_all", dlf)
    work.execute("""
        CREATE TABLE dl AS SELECT d.* FROM dl_all d
        WHERE NOT EXISTS (SELECT 1 FROM bars b WHERE b.security_id = d.security_id AND b.idx >= d.eff_idx_after)
    """)
    n_dl_all = dlf.height
    n_dl = work.execute("SELECT COUNT(*) FROM dl").fetchone()[0]
    n_dl_amend = work.execute("SELECT COUNT(*) FROM dl WHERE from_amendment").fetchone()[0]
    out.line("A6", f"Form 25 delister securities (accepted <= cutoff): {n_dl_all}; after excluding names with a bar"
             f" after effective_on: {n_dl} (from a /A only: {n_dl_amend})")

    # ---- 9. A4 exclusion source ----------------------------------------------
    # issuer excluded on E for any qualifying line with close(E-21) < a <= open(E)-15m,
    # i.e. E in [entry_idx(a), close_pos(a) + 20]
    work.execute(f"""
        CREATE TABLE excl_rng AS
        SELECT DISTINCT issuer, entry_idx AS e_lo, close_pos + {A4_CLOSE_OFFSET - 1} AS e_hi FROM q
    """)

    # ---- 10. per stratum ---------------------------------------------------------
    results: dict[str, dict[str, Any]] = {}
    alt_results: dict[str, dict[str, Any]] = {}
    neutral_a7: dict[str, tuple[int, int]] = {}
    for name, floor in STRATA:
        elig = (f"has_sec AND prior_close IS NOT NULL AND prior_close >= {PRICE_FLOOR}"
                f" AND nb >= {MIN_VOLUME_BARS} AND adv >= {floor}")
        work.execute(f"CREATE OR REPLACE TABLE el_{name} AS SELECT * FROM evf WHERE {elig}")
        s = work.execute(f"""
            SELECT
              (SELECT COUNT(*) FROM evf WHERE kind = 'opportunistic') opp_raw,
              (SELECT COUNT(*) FROM evf WHERE kind = 'opportunistic' AND has_sec) opp_mapped,
              COUNT(*) FILTER (WHERE kind = 'opportunistic') opp_elig,
              COUNT(*) FILTER (WHERE kind = 'opportunistic' AND has_entry_bar) opp_n,
              COUNT(DISTINCT issuer) FILTER (WHERE kind = 'opportunistic' AND has_entry_bar) opp_issuers,
              COUNT(DISTINCT entry_idx) FILTER (WHERE kind = 'opportunistic' AND has_entry_bar) opp_sessions,
              COUNT(*) FILTER (WHERE kind IN ('opportunistic', 'routine')) tr_elig,
              COUNT(*) FILTER (WHERE kind IN ('opportunistic', 'routine') AND has_entry_bar) tr_n,
              COUNT(*) all_elig,
              COUNT(*) FILTER (WHERE kind <> 'unclassifiable') all_classified,
              COUNT(*) FILTER (WHERE kind = 'routine') rout_elig,
              COUNT(*) FILTER (WHERE kind = 'mixed') mixed_elig
            FROM el_{name}
        """).fetchone()
        (opp_raw, opp_mapped, opp_elig, opp_n, opp_iss, opp_sess, tr_elig, tr_n, all_elig, all_cls,
         rout_elig, mixed_elig) = s

        def treated_cov(kinds_sql: str) -> tuple[int, int, int, int, int, int, int]:
            r = work.execute(f"""
                WITH t AS (
                  SELECT e.security_id, e.entry_idx E, d.L, d.L_eff FROM el_{name} e JOIN dl d USING (security_id)
                  WHERE e.kind IN ({kinds_sql})
                ), span AS (
                  SELECT t.*, (SELECT COUNT(*) FROM bars b WHERE b.security_id = t.security_id
                                 AND b.idx BETWEEN t.E AND t.L) nb_l,
                              (SELECT COUNT(*) FROM bars b WHERE b.security_id = t.security_id
                                 AND b.idx BETWEEN t.E AND t.L_eff) nb_eff,
                              (SELECT COUNT(*) FROM bars b WHERE b.security_id = t.security_id
                                 AND b.idx BETWEEN t.E AND LEAST(t.L, t.E + {HORIZON - 1})) nb_any
                  FROM t
                )
                SELECT
                  COUNT(*) FILTER (WHERE L BETWEEN E AND E + {HORIZON - 1}),
                  COUNT(*) FILTER (WHERE L BETWEEN E AND E + {HORIZON - 1} AND nb_l = L - E + 1),
                  COUNT(DISTINCT security_id) FILTER (WHERE L BETWEEN E AND E + {HORIZON - 1}),
                  COUNT(*) FILTER (WHERE L_eff BETWEEN E AND E + {HORIZON - 1}),
                  COUNT(*) FILTER (WHERE L_eff BETWEEN E AND E + {HORIZON - 1} AND nb_eff = L_eff - E + 1),
                  COUNT(*) FILTER (WHERE L >= E),
                  COUNT(*) FILTER (WHERE L >= E AND nb_any = LEAST(L, E + {HORIZON - 1}) - E + 1)
                FROM span
            """).fetchone()
            return r

        def neutral(sessions_kinds: str) -> dict[str, Any]:
            work.execute(f"""
                CREATE OR REPLACE TABLE ps AS SELECT DISTINCT entry_idx E FROM el_{name}
                WHERE kind IN ({sessions_kinds})
            """)
            work.execute(f"""
                CREATE OR REPLACE TABLE pool0 AS
                SELECT ps.E, c.security_id, s.cik10, st.adv
                FROM ps JOIN common_at c ON c.E = ps.E
                JOIN secs s ON s.security_id = c.security_id
                JOIN st ON st.security_id = c.security_id AND st.E = ps.E
                WHERE st.prior_close >= {PRICE_FLOOR} AND st.nb >= {MIN_VOLUME_BARS} AND st.adv >= {floor}
            """)
            work.execute("""
                CREATE OR REPLACE TABLE pool_x AS
                SELECT p.*, EXISTS (SELECT 1 FROM excl_rng x WHERE x.issuer = p.cik10
                                    AND p.E BETWEEN x.e_lo AND x.e_hi) a4,
                            EXISTS (SELECT 1 FROM evw e WHERE e.issuer = p.cik10 AND e.entry_idx = p.E) own_event
                FROM pool0 p
            """)
            r = work.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE a4), COUNT(*) FILTER (WHERE a4 AND own_event),
                       COUNT(DISTINCT cik10 || '@' || CAST(E AS VARCHAR)) FILTER (WHERE a4)
                FROM pool_x
            """).fetchone()
            k = work.execute("""
                SELECT COUNT(*), MEDIAN(n), MIN(n) FROM (
                  SELECT ps.E, COUNT(p.security_id) FILTER (WHERE NOT p.a4) n
                  FROM ps LEFT JOIN pool_x p ON p.E = ps.E GROUP BY ps.E)
            """).fetchone()
            r = (*r, *k)
            cov = work.execute(f"""
                WITH t AS (
                  SELECT p.security_id, p.E, d.L, d.L_eff FROM pool_x p JOIN dl d USING (security_id) WHERE NOT p.a4
                ), span AS (
                  SELECT t.*, (SELECT COUNT(*) FROM bars b WHERE b.security_id = t.security_id
                                 AND b.idx BETWEEN t.E AND t.L) nb_l,
                              (SELECT COUNT(*) FROM bars b WHERE b.security_id = t.security_id
                                 AND b.idx BETWEEN t.E AND t.L_eff) nb_eff,
                              (SELECT COUNT(*) FROM bars b WHERE b.security_id = t.security_id
                                 AND b.idx BETWEEN t.E AND LEAST(t.L, t.E + {HORIZON - 1})) nb_any
                  FROM t
                )
                SELECT
                  COUNT(*) FILTER (WHERE L BETWEEN E AND E + {HORIZON - 1}),
                  COUNT(*) FILTER (WHERE L BETWEEN E AND E + {HORIZON - 1} AND nb_l = L - E + 1),
                  COUNT(DISTINCT security_id) FILTER (WHERE L BETWEEN E AND E + {HORIZON - 1}),
                  COUNT(*) FILTER (WHERE L_eff BETWEEN E AND E + {HORIZON - 1}),
                  COUNT(*) FILTER (WHERE L_eff BETWEEN E AND E + {HORIZON - 1} AND nb_eff = L_eff - E + 1),
                  COUNT(*) FILTER (WHERE L >= E),
                  COUNT(*) FILTER (WHERE L >= E AND nb_any = LEAST(L, E + {HORIZON - 1}) - E + 1)
                FROM span
            """).fetchone()
            excl_dl = work.execute(f"""
                SELECT COUNT(*) FROM pool_x p JOIN dl d USING (security_id)
                WHERE p.a4 AND d.L BETWEEN p.E AND p.E + {HORIZON - 1}
            """).fetchone()[0]
            # A7 over neutral candidate rows (common, prior bar) on these sessions
            work.execute("""
                CREATE OR REPLACE TABLE ncand AS
                SELECT DISTINCT c.security_id, c.E FROM ps JOIN common_at c ON c.E = ps.E
            """)
            n20 = work.execute(f"SELECT COUNT(*) FROM ({pairs_sql.format(pairs='ncand', lb=VOLUME_LOOKBACK)})").fetchone()[0]
            n120 = work.execute(f"SELECT COUNT(*) FROM ({pairs_sql.format(pairs='ncand', lb=BETA_LOOKBACK)})").fetchone()[0]
            return {"rows_before_a4": r[0], "a4_excluded_rows": r[1], "a4_excluded_rows_own_event": r[2],
                    "a4_excluded_issuer_sessions": r[3], "sessions": r[4], "median_pool": r[5], "min_pool": r[6],
                    "cov": cov, "a4_excluded_inspan_delister_rows": excl_dl, "a7_20": n20, "a7_120": n120}

        tc_opp = treated_cov("'opportunistic'")
        tc_tr = treated_cov("'opportunistic', 'routine'")
        nb_opp = neutral("'opportunistic'")
        nb_tr = neutral("'opportunistic', 'routine'")

        def summarize(tc: tuple[int, ...], nb: dict[str, Any], n_ev: int, n_elig: int) -> dict[str, Any]:
            return {
                "n_upper": n_ev, "eligible": n_elig, "entry_missing": n_elig - n_ev,
                "entry_missing_share": frac(n_elig - n_ev, n_elig),
                "treated_dl_inspan": tc[0], "treated_dl_covered": tc[1], "treated_dl_issuers": tc[2],
                "treated_cov": frac(tc[1], tc[0]),
                "treated_eff_reading": [tc[4], tc[3]], "treated_anytime_reading": [tc[6], tc[5]],
                "neutral_dl_inspan": nb["cov"][0], "neutral_dl_covered": nb["cov"][1],
                "neutral_dl_issuers": nb["cov"][2], "neutral_cov": frac(nb["cov"][1], nb["cov"][0]),
                "neutral_eff_reading": [nb["cov"][4], nb["cov"][3]],
                "neutral_anytime_reading": [nb["cov"][6], nb["cov"][5]],
                "a4": {k: v for k, v in nb.items() if k != "cov"},
            }

        res = summarize(tc_opp, nb_opp, opp_n, opp_elig)
        alt = summarize(tc_tr, nb_tr, opp_n, opp_elig)  # floor and entry gate stay on opportunistic
        res.update({"opp_raw": opp_raw, "opp_mapped": opp_mapped, "opp_issuers": opp_iss, "opp_sessions": opp_sess,
                    "routine_eligible": rout_elig, "mixed_eligible": mixed_elig,
                    "classified_share": frac(all_cls, all_elig), "classified": all_cls, "all_eligible": all_elig,
                    "treated_opp_routine_eligible": tr_elig, "treated_opp_routine_with_bar": tr_n})
        results[name] = res
        alt_results[name] = alt
        neutral_a7[name] = (nb_tr["a7_20"], nb_tr["a7_120"])

        tag = f"stratum {name}"
        out.line(tag, f"opportunistic events in window: raw={opp_raw} mapped to common={opp_mapped}"
                 f" otherwise eligible={opp_elig} with entry bar (upper bound of n)={opp_n}"
                 f" [{opp_iss} issuers, {opp_sess} sessions] -> 400 floor {'HOLDS' if opp_n >= FLOOR_N else 'FAILS'}")
        out.line(tag, f"entry-bar gate: no_entry_bar {opp_elig - opp_n} / otherwise eligible {opp_elig}"
                 f" = {pct(opp_elig - opp_n, opp_elig)} -> 10% gate "
                 f"{'MET' if (frac(opp_elig - opp_n, opp_elig) or 0) <= ENTRY_MISSING_MAX and opp_elig else 'MISSED'}")
        out.line(tag, f"treated delister coverage (A6, opportunistic): covered {tc_opp[1]} / in-span {tc_opp[0]}"
                 f" = {pct(tc_opp[1], tc_opp[0])} [{tc_opp[2]} delisted securities] | reported only:"
                 f" effective-date {tc_opp[4]}/{tc_opp[3]} = {pct(tc_opp[4], tc_opp[3])},"
                 f" delisted-any-time {tc_opp[6]}/{tc_opp[5]} = {pct(tc_opp[6], tc_opp[5])}")
        c = nb_opp["cov"]
        out.line(tag, f"neutral delister coverage (A6, sessions of eligible opportunistic events): covered {c[1]} /"
                 f" in-span {c[0]} = {pct(c[1], c[0])} [{c[2]} delisted securities] | reported only:"
                 f" effective-date {c[4]}/{c[3]} = {pct(c[4], c[3])}, delisted-any-time {c[6]}/{c[5]} = {pct(c[6], c[5])}")
        out.line(tag, f"A4 effect: neutral rows before exclusion={nb_opp['rows_before_a4']} on {nb_opp['sessions']}"
                 f" sessions; excluded={nb_opp['a4_excluded_rows']} (issuer has its own event at E:"
                 f" {nb_opp['a4_excluded_rows_own_event']}; issuer-sessions {nb_opp['a4_excluded_issuer_sessions']});"
                 f" in-span delister rows excluded={nb_opp['a4_excluded_inspan_delister_rows']};"
                 f" pool per session after A4 median={nb_opp['median_pool']} min={nb_opp['min_pool']}")
        out.line(tag, f"classifiable share (otherwise eligible events, any kind): {all_cls}/{all_elig}"
                 f" = {pct(all_cls, all_elig)} | eligible routine={rout_elig} mixed={mixed_elig}")
        out.line(tag + " alt", f"treated = opportunistic+routine: treated delister {tc_tr[1]}/{tc_tr[0]}"
                 f" = {pct(tc_tr[1], tc_tr[0])}; neutral on all treated sessions {nb_tr['cov'][1]}/{nb_tr['cov'][0]}"
                 f" = {pct(nb_tr['cov'][1], nb_tr['cov'][0])}")

    out.line("A7", f"late bars in the window-cut read (known after the next session's entry cutoff): {n_late};"
             f" treated candidate pairs with one in the 20-session window={a7_t20}, in the 120-session window={a7_t120};"
             f" neutral candidate pairs: " + ", ".join(f"{k} 20={v[0]} 120={v[1]}" for k, v in neutral_a7.items())
             + " (expected 0)")
    out.put("a7", {"late_bars": n_late, "treated_20": a7_t20, "treated_120": a7_t120,
                   "neutral": {k: list(v) for k, v in neutral_a7.items()}})
    a7_fail = a7_t20 + sum(v[0] for v in neutral_a7.values())
    if a7_fail:
        out.line("A7", "WARNING: some pre-entry bars used are stamped after the entry cutoff; the liquidity stats"
                 " above include them (window-cut read). Stop: the owner decides before this counts.")

    out.put("strata", results)
    out.put("strata_alt_treated_opp_routine", alt_results)

    def verdict_of(res_map: dict[str, dict[str, Any]]) -> str:
        fails = []
        if not complete:
            fails.append("incomplete snapshot")
        for name, r in res_map.items():
            if r["n_upper"] < FLOOR_N:
                fails.append(f"{name}: {r['n_upper']} < 400 opportunistic events")
            if r["treated_dl_issuers"] == 0:
                fails.append(f"{name}: zero delisted issuers in the treated pool")
            if r["neutral_dl_issuers"] == 0:
                fails.append(f"{name}: zero delisted issuers in the neutral pool")
        if fails:
            return "FAIL (" + "; ".join(fails) + ")"
        missed = []
        for name, r in res_map.items():
            if r["entry_missing_share"] is None or r["entry_missing_share"] > ENTRY_MISSING_MAX:
                missed.append(f"{name}: entry-bar missing {r['entry_missing_share']}")
            if r["treated_cov"] is None or r["treated_cov"] < DELISTER_MIN:
                missed.append(f"{name}: treated delister coverage {r['treated_cov']}")
            if r["neutral_cov"] is None or r["neutral_cov"] < DELISTER_MIN:
                missed.append(f"{name}: neutral delister coverage {r['neutral_cov']}")
        if missed:
            return "PASS-SURVIVOR (" + "; ".join(missed) + ")"
        return "PASS-CLEAN"

    verdict = verdict_of(results)
    alt_verdict = verdict_of(alt_results)
    if a7_fail:
        verdict += " [A7 stamp failures > 0: not final]"
    if not complete:
        verdict += " [diagnostic run past a blocking ratio: not a result]"
    out.line("runtime", f"{time.time() - t0:.1f} s")
    out.line("VERDICT", verdict)
    out.line("VERDICT alt", f"with treated = opportunistic+routine for the coverage gates: {alt_verdict}"
             + (" (same label)" if alt_verdict.split(" ")[0] == verdict.split(" ")[0] else " (LABEL DIFFERS)"))
    out.put("verdict", verdict)
    out.put("verdict_alt", alt_verdict)
    conn.close()
    return verdict


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("store", type=Path, help="path to a COPY of the store (opened read_only=True)")
    p.add_argument("--json", type=Path, default=None, help="also write every count to this JSON file")
    p.add_argument("--continue-if-blocked", action="store_true",
                   help="compute diagnostic counts even if the ratio rule blocks (never a result)")
    p.add_argument("--i-know-this-is-the-live-store", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(list(argv) if argv is not None else None)
    store = args.store.resolve()
    if not store.is_file():
        print(f"no such store file: {store}", file=sys.stderr)
        return 2
    if store.name == "tradepartner.duckdb" and store.parent.name == "data" and not args.i_know_this_is_the_live_store:
        print("refusing: this looks like the live store; run on a copy", file=sys.stderr)
        return 2
    out = Out()
    st = store.stat()
    print(f"T342 recount | recount.py sha256={sha256_self()}")
    print(f"store={store} size={st.st_size} mtime={datetime.fromtimestamp(st.st_mtime, UTC).isoformat()}")
    print(f"cutoff={CUTOFF.isoformat()} window acceptances [{ACC_START.date()}, {ACC_END_EXCL.date()}) UTC")
    out.put("sha256", sha256_self())
    out.put("store", str(store))
    run(store, out, args.continue_if_blocked)
    if args.json:
        args.json.write_text(json.dumps(out.data, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
