# Personal Trading Research System — Context & Research Handoff

**Prepared:** September 24, 2026
**Purpose:** Full context from a planning conversation, for handoff to a build agent. This document records what was discussed, what the research found, and what is still open. **It does not choose a build direction.** Every design and strategy decision is left to the owner and the build agent (see Section 12).

**Disclaimer:** This is research and decision-support material for a personal learning project. It is not financial, tax, or legal advice.

---

## 1. How to use this document

- Sections 2–5 record the owner's stated goals and constraints, the concepts that shaped the discussion, and the components and inputs that were *discussed as possibilities*.
- Sections 6–9 hold the research findings, with evidence grades and sources.
- Section 10 lists risks and considerations raised.
- Section 11 lists known gaps, source-quality caveats, and research that did not finish.
- Section 12 lists the open decisions. They are framed as questions, not answers.
- Items labeled **"discussed"** were ideas raised in conversation, not decisions.

---

## 2. Owner context and stated constraints

These come from the owner during the conversation:

- The owner is not an experienced trader. The project is exploratory, and they are still surveying the landscape.
- The system would run **locally, for personal use only**.
- The owner was interested in **paper trading** plus a **small live account (~$100)**, possibly using **fractional shares**.
- The owner wants **all trade and decision data logged** so the system can be evaluated and tuned.
- **Excluded by owner decision:** the utility sector as a strategy target. The owner doesn't want to navigate what they can and can't trade in that space.
- The owner is interested in a **hybrid**: a quantitative strategy combined with external data (fundamentals, social/trend data, options and volume data, and similar), with an LLM helping make informed decisions.
- The owner wants research to be **grounded, tightly scoped, and graded by explicit evidence thresholds**, and has had research agents "run wild" before.
- The owner asked for consideration of **recent models** (Claude Fable/Mythos, GPT-6 Astra) and **open-weight models**.

---

## 3. Core concepts that shaped the discussion

These are summarized for the build agent's orientation.

**Edge vs. tools.** An LLM connected to a broker (for example via MCP) does not by itself give a trading edge; everyone has access to the same models. What changed is the cost of *building* infrastructure: data pipelines, backtesters, broker integrations. Any edge comes from the strategy, the data, or the discipline, not from the model.

**Latency.** LLM inference takes seconds and retail order routing adds more. Speed-based strategies (reacting to news or filings) are dominated by firms operating in microseconds to milliseconds. When a public filing posts, most of the price reaction happens almost immediately.

**Multiple testing / data mining.** If you test many strategies, some will look good by luck (the "coin flippers" analogy). Faster idea generation makes this worse. Discussed safeguards:
- form the hypothesis before looking at the data, with an economic reason behind it
- keep a locked holdout dataset that is tested once
- count every trial
- penalize results for the number of attempts (e.g., deflated Sharpe ratio)
- model costs realistically

**Paper trading's role.** Paper trading runs in real time, so it generates little data. It was discussed as a final validation step (does live behavior match the backtest? does anything break?), not as the search method. Paper fills are usually optimistic.

**Small edges are hard to prove.** A 1%/year excess return is statistically very hard to tell apart from luck. Depending on volatility relative to the benchmark, it can take decades to confirm. Fixed costs (data, APIs) can also exceed small-dollar gains.

**Expected value.** Short-term trading without an edge has negative expected value after costs. Broad-market index ownership has had positive expected value historically. The discussion used that distinction to separate gambling from investing.

**Profit ≠ success.** A strategy with no edge can make money for months. Risk management limits losses but doesn't create positive expectancy. Switching strategies after drawdowns tends to mean abandoning them near lows and adopting others near highs. Rules set *in advance* can be tested; discretionary pivots can't.

**Informed vs. profitable.** Better-informed, more disciplined decisions than the typical retail trader were described as achievable, and mostly as a defensive benefit: avoiding overtrading, concentration, and chasing. Beating the market is a separate, harder goal, because prices are set largely by institutions and market makers, not other retail traders.

**Fast vs. slow information.** Clear, widely watched information (earnings numbers, big headlines, filings on large companies) gets priced quickly. Ambiguous, judgment-heavy information (is a product trend real and material?) can be priced gradually. The discussion framed any potential retail edge as being in judgment about slow, messy information, not in speed.

**Forced selling.** A seller who *must* sell (e.g., a leveraged fund facing margin calls) can push prices below fundamental value, independent of the company's business. The case discussed was the Situational Awareness fund (reported in the news during the conversation: ~$45B peak in July 2026, heavy leverage, forced sale of public holdings to Citadel at a discount). Commentators linked that selling to Intel falling after strong earnings. The research later graded the systematic fire-sale reversal effect as MIXED (Section 6, H7).

**What market data reveals:**
- **Price:** the market's current consensus estimate of value.
- **Volume:** intensity and conviction of activity. It isn't directional by itself, since every sale has a buyer.
- **Options:** implied volatility shows the expected *size* of a move; put/call activity and skew show fear or positioning; unusual options activity sometimes precedes news.

**Price-based strategy families discussed:**
- **Mean reversion:** buy stretched-down prices expecting a snap back. The risk is "catching a falling knife."
- **Momentum:** winners keep winning over months.
- **Trend following:** stay in while above a long moving average.
- **Pairs trading:** trade the gap between two related stocks.

Short-term reversal and medium-term momentum coexist because they operate on different horizons.

**Stock buzz vs. product buzz.** Social chatter about *stocks* tends to follow price moves, and extreme retail attention has been followed by negative returns. Chatter about *products* was discussed as a potentially earlier demand signal, especially for smaller companies where one product is material. The research graded the product-search signal INSUFFICIENT (Section 6, H6).

**Forecasting vs. market edge.** AI forecasting has improved rapidly, but matching expert forecasters approximates market consensus rather than beating it. Research found liquid markets still outperform AI forecasters on market questions.

**Movies referenced:** *Margin Call* (2011) is fictional but modeled on the 2008 financial crisis. Its themes are a risk model breaking when history stops applying, and a firm selling toxic assets first before others figure it out. *The Big Short* was mentioned as the real-events companion.

---

## 4. System components discussed (not decided)

The conversation sketched a local architecture. It's listed here as a **catalog of components and considerations that came up**, not a chosen design.

| Component | What was discussed | Considerations raised |
|---|---|---|
| Scheduler | Run jobs on a schedule (daily/weekly/monthly); OS schedulers mentioned (cron/launchd/Task Scheduler) | Machine must be on at run time; market calendar and holidays |
| Data ingestion | Pull prices, filings, economic data, trends/social data | Source reliability; terms of service; stale-data handling |
| Local storage | Local single-file databases and columnar files were mentioned (e.g., DuckDB/SQLite, Parquet) | Store **when each data point became known** (point-in-time); include delisted securities; adjusted prices |
| Signal / quant screen | Code computes numeric signals and produces a shortlist | LLMs are unreliable at math, so numbers should be computed by code |
| Backtester | Historical simulation of strategies | Costs, holdout, trial logging, look-ahead and survivorship bias |
| LLM analyst layer | Reads a fixed input packet; writes a structured memo (e.g., bull/bear/judge, probability, invalidation criteria); veto-only authority was discussed | Nondeterminism; contamination; prompt injection from scraped text; model version pinning; cost |
| Risk rules | Fixed sizing, exposure limits, kill switch | Portfolio-level correlation, not just per-position size |
| Execution | Broker API; paper account first, then small live account | Keep order placement out of LLM control; duplicate-order protection; reconciliation |
| Journal / scoring | Log every signal, memo, order, fill, outcome, override | Calibration (Brier score), benchmark comparison, versioning |
| Dashboard / alerts | Local dashboard and summaries were mentioned | Optional |

**Tuning data discussed:** what logged trade data could reveal, per component:
1. Probability calibration: do "60%" calls hit about 60% of the time?
2. Which signals separate winners from losers (requires logging every signal value at decision time).
3. Loss attribution: thesis wrong, timing wrong, execution cost, or risk rules.
4. Execution costs: expected vs. actual fill.
5. Risk-rule behavior: stops protecting vs. selling at bottoms.
6. LLM consistency across repeated identical inputs.
7. Whether the full system beats a simpler version and an index benchmark.

A caution was raised: with around 100 trades, most P&L variation is noise. Tuning on outcomes risks live overfitting; tuning on process issues (calibration, costs, bugs, consistency) was contrasted with that.

---

## 5. Input categories discussed

| # | Category | Examples | Typical cost | Processed by (as discussed) |
|---|---|---|---|---|
| 1 | Price & volume | Momentum, moving-average distance, volatility, abnormal volume, earnings gaps | Free/cheap | Code |
| 2 | Fundamentals | Revenue/earnings growth, margins, earnings surprise, guidance, valuation, debt | Free (SEC) | Code + LLM |
| 3 | Company text | Earnings call transcripts, 10-K/10-Q, footnotes, press releases, tone changes | Free | LLM |
| 4 | Ownership & flows | Form 4 insider trades, 13F holdings, 13D activist stakes, short interest | Mostly free (SEC) | Code |
| 5 | Options | Implied volatility, put/call activity, skew, unusual activity | Usually paid | Code |
| 6 | News & analysts | Headlines, estimate revisions, rating changes | Mixed | LLM |
| 7 | Alternative / social | Google Trends, Reddit/Stocktwits mentions, product trends, web/app traffic | Free to expensive | Code + LLM |
| 8 | Market context | Rates, market volatility, sector performance, market trend (e.g., FRED data) | Free | Code |
| 9 | Industry-specific | Domain filings or data (utility dockets were discussed, then **excluded** by the owner) | Varies | LLM |
| 10 | Event calendar | Earnings dates, index changes, lockup expirations | Free | Code |

**Social-data pipeline stages discussed:**
1. Collect.
2. Clean: dedupe, remove bots/spam, count distinct accounts.
3. Link product → brand → parent company → ticker → revenue materiality.
4. Measure: velocity, acceleration, breadth, sentiment, product-vs-stock chatter.
5. Timestamp.
6. Hand off numbers to the screen and a summary to the LLM packet.

Constraints raised:
- Platform terms of service restrict scraping, and access rules change.
- Historical social data is largely unavailable (Reddit's Pushshift was restricted in 2023; TikTok offers little history), which blocks backtesting.
- Collecting your own timestamped data early was noted as the only way to build history.

---
## 6. Research Report 1: Strategy evidence (graded)

### 6.1 Evidence protocol used

- **Tier 1:** peer-reviewed journals; academic working papers (SSRN/NBER/arXiv); replication studies; official primary sources (SEC, FINRA, IRS, exchanges, broker docs).
- **Tier 2:** reputable practitioner research (AQR, Alpha Architect, Research Affiliates, etc.) and established financial press.
- **Tier 3:** blogs, forums, GitHub READMEs, vendor marketing. Allowed only for tooling status, never as evidence of returns.
- **SUPPORTED:** at least 3 independent Tier 1/2 sources (at least 2 Tier 1), at least 1 out-of-sample or post-publication test, and at least 1 addressing costs.
- **MIXED:** conflicting evidence, only pre-2005 evidence, effect confined to microcaps, or costs consume the effect.
- **NOT SUPPORTED:** credible replications show the effect is gone.
- **INSUFFICIENT:** fewer than 2 qualifying sources.
- A disconfirmation search was required per hypothesis. **It was not run for H2, H4, H6, and H9** (see Section 11).

Scope: US equities, low frequency, long-only, no leverage or options, utilities excluded.

### 6.2 Verdict summary

| # | Hypothesis | Verdict |
|---|---|---|
| H1 | Cross-sectional momentum (12-1 month, monthly) | **SUPPORTED** |
| H2 | Clustered insider purchases (Form 4) | **MIXED** |
| H3 | Post-earnings announcement drift (PEAD) | **NOT SUPPORTED** (investable, non-microcap universe) |
| H4 | Market trend filter (10-month / 200-day moving average) | **MIXED** |
| H5 | Retail attention spikes followed by reversal | **MIXED** |
| H6 | Product-level Google Trends / product buzz | **INSUFFICIENT** |
| H7 | Forced selling / fire-sale reversals | **MIXED** |
| H8 | LLM text analysis adds predictive value | **INSUFFICIENT** |
| H9 | Combining multiple signals | **INSUFFICIENT** |

### 6.3 Findings by hypothesis

**H1 — Momentum (SUPPORTED)**
- Daniel & Moskowitz (Journal of Financial Economics, 2016):
  - Momentum has strong average returns across asset classes.
  - It suffers infrequent, severe crashes in "panic states" after market declines with high volatility, coinciding with market rebounds.
  - The static long-short winners-minus-losers portfolio lost −88.48% (Jul–Aug 1932) and −45.60% (Mar–Apr 2009).
  - A dynamic, volatility-aware version roughly doubled the Sharpe ratio.
- Chen & Velikov: size, value, and momentum "performed well post-publication net of trading costs." This is consistent with Frazzini, Israel & Moskowitz (2015).
- Novy-Marx & Velikov (citing Chordia et al.): expected returns to prominent anomalies fell by about half after decimalization.
- **Gap:** no clean post-2010, long-only, net-of-cost US magnitude from a Tier 1 source.
- **Retail benchmark data point:** iShares MSCI USA Momentum Factor ETF (MTUM), per the iShares fact sheet as of June 30, 2026:
  - 17.58% annualized NAV return over 10 years; 16.79% since April 2013 inception.
  - Calendar-year returns: 2022 −18.23%, 2023 9.10%, 2024 32.88%, 2025 22.10%.
  - Expense ratio 0.15%; 126 holdings; 3-year beta 1.22; 53.22% Information Technology.
  - A Tier 3 comparison source put MTUM's 10-year return at ~16.23%/yr vs SPY's ~14.98%/yr, with 0.86 correlation (indicative only).
  - Observation from the report: MTUM's outperformance is partly a tech and beta exposure.

**H2 — Insider purchase clusters (MIXED)**
- Lakonishok & Lee (2001): purchases are more informative than sales (1975–1995), concentrated in smaller firms.
- Jeng, Metrick & Zeckhauser (2003): the insider purchase portfolio earned more than 50 bp/month abnormal returns (1975–1996). About a quarter of that accrued within 5 days and half within the first month. This measures insiders' own trades, not an outsider trading after the filing.
- Cohen, Malloy & Pomorski (2012): "opportunistic" (non-routine) trades carry the signal. Their classifier needs several years of each insider's history.
- The widely repeated "5.2% six-month alpha" and "7.4% cluster" figures appear only in vendor/blog summaries and are **unverified**.
- Recent academic work (arXiv 2602.06198) focuses on microcaps.
- **Gap:** no post-2010, filing-date-timed, net-of-cost, non-microcap Tier 1 test was found. Disconfirmation search not run.

**H3 — PEAD (NOT SUPPORTED outside microcaps)**
- Martineau (Critical Finance Review, 2022): drift began disappearing from non-microcap stocks in 2001 and was gone by 2006. It disappeared only recently in microcaps.
- Subrahmanyam (SSRN 5930255), Feb 2001–Dec 2024: t = 2.18 including microcaps, t = 1.43 excluding them (not significant). He attributes papers claiming PEAD is alive to keeping microcaps (defined as the bottom 20% by NYSE breakpoints, about 3% of market value).
- Counterpoint: Hirshleifer–Peng–Wang (Review of Financial Studies, 2025) find strong drift, but without filtering microcaps.

**H4 — Market trend filter (MIXED)**
- Faber (SSRN 962461): timing-model drawdown 16.52% vs 44.73% buy-and-hold in one test window. His multi-asset 10-month SMA update (1973–2012) shows 10.5% vs 9.9% annualized, gross.
- Faber cites Siegel's 1886–2006 DJIA 200-day test as improving absolute and risk-adjusted returns.
- The out-of-sample evidence is the author's own real-time update, so it is not independent.
- Contrary evidence (Tier 3 only): one post-March-2009 backtest showed 8.5% vs 12.8% CAGR for buy-and-hold, and a claim that only about 28% of crossover trades since 1960 were winners.
- Observed pattern: it reduces crash depth but lags in bull markets and whipsaws in choppy markets. The benefit is concentrated in a few bear markets.
- **Gap:** independent Tier 1 evaluation and disconfirmation not done.

**H5 — Retail attention reversal (MIXED)**
- Barber, Huang, Odean & Schwarz (Journal of Finance, 2022): intense Robinhood herding forecasts negative returns, averaging −4.7% abnormal return over 20 days for top-purchased stocks (May 2018–Aug 2020). They also document heavier short selling in herding stocks.
- Da, Engelberg & Gao (Journal of Finance, 2011): search-volume spikes predict higher short-run prices with reversal within a year.
- Contrary: Welch (Journal of Finance, 2022) finds the *aggregate* Robinhood crowd portfolio had good timing and alpha. The two findings are reconcilable, since extreme daily spikes differ from broad holdings.
- **Data constraint:** the Robintrack feed ended August 13, 2020, and any substitute proxy is unvalidated.

**H6 — Google Trends / product buzz (INSUFFICIENT)**
- Da, Engelberg & Gao ("In Search of Earnings Predictability"): product search volume strongly nowcasts revenue surprises. A later accounting study uses product searches to detect revenue management.
- No post-publication return test or cost analysis was found. Disconfirmation not run. Data access is fragile (Section 7).

**H7 — Fire-sale reversals (MIXED)**
- Coval & Stafford (Journal of Financial Economics, 2007): extreme mutual-fund-outflow stocks fell −18.13% (t−2 to t+3), then reversed +15.01% over three quarters.
- Wardlaw (Journal of Finance, 2020): the standard flow-pressure measure is mechanically tied to realized returns. After correcting that, the decline is "fairly negligible" with "no subsequent reversal."
- Form 13F is filed within 45 days of quarter end (Investor.gov/SEC), too late for real-time detection by a retail investor.

**H8 — LLM text layer (INSUFFICIENT)**
- Glasserman & Lin: in-sample results are distorted by look-ahead and a "distraction effect," strongest for large companies. Anonymization helps partially.
- Gao, Jiang & Yan (arXiv 2512.23847): the Lookahead Propensity measure is positive in-sample and "collapses essentially to zero" after the training cutoff. Predictive power loses significance post-cutoff.
- ForecastBench (Oct 2025): superforecasters scored 0.081 difficulty-adjusted Brier vs 0.101 for the best LLM (GPT-4.5).
- No qualifying study tested an LLM specifically as a *veto layer on top of quant signals*.

**H9 — Combining signals (INSUFFICIENT)**
- Chen & Velikov: choosing weights and spreads *in-sample* raised average anomaly net returns from 5 bp to 38 bp/month. This illustrates how selection inflates results.
- Only one source; disconfirmation not run.

---

## 7. Research Report 1: Design and implementation findings

### D1 — Backtesting methodology
- **Deflated Sharpe Ratio** (Bailey & López de Prado): corrects for selection bias under multiple testing and non-normal returns.
- **Probability of Backtest Overfitting** via combinatorially symmetric cross-validation (Bailey, Borwein, López de Prado, Zhu). They note simple hold-out methods can be unreliable for investment backtests.
- Practices cited: complete the research design before backtesting, and keep count of every backtest run.
- Rule-of-thumb from Sharpe-ratio sampling error (report's own inference): t ≈ SR × √years.
  - Confirming an annualized Sharpe of 0.5 vs zero at t ≈ 2 takes about 16 years.
  - An *excess* Sharpe of 0.3 over a benchmark takes about 44 years.
- Survivorship and look-ahead: store knowledge dates (filing/acceptance timestamps) and include delisted names.

### D2 — Retail execution (2025–2026)
- **Alpaca fractional orders** (official docs):
  - Market, limit, stop, and stop-limit order types, with **DAY time-in-force only**.
  - No fractional short sales.
  - Expected fill is the NBBO at submission, with no price improvement on fractional orders.
- Community reports (Tier 3) say bracket/OCO orders aren't available for fractional or notional orders. A QuantConnect forum report (Tier 3) says its paper brokerage doesn't support fractional equity orders with Alpaca.
- **Robinhood Agentic Trading:**
  - Launched in beta May 27, 2026.
  - External agents connect via an MCP endpoint to a dedicated, ring-fenced agentic account (one per customer).
  - Every trade triggers a notification, and agents can be disconnected.
  - Stocks were supported at launch, with options, crypto, and futures announced. Check current status.
  - Robinhood's disclosure warns of possible loss of the entire investment.
  - Robinhood's Q2 2026 results reported nearly 100,000 agentic accounts and over $100 million in assets under custody.
- **Cost assumptions** (report inference; no primary slippage study found): at least 5–10 bp per side plus half the spread for liquid large/mid caps, and far more for small caps. Paper fills are optimistic.
- Payment for order flow and actual fractional fill quality were **not researched**.

### D3 — Data availability
- **Google Trends:**
  - Official API entered **alpha on July 24, 2025** with application-gated access.
  - **pytrends was archived April 17, 2025.** Some methods reportedly still work; others return errors (Tier 3).
  - Manual CSV export from the website remains available.
- **SEC EDGAR:** free, authoritative source for Form 4, 13F, and XBRL financials. Form 4 must be filed within 2 business days of the transaction (Sarbanes-Oxley).
- **Not verified:** a free/low-cost survivorship-bias-free adjusted price source, earnings estimate data, GDELT terms, and Reddit API terms. Assume free price APIs are *not* survivorship-bias-free unless they document delisted coverage.

### D4 — Open-source tooling status (Tier 3 sources, approximate, as of Sept 2026)

| Tool | Purpose | Status noted |
|---|---|---|
| vectorbt (1.1.0) | Vectorized backtesting | Active; Apache 2.0 + Commons Clause; PRO is a separate paid product |
| Backtrader (1.9.78.123) | Event-driven backtest/live | **Dormant** upstream (README from 2018); forks exist |
| QuantConnect LEAN | Backtest + live engine (C#/Python) | Active (updated May 2026); Apache 2.0; heavy |
| NautilusTrader (1.231.0 / 2.0.0rc2) | Rust-native event-driven engine | Very active; v2 RCs not recommended for live money |
| Microsoft Qlib (pyqlib 0.9.7) | ML quant research platform, point-in-time data design | Probably active; MIT |
| OpenBB (4.7.2) | Financial data platform | Active; **AGPL-3.0** (check licensing) |
| TradingAgents (TauricResearch, ~v0.4.0, unverified) | Multi-agent LLM trading framework (analysts, bull/bear debate, trader, risk, portfolio manager) | Active; Apache-2.0; research-only disclaimer; supports Anthropic models |
| edgartools (5.58.0) | SEC EDGAR parsing (Form 4, 13F, XBRL) | Very active; MIT; one main maintainer |
| exchange_calendars (4.13.2) | Trading calendars | Active, community-maintained |
| Alpaca MCP server / Robinhood MCP | Broker access for agents | Official servers exist |

**Other tools mentioned in conversation** (status not independently verified):
- Scraping and collection: PRAW (Reddit API), Google's YouTube Data API client, youtube-transcript-api (unofficial), GDELT (open global news data with history), feedparser (RSS), Scrapy, Playwright, Crawl4AI.
- Transcription: Whisper / faster-whisper.
- Dashboard: Streamlit.
- Prototyping only: yfinance (unofficial, occasionally breaks).
- Unofficial platform scrapers for TikTok/X exist, but break often and violate platform terms (e.g., snscrape largely stopped working).

**Cautions raised about open-source trading repos:**
- GitHub stars reflect interest, not profitability.
- LLM-trading repo backtests are subject to training-data contamination.
- Read code before supplying broker keys; prefer original, maintained repos over random forks.

### D5 — Tax and regulatory
- **Pattern Day Trader rule:** the SEC approved FINRA's amendments to Rule 4210 on April 14, 2026, eliminating the PDT designation and the $25,000 minimum. FINRA Regulatory Notice 26-10 set the effective date at June 4, 2026, with phase-in allowed until October 20, 2027.
- **Wash sale rule** (not verified against IRS primary sources in this research; confirm with IRS Publication 550 / IRC §1091): a loss is disallowed if a substantially identical security is bought within 30 days before or after the sale. Automated rebalancing that re-buys recently sold losers can trigger this routinely.
- **Short-term gains** (held one year or less) are taxed at ordinary income rates. Monthly-turnover strategies generate mostly short-term gains.
- No AI-agent-specific FINRA/SEC retail rule was found. Broker terms are the binding constraint.

---
## 8. Research Report 2: LLM models for the analyst layer (frontier, open-weight, point-in-time)

### 8.1 Identification of "Astra"
- **GPT-6 Astra** is OpenAI's flagship LLM, announced September 3, 2026, with the `gpt-6-astra` API model released September 4.
- Pricing: $10 input / $1 cached input / $50 output per million tokens.
- Context: about 1.05M tokens; 128K max output.
- Stated knowledge cutoff: April 2026. One reviewer (Tier 3) reports it misreports its own cutoff when asked.
- It is the first OpenAI model rated "Critical" for cybersecurity. Its system card reportedly documents reduced chain-of-thought monitorability (Futurum, Tier 2).

### 8.2 Claude Fable / Mythos background
- Claude Fable 5 and Mythos 5 were released June 9, 2026. Access was suspended June 12, 2026 to comply with US Department of Commerce export controls. The controls were lifted June 30, and access was restored July 1, 2026 (Anthropic statement: https://www.anthropic.com/news/fable-mythos-access).
- The current generation is Fable 5.1 / Mythos 5.1, which share an underlying model. Fable has additional safeguards for biology, cybersecurity, and LLM R&D.
- Fable re-routes certain sensitive requests to other models and tells the user when it does. Research noted this as a hidden model switch for reproducibility-sensitive logging.

### 8.3 Independent and vendor evidence (finance, forecasting, trading)

**Financial document analysis: Vals AI Finance Agent v2** (independent; 450 held-out SEC-filing analyst questions × 3 runs; 68 models; LLM-judged; snapshot 9/22/2026, read via the BenchLM mirror)

| Model | Type | Score (rank) |
|---|---|---|
| Gemini 3.8 Flash | Closed | 61.44% (#1) |
| Muse Spark 1.2 / Muse Spark 1.3 Max | — | 60.6% / 60.0% |
| Claude Fable 5.1 | Closed | 58.9% (#6) |
| Claude Opus 5 | Closed | 58.6% (#7) |
| MiMo-V2.6-Pro | Open-weight | 58.3% (#9) |
| GLM-5.3-Flash | Open-weight | 57.9% (#11) |
| Claude Fable 5 | Closed | 56.3% (#14) |
| GLM-5.3 | Open-weight | 55.8% (#16) |
| GPT-6 Astra (max effort) | Closed | 53.5% (#26) |
| DeepSeek V4.1 Flash | Open-weight | 53.5% |
| Qwen3.8 Max | (licensing unverified) | 50.6% |
| MiniMax M3 | Open-weight | 48.3% |
| Qwen3.8-27B | Open-weight | 48.6% |
| Kimi K2.6 | Open-weight | 44.9% |

- The top models still fail about 40% of analyst tasks. For comparison, the best 2025 model (OpenAI o3) scored 46.8% on Finance Agent v1.
- Leaderboard position changes within weeks. Judge models may bias toward related model families.

**Vendor-published partner claims** (not independent):
- Fable 5.1 vs Fable 5 on Samaya "FrontierFinance": 55.9% vs 49.2%.
- Rogo: Fable 5.1 matches Fable 5 accuracy with 20% fewer tokens.
- Hebbia: best fact recall over financial documents.
- Jane Street: "state of the art on trading intuition."
- Fable 5 topped the Hebbia Finance Benchmark and "aced" IMC's evals.
- GPT-6 Astra on Agents' Last Exam (includes financial modeling): 59.3% vs Claude Opus 5 at 55.5% (vendor-reported).
- Andon Labs reported that Mythos 5 made less money than older models on Vending-Bench, a simulated business P&L (independent, relayed secondhand).

**Forecasting:**
- **ForecastBench, Oct 2025:** superforecasters 0.081 vs best LLM (GPT-4.5) 0.101 difficulty-adjusted Brier.
- **ForecastBench, July 16, 2026 update:** Cassi AI's multi-model ensemble pipeline and xAI and Google DeepMind submissions were "statistically indistinguishable" from superforecasters. FRI describes results as more consistent with parity than outperformance.
- **Open-weight baselines without tools** (ForecastBench dataset, Dec 2025): DeepSeek-R1 0.142, Qwen3-235B 0.152, Kimi-K2 0.178, GLM-4.5-Air 0.182, vs superforecaster median 0.092, public median 0.129, and naive baseline 0.176.
- **Prophet Arena** (ICLR 2026; 1,300+ Kalshi events): GPT-5 Brier 0.184 vs market 0.187; calibration error 0.042 vs 0.069; average return 0.943 (below break-even). Strong models had calibration error ≤0.05 vs 0.05–0.2 for weaker ones.
- **WC2026-Agents** (arXiv 2607.17765; 104 World Cup matches): four frontier agents predicted alike and did not beat the bookmaker.
- **Earlier AI-benchmarking evidence noted in conversation:** in one research team's report, an AI forecaster matched superforecasters and beat less active prediction markets, but the most liquid markets beat it (0.1106 vs 0.1258 Brier, lower is better).
- **Schoenegger, Tetlock et al.** (Science Advances, 2024): a crowd of 12 LLMs was statistically indistinguishable from a human crowd of 925 forecasters on 31 binary questions.

**Trading tests:**
- **Alpha Arena Season 1** (Nof1; Oct 18–Nov 3, 2025; real-money crypto perpetuals; $10K each; single run). Results via secondary relays:
  - Qwen3 Max +22.31%
  - DeepSeek +4.89%
  - Claude Sonnet 4.5 −42.01%
  - Gemini 2.5 Pro −45.55%
  - Grok 4 −57.92%
  - GPT-5 −58.74% (figures conflict across relays)
- **Alpha Arena Season 1.5** (US stocks; Nov 19–Dec 3, 2025; 8 models): Grok 4.20 ("Mystery Model") won with a +12.11% aggregate return. Later reporting in conversation described a multi-model competition in which the combined portfolio lost about a third and only 6 of 32 sessions were profitable; the founder said handing money to an LLM to trade on its own doesn't work yet. No 2026 season had been published as of August 2026.
- **StockBench** (arXiv 2510.02209; DJIA stocks; Mar–Jun 2025, post-cutoff): most models failed to beat buy-and-hold; static question-answering strength didn't transfer.
- **Profit Mirage** (arXiv 2510.07920; GPT-4o-based agents incl. FinMem, FinCON, TradingAgents): Sharpe decay of 51.48–62.23% and total-return decay of 50.18–71.85% after the training cutoff.
- **Chen, Green, Gulen, Zhou** (SSRN / AEA 2026): extrapolation and miscalibration in LLM stock-return distribution forecasts.
- **CLQT benchmark** (arXiv 2606.29771): returns rank poorly against diagnostic measures of reasoning.

### 8.4 Contamination (look-ahead) evidence
- LLM backtests on dates before the model's training cutoff are inflated by memorization.
- Lopez-Lira, Tang & Zhu ("The Memorization Problem," arXiv 2504.14765): instructions to respect historical boundaries don't prevent recall. Masking fails because models reconstruct entities and dates from minimal context.
- **DatedGPT:** biased models show a "lookahead premium" of 26.4 bp per standard deviation.
- **MemGuard-Alpha** (arXiv 2603.26797): in-sample accuracy rises with contamination (40.8% → 52.5%) while out-of-sample accuracy falls (47% → 42%).
- **Lookahead Propensity** (Gao, Jiang, Yan; arXiv 2512.23847): a date-only recall test estimating whether a model has internalized outcomes; it collapses to about zero after the cutoff.
- **FinCAD** (arXiv 2605.24564; inference-time suppression; requires logit access, i.e., open weights): cut in-sample returns up to −67.1% on memorized dates while keeping 2025 out-of-sample Sharpe within 0.10.
- **Profit Mirage / FinLake-Bench counterfactual perturbation:** 82% of FinMem predictions were unchanged when key events were perturbed, which indicates memorization.
- **NumLeak:** frontier LLMs recall public numeric series (e.g., Fama-French factors) with 0.97–0.99 correlation.
- A 2026 review of 164 finance-LLM papers (arXiv 2602.14233) found no single bias discussed in more than 28% of studies.

### 8.5 Point-in-time (chronologically consistent) models

| Family | Size / training | Cutoffs | Availability | Notes |
|---|---|---|---|---|
| ChronoBERT / ChronoGPT (He, Lv, Manela, Wu; arXiv 2502.21206) | ~1B params (GPT-2 style); up to 460B tokens | Annual, 1999–2024 | Open weights (Hugging Face: manelalab) | Next-day news-return long-short Sharpe 4.80 vs Llama 3.1 8B 4.90, i.e. modest look-ahead bias for that task |
| ChronoGPT-Instruct (arXiv 2510.11677) | 70B-token base | 1999–2024 | Open weights | IFEval ~25%; 0 correct post-cutoff event predictions in leakage tests |
| DatedGPT (arXiv 2603.11838) | 12 × 1.3B models | Annual, 2013–2024 | Authors say checkpoints "will" be open-sourced; **unconfirmed** | 61,000 firm-day headlines: lookahead-free Sharpe 3.20 |
| Scaling Point-in-Time LMs (Kelly, Malamud, Schwab, Xu; arXiv 2607.11889) | Up to 4B params; 1T tokens | Monthly, 2013–2024 | Code on GitHub; models on Hugging Face | IFEval 31.3%; news embeddings give positive out-of-sample Sharpe |
| Pitinf (PiT-Inference, commercial) | Not disclosed | Point-in-time | Commercial | Look-Ahead-Bench is run by the same vendor; independence unclear |
| NoLBERT | BERT-class | Time-stamped | Preprint | Encoder only |

**Limitations noted:**
- These models are 1–4B parameters with weak instruction-following (IFEval ~25–31%).
- Their positive results come from simple tasks (headline sentiment, embeddings into a regression), not multi-document reasoning memos.
- Timestamp leakage (e.g., misdated text) remains possible.

### 8.6 Reproducibility and determinism
- Temperature-0 API calls are not bit-reproducible. OpenAI documentation promises only a "best effort" and states determinism is not guaranteed.
- Thinking Machines Lab (Tier 2) traced nondeterminism to inference servers lacking batch invariance: 1,000 identical temperature-0 requests to Qwen3-235B yielded 80 distinct completions.
- Nondeterminism matters most for probabilities in the 0.2–0.8 range.
- Self-hosted open weights with batch-invariant kernels achieved bit-identical outputs, at a 34–62% throughput cost (arXiv 2606.03019).
- Hosted open-weight APIs are as nondeterministic as closed ones.

### 8.7 Frontier vs open-weight vs point-in-time: tradeoffs found

| Dimension | Frontier closed | Open-weight | Point-in-time |
|---|---|---|---|
| Reproducibility / pinning | Weak (nondeterministic; models get repriced or retired; silent re-routing possible) | Strong if self-hosted with batch-invariant kernels; weak if hosted | Strongest (fixed weights) |
| Cutoff verifiability | Vendor-stated only; self-reports unreliable | Vendor-stated; testable with logit access | Verified by construction plus published leakage tests |
| Local feasibility | None | Top models impractical locally; 27B-class needs a high-memory GPU at 4-bit; 4-bit quantization can cut math reasoning by >30% on some models | Trivial (laptop/CPU) |
| Cost per memo | ~$10 / $50 per million tokens (Fable 5, Astra list); ~20K-token packet + 3K output ≈ $0.35 per call | Much cheaper hosted; electricity only if local | ≈ $0 |
| Availability risk | Demonstrated (19-day Fable/Mythos suspension; Mythos still restricted) | Low once downloaded (licenses vary) | Negligible |
| Forecasting-quality evidence | Parity with superforecasters only as engineered ensembles; good calibration; no demonstrated trading edge | Near-frontier on finance document analysis; older open models near naive baseline on ForecastBench; won Alpha Arena S1 (n = 6) | Leak-free Sharpe on simple news tasks; no evidence for memo-style reasoning |

**Capability vs outcomes:** more capable models show better calibration and better document analysis. There is no consistent evidence that capability translates into trading P&L (Alpha Arena rankings inverted between seasons; StockBench; CLQT).

### 8.8 What the research leaves unresolved
- No independent ForecastBench, Prophet Arena, or trading-benchmark results yet for Fable 5.x, Mythos 5.x, or Astra. This is largely timing, since forecasting benchmarks need weeks to months for questions to resolve.
- Official knowledge cutoffs for Fable 5 / 5.1.
- Nof1's primary per-model Season 1 table (accessed only via relays).
- Whether DatedGPT weights have been released.
- Finance-specific quantization evidence; measured hardware needs for current 27B–100B+ open-weight models.
- Claude Opus 5.5 (released around September 22, 2026) had not been assessed.

---

## 9. Other context gathered in conversation

- **Retail AI trading landscape:**
  - People range from giving an agent broker access with "trade for me" instructions, to building rule-based bots with AI coding tools, to AI research and portfolio assistants.
  - Motives discussed: convenience, learning, entertainment, and selling courses or tools.
- **Documented published factor strategies mentioned:** momentum, value, quality, low volatility, trend following, post-earnings drift. Many are available as low-cost ETFs.
- **Replication literature:** large replication studies found many published anomalies fail on retest, and publication tends to shrink returns (McLean & Pontiff; Hou, Xue & Zhang, as referenced in conversation).
- **Day-trader outcomes:** a study of Brazilian day traders found most who persisted lost money (referenced in conversation).
- **Public disclosure timing** (as discussed):
  - 13F up to 45 days after quarter end.
  - 13D within about 5 business days.
  - Form 4 within 2 business days.
  - Congressional trade reports up to 45 days late.
  - ETFs that copy hedge-fund holdings and congressional trades exist, with mixed results.
- **Social listening and alt-data landscape:**
  - Marketing tools: Brandwatch, Sprout Social, Talkwalker, Meltwater.
  - Trend tools: Google Trends, TikTok Creative Center, Exploding Topics.
  - Institutional alternative data vendors: card spend, web traffic, sentiment.
  - Retail chatter trackers: Quiver Quantitative, Stocktwits.
- **The WSJ AI tool** that transcribes and reasons over TikTok content was mentioned by the owner as inspiration. It was not researched.

---

## 10. Risks and considerations raised

**Compliance and employment**
- The owner's employer may have a personal trading or insider-trading policy that applies to any personal trading. Material non-public information learned through work must never be used. The owner excluded the utility sector partly for this reason.

**Portfolio-level risk**
- Concentration and correlation across positions. The Situational Awareness case involved concentrated exposure plus leverage.
- Leverage, margin, and options were discussed as not part of an initial scope.

**Taxes**
- Short-term gains taxed as ordinary income.
- Wash-sale triggers from automated re-buys.
- Account type (taxable vs retirement) changes the tax picture. Verify with a tax professional.

**Data quality**
- Splits, dividends, ticker changes, mergers, and delistings.
- Survivorship bias in free price data.
- Point-in-time timestamps.

**Operational reliability**
- Reconciliation of broker positions against local records.
- Duplicate-order prevention on restarts.
- Market calendar, holidays, half days, and time zones.
- Halting on stale or failed data.
- Kill switch.
- Secrets management (API keys out of code).

**LLM-specific**
- Prompt injection via scraped content.
- Nondeterminism.
- Model version changes and silent re-routing.
- Vendor availability interruptions.
- Cost accumulation.
- Contaminated historical evaluation.
- Persuasive but wrong reasoning.

**Behavioral**
- Changing criteria midstream.
- Overriding the system without logging.
- Scaling capital after a lucky stretch.
- Strategy-hopping after drawdowns.

**Research hygiene**
- Keep a research notebook of every idea tested, including failures, to track the multiple-testing burden.

---

## 11. Known gaps, source-quality caveats, and unfinished research

**Source-quality audit of Report 1** (owner flagged heavy third-party reliance):
- Verdicts resting on Tier 1 sources: momentum, PEAD decay, Robinhood herding reversal, fire-sale correction, LLM contamination, backtest overfitting methods, PDT rule change, Alpaca order rules.
- Items leaning on Tier 3:
  - trend filter's contrary evidence (and its supporting out-of-sample evidence came from the strategy's own author)
  - MTUM vs SPY comparison
  - Alpha Arena per-model results (news relays, not Nof1 primary data)
  - tooling version numbers
  - insider-buying return figures (flagged unverified)
- Protocol breaches: required disconfirmation searches were skipped for H2, H4, H6, and H9. H9 rested on a single source. Wash-sale rules were not verified against the IRS.

**Remediation research not completed.** A targeted follow-up (Tier 1/official sources only, mandatory disconfirmation) was launched twice. **No results were returned in this conversation.** Its scope, still open:
- **G1:** post-2010 US momentum magnitude (long-only, net of costs) plus evidence of weakening.
- **G2:** insider purchases with post-2010, filing-date-timed, non-microcap, net-of-cost tests, plus disconfirmation.
- **G3:** independent (non-Faber) academic evaluations of market trend timing, out-of-sample and net of costs, plus critiques. Candidates named: Zakamulin, Glabadanidis, Moskowitz–Ooi–Pedersen, Hurst–Ooi–Pedersen, Clare et al., Marshall et al.
- **G4:** out-of-sample, net-of-cost evidence on combining signals. Candidates named: Green–Hand–Zhang; DeMiguel–Martin-Utrera–Nogales–Uppal; Harvey–Liu–Zhu; Hou–Xue–Zhang.
- **G5:** disconfirmation for Google search-volume signals.
- **G6:** Alpha Arena results from Nof1 primary sources.
- **G7:** IRS primary sources on wash sales (Pub 550, IRC §1091, Rev. Rul. 2008-5 on IRA interaction) and holding periods.
- **G8:** vendor documentation for survivorship-bias-free, adjusted US price data available to individuals. Candidates named: Norgate, Sharadar/Nasdaq Data Link, CRSP, Tiingo, Polygon/Massive, EODHD, Alpaca.

**Other gaps:**
- Payment for order flow and fractional fill quality.
- Reddit API and GDELT terms.
- Any study of LLMs as a *veto* layer on quant shortlists using post-cutoff data only.

---

## 12. Open decisions for the owner and build agent

These were **not decided** in the conversation.

1. **Objective and success criteria.** What counts as success (learning, calibration, beating a specific benchmark)? Which benchmark(s)? What stop and failure criteria, set in advance?
2. **Strategy scope.** Which hypotheses (if any) to test, in what order, and how to treat MIXED/INSUFFICIENT verdicts. Whether to wait for the unfinished remediation research.
3. **Universe.** Market-cap range, liquidity filters, sector exclusions (utilities already excluded).
4. **Horizon and cadence.** Rebalancing frequency and holding periods.
5. **Data sources.** Price data vendor (survivorship-free or not), fundamentals, options, social/trend data, and whether to begin collecting timestamped data early.
6. **Tooling.** Backtesting engine, storage, orchestration, dashboard; build vs adopt open-source components; license implications (e.g., AGPL).
7. **Evaluation methodology.** Holdout design, trial counting, deflated Sharpe / PBO usage, cost assumptions, and statistical thresholds.
8. **LLM role.** Whether to include an LLM layer at all; its authority (none / advisory / veto / other); input packet design; output schema.
9. **Model selection.** Frontier vs open-weight vs point-in-time models (or combinations); version pinning; ensembles; fallback on vendor outages; local vs hosted.
10. **LLM evaluation.** Post-cutoff-only scoring, calibration metrics, minimum sample size, counterfactual tracking of vetoed names, drift probes.
11. **Risk rules.** Position sizing, concentration and correlation limits, kill switch, drawdown responses.
12. **Execution.** Broker, paper vs live phasing, order types given fractional-share limits, reconciliation, and whether any agent is ever allowed order access.
13. **Capital and account type.** Amount, taxable vs retirement account, wash-sale handling.
14. **Compliance.** Review of the employer's personal trading policy.
15. **Human override policy.** When overrides are allowed and how they are logged.
16. **Logging schema.** What to record per signal, memo, order, fill, and outcome (including model ID/version, prompt version, input hashes).
17. **Social/alternative data scope.** Whether to include it, which platforms, and how to comply with terms of service.

---

## 13. Key sources

**Strategy and methodology**
- Daniel & Moskowitz, "Momentum Crashes," JFE 2016: https://www.sciencedirect.com/science/article/pii/S0304405X16301490 ; SSRN https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2371227
- Chen & Velikov, "Accounting for the Anomaly Zoo": https://jacobslevycenter.wharton.upenn.edu/wp-content/uploads/2019/09/Accounting-for-the-Anomaly-Zoo.pdf
- Novy-Marx & Velikov, "A Taxonomy of Anomalies and their Trading Costs": https://mysimon.rochester.edu/novy-marx/research/ToAatTC.pdf
- iShares MTUM fact sheet: https://www.ishares.com/us/literature/fact-sheet/mtum-ishares-msci-usa-momentum-factor-etf-fund-fact-sheet-en-us.pdf
- Martineau, PEAD (Critical Finance Review): https://ideas.repec.org/a/now/jnlcfr/104.00000122.html
- UCLA Anderson Review on PEAD: https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again/
- Faber, tactical asset allocation (SSRN 962461): https://mebfaber.com/wp-content/uploads/2016/05/SSRN-id962461.pdf
- Barber, Huang, Odean & Schwarz, Robinhood attention (JF 2022): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3715077
- Welch, "The Wisdom of the Robinhood Crowd" (JF 2022): https://onlinelibrary.wiley.com/doi/10.1111/jofi.13128
- Da, Engelberg & Gao, "In Search of Earnings Predictability": https://care-mendoza.nd.edu/assets/152190/engelberg.pdf
- Coval & Stafford, fire sales (NBER w11357): https://www.nber.org/system/files/working_papers/w11357/w11357.pdf
- Wardlaw, fund flow pressure (JF 2020): https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12962
- Bailey & López de Prado, Deflated Sharpe Ratio: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Bailey et al., Probability of Backtest Overfitting: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Insider purchases in microcaps (arXiv 2602.06198): https://arxiv.org/pdf/2602.06198

**Execution, data, regulation**
- Alpaca fractional trading docs: https://docs.alpaca.markets/us/docs/fractional-trading
- CNBC on Robinhood agentic trading (May 27, 2026): https://www.cnbc.com/2026/05/27/your-ai-agent-can-now-trade-for-you-on-robinhood-and-buy-stuff-with-your-credit-card-too.html
- FINRA Regulatory Notice 26-10 (PDT): https://www.finra.org/rules-guidance/notices/26-10
- edgartools: https://pypi.org/project/edgartools/
- TradingAgents: https://github.com/tauricresearch/tradingagents

**LLM research**
- Anthropic, Fable/Mythos access statement: https://www.anthropic.com/news/fable-mythos-access
- Anthropic, Fable 5.1 / Mythos 5.1: https://www.anthropic.com/claude-fable-and-mythos-5-1
- OpenAI, GPT-6 Astra: https://openai.com/index/gpt-6-astra/
- Vals AI Finance Agent v2: https://www.vals.ai/benchmarks/fabv2
- Finance Agent Benchmark (arXiv 2508.00828): https://arxiv.org/pdf/2508.00828
- FRI on ForecastBench parity: https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity
- ForecastBench (ICLR 2025): https://faculty.wharton.upenn.edu/wp-content/uploads/2026/02/ForecastBench_A_Dynamic_.pdf
- Prophet Arena (arXiv 2510.17638): https://arxiv.org/pdf/2510.17638v2
- StockBench (arXiv 2510.02209): https://arxiv.org/html/2510.02209v2
- Profit Mirage (arXiv 2510.07920): https://arxiv.org/pdf/2510.07920
- Detecting Lookahead Bias (arXiv 2512.23847): https://arxiv.org/html/2512.23847v2
- Chronologically Consistent LLMs (arXiv 2502.21206): https://arxiv.org/abs/2502.21206
- Instruction-tuned ChronoGPT (arXiv 2510.11677): https://arxiv.org/html/2510.11677
- DatedGPT (arXiv 2603.11838): https://www.researchgate.net/publication/401910731_DatedGPT_Preventing_Lookahead_Bias_in_Large_Language_Models_with_Time-Aware_Pretraining
- Scaling Point-in-Time LMs (arXiv 2607.11889): https://arxiv.org/html/2607.11889v2
- FinCAD (arXiv 2605.24564): https://arxiv.org/abs/2605.24564
- MemGuard-Alpha (arXiv 2603.26797): https://arxiv.org/pdf/2603.26797
- Token-probability nondeterminism (arXiv 2601.06118): https://arxiv.org/pdf/2601.06118
- Thinking Machines Lab, nondeterminism: https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- WC2026-Agents (arXiv 2607.17765): https://arxiv.org/pdf/2607.17765
- Bias in finance-LLM evaluation (arXiv 2602.14233): https://arxiv.org/html/2602.14233v1
- Alpha Arena (Nof1): https://nof1.ai/

*End of document.*
