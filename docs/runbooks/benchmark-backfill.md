# Runbook: backfill a missing benchmark (MTUM)

Owner-run, once, on the real store. Agents never run it (it uses the Alpaca keys in `.env` and writes the owner's store). Issue [#840](https://github.com/josejuarez96/tradepartner/issues/840).

## Why

A backtest buys every configured benchmark (`benchmarks`, default `SPY` and `MTUM`) at F_0 and refuses a trial without them. Since #840 the backtest finds a benchmark **by symbol**: the one security flagged `benchmark` whose listing carries the ticker, whatever the master rows' `known_at` (owner decision on #840). Its bars stay point-in-time, each known at its own session close.

The master seeds a benchmark only from the SEC ticker snapshot, and **MTUM is not in it**. So the store has no MTUM security and no MTUM bars, and every trial fails with `benchmark MTUM: no benchmark security lists it in the store`. `tradepartner backfill-benchmark` seeds the missing security and fetches its Alpaca bars and corporate actions.

## The command

From the main checkout, on `main`, after the PR for #840 is merged, with no ingest running (the command waits `store.lock_retry_seconds` for a writer, then fails with `store busy`):

```bash
cd ~/Projects/tradepartner
uv run tradepartner backfill-benchmark MTUM --since 2016-01-01 \
  --cik 0001100663 --name "iShares MSCI USA Momentum Factor ETF" --exchange CBOE
```

- `--cik`, `--name` and `--exchange` are used only when no benchmark security lists the symbol yet. The values above are the iShares Trust's CIK and MTUM's fund name and primary listing (Cboe BZX). **Check them on the issuer's page or EDGAR before you run.** They are labels: no read uses MTUM's cik, name or exchange to select anything.
- `--since` is the first day fetched. Use the same start as the price backfill (2016-01-01). MTUM began trading in 2013, so every session from then on should have a bar.

What it does:
1. It refuses a symbol that is not in `benchmarks`. It also refuses one that another security already lists, or whose `BENCH:<symbol>` id is taken. A refusal exits 2, with nothing written and nothing fetched.
2. If the store has no MTUM benchmark security, it writes `BENCH:MTUM` (`benchmark = TRUE`, source `config`), plus a `snapshot_static` listing from the calendar's first session. Both rows are stamped now, the same as the master's own SPY rows.
3. It fetches MTUM's bars and actions one calendar month at a time, up to the expected session, and writes them by the ingest rules. Bars keep their session-close `known_at`. A rerun adds nothing it already has.
4. It prints `MTUM: BENCH:MTUM seeded` (or `already in the store`), then one `alpaca: <status>, <rows> rows, …` line per month. It exits 0 when every month is `ok`.

**It writes no `ingestion_runs` row.** An `ok` row would read as a fresh store to the paper-run and dashboard freshness checks, but this command refreshes only one series. The printed lines are the only record, so keep them.

## If a month is `stale` or `failed`

A month where any session has no bar is `stale`. Nothing is written for that month and the run stops there; earlier months stay. Read the message, which names the sessions. To resume, rerun the same command: committed months add 0 rows, so a rerun is safe. If Alpaca simply has no bar for a session, record that on #840 and ask before you narrow `--since`.

## Afterwards

- `uv run tradepartner health` should list MTUM among the benchmark names with a bar at the last session. The daily ingest now fetches it like SPY, because benchmarks are always fetched.
- Rerun the H1 in-sample trial (#839). A trial still refuses a benchmark whose ticker another security holds inside the run window, or which two benchmark securities both list. The message names the symbol.
