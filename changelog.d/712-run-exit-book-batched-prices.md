- T87 (#712, PR #837): step 7's deferred names are priced in one batched reference_prices read, per name only when the batch raises; rows unchanged.
### Changed
- Step 7 prices the names only a journaled decision or an open order brings in with one batched read, falling back to one read per name only when that raises (#712).
