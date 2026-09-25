# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions are tagged at the end of each phase (`v0.<phase>.0`).

## [Unreleased]
### Added
- Phase 2 T1: pydantic settings with every ADR 0006 threshold (guarded SIC exclusion), pinned XNYS calendar wrapper, all Phase 2 runtime dependencies (#17).
- Phase 2 T4: DuckDB store schema for every table in the data-foundation spec (common `known_at`/`ingested_at`/`source`/`provenance` columns, provenance and timing `CHECK` constraints, uniqueness per revision), idempotent `init_schema`, `store.db` read-only/write-with-retry connection layer (`StoreLockedError`, tz-aware validation), `adapters/__init__.py`, and the shared `tests/conftest.py` (autouse no-network fixture, `fixture_store`, `settings`) (#19).
- Phase 2 T2: thin raw-fetch clients for Alpaca (`alpaca_raw`) and SEC EDGAR (`edgar_raw`), returning JSON-serializable raw payloads only; the owner-run fixture recorder (`cli_record`) with a scrub pass for secrets/emails/`User-Agent` headers; the scrub pattern test; network smoke tests for both clients (#21).
- Phase 2 T20: abstract `Broker` interface (`submit`/`cancel`/`positions`/`fills`) and an in-memory `FakeBroker` with duplicate-`client_order_id` rejection, explicit cancel/fill state transitions and net position aggregation; no risk logic (#27).
- Phase 2 T5: deterministic fixture-universe generator (`scripts/make_fixture_universe.py`) and its committed CSVs covering every spec req 13 case (delistings incl. truncated/window/clean/25-NSE/transfer, dual-class, ticker changes and reuse, splits, revised dividend, restated/stale shares, unclassifiable name, pre-2019 static listing, holiday/half day, SPY/MTUM benchmarks), with `tests/fixtures/universe/README.md` mapping each case to its rows (#22).

### Changed
- `insert_row` now binds the UTC-normalized value for `TIMESTAMPTZ` columns (one canonical stored form) instead of the caller's original tzinfo, and `ensure_tz_aware_utc` re-raises the `OverflowError` from `.astimezone(UTC)` near `datetime.min`/`datetime.max` as `ValueError` naming the field (#43).
- Process: multi-team orchestration. Any number of chat windows build in parallel as registered teams, each in its own clone; work is claimed on GitHub issues through `scripts/team.py` with a deterministic tiebreak; plans list chains; CI fails a PR whose issue is unclaimed or whose plan task has two open PRs; model tiers documented (#36).
- Process: `scripts/team.py start <name>` sets up a team directory outside the repo in one step; `register` refuses to overwrite another team's `.team`; sessions touch only their own directory (#40).
- Tooling: owner cockpit, `scripts/cockpit.py`, renders one local HTML page from GitHub claims and PRs, the plan on `origin/main`, the roadmap and local Claude Code session logs: teams with activity state, tokens and models, claims and PR state, roadmap phase, plan by chain, unclaimed queue (#65).
- `store.db.ensure_tz_aware` and the broker value objects share one tz-aware UTC check, `tradepartner.timeutil.ensure_tz_aware_utc`, which also rejects a `tzinfo` with no UTC offset; `ensure_tz_aware` now returns the value converted to UTC (#30).

## [0.1.0] - 2026-09-24
Phases 0 and 1: foundations, charter and decisions.

### Added
- Project scaffold: uv + ruff + mypy + pytest, CI, pre-commit, GitHub templates.
- Ways of working: git workflow, development process, doc map, templates, ADRs 0001–0002.
- Build agents: researcher, spec-critic, implementer, quant-auditor, safety-reviewer, doc-keeper.
- Draft charter, roadmap, STATUS, trial registry.

### Added (Phase 1)
- Charter accepted. ADR 0005: objective (learning and process), SPY/MTUM benchmarks, stop criteria on time, spend and integrity. ADR 0006: universe (top 1000 by cap, liquidity and price floors, utilities excluded, point-in-time) and monthly cadence.
- ADR 0003: data and broker access through adapters, local-first, paid vendor deferred to Phase 3.
- ADR 0004: tooling we own, adopt and avoid, from a 2026-09-24 repo survey; `bt` as a test-only oracle for our engine.

### Changed
- Roadmap: MVP scope (12-1 momentum vs SPY/MTUM), deferred items, and a UX slice per phase; charter scope updated to match.
- The main session may squash-merge a PR when the owner explicitly says to. Subagents never merge.
- Phase 2 spec and plan (data foundation), 24 tasks. Phase 1 retro: no stacked PRs, CI on the exact commit, bookkeeping inside the PR.
