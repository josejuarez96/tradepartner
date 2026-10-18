- Docs pass W1 (#1751): CLAUDE.md, agent files and the ready-pr skill rewritten shorter with the same rules; scripts/doc_check.py adds the citation check (enforced by a test) and warn-only size budgets.
### Changed
- Always-loaded instructions (CLAUDE.md, .claude/agents, ready-pr skill, STATUS layout) shortened with every rule kept or linked to its one home; `scripts/doc_check.py cite` checks that task, ADR, claim and requirement citations resolve, and `doc_check.py size` warns on per-file size budgets (#1751)
