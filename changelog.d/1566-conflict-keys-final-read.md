- adapter-statement-conflict-keys — #1566 (size: S)
### Fixed
- EDGAR statement facts: the conflicts withheld no longer accumulate across a same-run cache refresh. Adapting this to the final payload resolves #1566, because a resolved conflict no longer cascades to suppress the derived gross profit of whichever accession is affected.
