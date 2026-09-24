# 0003. Data and broker access go through adapters; local-first; paid price vendor deferred to Phase 3

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #5

## Context

Phase 1 originally required choosing a price-data vendor before building, because survivorship-bias-free prices decide whether a backtest can be trusted ([handoff](../research/2026-09-24-initial-research-handoff.md) D3, G8). That choice depends on a monthly budget the owner has not set. Vendor pricing has not been researched (G8 is open); the working assumption, unverified, is that any paid plan costs more per year than the ~$100 of planned live capital.

The owner wants to build and test the system's own logic first, locally, without depending on a vendor, and to be able to swap data sources later without rewriting the system. The parts that must be correct regardless of vendor are the point-in-time store, the security master, the calendar, the trial registry, the journal, the cost model and the backtester. None of them needs vendor data to be built or tested.

Two facts make a local-first approach honest rather than a shortcut:
- **Point-in-time correctness cannot be tested on real data**, because real data does not say what the right answer was. It is tested on synthetic fixtures where the answer is known.
- **SEC EDGAR is point-in-time by construction** (acceptance timestamps) and free. It includes Form 25 delisting notices (on EDGAR since the mid-2000s) and XBRL facts such as shares outstanding and public float, so a delisting record and a rough size measure can be built without a vendor. What free sources lack is *price history for delisted names*, especially the final decline before delisting, which is where survivorship bias in a momentum strategy lives.

## Options considered

1. **Pick the vendor now, build against real data.** Pro: real data from day one. Con: forces the budget decision before anything exists; couples the system to one vendor's schema; point-in-time tests would still need fixtures.
2. **Adapters with local defaults; vendor deferred** (this ADR). Pro: the owner's code is built and tested without spending; the vendor becomes a one-adapter change; the survivorship gap is bounded and documented rather than hidden. Con: Phase 3 backtests on free data are biased upward until a vendor is added, and the deferral must not become permanent.
3. **Free data only, permanently.** Pro: zero cost. Con: momentum backtests on survivors only overstate returns by an unknown amount; charter principle 4 ("including delisted names") is violated.

## Decision

We will define three interfaces in `src/tradepartner/` and put every external system behind one:

| Interface | Local default (Phase 2) | Later adapters |
|---|---|---|
| `PriceSource` | synthetic fixtures; Alpaca free market data | paid survivorship-free vendor (Phase 3 ADR) |
| `FilingSource` | synthetic fixtures; SEC EDGAR via `edgartools` | none expected; EDGAR is the primary source |
| `Broker` | in-memory fake broker | Alpaca paper (Phase 4), Alpaca live (Phase 6) |

### Rules

**1. Point-in-time storage.** Every stored record carries `known_at` (when the fact became knowable, UTC, tz-aware) and `ingested_at` (when we stored it) plus a source identifier. Prices are stored **raw** (unadjusted open, high, low, close, volume; the open is required because execution is at T+1 open per ADR 0006) alongside **corporate actions** (splits, dividends, ticker changes, delistings), each with its own `known_at`. Adjusted series are computed at read time "as of T", never stored as truth, because vendors rewrite adjusted history after the fact.

**2. Canonical timing rule.** A daily bar for session T has `known_at` equal to that session's close on the exchange calendar. A signal computed from session T's close may trade no earlier than session T+1. This rule lives in the core, not in adapters. An adapter whose source cannot supply a real `known_at` applies this rule and says so in its docstring.

**3. Security master.** Owned by us. Keyed by our own security ID, mapped to CIK and to dated ticker ranges, so a ticker reused by a new company never returns the old company's history. Populated from EDGAR (company facts, Form 25) plus adapter metadata. It is the single place that answers "what existed on date T". Adapters resolve securities through it, never by bare ticker.

**4. Fixture universe.** A checked-in synthetic dataset with at least: one delisting, one split, one ticker change, one restated fundamental (two `known_at` values for the same period), one exchange holiday and one half day. Two test layers:
- the **core no-look-ahead suite**, run against the fixture `PriceSource` and `FilingSource`;
- **per-adapter contract tests**, run against recorded responses, checking that every record has `known_at` and `ingested_at` filled per rules 1–2.

**5. Survivorship gap.** Tracked as two numbers, computed from filings dated before T and reported as a **lower bound**:
- *count share*: names in the security master that were listed on date T but are **missing price history**, divided by all names listed on T. A name is missing if it has no prices, or if its price history ends more than N sessions (config, proposed 5) before its Form 25 effective date;
- *size share*: the same, weighted by the last known `EntityCommonStockSharesOutstanding` (or public float) × the last price we have for that name, computed identically for survivors.

Both numbers appear on the Phase 2 data-health page and in every backtest result. The revisit threshold is on the **count** share (proposed 5% at any rebalance date), because the bias sits in small names.

**6. The holdout is not spent on free data.** Backtests on a `PriceSource` whose survivorship gap exceeds the threshold may not unlock the locked holdout. The single holdout run per hypothesis waits for a source below the threshold, or for an explicit owner decision, logged in the trial registry, to spend it anyway.

**7. Risk rules sit outside every broker.** The core sends orders only through a risk-gated wrapper around `Broker` (position limits, kill switch, idempotency). Adapters never contain or bypass risk logic, so swapping paper for live cannot skip it.

**8. Deferral has a deadline.** The price-vendor ADR, with the data budget, is due **at the start of Phase 3**, before the first registered hypothesis runs. It may record "no vendor", but then it must also amend charter principle 4 through the same ADR, and Phase 4 may not start until the owner has signed off on the gap in the trial registry.

### Verify before the Phase 2 plan

Unknown as of this ADR and to be checked in the plan, not assumed:
- Alpaca free plan: historical depth (believed ~2016), IEX-only vs consolidated daily bars, delisted-symbol coverage, corporate-actions depth, dividend data for SPY and MTUM total return, whether the terms allow local storage, whether the daily **open** is the official opening-auction price or an IEX print, and whether a fractional DAY order submitted before the open joins the opening auction or fills at NBBO afterwards. An account and API key are needed even for data.
- EDGAR: a declared `User-Agent` with contact details and a rate limit of about 10 requests per second.

## Consequences

- Good: the owner's code is testable and correct before any money is spent; vendor lock-in is limited to one adapter; the bias from free data is bounded and visible; the holdout stays clean.
- Bad / accepted risks: Phase 3 numbers on free data are optimistic and must not be used to size positions or claim an edge. Alpaca's free history (if ~2016) gives roughly nine usable years after the 12-month lookback, far below the ~16 years the handoff estimates to confirm a Sharpe of 0.5. `edgartools` has one maintainer; pin its version. The gap metric misses delistings the security master never learns about.
- Reversibility: cheap for adapters. The storage rules (1–3) are costly to change once data is loaded, which is why they are fixed here.
- Revisit if: the count-share gap exceeds the configured threshold at any rebalance date; the owner sets a data budget earlier than Phase 3; or Alpaca's terms or coverage fail the checks above.
