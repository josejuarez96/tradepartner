# Old-layout STATUS fragments

Before #351 a PR wrote its STATUS "Done" line here and its CHANGELOG bullets in
`changelog.d/`. New PRs write **one** file, `changelog.d/<issue>-<slug>.md`, that holds both
(see [changelog.d/README.md](../../changelog.d/README.md)). Files still arriving here from PRs
opened before the change are read, checked, shown and folded as before, so nothing breaks
during the transition.
