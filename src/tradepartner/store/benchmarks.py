"""Benchmark identity by symbol (#840).

A benchmark (`benchmarks` config: `SPY`, `MTUM`) is a reference series named
by a fixed symbol, not a member of the tradable universe. Owner decision on
#840 (2026-10-04): reading one goes **by symbol**, not through the
point-in-time listing gate of #35. Its `securities` and `listings` rows are
`snapshot_static` rows stamped at the snapshot fetch (SPY's at 2026-10-03),
so under the strict rule they are invisible at every in-sample `t`, while
its bars are known at each session's close. Naming a fixed instrument adds
no survivorship risk: no list of names is chosen with hindsight.

Only the **identity** is read this way. The bars stay point-in-time: every
caller reads them through `store.asof` at `t` (their own `known_at`, and the
adjustments known at `t`). And nothing here feeds `universe_as_of`, which
keeps its own rules (a benchmark is classified `etf` and is never a member).

**Fail closed on identity.** `benchmark_security_ids` maps each symbol to
the one security flagged `benchmark` (at its latest `securities` row) with a
listing under that ticker (any `known_at`; tickers compared trimmed and
upper-cased), and raises `BenchmarkIdentityError`, naming the symbol, when:

- no benchmark security lists the symbol (the store lacks it: the owner's
  one-off `tradepartner backfill-benchmark`, docs/runbooks/benchmark-backfill.md);
- more than one does (ambiguous);
- another security's listing history holds that ticker inside the window
  `[start, through]` (ticker reuse). Another security holds the ticker from
  a listing row's `valid_from` until its next row under a different ticker,
  or open-ended; `through=None` means open-ended too. Its listing ends are
  not consulted, so a reuse that ended by a delisting still refuses: the
  check errs toward refusing a run, never toward a wrong series.

A row withdrawn by a retraction (#859) is no evidence either way: a
`securities` row counts only when its latest revision is live, and a
listing row only when its key's latest revision is.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import date

import duckdb

from tradepartner.store.schema import has_retracted


class BenchmarkIdentityError(ValueError):
    """A configured benchmark symbol does not name exactly one security."""


def _live_listings(conn: duckdb.DuckDBPyConnection) -> str:
    """SQL for every `listings` key whose latest revision (any `known_at`) is
    not a retraction (#859), as one row: the latest. Below schema version 11
    there is no retraction, so every key."""
    live = "AND NOT retracted" if has_retracted(conn, "listings") else ""
    return f"""
    SELECT * EXCLUDE (n) FROM (
        SELECT *, row_number() OVER (
            PARTITION BY security_id, ticker, exchange, valid_from ORDER BY known_at DESC
        ) AS n
        FROM listings
    )
    WHERE n = 1 {live}
"""


def _latest_securities(conn: duckdb.DuckDBPyConnection) -> str:
    """SQL for each security's latest row with its `retracted` flag (FALSE
    below schema version 11)."""
    flag = "retracted" if has_retracted(conn, "securities") else "FALSE AS retracted"
    return f"""
            SELECT security_id, benchmark, {flag},
                   row_number() OVER (PARTITION BY security_id ORDER BY known_at DESC) AS n
            FROM securities
"""


def _norm(ticker: str) -> str:
    return ticker.strip().upper()


def benchmark_candidates(conn: duckdb.DuckDBPyConnection, symbol: str) -> list[str]:
    """Every security flagged `benchmark` at its latest `securities` row with
    a listing row (any `known_at`) under `symbol`, sorted."""
    rows = conn.execute(
        f"""
        WITH latest AS ({_latest_securities(conn)}), live AS ({_live_listings(conn)})
        SELECT DISTINCT l.security_id
        FROM live l JOIN latest s ON s.security_id = l.security_id AND s.n = 1
        WHERE s.benchmark AND NOT s.retracted AND upper(trim(l.ticker)) = ?
        ORDER BY l.security_id
        """,
        [_norm(symbol)],
    ).fetchall()
    return [sid for (sid,) in rows]


def ticker_holders(
    conn: duckdb.DuckDBPyConnection,
    symbol: str,
    *,
    start: date,
    through: date | None,
    exclude: str | None = None,
) -> list[str]:
    """Securities other than `exclude` whose listing history (any `known_at`)
    holds `symbol` at some day in `[start, through]` (module docstring), sorted."""
    rows = conn.execute(
        f"""
        WITH live AS ({_live_listings(conn)})
        SELECT DISTINCT security_id, upper(trim(ticker)), valid_from FROM live
        WHERE security_id IN (
            SELECT security_id FROM live WHERE upper(trim(ticker)) = ?
        )
        """,
        [_norm(symbol)],
    ).fetchall()
    history: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for sid, ticker, valid_from in rows:
        if sid != exclude:
            history[sid].append((valid_from, ticker))
    target, last = _norm(symbol), through or date.max
    holders: list[str] = []
    for sid, spans in sorted(history.items()):
        for valid_from, ticker in spans:
            if ticker != target or valid_from > last:
                continue
            ends = [day for day, other in spans if other != target and day > valid_from]
            if not ends or min(ends) > start:
                holders.append(sid)
                break
    return holders


def benchmark_security_ids(
    conn: duckdb.DuckDBPyConnection,
    symbols: Sequence[str],
    *,
    start: date,
    through: date | None = None,
) -> dict[str, str]:
    """Each of `symbols` to its one benchmark `security_id`, by symbol; raises
    `BenchmarkIdentityError` on a missing, ambiguous or reused symbol (module
    docstring). Reads identity rows only, never a bar."""
    out: dict[str, str] = {}
    for symbol in symbols:
        found = benchmark_candidates(conn, symbol)
        if not found:
            raise BenchmarkIdentityError(
                f"benchmark {symbol}: no benchmark security lists it in the store; the owner "
                "backfills it once (docs/runbooks/benchmark-backfill.md)"
            )
        if len(found) > 1:
            raise BenchmarkIdentityError(
                f"benchmark {symbol} is ambiguous: securities {', '.join(found)} all list it"
            )
        (sid,) = found
        others = ticker_holders(conn, symbol, start=start, through=through, exclude=sid)
        if others:
            window = f"{start.isoformat()}..{through.isoformat() if through else 'open'}"
            raise BenchmarkIdentityError(
                f"benchmark {symbol} ({sid}): ticker reused inside {window} by "
                f"{', '.join(others)}; refusing to read the series by symbol"
            )
        out[symbol] = sid
    return dict(sorted(out.items()))
