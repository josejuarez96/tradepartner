# Handoff: end-user app design spike

**Date:** 2026-10-10 (end of the fourth session) · **Branch:** `spike/design-ui` (pushed; no PR, the owner hasn't asked for one) · **Owner:** Jose

## Read in this order
1. **[interaction.md](interaction.md)**: the four routines the app is built around, the strict / teach / intuitive principle (§6), the owner's decisions of 2026-10-10 (§8), the glossary (§9), the retired names (§10).
2. This file: the owner's review rules, what's built, what's wrong in it, what's next.
3. [workflow.md](workflow.md) (a strategy's life, the five stages, the Run button's rules) and [engine-first.md](engine-first.md) (what the engine records).
4. [DESIGN.md](DESIGN.md) (tokens, type, the calmed workstation layout, the Today proposal) and [ai-tells.md](ai-tells.md). Older history: [README.md](README.md).

## The brief, as it stands now
An app for the owner as a **user**, built around his four routines: the morning check (phone), reading a trial result (desk), deciding (desk; the kill switch also on the phone), reviewing ideas and drafts (desk, weekly). It shows only what the engine computes and uses the engine's terms, taught in place. Prototype in React + TypeScript + Vite under `web/`, on sample data in `web/sample/`; no backend. Don't touch `src/`, `tests/` or other `docs/`. Don't read `data/*.duckdb` or `.env`.

## The owner's decisions
| Decision | Where it lives |
|---|---|
| A real web app ("option B"), React + TS + Vite, FastAPI read API, kill and resume as the only writes | ADR 0018 (merged on main) |
| Every screen built and tested for mobile; phone access itself not decided | comments on #1411 |
| The lab moves into the app: reading now, running after the ADR 0018 amendment | interaction.md §8 q4 |
| Registration happens in the app, in the same amendment as Run | interaction.md §8 q1 |
| The Run confirm shows N and SR* moving; needs a size-S engine function | interaction.md §8 q2 |
| Agent drafts are reviewed as PRs; the app links to them and shows their state | interaction.md §8 q3 |
| Real quant terms, taught at the point of use, never renamed | interaction.md §9, §10 |
| Show only what the engine does; process first; one chart per decision | rules 5 to 7 below |

## Owner's rules from review (don't break these)
1. **Say it once.** No code badge beside a name, no subtitle rewording its heading, no icon beside the word it illustrates. Internal codes (B10, H1) appear once, as a reference at the bottom of a detail view.
2. **No colour bars to decode.** Graphics explain themselves: tiles marked ✓ / – / ✕, a zoned scale with a marker, countable steps, labels on the track itself.
3. **Not a wall of text.** Rows are one line; detail opens on tap.
4. **Avoid the AI look.** No serif or italic accent word, no cream and terracotta, no untouched shadcn theme, no Inter or Geist alone, no ALL-CAPS labels, no `A · B · C` strings, no badge on every row, no icon in a tinted square, no shadows, gradients or glass.
5. **Explain numbers a researcher would question** with an ⓘ popover, using the repo's real method (`backtest/metrics.py`), in the real terms (DSR, SR*, N, holdout).
6. **Engine first.** Every number on screen is something the engine computes or stores; name its source (table or function) in the notes. If the engine doesn't do it, don't show it; if it needs an engine change, say so on screen.
7. **Process first.** Screens follow the owner's routines and the strategy's stage, not a catalogue of charts. One chart per decision; everything else on demand.
8. **Self-critique against these with screenshots** (desktop, phone, light) before showing him.

## What's built (fourth session), and its state
Screenshots: [`screens/routines/`](screens/routines/).

| Screen | Route | What it does |
|---|---|---|
| **Today** | `#today` (default) | Phone first. Healthy: one sentence ("Nothing wrong. Ingest finished Fri 6:42 pm. All 3 books ran Fri morning and reconciled. No alerts.") and a folded "Each job" list. Not healthy (`?state=alert`): a panel listing only the jobs not ok, with what holds. Waiting on you: finished trial, decision due, agent draft PR (number, draft state, checks). Books against their own backtests: tracking gap on a scale whose shaded zone is the tolerance; the forward holdout as countable progress. Only write: the kill switch (book or every book, required reason, one sentence, never delayed). `?state=calm` empties the waiting list. |
| **Strategies** | `#strategies` | One list by the five stages with a count track on top; registration drawn as the dashed step between Idea and Testing; per row: what it waits on (who, what), N, SR*, DSR, excess vs SPY, holdout state (spent, spent by family, unspent, forward). Parked and Retired in a side column with their reasons and what unparks them. |
| **A strategy's page** | `#strategies/<id>[/<tab>]` | The backtester: frozen specification on the left (registered date, `params_sha256`, file, frozen keys, windows with the development boundary, thresholds, kill criterion, trial budget, "nothing here edits"), folded to one line on the phone. Stage stepper and the family's N, SR*, holdout spends under the title. Tabs only for stages reached: Evidence (prior, claims with grades), Runs (every trial including refusals word for word, N at the run), Result, Holdout, Paper, Decisions (`owner_decisions` with reasons). |
| Result tab | `#strategies/momentum-topfrac-cadence/result`, `#strategies/h1-momentum-12-1/result` | Leads with the pre-registered thresholds, then pass / fail / reported rows with values (retirement line, promotion floor, DSR with N and SR*, expected range, survivorship gap, red flag), cost sensitivity as five ✓/✕ tiles, then "what the pre-registered rule decides" with Agree / Disagree with a reason, then one chart (the sweep's variants against `promote_at_least`; for H1, excess over SPY across the in-sample window), then "How it was computed" (identity, the sweep report table, the workstation view). |
| Run confirm | `#strategies/h1-momentum-12-1/run` | "Becomes trial 9 in the momentum family. SR* rises from 0.58 to 0.61" (sample, needs the engine function), window inputs capped at the development boundary, quiet-interval line, then the engine's answer word for word (`run: in-sample run`, or a refusal with `?engine=refused_window` / `refused_variant`). |
| Register | `#strategies/b10-short-term-momentum/register` | Previews exactly what freezes (frozen keys, `in_sample_start`, thresholds), the holdout and its kind (the family already spent it), kill criterion, budget, "N stays 8 until the first trial"; states the two steps the engine needs first (the `strategy.turnover_top_fraction` key, the one-value sweep file); then the engine's answer. |
| Holdout spend, gap override | `#strategies/b3-gross-profitability/spend-holdout`, `/override-gap` | Separate screens, each with a required reason and "once" stated; the spend shows the family cap (0 of 3, `max_family_holdout_spends`) and needs the family name typed; the override lists the breaching rebalances and applies to the one spend that follows. |
| **Books** | `#books/<id>` | Kept. "Stop book" is now the **kill switch** everywhere (engage, NOT-engaged failure text per ADR 0018, resume needs reconciliation ok); new **Kill switch history** section (`kill_switch` rows with reasons and reconciliation); the rail says plainly that `paper stop` closes the window and sells, unlike the kill switch. |
| Workstation view | `#strategies/h1-momentum-12-1/bench` | Kept as "how it was computed", terms renamed (DSR, N at the run, holdout). |

**All lab writes are prototype only** and every one says so with the command it would run (`PrototypeWrite`): they wait on the ADR 0018 amendment drafted in parallel, with a `safety-reviewer` pass. The kill switch is ADR 0018's own write; in the prototype it sends nothing.

**Renames (interaction.md §10) done:** `LuckInfo` → `components/Term.tsx` (`TermInfo` glossary ⓘ, `DsrMaths`); `LuckScale`, `ExamSteps` removed (pass marks, `TrackScale`, `Steps` in `components/Marks.tsx`); `types.ts` fields `luck`/`tries` → `dsr`/`n_trials`, the old `Idea`/`Family`/`WaitingItem` types gone; workflow.md, engine-first.md and ai-tells.md renamed in place; README.md and base44-prompt.md are historical records and carry a dated note instead. `grep -ri "luck\|exam" web/src` is clean.

**Removed:** the Overview, Research and simple Trial screens (replaced), `lib/stats.ts` (browser-side statistics), the sample screens A to E (their screenshots stay in `screens/samples/`), `ReplayChart`, `shoot-trial.mjs`.

### Where each number comes from (source map, start)
| On screen | Engine source |
|---|---|
| Ingest, paper runs, reconciliation, alerts (Today) | the last ingest run (dashboard header read); `paper_run_results.status`; `reconciliations.status`; `alerts` via the alerts-since reader ADR 0018 lists |
| Tracking gap and tolerance (Today, Paper tab) | `paper check`'s tracking line and `paper report`; per-period values need `paper_report_periods` (ADR 0018, not built) |
| Forward holdout progress | `paper.min_rebalances`, the window's rebalance count |
| Waiting on you | ADR 0018's "waiting on me" reader (to build) plus GitHub PR state for agent drafts |
| N, SR*, DSR, `dsr_excess`, excess | `trial_results` (`n_trials`, `sr_star`, `dsr`, `dsr_excess`), `trial_metrics` (`excess_cagr_spy`); a sweep's via `sweep report` |
| SR* at N+1 on the Run confirm | **needs the engine function** (`expected_max_sharpe(N+1, V)`), owner decision q2 |
| Thresholds, frozen keys, `params_sha256`, `registered_at` | `sweeps` row (`promote_at_least`, `retire_below`, `expected_range_*`), `hypotheses`, `frozen.frozen_values` |
| Cost sensitivity | `trial_metrics` per `cost_per_side_bps` |
| Survivorship gap | `trial_rebalances.gap_count_share`, `gap.count_share_threshold` |
| Refusals | `holdout.decide`'s outcome and message, logged in `trials` |
| Holdout spends | `owner_decisions.holdout_spend`, `max_family_holdout_spends` |
| Kill switch history | `kill_switch` |
| Book chart band | **browser-side, prototype only** (`expected_tracking_error`, sample); the real one is `tracking_error_spy` served by the API |

**Real values in the sample:** the frozen keys, slugs, windows and `[lab]` thresholds from `docs/hypotheses/` and `docs/sweeps/`; H1 trial 2's DSR 0.7262 (psr basis, N 2) and 1.25% cost drag; the momentum family's spent holdout; main's start of $100,008.90; the backlog's names, blockers and parked reasons. Everything else is sample and `web/sample/lab.json`'s note says so.

## Known gaps and self-critique left open
- Phone strategy page: the tabs wrap to two lines when a strategy has six (H1).
- The H1 excess chart is drawn from the sample replay's daily values, so it is noisy; the real one would come from `trial_equity`.
- The `?engine=refused_window` preview uses a canned message whose dates don't match the inputs.
- "Agree / Disagree" on a single trial's verdict has no engine command (said on screen); only the sweep's retire exists.
- The Book screen's band and range statistics still use the old browser-side placeholder (`expected_tracking_error`).
- B10's Register screen is shown as if its two engine prerequisites had landed (said on screen).
- Desktop Today is calm to the point of empty below the fold; that is the proposal, flagged in DESIGN.md.

## Next, in order
1. The ADR 0018 amendment (another session): lab reading, Run and Register as subprocess writes, with `spec-critic` and `safety-reviewer`. The screens here are its picture.
2. Engine tasks for the owner to schedule: `expected_max_sharpe` at N+1 for the Run confirm (size S); persist backtest scores and ranks per rebalance for the step-through; `paper_report_periods`; the waiting-on-me reader.
3. The webapp spec: map each screen here to its API resources (the source map above is the start).
4. Owner review of Today's look (DESIGN.md proposal) and the open questions below.

## Code map (`web/`)
| Path | What |
|---|---|
| `src/index.css` | All tokens |
| `src/App.tsx` | Hash routes: `#today`, `#strategies[/<id>[/<tab or action>]]`, `#books/<id>` |
| `src/components/Shell.tsx` | Top bar, phone tab bar, `Page`, `Section`, `Panel`, `PrototypeWrite` |
| `src/components/Term.tsx` | The glossary ⓘ (`TermInfo`) and the DSR formula |
| `src/components/Marks.tsx` | `PassMark`, `TrackScale`, `Steps`, `Spark`, `Pill` |
| `src/screens/Today.tsx`, `Strategies.tsx`, `Strategy.tsx`, `StrategyActions.tsx` | The routines; `StrategyActions` holds Run, Register, holdout spend, gap override |
| `src/screens/Book.tsx`, `TrialBench.tsx`, `Trial.tsx` | Books; the workstation view and its shared figures |
| `src/lib/lab.ts` + `sample/lab.json` | The lab, health, tracking and kill-switch sample, shaped like the API would serve it |
| `src/lib/data.ts`, `types.ts` + `sample/generate.mjs` → `sample/app-data.json` | Books, benchmark and H1's replay |
| `shoot.mjs`, `shoot-lab.mjs`, `shoot-book.mjs`, `shoot-bench.mjs`, `shoot-browser.mjs` | Playwright screenshots into `shots/` (gitignored) |

## Commands
```bash
cd web && npm install
npx vite --port 5173 --host 127.0.0.1     # dev server, in the background
npx tsc --noEmit && npm run build         # must stay clean
node sample/generate.mjs                  # after editing the books sample
npx playwright-core install chromium      # once, on a Mac (a cloud session uses /opt/pw-browsers)
node shoot.mjs today= strategies=#strategies result=#strategies/momentum-topfrac-cadence/result light=theme=light
node shoot-lab.mjs; node shoot-book.mjs; node shoot-bench.mjs [theme=light]
```
States: `?state=loading|error|empty|alert|calm|stopped|safety|stopfail`, `?theme=light`, `?engine=refused_window|refused_variant`.

## Open questions for the owner (one decision each)
1. **Today's look:** keep the calm workstation proposal (sentence when healthy, panels only for waiting and books), or go back to E's big number and chart?
2. **Dark by default:** keep it, or follow the phone's setting?
3. **Agreeing with a single trial's verdict:** should it be recorded (a new `owner_decisions` kind, an engine change), or is silence agreement?
4. **Resume from the phone:** the kill switch works from Today; should resume stay desk only (as now)?
5. **B4:** STATUS says "run now", ADR 0016 says parked; the app shows parked. Which is right?
