- #1093: EdgarConfig refuses inf/NaN and caps requests_per_second at 10 (no config can drop the SEC throttle); config sections hide input values in errors; hypothesis-file errors show key = file value.
### Fixed
- EdgarConfig refuses non-finite floats and caps requests_per_second at SEC's 10 req/s, so no config value can turn the SEC throttle off; config sections validated on their own no longer echo input values; a bad hypothesis-file value, or a bad frozen cost or risk value in a paper window, is named with its value through config.render_validation_errors (#1093).
