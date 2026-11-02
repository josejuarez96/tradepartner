# Spec: the idea pipeline, the front half of the research loop

**Status:** Draft for the owner's review  ·  **Issue:** #2896  ·  **Date:** 2026-11-02  ·  **Related:** [research program](../ways-of-working/research-program.md) (the loop; stage 4a), [hypothesis backlog](../research/hypothesis-backlog.md) (the ladder ledger, the search limit), [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) (the kill ladder), [research registry](research-registry.md) (`economic` and `return` runs, family N), [strategy discovery 2026-10-19](../research/2026-10-19-strategy-discovery.md) (rung 2-O, rules L1 to L5), [Gate 0 reconciliation 2026-10-27](../research/2026-10-27-discovery-system-gate0.md), [retro 2026-11-01](../retros/2026-11-01-strategy-search-retro.md)

This document designs a process. It proposes no strategy, builds nothing and reads no return. The owner reviews it and decides the items in [Decisions for the owner](#decisions-for-the-owner).

## Problem & why now

TradePartner can tell whether a candidate strategy is real: point-in-time data, honest backtests, a trial registry, frozen kill lines, paper books as forward exams. Nothing produces candidates. Every idea tested so far was picked in chat from whatever data was handy, almost all from published anomaly files in large liquid stocks, the most crowded pool there is. Eight candidates, eight failures, and the team expected each to fail before running it ([retro](../retros/2026-11-01-strategy-search-retro.md)). The owner's goal is plain: start trading a profitable strategy. What he has been handed instead is lists of strategies with no process behind them, and a project that drifts into infrastructure between lists.

**The goal of the pipeline.** Supply the validator with a steady flow of candidates that each have a stated reason TradePartner could do them better than a professional, screened cheaply and counted honestly, so that the strict tests we already have are spent on ideas worth testing. The pipeline is the front half of the research loop: it ends where [research-program stage 4a](../ways-of-working/research-program.md#4a-feasibility-check-before-an-experiment-file) begins. It changes no rule of the back half.

**How the pipeline's own success is measured** (monthly, one table in the cards file, [Data / interfaces](#data--interfaces)):

| Measure | What it tells |
|---|---|
| Cards written, by source | The sources are being mined, not remembered |
| Cards admitted / blocked / deferred / rejected | The filter is working, and how harsh it is |
| Fast looks run, and survivors forwarded to the strict tier | Throughput, and the fast tier's kill rate |
| Strict-tier verdicts (pass, fail, deferred) and the `TP-` claims written | Learning that stays learned |
| Agent-days spent, and survivors per agent-day | Learning per effort |
| The capability tally: good cards blocked, per missing capability | What the build should do next |

Targets are in [Acceptance criteria](#acceptance-criteria-testable). A profitable strategy is the aim; it is not the measure of the pipeline, because a good process over a market that has nothing for a $100 account produces honest zeros. The stop rule in [Risks](#risks--domain-checks) says when honest zeros end the pipeline.

## Users & usage

- **The owner** reads one page a month (the measures above, the capability tally, the strict-tier queue), decides admissions he wants to overrule, and adds his own cards. About 30 minutes. He never runs a mining pass himself unless he wants to.
- **Agents** run the mining passes, write cards, run fast looks under a declared rule, and forward survivors to the strict tier exactly as today (a kill line posted on the issue before any return is read).
- **The orchestrator** schedules one mining pass per source per month, keeps the tally current, and refuses any read that has no card.

## Requirements

### 1. Every idea enters as a card, from a named source

An idea exists in the pipeline only as a **card**: about twelve lines in a fixed shape ([Data / interfaces](#data--interfaces)), written before any return is read. A card has a source, a mechanism, an answer to each filter question, the capabilities it needs and a verdict. The owner's own ideas get the same card and the same filter; so do an agent's and an AI workflow's. A card is the ledger's unit: the first look on any card adds its [ladder ledger](../research/hypothesis-backlog.md#ladder-ledger) row (rule L1), and every mining pass adds a batch row (rule L2) with the number of cards written and admitted, so the denominator is visible.

### 2. Five sources, each mined on a schedule, each kept from fishing

Mining means reading a finite, named body of material once, under a rule written before the reading, and writing a card for everything that passes the rule and a one-line reject for everything that does not. Nothing is "remembered"; nothing is "picked".

| Source | What is mined, how | One pass yields | Anti-fishing rule |
|---|---|---|---|
| **A. Literature** | The held Chen-Zimmermann catalogue (`SignalDoc.csv`, the two portfolio parquets, held locally since #2888) and, on request, one newer review. A pass reads **every** row once against one pre-declared rule written on the batch row before the read: size-screened long leg to 2019-12 (rule L4), the cost line of [ADR 0023 rung 1](../decisions/0023-break-first-fast-books-paper-exam.md) at the signal's turnover, and a counterparty answer (question 1 below). The 2026-10-30 ranking left this [INCOMPLETE](../research/2026-10-30-strategy-first-ranking.md); the pass finishes it. | A short list, every row counted; the catalogue's size is the batch denominator | One read per row, one rule per batch, the window ends 2019-12. A survivor's later DSR discloses the batch denominator (rule L3). Nobody reads a second series for a row that failed the first |
| **B. Own data** | A **data-edge census**: for each field the store holds that most retail researchers do not (acceptance times to the second for every filing, first-vintage statement facts, Form 4 rows, 8-K items once `filing_events` is filled, delistings, corporate actions), the question "what did someone have to do after this fact became known, and could we act before they did?" is asked field by field, written as a table with no return in it. The [store census](../research/2026-10-28-strategy-frontier-verified.md#census-re-derived) is the field list. *Illustrative only: "a 10-K accepted after the issuer's own usual date" was such a card (C1); it was tested and retired, which is what the process is for.* | One card per field × event pair that names a counterparty; the rest are one-line rejects | The census reads counts and dates, never returns. A card with no published series takes rung 2-O (an `economic` run, `touches_returns = false`) before any return read |
| **C. Market structure and forced flows** | A **flow calendar**: the scheduled or rule-bound trades of price-insensitive holders, read from the rule documents themselves (index methodology papers, ETF prospectuses, fund mandate language, tax and settlement calendars, lock-up and dividend rules), one card per flow: who must trade, on what date rule, how much, and who is paid to take the other side today. *Illustrative only: an index reconstitution that forces buying of the names added on a known date.* | A list of flows with dates and sizes; cards for those where the forced side is identifiable | The flow and its date rule are written before any price is looked at; the test is one declared event window. A flow already arbitraged by large funds fails question 2 and is rejected on the card, not tested |
| **D. The owner's knowledge** | What the owner reads, hears and notices, written as a card by him or dictated to an agent in his monthly sitting, plus his reaction to the capability tally ("that is worth building" or "not that"). | Cards; the admission decisions he wants to overrule | Same card, same filter, no shortcut. His word admits; it does not pass a filter question for the card |
| **E. Practitioner sources** | A weekly digest of a fixed list of practitioner feeds (quant blogs, strategy encyclopaedias, podcasts' show notes, enforcement actions that describe what was exploited), collected by the Firecrawl monitor the owner already has, read once a week by an agent who writes a card for any item that names a mechanism and a counterparty, and one line for the rest. | A few cards a month, each `UNVERIFIED` until a graded source or our own read backs it | The feed list is fixed and dated on the batch row; an item without a mechanism and counterparty is a one-line reject; a vendor's return figure is never a kill or pass number |

A sixth feeder is not a source: **post-mortems** of our own failures (B11: H1's holdout win was beta; B3's mega-cap shortfall; the C1 run-2 benchmark error) go to the owner's sitting as cards under source D, written by the agent that closed the item.

### 3. The first-pass filter: the questions every card answers

A cheap, no-return screen. Answering takes an hour, not a day. Every question is answered in one or two lines; an unanswered question is a `rejected` verdict, never a blank.

1. **Who is on the other side, and why do they keep losing?** One of: a forced or price-insensitive flow; a capacity limit that keeps large funds out; a horizon or career constraint (they cannot hold through it); a behaviour with a named reason it persists. "The market under-reacts" alone is not an answer. The eight failures all fail here, which is the point.
2. **Why can our account do this when a professional cannot?** The structural advantages a small account has are size (capacity), patience (low turnover, long holds), and no benchmark or career risk. The card names which one it uses. An idea that needs speed, scale or data we cannot get fails here. An idea that only works if we trade like a fund (large liquid names, daily turnover, market orders) fails here too, whatever the current books do.
3. **What does it cost at our size, and is the plausible edge at least twice that?** Holding period, one-sided turnover, round-trip cost at the measured figure for the names it trades (today +24.2 bp per side all orders, +10.6 bp in liquid names if buys went out at the open, [fill-cost note](../research/2026-10-30-paper-fill-cost-arrival.md)), against the plausible gross effect after the publication haircut (QI-2). This is ADR 0023 rung 1 and stage 4a (d), unchanged; the card writes the arithmetic down.
4. **Is the data there, point-in-time?** The causal timestamp is held as `known_at`; the event count per year is known or countable without returns; delisted names in the window are covered; how long a history it needs (2016+ is held; older is bought). This is stage 4a (a), (b), (f), answered before an experiment file, not instead of 4a.
5. **What has already been tried?** The nearest retired, parked or deferred item, by mechanism, with its ledger row, and what is genuinely new. A second look at a killed mechanism is allowed and must say so (the [earnings-family design](../research/2026-11-02-earnings-event-family-design.md) is the model: "this is B9 again, with four changes, and must be disclosed as one").
6. **Which capabilities does it need?** From a fixed vocabulary ([Data / interfaces](#data--interfaces)), each marked `held`, `self-imposed gap` or `external limit`. **The filter does not reject a card for a self-imposed gap.** Daily bars only, market DAY orders only, long-only in practice, a $200M liquidity floor and US equities only are design choices we made; Alpaca serves minute bars and SIP quotes, limit, opening and closing auction and stop orders, options and crypto. A card that needs one of those is verdict `blocked: <capability>` and feeds the tally. Genuinely external limits (price history starts 2016 unless bought; the charter's $100 live cap; a cash account for live shorts; no futures at the broker) are recorded as `external` and may reject.
7. **Is a kill decidable?** The cheap screen of 4a (c): the effective number of independent events at which t reaches 1.0 at the plausible effect, counted not computed. Too few events is `deferred: power`, because a kill would say nothing.

**Verdicts.** `admit` (goes to the fast tier), `blocked: <capability>` (a good card waiting on a build; counted in the tally), `deferred: <reason>` (data or power; re-enters when the reason is removed), `rejected: <question n>` (stays on file as part of the denominator). The owner may overrule any verdict in his sitting; the overrule is written on the card.

### 4. Two speeds, and the accounting that keeps the fast one honest

**The fast tier: one declared look, half a day at most.** An admitted card gets one read, declared on the card before it runs: the series or table, its construction, the window and the kill line, in one or two lines. The read computes one number and the card records pass or kill in five lines. No report, no issue thread, no SHA ceremony beyond the script path and its output file set read-only, the discipline the #2888 read already used. A fast look takes one of three forms, in cost order:

- **(a) on open data** (the held catalogue files, an open dataset): a ledger look (rule L1), not in any family's N, window ending 2019-12 (rule L4). Cards of one source read in one sitting share one declared rule and one script; the batch row carries the count, and every survivor's later file discloses it (rule L3).
- **(b) on held facts without returns** (rung 2-O): an `economic` research run with `touches_returns = false`; a ledger look, no cost to N.
- **(c) on store returns**: a registered `return` run, counted by configurations in the family's N ([research-registry req 9](research-registry.md)). The fast tier reaches for (c) only when (a) and (b) cannot decide the kill, because (c) spends the deflation budget the strict tier needs. A scratch read of store returns on a store copy without a registration is forbidden; it is exactly the uncounted look the ledger exists to prevent.

The fast tier can kill or forward. It can never promote, register a family, or spend a holdout. Expected survival is one in five or worse.

**The strict tier: unchanged.** A survivor goes where every candidate goes today: the stage 4a check recorded on its ledger row; a kill line frozen on its issue before any return is read; a registered run or a counted trial; ADR 0023 rungs 3 to 5 at the harsh settings; a `TP-` claim whatever the verdict; a forward paper exam for a root family with an unspent holdout. Nothing in this spec loosens the strict tier, and nothing lets the fast tier's number stand in for it.

**What is new in the accounting, exactly.** Nothing in the registry or the DSR. Three practices: (i) the batch rule, one declared rule per sitting over many cards, is the standard first look rather than one bespoke desk read per idea; (ii) a card is the unit of the ledger and carries the filter answers the ledger does not; (iii) the capability tally. Everything else (L1 to L5, 4a, req 9, the rung ladder) exists and is reused as written.

### 5. The capability tally drives the build order, not the reverse

Each month the tally counts `blocked` cards per missing capability, weighted by nothing. The owner reads it. A capability earns a spec only when it blocks several admitted cards (the threshold is his, [decision D3](#decisions-for-the-owner)), and only after at least one of those cards has passed a fast look that did not need the capability (an open-data or `economic` read). A build that no admitted card is waiting on is infrastructure drift and does not start from this pipeline.

### 6. Cadence and budget

One mining pass per source per month, each at most one agent-day; the fast looks of a month at most two agent-days in all; the owner's sitting 30 minutes. If a month's budget is spent, the rest waits for next month; the pipeline does not borrow from the strict tier's time.

## Acceptance criteria (testable)

- [ ] Given the spec is accepted, when the first mining passes run, then by 2026-12-15 (the current search-limit date) at least 20 cards exist on file with every filter question answered, at least 5 are `admit`, at least 2 have had a fast look under a batch-declared rule, and the tally shows the blocked count per capability.
- [ ] Given any fast look, when its card is read afterwards, then the declared read and kill line on the card predate the output file, the output file is read-only, and the batch row names the denominator.
- [ ] Given a card forwarded to the strict tier, when its experiment or hypothesis file is drafted, then its prior-evidence disclosure names the card, the batch denominator and every ledger look on the mechanism (rule L3).
- [ ] Given three monthly cycles (by 2027-02-15), when the measures table is read, then at least one strict-tier verdict exists on a card that came through the pipeline; if none does and the reason is not a capability gap, the stop rule below fires.
- [ ] Given the owner's monthly page, when he reads it, then he can answer "what came in, what was killed and why, what is waiting on which build" without opening another document.

## Out of scope

- Any specific strategy, candidate or ranking. The one example per source above is illustrative and is not a proposal.
- Automated candidate generation (grammar or genetic search, RL, an allocator, a blinded multi-arm benchmark): the external report's Gates 2 to 5, not adopted on 2026-10-27 over overfitting; this spec does not reopen them. If the owner wants to revisit, that is a separate decision after the pipeline has three months of measures to compare against.
- A new store, schema, table or dashboard page. The cards file and the tally are Markdown.
- The builds the tally may call for (order types, minute bars, a lower liquidity band, shorts live, older prices, options). Each gets its own spec when the tally and the owner say so.
- Changes to the strict tier, the registry, the DSR or the holdout rules.

## Data / interfaces

**The cards file**, `docs/research/idea-cards.md` (class A, living). Header: the measures table and the capability tally, updated at each fold. Body: one section per card, newest first. The card shape (twelve lines or so):

```
### <id>  <one-line mechanism>
source: A|B|C|D|E (<batch row date>)   ·   written: <date>   ·   by: owner | agent | workflow <id>
1 other side:        <who loses, why they keep losing>
2 our advantage:     size | patience | no benchmark; <why a fund cannot>
3 cost:              hold <n> sessions, turnover <x>/month, round trip <bp> at <band>; plausible gross <bp>; multiple <k>
4 data:              timestamp <field>; events/yr <n>; delisted coverage <ok | thin>; history <2016+ | pre-2016>
5 tried before:      <ledger id or none>; what is new
6 capabilities:      <list from the vocabulary, each held | self-imposed | external>
7 kill decidable:    n_eff <n> at <effect>; <yes | deferred: power>
verdict:             admit | blocked: <capability> | deferred: <reason> | rejected: <question>
fast look:           declared <series, construction, window, kill line>  →  <pass | kill, one number, date, output path>
ledger:              <row id>   ·   strict tier: <issue # or ->
```

**The capability vocabulary** (the owner's list of 2026-11-02; extend by editing the header):

| Capability | Values | Status today |
|---|---|---|
| bars | daily · minute | daily held; minute self-imposed (Alpaca serves them; the 2026-11-02 replay pulled them) |
| quotes | none · SIP NBBO | self-imposed (pulled ad hoc, not stored) |
| order types | market DAY · limit · OPG/CLS auction · stop | market held; the rest self-imposed (the adapter implements market only) |
| side | long · short (paper) · short (live) | long held; short paper allowed by ADR 0024 but no book yet; short live external (cash account) |
| liquidity band | ≥ $200M · $50–200M · < $50M | ≥ $200M is the registration default; lower bands self-imposed |
| instruments | US equities · options · crypto · futures | equities held; options and crypto self-imposed (broker offers them); futures external |
| history | 2016+ · pre-2016 | 2016+ held; pre-2016 external, purchasable (ADR 0022) |
| data field | <named field> | held · parse-only · not held |
| live size | ≤ $100 | external (charter) |

**The ledger.** No new column. The `link` cell of a ledger row names the card id; the card names the row. Batch rows are written as today (rule L2), one per mining pass, with the rule used.

**The research-program page** gains one paragraph at stage 3 ("an item enters the backlog from a card") and one line at 4a item (a) pointing at filter question 6. Class B, one PR, with this spec or after it.

**What exists and is reused, nothing rebuilt:** the ladder ledger and rules L1 to L5; stage 4a; the ADR 0023 ladder and the one rung-1 cost rule; the research registry's `economic` and `return` kinds and req 9; the event-study runner; the held Chen-Zimmermann files and `SignalDoc.csv`; the claims register and `TP-` claims; the search-limit note in the backlog; the owner's Firecrawl monitor; the store census and the frontier re-check as the field list.

**Minimal new work, with sizes and classes:**

| # | Work | Size | Class |
|---|---|---|---|
| 1 | This spec | S | B (owner's word) |
| 2 | `docs/research/idea-cards.md`: header (measures, tally, vocabulary, card shape) and the first batch of cards from the sources run first | S | A |
| 3 | Research-program amendment: one paragraph and one line, as above | S | B |
| 4 | Source A pass: finish the catalogue screen on the held parquets under a declared rule (completes the 2026-10-30 INCOMPLETE) | S | A (report + cards) |
| 5 | Source B pass: the data-edge census as a table over the held fields, no returns | S | A |
| 6 | Source C pass: the flow calendar from rule documents, cards for identifiable forced sides | M | A |
| 7 | Source E: the feed list and the first weekly digest | S | A |
| 8 | Later, only if the cards file passes about thirty cards: a tally script (`scripts/idea_cards.py`) that reads the file and prints the measures | S | A, not now |

No new dependency, table, schema version or page.

## Risks & domain checks

- **The market may hold nothing for us.** The honest prior from the register: post-2005 non-microcap anomalies earn about 0.07 %/month before costs (G2-S10); our cost is 24 bp per side. A good pipeline over that market produces honest zeros. That is why the pipeline measures itself on throughput and learning, and has a stop rule.
- **"Who loses" is a story filter, not a return predictor.** Chen, Lopez-Lira and Zimmermann found that mechanism stories do not predict which predictors survive ([discovery report §B3](../research/2026-10-19-strategy-discovery.md#b3-what-mechanism-first-admission-buys-and-what-it-does-not)). The filter is used for what it does buy: fewer looks, a cheap kill, a clear failure, and a reason to be in a trade a fund is not. It is not evidence.
- **The fast tier becomes mining.** Many cheap looks are how p-hacking happens. Mitigations already in the rules: one declared rule per batch, the denominator on the batch row, the 2019-12 window, disclosure at rung 4, no promotion from the fast tier, store returns only through a registered run. The residual risk is an agent reading a second series "to check"; the ledger's `looks` column records it when it happens, and a fold that finds `looks` above 1 says so on the owner's page.
- **The tally pulls the project back into infrastructure.** The owner's complaint is drift into building. Mitigation: a capability earns a spec only on several admitted cards and after one has passed a look that did not need it; the owner sets the threshold (D3). Capabilities blocking no admitted card are not built from here.
- **Forced-flow ideas are published too.** Index-rebalance and dividend-date effects are known and have shrunk. Question 2 (why a fund cannot) is where most of them should fail on the card; a card that passes must say which fund-sized actor is absent and why.
- **Forty months of store returns can only kill.** The development window is 2020-08 to 2023-12. Confirmation comes from paper (ADR 0023), which takes weeks for daily and weekly books and months for monthly ones. The pipeline cannot shorten that, and does not claim to.
- **Owner time.** Thirty minutes a month, one page. If the page takes longer to read, the pipeline is producing too much paper, not too many ideas.
- **Point-in-time and look-ahead.** The pipeline reads no store return outside a registered run, so it adds no look-ahead path. Fast looks on open data use windows ending 2019-12. Cards record `known_at` fields by name.
- **Secrets, orders, LLM inputs.** None touched. An AI workflow that proposes cards is a source like any other (batch row, card, filter); its output never reaches a run without the card and the declared read.

**The stop rule for the pipeline itself.** The pipeline runs for three monthly cycles from acceptance. At the third owner sitting (about 2027-02-15), if **either** fewer than five cards have been admitted in total, **or** no card has reached a strict-tier verdict for a reason other than a capability gap the owner has chosen not to build, the pipeline stops as designed, the measures table is the retro, and the question goes up a level: buy older data, change the market or instrument, or run the machine as a paper system only. The charter's six-month continue-or-stop criterion is not replaced by this; it is fed by it.

## Decisions for the owner

Each with a recommendation. One line back on any of them is enough.

- **D1. Adopt the card and the filter as the only way an idea enters.** Including your own ideas and anything an agent proposes in chat. *Recommendation: yes. The alternative is the chat-picked list that produced the eight.*
- **D2. How fast looks count against the search limit.** Today a candidate counts "once it reaches a rung-2 read". A batch look over a catalogue would consume the whole limit at once. *Recommendation: fast looks are counted in the ledger with their denominators and do not count against the 4-candidate limit; a candidate counts when it enters the strict tier (a kill line frozen on its issue). This is a change to the limit's counting rule and is yours to make.*
- **D3. The tally threshold.** How many admitted cards must a missing capability block before it earns a spec. *Recommendation: three, and at least one of them past a fast look that did not need it.*
- **D4. Which sources run first, and the budget.** *Recommendation: A, B and C in the first month (the catalogue screen, the data-edge census, the flow calendar), E from week one at a few minutes a week, D at your first sitting; the budget in requirement 6 (about five agent-days a month) is a cap, not a plan.*
- **D5. Store returns in the fast tier.** Whether a fast look may ever read store returns without a registration (a scratch script on a copy). *Recommendation: no, ever. Registered or open data only.*
- **D6. The pipeline's stop rule and date.** The numbers in the stop rule above (five admitted cards; one strict-tier verdict; about 2027-02-15). *Recommendation: as written.*
- **D7. The current limit's two named candidates.** The earnings event family (candidate 1, waiting on the `filing_events` run) stays as named. Whether candidates 3 and 4 of the limit are pre-named or taken from the pipeline's first strict-tier survivors. *Recommendation: from the pipeline; if it produces none by 2026-12-15, that result is the retro's first exhibit.*

## Open questions

1. Should an `UNVERIFIED` practitioner card be allowed a fast look on open data before any graded source backs it, or must it wait for a claim? The draft allows the look (it is counted) and forbids the strict tier until a graded or own-read claim exists.
2. Does the owner want cards for ideas the filter rejects at question 1 kept in the file for the denominator (the draft says yes, as one line each), or only counted on the batch row?
3. When the cards file outgrows reading by hand, does the tally script (work item 8) stay a script, or does the measures table move onto the dashboard's research view? The draft defers this until it is a problem.
