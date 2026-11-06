- Merger exits read a chained-predecessor acquirer's close through the chain known at the cut (#2949)
### Fixed
- Merger exits: an acquirer that is a chained predecessor at the cutoff now reads its close under the successor the chain known at the cut maps it to, instead of a null that fell back to the last close (#2949).
