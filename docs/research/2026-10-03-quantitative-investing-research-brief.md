# The fundamentals of quantitative investing

Research brief • 3 October 2026 • Perspective: head of research

## Executive summary

Systematic investing is a disciplined way to acquire exposures, control costs and make decisions reproducible. It does not remove investment risk, and a large catalogue of backtested signals does not constitute a large catalogue of independent opportunities.

Start with three economic questions. Are you bearing a risk someone wants to transfer? Are you trading against a predictable decision error? Are you providing balance sheet, immediacy or flexibility that another participant cannot provide? A credible answer makes an effect plausible; it does not establish its size or prove that your implementation earns it.

The strongest starting point for a modest account is inexpensive, diversified exposure to productive equity capital. Among active research projects, slow profitability-aware stock selection and diversified liquid-market trend following have unusually substantial foundations. Value and cross-sectional momentum warrant investigation as complements, with patient holding periods, liquidity filters and explicit costs. Their original published spreads are not realistic forecasts for a contemporary account. Pure size, generic pairs trading, lottery-stock shorting and mechanical index-inclusion trades have much weaker practical cases.

Publication decay is real, but the replication debate is not a simple referendum on whether factors exist. Reproducing an original sample, testing a liquid portfolio, pooling evidence across countries, and demonstrating profits after borrow and impact are different exercises. Papers that appear to disagree often use different exercises. Replication is a necessary checkpoint, not an investment approval.

The practical edge is often in implementation: less turnover, better portfolio construction, cheaper funding, tax awareness, reliable point-in-time data and the ability to continue through a bad period. Individuals benefit from small capacity needs and freedom from benchmarks and redemptions. Institutions benefit from execution, financing, securities lending and broader access. An institution's fill model or borrow book cannot simply be copied into a retail backtest.

Weak effects take an extraordinarily long time to validate from a single portfolio. In an ideal independent-return calculation, detecting a true annual Sharpe of 0.3 with a one-sided significance test and 80% power requires about 69 years. This is a calculation, not a historical return claim; assumptions and alternatives appear in Q4. Cross-sectional data help, but correlated stocks and repeated research choices sharply reduce the apparent sample size.

Build a research program around a few economic themes, a trial register and an untouched evaluation process. Require factor attribution, independent replication, realistic implementation and sensitivity to bad regimes before allocating. Use machine learning first to combine established information, estimate risk and improve execution. A neural network's superior gross ranking is weak evidence of a superior investable portfolio.

Diversification should combine different reasons for being paid. Equity, credit, carry and short volatility can all lose together when risk-bearing capacity disappears. Value and momentum have historically complemented one another, but sample correlations are not guarantees. Trend can help in sustained crises; it cannot reliably protect against an instantaneous gap or every reversal. Leverage magnifies funding and liquidation risk as well as measured volatility.

The realistic objective is a transparent portfolio with several defensible return sources, modest implementation losses and a research process that can reject attractive backtests. A reliable forward net premium for a newly specified strategy is **unknown** until its construction, costs and evidence are established.

## Reading the evidence

References such as [1] link to numbered sources at the end. Numerical empirical claims are centralized in the evidence ledger, with exact quoted figures, sample, costs, test status and structure. Tables elsewhere refer to ledger IDs rather than silently changing definitions. Mathematical examples and proposed construction parameters are explicitly **illustrative or design choices**; they are not sourced historical performance.

**Gross** means before implementation costs. **Partial net** means only the named costs have been deducted. **Live net** means fund NAV returns after applicable fund expenses and trading effects, before shareholder taxes unless indicated. Neither a dollar-neutral spread nor a NAV return is automatically an alpha. Futures returns must distinguish excess P&L from interest on collateral.

Grades apply to the stated proposition, not to every variant bearing the same name. **SUPPORTED** requires the user's independent-source, out-of-sample and costs tests. **MIXED** reflects conflicting, old, illiquid or cost-sensitive evidence. **NOT SUPPORTED** is used narrowly where credible modern evidence defeats the stated strategy. **INSUFFICIENT** means inadequate qualifying evidence. Different papers from the same authors or the same manager do not count as independent corroborations.

This is a literature-based assessment, not a fresh point-in-time replication. I could not verify a complete, comparable post-2010, all-cost, after-tax panel for every theme. Those cells are **unknown**. Historical annualized returns and Sharpe ratios are not expected future returns. “Nothing found” below means no qualifying result found in this review, not proof that none exists.

The SUPPORTED grades pass the source rule as follows; the claim is deliberately narrower than a universal modern alpha estimate:

| Supported proposition | Independent qualifying evidence | OOS/post-launch test | Costs addressed |
|---|---|---|---|
| Diversified equity ownership is an implementable compensated exposure | Fama–French [14], Jordà and coauthors [15], McQuarrie [16], official investable-product data [70], all T1 | Cross-country/history extension [15,16] and post-launch product data [70] | Official product expense data [70]; a forward premium remains unknown |
| Slow liquid profitability-aware tilts have a substantial research foundation | Fama–French [14], Novy-Marx [22], Hou–Xue–Zhang [23], JKP [4], all T1 | International/pre/post-original tests [4] | Novy-Marx–Velikov [8]; institutional cost evidence [9]. Not every quality bundle or short book |
| Diversified liquid trend can be implemented net of costs | Moskowitz–Ooi–Pedersen [38], independent Lempérière team [40], Baltas–Kosowski [41], all T1; live filing [65] | Long-history extension [40] and actual post-launch [65] | Implementation/capacity [41], live NAV [65]. No universal Sharpe or crisis guarantee |

Historical risk-premium research does not necessarily have a meaningful “post-publication” event; the independent OOS and live tests above are used instead. The source count alone cannot settle contradictions: the MIXED themes retain that grade even when many papers exist.

## Q1. Where expected return comes from

### Economic mechanisms and the other side

The three groups overlap. A value spread can reflect distress risk, extrapolation and benchmark constraints at once. A momentum trade may be against underreaction today and against a leveraged forced seller tomorrow. Price data alone generally cannot identify the mechanism.

| Group and return source | Who supplies the opportunity, and why? | Persistence judgment and grade |
|---|---|---|
| Risk: equity ownership | Companies raise capital; investors accepting uncertain residual cash flows require compensation relative to safe assets. There need not be a mistaken counterparty in each trade. | Productive capital and aggregate risk persist; the price paid changes expected return. **SUPPORTED** for diversified ownership, not a fixed annual magnitude. [14,15,16,70] |
| Risk: term/duration | Borrowers prefer long funding; some investors prefer liquid short claims. Long bonds expose holders to inflation and changing discount rates. Liability-matching demand can bid duration up. | Duration risk persists; a positive unconditional excess return in every era does not. **MIXED** for a reliably positive tactical premium. [17,18] |
| Risk: credit and credit liquidity | Borrowers pay for default exposure; dealers and constrained investors pay for balance sheet and liquidity. Promised spread includes expected default losses. | Credit risk persists; residual compensation after duration, defaults and costs is contested, particularly investment grade. **MIXED**. [18,19] |
| Risk/mispricing: value | Owners of unpopular or distressed cash flows sell; extrapolators overpay for exciting growth; benchmark managers avoid unfashionable positions. | Mechanism plausible, accounting definition unstable, long dry periods and spanning concerns. **MIXED** for standalone modern all-cost alpha. [14,20,21,23] |
| Risk/mispricing: profitability and quality | Investors may underweight durable earnings and overpay for growth stories; discount rates and investment opportunities can also rationally link profitability to expected returns. | **SUPPORTED** narrowly for slow profitability-aware diversified tilts; broader “quality” bundles are **MIXED** as distinct independent premia. Causal risk explanation remains unknown. [14,22,24,25,8] |
| Risk/mispricing: investment, issuance and accruals | Investors extrapolate expanding firms or fail to distinguish cash earnings from accounting accruals; rational investment responds to discount rates. | Do not assume these are separately paid risks. Substantial redundancy and historical disconfirmation. **MIXED**. [14,23,26,27] |
| Risk/constraints: size | Small firms can expose investors to financing, distress and illiquidity risk; benchmark demand concentrates on large firms. | Pure market-cap sorting is unreliable; conditioning away junk changes the claim. **MIXED**. [3,28] |
| Risk: carry across assets | Investors wanting insurance, currency funding, duration or commodity hedges pay those willing to hold the opposite exposures. Carry is the return if market prices are unchanged, defined separately for each asset. | Insurance and funding demand persist; selection of high carry can concentrate crash exposure. **MIXED** for generic forward net alpha. [29,30,31,32] |
| Risk: variance/volatility premium | Equity and option hedgers pay for protection against volatility and jumps. The seller provides contingent balance sheet in bad states. | Protection demand persists, but collateral, spreads, jumps and leverage determine harvestability. **MIXED**. [33,34,35] |
| Behaviour: cross-sectional momentum | Investors incorporate news slowly, extrapolate, or trade gradually under constraints. Relative winners outperform relative losers before the eventual reversal. | Strong evidence of a historical effect, but cost and rebound-crash sensitivity. **MIXED** under the requested modern net standard. [20,36,37,9,12] |
| Behaviour/flows: time-series trend | Gradual information diffusion, persistent hedging and policy flows create sustained price moves; trend traders accommodate flows after they develop. | **SUPPORTED** for diversified liquid implementation at reasonable costs; not every trend rule or backtest Sharpe. [38,39,40,41,42,65] |
| Behaviour/liquidity: reversal | Urgent investors cross the spread or temporarily move prices; liquidity providers take inventory and adverse-selection risk. Long-horizon reversal often overlaps value. | The need for immediacy persists, but profits migrate to better execution. **MIXED** for short-horizon implementations. [43,44,45,3] |
| Behaviour: lottery preferences, attention, overreaction | Optimists buy positively skewed stories or salient news; constrained pessimists cannot fully offset buying. | Behaviour plausibly persists, but “avoid overpriced stocks” is easier than borrowing and shorting them. **MIXED** for a tradable premium. [46,47,48,3,13] |
| Structural: leverage limits, low beta/BAB | Investors unable or unwilling to lever safe assets bid up high-beta assets to obtain market exposure. | Constraints persist, but beta estimation, weighting, profitability exposures and financing can explain apparent alpha. **MIXED**. [49,50,51] |
| Structural: benchmarking/career risk | Managers prefer benchmark-like holdings and may abandon unpopular trades to protect employment or retain clients. | Durable constraint, but not a separately identified portfolio premium. **INSUFFICIENT** as standalone return source. [51] |
| Structural: index and other forced flows | Index trackers, redemptions, collateral calls and mandates require transactions independent of valuation. Arbitrageurs provide anticipatory inventory. | Flows persist; transparent events attract competitors. **NOT SUPPORTED** for transplanting the old index-addition jump into a modern strategy; other forced-flow alpha is **INSUFFICIENT** without event-specific evidence. [52] |
| Structural: short-sale constraints | Miller's heterogeneous optimists set prices when pessimists cannot short; sentiment exacerbates overpricing. | Restrictions persist, but the asset owner or lender may capture the premium through borrow charges. **MIXED** as harvestable alpha. [46,48,13] |
| Structural: commodity hedging pressure | Producers, consumers and intermediaries transfer price risk; the direction and horizon of pressure vary by contract and participant. | Economic demand persists, not a universal “producers are short, therefore all futures pay” rule. **MIXED**. [29,53] |
| Structural: market making, residual stat arb | Urgent traders pay for immediate execution; makers earn spreads while absorbing informed trading, inventory and funding risk. | Service demand persists; distributable profit depends on queue position, costs and competition. **MIXED** for residual stat arb; **INSUFFICIENT** for a generic market-making investment premium. [43,45,54,55] |

“They keep paying” sometimes means they rationally prefer insurance or immediacy to expected return. It does not imply a free transfer from a consistently foolish group. Structural persistence can coexist with a vanishing arbitrage margin when intermediaries compete.

### Collapsing the factor zoo

An economical research taxonomy is **market risks; valuation; profitability/investment quality; trend/momentum; carry/insurance; defensive constraints; liquidity/flows**. This is an economic grouping, not an assertion that all its members are statistically interchangeable.

| Economic group | Jensen–Kelly–Pedersen themes | Fama–French | Hou–Xue–Zhang q-factors | Asness–Moskowitz–Pedersen across assets |
|---|---|---|---|---|
| Market risks | Market treated as benchmark; size has its own theme | Market; SMB | Market; size | Asset-class exposure distinct from style spreads |
| Valuation | Value | HML | Often partly spanned by investment/profitability; not an exact replacement | Value in stocks, equity indices, bonds, FX and commodities |
| Business quality and reinvestment | Profitability, profit growth, quality; low accruals, low investment, low debt issuance | RMW and CMA; HML may overlap | ROE profitability and investment | Not a separate theme in the cited value/momentum study |
| Trend and information diffusion | Momentum; seasonality is separate and should not be relabelled momentum | Not in the original five-factor model | No momentum factor in the basic q-model | Cross-sectional momentum; time-series trend is a different construction |
| Defensive/financing constraints | Low risk, low leverage | No exact counterpart | May be partly spanned by profitability/investment | Not an exact value/momentum counterpart |
| Liquidity/flows | Short-term reversal; size may proxy liquidity | No dedicated liquidity factor | No dedicated liquidity factor | Liquidity can influence both styles without making them one style |
| Carry/insurance | No direct equity-characteristic equivalent | No direct counterpart | No direct counterpart | Needs the separate multi-asset carry and volatility literature |

JKP's themes are statistical clusters, not a proven list of causal premia. ROE in the q-model differs from operating profitability in FF; low balance-sheet leverage differs from low market beta. A model spanning a factor says its return is explainable within that model, not that its long-only holdings are useless. [4,14,20,23]

## Q2. What survives in practice?

### Replication, publication decay and the denominator problem

McLean–Pontiff find both out-of-sample and additional post-publication decay (E1). Hou–Xue–Zhang stress economic weighting and multiple testing rather than allowing tiny stocks to determine the result. Chen–Zimmermann recover most sufficiently clear original results (E2), a valuable code and definition check. JKP pool evidence across related factors and markets (E3). These findings can coexist: exact original reproduction is an easier question than a new, cost-effective deployment. [1–4]

Later evidence is not uniformly negative. Jacobs–Müller find publication decay concentrated in the United States rather than universal across countries. Linnainmaa–Roberts find accounting anomalies weaken outside the originally discovered periods, with diversification also deteriorating. Chen–Velikov's implementation-aware anomaly averages are much smaller than canonical backtests (E4). These studies support economic grouping and conservative priors; none licenses applying one universal haircut to every strategy. [5,6,7]

### Theme-specific survival and disconfirmation search

For each row, the search covered failed replication, publication decay, cost failure and crowding/capacity. **NF** means nothing qualifying found for that specific subquestion. Broad anomaly studies are not presented as a dedicated test of a particular construction.

| Theme | Replication and post-publication | Costs, size and legs | Crowding/capacity disconfirmation |
|---|---|---|---|
| Equity ownership | Long international history plus live investability; McQuarrie rejects an invariant equity advantage in every historical era. Not a discovered anomaly to which publication decay mechanically applies. | Long-only, ample liquid capacity; fees/spreads/tax matter, but there is no short leg. | Finite aggregate risk appetite; **NF** for credible evidence that broad equity ownership's premium has permanently disappeared. [15,16,70] |
| Term | Predictable bond premia vary with the state; liability and safe-asset demand complicate an always-positive term story. Pure post-publication net test: unknown. | Duration-matched benchmark necessary; convexity and funding alter trades. | Liquid futures have depth but delivery and basis constraints; universal capacity estimate unknown. [17,18] |
| Credit | Duration adjustment sharply reduces investment-grade excess-return interpretation; longer-history evidence complicates any “credit never pays” conclusion. | Deduct defaults, downgrades, illiquidity and financing; long-short credit needs CDS/bond basis and counterparty accounting. | Liquidity disappears during stress; no verified common capacity threshold. [18,19] |
| Value | International corroboration, but long modern underperformance and sensitivity to profitability/accounting definitions; backwards and forwards accounting-anomaly tests are adverse. | Slow turnover helps. Liquid value survives some cost models; a pure premium after all costs since 2010 is unknown. Long and short contributions are not interchangeable. | Crowding possible but valuation spreads alone do not prove crowding or predict timing. [20,21,7,8,12] |
| Profitability/quality | Independent models, international OOS and a recent retrospective support profitability; broad quality packages overlap defensive and value exposures. | Slow liquid tilts fare better in cost studies. Stronger evidence for profitability than every quality ingredient; exact modern long/short net split unknown. | Sector concentration and common rankings matter. **NF** for an independent all-cost replication establishing universal disappearance of slow profitability. [14,22–25,8] |
| Investment/issuance/accruals | q/FF explanatory success versus Linnainmaa–Roberts historical failures and HXZ filters. Explanatory fit is not independent alpha. | Some slow investment spreads survive modeled costs; accrual/issuance shorts can be difficult. Exact post-2010 all-cost result unknown. | Overlapping accounting signals inflate diversification claims. [14,23,26,27,8] |
| Size | Weak pure-size replication; “size without junk” is a materially different construction. | Microcap spreads, impact and delistings can dominate. Long-only small-cap return includes market and other exposures. | Low liquidity limits scale; do not substitute academic equal weights for feasible fills. [3,28] |
| Cross-sectional momentum | Strong historical and cross-market evidence; publication decay is not universal. Rebound crashes and aggregate net anomaly evidence conflict with an unqualified modern-alpha claim. | Israel–Moskowitz find returns are not solely a tiny-stock/shorting phenomenon; institutional costs can leave value. Hard-to-borrow losers and fast turnover hurt. | Shared deleveraging and abrupt reversals; original performance cannot be scaled without a cost model. [20,36,37,9,12] |
| Trend | Independent long histories, modern live delivery and implementation studies; Kim–Tse–Wald question how much improvement comes from timing rather than volatility scaling. | Liquid futures avoid stock borrow, but rolls, spread, collateral and execution count. Cost/capacity evidence is implementation-specific. | Whipsaw and common positions; oldest reconstructed markets were not equally accessible then. [38–42,65] |
| Carry | Hsu and coauthors find post-publication declines even when data-snooping tests pass; another OOS study finds ex-ante selection of the winning carry rule unreliable. | Recent FX impact-aware work finds naïve carry/momentum allocation can become unprofitable with size; cost-aware turnover control improves it. | Funding squeezes and common high-yield positions; carry is not independent of all equity stress. [29–32,71,72] |
| Variance/short volatility | Strong economic explanation and option-implied versus realized evidence; not proof that retail option writing replicates a variance swap. Dedicated post-publication decay result: **NF**. | Margin and costs can defeat seemingly good strategies; jump risk and option spreads matter, with results varying by underlying and moneyness. | Crowded insurance selling amplifies liquidation; capacity and losses must use capital at risk, not option premium received. [33–35] |
| BAB/low risk | Original multi-asset evidence and benchmark mechanism; Novy-Marx–Velikov challenge canonical weights, beta estimates and independent alpha. | Profitability/investment exposures and microcaps can explain favorable net results; short-beta hedge requires financing. Long-only minimum-volatility is not BAB. | Leverage/funding shocks specifically threaten the mechanism. [49–51] |
| Reversal/stat arb | Liquidity interpretation is supported; HXZ reject many trading-friction anomalies under their filters. Later practitioner-academic work supports selected liquid short-signal composites. | High turnover makes spreads and adverse selection central. Market-neutral dollar exposure can conceal factor and inventory risk. | Quant unwind documents common positions, funding and liquidation. Generic residual systems are not robust capacity-independent edges. [3,43–45,55,73] |
| Pairs | Gatev's historical result is followed by Do–Faff cost and profitability deterioration; no clean universal “pairs are dead” conclusion. | Repeated formations, asynchronous quotes, dividends, borrow and unclosed positions materially change results. | Avellaneda–Lee deterioration and the quant unwind are warnings; modern net result for a frozen basic pair rule is unknown. [44,45,55,56] |
| Lottery/attention/short constraints | Observed behaviour and sentiment asymmetry; HXZ reject several lottery/volatility anomalies. | A shortable subset is not the original universe; shorting-premium evidence shows lenders may capture much of apparent opportunity. | Scarce lendable inventory, recalls and squeezes are endogenous costs. [3,13,46–48] |
| Index/forced flows | Greenwood–Sammon show a greatly diminished modern index inclusion effect (E7). | Announcement abnormal return is not an executable annual portfolio return; anticipation and closing-auction costs matter. | Competition directly compresses a publicly specified event. Other flows need their own tests; **NF** for universal decay of all forced flows. [52] |

**Long leg versus short leg.** Report both relative to cash and relative to a matched benchmark. A profitable long-short portfolio may consist of an ordinary high-beta long book and a negative-beta short book; positive absolute long returns do not establish long-side alpha. Israel–Moskowitz emphasize long-side contributions to size, value and momentum. Stambaugh–Yu–Yuan find sentiment-linked mispricing concentrated on shorts. These statements concern different signals and conditioning. They are not contradictory universal rules. Borrow fees, manufactured dividends, recalls and financing must enter daily short P&L. Numeric modern splits by theme remain unknown here. [12,13,48]

**Pairs and residual stat arb.** Gatev's final paper uses an entirely pre-2010 historical sample. Distance matching is not cointegration, and neither guarantees future convergence. Avellaneda–Lee remove common PCA or ETF exposures and trade mean-reverting residuals; their reported decline within the old sample is E6, not post-publication validation. Khandani–Lo explain why correlated liquidations can overwhelm residual stationarity during the August 2007 unwind. Neutralizing market beta does not neutralize funding risk. Evidence about current institutional systems is generally proprietary; their net edge is unknown. [44,45,55,56]

### End-of-Q2 survival table

“Unknown” is a numerical audit result, not a claim of zero. Capacity is a qualitative comparison at the same execution horizon; no universally valid dollar limit was verified.

| Theme | Gross premium | Net premium | Post-2010 | Long leg / short leg split | Capacity | Verdict |
|---|---|---|---|---|---|---|
| Equity | Positive historical aggregate; exact comparable figure unknown | Forward magnitude unknown; inexpensive live implementation | Live long-only evidence | Long only | High in broad liquid exposure | SUPPORTED |
| Term | State-dependent; magnitude unknown | Unknown | Pure matched net test unknown | Long duration / cash or duration hedge | High liquid futures | MIXED |
| Credit | Spread is not premium; magnitude unknown | Duration-matched residual contested | Modern critique [18] | Long credit / matched Treasury or CDS hedge | Medium; stress-sensitive | MIXED |
| Value | Historically documented; magnitude unknown | Some modeled-cost survival; all-cost magnitude unknown | Conflicting; pure all-cost unknown | Both contribute; exact split unknown | High slow liquid; lower small caps | MIXED |
| Profitability | Documented; exact audited magnitude unknown | Slow liquid evidence favorable; magnitude unknown | OOS and recent retrospective; pure all-cost number unknown | Exact split unknown | Relatively high at slow horizon | SUPPORTED narrowly |
| Investment/accounting | Documented but contested | Some slow designs; magnitude unknown | Replication conflict | Shorts can dominate specific anomalies; unknown | Medium/high slow liquid | MIXED |
| Pure size | Unstable | Unknown, illiquidity erodes | Weak/definition-sensitive | Mainly a long-only exposure in many uses | Low in microcaps | MIXED |
| Cross-sectional momentum | Robust historical existence | Cost-sensitive; forward magnitude unknown | Live long-only E10; pure net spread unknown | Not exclusively shorts; exact split unknown | Medium; turnover-limited | MIXED |
| Time-series trend | Documented across assets | Live total return E8; pure excess magnitude unknown | Direct live net evidence | Dynamic long and short contracts | High liquid; lower exotic markets | SUPPORTED narrowly |
| Carry/hedging pressure | Documented, crash-prone | Cost/size-dependent; magnitude unknown | Decay and OOS conflict | Asset-specific long/short | High core FX; lower exotic assets | MIXED |
| Variance premium | Documented gross variance compensation | Instrument/collateral-dependent; unknown | Pure all-cost test unknown | Short insurance; hedge pays premium | Medium with tail-capital limit | MIXED |
| BAB/defensive | Historically favorable | Construction/spanning disputes | Independent pure net unknown | Levered low beta / short high beta | Medium/high liquid | MIXED |
| Reversal/residual stat arb/pairs | Old favorable examples E6 | Modern frozen-rule magnitude unknown | Generic rule unverified | Both inventory legs; borrow matters | Low at fast horizons | MIXED |
| Lottery/attention shorts | Mispricing evidence | Lender/impact may absorb; unknown | Pure all-cost unknown | Mainly avoidance or difficult short leg | Low | MIXED |
| Old index-addition rule | Old event gain E7 | Modern annual net unknown | Effect greatly diminished | Event inventory / benchmark hedge | Competitive auction capacity | NOT SUPPORTED |
| Generic market making | Case-study profit, not broad premium | Unknown | General investment claim unverified | Inventory and quote book | Venue/latency-specific | INSUFFICIENT |

## Q3. Implementation and requirements

### Structures are part of the strategy

| Structure | What it buys | Requirements and limitations |
|---|---|---|
| Long-only tilt | Equity premium plus relative overweight to a style | Cash securities/ETF access, benchmark-relative risk controls; cannot fully express negative signals; taxable gains on rebalancing |
| Hedged long-only | Long-selection exposure with index beta reduced | Margin plus liquid index futures or ETF hedge; estimated beta drifts; factor and sector risks remain |
| 130/30 | Limited shorts finance additional long exposure | By definition 130% long and 30% short; these are structure parameters, not empirical findings. Borrow, margin, dividend and financing accounting required; net long market exposure remains |
| Market-neutral long-short | More direct relative-value signal expression | Prime/retail borrow, cash/margin reconciliation, risk model, forced-close handling; dollar neutrality is not beta neutrality |
| Futures | Trend, duration, FX, commodity and some carry exposures | Futures permission, contract multipliers, expiry/roll, margin buffer, collateral yield, delivery prevention; broker minimum margin is not minimum practical capital |
| Options | Nonlinear insurance and volatility exposure | Options permission, complete chains, execution across legs, exercise/assignment, Greeks, jump and margin stress; a covered call bundles equity with short optionality |
| Swaps/forwards | Custom total-return, FX, volatility and credit exposure | Counterparty access, legal documentation, collateral and valuation operations; funding and embedded costs opaque relative to listed instruments |

### Minimum capital: calculate it, do not invent a universal threshold

Minimum practical capital is **unknown** as a universal dollar figure for every theme. Compute it from diversification, smallest contract risk, worst-case margin, fixed costs and the account's ability to survive a stress liquidation. A fund-share route and self-implementation have very different minima.

For a contract with multiplier \(M\), price \(P\), annual volatility \(\sigma\), and desired single-contract risk allocation \(b\), capital should satisfy approximately \(A\ge MP\sigma/b\), then pass separate margin and gap-risk tests. This is a sizing calculation, not a safety guarantee. Hypothetical contract exposure of $50,000, volatility 20% and permitted risk contribution of 2% requires $500,000 by this rule. A smaller contract or fund wrapper changes the answer; the inputs must be verified from the chosen instrument.

Fixed-data affordability: annual fixed expense \(F\) consumes \(F/A\) of capital. For the verified Norgate price in D1, a hypothetical $10,000 account would spend 6.3% of capital on that subscription alone; that calculation is not a return forecast. Its current fundamentals do **not** supply a point-in-time history sufficient for fundamental-factor testing.

| Theme | Data and infrastructure | Permissions, operations and skills | Who has the practical advantage? |
|---|---|---|---|
| Equity/slow value/profitability/investment | Adjusted and unadjusted prices, delistings, filing-time fundamentals; daily batch pipeline, reproducible accounting | Cash account for long-only; accounting, portfolio exposure control, tax lots | Individual: cheap ETFs and patient small book. Institution: data, optimizer, financing; size can hurt less-liquid variants |
| Size/lottery/attention | Delisted universe, float/liquidity, corporate actions, point-in-time news; borrow snapshots for shorts | Margin and borrow, squeeze/recall handling, accounting for distributions | Avoidance is accessible; full short strategy usually favors institutions with lending access |
| Cross-sectional momentum | Total returns, dividends, corporate actions and liquidity; batch daily/monthly | Cash for tilt, margin for hedge/shorts; turnover and crash controls | Individual avoids impact; institution gets cost-efficient crossing and risk models |
| Trend/term/carry | Individual futures/forwards, rates, full term structures, contract metadata; daily risk and margin reconciliation | Futures or FX permissions; contract mechanics, rolling, collateral and gap stress | Advanced individual can trade liquid contracts; institutions broaden markets and financing |
| Credit | Bond transactions/quotes, ratings, defaults, duration, callable features, CDS where relevant | Bond access or institutional derivatives; pricing, legal/counterparty and liquidity work | ETF access is individual-friendly; pure credit relative value is largely institutional |
| BAB | Stable beta estimation plus covariance and financing data | Leveraged longs and index/stock shorts; model instability and funding tests | Cheap defensive fund can be accessible; canonical hedged BAB favors financing scale |
| Variance | Expired options, bid/ask quotes, underlying prices, yield/dividends, Greeks and exercise data | Options or swap access, nonlinear risk, collateral and assignment; daily/intraday controls | Simple listed exposure accessible; diversified delta-hedged/swap execution institutional |
| Reversal/pairs/residual stat arb | Synchronized quotes, intraday corporate actions, point-in-time borrow and fill data | Shorts, broker API, inventory control; econometrics, execution and monitoring | Small size helps capacity; execution access remains a major handicap |
| Market making/flow arbitrage | Order book, trades, exchange events and own queue/fill history | Venue access, low latency, failover, inventory/funding; microstructure and systems engineering | Institutions/specialist firms; slow public-event research is not equivalent |

Approximate dataset prices and inaccessible inputs are addressed in Q6. Daily styles do not require a colocated server. Fast liquidity provision may require exchange proximity and specialized market data, but an exact latency threshold is unknown and venue-specific.

### The complete cost stack

Use a trade-level ledger, not one generic commission haircut:

\[
r_{net}=r_{gross}-\text{spread}-\text{impact}-\text{commissions/fees}-\text{borrow}-\text{financing}-\text{data/operations attributable to capital}.
\]

Taxes require a separate account-level path calculation. Do not subtract an annual tax rate from every gross trade return.

Spread must reflect executable sides, order type and time. Passive quotes avoid crossing but incur nonfills and adverse selection. Impact is commonly approximated by \(I(Q)=Y\sigma\sqrt{Q/V}\), where \(Q/V\) is participation relative to relevant volume and \(Y\) is calibrated. Holding horizon, execution duration and regime matter; the square-root approximation is not a universal physical law. With hypothetical \(Y=0.5\), daily volatility 2% and \(Q/V=1\%\), average impact is 10 basis points; at 4% participation it is 20 basis points. Cost dollars approximately scale with \(Q^{3/2}\) in this stylized model. [57]

Borrow is stock/date-specific, not a constant; unavailable names must be excluded before scoring a tradable strategy. Financing includes the rate paid on debit balances and the rate actually earned on cash/short proceeds. Notional exposure is not funded equity capital, and a futures contract is not costless leverage. Retail and institutional financing differences can dominate small premia. [9,13]

In a US taxable account, short holding periods generally lose long-term capital-gain treatment; short sales have special rules and commonly produce short-term gains. Dividend qualification, payments in lieu, wash sales, straddles, constructive sales and loss limitations complicate hedged books. Eligible section 1256 contracts are marked to market and generally split gains/losses into **“60%” long-term and “40%” short-term** treatment, regardless of holding period; not all options, FX instruments or derivatives qualify. Those quoted statutory proportions are not return evidence; sample/portfolio/IS status are not applicable. [69]

### Breadth, constraints and signal decay

The fundamental law is an approximation:

\[
IR\approx IC\sqrt{B}\,TC.
\]

IC is correlation of forecasts with subsequent residual returns; breadth \(B\) counts independent opportunities, not raw rows; transfer coefficient \(TC\) measures how faithfully risk-adjusted holdings express forecasts. With illustrative IC 0.03, breadth 100 and TC 0.5, predicted IR is 0.15; unconstrained TC 1 would give 0.30. Costs, unequal skill, correlated bets, forecast error and changing covariance break the approximation. Long-only and benchmark constraints can reduce TC, although avoiding expensive shorts can improve **net** outcomes. [58]

For exponentially decaying information \(s(t)=s_0e^{-\lambda t}\), half-life is \(\ln2/\lambda\). Estimate decay on independent observations after transaction timing, rather than interpreting the lookback length as the signal's half-life. Numeric half-lives by theme are unknown here. Slow accounting signals tolerate patient trading; very short reversal signals may die before the order fills.

Gârleanu–Pedersen's quadratic-cost framework trades partly toward an aim that incorporates expected future targets rather than immediately to the current frictionless target. Proportional costs can produce no-trade regions; do not confuse that with the exact quadratic-cost solution. Empirical buy/hold bands are supported by cost studies. Optimize **when to change a holding**, not just which names to rank. [8,59]

### Compounding and leverage

Arithmetic mean is not compound growth. In a small-return/lognormal approximation, \(g\approx\mu-\sigma^2/2\). For a financed continuous-return portfolio, \(g(L)\approx r_f+L\mu_{ex}-L^2\sigma^2/2\), less financing above the assumed risk-free rate and other costs. Hypothetical excess drift 6%, volatility 20% and zero cash rate imply approximate growth of 4% at leverage 1, 4% at leverage 2 and 0% at leverage 3. These are model outputs, not realistic return assumptions.

A 50% drawdown needs a 100% gain to recover. Bankruptcy, broker liquidation, increasing margin and discrete price gaps are absent from the smooth approximation. Leverage must be constrained by funding survival as well as forecast volatility.

### What live products establish

E8–E11 give official post-launch net records. Managed futures and multi-premia funds demonstrate that real implementations can produce positive long-run results. They do not establish their underlying backtest Sharpe, a universal style premium or a fixed future return. The momentum ETF's total return is mostly equity exposure; compare a parent benchmark and regression alpha rather than call the entire NAV return “momentum alpha.”

An exact like-for-like comparison with each product's frozen, prelaunch, risk-matched backtest is **unknown** in the material reviewed. Index splicing, changing fund processes, different collateral yields and manager changes prevent casually subtracting a marketing backtest from live NAV. The sample is selected and not a comprehensive survivor-free universe of factor ETFs, alternative risk-premia funds or CTAs. [65–68]

## Q4. How to know whether an edge is real

All numerical examples here are **worked calculations under stated assumptions**, not observed returns. Source URLs are in [58,60–64,74]. This distinction is essential to avoid assigning a historical sample or an “exact quoted figure” to a calculation generated for this report.

### 1. Sharpe sampling error, non-normality and years required

Let per-period excess returns have mean \(\mu\), volatility \(\sigma\), Sharpe \(s=\mu/\sigma\), skewness \(\gamma_3\), raw kurtosis \(\gamma_4\), and \(n\) independent observations. A large-sample approximation is

\[
SE(\hat s)\approx\sqrt{A/n},\qquad A=1-\gamma_3s+\frac{\gamma_4-1}{4}s^2.
\]

Normal returns give \(A=1+s^2/2\). The probabilistic Sharpe ratio relative to benchmark \(s_0\) is approximately

\[
PSR(s_0)=\Phi\left(\frac{(\hat s-s_0)\sqrt{n-1}}{\sqrt{1-\hat\gamma_3\hat s+((\hat\gamma_4-1)/4)\hat s^2}}\right).
\]

PSR is a frequentist tail calculation under an asymptotic model, not a posterior probability that the strategy is true. If monthly observations are IID, annual \(S=\sqrt{12}s\). For a small Sharpe, \(SE(\hat S)\approx1/\sqrt{Y}\), where \(Y\) is years.

| True annual Sharpe (assumed) | Expected test statistic reaches one-sided 95% threshold | One-sided 5% test with 80% power | Expected statistic reaches two-sided 95% threshold |
|---|---:|---:|---:|
| 0.2 | 67.6 years | 154.6 years | 96.0 years |
| 0.3 | 30.1 years | 68.7 years | 42.7 years |
| 0.5 | 10.8 years | 24.7 years | 15.4 years |
| 1.0 | 2.7 years | 6.2 years | 3.8 years |

The formulas are \(Y\approx(z_{.95}/S)^2\), \(Y\approx[(z_{.95}+z_{.80})/S]^2\), and \((z_{.975}/S)^2\). “Expected statistic reaches threshold” provides roughly even odds of significance, **not** high detection power. These approximate null-variance calculations ignore costs, selection, regime change and small finite-sample adjustments. [60,61]

Worked non-normal example: assume 120 monthly observations, sample monthly Sharpe 0.10, skewness −1 and raw kurtosis 6. Then \(A=1.1125\), PSR against zero is approximately 0.849. Negative skew and excess kurtosis reduce confidence relative to a normal approximation. Estimating high moments from a short history is itself unreliable.

Serial dependence invalidates square-root annualization. For monthly autocovariances \(\gamma_k\),

\[
Var\left(\sum_{t=1}^{m}r_t\right)=m\gamma_0+2\sum_{k=1}^{m-1}(m-k)\gamma_k.
\]

Annual mean is \(m\mu\); divide it by the square root of that variance. An illustrative stationary AR(1) with correlation 0.2 has long-horizon effective mean-estimation sample approximately \(n(1-0.2)/(1+0.2)=2n/3\). The mean-based observation requirement therefore grows about one-half. Sharpe inference needs the joint mean/variance dependence treatment or a valid block bootstrap, not just this shortcut. [60]

### 2. Multiple testing, deflation and backtest length

**HLZ.** The quoted **“greater than 3.0”** t-statistic hurdle is a factor-zoo response to research selection, not a universal significance rule for all projects. Its empirical universe and assumptions matter. A t-statistic also needs a dependence-robust standard error. [62]

**False discovery rate.** Benjamini–Hochberg sorts \(p_{(1)}\le\cdots\le p_{(m)}\), chooses the largest \(k\) with \(p_{(k)}\le kq/m\), and rejects through \(k\). Under appropriate independence or positive dependence, it controls the expected false-discovery proportion. With illustrative \(m=10,q=.05\), and first p-values .001, .004, .012, .030, the first three pass thresholds .005, .010, .015; the fourth fails .020, assuming none later passes. It does not say every accepted signal has only a 5% probability of being false. Arbitrary dependence requires a suitable adjustment, such as the harmonic-factor correction, or resampling. [74]

**Deflated Sharpe ratio.** Replace \(s_0\) in PSR with a selection-adjusted expected maximum. A common approximation for \(N\) independent, approximately Gaussian trial Sharpes is

\[
s^*=\bar s+\sigma_s\left[(1-\gamma)\Phi^{-1}(1-1/N)+\gamma\Phi^{-1}(1-1/(Ne))\right],
\]

where \(\gamma\) is Euler's constant and \(\sigma_s\) describes trial Sharpe dispersion under the relevant null. DSR then incorporates skew/kurtosis via the PSR denominator. For an illustrative hundred independent zero-edge trials, ten years per trial and annual Sharpe standard error \(1/\sqrt{10}\), the expected winning Sharpe is approximately 0.80. A selected winner with Sharpe 0.80 consequently has roughly a one-half deflated confidence level, not compelling evidence. Correlated trials, unequal variances and an incorrectly specified null can break this calculation. [61,63]

**Minimum track-record length.** For a chosen benchmark and confidence, the PSR approximation gives

\[
n_{min}\approx1+A\left(\frac{z_{1-\alpha}}{\hat s-s_0}\right)^2.
\]

It assumes the observed effect and moments are stable; plug-in estimates can make a lucky winner appear to require too little data. A separate illustrative conservative familywise calculation uses Bonferroni: at annual Sharpe 0.5, a hundred trials and one-sided familywise error 5%, \(Y\approx[z_{1-.05/100}/.5]^2=43.3\) years just to reach the expected threshold. Detection power requires more. DSR and familywise control answer different questions. [61–63]

**Probability of backtest overfitting (CSCV).** Split a common return panel into blocks, train/select the best candidate on one subset, rank that selected candidate in its complement, and examine how often its OOS rank is below the median. With higher return assigned higher relative rank \(\omega\), use \(\lambda=\log[\omega/(1-\omega)]\); estimated PBO is the fraction with \(\lambda<0\). Eight illustrative blocks give 70 half-sample combinations; 28 below-median outcomes give PBO 40%. This diagnoses selection instability. It is not a probability the economic hypothesis is false, and nonchronological complements can train on the future. CSCV therefore supplements rather than replaces chronological holdouts. [64]

**Trial budgeting.** Count changes to universe, labels, lag, weighting, costs, winsorization, filters, stopping rules, architecture and hyperparameters whenever performance is consulted. Human judgment consumes trials too. Keep both a raw count and a conservative effective-trial range; correlated variants are not fully independent, but a small effective count must be justified. A proposed program design is a dozen prespecified variants per economic hypothesis, a separate discovery dataset and one locked evaluation package. These are governance choices, not empirically optimal numbers. Failed trials remain in the register. Changing the hypothesis after a holdout failure starts a new family and requires new evidence.

### 3. Attribution and spanning

Regress excess returns on economically relevant tradable factors:

\[
r_t-r_{f,t}=\alpha+\beta'f_t+\epsilon_t.
\]

Use market, size, value, profitability, investment and momentum for equities; include duration, credit, carry/trend and volatility exposure where relevant. Match currencies, dates, liquidity and costs. HAC errors handle temporal dependence; resampling and multiple-test adjustment handle model selection. An omitted crash factor or a nonlinear option exposure can masquerade as alpha.

Worked example: assumed monthly alpha 0.20%, residual volatility 2%, and 120 independent months gives \(SE(\alpha)=2\%/\sqrt{120}=0.183\%\), t approximately 1.10. An attractive raw return does not establish incremental alpha. A spanning test asks whether adding the strategy improves the feasible investment opportunity set; joint restrictions, financing and long-only feasibility matter. A factor regression alone does not answer whether an investor can obtain the same exposure more cheaply. [14,23,50,60]

### 4. Cross-sectional evidence versus one return series

An IC measures correlation between a forecast and subsequent cross-sectional **residual** returns, often rank correlation. Compute one IC per independent decision period and test its mean with time dependence accounted for. Do not pool overlapping labels and pretend every stock-date is independent.

Worked example: a hypothetical monthly IC mean 0.02, standard deviation 0.06 and 120 independent months gives \(t=.02/(.06/\sqrt{120})=3.65\). That is evidence of ranking skill under those assumptions, not a portfolio net-return estimate. Weighting, turnover, factor exposures and short feasibility still determine monetization.

For the simpler mean of equally variable equicorrelated observations, \(N_{eff}=N/[1+(N-1)\rho]\). A hypothetical hundred stocks with pairwise residual correlation 0.10 provide only about 9.17 independent observations for that mean. This is an illustration of clustering, **not** a universally valid IC effective-sample formula. Sector/country clusters, common forecast errors and overlapping outcomes require clustered or block inference. [58,60]

### 5. Distributions and regimes

Fat tails, negative skew, volatility clustering and changing regimes affect estimated means, covariance, Sharpe, multiple-testing nulls and leverage survival. Momentum can crash during rapid loser rebounds; carry and short volatility sell bad-state insurance. Trend has whipsaw and gap vulnerability. A stable full-sample mean can hide materially different conditional distributions. [30,37,49,55]

Worked path: assume 99 monthly returns of +1% followed by −50%. The arithmetic monthly mean is +0.49%, while wealth becomes \(1.01^{99}\times.5\approx1.339\) times starting wealth, with a final 50% drawdown. A positive average conceals a devastating funding event. These invented returns are not a fitted market model.

Use dependence-preserving bootstrap, stress episodes, alternative block lengths, conditional exposures and drawdown/margin scenarios. If the fourth moment is infinite or unstable, the PSR moment approximation is not trustworthy. If the distribution changes, more old data can estimate the wrong present-day process very precisely. Report sensitivity rather than one confidence number.

### 6. Validation design

Freeze universe eligibility as known then; include delisted and acquired names; use publication/filing timestamps, historical index membership and contemporaneous borrow. Preserve original and restated fundamentals separately. A total-return field adjusted using later corporate actions can be useful for P&L but must not introduce future knowledge into tradability or raw-price signals.

Worked timestamp example: a fictional company has a December year-end and files on 15 March. Its new figures cannot enter a January portfolio. A prespecified pipeline could observe the filing on 16 March and trade on 17 March after processing; these dates are hypothetical. Assuming an arbitrary reporting lag is weaker than knowing the actual filing timestamp, and historical reporting delays may differ.

Chronological walk-forward evaluation refits only on available history. Keep a final lockbox or genuinely later period separate from model development. Purge training labels whose return interval overlaps validation; embargo boundary observations when feature/label dependence warrants it. Embargo duration must be justified by information flow, not chosen to improve scores. Group securities where common events could leak across folds.

Paper trading verifies data arrival, orders, fills, borrow, reconciliation and operational failure. It has little power to establish a low-Sharpe return edge over a short interval, and simulated liquidity does not verify capacity. Release small real capital only after these operational tests, with explicit limits and an audit trail.

## Q5. Building a portfolio of premia

### Correlations: structural reasoning before optimization

| Relationship | Long-run evidence/interpretation | Since-2010 coefficient |
|---|---|---|
| Value–cross-sectional momentum | Negative correlation in the AMP multi-asset study; useful style diversification | **Unknown** for a comparable all-cost, fixed-construction panel |
| Carry–trend | Different conditioning: carry holds remunerative exposure, trend changes direction; can offset sustained carry unwinds, can also align | **Unknown**; no universal sign |
| Equity–trend | Trend beta changes sign with trends and has helped in some sustained crises; not a permanent negative-equity-beta hedge | **Unknown** for matched live net exposures |
| Equity–credit | Both exposed to bad economic states; duration can obscure credit's equity sensitivity | **Unknown** matched credit-only coefficient |
| Equity–short variance/carry | Adverse co-losses in funding/crash states despite benign normal-period correlation | **Unknown** for fully matched strategies |
| Profitability–low risk–quality | Overlap in defensive holdings and exposures; separate labels overstate independence | **Unknown** after spanning and costs |
| Reversal/stat-arb books | Ordinary market neutrality can hide common liquidations and funding exposures | Crisis correlation not stable; numeric coefficient **unknown** |

Do not fill this table with coefficients from different leverage, asset universes or return denominators. A defensible program should rebuild a common panel and report rolling and stress correlations, uncertainty and costs. [20,29,30,37,38,50,55]

Illustrative diversification: two equal-weight strategies each with 4% expected arithmetic excess return, 10% volatility and correlation −0.3 have portfolio volatility about 5.92% and Sharpe about 0.68, versus 0.40 separately. If correlation becomes +0.7, volatility is about 9.22%. These assumed inputs demonstrate covariance arithmetic; they are not estimates of value and momentum.

### Tail protection and volatility scaling

| Theme | Principal bad state | Mitigation and its limitation |
|---|---|---|
| Equity, value, size, credit | Economic distress, liquidity loss, valuation/rate shock; value-specific long drought | Diversify countries/styles, reduce concentration and funding dependence; market hedge also removes equity premium |
| Momentum | Sudden rebound of previously distressed losers | Exposure caps and liquidity-aware shorts; volatility estimates may lag the reversal |
| Trend | Choppy reversals or gaps before positions can change | Diverse horizons/markets, position limits; cannot ensure immediate crash protection |
| Carry/short variance | Crash, volatility jump, funding squeeze | Collateral, bounded losses, insurance or trend complement; hedges cost premium and may have basis risk |
| BAB | Financing squeeze and beta-estimation changes | Conservative leverage, stable estimation and profitability attribution; mechanism itself depends on funding |
| Reversal/stat arb | Informed order flow, broken relationship, common deleveraging | Inventory limits, event exclusions, borrower diversification, exit rules; stops cannot promise fills |

Moreira–Muir show attractive volatility-managed historical portfolios. Cederburg and coauthors confirm some spanning results while finding practical OOS combinations often disappoint. Recent individual-factor evidence also questions automatic net benefits. Distinguish **risk targeting**, useful for exposure control, from a claim of incremental timing alpha. A risk estimate falling in a tranquil regime must not automatically permit unlimited leverage. [75,76,77]

The critique is not the last word: DeMiguel–Martín-Utrera–Uppal find favorable OOS, cost-adjusted results for their conditional **multifactor** construction, which nets trades and optimizes with costs. That supports further construction-specific testing, not automatic scaling of each factor independently. Its precise incremental magnitude is not used here. [77]

### Combining signals versus combining portfolios

An integrated portfolio aggregates standardized forecasts then optimizes holdings once; it can net opposing trades and control shared exposures. A mixed portfolio allocates to separately constructed sleeves; it is easier to audit and preserves different horizons and constraints. Integration can obscure which sleeve generated costs and profits; sleeve mixing can duplicate trades and inadvertently concentrate common exposures. Neither is universally superior.

Start with simple, prespecified weights across economically distinct sleeves; standardize risk without using future observations. Estimate covariance with shrinkage rather than invert a noisy raw matrix. Ledoit–Wolf shrinkage is \(\hat\Sigma=(1-\delta)S+\delta F\), with an appropriate structured target \(F\) and shrinkage intensity estimated from data. Shrinkage does not correct a stale regime or incorrect factor definition. [78]

| Sizing rule | Formula/idea | Where it fails |
|---|---|---|
| Equal weight | \(w_i=1/N\) | Equal capital is not equal risk; duplicated factors receive multiple allocations |
| Volatility parity | \(w_i\propto1/\hat\sigma_i\) | Ignores correlations/tails and adds leverage to low-volatility exposures |
| Risk parity | Equalize \(w_i(\Sigma w)_i\) contributions | Correlation estimation and leverage/funding; tail risk unequal |
| Mean–variance | \(w\propto\Sigma^{-1}\mu\), with constraints | Expected means are very noisy; unconstrained solutions extreme |
| Fractional Kelly | Fraction of growth-optimal leverage; scalar diffusion \(L^*=\mu_{ex}/\sigma^2\) | Estimated drift error, fat tails and path-dependent liquidation |

DeMiguel–Garlappi–Uppal show how estimation error makes 1/N difficult to beat in many studied settings; this is not proof equal weighting is optimal for any collection of funds. Weight optimization is another research family consuming trials. [79]

Worked Kelly example: hypothetical excess drift 6% and volatility 20% imply full-Kelly leverage 1.5 and half-Kelly 0.75 in the diffusion model. These are not recommendations. Apply concentration, gap, margin and financing limits before considering a fraction of a highly uncertain optimum.

### Live multi-premia delivery

The official Style Premia record (E9) demonstrates positive decade-long NAV performance net of expenses alongside large differences between horizons and material tax drag. Its return includes collateral and a changing proprietary combination; it cannot identify the expected return of a universal equal-risk basket. The reviewed examples are from one manager, so they are **not independent corroborations of manager skill**. A survivor-free, risk-matched, all-provider estimate of what diversified multi-premia portfolios have delivered live is **unknown** in this review. [66]

## Q6. Data: what is needed and what it costs

Free data can support education and benchmark replication. Research on fundamental equities still requires reconstructing what was actually available at the decision date. Public access is not the same as a clean historical research database.

| Theme | Required inputs | Sources and approximate cost | History and point-in-time pitfalls |
|---|---|---|---|
| Equity ownership | Total returns, shares, delistings, exchange/country definitions | French research library and official index/fund histories: free access; CRSP via licensed institutions: price **unknown** | Broad history across regimes; delisting returns, mergers and universe membership |
| Value, profitability, investment, issuance/accruals | Historical as-filed statements, filing times, prices, shares and corporate actions | SEC EDGAR: free; CRSP/Compustat/WRDS: quote/licence **unknown**; Nasdaq Sharadar SF1: current price **unknown** | Enough earlier statements for construction plus long independent validation; restatements, accounting changes, fiscal-year alignment and filing lags |
| Momentum, size, low beta | Adjusted return history plus raw prices, float, liquidity and delistings | Norgate historical prices/membership: D1; CRSP: **unknown**; free public price services incomplete | Required lookback plus independent regimes; dividends, spinoffs, stale prices, float and later membership changes |
| Trend/term | Individual futures settlement/quotes, contract specs, delivery and roll calendars; interest rates/collateral | Exchanges, Databento and institutional feeds: metered/quote price **unknown**; FRED macro/rates: free access | All contracts and rolls, not only stitched continuous series; back-adjusted levels are not P&L; collateral and rate changes |
| Carry/hedging pressure | Forward rates or futures curves, spot, synchronized bid/ask, rates, inventory and positions | CFTC commitments: free; exchange curves/FX feeds: price **unknown** | Publication-time COT availability, classifications, maturity selection, seasonality and FX funding conventions |
| Credit | TRACE transactions, evaluated quotes, ratings/defaults, accrued interest, duration/callability, CDS | FINRA public resources plus licensed historical TRACE/WRDS and institutional providers: full research cost **unknown** | Distressed/delisted bonds, sparse trade marks, duration, defaults and quote versus transaction availability |
| Variance | Complete expired option chains, NBBO, dividends/rates, exercise conventions, underlying quotes | OptionMetrics/IvyDB, exchange vendors, Databento: price **unknown** | Surviving contracts only create bias; stale/crossed quotes, multi-leg executable prices, assignment and model Greeks |
| Reversal/pairs/stat arb | Synchronized quotes/trades, borrow, order/fill outcomes, actions and event flags | Exchange/Databento prices **unknown**; broker own fills; S&P/Markit securities-finance or equivalent institutional lending data: **unknown** | Timestamp consistency, quotes versus trades, availability/recalls and order-queue assumptions |
| Attention/text/ML | Timestamped news/filings/estimates, model versions and original releases | EDGAR free; I/B/E/S, FactSet, LSEG and news archives: price **unknown** | Analyst revisions, archive revisions, content arrival, security mapping, model pretraining cutoff |

Construction lookback is different from validation length. A proposed twelve-month momentum score needs that lookback to run; it does not prove an edge with a year of performance. The idealized Sharpe validation years are in Q4. Low-Sharpe styles may never be convincingly validated from one's own short live track record, so independent market evidence matters.

**D1 — verified price, not return evidence.** Norgate US Platinum quotes **“12 months = USD 630”**; US Diamond quotes **“12 months = USD 787.50”**. Price observed at the cited vendor page during this review; historical-return sample, gross/net performance, IS/OOS and portfolio structure are not applicable. Packages differ in historical coverage. Current fundamental fields must not be mistaken for a historical point-in-time fundamentals product. Vendor pricing is Tier 3 and is used only for cost/access, never to establish returns. [80]

SEC APIs provide original filings and extracted XBRL, but a naïve “latest company facts” download is not automatically a point-in-time accounting panel. Preserve accession, filed date, period, unit and taxonomy; trace each value to the filing available then. [81]

Free-source entry points: [French factor library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html), [FRED/ALFRED API](https://fred.stlouisfed.org/docs/api/fred/overview.html), and [CFTC historical COT files](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm). French's research returns are not raw tradability data; use archived versions when revisions matter. FRED's latest macro observations can contain revisions; ALFRED vintages are preferable for historical forecasting. COT observation dates precede public availability, and classifications/revisions require care. [92–94]

CRSP/Compustat are not literally impossible for every individual to obtain: access may be institutional, academic or individually licensed if available. Their retail affordability and licensing terms are **unknown**. Original papers depending on these histories cannot be claimed replicated with a surviving-ticker web download. Analyst estimates, historical borrow, executable FX quotes, options archives and institutional execution histories often remain the decisive inaccessible or expensive inputs. [9,13,82,83]

## Q7. Where machine learning fits

Gu–Kelly–Xiu demonstrate nonlinear cross-sectional prediction on an explicitly held-out historical US period (E12). Their attractive gross long-short Sharpe is not a modern all-cost retail result. It is stronger evidence than an in-sample neural-network chart, but researcher choices and later reuse of the test period still matter. A future publication-date holdout is a stronger test. [82]

Avramov–Cheng–Metzker find performance worsens under economic restrictions, especially removing microcaps, distressed and high-volatility stocks, and incorporating frictions. They also find profitable long positions and recent-period opportunities: the correct conclusion is not that all ML profit is necessarily tiny-stock shorts. The net incremental value over a fair regularized linear comparator is **unknown** across a common liquid universe in the reviewed evidence. [83]

Later feature-engineering research finds that indiscriminately expanding predictors can worsen OOS performance relative to curated inputs and a simpler recursive ranking approach. Its own full shorting/impact audit remains incomplete. New implementable-frontier research is relevant, but a fully verified comparable incremental return figure was not available here. These are reasons to investigate disciplined ML, not to turn a gross forecasting improvement into a forecast return. [84,85]

IPCA instruments latent factor loadings with observed characteristics; autoencoders model nonlinear latent-factor structure. Better pricing fit or lower forecast error does not establish independent tradable alpha after fees and frictions. Latent factors can be a sophisticated compression of already familiar exposures. [86,87]

| Use | Defensible role | Honest test |
|---|---|---|
| Signal combination | Regularize correlated known signals; permit limited interactions | Same universe/labels/costs versus equal-weight ranks, ridge and elastic net; locked OOS |
| Covariance/denoising | Stabilize noisy risk estimates | Realized exposure forecast and portfolio loss, not only reconstruction error |
| Volatility forecasting | Improve risk and margin forecasts | Forecast calibration and risk outcomes; no automatic claim of timing alpha |
| Nonlinear interactions | Test economically motivated state dependence | Compare nested linear model; count interaction search; ensure sufficient observations per state |
| Execution | Forecast fill probability, impact and adverse selection using own trades | Real executable cost/fill improvement under randomized or controlled implementation |
| Validation tooling | Automated lineage, purging, embargoes and leak tests | Chronological availability and independent code review; automation cannot repair contaminated data |

Deep learning on raw prices, trading reinforcement learning and unconstrained hyperparameter search have **INSUFFICIENT** general evidence to recommend as standalone alpha programs here. This is not a claim no specialist can succeed. Their action space, nonstationarity, synthetic-fill assumptions and adaptive search frequently create more degrees of freedom than independent outcomes.

Text and LLM models have a distinctive hazard: a model pretrained on later news can encode future outcomes even when prompted with an old document. Historical company identifiers and generated summaries can leak too. Point-in-time checkpoints, permissible contemporaneous corpora and chronological evaluation are necessary. Recent point-in-time language-model work indicates credible testing is possible; net portfolio alpha remains **unknown** for a generic LLM signal. [88,89]

Guardrails: pin datasets and model versions; preserve all trials; use a cost-aware objective and liquid feasibility filters; benchmark simple models; refit only on available data; purge overlapping labels; stress the short leg; report gross, partial net and all-cost net separately; run factor spanning; and require genuinely later evidence before escalating capital. Optimizing validation folds is itself research selection.

## Decision table: themes and practical accessibility

This is the requested theme table, split into two linked views to keep the columns readable. IDs are shared. “Capital unknown” means no sourced universal practical threshold; Q3 gives the calculation. Validation entry **S** refers to the Q4 Sharpe table, not an arbitrary minimum history. Momentum/equity correlations are qualitative; exact contemporary net coefficients are unknown unless stated.

| ID / theme | Source/counterparty | Post-2010 net evidence and verdict | Best initial structure | Capital / data cost / permissions / infrastructure |
|---|---|---|---|---|
| A Equity | Residual business risk/capital demand | Live inexpensive implementation; SUPPORTED; forward magnitude unknown | Broad diversified long-only | Share/fractional access; prices; paid data unnecessary for wrapper; cash; basic records |
| B Profitability | Durable cash-flow underweighting and discount-rate/investment links | Independent OOS + cost evidence; pure modern number unknown; SUPPORTED narrowly | Slow liquid profitability-aware long-only tilt | Practical capital unknown; PiT fundamentals price unknown; cash; batch accounting/risk |
| C Value | Unpopular cash flows/extrapolators | Pure modern all-cost unknown; MIXED | Slow diversified tilt integrated with profitability | Capital unknown; PiT fundamentals unknown cost; cash or hedge permissions; batch |
| D Investment/accounting | Discount-rate/investment and accounting errors | Conflicting replication; MIXED | Small incremental tilt after spanning | Capital unknown; PiT statements unknown cost; cash/shorts by design; batch |
| E Cross-sectional momentum | Gradual response/flows | Live long-only E10; pure all-cost spread unknown; MIXED | Liquid long-only or modest index-hedged sleeve | Capital unknown; D1 prices; margin only for hedge; turnover model |
| F Trend | Persistent moves/gradual flows | Live net E8 + cost studies; SUPPORTED narrowly | Diversified fund or liquid futures | Contract-risk capital calculation; settlement data unknown price; futures/margin; daily reconciliation |
| G Carry | Funding/insurance/hedging demand | Post-publication and implementation conflicts; MIXED | Diversified liquid research sleeve with tail budget | Capital unknown; curves/FX data cost unknown; futures/forwards; collateral/risk |
| H Term/credit | Duration/default/liquidity risk | Matched residual premium disputed; MIXED | Transparent duration exposure; credit ETF only after duration decomposition | Share access or contract-risk capital; bond data quote unknown; cash/futures/CDS; complexity rises |
| I Low beta/BAB | Leverage/benchmark constraints | Construction and spanning disputes; MIXED | Unlevered defensive tilt first | Capital unknown; returns/covariance plus borrow if hedged; batch; avoid equating tilt with BAB |
| J Variance | Option hedgers buy protection | Pure modern all-cost number unknown; MIXED | Bounded, fully collateralized exposure only after chain audit | Tail/margin capital unknown; option data quote unknown; options/swaps; nonlinear daily risk |
| K Size | Financing/liquidity and market exposure | Weak pure premium; MIXED | Research only as conditional exposure | Capital unknown; delistings/liquidity D1 or CRSP unknown; cash; impact checks |
| L Liquidity/reversal/pairs | Urgent flow/inventory | Generic modern net unknown; MIXED | Institution-specific residual/inventory system | Capital unknown; tick/borrow unknown costs; margin/shorts; execution and monitoring |
| M Attention/lottery | Salient optimistic buyers | Borrow/cost-sensitive; MIXED | Avoidance filter rather than automatic shorts | Capital unknown; news/borrow unknown costs; cash or margin; event/borrow processing |
| N Index/market making | Forced flows/immediacy | Old index rule NOT SUPPORTED; generic market making INSUFFICIENT | No generic deployment recommendation | Venue and tail capital unknown; book/own-fill data unknown cost; specialized operations |

| ID | Capacity | Turnover/tax drag | Correlation with momentum | Correlation with equities | Years of data to validate |
|---|---|---|---|---|---|
| A | High liquid | Low with patient ownership | Conditional; no fixed sign | Direct equity exposure | S for specified excess return; long international evidence complements |
| B | Relatively high slow liquid | Lower if slow; lot management important | Definition-dependent | Long-only beta substantial | S; PiT historical fundamentals required |
| C | High liquid, lower small-cap | Slow costs favorable; realize gains cautiously | Historically negative versus relative momentum | Long-only beta substantial | S plus dry/regime periods |
| D | Medium/high slow liquid | Ingredient-specific | Unknown | Long-only beta or hedged residual | S plus genuine pre/post discovery tests |
| E | Medium | Higher turnover, taxable realization | Positive by construction; exact coefficient unknown | Long-only substantial; hedge changes it | S plus rebound/crash episodes |
| F | High core futures | Rolls/trading; wrapper taxes material | Related persistence, distinct construction | State-dependent, not permanently negative | S plus multiple independent markets |
| G | High liquid, lower exotic | Curve/roll-specific; derivatives tax varies | Unknown; can align | Often adverse stress co-losses | S plus funding-crash observations |
| H | High rates, lower credit | Coupons/taxes; bond spread and turnover | Unknown | Duration regime-sensitive; credit more procyclical | S plus rate/default cycles |
| I | Medium/high liquid | Slow tilt or costly financing/borrow | Unknown | Lower beta is not zero beta | S plus funding stress |
| J | Tail-capital-limited | Spread/hedges; tax treatment instrument-specific | Unknown | Often adverse downside association | S inadequate alone; jump and margin stress essential |
| K | Low in tiny stocks | Illiquidity and realization | Unknown | Equity and liquidity-sensitive | S with survivorship/delistings |
| L | Low fast; system-specific | Very high, often short-term tax realization | Often opposite at short horizon; not universal | Neutral ordinary beta can hide crisis risk | S with dependence/own-fill tests |
| M | Low difficult shorts | Borrow/recall, short-term taxes | State-dependent | Short stories can have extreme beta | S plus lending availability |
| N | Event/venue-specific | High execution dependence | Unknown | Inventory-specific | Unknown generic edge; event counts not independent years |

## Ranked shortlists by resources

These are **research/deployment priorities**, not personalized allocations. Ranking is by evidence first, then likely implementation burden and diversification. The exact proposed constructions below are testable **design choices**, not claims that the cited papers tested those exact variants. Consequently expected forward net magnitude is **unknown** unless independently replicated; historical ledger observations are explicitly separate. A supported theme does not automatically confer a supported grade on a new parameterization.

### (a) Individual with a modest account

**1. Diversified equity ownership with disciplined rebalancing.** Hypothesis: hold a low-cost global market-cap equity portfolio, reinvest distributions, and rebalance to prespecified country allocations only at annual review or a breached weight band, thereby capturing equity excess return with little turnover. Expected future net magnitude: **unknown**; support is broad historical equity evidence and official low-cost investability [15,16,70]. Requirements: cash account, suitable share/fractional access, basic records; avoid paid data costs. Main failure: prolonged aggregate losses or overconcentration rather than a missing ranking edge. Most decision-changing research: expected-return sensitivity to current valuations and the investor's actual tax/fee implementation.

**2. Slow liquid profitability-aware equity tilt.** Hypothesis: within a contemporaneously investable large/mid-cap universe, rank latest as-filed gross profits/total assets at each annual rebalance, overweight the top third relative to capitalization weights and underweight the bottom third subject to prespecified sector and position bounds. Expected net incremental magnitude: **unknown** for this exact construction; independent profitability and cost studies support testing [22,24,8]. A wrapper may be preferable when data costs dominate. Main failure: restated accounting, sector bets or confusing the market's return with tilt alpha. Most useful research: locked OOS incremental performance versus market, value, investment and a cheaper available fund, after tax lots and costs.

**3. Trend diversification through a transparent investable wrapper.** Hypothesis: a separately sized sleeve in a transparent diversified managed-futures product improves the portfolio's prespecified drawdown/risk criterion relative to cash or duration without relying on stock selection. Expected net magnitude: **unknown** for the chosen product and future period; E8 is a historical net reference, not its forecast. Requirements: fund access, fee/tax review and understanding leverage/holdings. Main failure: whipsaw, high costs or abandoning the sleeve after a drought. Most useful research: survivor-free live product comparison normalized for volatility and collateral, versus its prelaunch frozen model. [38–42,65]

### (b) Individual paying for data and using margin, shorts or futures

**1. Liquid multi-asset time-series trend.** Hypothesis: monthly, set each liquid equity-index, government-bond, commodity and currency future's sign from its past twelve-month excess-return sum, scale by lagged daily volatility, equalize asset-class risk and cap contract/gross exposure, then measure fully collateralized net P&L after rolls and fees. Exact choices are prespecified design parameters. Expected future net magnitude: **unknown**; E8 supplies a live manager reference and the literature supports the theme, not this exact account. Requirements: contract-granularity capital test, futures permission, individual-contract data, daily margin reconciliation. Main failure: contract concentration, underestimated gap risk and whipsaw. Most useful research: untouched recent multi-market performance with broker-specific fills and volatility-scaling-only comparator. [38–42,65]

**2. Integrated profitability and value with limited hedging.** Hypothesis: on the same liquid PiT universe, average standardized gross-profit/assets and book/market ranks, construct a sector-constrained long book, and hedge only the prespecified fraction of estimated market beta with a liquid index instrument, using buy/hold bands. Expected net alpha: **unknown**; independent evidence supports the components, but spanning and hedging costs can consume incremental benefit [14,20,22,8]. Requirements: historical filing-time statements, tax-lot engine, margin if hedged. Main failure: an unstable beta hedge or value drought disguising redundancy. Most useful research: net spanning against simpler profitability-only and unhedged versions on a locked holdout.

**3. Liquid cross-sectional momentum with trade bands.** Hypothesis: monthly rank liquid large/mid-cap stocks by total return from twelve months ago to one month ago, hold the top third long with capitalization-aware weights, and retain existing holdings until they fall below a prespecified wider threshold; first test without individual-stock shorts. Expected net incremental magnitude: **unknown**; E10 is long-only live total return, not pure momentum alpha [20,36,9,12,68]. Requirements: D1-like delisted prices/membership, corporate actions, execution and sector controls. Main failure: rebound crash, high turnover and unintended tech/sector exposure. Most useful research: cost/tax-aware holdout versus matched parent benchmark and current index momentum product.

### (c) Institution-dependent extensions

No economic style is reserved exclusively for institutions. The following **activities** require institutional infrastructure at meaningful scale. They are ranked research projects; unresolved all-cost evidence prevents unconditional deployment recommendations.

**1. Global liquid multi-signal long-short with execution integration.** Hypothesis: rank each stock in a contemporaneously borrowable global liquid universe using a frozen blend of established profitability, value and momentum signals, build a factor/sector/country-constrained risk book, and optimize trading jointly using calibrated impact and borrow. Expected future net alpha: **unknown**; cost-aware research and live market-neutral E11 show feasibility, not this book's forecast [8,9,20,67,73]. Requirements: PiT global accounting, lending feeds, prime brokerage, own execution history and independent daily reconciliation; practical capital/vendor budget unknown. Main failure: common crowded positions, funding withdrawal or lending premium capturing the shorts. Most useful research: truly post-launch, factor-attributed net results under the institution's own cost and financing conditions.

**2. Capacity-aware residual liquidity provision.** Hypothesis: on a borrowable liquid universe, neutralize frozen common-factor exposures, trade only residual dislocations exceeding executable cost plus an inventory-risk threshold, and evaluate all open positions and adverse fills rather than just converged pairs. Expected current net magnitude: **unknown**; E6 is **pre-2010 only**, with deterioration, so it is not a modern expectation. Requirements: synchronized quotes, lending, low-latency execution where appropriate, inventory and funding controls. Main failure: informed flow, relationship breaks or another crowded unwind. Most useful research: an independent frozen-rule modern replication with executable prices, borrow and opportunity-cost accounting. This remains **MIXED**, not an automatic allocation. [43–45,55]

**3. Diversified institutional insurance/variance supply.** Hypothesis: sell a prespecified bounded variance-risk exposure across liquid indices only when contemporaneous implied compensation exceeds a conservative realized/jump-risk estimate plus executable hedge and capital costs, with collateral and loss caps enforced before trading. Expected current net magnitude: **unknown**; gross insurance evidence and margin/cost failures require a contract-specific study [33–35]. Requirements: institutional option/swap access, full chains, counterparty/collateral operations and nonlinear stress testing; capital and data cost unknown. Main failure: rare crash, basis risk, margin spirals or a cost model calibrated only to normal markets. Most useful research: post-2010 all-cost return on stressed committed capital, including rejected/unfilled trades. Grade **MIXED**; ordinary option-selling access is not this institution-wide capability.

## Program-level guidance

1. **Define the accounting and benchmark first.** Specify excess versus total return, funded capital, market/sector exposures, drawdown and funding constraints, taxes and all executable costs. Establish a passive comparison before seeking alpha.
2. **Build data provenance and replication.** Reproduce a small number of established, simple constructions; reconcile corporate actions, filings, delistings and daily P&L. A replication mismatch is a data/definition investigation, not permission to optimize until the chart looks right.
3. **Sequence by economic simplicity.** Start with equity ownership and slow accounting-aware tilts; add multi-asset trend for a different mechanism. Investigate value/momentum integration after costs. Defer hard shorts, fast residual systems and nonlinear insurance until the operational platform supports them.
4. **Budget trials before seeing results.** Register hypotheses, permitted variants, selection metric, null, dependence treatment and a family-level error policy. Preserve all attempted variants and rejected cost assumptions. Reserve chronological evaluation data and an external market/geography test where plausible.
5. **Separate four gates.** Is the economic mechanism plausible? Does the result replicate independently? Does it add exposure beyond known factors? Does it survive executable costs, liquidity, funding and tax constraints? Statistical significance alone answers none of the operational questions.
6. **Escalate evidence before capital.** Point-in-time backtest → independent code/data verification → locked walk-forward/holdout → paper operational audit → small live implementation → controlled scaling. Predetermine stop/review conditions for data errors, drift, costs and margin, not just a disappointing return.
7. **Treat live adaptation as new research.** Changing a signal after losses consumes trials and changes the strategy. Keep both the originally frozen book and the adaptive decision log so subsequent evidence remains interpretable.

Accept a result provisionally when the economic, statistical and implementation evidence agree and remaining uncertainty is tolerable. Reject or defer when profits depend on a few illiquid names, unavailable shorts, revised data, one favorable era, a fragile optimizer or an undisclosed trial count. A result can be useful as cheap exposure even without novel alpha; price it accordingly.

## Evidence against: findings that should change decisions

- Published discoveries weaken outside their original samples and after publication; a historical factor average needs selection-aware interpretation. [1,6,7]
- Apparent disagreement over replication is not cured by selecting the highest replication percentage: original reproduction, liquidity filters and Bayesian pooling answer different questions. [2–4]
- Liquid, cost-aware anomaly averages are small; fast turnover often eliminates profitability. Institutional cost results do not imply the same fills for every account. [7–10]
- Pure size, several lottery/liquidity anomalies and accounting variants struggle with robust weighting, historical OOS or economic restrictions. [3,6,28]
- Credit excess return can be mostly duration rather than a large default premium. [18]
- BAB's canonical construction and independent alpha are challenged; a defensive portfolio may just repackage profitability/investment. [50]
- Carry can pass data-snooping tests yet decay after publication; choosing the recent best variant often fails in the next period, and impact changes feasible allocation. [31,32,71,72]
- Old index inclusion gains diminished sharply; readily anticipated flows are not a perpetual executable arbitrage. [52]
- Pairs/residual backtests deteriorated even in old samples; the quant unwind shows neutral books can share liquidation risk. [44,45,55,56]
- Volatility-managed in-sample alpha need not deliver a feasible OOS improvement; risk targeting and timing alpha are different claims. [75–77]
- Machine-learning advantages depend on universe and economic restrictions; more predictors or more complex models need not beat disciplined simple selection. [82–85]
- Live expenses, changing methods, collateral and taxes prevent treating research spreads as product returns. [65–68]

## Unverified or uncertain

- A uniform, all-cost post-2010 premium, long/short decomposition and confidence interval for every theme: **unknown**.
- Exact sample endpoints and cost completeness for some frequently cited abstracts: **unknown**, explicitly identified in the ledger rather than inferred.
- Forward expected net magnitude for each newly proposed construction: **unknown**; a historical related strategy is not its forecast.
- Reliable universal minimum capital, capacity in dollars, signal half-life or latency requirement by theme: **unknown**; these depend on instruments and execution.
- Historical point-in-time retail fundamentals, full institutional borrow books and own-trade institutional costs at an individual's affordable price: availability/cost **unknown**.
- Comparable net correlation matrix since 2010, including crisis conditional correlations and stable factor constructions: **unknown**.
- Survivor-free live results for the entire factor/ARP/CTA industry, matched against frozen prelaunch backtests: **unknown**.
- Pure current net alpha of generic pairs, attention shorts, deep price networks, reinforcement learning or general LLM signals: **unknown**; no deployment recommendation follows.
- Some early foundational examples are entirely **pre-2010**; E6 and Gatev's historical work must not be described as current evidence.
- Whether value, profitability or investment are primarily risk or behavioural compensation: not uniquely identified by their average returns.

## Numerical evidence ledger

Quotes below are deliberately short. Missing fields remain unknown. Sample endpoints derived mechanically from an official trailing-period endpoint are identified as such. Rates are annualized only when the source says so.

| ID | Exact quoted figure and source URL | Sample / portfolio / test status | Gross or net; interpretation and limitation |
|---|---|---|---|
| E1 | **“26% lower out-of-sample”**, **“58% lower post-publication”** — [McLean–Pontiff](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12365) | Many original characteristic-specific samples and later periods; exact common ending date **unknown** here. Extreme characteristic long-short portfolios; original versus OOS and post-publication. | Gross relative decline in published average returns, not percentage-point annual return and not a sequential haircut. Difference between the quoted declines is a derived comparison, not an additional quoted return. |
| E2 | **“98%”** — [Chen–Zimmermann](https://www.federalreserve.gov/econres/feds/open-source-cross-sectional-asset-pricing.htm) | Original-sample reproductions of sufficiently clearly described significant predictors; dates vary, aggregate endpoints **unknown**. Characteristic long-short. | Gross reproduction/significance rate, **not a return**, post-publication profit or cost survival rate. |
| E3 | **“82.4%”** — [JKP](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249) | US history extending through 2020, starts vary by factor; international and pre/post-original-sample tests. Characteristic L/S with specified weighting/benchmark and Bayesian pooling. | Gross model-based replication estimate, **not a return** or all-cost investability rate. Cannot compare directly with E2's original-sample statistic. |
| E4 | **“8 bps per month”**, **“10–20 bps”** — [Chen–Velikov](https://www.federalreserve.gov/econres/feds/zeroing-in-on-the-expected-returns-of-anomalies.htm) | US anomaly long-short portfolios; publication-aware, modern-market adjustments, additional OOS/shrinkage exercise for strongest signals. Exact final endpoint **unknown** here. | Partial net after modeled trading frictions and named adjustments; first figure average expected return estimate, second strongest-anomaly range. Full impact/borrow/tax coverage is **not established** here. Do not label this a pure post-2010 all-cost series. |
| E5 | **“50% turnover per month”** — [Novy-Marx–Velikov](https://academic.oup.com/rfs/article-abstract/29/1/104/1844518) | Historical equity long-short anomaly cost study; exact sample endpoint **unknown** here. Historical backtest, not verified post-publication live evidence. | Turnover threshold, **not a return**: lower-turnover anomalies often survive cost mitigation, few high-turnover ones do. Modeled transaction-cost net, not full shareholder after-tax net. |
| E6 | **“1.44”**, **“0.9”** — [Avellaneda–Lee](https://cims.nyu.edu/ams/abstracts/avellaneda.html) | First annual Sharpe: 1997–2007; second: 2003–2007 subperiod. PCA residual equity L/S; historical development/backtest. **Only pre-2010 evidence.** | Source describes returns after transaction costs. Complete borrow/financing/tax treatment **unknown**. Subperiod deterioration is not an independent post-publication test. |
| E7 | **“7.4%”**, **“less than 1%”** — [Greenwood–Sammon](https://onlinelibrary.wiley.com/doi/10.1111/jofi.13410) | First: S&P index additions in the 1990s; second: recent decade including 2010–2020 analysis. Event study across historical and modern periods. Addition stocks' abnormal event returns, not annual L/S return. | Gross event-price response; execution/hedge/auction/anticipation costs excluded. Modern period disconfirms simply extrapolating the old inclusion effect. |
| E8 | **“3.58%”**, **“11.13%”**, **“1.92%”** — [AQR Managed Futures SEC summary](https://www.sec.gov/Archives/edgar/data/1444822/000119312526198087/d24166d497k.htm) | Ten-year before-tax NAV, five-year before-tax NAV, ten-year after-tax distributions, respectively, ending 31 Dec 2025. Derived windows: 2016–2025 and 2021–2025. Class I; live post-launch, dynamic L/S multi-asset instruments plus collateral. | Annualized total returns including applicable fees/charges and trading results; shareholder taxes excluded except third figure, which uses standardized highest federal rates, excludes state/local taxes and does not include final sale. Not excess alpha or pure frozen trend. |
| E9 | **“5.99%”**, **“20.65%”**, **“3.06%”** — [AQR Style Premia SEC summary](https://www.sec.gov/Archives/edgar/data/1444822/000119312526198088/d42635d497k.htm) | Ten-year before-tax, five-year before-tax and ten-year after-tax distributions ending 31 Dec 2025; derived windows 2016–2025 and 2021–2025. Class I live L/S multi-asset style blend plus collateral. | Annualized net-of-applicable-fees total returns; standardized distribution taxes in third figure, no state/local or final-sale taxes. Changing proprietary process; not universal multi-premia expected excess return. |
| E10 | **“15.69%”**, **“15.06”**, **“104%”** — [MTUM annual report](https://www.ishares.com/us/literature/annual-report/ar-mtum-en.pdf) | Ten-year annualized NAV return and parent MSCI USA annualized percentage return, 1 Aug 2016–31 Jul 2026; turnover for fiscal year ending 31 Jul 2026. Live long-only ETF; underlying momentum index changed in 2020. | ETF NAV net operating expenses and trading effects, before shareholder taxes/brokerage spreads. Parent index gross of fund expenses. Turnover is **not a return**. Difference is not risk-adjusted alpha. URL is mutable; dates identify the retrieved version. |
| E11 | **“6.97%”**, **“22.68%”**, **“4.96%”** — [AQR Equity Market Neutral SEC summary](https://www.sec.gov/Archives/edgar/data/1444822/000119312526198092/d52681d497k.htm) | Ten-year before-tax, five-year before-tax and ten-year after-tax distributions ending 31 Dec 2025; derived windows 2016–2025 and 2021–2025. Class I live equity L/S market-neutral objective. | Annualized net-of-applicable-fees total return; third standardized federal distribution-tax figure. Not a single public signal, pure alpha or evidence every market-neutral manager survives. |
| E12 | **“1.35”** — [Gu–Kelly–Xiu](https://academic.oup.com/rfs/article/33/5/2223/5758276) | Annualized Sharpe of value-weighted top-minus-bottom predicted-return deciles; monthly US test period 1987–2016 after training 1957–1974 and validation 1975–1986. Historical OOS, partly post-2010, **not post-2020 publication**. | Gross, before trading/borrow/funding/tax costs. Attractive prediction result; not a net implementable Sharpe or a claim of incremental advantage over every regularized linear alternative. |

## Numbered sources, URLs and tiers

Tier 1 includes peer-reviewed articles, NBER/SSRN papers and official data/filings. Tier 2 includes established practitioner research. Tier 3 is used only for dataset access/pricing. Publication year is metadata, not a return observation. Practitioner affiliations and overlapping authors reduce source independence even when papers qualify as Tier 1.

1. **T1** McLean & Pontiff (2016), *Does Academic Research Destroy Stock Return Predictability?* [Journal of Finance](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12365).
2. **T1** Chen & Zimmermann, *Open Source Cross-Sectional Asset Pricing*. [Federal Reserve working paper](https://www.federalreserve.gov/econres/feds/open-source-cross-sectional-asset-pricing.htm); [replication code](https://github.com/OpenSourceAP/CrossSection).
3. **T1** Hou, Xue & Zhang (2020), *Replicating Anomalies*. [RFS](https://academic.oup.com/rfs/article/33/5/2019/5236964?guestAccessKey=7fd97e02-18ad-4e38-aec9-44cac1b9f75a).
4. **T1** Jensen, Kelly & Pedersen (2023), *Is There a Replication Crisis in Finance?* [JF](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249).
5. **T1** Jacobs & Müller (2020), *Anomalies Across the Globe: Once Public, No Longer Existent?* [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X19301618).
6. **T1** Linnainmaa & Roberts, *The History of the Cross Section of Stock Returns*. [NBER](https://www.nber.org/papers/w22894).
7. **T1** Chen & Velikov, *Zeroing in on the Expected Returns of Anomalies*. [Federal Reserve](https://www.federalreserve.gov/econres/feds/zeroing-in-on-the-expected-returns-of-anomalies.htm); [full paper](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf).
8. **T1** Novy-Marx & Velikov (2016), *A Taxonomy of Anomalies and Their Trading Costs*. [RFS](https://academic.oup.com/rfs/article-abstract/29/1/104/1844518).
9. **T1** Frazzini, Israel & Moskowitz, *Trading Costs*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3229719). Institutional execution data; author overlap with AQR research.
10. **T2** Frazzini, Israel & Moskowitz, *Trading Costs of Asset Pricing Anomalies*. [AQR](https://www.aqr.com/insights/research/working-paper/trading-costs-of-asset-pricing-anomalies). Not independent of [9].
11. **T1** *Interacting Anomalies* (2025). [Review of Asset Pricing Studies](https://academic.oup.com/raps/article/15/2/162/7979163). Relevant to double-sort expansion, not proof all basic themes vanish.
12. **T1** Israel & Moskowitz (2013), *The Role of Shorting, Firm Size, and Time on Market Anomalies*. [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X12002401).
13. **T1** Drechsler & Drechsler, *The Shorting Premium and Asset Pricing Anomalies*. [NBER](https://www.nber.org/papers/w20282).
14. **T1** Fama & French (2015), *A Five-Factor Asset Pricing Model*. [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X14002323).
15. **T1** Jordà, Knoll, Kuvshinov, Schularick & Taylor, *The Rate of Return on Everything, 1870–2015*. [NBER](https://www.nber.org/papers/w24112).
16. **T1** McQuarrie (2024), *Stocks for the Long Run? Sometimes Yes, Sometimes No*. [FAJ](https://www.tandfonline.com/doi/abs/10.1080/0015198X.2023.2268556).
17. **T1** Cochrane & Piazzesi (2005), *Bond Risk Premia*. [AER](https://www.aeaweb.org/articles?id=10.1257/0002828053828581).
18. **T1** van Binsbergen, Nozawa & Schwert (2025), *Duration-Based Valuation of Corporate Bonds*. [RFS](https://academic.oup.com/rfs/article/38/1/158/7762644).
19. **T1** Ghaderi, Plante, Roussanov & Seo (2026), *Reconstructing a Century of U.S. Corporate Bonds: Credit Risk in Historical Perspective*. [NBER w35578](https://www.nber.org/papers/w35578). Counterevidence to extrapolating a modern small duration-adjusted premium into every era; precise comparable magnitude unknown here.
20. **T1** Asness, Moskowitz & Pedersen (2013), *Value and Momentum Everywhere*. [JF DOI](https://doi.org/10.1111/jofi.12021).
21. **T1** Stagnol, Lopez, Roncalli & Taillardat, *Understanding the Performance of the Equity Value Factor*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3813572).
22. **T1** Novy-Marx, *The Other Side of Value: The Gross Profitability Premium*. [NBER](https://www.nber.org/papers/w15940).
23. **T1** Hou, Xue & Zhang, *Digesting Anomalies: An Investment Approach*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2508322).
24. **T1** Novy-Marx & Medhat (2025), *Profitability Retrospective: What Have We Learned?* [NBER](https://www.nber.org/papers/w33601). Shares an author with [22], not independent corroboration of it.
25. **T1** Asness, Frazzini & Pedersen (2019), *Quality Minus Junk*. [Review of Accounting Studies](https://doi.org/10.1007/s11142-018-9470-2).
26. **T1** *Does Risk Explain Anomalies? Evidence from Expected Return Estimates*. [NBER](https://www.nber.org/papers/w15950).
27. **T1** *An Intangibles-Adjusted Profitability Factor*. [NBER](https://www.nber.org/papers/w31068).
28. **T1** Asness and coauthors, *Size Matters, If You Control Your Junk*. [JFE DOI](https://doi.org/10.1016/j.jfineco.2018.05.006).
29. **T1** Koijen, Moskowitz, Pedersen & Vrugt, *Carry*. [NBER](https://www.nber.org/papers/w19325).
30. **T1** Brunnermeier, Nagel & Pedersen, *Carry Trades and Currency Crashes*. [NBER](https://www.nber.org/papers/w14473). Foundational empirical evidence is pre-2010.
31. **T1** Hsu, Li, Taylor & Wang (2025), *On the Profitability of Influential Carry-Trade Strategies: Data-Snooping Bias and Post-Publication Performance*. [Journal of Empirical Finance](https://www.sciencedirect.com/science/article/pii/S0927539825000623).
32. **T1** *The Out-of-Sample Performance of Carry Trades* (2024). [Journal of International Money and Finance](https://www.sciencedirect.com/science/article/pii/S0261560624000299).
33. **T1** Carr & Wu (2009), *Variance Risk Premiums*. [SSRN journal record](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1359527). Foundational evidence pre-2010.
34. **T1** Santa-Clara & Saretto (2009), *Option Strategies: Good Deals and Margin Calls*. [Journal of Financial Markets](https://www.sciencedirect.com/science/article/pii/S1386418109000123). Foundational evidence pre-2010.
35. **T1** Modern option return evidence including moneyness/underlying volatility. [Review of Finance](https://academic.oup.com/rof/article/27/1/289/6510952). Used qualitatively; exact all-cost post-2010 premium unknown.
36. **T1** Jegadeesh & Titman (1993), *Returns to Buying Winners and Selling Losers*. [JF DOI](https://doi.org/10.1111/j.1540-6261.1993.tb04702.x). Original evidence pre-2010.
37. **T1** Daniel & Moskowitz, *Momentum Crashes*. [NBER](https://www.nber.org/papers/w20439).
38. **T1** Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*. [JFE DOI](https://doi.org/10.1016/j.jfineco.2011.11.003).
39. **T1** Hurst, Ooi & Pedersen, *A Century of Evidence on Trend-Following Investing*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026). Author overlap with [38].
40. **T1** Lempérière and coauthors, *Two Centuries of Trend Following*. [Journal of Investment Strategies](https://www.risk.net/ja/node/2349968).
41. **T1** Baltas & Kosowski, trend-following implementation/capacity study. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1968996).
42. **T1** Kim, Tse & Wald (2016), *Time Series Momentum and Volatility Scaling*. [Journal of Financial Markets](https://www.sciencedirect.com/science/article/abs/pii/S1386418116301379).
43. **T1** Nagel, *Evaporating Liquidity*. [NBER](https://www.nber.org/papers/w17653).
44. **T1** Do & Faff (2012), *Are Pairs Trading Profits Robust to Trading Costs?* [Financial Review](https://onlinelibrary.wiley.com/doi/10.1111/j.1475-6803.2012.01317.x). Historical sample evidence predates 2010; modern result unknown here.
45. **T1** Avellaneda & Lee (2010), *Statistical Arbitrage in the U.S. Equities Market*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1153505); [NYU summary](https://cims.nyu.edu/ams/abstracts/avellaneda.html).
46. **T1** Miller (1977), *Risk, Uncertainty, and Divergence of Opinion*. [JF DOI](https://doi.org/10.1111/j.1540-6261.1977.tb03317.x). Theory, not modern net return evidence.
47. **T1** Barber & Odean (2008), *All That Glitters*. [RFS](https://academic.oup.com/rfs/article-abstract/21/2/785/1607197). Original empirical evidence pre-2010.
48. **T1** Stambaugh, Yu & Yuan, *The Short of It: Investor Sentiment and Anomalies*. [NBER](https://www.nber.org/papers/w16898).
49. **T1** Frazzini & Pedersen (2014), *Betting Against Beta*. [JFE DOI](https://doi.org/10.1016/j.jfineco.2013.10.005).
50. **T1** Novy-Marx & Velikov, *Betting Against Betting Against Beta*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3300965).
51. **T1** Baker, Bradley & Wurgler (2011), *Benchmarks as Limits to Arbitrage*. [FAJ](https://rpc.cfainstitute.org/research/financial-analysts-journal/2011/benchmarks-as-limits-to-arbitrage-understanding-the-low-volatility-anomaly).
52. **T1** Greenwood & Sammon (2025), *The Disappearing Index Effect*. [JF](https://onlinelibrary.wiley.com/doi/10.1111/jofi.13410).
53. **T1** Kang, Rouwenhorst & Tang (2020), *A Tale of Two Premiums: The Role of Hedgers and Speculators in Commodity Futures Markets*. [JF DOI](https://doi.org/10.1111/jofi.12845).
54. **T1** Menkveld (2013), *High Frequency Trading and the New Market Makers*. [Journal of Financial Markets](https://www.sciencedirect.com/science/article/pii/S1386418113000281).
55. **T1** Khandani & Lo, *What Happened to the Quants in August 2007?* [MIT paper page](https://web.mit.edu/Alo/www/Papers/august07.html); [journal follow-up](https://www.sciencedirect.com/science/article/abs/pii/S1386418110000261).
56. **T1** Gatev, Goetzmann & Rouwenhorst (2006), *Pairs Trading: Performance of a Relative-Value Arbitrage Rule*. [RFS](https://academic.oup.com/rfs/article-abstract/19/3/797/1646694). Final sample 1962–2002; entirely pre-2010.
57. **T1** Zarinelli, Treccani, Farmer & Lillo (2015), *Beyond the Square Root: Evidence for Logarithmic Dependence of Market Impact on Size and Participation Rate*. [Journal article repository](https://cris.unibo.it/handle/11585/597924).
58. **T1** Clarke, de Silva & Thorley (2002), *Portfolio Constraints and the Fundamental Law of Active Management*. [FAJ](https://www.tandfonline.com/doi/abs/10.2469/faj.v58.n5.2468).
59. **T1** Gârleanu & Pedersen (2013), *Dynamic Trading with Predictable Returns and Transaction Costs*. [JF](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12080/abstract).
60. **T1** Lo (2002), *The Statistics of Sharpe Ratios*. [FAJ](https://rpc.cfainstitute.org/research/financial-analysts-journal/2002/the-statistics-of-sharpe-ratios).
61. **T1** Bailey & López de Prado (2012), *The Sharpe Ratio Efficient Frontier*. [Author paper](https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf).
62. **T1** Harvey, Liu & Zhu (2016), *… and the Cross-Section of Expected Returns*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314).
63. **T1** Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*. [SSRN DOI](https://doi.org/10.2139/ssrn.2460551).
64. **T1** Bailey and coauthors, *The Probability of Backtest Overfitting*. [Journal of Computational Finance](https://www.risk.net/journal-of-computational-finance/2471206/the-probability-of-backtest-overfitting).
65. **T1** AQR Managed Futures Strategy Fund, May 2026 summary prospectus. [SEC](https://www.sec.gov/Archives/edgar/data/1444822/000119312526198087/d24166d497k.htm).
66. **T1** AQR Style Premia Alternative Fund, May 2026 summary prospectus. [SEC](https://www.sec.gov/Archives/edgar/data/1444822/000119312526198088/d42635d497k.htm).
67. **T1** AQR Equity Market Neutral Fund, May 2026 summary prospectus. [SEC](https://www.sec.gov/Archives/edgar/data/1444822/000119312526198092/d52681d497k.htm).
68. **T1** iShares MSCI USA Momentum Factor ETF, annual shareholder report July 2026. [Official report](https://www.ishares.com/us/literature/annual-report/ar-mtum-en.pdf). Official fund data, not independent evidence of a causal factor premium.
69. **T1** IRS, *Publication 550*, current published edition. [IRS](https://www.irs.gov/publications/p550); [official section 1256 statute](https://uscode.house.gov/view.xhtml?req=%28title%3A26+section%3A1256+edition%3Aprelim%29).
70. **T1** Vanguard official investment-product data. [Fund listing](https://investor.vanguard.com/investment-products/list/all?assetclass=equity&filters=open&managementstyle=index&strategy=total_market_etfs). Official access/expense evidence, not independent alpha research.
71. **T1** *Importance of Transaction Costs for Asset Allocation in Foreign Exchange Markets* (2024). [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X24001090).
72. **T1** *Foreign Exchange Risk and the Predictability of Carry Trade Returns*. [Journal of Banking & Finance](https://www.sciencedirect.com/science/article/pii/S0378426614000545).
73. **T1** Blitz, Hanauer, Honarvar, Huisman & van Vliet (2023), *Beyond Fama–French Factors: Alpha from Short-Term Signals*. [FAJ](https://www.tandfonline.com/doi/full/10.1080/0015198X.2023.2173492). Practitioner-affiliated academic paper, not universal validation of reversal.
74. **T1** Benjamini & Hochberg (1995), *Controlling the False Discovery Rate*. [JRSS B](https://rss.onlinelibrary.wiley.com/doi/10.1111/j.2517-6161.1995.tb02031.x).
75. **T1** Moreira & Muir (2017), *Volatility-Managed Portfolios*. [JF](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12513).
76. **T1** Cederburg and coauthors (2020), *On the Performance of Volatility-Managed Portfolios*. [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X2030132X).
77. **T1** DeMiguel, Martín-Utrera & Uppal (2024), *A Multifactor Perspective on Volatility-Managed Portfolios*. [JF](https://onlinelibrary.wiley.com/doi/10.1111/jofi.13395).
78. **T1** Ledoit & Wolf (2004), *Honey, I Shrunk the Sample Covariance Matrix*. [Author journal abstract](https://www.ledoit.net/honey_abstract.htm).
79. **T1** DeMiguel, Garlappi & Uppal (2009), *Optimal Versus Naive Diversification: How Inefficient Is the 1/N Portfolio Strategy?* [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=911512).
80. **T3, pricing only** Norgate US stock-market packages. [Vendor prices](https://norgatedata.com/stockmarketpackages.php).
81. **T1** SEC, EDGAR API and data access. [Official API/data announcement](https://www.sec.gov/newsroom/press-releases/2021-159); [data resources](https://www.sec.gov/data-research/sec-data-resources).
82. **T1** Gu, Kelly & Xiu (2020), *Empirical Asset Pricing via Machine Learning*. [RFS](https://academic.oup.com/rfs/article/33/5/2223/5758276).
83. **T1** Avramov, Cheng & Metzker (2023), *Machine Learning vs. Economic Restrictions: Evidence from Stock Return Predictability*. [Management Science](https://pubsonline.informs.org/doi/abs/10.1287/mnsc.2022.4449).
84. **T1** Li, Rossi, Yan & Zheng (2025), *Machine Learning from a “Universe” of Signals: The Role of Feature Engineering*. [JFE](https://www.sciencedirect.com/science/article/pii/S0304405X25001461); [author full paper](https://www.lehigh.edu/~xuy219/research/JFE_2025.pdf).
85. **T1** *Machine Learning and the Implementable Efficient Frontier* (2026). [RFS](https://academic.oup.com/rfs/article/39/10/3035/8524346). Full comparable numerical claim not verified here.
86. **T1** Kelly, Pruitt & Su, *Characteristics Are Covariances: A Unified Model of Risk and Return*. [NBER](https://www.nber.org/papers/w24540).
87. **T1** Gu, Kelly & Xiu, *Autoencoder Asset Pricing Models*. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3335536).
88. **T1** Bradford & Levy (2026), language-model look-ahead research. [Journal of Accounting Research](https://doi.org/10.1111/1475-679x.70058).
89. **T1** *Scaling Point-in-Time Language Models*. [NBER w35247](https://www.nber.org/papers/w35247). Point-in-time research design evidence; generic investable net premium unknown.
90. **T3, access only** Databento pricing. [Vendor](https://databento.com/pricing/). Dataset/exchange/use-specific; total research budget unknown.
91. **T3, access only** Nasdaq Sharadar SF1 documentation. [Nasdaq](https://data.nasdaq.com/databases/SF1/documentation?anchor=see-also). Current subscription price unknown; as-reported versus restated availability must be checked.
92. **T1** Kenneth French, research-data library and archives. [Dartmouth](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html).
93. **T1** Federal Reserve Bank of St. Louis, FRED/ALFRED API. [Official documentation](https://fred.stlouisfed.org/docs/api/fred/overview.html).
94. **T1** CFTC, historical Commitments of Traders reports. [Official historical files](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm).
