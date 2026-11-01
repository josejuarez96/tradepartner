task: T307 (PR #2567)
- T307 SEC ownership 13D discovery, checked stamp lookup, per-accession XML fetch and idempotent ingest write built on T306
### Added
- SEC ownership 13D discovery and Form 4 per-accession XML fetch use checked acceptance stamps; writes refuse changed re-reads and keep filer identities out of the security master.
