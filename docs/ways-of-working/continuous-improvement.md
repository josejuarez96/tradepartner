# Continuous improvement: the flow audit

**Status:** Accepted v1.0 (#769, 2026-10-04)

## Why this exists

The per-PR gates (reviewers, `ready_pr.py`, the train, the ruleset) guard each change. Nothing guarded the loop between them: by 2026-10-04 the owner was relaying commands only he could run, re-granting the same merge approval to every new orchestrator, answering decisions scattered over a dozen threads, and the system had no way to reach him on his phone. The owner asked for a focus on "how best me and you and this system can get better", keeping the friction that is appropriate and cutting the rest. This page is that loop, and `flow-auditor` (`.claude/agents/flow-auditor.md`) is its instrument. It also absorbs the "auditor of main" agreed on 2026-09-25 and deferred: periodic, read-only, non-blocking, findings become issues.

## The loop

```
audit (flow-auditor, read-only) ──► report PR in docs/retros/ (spec-critic, owner merges)
      ▲                                      │
      │                          do-now items ──► the owner inbox (he answers from the phone)
      │                          the rest     ──► one size:S issue per change, unclaimed
      └───────────── the next audit measures whether the baseline moved ◄── changes land as PRs
```

## Cadence

- **Hand-triggered for now.** The orchestrator (or the window the owner names) claims a `type:docs` issue, runs `flow-auditor` on Opus, and opens the report PR. Trigger it when the owner asks, after about 50 merges since the last report, at a phase close, or when a stall repeats (a memory note gains a third dated update).
- **Scheduled later.** The GitHub half (PR timelines, CI, issues, follow-ups, staleness) can run as a Routine (cloud cron) that posts a comment, once one is set up; the transcript and memory inputs are local and stay with the hand-triggered run. A scheduled run never opens a PR or an issue on its own; it posts its numbers as a comment on the open audit issue.

## Inputs

Git and PR history, verdict comments, CI runs, the train's comments, issues and labels, `docs/STATUS.md`'s "Updated" date, the ways-of-working pages, ADR 0002, the merge-train spec, the scripts, `.claude/settings.json`, the memory notes, and (sampled, counts only) the local Claude Code transcripts. Claude Code capabilities are verified against the product docs each time: features move.

## How friction is scored

Each candidate change gets four numbers for the audit window: **owner minutes** saved per week, **wall-clock hours** of stall removed (a ready PR waiting, a window blocked, a day with no merges), **tokens**, and **risk** added or removed (which gate it touches, and how). Rank by the first two per unit of work; tokens break ties; a change that adds risk needs the owner's name on it.

Classification of what exists today:

| Verdict | Rule |
|---|---|
| **Keep** | Removing it lets one mistake reach money, orders, secrets, `main` or the point-in-time rule (charter rule 5, git-workflow rule 7, the holdout flag, `known_at`). Cheap or not, it stays. |
| **Streamline** | Load-bearing in purpose, accidental in form: the same yes asked every session, a check that runs twice, a timeout shorter than the job. |
| **Automate** | A human does by hand what a script or a schedule can do with the same guard: a build that merges nothing, a fold, a digest, a notification. |
| **Remove** | Exists because of a tool limit, a stale document or a habit, and guards nothing: a denial on a read, a comment line zsh cannot paste, a rule the docs got wrong. |

## From findings to changes

1. The report leads with at most **three "do now" items**, each sized (minutes for an owner action, S or M for a build) and naming who decides. These go to the owner inbox, not to issues.
2. Every other ranked item becomes **one `size:S` issue per change**, unclaimed, filed by the window that ran the audit, linked from the report PR. Never one issue per finding on one file (plan-shape rule 6).
3. A change that **relaxes a gate** (a class of PR that lands under a standing approval, an `ask` entry moved to `allow`, a scheduled job that writes) is an **owner decision**: the report says so in those words, and it lands as an ADR amendment or a dated spec amendment, never as a side effect of a script PR.
4. A change to a script, CI, a hook or a permission rule goes through its own PR with the reviewers `ready_pr.py` requires for those paths; the audit PR itself is docs only.
5. The next audit opens with the baseline table and says what moved.

## Rules

- The auditor is read-only on everything but its own report. It never adds a blocking gate. It never weakens a safety gate without saying so explicitly as an owner decision. It never merges, claims, labels or files issues; the window does.
- No per-PR step is added by this page. Latency to merge is the constraint this loop exists to cut, not to grow.
- Transcripts leave counts, not prose; no secret, account id or email address appears in a report (the repository is public).

## Baseline (2026-10-04, first audit)

Measured in [flow-audit-2026-10-04.md](../retros/flow-audit-2026-10-04.md); the next audit reports the same rows.

| Metric | Baseline | Source |
|---|---|---|
| Merges per active day (min / median / max) | 7 / 27 / 83; two zero days while the owner was away | `gh pr list`, 09-24 to 10-04 |
| Ready → merged, last 60 merged PRs | median 22 min, p75 55 min, max 71 h | issue timelines |
| Created → merged, all merged PRs | median 1.2 h, p75 2.5 h | `gh pr list` |
| CI `checks` on code PRs | median 36 min, p90 45 min; 118 of 300 runs cancelled | `gh run list` |
| First-pass verdicts, last 60 PRs | 29 PASS, 21 PASS WITH FIXES, 4 FAIL | verdict comments |
| Classifier denials | 44 in 86 sessions; ~1 in 5 on read-only commands | transcripts |
| Owner decisions open | 11 decision-shaped issues; #366 digest open since 09-30 | `gh issue list` |
| Unsized open issues | 10 of 57 | `gh issue list` |
| STATUS staleness at audit time | 2 days, 41 merges | `docs/STATUS.md` |
| Standing merge approval re-grants | 4 (in chat, none recorded in the repo) | memory notes |
| Phone-reachable surfaces | 0 (no Remote Control, no hooks, no inbox) | `.claude/settings.json`, docs |
