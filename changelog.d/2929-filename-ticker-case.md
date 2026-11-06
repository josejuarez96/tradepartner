- #2929 (PR #2930): T184h filename listings key tickers case-free (avy25 = AVY25); a class conflict fails that CIK closed and is counted, the build goes on. BUILD_RULES_VERSION 3.
### Fixed
- Filename listings: a lowercase Form 25 file-name ticker no longer aborts the EDGAR build on a false class conflict; a true conflict writes no filename rows for that CIK and is counted in the run message (#2929).
