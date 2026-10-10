# Handoff: end-user app design spike

**Date:** 2026-10-10 · **Branch:** `spike/design-ui` (pushed; no PR, the owner hasn't asked for one) · **Owner:** Jose

Read this first, then [DESIGN.md](DESIGN.md) (the design contract) and [ai-tells.md](ai-tells.md) (what to avoid). Older history is in [README.md](README.md).

## The brief, in one paragraph
Design the end-user app for TradePartner for the owner as a *user*, not a developer, at his desk and on his phone. It answers: how am I doing (overall, per book, vs SPY); what each book holds and does, and why; is anything wrong or waiting on me (quiet when nothing is); stop/resume a book safely (reason required, clear confirm); and research (what am I testing, did it work, could it be luck, what's waiting on me). Plain language, every element earns its place, phone first-class, WCAG AA, finished states (hover/focus/loading/empty/error). Prototype as React + TypeScript + Vite under `web/`, fed by `web/sample/app-data.json`; no backend. Notes under `docs/research/ui/`. Don't touch `src/`, `tests/` or other `docs/`. Don't read `data/*.duckdb` or `.env`.

## Where it stands
- **Direction chosen: E, "research-grade Robinhood"** (Robinhood's frame: one big number, one big clean chart, a side rail of short rows, lots of air; plus a research layer: the backtest's expected range as a band, event dots, a statistics grid with a method note, a luck check with an explainer). Dark by default, light via the header switch. IBM Plex Sans for words, IBM Plex Mono for numbers. Colour only for gain, loss, attention.
- **Built in E:** Overview (`#overview`), Research (`#research`, with a results map: return vs S&P against luck check), the idea detail sheet, the luck-check info popover, loading/error/empty states (`?state=loading|error|empty|alert|stopped`, `?theme=light`).
- **Not built:** Book detail (`#books`, currently a placeholder): one book's value and chart with its expected range, holdings vs target weights, recent orders and fills, why it holds what it holds, and **stop/resume** (typed reason + confirm dialog stating exactly what happens; resume also needs a reason; show who stopped it and when). This is the agreed next screen.
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
AI-tells scanner: `https://github.com/funboy322/avoid-ai-design` (`scripts/detect.mjs`, read-only, no deps). Fetch it into a scratch dir and run `node detect.mjs web/src --min=P1`. Last run: one P1, a false positive (it compares dark-theme muted ink against the light ground).

## Environment notes
- `team.py start/claim` fails in cloud sessions (GitHub GraphQL is blocked); spike branches don't need a claim.
- Chromium for screenshots: `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` (via `playwright-core`).
- Commit with the Conventional Commit style used on the branch (`spike(ui): …`) and push to `spike/design-ui`.

## Open questions for the owner
- Research: read-only, or allow actions (register a run, record a decision, write a lesson) with reason + confirm?
- Dark by default ignores the OS setting; keep, or follow the OS?
- The expected-range width (6%/yr tracking error) is a placeholder; the real value should come from each book's backtest via the API.
- B4 shows as both "run now" (STATUS) and "parked" (ADR 0016); surfaced as a decision, not resolved.

## Suggested opening prompt for the next agent
> Continue the TradePartner end-user app design on branch `spike/design-ui`. Read `docs/research/ui/HANDOFF.md`, `DESIGN.md` and `ai-tells.md` first. Build the Book detail screen in direction E (value + chart with expected range, holdings vs target weights, recent orders, why it holds what it holds, stop/resume with reason and confirm), desktop and phone, with loading/empty/error states. Follow the owner's rules in the handoff. Screenshot and self-critique before showing me.
