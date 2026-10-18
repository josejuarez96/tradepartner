# Runbook: the `protect-main` ruleset

The server-side layer of [git-workflow.md, How the rules are enforced](../ways-of-working/git-workflow.md#how-the-rules-are-enforced). File: `.github/rulesets/protect-main.json`; active since 2026-10-04 (plan T75b, ruleset id 24443727). It refuses a direct push, a force-push and a deletion of `main` for every token, the owner's included, requires a PR, squash only, with the `checks` run on the head commit. It does **not** refuse a PR merge with a red `checks` run by the owner's admin token (#762 merged red on purpose; #772 reverts it), so the merge classes and the green-head condition of rule 7 are a process rule, not a server one.

## What the file says and why

JSON allows no comment, so the explanation lives here (merge-train spec req 10, owner decision 1 of 2026-10-01).

- `strict_required_status_checks_policy: false`. Strict would require every branch to be up to date with `main` before it merges, which is exactly what the train does not require: the train tests a PR against the current `main` as part of the batch, so a PR behind `main` is eligible as it stands.
- `bypass_actors`: the repository admin role (`actor_type: RepositoryRole`, `actor_id: 5`) with `bypass_mode: pull_request`, **never `always`**. GitHub's rulesets API reference requires an `actor_id` for `RepositoryRole` but no longer prints the numbers; the base repository role ids are maintain 2, write 4, admin 5 (the Terraform GitHub provider's `repository_ruleset` docs list them), and the read-back below is the check. A green PR needs no bypass. The bypass exists so the owner, in his own shell, can merge a PR whose `checks` run is red in an emergency, as an explicit act (the bypass dialog in the UI, or `gh pr merge --admin`), while a direct push to `main`, a force-push and a deletion stay refused for his token too. `OrganizationAdmin` does not apply to a personal repository.
- No `merge-train` status context. A context only the train would set would refuse every hand merge and turn a `main`-red hotfix into a one-PR train. The ruleset is a guard against habit, not an authentication boundary: every token in this repository is the owner's, and what keeps an agent from using the bypass is rule 7 and the `ask` entry in `.claude/settings.json`, not the server.

## Changing it (owner only)

`gh api -X POST` and `-X PUT` are not in an agent's allow list. The ruleset was created with:

```bash
gh api -X POST repos/josejuarez96/tradepartner/rulesets --input .github/rulesets/protect-main.json
```

A later change to the file is applied with `gh api -X PUT repos/josejuarez96/tradepartner/rulesets/<id> --input .github/rulesets/protect-main.json`, the id from `gh api repos/josejuarez96/tradepartner/rulesets`. Then read it back:

```bash
gh api repos/josejuarez96/tradepartner/rulesets/<id> --jq '.bypass_actors, (.rules[] | select(.type == "required_status_checks"))'
```

`bypass_actors` must show `RepositoryRole` 5 with `pull_request`, and the status-check rule `strict_required_status_checks_policy: false`. The file in the repository is the record of what is active.
