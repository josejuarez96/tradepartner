# Dashboard design standard

**Status:** Accepted v1.2 (#165, 2026-09-25; top navigation, Home and Book looks and three chart roles added by T274, #2170, 2026-10-26; open decision closed by [ADR 0011](../decisions/0011-dashboard-technology.md), #268, 2026-09-26) · **Applies to:** every dashboard page from T21 (data health) and T44 (trial registry) onward; T43's backtest page, merged before this standard, is retrofitted by T44 · **Owner:** Jose

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
| Benchmark / muted line | text secondary at 60 % (opacity applied by `series_encodings`) | text secondary at 60 % |
| Role `book` (the book's own line) | accent blue `#2a78d6`, solid | `#3987e5`, solid |
| Role `benchmark` (SPY) | text secondary `#5a6470`, dashed 4-4 | `#9aa5b1`, dashed 4-4 |
| Role `needs_you` | series 2 orange `#eb6834` | `#d95926` |
| Good | `#2c7a4b` on `#ddf0e3` | `#6cc08f` on `#17301f` |
| Warning | `#b0741c` on `#f8ebd2` | `#d9a650` on `#3a2e15` |
| Critical | `#b23f36` on `#f7dedb` | `#e07b73` on `#3b1f1d` |
| Sequential ramp (magnitude) | blue steps 100 to 700 | blue steps 100 to 700 |
| Diverging pair (polarity) | blue to red, grey midpoint `#f0efec` | blue to red, grey midpoint `#383835` |

**Colour rules for the roles (T274).** Pages name `book`, `benchmark` or `needs_you` (`theme.Palette`), never a hex. `book` is the accent: the one line or number the page is about. `benchmark` is SPY, always dashed grey (the `dash` field on the role is what the chart helper reads), never any other colour. `needs_you` is the orange series and is **reserved for items that need the owner** (a halted book, an overdue delisting, a failed run): on Home and the Book page it appears nowhere else, so a screen with no `needs_you` is one where nothing needs him (the same orange is still categorical slot 2 on multi-series charts elsewhere, e.g. Performance). It is a series colour, not a status colour: the item's accent bar and title carry it, an icon and a label go with it like a status chip, the one action follows as text, and any status chip beside it keeps its own status colour. "No orange" in the Home spec means no `needs_you`, not no warning chip.

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

## Navigation (T274, #2170)

The shell is a **top navigation**: `Home`, `Research`, `Books`, then a `Details` drop-down holding today's pages (Data health, Backtest, Trial registry, Operations, Trades, Performance, Override). Home is the default page. It replaces the sidebar radio; each page keeps drawing from the shell's one read-only connection (`render(conn)`; Home and Books take `render(conn, settings)`). Research is the existing developer view of registrations and runs, unchanged; a real Research workspace is a later amendment. Home and Books are placeholders ("Coming next") until T275 and T276. **Lab** (planned, T294 to T296, #2304) is a top tab after Research: the checkout's sweep and hypothesis files with their registry and git state, Register and Run as confirms that state the consequence in the method's terms (the file freezes; N and SR\* rise), a status card, and the result read thresholds first, then the result against them, then the rule's verdict, each quant term with an ⓘ.

## Home and Book looks (approved by the owner, 2026-10-26)

**Home**, top to bottom: a one-line status; **Needs you** (quiet when fine: "Nothing needs you" in one line and no orange anywhere; loud when not, each item in orange with its one action as copyable text); one **card per book** (status pill, equity as of its last marked session, return since start and SPY's over the same days in dollars and percent first, a small book-against-SPY line chart with `book` solid and `benchmark` dashed on one y-axis, the trades sentence, holdings count, cash, top tickers, a link to the book page); **Data status**; a **Research strip**. Every time is ET; the words "ops", "reconciliation", "known_at", ids and raw statuses never appear.

**Book page**: a header (name, status pill, a plain strategy sentence, value, return since start, SPY over the same days); the book-against-SPY chart with range buttons (since start, 1M, 3M) beside best day, worst day, maximum drawdown ("the biggest fall from a high") and "too early to judge: N of M rebalances"; **Today** as grouped sentences (sells then buys, time range, count, dollars, notable names, failures in plain words) or a plain empty state; **Holdings** (count, cash, up and down today, two tables of biggest gains and losses by ticker with value, dollars and percent since bought). The book is chosen by the Books tab's picker or the card's link, defaulting to the one open book. Light theme first; the dark tokens stay valid.

## Page map

| Phase | Page | KPI row | Hero | Supporting |
|---|---|---|---|---|
| 2 (T21) | Data health | coverage, gaps, delisted names present, last update | bars per session: rows ingested and missing share against `max_missing_share` | integrity checks as status chips; per-source staleness; gap report table |
| 3 (T43, T44) | Backtest, trial registry | CAGR, max drawdown, Sharpe and deflated Sharpe, cost drag | equity curve of the hypothesis (accent) against SPY and MTUM (muted), log scale toggle | drawdown chart; monthly returns bars; cost breakdown; trial registry table with holdout flag |
| 4 | Operations, override | positions, open orders, today's signals, kill-switch state | today's signal ranking with in/out reasons | above the Books table, "Waiting on you" (T257): every book's items, each a status chip with the command that clears it, or one good chip when nothing waits; the Risk card (T260): cash, gross exposure, largest position and drawdown, each against the window's frozen limit with the headroom left; fills table; journal chain per order; alerts list; the override form (the only write) |
| 4 (T274 to T276) | Home, Books | see "Home and Book looks" | the book against SPY | Needs you, book cards, Today, holdings |
| 4 (T259) | Trades | orders, filled, notional traded, the session's notional-weighted slippage (bp) | per-order slippage bars against the session's open, adverse positive, the largest in the accent | the order table (ticker, side, shares, fill price, status, slippage, reason; signal rank in the tooltip); a book and a session selector in the filter row |
| 4 (T252) | Performance | equity, cumulative return, daily P&L, versus SPY | equity curve rebased to 100 at the base session: the book (accent), SPY (muted, dashed), the tracking trial's line over its own span once `paper report` has run | daily P&L bars (latest day in the accent); the tracking state: `not yet`, `report due` or the trial line; a `benchmark unavailable` chip when SPY has no bar; a book selector when more than one book has a window |
| 4 (T294 to T296) | Lab | the family's N, SR\* today, the holdout's state, the run slot | the selected sweep's pre-registered thresholds, then its variants against them | the file listing with registry and git state; Register and Run confirms; the status card; the rule's verdict and "how it was computed" |
| 6 | Review | calibration, loss attribution, override count | paper versus backtest drift | override log with outcomes; attribution table |

## Streamlit implementation notes

- `.streamlit/config.toml` carries the theme tokens (`base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `font`); the theme toggle switches the `data-theme` attribute and Streamlit's theme together.
- One CSS block injected once in `app.py` styles cards (`div[data-testid="stVerticalBlockBorderWrapper"]`), metric tiles (`div[data-testid="stMetric"]`) and tables; pages never inject their own CSS.
- Charts are Plotly with a shared template from `theme.py`; a page never sets a colour literally. Categorical slot order is the template's `colorway`.
- `st.set_page_config(layout="wide")` stays; the grid is `st.columns` with the ratios above.
- Every page is a pure function of a read-only connection and a date range (T21a), so it renders headless in tests; the tests assert the header shows "as of" and "last updated" and that no colour literal appears in page code. The one exception is the override page's **submit** (ADR 0011, 2026-09-26): the form still renders read-only, but the shell runs its submit through T64b's `override` writer (an `on_click` callback) before it opens the read-only connection, since a write cannot open in-process while a read connection is held.
- `.streamlit/config.toml` binds Streamlit's server to localhost and turns usage telemetry off, and `render_app` refuses to render when the running options differ (ADR 0011); tests pin the file and the refusal.

## What stays out

Chat assistants, "AI insight" cards, upgrade or promotional banners, user avatars and notifications (single owner, local), live-streaming widgets, and any widget that writes to the store other than the override form and the three that run the desk's own command as a child process (ADR 0018 sections 2 and B to C): the Book page's "Stop this book now" (`paper kill`, T277) and the Lab's Register (`sweep register`) and Run (`sweep run`, `backtest`). If a mock shows it and this list names it, it is out.

## Open decision, closed (formerly "Open decision, deferred")

Whether Streamlit with this standard is enough, or a React front end behind a thin read-only API is warranted, was the one decision this standard left open for the start of Phase 4. [ADR 0011](../decisions/0011-dashboard-technology.md) (#268, owner decision on #247 question 1, 2026-09-26 ET) decides it: **Streamlit under this standard, no separate API, Streamlit's own server bound to localhost**. Pages render as pure functions of a read-only connection, the operations page draws `execution.ops.page_data`, the override form is the only write and its submit calls T64b's `override` writer outside the shell's read-only connection, and the roadmap's "no separate API, no duplicated logic" rule stands. A React front end is the rejected option, with the conditions for revisiting it, in the ADR.
