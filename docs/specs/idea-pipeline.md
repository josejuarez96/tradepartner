# Spec: the idea pipeline, the front half of the research loop

**Status:** Draft for the owner's review  ·  **Issue:** #2896  ·  **Date:** 2026-11-02  ·  **Related:** [research program](../ways-of-working/research-program.md) (the loop; stage 4a), [hypothesis backlog](../research/hypothesis-backlog.md) (the ladder ledger, the search limits), [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) (the kill ladder; the 2026-10-30 rung-1 cost rule), [research registry](research-registry.md) (`economic` and `return` runs, req 9, the 2026-10-31 exposure amendment), [strategy discovery 2026-10-19](../research/2026-10-19-strategy-discovery.md) (rung 2-O, rules L1 to L5), [Gate 0 reconciliation 2026-10-27](../research/2026-10-27-discovery-system-gate0.md), [retro 2026-11-01](../retros/2026-11-01-strategy-search-retro.md)

This document designs a process. It proposes no strategy, builds nothing and reads no return. The owner reviews it and decides the items in [Decisions for the owner](#decisions-for-the-owner).

## Problem & why now

TradePartner can tell whether a candidate strategy is real: point-in-time data, honest backtests, a trial registry, frozen kill lines, paper books as forward exams. Nothing produces candidates. Every idea tested so far was picked in chat from whatever data was handy, almost all from published anomaly files in large liquid stocks, the most crowded pool there is. Six candidates under the first search limit, and B3, B9, C1 and the momentum variants before it, all failed ([retro](../retros/2026-11-01-strategy-search-retro.md) section 1), and each carried a prior near zero when it was picked (the [backlog](../research/hypothesis-backlog.md) says so of itself: "the priors are deliberately low"). The owner's goal is plain: start trading a profitable strategy. What he has been handed instead is lists of strategies with no process behind them, and a project that drifts into infrastructure between lists.

**The goal of the pipeline.** Supply the validator with a steady flow of candidates that each have a stated reason TradePartner could do them better than a professional, screened cheaply and counted honestly, so that the strict tests we already have are spent on ideas worth testing. The pipeline is the front half of the research loop: it ends where [research-program stage 4a](../ways-of-working/research-program.md#4a-feasibility-check-before-an-experiment-file) begins. It changes no rule of the back half.

**How the pipeline's own success is measured** (monthly, one table in the cards file, [Data / interfaces](#data--interfaces)):

| Measure | What it tells |
|---|---|
| Cards written, by source | The sources are being mined, not remembered |
| Cards `admit` / `blocked` / `deferred` / `rejected` | The filter is working, and how harsh it is |
| Fast looks run, and survivors forwarded to the strict tier | Throughput, and the fast tier's kill rate |
| Strict-tier verdicts (pass, fail, deferred) and the `TP-` claims written | Learning that stays learned |
| Agent-days spent, and survivors per agent-day | Learning per effort |
| The capability tally: admitted cards blocked, per missing capability | What the build should do next |

Targets are in [Acceptance criteria](#acceptance-criteria-testable). A profitable strategy is the aim; it is not the measure of the pipeline, because a good process over a market that has nothing for a $100 account produces honest zeros. The stop rule in [Risks](#risks--domain-checks) says when honest zeros end the pipeline.

## Users & usage

- **The owner** reads one page a month (the measures table, the capability tally, the strict-tier queue), overrules the admissions he wants to, and adds his own cards. About 30 minutes, taken inside one of ADR 0023's weekly decision sittings, plus about a minute per exposure record written on the real store (an owner step under the 2026-10-31 amendment, or an agent at his word). He never runs a mining pass himself unless he wants to.
- **Agents** run the mining passes, write cards, run fast looks under a declared rule, and forward survivors to the strict tier exactly as today (a kill line posted on the issue before any return is read).
- **The orchestrator** schedules one mining pass per source per month, keeps the tally current, checks the exposure listing against the cards (`tradepartner exposures --candidates`, once that CLI ships), and refuses any read that has no card.

## Requirements

### 1. Every idea enters as a card, from a named source

An idea exists in the pipeline only as a **card**: about twelve lines in a fixed shape ([Data / interfaces](#data--interfaces)), written before any return is read for it. A card has a source, a mechanism, an answer to each filter question, the capabilities it needs and a verdict. The owner's own ideas get the same card and the same filter; so do an agent's and an AI workflow's. A card is the ledger's unit: the first look on any card adds its [ladder ledger](../research/hypothesis-backlog.md#ladder-ledger) row (rule L1), and every mining pass adds a batch row (rule L2) with the number of cards written and admitted, so the denominator is visible.

### 2. Five sources, each mined on a schedule

Mining means reading a finite, named body of material once, under a rule written before the reading, and writing a card for everything that passes the rule and a one-line reject for everything that does not. Nothing is "remembered"; nothing is "picked". Writing a card reads no return; the first return read, if any, is the card's fast look (requirement 4).

| Source | Body mined | One pass yields |
|---|---|---|
| **A. Literature** | The held Chen-Zimmermann catalogue (`SignalDoc.csv` and the two portfolio parquets, read locally since #2669, 2026-10-30) and, on request, one newer review. The pass finishes what the 2026-10-30 ranking left [INCOMPLETE](../research/2026-10-30-strategy-first-ranking.md). | Cards from the catalogue's descriptive columns; then one batch fast look over the admitted cards |
| **B. Own data** | A **data-edge census** over the fields the store holds that most retail researchers do not: filing acceptance times (complete for every form once T324 lands; partial before it), first-vintage statement facts, Form 4 rows, 8-K items once `filing_events` is filled, delistings, corporate actions. For each field: "who had to do something after this fact became known, and could we act before they did?" The [store census](../research/2026-10-28-strategy-frontier-verified.md#census-re-derived) is the field list. *Illustrative only: "a 10-K accepted after the issuer's own usual date" was such a card (C1); it was tested and retired, which is what the process is for.* | One card per field × event pair that names a counterparty; one-line rejects for the rest |
| **C. Market structure and forced flows** | A **flow calendar** of the scheduled or rule-bound trades of price-insensitive holders, read from the rule documents themselves (index methodology papers, ETF prospectuses, fund mandate language, tax and settlement calendars, lock-up and dividend rules). *Illustrative only: an index reconstitution that forces buying of the names added on a known date.* | One card per flow: who must trade, on what date rule, how much, who is paid to take the other side today |
| **D. The owner's knowledge** | What the owner reads, hears and notices, written as a card by him or dictated in his sitting, plus his reaction to the capability tally. Post-mortems of our own failures (B11: H1's holdout win was beta; B3's mega-cap shortfall; C1 run 2's benchmark error) come here too, written by the agent that closed the item. | Cards; his overrules |
| **E. Practitioner sources** | A fixed, dated list of practitioner feeds (quant blogs, strategy encyclopaedias, podcast show notes, enforcement actions that describe what was exploited), read once a week by an agent with the Firecrawl CLI it already uses by hand (`npx firecrawl`; development tooling, outside the charter's running-system budget; no scheduled job). | A few cards a month, each `UNVERIFIED` until a graded source or our own read backs it |

**Anti-fishing rules, one per source:**

1. **A.** Two steps, each counted. Step 1 writes cards from the catalogue's descriptive columns only (signal, paper, data category, construction); a script prints those columns and hides `Return` and `T-Stat`, which are in-sample returns. Because the row's own effect size is hidden, every step-1 card answers questions 3 and 7 with **one fixed prior for all rows**: the register's post-2005 non-microcap median, about 0.07 %/month gross (G2-S10), with the dispersion from the same source; a row-specific effect enters a card only after step 2's rule is posted. Rows already looked at (every retired item: AssetGrowth, ShareIss1Y, CompEquIss, CBOperProf, XFIN and the rest, from the ledger and, once T359 lands, the exposure table) are excluded from the pass; one may re-enter only as a disclosed second-look card under question 5. Step 2 is the batch fast look of requirement 4 (a): its rule is posted as a timestamped issue comment **before** `SignalDoc.csv`'s return columns or either parquet is opened, and it names the leg or legs the card trades (a long/short card reads both, as the XFIN read did; a short-leg card is not read long-only), the window 2010-01 to 2019-12 (ADR 0023 rung 2's start; rule L4's end), the floors (ADR 0023's rung-2 lines: at least 0.10 %/month and t at least 1.0 per leg read) and the break-even under the rung-1 rule at the signal's turnover. One read per row, one rule per batch; the batch row names the denominator; nobody reads a second series for a row that failed the first.
2. **B.** The census reads counts and dates, never returns. A card with no published series takes rung 2-O (an `economic` run, `touches_returns = false`) before any return read.
3. **C.** The flow and its date rule are written before any price is looked at; the test is one declared event window. A flow that large funds already arbitrage fails question 2 on the card and is not tested.
4. **D.** Same card, same filter, no shortcut. The owner's word admits a card; it does not answer a filter question for it.
5. **E.** The feed list is fixed and dated on the batch row; an item with no mechanism and no counterparty is a one-line reject; a vendor's return figure is never a kill or pass number.

### 3. The first-pass filter: the questions every card answers

A cheap, no-return screen. Answering takes about an hour. Every question is answered in one or two lines; an unanswered question is a `rejected` verdict, never a blank.

1. **Who is on the other side, and why do they keep losing?** One of: a forced or price-insensitive flow; a capacity limit that keeps large funds out; a horizon or career constraint (they cannot hold through it); a behaviour with a named reason it persists. "The market under-reacts" alone is not an answer. The candidates tested so far all fail here, which is the point.
2. **Why can our account do this when a professional cannot?** A small account's structural advantages are size (capacity), patience (low turnover, long holds) and no benchmark or career risk. The card names which one it uses and which fund-sized actor is absent, and why. The card fails here only when it cannot name one. Fitting the books' current shape (liquid names, daily turnover, market orders) is not by itself a reason to reject; needing speed, scale or data we cannot get is.
3. **What does it cost at our size, and does the edge clear it with margin?** Holding period, one-sided turnover, round-trip cost for the names it trades, against the plausible gross effect after the publication haircut (QI-2). The arithmetic is [ADR 0023 rung 1](../decisions/0023-break-first-fast-books-paper-exam.md) (needed edge = 2 × one-sided turnover × cost per side) and stage 4a (d) (plausible effect above 60 bp round trip), unchanged. The cost per side is the figure measured for the card's liquidity band under the [2026-10-30 rung-1 rule](../decisions/0023-break-first-fast-books-paper-exam.md#amendment-2026-10-30-2667-one-rung-1-cost-rule) (arrival, equal-weighted, in band, rebalance days), with the figure against the open beside it as that rule requires. Today only one band is measured: names over $200M, **+8.8 bp per side against arrival and +24.2 bp against the open** ([fill-cost note](../research/2026-10-30-paper-fill-cost-arrival.md), two rebalance days, paper simulator); both go on the card, and which one governs is part of [decision D9](#decisions-for-the-owner). Below $200M nothing is measured under the rule; the all-orders +24.2 bp of that note mixes bands and understates the cost of smaller names, so a card there writes `cost: unmeasured (<band>)`, and if its verdict turns on that cost it is `blocked: cost measurement (<band>)`, which the tally shows like any other missing capability. The margin the card must show above break-even is also D9.
4. **Is the data there, point-in-time?** The causal timestamp is held as `known_at`; the event count per year is known or countable without returns; delisted names in the window are covered; how long a history it needs (2016+ is held; older is bought). This is stage 4a (a), (b), (f), answered before an experiment file, not instead of 4a.
5. **What has already been tried?** The nearest retired, parked or deferred item, by mechanism, with its ledger row and exposure records, and what is genuinely new. A second look at a killed mechanism is allowed and must say so (the [earnings-family design](../research/2026-11-02-earnings-event-family-design.md) is the model: "this is B9 again, with four changes, and must be disclosed as one").
6. **Which capabilities does it need?** From a fixed vocabulary ([Data / interfaces](#data--interfaces)), each marked `held`, `self-imposed gap`, `charter (ADR)` or `external limit`. **The filter does not reject a card for a self-imposed gap.** Daily bars only, market DAY orders only, long-only in practice, a $200M liquidity floor: these are design choices we made, not facts about the broker. A card that needs one of those is verdict `blocked: <capability>` and feeds the tally. A capability the charter rules out (options, crypto, live shorts on the cash account, the $100 live cap) is `charter (ADR)`: the card is kept as `blocked` and the tally shows it, but only an ADR can unblock it. Genuinely external limits (price history starts 2016 unless bought; no futures at the broker) may reject.
7. **Is a kill decidable?** The cheap screen of 4a (c): the effective number of independent events at which t reaches 1.0 at the plausible effect, n = (sd / effect)², with the effect and its dispersion taken from the published source or the register's claim, never from held returns. Too few events is `deferred: power`, because a kill would say nothing.

**Admitted and the verdicts.** A card that answers questions 1 to 5 and 7 is **admitted**, whatever its capabilities. Its verdict is `admit` (every capability held; goes to the fast tier) or `blocked: <capability>` (a good card waiting on a build or an ADR; counted in the tally). The other verdicts are `deferred: <reason>` (data or power; re-enters when the reason is removed) and `rejected: <question n>` (stays on file, one line, as part of the denominator). "Admitted" below means `admit` plus `blocked`. The owner may overrule any verdict in his sitting; the overrule is written on the card.

### 4. Two speeds, and the accounting that keeps the fast one honest

**The fast tier: one declared look, cheap.** An admitted card gets one read, declared on the card before it runs: the series or table, its construction, the window and the kill line, in one or two lines, posted where it gets a timestamp (an issue comment or a commit). The read computes one number and the card records pass or kill in five lines. No report and no issue thread beyond that comment. The ceremony that stays is the one the 2026-10-31 exposure amendment requires: **every launch that computes a return outside a handle is one `research_exposures` record per card** (`candidate_ref` holds one candidate; `source = open_data` for the catalogue files, `store` for a store copy), with the kill-line URL and its hash, the probe path and SHA-256, `n_configurations`, `read_at` and the verdict. The exposure writer exists in `store/research.py`; until its CLI (research-registry plan T357) and backfill (T359) land, the record's fields are written on the card and recorded as the amendment's backfill section says. A fast look takes one of three forms, in cost order:

- **(a) on open data** (the held catalogue files, an open dataset): a ledger look (rule L1) and an `open_data` exposure record, which does not enter any family's N (the amendment's rule for `open_data`, its open question 1); window ending 2019-12 (rule L4). Cards of one source read in one sitting share one declared rule and one script; the batch row carries the count, and every survivor's later file discloses it (rule L3). *Estimate: half an agent-day per batch.*
- **(b) on held facts without returns** (rung 2-O): an `economic` research run with `touches_returns = false`; a ledger look, no cost to N, no exposure record (it reads no return).
- **(c) on store returns**: a registered `return` run, counted by configurations in the family's N ([research-registry req 9](research-registry.md)). A `return` run must name a family that exists in `hypotheses.families`, so (c) is open only to a card whose mechanism belongs to an existing family; adding a family is a strict-tier step, so a new-mechanism card decides its kill under (a) or (b) or goes to the strict tier. The fast tier reaches for (c) only when (a) and (b) cannot decide, because (c) spends the deflation budget the strict tier needs. A scratch read of store returns on a store copy outside a registered run, recorded only as exposure, is what the owner allowed on 2026-10-31; [decision D5](#decisions-for-the-owner) asks whether the fast tier is narrower than that.

The fast tier can kill or forward. It can never promote, register a family, or spend a holdout. *Estimate: one survivor in five or worse.*

**The strict tier: unchanged.** A survivor goes where every candidate goes today: the stage 4a check recorded on its ledger row; a kill line frozen on its issue before any return is read; a registered run or a counted trial; ADR 0023 rungs 3 to 5 at the harsh settings; a `TP-` claim whatever the verdict; a forward paper exam for a root family with an unspent holdout. Nothing in this spec loosens the strict tier, and nothing lets the fast tier's number stand in for it.

**One existing rule this spec asks to retire.** The ledger's **Upgrade path** ([backlog](../research/hypothesis-backlog.md#ladder-ledger)) has two triggers: a batch over 20 ideas, or more than one item a month reaching rung 1. Either one says: register the rung-2 read as a `return` experiment whose configurations count in N "once the family exists". The pipeline trips both by design. A catalogue pass is far over 20 rows and has no family. And question 3's break-even arithmetic is a rung-1 number, so under rule L1 every card that answers it gets a ledger row (the card is the row's content; this spec accepts that), and several cards a month will. The 2026-10-31 exposure amendment, written after the Upgrade path, records the same open-data read as `open_data` exposure outside N. The two rules overlap, and the newer one is the owner's. This spec proposes the amendment's rule for every open-data fast look from every source and retiring the Upgrade path's paragraph ([decision D8](#decisions-for-the-owner)). Until the owner decides, the Upgrade path stands and **no fast look of form (a) runs from any source**; forms (b) and (c) are registered runs and are not affected.

**What is new in the accounting, exactly.** Nothing in the registry, the exposure table or the DSR. Three practices: (i) the batch rule, one declared rule per sitting over many cards, is the standard first look rather than one bespoke desk read per idea; (ii) a card is the unit of the ledger and carries the filter answers the ledger does not; (iii) the capability tally. Everything else (L1 to L5, 4a, req 9, the exposure amendment, the rung ladder) exists and is reused as written.

### 5. The capability tally drives the build order, not the reverse

Each month the tally counts `blocked` admitted cards per missing capability. The owner reads it. A capability earns a spec only when it blocks several admitted cards (the threshold is his, [decision D3](#decisions-for-the-owner)), and only after at least one of those cards has passed a gate that did not need the capability: a fast look on open data, an `economic` rung 2-O read, or a graded claim in `claims.toml` (MIXED or better) for its mechanism. The third gate exists for cards that can only be tested with the capability (minute bars, intraday): without it they could never earn a build, which would bring back the rejection by current shape the owner ruled out on 2026-11-02. A build that no admitted card is waiting on is infrastructure drift and does not start from this pipeline. A `charter (ADR)` capability earns an ADR draft the same way, not a spec.

### 6. Cadence and budget

One mining pass per source per month, each at most one agent-day; the fast looks of a month at most two agent-days in all; the owner's 30 minutes inside one weekly sitting. If a month's budget is spent, the rest waits for next month; the pipeline does not borrow from the strict tier's time.

## Acceptance criteria (testable)

- [ ] Given the spec is accepted, when the first mining passes run, then by 2026-12-15 (the current search-limit date) at least 20 cards exist on file with every filter question answered, at least 5 are admitted, at least 2 have had a fast look under a batch-declared rule, and the tally shows the blocked count per capability. If D8 is declined, the fast-look item becomes at least 2 `economic` reads; if D2 is declined, it becomes at least 2 admitted cards queued for the strict tier with their declared reads written and unrun.
- [ ] Given a fast look of form (a), or any scratch read of store returns allowed under D5, when its exposure record is read (or, before T357 ships, the record's fields on the card), then `kill_line_basis` is `posted_before_read` (the kill line's comment `created_at` or commit time is at or before `read_at`), and the batch row names the denominator.
- [ ] Given a card forwarded to the strict tier, when its experiment or hypothesis file is drafted, then its prior-evidence disclosure names the card, the batch denominator, every ledger look on the mechanism and every exposure record id (rule L3; the exposure amendment).
- [ ] Given the third monthly sitting (about 2027-02-15), when the measures table is read, then the stop rule in [Risks](#risks--domain-checks) is applied on its two counts, and the outcome (continue or stop) is written on the page.
- [ ] Given the owner's monthly page, when he reads it, then it shows three things on one screen: the measures table for the month, the tally per capability, and the strict-tier queue (card id, issue, stage).

## Out of scope

- Any specific strategy, candidate or ranking. The one example per source above is illustrative and is not a proposal.
- Automated candidate generation (grammar or genetic search, RL, an allocator, a blinded multi-arm benchmark): the external report's Gates 2 to 5, not adopted on 2026-10-27 over overfitting; this spec does not reopen them. If the owner wants to revisit, that is a separate decision after the pipeline has three months of measures to compare against.
- A new store, schema, table or dashboard page. The cards file and the tally are Markdown; exposure records use the table the 2026-10-31 amendment already specifies.
- The builds or ADRs the tally may call for (order types, minute bars, a lower liquidity band, shorts live, older prices, options). Each gets its own spec or ADR when the tally and the owner say so.
- Changes to the strict tier, the registry, the DSR or the holdout rules.

## Data / interfaces

**The cards file**, `docs/research/idea-cards.md` (class A, living). Header: the measures table, the capability tally and the vocabulary, updated at each fold. Body: one section per card, newest first. The card shape (about twelve lines):

```
### <id>  <one-line mechanism>
source: A|B|C|D|E (<batch row date>)   ·   written: <date>   ·   by: owner | agent | workflow <id>
1 other side:        <who loses, why they keep losing>
2 our advantage:     size | patience | no benchmark; <which fund-sized actor is absent, and why>
3 cost:              hold <n> sessions, turnover <x>/month, cost/side <arrival bp> / <open bp> at <band> (or unmeasured); plausible gross <bp> (<source | fixed prior>); margin <k>
4 data:              timestamp <field>; events/yr <n>; delisted coverage <ok | thin>; history <2016+ | pre-2016>
5 tried before:      <ledger id, exposure ids, or none>; what is new
6 capabilities:      <list from the vocabulary, each held | self-imposed | charter (ADR) | external>
7 kill decidable:    n_eff <n> at <effect>; <yes | deferred: power>
verdict:             admit | blocked: <capability> | deferred: <reason> | rejected: <question>
fast look:           declared <series, construction, window, kill line; comment URL>  →  <pass | kill, one number, date, exposure id>
ledger:              <row id>   ·   strict tier: <issue # or ->
```

**The capability vocabulary** (the owner's list of 2026-11-02; extend by editing the header):

| Capability | Values | Status today |
|---|---|---|
| bars | daily · minute | daily held; minute self-imposed (Alpaca serves them; the 2026-11-02 replay pulled them) |
| quotes | none · SIP NBBO | self-imposed (pulled ad hoc for the cost notes, not stored) |
| order types | market DAY · limit · OPG/CLS auction · stop | market held; limit and stop self-imposed (the adapter implements market only); OPG/CLS self-imposed and **paper fills unproven** (every OPG order expired in Probe 3; the T374 probe's result is pending, and T375 builds only if it fills) |
| side | long · short (paper) · short (live) | long held; short on paper allowed by ADR 0024, no book yet; short live `charter (ADR)` (live stays long-only on the cash account until the live account decision) |
| liquidity band | ≥ $200M · $50–200M · < $50M | ≥ $200M is the registration default for daily and weekly books; lower bands self-imposed |
| cost measurement | per band, under the 2026-10-30 rung-1 rule | ≥ $200M measured (+8.8 bp arrival, +24.2 bp open; two rebalance days); lower bands unmeasured (self-imposed: no book trades them) |
| instruments | US equities · options · crypto · futures | equities held; options `charter (ADR)` ("no options trading"); crypto `charter (ADR)` (scope is US equities; the broker offers it); futures external (the broker has none) |
| history | 2016+ · pre-2016 | 2016+ held; pre-2016 external, purchasable (ADR 0022) |
| data field | <named field> | held · parse-only · not held |
| live size | ≤ $100 | `charter (ADR)` (the cap holds until the charter is amended) |

**The ledger.** No new column. The `link` cell of a ledger row names the card id; the card names the row and its exposure ids. Batch rows are written as today (rule L2), one per mining pass, with the rule used.

**The research-program page** gains one paragraph at stage 3 ("an item enters the backlog from a card; the card answers questions 1 to 7") and one line at the head of 4a pointing at the card's question 4 (the data items) and question 6 (capabilities). Class B, one PR, with this spec or after it.

**What exists and is reused, nothing rebuilt:** the ladder ledger and rules L1 to L5; stage 4a; the ADR 0023 ladder and the one rung-1 cost rule; the research registry's `economic` and `return` kinds, req 9 and the exposure amendment's table and writer; the event-study runner; the held Chen-Zimmermann files and `SignalDoc.csv`; the claims register and `TP-` claims; the search-limit notes in the backlog; the Firecrawl CLI; the store census and the frontier re-check as the field list.

**Minimal new work, with sizes and classes:**

| # | Work | Size | Class |
|---|---|---|---|
| 1 | This spec | S | B (owner's word) |
| 2 | `docs/research/idea-cards.md`: header (measures, tally, vocabulary, card shape) and the first batch of cards from the sources run first | S | A |
| 3 | Research-program amendment (one paragraph, one line) and, if D8 is yes, the backlog's Upgrade-path paragraph retired with a dated note | S | B (research-program) + A (backlog) |
| 4 | Source A pass: step 1 cards from the descriptive columns (script hides the return columns); step 2 the batch look under a posted rule, with its exposure records | S | A (report + cards) |
| 5 | Source B pass: the data-edge census as a table over the held fields, no returns | S | A |
| 6 | Source C pass: the flow calendar from rule documents, cards for identifiable forced sides | M | A |
| 7 | Source E: the feed list and the first weekly digest, by hand | S | A |
| 8 | Later, only if the cards file passes about thirty cards: a tally script (`scripts/idea_cards.py`) that reads the file and prints the measures | S | A, not now |

No new dependency, table, schema version or page.

## Risks & domain checks

- **The market may hold nothing for us.** The honest prior from the register: post-2005 non-microcap anomalies earn about 0.07 %/month before costs (G2-S10); our measured cost is +8.8 to +24.2 bp per side depending on the measure and the band, and unmeasured below $200M. A good pipeline over that market produces honest zeros. That is why the pipeline measures itself on throughput and learning, and has a stop rule.
- **"Who loses" is a story filter, not a return predictor.** Chen, Lopez-Lira and Zimmermann found that mechanism stories do not predict which predictors survive ([discovery report §B3](../research/2026-10-19-strategy-discovery.md#b3-what-mechanism-first-admission-buys-and-what-it-does-not)). The filter is used for what it does buy: fewer looks, a cheap kill, a clear failure, and a reason to be in a trade a fund is not. It is not evidence.
- **The fast tier becomes mining.** Many cheap looks are how p-hacking happens. Mitigations already in the rules: one declared rule per batch posted before the data is opened, the denominator on the batch row, the 2019-12 window, one exposure record per launch, disclosure at rung 4, no promotion from the fast tier, store returns only through a registered run (D5). The residual risk is an agent reading a second series "to check"; the ledger's `looks` column and the exposure listing record it when it happens, and a fold that finds `looks` above 1 or a record with no card says so on the owner's page.
- **The tally pulls the project back into infrastructure.** The owner's complaint is drift into building. Mitigation: a capability earns a spec only on several admitted cards and after one has passed a gate that did not need it; the owner sets the threshold (D3). Capabilities blocking no admitted card are not built from here.
- **Forced-flow ideas are published too.** Index-rebalance and dividend-date effects are known and have shrunk. Question 2 is where most of them should fail on the card; a card that passes must say which fund-sized actor is absent and why.
- **Forty months of store returns can only kill.** The development window is 2020-08 to 2023-12. Confirmation comes from paper (ADR 0023), which takes weeks for daily and weekly books and months for monthly ones. The pipeline cannot shorten that, and does not claim to.
- **Owner time.** Thirty minutes a month, one page. If the page takes longer to read, the pipeline is producing too much paper, not too many ideas.
- **Point-in-time and look-ahead.** The pipeline reads no store return outside a registered run (under D5), so it adds no look-ahead path. Fast looks on open data use windows ending 2019-12. Cards record `known_at` fields by name.
- **Secrets, orders, LLM inputs.** None touched. An AI workflow that proposes cards is a source like any other (batch row, card, filter); its output never reaches a run without the card and the declared read.

**The stop rule for the pipeline itself.** Two counts, read at the third monthly sitting (about 2027-02-15): **admitted cards in total**, and **strict-tier verdicts on cards that came through the pipeline**. Before the count, the owner may name capabilities he has chosen not to build; cards blocked on those leave the denominator and the first count is "admitted and not so blocked". If admitted < 5, or verdicts = 0, the pipeline stops as designed, the measures table is the retro, and the question goes up a level: buy older data, change the market or instrument, or run the machine as a paper system only. The charter's six-month continue-or-stop criterion is not replaced by this; it is fed by it.

## Decisions for the owner

Each with a recommendation. One line back on any of them is enough.

- **D1. Adopt the card and the filter as the only way an idea enters.** Including your own ideas and anything an agent proposes in chat. *Recommendation: yes. The alternative is the chat-picked list that produced the failures above.*
- **D2. How fast looks count against the search limit.** Today's rule: a candidate counts "once it reaches a rung-2 read or a registered event-study verdict". A fast look on the catalogue is a rung-2 read, so under today's rule a batch would consume the limit at once, and two fast looks by 2026-12-15 would use two of the three remaining slots. *Recommendation: fast looks are counted in the ledger and the exposure table with their denominators and do not count against the 4-candidate limit; a candidate counts when it enters the strict tier (a kill line frozen on its issue for a store read or a registered study). A rung 2-O `economic` read computes no return and is not a rung-2 read under either reading (the B7 precedent: published evidence read, no record, counted only when its study was registered). This changes the limit's counting rule and is yours. If the answer is no, the first month runs sources B and C (no-return census and calendar) and `economic` reads only, admitted cards queue for the strict tier with their declared reads written and unrun, and D7 falls away.*
- **D3. The tally threshold.** How many admitted cards must a missing capability block before it earns a spec, and which gate the cards must have passed first. *Recommendation: three, at least one past a gate that did not need the capability (an open-data look, an `economic` read, or a graded claim at MIXED or better).*
- **D4. Which sources run first, and the budget.** *Recommendation: A, B and C in the first month (the catalogue pass, the data-edge census, the flow calendar), E by hand from week one, D at your first sitting; the budget in requirement 6 (about five agent-days a month) is a cap, not a plan. If Firecrawl's cost is a concern, E waits.*
- **D5. Store returns in the fast tier.** On 2026-10-31 you allowed desk reads of store returns recorded as exposure. The fast tier proposes to be narrower: a store return is read only inside a registered `return` run (counted in N), never as a scratch script on a copy. *Recommendation: yes, narrower; registered or open data only.*
- **D6. The pipeline's stop rule and date.** The two counts above (five admitted; one strict-tier verdict; about 2027-02-15). *Recommendation: as written.*
- **D7. The current limit's two named candidates.** The earnings event family (candidate 1, waiting on the `filing_events` run) stays as named. Whether candidates 3 and 4 of the limit are pre-named or taken from the pipeline's first strict-tier survivors. *Recommendation: from the pipeline; if it produces none by 2026-12-15, that result is the retro's first exhibit.*
- **D8. Retire the ledger's Upgrade path.** Its two triggers (a batch over 20 ideas; more than one item a month reaching rung 1) both fire under this pipeline by design, and both say register a `return` experiment counting in N once the family exists; the 2026-10-31 exposure amendment records the same open-data read as `open_data` exposure outside N. *Recommendation: the amendment's rule for every open-data fast look from every source; retire the Upgrade path's paragraph with a dated note; a card's question-3 arithmetic gives it a ledger row under L1 and nothing more. Until you decide, no open-data fast look runs from any source.*
- **D9. The cost figure and the margin a card must show.** Rung 1 is break-even; 4a (d) is 60 bp round trip; the 2026-10-19 catalogue rule used three times the cost. For the one measured band the rule gives two figures, +8.8 bp against arrival and +24.2 bp against the open. *Recommendation: the arrival figure governs (it is the rule's measure) with the open figure shown beside it, as the rung-1 amendment says; the margin is twice the rung-1 break-even at that cost, written on the card; a card that clears break-even but not the margin is `deferred: margin`, not rejected; a card in an unmeasured band is `blocked: cost measurement`.*

## Open questions

1. Should an `UNVERIFIED` practitioner card be allowed a fast look on open data before any graded source backs it, or must it wait for a claim? The draft allows the look (it is counted and recorded) and forbids the strict tier until a graded or own-read claim exists.
2. Does the owner want cards for ideas the filter rejects at question 1 kept in the file for the denominator (the draft says yes, as one line each), or only counted on the batch row?
3. When the cards file outgrows reading by hand, does the tally script (work item 8) stay a script, or does the measures table move onto the dashboard's research view? The draft defers this until it is a problem.
