## What & why
<!-- One or two sentences. PR title must be a Conventional Commit: feat(scope): ... -->

Closes #

## Spec / plan
<!-- The plan line this implements, or "n/a (size S)" -->

## Checklist
- [ ] Scope matches the plan task or issue, nothing extra; no secrets, data files or notebooks with outputs committed
- [ ] The targeted tests named here pass locally and new behavior has tests (CI runs the suite): <!-- files -->
- [ ] Ran it for real (command/output below), or `n/a, <why>` (pure module, docs only)
- [ ] The reviewers `scripts/ready_pr.py` requires for these paths (`quant-auditor`, `safety-reviewer`) ran before pass 1, and each latest verdict comment is `<agent>: PASS`, or `n/a, none required`

## Family relatedness
<!-- Only if this PR adds or changes a FAMILIES / FAMILY_PARENTS entry (config_backtest.py, class B, #3126); else "n/a".
Name any existing or killed idea with the same mechanism, signal inputs or universe. If there is one, this family is a child (or that family), not a root. -->

## Notes for reviewer
<!-- Risks, open questions, things you're unsure about -->
