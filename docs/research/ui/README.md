# End-user app: design notes

**Status:** spike (`spike/design-ui`), prototype under [`web/`](../../../web/). Screen 1 of N. **Date:** 2026-10-10

The app is for the owner as a *user*: four questions, nothing about how the machine works.

1. How am I doing? (overall, per book, vs SPY): **Overview** (built)
2. What is each book holding and doing, and why?: Book detail (next)
3. Is anything wrong or waiting on me?: alerts at the top of Overview; quiet line when nothing is
4. Stop or resume a book, safely: on Book detail (reason required, explicit confirm)

## What makes it feel premium (the bar I designed to)

- **Restraint.** One hero number, one chart, one list. No KPI tile row, no gauges, no card per metric.
- **Colour is meaning, not decoration.** Only three hues carry information: gain, loss, attention. Your line is ink; the benchmark is a quieter ink. A fourth hue (blue) exists only as the keyboard focus ring.
- **Warm paper, not white.** Page `#f6f5f1`, cards white on a 1px hairline plus a barely-there shadow. Dark mode has its own steps (near-black `#0d0e10`, lifted surfaces), not an inversion.
- **Type does the hierarchy.** Inter with tabular figures everywhere (`tnum`), so digits never shift width when values update. Tight tracking on the hero number, a 7-step scale, three weights.
- **Chart treatment.** Lightweight Charts, no vertical grid, faint horizontal grid, a stronger 0% baseline, a soft ink gradient under your line, no border, no scroll/zoom (calm, and no fight with page scroll on a phone). Both lines are rebased to 0% at the start of the range, the honest comparison. Hovering scrubs the numbers above the chart to that date instead of popping a tooltip.
- **Plain language.** "Nothing needs you. Next: daily, Mon 9:25 am." "Stopped by you at 9:41 am." "Ahead by 2.3 pts." "Too early for a trend."

## Tokens

Single source: [`web/src/tokens/tokens.css`](../../../web/src/tokens/tokens.css). Components use `var(--…)` only; charts read the same variables at runtime ([`lib/tokens.ts`](../../../web/src/lib/tokens.ts)) and repaint when the scheme flips.

| Group | Tokens |
|---|---|
| Surfaces | `bg`, `surface`, `surface-sunken`, `hairline`, `hairline-strong` |
| Ink | `ink`, `ink-2`, `ink-3`, `ink-inverse` |
| Meaning | `gain`, `loss`, `attention` (+ `-soft` backgrounds, `attention-edge`) |
| Chart | `chart-line`, `chart-fill-top/bottom`, `chart-benchmark`, `chart-grid`, `chart-crosshair` |
| Type | `text-2xs` 11 to `text-3xl` 44; weights 420/520/600; `font-num-features` |
| Space | 4px base: `space-1` 4 … `space-16` 64; `gutter` 16 phone / 32 desktop |
| Shape | `radius-sm` 6, `radius-md` 10, `radius-lg` 16, `radius-pill` |
| Motion | `dur-fast` 120ms, `dur-med` 220ms, one easing; zeroed under reduced motion |

### Contrast (WCAG AA, text at 4.5:1 minimum)

| Token | Light on surface / bg | Dark on surface / bg |
|---|---|---|
| ink | 17.8 / 16.3 | 15.3 / 16.5 |
| ink-2 | 6.8 / 6.3 | 7.6 / 8.2 |
| ink-3 | 5.5 / 5.1 | 5.6 / 6.0 |
| gain | 5.4 / 4.9 | 7.7 / 8.3 |
| loss | 5.9 / 5.4 | 6.4 / 6.9 |
| attention | 5.9 / 5.4 | 8.6 / 9.2 |

The benchmark line colour is decorative (its value is always printed in text beside the chart). Touch: tabs 52px tall, buttons and segmented options grow to 44px / 36px under `pointer: coarse`, book rows ≥ 72px.

## States (preview with `?state=`)

| State | What you see |
|---|---|
| default | Calm: hero, quiet "Nothing needs you" line, chart, books |
| `alert` | An attention card *above* everything: what happened, what was (not) done, what to do; the affected book row says "Needs a look"; a dot on the Overview tab |
| `stopped` | The book row reads "Stopped by you at 9:41 am" in attention colour; the count reads "2 running, 1 stopped" |
| `loading` | A grey skeleton of the real layout, so nothing jumps |
| `error` | "Can't reach TradePartner on this computer" + reassurance that strategies keep running + Try again |
| `empty` | "No books running yet" + See strategies |
| a young book | main (day 1): "Too early for a trend", "Day 1" instead of a fake comparison |

Add `?theme=dark` or `?theme=light` to force a scheme (otherwise it follows the OS).

## Decisions and open questions for the owner

- **Today's change excludes new money.** main opened on Oct 9 with $100,008.90; that is not a gain. The API should serve a time-weighted index next to the summed value (the sample does: `portfolio.equity[].index`).
- **Combined line.** "You" on the chart is all books together, time-weighted. Per-book lines vs SPY belong on Book detail.
- **Inspiration images** were not visible to the agent in this session; the look was worked out from the brief. Re-attach them if the mood should be checked against them.
- **No theme toggle** in the UI: it follows the OS. Say if you want one.
- **Error with stale data** (show the last good numbers greyed, labelled with their time) is a better error state than a blank card once the API caches; deferred until the API shape is known.
- **Charts attribution.** Lightweight Charts' logo is off; attribution is a footer link instead (its licence asks for one).

## Screenshots

[`screens/`](screens/): `overview-desktop`, `overview-phone` (+ `-2` scrolled), `dark-desktop`, `alert-desktop`, `alert-phone`, `states-phone` (stopped, loading, error, empty).
