# Research Report: statement tag-alias census for the #2826 names

**Brief:** TradePartner T363 (#2831, spec amendment 2026-11-01 #2826 in `docs/specs/data-foundation.md`) · **Date:** 2026-11-01 · **Status:** COMPLETE · **Source:** the cached SEC `data/edgar_cache/bulk/companyfacts.zip` (fetched 2026-10-31), read in place and read-only; the stamps cache `data/edgar_cache/stamps/v1`, read-only; a byte-identical copy of `data/tradepartner.duckdb` (SHA-256 `e162ef7c…`) taken 2026-11-01 21:59 ET and opened read-only. The scripts are in the appendix. The candidate-alias list and the method below were committed (a0cfae24) before the run.

## Answer

1. **No alias joins any list.** The discovery search found no same-concept tag that the candidate lists miss. The accessions that carry none of a name's tags are other kinds of filer: banks for the current items and operating income, partnerships for EPS and equity, REITs for capex and PP&E. Every list stays as the spec wrote it, except one.
2. **`depreciation` drops `DepreciationAndAmortization`.** That tag was marked *(verify)*, and it fails both tests. Its taxonomy label is "Depreciation, Depletion and Amortization, **Nonproduction**", a narrower concept. In filings that carry both tags for one period, the values are equal only 45 % of the time (24,954 equal, 30,540 differ). The frozen list is `DepreciationDepletionAndAmortization` alone. Coverage falls from 80.8 % to 62.6 % of in-sample universe-months, so the dropped tag is a gap of 18.2 %.
3. **The 2021-22 dip is mostly blank-check composition, not a tag migration.** SIC 6770 filers rose from 2.8 % of accessions in 2020 to 10.4 % in 2021 and 12.3 % in 2022. With them removed, basic EPS, capex, depreciation and income tax stay within about 3 points of 2019 through 2023. Diluted EPS sits 5 to 6 points lower from 2022, because more filers report a basic figure only. Three real migrations are visible, in other names and other years: `cash` declines steadily from 2018 on, as filers move to the restricted-cash total (ASU 2016-18); deferred `ppe_net` declines from 2020 on, as filers move to the PP&E-plus-finance-lease tag (ASC 842); deferred `interest_expense` collapses from 2024 on (60 % in 2021, 19 % in 2025, ex-SIC 6770).
4. **Residue under the 0.5 % rule.** The denominator is 41,349 in-sample universe-months (41 month-ends, 2020-08 to 2023-12). Only one concept falls under the share: `CurrentIncomeTaxExpenseBenefit` against `income_tax` (0.21 %), which is accepted residue. Every other different concept is above 0.5 %, from 0.97 % for continuing-operations EPS to 41.5 % for `RepaymentsOfLongTermDebt`. Each of these may become a separate labelled name, and that choice is the owner question below.
5. **Recommended labelled names (owner question).** Eleven, chosen because a family in the spec's table needs that quantity to compute its documented definition. Eight are financing names, because #2670's XFIN counts long-term and short-term debt, total dividends and preferred stock. Two are accruals names (`cash_incl_restricted`, `depreciation_nonproduction`). One is `dividends_paid_noncontrolling`, so XFIN can subtract noncontrolling payouts from total dividends. The other eleven above-share concepts would stay out as stated gaps, and every report on that name would cite them.
6. **T368 budget.** The backfill would add about 6.5 million rows. That is about 0.5 GB of store (measured compact; allow 1 GB) and about 1.9 GB of statement cache. The zip reads and parses in about 3 minutes of CPU. The time is in API requests: **about 30,000**, about 56 minutes at 9 requests a second. Of these, 29,081 are CIKs that the zip lacks and whose cached statement result is already empty. Filed as [#2837](https://github.com/josejuarez96/tradepartner/issues/2837) for T365.

## The frozen lists, with coverage

Coverage is the share of the 41,349 in-sample universe-months at which the name has a first-vintage row known at the month-end with `period_end` within 400 days of the session (the #660 health bound). Accession shares are over 243,151 priced-CIK accessions of `edgar.statement_forms` filed 2009-2026, with SIC 6770 removed.

| Name | Frozen tags, in precedence order | Coverage, universe-months | Accessions carrying a tag |
|---|---|---|---|
| `net_income` | `NetIncomeLoss` | 91.9 % | 91.9 % |
| `eps_basic` | `EarningsPerShareBasic`, `EarningsPerShareBasicAndDiluted` | 97.0 % | 93.6 % |
| `eps_diluted` | `EarningsPerShareDiluted`, `EarningsPerShareBasicAndDiluted` | 97.3 % | 91.7 % |
| `operating_income` | `OperatingIncomeLoss` | 78.8 % | 78.0 % |
| `book_equity` | `StockholdersEquity` | 94.8 % | 92.4 % |
| `total_liabilities` | `Liabilities` | 75.3 % | 81.0 % |
| `current_assets` | `AssetsCurrent` | 81.5 % | 78.2 % |
| `current_liabilities` | `LiabilitiesCurrent` | 81.5 % | 78.0 % |
| `cash` | `CashAndCashEquivalentsAtCarryingValue` | 90.0 % | 89.1 % |
| `capex` | `PaymentsToAcquirePropertyPlantAndEquipment` | 72.6 % | 70.9 % |
| `depreciation` | `DepreciationDepletionAndAmortization` | 62.6 % | 50.1 % |
| `income_tax` | `IncomeTaxExpenseBenefit` | 97.6 % | 84.4 % |
| `buybacks` | `PaymentsForRepurchaseOfCommonStock` | 72.3 % | 41.9 % |
| `dividends_paid` | `PaymentsOfDividendsCommonStock` | 41.3 % | 24.0 % |
| `stock_issued` | `ProceedsFromIssuanceOfCommonStock` | 32.9 % | 30.6 % |
| `debt_issued` | `ProceedsFromIssuanceOfDebt` | 10.6 % | 5.6 % |
| `debt_repaid` | `RepaymentsOfDebt` | 10.9 % | 6.5 % |

All tags are `us-gaap:`. `EarningsPerShareBasicAndDiluted` stays on both EPS lists. Its definition is the amount "when basic and diluted earnings per share are the same amount and reported as a single line item", and it equals `EarningsPerShareBasic` in 93 % of filing-periods carrying both, and `EarningsPerShareDiluted` in 93 %. `debt_issued` and `debt_repaid` are thin: only 5 to 8 % of accessions use the total-debt concepts. Most filers tag the long-term and short-term lines instead, which is why the labelled debt names matter.

Deferred names, counted for later, with coverage: `long_term_debt` 51.0 %, `interest_expense` 70.6 %, `rd_expense` 41.4 %, `sga_expense` 51.3 %, `inventory` 54.8 %, `receivables` 64.3 %, `ppe_net` 85.0 %. When one is added, two findings apply. `interest_expense` needs `InterestExpenseNonoperating` judged; it is equal in only 74 % of shared filing-periods, so it is not the same concept by values. `ppe_net` needs the PP&E-plus-finance-lease ROU tag judged; it is a broader concept by definition, and equal in 71 %.

## The 2021-22 dip: composition, with three migrations elsewhere

The share of accessions carrying any listed tag, for all filers and then with SIC 6770 removed:

| Name | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|
| SIC 6770 share of accessions | 1.7 % | 2.8 % | 10.4 % | 12.3 % | 6.3 % |
| `eps_basic` | 94 / 95 | 94 / 95 | 88 / 93 | 83 / 92 | 87 / 92 |
| `eps_diluted` | 93 / 94 | 93 / 94 | 87 / 92 | 79 / 88 | 83 / 88 |
| `capex` | 72 / 73 | 70 / 72 | 63 / 71 | 63 / 72 | 67 / 71 |
| `depreciation` (both tags) | 69 / 71 | 67 / 69 | 60 / 67 | 60 / 69 | 64 / 68 |
| `income_tax` | 85 / 85 | 84 / 85 | 76 / 83 | 77 / 83 | 82 / 84 |
| `ppe_net` (deferred) | 87 / 89 | 84 / 86 | 72 / 81 | 70 / 80 | 73 / 78 |
| `interest_expense` (deferred) | 62 / 63 | 60 / 62 | 54 / 60 | 52 / 59 | 55 / 58 |
| `cash` | 89 / 90 | 87 / 88 | 87 / 88 | 84 / 86 | 83 / 85 |

With blank checks removed, the basic-EPS, capex, depreciation and income-tax dips close to within about 3 points of 2019. Blank checks have no capex or depreciation, and they file EPS per share class, as dimensioned facts that companyfacts leaves out. The diluted-EPS series still sits 5 to 6 points lower from 2022. Among accessions without diluted EPS, 26 % carry basic EPS only, so this is filers reporting one figure, not a missing tag. `ppe_net`, `interest_expense` (which falls to 43 % in 2024, 19 % in 2025 and 16 % in 2026, ex-SIC 6770) and `cash` decline beyond composition. Those three are the real migrations, and their alternate tags are the ones measured below.

## Freeze-rule gaps and residue

The gap is the share of the 41,349 member-months at which the name has no row by the #660 bound and the different concept has one. For the three preferred-inclusive equity concepts, member-months where both have rows and the concept's latest key is missing from the name's rows or differs are counted too. ADR 0023 D1's share is 0.5 %, 207 member-months.

| Name | Different concept | Gap | Verdict | Recommended |
|---|---|---|---|---|
| `net_income` | `ProfitLoss` | 7.09 % | above | stated gap |
| `net_income` | `NetIncomeLossAvailableToCommonStockholdersBasic` | 6.58 % | above | stated gap |
| `eps_basic` | `IncomeLossFromContinuingOperationsPerBasicShare` | 0.98 % | above | stated gap |
| `eps_diluted` | `IncomeLossFromContinuingOperationsPerDilutedShare` | 0.97 % | above | stated gap |
| `operating_income` | pre-tax income (`…BeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`) | 15.34 % | above | stated gap |
| `operating_income` | pre-tax income (`…BeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments`) | 6.41 % | above | stated gap |
| `book_equity` | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest` | 4.93 % | above | stated gap |
| `cash` | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` | 8.93 % | above | **`cash_incl_restricted`** |
| `cash` | `Cash` | 2.37 % | above | stated gap |
| `capex` | `PaymentsToAcquireProductiveAssets` | 14.20 % | above | stated gap |
| `depreciation` | `DepreciationAndAmortization` | 18.19 % | above | **`depreciation_nonproduction`** |
| `depreciation` | `Depreciation` | 23.10 % (against the frozen one-tag list) | above | stated gap |
| `income_tax` | `CurrentIncomeTaxExpenseBenefit` | 0.21 % | **residue** | none |
| `buybacks` | `PaymentsForRepurchaseOfEquity` | 2.94 % (lacks 2.02, differs 0.92) | above | **`buybacks_total`** |
| `dividends_paid` | `PaymentsOfDividends` | 25.58 % (lacks 23.31, differs 2.27) | above | **`dividends_paid_total`** |
| `dividends_paid` | `PaymentsOfDividendsMinorityInterest` | 2.00 % | above | **`dividends_paid_noncontrolling`** |
| `stock_issued` | `ProceedsFromIssuanceOrSaleOfEquity` | 4.07 % (lacks 2.97, differs 1.10) | above | **`stock_issued_total`** |
| `stock_issued` | `ProceedsFromStockOptionsExercised` | 31.25 % | above | stated gap |
| `debt_issued` | `ProceedsFromIssuanceOfLongTermDebt` | 39.01 % | above | **`debt_issued_long_term`** |
| `debt_issued` | `ProceedsFromShortTermDebt` | 7.35 % | above | **`debt_issued_short_term`** |
| `debt_issued`, `debt_repaid` | `ProceedsFromRepaymentsOfShortTermDebt` (net) | 11.90 %, 11.92 % | above | **`debt_short_term_net`** |
| `debt_repaid` | `RepaymentsOfLongTermDebt` | 41.53 % | above | **`debt_repaid_long_term`** |
| `debt_repaid` | `RepaymentsOfShortTermDebt` | 7.08 % | above | **`debt_repaid_short_term`** |

`total_liabilities`, `current_assets` and `current_liabilities` have no different concept in the table, so they have no gap to state.

**Why these eleven.** The rule lets an above-share concept become a labelled name. It does not require one. The recommendation admits a concept only when a family in the spec's table needs that quantity to compute its documented definition. XFIN as #2670 defines it counts long- and short-term debt, total dividends with preferred, and preferred issuance and repurchase. It leaves out noncontrolling payouts. Its common-equity variant could not cite the preferred parts as residue, because they are 2.9 to 25.6 %, so both variants need their own names. `cash_incl_restricted` follows the ASU 2016-18 migration, which is still growing. `depreciation_nonproduction` replaces the coverage that removing `DepreciationAndAmortization` costs the accruals family. The other eleven (`ProfitLoss`, the common-available net income, continuing-operations EPS, pre-tax income, equity including NCI, `Cash`, productive assets, `Depreciation`, option exercises) are different measures that no listed family's definition reads. They stay out as stated gaps, and a later family that needs one adds it through the same additive owner step.

## The other measures

- **Aliases among accessions carrying none.** None qualifies. For example, among ex-6770 accessions without `NetIncomeLoss`, 73 % carry `ProfitLoss` and 68 % carry the common-available figure, but `NetIncomeLoss` equals `ProfitLoss` in only 33 % of shared filing-periods. Among accessions without `PaymentsToAcquirePropertyPlantAndEquipment`, 34 % carry `PaymentsToAcquireProductiveAssets` (37 % equal values). Among accessions without `Liabilities`, 97 % carry `LiabilitiesAndStockholdersEquity`, a different quantity (question 5: a read-time computation). The full counts come from rerunning the appendix scripts.
- **Discovery (top 20 over-represented concepts).** The lists show filer type, not missing tags. Current items and operating income: bank concepts (`Deposits`, `NoninterestExpense`, `InterestAndFeeIncomeLoansAndLeases`, which appear in about 40 % of none-accessions against 0 to 1 % of carrying ones). EPS and equity: partnership concepts (`PartnersCapital`, `LimitedPartnersCapitalAccount`, about 17 % against 0 %). Capex and PP&E: real-estate concepts. Income tax and the payout names: development-stage filers (`EarningsPerShareBasicAndDiluted`, warrant and issuance-cost concepts). For `interest_expense` and `ppe_net`, discovery surfaces the migration tags named above.
- **Units.** Every candidate tag is overwhelmingly in `USD` (EPS in `USD/shares`). Foreign-currency entries are under 1 %, and are skipped and counted as `statement_non_usd` today. EPS also shows about 300 `pure` and 40 `shares` entries per tag, which are mis-unit filings and are skipped.
- **Wrong kind.** The instant names have zero duration entries. The duration names have a few instant entries: under 0.02 % for most, but 1.6 % for `dividends_paid` (2,174 of 135,164 entries) and 2.4 % for `stock_issued` (3,760 of 157,326). These become rows with `period_days = 0`, which every flow reader already filters out. That is not material for a reader that filters on `period_days`, as the spec requires, so no parser amendment is proposed.
- **Restatements** (priced CIKs, first vintage, a later different value; counted as restating events per stored row, so one key restated twice counts twice). `net_income` 11.7 % of rows, EPS 11.7 %, `operating_income` 16.0 %, `book_equity` 17.0 %, `income_tax` 10.2 %, `cash` 9.2 %, `depreciation` (both tags) 8.7 %, the current items about 7.4 %, `capex` 5.7 %, and the financing names 2.7 to 5.0 %. **EPS restatements a split explains:** `eps_basic` 9,855 of 48,160 (20 %) and `eps_diluted` 8,961 of 47,457 (19 %). The rest are other restatements (discontinued operations, reclassification, errors). The read-time split rule in the spec handles the 20 %. The other 80 % are first-vintage differences like any name's.

## T368's time and disk budget

Over all 58,193 non-benchmark issuer CIKs in `securities`. 46,274 have a stamped cover-form accession and get a statement read. 17,191 of those have a zip member and parse.

| Measure | Value |
|---|---|
| Projected rows, the 17 names (`depreciation` with both tags) | 5,877,260 |
| Plus the eleven recommended names, net of the `depreciation` change (scaled from priced CIKs, which hold 64 % of rows) | about 0.67 million, so about 6.5 million in all |
| Store bytes, at 76 bytes a row (measured: `statement_facts` copied with its UNIQUE index) | about 0.5 GB compact; allow 1 GB for in-place growth |
| Statement-cache bytes, at 283 bytes a row (the cache's record format) | 1.66 GB measured for the 17 names; about 1.85 GB with the eleven |
| Zip read and JSON load, all members | 95 s (median 3 ms, max 0.06 s) |
| Parse with the new lists, all members | 89 s (median 2.4 ms, p99 0.05 s, max 0.13 s) |
| CIKs absent from the zip, with a cover-form stamp (API requests) | 29,081; 24,875 last filed a 10-K, 10-Q, 20-F or 40-F in 2009 or earlier; **all 29,081 already hold an empty statement cache** |
| CIKs whose member trails their latest stamp (API requests) | 916 (425 latest filed 2020 or later) |
| Empty members with a cover stamp | 2 |

The times were measured on four parallel workers (one process each), so they are a slight overestimate of a single-process run. The statement cache is keyed by the latest accession and a digest of the tag lists, so T367's change misses every key, and each zip-absent CIK goes to the per-CIK API. That is about 30,000 requests, about 56 minutes at `edgar.requests_per_second = 9`, almost all of them 404s that are already known. [#2837](https://github.com/josejuarez96/tradepartner/issues/2837) proposes carrying an empty cached result forward in T365. Without it, plan T368 for about 1.5 hours, almost all of it requests. For T368's start check, the projected cache plus store bytes are **about 3 GB**, with the allowance included.

## Limits

- The real store is at schema 41 and has no `filing_index` table yet. The stamps cache stands in for it: form and acceptance per accession, as the ingest read them before T323a.
- Shares and equality are over companyfacts entries, which carry no dimensioned facts. A filer that tags EPS only per share class has none here, as in the store.
- "SIC 6770" is the latest `classifications` row known at acceptance for any of the CIK's securities, or the earliest row when none is known yet (a later row standing in). A blank check that merged keeps 6770 until its SIC changes.
- Gaps use simulated first-vintage rows from the production parser and the ingest's first-vintage rule, without the hold rule. The census drops 360 unstamped records, which would be held.
- Coverage counts any row by the #660 bound, including the few wrong-kind (instant) rows of a flow name; this overstates `stock_issued` and `dividends_paid` coverage by at most their 1.6 to 2.4 % instant entries.
- In-sample members are `universe_as_of` under default settings (about 1,009 a month). This is the top-1,000 universe, not every priced CIK.

## Candidate-alias list (written before the run)

The spec asks this PR to name, before the run, the us-gaap concepts it checks among the accessions that carry none of a name's candidate tags. Each concept below is only **looked for**. It joins a name's list only if the freeze rule admits it: the same concept by its taxonomy definition (the `description` the companyfacts payload carries) and equal values in filings that carry both. A concept that is broader or narrower is never an alias. Its gap is measured instead. The 20 most over-represented concepts per name (discovery) are reported beside this list and are not part of it.

| Name | Concepts checked among accessions carrying none |
|---|---|
| `net_income` | `ProfitLoss`, `NetIncomeLossAvailableToCommonStockholdersBasic`, `NetIncomeLossAvailableToCommonStockholdersDiluted`, `IncomeLossFromContinuingOperations`, `IncomeLossAttributableToParent`, `ComprehensiveIncomeNetOfTax`, `NetIncomeLossAllocatedToLimitedPartners`, `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest` |
| `eps_basic` | `IncomeLossFromContinuingOperationsPerBasicShare`, `IncomeLossFromContinuingOperationsPerBasicAndDilutedShare`, `NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax`, `EarningsPerShareBasicDistributed`, `EarningsPerShareDiluted` |
| `eps_diluted` | `IncomeLossFromContinuingOperationsPerDilutedShare`, `IncomeLossFromContinuingOperationsPerBasicAndDilutedShare`, `NetIncomeLossPerOutstandingLimitedPartnershipUnitDilutedNetOfTax`, `EarningsPerShareBasic` |
| `operating_income` | `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`, `IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments`, `OperatingExpenses`, `CostsAndExpenses`, `BenefitsLossesAndExpenses` |
| `book_equity` | `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`, `PartnersCapital`, `PartnersCapitalIncludingPortionAttributableToNoncontrollingInterest`, `MembersEquity`, `LimitedLiabilityCompanyLlcOrLimitedPartnershipLpMembersOrLimitedPartnersOwnershipInterest` |
| `total_liabilities` | `LiabilitiesAndStockholdersEquity`, `LiabilitiesNoncurrent`, `LiabilitiesFairValueDisclosure` |
| `current_assets` | `AssetsNoncurrent`, `OtherAssetsCurrent`, `CashAndCashEquivalentsAtCarryingValue` |
| `current_liabilities` | `LiabilitiesNoncurrent`, `OtherLiabilitiesCurrent`, `AccountsPayableCurrent` |
| `cash` | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`, `Cash`, `CashAndDueFromBanks`, `CashEquivalentsAtCarryingValue`, `CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations`, `CashCashEquivalentsAndShortTermInvestments` |
| `capex` | `PaymentsToAcquireProductiveAssets`, `PaymentsToAcquireOtherPropertyPlantAndEquipment`, `PaymentsForCapitalImprovements`, `PaymentsToAcquireRealEstate`, `PaymentsToDevelopRealEstateAssets`, `PaymentsToAcquireAndDevelopRealEstate`, `PaymentsToAcquireOilAndGasPropertyAndEquipment`, `PaymentsToAcquireOilAndGasProperty`, `PaymentsToAcquireMachineryAndEquipment` |
| `depreciation` | `Depreciation`, `DepreciationAmortizationAndAccretionNet`, `AmortizationOfIntangibleAssets`, `OtherDepreciationAndAmortization`, `DepreciationNonproduction`, `CostDepreciationAmortizationAndDepletion` |
| `income_tax` | `CurrentIncomeTaxExpenseBenefit`, `DeferredIncomeTaxExpenseBenefit`, `IncomeTaxesPaid`, `IncomeTaxesPaidNet`, `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest` |
| `buybacks` | `PaymentsForRepurchaseOfEquity`, `PaymentsForRepurchaseOfPreferredStockAndPreferenceStock`, `TreasuryStockValueAcquiredCostMethod`, `StockRepurchasedDuringPeriodValue` |
| `dividends_paid` | `PaymentsOfDividends`, `PaymentsOfDividendsMinorityInterest`, `PaymentsOfOrdinaryDividends`, `PaymentsOfDividendsPreferredStockAndPreferenceStock`, `DividendsCommonStockCash`, `DividendsCommonStock`, `DividendsCash`, `DistributionMadeToLimitedPartnerCashDistributionsPaid` |
| `stock_issued` | `ProceedsFromIssuanceOrSaleOfEquity`, `ProceedsFromStockOptionsExercised`, `ProceedsFromIssuanceOfInitialPublicOffering`, `ProceedsFromIssuanceOfPreferredStockAndPreferenceStock`, `ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans`, `ProceedsFromIssuanceOfPrivatePlacement`, `SaleOfStockConsiderationReceivedOnTransaction` |
| `debt_issued` | `ProceedsFromIssuanceOfLongTermDebt`, `ProceedsFromShortTermDebt`, `ProceedsFromRepaymentsOfShortTermDebt`, `ProceedsFromRepaymentsOfDebt`, `ProceedsFromIssuanceOfSeniorLongTermDebt`, `ProceedsFromConvertibleDebt`, `ProceedsFromLinesOfCredit`, `ProceedsFromLongTermLinesOfCredit`, `ProceedsFromNotesPayable`, `ProceedsFromRelatedPartyDebt`, `ProceedsFromBankDebt`, `ProceedsFromSecuredNotesPayable`, `ProceedsFromIssuanceOfUnsecuredDebt`, `ProceedsFromIssuanceOfSecuredDebt`, `ProceedsFromIssuanceOfCommercialPaper`, `ProceedsFromRepaymentsOfCommercialPaper` |
| `debt_repaid` | `RepaymentsOfLongTermDebt`, `RepaymentsOfShortTermDebt`, `ProceedsFromRepaymentsOfShortTermDebt`, `ProceedsFromRepaymentsOfDebt`, `RepaymentsOfLinesOfCredit`, `RepaymentsOfLongTermLinesOfCredit`, `RepaymentsOfNotesPayable`, `RepaymentsOfConvertibleDebt`, `RepaymentsOfSeniorDebt`, `RepaymentsOfRelatedPartyDebt`, `RepaymentsOfSecuredDebt`, `RepaymentsOfUnsecuredDebt`, `RepaymentsOfBankDebt`, `RepaymentsOfCommercialPaper`, `RepaymentsOfLongTermCapitalLeaseObligations`, `FinanceLeasePrincipalPayments` |
| `long_term_debt` (deferred) | `LongTermDebt`, `LongTermDebtAndCapitalLeaseObligations`, `LongTermNotesPayable`, `SeniorLongTermNotes`, `ConvertibleNotesPayable`, `DebtInstrumentCarryingAmount`, `LongTermDebtCurrent` |
| `interest_expense` (deferred) | `InterestExpenseDebt`, `InterestExpenseNonoperating`, `InterestAndDebtExpense`, `InterestExpenseOperating`, `InterestPaid`, `InterestPaidNet`, `InterestIncomeExpenseNonoperatingNet` |
| `rd_expense` (deferred) | `ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost`, `ResearchAndDevelopmentExpenseSoftwareExcludingAcquiredInProcessCost`, `ResearchAndDevelopmentInProcess` |
| `sga_expense` (deferred) | `GeneralAndAdministrativeExpense`, `SellingAndMarketingExpense`, `SellingExpense`, `OperatingExpenses` |
| `inventory` (deferred) | `InventoryGross`, `InventoryFinishedGoods`, `InventoryFinishedGoodsNetOfReserves`, `InventoryRawMaterialsNetOfReserves`, `InventoryWorkInProcess` |
| `receivables` (deferred) | `ReceivablesNetCurrent`, `AccountsNotesAndLoansReceivableNetCurrent`, `AccountsReceivableGrossCurrent`, `AccountsAndOtherReceivablesNetCurrent` |
| `ppe_net` (deferred) | `PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization`, `PropertyPlantAndEquipmentGross`, `RealEstateInvestmentPropertyNet`, `PublicUtilitiesPropertyPlantAndEquipmentNet`, `OilAndGasPropertySuccessfulEffortMethodNet`, `OilAndGasPropertyFullCostMethodNet` |

Every concept the spec's names table puts in its "Not aliased" column is also measured against its name by the freeze rule's gap, whether or not it appears above.

## Method (fixed before the run)

- **Accessions.** For each priced CIK (a CIK with a `securities` row whose security has a `prices_daily` row), every accession that carries at least one us-gaap fact in the CIK's zip member and whose stamped form is in `edgar.statement_forms`. The form and the acceptance come from the stamps cache (`data/edgar_cache/stamps/v1`), not the entry. The real store is at schema 41 and has no `filing_index` table yet (T324's seed adds it), so the stamps cache stands in for it; it is the source T323a's index rows were checked against. The filing year is the acceptance's New York year.
- **Carrying.** An accession carries a tag when the member has an entry for that tag with that `accn` in the name's read unit (`USD`, or `USD/shares` for EPS).
- **SIC 6770.** An accession is a blank-check filing when the latest `classifications` row of any of its CIK's securities known at the acceptance has SIC 6770.
- **First vintage and restatements.** Records come from `adapters.edgar.parse_statement_facts` (the production parser: precedence, conflicts, malformed and late entries), then the ingest's first-vintage rule keyed by `(fact_name, period_end, period_days)` per CIK in acceptance order. A later filing with a different value is `statement_restated`. An EPS restatement is **split-explained** when its value times the cumulative factor of the CIK's split rows with ex-date after the vintage's acceptance and on or before the restating filing's acceptance matches the vintage within 1 % or USD 0.01.
- **Freeze-rule gap.** In-sample month-ends are the last calendar day of each month from 2020-08 (the registered families' `in_sample_start`, 2020-08-31) to 2023-12 (the month before `holdout_start`, 2024-01-01), at 23:59 New York: 41 month-ends. Members are `universe_as_of` at each, under default settings. A name or a different concept "has a row known at the month-end" when its simulated first-vintage rows include one with `known_at` at or before the month-end and `period_end` within `universe.max_shares_age_days` (400) of the last completed session, the #660 health bound. The gap is the share of member-months where the name has none and the concept has one; for the three preferred-inclusive equity concepts it also counts member-months where both have one and the concept's latest key is missing from the name's rows or carries a different value. Above 0.5 % (ADR 0023 D1) the concept may become a separate labelled name; at or below, it is accepted residue.
- **T368 budget.** Over every non-benchmark issuer CIK in `securities` (the ingest's `_issuer_ciks`), the parse wall time per zip member with the frozen lists, the projected first-vintage rows of the new names, their JSON bytes in the statement cache's record format, store bytes at the measured bytes per `statement_facts` row, and the CIKs whose member is absent or does not hold their latest stamped cover-form accession (each one an API request).

## Appendix: the scripts

Run as `python -I store_inputs.py <store copy> store_inputs.pkl`, then `python -I census.py <companyfacts.zip> <edgar_cache/stamps> store_inputs.pkl census_out.pkl 4`, then `python -I analyze.py store_inputs.pkl census_out.pkl`, from a scratch directory, with the project's virtualenv interpreter. The census run took 105 s on four workers. It had no errors over the 58,193 CIKs.

<details><summary><code>store_inputs.py</code></summary>

```python
"""T363 phase A: read-only inputs from a copy of the store.

Usage: python -I store_inputs.py <store copy> <out.pickle>
Opens the copy read-only. Writes only <out.pickle>.
"""

import calendar
import pickle
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.calendar import last_completed_session
from tradepartner.config import Settings
from tradepartner.universe import universe_as_of

NY = ZoneInfo("America/New_York")


def month_ends() -> list[datetime]:
    out = []
    y, m = 2020, 8
    while (y, m) <= (2023, 12):
        day = calendar.monthrange(y, m)[1]
        out.append(datetime(y, m, day, 23, 59, 0, tzinfo=NY))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def main(store: str, out: str) -> None:
    settings = Settings(_env_file=None)
    conn = duckdb.connect(store, read_only=True)
    priced = {
        r[0]
        for r in conn.execute(
            "select distinct cik from securities where security_id in "
            "(select distinct security_id from prices_daily)"
        ).fetchall()
    }
    issuers = sorted(
        r[0]
        for r in conn.execute("select distinct cik from securities where not benchmark").fetchall()
    )
    sid_cik = dict(conn.execute("select distinct security_id, cik from securities").fetchall())
    sic_hist: dict[str, list[tuple[float, int | None]]] = defaultdict(list)
    for sid, sic, known_at in conn.execute(
        "select security_id, sic, known_at from classifications"
    ).fetchall():
        cik = sid_cik.get(sid)
        if cik is not None:
            sic_hist[cik].append((known_at.timestamp(), sic))
    for v in sic_hist.values():
        v.sort(key=lambda x: x[0])
    splits: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    for sid, ex_date, ratio, known_at in conn.execute(
        "select security_id, ex_date, ratio_or_amount, known_at from corporate_actions "
        "where action_type = 'split' and not cancelled"
    ).fetchall():
        cik = sid_cik.get(sid)
        if cik is not None:
            ex = datetime(ex_date.year, ex_date.month, ex_date.day, tzinfo=NY).timestamp()
            splits[cik].append((ex, float(ratio), known_at.timestamp()))
    months = []
    bound = settings.universe.max_shares_age_days
    for t in month_ends():
        t0 = time.time()
        u = universe_as_of(conn, t, settings)
        session = last_completed_session(t)
        members = [(sid, cik) for sid, cik in u.members.select("security_id", "cik").iter_rows()]
        months.append(
            {
                "t": t.timestamp(),
                "label": t.strftime("%Y-%m"),
                "session": session.toordinal(),
                "fresh_from": (session - timedelta(days=bound)).toordinal(),
                "members": members,
            }
        )
        print(t.date(), len(members), f"{time.time() - t0:.1f}s", flush=True)
    with open(out, "wb") as fh:
        pickle.dump(
            {
                "priced": priced,
                "issuers": issuers,
                "sic_hist": dict(sic_hist),
                "splits": dict(splits),
                "months": months,
                "bound": bound,
            },
            fh,
        )
    print("priced", len(priced), "issuers", len(issuers), "months", len(months))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

</details>

<details><summary><code>census.py</code></summary>

```python
"""T363 phase B: the statement tag-alias census over the cached companyfacts.zip.

Usage: python -I census.py <companyfacts.zip> <stamps dir> <store_inputs.pickle> <out.pickle> [workers]

Read-only: opens the zip and the stamps files for reading, writes only <out.pickle>.
Uses the production parser (`adapters.edgar.parse_statement_facts`) for records,
and replicates the ingest's first-vintage rule (`ingest.statement_fact_rows`,
keyed by `(fact_name, period_end, period_days)`, acceptance order).
"""

import json
import pickle
import sys
import time
import zipfile
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from zoneinfo import ZoneInfo

from tradepartner.adapters.edgar import parse_statement_facts
from tradepartner.adapters.edgar_source import (
    _statement_conflict_to_json,
    _statement_record_to_json,
)
from tradepartner.config_edgar import _STATEMENT_TAGS

NY = ZoneInfo("America/New_York")
FORMS = ("10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT")
COVER = ("10-K", "10-Q", "20-F", "40-F")
G = "us-gaap:"

NEW = {
    "net_income": ["NetIncomeLoss"],
    "eps_basic": ["EarningsPerShareBasic", "EarningsPerShareBasicAndDiluted"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "operating_income": ["OperatingIncomeLoss"],
    "book_equity": ["StockholdersEquity"],
    "total_liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "depreciation": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends_paid": ["PaymentsOfDividendsCommonStock"],
    "stock_issued": ["ProceedsFromIssuanceOfCommonStock"],
    "debt_issued": ["ProceedsFromIssuanceOfDebt"],
    "debt_repaid": ["RepaymentsOfDebt"],
}
DEFERRED = {
    "long_term_debt": ["LongTermDebtNoncurrent"],
    "interest_expense": ["InterestExpense"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": ["SellingGeneralAndAdministrativeExpense"],
    "inventory": ["InventoryNet"],
    "receivables": ["AccountsReceivableNetCurrent"],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
}
NAMES = {**NEW, **DEFERRED}
INSTANT = {
    "book_equity",
    "total_liabilities",
    "current_assets",
    "current_liabilities",
    "cash",
    "long_term_debt",
    "inventory",
    "receivables",
    "ppe_net",
}
EPS = {"eps_basic", "eps_diluted"}
NOT_ALIASED = {
    "net_income": ["ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "eps_basic": ["IncomeLossFromContinuingOperationsPerBasicShare"],
    "eps_diluted": ["IncomeLossFromContinuingOperationsPerDilutedShare"],
    "operating_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    "book_equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "total_liabilities": [],
    "current_assets": [],
    "current_liabilities": [],
    "cash": ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "capex": ["PaymentsToAcquireProductiveAssets"],
    "depreciation": ["Depreciation"],
    "income_tax": ["CurrentIncomeTaxExpenseBenefit"],
    "buybacks": ["PaymentsForRepurchaseOfEquity"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsMinorityInterest"],
    "stock_issued": ["ProceedsFromIssuanceOrSaleOfEquity", "ProceedsFromStockOptionsExercised"],
    "debt_issued": [
        "ProceedsFromIssuanceOfLongTermDebt",
        "ProceedsFromShortTermDebt",
        "ProceedsFromRepaymentsOfShortTermDebt",
    ],
    "debt_repaid": [
        "RepaymentsOfLongTermDebt",
        "RepaymentsOfShortTermDebt",
        "ProceedsFromRepaymentsOfShortTermDebt",
    ],
    "long_term_debt": [],
    "interest_expense": [],
    "rd_expense": [],
    "sga_expense": [],
    "inventory": [],
    "receivables": [],
    "ppe_net": [],
}
# Preferred-inclusive equity concepts: a differing value also counts as a gap.
DIFFERS = {
    ("buybacks", "PaymentsForRepurchaseOfEquity"),
    ("dividends_paid", "PaymentsOfDividends"),
    ("stock_issued", "ProceedsFromIssuanceOrSaleOfEquity"),
}
ALIASES = {
    "net_income": [
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLossAvailableToCommonStockholdersDiluted",
        "IncomeLossFromContinuingOperations",
        "IncomeLossAttributableToParent",
        "ComprehensiveIncomeNetOfTax",
        "NetIncomeLossAllocatedToLimitedPartners",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ],
    "eps_basic": [
        "IncomeLossFromContinuingOperationsPerBasicShare",
        "IncomeLossFromContinuingOperationsPerBasicAndDilutedShare",
        "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax",
        "EarningsPerShareBasicDistributed",
        "EarningsPerShareDiluted",
    ],
    "eps_diluted": [
        "IncomeLossFromContinuingOperationsPerDilutedShare",
        "IncomeLossFromContinuingOperationsPerBasicAndDilutedShare",
        "NetIncomeLossPerOutstandingLimitedPartnershipUnitDilutedNetOfTax",
        "EarningsPerShareBasic",
    ],
    "operating_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "OperatingExpenses",
        "CostsAndExpenses",
        "BenefitsLossesAndExpenses",
    ],
    "book_equity": [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapital",
        "PartnersCapitalIncludingPortionAttributableToNoncontrollingInterest",
        "MembersEquity",
        "LimitedLiabilityCompanyLlcOrLimitedPartnershipLpMembersOrLimitedPartnersOwnershipInterest",
    ],
    "total_liabilities": [
        "LiabilitiesAndStockholdersEquity",
        "LiabilitiesNoncurrent",
        "LiabilitiesFairValueDisclosure",
    ],
    "current_assets": [
        "AssetsNoncurrent",
        "OtherAssetsCurrent",
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    "current_liabilities": [
        "LiabilitiesNoncurrent",
        "OtherLiabilitiesCurrent",
        "AccountsPayableCurrent",
    ],
    "cash": [
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
        "CashAndDueFromBanks",
        "CashEquivalentsAtCarryingValue",
        "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
        "CashCashEquivalentsAndShortTermInvestments",
    ],
    "capex": [
        "PaymentsToAcquireProductiveAssets",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireRealEstate",
        "PaymentsToDevelopRealEstateAssets",
        "PaymentsToAcquireAndDevelopRealEstate",
        "PaymentsToAcquireOilAndGasPropertyAndEquipment",
        "PaymentsToAcquireOilAndGasProperty",
        "PaymentsToAcquireMachineryAndEquipment",
    ],
    "depreciation": [
        "Depreciation",
        "DepreciationAmortizationAndAccretionNet",
        "AmortizationOfIntangibleAssets",
        "OtherDepreciationAndAmortization",
        "DepreciationNonproduction",
        "CostDepreciationAmortizationAndDepletion",
    ],
    "income_tax": [
        "CurrentIncomeTaxExpenseBenefit",
        "DeferredIncomeTaxExpenseBenefit",
        "IncomeTaxesPaid",
        "IncomeTaxesPaidNet",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ],
    "buybacks": [
        "PaymentsForRepurchaseOfEquity",
        "PaymentsForRepurchaseOfPreferredStockAndPreferenceStock",
        "TreasuryStockValueAcquiredCostMethod",
        "StockRepurchasedDuringPeriodValue",
    ],
    "dividends_paid": [
        "PaymentsOfDividends",
        "PaymentsOfDividendsMinorityInterest",
        "PaymentsOfOrdinaryDividends",
        "PaymentsOfDividendsPreferredStockAndPreferenceStock",
        "DividendsCommonStockCash",
        "DividendsCommonStock",
        "DividendsCash",
        "DistributionMadeToLimitedPartnerCashDistributionsPaid",
    ],
    "stock_issued": [
        "ProceedsFromIssuanceOrSaleOfEquity",
        "ProceedsFromStockOptionsExercised",
        "ProceedsFromIssuanceOfInitialPublicOffering",
        "ProceedsFromIssuanceOfPreferredStockAndPreferenceStock",
        "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans",
        "ProceedsFromIssuanceOfPrivatePlacement",
        "SaleOfStockConsiderationReceivedOnTransaction",
    ],
    "debt_issued": [
        "ProceedsFromIssuanceOfLongTermDebt",
        "ProceedsFromShortTermDebt",
        "ProceedsFromRepaymentsOfShortTermDebt",
        "ProceedsFromRepaymentsOfDebt",
        "ProceedsFromIssuanceOfSeniorLongTermDebt",
        "ProceedsFromConvertibleDebt",
        "ProceedsFromLinesOfCredit",
        "ProceedsFromLongTermLinesOfCredit",
        "ProceedsFromNotesPayable",
        "ProceedsFromRelatedPartyDebt",
        "ProceedsFromBankDebt",
        "ProceedsFromSecuredNotesPayable",
        "ProceedsFromIssuanceOfUnsecuredDebt",
        "ProceedsFromIssuanceOfSecuredDebt",
        "ProceedsFromIssuanceOfCommercialPaper",
        "ProceedsFromRepaymentsOfCommercialPaper",
    ],
    "debt_repaid": [
        "RepaymentsOfLongTermDebt",
        "RepaymentsOfShortTermDebt",
        "ProceedsFromRepaymentsOfShortTermDebt",
        "ProceedsFromRepaymentsOfDebt",
        "RepaymentsOfLinesOfCredit",
        "RepaymentsOfLongTermLinesOfCredit",
        "RepaymentsOfNotesPayable",
        "RepaymentsOfConvertibleDebt",
        "RepaymentsOfSeniorDebt",
        "RepaymentsOfRelatedPartyDebt",
        "RepaymentsOfSecuredDebt",
        "RepaymentsOfUnsecuredDebt",
        "RepaymentsOfBankDebt",
        "RepaymentsOfCommercialPaper",
        "RepaymentsOfLongTermCapitalLeaseObligations",
        "FinanceLeasePrincipalPayments",
    ],
    "long_term_debt": [
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermNotesPayable",
        "SeniorLongTermNotes",
        "ConvertibleNotesPayable",
        "DebtInstrumentCarryingAmount",
        "LongTermDebtCurrent",
    ],
    "interest_expense": [
        "InterestExpenseDebt",
        "InterestExpenseNonoperating",
        "InterestAndDebtExpense",
        "InterestExpenseOperating",
        "InterestPaid",
        "InterestPaidNet",
        "InterestIncomeExpenseNonoperatingNet",
    ],
    "rd_expense": [
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        "ResearchAndDevelopmentExpenseSoftwareExcludingAcquiredInProcessCost",
        "ResearchAndDevelopmentInProcess",
    ],
    "sga_expense": [
        "GeneralAndAdministrativeExpense",
        "SellingAndMarketingExpense",
        "SellingExpense",
        "OperatingExpenses",
    ],
    "inventory": [
        "InventoryGross",
        "InventoryFinishedGoods",
        "InventoryFinishedGoodsNetOfReserves",
        "InventoryRawMaterialsNetOfReserves",
        "InventoryWorkInProcess",
    ],
    "receivables": [
        "ReceivablesNetCurrent",
        "AccountsNotesAndLoansReceivableNetCurrent",
        "AccountsReceivableGrossCurrent",
        "AccountsAndOtherReceivablesNetCurrent",
    ],
    "ppe_net": [
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        "PropertyPlantAndEquipmentGross",
        "RealEstateInvestmentPropertyNet",
        "PublicUtilitiesPropertyPlantAndEquipmentNet",
        "OilAndGasPropertySuccessfulEffortMethodNet",
        "OilAndGasPropertyFullCostMethodNet",
    ],
}
NAME_ORDER = list(NAMES)
BIT = {n: 1 << i for i, n in enumerate(NAME_ORDER)}


def unit_of(name: str) -> str:
    return "USD/shares" if name in EPS else "USD"


# Virtual names simulated through the parser and the first-vintage rule.
VIRTUAL: dict[str, tuple[list[str], str]] = {}
for _n, _tags in NAMES.items():
    VIRTUAL[_n] = (_tags, unit_of(_n))
    for _c in NOT_ALIASED[_n]:
        VIRTUAL[f"alt|{_n}|{_c}"] = ([_c], unit_of(_n))
VIRTUAL["only|depreciation|DepreciationDepletionAndAmortization"] = (
    ["DepreciationDepletionAndAmortization"],
    "USD",
)
VIRTUAL["only|depreciation|DepreciationAndAmortization"] = (["DepreciationAndAmortization"], "USD")
VIRTUAL["only|eps_basic|EarningsPerShareBasic"] = (["EarningsPerShareBasic"], "USD/shares")
VIRTUAL["only|eps_diluted|EarningsPerShareDiluted"] = (["EarningsPerShareDiluted"], "USD/shares")

# The production map the backfill would parse: today's eight names plus the new ones.
PROD_USD = {k: list(v) for k, v in _STATEMENT_TAGS.items()}
PROD_USD.update({n: [G + t for t in tags] for n, tags in NEW.items() if n not in EPS})
PROD_SHARES = {n: [G + t for t in NEW[n]] for n in EPS}

WATCH: dict[str, set[str]] = {
    n: set(NAMES[n]) | set(NOT_ALIASED[n]) | set(ALIASES[n]) for n in NAMES
}
ALL_WATCH = set().union(*WATCH.values())


@dataclass(frozen=True)
class Stamp:
    accession: str
    form: str
    accepted_at: datetime | None


_zip: zipfile.ZipFile | None = None
_inputs: dict = {}


def init(zip_path: str, stamps_dir: str, inputs_path: str) -> None:
    global _zip, _inputs
    _zip = zipfile.ZipFile(zip_path)
    with open(inputs_path, "rb") as fh:
        _inputs = pickle.load(fh)
    _inputs["stamps_dir"] = Path(stamps_dir)
    _inputs["gap_ciks"] = {cik for m in _inputs["months"] for _, cik in m["members"]}


def load_stamps(cik: str) -> dict[str, Stamp]:
    path = _inputs["stamps_dir"] / "v1" / f"{cik}.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    out = {}
    for accn, rec in data["records"].items():
        acc = rec[3]
        out[accn] = Stamp(accn, rec[0], None if acc is None else datetime.fromisoformat(acc))
    return out


def sic_at(cik: str, ts: float) -> int | None | str:
    hist = _inputs["sic_hist"].get(cik)
    if not hist:
        return "none"
    i = bisect_right([h[0] for h in hist], ts)
    if i == 0:
        return hist[0][1]  # earliest known (none known yet at acceptance)
    return hist[i - 1][1]


def first_vintage(records):
    """The ingest's rule: stamped records in (accepted_at, accession) order;
    the first record of a (fact, end, days) key is the vintage; a later
    filing (not earlier-accepted) with a different value is restated."""
    by_filing = defaultdict(list)
    unstamped = 0
    for r in records:
        if r.accepted_at is None:
            unstamped += 1
            continue
        by_filing[(r.accepted_at, r.accession)].append(r)
    vint: dict = {}
    restated = Counter()
    late = Counter()
    restate_events = []
    for (acc, accn), recs in sorted(by_filing.items()):
        for r in recs:
            key = (r.fact_name, r.period_end, r.period_days)
            v = vint.get(key)
            if v is None:
                vint[key] = (acc, r.value, accn, r)
                continue
            if v[2] == accn:
                continue
            if acc < v[0]:
                late[r.fact_name] += 1
            elif r.value != v[1]:
                restated[r.fact_name] += 1
                if r.fact_name in EPS:
                    restate_events.append(
                        (r.fact_name, v[0].timestamp(), v[1], acc.timestamp(), r.value)
                    )
    return vint, restated, late, restate_events, unstamped


def process(cik: str):
    try:
        return _process(cik)
    except Exception as error:  # recorded, never silent
        return {"cik": cik, "error": repr(error)[:300]}


def _process(cik: str):
    assert _zip is not None
    res: dict = {"cik": cik}
    stamps = load_stamps(cik)
    cover = [
        s
        for s in stamps.values()
        if s.accepted_at is not None and s.form.removesuffix("/A") in COVER
    ]
    latest = max(cover, key=lambda s: (s.accepted_at, s.accession)).accession if cover else None
    res["has_cover"] = latest is not None
    member = f"CIK{cik}.json"
    t0 = time.perf_counter()
    try:
        raw = _zip.read(member)
    except KeyError:
        res["in_zip"] = False
        return res
    res["in_zip"] = True
    payload = json.loads(raw)
    t_load = time.perf_counter() - t0
    if "facts" not in payload:
        res["empty"] = True
        return res
    payload.setdefault("cik", int(cik))
    facts = payload["facts"]
    holds = latest is not None and any(
        e["accn"] == latest
        for concepts in facts.values()
        for concept in concepts.values()
        for entries in concept["units"].values()
        for e in entries
    )
    res["holds_latest"] = holds
    if latest is None:
        return res  # no statement read for this CIK at all
    # Wall time: what the backfill's parse costs with the new lists.
    t1 = time.perf_counter()
    prod_u = parse_statement_facts(payload, PROD_USD, FORMS, ["USD"], stamps)
    prod_s = parse_statement_facts(payload, PROD_SHARES, FORMS, ["USD/shares"], stamps)
    t_parse = time.perf_counter() - t1
    res["t_load"] = t_load
    res["t_parse"] = t_parse
    new_names = set(NEW)
    new_recs = [r for r in (*prod_u.records, *prod_s.records) if r.fact_name in new_names]
    new_conf = [c for c in (*prod_u.conflicts, *prod_s.conflicts) if c.fact_name in new_names]
    res["cache_bytes"] = sum(
        len(json.dumps(_statement_record_to_json(r))) + 2 for r in new_recs
    ) + sum(len(json.dumps(_statement_conflict_to_json(c))) + 2 for c in new_conf)
    vint_prod, restated_prod, _, _, _ = first_vintage(new_recs)
    res["new_rows"] = Counter(k[0] for k in vint_prod)
    res["new_restated"] = restated_prod
    res["new_conflicts"] = Counter(c.fact_name for c in new_conf)
    res["non_unit"] = prod_u.non_unit + prod_s.non_unit
    if cik not in _inputs["priced"]:
        return res

    # ---- priced CIK: the accession-level census ----
    usgaap = facts.get("us-gaap", {})
    acc_concepts: dict[str, set[str]] = defaultdict(set)
    for concept, cdata in usgaap.items():
        accns = {e["accn"] for entries in cdata["units"].values() for e in entries}
        for a in accns:
            acc_concepts[a].add(concept)
    # read-unit entries of watched tags, per accession
    watched: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    units_seen = Counter()
    for tag in ALL_WATCH:
        cdata = usgaap.get(tag)
        if cdata is None:
            continue
        for unit, entries in cdata["units"].items():
            units_seen[(tag, unit)] += len(entries)
            for e in entries:
                watched[e["accn"]][tag].append((unit, e.get("start"), e["end"], e["val"]))
    res["units_seen"] = units_seen
    res["descriptions"] = {
        tag: (usgaap[tag].get("label"), usgaap[tag].get("description"))
        for tag in ALL_WATCH
        if tag in usgaap
    }
    acc_total = Counter()
    tag_carry = Counter()
    none_ct = Counter()
    alias_none = Counter()
    disc: dict = defaultdict(Counter)
    disc_n = Counter()
    pair_eq = Counter()
    pair_ne = Counter()
    wrong_kind = Counter()
    kind_total = Counter()
    sic_unknown = 0
    for accn, concepts in acc_concepts.items():
        st = stamps.get(accn)
        if st is None or st.accepted_at is None or st.form not in FORMS:
            continue
        ts = st.accepted_at.timestamp()
        year = st.accepted_at.astimezone(NY).year
        sic = sic_at(cik, ts)
        if sic == "none":
            sic_unknown += 1
        spac = sic == 6770
        acc_total[(year, spac)] += 1
        w = watched.get(accn, {})
        mask = 0
        for name in NAME_ORDER:
            unit = unit_of(name)
            carried = [t for t in NAMES[name] if any(x[0] == unit for x in w.get(t, ()))]
            for t in WATCH[name]:
                if any(x[0] == unit for x in w.get(t, ())):
                    tag_carry[(name, t, year, spac)] += 1
            if carried:
                mask |= BIT[name]
            else:
                none_ct[(name, year, spac)] += 1
                for a in ALIASES[name]:
                    if any(x[0] == unit for x in w.get(a, ())):
                        alias_none[(name, a, year, spac)] += 1
            # wrong kind, on the candidate tags in the read unit
            for t in NAMES[name]:
                for unit_, start, end, val in w.get(t, ()):
                    if unit_ != unit:
                        continue
                    kind_total[name] += 1
                    if (start is None) != (name in INSTANT):
                        wrong_kind[(name, t)] += 1
            # value equality: each candidate against every other watched tag
            per_tag: dict[str, dict] = {}
            for t in WATCH[name]:
                vals = defaultdict(set)
                for unit_, start, end, val in w.get(t, ()):
                    if unit_ == unit:
                        vals[(start, end)].add(val)
                if vals:
                    per_tag[t] = vals
            for c in NAMES[name]:
                if c not in per_tag:
                    continue
                for o, ov in per_tag.items():
                    if o == c:
                        continue
                    for period, cv in per_tag[c].items():
                        if period in ov and len(cv) == 1 and len(ov[period]) == 1:
                            if cv == ov[period]:
                                pair_eq[(name, c, o)] += 1
                            else:
                                pair_ne[(name, c, o)] += 1
        bucket = "2009-2020" if year <= 2020 else ("2021-2022" if year <= 2022 else "2023-2026")
        disc[(bucket, spac, mask)].update(concepts)
        disc_n[(bucket, spac, mask)] += 1
    res.update(
        acc_total=acc_total,
        tag_carry=tag_carry,
        none_ct=none_ct,
        alias_none=alias_none,
        disc=dict(disc),
        disc_n=disc_n,
        pair_eq=pair_eq,
        pair_ne=pair_ne,
        wrong_kind=wrong_kind,
        kind_total=kind_total,
        sic_unknown=sic_unknown,
    )
    # first-vintage simulation of every virtual name (restatements, gap rows)
    vu = {k: [G + t for t in v[0]] for k, v in VIRTUAL.items() if v[1] == "USD"}
    vs = {k: [G + t for t in v[0]] for k, v in VIRTUAL.items() if v[1] == "USD/shares"}
    pu = parse_statement_facts(payload, vu, FORMS, ["USD"], stamps)
    ps = parse_statement_facts(payload, vs, FORMS, ["USD/shares"], stamps)
    vint, restated, late, events, unstamped = first_vintage([*pu.records, *ps.records])
    res["v_restated"] = restated
    res["v_rows"] = Counter(k[0] for k in vint)
    res["v_unstamped"] = unstamped
    # EPS restatements a split explains
    splits = _inputs["splits"].get(cik, [])
    split_ok = Counter()
    split_other = Counter()
    for name, v_ts, v_val, r_ts, r_val in events:
        factor = 1.0
        for ex, ratio, _known in splits:
            if v_ts < ex <= r_ts:
                factor *= ratio
        explained = factor != 1.0 and (abs(r_val * factor - v_val) <= max(0.01, 0.01 * abs(v_val)))
        (split_ok if explained else split_other)[name] += 1
    res["eps_split_restated"] = split_ok
    res["eps_other_restated"] = split_other
    if cik in _inputs["gap_ciks"]:
        rows = defaultdict(list)
        for (fname, end, days), (acc, val, accn, r) in vint.items():
            rows[fname].append((acc.timestamp(), end.toordinal(), days, val))
        res["gap_rows"] = dict(rows)
    return res


SUMMED = (
    "acc_total",
    "tag_carry",
    "none_ct",
    "alias_none",
    "disc_n",
    "pair_eq",
    "pair_ne",
    "wrong_kind",
    "kind_total",
    "units_seen",
    "v_restated",
    "v_rows",
    "eps_split_restated",
    "eps_other_restated",
    "new_rows",
    "new_restated",
    "new_conflicts",
)


def merge(agg: dict, r: dict) -> None:
    """Fold one CIK's counters into the run totals and drop the bulky ones."""
    for key in SUMMED:
        if key in r:
            agg.setdefault(key, Counter()).update(r[key])
            if key not in ("new_rows", "new_restated"):
                del r[key]
    for k, c in r.pop("disc", {}).items():
        agg["disc"][k].update(c)
    for tag, d in r.pop("descriptions", {}).items():
        agg["descriptions"].setdefault(tag, d)
    for key in ("sic_unknown", "v_unstamped"):
        agg[key] = agg.get(key, 0) + r.pop(key, 0)


def main() -> None:
    zip_path, stamps_dir, inputs_path, out = sys.argv[1:5]
    workers = int(sys.argv[5]) if len(sys.argv) > 5 else 4
    with open(inputs_path, "rb") as fh:
        inputs = pickle.load(fh)
    ciks = inputs["issuers"]
    if len(sys.argv) > 6:
        ciks = ciks[: int(sys.argv[6])]
    t0 = time.time()
    results = []
    agg: dict = {"disc": defaultdict(Counter), "descriptions": {}}
    with Pool(workers, initializer=init, initargs=(zip_path, stamps_dir, inputs_path)) as pool:
        for i, r in enumerate(pool.imap_unordered(process, ciks, chunksize=8)):
            merge(agg, r)
            results.append(r)
            if i % 2000 == 0:
                print(i, f"{time.time() - t0:.0f}s", flush=True)
    with open(out, "wb") as fh:
        pickle.dump(
            {"results": results, "agg": agg, "wall": time.time() - t0, "workers": workers}, fh
        )
    print("done", len(results), f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
```

</details>

<details><summary><code>analyze.py</code></summary>

```python
"""T363 phase C: tables from the census output. Usage: python -I analyze.py <store_inputs.pickle> <census_out.pickle>"""

import pickle
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(
    0, __import__("os").path.dirname(__import__("os").path.abspath(__file__))
)  # census.py beside this file

STORE_BYTES_PER_ROW = 76  # measured: statement_facts with its UNIQUE index, compact copy


def main() -> None:
    inputs = pickle.load(open(sys.argv[1], "rb"))
    out = pickle.load(open(sys.argv[2], "rb"))
    from census import ALIASES, DIFFERS, EPS, NAMES, NEW, NOT_ALIASED  # noqa: PLC0415

    agg = out["agg"]
    results = out["results"]
    years = list(range(2009, 2027))
    acc = agg["acc_total"]
    print("== run ==")
    print(
        "results",
        len(results),
        "errors",
        sum("error" in r for r in results),
        "wall",
        round(out["wall"]),
        "workers",
        out["workers"],
    )
    for r in [r for r in results if "error" in r][:10]:
        print("  error", r["cik"], r["error"])
    print(
        "sic_unknown accessions",
        agg.get("sic_unknown"),
        "unstamped records dropped",
        agg.get("v_unstamped"),
    )

    print("\n== accessions per year (all, SIC 6770) ==")
    for y in years:
        a = acc[(y, False)] + acc[(y, True)]
        print(y, a, acc[(y, True)], f"{100 * acc[(y, True)] / a:.1f}%" if a else "")

    def share(name, y, spac_filter):
        keys = [(y, s) for s in (False, True) if spac_filter is None or s == spac_filter]
        tot = sum(acc[k] for k in keys)
        none = sum(agg["none_ct"][(name, *k)] for k in keys)
        return (tot - none) / tot * 100 if tot else float("nan"), tot

    print("\n== share of accessions carrying any candidate tag, by year: all / ex-6770 ==")
    print("name".ljust(20), " ".join(str(y)[2:].rjust(9) for y in years[1:]))
    for n in NAMES:
        cells = []
        for y in years[1:]:
            a, _ = share(n, y, None)
            b, _ = share(n, y, False)
            cells.append(f"{a:4.0f}/{b:<4.0f}")
        print(n.ljust(20), " ".join(cells))

    print("\n== share per candidate tag (all years 2010-2026, ex-6770) ==")
    tc = agg["tag_carry"]
    tot_ex = sum(acc[(y, False)] for y in years)
    tot_all = sum(acc[(y, s)] for y in years for s in (False, True))
    for n in NAMES:
        parts = []
        for t in NAMES[n]:
            c = sum(tc[(n, t, y, False)] for y in years)
            parts.append(f"{t} {100 * c / tot_ex:.1f}%")
        none = sum(agg["none_ct"][(n, y, False)] for y in years)
        print(n.ljust(20), "; ".join(parts), f"; none {100 * none / tot_ex:.1f}%")
    print("accessions ex-6770", tot_ex, "all", tot_all)

    print(
        "\n== aliases among accessions carrying none (ex-6770, all years): count, share of none =="
    )
    an = agg["alias_none"]
    for n in NAMES:
        none = sum(agg["none_ct"][(n, y, False)] for y in years)
        parts = []
        for a in ALIASES[n]:
            c = sum(an[(n, a, y, False)] for y in years)
            if c:
                parts.append(f"{a} {c} ({100 * c / none:.1f}%)")
        print(f"{n} [none={none}]:", "; ".join(parts) or "-")

    print(
        "\n== discovery: top 20 over-represented concepts among none vs carrying (ex-6770, all years) =="
    )
    from census import BIT  # noqa: PLC0415

    disc = agg["disc"]
    dn = agg["disc_n"]
    for n in NAMES:
        none_c, carry_c = Counter(), Counter()
        none_n = carry_n = 0
        for (bucket, spac, mask), c in disc.items():
            if spac:
                continue
            if mask & BIT[n]:
                carry_c.update(c)
                carry_n += dn[(bucket, spac, mask)]
            else:
                none_c.update(c)
                none_n += dn[(bucket, spac, mask)]
        scores = []
        for concept, k in none_c.items():
            pn = k / none_n if none_n else 0
            pc = carry_c[concept] / carry_n if carry_n else 0
            scores.append((pn - pc, concept, pn, pc))
        scores.sort(reverse=True)
        print(
            f"{n} [none {none_n}, carrying {carry_n}]:",
            "; ".join(f"{c} {100 * pn:.0f}/{100 * pc:.0f}" for _, c, pn, pc in scores[:20]),
        )

    print("\n== value equality, candidate vs other tag, same filing and period: equal / differ ==")
    eq, ne = agg["pair_eq"], agg["pair_ne"]
    for n in NAMES:
        pairs = sorted({(c, o) for (nn, c, o) in set(eq) | set(ne) if nn == n})
        parts = []
        for c, o in pairs:
            e, d = eq[(n, c, o)], ne[(n, c, o)]
            if e + d >= 1 and (o in NAMES[n] or o in NOT_ALIASED[n] or e + d >= 50):
                parts.append(f"{c}~{o} {e}/{d} ({100 * e / (e + d):.0f}% eq)")
        print(f"{n}:", "; ".join(parts) or "-")

    print("\n== units seen (entries) per candidate / not-aliased tag ==")
    us = agg["units_seen"]
    for n in NAMES:
        for t in NAMES[n] + NOT_ALIASED[n]:
            u = {unit: c for (tag, unit), c in us.items() if tag == t}
            if u:
                print(f"{n} {t}:", u)

    print("\n== wrong-kind entries on candidate tags (read unit) ==")
    for n in NAMES:
        wk = {t: agg["wrong_kind"][(n, t)] for t in NAMES[n]}
        print(n, "total", agg["kind_total"][n], "wrong", wk)

    print("\n== restatements (priced CIKs, first-vintage simulation) ==")
    vr, vrows = agg["v_restated"], agg["v_rows"]
    for n in NAMES:
        print(
            n,
            "rows",
            vrows[n],
            "restated",
            vr[n],
            f"{100 * vr[n] / vrows[n]:.2f}%" if vrows[n] else "",
        )
    for n in EPS:
        print(
            n,
            "split-explained",
            agg["eps_split_restated"][n],
            "other",
            agg["eps_other_restated"][n],
        )

    print("\n== descriptions (taxonomy, from companyfacts) ==")
    for t in (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
        "EarningsPerShareBasic",
        "EarningsPerShareBasicAndDiluted",
        "EarningsPerShareDiluted",
        "PaymentsOfDividends",
        "PaymentsOfDividendsMinorityInterest",
        "PaymentsOfDividendsCommonStock",
        "ProceedsFromIssuanceOfDebt",
        "ProceedsFromIssuanceOfLongTermDebt",
        "RepaymentsOfDebt",
        "RepaymentsOfLongTermDebt",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
    ):
        d = agg["descriptions"].get(t)
        print(t, "::", d)

    # ---- freeze-rule gaps on in-sample universe-months ----
    print("\n== freeze-rule gaps, in-sample month-ends x universe_as_of members ==")
    gap_rows = {r["cik"]: r.get("gap_rows", {}) for r in results if "gap_rows" in r}
    months = inputs["months"]
    denom = sum(len(m["members"]) for m in months)
    print("member-months", denom, "0.5% =", denom * 0.005)

    def fresh(rows, t, ff):
        return [r for r in rows if r[0] <= t and r[1] >= ff]

    cov = Counter()
    gaps = Counter()
    for m in months:
        t, ff = m["t"], m["fresh_from"]
        for _sid, cik in m["members"]:
            g = gap_rows.get(cik, {})
            for n in NAMES:
                fn = fresh(g.get(n, ()), t, ff)
                if fn:
                    cov[n] += 1
                for d in NOT_ALIASED[n]:
                    fd = fresh(g.get(f"alt|{n}|{d}", ()), t, ff)
                    if not fd:
                        continue
                    if not fn:
                        gaps[(n, d, "lacks")] += 1
                    elif (n, d) in DIFFERS:
                        latest = max(r[1] for r in fd)
                        known = {(r[1], r[2]): r[3] for r in g.get(n, ()) if r[0] <= t}
                        if any(known.get((r[1], r[2])) != r[3] for r in fd if r[1] == latest):
                            gaps[(n, d, "differs")] += 1
            for variant in (
                "only|depreciation|DepreciationDepletionAndAmortization",
                "only|depreciation|DepreciationAndAmortization",
                "only|eps_basic|EarningsPerShareBasic",
                "only|eps_diluted|EarningsPerShareDiluted",
            ):
                if fresh(g.get(variant, ()), t, ff):
                    cov[variant] += 1
            # DA against the DDA-only list
            if not fresh(
                g.get("only|depreciation|DepreciationDepletionAndAmortization", ()), t, ff
            ) and fresh(g.get("only|depreciation|DepreciationAndAmortization", ()), t, ff):
                gaps[("depreciation(DDA only)", "DepreciationAndAmortization", "lacks")] += 1
    print("coverage per name (share of member-months with a row by the #660 bound):")
    for n in list(NAMES) + [k for k in cov if k.startswith("only|")]:
        print(f"  {n} {cov[n]} {100 * cov[n] / denom:.1f}%")
    print("gaps:")
    for (n, d, kind), c in sorted(gaps.items()):
        print(
            f"  {n} vs {d} [{kind}] {c} {100 * c / denom:.2f}% {'ABOVE' if c > denom * 0.005 else 'residue'}"
        )
    for n, ds in NOT_ALIASED.items():
        for d in ds:
            tot = gaps[(n, d, "lacks")] + gaps[(n, d, "differs")]
            if (n, d) in DIFFERS:
                print(
                    f"  {n} vs {d} [lacks+differs] {tot} {100 * tot / denom:.2f}% {'ABOVE' if tot > denom * 0.005 else 'residue'}"
                )

    # ---- T368 budget ----
    print("\n== T368 budget (every issuer CIK) ==")
    iss = [r for r in results if "error" not in r]
    in_zip = [r for r in iss if r.get("in_zip")]
    with_cover = [r for r in iss if r.get("has_cover")]
    absent_cover = [r for r in iss if r.get("has_cover") and not r.get("in_zip")]
    trailing = [
        r for r in in_zip if r.get("has_cover") and not r.get("empty") and not r.get("holds_latest")
    ]
    empty_cover = [r for r in in_zip if r.get("has_cover") and r.get("empty")]
    parsed = [r for r in iss if "t_parse" in r]
    rows = Counter()
    restated = Counter()
    for r in parsed:
        rows.update(r.get("new_rows", {}))
        restated.update(r.get("new_restated", {}))
    total_rows = sum(rows.values())
    cache_bytes = sum(r["cache_bytes"] for r in parsed)
    t_load = [r["t_load"] for r in parsed]
    t_parse = [r["t_parse"] for r in parsed]
    print(
        "issuer CIKs",
        len(iss),
        "in zip",
        len(in_zip),
        "with a cover-form stamp",
        len(with_cover),
        "parsed",
        len(parsed),
    )
    print(
        "API requests: absent from zip with a cover stamp",
        len(absent_cover),
        "; member trails latest stamp",
        len(trailing),
        "; empty member with cover stamp",
        len(empty_cover),
    )
    print(
        "projected rows",
        total_rows,
        "store bytes",
        total_rows * STORE_BYTES_PER_ROW,
        "cache bytes",
        cache_bytes,
    )
    print("rows per name", dict(rows))
    print("restated per name (all issuers)", dict(restated))
    print(
        "member load s: total",
        round(sum(t_load)),
        "p50",
        round(statistics.median(t_load), 4),
        "max",
        round(max(t_load), 2),
    )
    print(
        "parse s: total",
        round(sum(t_parse)),
        "p50",
        round(statistics.median(t_parse), 4),
        "p99",
        round(sorted(t_parse)[int(0.99 * len(t_parse))], 3),
        "max",
        round(max(t_parse), 2),
    )
    print("non_unit pairs", agg.get("non_unit", "n/a"))
    print("conflicts per new name", dict(agg.get("new_conflicts", {})))


if __name__ == "__main__":
    main()
```

</details>
