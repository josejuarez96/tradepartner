# Fragments

One file per PR, `<issue>-<slug>.md`: for a plan task, first the line `task: Tn (PR #n)` (#2280; the PR never ticks its plan line), then an optional bullet that becomes the PR's line in
STATUS "Recently done" (at most 240 characters), then `### Added` / `### Changed` /
`### Fixed` / `### Removed` headings with the bullets that belong under `[Unreleased]` in
[CHANGELOG.md](../CHANGELOG.md) (#351). Write it with one call:

    uv run python scripts/fragments.py add <issue> --slug <slug> [--task Tn --pr n] --status "..." --added "..."

`fragments.py fold` merges every fragment in issue order, keeps the last 10 STATUS lines, ticks
and collapses each named task's plan line, and deletes the files. See [teams.md](../docs/ways-of-working/teams.md#shared-files-how-n-prs-avoid-conflicts).
