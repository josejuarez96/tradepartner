- Form 4 as-of reads exclude implausible filer dates by default and health warns on stored rows (#2754).
### Changed
- Form 4 transaction dates stay as filed; as-of readers use a configurable acceptance-day window and expose the excluded count, while health warns on implausible stored dates.
