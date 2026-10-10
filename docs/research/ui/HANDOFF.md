# Handoff: end-user app design spike

**Date:** 2026-10-10 · **Branch:** `spike/design-ui` (pushed; no PR, the owner hasn't asked for one) · **Owner:** Jose

Read this first, then [engine-first.md](engine-first.md) (the step back: what the engine does, how other platforms show theirs, the proposed structure and the owner's open decisions), [DESIGN.md](DESIGN.md) (the design contract) and [ai-tells.md](ai-tells.md) (what to avoid). Older history is in [README.md](README.md).

## The brief, in one paragraph
Design the end-user app for TradePartner for the owner as a *user*, not a developer, at his desk and on his phone. It answers: how am I doing (overall, per book, vs SPY); what each book holds and does, and why; is anything wrong or waiting on me (quiet when nothing is); stop/resume a book safely (reason required, clear confirm); and research (what am I testing, did it work, could it be luck, what's waiting on me). Plain language, every element earns its place, phone first-class, WCAG AA, finished states (hover/focus/loading/empty/error). Prototype as React + TypeScript + Vite under `web/`, fed by `web/sample/app-data.json`; no backend. Notes under `docs/research/ui/`. Don't touch `src/`, `tests/` or other `docs/`. Don't read `data/*.duckdb` or `.env`.

## Where it stands
- **Direction chosen: E, "research-grade Robinhood"** (Robinhood's frame: one big number, one big clean chart, a side rail of short rows, lots of air; plus a research layer: the backtest's expected range as a band, event dots, a statistics grid with a method note, a luck check with an explainer). Dark by default, light via the header switch. IBM Plex Sans for words, IBM Plex Mono for numbers. Colour only for gain, loss, attention.
- **Built in E:** Overview (`#overview`), Research (`#research`, with a results map: return vs S&P against luck check), the idea detail sheet, the luck-check info popover, loading/error/empty states (`?state=loading|error|empty|alert|stopped`, `?theme=light`).
- **Built in E (2026-10-10, second session): Book detail** (`#books/<id>`; `#books` opens the book with the longest record). Value and change since start; chart against the S&P with the book's own expected range (ⓘ explains the band) and dots on days orders filled; holdings (rank today, value, weight, gain; target said once in the heading; weight turns attention past the drift tolerance); "Why it holds these" (the rule in one sentence, then a rank scale with its zones named on it: buys / keeps / sells, filled dots for holdings, hollow for recent sells); recent orders grouped by day with the fill cost against the backtest's assumption; tap a holding or order for a sheet with the reason in the rule's terms. Rail: books, schedule, what the backtest said; phone swaps the rail's book list for a pill switcher at the top. **Stop/resume:** dialog lists exactly what happens (next run skipped, holdings and cash kept, open orders cancelled), reason required (attention message if empty), pending and failure states; stopped books show a boxed notice with who, when and why, and a Resume dialog (also needs a reason; safety stops say the rule still applies). States: `?state=loading|error|empty|stopped|safety|stopfail`, `#books/main` (no history: chart, holdings and orders empty states), `#books/zzz` (not found). Screenshots in `screens/book/`; interactions via `node shoot-book.mjs`.
- **Built in E (2026-10-10, third session): the trial page with the step-through** (`#research/trial/h1`; opened from an idea's sheet in Research and from a book's "What the backtest said"). The result in one line, then the honesty strip (luck check, versions tried, the exam on paper), the run's chart with a playhead (play, step, scrub; the axes stay fixed while it draws), **what the engine did on each rebalance** as a pipeline (universe, excluded, ranked, held, bought and sold, traded back to equal weight, costs), the signal distribution with the cut drawn on it, the names at the cut and in and out (rail on desktop, inline on phone), one bar per year, the same run at every cost level, where the test sits (development, exam window, paper, labelled on the track) and the assumptions. See [engine-first.md](engine-first.md) for why.
- **Workstation view of the same trial** (`#research/bench/h1`, linked from the trial page as "Workstation view"), built after the owner pointed at Build Alpha, RealTest and AmiBroker as the "analog research feel" he means: tiled panels with title bars and live readouts, gridded equity with a locked drawdown pane, a rebalance inspector (the stages as a table, the names at the cut), the trade tape, a Monte Carlo of the monthly results drawn as percentile bands (not spaghetti), a month-by-year grid with the number in every cell, by-year and cost bars, properties. Deliberately left out: 3D optimisation surfaces, rainbow heatmaps, floating windows. **Then "with a modern twist" (owner, 2026-10-10):** the transport is pinned and the playhead is a timeline scrubber (a miniature of the run with a tick per rebalance; drag, click, arrow keys, Home/End, Space to play), panels are soft surfaces with gaps instead of ruled boxes, gridlines are dotted, any panel widens to full width, and the month grid and the equity chart point at each other on hover. **Open for the owner: workstation for the lab (and maybe books), simple E for Overview and phone, or one of them everywhere?**
- **Sample data now follows the engine's real rule** (the hypothesis files): the 1000 largest US stocks, top 10% held at equal weight (about 97 to 98 names), every rebalance trades back to equal weight, 15 bp a side, fill at the close, no sell buffer. The Book screen's "Why it holds these" was rewritten to match (an earlier version invented a keep-until-rank-20 buffer and 10% positions, which the risk rules' 5% cap forbids). The replay is invented data in H1's real window, calibrated to H1's recorded result; backtest ranks are **not persisted by the engine today**, so this view needs that engine change to be real (engine-first.md §6).
- **Not built:** a stop/resume history list per book (only the current stop is shown); open/partly filled orders aren't in the sample.
- **Reference samples** (same data, design only): `#sample-a` … `#sample-e`; screenshots in `screens/samples/`.

## Owner's rules from review (don't break these)
1. **Say it once.** No code badge beside a name, no "next step" restating the title, no icon beside the word it illustrates, no subtitle rewording its heading. Internal codes (B10, H1) appear once, as a reference at the bottom of a detail view.
2. **No colour bars to decode.** Graphics must explain themselves: evidence = one tile per study marked ✓ / – / ✕; luck = zoned scale with a marker; exams = countable steps (≤13) or a labelled track.
3. **Not a wall of text.** Each screen leads with a picture; rows are one line; detail opens on tap. (Direction C was rejected for this.)
4. **Avoid the AI look** (he rejected four directions for it): no serif/italic accent word in a headline, no cream + terracotta, no untouched shadcn theme, no Inter/Geist as the only face, no ALL-CAPS labels, no `A · B · C` strings, no badge on every row, no icon-in-tinted-square. Run the scanner after changes (below).
5. **Explain numbers a researcher would question** with an ⓘ popover (see `LuckInfo.tsx`); use the repo's real method (`src/tradepartner/backtest/metrics.py`).

## Code map (`web/`)
| Path | What |
|---|---|
| `src/index.css` | All tokens (shadcn variable names, our values) + gain/loss/attention/bench/raised |
| `src/components/Shell.tsx` | Top bar, phone bottom tabs, `Page` (main + 340px rail), `Section`, `RailHead` |
| `src/components/ReturnChart.tsx` | Lightweight Charts hero: your line, dashed S&P, expected-range band, event dots, scrub callback |
| `src/components/Visuals.tsx` | `EvidenceTally`, `LuckScale`, `ExamSteps`, `Slots` |
| `src/components/LuckInfo.tsx` | ⓘ popover explaining the deflated Sharpe, with an idea's inputs |
| `src/screens/Overview.tsx` | Overview (exports `Spark`, `Pill` for reuse) |
| `src/screens/Research.tsx` | Research: `ResultsMap` (collision-checked labels), needs-you, ideas, rail, `IdeaSheet` |
| `src/screens/OverviewStates.tsx` | Loading / error / empty |
| `src/screens/Book.tsx` | Book detail: holdings, rank scale, orders, rail, stop/resume dialog, detail sheet, loading and not-found |
| `src/screens/Trial.tsx`, `src/components/ReplayChart.tsx` | The trial page and its replay chart; `node shoot-trial.mjs` screenshots it |
| `src/screens/TrialBench.tsx`, `src/components/BenchChart.tsx` | The workstation view and its gridded equity/drawdown chart; `node shoot-bench.mjs` |
| `src/components/RangePicker.tsx` | 1W/1M/3M/All pills shared by Overview and Book |
| `src/lib/` | `data.ts` (comparison, ranges, TWR change), `stats.ts`, `format.ts`, `tokens.ts` (reads CSS vars for canvas), `types.ts`, `scenarios.ts` |
| `src/components/ui/` | shadcn new-york-v4 sources copied from GitHub (the CLI's registry host is blocked here) |
| `sample/generate.mjs` → `sample/app-data.json` | Deterministic sample data. **Real:** main's start ($100,008.90, 2026-10-09), H1 luck 0.7262 (psr basis, 40 monthly returns), backlog names/stages/blockers, pending decisions. **Invented:** everything else, marked "sample" in data and UI. |
| `src/samples/` | The five comparison samples |
| `shoot.mjs`, `shots/shot2.mjs`, `shots/shot-luck.mjs` | Playwright screenshots (`node shoot.mjs name=query#hash …`; needs the dev server) |

## Commands
```bash
cd web && npm install
npx vite --port 5173                      # dev server (run in background)
npx tsc --noEmit && npm run build         # must stay clean
node shoot.mjs overview= research=#research research-sheet=#research light=theme=light
node sample/generate.mjs                  # after editing sample data
```
AI-tells scanner: `https://github.com/funboy322/avoid-ai-design` (`scripts/detect.mjs`, read-only, no deps). Fetch it into a scratch dir and run `node detect.mjs web/src --min=P1`. Last run: one P1, a false positive (it compares dark-theme muted ink against the light ground). Not re-run after Book detail: the auto-mode permission check blocked running fetched code in the second session; Book.tsx was grepped by hand for caps, `·` strings, shadows, arrows and fonts (none).

## Environment notes
- `team.py start/claim` fails in cloud sessions (GitHub GraphQL is blocked); spike branches don't need a claim.
- Chromium for screenshots: `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (via `playwright-core`).
- Commit with the Conventional Commit style used on the branch (`spike(ui): …`) and push to `spike/design-ui`.

## Open questions for the owner
- Research: read-only, or allow actions (register a run, record a decision, write a lesson) with reason + confirm?
- Dark by default ignores the OS setting; keep, or follow the OS?
- The expected-range width is a placeholder per book (main 6%, daily 8%, b3 5% a year); the real value should come from each book's backtest via the API.
- Book detail: should "Stop book" also be offered on the Overview rail, or only here? Should resume let him choose "trade back to the rule now" vs "wait for the next run"? (Built: wait for the next run.)
- Holdings, ranks, signals and orders are invented sample data; the API needs to serve rank and signal per holding and the decision price per order for "why" and fill cost to be real.
- B4 shows as both "run now" (STATUS) and "parked" (ADR 0016); surfaced as a decision, not resolved.

## Suggested opening prompt for the next agent
> Continue the TradePartner end-user app design on branch `spike/design-ui`. Read `docs/research/ui/HANDOFF.md`, `DESIGN.md` and `ai-tells.md` first. Build the Book detail screen in direction E (value + chart with expected range, holdings vs target weights, recent orders, why it holds what it holds, stop/resume with reason and confirm), desktop and phone, with loading/empty/error states. Follow the owner's rules in the handoff. Screenshot and self-critique before showing me.
