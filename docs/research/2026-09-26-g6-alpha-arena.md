# Research Report: G6, Alpha Arena results from Nof1 primary sources

**Brief:** #230  ·  **Date:** 2026-09-26  ·  **Status:** COMPLETE  ·  **Agent/model:** researcher, claude-opus-5-5 (orchestrated by team minnow; the orchestrator fetched the Nof1 pages with the Firecrawl CLI)

## Answer
**Verdict:** INSUFFICIENT for any claim that frontier-model capability translates into trading P&L, in either direction  ·  **Confidence:** medium

Nof1's own records do not measure capability, so they cannot support a claim that capability turns into P&L. Nof1 also says they cannot rank the models reliably. TechPost1 says: "The goal is **not** to use a single run to declare a permanent 'best' trading model"; "early successes may be the result of luck"; "statistical power is limited and early standings can move. We've seen run-to-run variation in both rankings and inter-model correlations." Season 1 was one live run per model. Nof1 names the winner (Qwen 3 MAX) but no longer serves any Season 1 per-model figures: its Season 1 model pages now return "MODEL NOT FOUND".

Season 1.5 has four parallel competitions per model, all over the same two weeks: 32 accounts. In the retrieved leaderboard, 6 of the 32 accounts show a positive return. The Season 1 winner, Qwen3 Max, lost money in all four of its Season 1.5 competitions. Its best rank was 7 of 32 (-6.79%) and its worst was 31 of 32 (-82.06%). This confirms from primary data the handoff's factual point that standings did not carry over between seasons.

Grok 4.20 was the only model with a positive return in all four Season 1.5 competitions (+0.48% to +34.59%). Four runs over one shared two-week window are not independent draws of market conditions, and Nof1 reports no significance test. That consistency is therefore a single observation, not evidence of skill.

The handoff's broader reading, that there is "no consistent evidence that capability translates into trading P&L", is not contradicted by Nof1's data. But Alpha Arena is not positive evidence for it either. It is a short, leveraged, single-period benchmark that its own publisher disclaims.

## How the §6.1 criteria were applied to a competition leaderboard
- **Qualifying sources:** under the brief, Tier 1 means Nof1's own publications and data. Four Tier 1 documents were read: TechPost1, the leaderboard, the home page, and the founder's end-of-Season-1 post. The blog index was used only to confirm that no other posts exist. All four documents come from one publisher, so they do not count as independent sources under §6.1's "at least 3 independent" test.
- **"Out-of-sample" test:** I read this as a second, independent run of the same models under the same setup, published by Nof1. Season 1.5 is a second season, but it changed the market (crypto perpetuals to US-equity names), the roster, the prompts (four themed competitions) and several model versions. It is therefore a different experiment, not a replication. Its four competitions share one market window.
- **"Addresses costs":** Nof1 reports fees per account for Season 1.5 and describes fees for Season 1 (see the table and "Setup"). In every Season 1.5 row, P&L equals account value minus $10,000. P&L is therefore account-level, and I infer it is net of the fees debited to the account. Nof1 does not say this explicitly. Slippage is not reported.
- **Capability:** Nof1 publishes no capability measure. Any capability-to-P&L link would have to import external benchmark scores, which is outside the brief. That alone rules out SUPPORTED for the capability claim.
- **Result:** no stable, cost-stated ordering across independent runs exists in Nof1's records. Nof1 itself reports run-to-run ranking variation. I did not grade NOT SUPPORTED, because there was never a demonstrated effect to fail a replication. The claim was never testable with this design.

## Setup, from Nof1 (Season 1)
All quotes are from https://nof1.ai/blog/TechPost1 (post dated 2025-10-27 on https://nof1.ai/blog; retrieved 2026-09-26).
- **Capital and venue:** "We gave the leading LLMs **$10,000 each** to trade on [Hyperliquid], **with zero human intervention**." The universe was "six popular cryptocurrencies on Hyperliquid: BTC, ETH, SOL, BNB, DOGE, & XRP", traded as perpetual futures.
- **Models:** "**GPT-5**, **Gemini 2.5 Pro**, **Claude Sonnet 4.5**, **Grok 4**, **DeepSeek v3.1**, and **Qwen3-Max**." "With the exception of Qwen3-Max, we enable reasoning with the highest configurable setting for all models."
- **Prompt:** "all agents were provided the same system prompt, user prompt template, data, and their default sampling configuration." The system prompt was not published: "The system prompt is something that we may open source at some point in the future."
- **Cadence and tools:** "At each inference call (~2-3 mins)". "We avoided multi-agent orchestration, tool use, and long conversation histories". The inputs were numerical market data only, with no news.
- **Sizing and leverage:** "Position sizing … is computed by the agent itself, conditioned on available cash, leverage, and its internal risk preference." The worked example shows a model choosing `"leverage": 20`. No leverage cap is stated in the post.
- **Costs:** "this is **live trading** … so models face real executions, real fees". "Overall PnL was dominated by trading costs in early runs as agents over-traded". The prompt provides "details on expected fees".
- **Prompt tuning before launch:** the prompt fields "were found to improve performance", and the instructions "were curated over many iterations". There were "multiple pre-launch test runs" whose results are not published.
- **Window:** "Season 1 of Alpha Arena runs live through November 3, 2025, 5:00 p.m. ET." The founder's post of 2025-11-03T22:57:30Z says: "Season 1 of Alpha Arena has officially ended." The start date is not stated in the retrieved pages.
- **Market regime:** Nof1 does not describe it for either season. The only regime information is incidental, from prices quoted in the worked example (for example, BTC `current_price = 107982.5` on 2025-10-19).

## Setup, from Nof1 (Season 1.5)
- The home page (https://nof1.ai/, retrieved 2026-09-26) lists the instruments as TSLA, NDX, NVDA, MSFT, AMZN, GOOGL and PLTR. It lists the competitions as "1: New Baseline", "2: Monk Mode", "3: Situational Awareness" and "4: Max Leverage", and describes "the aggregate performance across all competitions in Alpha Arena Season 1.5".
- The home page also says: "**Update:** The official competition has ended as of December 3rd, 2025 at 5:00 PM EST. Models are no longer running. **Mystery Model** was the winner with a **12.11% aggregate return** in 2 weeks. In total across competitions, it made **$4,844**."
- The leaderboard (https://nof1.ai/leaderboard, retrieved 2026-09-26) names "WINNING MODEL GROK-4.20". Together with the home-page line, Nof1's own pages identify the winning "Mystery Model" as grok-4.20. That is an inference from two Nof1 pages; no retrieved Nof1 sentence says so in words.
- **Not retrieved:** the rules of each themed competition, leverage limits, the prompt, the cadence and the start date. The home page's "SEASON 1.5 DETAILS" tab was not captured in the fetched markdown.
- The model chat excerpts on the home page mention "20x leveraged" NDX positions, "funding" and "XYZ100". That suggests the equities were traded as leveraged perpetuals, but these are model outputs, not Nof1 statements, so this is **UNVERIFIED**.
- The founder's 2025-11-03 post announced the design: "In the next season, we'll ship lots of improvements and test multiple prompts in parallel, as well as many instances of each model". The leaderboard shows one instance per model per competition.

## Per-model results: Season 1 (crypto perpetuals, ended Nov 3, 2025)
Nof1 no longer publishes Season 1 per-model figures, so the numeric cells are blank. The current leaderboard shows only Season 1.5. The Season 1 model pages found by search (https://nof1.ai/models/qwen3-max, /models/gpt-5, /models/grok-4 and /models/gemini-2.5-pro) were fetched with Firecrawl on 2026-09-26. Each returned the Season 1.5 site shell with "MODEL NOT FOUND" and "The specified AI model does not exist in the system.", and no Season 1 content. The only primary result is the winner, from the founder's post (https://x.com/jay_azhang/status/1985481491078328621, posted 2025-11-03T22:57:30Z, retrieved 2026-09-26; a readable page, not a login wall): "Qwen 3 MAX pulled ahead at the very end to secure the win".

| Model | Final rank | Return | Final account value | Max drawdown | Trade count | Fees | Primary citation |
|---|---|---|---|---|---|---|---|
| Qwen3-Max | 1 ("secure the win") | | | | | | x.com/jay_azhang/status/1985481491078328621 (2026-09-26) |
| DeepSeek v3.1 | | | | | | | |
| Claude Sonnet 4.5 | | | | | | | |
| Gemini 2.5 Pro | | | | | | | |
| Grok 4 | | | | | | | |
| GPT-5 | | | | | | | |

## Per-model results: Season 1.5 (US-equity names, Nov–Dec 3, 2025)
Every figure below is from https://nof1.ai/leaderboard, retrieved 2026-09-26 (the "OVERALL STATS" table), exactly as printed. The page does not print the word "Season". I assign the table to Season 1.5 because its roster and four competition names match what the home page labels "Alpha Arena Season 1.5". The page gives no timestamp for the figures. Nof1's note on the page: "All statistics (except Account Value and P&L) reflect completed trades only. Active positions are not included in calculations until they are closed." Nof1 gives no drawdown figure, so there is no drawdown column. The Sharpe definition and annualization are not stated.

| Rank | Model | Competition | Acct value | Return % | Total P&L | Fees | Win rate | Biggest win | Biggest loss | Sharpe | Trades |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | grok-4.20 | 3: Situational Awareness | $13,459 | +34.59% | $3,459 | $730.31 | 31.6% | $3,084 | -$2,066 | 0.019 | 158 |
| 2 | gpt-5.1 | 4: Max Leverage | $10,888 | +8.88% | $888.25 | $1,574 | 34.1% | $1,616 | -$552.36 | 0.009 | 537 |
| 3 | deepseek-chat-v3.1 | 2: Monk Mode | $10,730 | +7.3% | $729.63 | $1,923 | 33% | $4,435 | -$897.01 | 0.000 | 667 |
| 4 | grok-4.20 | 2: Monk Mode | $10,366 | +3.66% | $366.37 | $298.05 | 35% | $467.35 | -$493.02 | 0.016 | 117 |
| 5 | grok-4.20 | 4: Max Leverage | $10,193 | +1.93% | $192.97 | $292.75 | 40.6% | $380.10 | -$632.59 | 0.004 | 143 |
| 6 | grok-4.20 | 1: New Baseline | $10,048 | +0.48% | $47.58 | $966.08 | 38.3% | $1,849 | -$619.29 | -0.010 | 358 |
| 7 | qwen3-max | 2: Monk Mode | $9,321 | -6.79% | -$678.72 | $581.57 | 30.3% | $378.28 | -$129.52 | -0.038 | 861 |
| 8 | kimi-k2-thinking | 2: Monk Mode | $8,955 | -10.45% | -$1,045 | $1,830 | 31.5% | $1,006 | -$478.50 | -0.029 | 505 |
| 9 | gemini-3-pro | 2: Monk Mode | $8,906 | -10.94% | -$1,094 | $1,067 | 31.6% | $542.95 | -$382.32 | -0.018 | 528 |
| 10 | gpt-5.1 | 2: Monk Mode | $8,748 | -12.52% | -$1,252 | $529.47 | 34.2% | $458.45 | -$386.20 | -0.063 | 409 |
| 11 | claude-sonnet-4-5 | 2: Monk Mode | $8,085 | -19.15% | -$1,915 | $363.30 | 35.1% | $669.65 | -$610.69 | -0.037 | 131 |
| 12 | gpt-5.1 | 1: New Baseline | $8,041 | -19.59% | -$1,959 | $906.53 | 35.6% | $722.02 | -$476.06 | -0.051 | 652 |
| 13 | qwen3-max | 4: Max Leverage | $7,976 | -20.24% | -$2,024 | $2,184 | 31.3% | $739.57 | -$479.26 | -0.040 | 600 |
| 14 | gpt-5.1 | 3: Situational Awareness | $7,488 | -25.12% | -$2,512 | $1,671 | 28% | $953.20 | -$821.74 | -0.054 | 789 |
| 15 | kimi-k2-thinking | 4: Max Leverage | $6,344 | -36.56% | -$3,656 | $1,484 | 36.7% | $2,364 | -$2,813 | -0.032 | 349 |
| 16 | qwen3-max | 1: New Baseline | $5,863 | -41.37% | -$4,137 | $1,299 | 28.4% | $232.55 | -$737.61 | -0.100 | 976 |
| 17 | deepseek-chat-v3.1 | 1: New Baseline | $5,278 | -47.22% | -$4,722 | $1,305 | 34% | $1,643 | -$2,984 | -0.034 | 892 |
| 18 | grok-4 | 1: New Baseline | $5,160 | -48.4% | -$4,840 | $1,514 | 35.4% | $2,421 | -$1,453 | -0.055 | 495 |
| 19 | grok-4 | 2: Monk Mode | $5,101 | -48.99% | -$4,899 | $1,004 | 37.1% | $1,558 | -$1,733 | -0.061 | 439 |
| 20 | gemini-3-pro | 3: Situational Awareness | $4,850 | -51.5% | -$5,150 | $1,998 | 29.6% | $1,706 | -$1,209 | -0.072 | 785 |
| 21 | gemini-3-pro | 4: Max Leverage | $4,714 | -52.86% | -$5,286 | $1,359 | 27.4% | $1,004 | -$1,838 | -0.066 | 595 |
| 22 | claude-sonnet-4-5 | 1: New Baseline | $4,575 | -54.25% | -$5,425 | $2,086 | 34.2% | $953.79 | -$1,077 | -0.076 | 666 |
| 23 | kimi-k2-thinking | 3: Situational Awareness | $4,404 | -55.96% | -$5,596 | $3,464 | 30.2% | $2,959 | -$1,998 | -0.079 | 819 |
| 24 | kimi-k2-thinking | 1: New Baseline | $4,235 | -57.65% | -$5,765 | $2,114 | 35.5% | $1,007 | -$1,168 | -0.078 | 498 |
| 25 | claude-sonnet-4-5 | 4: Max Leverage | $4,131 | -58.69% | -$5,869 | $755.33 | 32.8% | $543.42 | -$1,125 | -0.084 | 268 |
| 26 | grok-4 | 4: Max Leverage | $3,924 | -60.76% | -$6,076 | $745.54 | 26.3% | $749.87 | -$2,422 | -0.058 | 304 |
| 27 | deepseek-chat-v3.1 | 4: Max Leverage | $3,772 | -62.28% | -$6,228 | $1,087 | 32.4% | $939.14 | -$3,188 | -0.044 | 398 |
| 28 | gemini-3-pro | 1: New Baseline | $3,506 | -64.94% | -$6,494 | $1,876 | 31.3% | $532.05 | -$694.27 | -0.130 | 1,474 |
| 29 | deepseek-chat-v3.1 | 3: Situational Awareness | $3,341 | -66.59% | -$6,659 | $4,419 | 30.3% | $1,342 | -$1,171 | -0.069 | 1,092 |
| 30 | claude-sonnet-4-5 | 3: Situational Awareness | $2,839 | -71.61% | -$7,161 | $3,875 | 31.7% | $674.25 | -$626.96 | -0.123 | 1,217 |
| 31 | qwen3-max | 3: Situational Awareness | $1,794 | -82.06% | -$8,206 | $4,066 | 28.7% | $574.81 | -$1,953 | -0.136 | 1,418 |
| 32 | grok-4 | 3: Situational Awareness | $385.02 | -96.15% | -$9,615 | $3,521 | 32.8% | $1,846 | -$2,022 | -0.181 | 1,072 |

**Per-model summary.** These are my own arithmetic on the rows above, not Nof1 figures. Inputs are Nof1's rounded values, so the sums are approximate.

| Model | Competitions with return > 0 | Mean return across the 4 competitions | Sum of Total P&L |
|---|---|---|---|
| grok-4.20 | 4 of 4 | +10.17% | ≈ $4,066 |
| gpt-5.1 | 1 of 4 | -12.09% | ≈ -$4,835 |
| qwen3-max | 0 of 4 | -37.62% | ≈ -$15,046 |
| kimi-k2-thinking | 0 of 4 | -40.16% | ≈ -$16,062 |
| deepseek-chat-v3.1 | 1 of 4 | -42.20% | ≈ -$16,879 |
| gemini-3-pro | 0 of 4 | -45.06% | ≈ -$18,024 |
| claude-sonnet-4-5 | 0 of 4 | -50.93% | ≈ -$20,370 |
| grok-4 | 0 of 4 | -63.58% | ≈ -$25,430 |

Across all 32 accounts, the Total P&L values sum to about -$112,580 on $320,000 of starting capital (32 × $10,000), about -35.2%. That is consistent with the handoff's "lost about a third" and "6 of 32" (my arithmetic).

**Internal inconsistency in Nof1's own pages.** The home page says the winner "made **$4,844**" at a "**12.11% aggregate return**" (4,844 / 40,000 = 12.11%). The leaderboard's four grok-4.20 rows sum to about $4,066, or about +10.17%. The leaderboard has no timestamp, so I cannot tell whether it reflects a different cut-off, positions closed after December 3, or a different aggregation. I have not reconciled the two, and neither figure is preferred here.

## Run count and significance
- **Season 1:** one live run per model, preceded by unpublished pre-launch runs. Nof1 (TechPost1): "The goal is **not** to use a single run to declare a permanent 'best' trading model. We are deeply aware of the flaws in Season 1, including but not limited to: prompt bias, limited sample sizes / lack of statistical rigor, and shortness of evaluation period". Also: "this is a single live season with a finite window, so statistical power is limited and early standings can move. We've seen run-to-run variation in both rankings and inter-model correlations." And: "early successes may be the result of luck."
- **Behavioral consistency versus P&L:** Nof1 separates the two. The behavioral patterns "have been consistent across early trials". Self-reported confidence "appears decoupled from actual trading performance". The founder's post says: "consistent biases … Something like an investing 'personality'". Nof1 claims consistent behavior, not consistent P&L.
- **Season 1.5:** four competitions per model, one instance each, all run over the same two weeks on the same instruments: 32 accounts. The four runs share one market path, so they are not independent tests of a model across market conditions.
- **Significance:** no retrieved Nof1 page reports a significance test, a confidence interval, or a benchmark comparison such as buy-and-hold of the same instruments. TechPost1 promises fuller results "once they meet our bar for stable conclusions". The blog index (https://nof1.ai/blog, retrieved 2026-09-26) lists only TechPost1 (dated 2025-10-27), so no follow-up has been posted there.

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Nof1 disclaims single-run rankings and reports run-to-run rank variation | TechPost1, "What this is not" and "Future work" | 1 | "We've seen run-to-run variation in both rankings and inter-model correlations" | Pre-launch runs referenced, not published | n/a |
| Season 1 setup: $10,000 each, live, same prompt, ~2-3 min cadence, agent-chosen leverage | TechPost1, "Alpha Arena Design" | 1 | "$10,000 each"; "~2-3 mins" | n/a | "real fees"; "PnL was dominated by trading costs in early runs" |
| Season 1 winner | Founder's X post, 2025-11-03 | 1 | "Qwen 3 MAX pulled ahead at the very end to secure the win" | No | Not stated |
| Season 1.5 ended; winner and aggregate | nof1.ai home page | 1 | "12.11% aggregate return in 2 weeks"; "$4,844" | No | Not stated |
| Season 1.5 per-account results (32 rows) | nof1.ai/leaderboard | 1 | 6 of 32 rows positive; Qwen3-Max ranks 7, 13, 16, 31 | Second season, but a changed design; not a replication | Fees reported per row; P&L = account value - $10,000 (inference: net of fees) |
| No other Nof1 posts on the blog | nof1.ai/blog | 1 | Only one post listed, dated "2025-10-27" | n/a | n/a |

## Disconfirmation
- **Searches run (11 of 20):**
  1. `nof1.ai blog Alpha Arena season 1 results methodology`
  2. `github nof1-ai alpha arena repository`
  3. `nof1.ai blog season 1.5 US stocks results` (limited to nof1.ai)
  4. `Nof1 Alpha Arena arXiv paper Azhang LLM trading live markets`
  5. `"nof1" alpha arena official data release trades csv github OR huggingface`
  6. `xAI Grok 4.20 Alpha Arena Season 1.5 Musk 12.11% stocks` (Tier 2 vendor check)
  7. `nof1.ai/blog Alpha Arena season 1.5 recap "aggregate" "mystery model"`
  8. `Jay Azhang nof1 Alpha Arena statistical significance single run "season 2"`
  9. **Noise and setup critiques:** `Alpha Arena critique leverage noise luck single run LLM trading benchmark not statistically significant`
  10. **Ranking changes between seasons:** `Alpha Arena season 1.5 Qwen3 Max DeepSeek ranking reversed stocks losses final leaderboard`
  11. `"nof1.ai/blog" season 1.5` (locator)
- **What was found against a capability-to-P&L reading, from Tier 1:**
  - *Rankings changed between runs:* Nof1 reports run-to-run rank variation (TechPost1). Within Season 1.5, one model's rank swings widely by competition: deepseek-chat-v3.1 is 3rd in Monk Mode and 29th in Situational Awareness, and gpt-5.1 is 2nd in Max Leverage and 14th in Situational Awareness (leaderboard).
  - *Rankings changed between seasons:* the Season 1 winner, Qwen3 Max (founder's post), has returns of -6.79%, -20.24%, -41.37% and -82.06% in Season 1.5 (leaderboard). Season 1's full ordering could not be checked, so the handoff's claim that rankings "inverted" is confirmed only for the winner.
  - *Within noise:* Nof1 itself says "statistical power is limited", and no significance test is published.
  - *Setup critiques:* the leverage is chosen by the agent, and one Season 1.5 competition is themed "Max Leverage". The prompt was tuned over "many iterations" on unpublished pre-launch runs. Nof1 lists "prompt bias" among Season 1's flaws. Qwen3-Max ran without reasoning enabled, while the other models ran at the "highest configurable setting". That is a model-configuration difference inside the comparison.
  - *Survivorship of the leaderboard:* the Season 1.5 leaderboard shows all 32 accounts, including the -96.15% one, so there is no sign of accounts being dropped. However, Season 1's per-model results are no longer on the leaderboard page, and the pre-launch runs were never published.
- **Tier 3 (logged only, not evidence):** relays report conflicting Season 1.5 figures, for example "+12.11%" next to "$10,000 into $12,193", and "from about 12% to nearly 35%". The leaderboard's +34.59% grok-4.20 row likely explains the "35%". Commentary such as borisagain.substack.com argues that two weeks of results says nothing about skill, but offers no test.
- **Possible impersonation:** several GitHub repos share an identical description of a "deep reinforcement learning" bot "built by Nof1.ai". They look like lookalikes, and I did not fetch or use them. No official Nof1 GitHub or data release was found.

## Caveats & gaps
- **Why COMPLETE.** Every item in the brief's done-when is met:
  - a §6.1 grade;
  - a per-model table for each season, with a primary citation for every figure and blank cells where no primary figure exists;
  - the run-count and significance section;
  - the disconfirmation log;
  - the verdict.

  The Season 1 cells are blank because Nof1 no longer publishes those figures. The four Season 1 model pages return "MODEL NOT FOUND" (fetched 2026-09-26), and the leaderboard and blog carry no Season 1 table. That counts as "no primary figure exists", not as unfinished work.
- **Why the remaining gaps don't change the grade.** The grade rests on three things:
  - Nof1 publishes no capability measure;
  - Nof1 itself disclaims single-run rankings and reports run-to-run rank variation;
  - there is no significance test or benchmark.

  None of the open items can change those:
  - Season 1 per-model returns: even a full Season 1 table would be one run per model, which Nof1 says cannot rank the models;
  - the Season 1.5 rules tab;
  - the $4,844 vs about $4,066 discrepancy: either figure leaves grok-4.20 as a positive outlier in one shared two-week window;
  - the Sharpe definition.
- **Season 1 per-model figures are unrecoverable from Nof1** as of 2026-09-26 (see the Season 1 table). The on-chain Hyperliquid wallet histories are the remaining primary record. Examining them is a data task outside this brief.
- **Missing:** the Season 1.5 competition rules, leverage limits, prompt, cadence and start date. The "SEASON 1.5 DETAILS" tab was not captured.
- **Missing:** Nof1's description of the market regime in either season. Nof1 gives none, so I report none.
- The leaderboard has no timestamp, and its grok-4.20 total does not match the home page's $4,844 (see "Internal inconsistency").
- The leaderboard's Sharpe definition is not given. The values (-0.181 to 0.019) look like per-period, unannualized figures, but that is **UNVERIFIED**.
- No max-drawdown figure is published on the retrieved pages. "Biggest loss" is a single-trade figure, not a drawdown.
- All Tier 1 material comes from one publisher (Nof1). There is no independent audit of the figures here. The on-chain wallet records for Season 1 are a possible check, but were not examined.
- Model identities follow Nof1's labels. I made no inference about what "grok-4.20" was beyond the Nof1 pages.

## UNVERIFIED items
- Whether reported P&L is net of fees: this is my inference from P&L = account value - $10,000. Nof1 does not state it.
- Whether the Season 1.5 equities were traded as leveraged perpetuals with funding: the only support is model-chat text, not Nof1 statements.
- The Season 1.5 start date (relays say Nov 19, 2025) and the Season 1 start date (relays say Oct 18, 2025).
- Which figure is Nof1's final Season 1.5 result for grok-4.20: the home page's $4,844 / 12.11%, or about $4,066 from the leaderboard.
- The definition and annualization of the leaderboard Sharpe column.
- Season 1 per-model figures in the handoff (for example, Qwen3 Max +22.31%). These remain relay-only. They cannot be verified against Nof1, because its Season 1 model pages return "MODEL NOT FOUND" (https://nof1.ai/models/qwen3-max, /gpt-5, /grok-4, /gemini-2.5-pro; fetched 2026-09-26). No Nof1 page retrieved for this report carries a Season 1 results table.

## Follow-up questions (not answered here)
- Can Season 1 final figures be reconstructed from an archived copy of Nof1's pages or from the on-chain Hyperliquid wallets? Nof1 no longer serves them.
- Did Nof1 publish Season 1.5 rules and prompts (the "SEASON 1.5 DETAILS" tab), or any significance analysis, after December 2025? Has any Season 2 run?
- Can the Hyperliquid wallet histories reproduce Nof1's reported returns net of fees and funding? That would be a data task.
- How did each model's return compare with buy-and-hold of the same instruments over the same window? Nof1 publishes no benchmark.

## Budget
- **Searches:** 11 of 20.
- **Sources:** 10 of 12.
  - The 5 Nof1 pages fetched by the orchestrator with Firecrawl on 2026-09-26: techpost1, leaderboard, home, blog, x_jay.
  - The 4 Season 1 model pages fetched with Firecrawl on 2026-09-26 (s1_qwen3-max, s1_gpt-5, s1_grok-4, s1_gemini-2.5-pro). Each returned the same 3,686-byte "MODEL NOT FOUND" shell; I confirmed this by reading s1_gpt-5.md and grepping all four for Season 1 content (none found).
  - The arXiv 2609.05663 abstract page. It returned content, but has no Alpha Arena content and contributed nothing.
  - My earlier blocked fetches returned no content and are not counted.

## Sources
1. Nof1, "Exploring the Limits of Large Language Models as Quant Traders" (TechPost1, dated 2025-10-27). https://nof1.ai/blog/TechPost1 (Tier 1; retrieved 2026-09-26 via Firecrawl)
2. Nof1, Alpha Arena leaderboard ("OVERALL STATS", 32 rows). https://nof1.ai/leaderboard (Tier 1; retrieved 2026-09-26 via Firecrawl; no timestamp on page)
3. Nof1, Alpha Arena home page (Season 1.5 end notice). https://nof1.ai/ (Tier 1; retrieved 2026-09-26 via Firecrawl)
4. Nof1, blog index. https://nof1.ai/blog (Tier 1; retrieved 2026-09-26 via Firecrawl)
5. Jay Azhang (Nof1 founder), X post, 2025-11-03T22:57:30Z, and thread reply linking TechPost1. https://x.com/jay_azhang/status/1985481491078328621 (Tier 1 as an official post; retrieved 2026-09-26 via Firecrawl; readable, not a login wall)
6. Nof1, Season 1 model pages: https://nof1.ai/models/qwen3-max, https://nof1.ai/models/gpt-5, https://nof1.ai/models/grok-4, https://nof1.ai/models/gemini-2.5-pro (Tier 1; retrieved 2026-09-26 via Firecrawl; all four return "MODEL NOT FOUND" with no Season 1 content; 4 fetches against the budget)
7. Barton et al., arXiv 2609.05663 (abstract page). https://arxiv.org/abs/2609.05663 (retrieved 2026-09-26; no Alpha Arena content; not used)

Tier 3, used only as locators or in the disconfirmation log: https://borisagain.substack.com/p/why-alpha-arena-is-literally-the · https://www.iweaver.ai/blog/alpha-arena-ai-trading-season-1-results/ · https://www.gncrypto.news/news/mystery-model-alpha-arena-season-1-5-winner/ · https://finance.yahoo.com/news/elon-musks-grok-4-20-123855766.html · https://github.com/alpha-arena-nof1-ai/nof1ai-alpha-arena (suspected lookalike)
