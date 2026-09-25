# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions are tagged at the end of each phase (`v0.<phase>.0`).

## [Unreleased]
### Added
- Phase 2 T1: pydantic settings with every ADR 0006 threshold (guarded SIC exclusion), pinned XNYS calendar wrapper, all Phase 2 runtime dependencies (#17).
- Phase 2 T4: DuckDB store schema for every table in the data-foundation spec (common `known_at`/`ingested_at`/`source`/`provenance` columns, provenance and timing `CHECK` constraints, uniqueness per revision), idempotent `init_schema`, `store.db` read-only/write-with-retry connection layer (`StoreLockedError`, tz-aware validation), `adapters/__init__.py`, and the shared `tests/conftest.py` (autouse no-network fixture, `fixture_store`, `settings`) (#19).
- Phase 2 T2: thin raw-fetch clients for Alpaca (`alpaca_raw`) and SEC EDGAR (`edgar_raw`), returning JSON-serializable raw payloads only; the owner-run fixture recorder (`cli_record`) with a scrub pass for secrets/emails/`User-Agent` headers; the scrub pattern test; network smoke tests for both clients (#21).

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
