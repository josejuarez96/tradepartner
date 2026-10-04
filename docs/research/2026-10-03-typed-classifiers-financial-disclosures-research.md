RESEARCH RESULTS
Typed Classifier Models vs Generative LLMs for Structured Financial-Disclosure Extraction

Date: October 3, 2026
Application: TradePartner financial-document research layer
Decision: Whether typed decision models should become the primary semantic extractor
Status: Completed desk research; financial-domain experiments remain to be conducted

1. DECISION AND EXECUTIVE FINDINGS

TradePartner should keep semantic extraction provider-neutral and benchmark Jev as a candidate. The available evidence supports spending effort on a small financial-domain pilot. It does not support adopting Jev as the default extractor, assuming its probabilities are calibrated for financial language, or treating its classifications as investable signals.

The strongest near-term architecture hypothesis is a cascade: deterministic document processing, a cheap semantic classifier, selective fallback to a generative model, and an explicit unresolved outcome. The cheap classifier could ultimately be Jev, a supervised encoder, or a conventional classifier. The identity of that component should be earned through evaluation.

Five findings change the supplied brief materially:

* The independent Jev benchmark includes financial sentiment. Financial PhraseBank accuracy is 73.0%, with expected calibration error of 0.138. Financial-domain calibration is therefore already a concern, rather than a merely hypothetical risk. [1]
* Jev's API confidence is not the selected class probability. Routing policies must distinguish these quantities. [5]
* The vendor documents sensitivity to choice order and irrelevant context, alongside limitations in numbers, dates, indirection and adversarial content. Broad option-rotation results do not establish universal invariance. [6]
* Finance Agent Benchmark's often-quoted 46.8% result is class-balanced accuracy. The same model achieves 51.4% naive accuracy. Saying simply that every tested agent scored below 50% is misleading. [10]
* Economic forecasting, extraction quality and return prediction require separate adoption decisions. A useful research assistant need not possess investment alpha; an automated ranking feature requires stronger evidence.

The main investment in this program should be a defensible label taxonomy, evidence alignment and independent human annotation. At advertised inference prices, those activities are likely to matter much more than Jev's token bill.

2. SCOPE AND RESEARCH METHOD

This report reviews the primary sources behind the supplied brief and related technical research. Searches covered Jev's official documentation, independent typed-model papers, financial NLP, financial reasoning benchmarks, calibration research and SEC developer documentation. Sources were accessed on October 3, 2026. The central Jev and financial-benchmark papers were inspected through accessible primary-source HTML; some financial literature was available only through abstracts or institutional publication records.

This is a targeted evidence review, not a formal systematic review or meta-analysis. It does not claim exhaustive coverage, independent replication, verified vendor billing, or measured TradePartner model performance. No Jev API requests, human annotation exercise, prospective collection or investment backtest were performed. The numerical benchmark results below are published results; budget and operating examples are explicitly illustrative.

Evidence categories used throughout:
Published result: a finding reported by identified researchers.
Vendor specification: interface behavior or operational information supplied by TypeSafe.
Inference: a reasoned implication for TradePartner.
Recommendation: a proposed experimental or architectural choice.
Unknown: an issue that available evidence cannot resolve.

GitHub was inspected read-only for project context. The repository README, documentation map and status file were read. The status file is dated October 2, 2026. No files, issues, branches or pull requests were changed. [17-19]

3. VERIFIED JEV EVIDENCE

Published result: Deusser, Sparrenberg and Sifa evaluated pinned jev-1.13.0 on 37 datasets using 346,009 requests, reportedly costing under $10. On Financial PhraseBank, n=970, accuracy was 0.730, its reported 95% interval was [0.699, 0.756], and ECE was 0.138. The matched reference models scored 0.677 and 0.652. These were open-weight option-probability baselines, not a comprehensive frontier-LLM comparison. Binary threshold tuning helped some tasks; training contamination was not ruled out. [1]

Inference: the directly relevant finance result deserves greater weight than generic sentiment or commonsense headlines. Its calibration error exceeds the brief's proposed 0.05 target. However, Financial PhraseBank measures sentence sentiment, not changes between matched disclosures. It cannot establish TradePartner performance in either direction.

Published review: Tang and Zheng's September 26 evidence audit describes the literature as preliminary and does not establish an independent accuracy advantage from typed readout itself. It locates the clearer early advantages in cost and latency. [2]

Interpretation: one model's hosted behavior does not isolate the effects of architecture, training data, training objective or interface. Evaluating Jev can identify a useful service; it cannot prove that all typed decision models are superior.

4. OFFICIAL INTERFACE AND OPERATIONAL FACTS

Vendor specification: Choice returns a selected option, probabilities over the enumerated options and confidence; it supports up to 255 options. Multiple questions can share a state in one call. [4]

The official Models page lists jev-1.13.0 at $0.042 per million input tokens, with free output tokens. It distinguishes a 64k total request budget from a 32k state-plus-longest-question budget. It lists 100k tokens/second and 80 requests/second, while warning that rate limits can change. Moving aliases resolve to versioned IDs, which the response reports. Customer fine-tuning or LoRA adaptation is not currently offered. These are vendor specifications, not independently tested service guarantees. [3]

Recommendation: use the official documentation, rather than similarly named third-party Jev websites, for interface and pricing assumptions. Capture the observed model ID, SDK version, token usage, endpoint, date and actual billed cost during the experiment. Pinning a name helps reproducibility but does not remove the need to preserve raw responses and check service behavior.

Typed output restricts possible labels. It does not prevent choosing the wrong label, applying the wrong period, misreading a hedge or confusing business segments. Schema correctness and semantic correctness are separate outcomes.

5. CONFIDENCE: A CRITICAL CORRECTION

Vendor specification: for Choice with K options and selected probability p_max, the documented confidence is:

confidence = (p_max - 1/K) / (1 - 1/K).

For four options, confidence 0.90 corresponds to p_max 0.925; p_max 0.90 corresponds to confidence approximately 0.867. Noul returns a yes probability without the same separate confidence field. [5]

Inference: a threshold described merely as "confidence >= 0.90" is underspecified. The transformed statistic is not itself an estimated 90% probability of correctness. Its documented Choice formula also uses only the top probability, so it does not independently measure the runner-up margin.

Recommendation: store both quantities, but evaluate calibration using the actual probability vector. Define routing on validation-calibrated probability, optionally supplemented by evidence sufficiency and out-of-distribution checks. Freeze whether the threshold uses raw p_max, calibrated p_max, vendor confidence, entropy or another statistic. Do not substitute these quantities silently.

General calibration research finds that accurate neural classifiers can still be miscalibrated, and identifies temperature scaling as a practical post-processing approach in studied settings. This provides a baseline method, not a guarantee for financial disclosure shift. [13]

6. VENDOR CLAIMS AND DEMOS: WHAT THEY DO NOT ESTABLISH

TypeSafe's launch announcement is dated September 15, 2026. Its large advertised efficiency ratios arise from vendor-designed workflows using model-based reference probabilities. The announcement acknowledges evaluation limitations and says the large ratios are toward the high end of real-world gains. [7]

Inference: those ratios cannot be imported into a financial-document budget or treated as accuracy against independent human truth. A fair comparison should use identical decision targets and separately measure output quality, token usage, latency, retries and annotation overhead.

A relevant vendor cookbook classifies 60 selected SEC business sections into SIC groups. It reports 39/60 correct forced group labels and 27/30 correct in its confident subset. Its cached results use jev-1.12 on August 12, 2026, and its sample was filtered to support the reported SIC label. This is a useful integration example, not validation of financial change detection. [8]

The dated internal example does not contradict a later public release, but it illustrates why publication date, experiment date, sample selection and model version must be recorded separately.

7. FINANCIAL NLP AND BENCHMARK EVIDENCE

Published finance literature: Cohen, Malloy and Nguyen's Lazy Prices finds that changes in corporate filings relate to future operations and returns. It supports testing document change as information. Its historical portfolio findings are not a current expected-return forecast or proof of semantic-model superiority. [11]

The Loughran-McDonald survey emphasizes the imprecision and implementation sensitivity of textual analysis in finance. Financial dictionaries therefore remain essential measurement baselines, even when they are unlikely to solve nuanced pairwise tasks alone. [12,16]

Araci's FinBERT work supplies a domain-adapted sentiment baseline. An off-the-shelf sentiment model should not be relabeled as a demand-change classifier. The appropriate comparison is a supervised pair classifier built from financial-domain representations, with the task and training regime stated explicitly. [14]

Ng, Zheng and Zheng's EASE article combines financial lexicon knowledge and embedding geometry. Its accessible institutional abstract reports better explanatory power for market returns and future earnings relative to unaugmented measures. That is narrower than verified prospective return prediction. Full-text replication details were not available in this review. [15]

Fin-RATE evaluates longitudinal and cross-entity SEC reasoning and reports deterioration as tasks become cross-document. It supports testing period/entity alignment and separating retrieval failure from reasoning failure; it is not a direct estimate of four-class disclosure-change performance. [9]

SECQUE comprises 565 expert-written financial-analysis questions and uses an LLM-based judging mechanism. It is useful for designing difficult financial examples, but its broad question-answering targets cannot replace human labels for TradePartner's taxonomy. [20]

Aavang and colleagues report SEC-to-earnings-call domain shift and 79.7% human-evaluated precision for their open-ended LLM KPI extraction system. This supports separate document-type evaluation. It does not imply a universal 79.7% financial-extraction ceiling. [21]

Finance Agent Benchmark's original 2025 evaluation reports 46.8% class-balanced versus 51.4% naive accuracy for o3. It combines retrieval, tool use and financial reasoning. It should be cited as a dated, specific benchmark result, not the October 2026 frontier or a semantic-classification error rate. [10]

8. ANSWERS TO THE RESEARCH QUESTIONS

Accuracy: Unknown for TradePartner's paired financial taxonomy. Published finance sentiment evidence is insufficient to establish adoption.

Calibration: Potentially useful, but not portable by assumption. The financial benchmark provides a concrete reason to recalibrate and validate.

Selective prediction: Plausible and testable. The decisive quantity is accepted-set error at useful coverage, including its uncertainty.

Domain transfer: Must be measured. SEC filings, earnings releases and call Q&A should be separate evaluation strata.

Temporal robustness: Unknown. Freeze outputs prospectively and retain historical period tests without claiming training contamination is eliminated.

Cross-sector robustness: Unknown. Use sector-appropriate applicability rules; backlog quality is not equally meaningful for all businesses.

Cross-document reasoning: An important unresolved risk. Test supplied matched passages first, then the full retrieval pipeline.

Incremental information: Not established for Jev features. Compare against deterministic change, sentiment and point-in-time numerical features.

Economic usefulness: Not established. Fundamentals, revisions and returns are distinct endpoints.

Cost and scalability: Advertised token costs are attractive; end-to-end economics depend on repeated context, fallback, annotation and operations.

9. COMPARISON OF ARCHITECTURES

Deterministic NLP:
Best role: section parsing, sentence differences, dictionaries, exact numbers and timestamps.
Strength: reproducible, inexpensive, auditable.
Limitation: semantic direction and qualification are difficult.

Classical supervised classifier:
Best role: strong cheap baseline once domain labels exist.
Strength: reproducible training and straightforward calibration.
Limitation: sparse rare classes and vocabulary shift.

Domain encoder or embedding classifier:
Best role: task-specific classification with bounded context.
Strength: domain adaptation and flexible supervised learning.
Limitation: pair alignment and document-type shift remain problems.

Typed decision service such as Jev:
Best role: zero-shot or instruction-defined bounded decisions with probability output.
Strength: convenient interface and potentially low variable cost.
Limitation: unproven paired-finance accuracy, opaque training and limited evidence generation.

Generative LLM with constrained output:
Best role: difficult interpretation and selection of supporting passages.
Strength: flexibility in handling compound context.
Limitation: semantic errors persist despite a valid schema; self-reported confidence needs validation.

Cascade:
Best role: accept reliable inexpensive decisions and defer difficult ones.
Strength: tunable coverage and cost.
Limitation: two models may share errors; fallback quality on the hard tail can be substantially worse than its average score.

Recommendation: compare a small structured-output generative model as well as a frontier model. Otherwise Jev could appear economical only because the competing service was unnecessarily expensive.

10. REDEFINE THE MEASUREMENT BEFORE CHOOSING A MODEL

The taxonomy currently blends narrated economic conditions, management tone, specificity and realized fundamentals. Separate them.

Recommended first three targets:
Demand: direction of explicitly described demand conditions for a matched entity, segment and horizon.
Inventory pressure: direction of explicitly described excess, obsolescence, clearance or shortage pressure; do not treat lower inventory automatically as improvement.
Liquidity pressure: direction of explicitly described funding or payment constraints; leave ratio calculation in code.

Defer backlog quality until applicable sectors and quality criteria are defined. Separate pricing realization, pricing power and input-cost pass-through. Treat management confidence and specificity as communication variables, rather than direct economic state.

Use two output dimensions:
Evidence status: sufficient, not discussed, mixed, incomparable, or extraction failure.
Economic direction: improved, unchanged or deteriorated, only when evidence supports comparison.

This prevents absent discussion from becoming "unchanged" and prevents a confidently selected "unclear" label from becoming an automated directional feature. Model abstention is a third, operational state: the evidence may be sufficient while the model cannot resolve it reliably.

For pressure measures, preserve the native increased/decreased label and separately map it into a favorable/adverse convention. Lower liquidity pressure is favorable; greater management confidence is not necessarily an economic improvement.

11. COMPARISON SCOPE AND GROUND-TRUTH RULES

Primary unit: issuer, topic, segment/geography, reporting period, comparator basis, and matched evidence pair.

Each observation should identify whether it measures sequential change, year-over-year change or changed guidance about a fixed future period. Never pool those comparisons without recording their meaning.

Start with same-form matched sections where practical. Comparing a 10-Q to a preceding 10-K can confound quarterly and full-year scope. Removing a claim is not automatically the opposite claim. Silence can be a separate disclosure-change feature.

Annotators should use only the supplied, contemporaneously available disclosures and the written manual. Hide future outcomes and model predictions. Numbers stated in the disclosure can be evidence for narrated economic conditions if the manual permits them; externally calculated realized growth belongs in a separate fundamentals layer.

Require exact source spans supporting the decision. Preserve genuine disagreement before adjudication. Gold-standard labels are operational reference labels, not infallible truth about underlying business conditions.

Contradiction requires incompatible claims about the same entity, scope, period and conditions. Demand changing between quarters is ordinarily a change, not a contradiction. A forecast revised after new information is not automatically dishonest. Use a separate relation task with entailed/compatible/incompatible/insufficient evidence, and an explicit premise-hypothesis direction.

12. ANNOTATION AND SAMPLE DESIGN

Recommendation: begin with 150-250 pilot pairs and three core dimensions. Obtain two independent labels throughout and a third independent label on a prespecified random subset. Add adjudication for disagreements without hiding their original frequency.

Assess agreement per dimension and class, with raw agreement, prevalence, confusion matrices and an agreement statistic such as Krippendorff's alpha or Cohen's kappa. A high overall score dominated by "unchanged" is insufficient. Rewrite unstable definitions before the final benchmark.

Next build approximately 2,000 pairs, expanding toward 3,000 if rare adverse states lack support. An illustrative allocation is 900 development, 500 calibration/validation and 600 sealed test pairs. These are planning numbers, not a power calculation.

Use two samples:
Representative sample: approximates the intended issuer and document distribution, with a known sampling frame.
Challenge sample: deliberately enriched for hedges, mixed segments, scope changes, negation and rare deterioration.

Report them separately. Enriching adverse examples changes class priors; calibration measured on the enriched sample may not describe production. Record sampling probabilities when weighted population estimates are needed.

At 600 independent cases, an accuracy near 85% has a rough 95% sampling margin of about three percentage points. Issuer clustering can widen this. Splitting into many sectors and rare classes creates much smaller denominators. The nine labels on one pair are not nine independent documents.

Illustrative annotation budget: 2,000 pairs x two annotators x six minutes per pair = 400 annotator-hours. At $40/hour this is $16,000, before training, adjudication and QA. Actual time should be estimated in the pilot; additional dimensions can increase it substantially.

13. SPLITS, LEAKAGE AND PROSPECTIVE COLLECTION

The brief's historical train/validation/test periods are useful but not sufficient. Adjacent pairs can share the same filing, and boilerplate can appear across many issuers.

Freeze an issuer-level split and ensure that neither document of a test pair appears in training or calibration. Deduplicate passages and near-duplicate templates across splits. Include one out-of-time evaluation for continuing issuers and another for unseen issuers. If resources require a combined holdout, label both axes clearly and avoid claiming their effects have been isolated.

Keep all retrieval candidates, prompt tuning, feature selection, threshold selection and classifier training outside the sealed test set. If probabilities are calibrated and routing is tuned on the same validation sample, use a nested or subdivided procedure where feasible.

Modern pretrained models may have seen historical filings or later outcomes. That can affect extraction as well as investment backtests. Human labels remain useful for historical task evaluation, but do not establish uncontaminated historical generalization.

Begin immutable prospective capture immediately after taxonomy and inference configuration are frozen. Capturing new disclosures does not require waiting until historical return tests are complete. Outcomes can mature later, providing an independent test while historical work continues.

14. BENCHMARK DESIGN AND FAIRNESS

Run two distinct experiments:

A. Measurement with supplied evidence.
Every system receives the same human-verified matched passages, surrounding context, dates and scope metadata. This isolates classification quality.

B. End-to-end extraction.
Every system starts from the same archived filings. Evaluate section parsing, relevance retrieval, evidence coverage, alignment, classification and unresolved cases. This identifies pipeline failures.

For deterministic change measures, train a disclosed supervised mapping when evaluating semantic labels. A cosine similarity score is not naturally an improved/unchanged/deteriorated classifier. Retain raw change features separately for economic ablation.

Use these comparison arms:
Majority-class baseline.
Dictionary/change features plus logistic regression.
Paired TF-IDF plus logistic regression or linear SVM.
Financial encoder/embedding pair classifier.
Pinned Jev with frozen instructions and criteria.
Small generative model with constrained labels.
Frontier generative model with constrained labels.
Best validated cheap model plus fallback and possible unresolved output.

Report zero-shot and supervised regimes separately. A supervised encoder has access to labels that zero-shot Jev does not, but it is still a relevant deployment alternative. Conversely, do not grant one system unlimited prompt optimization while freezing another arbitrarily. Prespecify development budgets and learning curves.

Do not require costly explanations from the LLM while requesting only labels from Jev in the main classification comparison. Benchmark evidence selection as a second endpoint. All systems should link accepted labels to actual source spans, even if the model cannot generate prose.

15. METRICS, STATISTICAL TESTS AND ADOPTION GATES

Recommended primary endpoint: average of per-dimension macro-F1 across the three frozen core dimensions, on the representative sealed test sample. Also report each dimension separately and include evidence-status performance.

Secondary endpoints: adverse-state precision and recall, unchanged confusion, coverage, accepted-set error, evidence support, calibration, instability and end-to-end failure rate. Report denominators. Overall accepted-set accuracy is not the same quantity as deterioration precision.

Use paired issuer-cluster bootstrap intervals for model differences. A paired correctness test can supplement accuracy analysis but does not replace class-specific metrics or cluster handling. Adjust the prespecified family of confirmatory comparisons, for example with Holm correction. Treat additional sector and prompt slices as exploratory unless preregistered.

Proposed planning gates, to be revised after pilot operational requirements and then frozen:
Core macro-F1 >= 0.85, with no core dimension hidden by the average.
Accepted adverse-label precision >= 0.95, with a one-sided lower confidence bound >= 0.90.
Meaningful automated directional coverage, provisionally >= 0.50 of eligible representative cases.
No more than 0.02 macro-F1 loss versus the best qualified alternative if lower cost motivates adoption; establish this through a prespecified noninferiority comparison.
No adequately powered major sector showing a degradation beyond the frozen tolerance; underpowered sectors remain unapproved rather than being assumed equivalent.
Every accepted record carries valid evidence provenance and scope.

These are recommended operating proposals, not thresholds validated by literature. Higher precision requirements may require more annotated adverse predictions. For example, even 60 independent successes out of 60 provide only about a 95.1% one-sided exact lower bound; clustered cases provide less information. A tiny perfect subset proves little.

16. PROBABILITY EVALUATION AND SELECTIVE PREDICTION

For K classes, use multiclass Brier score:
Brier = mean over observations of sum over classes (p_k - indicator[y=k])^2.
State whether the score is divided by K; conventions differ.

Also report log loss, classwise reliability curves and ECE with frozen binning. ECE is a summary sensitive to binning and can hide rare-class overconfidence. A lower Brier score reflects both calibration and discrimination, so inspect both rather than calling it pure calibration.

Fit temperature scaling or another preselected calibrator using only calibration data. If the sample is small, flexible per-sector calibration may overfit. Evaluate raw and calibrated predictions separately.

For generative systems, distinguish label-token probabilities from a generated "confidence: 0.9" field. The latter is a prediction to validate, not a measured probability. If comparable probabilities are unavailable, mark calibration as unavailable and evaluate an independently validated routing score.

Plot risk versus coverage, stratified by dimension and adverse label. Report directional coverage excluding not-discussed, incomparable and unresolved cases. Check whether excluded cases concentrate in small companies, stressed issuers or particular sectors.

Selective measurement changes the economic sample. Features observed only in easy disclosures can yield biased economic conclusions. Maintain missingness indicators and analyze the routing policy as part of the research system.

17. CASCADES AND COST-SENSITIVE ROUTING

Evaluate fallback on the cases actually rejected by the first model. Applying the LLM's overall accuracy to the rejected tail is invalid. Do not assume two models' errors are independent or multiply their confidence scores.

Compare: cheap model alone; fallback alone; confidence routing; confidence plus evidence/scope routing; and human review on a bounded unresolved tail. Freeze the policy before testing.

Let q be the routed fraction. Variable cascade cost is:
N x (cheap-call cost + q x fallback-call cost),
plus retrieval, retries, calibration, evidence verification and review.

Illustrative arithmetic, not observed billing: 100,000 paired requests at 6,000 billed input tokens each consume 600 million tokens. At the official advertised $0.042/million, the Jev portion is $25.20. If nine independent requests repeat the same context, it becomes roughly $226.80 before different instruction lengths. Shared-state batching may avoid much of that repetition. [3]

If fallback costs an assumed $0.02 per pair, routing 20% adds $400; routing 80% adds $1,600. If fallback costs $0.10, those additions become $2,000 and $8,000. These assumed prices are scenarios, not current quotes for a named LLM.

Cost per correct classification can conceal class imbalance and the cost of errors. Report it alongside expected error loss, human-review hours and cost per accepted supported feature. A cheap high-volume source of confident errors is not an economical research layer.

Measure median/p90/p95/p99 latency under a fixed concurrency and geographical setup. Separate request latency, rate-limit waiting and whole-job throughput. Long paired text can hit token throughput limits well before request limits. Validate service availability before making it a workflow dependency.

18. ROBUSTNESS AND NEGATIVE CONTROLS

Vendor-documented choice-order and irrelevant-context weaknesses make targeted perturbation essential. [6]

Prespecify semantic-preserving tests: option permutations, paraphrased instructions, equivalent date formats with explicit metadata, reordered passages retaining chronological labels, irrelevant paragraphs, longer context, repeated boilerplate and issuer masking. Measure class flips and probability changes.

Prespecify semantic-changing tests separately: negate a claim, change the referenced segment, substitute the comparison period, remove supporting evidence or reverse an actual matched trend. These should change the output when the label rules require it.

Same-document comparisons should be unchanged only when the topic is sufficiently discussed; absent topics should remain not discussed. Reversal is not universally an opposite-class operation for forecasts, silence or asymmetric scope.

Random labels should eliminate generalizable label association. Shuffled issuer identities need not eliminate a genuine text-content effect; identity can be irrelevant to supplied-evidence classification. Shuffled period pairs can still produce classifiable differences. Interpret each control against its precise hypothesis rather than demanding automatic collapse.

Use a held-out adversarial set for financial hedging, scope and time confusion. Synthetic examples are diagnostics, not a substitute for representative real disclosures.

19. ECONOMIC VALIDATION

After qualifying measurement quality, choose one primary fundamentals target. A suitable first hypothesis is whether demand deterioration predicts next-quarter revenue-growth deceleration beyond contemporaneous growth and deterministic text change. Define the target, horizon, availability timestamp, sector controls and missingness rules in advance.

Inventory and liquidity targets should account for business model. Margin deterioration can reflect commodity inputs rather than inventory pressure; cash flow can improve temporarily through working-capital release. Statistical association alone does not establish a clean causal mechanism.

Compare identical data splits and downstream modeling budgets:
A: market and point-in-time fundamentals.
B: A plus deterministic text/dictionary features.
C: B plus cheap-model semantic features.
D: B plus generative-model semantic features.
E: B plus the actual cascade outputs and routing indicators.

Keep an interpretable regression as a mechanism check before moving to more flexible LightGBM/XGBoost models. Fit vocabulary, embeddings that require fitting, normalization and feature selection within each training fold. Report out-of-sample loss differences, uncertainty and stability across time blocks.

Analyst revisions are a useful optional mechanism if timestamped historical consensus data are available. Lack of licensed revision data should not block all fundamentals research. Do not infer the feasibility of this dataset from SEC coverage alone.

20. RETURN VALIDATION AND AVAILABILITY

Choose one primary return horizon; treating 5, 20, 60 and 120 days as simultaneous discovery endpoints increases the opportunity for false findings. Secondary horizons can be reported with multiplicity control.

Define investable known_at as no earlier than the public disclosure's availability and completion of the actual extraction process. Earnings releases or calls can precede the 10-Q, so the filing may repeat information already priced. Separate first disclosure from confirmation.

Use point-in-time issuer/security mapping, delistings, amendments, corporate actions and a realistic next executable price. Portfolio evaluation should include liquidity, turnover, transaction costs and, for short strategies, borrow feasibility and costs. Overlapping long-horizon returns require dependence-aware inference and split boundaries that prevent outcome leakage.

Freeze model-produced features and the downstream strategy separately. A historic filing can be public at t while a modern model has learned later information; archival availability alone does not prove an uncontaminated backtest.

Historical return findings from modern pretrained models should be exploratory. Prospective frozen outputs are stronger evidence, although a short prospective sample still cannot establish robust alpha.

21. TRADEPARTNER CONTEXT AND INTEGRATION

Observed repository context: TradePartner describes itself as a personal local research system emphasizing evidence grading, point-in-time data, honest backtests and paper trading. Its status file places data foundation and backtesting near completion while paper trading remains in build, with an EDGAR backfill and data-quality work still active. [17,18]

Recommendation: treat semantic extraction as a separate research experiment. It should not become a prerequisite for the existing backfill or baseline strategy, and it should not alter paper-trading execution while measurement validity is unresolved.

Add a future provider-neutral feature interface with these records:
issuer/CIK and point-in-time security mapping reference;
previous/current accession and document hashes;
form, reporting period, public availability and ingestion time;
topic, entity/segment, comparator basis and applicability;
source spans and source offsets;
parser, retrieval and taxonomy versions;
requested and observed model versions and SDK/configuration;
full raw request/response hashes and stored probabilities;
raw label, calibrated probabilities and calibration version;
evidence status, routing decision, fallback result and unresolved reason;
inference completion, billed usage and measured cost;
human-label provenance for benchmark records.

Archive source filings and parsed representations separately. An amended filing is a new source event, not a replacement of what the system historically knew. SEC submission/XBRL APIs support the underlying data infrastructure, but they do not produce this semantic ground truth. SEC documentation also specifies fair-access limits; reuse archived data rather than repeatedly downloading it for each model. [22,23]

The documentation map describes research reports and hypotheses as frozen artifacts after registration/merge. A future report can follow that convention, but this task makes no repository changes. [19]

The semantic layer produces research features. Statistical ranking, deterministic portfolio construction, risk checks and broker controls remain distinct components.

22. DECISION FRAMEWORK AND FALSIFICATION

Adopt Jev for qualified feature dimensions if it meets the frozen measurement and evidence gates, offers useful directional coverage, and achieves a favorable cost-quality tradeoff against the best relevant alternatives. Do not demand identical conclusions for every feature or document type.

Reject Jev as the primary extractor for a dimension if it fails semantic quality, remains overconfident after calibration, handles document pairs poorly, shows instability beyond tolerance, or routes so much traffic that its operational advantage disappears. Preserve the negative result.

A supervised classifier matching quality at lower total cost should win. A generative model materially exceeding the reliability gates may justify its expense. If all systems fail, narrow the taxonomy or retain human-assisted research rather than manufacturing directional labels.

Extraction adoption and investment adoption are separate:
Research assistance: supported document organization or analyst triage.
Measurement automation: reliable, supported structured features.
Fundamentals forecasting: incremental out-of-sample economic information.
Portfolio use: incremental implementable return information and prospective support.

No return edge is required to use an extractor for research assistance. A high-quality extractor with no incremental economic information should not be promoted into the ranking model merely because it works technically.

23. PRIORITIZED RESEARCH PROGRAM

Priority 1: define three dimensions, evidence statuses and comparator scope; label a 150-250-pair pilot; estimate agreement and annotation time.

Priority 2: run supplied-evidence Jev, classical/encoder and structured generative comparisons. Inspect adverse-state errors and probability calibration before expanding the dataset.

Priority 3: freeze taxonomy, provider configurations, calibration/routing policy and sealed benchmark. Start prospective capture at this point.

Priority 4: evaluate representative and challenge sets separately; qualify only dimensions and document types with sufficient support. Test full retrieval/alignment separately from classification.

Priority 5: preregister one fundamentals hypothesis, then optional revision analysis and one primary return horizon. Preserve every trial, including failed taxonomies and models.

The preregistration should specify the sampling frame, issuer/document splits, all primary endpoints, comparison margins, statistical tests, exclusion rules, missingness treatment, calibration procedure, routing score, cost assumptions and what constitutes a new experiment.

Do not specify a fixed calendar duration until the pilot establishes label feasibility and the sealed-test sample size needed. Cheap inference does not make independent annotation or a 120-day prospective outcome mature faster.

24. FINAL RESEARCH JUDGMENT

Jev is a credible benchmark candidate for narrow financial decisions. Its accessible probability interface and low advertised cost make it worth testing. The evidence does not establish that it is a calibrated financial measurement system, that typed readout confers a general accuracy advantage, or that its features predict returns.

The most decision-relevant warning is the gap between broad benchmark headlines and finance sentiment calibration. The most promising opportunity is a measured cascade that retains evidence and unresolved outcomes. The most important work is defining a financial comparison that humans can label consistently.

Recommended decision today: authorize a bounded, provider-neutral extraction pilot; defer selection of a primary semantic model and portfolio use until the appropriate gates are passed.

25. SOURCES AND ACCESS NOTES

All URLs below identify primary research, official documentation, institutional publication records or the inspected repository. Access date: October 3, 2026. No quoted marketing claim is treated as independent validation.

[1] Tobias Deusser, Lorenz Sparrenberg and Rafet Sifa. Evaluating and Benchmarking the System One Model Jev. arXiv:2609.37647v1, September 29, 2026. Preprint. Methods/results HTML inspected; benchmark was not independently rerun.
https://arxiv.org/html/2609.37647v1

[2] Lijuan Tang and Yuemeng Zheng. Typed Decision Models: An Early Evidence Audit and Evaluation Checklist. arXiv:2609.32160v1, September 26, 2026. Preprint evidence review; its referenced studies were not all individually replicated or audited here.
https://arxiv.org/html/2609.32160v1

[3] TypeSafe AI. Models. Official vendor documentation; current listed price, limits, aliases and customization behavior.
https://docs.typesafe.ai/models

[4] TypeSafe AI. Choice. Official interface specification.
https://docs.typesafe.ai/primitives/choice

[5] TypeSafe AI. Confidence. Official formulas and distinction between confidence and probabilities.
https://docs.typesafe.ai/confidence

[6] TypeSafe AI. Jev 1.13 jaggedness. Vendor-listed failure modes; page says last reviewed October 2, 2026.
https://docs.typesafe.ai/model-jaggedness/jev-1.13

[7] Diogo Almeida / TypeSafe AI. Introducing System One Models & Jev. September 15, 2026. Vendor announcement and evaluation caveats.
https://typesafe.ai/blog/introducing-system-one-models-and-jev

[8] TypeSafe AI. Classification using confidence. Vendor SEC/SIC cookbook; selected 60-report sample, cached jev-1.12 results dated August 12, 2026.
https://docs.typesafe.ai/cookbooks/classification_using_confidence

[9] Yidong Jiang et al. Fin-RATE: A Real-world Financial Analytics and Tracking Evaluation Benchmark for LLMs on SEC Filings. arXiv:2602.07294v1, February 7, 2026. Preprint; broader financial QA, not the proposed classification task.
https://arxiv.org/html/2602.07294v1

[10] Antoine Bigeard, Rayan Krishnan, Shirley Wu and Langston Nashold. Finance Agent Benchmark: Benchmarking LLMs on Real-world Financial Research Tasks. arXiv:2508.00828v1. Original 2025 experiment; inspect Table 2 for class-balanced and naive accuracy. Vals AI-funded evaluation, not a current universal frontier estimate.
https://arxiv.org/html/2508.00828v1

[11] Lauren Cohen, Christopher Malloy and Quoc Nguyen. Lazy Prices. Journal of Finance, 2020. Publisher abstract and publication information inspected; historical findings are not forecast performance for TradePartner.
https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12885
https://doi.org/10.1111/jofi.12885

[12] Tim Loughran and Bill McDonald. Textual Analysis in Accounting and Finance: A Survey. Journal of Accounting Research, 2016. Accessible publication/search abstract information; full publisher text access was unsuccessful.
https://doi.org/10.1111/1475-679X.12123

[13] Chuan Guo, Geoff Pleiss, Yu Sun and Kilian Q. Weinberger. On Calibration of Modern Neural Networks. ICML / PMLR 70, 2017. Primary conference publication.
https://proceedings.mlr.press/v70/guo17a.html

[14] Dogu Araci. FinBERT: Financial Sentiment Analysis with Pre-trained Language Models. arXiv:1908.10063, 2019. Domain-sentiment research baseline; not paired disclosure ground truth.
https://arxiv.org/html/1908.10063v1

[15] Ka Chung Ng, Jiexin Zheng and Rong Zheng. Augmenting Machine Learning with Human Knowledge for Sentiment Analysis of Corporate Disclosures. Decision Support Systems 209, article 114735, October 2026. Institutional abstract inspected; full publisher page unavailable. Specific prospective-prediction claims not established here.
https://research.polyu.edu.hk/en/publications/augmenting-machine-learning-with-human-knowledge-for-sentiment-an/
https://doi.org/10.1016/j.dss.2026.114735

[16] University of Notre Dame, Software Repository for Accounting and Finance. Loughran-McDonald Master Dictionary with Sentiment Word Lists. Official research resource; freeze the dictionary release used and review its stated license for the intended use.
https://sraf.nd.edu/loughranmcdonald-master-dictionary/

[17] TradePartner README. Read-only project context; GitHub content SHA 52fb48e37293a892561347b98fa1101a8b71a4f5.
https://github.com/josejuarez96/tradepartner/blob/main/README.md

[18] TradePartner docs/STATUS.md. Updated October 2, 2026; inspected content SHA b9ea5060be1d14550bcc182421238ba40e0fb8e7. This supports project-stage context, not an audit of the complete implementation.
https://github.com/josejuarez96/tradepartner/blob/main/docs/STATUS.md

[19] TradePartner documentation map. Inspected content SHA fc569644a67146f712e1de156cf2a1b64f7c6f65.
https://github.com/josejuarez96/tradepartner/blob/main/docs/README.md

[20] Noga BenYoash et al. SECQUE: A Benchmark for Evaluating Real-World Financial Analysis Capabilities. GEM workshop, ACL Anthology, 2025. Primary publication record and abstract inspected.
https://aclanthology.org/2025.gem-1.16/

[21] Rasmus T. Aavang et al. Effective Performance Measurement: Challenges and Opportunities in KPI Extraction from Earnings Calls. ACL Industry Track, 2026. Primary publication record and abstract inspected.
https://aclanthology.org/2026.acl-industry.100/

[22] U.S. SEC. EDGAR Application Programming Interfaces. Official submission/XBRL API documentation.
https://www.sec.gov/search-filings/edgar-application-programming-interfaces

[23] U.S. SEC. Accessing EDGAR Data / Developer Resources. Official fair-access guidance, including the stated aggregate maximum of 10 requests per second.
https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
https://www.sec.gov/about/developer-resources

END OF REPORT
