# End-user app: design notes

**Status:** spike (`spike/design-ui`), prototype under [`web/`](../../../web/). **Date:** 2026-10-10 · **Direction:** v4, built on [shadcn/ui](https://github.com/shadcn-ui/ui) (the stack Base44 apps use: React, Vite, Tailwind, Radix, lucide)

> **Current direction: E, "research-grade Robinhood"**, chosen by the owner on 2026-10-10 after five samples (C rejected as a wall of text). The design contract is [DESIGN.md](DESIGN.md); the samples are in [`screens/samples/`](screens/samples/). Sections below about v4 describe the previous build and are kept for history.


**What to avoid:** [ai-tells.md](ai-tells.md), the checklist of AI design tells, our four versions mapped onto it, and an audit of the current build.

The app is for the owner as a *user*, not its developer. Screens built: **Overview** and **Research**. Next: Book detail (holdings vs targets, orders, why, stop/resume).

## How we got here

| Version | What it was | Owner's verdict | Lesson |
|---|---|---|---|
| v1 | Light, warm paper, Inter, cards | "Feels AI" | Generic defaults |
| v2 | Dark trading terminal after the owner's inspiration images | "Still AI" | Inter, bordered cards, status dots, pill tags, neon green, glyph logo: the catalogue of AI tells |
| v3 | "Ledger": serif sentence headline, mono numbers, warm cream | "Even more AI"; the serif hero and italics are a tell | A serif headline on warm cream is close to Claude's own house style |
| **v4** | **shadcn/ui as shipped**, with TradePartner's three meanings added | — | Use a real, maintained design system instead of inventing a look |

Reference apps reviewed for v3 (from their READMEs): Wealthfolio, Ghostfolio, Sure (Maybe fork), FreqUI. The v4 layout follows shadcn's own `dashboard-01` block: inset sidebar, slim header, summary cards, one interactive chart card, a data table.

## Tokens

One file: [`web/src/index.css`](../../../web/src/index.css). shadcn's theme variables are kept exactly as shipped (neutral, `--radius: 0.625rem`, Geist and Geist Mono) so upstream components drop in unchanged. TradePartner adds only three meanings, exposed as Tailwind colours (`text-gain`, `text-loss`, `text-attention`, `bg-attention-soft`):

| Token | Light | Dark | Means |
|---|---|---|---|
| `gain` | `#047857` (5.5:1 on white) | `#34d399` | money made, ahead of the S&P |
| `loss` | `#dc2626` (4.8:1) | `#f87171` | money lost, behind the S&P |
| `attention` | `#b45309` (5.0:1) | `#fbbf24` | waiting on you |

`chart-1` (your line) and `chart-2` (the S&P) are neutral. Charts read every colour at runtime ([`lib/tokens.ts`](../../../web/src/lib/tokens.ts), which normalises oklch to rgba for the canvas) and repaint on theme change. Dark is the default; the header switch picks light.

Components come from shadcn's registry source on GitHub (`apps/v4/registry/new-york-v4/ui`), copied into `web/src/components/ui/` (the CLI's registry host is blocked from this environment, so they were added by hand, unmodified except import paths).

## Overview ("How am I doing?")

- Four summary cards: total value (with the day's change; new money not counted as gain), return since start vs the S&P, **Needs you** (Nothing / n things), next run.
- One chart card: your return vs the S&P 500 from 0%, 1W/1M/3M/All (ranges longer than your history are disabled), hover updates the legend values.
- Books table: status (Running / Needs you / Stopped by you 9:41 am), value, since start, vs S&P, next run.
- An alert appears above the cards when something waits on you.

## Research: a human-centred interface to the lab

The lab is rigorous (claims register, ranked backlog, frozen hypotheses, sweeps, a trial count behind the deflated Sharpe, single-use holdouts, forward exams on paper, owner decisions), but today it is spread over the CLI, three Streamlit pages and GitHub issues. The Research screen organises it around the **owner's questions**, not the pipeline:

| Question | Where it's answered |
|---|---|
| What's waiting on me? | **Waiting on you** list at the top (count in the sidebar): what, why it matters, the exact next step, how long it takes |
| What's on the go, and what's stuck? | **Ideas** tab, grouped by stage: On paper · Ready to test · Blocked · Exploring · Parked (with the reason) |
| Did it work? Could it be luck? | **Results** tab: return vs the S&P a year after costs; a **luck check** (the deflated Sharpe as "chance the edge is real after counting every version tried"), tries counted, exam status |
| What have I used up? | **Honesty budget** tab, per family: versions tried, final exam still unseen or used (date), promotions to paper used |
| What have I learned? | **Lessons** tab: your own recorded lessons (empty today, with the first one to write), then the published findings your ideas lean on, graded in words ("Holds up", "Mixed", "Doesn't hold") |

Every idea opens the same **story** in a side panel, in the order a person asks: why it might work (evidence bar and note) → what you said before testing (expected result, stop rule) → what happened (or "not tested yet") → the exam (paper progress or holdout status) → blocked by / parked because / what's next. If the idea is waiting on you, that comes first.

Plain-language mapping kept consistent everywhere: DSR → "luck check"; N → "versions tried"; holdout → "final exam"; forward exam → "exam on paper"; claim grades → Holds up / Mixed / Doesn't hold / Not enough evidence.

**Sample data.** Names, stages, blockers and pending decisions come from the repo's docs (backlog B1 to B10, STATUS, ADRs 0016 and 0017). H1's luck check (DSR 0.7262, psr basis) is real; numbers the docs don't give (e.g. excess returns) are invented and tagged "sample" in the UI.

**Open questions for the owner**
- Read-only, or should Research let you act (register a run, record a decision, write a lesson) with a reason and a confirm, like stop/resume?
- B4 shows as both "run in-sample" (STATUS) and "parked" (ADR 0016); the screen surfaces this as a decision rather than picking.
- Dark by default; say if it should follow the OS.

## States (`?state=`)

`alert`, `stopped`, `loading` (skeletons in the real layout), `error` ("Can't reach TradePartner"; strategies keep running), `empty` ("No books running yet"). `?theme=light` forces light.

## Screenshots

[`screens/`](screens/): `overview-desktop`, `overview-phone`, `research-desktop`, `research-phone`, `research-sheet-desktop`, `light-desktop`, `alert-desktop`, `loading-desktop`, `error-phone`. v2 for comparison in [`screens/previous/`](screens/previous/).
