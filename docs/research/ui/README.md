# End-user app: design notes

**Status:** spike (`spike/design-ui`), prototype under [`web/`](../../../web/). Screen 1 of N. **Date:** 2026-10-10

The app is for the owner as a *user*: four questions, nothing about how the machine works.

1. How am I doing? (overall, per book, vs SPY): **Overview** (built)
2. What is each book holding and doing, and why?: Book detail (next)
3. Is anything wrong or waiting on me?: alerts at the top of Overview; a quiet status line when nothing is
4. Stop or resume a book, safely: on Book detail (reason required, explicit confirm)

## What makes it feel premium (checked against the owner's inspiration images)

The references (two dark trading terminals, one light/dark data dashboard) share a mood rather than a layout: near-black, precise, quiet chrome, numbers doing the talking. What I took from them, and what I left out:

| Taken | Left out (fails "every element earns its place") |
|---|---|
| Dark first: near-black page `#09090b`, panels one step up, 1px hairline edges, no shadows, 12px corners | Watchlists, order entry, order book, screener, news and "AI insight" cards, gauges |
| Colour only where it means something: your line and its fill are green or red by how the shown period ended; the benchmark is a quiet grey | Brand colour, coloured icons, logos |
| Each line ends in a tag on the price axis ("You +0.8%", "S&P −1.6%"); the crosshair carries a date tag | Indicators, drawing tools, zoom and scroll |
| A faint dot field behind the plot instead of grid lines; one dotted 0% baseline | Vertical grid, chart borders |
| Period tiles tinted by sign (their "Performance" block), merged into the range control so it does two jobs | A separate KPI row |
| Uppercase micro labels over right-aligned tabular numbers, hairline rows: a real table for the books | Dense multi-panel layout; the phone gets stacked rows instead |
| Status top right in small type ("Nothing needs you · Next … · Prices as of …"), like their "Last updated" | Ticker tape |

Other choices:

- **Restraint.** One hero number, one chart, one table. Detail is a click away (Book detail).
- **Type.** Inter with tabular figures (`tnum`) so digits never shift width. A tight hero, then a quiet scale.
- **Hover scrubs the numbers.** Moving across the chart updates "You", "S&P 500", the date span and the verdict to that day, instead of a floating tooltip.
- **Plain language.** "Nothing needs you." "Stopped by you at 9:41 am." "Ahead by 2.3 pts." "Not enough history yet."
- **Light theme** is its own set of steps (switch in the top bar, remembered per browser), not an inversion.

## Tokens

Single source: [`web/src/tokens/tokens.css`](../../../web/src/tokens/tokens.css). Components use `var(--…)` only; charts read the same variables at runtime ([`lib/tokens.ts`](../../../web/src/lib/tokens.ts)) and repaint when the theme flips.

| Group | Tokens |
|---|---|
| Surfaces | `bg`, `surface`, `surface-raised`, `surface-sunken`, `hairline`, `hairline-strong` |
| Ink | `ink`, `ink-2`, `ink-3`, `ink-inverse` |
| Meaning | `gain`, `loss`, `attention` (+ `-soft` backgrounds, `-fill` chart areas, `attention-edge`) |
| Chart | `chart-benchmark`, `chart-dot`, `chart-crosshair` (your line uses the meaning tokens) |
| Type | `text-2xs` 11 to `text-3xl` 44; weights 420/520/600; `font-num-features`; `tracking-micro` for uppercase labels |
| Space | 4px base: `space-1` 4 … `space-16` 64; `gutter` 16 phone / 32 desktop |
| Shape | `radius-sm` 6, `radius-md` 8, `radius-lg` 12, `radius-pill` |
| Motion | `dur-fast` 120ms, `dur-med` 220ms, one easing; zeroed under reduced motion |

### Contrast (WCAG AA, text at 4.5:1 minimum)

| Token | Dark on panel / page | Light on panel / page |
|---|---|---|
| ink | 17.0 / 18.1 | 19.2 / 17.3 |
| ink-2 | 7.8 / 8.2 | 6.9 / 6.2 |
| ink-3 | 5.3 / 5.7 | 5.7 / 5.1 |
| gain | 9.7 / 10.3 | 5.4 / 4.9 |
| loss | 6.2 / 6.6 | 5.6 / 5.1 |
| attention | 9.2 / 9.7 | 6.1 / 5.5 |

Text in these colours also passes on the tinted backgrounds it sits on (period tiles, the alert card, the sample tag): ≥ 4.6:1. The benchmark line colour is decorative (its value is always printed beside the chart). Touch: tabs 52px tall, period tiles 44px, buttons and the theme switch grow to 44px under `pointer: coarse`, book rows ≥ 72px.

## States (preview with `?state=`)

| State | What you see |
|---|---|
| default | Calm: hero, "Nothing needs you" top right, chart, books |
| `alert` | An attention card *above* everything: what happened, what was (not) done, what to do; the status reads "1 thing needs you"; the book row reads "Needs a look"; a dot on the Overview tab |
| `stopped` | The book row reads "Stopped by you at 9:41 am" in attention colour; the count reads "2 running · 1 stopped" |
| `loading` | A grey skeleton of the real layout, so nothing jumps |
| `error` | "Can't reach TradePartner on this computer" + reassurance that strategies keep running + Try again |
| `empty` | "No books running yet" + See strategies |
| a young book | main (day 1): "Day 1" and a dash instead of a fake comparison; period tiles longer than your history read "—" and are disabled |

Dark is the default; the top-bar switch (or `?theme=light`) picks light.

## Decisions and open questions for the owner

- **Today's change excludes new money.** main opened on Oct 9 with $100,008.90; that is not a gain. The API should serve a time-weighted index next to the summed value (the sample does: `portfolio.equity[].index`).
- **Combined line.** "You" on the chart is all books together, time-weighted. Per-book lines vs SPY belong on Book detail.
- **Dark by default**, ignoring the OS setting, because the references are dark. The switch is there for a bright desk. Say if it should follow the OS instead.
- **Axis tags can sit on a tick label** (the S&P tag over "−2.0%"). Lightweight Charts draws both; acceptable for now.
- **Error with stale data** (show the last good numbers dimmed, labelled with their time) is a better error state than a blank card once the API caches; deferred until the API shape is known.
- **Charts attribution.** Lightweight Charts' logo is off; attribution is a footer link instead (its licence asks for one).

## Screenshots

[`screens/`](screens/): `overview-desktop` (dark), `overview-phone` (+ `-2` scrolled), `light-desktop`, `alert-desktop`, `alert-phone`, `states-phone` (stopped, loading, error, empty).
