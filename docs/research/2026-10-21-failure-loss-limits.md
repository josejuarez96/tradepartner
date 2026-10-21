# Research Report: Limiting losses when part of the system fails (risk layers for faster and short books)

**Brief:** #2157  ·  **Date:** 2026-10-21  ·  **Status:** COMPLETE for the four questions the orchestrator scoped (issue #2157 states no budget; this run used 16 searches and 13 fetches). Gaps are listed below.  ·  **Agent/model:** research agent, claude-opus-5-5

I could not run `gh issue view`. I read the issue through its public page, and the frame and principles below come from that page and the orchestrator's brief.

## Answer
**Verdict:** MIXED overall (graded per question below)  ·  **Confidence:** medium

1. **Broker-side protection (SUPPORTED, with limits).** Alpaca holds stop, stop-limit, trailing-stop, OCO and bracket orders while our host is down. They have hard gaps, though. Stops do not work in extended hours. Fractional stops cannot be GTC. GTC orders expire after 90 days. A stop fills at market after a gap, and in a halt reopen it can miss entirely. Alpaca offers no cancel-on-disconnect or dead-man's timer. Alpaca does **not** force-cover a short that becomes hard-to-borrow, but it may liquidate anything without notice to meet a margin call.
2. **Gap sizing for shorts (SUPPORTED as a principle, INSUFFICIENT for a published numeric rule).** The tail is documented: takeover targets gain about 32% in 3 days, GME rose about 1,600% in a month, and our own spike shows a short-leg month of +26.2% and one name nearly doubling. I found no Tier 1 or Tier 2 source that gives a standard per-name short cap. The practitioner evidence (Third Point) is that funds shrink single-name shorts and move to baskets or index hedges.
3. **Independent watchdog (MIXED).** The pattern is well established at venues (cancel-on-disconnect, cancel-all-after), but it *cancels orders* and does not reduce positions. With Alpaca, any off-host watchdog shares two things with the main system: the broker (class C) and, apparently, the single API key pair per account.
4. **Minimal layer sets:** see the Recommendation section. It is marked as a recommendation, and the owner decides.

## Evidence

### Q1. Alpaca protections that survive our host being down
| Claim | Source | Tier | Key figure (quoted) |
|---|---|---|---|
| A stop becomes a market order once triggered; the fill price is not guaranteed | Alpaca, Orders at Alpaca, "Stop Orders" | 1 | "A stop order does not guarantee the order will be filled at a certain price after it is converted to a market order" |
| A stop-limit can trigger on a gap and then not fill | same, "Stop-Limit Orders" | 1 | after a gap, the order "will remain active as a limit order until it is executable or cancelled" |
| Trailing stops work in regular hours only; Alpaca warns about GTC orders across sessions and halts | same, "Trailing Stop Orders" | 1 | "Trailing stop will not trigger outside of the regular market hours" |
| Stops are refused in extended hours, so **nothing protects an overnight or pre-market gap** | our sandbox probe 2026-10-19, b2 `ext.stop` | recorded (1) | 422 "stop orders are not eligible for extended hours trading" |
| GTC orders are auto-cancelled at 90 days | Orders at Alpaca, TIF; sandbox b2 `tif.*` | 1 + recorded | "GTC orders 90 days after creation"; recorded `expires_at` 2027-01-15 for an order placed 2026-10-19 |
| **Fractional stops and stop-limits cannot be GTC** (they must be resubmitted every session, which needs our host) | sandbox b2 `frac.notional_stop_gtc`, `frac.limit_gtc` | recorded | "stop/stop_limit fractional GTC orders are not enabled"; "fractional orders must be DAY orders" |
| Trailing stops take whole shares only | sandbox b2 `frac.*` | recorded | 422 "fractional orders must be market, limit, stop, or stop_limit orders" |
| Cancelling a bracket's take-profit leg also cancels its stop | sandbox b2 `bracket.sibling_after_tp_cancel`; docs "if one order in a bracket group is canceled, the remaining … are canceled" | recorded + 1 | legs `('limit','canceled'), ('stop','canceled')` |
| Resting exits reserve shares, so an exit must cancel the stop first, which opens a window with no protection | sandbox b2 `sell_side.*` | recorded | `qty_available` fell from 7 to 2 with five exits resting |
| A buy-stop that covers a short must sit above the market | sandbox b2 `stops.stop_buy_below_market` | recorded | "stop price must be greater than current price" |
| A stop can be elected by the reopen print after a halt, gap through, and still not fill | Alpaca forum, staff answer (ORPH, 2021-06-11) | 3 (vendor staff, anecdote) | stop at 69; "re-opened from a halt at 63.00. This print elected your stop order"; "market orders to sell have priority over … elected stop orders" |
| LULD bands (5%/10%/20%, doubled at open and close) and 5-minute pauses run only in regular hours; a stock market halt ladder sits above them | SEC investor bulletin on circuit breakers | 1 | single-stock pause after 15 s outside the band, "five-minute trading pause"; S&P 500 7%/13% halts for 15 min before 3:25 pm, 20% for the rest of the day |
| Account controls: `suspend_trade` blocks new orders, `no_shorting` makes the account long-only, `max_margin_multiplier` can be 1, 2 or 4 | Alpaca API ref, PATCH account configurations | 1 | "If true, new orders are blocked."; "If true, account becomes long-only mode." |
| One call flattens everything | Alpaca API ref, DELETE /v2/positions | 1 | "Closes (liquidates) all of the account's open long and short positions"; `cancel_orders` cancels open orders first |
| An existing short is **not** force-closed when it becomes hard-to-borrow; its fee may rise | Alpaca forum, staff (2023-10-05); repo report 2026-10-10 beyond-long-only | 3 (vendor staff) | "Alpaca doesn't close an existing short position if it becomes not easy_to_borrow but the borrow rates may increase" |
| A hard-to-borrow short needs a single-use locate; availability "can change quickly" | Alpaca blog, HTB locates (2026-06-24); margin doc | 1 (vendor) | "Active locates are single use" |
| Short maintenance margin is the greater of $5/share or 30% (≥$5 names) and $2.50/share or 100% (<$5); margin is checked at end of day; Alpaca may liquidate without notice | Alpaca, Margin and Short Selling | 1 | "You are not entitled to an extension of time on a margin call"; may "sell any securities in your account, without prior notice" |
| Shorting needs ≥$2,000 equity | same | 1 | "you must have $2,000 or more account equity" |
| Alpaca itself goes down (class C is real) | status.alpaca.markets incidents | 1 (vendor) | e.g. 2026-05-22 partial Trading API outage, about 5 min; 2026-08-31 5xx on order creation, about 3 h 16 m (third-party tracker figure); 2026-07-31 overnight trading paused |

### Q2. Sizing against gap risk on a short book
| Claim | Source | Tier | Key figure |
|---|---|---|---|
| A takeover is the standard upward gap for a short | Bargeron, Schlingemann, Stulz, Zutter, NBER w13061 / JFE 2008 | 1 | 3-day target gain **31.74%** with a public bidder; 22.20% private; 20.47% private equity |
| The squeeze tail is not bounded by "normal" volatility | SEC Staff Report on Early-2021 Market Conditions (2021-10-18), as summarised by trade press | 1 via 2 | GME short interest **109%** of float (2020-12-31); price up about **1,600%** in January 2021. Exact pages are UNVERIFIED (the PDF did not render). Staff found the move was driven mainly by buying, not only by the squeeze |
| Short-selling risk (fee variance and recall) is priced and limits shorts | Engelberg, Reed, Ringgenberg, JF 73(2) 2018, 755–786 | 1 | stocks with more short-selling risk have lower returns and less shorting; recalls and fee jumps "reflect the same underlying event". Figures taken from the abstract, not the full text |
| Practitioners shrink single-name shorts against squeezes | Hedgeweek, 2023-08-03 (Third Point letter) | 2 | cut single-name shorts in favour of "market hedges and short baskets", "limiting our vulnerability to short squeezes" |
| Our own data | repo report 2026-10-20 long-short spike | internal | worst short-leg month **+26.2%**; one name marked **1.955×** in a month; the stock short leg cost 1.58 pp/yr against a SPY hedge |
| Stop-losses lower expected return under a random walk and add value only with momentum | Kaminski & Lo, J. Financial Markets 18 (2014) 234–254 | 1 | "always decrease" expected return under the random walk hypothesis (abstract) |

The arithmetic follows from these sources (it is not a published rule). The worst-case loss on a name is its weight × its worst gap, and a stop does not help because gaps happen outside regular hours. At a 1% short weight, a +32% takeover gap costs 0.32% of equity, a doubling costs 1%, and a GME-like +1,600% costs 16%. The cap that matters is therefore **weight × a tail multiple chosen by the owner**, set per book from backtested worst gaps in our own store (point-in-time, delisted names included). I found no Tier 1 or Tier 2 source that gives a standard number such as "1% per short".

### Q3. Independent watchdogs
| Claim | Source | Tier | Key figure |
|---|---|---|---|
| Venue dead-man's switches cancel **orders**, not positions | BitMEX API `cancelAllAfter`; Deribit `enable_cancel_on_disconnect` | 1 (vendor docs, crypto) | the example refreshes every 15 s with a 60 s timeout; orders "automatically cancelled when the connection closes" |
| Cancel-on-disconnect complements kill switches, and kill switches are a last resort | FIA response to the CFTC concept release (2013); FIA Best Practices (2024-07-18) | 2 | kill switches "not a panacea". The 2024 text on heartbeats is UNVERIFIED (not retrieved) |
| Alpaca offers neither | 1 search, nothing found; the 2019 bulk-cancel endpoint is manual | — | UNVERIFIED (absence of evidence) |
| One API key pair per account; regenerating a key kills the old one | Alpaca Learn, key security (2026-09-14) | 1 (vendor) | "Generating new keys invalidates the existing key pair." A separate scoped key is not offered; an OAuth token as a second credential is UNVERIFIED |
| Runaway software beats humans; alerts get ignored | SEC press release 2013-222 (Knight Capital) | 1 | 45 min, 4 million orders for 212 customer orders, $460 million lost; **97 automated emails** before the open were not acted on |
| False positives are a documented failure of automated kill switches | SEC comment letter, file 4-652 | 1 (comment) | warns of "the potentially destabilizing impact of false positives" |

**Failure modes of an off-host watchdog with Alpaca** (my synthesis of the rows above):
- (i) It shares class C. If Alpaca is down, the watchdog cannot act either.
- (ii) It shares the credential. If the key leaks or is rotated, both stop at once.
- (iii) A blind "cancel all" removes our protective stops, which is the opposite of what is wanted.
- (iv) It can fire falsely on its own network partition and flatten a healthy book.
- (v) It can split-brain: the Mac and the watchdog both act, double-sell, and are then refused by `qty_available` (403 40310000).
- (vi) Its actions are fills that the next run's reconciliation must expect, or the run halts (the open item in #2157).
- (vii) Its alert reaches a person who is not there (Knight).

## Disconfirmation
- **Searches run:**
  - Do stop-losses help? Kaminski & Lo (2014), and Osler, NY Fed SR 150 (2002): stop orders cluster and *cause* fast cascades in FX, significant over hours, not over days.
  - Do stops fill after gaps and halts? ORPH staff answer.
  - Does Alpaca force-cover HTB shorts?
  - Watchdog and kill-switch false positives.
  - Does Alpaca have cancel-on-disconnect?
  - Is the GME squeeze the cause of the move? The SEC staff said mainly buying; a Columbia academic critique disputes that.
- **Found against the layers:**
  - Stops lower expected return on a random walk, and they fill badly in exactly the events they are meant for (gaps, halts).
  - Crowded stops can feed cascades (FX evidence, Tier 1, not equity-specific).
  - Automated kill switches have false positives.
  - No evidence was found that a stop protects an overnight gap; the sandbox shows it cannot rest in extended hours.
- **Not found:** a failed replication of the takeover-premium magnitude. I did not search post-2008 premium decay, so it is UNVERIFIED that about 30% still holds.

## Recommendation (the owner decides; this is not evidence)
Principle check from #2157: only sizing survives class C and D. Everything else is a layer on top.

**(a) Faster long book**
| Class | Minimal layer |
|---|---|
| A software | Existing `risk.*` entry caps, plus a per-run order-count and notional throttle. After the kill switch fires, a reduce-only mode: entries are cancelled, stops stay, exits are allowed. Never "cancel all". |
| B host down | A broker-held **GTC stop on whole-share positions**, renewed before day 90. The fractional remainder cannot hold a GTC stop, so either trade whole shares only in this book or let sizing cover that remainder. An off-Mac watchdog (a scheduled cloud function on a second network) that checks a heartbeat and whether every position has a live stop. Its only autonomous actions are to re-place a missing stop or flatten one book (no LLM), and every action is journaled. |
| C broker down | Sizing only. Gross exposure × a multi-hour adverse move must stay within the loss budget. Alert the owner off-Mac (#2155). |
| D gaps | Size on backtested worst overnight gap per name (not the expected move). Consider ending each day flat, since stops cannot protect extended hours. |
| E people | The watchdog acts without the owner. Runbook. An alert that escalates if unacknowledged. |

**(b) Paper short book**
| Class | Minimal layer |
|---|---|
| All | **A hard per-name short cap set from the tail**: weight × an owner-chosen gap (for example +100%) ≤ the owner's per-name loss budget. A gross short cap. Prefer baskets or an index short over single names (our spike and Third Point point the same way). Exclude non-ETB names and names with a pending deal (UNVERIFIED that a feed for deals exists). |
| A, B, E | A broker-held **GTC buy-stop (market, not stop-limit)** above each short, whole shares, renewed before day 90. A stop-limit can trigger and then never fill on a squeeze. The same watchdog as (a). |
| C, D | Sizing only. Keep a buffer over `maintenance_margin`, since Alpaca may liquidate without notice. Paper probably does not simulate margin calls, recalls or HTB fees (UNVERIFIED), so the paper book cannot test these failures. |

## Caveats & gaps
- Paper behaviour is not live behaviour. Halts, LULD, recalls, buy-ins and margin liquidation were not observed on paper.
- **Going live:** shorting needs a margin account and ≥$2,000 equity. The owner chose a taxable cash account of about $100 for live (#813), so as planned the short book cannot go live. This is a fact, not a recommendation.
- Whether Alpaca elects stops in its own system or at the venue is UNVERIFIED. If Alpaca elects them, a resting stop does not protect during an Alpaca outage (class C).
- The halt behaviour comes from one 2021 forum case.

## UNVERIFIED items
- Whether `suspend_trade` also blocks closing orders.
- Whether `max_margin_multiplier`=1 limits shorts.
- An OAuth token as a second credential.
- Whether Alpaca offers a cancel-on-disconnect feature.
- The exact GME figures and pages in the SEC report.
- The FIA 2024 text on heartbeats.
- Takeover premiums after 2008.
- What Alpaca paper does with halts and margin calls.

## Follow-up questions (not answered here)
- How should reconciliation classify broker-fired stops and watchdog fills so the next run does not halt?
- Where should the watchdog run, and how is its secret stored?
- A brief on SEC 15c3-5 and the FIA 2024 best practices in full, as #2157 asks.
- Which venue holds and elects Alpaca stops?
- How does a short book get a live account (margin account, $2,000 minimum)?

## Sources
- Alpaca: [Orders at Alpaca](https://docs.alpaca.markets/docs/orders-at-alpaca); [Margin and Short Selling](https://docs.alpaca.markets/docs/margin-and-short-selling); [Account configurations](https://docs.alpaca.markets/reference/patchaccountconfig-1); [Close all positions](https://docs.alpaca.markets/reference/deleteallopenpositions-1); [HTB locates blog](https://alpaca.markets/blog/htb-trading-api-locates/); [API key security](https://alpaca.markets/learn/api-key-security-best-practices-for-alpaca-builders); [forum: HTB shorts](https://forum.alpaca.markets/t/shortable-stock-vs-easy-to-borrow/3824); [forum: stop after halt](https://forum.alpaca.markets/t/sell-stop-order-was-not-filled-after-halt/5940); [status incidents](https://status.alpaca.markets/incidents/d5kj1t9g42qc), [incident tracker](https://incidenthub.cloud/status/alpaca/live-trading-api-orders-api)
- Repo: [sandbox probe](2026-10-19-alpaca-sandbox-probe.md), [after-hours](2026-10-19-alpaca-sandbox-after-hours.md), [long-short spike](2026-10-20-long-short-spike.md), [beyond long-only](2026-10-10-beyond-long-only.md), [paper facts](2026-10-08-alpaca-paper-facts.md)
- Regulators: [SEC circuit-breaker bulletin](https://www.sec.gov/investor/alerts/circuitbreakersbulletin.htm); [SEC 2013-222 Knight](https://www.sec.gov/newsroom/press-releases/2013-222); [SEC 2021-212 GME report](https://www.sec.gov/news/press-release/2021-212); [SEC comment 4-652](https://sec.gov/comments/4-652/4652-34.pdf)
- Academic: [Bargeron et al., NBER w13061](https://www.nber.org/papers/w13061); [Engelberg, Reed, Ringgenberg](https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2014/03/riggenberg.pdf); [Kaminski & Lo](https://dspace.mit.edu/entities/publication/bb69ca4b-0cdc-487f-831d-63b2e84fafee); [Osler SR 150](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr150.html)
- Practitioner and press: [Hedgeweek, Third Point](https://www.hedgeweek.com/2023/08/03/321541/activist-third-point-reduces-single-name-short-positions); [FIA best practices 2024](https://www.fia.org/fia/articles/fia-releases-best-practices-automated-trading-risk-controls-and-system-safeguards); [Columbia critique](https://clsbluesky.law.columbia.edu/2022/02/22/an-academic-critique-of-the-secs-gamestop-report/); [The TRADE on the SEC report](https://www.thetradenews.com/trading-in-meme-stocks-tested-the-resiliency-and-capacity-of-markets-sec-report-concludes/)
- Venue dead-man's switches: [BitMEX cancelAllAfter](https://docs.bitmex.com/api-explorer/cancel-all-after-order); [Deribit cancel-on-disconnect](https://docs.deribit.com/api-reference/session-management/private-enable_cancel_on_disconnect.md)
