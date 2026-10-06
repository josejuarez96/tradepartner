- #1093: EdgarConfig refuses inf/NaN (inf meant no SEC throttle); config sections hide input values in errors; hypothesis-file errors show key = file value.
### Fixed
- EdgarConfig refuses non-finite floats, so requests_per_second=inf can no longer turn the SEC throttle off; config sections validated on their own no longer echo input values; a bad hypothesis-file value is named with the file's own value (#1093).
