# Flow audit 1: the owner, the orchestrator and the system (2026-10-04)

**Status:** first audit, hand-triggered  ·  **Issue:** #769  ·  **Window:** 2026-09-24 to 2026-10-04 (9 active days, 315 merged PRs)  ·  **Next:** after the three items below land, or at ~50 more merges ([continuous-improvement.md](../ways-of-working/continuous-improvement.md))

The question Jose asked: where do he, the orchestrator and the system stall, which of that friction is load-bearing, and what keeps moving when he is away from the laptop. Short answer: the gates that protect money, orders, secrets and point-in-time data are all cheap and stay; most of the owner's minutes go to being a **relay** (commands only he can run, approvals re-granted every session, decisions scattered over a dozen threads), and the system has **no phone-reachable surface** at all. Three changes fix most of that without touching a gate.

## Do now (three)

**1. Drive the orchestrator from your phone: turn on Remote Control and its push notifications.** Claude Code has this today (`/remote-control` in the orchestrator window, then the Code tab in the Claude phone app; `/config` turns notifications on). You get a push when a window needs a decision, and you answer the permission prompt or type "merge train `<id>`" from the phone. Nothing about the gates changes: the window keeps its permission mode, the phone cannot select bypass, and `merge_train.py merge` still prompts. Constraint: the Mac must stay awake (plugged in, "prevent automatic sleeping on power adapter", or `caffeinate -s`). *Size: 10 minutes, you. Decides: you. Cuts: every "I'm back, what happened?" round trip and the hours a ready batch waits.*

**2. One owner inbox: a single pinned issue you answer from the phone with one line.** Every open owner question (today 11 issues plus the stale #366 digest and the "Decisions needed" list in STATUS, last refreshed 2026-10-02) is regenerated into one issue, ordered by what it blocks, each with lettered options and the drafting agent's recommendation. You answer by comment: `#750: b`, `#731: 1`, `hold #729`. The orchestrator polls it (`/loop 15m`), counts only comments by your GitHub login (the repository is public; anyone can comment), and writes each answer back on the source issue; a rule change still lands as an ADR or spec amendment PR, as now. The inbox carries **answers to questions, not the merge word**: "merge train `<id>`" stays something you say to a window (git-workflow rule 7), from the phone over Remote Control (item 1). Accepting it as an inbox comment would make a comment a consent channel; that is a separate owner decision, named under the top 10 below, and not proposed here. *Size: S (a `team.py inbox` subcommand or `scripts/inbox.py`; a window builds it; until it exists, do-now items live in this report and each build item gets one tracking issue). Decides: you pick the label and the answer format.*

**3. Write the standing merge approval down, by PR class, instead of re-granting it in chat every session.** In plain words: you say once, in writing, which kinds of PR the orchestrator may land without asking, and everything else keeps waiting for your word. You have granted "merge what is ready and green" to four orchestrators in a row (9c, 03, 1c, 3f); each new one has to ask again, and nothing says which PRs it covers. Proposal, as an ADR 0002 amendment. **Class A** is a short positive list of paths, and a PR is class A only when every changed path is inside it: `docs/research/`, `docs/retros/`, `docs/work-map.toml`, `changelog.d/`, the dashboard pages `src/tradepartner/dashboard/` **except** `override_page.py` (the kill-switch page), and their tests `tests/dashboard/`, `tests/test_cockpit.py`, `tests/test_docs_budget.py`. Nothing else is in it until the ADR is amended: not the guard tests (`tests/test_ready_pr.py`, `tests/test_merge_train.py`, `tests/test_secret_scrub.py`, `tests/test_team.py`), not `tests/adapters/`, `tests/lookahead/` or `tests/execution/`. The orchestrator's `merge_train.py merge` may land a train whose PRs are all class A, every verdict `PASS`, train green. **Class B is everything else** (the order path, the kill-switch page, the store and the point-in-time code, `config.py`, the gate scripts `ready_pr.py`, `merge_train.py` and `team.py` and their tests, `.claude/`, CI, rulesets, `pyproject.toml` and `uv.lock`, ADRs, specs, process pages, and any PR labelled `hold`): your word per batch, as today. Kill switch and cap, each with what enforces it: a `hold` label on a PR (you can add it from GitHub mobile) makes `merge_train.py build` skip it; the approval is revoked by one comment on the ADR's issue, which the orchestrator reads before every train; `merge_train.py merge` refuses a third unattended batch in 24 hours by counting its own `merge-train: MERGED` comments; one digest comment per train. Two things said plainly: **this relaxes git-workflow rule 7 for class A and is yours to decide**; and an unattended landing also needs the `ask` prompt on `merge_train.py merge` answered, which you do over Remote Control (item 1) or by moving that entry to `allow`, a second, separate decision this report does not recommend. Nothing changes in what a train tests. *Size: S docs (ADR amendment, rule 7, teams.md) plus S script (the `hold` label and the cap in `merge_train.py`), spec-critic and safety-reviewer. Decides: you.*

## What the numbers say

- **Throughput is high and bursty:** 7, 83, 20, 35, 7, 0, 0, 69, 26, 27, 41 merges per day. The two zero days are the owner's days off: nothing lands without him.
- **When he is present, approval is fast:** ready→merged median 22 min, p75 55 min on the last 60 merged PRs (47 under an hour, 12 in 1–4 h, one at 71 h). Created→merged median 1.2 h. Approval latency is not the problem while he is at the keyboard; absence is.
- **The owner's keyboard hours run from about 12:00 to 06:00 UTC**, with peaks at 15:00–18:00 and 00:00–03:00 and near zero at 09:00–12:00 (transcripts 2026-09-24 to 10-04; upper bounds, see caveats). The system has no way to reach him outside those hours and no way for him to answer from the phone.
- **CI is the wall-clock floor:** `checks` median 36 min, p90 45 min on PRs that run pytest; a train adds another full run. 118 of the last 300 runs were cancelled (superseded pushes), 6 failed (none on `main`), 2 were three-second billing failures.
- **Reviews are already capped and mostly first-pass clean:** of the last 60 merged PRs, 58 carry a verdict comment; the first verdicts were 29 `PASS`, 21 `PASS WITH FIXES`, 4 `FAIL`, 2 spec-critic `APPROVE` and 2 in other wording. 39 follow-up issues filed, 9 open. This costs tokens, not owner minutes.
- **The owner relay:** 44 auto-mode classifier denials across 86 sessions (transcripts 2026-09-24 to 10-04); about one in five on read-only commands (`gh pr view` batches, reading a task's output file), the rest on cleanup (`git worktree remove`, `git branch -D`, `git push --delete`, `team.py prune`, `release --force`) and one `gh api -X POST` (the ruleset). The memory note on this has eight dated updates since 09-24: the same lesson re-learned per session.
- **Decisions are scattered:** 57 open issues, 11 owner-decision-shaped, 10 with no size label (unclaimable until he sizes them), the #366 digest open since 09-30 with four comments and one answer.
- **Window-to-window messages are the most expensive thing a session does** (97% of tokens were re-reads on 10-01, #489); the "ready / blocked only" rule holds, but background helpers of a team hand back to the orchestrator, not to the team, so teams stall or the orchestrator forwards by hand.
- **Tooling that exists and is unused here:** Remote Control, phone notifications, Routines (cloud cron), cloud sessions from the phone, `PermissionRequest` and `Notification` hooks, `/loop`. No hook is configured; `allow_auto_merge` is off; the only workflow is `ci.yml`.

## Top 10, ranked

Ranked by owner minutes and wall-clock hours saved per unit of work, with the gate each one touches. "Decides" names who says yes.

| # | Change | Cuts | Size · decides |
|---|---|---|---|
| 1 | **Remote Control + push notifications** on the orchestrator window; Mac awake on power | Round trips after absences; batch wait while away | 10 min · owner |
| 2 | **Owner inbox** issue, regenerated, answered by one-line comments (answers only, never the merge word); orchestrator polls with `/loop`, owner's login only | The scattered decision queue; sizing stalls | S · owner (format), window (build) |
| 3 | **Standing merge approval by class A** (a positive path list), written into ADR 0002 and rule 7, with the `hold` label and a daily cap enforced by `merge_train.py` | Re-granting every session; class-A batches waiting overnight | S docs + S script · **owner (relaxes rule 7 for class A)** |
| 4 | **Auto-mode allow list for cleanup and `gh` reads** in `~/.claude/settings.json` (`/auto-mode-setup`): `git worktree prune`, `team.py prune --yes`, `git push origin --delete` of merged branches, `gh pr|issue|run view|list`, `gh pr checks`. Not `team.py release --force` (CLAUDE.md rule 9 keeps it the owner's) and not `gh api` until a mid-command deny pattern is verified (see the away table). Plus `setopt interactivecomments` in `.zshrc`, and paste blocks with no `#` lines (teams.md) | The relay for hygiene; broken pastes | 15 min · **owner (relaxes the classifier for the cleanup class; it blocks branch deletion today as destructive)** |
| 5 | **Train build on a trigger** (≥3 eligible PRs, or every 2 h while one is eligible); `build` merges nothing | A ready PR waiting for someone to ask for a train | M · **owner (reverses merge-train decision 5)**; record must live where `merge` runs, see below |
| 6 | **PR CI runs the targeted tests; the full suite moves to `train/**` and `main`**; a hand merge of one PR needs a full-suite run on its head (`ready_pr --full-tests`) | PR CI from ~36 to ~10 min; fewer cancelled runs | S (`ci.yml`, `ready_pr.py`) · owner (moves a gate, same strength) |
| 7 | **`ready_pr.py` default `--timeout-min 45`** (CI takes 36–45 min; the 25-min default loops) | Timeout loops, repeated merges of `main` | S · none needed |
| 8 | **Daily fold** when ≥10 fragments are pending (`doc-keeper`, one docs PR, rides the train) | STATUS staleness; the "fold when stale" ask | S rule · owner |
| 9 | **Notification hook → ntfy/phone** for `blocked:` and `ready` from every window (payload: PR numbers and one line; topic in user settings, never the repo) | Blind spots item 1 does not cover (windows other than the orchestrator) | S · owner installs; safety-reviewer on the settings diff |
| 10 | **Follow-up issues are `size:S` by default**; unsized issues appear in the inbox; a window never waits on sizing for a follow-up it filed | The 10 unclaimable issues; sizing round trips | S docs · owner |

Named, not ranked, because it relaxes a gate: accepting "merge train `<id>`" as a comment on the inbox (a comment becomes a consent channel, git-workflow rule 7; owner decision; only after item 2 has the login check, and the window's `ask` prompt still has to be answered). Also not ranked, worth a line: a nightly `ingest` launchd job on the awake Mac that posts its result to an issue (the runbook exists; owner-installed; read-only data; `.env` stays local) would let a team pick up backfill failures (#687, #710, #735 cost three days) without him at the keyboard.

## Away from the laptop: the design

Each candidate was checked against the Claude Code docs on 2026-10-04 rather than assumed. "Awake" means the Mac must be on; "cloud" runs without it.

| Candidate | Status | Use it for | Guardrail |
|---|---|---|---|
| **Remote Control** (phone drives a local window) | Exists; awake | Approvals, prompts, "merge train `<id>`", steering | Permission mode unchanged; no bypass from the phone; `ask` entries still prompt |
| **Phone push notifications** | Exists for Remote Control sessions only | "Needs a decision", "finished" | Nothing to guard; it is read-only |
| **Owner inbox** (GitHub issue) | Build, size S | Every owner question, answered by one line | Only the owner's login counts; closed answer vocabulary; answers copied to the source issue; rule changes still land as ADR/spec PRs |
| **GitHub-mobile comment commands** (`merge train <id>` as a comment) | Decide; not proposed now | Approving a batch from the phone without Remote Control | A comment is a new consent channel (**relaxes rule 7; owner decision**); needs item 2's owner-login check first; the window's `ask` prompt on `merge_train.py merge` still has to be answered, over Remote Control or by a weakened `ask` (**a further decision**) |
| **Standing approval by class** | Decide, size S docs + S script | Class-A batches landing unattended | Positive class-A path list in the ADR; `hold` label skipped by `build`; cap enforced by `merge`; revocation by one comment; digest per train |
| **Auto-build train when N are ready** | Decide, size M | Removing the "build a train" ask | `build` merges nothing; one at a time. The record lives in `~/.tradepartner/merge_train/` on the machine that built, and `merge` must run there, so this runs on the awake Mac (`/loop` in the orchestrator, or launchd), not in the cloud, until a spec amendment makes the record reconstructible from the PR comments |
| **Permission allow-rules for read-only commands** | Exists; user-level `autoMode.allow`, not the repo file | Ending the relay for `gh` reads and cleanup | Rules match command text, not HTTP method, and `gh api <path> -f k=v` POSTs with no `-X`; a mid-command deny (`gh api * -f *`) is in the docs' wildcard syntax but was not tested here, so `gh api` stays off the list until it is, or reads go through a read-only wrapper script. `gh pr merge`, `merge_train.py merge`, `--admin`, pushes to `main`, `.env` and `release --force` stay as they are |
| **Hooks** (`Notification`, `Stop`, `PermissionRequest`) | Exist; local, or HTTP to an always-on service | Push to the phone from any window; later, auto-answering a closed class of prompts | A `PermissionRequest` hook that answers `allow` is a gate removal: not proposed now; if ever, only for the cleanup class in item 4 |
| **Routines** (cloud cron) | Exist (Pro/Max); cloud; repo via the Claude GitHub app | Read-only jobs: this audit's GitHub half, a drift check, a stale-PR sweep, a daily digest comment | The Claude GitHub app holds write access to the repository, a second credential surface (**owner decision before the first routine**, the same objection as the Action row). No local transcripts, no `.env`, branches are `claude/…` (fails the claims guard unless renamed), so first uses post comments only: no claim, no merge, no PR |
| **Cloud sessions from the phone** | Exist | Starting a one-off cloud task from the phone | Same `claude/` branch caveat; no local store; keep to docs and research |
| **Claude GitHub Action** (`@claude` in comments) | Exists | Not now | Needs an API key in GitHub secrets (a second credential surface) and duplicates the windows; revisit when no window is running |
| **Decision page as an artifact** | Possible | Rejected | GitHub is the source of truth (teams.md rule 1); a second store of answers would drift |

What this gives when Jose is away: windows keep building and reviewing (unchanged); class-A batches land under the standing approval (item 3) once a train has run (item 5 or a window asked for one); everything else queues in the inbox (item 2) and he answers from the phone (items 1 and 2); the Mac stays awake so the train record and the ingest job are reachable. What still needs his hands: keys, the real store, order-path and config merges, rule changes, the ruleset, `--admin`.

## Friction inventory

Verdicts: **keep** (load-bearing: removing it lets one mistake reach money, orders, secrets, `main` or the point-in-time rule) · **streamline** · **automate** · **remove**.

| Friction | Evidence | Verdict |
|---|---|---|
| Owner's word on every merge (rule 7) | 0 merges on his days off; approval fast when present | **Keep** for class B; **streamline** for class A via item 3 |
| Train built on request only (decision 5) | #764 first real build hit an unexercised path; batches wait for the ask | **Automate** the build (item 5); the merge stays his |
| Owner as relay for cleanup and `gh api -X POST` | 44 denials; 8 memory updates; zsh ate `#` lines in pastes | **Remove** via item 4; the ruleset `POST` was one-off |
| Classifier denials on read-only reads | ~1 in 5 denials; blocked teams wait or re-ask | **Remove** via item 4 |
| Standing approval re-granted per orchestrator | 4 grants in chat, none in the repo | **Streamline** via item 3 |
| Decisions scattered across issues, PRs, STATUS | 11 decision issues; #366 since 09-30; STATUS stale 2 days | **Automate** via item 2 |
| Unsized issues unclaimable | 10 open without `size:` | **Streamline** via item 10 |
| Full pytest on every code PR, then again on the train | 36–45 min per run; 118/300 cancelled | **Streamline** via items 6 and 7 |
| Two review passes per reviewer, verification diff | 21 of the last 60 PRs needed a second pass; tokens, not minutes | **Keep** (#489 already capped it) |
| Claim before branch, one fragment per PR, template boxes, work-map entry | Prevents the 09-24 duplicates and the shared-file conflicts; seconds each | **Keep** |
| Owner tasks: keys, real store, recordings, Probe 3 | Backfill chain cost three days (#687, #710, #735) | **Keep** the ownership; **automate** the nightly run on the awake Mac |
| Background helpers hand back to the orchestrator, not the team | ibis on #532 idled; `/code-review` finders report empty | **Streamline**: the rule is in every brief; it now sits in agents.md (this PR) |
| agents.md said subagents cannot spawn subagents | Teams spawn reviewers daily; docs allow depth 3 | **Remove** the stale line (this PR) |
| Window messages wake and re-read whole sessions | 97% of tokens (#489) | **Keep** the "ready / blocked only" rule |
| `ask` on `gh pr merge` and `merge_train.py merge`; `deny` on `main`, `.env`, `--no-verify` | The last local layer before the ruleset | **Keep** |
| Ruleset on `main`; the owner-only `--admin` bypass | Active since 10-04 | **Keep** |
| Holdout flag; trial registry; `known_at` everywhere | Charter | **Keep** |

## Agents: keep / change / delete

`spec-critic`, `quant-auditor`, `safety-reviewer`, `implementer`, `backtest-runner`, `researcher`: keep, in use daily. `doc-keeper`: keep; give it the daily fold (item 8). New: `flow-auditor` (this PR), read-only, hand-triggered; judge it at the next audit by whether the three do-now items landed and the baseline moved.

## Changes to ways of working (made in this PR)

1. `flow-auditor` added to the roster and the stage diagram ([agents.md](../ways-of-working/agents.md)).
2. The stale "a subagent cannot spawn subagents" line replaced by the verified rule: depth is two (the window → a team run as a background subagent → its reviewers and helpers), and a team runs reviewers in the foreground because a background helper's hand-back goes to the window ([agents.md](../ways-of-working/agents.md), Parallelism inside a team).
3. [continuous-improvement.md](../ways-of-working/continuous-improvement.md): cadence, inputs, scoring, how findings become inbox entries or issues, and the baseline table this audit starts.

Nothing in this PR changes a script, CI, a hook or a permission rule; every such change above is a proposal with an owner.

## Method and caveats

Inputs: `gh` (336 PRs, 300 CI runs, 57 open issues, timelines of the 60 most recent merged PRs), the ways-of-working docs and scripts, ADR 0002, the merge-train spec, the memory notes, and the local Claude Code transcripts (86 session files, streamed with a stdlib script; counts only, two one-line quotes, no values). Claude Code capabilities were checked against the product docs on 2026-10-04 by the `claude-code-guide` agent. Caveats: about half the "human" turns in the transcripts are window-to-window messages the harness tags as human, so per-day message counts are upper bounds; the ready→merge pairing in chat is approximate (the GitHub timeline numbers above are the reliable ones); the read-only share of denials is an undercount (compound `cd … && grep` commands were scored as mutating). Raw counts are in the session scratchpad, not in the repo.
