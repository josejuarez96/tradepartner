# Handoff: end-user app design spike

**Date:** 2026-10-10 (end of the third session) · **Branch:** `spike/design-ui` (pushed; no PR, the owner hasn't asked for one) · **Owner:** Jose

## Read in this order
1. **[workflow.md](workflow.md)** leads the design now: a strategy's life, the owner's routine, the five stages, the Run button's rules, and the app structure that follows.
2. **[engine-first.md](engine-first.md)**: what the engine actually computes and records (with table names), how QuantConnect, Quantopian, Composer, TradingView and MLflow show theirs, and what would need an engine change.
3. This file: decisions, what's built, what's wrong in it, what's next.
4. [DESIGN.md](DESIGN.md) (tokens and type, still valid; its "direction E, airy Robinhood" layout is **out of date**, see below) and [ai-tells.md](ai-tells.md) (what to avoid). Older history: [README.md](README.md).

## The brief, as it stands now
An app for the owner as a **user**: phone first for the morning check, the desk for research. It follows the process the repo already enforces: an idea becomes a registered strategy, is tested (every run counted), judged, examined, paper-traded, and maybe traded live. It shows **only what the engine computes**, and makes the engine legible ("a lab meets a trading engine"). Prototype in React + TypeScript + Vite under `web/`, on `web/sample/app-data.json`; no backend. Notes under `docs/research/ui/`. Don't touch `src/`, `tests/` or other `docs/`. Don't read `data/*.duckdb` or `.env`.

## The owner's decisions (2026-10-09 and 10)
| Decision | Where it lives |
|---|---|
| A real web app, not Streamlit ("option B") | ADR 0018, [PR #1411](https://github.com/josejuarez96/tradepartner/pull/1411), owned by another session, reviewers PASS, waiting on his merge |
| Phone access (Tailscale or otherwise) is **not decided**; version 1 runs on the Mac only, but **every screen is still built and tested for mobile** | Relayed as two comments on #1411 |
| ADR 0018's open question 7 keeps backtests and research on Streamlit; this design puts the lab in the app. **Unanswered** | engine-first.md §7 |
| "More analog research feel" = a research workstation (Build Alpha, RealTest, AmiBroker) **with a modern twist** | Built as `#research/bench/h1` |
| **Show only what the engine does.** No Monte Carlo (the engine has none), no graphs added for their own sake | Monte Carlo removed; fields renamed to the engine's |
| **Follow the process.** Phone first; five stages (Idea, Testing, Exam, Paper, Live; Parked and Retired off to the side) | workflow.md §6 |
| **Agents draft strategies** (hypothesis files); he reviews and registers | workflow.md §6, item 3; needs its own decision |
| **A Run button that follows the engine's rules**: registered strategies only, the engine decides and the app shows its answer, the cost in tries shown before confirming, exam and gap override as separate actions, quiet intervals respected | workflow.md §6, item 4. **Not yet relayed to #1411**: it needs an amendment to ADR 0018's writes; ask the owner before commenting |

## Owner's rules from review (don't break these)
1. **Say it once.** No code badge beside a name, no subtitle rewording its heading, no icon beside the word it illustrates. Internal codes (B10, H1) appear once, as a reference at the bottom of a detail view.
2. **No colour bars to decode.** Graphics explain themselves: tiles marked ✓ / – / ✕, a zoned scale with a marker, countable steps, labels on the track itself.
3. **Not a wall of text.** Rows are one line; detail opens on tap.
4. **Avoid the AI look.** No serif or italic accent word, no cream and terracotta, no untouched shadcn theme, no Inter or Geist alone, no ALL-CAPS labels, no `A · B · C` strings, no badge on every row, no icon in a tinted square, no shadows, gradients or glass.
5. **Explain numbers a researcher would question** with an ⓘ popover, using the repo's real method (`backtest/metrics.py`).
6. **Engine first.** Every number on screen is something the engine computes or stores; name its source (table or function) in the notes. If the engine doesn't do it, don't show it; if it needs an engine change, say so on screen.
7. **Process first.** Screens follow the owner's workflow and the strategy's stage, not a catalogue of charts. One chart per decision; everything else on demand.
8. **Self-critique against these with screenshots** (desktop, phone, light) before showing him.

## What's built, and its state
| Screen | Route | State |
|---|---|---|
| Overview | `#overview` | Becomes **Today**. Its statistics grid is computed in the browser (`lib/stats.ts`), which the real app must not do (code computes numbers in Python, served by the API). Add the machine's health (last ingest, last paper run per book, reconciliation, alerts). |
| Research | `#research` | Becomes **Strategies**: the five-stage list. The results map becomes a view inside a family. |
| Book detail | `#books/<id>` | Mostly keeps. Fixed this session to the real rule: top 10% of 1,000 at equal weight, ranks are the **last rebalance's** (the journal's `signals` rows exist only on rebalance days), no fill-cost claim (no decision price is journalled). "Stop book" is the kill switch (`paper kill`); keep it apart from `paper stop`, which closes the window and sells. |
| Trial page | `#research/trial/h1` | Becomes the **Result** tab of a strategy's page. Pipeline labels follow `trial_rebalances`. |
| Workstation view | `#research/bench/h1` | The look the owner chose for the lab: tiled soft panels, pinned transport with a timeline scrubber (drag, click, arrows, Home/End, Space), linked hover between the month grid and the equity chart, widen any panel. Panels: equity with a drawdown pane, this rebalance (from `trial_rebalances`), scores that day (**not stored by the engine**, labelled so), turnover and costs, month by month, trades, by year, cost levels, properties. Per rule 7, most of these should move behind "details". |
| Samples A to E | `#sample-a` … | Reference only. |

**Sample data** (`web/sample/generate.mjs`) follows the hypothesis files: the 1,000 largest US stocks after the universe rules, the top 10% held at equal weight (97 to 98 names), every rebalance trades back to equal weight, 15 bp a side, filled at the close. The replay uses H1's real window, calibrated to its recorded result. **Real values in it:** main's start ($100,008.90, 2026-10-09), H1's luck check 0.7262, the backlog's names, stages and blockers. Everything else is invented and marked "sample" in the data and the UI.

## Next, in order
1. **Ask the owner** whether to relay the Run-button decision to #1411 (an amendment to ADR 0018's writes, with a `safety-reviewer` pass).
2. **Rebuild the prototype around the workflow** (workflow.md §3 and §6):
   - **Today**, at phone width first: machine health, waiting on you, each book against its expected range. Quiet when nothing is wrong.
   - **Strategies**: one list by the five stages, each row showing its stage, what it waits on, and its latest numbers.
   - **A strategy's page**, laid out like a desktop backtester: the frozen specification on the left (read-only), the Run button with its cost in tries and the engine's refusals, and tabs for the stages it has reached (Evidence, Runs, Result, Exam, Paper, Decisions). The workstation becomes the Result tab, trimmed to one chart per decision.
   - Keep Books; add the stop/resume history.
3. **Update DESIGN.md** to the chosen look: the workstation with a modern twist for the lab and books; decide with the owner whether Today keeps the calmer E style.
4. **A source map** for every number on screen (rule 6), starting from engine-first.md §6 and workflow.md §4.
5. Outside the spike, for the owner to schedule: an engine task to **persist backtest scores and ranks** per rebalance (they exist only on the in-memory `Plan`), so the step-through can be real.

## Code map (`web/`)
| Path | What |
|---|---|
| `src/index.css` | All tokens (shadcn variable names, our values) plus gain, loss, attention, bench, raised |
| `src/components/Shell.tsx` | Top bar, phone bottom tabs, `Page` (main plus a 340px rail), `Section`, `RailHead` |
| `src/components/ReturnChart.tsx` | Overview and Book hero chart: your line, dashed S&P, expected-range band, event dots |
| `src/components/ReplayChart.tsx`, `BenchChart.tsx` | Replay charts: drawn to a playhead with fixed axes; `BenchChart` adds gridlines, a drawdown pane and a crosshair other panels can point |
| `src/components/Scrubber.tsx` | The timeline scrubber (run miniature, a tick per rebalance, keyboard slider) |
| `src/components/RangePicker.tsx`, `Visuals.tsx`, `LuckInfo.tsx` | Range pills; `EvidenceTally`, `LuckScale`, `ExamSteps`; the luck-check ⓘ popover |
| `src/screens/` | `Overview`, `OverviewStates`, `Research` (with `IdeaSheet`), `Book`, `Trial`, `TrialBench` |
| `src/lib/` | `data.ts`, `stats.ts` (browser-side statistics, prototype only), `format.ts`, `tokens.ts`, `types.ts` (the sample's shape; replay fields follow `trial_rebalances`), `scenarios.ts` (`?state=` previews) |
| `src/components/ui/` | shadcn new-york-v4 sources copied from GitHub (the CLI's registry host is blocked here) |
| `sample/generate.mjs` → `sample/app-data.json` | Deterministic sample data (about 2 MB) |
| `shoot.mjs`, `shoot-book.mjs`, `shoot-trial.mjs`, `shoot-bench.mjs` | Playwright screenshots into `shots/` (gitignored); keepers are copied to `docs/research/ui/screens/` |

## Commands
```bash
cd web && npm install
npx vite --port 5173 --host 127.0.0.1     # dev server, in the background
npx tsc --noEmit && npm run build         # must stay clean
node sample/generate.mjs                  # after editing the sample
node shoot.mjs overview= book=#books/daily light=theme=light   # name=query#hash, desktop and phone
node shoot-book.mjs; node shoot-trial.mjs; node shoot-bench.mjs [theme=light]
```
States: `?state=loading|error|empty|alert|stopped|safety|stopfail`, `?theme=light`.

## Environment notes
- `team.py start/claim` fails in cloud sessions (GitHub GraphQL is blocked); spike branches need no claim.
- The AI-tells scanner (`github.com/funboy322/avoid-ai-design`, `scripts/detect.mjs`) was **blocked by the auto-mode permission check** as fetched code; this session grepped the new files by hand for caps, `·` strings, shadows, arrows and fonts. The owner can allow it in his Claude Code settings.
- Chromium for screenshots: `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (via `playwright-core`).
- Vendor sites (QuantConnect, Composer) are blocked by the proxy; platform details in engine-first.md marked *(unverified)* came from search summaries.
- Commit as `spike(ui): …` or `docs(ui): …` and push to `spike/design-ui`.

## Open questions for the owner
- ADR 0018 question 7: does the lab (backtests, sweeps, research) move into the app? This design assumes yes.
- Relay the Run-button decision to #1411 now?
- Today: the workstation look too, or the calmer E style for the phone check?
- How the app starts a drafting agent, and where its draft is reviewed (a PR, or the app).
- Dark by default ignores the OS setting; keep, or follow the OS?
- B4 shows as both "run now" (STATUS) and "parked" (ADR 0016).

## Suggested opening prompt for the next agent
> Continue the TradePartner end-user app design on branch `spike/design-ui`. Read `docs/research/ui/workflow.md`, `engine-first.md` and `HANDOFF.md` first, then `DESIGN.md` and `ai-tells.md`. Rebuild the prototype around the owner's workflow: Today (phone first: machine health, waiting on you, books against their expected range), Strategies (the five stages), and a strategy's page laid out like a desktop backtester (frozen specification on the left, a Run button that follows the engine's rules, tabs for Runs, Result, Exam and Paper, with the workstation view as the Result tab, trimmed to one chart per decision). Show only what the engine computes. Follow the owner's rules in the handoff. Screenshot desktop, phone and light, and self-critique before showing me.
