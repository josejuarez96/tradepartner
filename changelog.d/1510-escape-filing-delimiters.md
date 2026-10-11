- #1510 research-labeling `build_packet` neutralises the filing-text delimiters a body carries
### Fixed
- research-labeling `build_packet`: a filing body carrying `=== FILING TEXT START ===` or `=== FILING TEXT END ===` can no longer close or reopen the delimited block; each marker inside the body is rewritten to its bracketed form and a body without them builds the same packet as before (#1510)
