# Research Report: why the store holds no 2016-2018 delisters (delisting coverage attribution)

**Brief:** #2913 (owner-approved read-only probe)  ·  **Date:** 2026-11-02  ·  **Status:** COMPLETE  ·  **Agent/model:** team probedelist, Claude Opus 5.5

No production change, no schema change, no store write, no backtest, no strategy evaluation, no return read. The store copy was opened read-only. Nothing under `data/` was opened. Licensed Sharadar data was not read.

## Answer

**Outcome: A, an identity bottleneck.** The bars exist at Alpaca. The store has no listing for these companies, so the backfill never asked Alpaca for them.

- Of 982 first common-stock exits from NASDAQ, NYSE and NYSE American in 2016-2018 (Form 25-NSE), **925 (94%) have no listing known at their Form 25**. Every one of them has a `securities` row.
- **0 of 844 genuine-looking exits have a positive-volume bar.** The 57 names that do have bars all kept trading (transfers, reorganisations).
- In a fresh Alpaca sample of 30 Nasdaq exits from that no-listing group (tickers from the Form 25 exhibit; no NYSE name was sampled), **28 of 29 genuine exits return bars up to the Form 25** when the request carries `asof`. INVT has none; the 30th, AVGO, is a redomicile, not an exit.
- A small part is our own ingestion (outcome C), and none of it is in 2016-2018. Across the whole store, 25 delisted common securities have a listing but no bars. 22 of them are old holders of a ticker that another company took later. Alpaca serves all 9 that were sampled under `asof`.

**The fix is already built and the owner has already chosen it.** It is T184h, release `pre2019-listings-1597`: size S, owner-run. The code for T184 to T184g and T199 is merged. On 2026-10-31 the owner answered #2670 question 3 with (a): un-pause T184h after the T308 sitting ([#836](https://github.com/josejuarez96/tradepartner/issues/836)). It is **not "recoverable but too slow to matter"**. The release is one sitting: hours of EDGAR fetches, then an `asof` backfill. EODHD (about $20 a month) would not help. It sells bars, and bars are not what is missing. What is missing is a ticker and exchange known at the filing date, and a vendor's current ticker list cannot supply that (section 5).

**Cards whose verdict would change: FC-1 only.** It would no longer fail the power screen at the published +2% (an upper bound, not a measured count) (8 June dates, at most about 80 effective events, against 36 needed). It would stay deferred at +1% (144 needed). FC-2, FC-5 and the source B cards do not change (section 1).

## 1. Step 1: would a longer clean window change any card?

This uses each card's own arithmetic (PR #2910, [idea-cards.md](idea-cards.md)). The clean window today is 2020-08-31 to 2023-12-29. Extending it back to 2016-01 adds the event dates from 2016 to mid-2020.

| Card | Need | Today: dates → effective events | From 2016-01: dates → effective events | Verdict change? |
|---|---|---|---|---|
| FC-1 Russell R2000→R1000 (≤ 10 effective per date) | 36 at +2.0%; 144 at +1.0% | 3 Junes (2021-2023) → ≤ 30 | 8 Junes (2016-2023) → ≤ 80 | **Yes at +2.0%** (≤ 80 ≥ 36). No at +1.0%. Adding only June 2019 and June 2020 already gives 5 dates → ≤ 50 |
| FC-2 Nasdaq-100 December additions (about 3.5 per date) | 64 at +1.0% | 4 Decembers → about 14 | 8 Decembers (2016-2023) → about 28 | No |
| FC-5 December tax-loss (≤ 7 per date) | 225 at +1.0% | 3 Januaries → about 20 | 7 Januaries (2017-2023; January 2016 needs 2015 closes) → about 49 | No |
| IC-B3 merger arbitrage (source B) | about 160 | 2019-2023: ≤ 45 at ≥ $200M, ≤ 173 with $50-200M | 2016-2023 at ≥ $200M, scaled: about 70 | No. It stays `blocked: data field`, and the power line stays marginal |
| IC-B1, IC-B2, IC-B4, IC-B5 | n/a | rejected on question 3, or blocked on a field | same | No |

Ledger items: C1 is retired by the owner, and C2's spin-off history is limited by `corporate_actions` (2020-11 on), not by delisters. Neither is waiting on this probe.

FC-1 would still be gated after the change:
- by the cost of the $50-200M band, which is unmeasured;
- by a Russell membership rebuild, whose first year needs a prior-year membership;
- by its own window note: the Dimensional 2014-2023 result has already seen 2016-2023;
- by the in-sample start of 2020-08. That start is a registry boundary, so using 2016-2020 dates is the owner's decision for the family.

So step 1 matters for one card, and steps 2 and 3 were run.

## 2. Baseline manifest

| Item | Value |
|---|---|
| Store copy | `scratchpad/replay/store-20261102.duckdb` (taken 2026-11-02 morning), opened `read_only=True` |
| Schema | 41 (`max(schema_version.version)`) |
| Repo commit | `c45360d7` (origin/main at branch time) |
| `securities` | 63,583 |
| `listings` | 22,674: `filing` 17,196 (`valid_from` 2019-06-11 on), `snapshot_static` 5,459 (all `known_at` 2026-10 or later), `snapshot` 17, config 2. **No `filing_name` row** (T184h has not run) |
| `delistings` | 3,157 (2016: 19, 2017: 19, 2018: 16 by filing year) |
| `prices_daily` | 13,721,968 rows on 8,867 securities, 2016-01-04 to 2026-10-30. Last full Alpaca backfill run: 2026-10-14 |
| Securities whose last positive-volume bar falls in 2016 / 2017 / 2018 | 4 / 3 / 3 |
| Outside reads | EDGAR full-index `form.idx` for 2016Q1-2020Q4 (20 requests) and 4,639 Form 25/25-NSE submission texts, through `edgar_raw._get`. Alpaca: 39 symbols × 2 requests, through `alpaca_raw.daily_bars`. No key was printed |

Scripts and outputs are in the session scratchpad (`probedelist/scripts/`: `fetch_form25.py`, `funnel.py`, `waterfall.py`, `alpaca_sample.py`). They are not in the repo.

## 3. Step 2: the funnel for 2016-2018 Form 25-NSE exits

**Population.** All Form 25-NSE filings accepted in 2016-2018, with the exchange's duplicate copy dropped (the rule of `accession_rows.issuer_delistings`). Each was parsed with the repo's `parse_delisting`, giving 2,374 filings.
- 1,399 are equity by `classify.listing_kind`. Keeping the first per CIK and exchange leaves 1,121.
- 1,046 of those are on NASDAQ, NYSE or NYSE American. The other 75 are Arca and Cboe ETFs and trusts.
- 982 have a common-stock title (master's `_is_common`). The other 64 are ADSs, shares of beneficial interest and similar. **The denominator is these 982** (2016: 390, 2017: 310, 2018: 282).

#1311 counted 939 with a slightly different equity filter; the gap does not change any conclusion. Separately, there are 302 issuer-filed Form 25s from 2016-2018, from 209 CIKs with no 25-NSE. They are HTML, which the repo skips by design (`pre_xml_delistings`). They are not in the denominator (section 6).

| Step (reason code) | Dropped | Remaining | Note |
|---|---|---|---|
| Population: first common exit per CIK × exchange | | 982 | |
| 1 never discovered (no `securities` row for the CIK) | 0 | 982 | Every CIK is in `securities` |
| 2 discovered without a listing (no listing row of any kind) | **913** | 69 | No listing before 2019-06 exists for anyone. The Alpaca backfill only asks for listed spans, so these were never requested |
| 3 listing rejected, or known only after the Form 25 | 12 | 57 | Each is a later life of the CIK: a preferred class, an OTC row, a successor's ticker, or a `snapshot_static` OTC row known in 2026. 10 of the 12 kept filing. None is a usable historical listing. No `master_underived` or retracted row belongs to these CIKs |
| 4 Form 25 not mappable to an eligible common class on its exchange | 0 | 57 | |
| 5 identified, but no bars stored | 0 | 57 | |
| 6 bars retrieved but discarded, rejected or quarantined | not observable offline | | The raw payloads are under `data/`, which was not read. Zero-volume placeholders stored for these names: 0 (dropped by design) |
| 7 bars present, Form 25 unmatched (terminal outcome unresolved) | 24 | 33 | All 24 kept trading (20 have bars after the Form 25) or kept filing: transfers and reorganisations |
| 8 bars present and a matched `delistings` row | | 33 | All 33 kept trading or filing: transfers, not exits |

**Genuine exit or transfer.** A name counts as "continued" when its CIK files a 10-K or 10-Q, or a Form 4 names it as issuer, more than 180 days after the Form 25, or when it has a bar more than 30 days after it. This is a heuristic: it misses a redomicile under a new CIK, such as AVGO in the sample below. By that test, 81 of the 925 no-listing names continued and **844 look like genuine exits. 0 of the 844 have a bar.**

**A ticker known at filing time, for the 925 no-listing names.** These are the two sources T184 reads.
- 483 have the ticker in the Nasdaq Form 25 exhibit file name. 448 of them also have a 10-K or 10-Q filed in 2015 or later, before the Form 25. The other 35 (34 of them genuine exits) have a ticker only from the Form 25 itself, so it is known only at the exit: enough to end a span, not to place the name in a universe before it.
- 323 more have only such a 10-K or 10-Q, so the ticker can come only from the XBRL instance file-name prefix. That route matched 10 of 10 spot-checked delisters on #1597 and 36 of 40 survivors.
- 119 have neither: mostly 2016 exits whose last periodic filing predates 2015, and NYSE names. These stay out even after T184h unless its `filename_listings_since` reaches back further.
- By exchange: NASDAQ 486, NYSE 381, NYSE American 58.

**2019 and 2020 (the secondary window).**

| Year | Common exits | No listing (codes 2+3) | Listing but no bars (5) | Bars, Form 25 unmatched (6) | Bars and a delisting row |
|---|---|---|---|---|---|
| 2019 | 313 | 239 | 2 | 34 (18 end before the Form 25) | 38 |
| 2020 | 263 | 96 | 7 | 93 (79 end before the Form 25) | 66 |

The 2019 hole is the first half of the year: cover-page listings start in 2019-06. The "ends before the Form 25" rows are bars whose terminal outcome is unresolved. They are T326 to T329's question (delisting reasons), not this probe's.

**The C1 population, kept apart from the denominator.** 1,306 CIKs filed a 10-K or 10-Q in 2017 and none after 2019-06-30 (#1311's figure is 1,301, with a slightly different form filter).
- All 1,306 are in `securities`. 13 have any listing row (5 `filing`, 9 `snapshot_static`, all later lives). 4 have positive-volume bars. 3 have a `delistings` row.
- 622 filed a Form 25 or 25-NSE of any class in 2016-2020. 551 of them are in the common exchange-exit population above.
- **684 have no Form 25 at all**: OTC and non-listed registrants. Alpaca's SIP feed has no OTC tail and no exchange universe holds these names, so neither T184h nor a bar vendor changes them for an exchange-listed test.

**Why each missing name has no bars (offline price check).**
- 2016-2018: absent listing, all 925. The other codes are empty.
- 2019-2020: absent listing (335), and listing present but no bars (9). There is no rejected-span or quarantine code to attribute, because the run messages and the raw cache under `data/` were not read.
- The 9 "listing but no bars" names, and 25 delisted common securities store-wide, share one pattern. 22 of the 25 are old holders of a ticker that another company took later (AVX, STI, GV, GCI, CZR, MTCH, NTRP, GLIBA, WUBA). The per-span `asof` resolver rule (T192, #1696, merged 2026-10-16) postdates the store's last full backfill (2026-10-14), so these spans were most likely never asked with `asof`. That is unverified, because the run payloads were not read. T198's `--backfill --fill-holes` run with `asof`, already in the planned data sitting, should cover them.

## 4. Step 3: a small live Alpaca sample, to separate Alpaca from ingest

Two deterministic groups, through the repo's `alpaca_raw.daily_bars` and `get_settings()`. Each symbol was asked twice, with no `asof` and with `asof` = Form 25 date − 7 days, over a window from 120 days before the Form 25 to 30 days after. Positive-volume bars only.

| Group | n | Bars without `asof` | Bars with `asof` | Note |
|---|---|---|---|---|
| Code 5: listing present, no bars stored (2019-2020) | 9 | 7 (3 with another company's tail: GCI, CZR, NTRP) | **9 of 9** (8 end at their last session; MTCH's run past its Form 25 into the next company's prices) | STI and GLIBA return nothing without `asof`. Alpaca has every one, so the miss is ours (outcome C) |
| No listing: Nasdaq genuine-looking exits, 10 per year 2016-2018, seed 2913, ticker from the Form 25 exhibit | 30 | 29 (NILE contaminated: Blue Nile's ticker returns a later holder's prices, last close 0.83 against 40.71) | 29 (INVT none; TW has one bar, because its exit is on Alpaca's first day) | 28 of the 29 real exits are recovered, so the miss is identity (outcome A). AVGO is a redomicile the "continued" heuristic missed |

This matches #1311 (47 of 48 real exits under `asof`) and #1597 (51 of 51). Alpaca's coverage did not change between 2026-10-12 and today.

## 5. What it takes, and the vendor comparison

**Minimum work.** Run **T184h** (owner, size S, already built), in the post-span data sitting beside T198, as its plan line allows. Afterwards, re-run this probe's funnel on the released store, read-only, as an XS check. Expected result: up to about 725 of the 844 genuine 2016-2018 exits gain a filing-time listing: about 691 known before the exit (a periodic filing's XBRL prefix) and 34 known only at the exit (the Form 25 exhibit alone). The 119 with neither source stay out. Each gets bars through the existing `asof`-per-span backfill and a Form 25 end. #1597's sample gives roughly 90% or more of those with bars.

Not counted: T184h's own stop rules, the collision deny-list, and the step-6 retractions. The T198 sitting's `--fill-holes` run covers the 25-security residual described in section 3. FC-1 still needs its own no-return count of rebuilt migrants after that.

**EODHD (about $20 a month, delisted EOD bars, all of them before 2018).** It does not close the gap:
1. Bars are not missing. Alpaca serves 28 of 29 sampled genuine exits (Nasdaq only).
2. A vendor's ticker for a delisted name is today's snapshot. Using it as a historical listing is look-ahead unless a filing confirms it, and that confirmation is T184's rule.
3. ADR 0022 says no vendor in the store, and its revisit trigger is not met.

So the vendor costs money and still needs T184h's identity work.

## 6. Earlier conclusions that are now obsolete

- **#1597's "only 47 of 779 2016-2018 delisters have bars":** in today's store, every 2016-2018 common exit with bars kept trading. **0** genuine exits have bars.
- **The plan's T184h "paused" note and STATUS's pause list:** the owner un-paused T184h on 2026-10-31 (#2670 question 3 (a)). The plan line still carries the 2026-10-18 note.
- **#1311's "without a `TradingSymbol` there is nothing to ask Alpaca (54%)":** T184's sources (the Nasdaq exhibit and the XBRL file-name prefix) reach 806 of 925 no-listing names, not 54%.
- **#1314's "`alpaca_raw.daily_bars` sends no `asof`":** it does now (the per-span `asof` rule).
- **FC-1's re-entry note, "a membership history that includes delisters (bought, or pre-2016 history under ADR 0022)":** for 2016 onward, nothing needs to be bought. T184h supplies the delisters.

## 7. Limits

- The "continued" test is a heuristic. A redomicile under a new CIK (AVGO) reads as an exit, and a debt-only filer reads as continued.
- "Listing rejected" cannot separate the confirmation rule's refusals from a reading that was never made, because no `filing_name` row exists yet.
- Bars dropped after fetch (step 6) were not observable without the raw cache under `data/`.
- 302 issuer-filed Form 25s (HTML, from 209 CIKs with no 25-NSE) are outside the denominator. The repo does not parse them, so such an exit would be dated only by the stale-listing rule.
- The FC-1 counts are the card's own upper bounds (≤ 10 effective per date), not measured counts.
