- T267 first PR: A pay-date cash dividend can reconcile after an earlier ok cash base; the per-dividend matching seam follows.

### Fixed
- Cash dividends credited within the frozen ex-date window are netted against journaled credits oldest first, so a later pay-date credit can reconcile without halting (#2183).
