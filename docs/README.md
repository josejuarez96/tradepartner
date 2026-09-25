# Documentation Map

Start with [STATUS.md](STATUS.md) to see where things stand now, then the [charter](charter.md) for why the project exists.

## Folder layout

```
docs/
├── STATUS.md             ← living: current phase, in progress, next, blocked, decisions needed
├── status.d/             ← one Done line per open PR, folded into STATUS.md by doc-keeper
├── charter.md            ← why, success/stop criteria, scope, constraints (changes only via ADR)
├── roadmap.md            ← phases with exit criteria
├── work-map.toml         ← living: one plain-English what/why per task, issue, report, ADR; read by the cockpit
├── architecture.md       ← (Phase 1+) living system overview: components, data flow
├── ways-of-working/      ← how we build: git, process, agents
├── decisions/            ← ADRs: NNNN-<slug>.md, append-only
├── research/             ← briefs' answers: YYYY-MM-DD-<slug>.md + trial-registry.md
├── specs/                ← what & why per feature: <feature>.md
├── plans/                ← how per feature: <feature>.md (tasks → PRs)
├── hypotheses/           ← pre-registered backtest hypotheses: <slug>.md, frozen once registered
├── retros/               ← phase-N.md
└── templates/            ← copy these; don't edit in place
```

## Document types

| Type | Answers | Created when | Lifespan | Template |
|---|---|---|---|---|
| **Charter** | Why are we doing this? What counts as success, and when do we stop? | Once, in Phase 1 | Changes only through an ADR | n/a |
| **Research report** | What does the evidence say about question X? | When a decision is blocked by an unknown | **Frozen** once merged. Newer research supersedes it; it isn't edited | [brief](templates/research-brief.md), [report](templates/research-report.md) |
| **ADR** (decision record) | What did we choose, why, and what did we reject? | Any hard-to-reverse choice | **Append-only.** Change a decision by writing a new ADR that supersedes it | [adr](templates/adr.md) |
| **Spec** | What are we building and why? How do we know it's done? | Size-L features | Living until the feature ships, then frozen | [spec](templates/spec.md) |
| **Plan** (the "build document") | How do we build it: ordered tasks, one PR each | After the spec is approved | Checkboxes are ticked as PRs merge. Frozen at the end | [plan](templates/plan.md) |
| **STATUS** | Where are we right now? | Always exists | Living; updated every session | n/a |
| **Architecture** | How do the pieces fit today? | Phase 1+ | Living; updated when structure changes | n/a |
| **Hypothesis** | What exactly are we testing, with which parameters and holdout, and what would retire it? | Before any backtest run of a new idea | **Frozen** once registered: any edit is a new hypothesis | [hypothesis](templates/hypothesis.md) |
| **Retro** | What should we change about how we work? | End of each phase | Frozen | [retro](templates/retro.md) |
| **CHANGELOG** | What changed, per version? | Every feat/fix PR | Fragment in `changelog.d/`, folded by doc-keeper | n/a |
| **Work map** | What is this piece of work, in plain English, and why does it matter for the MVP? | When an issue, research brief, ADR, spec or plan is opened | Living; the cockpit lists the ids it is missing | the header comment in [work-map.toml](work-map.toml) |

## Rules

- **Link, don't copy.** Specs link to research and ADRs, and plans link to specs. Never paste the same content into two places.
- **Frozen docs stay frozen.** Fixing a typo is fine. Changing the substance means writing a new doc that supersedes the old one.
- **Dates are absolute** (`2026-09-24`), never "last week".
- **Every piece of work gets a work-map entry.** When you open an issue or a research brief, add `[issue.<n>]` (or `[research.<stem>]`, `[adr.<nnnn>]`, `[task.<id>]`) to [work-map.toml](work-map.toml): one sentence on what it is, one on why it matters on the way to the MVP, and `unblocks` for the decision or task it feeds. The cockpit (`uv run python scripts/cockpit.py`) reads it and lists what is missing.
- **Keep CLAUDE.md short.** It's loaded into every agent session, so it holds rules and pointers, not specs.
