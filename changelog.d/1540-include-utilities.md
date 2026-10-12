- #1540 amendment (class B): the SIC 4900-4999 utility exclusion was mis-recorded as a compliance decision; charter, ADR 0006, spec and plan amended (T176: default [], no guard); open windows and registered trials keep their universe.
### Changed
- Charter, ADR 0006 and the data-foundation spec drop the utility-sector (SIC 4900-4999) universe exclusion, which was mis-recorded as a compliance decision; plan task T176 changes the `universe.exclude_sic_ranges` default to `[]` and removes its guard; registrations and paper windows made before it keep the universe they froze (#1540).
