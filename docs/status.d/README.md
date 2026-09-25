# STATUS fragments

One file per PR, `<issue>-<slug>.md`, holding the bullet(s) that belong in the "Done" list of
[STATUS.md](../STATUS.md). New files never conflict, so parallel PRs stop fighting over one
line. Write it with `uv run python scripts/fragments.py add <issue> --slug <slug> --status "..."`.
`fragments.py show` prints what is pending; `fragments.py fold` moves everything into STATUS.md
(run inside a PR that already edits STATUS.md, never in a PR of its own). See
[teams.md](../ways-of-working/teams.md#shared-files-how-n-prs-avoid-conflicts).
