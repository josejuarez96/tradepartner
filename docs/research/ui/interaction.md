# Interaction model: the human workflow, from tools that share our method

**Status:** research note and recommendation for the owner · **Date:** 2026-10-10 · **Branch:** `spike/design-ui`
**The question (owner):** "Not so much the design strategy but how do we interact with this. What is the human-centric workflow. What apps that follow our methodology use, and the why behind it. I think QuantConnect actually looks like a decent UI, no?"
**Read with:** [workflow.md](workflow.md) (the five stages, the Run button's rules) and [engine-first.md](engine-first.md) (what the engine records). This note is about loops and decisions, not panels or colours. Owner's review rules in [HANDOFF.md](HANDOFF.md) apply. **Terminology rule (owner, 2026-10-10):** the UI uses the real quant terms and the engine's field names; it teaches them at the point of use, it never renames them. Section 9 is the glossary; section 10 lists the spike's invented names to retire.

## Summary (one screen)

**Recommendation.** The owner lives in four loops, and the app should be built as those four, in this order of daily importance:

| Loop | When | Length | Where | What he does |
|---|---|---|---|---|
| 1. **Morning check** | every session | 1 to 2 min | phone | Reads "nothing wrong, nothing waiting" or the one thing that is. The only write is the kill switch, with a reason. |
| 2. **Read a trial result** | when a backtest, sweep or holdout run finishes | 10 min | desk | Reads one result against the thresholds pre-registered before the run (`promote_at_least`, `retire_below` on `dsr_excess`), with the family's number of trials N and the deflated Sharpe ratio beside it. The page states what the pre-registered rule decides; he confirms or disagrees with a reason. |
| 3. **Decide** | a few times a month | 2 min each | desk (kill: phone) | Register (pre-registration freezes the parameters), run (a counted trial), promote or retire, spend the holdout, start a paper book, kill or resume, move the development boundary. One screen each: consequence stated, reason required where the engine records one, done once. |
| 4. **Review ideas and drafts** | weekly | 20 min | desk | The backlog by stage; an agent's draft hypothesis file read against the claims it cites; "draft this" and "register this" as the two actions. |

The organising object stays the **strategy**, as workflow.md says. What workflow.md misses is that the method has three **moments** that carry the whole discipline, and the interaction must make them unmistakable: **pre-registration** (the parameters freeze, `params_sha256`), **the trial count** (every run raises the family's N, and with it SR*, the multiple-testing adjustment), and **the holdout spend** (once per family, with a reason). Everything else should be as light as a broker app.

**Design principle: show the method's strictness, teach it, and stay intuitive, all at once.** Strictness is *shown* as state always on screen (N and SR* beside every result, the holdout's spent / unspent / forward status on every strategy row, `params_sha256` and `registered_at` on the specification). It is *taught* inline at the moment of a decision, by stating the consequence in one sentence in the real terms before the confirm, with an ⓘ on each term, never by help text. It stays *intuitive* because the engine already enforces most rules, so the app simply does not offer what `holdout.decide` would refuse (no window past the development boundary, no parameter form, no holdout checkbox on Run). Section 6 says how, with the prior art.

**QuantConnect, honestly.** Its *shape* is right for us and the owner's instinct is sound: one project holds code, parameters, every backtest, optimizations and the live deployment; the result page is a tabbed record with the code snapshot stored; the step from backtest to live is a wizard. Its *incentives* are the opposite of ours: backtests are unlimited and one click, the optimizer is a built-in tweak-and-rerun, and the only counter-measure is a gauge that counts your backtests and labels "70+: probably overfitting" without stopping you. QuantConnect's own research guide says every backtest "moves it one step closer to being overfitted", and Quantopian's study of 888 algorithms found that the more backtests, the bigger the gap between backtest and out-of-sample performance. Take the shape, reject the loop: our Run is a counted trial shown as a cost in N, our parameters are a read-only frozen specification, and our optimizer is the pre-registered sweep with its thresholds declared before the run.

**Open questions for the owner** (section 8, one decision each): where registration happens (app or CLI); what the Run confirm shows as the cost (N+1 only, or the new SR* too, which needs an engine function); where an agent's draft is reviewed (PR or app); whether the lab's *reading* side moves into the app now while *running* waits for the ADR 0018 amendment.

---

## 1. Our method, as the interface must carry it

Verified against the repo (research-program.md, ADRs 0005, 0008, 0016, 0017, `backtest/holdout.py`, `backtest/results.py`, `backtest/metrics.py`, `execution/check.py`):

- **Pre-registration.** A hypothesis file is frozen at `hypothesis register` (`hypotheses.params_sha256`, `registered_at`); a changed parameter is a new sweep variant, registered and counted.
- **Every run is a trial.** `results.family_n` counts the family's `ok` in-sample trials as N (`trial_results.n_trials`); SR* (`sr_star`, the expected maximum Sharpe of N trials, `metrics.expected_max_sharpe(N, V)`) rises with N, and the deflated Sharpe ratio (`dsr`, `dsr_excess`) is the probability the observed Sharpe beats SR*. Refused runs are logged but do not count.
- **Refuse, don't warn.** `holdout.decide` refuses a window past the development boundary or into the holdout (`refused_window`, `refused_holdout`), a sweep variant spending the holdout (`refused_variant`), a spend over the family cap, hard and with no override, "because agents run backtests too".
- **Judge by pre-registered thresholds.** A sweep declares `promote_at_least` and `retire_below` on `dsr_excess` before it runs; promotion and retirement are `owner_decisions` rows (`promotion`, `sweep_retired`) with reasons.
- **Spend the holdout once.** `--spend-holdout --holdout-reason`, one spend per family under `max_family_holdout_spends`; a **forward holdout** (its `holdout.start` after registration) is judged by the paper book under the ADR 0005 tracking check, over `paper.min_rebalances`.
- **Then paper, with a daily check.** `paper run` each session (switch check, stale-data check, fills, reconcile, mark, plan, trade); `paper check`'s four lines (`rebalance_count`, `tracking`, `chain`, `override_reason`); the kill switch is never delayed.
- **Code computes numbers; agents draft; LLM output never reaches an order** (ADR 0008). The owner sets the backlog order, merges files, registers, spends holdouts, decides ADRs.

Three of these are *moments* the owner acts in (register, spend, decide); one is a *cost* he incurs (the trial count); the rest are *machine rules*. The interaction follows that split: a deliberate step for each moment, a visible counter for the cost, silence for the rules.

## 2. Tools that overlap our method, and why they are built the way they are

| Tool | Unit of work | Core loop | Human decides | Machine decides | Overfitting: encourages or prevents | Fits us | Clashes |
|---|---|---|---|---|---|---|---|
| **QuantConnect** | project (code, parameters, backtests, optimizations, live) | edit → Backtest → results tab → optimize → deploy wizard | everything, including when to stop iterating | nothing; a gauge counts backtests, parameters and hours and labels 70+ backtests "probably overfitting", but "does not restrict the number of backtests" | encourages: unlimited one-click reruns, an optimizer (up to 3 parameters, paid in credits); prevents a little: an org-level out-of-sample period and the gauge | one container per strategy; result page as a tabbed record with the code snapshot; backtest-to-live wizard; the gauge's *idea* (count the trials) | IDE first; free reruns; optimizer; "clone" a backtest into a new project |
| **Quantopian** (closed 2020) | algorithm; research notebook apart | notebook → algorithm → backtest → paper → contest → allocation | the author iterates freely | the fund judged on **out-of-sample = after the code was frozen**, 6 months minimum, 50+ features | encouraged in the IDE, prevented at the gate: their own study of 888 algorithms found backtest Sharpe predicts out-of-sample with R² < 0.025 and "the more backtesting a quant has done for a strategy, the larger the discrepancy" | the code-freeze date as the start of evidence; judging on out-of-sample data; pass/fail criteria with values | the contest as a leaderboard (we rank a book only against its own backtest's tracking check, ADR 0017) |
| **Composer** | symphony (a tree of rules) | build tree → backtest → invest → it rebalances for you | the rules; when to edit | execution, rebalancing | encourages: edit and re-backtest freely; editing a live symphony "liquidates and reinvests at the next scheduled trade" | the rule as its own explanation; "what it held on this date"; automation that removes the daily hand | no trial count; edits to a live strategy are one click (ours is a new variant, counted) |
| **RealTest / AmiBroker / Build Alpha** | a script file (RealTest: "like filling out a form"); an analysis window | think → test → review → repeat; walk-forward; noise tests | everything; Parker keeps a "discretionary layer" and trades "less than 5 minutes per day" | walk-forward picks parameters per interval; noise test re-trades on 1,000 perturbed series | encourages reruns by design (a workstation is for iteration); prevents by tests after the fact (walk-forward, noise) | the script as a frozen, readable form; "a backtest run in the future must match today's live trades" (Parker); results in tabs; 5 minutes a day | the optimizer is the main tool; no registry of trials |
| **MLflow / W&B** | run, inside an experiment or project; registered model with versions and aliases | log every run → compare → register the chosen version → alias it (`champion`) → deploy | which version to promote | lineage: each version "is linked to the run that produced it" | neutral: they count and keep everything, the precondition for honesty, but the compare view invites picking the best cell | the run ledger; lineage (code version, data cutoff, config) on every result; promotion as an explicit, auditable step; aliases instead of editing | sweeps as hyperparameter search |
| **OSF / AsPredicted** (pre-registration) | a registration | draft → submit → **frozen** (OSF: 48-hour approval window, then immutable; AsPredicted: nine questions, timestamped PDF, "cannot be modified") → collect data → report deviations in words | what to predict; when to make it public | immutability, the timestamp | prevents: the whole point; deviations are "declared, not hidden"; "the purpose of registration is not to put the study in a straitjacket but to make departures transparent" | pre-registration as a short, standard form; a cooling window before it locks; withdrawal leaves a record; updates by a justified, visible process | none; this *is* our method |
| **Registered Reports / ClinicalTrials.gov** | a protocol | stage 1 review before data → in-principle acceptance → stage 2 | the question and analysis | the journal binds itself to publish | prevents outcome switching; after registration became mandatory, large positive cardiac trials fell from 57% to 8% (secondary source) | our hypothesis file is a stage-1 protocol; the `TP-` claim is stage 2, good or bad | none |
| **Numerai** | a model slot | download live data → predict → submit → scored over 20 updates per round → stake pays or burns | what to submit; how much to stake | scoring on live data only; payouts | prevents by construction: no backtest is scored, only out-of-sample rounds; "skin in the game" | the forward holdout as the only evidence of record; long-run averages, not a single round | tokens and a leaderboard |
| **Electronic lab notebooks** (21 CFR Part 11) | an entry | write → timestamp → sign and witness → audit trail of every later change | the record | immutability, before-and-after on every change | prevents retrospective editing | `owner_decisions` with reasons is our audit trail; show it as one | none |
| **Google SRE / PagerDuty** (ops) | an alert | symptom → page → act | what to do | what is worth a human | n/a | "every page should be actionable"; "every page response should require intelligence"; dashboards for sub-critical issues; PagerDuty: 30 to 40% of alerts are noise | n/a |
| **Robinhood** (the glance) | the portfolio | open → one number, its change, the chart → detail on scroll | nothing | nothing | n/a | the hierarchy for a one-minute check; widgets | everything else (it is built to make trading frequent) |
| **freqtrade's FreqUI** | a bot | watch open trades; start / stop / force-exit; backtest from the UI | stop, force exit | the bot | encourages reruns from the UI | bot controls as the few writes; mobile-responsive; ADR 0018 already borrowed its API shape | backtesting from the same screen as live |

**The gap, confirmed.** No tool found puts N and the deflated Sharpe ratio beside the winning result as a *cost*. QuantConnect's gauge is the closest and it is advisory. Lopez de Prado's line ("backtesting is not a research tool") is the stance; we are the only one here that builds the multiple-testing adjustment into the Run button.

## 3. QuantConnect, answered

| In QuantConnect | Verdict | For TradePartner |
|---|---|---|
| One project holds everything about a strategy | **Take.** This is workflow.md's strategy page. | Specification, trials, results, holdout, book, decisions, all under one name |
| Backtest results table per project, random names, clone, delete | **Take the table, not the verbs.** | The trial registry (`trials`) with `n_trials` at the time of each run; no clone, no delete (append-only) |
| Result page: overview, orders, trades, logs, code snapshot, updating while it runs | **Take.** | Result tab with the frozen settings, `code_version` and `data_cutoff` beside the numbers; a visible "running" state |
| Backtest button: one click, unlimited | **Reject.** | Run is a counted trial and says so before the confirm |
| Parameters editable outside the code; optimizer up to 3 parameters | **Reject, replace.** | A read-only frozen specification; a sweep registered with `promote_at_least` and `retire_below` is our optimizer |
| Research guide gauge (0 to 30 backtests "likely not overfit", 70+ "probably") | **Take the idea, make it real.** | N is our gauge, and it is not advisory: SR* moves and DSR is deflated by it |
| Org-level out-of-sample period | **Already stronger here.** | The development boundary and the holdout are refused by `holdout.decide`, not configured |
| Deploy wizard to live, notifications, stop and liquidate | **Take the shape.** | `paper start` as a stepped confirm; `paper kill` and `paper stop` as two different, named actions |
| IDE first | **Reject.** | The owner is an end user here; code is for agents and the desk |

So: yes, it looks like a decent UI, and its container-and-record layout is what a strategy page should feel like. Its loop is built for a researcher paid per backtest. Ours is built for one person who must not fool himself.

## 4. The four loops, in detail

### 4.1 Morning check (phone, every session)

Pattern: Google SRE's paging rule, plus Robinhood's glance hierarchy. One screen, three questions, in this order:

1. **Is anything wrong?** The scheduled jobs: last ingest, each book's last `paper run` (`paper_run_results.status`: ok, halted, stale, skipped_kill_switch, crashed, failed), `reconciliations.status`, `alerts`. Shown only when not ok. When all is well, one line: "Ran at 08:05, reconciled, no alerts."
2. **Is anything waiting on me?** A finished trial to read, a decision due (a sweep that reached a threshold, a holdout ready to spend, a book past `paper.min_rebalances`), an agent draft to review. Each is one row that opens loop 2, 3 or 4.
3. **How are the books doing?** Each book against its *own* backtest under the ADR 0005 tracking check (`paper check`'s `tracking` line; `tracking_error_spy` from `trial_metrics`), never against another book. One number and its position against the tolerance.

Writes from this screen: **the kill switch**, with a reason, one tap plus the reason, never delayed. Nothing else. Resume stays at the desk (ADR 0018 question 4).

### 4.2 Read a trial result (desk, when a run finishes)

Pattern: Registered Reports' stage 2, MLflow's run page, Quantopian's pass/fail with values. The page reads in this order:

1. **The hypothesis and its pre-registered thresholds.** What the file asked, the prior it wrote down, `promote_at_least` and `retire_below`, the kill criterion. These were frozen before the run; they lead.
2. **The result against them.** Excess return over SPY, `dsr_excess` against `promote_at_least`, DSR on its scale with `n_trials` and `sr_star` beside it (ⓘ: "deflated Sharpe ratio: the probability the observed Sharpe beats SR*, the Sharpe you would expect from the best of N trials by chance"). Cost sensitivity at each `cost_per_side_bps` level in `trial_metrics`. The survivorship-gap count share against `gap.count_share_threshold`. Each a pass or fail with its value.
3. **What the pre-registered rule decides now.** "Promote variant 3" / "Retire the sweep" / "Nothing: below `promote_at_least`, above `retire_below`; 2 variants of 3 left in the family's trial budget." The owner confirms, or disagrees with a reason that is recorded in `owner_decisions`.
4. **How it was computed**, behind one disclosure: the rebalances (`trial_rebalances`), equity by cost level (`trial_equity`), the step-through, the frozen properties. This is the workstation view; it teaches, it does not decide.

Agents' part: draft the `TP-` claim from the result. The owner approves it as part of step 3, so "Record" (workflow.md's stage 9) stops being a separate chore.

### 4.3 Decide (desk; the kill switch also on the phone)

Every decision is one screen with the same anatomy: **what happens, what it costs, what it needs from you, then one button.** From the lightest to the heaviest:

| Decision | Reversible? | Recorded as | Friction | Inline teaching (one sentence, before the button) |
|---|---|---|---|---|
| Run a registered hypothesis on development data | No (N rises) but cheap | `trials` row | Shown cost, one confirm, no reason | "Becomes trial 7 of the momentum family (N = 7). SR*, the Sharpe expected from the best of 7 trials by chance, rises; every later DSR in this family is deflated against it." |
| Engage the kill switch | Yes (resume) | `kill_switch` engaged | Reason required, no other step; works from the phone | "Holdings stay. The next scheduled run submits nothing until you resume after a reconciliation with status ok." |
| Release the kill switch (resume) | Yes | `kill_switch` released | Desk only; refused unless reconciled | "Needs `reconciliations.status = ok` first. Last one: ok at 08:06." |
| Register (pre-register) a hypothesis | No | `hypotheses` row, `params_sha256`, `registered_at` | The frozen values listed, the holdout named with its kind (historical or forward), the family's trial budget stated, then confirm | "From here these parameters cannot change (`params_sha256`). A change is a new variant and counts against the budget of 3." |
| Promote a variant / retire a sweep | No | `owner_decisions` (`promotion`, `sweep_retired`) | Reason required; the rule's own verdict shown first | "The pre-registered rule says promote (`dsr_excess` 0.93 ≥ `promote_at_least` 0.90). Disagreeing is allowed and recorded with your reason." |
| Start a paper book | Mostly (`paper stop` sells) | `paper_windows` | Stepped confirm, like a deploy wizard: cadence, account flat, gap sign-off | "This is the family's forward holdout: the book's tracking check is the evidence of record, over at least `paper.min_rebalances` rebalances." |
| Spend the holdout | **Never** | `holdout` trial, `owner_decisions.holdout_spend` | Reason required; a separate screen, never a checkbox on Run; the cap shown ("1 of `max_family_holdout_spends` left") | "The holdout is out-of-sample data this family has never read. It is spent once, on this frozen finalist. There is no second holdout." |
| Move the development boundary | **Never** (a new row, never an edit) | `owner_decisions.development_boundary` | Reason naming every family affected; type the date | "Trials run under the old boundary stay counted in N and become stale: they never select and are rerun." |
| Override the survivorship gap | Once per run | `owner_decisions` | Reason required; separate action | "Accepting a survivorship-gap count share above `gap.count_share_threshold` for this run only." |

Friction scales with irreversibility, and nothing nags: a decision asks once, with the consequence, and is then silent. The daily jobs ask nothing.

### 4.4 Review ideas and drafts (desk, weekly or when a result lands)

Pattern: the backlog as a board by the five stages, with Parked and Retired to the side; MLflow's lineage for the draft. Two actions only:

- **"Draft this as a hypothesis"** on a backlog item starts an agent. Its draft comes back for review *against its claims* (the ids it cites in `claims.toml`, their grades, the prior, the kill criterion, the family's trial budget). The owner reads, asks for changes, or accepts. Acceptance merges the file (today a PR; question 3 below).
- **"Register"** is the pre-registration step of 4.3, and it is the transition from Idea to Testing.

Agents never reorder the backlog; they propose, with reasons, and the owner moves the row.

## 5. Who does what

| | Human (owner) | Engine (code) | Agents |
|---|---|---|---|
| Decides | backlog order; what to register; promote or retire against the rule; holdout spend; book start, kill, resume; the development boundary; ADRs | window and holdout refusals; N, SR*, DSR, the verdict against the thresholds; the risk gate; reconciliation; halts; quiet intervals | nothing |
| Computes | nothing | every number on screen | nothing |
| Drafts | reasons, in his words | nothing | hypothesis files, sweep files, `TP-` claims, ADRs, backlog re-ranking proposals |
| Reads | Today; results; the backlog | the store | claims, the backlog, the result |
| Writes to the store | through the engine's commands only | yes | never |

## 6. Show it, teach it, keep it intuitive: the principle and how to do all three

**The principle.** The app must make the method's strictness visible (frozen parameters, every trial counted, the holdout spent once), teach it as it goes, and still feel like a product rather than a procedure. These pull against each other only if strictness is implemented as *nagging*, or if the terms are hidden behind friendlier words, which breaks the teaching. The way out is to split strictness into three things that live in three places:

1. **State that is always on screen** (show). Not a warning, a fact, in the engine's terms: "trial 6 · N = 6 · SR* 1.84 · DSR 0.73 · holdout unspent · registered 2026-10-02 · a1b2c3". Like Stripe's orange test-mode bar, like Beeminder's bright red line on the graph, like a git SHA beside a commit. Once it is always there, nobody has to be told.
2. **One sentence at the moment of a decision** (teach). The consequence, in the real terms, with an ⓘ that explains each term by the repo's method (`backtest/metrics.py`), before the button, and the cost in the thing that is actually at stake (N, SR*, spends). Like Into the Breach showing every enemy's move before you commit, like GitHub's "this cannot be undone", like AsPredicted's form that takes "less effort to evaluate than the study itself".
3. **Absence** (intuitive). Where the engine already refuses, the app does not offer. No window picker past the development boundary; no parameter fields; no "spend holdout" checkbox; no Run during a quiet interval (it queues and says when). Strictness the user never meets is strictness that never annoys.

**Prior art for cost without nagging**, and what each gives us:

| Pattern | Where it comes from | What it teaches us |
|---|---|---|
| Consequence-specific confirm, rare | GitHub's delete repository: three statements, then type the name; the usability literature's rule that generic "Are you sure?" trains dismissal | State the exact consequence; make the confirm rare and proportional (type the date for a boundary move, one tap for the kill switch) |
| Commit is permanent; edit makes a new object | git: `--amend` creates a new commit, the reflog keeps the old one | No edit of a registered hypothesis, ever; a change is a new registered variant with lineage to its parent, and the old one stays in the registry |
| Freeze with a cooling window | OSF: 48 hours to cancel after submit, then immutable; withdrawal leaves metadata | Registration shows "registered at 14:02, `params_sha256` a1b2c3" and allows retirement (a record), never deletion; a preview before the freeze is enough, we need no 48 hours |
| A short standard form | AsPredicted's nine questions | The registration preview lists the same few frozen keys every time: universe, signal, cadence, costs, holdout and its kind, `promote_at_least`, `retire_below`, kill criterion, trial budget |
| Telegraphed consequences | Into the Breach: every threat shown before you act; "harsh because it is honest" | The Run confirm shows N and SR* before, not after; the result page shows the thresholds before the result |
| The empty promise | Telltale's "X will remember that" became a joke because nothing followed | Say a consequence only when it is real and the engine enforces it; never decorate |
| Mode made visible, keys kept apart | Stripe: `sk_test_` vs `sk_live_`, orange banner in test mode | Paper and live books carry their mode on every screen; the live book (Phase 6) gets its own banner and its own keys (ADR 0017 already) |
| No "just this once" | Beeminder: the catch-up is the failure mode; the line is literal | No override on `refused_window` or `refused_holdout`; the only overrides that exist (the survivorship gap, the kill switch) are their own recorded actions |
| Dashboards for the sub-critical, pages for the actionable | Google SRE | Today shows health in one line; it alerts only on `missed_run`, `halted`, `reconciliation`, `stale_data` |
| Stake and burn, forward only | Numerai | The forward holdout is the evidence of record; the book's screen shows "forward holdout: 3 of 6 rebalances" as the stake |

**Where teaching is inline, at the decision:**
- Run: the trial number, N, and SR* (ⓘ: expected maximum Sharpe of N trials, `metrics.expected_max_sharpe`).
- Register: which keys freeze, `params_sha256`, and why a change is a new variant.
- Result: the ⓘ on DSR, on `promote_at_least`, on each `cost_per_side_bps` level (HANDOFF rule 5 already).
- Holdout: once, the cap, and that a forward holdout is judged by the paper book.
- Refusals: the engine's code (`refused_window`) with its rule in one sentence ("ends after the development boundary, 2023-12-29").
- `paper kill` vs `paper stop`: two names, two sentences, side by side.

**Where strictness stays invisible, because the engine has it:** the development boundary and dead months (the date picker ends there, locked months drawn as locked, no text); variants cannot spend the holdout (no control); quiet intervals (`backtest/quiet.py`: queue, then a time); frozen keys (no form); the kill switch is never delayed (no queue for it); the risk gate (the decision's reason appears in the book's `decisions` journal, not as a prompt).

**Where it should not be:** no help pages, no onboarding tour, no tooltip on every label, no banner that repeats a rule the screen already shows. If a sentence is needed twice, the screen is wrong (HANDOFF rule 1). And no renaming: "DSR" with an ⓘ teaches; "luck check" hides the term the owner will meet in every paper and in the engine.

## 7. workflow.md: right, wrong, missing

| | Point | Verdict |
|---|---|---|
| Right | The strategy, not the chart, is the organising object; Today is quiet; the specification is read-only; Run follows the engine's rules; the holdout spend and the gap override are separate actions; one chart per decision | Keep all of it |
| Right | Five stages with Parked and Retired aside | Keep; they match the backlog's columns. Rename the stage "Exam" to **Holdout** (section 10) |
| Wrong | "Testing" swallows specify, develop and judge, so **registration** is invisible inside a stage | Keep five stages, but make registration the visible transition from Idea to Testing: its own screen, and "registered <date>" on every row after it |
| Wrong | The Result tab leads with the equity chart | Lead with the pre-registered thresholds and the verdict against them (4.2); the chart is second; the workstation is "how it was computed" |
| Wrong | Starting runs is framed as "the app shows the command" (section 3) and then as a Run button (section 6) | One answer: the Run button, under the ADR 0018 amendment; until then the command, shown with the same cost sentence |
| Wrong | Its prose uses "luck check" and "luck bar" | DSR and SR*, with ⓘ (section 10) |
| Missing | The **trial result page** as a loop of its own | Section 4.2 |
| Missing | **Record** (the `TP-` claim) has no place in the app | An agent drafts it from the result; the owner approves it on the result page |
| Missing | The **cost on the Run confirm needs an engine number**: N+1 is free, but "SR* becomes 1.91" needs `expected_max_sharpe` at N+1 with the family's current V, which no reader exposes | Question 2 |
| Missing | The **queue state**: queued for the next quiet interval, running, finished, refused | A row on Today under "waiting on me" when finished; a line on the strategy page while queued |
| Missing | **Parked** needs its reason and what would unpark it; **Retired** needs its `TP-` claim | One line each on the row |
| Missing | The agent loop's shape: where a draft is reviewed and how "draft this" starts | Question 3 |
| Missing | Comparison rules: within a family on development data, yes (`sweep report`); across families on holdout data, never | The only compare view is the sweep report's, with its thresholds drawn |

## 8. Open questions for the owner (one decision each)

1. **Where does registration happen?** (a) In the app: a screen that previews the frozen values and runs `hypothesis register` as a subprocess, which needs an ADR 0018 amendment and a `safety-reviewer` pass alongside the Run button. (b) At the CLI, with the app showing the registered result afterwards. Recommend (a), in the same amendment as Run: pre-registration is the method's most important moment and should not be the one thing done elsewhere.
2. **What does the Run confirm say the cost is?** (a) "Trial N+1 of the family" only. (b) Also "SR* rises from X to Y", which needs a small engine function (`expected_max_sharpe` at N+1 with the family's current pair-Sharpe variance V). Recommend (b) as a size-S engine task, (a) until it lands.
3. **Where is an agent's draft reviewed?** (a) As a PR, as today, with the app linking to it and showing its state. (b) In the app, as a review screen against the claims. Recommend (a) now: the repo's review machinery exists, and ADR 0008 keeps LLM output off the store either way.
4. **ADR 0018 question 7, split in two.** Reading the lab (the trial registry, the result page, the sweep report) in the app: yes, now, it is read-only and it is where the method shows. Running from the app: after the amendment. Recommend saying yes to the first half so the webapp spec can include the result page.

## 9. Glossary: the term the UI uses, and where the engine has it

| Concept | Term on screen | Engine source | One-line ⓘ |
|---|---|---|---|
| Registering a hypothesis with frozen parameters | **pre-registration**, "registered <date>" | `hypothesis register`, `sweep register`; `hypotheses.registered_at`, `params_sha256`; `backtest/frozen.py` | "The parameters were frozen before any run; this hash identifies them." |
| A frozen parameter set | **frozen parameters** (the specification) | `FROZEN_KEY_DEFAULTS`, `frozen.frozen_values` | "Changing one creates a new variant, registered and counted." |
| One backtest run | **trial** | `trials`, `trial_results`, `trial_metrics`, `trial_equity`, `trial_rebalances`, `trial_weights` | "Every run is a trial, including refused ones (which do not count in N)." |
| How many trials the family has | **N, number of trials** | `trial_results.n_trials`, `results.family_n` (counts `ok` in-sample trials) | "Each trial raises N; more trials make a high Sharpe easier to find by chance." |
| The multiple-testing adjustment | **SR\*** (expected maximum Sharpe of N trials) | `sr_star`, `sr_star_excess`; `metrics.expected_max_sharpe(N, V)` | "The Sharpe you would expect from the best of N trials with no real edge." |
| The adjusted significance of a result | **deflated Sharpe ratio (DSR)** | `dsr`, `dsr_excess`; `metrics.deflated_sharpe`; Bailey and López de Prado | "The probability the observed Sharpe beats SR*, after N trials, skew and kurtosis." |
| The single-trial version | **probabilistic Sharpe ratio (PSR)** | `psr` | "DSR with N = 1." |
| A family of related hypotheses sharing N | **family** | `config.FAMILIES`, `FAMILY_PARENTS`; ADR 0014 | "Variants of one idea count together." |
| A sweep's pre-declared rule | **promotion threshold** / **retirement threshold** | `promote_at_least`, `retire_below` (on `dsr_excess`); `sweep report`, `sweep promote`, `sweep retire` | "Declared before the sweep ran; the result is judged against it, not the other way round." |
| The family's declared number of variants | **trial budget** | the hypothesis file's budget; research-program §4 item 4 | "How many variants will run, all counted." |
| The last date any in-sample run may read | **development boundary** | `owner_decisions.development_boundary`, `trials.development_boundary`; ADR 0016 | "No in-sample run reads a session after it." |
| The reserved evaluation period | **holdout** (out-of-sample period) | `holdout.start`, `holdout.end`; trial kind `holdout`; `owner_decisions.holdout_spend`; `--spend-holdout --holdout-reason` | "Data the family never read in development; spent once per family." |
| A holdout that lies after registration | **forward holdout** (judged by the paper book) | ADR 0016 point 4; `lab status` says `forward` | "No session of it existed when the strategy was designed; the paper book is its evidence of record." |
| Sessions between the boundary and the holdout | **dead months** | ADR 0016 point 2 | "Read by no run of this family." |
| A counted trial whose default window moved | **stale trial** | strategy-lab spec vintage rule; `trials.development_boundary` | "Counts in N, never selects, rerun by the next `sweep run`." |
| Why a run did not happen | **refusal** with its code | `holdout.decide`: `refused_window`, `refused_holdout`, `refused_gap`, `refused_variant`, `needs_gap` | The rule, in one sentence, beside the code. |
| Delisted names the data cannot see | **survivorship gap** (count share) | `trial_rebalances.gap_*_share`; `gap.count_share_threshold`; `--override-gap --gap-reason` | "The share of the universe whose delisting the store may have missed, per rebalance; gated on holdout runs." |
| Result at each cost assumption | **cost sensitivity** | `trial_metrics` per `cost_per_side_bps` | "The same trial at 0, 15, 30, 60, 100 bp a side." |
| Paper against its backtest | **tracking check** (ADR 0005) | `paper check` `tracking`; `paper report`; `tracking_error_spy` in `trial_metrics`; `paper.min_rebalances` | "Does the book track the backtest recomputed over the same sessions, within tolerance." |
| The book's emergency stop | **kill switch** (engaged / released) | `kill_switch`; `paper kill`, `paper resume` | "Holdings stay; runs submit nothing until released after a reconciliation with status ok." |
| Closing a book | **stop (closes the window and sells)** | `paper stop`; `paper_windows` | Kept apart from the kill switch on every screen. |
| Account against journal | **reconciliation** | `reconciliations.status` (`ok`, `mismatch`, `pending_unresolved`, `fills_lagging`) | — |
| A recorded owner action | **owner decision** | `owner_decisions` (`promotion`, `sweep_retired`, `holdout_spend`, `development_boundary`, …) | "Every one carries a reason." |
| When runs may write to the store | **quiet interval** | `backtest/quiet.py` | "Never during the evening ingest or a book's submit window." |
| What a finished test adds to knowledge | **`TP-` claim** | `docs/research/claims.toml` | "Our own evidence, graded like any other claim." |
| Benchmarks | **SPY**, **MTUM** (total return) | ADR 0005 | Measured against, never targeted. |

## 10. Invented names in the spike to retire

The owner's rule: real terms, explained at the point of use. These use friendly stand-ins and need a rename (files on `spike/design-ui`):

| Where | Invented | Real term |
|---|---|---|
| `web/src/components/LuckInfo.tsx` (component name, "How the luck check works", "Luck bar", "luck = Φ(…)") | luck check, luck bar | DSR; SR*; rename the component (`DsrInfo`) |
| `web/src/components/Visuals.tsx` `LuckScale` ("probably luck / could be luck / likely real"), `ExamSteps` | luck scale, exam | DSR scale (zones labelled by DSR value, e.g. "DSR 0.73"); holdout progress |
| `web/src/lib/types.ts` fields `luck`, `tries`, `luck_bar_note`, `exam`, `exam_date` | luck, tries, exam | `dsr`, `n_trials`, `sr_star`, `holdout`, `holdout_spent_at` |
| `web/src/screens/Overview.tsx` ("Luck check, H1", "On paper, taking their exam", "luck check") | luck check, exam | DSR; "On paper, forward holdout" |
| `web/src/screens/Trial.tsx` ("whether that could be luck", "Luck check", "The exam, on paper", "final exam window") | luck, exam | DSR; holdout |
| `web/src/screens/Research.tsx`, `TrialBench.tsx`, `samples/SampleE.tsx` | luck check, exam, tries | DSR, holdout, trials |
| `web/sample/generate.mjs`, `app-data.json` | `luck`, `tries`, `exam` keys | as types.ts |
| `workflow.md` (stage "Exam", "luck check", "luck bar", "cost in tries") | exam, luck check, luck bar, tries | Holdout, DSR, SR*, trials |
| `engine-first.md` ("luck check", "honesty strip", "exam status") | luck check, honesty strip, exam | DSR; a strip of `n_trials`, `sr_star`, `dsr`; holdout status |
| `HANDOFF.md` (rules 2 and 5, "H1's luck check 0.7262", stage names) | luck check, exam | DSR 0.7262; Holdout |
| `DESIGN.md` ("a zoned scale for the luck check"), `README.md`, `ai-tells.md`, `base44-prompt.md` | luck check | DSR scale |

Plain words around the terms stay ("could this be chance?" as a heading over the DSR value is fine); the term itself is the one the engine and the literature use.

## 11. Sources

Primary docs and papers:
- QuantConnect: [backtesting getting started](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/getting-started) (one-click launch, results table, org out-of-sample period), [results](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results) (tabs, code snapshot, clone), [research guide](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/research-guide) (the gauge: 0 to 30 / 30 to 70 / 70+ backtests; 0 to 10 / 10 to 20 / 20+ parameters; 0 to 8 / 8 to 16 / 16+ hours; "does not restrict the number of backtests"; "each backtest ... moves it one step closer to being overfitted"), [optimization](https://www.quantconnect.com/docs/v2/cloud-platform/optimization/getting-started) (3 parameters, credits), [projects](https://www.quantconnect.com/docs/v2/cloud-platform/projects/getting-started), [live deployment](https://www.quantconnect.com/docs/v2/cloud-platform/live-trading/deployment), [forum: limited number of backtests](https://www.quantconnect.com/forum/discussion/15177/limited-number-of-backtests/) (staff: the counter "allows us to gauge whether our algorithm is over-fitted").
- Quantopian: Wiecki, Campbell, Lent, Stauth, [All that glitters is not gold](http://papers.ssrn.com/sol3/papers.cfm?abstract_id=2745220) (888 algorithms; Sharpe R² < 0.025; more backtests, larger discrepancy), via [Quantpedia's summary](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/); Stauth, [Making the grade](https://www.slideshare.net/slideshow/making-the-grade-a-look-inside-the-algorithm-evaluation-processby-dr-jess-stauth-vice-president-of-quant-strategy-at-quantopian/75852992) (out-of-sample = after the code was frozen; six months).
- Composer: [backtest basics](https://help.composer.trade/article/67-backtest-basics), [slippage and fees](https://help.composer.trade/article/78-slippage-and-fees) (1 bp default), [how Composer trades](https://help.composer.trade/article/65-how-does-composer-trade) (edits liquidate and reinvest at the next trade).
- RealTest: [walk-forward](https://mhptrading.com/docs/topics/idh-topic98.htm); Marsten Parker, [TraderLion 2024 talk notes](https://retailtradersrepository.substack.com/p/marsten-parker-traderlionconference2024). AmiBroker: [walk-forward testing](https://www.amibroker.com/guide/h_walkforward.html). Build Alpha: [the noise test](https://seeitmarket.com/?p=48626).
- MLflow: [model registry](https://mlflow.org/docs/latest/ml/model-registry/) (versions, aliases replacing stages, lineage to the run).
- OSF: [create a preregistration](https://help.osf.io/article/158-create-a-preregistration) (48-hour approval, immutable, embargo, withdrawal, transparent updates). AsPredicted: [home](https://aspredicted.org/) (private until public, timestamped PDF, "cannot be modified", suggested wording for deviations). Bishop, [Pre-registration and Registered Reports](https://bookdown.org/dorothy_bishop/Evaluating-What-Works/prereg.html) (deviations declared, not hidden; the 57% to 8% figure, cited there from Kaplan and Irvin 2015).
- Numerai: [docs](https://docs.numer.ai/) (live data daily, 20 score updates per round, stake and burn, "skin in the game"), [models](https://docs.numer.ai/numerai-tournament/models) (slots, one-year average ranks).
- Ops: Google SRE, [Monitoring distributed systems](https://sre.google/sre-book/monitoring-distributed-systems/); PagerDuty, [alert fatigue](https://www.pagerduty.com/blog/uncategorized/lets-talk-about-alert-fatigue-2/); freqtrade, [FreqUI](https://www.freqtrade.io/en/stable/freq-ui/).
- Cost without nagging: GitHub, [deleting a repository](https://docs.github.com/en/repositories/creating-and-managing-repositories/deleting-a-repository); Stripe, [test mode](https://docs.stripe.com/test-mode); Beeminder, [what is Beeminder](https://wiki.beeminder.com/what-is-beeminder.html) and [the catch-up post](https://blog.beeminder.com/catchup); Atlassian, [rewriting history](https://www.atlassian.com/git/tutorials/rewriting-history); Into the Breach, [PC Gamer preview](https://www.pcgamer.com/into-the-breach-preview); Telltale, [Game Informer on "will remember that"](https://www.gameinformer.com/b/features/archive/2015/02/03/why-your-choices-dont-matter-in-telltale-games.aspx).
- The method's terms: Bailey and López de Prado, "The Deflated Sharpe Ratio" (2014), as implemented in `src/tradepartner/backtest/metrics.py` (`deflated_sharpe`, `expected_max_sharpe`).

**Not verified, and marked as such:** where in QuantConnect's IDE the research-guide gauge is drawn; Composer founders' stated reasons (the Alpaca interview gave growth, not design, quotes); Quantopian's contest rules in detail (the site is gone; the slide deck and the paper are what survive); RealTest's "like filling out a form" line (from Norgate's page via a search summary, not the user guide); the 57% to 8% clinical-trials figure (secondary, via Bishop); the Lopez de Prado quote (widely cited; the book was not opened); Robinhood's design intent (third-party critiques only). ELN claims are from vendor pages (Labguru, SciNote, labfolder), not the regulation's text.
