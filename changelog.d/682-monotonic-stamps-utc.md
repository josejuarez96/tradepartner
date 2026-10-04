- #682 resume: _MonotonicStamps.observe raises the floor by the reading's UTC instant, so floor, clamped stamps and the clamp warning are always UTC; an offset-less reading no longer raises TypeError there.
### Fixed
- Paper resume's monotonic-stamp floor is normalised to UTC when `collect`'s readings raise it, so clamped `known_at` stamps and warnings never carry a non-UTC offset (#682).
