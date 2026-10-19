# Research Report: A day in the life of the owner, as a user

**Brief:** #1924 (the orchestrator's brief, 2026-10-19, from the owner's note on #1915)  ·  **Date:** 2026-10-19  ·  **Status:** COMPLETE  ·  **Agent/model:** team mvpreport, claude-opus-5-5  ·  **Repo at:** `888b04b0` (origin/main)

**Question.** The owner wrote: "When I use this application what is needed to really run day to day. How as a user do I monitor what I'm doing, whether that's looking at portfolio performance. How do I actually develop new strategies, is that done externally working with Claude or something. How do we read backtest results or strategy testing results in a way that is legible for myself. How do I see what trades happened on which day. Maybe some of this stays in the Alpaca platform."

[The readiness report](2026-10-19-mvp-usability-readiness.md) (#1915) answered this from the system's side: which commands exist and run. This report walks the same ground as **the owner lives it**: seven routines, what he sees, and what is missing.

**Method.** The orchestrator rendered the six Streamlit dashboard pages to text and ran the CLI, all read-only, on the owner's store on the evening of 10-19 (store schema 33, main checkout at `9d776b2d`). Quotes below are from those outputs, marked **(seen 10-19)**. I compared each routine with the owner's own prototype on `origin/spike/design-ui` ([HANDOFF.md](https://github.com/josejuarez96/tradepartner/blob/spike/design-ui/docs/research/ui/HANDOFF.md) and `web/src/screens/`). That prototype is his design for four routines: the morning check, reading a trial result, deciding, and the weekly review of ideas and drafts.

**One fact shapes everything.** No book has traded yet: **zero orders and zero fills** on any book, ever. The daily book's first rebalance is **Tue 10-20 at 08:05 ET**. Routines 2 to 4, and 8 to 11 in Part B, can only be judged on their empty states today. The Part 2 checklist at the end says what to re-walk once fills exist.

**Severity used below.**
- **High:** he cannot do the routine without a developer or an agent.
- **Medium:** he can do it, but must read developer-shaped output or know where to look.
- **Low:** cosmetic.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium (routines 2 to 4 are judged on zero trades)

- **Monitoring works on the Mac, but it does not tell him things.** The parts exist: `paper status --all`, the Operations page and the Data health page. No screen answers "did everything work, and is anything waiting on me?" in one line. Some badges look alarming without saying whether they matter.
- **Portfolio performance has no home in our system.** No page or command shows a paper book's equity, its return against SPY, or its return against the backtest's expectation. `paper report` is the one comparison command. It failed on both books tonight, and the failure added two trials to the registry (#1923). **Alpaca's own dashboard is the only equity curve he has**, and it is a good one, because each book is its own Alpaca paper account.
- **Trades by day: Alpaca shows what and when; only we know why.** Our Fills card has no date filter, no planned price and no reason. The "why" sits on another card, and only for the latest plan.
- **New strategies are made with Claude, by design, at three points.** An agent drafts the hypothesis file (he approves the PR). A new kind of signal is code, so it needs an agent build. An experiment that is not a backtest needs an agent-written script. He runs everything after that himself.
- **Results are not yet legible to a non-developer.** The backtest page has the numbers but no sentence saying what they mean. Its main table is 20 rows by 16 columns of raw fractions. The clearest verdicts in the store are two sentences someone typed by hand. The newest experiment's verdict lives only in a GitHub comment.

- **Beyond his seven examples** (Part B covers 22 routines by cadence): the risk limits enforce themselves, but he cannot see how close a book is to them. Corporate events on held names are handled in reconciliation but shown only as raw JSON. Being away means no alerts and no stop path. Upkeep and recovery (code pulls, migrations, backups) are done by the orchestrator today, and he has no one-page runbook to do them alone.

## Terms used once, explained here
- **Trial:** one backtest run, logged in the trial registry whatever its outcome.
- **Family:** a group of related ideas, such as `momentum`, that shares one holdout and one trial count.
- **N:** the number of trials the family has run. More tries make a lucky best result more likely.
- **Sharpe ratio:** return per unit of risk, per year.
- **SR\*:** the best Sharpe you would expect from N tries of pure luck.
- **DSR (deflated Sharpe ratio):** the probability that the strategy's Sharpe beats SR\*. Read it as "how likely this is real, after counting every try". Above 0.95 is strong. Around 0.5 is a coin flip.
- **Excess over SPY:** the strategy's yearly return minus SPY's.
- **Holdout:** a sealed stretch of history (here 2024-01-01 to 2026-09-30) that no test may read until he spends it once, on purpose, with a reason.
- **Tracking:** how closely a paper book follows the backtest of the same rules.
- **bps (basis points):** hundredths of a percent; 15 bps per side is 0.15% paid on each buy and each sell.

## Part A: the seven routines he named

Each is walked in full, with what he sees today. Part B places them, and fifteen more, by how often they come up.

### Routine 1: the morning check (phone in his design; Mac today)
**His question.** Did last night's 18:30 ingest and this morning's 08:05 run work? Is anything waiting on me?

**Where he goes today.**
1. `uv run tradepartner paper status --all` prints **(seen 10-19)**:
   > `book daily: window 5 open; positions 0; open orders 0; kill switch released; last run ok; next rebalance 2026-10-20`
   > `book main: window 6 open; positions 0; open orders 0; kill switch released; last run ok; next rebalance 2026-10-30`

   This is the most legible output in the system: two plain lines.
2. The dashboard (`uv run tradepartner dashboard`), **Operations** page. The Books table repeats those lines. For the selected book it then shows **(seen 10-19)**:
   > `stale: no run yet for the required session`

   That badge sits beside `last_run ok` and a green `kill switch: ok`, and nothing says which one to believe.
3. The **Data health** page leads with an orange `stale: 1 session` badge and the metric `Coverage: 0.0%`. Further down it shows `Count share 95.26% · size share 99.75% · 4096 of 4300 listed names missing (window after 2026-09-30)` and `Live names with no bar on 2026-10-19 (5298)`. All 25 integrity checks are green. The page was rendered at 19:40 UTC, before that night's ingest, so the missing bars for 10-19 are expected. **The page does not say that**, and a non-developer cannot tell an expected gap from an alarm.

**What is missing.**
- One line that answers the question, the way his prototype's **Today** screen does: "Nothing wrong. Ingest finished Fri 6:42 pm. All 3 books ran Fri morning and reconciled. No alerts." When something is wrong, it lists only the jobs that failed.
- A "waiting on you" list (a finished trial, a decision due, an agent's draft PR). ADR 0018 names this reader; it is not built.
- Phone access. Everything today is on the Mac.
- A missed run is invisible unless he looks. No paper run was recorded for 10-14 or 10-15 (the Mac slept; #1759).

**Alpaca vs ours.** Alpaca knows nothing about our ingest, our runs or our reconciliation. This routine is ours alone.

**Severity: Medium.** He can do it in two commands, but the badges need a developer to read them.

### Routine 2: portfolio performance
**His question.** How is each book doing: its equity, its return against SPY, and against what the backtest said to expect?

**Where he goes today.**
- **Operations page:** counts only, `Positions: 0`, `Open orders: 0`, `Today's signals: 0`, and under Reconciliation `broker cash: 100,008.88`. There is no equity line, no profit and loss, and no chart. The page's code has no equity section at all (`src/tradepartner/dashboard/ops_page.py`).
- **`paper report --book <name>`** is the one command that compares paper with the backtest. Tonight it failed on both books **(seen 10-19)**:
  > `failed: ValueError: tracking trial 18 did not run (failed)`
  > `failed: ValueError: tracking trial 19 did not run (refused_window)`

  It also wrote trials 18 and 19 into the trial registry as a side effect (#1923). So a read-only question changed his research record.
- When it works, its output is one line per rebalance: `raw … dividend … fill … residual … residue … cost … pass`. That is a decomposition of the tracking gap for an auditor, not a performance view for an owner.
- `paper check --book <name>` prints the four exit-criteria checks as PASS or FAIL lines. That is useful weekly (routine 7), but it is not performance.

**What his design intends.** The **Books** screen shows each book against its own backtest: a tracking gap on a scale with a shaded tolerance zone, and the forward holdout as countable progress ("rebalance 3 of 12"). HANDOFF.md notes that the per-period values need `paper_report_periods`, which ADR 0018 lists and nobody has built.

**Alpaca vs ours.** Each book is its own Alpaca paper account (`paper_windows.account_id` differs per book). So **Alpaca's portfolio chart is a correct per-book equity curve today**, with daily profit and loss. What Alpaca cannot show is the comparison with SPY, or with the backtest's expected path.

**Severity: High** for "versus the backtest" (nothing works). **Low** for the plain equity curve (use Alpaca).

### Routine 3: trades by day
**His question.** What was bought and sold on which day, at what price, why, and did every order fill?

**Where he goes today.**
- **Operations page, Fills card:** `No fills yet in this window.` When it has rows, the columns are `security_id, symbol, side, quantity, price, broker_fill_id, known_at`. It covers the whole window, newest first, and is capped with `Showing the newest fills only`. It has no date picker, no planned price beside the fill price, and no reason column.
- **Order chain card:** pick one order id from a dropdown to see its steps (`at, kind, detail`). This is good for chasing one stuck order. It is not a day's blotter.
- **Signal ranking card:** the "why". Its tooltip carries `signal_reason`, `decision` and `decision_reason` for each name. It shows **the latest plan only**, so last Tuesday's reasons are not on screen.
- **CLI:** no command lists orders or fills. The `paper` command list has `status`, `report`, `check` and `reconcile`, but nothing like `trades`. The slippage lines (fill price against the planned price) appear only inside `paper report`.

**What is missing.** A blotter for one session: name, side, shares, planned price, fill price, slippage, and the decision reason, with a date picker. His prototype does not have one either; its Book screen focuses on tracking and the kill switch.

**Alpaca vs ours.** Alpaca's **Orders** and **Activities** tabs list every order and fill by date and price, with ticker symbols. Use them for "what happened". Only our system knows **why** a name was traded and whether the fill matched the plan.

**Severity: Medium.** Alpaca covers "what". "Why" needs a developer's query or the latest-plan card.

### Routine 4: something went wrong
**His question.** An alert or a halt came in. What does it say, what do I do, and how do I resume?

**What he gets today.**
- Alerts go to two channels only: the store and a **macOS notification** (`alert_deliveries`). The email channel is built but not configured. **Away from the Mac, he hears nothing.**
- The real halts so far read like this (`kill_switch.reason`, **seen 10-19**):
  > `AlpacaTradingError: alpaca trading request failed (404): {"c…` (daily book, 10-16)
  > `ReconciliationError: reconciliation 4 of window 2 on 2026-10…` (daily book, 10-13)

  These are exception names and broker codes, not instructions.
- **What he does:** read the Operations page's Alerts and Reconciliation cards, then the [scheduling runbook](../runbooks/scheduling.md)'s fault section. Its alert table lacks four of the alert kinds the code can send (#1915 found this). There is no entry for the 404 on a delisted name (#1480).
- **How he resumes:** `uv run tradepartner paper resume --book <name> --reason "<why>"`, which settles, reconciles and releases the kill switch. That step is mechanical and he can do it alone. **Diagnosing why it halted has needed an agent every time** (four of four real faults).

**What his design intends.** Today's not-healthy panel lists only the failed jobs and what they hold. The Book screen adds a **kill switch history** with reasons. Engaging the kill switch also works from the phone.

**Alpaca vs ours.** Alpaca shows a rejected order with its reason. It does not know that our book halted.

**Severity: High.** Off the Mac he gets no alert, and on it the diagnosis needs an agent.

### Routine 5: a new strategy idea
**His question.** I have an idea. How do I get from the idea to an honest, registered result, and which parts do I do myself?

**The path today**, with who does each step:

| Step | Who | What it means for him |
|---|---|---|
| 1. Check the idea against the evidence: [hypothesis-backlog](hypothesis-backlog.md), [claims.toml](claims.toml) | He asks Claude | Has this been tried? What does the literature grade it? |
| 2. Write the hypothesis file (`docs/hypotheses/<slug>.md`, [template](../templates/hypothesis.md)) | **An agent drafts it on his request**; he approves the PR | The file freezes every setting before any run. Changing a setting later is a new hypothesis, never an edit. |
| 3. If the idea is a **new kind of signal** (not new settings for momentum or profitability) | **An agent builds it** under a plan task | Signals are code (`backtest/signals.py`, `strategies.py`). A file can only choose among keys that exist. B10, for example, waits on the new key `strategy.turnover_top_fraction`. |
| 4. `uv run tradepartner hypothesis register docs/hypotheses/<slug>.md` (or `sweep register` for a set of variants) | He runs it | Prints the frozen settings and their hash. |
| 5. `uv run tradepartner backtest <slug>` (or `sweep run <slug>`) | He runs it | A trial. **It counts in the family's N even if it fails**, which raises the bar (SR\*) for every later idea in the family. A window that touches the holdout is refused unless he adds `--spend-holdout --holdout-reason "…"`. Each family may spend its holdout at most 3 times, and `momentum` has spent 1. |
| 6. Read the result (routine 6) | He reads; Claude explains | |
| 7. An experiment that is not a backtest (T218's kind) | `experiment register`, `dataset register`, `experiment open`, **then an agent-written script**, then the verdict read by hand | T218 took a seven-step sheet in a GitHub comment (#1876). There is no `experiment run` command. |

**How long it takes** (from the registry, **seen 10-19**): the daily-shakedown backtest (trial 17) ran from 18:40 to 20:31, almost two hours. The six-variant sweep (trials 11 to 16) ran from 14:16 to 16:41. The machine time is short. The wait is the PR review for the file, and for a new signal a plan task and its build, which takes days.

**So: is it done externally, with Claude?** Yes, for drafting and for anything that is code. That is intended: the [strategy-lab spec](../specs/strategy-lab.md) has agents draft files so that the owner's approval is the gate. Running and reading are his. What is missing is an owner-facing page that walks steps 1 to 7 in order. `docs/runbooks/` has no research runbook.

**What his design intends.** The **Strategies** screen shows every idea by stage (Idea, Testing, …) and what each one waits on. Agent drafts appear as linked PRs with their state. **Register** and **Run** become buttons, and the Run confirm says what the run costs: "Becomes trial 9 in the momentum family. SR\* rises from 0.58 to 0.61". Those writes wait on the ADR 0018 amendment and a size-S engine function.

**Alpaca vs ours.** Alpaca has no part in research.

**Severity: Medium.** It works, by design with an agent. Non-backtest experiments are **High**: they need a script each time.

### Routine 6: reading a result
**His question.** Is this strategy any good, and how sure can I be?

**What the Backtest page shows** (trial 17, **seen 10-19**):
- The header is clear: `momentum-daily-shakedown · family momentum · kind in_sample · window 2020-08-31 → 2023-12-29 · status ok`.
- Three charts (equity against SPY and MTUM on a log axis, drawdowns, turnover and costs). They render in the browser but not in the text dump, so this report cannot judge them.
- **Metrics per cost level:** a table of 20 rows by 16 columns (`MTUM @ 0.0 bps (ladder)` … `strategy @ 100.0 bps (ladder)`). Values are raw fractions: strategy `cagr 0.049506` against SPY `0.110851` at the base cost. So **the strategy made 5.0% a year while SPY made 11.1%**, but no sentence on the page says so. Metric names such as `sharpe_period_excess_spy` are code names.
- **Deflated Sharpe:** a 2-row by 15-column table. The answer to "how likely is this real" is in the cell `today DSR 0.131177` on the `excess_spy` row. Read plainly: about a 13% chance that its edge over SPY is more than luck, after 11 tries in the family.
- **Weights:** names appear as `security_id` (`0000002488`), not tickers.
- **Per rebalance:** 838 rows by 15 columns of survivorship-gap detail, which is for audit.
- **There is no verdict line.** A single trial has no pass or fail on screen.

**The Trial registry page** has the family view (`N today: 11`, `SR*_annual: 0.294`) and the trial list. Its most legible text is a sweep's retirement reason, written by a person:
> `Run 4 (trials 11-16, 2026-10-10): all 6 variants lose to SPY (-2.6% to -6.4%/yr excess); promotion refused, retire_below met.`

**The Research page** (experiments) shows a 23-column registration row, and the run's verdict is `n/a`. The T218 result lives only in a GitHub comment on #1876:
> `Primary net_spread_b30_pp_per_year = -6.82 (95% CI -24.91 to +11.27, n 40, se 11.0). Kill rule: value below 0 → kills the long/short momentum idea`

That says: the long/short book lost 6.8 points a year after costs. Its 95% range (CI, the confidence interval) runs from −24.9 to +11.3, so the true edge could be anything in that range. The rule decided in advance to kill the idea, and it did. **The store holds the run but not the verdict**, and the result file is gitignored.

**What his design intends.** A **Result** tab that leads with the thresholds he set before the run, then pass, fail or reported rows with values, cost sensitivity as five ✓/✕ tiles, and "what the pre-registered rule decides", with Agree or Disagree and a reason. It has one chart, and an ⓘ beside DSR, N and SR\*. Every number on our page exists in the store. The gap is the reading layer.

**Alpaca vs ours.** None.

**Severity: Medium** for backtests and sweeps (the numbers are there; he needs the explanation). **High** for experiments (the verdict is not in the system).

### Routine 7: the weekly review
**His question.** How did the week go? What should I decide? What are the agents waiting on me for?

**Where he goes today.** Nowhere in particular.
- `paper check --book <name>` for the four exit criteria.
- `lab status` for registry counts and open sweeps.
- GitHub for agent PRs.
- [STATUS.md](../STATUS.md) and the orchestrator's handover for project state.
- No weekly digest exists, and no screen lists decisions due.

**What his design intends.** Desk, weekly: the Strategies screen by stage, each row with what it waits on and from whom. Agent draft PRs with their checks. Parked and retired ideas with the reason and what would unpark them.

**Alpaca vs ours.** Alpaca's monthly statements and account history cover the money side.

**Severity: Medium.**

## Part B: every routine, by how often it comes up
One line per routine. §n points to its section in Part A. **Sev** uses the scale above. "No task yet" means no open issue or plan line covers the gap. NOT VERIFIED means the claim was not checked against a real output.

### Daily
| # | Routine: his question | Where today | What is missing | Alpaca | Sev | Task |
|---|---|---|---|---|---|---|
| 1 | Morning check: did it all work, is anything waiting on me? | §1: `paper status --all`; Operations and Data health pages | One-line answer; "waiting on me" list; badges that say whether they matter; phone | none | Med | ADR 0018 reader: no task yet |
| 2 | Performance: equity, vs SPY, vs backtest | §2: no page; `paper report` fails pre-rebalance | Any performance view of a book | equity curve and daily P&L per book | High | #1923; `paper_report_periods`: no task yet |
| 3 | Trades by day: what, price, why | §3: Fills, Order chain, Signal ranking cards | Per-session blotter with reason and planned-vs-fill price; CLI listing | Orders and Activities tabs | Med | no task yet |
| 8 | Risk and capital: cash, exposure, concentration, drawdown vs limits, headroom | Limits are config (`risk.max_position_weight` 0.05, `max_order_notional_fraction` 0.05, `max_gross_exposure` 1.0, `max_drawdown` 0.30) and enforce themselves: a 30% drop from the peak engages the kill switch with a `drawdown` alert. The Operations page shows only `broker cash: 100,008.88` (seen 10-19) | Current value beside each limit, and the headroom left; the Operations page has no exposure, weight or drawdown figure | cash, buying power, equity, position values | Med | no task yet |
| 9 | Holdings events: splits, dividends, mergers, delistings on held names; dividends received | Each run's reconciliation explains broker activities (dividends, cash in lieu, symbol changes) against the store. The Operations page shows them as raw JSON: `{"activities": [], "allowed": [], "explained": [], "lagging": [], "mismatches": [], "pending": [], "symbol_changes": []}` (seen 10-19). An unexplained one halts the book | A plain list of "events on names you hold" and "dividends received"; known holes: a merger's acquirer shares, a spin-off child, a delisted name's 404 | Activities tab lists dividends and corporate actions | Med | #1762, #1896, #1480, #1760 |
| 14 | Data trust: is today's data good enough? | `tradepartner health --check`; the Data health page (25 integrity checks green; `stale: 1 session`, `Coverage: 0.0%`, seen 10-19) | One sentence: "data is fine for tomorrow's run", or what is not | none | Med | #1922; wording: no task yet |

### Event-driven
| # | Routine | Where today | What is missing | Alpaca | Sev | Task |
|---|---|---|---|---|---|---|
| 4 | Something went wrong: alert or halt, then resume | §4: macOS notification; Operations alerts; `paper resume --book <b> --reason` | Off-Mac alerts; diagnosis without an agent; runbook entries | rejected orders only | High | #1480, #1848, #1921; email: no task yet |
| 17 | Being away: phone alerts, "stop everything", a sleeping Mac | Alerts go to the store and macOS only; email is built (`alerts.channels`, the `ALERT_EMAIL_*` variables) but not configured. "Stop everything" is `paper kill --all --reason`, on the Mac only. A sleeping Mac skips the run (10-14 and 10-15 have no run) | Any alert or kill path off the Mac; ADR 0018 leaves phone access undecided. Closing positions in Alpaca's app would bypass our journal; the next run would likely halt on a mismatch (NOT VERIFIED) | mobile app: positions, cancel and close | High | #1759; email and phone: no task yet |
| 21 | Recovery: corrupted store, bad release, restore | [data-releases](../runbooks/data-releases.md): a file backup before each release, and restore steps (`rm -f data/tradepartner.duckdb.wal`). [scheduling](../runbooks/scheduling.md) "Before pulling a schema migration": copy the store first | No routine backup schedule; no runbook for a store corrupted outside a release (disk, power loss) | none | Med | no task yet |

### Weekly
| # | Routine | Where today | What is missing | Alpaca | Sev | Task |
|---|---|---|---|---|---|---|
| 7 | Weekly review | §7: `paper check`, `lab status`, GitHub, STATUS.md | One weekly screen or digest; decisions due | statements | Med | no task yet |
| 10 | Costs: real slippage and fees vs the backtest's 15 bps per side | `paper report` ends with slippage lines (fill vs planned price); it fails before the first rebalance. `backtest --measured-cost-bps <x> --note "<source>"` re-runs a backtest at a measured cost (in-sample only) | A page showing measured against modelled cost per book | fills and fees (commission 0) | Med | #1923; view: no task yet |
| 11 | Attribution: what drove the period; paper vs backtest drift | `paper report` splits each period's tracking gap into `raw`, `dividend`, `fill`, `residual`, `residue` and `cost` terms, and lists paper weight vs trial weight per name | Return contribution per position or decision; no P&L anywhere in our pages | unrealised P&L per position | Med | `paper_report_periods`: no task yet |
| 13 | Research pipeline: what is being tested, what is next, N and holdout left | [hypothesis-backlog](hypothesis-backlog.md) and [claims.toml](claims.toml) (agent-kept files); the Trial registry page: `N today: 11`, `Holdout spends: 1 of 3`, `Promotions: 0 of 2` for `momentum` (seen 10-19); the Research page; `lab status` | One list by stage with what each item waits on (his Strategies screen) | none | Med | ADR 0018: no task yet |
| 16 | Working with agents: directing work, reviewing | Orchestrator sessions; the handover comment on #836; class-B PRs need his word; owner-run tasks (T161, T162, T165e, T218); owner decisions in chat | One "waiting on me" list across PRs, decisions and owner-run steps. His review time per week is not measured (NOT VERIFIED) | none | Med | no task yet |

### Monthly or occasional
| # | Routine | Where today | What is missing | Alpaca | Sev | Task |
|---|---|---|---|---|---|---|
| 5 | A new strategy idea | §5: an agent drafts the file; `hypothesis register`, `backtest`, `sweep *`; experiments need a script | An owner research runbook; `experiment run` | none | Med (experiments High) | no task yet |
| 6 | Reading a result | §6: Backtest, Trial registry and Research pages | A verdict sentence; experiment verdicts in the store | none | Med (experiments High) | no task yet |
| 12 | Lifecycle: promote to paper, compare books, stop or retire | Promote: `sweep promote`, `decision gap-signoff --trial <id> --reason`, then `paper start`. Stop: `paper stop` (sells and closes the window), `paper kill` (halts, keeps positions), `paper abandon` (ends the window without selling). Retire: `sweep retire`. Records: `owner_decisions`, shown as "Owner decisions" on the Trial registry page (15 rows, seen 10-19) | Books side by side; a retire command for a single hypothesis (only sweeps have one); the prototype's Decisions tab with reasons | none | Med | no task yet |
| 15 | Software upkeep: new code, schema migrations, `uv sync`, schedulers | The orchestrator does it. On 10-19 it migrated the store to schema 33 after a copy check; schema 34 is on main but not pulled. Alone, he would follow the scheduling runbook's "Before pulling a schema migration" (`git diff HEAD origin/main -- src/tradepartner/store/schema.py`; any write migrates silently) | A one-page "update the system" runbook (pull, check the migration, back up, sync, restart the jobs) | none | Med | no task yet |
| 18 | Records and taxes: realised P&L, lots, wash sales, 1099-B | The lot ledger (`execution/lots.py`, wash-sale logic in `lots.py` and `outcomes.py`); `paper lots-reconcile` "Compare a broker 1099-B export with the lot ledger; writes nothing" | A realised-P&L or tax-lot report command (none in the `paper` list). Matters only once live | 1099-B and statements | Low now | no task yet |
| 19 | Going live (Phase 6) | Not started. ADR 0017: the ~$100 live account is one more book on the live endpoint under its own ADR, gated on the shakedown pass, the employer compliance check, written stop criteria (#878), a taxable cash account and a safety review. `alpaca.paper = True` refuses `false` | Day to day the same routines, plus a settled-cash read and taxes | funding, the live account | Low now | T163, T163b, T163c |
| 20 | Broker admin: account status, funding, key rotation | One Alpaca paper account and key pair per book (T159), in `.env` as `ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY`; a separate sandbox account for probes | A key-rotation runbook (none found in `docs/runbooks/`) | account status, keys, paper reset | Low | no task yet |
| 22 | Running costs | The charter's budget is $0 a month for the running system (data, APIs, LLM calls; ADR 0009). Alpaca data, FRED and EDGAR are free. Agent and Claude spend is development tooling, outside that budget and not tracked in the repo (NOT VERIFIED) | A monthly tally of agent spend | none | Low | no task yet |

### Added: operator duties the runbooks and specs name that the list above does not
| Duty | Where | Sev | Task |
|---|---|---|---|
| Shakedown bookkeeping: open the span (`decision shakedown-span`), note each alert inside it (`decision shakedown-note`), then `paper shakedown` must exit 0 | [scheduling](../runbooks/scheduling.md) "The shakedown" | Med | T162 |
| The kill-switch drill, once, inside the span | scheduling, "The kill-switch drill" | Med | T162 |
| Approving a data release: back up, write the `before` and `after` records, `decision data-release` | [data-releases](../runbooks/data-releases.md) | Med | no task yet (owner-facing summary) |
| Keeping research out of the jobs' way: `sweep run` must respect the quiet intervals (16:00 to 21:00 ET, and around the 08:05 run) | scheduling, "Sweeps and the quiet intervals" | Low | none needed |
| The development boundary: `decision development-boundary`, never written so far | ADR 0016 | Low | no task yet |
| The Override page: exclude a name from one named rebalance, with a reason | the Override page | Low | none needed |
| Settling an order no collector can close: `paper settle` | the `paper` command list | Low | none needed |
| Window restarts reset the paper record: four restarts so far (10-13 and 10-18) | `paper_window_stops` (seen 10-19) | Med | no task yet (a restart checklist) |
| After an EDGAR cache version bump | scheduling, "After an EDGAR cache version bump" | Low | none needed |

## (a) Top friction points, ranked across all routines
1. **No performance view of a paper book (equity, vs SPY, vs its backtest, attribution); `paper report` fails before the first rebalance and writes trials as a side effect.** High (routines 2, 10, 11). → #1923; `paper_report_periods` and the Books screen: ADR 0018, **no task yet**.
2. **Away from the Mac he gets no alert and has no stop path; a sleeping Mac skips runs.** High (4, 17). → #1759; email channel and phone access: **no task yet**.
3. **Every real halt so far needed an agent to diagnose; runbook entries are missing.** High (4, 9). → #1480, #1848, #1921, #1762, #1896; the alert-table fix: **no task yet**.
4. **No single "did it work, what is waiting on me" answer across runs, data, PRs and decisions; badges look alarming without saying whether they matter.** Medium (1, 14, 16, 7). → ADR 0018 reader: **no task yet**; #1922.
5. **Results have no verdict sentence, and experiment verdicts live only in GitHub comments.** Medium; High for experiments (6). → **no task yet**.
6. **Non-backtest experiments need an agent-written script each time.** High for that path (5). → **no task yet**.
7. **No risk-headroom view: limits enforce themselves, but he cannot see how close each book is to them.** Medium (8). → **no task yet**.
8. **No per-day trade blotter with reason and planned-vs-fill price; no plain list of corporate events on held names.** Medium (3, 9). → **no task yet**; #1760.
9. **Upkeep and recovery are orchestrator-only: no "update the system" runbook and no routine backup.** Medium (15, 21). → **no task yet**.
10. **No research-pipeline or lifecycle screen: stages, what waits on whom, books side by side.** Medium (12, 13). → ADR 0018 Strategies screen: **no task yet**.
11. **Low now, needed before live: a realised-P&L and tax-lot report, a key-rotation runbook, an agent-spend tally.** Low (18, 20, 22). → T163 family; **no task yet**.

## (b) What to leave to Alpaca's dashboard for now
- **Each book's equity curve and daily profit and loss.** One Alpaca paper account per book, so the chart is exact.
- **The list of orders and fills by date,** with tickers and prices (Orders and Activities tabs).
- **Account statements and the money history** for the weekly look.
- **Cash, buying power and position values** (routine 8), and **dividends and corporate actions received** (routine 9, Activities tab).
- **Account admin:** keys, status and the paper reset (routine 20).
- **Not Alpaca:** "why this trade", "versus the backtest", "versus SPY", alerts, halts and anything about research. Only our system knows these.

## (c) Part 2 checklist: re-walk after the first real fills (Tue 10-20 onward)
- [ ] Morning of 10-20: does `paper status --all` show the daily book's positions and `last run ok`? Is the `stale` badge on Operations gone or explained?
- [ ] Does the Fills card list the 10-20 fills with symbols and prices? Do they match Alpaca's Activities tab line for line?
- [ ] Does the Signal ranking card's `decision_reason` explain each name traded? Is it still readable after the 10-21 rebalance replaces it?
- [ ] Does `paper report --book daily` now run, and is its output readable? Does it still write a trial each time (#1923)?
- [ ] Compare Alpaca's equity for the daily account with what our system can say about the same day. Write down the gap.
- [ ] On the first alert or halt: did the macOS notification arrive? Could the owner resume from the runbook alone, without an agent? Note the time it took.
- [ ] With positions held: does any page show exposure, the largest weight and drawdown against `risk.*`? Write down where he had to look (routine 8).
- [ ] On the first dividend or corporate action on a held name: did reconciliation explain it, and where did he see it (routine 9)?
- [ ] After the first fills: compare `paper report`'s slippage lines with the 15 bps modelled cost (routine 10).
- [ ] After five fill sessions: does `paper check --book daily` give a verdict he can read?

## Evidence
- Dashboard page dumps: Operations, Data health, Backtest (trial 17), Trial registry, Research, Override. Rendered read-only on the owner's store at 19:40 UTC on 10-19 (orchestrator evidence folder).
- CLI output: `paper status --all`, `paper report --book daily|main`, `experiments`, and the `paper`, `sweep`, `backtest`, `hypothesis` and `lab` help screens (same folder).
- Journal queries: `paper_windows`, `paper_runs`, `reconciliations`, `kill_switch`, `alert_deliveries`; orders and fills are empty (same folder).
- Code: `src/tradepartner/dashboard/ops_page.py` (Fills, Order chain and Signal ranking cards; no equity section); `src/tradepartner/cli.py` (`paper report`, `paper check`).
- Owner's design: `origin/spike/design-ui`, `docs/research/ui/HANDOFF.md` and `web/src/screens/` (Today, Strategies, Strategy, StrategyActions, Book, Trial).
- T218's result and command sheet: #1876 comments; [long-short-spike](../experiments/long-short-spike.md).

## Disconfirmation
- **Perhaps Alpaca covers performance well enough.** It does for the equity curve, which is why (b) leaves it there. It cannot compare with the backtest. That comparison is the reason the paper phase exists (tracking is an exit criterion), so friction 1 stands.
- **Perhaps the agent dependency in routine 5 is a defect.** It is not, for drafting and code: the strategy-lab spec makes the owner's PR approval the gate. Only the experiment script (friction 5) is a gap.
- **Perhaps the Data health badges are true alarms.** I could not rule that out from the dump. Either way, the friction is that the page does not say which they are.

## Caveats & gaps
- Routines 2 to 4 and 8 to 11 were judged on zero trades. The Part 2 checklist exists for this.
- Part B's routines 8 to 22 rest mostly on the code, config and runbooks, not on rendered outputs. Where a real output exists it is quoted and marked (seen 10-19).
- The charts on the Backtest and Data health pages did not render in the text dump. Their legibility is unjudged.
- I did not open Alpaca's dashboard. Section (b) relies on what Alpaca's paper accounts generally show.

## UNVERIFIED items
- Whether trials 18 and 19 count in the `momentum` family's N. The Trial registry dump shows 17 trials, so it may predate them. #1923 decides.
- What the Data health page's `Coverage: 0.0%` metric measures, and whether the `4096 of 4300 listed names missing` gap reflects that night's not-yet-run ingest.

## Follow-up questions (not answered here)
- Should the owner configure the email alert channel before the shakedown span, or after it?
- Which comes first, the reading layer in Streamlit (friction 4) or the ADR 0018 web app?

## Sources
- [2026-10-19 MVP usability readiness](2026-10-19-mvp-usability-readiness.md) (#1915)
- [ADR 0018: the end-user app](../decisions/0018-end-user-web-app.md)
- [Scheduling runbook](../runbooks/scheduling.md)
- [Strategy-lab spec](../specs/strategy-lab.md); [research-registry spec](../specs/research-registry.md)
- Issues #1480, #1759, #1848, #1876, #1921, #1923
