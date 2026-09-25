"""The point-in-time DuckDB store: schema, connection management, and (in
later tasks) the as-of read API, security master and classifier.

This package owns every table listed in docs/specs/data-foundation.md
("Data / interfaces" > Tables) and the rules in ADR 0003 rule 1: every
fact table carries `known_at`, `ingested_at`, `source` and `provenance`;
raw prices and corporate actions are stored separately and adjusted at
read time; nothing here computes an adjusted series as stored truth.
"""

from __future__ import annotations
