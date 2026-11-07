- paper: the fill-price band default is 0.5 (real 25-30% gaps such as INTC 10-30 no longer halt), and paper resume --accept-fill-band releases a checked band fault instead of abandoning the window (#3130)
### Added
- paper resume --accept-fill-band: after the owner checks a fill_price_outside_band print, resume releases past it and journals the accepted fault in resume_acceptances; the resume that first collects a bad fill still refuses (#3130)
### Changed
- paper.max_fill_price_deviation defaults to 0.5 (was 0.25): real overnight gaps of 25% or more halted the books about once in eight daily sessions; 100x, 0.01x and 10x prints still halt (#3130)
