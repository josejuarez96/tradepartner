# End-user app: design notes

**Status:** spike (`spike/design-ui`), prototype under [`web/`](../../../web/). Screen 1 of N. **Date:** 2026-10-10 · **Direction:** v3 "ledger" (v2 "terminal" screenshots kept in [`screens/previous/`](screens/previous/))

The app is for the owner as a *user*: four questions, nothing about how the machine works.

1. How am I doing? (overall, per book, vs SPY): **Overview** (built)
2. What is each book holding and doing, and why?: Book detail (next)
3. Is anything wrong or waiting on me?: a "needs you" block at the top of Overview; one quiet sentence when nothing is
4. Stop or resume a book, safely: on Book detail (reason required, explicit confirm)

## Why v3: v2 still read as AI-made

The owner's verdict on v2 was "something about it feels AI still". Two kinds of evidence on why:

**What similar open-source apps do** (screenshots pulled from each repo's README):

| Project | What it is | What it looks like | Takeaway |
|---|---|---|---|
| [Wealthfolio](https://github.com/afadil/wealthfolio) | Local, private portfolio tracker (desktop) | The whole UI in a monospace (`font-mono` on `<body>`, JetBrains Mono), Merriweather serif for headings, warm cream and warm-black themes, olive and burnt-orange instead of neon green and red | The one that feels *made by a person*. It commits to a voice: a private ledger, not a SaaS dashboard |
| [Ghostfolio](https://github.com/ghostfolio/ghostfolio) | Self-hosted wealth tracker (web) | White, Material-style cards, one teal area chart | Clean but stock; nothing you'd recognise it by |
| [Sure](https://github.com/we-promise/sure) (Maybe fork) | Personal finance | Dark rounded cards, "Welcome back, Jack", donut and Sankey, AI chat panel | The template look: greeting header, card grid, chart-per-card |
| [FreqUI](https://github.com/freqtrade/frequi) | Web UI for the Freqtrade bot | Bootstrap trading terminal, candles, indicators, tooltip dumps | Developer tool; what this app must *not* become |

**What design writers name as "AI tells"** (blog posts, informed opinion, not measurements): Inter by default; centred stacks of bordered cards; accent colour on everything; decorative status dots; uppercase spaced-out micro labels; pill badges; a greeting or subtitle under every heading; happy-path only. v2 had most of these: Inter everywhere, three bordered cards, a green-dot "Nothing needs you", pill tags for "Sample data" and "Paper money", uppercase column labels, neon green on blue-black, a chart-line logo in a rounded square.

## The v3 direction: a private ledger

| Choice | Instead of (v2) | Why |
|---|---|---|
| **The answer is a sentence**, set in a serif (Newsreader): "Since Aug 3 you're *up 0.79%*, 2.3 points ahead of the S&P 500." It changes with the range | A big dollar number plus a stats row | Question 1 answered in words, the way a person would say it. The dollar total sits under it in mono |
| **Every number in a monospace** (Geist Mono): totals, the legend, axis, the books ledger, the masthead | Inter with tabular figures | Columns line up like a statement, and the page gets a voice (Wealthfolio's move) |
| **Prose in a plain sans** (Geist) | Inter | Only for sentences: alerts, the quiet line |
| **Rules, not cards.** Sections are separated by 1px lines on the page itself; corners almost square (3 to 6px) | Bordered rounded cards | Reads like paper; nothing floats |
| **Warm paper and ink.** Warm black `#13120f` with bone ink `#ece7dc`; light is paper `#f4f0e7` with near-black ink | Blue-black and white | Warmth is what keeps a dark page from looking like every trading template |
| **Moss for gains, rust for losses**, used only on numbers and the one italic phrase | Neon `#2fd480` / `#f8636a`, tinted tiles, coloured lines | Muted, natural; still unmistakable, and signs (+/−) are always printed |
| **"Needs you" is inverted ink**, not a colour: the alert is a bone block with dark text; a book row gets a small inverted "needs you" mark; the nav shows a count | An amber card, amber text, dots | The loudest thing the page can do without adding a hue, so attention never competes with gain/loss |
| **Your line is ink**, a faint wash under it; the S&P is a thin grey line; no axis tags | Line coloured by result, tags on the axis | The legend above the chart prints both values and follows the pointer |
| **A lowercase wordmark** `tradepartner`, text navigation, text tabs on phone | Logo glyph, icon tab bar | Fewer borrowed symbols |
| **Plain words for state**: "paper account · sample data", "‖ stopped by you, 9:41 am", "day 1", "not enough history yet" | Pills, dots | |

## Tokens

Single source: [`web/src/tokens/tokens.css`](../../../web/src/tokens/tokens.css). Components use `var(--…)` only; charts read the same variables at runtime ([`lib/tokens.ts`](../../../web/src/lib/tokens.ts)) and repaint when the theme flips.

| Group | Tokens |
|---|---|
| Paper and ink | `bg`, `bg-raised`, `rule`, `rule-strong`, `ink`, `ink-2`, `ink-3`, `ink-inverse` |
| Meaning | `gain` (moss), `loss` (rust), `attention-bg` / `attention-ink` (inverted ink) |
| Chart | `chart-you`, `chart-you-fill`, `chart-bench`, `chart-crosshair` |
| Type | `font-serif` (Newsreader), `font-sans` (Geist), `font-mono` (Geist Mono); `text-2xs` 11 to `text-3xl` 42 |
| Space | 4px base: `space-1` 4 … `space-16` 64; `gutter` 16 phone / 40 desktop |
| Shape | `radius-sm` 3, `radius-md` 6 |
| Motion | `dur-fast` 120ms, `dur-med` 220ms, one easing; zeroed under reduced motion |

### Contrast (WCAG AA, text at 4.5:1 minimum)

| Token | Dark on page / raised | Light on page / raised |
|---|---|---|
| ink | 15.2 / 14.1 | 15.3 / 14.0 |
| ink-2 | 8.4 / 7.8 | 6.9 / 6.3 |
| ink-3 | 5.7 / 5.3 | 5.2 / 4.7 |
| gain | 9.7 / 9.0 | 5.6 / 5.1 |
| loss | 7.7 / 7.2 | 5.3 / 4.9 |
| inverted "needs you" | 15.2 | 15.3 |

The benchmark line colour is decorative (its value is always printed in the legend). Touch: text tabs 52px tall, range options and buttons 44px under `pointer: coarse`, ledger rows ≥ 64px.

## States (preview with `?state=`)

| State | What you see |
|---|---|
| default | The sentence, the total, "Nothing needs you. Next run: daily, Mon 9:25 am. Prices as of Fri's close." Then the chart and the books ledger |
| `alert` | An inverted block above everything: "needs you · fri 9:25 am", what happened, what was (not) done, what to do, "open daily →"; the book row carries the "needs you" mark; the nav shows "overview 1" |
| `stopped` | "‖ stopped by you, 9:41 am" in the book's Next column; the count reads "2 running, 1 stopped" |
| `loading` | Grey bars in the real layout, so nothing jumps |
| `error` | "This page can't reach TradePartner on your computer." Strategies keep running; only the view is affected; try again |
| `empty` | "Nothing is trading yet." + see strategies |
| a young book | main: "day 1" and a dash instead of a fake comparison; ranges longer than your history (3m) are disabled with "Not enough history yet" |

Dark is the default; the masthead switch (or `?theme=light`) picks paper.

## Decisions and open questions for the owner

- **Today's change excludes new money.** main opened on Oct 9 with $100,008.90; that is not a gain. The API should serve a time-weighted index next to the summed value (the sample does: `portfolio.equity[].index`).
- **Combined line.** "You" is all books together, time-weighted. Per-book lines vs SPY belong on Book detail.
- **Research has no home yet.** Proposed: Strategies becomes Research, ideas grouped by stage (on paper, tested, up next, parked), with research decisions joining "needs you". Waiting on the owner: read-only or approvals, and order relative to Book detail.
- **Dark by default**, ignoring the OS setting. Say if it should follow the OS instead.
- **Error with stale data** (show the last good numbers dimmed, labelled with their time) is a better error state once the API caches; deferred until the API shape is known.
- **Charts attribution.** Lightweight Charts' logo is off; attribution is a footer link (its licence asks for one).

## Screenshots

[`screens/`](screens/): `overview-desktop`, `overview-phone` (+ `-2` scrolled), `light-desktop`, `light-phone`, `alert-desktop`, `alert-phone`, `states-phone` (stopped, loading, error, empty). v2 for comparison: [`screens/previous/`](screens/previous/).
