# Plan: Technical hardening (review of 2026-11-20)

**Spec:** none. Every item here is size S or M, so the spec-lite is each issue's own body (Why, Confirm first, Change, Tests, Acceptance, Out of scope), as development-process "Right-size the ceremony" allows. No ADR is written here; the six choices that would need one are filed as owner decisions below · **Status:** Draft · **Issue:** #3618 (tracker) · **Source:** the technical review of 2026-11-20 (nine reviewers, main `0c4d7f9f`), the engineering-health companion to the functional review of the same night (#3523).

## Approach (short)

The review graded nine dimensions. Execution, store, backtest, security, CI and cross-cutting quality came out at 4 of 5; adapters and ingest at 3.5; architecture and the test suite at 3. The things that are hard to retrofit are in place (point-in-time constraints in the schema, one transactional write gate, write-ahead order intent, one submit site, a hard-wired paper endpoint, deterministic trials). The debt is structural: four files of 5,000 to 8,700 lines, no enforced layering, 395 SQL `execute` sites outside `store/`, and a 19,214-test suite that costs about 394 CPU-minutes and patches 380 private names. Main took 1,516 commits in eight weeks, of which 15 were refactors.

#3523 filed 84 issues the night before. This plan files only what those do not cover (48 issues) and gives each size-M build its task line (T436 to T446). Size-S issues need no task (CLAUDE.md rule 2) and are listed by lane.

**Four rules for the order of work.**
1. **Fix what hurts the scheduled runs first** (lane H1): they are small and independent.
2. **Ratchets before refactors** (lane H2). Each ratchet is a test with today's counts as a committed baseline, in the style of #3597: it changes no source, stops the debt growing, and makes each later refactor show its effect as a shrinking baseline.
3. **Splits are verbatim moves at a quiet point** (lane H4), under the [config-split move rules](config-split.md): moved lines reappear unchanged, the old module re-exports every name, and a pin test proves nothing was lost. A split lands last in a batch, after the open PRs on that file, and is replayed after merging main, never hand-resolved.
4. **No order-path behaviour changes on an agent's judgment.** The three order-path choices (lane H3) are owner decisions; the builds that follow them are not planned here. Three tasks edit order-path files without changing what an order does: T442 (moves no command line), T443 (import timing only) and T445 (one error type in `round_down`, see its line).

**What #3523 already covers** (not re-filed; this plan's issues name them under "Depends on"):

| Finding of this review | Covered by |
|---|---|
| The engine checks only that it holds a `TrialHandle`, not the window or the holdout | #3533 (PR #3616), #3568 |
| The kill switch depends on the store it guards | #3577 (ADR 0027), #3578 |
| A late-landing order can be submitted twice after `not_received` | #3575 (PR #3613) |
| The halt skips confirmed `pending_unresolved` orders | #3579 |
| Retries differ per vendor | #3542, #3543 |
| Row-at-a-time revision writes | #3528 |
| No layering check; duplicated SHA-256 helpers | #3597, #3596 |
| `scripts/` is outside mypy | #3599 |
| Suite cost on PRs; the fixture store rebuilt per test | #3598, #3600, #3347 |
| `cli.py`: flat help, and `empyrical` at import | #3592, #3593 |
| `reconcile` classification, the signed book and the restamp tools inside larger modules | #3583, #3565, #3557 |
| One redaction path | #3590 |

**What the drafting pass corrected in the review** (each issue carries the measured figure):
- The read-write store handle held across a fetch pass ({{B-02}}) opens only for `--restamp`, `--rebuild-classifications` and `master-retract`, not for the nightly ingest.
- `cli_paper.py` as a sibling typer module is not possible today: `tests/execution/test_accept_rejections_boundary.py` forbids a second typer module and pins `paper resume` to `cli.py`, and [config-split](config-split.md) lists it as do-not-refactor. {{D-01}} therefore splits `make_app` in place. The same holds for the later slices #3592 lists.
- A transition check in `journal.append` would break flows that append after a terminal event on purpose (`cancel_noop` after `expired`; the halt's `cancel_failed`), so {{A-06}} is the enums only.
- A fresh store and a migrated store differ today: 71 tables against 79, with no lab tables in a fresh one ({{C-01}} pins the difference and files the follow-ups).
- The secret scan finding holds ({{F-01}}): the hook is `gitleaks protect --staged`, which scans zero commits in CI. A full scan of all 1,515 commits finds no leak.
- Four numeric functions return NaN or raise on a non-finite input today (`trade_cost`, `buy_notional_after_costs`, `expected_max_sharpe`, `round_down`); {{E-03}} makes them refuse.

## Lanes

Issue numbers are the build issues; "→" is merge order. A lane is one team at a time where it shares a file.

**H1. Scheduled runs and secrets (do first).**
- {{A-01}} a failed paper command prints its scrubbed traceback (prio 1) → {{A-02}} halt notes keep the message.
- {{B-01}} a timeout on the Alpaca data, actions and assets clients (prio 1); land before #3542 and #3546.
- {{F-01}} a real secret scan in CI (prio 1, class B).
- {{E-05}} the test process never loads the real `.env` (prio 2).
- {{C-04}} a durable, hashed, test-opened store backup (prio 2).
- {{B-02}} the repair's stored-index handle opens read-only (prio 2).
- {{A-04}} the run lock retries briefly; {{F-04}} owner-only store files.

**H2. Ratchets (no source change; land early, regenerate each baseline on the batch's final main).**
- {{C-01}} fresh store against migrated store.
- {{C-05}} SQL `execute` sites outside `store/`.
- {{E-04}} the tests' use of private names.
- T437 the lint ratchet (class B) and {{E-01}} the `slow` tier (class B).
- {{E-02}} weekly coverage on main (class B), {{F-02}} action pins, dependabot, audit and job timeouts (class B).

**H3. Order path and live readiness.**
- T436 the broker contract suite: tests only, no order-path source.
- {{A-06}} one `StrEnum` module for statuses, values unchanged.
- Decisions: {{A-09}}, {{A-07}}, {{A-08}} (below). No build follows from them in this plan.

**H4. Structure (verbatim moves; each waits for its quiet point).**
- `cli.py`: #3592 → T442 → {{D-02}}; T443 after #3593.
- `ingest.py` and the EDGAR source: PR #3609 (#3595) → {{B-06}}; #3550 → T439 → {{B-07}}; {{B-08}}; {{B-04}}.
- `store/schema.py`: {{C-01}} → T440 → T441, all after #3531 is decided.
- `store/` as a bottom layer: #3528 → {{C-06}}; #3597 → {{C-07}}; {{C-08}}.
- `health.py`: #3553, #3556 → T444. {{D-05}} moves `home_view` to the dashboard.
- Backtest: {{D-06}} the `marking_anchor` hook; {{D-07}} the duplicated helpers.

**H5. Observability.** #3590 → T438 the run log (after {{A-01}}).

**H6. Tests.** T445 property tests (class B: a new dev dependency); {{E-06}} injected clocks for the lock-retry tests.

**H7. Research.** {{F-08}} → T446 the labeling resume. {{F-05a}} and {{F-05b}} are two small defence-in-depth fixes.

**Shared files (plan-shape rule 3).** Among this plan's open tasks: `src/tradepartner/cli.py` is named by T438, T442 and T446 on disjoint parts (T438 `main()` and the paper and ingest entry lines; T442 the body of `make_app`, moving no command line; T446 one option on `research label`), T442 last; T442 re-adds `make_app` just above `main()`, next to T438's hunk, so the later of the two replays its edit on main. T437 adds comment-only `# noqa` lines to 52 files, `cli.py`, `ingest.py`, `health.py` and files under `execution/` among them: it shares those files with every task here and in other plans, on comment text only, and its comments are regenerated with `ruff --add-noqa`, never merged by hand. A task that lands after T437 and moves or creates an oversized function (T442's `_register_<group>` functions, T441, T444) runs `uv run ruff check --add-noqa` on the files it touched and removes any comment ruff reports as unused; `src/tradepartner/store/schema.py` by T440 and T441, a declared chain in that order; `pyproject.toml` by T437 and T445 on disjoint blocks (`[tool.ruff.lint]`; the dev group); `src/tradepartner/ingest.py` by T439 alone; `src/tradepartner/health.py` by T444 alone. Against other plans' open tasks: `docs/runbooks/scheduling.md` (T438 adds one section beside T22, T163b, T171d and T377b, disjoint); `src/tradepartner/backtest/quiet.py` (T443's import lines beside T377b's window rule, disjoint); `cli.py` (named by more than thirty open tasks of other plans, T377b, T381a, T400 and T401 and #3578 among them: `team.py graph` FILE CONTENTION is the list, and each adds or edits its own command), `ingest.py` (#3524, #3550, #3554), `health.py` (T403, #3537, #3554), `schema.py` (T401, T402, T403, #3527), `edgar_source.py` (#3524, #3550, #3548), `pyproject.toml` (#3598). In every case whichever lands later merges main first, and a verbatim move replays its move on the new main.

## Tasks
Each task = one branch = one PR. The issue is the full brief; the line below is its summary.

- [ ] **T436: One broker contract suite run against FakeBroker and AlpacaBroker ({{A-05}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `tests/adapters/broker_contract.py`, `tests/adapters/test_broker_contract.py` · Tests: `tests/adapters/test_broker_contract.py`, every case parametrised over `fake` and `alpaca`; `uv run pytest tests/adapters/test_broker_contract.py tests/adapters/test_fake_broker.py tests/adapters/test_alpaca_broker.py` · Depends on: n/a · Review: none · Done: a behavioural difference between the two brokers is filed as its own issue and marked `xfail(strict=True)` with that number, never fixed in this PR.
- [ ] **T437: Ratchet blind excepts, prints in `src` and function size with ruff; today's 138 lines grandfathered by per-line `noqa` ({{F-03}}).** (size M; class B: `pyproject.toml`; merges on the owner's word; technical review 2026-11-20, tracker #3618) · Files: `pyproject.toml`, `tests/test_lint_ratchet.py`, comment-only `# noqa` lines in 52 files under src, scripts and tests (the issue lists them) · Tests: `tests/test_lint_ratchet.py::test_the_ratchet_rules_are_selected_with_the_measured_limits`, `::test_a_new_blind_except_and_an_oversized_function_fail`; `uv run pytest tests/test_lint_ratchet.py` and `uv run ruff check .` · Depends on: n/a (merge order with #3599 on `pyproject.toml`; built last in a batch and its comments regenerated with `ruff --add-noqa` after merging main) · Review: quant-auditor, safety-reviewer (by path; the diff is comments only).
- [ ] **T438: A dated run log file with UTC time, command, book and run id, secrets filtered ({{A-03}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/runlog.py`, `src/tradepartner/config_data.py`, `src/tradepartner/config.py`, `src/tradepartner/cli.py`, `docs/runbooks/scheduling.md`, `tests/test_runlog.py`, `tests/test_cli_paper.py`, `tests/test_cli.py`, `tests/test_config.py` · Tests: `tests/test_runlog.py::test_a_configured_secret_never_reaches_the_log_file`, `::test_each_line_carries_utc_time_command_book_and_run_id`, `::test_files_older_than_retention_days_are_removed`, `tests/test_cli_paper.py::test_paper_run_writes_entry_and_exit_lines_to_the_run_log`; `uv run pytest tests/test_runlog.py tests/test_cli_paper.py tests/test_cli.py tests/test_config.py` · Depends on: n/a · Review: quant-auditor, safety-reviewer · **Parked 2026-11-20** until #3590 (the one redaction module the filter is built on) and {{A-01}} merge; the PR that merges the later of them, or a one-line amendment, un-parks it. `.env.example` gets no line: the default directory needs no setting, which keeps the task class A.
- [ ] **T439: A `FailureReporting` protocol replaces `ingest.py`'s private failure-state probes, with a ratchet test ({{B-03}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/adapters/filings.py`, `src/tradepartner/adapters/edgar_source.py`, `src/tradepartner/ingest.py`, `tests/test_ingest_source_access.py`, `tests/test_ingest.py`, `tests/test_retract.py`, `tests/test_backfill.py`, `tests/lookahead/test_incremental_edgar.py`, `tests/adapters/test_edgar_failures.py` · Tests: `tests/test_ingest_source_access.py::test_private_source_access_in_ingest_is_capped`, `::test_edgar_source_satisfies_failure_reporting`; `uv run pytest tests/test_ingest_source_access.py tests/test_retract.py tests/test_backfill.py tests/adapters/test_edgar_failures.py tests/test_ingest.py` · Depends on: n/a · Review: quant-auditor, safety-reviewer · **Parked 2026-11-20** until #3550 merges (it rewrites the failure policy these probes serve); un-parked by a one-line amendment.
- [ ] **T440: One ordered migration registry in `store/schema.py`; `init_schema(conn, *, allow_migration=False)` ({{C-02}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/store/schema.py`, `src/tradepartner/store/migrate.py`, `tests/store/test_migration_registry.py`, `tests/store/golden/migration_call_order.json`, `tests/store/test_lab_schema.py`, the ten `tests/store/` version-pin files · Tests: `test_the_call_order_matches_the_golden`, `test_the_supported_versions_are_todays`, `test_migrate_passes_allow_migration_and_leaves_the_global`; `uv run pytest tests/store/test_migration_registry.py tests/store/test_schema_equivalence.py tests/store/test_migrate.py tests/store/test_schema.py tests/store/test_lab_schema.py tests/store/test_lab_migration.py` · Depends on: n/a · Review: quant-auditor · **Parked 2026-11-20** (owner decision #3531: a raised version floor deletes registry entries, so nobody builds before it is answered) and until {{C-01}} merges, whose equivalence test is half the proof; un-parked by a one-line amendment that names the decided floor. The issue lists the full test and run set.
- [ ] **T441: Move `store/schema.py`'s enums and DDL constants to `schema_enums.py` and `schema_ddl.py`, verbatim, every name re-exported ({{C-03}}).** (size M; class A; a verbatim move of about 2,250 lines, over the 400 bound on purpose; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/store/schema_enums.py`, `src/tradepartner/store/schema_ddl.py`, `src/tradepartner/store/schema.py`, `tests/store/test_schema_names.py`, `tests/store/golden/schema_names.json`, `tests/execution/test_boundaries.py`, `tests/lookahead/test_check_only_inputs.py` · Tests: `test_every_name_and_value_matches_the_golden`, `test_the_new_modules_never_import_schema`; `uv run pytest tests/store/test_schema_names.py tests/store/test_schema_equivalence.py tests/store/test_schema.py tests/execution/test_boundaries.py tests/lookahead/test_check_only_inputs.py` · Depends on: T440 · Review: quant-auditor · Also after {{C-01}} and #3531, through T440. The issue lists the full test and run set.
- [ ] **T442: Split `cli.make_app` in place into nine `_register_<group>(app, edges)` functions ({{D-01}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/cli.py`, `tests/test_cli_tree.py`, `tests/fixtures/cli_tree.json` · Tests: `test_the_command_tree_is_pinned`, `test_make_app_only_delegates`, `test_each_group_reads_only_its_edges`; `uv run pytest tests/test_cli_tree.py tests/execution/test_accept_rejections_boundary.py tests/test_cli_paper.py -q` · Depends on: n/a (after #3592, merge order; last in its batch) · Review: safety-reviewer · If T437 has merged, the new `_register_<group>` functions are over its size limits: regenerate the comments with `uv run ruff check --add-noqa` on `cli.py` and remove the one on `make_app` that ruff then reports as unused.
- [ ] **T443: CLI start without pandas, `exchange_calendars` or the Alpaca SDK ({{D-03}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/calendar.py`, `src/tradepartner/backtest/quiet.py`, `src/tradepartner/adapters/alpaca_prices.py`, `src/tradepartner/adapters/alpaca_raw.py`, `src/tradepartner/adapters/alpaca_trading_raw.py`, `src/tradepartner/cli_record.py`, `tests/test_cli_startup.py` · Tests: `test_importing_the_cli_loads_no_pandas_stack`, `test_a_calendar_call_still_loads_it`; `uv run pytest tests/test_cli_startup.py tests/test_calendar.py tests/adapters/test_alpaca_trading_raw.py tests/execution/test_sdk_boundary.py tests/execution/test_boundaries.py -q` · Depends on: n/a · Review: quant-auditor, safety-reviewer · **Parked 2026-11-20** until #3593 merges (`empyrical` imports pandas, so the new test cannot pass before it); un-parked by a one-line amendment.
- [ ] **T444: `health.py` split slice 1, the report types to `health_reports.py` ({{D-04}}).** (size M; class A; a verbatim move of about 830 lines; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/health_reports.py`, `src/tradepartner/health.py`, `tests/test_health_split.py` · Tests: `test_health_re_exports_every_moved_name`, `test_health_reports_does_not_import_health`; `uv run pytest tests/test_health_split.py tests/dashboard/test_health_page.py tests/execution/test_home_view.py -q` · Depends on: n/a (after #3553 and #3556, merge order) · Review: quant-auditor · Note: the fence task and the trial-ledger task of other plans (#3475, #3537) add blocks to `health.py`; whichever lands later merges main first.
- [ ] **T445: Property-based tests for the pure numeric core, first slice ({{E-03}}).** (size M; class B: it adds the dev dependency `hypothesis` to `pyproject.toml` and `uv.lock` and edits `.gitignore`; merges on the owner's word; technical review 2026-11-20, tracker #3618) · Files: `pyproject.toml`, `uv.lock`, `.gitignore`, `tests/properties/conftest.py`, `tests/properties/test_prop_metrics.py`, `tests/properties/test_prop_costs.py`, `tests/properties/test_prop_risk.py`, `tests/properties/test_prop_adjustment.py`, `src/tradepartner/backtest/costs.py`, `src/tradepartner/backtest/metrics.py`, `src/tradepartner/execution/risk.py`, `tests/backtest/test_costs.py`, `tests/backtest/test_metrics.py`, `tests/execution/test_risk.py` · Tests: the 12 properties P1 to P12 and `test_nothing_shadows_the_hypothesis_package`; `uv run pytest tests/properties tests/backtest/test_costs.py tests/backtest/test_metrics.py tests/execution/test_risk.py -q` · Depends on: n/a · Review: quant-auditor, safety-reviewer · Done: the four functions that return NaN or raise the wrong error on a non-finite or out-of-range input today refuse it with `ValueError`; no backtest golden, fixture or stored metric changes. Order path: the `risk.py` edit only turns `round_down`'s `decimal.InvalidOperation` into the `ValueError` its callers already handle; the PR names each caller and what it does on `ValueError`, and if any caller would behave differently the guard is left out of this PR and filed as its own issue for `safety-reviewer`.
- [ ] **T446: `research label --resume-from <run_id>` reuses a failed run's ok answers and pays only for new calls ({{F-06}}).** (size M; class A; technical review 2026-11-20, tracker #3618) · Files: `src/tradepartner/research/labeling/job.py`, `src/tradepartner/cli.py`, `tests/research/labeling/test_job.py`, `tests/test_cli_research.py` · Tests: `tests/research/labeling/test_job.py::test_resume_reuses_ok_records_and_charges_only_new_calls`, `::test_resume_refuses_a_different_model_option_set_or_dataset`, `::test_resume_never_reuses_a_non_ok_or_mismatched_record`, `::test_resume_stops_before_any_call_when_a_packet_changed`; `uv run pytest tests/research/labeling/test_job.py tests/test_cli_research.py tests/test_llm_boundary.py` · Depends on: n/a (after {{F-08}}, merge order: the same functions in `job.py`) · Review: quant-auditor, safety-reviewer · **Parked 2026-11-20** (owner: it adds the field `reused_from_record_id` to the per-call record that [research-labeling spec](../specs/research-labeling.md) C8 defines; C8 is amended first, on the owner's word, then a one-line amendment un-parks this).

**Plan shape (rule 8).** `uv run python scripts/team.py graph --ref ''` before (main at `0c4d7f9f`): longest open chain 8 (T377 → T377a → T378 → T379 → T380 → T371 → T372 → T373); depth 1 has 45 tasks, depth 2 has 21. After: the longest chain is unchanged (8); depth 1 has 55 (T436, T437, T438, T439, T440, T442, T443, T444, T445 and T446 join it) and depth 2 has 22 (T441). The only chain this plan adds is T440 → T441, two links. The size-M lines of #3610 (the functional review's tasks) are not on main yet, so this plan names their issues, not their task ids; T439 follows #3550's task once it lands. Eleven tasks: nine class A, two class B (T437, T445). Five are parked on their lines because `team.py` reads only task ids as dependencies and theirs are issues or decisions: T438 (#3590), T439 (#3550), T440 (the #3531 decision, which also holds T441 behind it), T443 (#3593) and T446 (the C8 amendment). Ready on merge of this plan: T436, T437, T442 (after #3592), T444 (after #3553 and #3556) and T445. No owner task: the owner's items are decisions and class-B merges, listed below, and none gates the middle of a chain (rule 4).

## Size-S issues (no task line; Issue → PR)

| Lane | Issue | What | Class | Prio |
|---|---|---|---|---|
| H1 | {{A-01}} | A failed paper command prints its scrubbed traceback to stderr | A | 1 |
| H1 | {{B-01}} | A request timeout on the Alpaca data, actions and assets clients | A | 1 |
| H1 | {{F-01}} | CI scans each PR's commit range and main's history for secrets | B | 1 |
| H1 | {{E-05}} | The test process never loads the real `.env` | A | 2 |
| H1 | {{C-04}} | A durable, hashed, test-opened store backup; crashed copies swept | A | 2 |
| H1 | {{B-02}} | The repair's stored-index handle opens read-only for the fetch pass | A | 2 |
| H1 | {{A-02}} | Halt and failed-path notes keep the scrubbed message | A | 3 |
| H1 | {{A-04}} | The paper run lock retries briefly, so a status probe cannot make a run exit locked | A | 3 |
| H1 | {{F-04}} | The store, backups and archive are created owner-only | A | 3 |
| H7 | {{F-08}} | A labeling batch stops on the first HTTP 400 and charges nothing for it | A | 3 |
| H2 | {{C-01}} | A test that a fresh store has the shape of a migrated one, today's differences as a baseline | A | 2 |
| H2 | {{C-05}} | A ratchet on SQL `execute` sites outside `store/` | A | 3 |
| H2 | {{E-04}} | A ratchet on the tests' use of private names | A | 3 |
| H2 | {{E-01}} | A `slow` marker, strict markers, heavy files auto-marked | B | 3 |
| H2 | {{E-02}} | A weekly branch-coverage run on main, no gate | B | 3 |
| H2 | {{F-02}} | Actions pinned by SHA, dependabot, a weekly audit, a timeout on every job | B | 3 |
| H3 | {{A-06}} | One `StrEnum` module for order-event and decision statuses, values unchanged | A | 3 |
| H4 | {{B-04}} | Name-change and merger fetches tolerate only transient errors; a typed report | A | 3 |
| H4 | {{B-06}} | The ingest run framework moves to `ingest_runs.py` | A | 3 |
| H4 | {{C-06}} | The fact revision writer moves to `store/facts_write.py` | A | 3 |
| H4 | {{D-06}} | A `Strategy.marking_anchor` hook, so the engine names no family | A | 3 |
| H4 | {{B-07}} | The failed-filings store is extracted from `EdgarFilingSource` | A | 4 |
| H4 | {{B-08}} | The EDGAR filing-date clock rule moves to a leaf module | A | 4 |
| H4 | {{C-07}} | `identity_check.py` moves out of `store/` | A | 4 |
| H4 | {{C-08}} | `settings` is required in four `store/asof.py` readers; a ratchet on `get_settings()` | A | 4 |
| H4 | {{D-02}} | The exit codes are named in two `IntEnum`s, values unchanged | A | 4 |
| H4 | {{D-05}} | `home_view` moves out of `execution/` | A | 4 |
| H4 | {{D-07}} | One definition for the helpers `sweep.py` and `lab.py` copy | A | 4 |
| H6 | {{E-06}} | The lock-retry tests take an injected clock and sleep | A | 4 |
| H7 | {{F-05a}} | Each model API base URL accepts only the vendor's own host | A | 4 |
| H7 | {{F-05b}} | Stored reconciliation and delisting text renders as plain text | A | 4 |

## Owner items, by what they unblock (plan-shape rule 7)

**Decisions** (each decision issue ends with one question):

| Issue | Question | Unblocks |
|---|---|---|
| {{A-09}} (prio 1) | Do the paper/live boundary and order-path files become class B? | Who may land a change to the `paper=True` literal, the one submit site and the paper guard. One line in `scripts/merge_train.py` and CLAUDE.md rule 1. Decide with #3604. |
| {{A-07}} (prio 2) | Should `paper kill` cancel the book's open broker orders? | A build issue after #3577, #3578 and #3579; a precondition line in the Phase 6 live ADR (#2124). |
| #3531 (already filed) | A schema-version floor? | T440 and T441 start from the decided floor. |
| {{A-08}} (prio 3) | Float or Decimal for money and quantities before live? | The live ADR's amounts (#3581); a journal migration if Decimal is chosen. |
| {{B-05}} (prio 4) | Which source adopts raw-archive-first next? | A keeper at the `alpaca_raw` or `fred_raw` boundary; ADR 0028's terms check for Alpaca. |
| {{F-06}} (prio 3) | May the per-call record (labeling spec C8) gain `reused_from_record_id`? | T446, parked until C8 is amended. |
| {{F-07}} (prio 4) | Delete the unreachable Claude API client, or keep it? | Half of {{F-05a}}. |
| {{D-08}} (prio 4) | Do docstrings state only the contract, with history in commits and ADRs? | Whether the H4 moves trim the 11,800 lines of module docstrings as they go. |

**Class-B builds** (an agent can ready them; each merges on your word): {{F-01}}, T437 ({{F-03}}), {{E-01}}, {{E-02}}, {{F-02}}, T445 ({{E-03}}), and this plan.

**Operator steps** are on each issue. The ones that matter first: after {{A-01}} merges, the scheduled checkout must be pulled for `paper.err.log` to carry tracebacks (#3558); after {{F-04}}, a one-off `chmod` of the existing store files; after {{C-04}}, one fresh backup.

**For the Phase 6 live ADR (#2124), from this review.** Not planned here; listed so the ADR can name them as preconditions: kill and halt can cancel at the broker ({{A-07}}, #3579); the stop does not depend on the store (#3577, #3578); the contract suite is green on both brokers (T436); a decided money type ({{A-08}}); a positive check at start-up that the account is the paper account, and distinct variable names for live keys; the order path lands only on the owner's word ({{A-09}}).

## Not filed

Dropped as nits under plan-shape rule 6, or left to a later slice named on an issue: a common `TradePartnerError` root for the 188 exception classes; DuckDB sequences in place of `MAX(id)+1`; primary keys on `order_events` and `decision_events`; a `tests/support/` builders package for the 69 copies of `_settings`; moving the process-tooling tests to `tests/tooling/`; `scripts/repair_stamps.py` into the package; a `dashboard` extra and an `oracle` dependency group; the version string; `raw_rederive.py` out of `store/`; the remaining `ingest.py`, `EdgarFilingSource`, `health.py` and migration slices (each named under "Out of scope" on {{B-06}}, {{B-07}}, T444 and T441).

## Verification

- Each issue's "Confirm first" test fails on main and passes on its branch; that pair is in the PR body.
- Each ratchet's baseline only shrinks: `tests/test_sql_boundary.py`, `tests/test_private_coupling.py`, `tests/test_lint_ratchet.py`, the schema-equivalence baseline and #3597's layering baseline are the numbers to read at the next review.
- Each verbatim move shows `git diff --color-moved=zebra main...HEAD -- src/` with no changed line apart from imports, and its pin test (the command tree, the schema names, the health re-exports) passes.
- The numbers this review measured, to compare against later: files over 1,500 lines, 26; functions over 100 lines, 155; private-name patches in tests, 380; SQL `execute` sites outside `store/`, 395; `import tradepartner.cli`, 2.56 s; the fast tier's share of suite CPU, 22 %.

## Rollback

Every task and issue is one PR and is undone by reverting it. None migrates the store: T440 reorders how migrations are listed and keeps today's call order (a golden pins it), and T441 moves constants. The class-B CI changes ({{F-01}}, {{F-02}}, {{E-02}}) add steps and a scheduled workflow; reverting removes them. The `noqa` comments of T437 are inert without the `pyproject.toml` selection.
