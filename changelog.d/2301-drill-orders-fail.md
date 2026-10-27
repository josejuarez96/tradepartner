- #2301: paper shakedown E.4 fails if any drilled scheduler run wrote an order, even when another drill is clean; ADR 0017 E.4 corrected.
### Fixed
- A kill-switch drill that wrote orders now makes E.4 failing and names the run, so it reaches the owner's queue (#2301).
