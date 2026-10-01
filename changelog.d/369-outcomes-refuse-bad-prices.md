- #369 outcomes refuse a NaN, infinite, zero or negative mark, close, start price or equity with ValueError before anything is appended (a zero mark no longer falls back to the raw close)
### Fixed
- Outcomes refuse non-finite or non-positive marks, start prices and equity instead of journaling them (#369)
