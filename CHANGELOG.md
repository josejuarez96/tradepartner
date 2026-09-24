# Changelog

Format: [Keep a Changelog](https://keepachangelog.com). Versions are tagged at the end of each phase (`v0.<phase>.0`).

## [Unreleased]
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
