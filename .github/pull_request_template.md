## What & why
<!-- One or two sentences. PR title must be a Conventional Commit: feat(scope): ... -->

Closes #

## Spec / plan
<!-- Link the spec + plan task this implements, or "n/a (size S)" -->

## How it was verified
- [ ] `uv run pytest` passes; new behavior has tests
- [ ] Ran it for real (command/output or screenshot below), not just tests

## Review checklist
- [ ] Scope matches the plan task — nothing extra
- [ ] No secrets, data files, or notebooks with outputs committed
- [ ] Docs updated (STATUS/CHANGELOG fragment via `scripts/fragments.py add`, spec/plan checkboxes, `.env.example`, ADR if a decision was made)
- [ ] Touches data/backtest/signals → `quant-auditor` run, findings addressed, verdict posted as a PR comment (`quant-auditor: PASS` / `PASS WITH FIXES`)
- [ ] Touches broker/orders/LLM inputs/secrets → `safety-reviewer` run, findings addressed, verdict posted as a PR comment (`safety-reviewer: PASS` / `PASS WITH FIXES`)

## Notes for reviewer
<!-- Risks, open questions, things you're unsure about -->
