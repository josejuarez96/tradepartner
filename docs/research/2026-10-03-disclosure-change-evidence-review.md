# Research Report: Disclosure-change and cross-document text measurement, evidence review

**Brief:** #618  ·  **Date:** 2026-10-03  ·  **Status:** COMPLETE. All 9 topics are covered. The egress proxy blocked full-text access (Wiley, NBER, SSRN relays, arXiv, RePEc, Harvard DASH), so most figures come from abstracts or search-engine summaries. Each one is flagged; see UNVERIFIED.  ·  **Agent/model:** research agent (`researcher`), reviewed and edited by the main session

**Budget.** The cap was 70 web searches and fetches; 68 were used: 55 searches and 13 fetches. Of the fetches, 3 read the 2026-10-03 spike inputs from the public branch, and 10 were blocked by the proxy. The coordinator later supplied local copies of the inputs, which were read in full, including the integration note and the QI brief list.

**Companion artifacts (#618):** [taxonomy v0](2026-10-03-semantic-taxonomy-v0.md) · [annotation protocol](2026-10-03-disclosure-annotation-protocol.md) · [ML validation brief](2026-10-03-ml-validation-methodology-brief.md) · [prospective feature spec](2026-10-03-prospective-semantic-feature-spec.md).

## Objective, scope and definitions
- **Objective:** establish which disclosure-change and cross-document text techniques have credible empirical support, what has been independently replicated, which tasks are open, and which TradePartner hypotheses are novel. This sets the deterministic baselines and the semantic benchmark design. **It does not choose a model.**
- **Scope:** the 9 topics below, plus an audit of the sources behind the 2026-10-03 typed-classifiers note (§ "Audit of the typed-classifiers note's sources").
  - Out of scope: returns of any TradePartner strategy; vendor selection.
- **Definitions:** *published result*, *vendor claim*, *inference*, *recommendation* and *unknown* as in the grading section. *Replicated* means an independent team, on its own data or measure.

## Answer
**Verdict:** a single grade does not apply. Each topic is graded below. Measurement claims and return claims are graded separately.  ·  **Confidence:** medium on what exists and what was replicated; low on exact figures, because full texts were unreachable.

1. **Filing change versus the prior filing (10-K vs 10-K, 10-Q vs 10-Q) is the best-studied object here.** Lazy Prices (Cohen, Malloy and Nguyen, JF 2020) reports that "changers" underperform and that changes predict future earnings, profitability, news and bankruptcies. Adosoglou et al. independently re-find the direction in-sample with embeddings. Padyšák studies a related, category-specific measure and finds the **opposite sign** for positive-language change: firms whose positive language changed most did *better*. So "any change is bad" does not hold component by component.
2. **No Tier 1 or Tier 2 post-publication or net-of-cost test of the return effect was found.** The only post-publication test seen is a Tier 3 GitHub replication that finds nothing on the S&P 100 from 2009 to 2026. Under §6.1 the return claim is therefore **INSUFFICIENT for post-publication, net-of-cost performance**, even though it is well replicated in-sample.
3. **Cross-document comparisons are studied piecewise, not as incompatibility.** On filing versus release, the market reacts little to a 10-Q filed after the earnings release (Li and Ramesh 2009). On prepared remarks versus Q&A, the Q&A carries more information than the presentation (Matsumoto et al. 2011; Price et al. 2012). Language also differs across outlets (Davis and Tama-Sweet 2012). No study was found that labels *incompatibility* between two documents and tests whether it predicts outcomes.
4. **The narrative-versus-fundamentals gap is well studied as "abnormal tone"**: tone not explained by fundamentals predicts lower future earnings and a delayed negative return (Huang, Teoh and Zhang 2014). A typed, per-dimension gap against XBRL fundamentals was not found.
5. **The measurement tooling is uneven.**
   - Domain encoders beat dictionaries at *sentence sentiment classification* (Huang, Wang and Yang, CAR 2023).
   - Finance-specific embeddings beat general ones on FinMTEB.
   - Financial NLI is hard: the best macro-F1 on FinNLI is 74.6% for fine-tuned encoders and 78.6% for LLMs, and domain shift degrades general NLI models.
   - No source shows that semantic change measures beat bag-of-words change measures *for prediction* in a head-to-head test.
6. **LLM disclosure studies report information gains**: Kim, Muhn and Nikolaev; de Kok; Jha et al.; Lopez-Lira and Tang. All of them run inside or near the models' training windows, and Glasserman and Lin and Sarkar and Vafa show that this inflates or distorts results. For typed decision models (Jev), the only finance figures come from one unreplicated preprint (PhraseBank 73.0%, ECE 0.138). A separate preprint shows that a constrained head's decisions follow the *option name*, not the bound rubric: 32.5% of decisions flipped against a 1.33% test-retest floor.
7. **Novelty for TradePartner** (see the Novelty map):
   - Typed direction labels (demand, inventory, liquidity) beyond deterministic change look **apparently novel**.
   - So do NLI-style cross-document incompatibility labels and the semantic change × insider purchase interaction.
   - Hypotheses (a) and (e) are well studied, and (b) is partly studied.

## Grading: how §6.1 is adapted
- **Return claims** (a text measure predicts returns) use §6.1 unchanged. **SUPPORTED** needs 3 or more independent Tier 1/2 sources (at least 2 Tier 1), at least 1 out-of-sample or post-publication test, and at least 1 test net of costs. **MIXED** means the evidence conflicts or the effect is confined to microcaps. **NOT SUPPORTED** means credible replications show the effect is gone. **INSUFFICIENT** means fewer than 2 qualifying sources, *or* no qualifying post-publication or net-of-cost evidence.
- **Measurement-validity claims** (a measure captures what it claims, or predicts fundamentals or reactions) keep §6.1's source-count rule and drop the OOS and costs tests, as in [2026-10-02-edgar-data-pitfalls.md](2026-10-02-edgar-data-pitfalls.md). SUPPORTED needs 3 or more independent sources, at least 2 of them Tier 1. MIXED means the sources conflict. INSUFFICIENT means fewer than 2.
- **Benchmark numbers** (FinNLI, FinMTEB, FAB and others) describe how hard a task is in that benchmark's domain. They are never predictions of TradePartner performance.
- **Category column:** *published result* (peer-reviewed or working paper); *vendor claim*; *inference* (this agent's reasoning, never evidence); *recommendation*; *unknown*.
- **Replicated?** counts only an independent team re-finding the result on its own data or measure. A summary of the original paper does not count.

## Topic 1. 10-Q vs prior 10-Q; 10-K vs prior 10-K

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| A short-"changers", long-"non-changers" portfolio earns large abnormal returns | S1 Cohen, Malloy, Nguyen, JF 75(3):1371–1415, 2020 (NBER w25084, 2018) | 1 | published result | "up to 188 basis points per month (over 22% per year)" (abstract); "no announcement effect" | Partly: S4 (embeddings, 10-K, 1998–2018) re-finds the direction; S3 finds a related effect | No Tier 1/2 post-publication test found; S7 (Tier 3) finds none on the S&P 100, 2009–2026 | Not verified (full text unreachable) |
| Filing changes predict future earnings, profitability, news and bankruptcies | S1 | 1 | published result | Abstract: changes "predict future earnings, profitability, future news announcements, and even future firm-level bankruptcies" | No independent replication found | No | n/a |
| Changes in language about the CEO/CFO team and litigation are the most informative | S1 | 1 | published result | Abstract summary (search snippet) | No | No | n/a |
| Firms that do not change their 10-K "in a semantically important way" earn positive abnormal returns | S4 Adosoglou, Lombardo, Pardalos, *Expert Systems with Applications* 164 (2021) 114053 | 1 | published result | Doc2Vec PV-DM beats PV-DBOW and averaged Word2Vec; alpha figures **UNVERIFIED** | Itself a partial replication of S1 with another measure; overlapping sample | No (1998–2018) | Not found |
| Low *positive-word* similarity stocks outperform high positive-word similarity stocks | S3 Padyšák, SSRN 2020 | 1 (content read via a Tier 3 relay) | published result | "0.41% monthly" decile spread, 1-month hold (relay figure, **UNVERIFIED**) | Variant of S1; the **sign is opposite** for positive language | Unknown | Unknown |
| Larger economic changes come with larger MD&A modifications; the 10-K price reaction rises with the modification score; analysts ignore it; usefulness has declined | S2 Brown & Tucker, JAR 49(2):309–346, 2011 | 1 | published result | Modification scores "declined in the past decade"; the price reaction "has also weakened" | No independent replication found | Within-sample decline only | n/a |
| Changes to 10-Ks move option volatility smirks (options traders are attentive), "contrasting with" Lazy Prices | S43 Cheng, Liu, Qiao, Wang, "Attentive Options Traders" (JFQA PDF) | 1 (venue status **UNVERIFIED**) | published result | Snippet only | n/a | n/a | n/a |
| A practitioner study of the 5 sections shared by 10-K and 10-Q filings | S6 S&P Global Market Intelligence, "US Filings: No News is Good News" | 2 | unknown (content not read) | Not read (blocked) | n/a | **UNVERIFIED** | **UNVERIFIED** |
| Large-cap vs small-cap subsample | S1, S4 | 1 | unknown | Not found in readable material. One relay says a commercial similarity dataset covers about the largest 1,000 US stocks (Tier 3) | n/a | n/a | n/a |

**Grades.**
- **Return claim** (changers underperform): in-sample, 2 Tier 1 sources plus a variant. Post-publication: none at Tier 1/2. Costs: none verified. Grade: **INSUFFICIENT** for post-publication, net-of-cost performance. The in-sample direction is consistent across S1 and S4.
- **Measurement claim** (change predicts future fundamentals): only S1, with S2 contemporaneous. Grade: **INSUFFICIENT**, since one Tier 1 source is predictive.
- **Measurement claim** (change tracks economic change): S1 and S2. Grade: **INSUFFICIENT** (2 sources, not 3).

## Topic 2. Filing vs earnings release

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| Little or no market reaction to a 10-Q filed after the earnings release; a reaction only when filing and release coincide, or for some 10-Ks | S8 Li & Ramesh, *The Accounting Review* 2009 (volume and pages **UNVERIFIED**) | 1 | published result | "22.7% of interim and 16.4% of annual SEC filing dates coincide with the first release of earnings"; no significant reaction to 10-Q, 10-KSB and 10-QSB filed after the release | No | n/a | n/a |
| Excess return is greater on and just after 10-K and 10-Q filing days, more so for 10-Ks | S9 Griffin, *Review of Accounting Studies* 8(4):433–460, 2003 | 1 | published result | 1996–2001 filings; the response increased over the period | Conflicts with S8 on 10-Qs (different period and design) | n/a | n/a |
| Managers use different language in the release than in the MD&A | S10 Davis & Tama-Sweet, *Contemporary Accounting Research* 29(3):804–837, 2012 | 1 | published result | Direction and outcome results **UNVERIFIED** (abstract not retrieved); there is a published discussion by Mayew (CAR 29(3):838–844) | No | n/a | n/a |

**Grade:** the claim that the 10-Q adds information after the release is **MIXED** (S8 against S9). No study was found that measures disagreement between release and filing and tests whether it predicts outcomes. That is the TradePartner (d) gap.

## Topic 3. Prepared remarks vs Q&A

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| The Q&A ("discussion") is relatively more informative than the presentation; both add to the press release | S11 Matsumoto, Pronk, Roelofsen, TAR 86(4):1383–1414, 2011 | 1 | published result | The effect rises with analyst following and is larger when performance is poor | Consistent with S15 | n/a | n/a |
| Q&A tone adds explanatory power for PEAD | S15 Price, Doran, Peterson, Bliss, *Journal of Banking & Finance* 36(4):992–1011, 2012 | 1 | published result | Concentrated in non-dividend payers; a finance dictionary beats Harvard IV-4 | Consistent with S11 | No | No |
| Linguistic cues in calls flag deceptive discussions out of sample | S12 Larcker & Zakolyukina, JAR 50(2):495–540, 2012 | 1 | published result | Out-of-sample "better than a random guess by 6–16%" (snippet; another relay says 4–6%; **UNVERIFIED**) | No | Holdout within sample | n/a |
| Managers' vocal affect during analyst scrutiny predicts the firm's future; analysts underuse negative affect | S13 Mayew & Venkatachalam, JF 67(1):1–43, 2012 | 1 | published result | About 700 firms (Duke summary, Tier 2) | No | No | n/a |
| Complexity splits into "information" and "obfuscation"; the presentation is more complex than the response | S14 Bushee, Gow, Taylor, JAR 56(1):85–121, 2018 | 1 | published result | Information component negatively, and obfuscation component positively, related to information asymmetry | No | n/a | n/a |

**Grade** (narrowed by the lead session on review):
- **Call Q&A carries incremental information** (beyond the release and the presentation): **SUPPORTED** (S11, S15, S13; all Tier 1).
- **Q&A carries *more* information than prepared remarks:** only S11 compares the two directly, so this is **INSUFFICIENT** under the source count. S15 and S13 study the Q&A without that comparison.

No source measured *divergence* between prepared remarks and Q&A as a feature.

## Topic 4. Narrative vs fundamentals

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| Abnormal positive tone (the residual after fundamentals) predicts lower future earnings and cash flows, positive announcement returns, and a delayed negative reaction | S16 Huang, Teoh, Zhang, TAR 89(3):1083–1113, 2014 | 1 | published result | ABTONE "predicts negative future earnings and cash flows"; delayed negative reaction "in the one and two quarters afterward" | Not independently replicated in this search; later work applies ABTONE to other text (Tier 3 theses seen) | No | No |
| Tone of MD&A forward-looking statements, from a naive Bayes model, predicts future earnings; general dictionaries do not | S17 Li, JAR 2010 (volume **UNVERIFIED**) | 1 | published result | "Diction, General Inquirer, and LIWC … do not positively predict future performance" | Partly: S21 also shows general dictionaries misfire | No | n/a |
| Inconsistency between MD&A tone and performance relates to future performance and analyst errors | Unnamed studies in search results | unknown | unknown | Venue and authors **UNVERIFIED** | n/a | n/a | n/a |

**Grade:** the measurement claim that tone beyond fundamentals predicts future fundamentals is **INSUFFICIENT** under a strict count. There are 2 Tier 1 sources (S16, S17), and S17 measures tone, not a gap. The return claim is **INSUFFICIENT**: one in-sample source, no costs.

## Topic 5. Semantic change measures

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| Embedding-based year-on-year 10-K change predicts abnormal returns, like Lazy Prices | S4 | 1 | published result | See Topic 1 | Partial (of S1) | No | No |
| An embedding-change network ("Lazy Network") earns monthly 5-factor alpha | S5 Adosoglou et al., "Lazy Network" (journal *Complexity*, **UNVERIFIED**) | 1 | published result | "up to 95 basis points in monthly five-factor alphas" (snippet) | Same authors as S4: not independent | No | No |
| A supervised text-to-return model (SESTM) beats dictionary sentiment for return prediction | S19 Ke, Kelly, Xiu, NBER w26186, 2019 | 1 | published result | News articles (Dow Jones Newswires), not filing change | No | Has OOS design (details **UNVERIFIED**) | **UNVERIFIED** |
| On granular tasks, contextual embeddings are often beaten by TF-IDF | S41 "The Devil is in the Details", COLING 2020 | 1 | published result (not finance) | "Contextual embeddings are consistently outperformed by simple baselines like TF-IDF for more granular tasks" (snippet) | n/a | n/a | n/a |

**Grade:** the claim that semantic change beats bag-of-words change for prediction is **INSUFFICIENT**. S4 reports a comparison, but its figures are unverified, the authors are not independent of S5, and there is no post-publication test. Disconfirming context (S41) is general-domain. *Inference, not evidence:* the "robust → resilient → healthy" narrowing in the neglected-equities note is exactly the case where lexical overlap stays high. No study tests that case.

## Topic 6. Contradiction / incompatibility detection; financial NLI

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| Financial NLI is hard; domain shift degrades general NLI models | S28 Magomere et al., "FinNLI", Findings of NAACL 2025 (arXiv 2504.16188) | 1 | published result | 21,304 pairs; expert test set of 3,304; best macro-F1 **74.57%** (fine-tuned encoders) and **78.62%** (LLMs) | No | n/a | n/a |
| NLI labels can be predicted from the hypothesis alone: annotation artifacts | S29 Gururangan et al., NAACL 2018 | 1 | published result | Hypothesis-only classifier correct on "about 67% of SNLI" | Widely cited; general domain | n/a | n/a |
| LLMs plus embedding clustering can surface inconsistencies within financial reports | S30 Lamarr Institute / Fraunhofer, "Uncovering Inconsistencies and Contradictions in Financial Reports using Large Language Models" | 1 (venue **UNVERIFIED**) | published result | Zero-shot, 3 datasets; figures not retrieved | No | n/a | n/a |
| A POS-aware transformer reaches 89.55% F1 on "financial contradiction detection" | search summary, source unidentified | unknown | unknown | **UNVERIFIED** | n/a | n/a | n/a |
| Contradictions detected between filing and call predict outcomes | (none found) | — | unknown | No credible evidence found | — | — | — |

**Grade:** the measurement claim that current models label financial entailment and contradiction reliably is **INSUFFICIENT** (one dataset, S28, with best macro-F1 below 80%). The 5-way relation set (entailed, compatible, tension, incompatible, insufficient_evidence) has no published financial dataset; FinNLI is 3-way.

## Topic 7. Document similarity methods in finance

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| Cosine similarity of 10-K product descriptions defines text-based industries (TNIC) that beat SIC/NAICS on competitor identification | S20 Hoberg & Phillips, JPE 124(5):1423–1465, 2016 | 1 | published result | "50,673 firm 10-K statements"; the data library extends to 2021 | Widely used (method precedent) | n/a | n/a |
| General dictionaries misclassify finance words; the LM lists fix this | S21 Loughran & McDonald, JF 2011 | 1 | published result | "almost three-fourths of the words identified as negative by the widely used Harvard Dictionary are words typically not considered negative in financial contexts" | Yes in effect: S17 and S15 find general dictionaries underperform | n/a | n/a |
| Textual results are sensitive to preprocessing and researcher choices | S22 Loughran & McDonald, JAR 54(4):1187–1230, 2016 | 1 | published result | Discusses subjectivity in stemming and stop-word choices (snippet) | n/a | n/a | n/a |
| Lazy Prices used cosine, Jaccard, minimum-edit and simple similarity | S1 | 1 | **UNVERIFIED** (from memory; full text unreachable) | — | — | — | — |

**Grade:** the measurement claim that finance-specific word lists beat general ones is **SUPPORTED** (S21, S17, S15; all Tier 1). The claim that results are robust to the choice of similarity metric is **INSUFFICIENT**: the only claim found is S22's warning that they may not be.

## Topic 8. Domain-specific encoders and embeddings

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| FinBERT (Araci) improves on the prior state of the art for financial sentiment | S23 Araci, arXiv 1908.10063, 2019 (master's thesis) | 1 | published result | Financial PhraseBank and FiQA; exact figures **UNVERIFIED** | Superseded by S24 and others | n/a | n/a |
| FinBERT (Huang, Wang, Yang) beats the LM dictionary, NB, SVM, RF, CNN and LSTM on analyst-report sentence sentiment, most of all with small training sets | S24 Huang, Wang, Yang, CAR 40(2), 2023 | 1 | published result | Accuracy figures **UNVERIFIED** | Not independently re-tested in this search | n/a | n/a |
| A filings-pretrained BERT with numeric shape tokens is best on XBRL tagging | S25 Loukas et al., "FiNER", ACL 2022 (arXiv 2203.06482) | 1 | published result | FiNER-139: 1.1M sentences, 139 tags; sec-bert-shape **82.1 micro-F1** | No | n/a | n/a |
| A corpus of 10-Ks (1993–2020) split into items; EDGAR-W2V embeddings | S26 Loukas et al., EDGAR-CORPUS (arXiv 2109.14394; venue **UNVERIFIED**) | 1 | published result | Annual reports only (no 10-Q) | n/a | n/a | n/a |
| Finance-adapted embeddings beat general ones; general benchmark rank predicts finance performance poorly | S27 Tang & Yang?, FinMTEB, EMNLP 2025 (arXiv 2502.10990; authors **UNVERIFIED**) | 1 | published result | 64 datasets, 7 tasks; Fin-E5 ranks first | No | n/a | n/a |
| News sentiment from a larger LLM (OPT) predicts next-day returns better than FinBERT and LM | S42 arXiv 2412.19245 "Sentiment trading with large language models" | 1 | published result | 965,375 news articles, 2010–2023; OPT "74.4%" accuracy (snippet) | No | Unclear | Unclear |

**Grade:**
- That domain encoders beat dictionaries at *classifying sentence sentiment*: **INSUFFICIENT** on the strict count (S24 and S23, plus S42 on a different task). The direction is consistent.
- That this advantage carries over to *predicting returns or fundamentals from filing change*: **INSUFFICIENT**. No source found.

## Topic 9. LLM-based disclosure analysis

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| GPT-4 on anonymized statements predicts the direction of earnings changes better than analysts | S31 Kim, Muhn, Nikolaev, "Financial Statement Analysis with LLMs" (arXiv 2407.17866) | 1 | published result | "60.35%" accuracy, about 7 percentage points above analysts (snippet); FF3 alpha ">12% per year" from a Tier 3 relay (**UNVERIFIED**) | No independent replication found | Critics note only 2023 is post-cutoff (S47, Tier 3: a pointer, not evidence) | **UNVERIFIED** |
| LLM summaries of disclosures are over 70% shorter and explain market reactions better; "bloat" goes with lower price efficiency | S32 Kim, Muhn, Nikolaev, "Bloated Disclosures" (arXiv 2306.10224) | 1 | published result | "often by more than 70%" | No | No | n/a |
| A careful LLM workflow detects non-answers in calls more accurately than prior methods | S33 de Kok, *Management Science* 71(9):7888–7906, 2025 | 1 | published result | "96% accuracy", error rate "reduced by 70%" | No | n/a | n/a |
| ChatGPT headline scores predict next-day returns, more for small stocks and bad news; strategy returns fall as LLM adoption rises | S34 Lopez-Lira & Tang (arXiv 2304.07619, v6 2025) | 1 | published result | Decline "as LLM adoption rises" | Partly: S42 is related | Partly (the decline is itself a post-adoption finding) | **UNVERIFIED** |
| A ChatGPT "investment score" from calls predicts capex for up to 9 quarters; high-score firms earn negative abnormal returns | S37 Jha, Qian, Weber, Yang, NBER w32161, 2024 | 1 | published result | "up to nine quarters"; validated against CFO survey responses | No | No | No |
| In-sample LLM sentiment is distorted more by *distraction* (company knowledge) than by look-ahead; anonymizing helps, more for large firms | S35 Glasserman & Lin (arXiv 2309.17322, 2023) | 1 | published result | Anonymized headlines outperformed in-sample | n/a | n/a | n/a |
| Pretrained models leak the future: Llama 2 predicts COVID-19 risks from 2019 calls | S36 Sarkar & Vafa, "Lookahead Bias in Pretrained Language Models" (venue **UNVERIFIED**) | 1 | published result | "over 25%" versus "6.8%" in two relays (**UNVERIFIED**) | Consistent with the handoff §8.4 sources | n/a | n/a |
| Cross-document and longitudinal SEC tasks are harder for LLMs | S38 Jiang et al., Fin-RATE (arXiv 2602.07294, 2026) | 1 | published result | Three pathways (detail, cross-entity, longitudinal); lead session (abstract-level): accuracy drops of "−18.60%" on longitudinal tasks and "−14.35%" on cross-entity tasks | No | n/a | n/a |
| Expert SEC-analysis questions remain hard | S39 BenYoash et al., SECQUE, GEM workshop 2025 (arXiv 2504.04596) | 1 | published result | 565 questions | n/a | n/a | n/a |
| The best agent answers under half of analyst tasks | S40 Bigeard et al., Finance Agent Benchmark (arXiv 2508.00828) | 1 | published result | o3 "46.8%" at "$3.79 per query"; 537 tasks. The typed-classifiers note's "51.4% naive accuracy" was **not found** by the lead session (**UNVERIFIED**) | n/a | n/a | n/a |
| LLM KPI extraction from earnings calls reaches 79.7% human-rated precision | S53 Aavang et al. (2026), ACL Industry Track | 1 | published result | "79.7%" precision (confirmed by the lead session) | No | n/a | n/a |
| Text-based earnings surprise drifts when classic PEAD is about zero | S52 Meursault, Liang, Routledge, Scanlon, "PEAD.txt", JFQA 58(6), 2023 | 1 | published result | Text-based drift "considerable" when classic PEAD ≈ 0 (lead session; figures not read here) | No | Post-2000s sample (details **UNVERIFIED**) | **UNVERIFIED** |

### Topic 9b. Typed decision models ("Jev") and LLM disclosure classification
The coordinator supplied these findings from the lead session. They were checked against search abstracts only, because arXiv is blocked here. They are all very recent preprints (September 2026) and have not been replicated. Vendor statements stay vendor claims.

| Claim | Source | Tier | Category | Key figure | Replicated? | Post-pub / OOS? | Net of costs? |
|---|---|---|---|---|---|---|---|
| A typed decision model (jev-1.13.0), zero-shot, beats an open-weight Qwen baseline on most of 37 datasets at very low cost | S48 Deußer, Sparrenberg, Sifa, arXiv 2609.37647 (Sept 2026) | 1 | published result | 37 datasets, 346,009 requests, "<USD 10"; beats Qwen on 27/37 (abstract) | **No**. A Tier 3 evaluation reports 85.3% on PhraseBank (999 samples) against S48's 73.0%: the result depends on subset and template | n/a | n/a |
| Jev is miscalibrated on finance sentiment | S48 | 1 | published result | Financial PhraseBank accuracy 73.0%, ECE 0.138 (search extracts, not page-verified); ECE ≤ 0.03 on 11 of 22 datasets; miscalibration is mostly overconfidence | No | n/a | n/a |
| A typed readout has no independent accuracy advantage over label-probability readouts; Jev's clearest gains are latency and cost; it falls behind on harder tasks | S49 Tang & Zheng, "Typed Decision Models: An Early Evidence Audit and Evaluation Checklist", arXiv 2609.32160 | 1 | published result (review of 28 papers posted 19–24 Sept 2026) | 14-item evaluation checklist | n/a (a review) | n/a | n/a |
| A constrained decision head follows the **option name**, not the rubric bound to it | S50 "Type-Safe Is Not Error-Free…", arXiv 2609.26758 (23 Sept 2026) | 1 | published result | Permuting name-to-rubric binding flipped **32.5%** of yes/no decisions on 1,200 questions, against a **1.33%** test-retest floor; type-error rate stayed **0%** | No | n/a | n/a |
| Vendor-stated rate limits and a cookbook result (39/60; 27/30 confident subset) | Typed-classifiers note refs [3], [8] | 3 | vendor claim | Cookbook figures **not found** by the lead session; the stated rate limits are contradicted by snippets (250k tok/s; 1,200 req/min) | n/a | n/a | n/a |

**Grade:** typed decision models classify financial disclosure change accurately and in a calibrated way: **INSUFFICIENT**. There is one sentence-sentiment evaluation (S48) whose figures vary by subset, and none on paired disclosure change. S50 is direct evidence that a typed output schema does **not** protect against label-name effects, which matters for taxonomy v0 label names. *Inference, not evidence:* label wording such as "tension" and "incompatible" could move decisions independently of their definitions. A name-permutation test like S50's would detect that.

**Grade:**
- LLMs extract economically meaningful measures from disclosures (measurement): **SUPPORTED** in-sample (S33, S37, S32; all Tier 1). Every study used models whose training windows overlap the sample, so this is subject to S35 and S36.
- LLM-derived text signals predict returns after costs, post-cutoff: **INSUFFICIENT** (no qualifying test), in line with handoff H8.

## Audit of the typed-classifiers note's sources

The 2026-10-03 [typed-classifiers research results](2026-10-03-typed-classifiers-financial-disclosures-research.md) is the main input that argues for testing Jev, so its 23 references were checked on 2026-10-03:
- A separate research agent worked from search-result snippets: all of its primary-page fetches were blocked by the network proxy.
- The lead session then ran independent searches for the items that agent could not find.

**No reference was read at page level.** Treat every "confirmed" below as abstract- or snippet-level.

| Ref in the note | What it claims | Result of the check |
|---|---|---|
| [1] Deußer, Sparrenberg, Sifa, arXiv 2609.37647 | Independent Jev benchmark; PhraseBank 73.0%, ECE 0.138 | **Exists.** Abstract confirms jev-1.13.0, 37 datasets, 346,009 requests, < USD 10. The PhraseBank figures appear only in search extracts. A Tier 3 evaluation reports 85.3% on a 999-sample PhraseBank subset, so the figure depends on the setup. |
| [2] Tang & Zheng, arXiv 2609.32160 | Evidence audit: no typed-readout accuracy edge; gains in cost and latency | **Exists**; the abstract matches the note's characterization (28 papers posted 19–24 Sept 2026; 14-item checklist) |
| [3] Vendor Models page | $0.042/M input tokens, free output, 100k tok/s, 80 req/s, pinned versions | Price, free output and pinning corroborated by Tier 3 snippets. **Rate limits contradicted** (snippets: 250k tok/s, 1,200 req/min). 64k/32k budgets and no fine-tuning not found. |
| [4] Choice; [6] jaggedness | ≤ 255 options, probabilities + confidence; documented weaknesses | Broadly corroborated (snippets). The choice-order sensitivity claim in [6] was **not found**. |
| [5] Confidence formula | confidence = (p_max − 1/K)/(1 − 1/K) | Confidence ≠ selected probability corroborated; **the formula was not found** |
| [7] Launch post, 15 Sept 2026 | Efficiency ratios "toward the high end" | Corroborated (snippets) |
| [8] SEC/SIC cookbook | 39/60, 27/30, jev-1.12, 12 Aug 2026 | **Not found** |
| [9] Fin-RATE | Cross-document degradation | Confirmed (−18.60% longitudinal, −14.35% cross-entity, abstract level) |
| [10] Finance Agent Benchmark | 46.8% class-balanced vs 51.4% naive for o3 | 46.8% confirmed; **51.4% not found** |
| [11] Lazy Prices | — | Confirmed, JF 75(3):1371–1415 |
| [15] Ng, Zheng, Zheng, DSS 209:114735 | EASE embeddings | **Title, volume and DOI not found**; the same authors have related work |
| [20] SECQUE; [21] Aavang et al. | 565 questions; 79.7% precision | Confirmed |

Also found during the check and **not cited by the note**: S50 (option-name effect, Topic 9b). Tier 3 commentary noted that the vendor's launch benchmark scored against model-derived references rather than human labels, which the note itself acknowledges in its §6.

**Conclusion (inference):** the note's two academic Jev sources are real, and its central caution (promising but unproven for finance) stands. Its specific vendor numbers [3], [5], [8] and its 51.4% figure should not be reused until read at page level.

## Replication ledger

| Result | Original | Independent replication | Status |
|---|---|---|---|
| Changers underperform (10-K/10-Q similarity) | S1 | S4 (embeddings, 10-K, 1998–2018); S3 (positive-word variant, **opposite sign** for that subset) | Replicated in direction, in-sample; **no Tier 1/2 post-publication replication**; Tier 3 S7 null on the S&P 100 |
| Change predicts future earnings and bankruptcies | S1 | None found | Reported once |
| MD&A modification tracks economic change; usefulness declining | S2 | None found | Reported once |
| Little reaction to a 10-Q filed after the release | S8 | S9 partly conflicts | Conflicting |
| Q&A more informative than the presentation | S11 | S15 (tone, PEAD), S13 (vocal affect in Q&A) | Replicated in spirit |
| Abnormal tone predicts negative future earnings and a delayed price reversal | S16 | None verified | Reported once (here) |
| General dictionaries misfire on finance text | S21 | S17, S15 | Replicated |
| Domain encoder beats dictionary at sentence sentiment | S23, S24 | S42 (different task) | Partly |
| Hypothesis-only NLI artifacts | S29 | Widely reproduced (general domain; not checked here) | General-domain replicated; untested on financial NLI |
| LLM beats analysts at earnings direction | S31 | None | Reported once |
| LLM look-ahead and distraction bias | S35, S36 | Handoff §8.4 sources (Lopez-Lira, Tang and Zhu; MemGuard-Alpha; NumLeak) | Replicated as a phenomenon |
| Jev PhraseBank accuracy and calibration | S48 (73.0%, ECE 0.138) | Tier 3 evaluation: 85.3% on 999 samples | **Not replicated**; the figure depends on subset and template |
| Typed head follows the option name, not the rubric | S50 | None | Reported once |
| Typed readout has no accuracy edge over label probabilities | S49 (a review of 28 preprints) | n/a | Review-level, preprints only |
| Text-based surprise drift where classic PEAD ≈ 0 | S52 | None found | Reported once |

## Open tasks (unstudied or unsolved, as far as this search found)
1. A **post-2018, Tier 1/2, net-of-cost** test of Lazy Prices, split by size: large caps (a top-1,000 universe) versus small caps.
2. **10-Q-specific** change against **next-quarter** fundamentals (revenue deceleration, inventory build, liquidity draw), as opposed to annual earnings or returns.
3. **Typed directional** change labels (demand, inventory pressure, liquidity pressure) and their incremental value over aggregate similarity. Padyšák's sign flip for positive language (S3) hints that direction matters. That is an inference, not a test.
4. A **head-to-head** of semantic versus bag-of-words change measures on the same sample, with post-publication data.
5. A financial NLI dataset with a **graded relation set** (tension, compatible, insufficient_evidence) and **paired documents** (release vs 10-Q, prepared vs Q&A). FinNLI is single-pair and 3-way.
6. **Annotation-artifact checks** (a hypothesis-only baseline) for financial NLI.
7. Whether **cross-document incompatibility** predicts fundamentals or returns.
8. **Semantic change × insider purchase** interactions.
9. Post-cutoff (prospective) evaluation of any LLM-derived disclosure measure.

## Novelty map (TradePartner hypotheses)

| # | Hypothesis | Status | Citations | What a TradePartner test would add (one line) |
|---|---|---|---|---|
| a | Deterministic 10-Q change predicts returns | **Well studied** (in-sample) | S1, S3, S4, S2; S7 (Tier 3 null) | A post-publication, net-of-cost test inside the top-1,000 universe: the missing Tier 1/2 evidence class |
| b | Deterministic change predicts next-quarter fundamentals | **Partly studied** | S1 (future earnings and bankruptcy), S2 (contemporaneous), S17 (tone → earnings), S16 | Quarterly horizon and typed fundamentals (revenue, inventory, liquidity from XBRL) rather than annual earnings |
| c | Typed semantic direction labels add information beyond deterministic change | **Apparently novel** for paired-filing direction labels; adjacent work exists | S37 (typed capex score from calls), S3 (category-specific change has the opposite sign), S52 (text adds drift beyond numeric PEAD), S19, S33; measurement risks: S48 (unreplicated, miscalibrated), S49, S50 (label-name effects) | The first test of incremental value of labelled direction over a similarity baseline, on a sealed human-labelled set, with a label-name permutation control |
| d | Cross-document incompatibility (filing vs release; prepared vs Q&A) predicts outcomes | **Apparently novel** as incompatibility; components partly studied | S8, S9, S10, S11, S15, S12, S28, S30 | The outcome link, plus a 5-way relation benchmark; the tooling is immature (S28 macro-F1 below 80%) |
| e | Narrative-fundamental gap | **Well studied** as abnormal tone; **partly** as a typed per-dimension gap | S16, S17, S10 | A per-dimension gap (demand narrative vs revenue; inventory narrative vs inventory days) instead of one tone residual |
| f | Semantic change × insider purchase | **Apparently novel** (no study found) | Nearest: S44 (insider trading around 10-K/10-Q filings); a readability × insider-profit study (venue **UNVERIFIED**); handoff H2 (insider purchases MIXED) | A pre-registered interaction test; the base insider effect is only MIXED, so the interaction is doubly uncertain |

"Apparently novel" means only that no study was found within 68 searches with mostly blocked full texts. It is not proof of absence.

## Disconfirmation
- **Searches run:**
  1. "Lazy Prices replication post-publication decay Padysak"
  2. "Lazy Prices … out-of-sample replication large-cap no longer works"
  3. "Lazy Prices effect weaker after publication … decline evidence" (extended)
  4. Chen-Zimmermann open-source asset pricing and Lazy Prices
  5. "FinBERT does not outperform dictionary … return prediction"
  6. "simple bag-of-words TF-IDF baseline matches or beats BERT embeddings financial"
  7. "Financial Statement Analysis with LLMs critique replication look-ahead"
  8. Glasserman-Lin look-ahead; Sarkar-Vafa lookahead
  9. NLI annotation artifacts
  10. Narrative-numbers inconsistency
  11. Insider × textual change
- **Found against:**
  - **Lazy Prices decay:** a Tier 3 GitHub replication finds no effect on the S&P 100 from 2009 to 2026, with similarity "essentially 1 for every firm". That is as consistent with a measurement problem as with decay. It is a pointer, not evidence.
  - **Within-sample decline:** S2 reports declining MD&A informativeness within its own sample.
  - **Coverage:** Chen-Zimmermann's open-source set could not be confirmed to include the signal, so there is no independent replication there.
  - **Sign:** S3's opposite sign for positive-word change means "change is bad" does not hold for every component.
  - **Embeddings vs TF-IDF:** S41, general domain.
  - **FinBERT vs LLMs:** S42 has a larger LLM beating FinBERT on news-return prediction. This weakens "FinBERT is the finance default", not "dictionaries win".
  - **KMN critique:** the only one found (S47) is Tier 3: just one post-cutoff year.
  - **LLM in-sample results are biased** (S35, S36).
  - **NLI accuracy is overstated** by artifacts (S29).
  - **Filing after release:** S8 finds the 10-Q after the release is largely uninformative to prices.
- **Nothing found:** no Tier 1/2 post-publication Lazy Prices test; no failed replication of S16 or S11; no evidence for or against hypotheses (c), (d) or (f).

## Caveats & gaps
- Full texts were unreachable. Figures come from abstracts or search summaries, and table and page references could not be given. Table-level claims (size splits, value weighting, costs, similarity metric used) are **UNVERIFIED**.
- The S&P Global practitioner study (S6) was identified but not read.
- Search summaries are produced by a model and can misstate a paper. Each figure quoted from one is attributed to its snippet.
- The 2026-10-03 inputs were read for context only. Their own citations were not re-verified here, apart from FAB, Fin-RATE, SECQUE, Araci, Lazy Prices and Loughran-McDonald. This includes the "Jev" and Deusser et al. arXiv 2609.37647 citations in the typed-classifiers note.
- No Tier 1 source was found for a 5-way relation label set; that design choice has no empirical precedent here.

## UNVERIFIED items
- Lazy Prices: similarity metrics, 10-Q vs 10-K split, size and value-weighted results, costs, sections. Some indexes give the year as 2018 (the NBER WP); the JF version is 2020, 75(3).
- S6 S&P Global study content; S3 "0.41% monthly" and its sample; S4 alpha figures; S5 venue and "95 bps".
- S8 volume and pages; S10 results; S12 OOS figure (6–16% vs 4–6%); S17 volume.
- S23 and S24 accuracy figures; S26 venue; S27 authors; S41 authors.
- S30 venue; the 89.55% F1 contradiction model (source unidentified); the MD&A tone-inconsistency studies (Topic 4).
- S31 alpha and Sharpe figures; S36 venue and "25% vs 6.8%"; S43 publication status; S44 venue and year.
- S38, S48–S53: figures taken from lead-session abstract checks, not verified by this agent. S48's 73.0% and ECE 0.138 are not page-verified, even by the lead session.
- Typed-classifiers note: FAB "51.4% naive accuracy" and ref [8] (cookbook 39/60, 27/30) were not found; ref [3] rate limits are contradicted.

## Follow-up questions (not answered here)
- Does the Brain Language Metrics or S&P similarity data reproduce the Lazy Prices effect after 2018 in large caps? (vendor data, Phase 3 budget)
- What inter-annotator agreement is reachable on the 5-way relation labels? This needs a pilot, not literature.
- Does the positive-language sign flip (S3) replicate, and does it map onto the demand-direction label?

## Unresolved questions
The follow-up questions above, plus:
1. What do the full texts say? The blocked items include Lazy Prices' 10-Q vs 10-K split, its size subsamples and its costs, and they are the first to read on an unblocked network.
2. Is Lazy Prices in the Chen-Zimmermann open-source set? If yes, its post-publication returns can be read from there.

## Proposed tests (each lands in the [ML brief](2026-10-03-ml-validation-methodology-brief.md))
- **(a)** A post-publication, net-of-cost Lazy Prices-style test inside the ADR 0006 universe, using deterministic features only. Its prior is set at or below the McLean-Pontiff haircut.
- **(b)** The E1-style fundamentals regression with deterministic text as the treatment (ML brief §8). This is cheap, needs no model, and gives the semantic arms their bar.
- **(c)** The semantic benchmark with a name–definition swap control (S50), and the incremental test C vs B.
- **(d)** The relation-label pilot (protocol §9), including a hypothesis-only baseline (S29) before any outcome test.

## Acceptance criteria for this review
- Every claim used by a companion artifact appears in a table here with its category and its verification level.
- The owner accepts the novelty map as the basis for prioritizing (c) and (b) over (d) and (f).

## Dependencies
Full-text access for the UNVERIFIED items. The other four #618 artifacts cite this one.

## Risks
| Risk | Mitigation |
|---|---|
| Snippet-level figures repeated as fact in later docs | Figures here are labeled at their verification level; later docs must cite the level |
| "Apparently novel" read as "promising" | Novelty only means untested. Priors for (c), (d) and (f) stay centered near zero. |
| Survivorship of positive papers in search | Disconfirmation searches are listed; failed replications are rare in print, which is why the post-publication test (a) matters |

## Recommended next step
Read the UNVERIFIED Tier 1 full texts that bear on design choices: Lazy Prices' similarity measures and its 10-Q handling, FinNLI's label design, and S50's method. Then run test (b) on deterministic features as soon as a document corpus exists. It needs no ADR change.

## Sources
All accessed 2026-10-03, mostly via search-result abstracts (full texts were blocked).
- S1 Cohen, Malloy, Nguyen, "Lazy Prices", JF 75(3):1371–1415 (2020); NBER w25084: https://www.nber.org/papers/w25084 ; https://onlinelibrary.wiley.com/doi/10.1111/jofi.12885 (Tier 1)
- S2 Brown & Tucker, JAR 49(2):309–346 (2011): https://ideas.repec.org/a/bla/joares/v49y2011i2p309-346.html (Tier 1)
- S3 Padyšák, "The Positive Similarity of Company Filings and the Cross-Section of Stock Returns", SSRN (2020), read via https://quantpedia.com/the-positive-similarity-of-company-filings-and-the-cross-section-of-stock-returns/ (Tier 1 paper; Tier 3 relay)
- S4 Adosoglou, Lombardo, Pardalos, "Neural network embeddings on corporate annual filings for portfolio selection", ESWA 164 (2021) 114053: https://air.unipr.it/handle/11381/2887410 (Tier 1)
- S5 Adosoglou et al., "Lazy Network": https://philpapers.org/rec/ADOLNA ; https://ideas.repec.org/a/hin/complx/9430919.html (Tier 1; venue unverified)
- S6 S&P Global MI, "US Filings: No News is Good News": https://www.spglobal.com/marketintelligence/en/news-insights/research/us-filings-no-news-is-good-news (Tier 2; not read)
- S7 iqueipopg/lazy-prices (S&P 100 replication): https://github.com/iqueipopg/lazy-prices (Tier 3)
- S8 Li & Ramesh, "Market Reaction Surrounding the Filing of Periodic SEC Reports", TAR (2009) (Tier 1; found via search, no stable URL)
- S9 Griffin, RAST 8(4):433–460 (2003): https://www.doi.org/10.1023/A:1027351630866 (Tier 1)
- S10 Davis & Tama-Sweet, CAR 29(3):804–837 (2012); Mayew discussion, CAR 29(3):838–844: https://ideas.repec.org/a/wly/coacre/v29y2012i3p838-844.html (Tier 1)
- S11 Matsumoto, Pronk, Roelofsen, TAR 86(4):1383–1414 (2011): https://academicnewsletter.sufe.edu.cn/info/415945 (Tier 1)
- S12 Larcker & Zakolyukina, JAR 50(2):495–540 (2012): https://ideas.repec.org/a/bla/joares/v50y2012i2p495-540.html (Tier 1)
- S13 Mayew & Venkatachalam, JF 67(1):1–43 (2012): https://scholars.duke.edu/publication/803848 (Tier 1)
- S14 Bushee, Gow, Taylor, JAR 56(1):85–121 (2018): https://ideas.repec.org/a/bla/joares/v56y2018i1p85-121.html (Tier 1)
- S15 Price, Doran, Peterson, Bliss, JBF 36(4):992–1011 (2012): https://ideas.repec.org/a/eee/jbfina/v36y2012i4p992-1011.html (Tier 1)
- S16 Huang, Teoh, Zhang, "Tone Management", TAR 89(3):1083–1113 (2014): https://academicnewsletter.sufe.edu.cn/info/415724 (Tier 1)
- S17 Li, "The Information Content of Forward-Looking Statements in Corporate Filings", JAR (2010): https://academicnewsletter.sufe.edu.cn/info/413371 (Tier 1)
- S18 Campbell et al., RAST 19(1):396–455 (2014): https://ideas.repec.org/a/spr/reaccs/v19y2014i1d10.1007_s11142-013-9258-3.html (Tier 1; context: risk-factor disclosures are informative)
- S19 Ke, Kelly, Xiu, NBER w26186 (2019): https://www.nber.org/papers/w26186 (Tier 1)
- S20 Hoberg & Phillips, JPE 124(5):1423–1465 (2016): https://www.journals.uchicago.edu/doi/10.1086/688176 (Tier 1)
- S21 Loughran & McDonald, "When Is a Liability Not a Liability?", JF (2011): https://academicnewsletter.sufe.edu.cn/info/360401 (Tier 1)
- S22 Loughran & McDonald, JAR 54(4):1187–1230 (2016): https://ideas.repec.org/a/bla/joares/v54y2016i4p1187-1230.html (Tier 1)
- S23 Araci, FinBERT, arXiv 1908.10063 (2019): https://arxiv.org/abs/1908.10063v1 (Tier 1)
- S24 Huang, Wang, Yang, FinBERT, CAR 40(2) (2023): https://academicnewsletter.sufe.edu.cn/info/410757 (Tier 1)
- S25 Loukas et al., FiNER, ACL 2022: https://arxiv.org/abs/2203.06482v2 (Tier 1)
- S26 Loukas et al., EDGAR-CORPUS: https://ar5iv.labs.arxiv.org/html/2109.14394 (Tier 1)
- S27 FinMTEB, EMNLP 2025: https://arxiv.org/abs/2502.10990v2 (Tier 1)
- S28 Magomere et al., FinNLI, Findings of NAACL 2025: https://arxiv.org/abs/2504.16188v1 (Tier 1)
- S29 Gururangan et al., NAACL 2018: https://arxiv.org/pdf/1803.02324 (Tier 1)
- S30 "Uncovering Inconsistencies and Contradictions in Financial Reports using Large Language Models": https://lamarr-institute.org/publication/uncovering-inconsistencies-and-contradictions-in-financial-reports-using-large-language-models/ (Tier 1; venue unverified)
- S31 Kim, Muhn, Nikolaev, "Financial Statement Analysis with Large Language Models": https://arxiv.org/html/2407.17866v2 (Tier 1)
- S32 Kim, Muhn, Nikolaev, "Bloated Disclosures": https://arxiv.org/html/2306.10224v1 (Tier 1)
- S33 de Kok, Management Science 71(9):7888–7906 (2025): https://ideas.repec.org/a/inm/ormnsc/v71y2025i9p7888-7906.html (Tier 1)
- S34 Lopez-Lira & Tang, "Can ChatGPT Forecast Stock Price Movements?": https://arxiv.org/abs/2304.07619v5 (Tier 1)
- S35 Glasserman & Lin (2023): https://arxiv.org/abs/2309.17322 (Tier 1)
- S36 Sarkar & Vafa, "Lookahead Bias in Pretrained Language Models": https://icml.cc/virtual/2025/48685 (Tier 1; venue unverified)
- S37 Jha, Qian, Weber, Yang, "ChatGPT and Corporate Policies", NBER w32161: https://www.nber.org/papers/w32161 (Tier 1)
- S38 Jiang et al., Fin-RATE (2026): https://arxiv.org/abs/2602.07294v1 (Tier 1)
- S39 BenYoash et al., SECQUE, GEM 2025: https://aclanthology.org/2025.gem-1.16/ (Tier 1)
- S40 Bigeard et al., Finance Agent Benchmark: https://arxiv.org/pdf/2508.00828 (Tier 1)
- S41 "The Devil is in the Details: Evaluating Limitations of Transformer-based Methods for Granular Tasks", COLING 2020: https://preview.aclanthology.org/setup/2020.coling-main.326 (Tier 1)
- S42 "Sentiment trading with large language models": https://arxiv.org/pdf/2412.19245 (Tier 1)
- S43 Cheng, Liu, Qiao, Wang, "Attentive Options Traders: Textual Changes to 10-Ks and Option Volatility Smirk": https://jfqa.org/wp-content/uploads/2026/03/24613_Attentive_Options_Traders.pdf (Tier 1; status unverified)
- S44 "Jeopardy? Non-public information and insider trading around SEC 10-K and 10-Q filings": https://pure.psu.edu/en/publications/jeopardy-non-public-information-and-insider-trading-around-sec-10/ (Tier 1; venue unverified)
- S48 Deußer, Sparrenberg, Sifa, "Evaluating and Benchmarking the System One Model Jev", arXiv 2609.37647 (Tier 1 preprint; confirmed by the lead session from the abstract; not fetched here)
- S49 Tang & Zheng, "Typed Decision Models: An Early Evidence Audit and Evaluation Checklist", arXiv 2609.32160 (Tier 1 preprint; lead session, abstract only)
- S50 "Type-Safe Is Not Error-Free: A Constrained Decision Head Follows the Option Name, Not the Rubric Bound to It", arXiv 2609.26758 (Tier 1 preprint; lead session, abstract only)
- S52 Meursault, Liang, Routledge, Scanlon, "PEAD.txt: Post-Earnings-Announcement Drift Using Text", JFQA 58(6) (2023) (Tier 1; lead session)
- S53 Aavang et al., "Effective Performance Measurement: Challenges and Opportunities in KPI Extraction from Earnings Calls", ACL Industry Track (2026) (Tier 1; lead session)
- S47 Critique of S31 (post-cutoff sample): https://aiandfinance.substack.com/p/financial-statement-analysis-with-large-language-models (Tier 3; pointer only)
- Internal: [handoff §6.1 and §8.4](2026-09-24-initial-research-handoff.md); [ADR 0008](../decisions/0008-llm-role.md); the 2026-10-03 inputs in this folder: [typed-classifiers research results](2026-10-03-typed-classifiers-financial-disclosures-research.md), [neglected-equities note](2026-10-03-ml-llm-neglected-equities-research.md), [claims pilot](2026-10-03-claims-pilot.md), [integration note](2026-10-03-tradepartner-ml-research-integration.md)
