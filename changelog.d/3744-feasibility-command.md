task: T455 (PR #3752)
- T455 (#3744, PR #3752, draft, stacked on #3749): tradepartner feasibility --store <copy> --design <file>; refuses the configured store on paths before any connection, opens the copy read-only, prints the counts block.
### Added
- tradepartner feasibility: a counts-only feasibility block from a copy of the store; refuses the configured store (also by hard link, symlink or relative path) and a missing path with exit 2 before any connection.
