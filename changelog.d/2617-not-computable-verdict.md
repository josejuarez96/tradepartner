- #2617 (PR #2657): a NaN or infinite research primary gives the verdict not_computable (no evidence), not a quiet fail; schema 41 widens the verdict CHECK.
### Fixed
- Research results: a NaN or infinite primary statistic or interval bound is recorded with the verdict `not_computable`, not `fail`; schema version 41 (#2617).
