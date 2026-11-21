# 0005. Objective, benchmarks and stop criteria

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #7

## Context

The [charter](../charter.md) left the objective, benchmarks and stop criteria open. The [handoff](../research/2026-09-24-initial-research-handoff.md) makes two facts unavoidable:

- **An edge cannot be proven at this scale.** Sampling error on a Sharpe ratio gives t ≈ SR × √years (D1). Confirming an excess Sharpe of 0.3 over a benchmark takes ~44 years; around 100 trades is mostly noise (§4).
- **Long-only momentum already exists as a product.** MTUM charges 0.15%/year for roughly the MVP strategy (H1). A homemade version beating it after costs and taxes is unlikely.

So a P&L objective would be unmeasurable, and a benchmark-beating objective would be a bet against a cheap ETF. What *is* measurable within the project's life is whether the system is correct, honest and disciplined.

## Options considered

1. **Beat a benchmark.** Pro: simple to state. Con: unmeasurable in the available time; invites strategy-hopping after noise; the research says the retail edge, if any, is defensive.
2. **Learning and process quality, with benchmarks as instruments** (this ADR). Pro: every criterion is checkable by a command; matches the charter's purpose; the benchmarks still expose a broken system. Con: it is possible to "succeed" while losing money, which must be stated plainly.
3. **Calibration only** (Brier scores on probabilistic calls). Pro: rigorous. Con: needs the LLM layer or human forecasts, both deferred; too narrow on its own.

## Decision

**Primary objective, ranked:** (1) learn to make disciplined, evidence-based decisions, demonstrated by process metrics; (2) calibration, once anything in the system emits probabilities; (3) beating a benchmark, explicitly optional and not a criterion for any phase exit. *(Amended 2026-11-21, #3695: how objective 3 reads for one strategy book is recorded in the [amendment of 2026-11-21 below](#amendment-2026-11-21-what-a-strategy-book-is-for-core-and-satellite-3695), on the owner's choice among three options.)*

**Benchmarks:** SPY and MTUM, both total return (dividends reinvested), computed inside the system from the same `PriceSource` and with the same cost model as the strategy. They are measured against, never targeted. A backtest or paper result is reported only alongside both. *(Amended 2026-11-20, #3686: the series a book's excess is judged over is its **yardstick**, the net-matched blend of SPY and T-bills for a book with a short leg. It is SPY for every long-only book, so nothing changes there, and SPY and MTUM are still reported beside every result, [amendment below](#amendment-2026-11-20-the-yardstick-is-the-net-matched-blend-3686).)*

**Success criteria** are the phase exit criteria in the [roadmap](../roadmap.md). In addition, at the end of Phase 4:
- after at least **6 paper rebalances** (`paper.min_rebalances`), the **maximum absolute difference** between the paper and backtest monthly returns on the same dates is ≤ `paper.tracking_k` × the modeled cost per rebalance (proposed k = 2), excluding months flagged as missed rebalances, which are reported separately; *(amended 2026-10-09, [ADR 0017](0017-fast-paper-and-machine-readiness-gate.md) part A: this criterion is reported per paper book, in rebalance periods of the book's cadence, for as long as the book runs; it leaves the Phase 4 exit, which is the machine-readiness shakedown of ADR 0017 part E; it stays required for a forward exam (ADR 0016 point 4) and before a strategy goes live (ADR 0017 part F); the three criteria below stay exit criteria)*
- every paper order has a journal chain signal → decision → order → {fill | expired | rejected | cancelled} → outcome, with those four as the only allowed terminal states, verified by a query;
- the override log has a reason on every row;
- the engine **cannot run without a trial ID**: it obtains one from the registry before computing and refuses to return results otherwise, so "every run is registered" holds by construction and is verified by a test that the engine raises without an ID.

**Stop / failure criteria** (set now, checked at every phase retro):
- **Time:** if no phase exits within 6 calendar months of the previous phase tag, the retro must record an explicit continue-or-stop decision by the owner. Weekly hours are not fixed; the owner records actual build hours and review hours separately in each retro. **Machine assumption:** the system needs a local machine that is on and online after each session close (ingest) and before the open of the first session of each month (orders); misses follow ADR 0006's missed-rebalance rule. *(Amended 2026-10-09, [ADR 0017](0017-fast-paper-and-machine-readiness-gate.md) part C: with paper books at every cadence, "before the open of the first session of each month" reads "before the open of every session".)*
- **Spend:** the charter's budget ceiling, scoped to data, API and LLM spend **by the running system** (development tooling such as the owner's Claude subscription is excluded). The owner records the month's spend as a row in the retro or the database, so the check is a query. Exceeding the ceiling in any month halts paid ingestion until the owner amends the charter.
- **Live capital:** ~$100 and never topped up without a charter amendment. A total loss of that amount is an accepted outcome, not a stop trigger. Risk rules (Phase 4 ADR) handle drawdown responses; they are operational rules, not success metrics.
- **Integrity:** any discovered look-ahead in a result that was already reported, or any untracked backtest run, halts the current phase until fixed and retro'd.
- **Amendment 2026-10-18 (Phase 6), Live** (#878 decided by the owner on 2026-10-18, recorded by [ADR 0023](0023-break-first-fast-books-paper-exam.md) D9 and his merge of the alignment PR; the memo on #878's first comment, its options Q1 (b), Q2 (a), Q3 (a); written for **the live book**, whichever strategy the Phase 6 ADR (T163) names, since ADR 0023 D6 chooses none): **Q1 (b)** the live book inherits every paper stop above and the risk rules of [ADR 0010](0010-phase-4-risk-rules.md) unchanged, plus one **tracking stop**: when the live book's tracking residual **against its own backtest** (ADR 0005 criterion 1 as amended, on the live window's own rows) breaches the frozen bound, the kill switch engages with a new `kill_switch.source` value **`tracking`**, specified in the Phase 6 spec (the paper spec's live amendment under T163), never here; **Q2 (a)** the first live window runs **six rebalances** at the live book's cadence and then a recorded continue-or-stop retro by the owner, with this ADR's six-month rule above on top of it, not instead; **Q3 (a)** recorded as this bullet, and the [roadmap](../roadmap.md)'s Phase 6 row's "written down" item ticked with the date. A total loss of the ~$100 stays an accepted outcome, not a stop trigger.

## Consequences

- Good: objective (1) is measured by exactly the four Phase 4 checks above, each a command or query; the time and spend criteria are retro judgments against recorded numbers; the owner cannot fool themselves with a lucky quarter; benchmarks catch a broken system without being a moving target.
- Bad / accepted risks: the project can be a success by its own criteria while underperforming SPY. The owner accepts this. If the owner later wants a return objective, that is a new ADR and a much longer time horizon.
- Reversibility: cheap on paper, but changing the objective after results are in is exactly the behavior the research warns about (§10, Behavioral). Any amendment must cite results that were not the reason for the change.
- Revisit if: the LLM layer enters scope (calibration criteria become primary for it); the owner's time or money situation changes materially.

## Amendment 2026-10-09: Phase 4 residual tracking (T70, #294)

**Status: Accepted by the owner's merge of PR #297 (#247 Q4, decided 2026-09-27; drafted 2026-09-27, completed 2026-10-09 once Probe 3 reported).** This and the matching [ADR 0006 amendment](0006-universe-and-cadence.md#amendment-2026-10-09-phase-4-paper-execution-t70-294) are one amendment, merged before `paper start` (T71). The reason for it is evidence gathered before any Phase 4 result exists, as the reversibility clause above asks: the execution and dividend mismatch documented in [#86's report](../research/2026-09-25-alpaca-open-and-depth.md) and measured by [Probe 3 (#182)](../research/2026-10-09-probe3-preopen-fills.md), never a Phase 4 return.

**The rule.** For Phase 4, success criterion 1 is checked on `paper.tracking_rule = residual`: `residual = raw + dividend term + fill-timing term`, with the terms and the monthly boundaries defined in [paper-trading requirement 10](../specs/paper-trading.md). The raw difference is paper return minus backtest return. The dividend term adds back the dividends the backtest reinvested and paper did not receive; the fill-timing term adds back the execution-price difference against the frozen `execution.fill_price` bar price (positive for a buy above, or a sell below, that price). Both are computed from recorded rows, never fitted to make a month pass. **The raw series is always printed beside the residual** and its two terms; the residue term is printed beside them and is never part of the residual.

Each non-excluded month's absolute residual must be at most the window's frozen `paper.tracking_k` times that month's modelled cost per rebalance. **Months whose rebalance has a `missed` event or an override are excluded from the maximum and listed with the reason** (ADR 0005 as written excludes missed months only; req 10 assigned the override exclusion to this amendment, since an override is the owner's decision, not the system's tracking). Months with `skip_*` decisions are listed with the skipped names and stay in the check. At least `paper.min_rebalances` (default 6) scheduler-executed rebalances and as many compared, non-excluded months are still required; exclusions never lower that count. The rule is frozen into `paper_windows` at `paper start`; reports and checks read that record, never the live settings. The other three success criteria stand.

**Where the residual sits.** The trial's month return is net of its modelled `cost_paid`; paper pays no commission, and the spread and drift inside its fill prices are what the fill-timing term adds back. So a system that tracks the backtest perfectly at bar prices shows a residual of about **+1× the modelled cost** each month, not zero, and the check `|residual| ≤ k × cost` with `k = 2` is one-sided: about three times the cost of room below that centre and one above it. This is stated here so that nobody reads a residual near +1× cost as drift, and because the rule is fixed before the first order (req 10). It also means this series cannot show whether real execution costs exceed the cost model (the ADR 0006 "paper fills diverge from the cost model" trigger); that is checked from the fills themselves, fill against the NBBO or the official open per session, as Probe 3 did, never from the residual. Netting the modelled cost out of the residual instead is a change to req 10, the report code and its tests; the owner decides it on this PR or before T71, and this paragraph records the rule as it stands.

**What Probe 3 measured, and its limits.** On Alpaca's paper simulator, on two sessions (2026-10-05 and 2026-10-09) with two liquid names (KO, AAPL), a $5 notional fractional buy queued before the open filled at the listing exchange's official opening print within 2 s of 09:30 ET in 4 of 4 cases; a 1-share buy filled at the NBBO ask 17 to 118 s after the open, −9 to +35 bps from the official open (median +6 bps), in 4 of 4 cases; a 1-share OPG order expired unfilled in 4 of 4. Those gaps are against the official open; the fill-timing term is against the frozen `execution.fill_price` bar price, the close for H1, so it also carries each name's open-to-close move on the fill session, which Probe 3 did not measure. What the probe shows is the execution side of the term: on paper it is zero on a pre-open fractional and tens of basis points on a whole share, for these names. **The evidence is thin: the owner chose two sessions rather than the protocol's five (#182, 2026-10-08); four buy fills per order type; no sell was probed; paper, not live.** The direction of every outcome was the same in all four cases, and the timing keys rest on it; the magnitudes (the median and worst latency and gap) are indicative, not a distribution, and the report grades them INSUFFICIENT. The first paper window's own fills, across many names and both sides, are the larger sample this rule is checked against; if they show that the fill-timing term is systematically larger than the modelled cost, that is a finding to record, not a reason to change this rule after the fact.

## Amendment 2026-11-20: the yardstick is the net-matched blend (#3686)

*A proposal for the owner's word on this class-B PR (#3686), on his instruction of 2026-11-20: "Fix yard stick". It is the amendment [ADR 0024](0024-lift-long-only-and-top-1000.md) item 7 asks for. It answers the [backtest spec](../specs/backtest.md)'s open questions 24 and 40 with their option (a). The decisions it needs are listed at the end; merging the PR records the recommended answer to each unless the owner says otherwise on the PR. Three questions that go beyond fixing the yardstick are not recorded by the merge; they are the backtest spec's open questions 48 to 50. Docs only: no code, no store read, no return read, no trial.*

*Words used.* A book's **net** is its long weight minus its short weight, as a share of equity: 0 for a market-neutral book. A book's **yardstick** is the one series its excess return is measured over when a rule says pass, kill, promote or retire. The **T-bill** is the store's risk-free series ([ADR 0020](0020-fred-risk-free-rate.md); backtest spec amendment #1498).

**Why.**
- This ADR names SPY and MTUM. It was written for a long-only book and says nothing about a book with a short leg. ADR 0024 lifted long-only on 2026-10-19 and listed "A benchmark for a book with a short leg" as item 7 of what must exist before the first short order.
- [ADR 0023](0023-break-first-fast-books-paper-exam.md)'s line 40 kills at rung 4 on "net excess CAGR over SPY". A book with net 0 holds no market exposure by design. It "trails SPY by roughly the equity premium (about 7 to 10 pp/yr) by construction" ([beyond long-only](../research/2026-10-10-beyond-long-only.md), "In plain words", point 4), whatever its edge. So line 40 as written kills every market-neutral book before its signal is read. The rung-4 pass arithmetic (PR #3683, the first of its ambiguous rules) found the same.
- **The evidence is older than any result it could flatter**, as the reversibility clause above asks. The report is dated 2026-10-10 and the spec's recommendation 2026-10-19. The one long/short result the project holds is the T218 spike of 2026-10-20 ([report](../research/2026-10-20-long-short-spike.md)). It was killed on its own registered spread rule, not on SPY, and this amendment does not re-read it. PR #3683 read no return.

**The rule, from the owner's merge.**
1. **A long-only book's yardstick is SPY, as before.** That holds for every file whose frozen `portfolio.side` is `long_only`, whatever its `portfolio.gross_exposure`. A long-only book that holds part of its equity in cash is still judged on SPY, as it is today.
2. **A book with a short leg is judged on the net-matched blend.** For a file whose frozen `portfolio.side` is `long_short`, with net exposure `net`, in every period: **yardstick = net × SPY + (1 − net) × T-bill**. SPY is its total return, computed as the Decision above says. The T-bill term is the T-bill accrual over the same sessions. The weights are fixed, so the blend goes back to `net` at the start of each period. At net 0 the yardstick is the T-bill. At net 1 it is SPY.
3. **"Net" is the frozen target, not the realised figure.** It is the registration's frozen `portfolio.net_exposure` (backtest spec amendment #1872, item 1): the sum of the signed target weights, fixed at registration, never a sweep axis, and a new hypothesis identity if it changes. It is known before any return is read, so it cannot be tuned after the fact. A trial's realised `net_exposure_mean` is an outcome. It moves with drift, with a short leg cut by the shortability proxy, and with the cost level. It never sets the yardstick.
4. **The held net must stay near the frozen net.** A short leg that is cut short keeps only its own share, so a book frozen at net 0 can hold a net well above 0 and still be measured against the T-bill. So a long/short book cannot pass rung 4 or be promoted when, at the decision's cost level, the gap between its `net_exposure_mean` and its frozen net is above `risk.net_exposure_band` (default 0.10, the band the paper risk rules already hold a short book to). The band is fixed before the result is seen: the kill line posts its value, and a sweep freezes it at registration. This gate is what makes rule 3 safe.
5. **What reads the yardstick.** ADR 0023's rung 4 (line 40) and promotion (strategy-lab requirement 4) read the excess over the book's yardstick wherever they say "over SPY". The deflated Sharpe in those rules is the Sharpe of the same excess series. The backtest spec's amendment of this date names the stored statistics. No file can name a benchmark of its own today; if a later spec adds such a key, what it means for a `long_short` file is that spec's question, and until then the yardstick is the only series a rule reads.
6. **Market beta is not edge, at rung 4.** For a long/short book whose net is not 1, a rung-4 pass also needs `alpha_spy_annual` above 0. A dollar-neutral book is not beta-neutral, and a T-bill yardstick alone would pay a book for leftover market exposure in a rising market. This is a sign check in the posted kill line. It is not a significance test (see Consequences), and `sweep promote` does not enforce it in code (backtest spec open question 49).
7. **SPY and MTUM are still reported beside every result, for every book.** They are "measured against, never targeted", as above. For a long/short book whose net is not 1 they are reported and decide nothing.
8. **The forward exam is not decided here.** [ADR 0016](0016-development-boundary-and-forward-exams.md)'s exam subtracts "the benchmark the finalist's file froze" and regresses on that benchmark's return over the T-bill. With a T-bill benchmark that regressor is zero in every month, so the exam's second leg is undefined. How a long/short finalist's exam reads its yardstick is the backtest spec's open question 48, the owner's. Until he answers it, no exam window starts for a `long_short` book; a tracking window may.
9. **The objective is unchanged.** The ranking in the Decision above stands: process first, calibration second, beating a benchmark optional and never a phase-exit criterion.

**What this does not change.**
- **A long-only book: nothing.** Its yardstick is SPY whatever its gross exposure (rule 1), and every statistic, kill line and refusal reads as before. No new refusal applies to a `long_only` file.
- **Past trials and kills are not re-scored.** No hypothesis or sweep file in the repository names a `portfolio.*` key, so every trial and every rung-4 kill of those files was a long-only book judged on SPY. T218's kill was a registered research run, and it stands on its own rule.
- **The success, stop and tracking criteria above.** Criterion 1 compares a paper book with its own backtest, not with a benchmark.
- **The forward exam's rule** (ADR 0016). Rule 8 leaves it as written.
- **What a book's own cash earns.** The yardstick is what a saver earns on cash. What a book's free cash and short proceeds earn, and what it pays to borrow, stay the engine's rules (backtest spec amendment #1872). A market-neutral book that earns nothing on its short proceeds still has to beat the T-bill.
- **Live trading stays long-only** (ADR 0024). This amendment allows no order.
- **Event studies.** They already judge a treated leg against matched peers, by the rule their experiment file freezes (ADR 0023's amendment of 2026-11-16, item 2).
- **Two open decisions on the deflated Sharpe:** #3571 (should a new root family start at SR\* = 0) and #3572 (which deflated Sharpe the kill rules read). This amendment names only the series the Sharpe is taken over. It works with either answer, for a family whose counted trials are all judged on one yardstick; the backtest spec's amendment, item 9, states the limits.

**Consequences.**
- Good: a market-neutral idea can now be read on our own data. One rule covers neutral and partly hedged books, and long-only books stay where they were. ADR 0024 item 7 is met.
- Bad / accepted risks:
  - The T-bill is a lower bar in level than SPY. A neutral book can pass while making far less than the index, and the bar is lowest when rates are near zero. That is consistent with objective 3 being optional, and the owner accepts it.
  - **The alpha test is a sign check, not evidence.** It takes out the average effect of the book's market beta. It does not take out the noise: a book with zero true alpha passes it about half the time, and no standard error is stored. Whether rung 4 should ask for more is the backtest spec's open question 50.
  - Only the two ends, net 1 and net 0, have stored statistics today. A long/short book in between is refused by name until its blended series is built (decision 4).
  - The net-gap gate (rule 4) can stop a real idea whose short leg cannot be filled as registered. Its fix is a new registration at the net it can hold, which is a new identity.
  - The yardstick now depends on a frozen key, so a wrong `portfolio.net_exposure` in a file sets the wrong bar. The frozen-set checks at registration and promotion, and rule 4, are the guards.
- Reversibility: cheap today, since no long/short book has been promoted and none is on paper. After the first long/short promotion, a change of yardstick is the behaviour the reversibility clause above warns about, and it needs evidence that is not that book's result.
- Revisit if: the owner allows gross exposure above 1.0 (ADR 0024 item 4), which makes a book near net 1 with a short sleeve possible and the in-between case common; or a paper short book shows that short proceeds earn interest.

**Decisions for the owner** (one line back on any of them is enough):
1. **Make a long/short book's yardstick the net-matched blend, net × SPY + (1 − net) × T-bill, and leave every long-only book on SPY?** *Recommend yes. It is open question 24's option (a). The alternatives: T-bills for every long/short book (b), which pays a partly hedged book for its market exposure; SPY for every book (c), which kills every neutral book by construction; and a beta-matched yardstick, beta × SPY + (1 − beta) × T-bill with the book's measured `beta_spy`. The last is rejected because the estimated beta is an outcome of the run, so the bar would move with the result.*
2. **Define net as the frozen `portfolio.net_exposure`, not the realised mean?** *Recommend yes. The alternative is the trial's realised `net_exposure_mean`. It describes the book better when a leg is cut short. But it is known only after the run and differs by cost level, so the yardstick would move with the result. Decision 5 covers the gap.*
3. **At rung 4, for a long/short book whose net is not 1, require `alpha_spy_annual` above 0 and read the deflated Sharpe on the yardstick's own excess?** *Recommend yes. It is open question 40's option (a), the `irrx` working rule, made the rule for every such book. It is a sign check in the posted kill line, not a code gate and not a significance test.*
4. **Build the two ends only (net 1 and net 0), one end per family, and refuse a long/short book in between by name until its blended series exists?** *Recommend yes. The store holds every statistic for the two ends today. A net strictly between needs a third excess series, new metric rows and a third basis for V. No idea in the pool needs it: `irrx` is net 0, and gross is capped at 1.0. One end per family keeps a family's deflated Sharpe on one basis. The alternative is to build the general series now, about one more size-M task. No long-only file is refused. This lifts the "reported only" status the backtest spec's #1872 amendment, item 8, gave `excess_cagr_rf`: at the T-bill end it becomes a selection statistic.*
5. **Add a gate on the gap between the held and the frozen net** (rule 4): no rung-4 pass and no promotion when it is above `risk.net_exposure_band`? *Recommend yes. It is a new gate. It is what makes decision 2 safe: without it a book frozen at net 0 that holds net 0.5 is judged on T-bills. The band is frozen before the run: the kill line posts it, and a sweep copies it at registration. This lifts the "reported only" status of `net_exposure_mean` (#1872 amendment, item 8): it becomes a gate. It still never picks among variants.*
6. **Leave past trials and kills as they stand?** *Recommend yes. None changes under the rule.*
7. **Record on [ADR 0004](0004-tooling-adopt-avoid.md) that the engine was extended for shorts** and checked by the `bt` long/short case? *Recommend yes. It is the note open question 24 asked for.*

**Not recorded by the merge** (the backtest spec's open questions, each with options and a recommendation): 48, how a long/short finalist's forward exam reads its yardstick; 49, whether `sweep promote` enforces the alpha test in code; 50, a significance bar for the alpha test.

## Amendment 2026-11-21: what a strategy book is for, core and satellite (#3695)

*A decision for the owner, drafted on his word of 2026-11-21 (#3695). **He has not chosen an option.** Merging the PR records option (b), the recommended one, and the numbered decisions at the end, unless the owner says otherwise on the PR. One line back ("a", "b" or "c") is enough. This amendment is stacked on the [amendment of 2026-11-20](#amendment-2026-11-20-the-yardstick-is-the-net-matched-blend-3686) (#3686, PR #3690) and merges after it. This PR adds three inline markers. The one on the Decision above points here and is true under (a) and (b); it is edited before merge only if the owner picks (c). The two in the hypothesis backlog say only "under review" and stay as they are under every option, since Q4 stays open. Docs only: no code, no store read, no return read, no trial.*

### The choice, in two minutes

You asked on 2026-11-21 whether the strategy is too narrow. One part of the answer is a choice only you can make: **what counts as a win for a strategy book?**

- **(a) Keep "beat SPY in return".** A long-only book wins only if it ends with more money than buy-and-hold SPY. Nothing changes. The records below say the odds of that are close to zero without leverage, and this option writes that down.
- **(b) Core and satellite (recommended).** A strategy book is judged as a small side bet, a satellite, beside a market core held outside this system. Whether one is held is not recorded and is not the system's business. The book wins if it earns more than its own fair yardstick, and the extra is not just market exposure. **A book can win while making less than SPY.**
- **(c) A risk-adjusted goal for the whole account.** The system is judged on return per unit of risk against SPY or a 60/40 mix. This changes what the account is for, and it needs a new exam design and a new ADR.

**Four things to know before you say (b).**
1. **It will not find a strategy by itself.** The pipeline review (PR #3692) puts the objective at 5% of the reason nothing has passed. It says of the long-only books that were read: "A matched yardstick would not have made any of them an edge." By its table, one idea of 37 (B2) was stopped by the objective alone.
2. **A win under (b) does not mean you are richer than if the money had stayed in SPY.** Each dollar in a market-neutral satellite is a dollar that does not earn the equity premium. At a net edge of 2 to 4% a year, the satellite is expected to make about $2 to $4 a year **less** per $100 than SPY, and $200 to $400 less per $10,000 ([arithmetic below](#dollars)). These are paper or hypothetical figures: the live $100 book is long-only and stays judged on SPY under every option. What a win means is that the book earned something the market did not hand it.
3. **On #659 it adds nothing on merge day.** On 2026-10-03 you said a higher Sharpe at a lower return is not acceptable. The 2026-11-20 amendment already accepts that a neutral book "can pass while making far less than the index". This PR narrows #659 no further unless Q1 is answered later. A higher Sharpe alone is still never a pass. What that "no" meant is put to you as Q4.
4. **It comes after 37 failed ideas.** Changing what a win is after the results are in is what this ADR's reversibility clause warns about. The limits on that are [stated below](#reversibility-and-the-clause-on-changing-the-objective-after-results).

**Picking (a), with the evidence written down, is equally sound.** Unless Q1 is opened later, (a) and (b) differ only in words.

**What the merge changes on the day: words, not rules.** No gate, exam length or pass chance changes. A realistic edge still passes the forward exam about 1 to 4% of the time under every option. No book is read differently the day this merges. Every long-only book stays on SPY, as the 2026-11-20 amendment's rule 1 says, and that includes the live $100 book. The rule changes that would give (b) teeth are two open questions, **Q1 and Q2 below**. They are yours, and this merge does not record them.

*Words used.* The **core** is a holding of the broad market outside this system. It is the assumption the judging rests on: a book is judged as a satellite to such a core, and whether one is held is not recorded and is not the system's business. The system does not hold it, size it, measure it or advise on it. A **satellite** is one strategy book inside the system. A book's **yardstick** is as the 2026-11-20 amendment defines it. **Alpha** is the part of a book's return that its market exposure does not explain (`alpha_spy_annual` in the store). **IR** (information ratio) is a book's yearly excess return over its yardstick divided by how much that excess bounces around.

### Context: what the records show

- **The written objective and the practised rule disagree.** The Decision above ranks "beating a benchmark" third, "explicitly optional and not a criterion for any phase exit". Yet rung 4 kills on "net excess CAGR over SPY < 0" ([ADR 0023](0023-break-first-fast-books-paper-exam.md), line 40), and the forward return exam reads monthly returns "minus the total return of the benchmark the finalist's file froze" ([ADR 0016](0016-development-boundary-and-forward-exams.md), amendment 2026-11-06, decision 2). The pipeline review lists this as item 17 of its table of rules that disagree (PR #3692, section 5).
- **The backlog's record of #659 and this ADR's ranking read differently.** The [hypothesis backlog](../research/hypothesis-backlog.md) parks B2 because a higher Sharpe at a lower return is not acceptable under ADR 0005, "whose objective is return against the benchmark". The Decision above ranks process first. Whether the owner's "no" of 2026-10-03 was a wish for return over SPY, which points to option (a), or a reading of this ADR, is his to say. It is Q4.
- **37 named ideas in eight weeks; six return reads on our own store; all six failed** (PR #3692, finding 1). None had the shape the gates can pass (finding 2).
- **No ETF-level rule has credible evidence of beating buy-and-hold SPY in return**, net of costs and taxes. "Only leverage ... can lift the expected return above SPY's, and that adds market exposure rather than edge" (PR #3682, Answer, points 1 and 2). The charter allows no leverage.
- **Small-account structural trades are worth a few hundred dollars a year at $10,000 and cannot be run at the broker** (PR #3678, Answer: about $300 to $1,400 gross for odd-lot tenders at a $0-fee broker; $100 per voluntary action at Alpaca).
- **No filing event and no price event clears the passable shape** (PRs #3693 and #3691, Answers). The nearest filing miss, cash merger arbitrage, has "the wrong yardstick under line 40": "a cash-like book cannot beat SPY in a rising market" (PR #3693, point 4).
- **Rung 4 is a cost test, not an evidence test**, and the two queued candidates could not pass it (PR #3683, findings 1, 3 and 4). The evidence test is the forward exam.
- **The exam is the tight gate, and no option here loosens it.** Its bounds are t ≥ 4.762 / 3.367 / 2.750 at 12 / 24 / 36 months (ADR 0016, amendment 2026-11-07). With t ≈ IR × √years, that is a realised net IR of about 4.8 / 2.4 / 1.6. The ETF report's figures check out against the ADR's own: 4.762 / √1, 3.367 / √2 and 2.750 / √3 are 4.76, 2.38 and 1.59, and the ADR gives 1.60 as the IR that passes half the time.
- **Market-neutral books already have their yardstick.** The 2026-11-20 amendment moved a long/short book to the net-matched blend, T-bills at net 0, and left every long-only book on SPY. Its rule 9 says the objective is unchanged. This amendment is where the objective is looked at.

### The three options, and what each changes

| Where | (a) Keep "beat SPY in return" | (b) Core and satellite | (c) Risk-adjusted, whole account |
|---|---|---|---|
| The ranking in the Decision above | Unchanged. A sentence is added saying what the evidence shows about objective 3. | Unchanged. Objective 3, applied to one book, reads "earn more than the book's yardstick, with positive alpha where its yardstick is not SPY". It stays optional. | Objective 3 is rewritten as a risk-adjusted goal against SPY or 60/40. The Consequences above call a change of this kind "a new ADR and a much longer time horizon". |
| Yardstick (2026-11-20 amendment) | As that amendment leaves it. Read literally, "every book" would also undo it for long/short books; the owner would say so on PR #3690. | As that amendment leaves it. Whether a long-only book with less than full market exposure moves off SPY is **Q1**, not recorded. | Replaced by a risk-matched comparison for every book. |
| Tracking check (success criterion 1) | Unchanged | Unchanged. It compares a paper book with its own backtest, never with a benchmark. | Unchanged |
| ADR 0023, line 40 (rung 4) | As amended 2026-11-20 | As amended 2026-11-20. No new text. A change would follow Q1 only. | The excess-CAGR test and `dsr_excess` are replaced by a risk-adjusted statistic. A new dated amendment. |
| ADR 0016, forward return exam | Unchanged | Bounds, looks, budget, skew guard and both legs unchanged. How the exam reads a yardstick other than SPY is the backtest spec's question 48 and **Q2**. ADR 0016 is not edited. | A new test statistic. The sizing runs behind the bounds are for a mean excess return, so the false-pass rate would have to be calibrated again before any exam starts. |
| Phase 6 stop criteria (Live amendment, 2026-10-18) | Unchanged | Unchanged. None of them reads SPY: the paper stops, the risk rules, the tracking stop against the book's own backtest, six rebalances then a retro. The $100 live book is long-only and stays judged on SPY. | "Whole account" means either the $100, where nothing changes, or holdings outside the system, which the charter does not let it manage. The second is a charter amendment. |
| #659 and B2 | Stand as decided | Nothing is added on merge day: the 2026-11-20 amendment already lets a book whose yardstick is not SPY pass at a return below SPY's. B2's yardstick is SPY until Q1 is answered, so **B2 stays parked**, and the evidence against it stands whatever the yardstick. What the "no" meant is Q4. | Reversed outright. B2's reason for parking is gone, though its evidence grade is not. |
| Charter | Unchanged | Unchanged. Purpose, scope, the $100 cap, no leverage, no options. | Purpose and the live-capital line would need an ADR. |

**Which killed or parked ideas could come back.** **None is un-retired by this merge, under any option.** A returning idea is a new registration with a new identity. It starts at the first step of the route (ADR 0023 as amended 2026-11-16, rung 1's screen included), its kill line is frozen before any read, and its trials count.
- **(a): none.**
- **(b) as merged: none.** Market-neutral ideas got their read from the 2026-11-20 amendment, not from this one, and the two the project holds died on other grounds: the long/short momentum spike on its own spread rule, and `irrx` on cost (PR #3683, finding 3).
- **(b) with Q1 answered for an exposure-matched yardstick:** B2 (trend filter); cash merger arbitrage (card IC-B3); market volatility targeting (PR #3682). Each arrives with a weak prior. B2's best out-of-sample result is about +0.3 pp a year over a volatility-matched mix, an IR of about 0.04 to 0.2 (PR #3682, point 4 and the exam section). Merger arbitrage earns about 100 bp per deal over T-bills against about 176 bp needed, with losses that cluster in crashes (PRs #3693 and #3678).
- **(c):** the same three, plus any rule whose only claim is a smoother ride.
- **Not candidates under any option:** H1, B3, B10 and candidate 5. They were fully invested long-only books, SPY was their fair yardstick, and they lost to it (PR #3692, section 6 (e)). Nor the ideas killed on published evidence, on event counts or on cost. Nor covered calls: the charter's scope says "no options trading".

### Dollars

*Assumption, not evidence:* a satellite that passes earns a **net alpha of 2 to 4% a year** over its yardstick, after costs and before tax. That is generous. PR #3683 says a survivor of the exam "needs an edge three times the size of anything in the register".

*Paper or hypothetical figures.* Live trading stays long-only on the cash account ([ADR 0024](0024-lift-long-only-and-top-1000.md), decision 1). So the live $100 book cannot be market-neutral, and it stays judged on SPY under every option. The $100 row shows the scale for a paper book of that size.

| Account | Satellite sleeve | Net alpha at 2 to 4% a year | Against the same dollars left in SPY (market-neutral satellite, equity premium taken as 6% a year) |
|---|---|---|---|
| $100 (the size of the charter's live cap; paper only) | $100 | **$2 to $4 a year** | $2 to $4 a year **less** |
| $10,000 (needs a charter amendment) | all of it | **$200 to $400 a year** | $200 to $400 a year **less** |
| $10,000 | a 20% sleeve, $2,000 | **$40 to $80 a year** | $40 to $80 a year **less** |

- **The last column is the honest cost of (b).** A market-neutral satellite earns the T-bill rate plus its alpha. The same dollars in SPY are expected to earn the T-bill rate plus the equity premium. The premium is taken here as 6% a year (the figure PR #3682 uses; [beyond long-only](../research/2026-10-10-beyond-long-only.md) says 7 to 10). So the satellite adds expected dollars only if its alpha is above the premium it gives up, or if the money would otherwise sit in cash. What it adds otherwise is a return that does not move with the market. That is a risk benefit, the trade-off #659 was about.
- **Taxes.** At a 24% marginal rate on short-term gains (PR #3682's assumption), $2 to $4 becomes about $1.50 to $3, and $200 to $400 about $150 to $300.
- **A retail short book earns nothing on its short proceeds** (beyond long-only, point 3), and still has to beat the T-bill.
- **Under (a)** the same 2 to 4% would sit on top of SPY: $2 to $4, or $200 to $400, **more** than the index. No candidate in the records has a positive expected excess over SPY.
- **Under (c)** the best out-of-sample figure for the trend filter is about +0.3 pp a year over a volatility-matched mix (PR #3682): about $0.30 a year at $100 and $30 at $10,000, at a lower return than SPY.

### How long an exam would take

The exam's rule is the same under (a) and (b): looks at 12, 24 and 36 months, the bounds above, and a window that reaches month 36 without a pass is a failed exam (ADR 0016, amendment 2026-11-07). So **the longest an exam can run is 36 months, and the question is how often a real edge passes.** With t ≈ IR × √years, the chance that the first leg alone reaches 2.750 at month 36 is (our arithmetic, a t distribution with 35 degrees of freedom; an approximation, not a strict upper bound: the second leg and the futility stops lower it, and passes at the 12- and 24-month looks add a little):

| Realised net IR against the yardstick | 0 (noise) | 0.25 | 0.5 | 0.8 | 1.0 | 1.6 |
|---|---|---|---|---|---|---|
| Passes by month 36, about | at most 1.25% (the calibrated worst case, skew −0.85); 0.343% for a symmetric fat-tailed no-edge book (ADR 0016, amendment 2026-11-07) | 1.4% | 3.8% | 10% | 17% | 52% |
| Years of months for an even chance at t = 2.75 | never | 121 | 30 | 12 | 7.6 | 3.0 |

- An alpha of 2 to 4% a year on a book whose excess moves 8% a year (the tracking error ADR 0016's sizing runs assume) is an IR of 0.25 to 0.5. **Such a book passes about 1 to 4% of the time.** At IR 0.25 its chance (1.4%) is close to the worst-case chance of a no-edge book at skew −0.85 (1.25%), and about four times that of a symmetric no-edge book (0.343%).
- ADR 0016's own simulation is lower, because its IR is measured before paper cost: a net IR 0.5 slow book passes 0.84% of exams at the 60 bp paper cost, and a four-exam programme of such books reaches a live order 3.2% of the time at 60 bp. Its 12.1% assumes a 24 bp paper cost the project does not have.
- **(b) does not shorten the exam or raise these odds.** It changes one thing: for a low-beta book, the first leg stops being impossible by construction. A cash-like book measured against SPY is expected to lose by about the equity premium every year, whatever its edge.
- **Under (c)** no exam exists. The plausible IR of a trend or volatility rule against a risk-matched mix is 0.04 to 0.2. At 0.2, t = 2 takes about 100 years (PR #3682).

### False-pass exposure

- **(a): as today.** At most 1.25% per exam and 4.9% for four, inside the skew guard (ADR 0016, amendment 2026-11-07). Rung 4 passes a low-turnover long-only book with no edge 39 to 45% of the time (PR #3683, finding 7); the exam is the control.
- **(b) as merged: the same.** No rule moves.
- **(b) with Q1 answered:** the exam's size is unchanged if its bounds and budget are. Three risks are new, and each has a guard to be specified before a first read, never after:
  1. **Beta paid as edge.** A yardstick that understates a book's market exposure pays it for a rising market. The guards are a frozen exposure with a band on the held figure, the alpha sign check, and the exam's second leg.
  2. **A lower bar at rung 4 fills exam slots.** Rung 4 is a sign test at N = 1. A T-bill-like bar is lower in level than SPY, so more no-edge books pass it, and each started exam uses one of four slots for up to 36 months. This sits beside #3571, #3572 and the backtest spec's question 50, which this amendment does not answer.
  3. **Skew.** Cash-like books lose in clusters. The exam's stated rate holds only for a skew no lower than −0.85, and `paper start` refuses a finalist below it. Some satellites will not be able to take the exam.
- **(c): unknown.** Sharpe and drawdown comparisons are noisier than a mean, and they rest on a handful of bad markets ("about four in 80 years for US trend", PR #3682). No false-pass rate can be stated until a new sizing run exists.

### Recommendation: (b), with its limits stated

The drafting brief gave three reasons for (b). Each was checked against the documents.
1. **"(b) lines the rules up with the stated objective."** Holds. The Decision above ranks process first and benchmark-beating optional, and the charter's Purpose says "Beating the market is a separate, harder, optional goal". (b) changes no ranking. It says what "beating a benchmark" means for one book.
2. **"The evidence says (a) has near-zero odds without leverage."** Holds, with a caveat. No candidate in the records has a positive expected excess over SPY, and the exam needs a realised IR near 1.6. The caveat: **the exam is just as hard under (b).** (b) removes a handicap for low-beta books. It does not make a confirmed win likely.
3. **"(c) is a bigger decision than the system needs."** Holds. (c) needs a new exam statistic with no calibrated false-pass rate, a new ADR by this record's own Consequences, and, if "whole account" means holdings outside the system, a charter amendment.

**Where the documents do not support the case as it was put to the owner.** He was told the binding narrowness is the definition of a win. The pipeline review weights the objective at 5%, behind the sources (35%), the gates and cost basis (25%), the market (20%) and throughput (15%). (b) is still the best supported of the three options. But it is the smallest of the five causes, and after the 2026-11-20 amendment most of what it could fix is already fixed. **If the owner would rather not open Q1, (a) and (b) differ only in words**, and choosing (a) with the evidence written down is equally sound.

### Consequences

- **Good, and modest:** the written objective says how it reads for one book, and the owner has in writing what a win would and would not be worth. If Q1 is answered later, a low-beta long-only idea would have a route to a fair read. **None of this raises the chance that a strategy passes, and it does not solve the problem the owner asked about.**
- **Bad:** it is a change to the meaning of a win, made after results. It fixes the smallest cause. It may read as progress when the binding problems (sources, the cost basis, the exam's power) are untouched.
- **Accepted, if the owner merges:** a satellite may pass while trailing SPY, as the 2026-11-20 amendment already accepts for a neutral book. A passed satellite at $100 is worth a few dollars a year. Whether a core is held is not recorded. Without one, a market-neutral book is simply a lower-return account.
- **Revisit if:** the owner wants the system to judge his whole account (that is (c), by a new ADR); live capital rises above the charter's $100; or Q1 is answered and a first exposure-matched book is registered.

### Reversibility, and the clause on changing the objective after results

The Consequences above say: "Any amendment must cite results that were not the reason for the change."
- **This amendment was prompted by results.** The owner's question followed 37 failed ideas. That is said here so that nobody reads it otherwise.
- **The reasons it rests on are older than those results.** The ranking is this ADR's own text of 2026-09-24. That a low-beta book trails SPY by construction is in the beyond long-only report of 2026-10-10 ("In plain words", point 4). The trend-timing review is dated 2026-09-25.
- **It flatters no result.** No past trial is re-scored, no kill is re-read, no idea is un-retired, and no bound, budget or cost cell moves.
- **Cheap to reverse today:** no book has been read under (b), and none will be until Q1 is answered. After a first satellite is registered against a yardstick other than SPY, a change back needs evidence that is not that book's result.

### What this does not change

- **The charter's purpose and this ADR's ranking.** Process first, calibration second, beating a benchmark optional and never a phase-exit criterion.
- **Point-in-time data and no look-ahead.** Every stored fact keeps its `known_at`, and delisted names stay in.
- **Every run is counted.** The engine cannot run without a trial ID. N never resets. A returning idea's trials count.
- **Kill lines are frozen before a read.** A yardstick, an exposure and a band are fixed at registration, never after a result.
- **The holdout rules** (ADR 0016): the development boundary, one spend, frozen finalists, no holdout read without the explicit flag.
- **The forward return exam:** its looks, bounds, futility and void rules, skew guard, one exam per family and the four-exam budget. ADR 0016 is not edited by this PR.
- **Code computes numbers, and an LLM never places an order.**
- **The stop criteria above:** time (the six-month continue-or-stop rule), spend, live capital of about $100 never topped up without a charter amendment, integrity, and the Live amendment of 2026-10-18.
- **Scope:** no leverage, no options, live long-only on the cash account (ADR 0024).
- **SPY and MTUM are reported beside every result**, for every book.
- **The 2026-11-20 amendment,** every rule of it, and the backtest spec's open questions 46 to 50.
- **The open decisions #3571 and #3572.**
- **Past trials, kills and parkings.** B2 stays parked.

### What would follow

Nothing is built for this merge. The work below exists only if the owner answers the open questions, and none of it is written here: no spec text, no plan task.
- **If Q1 is answered for an exposure-matched yardstick:** a backtest spec amendment (a frozen exposure key for a long-only book, the blended series between the two ends that the spec's question 47 defers, the registration, report and promotion refusals extended, a band on the held exposure), with dated markers on the 2026-11-20 amendment's rule 1 and on the strategy-lab spec's requirement 4. Our estimate: three to five class-A tasks after T436 to T439, one of them size M for the blended series.
- **If Q2 is answered:** dated markers on ADR 0016 and one paper-plan task, as the spec's question 48 already says, widened from long/short books to every book whose yardstick is not SPY. T440's refusal is lifted in the same task.
- **Small either way:** one line in the hypothesis template and on the idea card naming the book's yardstick and its expected alpha, and the yardstick shown beside SPY and MTUM on the Books page. Size S each.
- **Under (a):** nothing. **Under (c):** a new ADR, a sizing run for a new exam statistic, and amendments to ADR 0016, ADR 0023 and possibly the charter, before any build.

### Open questions (the owner's; not recorded by the merge)

- **Q1. The yardstick of a long-only book that holds less than full market exposure** (a trend-filtered SPY book, a cash-heavy event book, a merger-arbitrage book). The 2026-11-20 amendment's rule 1 keeps it on SPY "whatever its gross exposure". Options:
  - (i) Leave it. Every long-only book is judged on SPY. Then (b) changes nothing for any long-only book, the live book included.
  - (ii) An exposure-matched blend, frozen before any read: the registration fixes the book's market exposure m from its design, and the yardstick is m × SPY + (1 − m) × T-bill, with a band on the held exposure and the alpha sign check. This extends the 2026-11-20 amendment's rule 2 to long-only books and amends its rule 1.
  - (iii) A beta-matched yardstick on the measured beta. The 2026-11-20 amendment's decision 1 rejects it, because the bar would move with the result.
  - (iv) Keep SPY as the yardstick and let alpha decide, with a significance bar. This is the backtest spec's question 50 and #3572 under another name.

  *Recommendation: (ii), decided after PR #3690 has merged, together with question 48, since it is the same mechanism. Three things the owner should see before he agrees:*
  - *It reopens the 2026-11-20 amendment's decisions 1 and 2 for long-only books.* Decision 1 rejected a yardstick on the measured beta. Decision 2 made net the frozen target. An exposure-matched yardstick for a long-only book is a new answer to both.
  - *Any exposure figure must come from the design, never from studied history.* A book that is in the market a stated share by construction has an m. An m estimated from past data does not qualify: for B2 it would come from history the project has already studied.
  - *A fully invested book of low-beta stocks has no m under this rule.* Merger arbitrage is one. Its dollar exposure is 1 and its beta is near 0, so its m would be a claim about beta, and a band on the measured beta brings back the measured-beta yardstick that decision 1 rejects. Such a book stays on SPY, or waits for option (iv).
- **Q2. How the forward exam reads a satellite's yardstick.** The backtest spec's question 48 asks this for a long/short finalist and recommends reading the first leg over the yardstick and regressing the second on SPY's return over the T-bill. If Q1 is answered with (ii), the same question covers long-only satellites. It changes how ADR 0016's rule is applied, so it needs dated markers there and the owner's word. *Recommendation: answer it with question 48, once, for every book whose yardstick is not SPY.* Until then no exam window starts for such a book.
- **Q3. Should rung 4 ask a satellite for more than a sign?** With a lower bar, more no-edge books reach the exam door. This is #3571, #3572 and question 50. It is named here so the owner decides them knowing (b) raises their weight. No recommendation is added to theirs.
- **Q4. What did the "no" of 2026-10-03 on B2 mean?** The backlog records it as: a higher Sharpe at a lower return is not acceptable under ADR 0005, "whose objective is return against the benchmark". The Decision above ranks process first and benchmark-beating optional. So which was it?
  - (i) You want a return above SPY, whatever this ADR says. That is option (a), and it is a reason to pick (a) here.
  - (ii) It was a reading of ADR 0005. Then B2's parking note is restated after you choose an option.

  *No recommendation. B2 stays parked either way, and this merge changes no word of the 2026-10-03 record.*

### Decisions this merge records

Unless the owner says otherwise on the PR:
1. **Option (b).** A strategy book is judged as a satellite to a market core held outside the system. Whether one is held is not recorded and is not the system's business. The book's job is a net return above its yardstick that is not market beta.
2. **The ranking in the Decision above stands.** Objective 3 stays optional and is never a phase-exit criterion. Applied to one book it reads "earn more than the book's yardstick, with positive alpha where its yardstick is not SPY", not "end with more than buy-and-hold SPY".
3. **What the evidence says is on the record:** no idea in the project's records has a positive expected excess over SPY in return, and the system is not expected to beat SPY. Under objective 1 that is not a failure.
4. **#659 stands as recorded, and B2 stays parked.** This merge adds nothing to what the 2026-11-20 amendment already accepts, that a book whose yardstick is not SPY may pass at a return below SPY's. A higher Sharpe alone is never a pass.
5. **No idea is un-retired.** A returning idea is a new registration from the first step of the route, with its kill line frozen first and its trials counted.
6. **No rule a book is read by changes:** not the yardstick rules of 2026-11-20, not line 40, not the forward exam, not the stop criteria, not the charter.
7. **Q1 to Q4 are open, and they are the owner's.**

**If the owner says (a):** decisions 1 and 2 are dropped, and 3 to 7 are recorded. **If he says (c):** only 5 is recorded, and a new ADR is drafted for his word before any rule moves. The marker on the Decision above is edited before merge only if he says (c); the two backlog markers stay as they are.
