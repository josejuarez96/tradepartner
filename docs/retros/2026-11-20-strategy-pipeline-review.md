# Review: the strategy identification pipeline, end to end

**Brief:** #3689  ·  **Date:** 2026-11-20  ·  **Status:** COMPLETE as a records review. Docs, plans and GitHub issue and PR history only. No store was opened, no return series was read, nothing was run or registered  ·  **Agent/model:** team pipereview

Uses and does not redo: the rung-4 pass arithmetic (PR #3683, brief #3669), the machinery audit (#3679, #3680, #3681), the 2026-11-20 code-review decisions (#3571, #3572, #3574, #3559, #3560, #3561, #3566), the owner's IRRX stop (#3684) and yardstick direction (#3686). Dates are local unless an issue timestamp is quoted (those are UTC).

## Answer

**Verdict:** the pipeline has not failed to find a strategy so much as it has not yet tested an idea that its own gates could pass. **Confidence:** high on the counts and the dated rule history (read from the records); medium on the time splits (from issue and PR timestamps, not from logged hours); low on anything about whether a tradable edge exists.

1. **37 named ideas in eight weeks; 6 got a return read on our own store; none since 2026-10-31.** Nine died on a published (Chen-Zimmermann) yardstick, five on a count gate, six on cost arithmetic, six on missing data or capability, one waits on an owner run, four were dropped without any test. The six own-store reads all failed.
2. **No idea had the passable shape when it entered.** PR #3683 says a pass needs low turnover, gross at least twice the round trip (200 bp or more per event at the harsh cost) and a few hundred independent events. 0 of 37 had all three. Two (C2 spin-offs, FC-1 Russell promotions) had the edge size and the holding period and failed on event count (8 to 12, and about 30). Every prior written at entry was "near zero", "0 to +0.5 pp/yr" or "expected to kill".
3. **No document tells an author what shape to look for.** The card asks for break-even at the 8.8 bp arrival cost and for t = 1.0 at the gross effect. Rung 4 then decides at 50 bp and the exam door at 60 bp. The backlog ranks by "information value per unit of cost, not expected return". The front of the pipeline is aimed at cheap kills, and it has produced them.
4. **The current route (2026-11-16) has judged nothing.** Seven of its nine steps have never been reached by a candidate: rung 3, rung 4 at the harsh cell, rung 5, the event-study-to-trial step, promotion, the exam door and the forward exam. The exam ledger has no entry. There were ten dated rule changes in the 33 days after the ladder was adopted (2026-10-18 to 2026-11-20); no idea has yet been read under the version in force.
5. **Three gates are one number written in three places, and the cost tests run weakest first.** Card question 3, stage 4a (d) and the rung-1 screen are the same break-even at 8.8 bp. They are followed by 50 bp at rung 4 and 60 bp at the exam door. A weak screen ahead of a strong test stops nothing the strong test would pass, and it lets doomed builds start. IRRX is the case: it cleared the screen (break-even 14.5 bp), got a spec, nine plan tasks and four PRs, and was stopped by arithmetic whose inputs were in the repo on 2026-10-30.
6. **Agent build is not the bottleneck. Owner store runs and bespoke engine families are.** An event-study idea on held data took one day end to end (C1). A desk read took hours. Every idea that took more than two weeks was waiting on an owner run or a data release: B7 28 days, B10 18, IRRX 42, E1 18 and counting. T164e's code merged on 2026-10-10; its real-store run is still not made.
7. **The route, as written, cannot end in a survivor inside the charter's horizon, and the third search limit cannot end by count.** The exam needs a paper-net information ratio near 1.6 for an even chance and holds a slot for up to 36 months. A candidate counts only at a rung-4 verdict, and nothing on the list is expected to reach rung 4.
8. **Weighting for "why no strategy yet":** sources 35 %, gates and the cost basis 25 %, the market 20 %, throughput 15 %, the objective 5 %. Section 6 gives the evidence for each.

**One line:** the measuring side is sound and over-built; the looking side has been pointed at ideas everyone expected to fail. Aim the sources at the passable shape, run the two owner steps that unblock event data, measure the cost, and stop amending the gates until one idea has been through them.

## 1. The funnel, counted

"Entered" is the first dated record. "Days" is entry to the recorded verdict. "Read" is what decided: **CZ** a published or Chen-Zimmermann desk read; **count** a counts-only gate; **store** a return read on our own store; **arith** desk arithmetic on published figures; **none**. Build cost is what the docs name; "est." marks our estimate. Backlog rows are `docs/research/hypothesis-backlog.md:25-39`; ledger rows are lines 238-261; batches 292-298.

| # | Idea | Source | Entered | Stopped at | Stated reason | Days | Read | Build cost named in the docs |
|---|---|---|---|---|---|---:|---|---|
| 1 | B1 / H1 12-1 momentum (and the T114 sweep variants) | handoff literature (HO-1) | 09-24 | own-store trial 2 (10-06); holdout spent by trial 4; retired by its own rule 11-06 | −5.77 pp/yr against SPY, `dsr_excess` 0.3152 (backlog:25) | 12 to the read, 43 to the record | store | the whole Phase 1 engine; PR #1020 |
| 2 | B2 trend filter | literature | 10-03 | parked at entry | lower return is not the objective (#659; backlog:169) | 0 | none | none |
| 3 | B3 gross profitability | literature (QI-6) | 10-03 | own-store trial 3 (10-07); retired 10-18 | −3.28 pp/yr, `dsr_excess` 0.3069 (backlog:27) | 4 to the read, 15 to the verdict | store | data spec #660, family amendment #720, T85 to T85f, owner runs T78 and T85f |
| 4 | B3b cash profitability tilt | literature | 10-03 | retired with B3, not run | dependency (backlog:28) | 15 | none | none |
| 5 | B4 momentum + profitability | literature | 10-03 | dropped 10-18, not run | B3 retired (backlog:29) | 15 | none | one sweep file |
| 6 | B5 filing change → fundamentals | literature | 10-03 | paused 10-18 | needs filing-text ingest (backlog:30) | no verdict | none | none |
| 7 | B6 Lazy Prices, net | literature | 10-03 | paused 10-18 | after B5 (backlog:31) | no verdict | none | none |
| 8 | B7 insider purchases | literature (G2) | 10-03 | desk read INSUFFICIENT, then its own count gate, 10-31 | 47 events against a 400 floor in the $200M stratum; no treated delister (backlog:32, 240) | 28 | count | Form 4 spec #2435, T305 to T308 (owner backfill), plan PR #2680 with T332 to T344, of which eleven were struck unbuilt |
| 9 | B9 earnings-reaction drift | agent batch 10-09 | 10-09 | rungs 1-2 on a CZ file, 10-18 | long leg +0.07 %/mo, t 0.94; break-even 1.75 to 3.5 bp (backlog:34) | 9 | CZ | hypothesis file #1358; T164 to T164e code (PR #1459, merged 10-10); real-store run never made |
| 10 | B10 short-term momentum | agent batch 10-09 | 10-09 | own-store sweep 10-27 | −7.67 and −14.63 pp/yr; `dsr_excess` 0.0135; "neither rule formally read, no audited trial" (backlog:35) | 18 | store | one family key, one sweep file, trials 23 to 26 |
| 11 | IRRX industry-relative reversal | agent batch 10-09 | 10-09 | rung 2 undecidable 10-18; specified 11-16; stopped 11-20 | no published long leg; then cost: 100 bp/month drag against 29 bp gross (#3684) | 42 | arith | spec amendment #3504, T414 to T422 (about 2,300 planned lines, #3574); PR #3521 merged, #3519, #3676, #3677 open |
| 12 | Long/short momentum spike | owner direction (ADR 0024) | 10-19 | registered `return` run 10-20 | net spread −6.82 pp/yr (`2026-10-20-long-short-spike.md`) | 1 | store | T218, one experiment file |
| 13 | Q1 net repurchasers | agent, "chosen for kill-cost" (backlog:294) | 10-19 | rung 2 | +0.0996 against a 0.10 floor, t 1.12 (backlog:242) | 0 | CZ | none |
| 14 | L-1 candlestick patterns | owner (#1944) | 10-20 | rung-1 arithmetic, parked | fails a 3x-cost line (backlog:243) | 0 | arith | one report |
| 15 | C1 / C1b / C1c filing delay | agent brief 10-27 | 10-27 | registered runs 2, 3 (10-27) and 4 (10-28) | +5 bp gross, t −1.46 on matched peers; "killed on survivor data" (backlog:244; `2026-10-28-c1-filing-delay-results.md`) | 1 | store | three experiment files; scripts outside the repo |
| 16 | C2 spin-off children | agent brief 10-27 | 10-27 | stage 4a (c) | 8 to 12 readable events (backlog:245) | 0 | count | one design file |
| 17 | C3 13D activist drift | agent brief 10-27 | 10-27 | re-scoped, left open | needs the 13D rows (backlog:296) | no verdict | none | parsers in T305 |
| 18 | I1 asset growth | free-model frontier 10-28 | 10-28 | rung 2, 10-30 | +0.0076 %/mo (backlog:37) | 2 | CZ | none |
| 19 | Equity issuance | CZ catalogue | 10-30 | desk read | best cell t 0.84 (retro 2026-11-01) | 0 | CZ | none; 18 exploratory cuts disclosed (backlog:263-284) |
| 20 | CBOperProf | owner (#2675) | 10-31 | rung 2 "as practised" | weak short leg in all six cells (backlog:38) | 0 | CZ | none; T330, T331 not built |
| 21 | Candidate 5, long-only cash profitability | orchestrator (#2689) | 10-31 | store desk read, recorded later as exposure | −0.088 %/mo net against SPY, t −0.24 (backlog:17) | 0 | store | one probe script outside the repo |
| 22 | XFIN | owner (#2828) | 11-01 | rung 2 "as practised" | all six cells fail (backlog:39) | 0 | CZ | none |
| 23 | Composite of weak slow signals | retro 11-01 (#2888) | 11-02 | its frozen line | screened 2016+ t 0.993 against 1.0 (backlog:15) | 0 | CZ | none |
| 24 | `cfp` cash-flow value | orchestrator (#2946) | 11-03 | rung 2 "as practised" | short leg t ≤ 0.36 in every cell (backlog:255) | 0 | CZ | none |
| 25 | L-2 pooled CZ long-leg composite | architecture synthesis (#3092) | 11-06 | its pre-registered line | t 0.53 / −0.02; retires the whole source, TP-10 (backlog:16, 256) | 0 | CZ | calibration scripts outside the repo |
| 26 | E1 earnings reaction against matched announcers | B9 redesign (#2892, #3503) | 11-02 | waits on T164e's owner run; expected `deferred: power` | `filing_events` has 0 rows (`2026-11-02-earnings-event-family-design.md`, Summary) | 18 so far | none | two design files, a kill-window key, an event-builder probe |
| 27 | FC-1 Russell promotions | pilot source C | 11-02 | card, question 7 | about 30 effective events against 36 (backlog:246) | 0 | count | card |
| 28 | FC-2 Nasdaq-100 additions | pilot C | 11-02 | card, question 7 | 14 events against 64 (backlog:247) | 0 | count | card |
| 29 | FC-3 month-end rebalancing | pilot C | 11-02 | card, question 3 | 7 bp against 17.6 bp (backlog:248) | 0 | arith | card |
| 30 | FC-4 settlement-cycle cash | pilot C | 11-02 | card, question 3 | 12 bp against 17.6 bp (backlog:249) | 0 | arith | card |
| 31 | FC-5 tax-loss recovery | pilot C | 11-02 | card, question 7 | 20 events against 225 (backlog:250) | 0 | count | card |
| 32 | FC-6 lock-up expiry | pilot C | 11-02 | card, blocked | cost unmeasured under $50M; no short paper book (backlog:251) | no verdict | none | card |
| 33 | IC-B1 officer purchases, mid-liquidity | pilot source B | 11-02 | card, question 3 | 0.74x break-even (backlog:257) | 0 | arith | card |
| 34 | IC-B2 stealth revisions | pilot B | 11-02 | card, blocked | later statement vintages not kept (backlog:258) | no verdict | none | card |
| 35 | IC-B3 cash-deal merger arbitrage | pilot B | 11-02 | card, blocked | cost unmeasured under $50M; deal terms not held (backlog:259) | no verdict | none | card |
| 36 | IC-B4 low-attention 8-K items | pilot B | 11-02 | card, question 1 (review #2914) | primary evidence finds no inattention (backlog:260) | 0 | none | card |
| 37 | IC-B5 10 %-owner purchases | pilot B | 11-02 | card, question 3 | under break-even (backlog:261) | 0 | arith | card |

Not counted as an idea: B8 (a labelling track, backlog:33). Behind the 37 stand four batch denominators: 10 short-horizon candidates (3 admitted), 7 long/short candidates (0), 15 frontier mechanisms (1), and 50 pilot pairs and flows (11 cards, 39 one-line rejects) (backlog:292-298).

**Totals (37 ideas).**

| Where it stopped | Count | Ideas |
|---|---:|---|
| A return read on our own store | **6** | H1, B3, B10, long/short spike, C1, candidate 5. All failed. The last was 2026-10-31 (ADR 0023:159) |
| A published yardstick | **9** | B9, Q1, I1, issuance, CBOperProf, XFIN, composite, `cfp`, L-2 |
| A count or power gate | **5** | B7, C2, FC-1, FC-2, FC-5 |
| Cost arithmetic, no read | **6** | L-1, FC-3, FC-4, IC-B1, IC-B5, IRRX |
| Missing data or capability | **6** | B5, B6, C3, FC-6, IC-B2, IC-B3 |
| Waiting on an owner run | **1** | E1 |
| Dropped with no test | **4** | B2 (objective), B3b and B4 (dependency), IC-B4 (primary evidence) |

Two more facts from the table. **Verdict time is bimodal:** 22 of the 30 ideas with a verdict got it within two days of entry; the eight that took 9 days or more are the ones that needed a build, an owner run or an owner decision. **Build went where reads did not:** the three largest builds named above (B7's Form 4 ingest and plan, B9's 8-K items path, IRRX's family) produced one count and no return read between them.

## 2. The gates, one by one

Order as in ADR 0023's 2026-11-16 amendment (`docs/decisions/0023-break-first-fast-books-paper-exam.md:166-175`).

| Gate | Meant to filter | Has actually filtered | Cost in time and owner steps | False-kill exposure | False-pass exposure |
|---|---|---|---|---|---|
| **Card, questions 1 to 7** (`docs/specs/idea-pipeline.md:69-81`) | incoherent, uncapturable, unpowered or unbuildable ideas, before any return | 11 cards: 4 rejected on cost (q3), 1 on evidence (q1), 3 deferred on power (q7), 3 blocked on capability (q6); 0 admitted (`docs/research/idea-cards.md:11-24`) | about 3 agent-days for 11 cards and a review; no owner step | q3 used UNVERIFIED gross figures against one cost; q7 used assumed dispersions. Not measurable | passes anything above 17.6 bp round trip, a sixth of what rung 4 charges |
| **4a (a) timestamp** (`research-program.md:44`) | events without a causal `known_at` | nothing | minutes | none seen | none seen |
| **4a (b), (c) counts and power** (`:45-46`) | ideas where a kill would say nothing | C2, FC-1, FC-2, FC-5; B7 through its own 400-event floor; E1 expected | minutes on held data. B7's count needed an owner backfill first (T308) | the bar is t = 1.0 on the **gross** effect; the deciding read is on the **net** effect at 50 bp per side. So it is too lenient for the route, not too strict | passes ideas rung 4 cannot (E1 at +30 bp net needs n_eff 2,500; PR #3683) |
| **4a (d) cost** (`:47`) | gross below round trip | FC-3, FC-4 through q3 | none | at the old 60 bp figure it noted E1 as failing (design, 11-02); at 8.8 bp it passes almost everything | same number as q3 and the rung-1 screen |
| **4a (e) matched benchmark** (`:48`) | size and beta read as signal | one design fault, in hindsight: C1 run 2 against SPY, fixed by C1b | one extra run | none | none |
| **4a (f) delister coverage** (`:49`) | survivor-only kills and passes | C1 "killed on survivor data" (4 of 1,301 exits had bars); B7's zero treated delisters | forces a 2020-08 or 2019 window start until T184h (owner run) | shortens every window to about 40 months, which cuts power | none |
| **Rung 3 capacity** | trades that do not exist at $100 | **never fired** (IRRX preview only: about 98 names fit, backlog:162) | arithmetic | none | none |
| **Rung-1 screen** (ADR 0023:182) | ideas under break-even | B9 (1.75 to 3.5 bp against 6); L-1; B10's was read after rung 4 (backlog:239) | arithmetic | under the 60 bp version of 11-07 to 11-16 it would have killed every fast idea; no idea was formally judged under it | at 8.8 bp it passed IRRX (break-even 14.5 bp) into a nine-task build |
| **Rung 2, now a prior** (ADR 0023:186) | published effects that are absent post-publication | 7 single signals and 2 composites, all before it was demoted | hours each; no owner step | high for the "as practised" rule: at a planted IR of 0.5 its true-positive rate was 12.5 to 24 %, and 0 % when the short leg is dead (ADR 0023:68-77). CBOperProf, XFIN and `cfp` were killed on the short leg. The re-score (#3098) was closed unrun | none now (kills nothing) |
| **Rung 4, the deciding read** (ADR 0023:176-180) | ideas that lose to the yardstick net of the harsh cost | under the pre-route rules: H1, B3, B10 (at the 15 bp base, not the harsh cell). **Under the 2026-11-16 rule: nothing; no read has been made** | one registration and run by the owner; days to weeks of build for a new family; needs T184h for coverage | a real gross IR-0.5 low-turnover edge fails 15 to 23 % of the time; any real edge at monthly turnover fails almost always (PR #3683) | noise passes 39 to 45 % at low turnover for a new root family (PR #3683); 19.0 % through promotion, rung 4 and rung 5 at H1-like costs (ADR 0016:82) |
| **Rung 5 placebos** | a good number that is a bug | **never run** (pass only) | counted trials | none | part of the 19.0 % above |
| **Event study → counted trial** (ADR 0023:181) | nothing new; it turns a matched-peer pass into a family | **never reached** | a new family's sweep file, engine work, one more owner run | judged against SPY, not peers; the engine refuses shorts, so a short-side pass has no road onward (E1 design, section 1) | another look at the same months |
| **Promotion, strategy-lab req 4** | luck within a family's search | no candidate has ever been promoted | owner command | in an old family the bar is real (SR\* 0.35 at N = 15) | for a root family it is rung 4 again: PSR(0) ≥ 0.5 (#3571) |
| **Exam door** (ADR 0023:185) | books that cannot pass the exam at the paper cost | **never reached** | arithmetic | re-prices at 60 bp a figure that is "48 bp without the top 5 names" (ADR 0023:143) | none |
| **Forward return exam** (ADR 0016:197-206) | everything the screens let through | **never started**; ledger empty (`docs/research/exam-ledger.toml:65-67`) | 6 to 36 months of paper per finalist; five class-B PRs per exam; four slots for the programme | fails a real paper-net IR 0.5 edge about 96 to 99 % of the time; "no exam design that keeps four ideas under 5% can confirm an IR 0.5 edge within 3 years" (ADR 0016:192) | at most 1.25 % per exam, 4.9 % for four |

**Redundant.**
- Card question 3, stage 4a (d) and the rung-1 screen are one break-even at one cost (`idea-pipeline.md:8`, `research-program.md:47`, ADR 0023:182).
- Card question 7 is 4a (c); card question 4 is 4a (a), (b) and (f) (`idea-pipeline.md:76, 79`).
- For a new root family, line 40's deflated-Sharpe leg repeats its CAGR leg, and promotion repeats rung 4 (PR #3683; #3571).
- The exam door is rung 4's cost test at a different cost.

**In the wrong order.**
- **The three cost tests run weakest first:** 8.8 bp at the card, 50 bp at rung 4, 60 bp at the exam door. The strongest belongs first, because it is arithmetic and free.
- **IRRX:** the build (T414 to T419) sat ahead of the counts (T420) and of the cost arithmetic that stopped it. The spec that ordered the build said the read was expected to kill (backlog:158).
- **B7:** a 13-task plan was written and merged (PR #2680) before the counts-only gate that struck eleven of its tasks the same day.
- **B10:** rung 4 ran before rung 1 (backlog:239).
- **E1:** an owner run (T164e) stands ahead of a power gate that the design's own expected count cannot pass at any bunching factor on the pinned 2019 window (PR #3683). The deferral can be recorded from arithmetic; the run is needed for other ideas, not for this verdict.

**Never fired:** rung 3, rung 4 at the harsh cell, rung 5, the event-to-trial step, promotion of a candidate, the exam door, the forward exam, and the calibrated rung 2 (its required sensitivity run was never made, ADR 0023:101).

**Thresholds changed after ideas were judged.**

| Date | Change | Ideas judged under the version it replaced |
|---|---|---|
| 10-18 | ADR 0023's kill ladder; rung 2 is long leg ≥ 0.10 %/mo and t ≥ 1.0 (line 40) | H1 and B3 were judged before any ladder, on their files' own rules at the 15 bp base |
| 10-19 | Ledger rule L4: rung-2 windows end 2019-12 (backlog:228) | B9 and IRRX read 2010-2024, not re-read |
| 10-27 | Stage 4a added (#2409) | everything before C1; C1's entry is hindsight |
| 10-30 | One rung-1 cost rule, arrival measure; first search limit; $200M floor (#2667) | B9 (6 bp line), B10 (12 bp line) |
| 10-31 | Rung 2 "as practised": both legs, two files, three windows through 2025, mean floors, 18 checks (ADR 0023:79). It was never written as a rule and it broke L4 | Q1 and I1 were judged on the long leg, 2010-2019 |
| 10-31 | Store desk reads allowed as recorded exposure (#2723); narrowed to registered runs on 11-02 and 11-16 | candidate 5 is the only idea read that way |
| 11-06 | Calibrated rung 2: long-leg t ≥ 2.22, 2009-2025 (ADR 0023:64); the published-signal source retired (TP-10) | CBOperProf, XFIN, `cfp`, the #2888 composite, judged on the rule the calibration found weak |
| 11-06, 11-07 | Forward return exam, four-exam budget, then group-sequential 12/24/36 months (ADR 0016:78, 183, 256) | none; no finalist exists |
| 11-07 | Rung 1 priced at the 60 bp paper basis (ADR 0023:121) | none formally; in force nine days |
| 11-16 | Own-store read decides at 50 bp; rung 1 a screen at 8.8 bp; rung 2 a prior; a candidate counts at rung 4; third limit (ADR 0023:146) | every earlier candidate was counted at a rung-2 read |
| 11-20 | Yardstick becomes the net-matched blend (#3686, in progress) | line 40 "over SPY" applied to every earlier read |

## 3. The sources

| Source | Ideas | What it produced |
|---|---:|---|
| Published anomaly literature and the CZ catalogue (handoff, claims register, frontier check, owner and orchestrator picks) | 17 (rows 1 to 8, 13, 18 to 25) | 3 own-store fails, 9 published-yardstick kills, the rest unrun. Retired as a source on 11-06 (TP-10) |
| Agent short-horizon batch of 10-09 | 3 (B9, B10, IRRX) | all high turnover; all dead on cost |
| Agent "sharper candidates" brief of 10-27 | 3 (C1, C2, C3) | the only own-data event read (C1); one power deferral; one open |
| Pilot source B, the data-edge census | 5 | 0 admitted |
| Pilot source C, the flow calendar | 6 | 0 admitted |
| Owner | 2 (L-1; the long/short spike) | both killed in a day |
| Redesign of a killed idea | 1 (E1) | waiting |

- **Does generation aim at what the gates can pass? No.** The backlog's ranking rule is "information value per unit of cost, not expected return" (`research-program.md:34`), and its header says "the priors are deliberately low" (backlog:5). Q1 was "chosen for kill-cost" (backlog:294). The owner's open question on that ranking rule has stood since 10-03 (`research-program.md`, Open questions, item 1).
- **Shape at entry.** Low turnover and enough events but an edge far under the bar: the 13 slow accounting signals (prior about 7 bp/month gross, G2-S10), B3, C1. High turnover: H1, B9, B10, IRRX, L-1, FC-3, FC-4, E1. Edge of 200 bp or more and a long hold, but too few events: C2 (2 to 4 points on 8 to 12 events) and FC-1 (+2 % on about 30). **All three legs: none.**
- **Does any document state the shape?** Not before today. The card's questions (`idea-pipeline.md:73-79`) ask for break-even at arrival cost and t = 1.0 at the gross effect. The first statement of the passable shape is PR #3683, and the first briefs written to it are #3687 and #3688, both opened today.
- **The pilot was honest about this.** The spec says B and C "are not obviously better than the published pool" (`idea-pipeline.md:57`). The pilot's own test was whether cards improve selection; it ended with no card selected and no read.

## 4. The bottlenecks

Reconstructed from issue and PR timestamps and the plan lines. Splits are our estimates.

| Idea | Proposal → verdict | Agent build | Waiting on an owner run or decision | Process (spec, plan, review, merge) |
|---|---|---|---|---|
| **B3** | 10-03 → read 10-07 → retired 10-18 (15 days) | about 3 days: data spec #660, family #720, T85 to T85e | T78 and T85f ran the day the code was ready (PR #1139, #1158, both 10-07). Then 11 days until the retirement was written, in ADR 0023's batch | within the 3 days |
| **B7** | 10-03 → 10-31 (28 days) | about 3 days: Form 4 spec #2435 and parsers (PR #2482, 10-28) | 15 days ranked behind B3 and B5, 10 days paused by D5 (10-18); the T308 backfill on 10-31 | desk read, kill line, plan and count all on 10-31 |
| **C1 / C1b / C1c** | 10-27 → 10-28 (1 to 2 days) | hours: a script on the existing event-study runner | the owner registered and ran the same day | three experiment files, same day |
| **IRRX** | 10-09 → 11-20 (42 days), no read | about 4 days (11-16 to 11-20): spec, nine tasks, four PRs | 29 days parked behind T198; then gated on T164e and T184h | rung-2 retry notes 10-18; spec amendment 11-16; PR #3521 took 3 days to merge (class B) |
| **E1** | 10-09 (as B9) → none (42 days); 18 days since the redesign | about 2 days: two design files, a key, a probe (#3503) | T164e's run: code merged 10-10 (PR #1459), run still pending, gated on the `restamp-1382a` release (`docs/plans/data-foundation.md:114`) | two design rounds (11-02, 11-16), the second forced by the route change |

**What one idea costs end to end today.**
- A desk read on a published file: hours, no owner step. No longer decides anything.
- A card: about an hour, plus a share of a review. 11 cards took about 3 agent-days (`idea-cards.md:24`).
- An event study on data already in the store: about one day, one owner registration (C1).
- An event study that needs a new ingest: a spec, three to five tasks, and an owner backfill. Three days of work, four weeks elapsed (B7).
- A backtest in a new engine family: a spec amendment, nine tasks, about 2,300 lines, two owner steps (IRRX, #3574). Four days of build bought one merged PR and no read.
- A survivor: all of the above, then one more counted trial, promotion, the exam door, and 12 to 36 months of paper.

**Where the calendar time went, overall.** Own-store candidate reads happened on 10-06, 10-07, 10-20, 10-27, 10-28 and 10-31: six ideas, about three a month. From 11-01 to 11-20 there were none. In those twenty days the programme produced three CZ reads, eleven cards, six ADR amendments (ADR 0023 on 11-06, 11-07 and 11-16; ADR 0016 on 11-06 and twice on 11-07), a round of audits (B and F among them), a retro, an idea-pipeline spec, an exam ledger and a code review. The process has been reviewing and amending itself faster than it has been reading returns.

**What would change it most.**
1. **The two owner runs, T164e and T184h.** They gate every 8-K event idea, every window longer than 40 months and every new family's registration (a family's `in_sample_start` is fixed at first registration, backlog:36). T164e has waited 41 days since its code merged; T184h was specified on 10-12 (#1597), paused on 10-18 (ADR 0023 pause list) and put back in the queue on 11-14 (ADR 0023:163).
2. **Event studies as the default first read (#3574 option 2).** It is the one path that has produced an own-data verdict in a day. It needs no new code.
3. **A generic point-in-time scores family (#3574 option 1).** It turns "a spec and nine tasks" into "one builder script and one sweep file". Worth building once an event-study survivor needs an engine trial, not before.
4. **A counts-only feasibility command on a store copy.** 4a (a), (b), (c) and (f) are read-only counts. Run before any spec or plan, it would have saved B7's plan and IRRX's build order. The queries exist in the B7 recount's appendix and C1b's script.
5. **Batching owner steps is already the rule** (ADR 0023, "the owner's time") and has not been enough: the 11-09 sitting had 17 steps (STATUS.md, In progress) and T164e's line is still unticked (`docs/plans/data-foundation.md:114`).

## 5. The rules' consistency

Items 1 to 6 are PR #3683's; they stand and are not repeated in full.

| # | Where | The disagreement or gap |
|---|---|---|
| 1 | ADR 0023:40 against `backtest.md` open question 40 | The yardstick for a book that is not net long. Being fixed (#3686) |
| 2 | ADR 0023:40 against :177 | Is the one-session delay still in the harsh cell? |
| 3 | ADR 0023:40 against `results.py` | "`dsr_excess` under those settings" is computed at the base level only |
| 4 | ADR 0023:177 | "At least 50 bp … twice a base of at least 25 bp": a floor, or twice the band's measured base? This decides whether the cost work can ever lower the cell |
| 5 | ADR 0023:178, #3572 | "Read at the registry's N" has three V's and three SR\* values for one trial (0, about 0.4, about 1.3) |
| 6 | ADR 0023:181 | The event-study pass and the counted trial that follows are judged against different yardsticks |
| 7 | `idea-pipeline.md:8-12` against its own lines 75, 101, 113 and 251 | The 11-16 header reverses the body; the body was not edited. Question 3 still cites "60 bp round trip" and a margin; requirement 5 still says a rung-2 read makes a candidate, that windows end 2019-12, and that promotion is read at N + k |
| 8 | `research-program.md:55` against :57 | "Must be ≥ 2.22" and "kills nothing" in consecutive paragraphs. Line 34 still says "today only the `momentum` family exists" |
| 9 | Backlog:17 and :15 against ADR 0023:206 | Three definitions of a candidate. Limits one and two counted at "a rung-2 read or a registered event-study verdict"; limit three counts at a rung-4 verdict. B7 was counted with no return read; under today's rule it would not count. Whether Q1 counts was left to the owner and never answered |
| 10 | ADR 0023:206 | **The third limit cannot close by count.** Screen kills and 4a deferrals do not count. IRRX is parked, E1 is expected to defer, FC-1 is conditional. The limit ends on 2027-01-31 with a tally of 0, which is neither a survivor nor a clean stop |
| 11 | ADR 0023:198 against #3571 option 1 | The ADR chose "N + k is printed, not a gate" because a gate "would stop any 40-month read". #3571 proposes to seed a root family's mark from k. With the ledger's k "above 50" (ADR 0023:191) that is SR\* about 1.28: the gate the ADR rejected four days earlier. With k = 20 it is about 1.2 (#3571). Which k is meant is not written anywhere |
| 12 | 4a (c) (`research-program.md:46`) against rung 4 | Power is screened at t = 1.0 on the gross effect; the read decides on the net effect at 50 bp per side with its own t floor. An idea can pass 4a (c) and have no chance at rung 4 |
| 13 | Card q3 / 4a (d) / rung-1 screen against rung 4 against the exam door | Three costs for one idea: 8.8, 50 and 60 bp per side. Only the last two decide, and the last is higher than the one before it |
| 14 | ADR 0023:179 with backlog:36 | A pass needs delister coverage over the whole window, "otherwise the window starts where coverage holds", and a family's start is fixed at first registration. So a new family either waits for T184h or is held to 40 months for good |
| 15 | ADR 0023:162 with `idea-pipeline.md:259` (D10) | 2024-01 to 2026-09 are dead months for a new family, and a pipeline candidate also forgoes the point-8 historical exam. A third of the held history serves neither development nor confirmation |
| 16 | ADR 0016:116, :192, :229 with `docs/charter.md:13` | **The route cannot end in a survivor on the charter's clock.** Four slots; each held up to 36 months; started exams count; noise fills slots at 39 to 45 % per low-turnover rung-4 read; a real IR-0.5 edge passes 1 to 4 %. Given a pass, the median time to a live order is 42 to 46 months (ADR 0016:228). The charter asks for a continue-or-stop decision at six months |
| 17 | ADR 0005:22 against ADR 0023:40 | The objective says beating a benchmark is "explicitly optional and not a criterion"; every kill rule is "net excess over SPY below zero" |
| 18 | E1 design, section 1, against ADR 0024 | The long-only constraint was lifted on 10-19; the route's engine step refuses shorts, so a short-side event pass cannot reach an exam |
| 19 | Backlog:35; `docs/plans/backtest.md:88` | B10 is retired "by the owner" while "neither rule formally read, no audited trial"; T165e is still unticked. A kill that no written rule produced |
| 20 | `docs/STATUS.md` (updated 11-07) | Says "Rung 1 prices exam-eligible ideas at 60 bp"; superseded on 11-16. CLAUDE.md tells every session to read it first |
| 21 | ADR 0023:101 | The calibrated rung 2 requires a sensitivity run "before the first read"; none was run; the rule is now optional. Nothing says whether a prior-setting read still needs it |

**Rules that stop the process ending in a survivor:** 13, 14, 16, and 11 if #3571 option 1 is taken with the ledger's k. **Rules that stop it ending in a clean stop:** 9 and 10. **Rules that make a verdict mean something different depending on where it is read:** 3, 5, 6.

## 6. Overall judgement

Why no strategy yet, weighted. The weights are a judgement; the evidence under each is from the records.

- **(b) The sources, 35 %.** About two thirds of the 37 were published effects or one step from one (the 17 literature rows, the three short-horizon rows, and the cards the ledger marks "paper"), and the backlog entries carried a written prior of about zero. The ranking rule rewards cheap kills. Nothing told an author the passable shape until today. The two own-data sources admitted 0 of 11 cards. The one own-data event read (C1) was chosen for using "the one data edge we own", with a published sign and no published size.
- **(c) The gates and the cost basis, 25 %.** Not for the past kills: the machinery audit found no defect large enough to explain them (#3679 to #3681), and the pooled long-leg test (L-2, t 0.53) makes it unlikely the weak 10-31 rule killed a real slow signal. For the route going forward, yes. At 50 to 60 bp per side nothing that trades monthly can pass, and that figure comes from open fills in thin names (ADR 0023:160), not from the names a floor-screened book would hold. Rung 4 cannot tell a low-turnover edge from noise (39 to 45 % against 77 to 85 %). The exam can, at a price of 1 to 4 % power for an ordinary real edge.
- **(a) The market, 20 %.** In the region searched (liquid US names, public signals, daily bars, monthly or slower trading) the published evidence already said "about 7 bp a month before cost" and our reads agreed nine times out of nine. That is a real finding about that region. It says nothing about regions not searched.
- **(d) Throughput, 15 %.** Six own-data reads in eight weeks and none in the last three. The cause is not agent speed. It is two pending owner runs, one family per idea, and three weeks spent on rules and audits.
- **(e) The objective, 5 %.** H1, B3 and candidate 5 were equal-weight books judged against cap-weighted SPY over 2020-08 to 2023-12; B11 showed H1's apparent win was beta. A matched yardstick would not have made any of them an edge. #3686 fixes the rule for the books where it does matter.

Said plainly: the project built a careful instrument for measuring edges and then fed it ideas it did not believe in. The instrument is not the first problem. But it has also been tightened, step by step, to the point where a real edge of ordinary size would not get through either, so fixing the sources alone will not be enough.

## 7. Recommendations

Ranked by how much each raises the chance of a real survivor per month of calendar time. None adds a step; where a step is added, others are removed.

| # | Who | What changes | Cost | False-pass risk |
|---|---|---|---|---|
| 1 | Agents (one class-B docs PR, owner's word) | **Put the passable shape on the card and collapse the cost gates into it.** In `idea-pipeline.md` requirement 3 and `research-program.md` 4a: replace question 3, 4a (d) and the rung-1 screen with one line, "gross per bet ≥ 2 × the round trip at the cost rung 4 will charge", and replace question 7 and 4a (c) with "n_eff ≥ the PR #3683 table at the idea's own effect and hold" (255 at 200 bp and 20 sessions; 765 at 60 sessions). Replace the backlog's ranking rule with "chance of a rung-4 pass per unit of cost". Three gates become one; two become one | one PR, half a day | none: it is a pre-read screen. It will reject ideas with moderate edges, which rung 4 would reject later anyway |
| 2 | **Owner, on his machine** | **Run T164e (with the `restamp-1382a` release it waits on) and T184h in one sitting.** They unblock every filing-event idea that #3687 returns, the guard on `filing_events`, 84-month windows and every new family's registration | two size-S owner runs, one sitting | none |
| 3 | **Owner** (decision and one switch), then agents | **Get the cost measured where a screened book would trade.** Un-stall PR #2953 (T377), decide T386 so T378 can run, and let T379 read 20 sessions mid-session over $200M. Decide ambiguity 4 in the same breath: the harsh cell is twice the band's measured base, not a fixed 50 | one owner decision, one owner switch, about a month of sessions | real: at 12 bp per side the null passes 5 to 6.5 % for turnover books (PR #3683). The forward exam still bounds a false live order at 4.9 % |
| 4 | **Owner** (#3574, one word), then agents | **Take #3574 option 2 now:** every event idea's first read is the registered event-study runner; no engine family before an event-study pass. Record E1 as `deferred: power` from the arithmetic, without waiting for a count. Commission option 1's spec only when a survivor needs it | a backlog rule; no code | none added; the event-study t runs high under the null until #3679 is fixed, so fix that first |
| 5 | Agents | **A counts-only feasibility command on a store copy** (4a (a), (b), (c), (f) in one read-only call, boundary-respecting, no returns), and the rule that it runs before any spec or plan is drafted. This moves an existing gate earlier; it adds none | size S to M; the queries exist | none (no return is read) |
| 6 | **Owner** (#3571 and #3572, decided together) | **Say what rung 4 is, and merge what repeats it.** Recommended: one decision statistic (#3572 option 1), read once, at the cost the exam will charge; drop the separate exam-door re-read and, for a root family, the separate promotion read, since both repeat it. If rung 4 is to carry evidence, add PR #3683's t ≥ 1.0 on the net mean rather than seeding from k | one ADR note and about 120 lines (#3572) | lowers it: the low-turnover null falls from 39 to 45 % to about 10 to 12 %. It also cuts a real IR-0.5 edge's pass from about 80 % to about 45 % |
| 7 | **Owner** (ADR 0016 point 2, D10) | **Let a rung-4 passer be killed on its dead months (2024-01 to 2026-09) before it takes an exam slot** (stage 5b, kill only; a pass changes nothing). It replaces 6 to 36 months of paper for the noise that rung 4 lets through | one class-B amendment and a small refusal change | none if a pass confers nothing. Not clean for a published signal already read through 2025 (ADR 0016:145) |
| 8 | Agents (one class-B docs PR) | **Make the limit able to end, and stop amending.** A candidate counts at its first recorded verdict after a posted line, deferrals included, so the third limit can close by count. Delete the superseded body text in `idea-pipeline.md` and `research-program.md` instead of adding dated notes. Refresh STATUS. Then no further amendment to ADR 0016 or ADR 0023 beyond #3686, #3571 and #3572 until one idea has a rung-4 verdict | one PR | none |

Not recommended: more published-signal desk reads; another card pass under the old questions; loosening the forward exam; building the scores family before an event-study survivor exists.

## Caveats and gaps

- **Time splits are from timestamps, not logs.** The retros do not record build and review hours separately, though ADR 0005 asks for it (line 33).
- **We could not establish why T164e's run was not made at the 11-09 sitting,** or whether `restamp-1382a` has run. Issue #1382 is closed (10-20); the plan line still names the gate.
- **We could not establish the outcome of the 11-09 sitting.** STATUS was last updated on 11-07.
- **B10's and H1's stored figures are quoted from the backlog,** which quotes a read-only store check of 11-06. We opened no store.
- **The past-kill false-kill rate is not measured.** #3098 was closed without a re-score. Our "unlikely" rests on L-2's pooled long-leg result and on `cfp`'s screened long leg (t 1.19 to 1.95, below 2.22).
- **The count of 37 depends on what is called an idea.** With the batch one-line rejects it is 37 named plus about 70 unnamed. With B8 it is 38. With C1b and C1c apart it is 39.
- **The source weights in section 6 are a judgement.** The records can show what was searched and how; they cannot show what an unsearched region holds.
- **The two shape briefs (#3687, #3688) are still running.** If they return nothing of the passable shape, the weight on the market rises and recommendation 1 will have little to select from.

## Sources

- `docs/research/hypothesis-backlog.md` (notes at lines 5 to 19; summary 25 to 39; parked 165 to 181; ledger 205 to 298), `docs/research/idea-cards.md`, `docs/research/exam-ledger.toml`, `docs/research/trial-registry.md`
- `docs/specs/idea-pipeline.md`, `docs/ways-of-working/research-program.md`, `docs/specs/strategy-lab.md` (req 4)
- `docs/decisions/0023-break-first-fast-books-paper-exam.md` (line 40; amendments 2026-10-30, 11-06, 11-07, 11-16), `docs/decisions/0016-development-boundary-and-forward-exams.md` (amendments 2026-11-06, 11-07), `docs/decisions/0005-objective-benchmark-stop-criteria.md`, `docs/charter.md`
- `docs/retros/2026-11-01-strategy-search-retro.md`; the desk reads and kill notes under `docs/research/` dated 2026-10-18 to 2026-11-16; `docs/experiments/`, `docs/hypotheses/`, `docs/sweeps/`
- `docs/plans/backtest.md` (T85f, T165e, T414 to T422), `docs/plans/data-foundation.md` (T78, T164e, T184h, T305 to T308, T324), `docs/plans/research-registry.md` (T342), `docs/plans/paper-trading.md` (T377 to T379), `docs/STATUS.md`
- GitHub issues and PRs, read through the REST API on 2026-11-20: #620, #660, #720, #1020, #1139, #1158, #1353, #1358, #1382, #1459, #1597, #1731, #2403, #2409, #2435, #2482, #2673, #2674, #2680, #2689, #2828, #2835, #2888, #2892, #2896, #2907, #2908, #2914, #2946, #2953, #3092, #3098, #3125, #3143, #3146, #3496, #3503, #3504, #3519, #3521, #3571, #3572, #3574, #3676, #3677, #3679 to #3684, #3686 to #3689
- The rung-4 pass arithmetic: PR #3683 (brief #3669)

## Appendix: evidence behind three claims

**A. "Three weeks without an own-store read."** ADR 0023's 11-16 amendment lists the store return reads after 10-18 (B10's sweep on 10-27; C1 run 2 and C1b run 3 on 10-27; C1c on 10-28; candidate 5 on 10-31) and states: "Since 2026-11-01 there has been no candidate return read on the store: the second limit's three reads (#2888, #2946, #3092) were all Chen–Zimmermann (CZ) files" (lines 153 to 159). Nothing after 11-16 changes that: IRRX's read (T421) and E1's are both unmade.

**B. "The 10-31 rung-2 rule was weak where it was used."** From the 11-06 calibration quoted in ADR 0023 (lines 66 to 77): at a planted IR of 0.5 and a matched false-discovery rate of 10 %, the rule as practised had a true-positive rate of 24.3 % against 70.2 % for a single-window t ≥ 2 rule; "when the short leg is dead, the current rule has 0.0% TPR at any FDR ≤ 20%". CBOperProf ("weak short leg in all six cells"), `cfp` ("short leg t ≤ 0.36 in every cell") and XFIN ("VW short-leg t below 1.0") were killed on that leg (backlog:38, 39, 255). #3098 listed all eight past kills for a re-score and was closed the same day because TP-10 had retired the source.

**C. "The exam cannot confirm an ordinary edge in time."** ADR 0016's 11-07 amendment, quoting the strictness report: a real net IR 0.5 edge "reaches a live order in 1.45% (slow) and 0.18% (event) of four-exam programmes" as written (line 188); under the group-sequential exam with the cost work done, 12.1 % (ADR 0023:139); "given a pass, the median time to the first live order rises from about 22 to about 42-46 months" (ADR 0016:228). PR #3683's cross-check gives 3.8 % per exam at a paper-net IR of 0.5 and 43 % at 1.5.
