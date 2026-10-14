- T191 (#1697): `delete-master-rows` built: a reviewed manifest's never-true listings/delistings rows deleted under a named release, refused on later or foreign evidence and across an open paper window; runs in T197.
### Added
- `tradepartner delete-master-rows --manifest <csv> --release <name> [--dry-run]` (#1682 lever B, T191): deletes a reviewed manifest's never-true `listings` and `delistings` rows, each with the filing whose SGML header names the security's CIK and was accepted no later than the row's `known_at`; refuses every row an open paper window read.
