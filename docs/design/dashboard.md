# Dashboard design standard

**Status:** Accepted v1.0 (#165, 2026-09-25) · **Applies to:** every dashboard page from T21 (data health) and T44 (trial registry) onward; T43's backtest page, merged before this standard, is retrofitted by T44 · **Owner:** Jose

## Why this exists

The roadmap plans one dashboard page per phase but no shared look. Left alone, each page invents its own tiles, colours and charts (T43 already did), and the result reads like a notebook. The owner wants a clean product surface: a KPI row, one hero chart, supporting cards, compact tables, light and dark. This document fixes that look once so every page inherits it. It changes nothing about what the dashboard is allowed to do (roadmap "User experience": read-only, one override screen, same database, no separate API).

## Principles

1. **Numbers first, decoration never.** Every element answers a question the owner has that day. No chat panel, no "AI insight" card, no promotional banner, no gradients behind text. LLM output, if Phase 5 ever admits it, is a memo beside a signal (ADR 0008), never a widget of its own.
2. **One accent series.** The thing being examined is the accent colour; benchmarks (SPY, MTUM) and comparison lines are muted. Colour follows the entity and never its rank.
3. **Status colours are reserved.** Good, warning, serious and critical mean exactly that, ship with an icon and a label, and are never reused as series colours.
4. **Time is the x-axis, money is the y-axis, and there is one y-axis.** Two measures of different scale are two charts, never a dual axis.
5. **Point-in-time everywhere.** Every page shows "as of" (the store's last `known_at`) and "last updated" (the last ingest run) in the header. A stale page says so with a warning chip, not a subtle grey.
6. **Both themes are designed, not flipped.** Dark mode uses its own validated steps from the same hues.

## Tokens

Defined once in `src/tradepartner/dashboard/theme.py` and mirrored in `.streamlit/config.toml`. Charts read the same values through a Plotly (or Altair) template built from them, so a chart and a tile never disagree on a colour. Reference instance: the validated default palette in the `dataviz` skill; swap values only by re-running its validator.

| Role | Light | Dark |
|---|---|---|
| Page background | `#f6f7f4` | `#12161a` |
| Card surface | `#ffffff` | `#1a2027` |
| Border | `#d6dad3` | `#303841` |
| Text primary | `#1c2026` | `#e7eaed` |
| Text secondary | `#5a6470` | `#9aa5b1` |
| Accent (series 1, blue) | `#2a78d6` | `#3987e5` |
| Series 2 (orange) | `#eb6834` | `#d95926` |
| Series 3 (aqua) | `#1baf7a` | `#199e70` |
| Series 4 (yellow) | `#eda100` | `#c98500` |
| Benchmark / muted line | text secondary at 60 % | text secondary at 60 % |
| Good | `#2c7a4b` on `#ddf0e3` | `#6cc08f` on `#17301f` |
| Warning | `#b0741c` on `#f8ebd2` | `#d9a650` on `#3a2e15` |
| Critical | `#b23f36` on `#f7dedb` | `#e07b73` on `#3b1f1d` |
| Sequential ramp (magnitude) | blue steps 100 to 700 | blue steps 100 to 700 |
| Diverging pair (polarity) | blue to red, grey midpoint `#f0efec` | blue to red, grey midpoint `#383835` |

Categorical slots are used **in fixed order** and never cycled; a fifth series folds into "Other" or becomes small multiples. Type: the system sans (Streamlit default) for text, a monospace face for every number column so digits align. Radius 8 px on cards, 999 px on chips. Spacing on an 8 px grid; 16 px gutters at phone width.

## Layout

Every page is the same skeleton, top to bottom:

1. **Header row:** page title left; right, the date-range control, "as of" and "last updated", and the theme toggle. One row, no wrapping on a laptop.
2. **KPI row:** three to five stat tiles, equal width. Each tile: label, value, delta versus the prior period with a directional arrow and the comparison ("vs 5,732 last period"), optional sparkline. A tile with no comparison shows none rather than a fake one.
3. **Hero chart:** one wide card, the page's main time series, with the accent series and muted benchmarks, a crosshair tooltip and a legend when two or more series are present.
4. **Supporting cards:** a two- or three-column grid of smaller charts, gauges and lists. Each card has a title and an optional "…" menu that only ever offers "view as table" and "export".
5. **Table:** compact, striped, numbers right-aligned in monospace, at most eight columns; anything wider becomes a detail view.

## Components

| Component | Rule |
|---|---|
| Stat tile | Value in the largest type on the page; delta as a chip (good or critical status colour by sign, with an arrow); comparison text in secondary ink. Never a number on every point of the sparkline. |
| Time-series card | 2 px lines, markers of at least 8 px on hover only, recessive grid, one y-axis, crosshair with a tooltip that names every series at that x. Benchmarks dashed and muted. Direct labels at the line ends for up to four series; a legend box always present for two or more. |
| Bar card | Thin bars, 4 px rounded data ends anchored to the baseline, 2 px surface gap between adjacent bars, per-bar hover tooltip. Highlight one bar (the selected day, the current month) with the accent; the rest in a muted step. |
| Gauge | Only for a value against a target (repeat rate against its goal, paper tracking against its tolerance). Arc in the accent, target tick in text ink, the number centred in monospace. |
| Status chip | Icon + label + colour, e.g. "⚠ stale: 2 sessions". Never colour alone. |
| Table | Striped rows, sticky header, right-aligned monospace numbers, positive and negative values with a leading sign, not colour alone. |
| Empty and busy states | The shell's existing "no store yet", "store busy" and "unreadable" panels (T21a) are the only empty states; a page never renders an empty chart frame. |

Interaction defaults: every chart with a plot has a hover layer; hit targets are larger than the mark; filters live in one row above the charts, never inside a card. A table view exists for every chart (the "…" menu).

## Page map

| Phase | Page | KPI row | Hero | Supporting |
|---|---|---|---|---|
| 2 (T21) | Data health | coverage, gaps, delisted names present, last update | bars per session: rows ingested and missing share against `max_missing_share` | integrity checks as status chips; per-source staleness; gap report table |
| 3 (T43, T44) | Backtest, trial registry | CAGR, max drawdown, Sharpe and deflated Sharpe, cost drag | equity curve of the hypothesis (accent) against SPY and MTUM (muted), log scale toggle | drawdown chart; monthly returns bars; cost breakdown; trial registry table with holdout flag |
| 4 | Operations, override | positions, open orders, today's signals, kill-switch state | today's signal ranking with in/out reasons | fills table; journal chain per order; alerts list; the override form (the only write) |
| 6 | Review | calibration, loss attribution, override count | paper versus backtest drift | override log with outcomes; attribution table |

## Streamlit implementation notes

- `.streamlit/config.toml` carries the theme tokens (`base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `font`); the theme toggle switches the `data-theme` attribute and Streamlit's theme together.
- One CSS block injected once in `app.py` styles cards (`div[data-testid="stVerticalBlockBorderWrapper"]`), metric tiles (`div[data-testid="stMetric"]`) and tables; pages never inject their own CSS.
- Charts are Plotly with a shared template from `theme.py`; a page never sets a colour literally. Categorical slot order is the template's `colorway`.
- `st.set_page_config(layout="wide")` stays; the grid is `st.columns` with the ratios above.
- Every page is a pure function of a read-only connection and a date range (T21a), so it renders headless in tests; the tests assert the header shows "as of" and "last updated" and that no colour literal appears in page code.

## What stays out

Chat assistants, "AI insight" cards, upgrade or promotional banners, user avatars and notifications (single owner, local), live-streaming widgets, and any widget that writes to the store other than the override form. If a mock shows it and this list names it, it is out.

## Open decision, deferred

Whether Streamlit with this standard is enough, or a React front end behind a thin read-only API is warranted, is decided by an ADR at the start of Phase 4, when the operations page (the densest one) is specified. Until then this standard is implemented on Streamlit. A React choice would amend the roadmap's "no separate API" rule and must say why the duplicated logic is worth it.
