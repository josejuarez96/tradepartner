# data/

Local data lives here and is **gitignored** (databases, parquet, raw downloads).

Rules:
- Every stored fact carries a `known_at` timestamp (when it became public), separate from the date it describes.
- Raw downloads are immutable; derived tables are rebuildable from raw + code.
- Back up anything that cannot be re-downloaded (e.g. self-collected social/trend history).
