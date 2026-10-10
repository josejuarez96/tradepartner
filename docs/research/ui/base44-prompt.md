# Base44 prompt: TradePartner app

Paste everything below the line into Base44.

---

Build **TradePartner**, a private app for one person (me) to follow my automated US-stock trading strategies. I check it at my desk and on my phone, so phone layout must be first-class. It is read-only except for stopping and resuming a strategy. It never places trades and needs no logins to brokers; use the sample data below and label it "Sample data" in the header.

## What the app must answer (cut anything that doesn't serve one of these)
1. How am I doing, overall and per strategy, against the S&P 500?
2. What is each strategy holding and doing, and why?
3. Is anything wrong or waiting on me? (Rare. Quiet when nothing is.)
4. Stop or resume a strategy, safely.
5. Research: what am I testing, did it work, could it be luck, what's waiting on me.

## Words used in the app
- A **book** is one strategy trading its own paper (practice-money) account.
- All money is **paper money** today. Show "Paper account" in the sidebar.
- Plain language only: "Stopped by you at 9:41 am", never system codes.

## Screens

### 1. Overview
- Total value of all books, and the last trading day's change in dollars and percent (money newly added to a book is not a gain).
- Return since start vs the S&P 500, said once: "2.3 points ahead of the S&P 500".
- Needs you: "Nothing" when nothing waits; otherwise the count and the first item.
- Next run: the soonest scheduled run and which book.
- One line chart: my return vs the S&P 500, both starting at 0% at the left edge of the selected range. Ranges 1W, 1M, 3M, All; disable a range longer than my history and say "Not enough history yet". Hovering updates both values and the date above the chart.
- A books table: book, strategy, status (plain "Running"; only "Needs you" or "Stopped by you at 9:41 am" stand out), value, return since start, vs S&P 500, next run. Rows open the book.
- When something needs me, an alert sits above everything: what happened, what was or wasn't done, what to do, and a link to the book.

### 2. Book detail
- Value over time vs the S&P 500; return since start.
- Holdings: each stock with its weight vs its target weight (show the gap in percentage points).
- Recent orders and fills, and the last and next run in a sentence ("Sold 1, bought 1. 2 orders filled.").
- Why it holds what it holds: the strategy's one-line idea.
- **Stop / resume:** a button. Stopping requires a typed reason and a confirmation dialog that says exactly what happens ("No new orders will be placed for main until you resume. Current holdings stay."). Resuming also requires a reason. Show who stopped it and when.

### 3. Research
- **Waiting on you** list at the top (count badge in the navigation). Each item: an action title, one sentence on why it matters, how long it takes. Click opens the idea.
- **Ideas** grouped by stage: On paper, Ready to test, Blocked (show what blocks it), Exploring, Parked (show why).
- **Results** table: idea, test window, return vs the S&P 500 per year after costs, luck check, versions tried, exam status.
- **Honesty budget** per strategy family: versions tried, final exam ("still unseen" or "used on <date>"), promotions to paper used out of the cap.
- **Lessons**: lessons I've recorded from my own tests (empty state: "None recorded yet", with a button to open the first idea ready to write up), then published findings graded "Holds up", "Mixed", "Doesn't hold".
- **Idea detail** (side panel; full screen on phone), in this order: waiting-on-you box if any → published evidence → what I said before testing (expected result, stop rule) → what happened (or "Not tested yet") → the exam → blocked by / parked because / what's next. The backlog code (e.g. B10) appears once, at the bottom, as a reference.

Plain-language mapping, used everywhere: deflated Sharpe → "luck check" (chance the edge is real after counting every version tried); number of trials → "versions tried"; holdout → "final exam"; forward test on paper → "exam on paper".

## Graphics that explain themselves (no legends needed)
- **Evidence:** one small square tile per study, each marked with a symbol: ✓ supports, – mixed, ✕ against, followed by a verdict ("Mostly supported"). Never a multi-coloured bar.
- **Luck check:** a scale with labelled zones (under 50% "probably luck", 50–95% "could be luck", 95%+ "likely real"), a marker at the value, and the number above it.
- **Exam progress:** one step per rebalance when there are 13 or fewer (6 boxes for 6 monthly rebalances); otherwise a single track labelled "48 of 63 trading days".
- **Promotions used:** small slots, filled when used.

## Design rules (important)
- **Say it once.** Never repeat the same information in one block: no code badge next to a name that the description then repeats, no "Next step" line that restates the title, no icon beside the word it illustrates, no badge that repeats the text under it, no subtitle that rewords its heading.
- **Colour means something** and only three things get one: gain (green), loss (red), waiting on you (amber). Everything else is neutral. Colour is never the only signal: pair it with a sign (+/−), a symbol or a word.
- **Avoid the common AI-generated look:** no purple/indigo gradients or gradient text; don't leave the component library's default theme, radius and font untouched; no Inter, Geist, Space Grotesk, Instrument Serif or Fraunces as the only typeface; no cream-and-terracotta "editorial" look; no near-black with one neon accent; no serif or italic word highlighted in a headline; no ALL-CAPS spaced-out labels; no "A · B · C" meta strings; no arrows glued to link text; no rows of identical icon cards; no icons in tinted rounded squares; no badge on every table row; no cards inside cards; no emoji; no fade-up animation on every section; no stat strips of round numbers.
- Numbers use tabular figures so they don't shift width when they update. Right-align numbers in tables.
- Dark theme by default, with a light theme toggle in the header. Both must meet WCAG AA contrast.
- Phone: comfortable touch targets (at least 44px), no horizontal scrolling, tables collapse to two-line rows.
- Every screen has designed loading (skeletons in the real layout), empty and error states. Error copy: "Can't reach TradePartner. Your strategies keep running on their schedule; only this view is affected." with a Try again button.

## Sample data (label it as sample)
Benchmark: S&P 500 (SPY). Today's prices are as of Friday's close.

**Books**
| Book | Strategy | Idea | Cadence | Started | Start value | Value now | Return | vs S&P | Next run |
|---|---|---|---|---|---|---|---|---|---|
| main | Monthly momentum | Own the stocks that rose most over the past year, skipping the last month. Reshuffle monthly. | monthly | Oct 9 | $100,008.90 | $100,008.90 | 0.00% (day 1, holding cash) | — | First rebalance, Oct 30 |
| daily | Daily momentum | The same momentum idea, rechecked every day before the open. | daily | Aug 3 | $100,000.00 | $104,377.96 | +4.38% | +5.9 pts | Daily check, Mon 9:25 am |
| b3 | Profitability | Own companies that turn the most gross profit per dollar of assets. | monthly | Sep 1 | $100,000.00 | $98,938.54 | −1.06% | −2.2 pts | Monthly rebalance, Oct 30 |

All books: $303,325.40, +$2,473.79 (+1.23%) on Fri Oct 9; +0.79% since Aug 3 vs S&P 500 −1.56%. Generate a plausible daily value series for each book and the S&P 500 from its start date.

daily holds NVDA, AVGO, PLTR, META, GE (about 10% each, target 10%). b3 holds 20 names at 5% each (AAPL 5.1%, MSFT 4.9% shown). main holds cash until Oct 30.

**Research: waiting on you**
1. Run the short-term momentum test. "Everything is built. One overnight run answers whether last month's busiest winners keep rising after costs." 15 min, then overnight.
2. Answer 7 questions on earnings-reaction drift. "Hold length, which filings count, how to treat restatements. The test can't be registered until these are settled." 30 min.
3. Set the development boundary. "No backtest will read past this date. Recommended: Dec 29, 2023, where your first tests already stop." 2 min.
4. Momentum + profitability: test now or wait? "Your notes disagree: one says run it now, the newer decision says wait for a profitability finalist." 2 min.
5. Write down what monthly momentum taught you. "Its test and exam are done, but nothing is recorded, so your evidence still only shows other people's studies." 10 min.

**Research: ideas**
| Code | Idea | Stage | Evidence (for / mixed / against) | Result vs S&P per yr | Luck check | Versions | Exam |
|---|---|---|---|---|---|---|---|
| H1 | Monthly momentum | On paper (book main) | 3 / 2 / 0 | +1.1 pts | 73% | 2 | Paper: 0 of 6 monthly rebalances |
| S1·d | Daily momentum | On paper (book daily) | 1 / 2 / 1 | +0.4 pts | 41% | 8 | Paper: 48 of 63 trading days |
| B3 | Profitability | On paper (book b3) | 2 / 1 / 0 | −0.6 pts | 38% | 1 | Final exam unseen, on hold |
| B10 | Short-term momentum | Ready to test | 0 / 0 / 2 | — | — | — | — |
| B9 | Earnings-reaction drift | Blocked: filing-events data; a filing clock defect; 7 open questions | 0 / 1 / 1 | — | — | — | — |
| B3b | Cash profitability | Exploring (after profitability's exam) | 1 / 0 / 0 | — | — | — | — |
| B5 | Filing changes → fundamentals | Exploring, needs filing text | none yet | — | — | — | — |
| B7 | Insider buying | Exploring, needs insider-filing data | 0 / 1 / 1 | — | — | — | — |
| B4 | Momentum + profitability | Parked: waiting for a profitability finalist | — | — | — | — | — |
| B2 | Trend filter | Parked: lowers risk but also return; the goal is beating the S&P 500 | — | — | — | — | — |

Families: Momentum (9 versions tried, final exam used Oct 6, 1 of 2 promotions used), Profitability (1 version, exam unseen, 0 of 2), Momentum + profitability (0, unseen, 0 of 2).

Published findings: "12-1 momentum beat the market in long historical studies" (Holds up); "After 2010, net of costs, momentum's edge is weak and uneven" (Mixed); "Gross profitability predicts returns, in a narrow, long-short form" (Holds up); "Short-term earnings reactions mostly don't survive realistic costs" (Doesn't hold).
