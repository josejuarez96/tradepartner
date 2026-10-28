# Plan: Split config.py into sibling modules (R0, R1)

**Spec:** none. This is a refactor that keeps behaviour unchanged, so the size-M spec-lite is issue #2512 plus this plan's Acceptance section (development-process, "Right-size the ceremony"). No ADR: a module layout is not on the always-ADR list, and each slice can be undone by reverting one PR · **Status:** Draft · **Issue:** #2512 · **Source:** the owner's architecture review of the churn hotspots (2026-10-28, read-only, outside the repo), R0 and R1 only, checked against the code at `3ee5d844`.

## Approach (short)

`src/tradepartner/config.py` (2,986 lines) has changed 126 times since 2026-09-28, more than any other file: 33 of those changes were data work, 19 execution and 11 backtest. One file serves every domain, so unrelated PRs collide in it. R1 moves the data sections and the backtest sections into three sibling modules. `config.py` keeps `Settings` and re-exports every moved name, so no caller changes. They are sibling modules, not a `config/` package: the paths other code pins (below) stay valid, and `from tradepartner.config import X` keeps working everywhere. R0 comes first: it widens the review prefixes in `ready_pr.py` from file names to stems, so the new modules still get their reviewers.

**Module map (the classes, at `3ee5d844`).** Each moved helper and regex goes with the class that uses it.
- `config_base.py` (T311): `_ClosedConfig` and `_PHASE3_MODEL_CONFIG`. These are the only definitions shared by data, backtest and the sections that stay.
- `config_data.py` (T312, T313, T314):
  - T312: `CalendarConfig`, `StoreConfig` with `_DUCKDB_SIZE_RE`, `IngestConfig`, `RatesConfig`.
  - T313: `EdgarConfig` with `_STATEMENT_TAGS`, `_CIK`, `_CLASS_MEMBER`, `_QUALIFIED_TAG`, `_REFUSED_FILENAME` and `_default_edgar_cache_dir`; `MasterConfig` with `parse_chains`, `parse_successor_id`, `check_security_id` and their regexes.
  - T314: `UniverseConfig` with `_parse_id_at_day` and `parse_accepted_*`, `HealthConfig`, `BorrowConfig` with `_default_alpaca_assets_dir`, `IndexCheckConfig` with `_MONTH_PATTERN`, `OpenfigiConfig`, `AdjustConfig`, `GapConfig`, `CoverageConfig` with `ReferenceName`, `parse_reference_name`, `DEFAULT_REFERENCE_NAMES` and their regexes, and `EventsConfig`.
- `config_backtest.py` (T315): `HypothesisFamily`, `Cadence`, `SignalAnchor`, `HypothesesConfig`, `StrategyConfig`, `ProfitabilityConfig`, `CombinedConfig`, `PortfolioConfig`, `FilingDelayConfig`, `SpinoffConfig`, `FamilySpec`, `FAMILIES`, `_HYPOTHESIS_FAMILIES`, `FAMILY_PARENTS`, `FAMILY_SIGNAL_SECTIONS`, `ENGINE_FAMILIES`, `CostsConfig`, `HoldoutConfig`, `BacktestConfig`, `MetricsConfig`, `ScheduleConfig`.
- **Stays in `config.py`:**
  - `Settings` and everything that refers to it: `_settings_has_key`, `kill_wait_seconds`, `get_settings`, `clean_message`, `secret_values`, `render_validation_errors`, `_CONTROL_CHARS`, `_is_set`, `_EMAIL_REQUIRED_SETTINGS`, `_default_env_file`.
  - The paper and execution names: `AlpacaConfig`, `ExecutionConfig`, `AlpacaPaperKeyPair`, `paper_key_variable_names`, `MAIN_BOOK_ID`, `SANDBOX_BOOK_ID`, `_BOOK_PAIRS_FIELD`, `SmallAccountConfig`, `ShortRiskConfig`, `RiskConfig`, `PaperConfig`, `_DEFAULT_MIN_REBALANCES`, `AlertsConfig`, `AlertChannel`, `_DEFAULT_ALERT_CHANNELS`, `DashboardConfig`, `UiConfig` (it calls `kill_wait_seconds`), `FROZEN_PAPER_KEYS`, `FROZEN_EXECUTION_KEYS`, `FROZEN_COSTS_KEYS` and `PAPER_FAMILIES`.
  - The Lab: `LabConfig`, `_HHMM_RE`, `FORBIDDEN_AXIS_PREFIXES`, `ALLOWED_AXIS_PREFIXES`, `_DEFAULT_SWEEPABLE_KEYS`, `_DEFAULT_SECONDS_PER_VARIANT`.
  - Research: `ResearchLabelingConfig`, `ResearchClaudeConfig`, `ResearchConfig`, `_DEFAULT_EIGHTK_ITEMS`, `_EIGHTK_ITEM`, `_default_research_data_dir`.
  - The module docstring.

  `config.py` imports what it uses from the new modules, for example `check_security_id` for `AlpacaConfig`, `FAMILIES` for the Lab names and `Cadence` for `PaperConfig`.

**Move rules (every task T311 to T315; a writer reads this list with the line).**
1. Classes, helpers, regexes and their comments move verbatim, in their current order. The only new lines are module docstrings and imports. Review the diff with `git diff --color-moved=zebra main...HEAD -- src/`: apart from imports, every removed line reappears unchanged.
2. `config.py` re-exports each moved name as `from tradepartner.config_<x> import Y as Y`. The explicit alias is what mypy strict and ruff accept as a re-export. Private names are re-exported too, so `vars(tradepartner.config)` still holds every class: `tests/test_config.py::_config_models` scans it.
3. The `tests/` diff is empty, and the T310 goldens pass unchanged. Together they are the proof. No test retargets an import.
4. A new module never imports `tradepartner.config`. `config_data.py` and `config_backtest.py` import only `config_base.py`, the standard library and pydantic, so nothing can form a cycle.
5. Nothing paper, execution, research, Lab or `Settings` moves while the ADR 0017 span runs (that is R7).
6. Check before claiming. `team.py graph` FILE CONTENTION reports at file level, and `config.py` is named by six open tasks, so also read those tasks' lines (`team.py show <Tn>`) and the open PRs' `config.py` hunks. Merge the slice early in a train batch, when no open task or PR edits the classes it moves. The other plans' tasks that do name those classes are this plan's `Depends on`.
7. Until T315 merges, a new config section is added to `config.py` as it is today. Open question 3 covers where new sections go after that.

**What the review got right, and where this plan corrects it** (checked at `3ee5d844`):
- (a) `_section_keys` and `frozen_keys` live in `backtest/hypothesis.py`, not `frozen.py`. `_section_keys` reads `Settings.model_fields[section].annotation.model_fields`, and `frozen_keys()` walks `FROZEN_SECTIONS`. Moving a class verbatim changes neither the field order nor the section order.
- (b) `canonical_params_json` and `params_sha256` live in `store/registry.py` and use `sort_keys=True`. A window's `frozen_json` is `registry.canonical_params_json(params)` (`execution/window.py`), so it is sorted too. `Settings` does not move here, so the "last so no section shifts" comments stay; relaxing them is out of scope.
- (c) `config_base.py` gets only the two shared definitions, not "the regexes". Each regex is used by exactly one class and goes with it. `_HHMM_RE` is used only by `LabConfig`, which stays, and by `backtest/quiet.py`, so it does not move at all.
- (d) The review's stay-list misses three items: `_settings_has_key` (it calls `Settings`), `UiConfig` (it calls `kill_wait_seconds`, which takes `Settings`) and `_default_research_data_dir`. That last one stays because its text names `data/research`, which `tests/test_llm_boundary.py` allows only in the files listed in `DATA_DIR_FILES`. The review's move-lists miss `FilingDelayConfig`, `SpinoffConfig` (both used inside `FAMILIES`), the `Cadence`, `HypothesisFamily` and `SignalAnchor` literals, `ReferenceName` and its helpers, and the derived family tuples.
- (e) The only private names imported from `tradepartner.config` elsewhere are these: `_HHMM_RE` (`backtest/quiet.py`), `_default_env_file` (`calendar.py`, `tests/test_config.py`), and `_DEFAULT_SWEEPABLE_KEYS` and `_settings_has_key` (`tests/test_config.py`). All four stay in `config.py`. `dashboard/book_actions.py` uses `config.__file__` as the project-root anchor, and `config.py` keeps its path. The moved `_default_*_dir` helpers resolve `Path(__file__).parents[2]`, which is the same directory from a sibling module; the T310 dump golden checks this.
- (f) Tests that pin `config.py` by path:
  - `tests/test_llm_boundary.py` `VENDOR_FILES` and `DATA_DIR_FILES`. Every matching string (`api_base_url`, the vendor hosts, `TYPESAFE_API_KEY`, `data/research`) sits in the research sections or the module docstring, which stay.
  - `tests/test_ready_pr.py` `_on_tree`. It globs `<prefix>*`, so a stem such as `src/tradepartner/config` is on the tree.
  - `tests/test_ready_pr.py` `targeted_tests` example, which maps `config.py`.
  - None of them needs an edit.
- (g) The review's caveat is backwards. `ready_pr.targeted_tests` does not skip a moved module: a `src/` module that no test names makes it fall back to the **full** suite. By default `ready_pr` runs no local pytest at all on a `src/` diff (#1130), and CI runs the full suite on every PR under `src/` (`TEST_TRIGGER_PREFIXES` has `src/`, which already covers the new files). Neither `TEST_TRIGGER_PREFIXES` nor `TEST_TRIGGER_FILES` needs a change.
- (h) R0, the edgar stem. `tests/test_ready_pr.py::test_edgar_source_requires_the_quant_audit` pins `edgar_source.py` to quant-auditor alone (#382, owner decision 2026-10-04). So `SAFETY_PREFIXES` keeps `src/tradepartner/adapters/edgar.py` as a file name. Only `QUANT_PREFIXES` widens to `src/tradepartner/adapters/edgar`, which adds `edgar_raw.py` and `edgar_validation.py` to the quant audit (open question 4).
- (i) R0, the cli stem. It covers `cli_*.py` but not R2's proposed `commands_<group>.py`. R2 needs its own prefixes, or modules named `cli_*`. That is R2's business, not this plan's.

**Lanes.**
- `src/tradepartner/config_data.py` is one declared chain of three, in order: T312, then T313, then T314. Each slice appends its block to the end of the file.
- `src/tradepartner/config.py` is named by T311 to T315. They touch disjoint parts: each deletes a different block of classes and adds its own import line. Whichever lands later merges main first (plan-shape rule 3).
- T310 has its own files. T309 has its own files.
- T315 can run beside T312 to T314, since its only dependency is T311.

**Plan shape numbers** (development-process, "Plan shape"; `uv run python scripts/team.py graph --ref docs/2512-config-split-plan` on 2026-10-28, this plan's tasks only): the numbers are in the PR body, which is refreshed after every amendment.
- Longest open chain: T310 → T311 → T312 → T313 → T314, 5 PRs.
- T313 also waits on T184h and T198, the post-span data sitting. T314 also waits on T171d and T305b.
- Depth 1: T309, T310.
- Files shared with open tasks of other plans: `src/tradepartner/config.py`, also named by T163b (alpaca, stays), T171d (borrow), T184h and T198 (edgar), T214 (a new `data` section) and T305b (events). The overlap is resolved by rule 6 and the `Depends on` lines.

**Owner items, ordered by what they unblock:** the open questions below. Question 1 gates T313 and T314. No owner task gates anything else.

## Tasks

Each task = one branch = one PR (~≤400 lines; a verbatim move counts its moved block once). Tasks with no shared files may run in parallel.
Shape (development-process.md, "Plan shape"): slice by file, not by step; no open chain over six PRs without a reason; no file named by more than two unticked tasks unless they are one chain; owner tasks gate only the edge, with the stub declared on the waiting line; the numbers above come from `uv run python scripts/team.py graph`.

- [ ] **T309: R0, review prefixes from file to stem.** (#2512; size S; class B, `scripts/`) Files: `scripts/ready_pr.py`, `tests/test_ready_pr.py` · Tests: `required_reviews` gives quant-auditor and safety-reviewer for `src/tradepartner/config_data.py`, `config_backtest.py`, `config_base.py` and `ingest_edgar.py`; quant-auditor alone for `health_rules.py` and `adapters/edgar_insider_bulk.py`; safety-reviewer alone for `cli_commands.py`; the existing edgar_source quant-only test and `test_every_review_prefix_exists_on_the_tree_unless_planned` pass unchanged · Depends on: n/a · Review: safety-reviewer. The change: in `QUANT_PREFIXES`, `config.py`, `health.py` and `ingest.py` become `src/tradepartner/config`, `/health` and `/ingest`, and `adapters/edgar.py` with `adapters/edgar_source.py` become `src/tradepartner/adapters/edgar`. In `SAFETY_PREFIXES`, `cli.py`, `config.py` and `ingest.py` become their stems, and `adapters/edgar.py` stays a file name (correction h). Every change widens a list, none shrinks one.
- [ ] **T310: R1 PR-A, golden tests for config (tests only).** (#2512; size M; class A) Files: `tests/test_config_golden.py`, `tests/fixtures/config_golden/` · Tests: (1) `Settings(_env_file=None).model_dump(mode="json")`, with the environment cleared of every `Settings` env name and the checkout root replaced by a placeholder, so any team's worktree gives the same result; (2) `Settings.model_json_schema()`; (3) `hypothesis.frozen_keys()`, in order; (4) `registry.params_sha256(hypothesis.frozen_params_of(settings, family=f))` for every `f` in `FAMILIES`; (5) the full env-var name set: every top-level field, and every nested `SECTION__FIELD` under the `__` delimiter; (6) the importable-name set: every top-level name `config.py` defines at this commit, private names included, each of which must stay an attribute of `tradepartner.config` (stdlib and pydantic imports are not pinned); (7) a synthetic `.env` that sets each name listed in `.env.example` to its current default (secrets to a dummy) loads, and dumps equal to (1), secrets aside. Each golden is a JSON file; `CONFIG_GOLDEN_UPDATE=1` rewrites them, and a PR that changes config on purpose rewrites them in the same PR, so the golden diff shows the change (open question 2) · Depends on: n/a · Review: quant-auditor.
- [ ] **T311: R1 PR-B, `config_base.py`.** (#2512; size S; class A) Files: `src/tradepartner/config_base.py` (new: `_ClosedConfig`, `_PHASE3_MODEL_CONFIG`), `src/tradepartner/config.py` · Tests: none new. The `tests/` diff is empty; T310's goldens and the full CI suite pass; `uv run mypy` passes · Depends on: T309, T310 · Review: quant-auditor, safety-reviewer (both by path). Follows the Move rules.
- [ ] **T312: R1 PR-C, Calendar, Store, Ingest and Rates to `config_data.py`.** (#2512; size S; class A) Files: `src/tradepartner/config_data.py` (new), `src/tradepartner/config.py` · Tests: as T311 · Depends on: T311 · Review: quant-auditor, safety-reviewer. Moves the T312 block of the module map. Follows the Move rules. First in the `config_data.py` chain.
- [ ] **T313: R1 PR-D, Edgar, Master and their parse helpers to `config_data.py`.** (#2512; size M; class A) Files: `src/tradepartner/config_data.py`, `src/tradepartner/config.py` · Tests: as T311 · Depends on: T312, T184h, T198 · Review: quant-auditor, safety-reviewer. Moves the T313 block. `config.py` imports `check_security_id` back for `AlpacaConfig`. It waits on the two sitting tasks that flip `edgar.*` defaults (Move rule 6); open question 1 asks whether to amend their lines instead. Follows the Move rules.
- [ ] **T314: R1 PR-E, the remaining data sections to `config_data.py`.** (#2512; size M; class A) Files: `src/tradepartner/config_data.py`, `src/tradepartner/config.py` · Tests: as T311 · Depends on: T313, T171d, T305b · Review: quant-auditor, safety-reviewer. Moves the T314 block, ending with `EventsConfig`. It waits on the open tasks that edit `borrow.*` and `events.*` (Move rule 6). Follows the Move rules. Last in the `config_data.py` chain.
- [ ] **T315: R1 PR-F, the backtest sections to `config_backtest.py`.** (#2512; size M; class A) Files: `src/tradepartner/config_backtest.py` (new), `src/tradepartner/config.py` · Tests: as T311 · Depends on: T311 · Review: quant-auditor, safety-reviewer. Moves the `config_backtest.py` block of the module map, `FAMILIES` together with `FamilySpec`. `backtest/frozen.py` and `backtest/hypothesis.py` keep importing from `tradepartner.config`. `PAPER_FAMILIES`, `ALLOWED_AXIS_PREFIXES` and `_DEFAULT_SWEEPABLE_KEYS` stay and import `FAMILIES`. `_settings_has_key`, which sits between `MetricsConfig` and `ScheduleConfig`, stays (correction d). Open question 5 asks whether to hold this task until the span closes. Follows the Move rules.

## Chains (for team claims)

| Chain | Tasks | Starts when |
|---|---|---|
| prefixes | T309 | this plan merges |
| goldens | T310 | this plan merges; beside T309 |
| base | T311 | T309 and T310 merged |
| data | T312 → T313 → T314 | T311 merged; T313 also after T184h and T198, T314 also after T171d and T305b |
| backtest | T315 | T311 merged; beside the data chain |

## Acceptance

1. After each of T311 to T315: `git diff main...HEAD -- tests/` is empty, the T310 goldens pass unchanged, the full CI suite is green, and `uv run mypy` is clean.
2. After T315: `config.py` holds only the names in the module map's stay-list, plus imports and re-exports. Every name in T310's importable-name golden is still importable from `tradepartner.config`. No new module imports `tradepartner.config` (checked with `grep -n 'tradepartner.config\b' src/tradepartner/config_*.py`).
3. `ready_pr.required_reviews` on each new module path gives quant-auditor and safety-reviewer (T309).

## Verification

1. Any checkout, after each slice: `uv run pytest tests/test_config_golden.py tests/test_config.py tests/test_llm_boundary.py tests/test_ready_pr.py -q` and `uv run mypy`; CI runs the full suite.
2. The reviewer reads the move with `git diff --color-moved=zebra main...HEAD -- src/` and checks that no line outside the import blocks is added or changed.

## Rollback

Each slice is a pure move, so reverting that one PR undoes it; later slices revert first (T314 before T313 before T312). The goldens (T310) and the stems (T309) can stay after a rollback: they hold on the unsplit tree too. No store, schema, frozen hash or environment variable changes at any step.

## Out of scope

- R2 (thin `cli.py` into framework-free command modules), R3 (split `health.py`), R4 (split `ingest.py`), R5 (split `adapters/edgar_source.py`), R6 (`ListingResolver` to `resolver.py`), R7 (after the span: `execution/reconcile_actions.py`, the paper CLI helpers, the execution config sections to `config_paper.py`). Each needs its own plan, on the owner's word.
- The review's do-not-refactor list:
  - `execution/` during the span, and `wrapper.py`'s allowlist after it (ADR 0010).
  - More than one typer module, or a `cli/` package (the `--accept-rejections` fence, #1312).
  - The research CLI and the research config (the LLM-boundary tests pin them).
  - `store/schema.py` and `store/registry.py`.
  - Merging `backtest/frozen.py` with `hypothesis.py`.
- A `config/` package; renaming any class, key or env var; reordering `Settings` fields, or relaxing its "last so no section shifts" comments; any behaviour change.

## Open questions for the owner

Ordered by what they unblock.
1. **T313 and T314 against the sitting tasks (gates T313, T314).** T184h and T198 flip `edgar.*` defaults in the post-span sitting (about 11-05). T171d (`borrow.*`) and T305b (`events.*`) are open now. The default is that T313 and T314 wait for them: no amendment, and R1 finishes after the sitting. The alternative: T313 and T314 land first, and their PRs amend those four lines to name `config_data.py`. That alternative makes T313 and T314 class B. Also: if T314's gates clear before T313's, may the chain run T314 before T313, by a one-line amendment?
2. **Goldens after R1 (affects T310's line).** The default: they stay, and any PR that changes a config default rewrites them in the same PR with `CONFIG_GOLDEN_UPDATE=1`. That costs every config PR one regenerated file, and shows the reviewer the exact change. The alternative: delete them in a size-S PR once T315 lands.
3. **Where new sections go after T315 (no task gated).** The default: in the module of their domain, with the `Settings` field in `config.py`. The alternative: always in `config.py`, moved later in batches. Pending sections such as T214's `data` would follow the answer.
4. **R0's edgar stem (affects T309).** The quant stem adds `edgar_raw.py` and `edgar_validation.py` (and T305b's new `edgar_insider_bulk.py`) to the quant audit. Safety stays as today. Accept, or keep the edgar entries as file names until R5?
5. **T315 during the span (affects T315).** `execution/` reads `CostsConfig`, `Cadence`, `HypothesisFamily` and `FAMILY_SIGNAL_SECTIONS`, and `FAMILIES` holds the `paper_ready` flags. All of them move verbatim and are re-exported, so the paper path runs the same code. The default is that T315 does not wait for the span to close; hold it until then if you prefer.
