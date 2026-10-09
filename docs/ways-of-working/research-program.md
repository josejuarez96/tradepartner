# Research Program: how research turns into tests, and tests back into knowledge

**Status:** Draft v0.2 (#620; Zones added by #1331)  ·  **Owner:** Jose  ·  **Related:** [development-process.md](development-process.md) (lifecycle and domain rules), [charter](../charter.md) principles 2 and 6, [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md) (objectives), [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) (the development boundary, forward exams), [backtest spec](../specs/backtest.md) (hypotheses, registry, holdout), [strategy-lab spec](../specs/strategy-lab.md) (families, N, the deflated Sharpe), #1301 (the owner's discussion of 2026-10-08)

## Why this exists

TradePartner has a tool that can test a strategy honestly: point-in-time data, costs, a locked holdout and a trial registry. It did not have a written answer to three questions:

1. **What do we test next, and why?**
2. **How does a research finding become a test?**
3. **What happens to a result?**

This page answers them. It adds two living files and one loop. It changes no rule in the charter, the ADRs or the backtest spec.

## The loop

```
 research report ──► claims register ──► hypothesis backlog ──► hypothesis file ──► trials ──► result
  (frozen)            (living, graded)    (living, ranked)       (frozen once        (registry)     │
       ▲                    ▲                                     registered)                       │
       │                    └───────────── result recorded as a TradePartner claim ◄───────────────┘
       └── new research brief when a backlog item is blocked by an unknown
```

| Stage | Artifact | Where | Changes how |
|---|---|---|---|
| Research | Report | `docs/research/YYYY-MM-DD-<slug>.md` | Frozen once merged. Newer research supersedes it. |
| Claims | Claims register | [`docs/research/claims.toml`](../research/claims.toml) | Living. One entry per decision-relevant finding, with its grade and source. |
| Backlog | Hypothesis backlog | [`docs/research/hypothesis-backlog.md`](../research/hypothesis-backlog.md) | Living. Ranked by the owner. |
| Test | Hypothesis file | `docs/hypotheses/<slug>.md` | Frozen once registered (backtest spec req 10). |
| Result | Trial outcome | the store's registry (`hypotheses`, `trials`, `trial_results`) | Append-only. |
| Decision | ADR | `docs/decisions/NNNN-<slug>.md` | Append-only. Cites the claim ids it rests on. |

## Rules per stage

### 1. Research enters

- **Any research document may be added to `docs/research/` at any time**, as a docs-only change.
- **A report is context, not evidence**, until its decision-relevant findings are in the claims register with a grade. Agents act on the register, not on prose they skimmed.
- A **research brief** (the [template](../templates/research-brief.md)) is commissioned only when a backlog item or a decision is blocked by an unknown. Curiosity alone is not a reason to commission one.

### 2. Claims register

- **Who adds:** whoever lands a report adds its claims, in the same change or a follow-up. An entry is one sentence, faithful to the source and never stronger. Fields are defined in the file header.
- **Grades:** the evidence protocol in [handoff §6.1](../research/2026-09-24-initial-research-handoff.md): SUPPORTED, MIXED, NOT SUPPORTED, INSUFFICIENT. A claim stated without a grade is `UNGRADED`, and it never counts as support.
- **Conflicts:** when two claims disagree, both stay, and each names the other in `note`. Resolving a conflict is a decision or a test, never an edit.
- **Regrading** needs new evidence: a newer report, or a TradePartner result (§6). Changing a grade means adding the new evidence and noting the old grade, never silently overwriting it.

### 3. Hypothesis backlog

Each entry states:
- the claims it tests;
- the **prior**: the expected effect, normally at or below the publication-decay haircut (claims QI-2, G2-S10);
- the **data** it needs, and whether we have it;
- the **engine work** it needs (today only the `momentum` family exists);
- **cost** (S, M or L);
- the **kill criterion**;
- the **information value**: which decision changes if it passes or fails.

The ranking rule:
- Rank by **information value per unit of cost**, not by expected return.
- A test that would retire a whole line of work cheaply ranks above an exciting test that needs new data.
- The owner sets the order. Agents propose changes to it, but never reorder it themselves.

### 4. Promotion: backlog → hypothesis file

An item is promoted only when all five hold:
1. Its data exists **point-in-time** in the store, or a data spec for it is merged.
2. The engine can run its family, or the engine work is a merged plan task.
3. Its prior, kill criterion and holdout are written in a hypothesis file from the [template](../templates/hypothesis.md).
4. The family's trial budget is stated: how many variants will run, all of them counted (QI-11).
5. The owner merges the hypothesis file and runs `hypothesis register`.

The **semantic and model-derived features** have their own, stricter gates: the [ML validation brief](../research/2026-10-03-ml-validation-methodology-brief.md#2-scope-and-stage-gates). None of them is promoted before those gates pass.

### 5. Test

Unchanged:
- every run is a trial;
- the holdout is spent once;
- results are reported beside SPY and MTUM;
- deflated Sharpe uses the family's trial count.

Which data a run may read is [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md)'s rule; the principles behind it are in [Zones](#zones).

### 6. Result → register

When a hypothesis reaches a verdict (the in-sample result, the holdout result, or retirement):
- **The result becomes a claim** with prefix `TP-` (TradePartner evidence), graded by what our own test showed. It names the trial ids, and every claim it tested lists it under `bears_on`.
- **Negative results are recorded the same way**, and they are the most common outcome. A retired hypothesis stays retired unless a new hypothesis, with its own registration and budget, revisits it.
- The backlog item moves to `tested`, `retired` or `promoted to paper`.

### 7. Research → decision

When a claim would change **the tool itself** rather than a strategy, it goes to an ADR, never straight into code. Examples: new data to ingest, a model's authority, the universe, a risk rule. The ADR cites the claim ids. Current examples waiting on the owner:
- whether research-only model benchmarking may run (ADR 0008, claims-pilot tension CP-X2);
- filing-text ingest;
- Form 4 ingest.

## Zones

Data sits in three zones, and the evidence a run gives gets stronger from one to the next. The rules live in the [strategy-lab spec](../specs/strategy-lab.md) and [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md); the owner's decisions of 2026-10-08 are on #1301 (decisions 1 to 5, and his governance brief in comment 6065810154). This section states the principles so agents follow them; it adds no rule.

1. **Development data**: the practice years, up to the development boundary. Permissive on purpose: run any registered variant, compare anything, iterate. The price is counting: every run is a trial, every trial counts in its family's N, and the deflated Sharpe carries the search, so the bar a result must clear rises with every try. Nothing here is final evidence.
2. **Exams**: a family's holdout. Strict: spent once, on a frozen finalist, chosen on development data. Never pick among several strategies on one exam (#1301 decision 2); a changed strategy is a new sweep, and its trials count (strategy-lab spec req 4). The lab refuses a window that reaches into exam months, or past the boundary, hard and with no override, because agents run backtests too; the rest is discipline. If a rule would depend on someone remembering which months a strategy saw, that is a defect to fix, not a procedure to follow.
3. **Forward exams**: the paper book. A family whose holdout lies in the future when it registers takes its exam in its own paper book, with its promoted hypothesis, under the [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md) tracking check. The data did not exist when the strategy was designed, which is why this is the strongest evidence and the final judge. On today's history every new family's exam is forward.

**The boundary.** One date, written by the owner into the store. Once he writes it (ADR 0016 point 1; until then today's window rule applies, and the code is the data-foundation plan's T142 tasks), every run reads it and no in-sample run reads a session after it. It is not a per-strategy choice (#1301 decision 1) and no hypothesis carries it as a key. Only the owner moves it, by a new recorded decision with a reason, never onto any family's exam months; trials run under the old value stay counted and go stale: they count in N, never select, and are rerun. ADR 0016 has the rule, the refusals and the open questions.

**Where things stand** (#1301 decisions 3 to 5): one track now, H1 into paper trading, to validate the app; B3's exam stays unspent until a profitability finalist exists; B4 is parked, and its exam, when it registers, is forward.

In one line, the owner's: research freedom in development, evidence discipline in validation, maximum skepticism at promotion, prospective data as the final judge.

## Who does what

| Who | Does |
|---|---|
| Owner | Sets backlog priority; approves promotions by merging hypothesis files; registers; spends holdouts; sets and moves the development boundary; decides ADRs |
| Agents | Extract claims from new reports; propose backlog entries and re-rankings with reasons; draft hypothesis files and ADRs; record `TP-` results after trials |
| `researcher` agent | Answers briefs. Its report's claims land in the register. |
| `quant-auditor` | Reviews any change that adds data, a signal or a hypothesis |

## How agents use this (reading order)

1. Read the **backlog** first, to see what is being worked on and what is next.
2. Read the **claims** an item cites in `claims.toml`, by id. Open the source report only for the section a claim names.
3. Never cite a report's prose as support for a test when its claim is `UNGRADED`, `INSUFFICIENT` or conflicted. Say so instead.

## Cadence

The backlog is reviewed:
- when a test reaches a verdict;
- when a report lands that changes a grade;
- at each phase retro.

A review that changes nothing is fine. The review is a check, not a reason to change things.

## On knowledge graphs

The register already is a graph:
- claims are nodes;
- `bears_on` links connect claims to ADRs, hypotheses and backlog items;
- `note` records conflicts between claims.

A generated graph view, or a validating script (unique ids, links that resolve, grades from the allowed set), is worth building when the register outgrows reading by hand. The claims pilot judged that point to be beyond today's 26 reports. Either is a size-S issue when wanted.

## Open questions for the owner

1. Is "information value per unit of cost" the ranking rule you want, or do you want expected return weighted in?
2. Should research documents be exempt from the claim/issue ceremony (a docs-only fast path)? Today every change, docs included, needs an issue and a claim (CLAUDE.md rules 1 and 9).
