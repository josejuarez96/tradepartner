# Research Program: how research turns into tests, and tests back into knowledge

**Status:** Draft v0.2 (#620; [changes](#changes) at the end) · **Owner:** Jose · **Related:** [development-process.md](development-process.md), [charter](../charter.md) principles 2 and 6, [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md) (objectives), [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) (the development boundary, forward exams), [backtest spec](../specs/backtest.md) (hypotheses, registry, holdout), [strategy-lab spec](../specs/strategy-lab.md) (families, N, the deflated Sharpe), #1301 (the owner's discussion of 2026-10-08)

Three questions: what do we test next, and why; how does a research finding become a test; what happens to a result. This page adds two living files and one loop and changes no rule in the charter, the ADRs or the backtest spec.

## The loop

1. A **research report** lands in `docs/research/YYYY-MM-DD-<slug>.md`, frozen once merged; newer research supersedes it.
2. Its decision-relevant findings enter the **claims register**, [`docs/research/claims.toml`](../research/claims.toml): living, one entry per finding, with grade and source.
3. Claims feed the **hypothesis backlog**, [`docs/research/hypothesis-backlog.md`](../research/hypothesis-backlog.md): living, ranked by the owner.
4. A backlog item is promoted to a **hypothesis file**, `docs/hypotheses/<slug>.md`, frozen once registered (backtest spec req 10).
5. It runs as **trials**; the store's registry (`hypotheses`, `trials`, `trial_results`) is append-only.
6. The **result** becomes a TradePartner claim (`TP-`), back into step 2. A claim that would change the tool goes to an **ADR** (`docs/decisions/NNNN-<slug>.md`, append-only, citing the claim ids).
7. A backlog item blocked by an unknown commissions a new **research brief**, back to step 1.

## Rules per stage

### 1. Research enters

- **Any research document may be added to `docs/research/` at any time**, as a docs-only change.
- **A report is context, not evidence**, until its decision-relevant findings are in the claims register with a grade. Agents act on the register, not on prose they skimmed.
- A **research brief** ([template](../templates/research-brief.md)) is commissioned only when a backlog item or a decision is blocked by an unknown; curiosity alone is not a reason.

### 2. Claims register

- **Who adds:** whoever lands a report adds its claims, in the same change or a follow-up. An entry is one sentence, faithful to the source and never stronger; fields are defined in the file header.
- **Grades** follow the evidence protocol in [handoff §6.1](../research/2026-09-24-initial-research-handoff.md): SUPPORTED, MIXED, NOT SUPPORTED, INSUFFICIENT. A claim without a grade is `UNGRADED` and never counts as support.
- **Conflicts:** both claims stay, each naming the other in `note`. Resolving a conflict is a decision or a test, never an edit.
- **Regrading** needs new evidence (a newer report, or a TradePartner result, stage 6): add the evidence and note the old grade, never overwrite silently.

### 3. Hypothesis backlog

Each entry states the claims it tests; the **prior** (the expected effect, normally at or below the publication-decay haircut, claims QI-2 and G2-S10); the **data** it needs and whether we have it; the **engine work** it needs (today only the `momentum` family exists); **cost** (S, M or L); the **kill criterion**; and the **information value**, which decision changes if it passes or fails. Rank by **information value per unit of cost**, not expected return: a test that would retire a whole line of work cheaply ranks above an exciting test that needs new data. The owner sets the order; agents propose changes with reasons and never reorder it themselves.

### 4. Promotion: backlog → hypothesis file

An item is promoted only when all five hold: (1) its data exists **point-in-time** in the store, or a data spec for it is merged; (2) the engine can run its family, or the engine work is a merged plan task; (3) its prior, kill criterion and holdout are written in a hypothesis file from the [template](../templates/hypothesis.md); (4) the family's trial budget is stated, how many variants will run, all of them counted (QI-11); (5) the owner merges the hypothesis file and runs `hypothesis register`. Semantic and model-derived features have stricter gates, the [ML validation brief](../research/2026-10-03-ml-validation-methodology-brief.md#2-scope-and-stage-gates); none is promoted before those pass.

### 5. Test

Unchanged: every run is a trial; the holdout is spent once; results are reported beside SPY and MTUM; deflated Sharpe uses the family's trial count. Which data a run may read is [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md)'s rule; the principles are under [Zones](#zones).

### 6. Result → register

When a hypothesis reaches a verdict (in-sample, holdout, or retirement): the result becomes a `TP-` claim graded by what our own test showed, naming the trial ids, and every claim it tested lists it under `bears_on`. Negative results are recorded the same way, and they are the most common outcome; a retired hypothesis stays retired unless a new hypothesis, with its own registration and budget, revisits it. The backlog item moves to `tested`, `retired` or `promoted to paper`.

### 7. Research → decision

A claim that would change **the tool itself** (new data to ingest, a model's authority, the universe, a risk rule) goes to an ADR citing the claim ids, never straight into code. Waiting on the owner: research-only model benchmarking (ADR 0008, claims-pilot tension CP-X2); filing-text ingest; Form 4 ingest.

## Zones

Three data zones; the evidence a run gives gets stronger from one to the next. The rules are in the [strategy-lab spec](../specs/strategy-lab.md) and [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md); the owner's decisions of 2026-10-08 are on #1301 (decisions 1 to 5, his governance brief in comment 6065810154). This section states principles and adds no rule.

1. **Development data**, the practice years up to the development boundary. Permissive on purpose: run any registered variant, compare anything, iterate. The price is counting: every run is a trial in its family's N and the deflated Sharpe carries the search, so the bar rises with every try. Nothing here is final evidence.
2. **Exams**, a family's holdout. Strict: spent once, on a frozen finalist chosen on development data; never pick among several strategies on one exam (#1301 decision 2); a changed strategy is a new sweep whose trials count (strategy-lab req 4). The lab refuses a window that reaches into exam months or past the boundary, hard and with no override, because agents run backtests too. A rule that depends on someone remembering which months a strategy saw is a defect to fix, not a procedure.
3. **Forward exams**, the paper book. A family whose holdout lies in the future when it registers takes its exam in its own paper book, with its promoted hypothesis, under the [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md) tracking check. The data did not exist when the strategy was designed: the strongest evidence and the final judge. On today's history every new family's exam is forward.

**The boundary** is one date the owner writes into the store (ADR 0016 point 1; until then today's window rule applies, built by the data-foundation plan's T142 tasks). Every run reads it; no in-sample run reads a session after it. It is not a per-strategy choice (#1301 decision 1) and no hypothesis carries it as a key. Only the owner moves it, by a recorded decision with a reason, never onto a family's exam months; trials under the old value stay counted and go stale (count in N, never select, rerun). ADR 0016 has the rule, the refusals and the open questions.

The owner's line: research freedom in development, evidence discipline in validation, maximum skepticism at promotion, prospective data as the final judge. Current tracks and parked families: #1301 decisions 3 to 5 and `docs/STATUS.md`.

## Who does what

| Who | Does |
|---|---|
| Owner | Sets backlog priority; approves promotions by merging hypothesis files; registers; spends holdouts; sets and moves the development boundary; decides ADRs |
| Agents | Extract claims from new reports; propose backlog entries and re-rankings with reasons; draft hypothesis files and ADRs; record `TP-` results after trials |
| `researcher` | Answers briefs; its report's claims land in the register |
| `quant-auditor` | Reviews any change that adds data, a signal or a hypothesis |

**Reading order for agents:** the backlog first; then the claims an item cites in `claims.toml`, by id, opening the source report only for the section a claim names. Never cite a report's prose as support when its claim is `UNGRADED`, `INSUFFICIENT` or conflicted; say so instead. **Cadence:** the backlog is reviewed when a test reaches a verdict, when a report changes a grade, and at each phase retro; a review that changes nothing is fine. **Knowledge graph:** the register already is one (claims as nodes, `bears_on` links to ADRs, hypotheses and backlog items, `note` for conflicts); a generated view or a validating script (unique ids, resolving links, allowed grades) is a size-S issue when the register outgrows reading by hand, which the claims pilot judged beyond today's 26 reports.

## Open questions for the owner

1. Is "information value per unit of cost" the ranking rule you want, or do you want expected return weighted in?
2. Should research documents be exempt from the claim/issue ceremony (a docs-only fast path)? Today every change, docs included, needs an issue and a claim (CLAUDE.md rules 1 and 9).

## Changes

| Date | Change | Where |
|---|---|---|
| 2026-10-02 | Draft v0.1: the loop, the claims register, the backlog, promotion gates | #620 |
| 2026-10-09 | Zones, the development boundary and forward exams, from the owner's decisions of 2026-10-08 | #1331, #1301 |
| 2026-10-18 | Rewritten for length, meaning unchanged; the loop as a numbered list; the 10-08 track snapshot points at STATUS | #1752 |
