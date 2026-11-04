- Chain candidates no longer skip a pair whose predecessor files a listing row after the hand-over (XOM); owner adds the XOM chain entry (#2939, PR #2963)
### Fixed
- A chain candidate ignores predecessor listing rows after the successor's first row, so a reorganisation like ExxonMobil's is named on the ingest run row (#2939).
