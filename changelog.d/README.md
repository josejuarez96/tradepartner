# CHANGELOG fragments

One file per PR, `<issue>-<slug>.md`, with `### Added` / `### Changed` / `### Fixed` / `### Removed`
headings and bullets that belong under `[Unreleased]` in [CHANGELOG.md](../CHANGELOG.md).
Write it with `uv run python scripts/fragments.py add <issue> --slug <slug> --added "..."`.
`fragments.py fold` merges every fragment into CHANGELOG.md in issue order and deletes it.
See [teams.md](../docs/ways-of-working/teams.md#shared-files-how-n-prs-avoid-conflicts).
