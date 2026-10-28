> **Provenance (added by TradePartner, not part of the report).** This is an external report the owner uploaded on 2026-10-27, saved unedited below the rule. It was not produced by a TradePartner agent. **Its citations are unverified**: no source was opened. Two are marked **UNVERIFIED** in particular: the 2026 preprints **arXiv 2608.28632** (AutoScientist-Quant) and **SSRN 7555699** (Does Autonomous Quant Research Improve?); do not cite either as evidence until someone opens it. Its claims are not in [claims.toml](claims.toml) and are context, not evidence ([research-program](../ways-of-working/research-program.md) stage 1). The owner adopted its Gates 0 and 1 only (#2409); how this maps onto what we already have is in [the Gate 0 note](2026-10-27-discovery-system-gate0.md).

---

# TradePartner: Automated Strategy Discovery Systems — Research Findings and Architecture

**Date:** October 27, 2026
**Type:** Research report, read-only
**Status:** Proposed architectural direction, not implementation approval
**Repository:** https://github.com/josejuarez96/tradepartner

## Executive verdict

A strategy *tester* answers whether a specified rule appears to work. A strategy *discovery system* determines which mechanisms to investigate, generates diverse testable hypotheses, allocates a limited research budget, learns from informative failures, and hands frozen candidates to an independent assessment process.

**Recommendation:** Adopt a staged, hybrid *discovery workbench* centered on mechanism-guided hypothesis generation, machine-readable research memory, read-only data profiling and economic feasibility screens. Evaluate genetic programming, symbolic regression, ML-assisted feature generation, and autonomous agent search against simple baselines before promoting them into the default workflow. Keep the existing Research Lab and forward paper exam as the validation authority.

**Confidence:** High on selection-bias and test-contamination risks; medium on feasibility of a low-cost discovery workbench; low on which algorithm will yield persistent net alpha for TradePartner. No algorithms were benchmarked in this investigation. Repository facts are from docs and GitHub PRs/issues; the owner's private data store was not queried.

## 1. Existing TradePartner work to preserve

The repository already has a substantial discovery-adjacent report: `docs/research/2026-10-19-strategy-discovery.md`, which identifies (a) lack of systematic counting for preliminary rungs 1–3, (b) incomplete propagation of negative evidence into claims, and (c) the need for a mechanism test on non-return outcomes when no published long leg exists. It explicitly recommends using existing artifacts, not building a new engine. This report should be treated as a predecessor, not superseded.

Other core components already exist:

- `docs/decisions/0016-development-boundary-and-forward-exams.md`: global development cutoff (recommended 2023-12-29), protected examinations, frozen finalists, and forward paper evaluation.
- `docs/research/trial-registry.md`: hypothesis/trial/decision lineage and trial accounting.
- `docs/research/2026-10-18-daily-data-readiness.md`: practical constraints including history beginning in 2016, in-sample from roughly 2020-08 to 2023-12 (about 40 monthly observations), point-in-time data defects and cost constraints.
- `docs/research/2026-10-27-sharper-candidates.md`: event-driven candidate generation, economic kill ladders, power and coverage checks. Later C2 design PR #2403 documents only 12 readable events at six months and eight at twelve months for its restricted sample, illustrating the difference between an intriguing mechanism and a testable candidate.
- Research registry, sweeps, family controls, and a paper-trading pathway already provide the downstream experimental discipline.

**Gap:** There is no documented general-purpose system that *compares candidate-generation methods*, tracks the origin/coverage of every generated idea, uses failure findings to prioritize the next question, allocates discovery budget, and demonstrates superior *out-of-sample discovery productivity* relative to simple research baselines.

## 2. Evidence map: what works, what remains unproven

| Method | Core idea | Strength | Principal failure mode | Fit now |
|---|---|---|---|---|
| Mechanism-guided research | Identify forced trades, delayed response, institutional constraints and verifiable events | Interpretable; naturally permits cheap non-return tests | Stories can be fabricated after returns are seen | **High** |
| Literature-assisted generation (LLM) | Map evidence to explicit, timestamped, falsifiable candidates | Fast source synthesis and diverse candidate framing | Citation hallucinations, duplicated known anomalies, selective interpretation | **High, with verification** |
| Rule grammars / constrained enumeration | Compose a small set of lagged inputs and operators | Cheap, auditable, easy to compare | Combinatorial multiple testing; trivial variants | **Medium** |
| Symbolic regression / genetic programming | Evolve formulas to optimize a predictive or economic objective | Interpretable expressions, broad exploration | Reward hacking and repeated feedback overfitting | **Pilot only** |
| Reinforcement-learning alpha generation | Learn a policy for creating complementary formulas | Searches interacting alpha collections | Expensive, opaque reward design, dataset-specific evidence | **Later** |
| Supervised ML feature attribution | Discover nonlinear associations or interactions with returns | Useful for screening conditionality and combinations | Not causal; instability, leakage and tuning | **Later**, after feature tests |
| Fully autonomous research agents | Generate, code, test, revise, select and stop | Can reduce researcher labor | Test-window contamination; high trial volume; false sense of independence | **Not default yet** |

### Representative primary research

1. **AlphaGen** (KDD 2023), *Generating Synergistic Formulaic Alpha Collections via Reinforcement Learning*: reinforcement learning for formulaic alpha collections. Code: https://github.com/ICT-FinD-Lab/alphagen . Its existence establishes technical feasibility, not performance transferability.
2. **R&D-Agent(Q)** (NeurIPS 2025), *A Multi-Agent Framework for Data-Centric Factors and Model Joint Optimization*: a research-development-feedback loop, bandit allocation and joint factor/model work. Paper: https://www.microsoft.com/en-us/research/publication/rd-agent-quant-a-multi-agent-framework-for-data-centric-factors-and-model-joint-optimization/ ; code: https://github.com/microsoft/RD-Agent . The authors' returns are benchmark claims, not verified deployment results.
3. **AutoScientist-Quant** (2026 preprint): budget-aware controller choosing improve/combine/pivot/stop, with separation of feedback and final test. https://arxiv.org/abs/2608.28632 . Interesting architecture; limited independent replication evidence.
4. **Does Autonomous Quant Research Improve?** (October 2026 preprint): analyzes an LLM evolution loop with about 2,500 evaluations and investigates feedback leakage on subsequent data. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7555699 . Preliminary but unusually directly relevant warning.
5. **The Deflated Sharpe Ratio** (Bailey and López de Prado, 2014), https://doi.org/10.3905/jpm.2014.40.5.094 ; **The Probability of Backtest Overfitting** (Bailey et al., 2017), https://scholarworks.wmich.edu/math_pubs/42/ . Formal techniques for selection-bias diagnosis; no single metric is a license to query the same holdout repeatedly.
6. **… and the Cross-Section of Expected Returns** (Harvey, Liu, Zhu, 2016), https://doi.org/10.1093/rfs/hhv059 ; hundreds of candidate factors require stronger evidence standards than isolated t-tests.
7. **Does Academic Research Destroy Stock Return Predictability?** (McLean and Pontiff, 2016), https://doi.org/10.1111/jofi.12365 . Across 97 studied predictors, average portfolio returns were 26% lower out of sample and 58% lower post-publication; these are study averages, not a discount to mechanically apply to every TradePartner signal.
8. **Open Source Cross-Sectional Asset Pricing** (Chen and Zimmermann, 2022), https://doi.org/10.1561/104.00000112 . Useful reproducibility baseline for published factors; replicability is not investability.

**Research limitation:** The review prioritized representative methodology and core validity papers, not a systematic meta-analysis. Claims of net trading profitability by agent systems were not independently reproduced. No backtests were run.

## 3. Recommended architecture

### A. Evidence and discovery memory

Every candidate gets a versioned, immutable `idea_id`, origin, creation timestamp, author/model, sources, market mechanism, target universe, prediction horizon, target variable, preliminary looks, constraints, relatives/duplicates, and decision. Attach every dataset probe and preliminary result, including negative or inconclusive work. Distinguish *theoretical* claims, *observed* results, and *untested* conjectures.

Avoid constructing a graph database initially: a typed file or small relational table, exportable to human-readable Markdown and backed by the existing research registry, is enough for a pilot. Inspect whether the existing claims and registry can hold these fields before inventing schemas.

### B. Candidate-generation routes (run as separate experimental arms)

- **Baseline A:** human/agent literature synthesis using the existing backlog and kill ladder.
- **Baseline B:** mechanism-first, event-based candidate generation, with mandatory non-return predictions.
- **Arm C:** constrained grammar of simple point-in-time formulas and feature interactions on already-held daily data.
- **Arm D:** LLM proposes structured hypotheses, but deterministic validators enforce data availability, temporal causality, costs and duplicate detection.
- **Optional Arm E:** small genetic-programming or symbolic-regression pilot, with exactly the same data access and research budget as baseline controls.

Do not conflate hypothesis generation with optimizer tuning: every formula and mutation generated in response to performance feedback counts as an exploratory attempt.

### C. Feasibility router, before any backtest

Reject or defer ideas that lack a causal timestamp, enough available observations, adequate breadth, implementable fills/borrow/fractionability, or a plausible gross edge above conservative transaction costs. For a non-return mechanism, test whether the proposed *observable intermediate consequence* exists before asking about returns. This makes failures informative without exposing protected returns.

### D. Bounded experiment allocator

For each candidate, record expected information gain, likely power, estimated cost and novelty. Choose among *reject*, *retrieve evidence*, *non-return probe*, *small return study*, *full Research Lab sweep*, *pivot*, and *stop*. The planner can use heuristics first. A bandit or Bayesian allocator is justified only if the logged observations show genuine improvement, and it must never treat protected-exam results as development reward.

### E. Validation boundary

The discovery process is an optimizer over development feedback, regardless of whether decisions are made by people, LLMs or GP. Use the existing frozen development boundary. Maintain a clean distinction among:
- training/exploration data, freely re-used but recognized as in-sample;
- an **internal development comparison**, useful for choosing research direction but not a final exam;
- strictly one-time protected historical exam, where available and truly untouched;
- ongoing prospective paper exam for frozen finalists.

Pre-register the final candidate, portfolio construction, cost stress, allowable changes, comparison benchmark and stop rule *before* the exam. Do not spend one untouched test on a tournament and then call the winner out of sample. Later modifications become new candidates needing new forward evidence.

## 4. Discovery-system benchmark: proposed experiment

**Question:** Compared at the same research budget, does a discovery method generate more *credible, independent, feasible candidates* than current manual/literature-led work?

**Design:** Run four candidate-generation arms (human/literature, mechanism-guided, constrained grammar, LLM-assisted) against the same documented snapshot of TradePartner's usable data. Each arm receives the same available-source list, the same maximum number of candidate submissions, the same maximum compute/tool budget and the same known historical-exam restrictions. A separate adjudicator, blind to which arm created a candidate, scores feasibility and clarity. Deduplicate near-equivalent hypotheses across arms. Use fixed ordering or randomize the sequence of arms to limit learning spillover. Do not allow agents to read others' result feedback within the comparison.

**First-round budget (proposed, not evidence based):** 4 arms × 10 candidates, at most 2 preliminary non-return probes per candidate, and no more than 2 full backtest candidates total across the pilot. This caps exposure, not statistical power; a pilot of this size cannot establish superiority in alpha generation.

**Primary outcome:** blinded proportion of unique candidates that (i) are implementable with known data, (ii) have an explicit falsifiable mechanism or statistical conjecture, (iii) pass predetermined economic and power feasibility gates, and (iv) are not duplicates of known trials.

**Secondary outcomes:** human/agent minutes per admissible idea; dollars/CPU per admissible idea; total attempted looks; diversity across economic mechanisms; rates of coding or PIT errors; rate of informative rejection; incremental correlation of resulting signals; and independent *future* economic performance only when enough prospective evidence accrues.

**Negative controls:** shuffled outcome labels, random timing and implausible lags should not be repeatedly rewarded; code- and information-boundary tests must ensure the agent cannot accidentally query forbidden dates. If a shuffled-label task looks successful, stop the pilot and audit the scoring interface.

**Critical interpretive rule:** A method winning on feasibility is a more efficient research assistant; it is not thereby a source of alpha. A method winning a development backtest is an optimizer of that development period; it is not thereby independently validated.

## 5. Discovery search space for TradePartner

Favor opportunities that exploit actual data/institutional asymmetry:

1. **Issuer calendar deviations:** punctuality, missing filing, corrective filing, and acceptance-time shifts; test an intermediate accounting outcome and size/sector control before returns.
2. **Forced holders and corporate events:** spin-offs, delistings, merger conversions, index-driven flows; verify event count and point-in-time child-parent mapping before returns. The current C2 store coverage is too low for a decisive historical result.
3. **Liquidity and capacity interactions:** small and hard-to-own securities, but require reliable spread/borrow/partial-fill approximations and survivorship-robust universe membership.
4. **Disclosure changes:** structured change detection with versioning and amendment tracking; typed models/LLMs may create *features*, but those outputs require annotation, calibration and PIT timestamping, and are not automatically strategies.
5. **Event-conditioned factor behavior:** test whether known effects become economically stronger in specifically defined environments; guard strictly against retrospective condition selection.

Do not precommit to any of these as profitable. They are families of research questions, not recommendations to trade.

## 6. What not to build yet

- A sprawling multi-agent orchestration layer parallel to the existing orchestrator.
- Unbounded parameter/grammar generation with repeated score feedback.
- A general purpose semantic market knowledge graph before proving that a simple idea ledger helps.
- New historical-data procurement solely for a candidate without showing that its coverage and statistical power solve a specific bottleneck.
- A sophisticated reinforcement-learning controller trained on TradePartner's small, highly dependent sample of research outcomes.
- Promotion rules that automatically treat a discovery agent's favorite backtest as confirmed evidence.

## 7. Implementation decision gates (not authorization)

**Gate 0: Reconcile existing work.** Map this proposal to the 2026-10-19 strategy discovery report, the claims register, registry, ADR 0016 and experimental families. Identify the exact fields not already available.

**Gate 1: Manual protocol.** Run a small pilot with candidate templates, duplicate checks and feasibility scoring, no engine modification. Verify that an independent operator can reproduce each decision.

**Gate 2: Read-only automation.** Automate candidate intake, data coverage checks and source/citation logging; keep all market-return tests through the existing Research Lab interface.

**Gate 3: Comparative experiment.** Execute the fixed-budget, blinded study above. Advance only if the alternative is demonstrably better on preregistered *research productivity* measures and does not increase false positives or prohibited looks.

**Gate 4: Statistical discovery pilot.** Only then trial a narrow GP/symbolic grammar. Require noise controls and continued accounting for the full search budget, including failed candidates.

**Gate 5: Forward evidence.** Send the best frozen candidates through existing paper-exam policies; do not claim economic performance from development feedback alone.

## 8. Open questions requiring owner/store evidence

1. What current datasets are actually populated and usable, not merely specified in code? Which start dates and security coverage apply to each event type?
2. How many preliminary economic and store probes have already been attempted but never entered the trial counter?
3. Does the current claim registry preserve sufficiently rich negative evidence for retrieval by future discovery agents?
4. What exact compute/model-dollar and investigator-time budgets are available for each research method?
5. Which evaluation periods are now inaccessible because humans or agents have already seen their results? A held-out period is not clean merely because code denies access later.
6. Which brokerability and cost assumptions can be established independently of misleading paper fills?
7. What threshold of independent forward evidence would justify expanding beyond research into systematic deployment?

## Final recommendation

Treat **strategy discovery** as a separate, measurable discipline upstream of the Research Lab, but implement the first version as a *research protocol plus thin automation*, not as a new quantitative engine. The first scientific milestone is not discovering a winner: it is demonstrating, under fixed and transparent budgets, that a proposed discovery process generates more unique, feasible, economically credible hypotheses than existing baseline research **without consuming or corrupting protected examination evidence**.

No repository changes were made; no model, trader or store was executed.
