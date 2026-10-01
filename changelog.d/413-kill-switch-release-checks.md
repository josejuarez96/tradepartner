- #413 #414 #415 kill switch: release refuses a row that would not clear the switch (a timestamp tie or skew), a resume that released any window, and both writers read the clock before taking the store
### Fixed
- Kill switch: release re-derives and refuses a row that would not clear; the resume check spans every window; engage_from_overrides and release read the clock outside the write (#413, #414, #415)
