# ML/LLM Research Brief: Neglected U.S. Equities

**Date:** 2026-10-03
**Status:** Research note
**Scope:** Small-cap/low-attention earnings behavior, insider purchases, neglected-company text, and cross-document inconsistencies.

## Executive conclusion

The most interesting opportunity is not a standalone rule such as "buy positive earnings surprises" or "buy insider purchases."

The stronger research program is:

\[
\text{Event}
+
\text{information content}
+
\text{attention}
+
\text{management behavior}
+
\text{market state}
\rightarrow
\text{future return distribution}
\]

The core thesis is:

> Public information is not equally machine-readable, equally attended to, or equally incorporated into prices.

The most promising use of LLMs is therefore **information extraction**, not direct stock selection. LLMs can convert messy filings and corporate communications into structured economic features; conventional ML can then test whether those features contain out-of-sample predictive information.

## 1. Small-cap and low-attention post-earnings behavior

### Hypothesis

Post-earnings-announcement drift (PEAD) may be stronger where attention and arbitrage capacity are limited.

Older literature documents PEAD and finds stronger underreaction in smaller, low-coverage firms. More recent work argues that unconditional PEAD has weakened materially or may disappear after better risk and microstructure treatment.

That changes the research question from:

> Does PEAD exist?

to:

> Under what observable conditions does earnings information appear to be incorporated unusually slowly today?

### Candidate features

- standardized unexpected earnings
- revenue surprise
- guidance revision
- analyst estimate dispersion
- analyst count
- prior estimate revisions
- gross-margin surprise
- operating-margin surprise
- market capitalization
- dollar volume
- liquidity/spread
- short interest
- institutional ownership
- prior momentum
- announcement return
- announcement volume
- earnings-day congestion
- textual changes in demand, pricing, backlog and guidance confidence

### ML formulation

Instead of:

\[
SUE \rightarrow Return
\]

test:

\[
f(
SUE,
guidance,
tone,
attention,
liquidity,
analyst\ coverage,
valuation,
momentum,
industry
)
\rightarrow
R_{5,20,60}
\]

The intended use is **conditional PEAD**, not unconditional PEAD.

## 2. Insider purchases

### Hypothesis

Open-market insider purchases may contain useful information, particularly in smaller firms and when purchase behavior is abnormal relative to the insider's own history.

Historical evidence generally finds purchases more informative than sales, with predictability concentrated more heavily among smaller firms.

### Why a binary insider-buy flag is weak

These two events should not be treated equally:

- a CEO buys $30,000 while already owning tens of millions of dollars;
- a CFO buys $400,000 after a major drawdown, after years of no purchases, while two directors also buy.

The research object is therefore **purchase abnormality and context**.

### Candidate features

Transaction-level:

- purchase value
- shares purchased
- purchase as percentage of prior holdings
- purchase as percentage of market cap
- price paid relative to market price
- transaction code
- open-market/private purchase indicator

Insider-level:

- role
- historical purchase frequency
- historical purchase size
- time since last purchase

Cluster-level:

- buyers within 5/10/20 days
- aggregate value
- CEO+CFO combination
- number of independent insiders

Company-state:

- recent drawdown
- valuation
- earnings surprise
- estimate revisions
- volatility
- liquidity
- disclosure changes

### Key interaction to test

\[
\text{InsiderPurchase}
\times
\text{NegativeNarrative}
\times
\text{FundamentalStability}
\]

This asks whether insiders help distinguish temporary market overreaction from persistent deterioration.

## 3. Neglected-company textual information

### Hypothesis

Important information embedded in filings and earnings materials may be incorporated less efficiently for firms with limited coverage.

Traditional NLP often compresses documents into a single sentiment score. Modern LLMs make richer extraction possible.

Instead of:

> Is this document positive?

extract:

- demand trend
- pricing power
- inventory pressure
- backlog quality
- customer concentration
- capex intent
- liquidity pressure
- management confidence
- guidance specificity
- uncertainty

### Preferred architecture

\[
Document
\xrightarrow{LLM}
Structured\ economic\ state
\xrightarrow{ML}
Future\ fundamentals/returns
\]

The LLM should act as a measurement instrument, not as the final predictor.

### Provenance requirement

Every extracted feature should retain:

- feature name
- previous text
- current text
- classification
- confidence
- source document
- document section
- model version
- prompt/schema version

A feature without traceable evidence should not enter the research database.

## 4. Cross-document inconsistencies and changes

This is the most compelling area for new investigation.

### Hypothesis

The economically useful object is not simply sentiment, but **how the company's description of reality changes across time and across documents**.

Examples:

\[
10Q_t \leftrightarrow 10Q_{t-1}
\]

\[
10Q \leftrightarrow EarningsRelease
\]

\[
PreparedRemarks \leftrightarrow Q\&A
\]

\[
Narrative \leftrightarrow Fundamentals
\]

### Example

Quarter 1:

> Demand remains robust across all major channels.

Quarter 2:

> Demand remained resilient despite moderation in selected channels.

Quarter 3:

> We continue to see healthy demand from strategic customers.

A bag-of-words model may treat these as similar. A semantic model may detect progressive narrowing:

\[
robust \rightarrow resilient \rightarrow healthy
\]

and:

\[
all\ channels
\rightarrow
selected\ weakness
\rightarrow
strategic\ customers
\]

### Candidate derived features

- filing semantic change
- risk escalation
- management consistency
- guidance-confidence change
- quantitative-specificity change
- prepared-vs-Q&A divergence
- filing-vs-call divergence
- narrative-vs-fundamental divergence

A particularly interesting quantity is:

\[
NarrativeFundamentalGap
=
NarrativeScore-FundamentalScore
\]

For example, management remains optimistic while backlog falls, inventories rise, receivables deteriorate and margins compress.

## Unified research thesis

The four areas should eventually be tested together:

\[
\boxed{
\text{LLM information extraction}
+
\text{neglected equities}
+
\text{event data}
+
\text{rigorous ML validation}
}
\]

A hypothetical event state might contain:

- small market cap
- sparse analyst coverage
- negative earnings surprise
- large stock decline
- seemingly positive earnings-call language
- newly introduced risk language in the 10-Q
- deterioration in backlog quality
- CFO open-market purchase shortly afterward

A conventional strategy may see these as unrelated signals. A richer model can ask what **information environment** generated them.

## Recommended first experiments

1. Replicate filing-change relationships using deterministic text metrics only.
2. Add semantic filing-change features and test incremental value.
3. Determine which filing sections matter.
4. Test narrative-vs-fundamental divergence.
5. Replicate modern insider-purchase results.
6. Replace binary insider signals with abnormal purchase intensity.
7. Test insider clustering.
8. Test insider purchase × disclosure-state interactions.
9. Test PEAD conditional on attention and liquidity.
10. Build a unified ranking model only after the components demonstrate incremental value.

## Scientific controls

Any promising result should survive:

- point-in-time reconstruction
- walk-forward validation
- purging/embargo where labels overlap
- explicit transaction-cost assumptions
- multiple liquidity thresholds
- multiple holding periods
- multiple model families
- negative controls
- shuffled-feature tests
- final untouched holdout
- prospective validation

The project should optimize for **falsification**, not the number of promising backtests.

## Core principle

\[
\boxed{\text{Build the falsification machine before building the prediction machine.}}
\]

Modern LLMs make generating hypotheses and models cheap. The scarce capability is determining which discoveries are real.

## Selected source trail

- Lakonishok & Lee, insider trading and smaller firms: https://www.nber.org/papers/w6656
- Cohen, Malloy & Nguyen, *Lazy Prices*: https://onlinelibrary.wiley.com/doi/10.1111/jofi.12885
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC ownership transaction codes: https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html
- SEC Forms 3/4/5: https://www.sec.gov/about/forms/forms-3-4-5.pdf
