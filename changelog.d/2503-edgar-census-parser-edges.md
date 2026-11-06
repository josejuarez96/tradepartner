- Fixed invalid date counting, open-quarter 404, one-space rows, and optional Form 4 date
### Fixed
- census_quarter validates filing dates and counts malformed wanted rows; _census_sec_forms treats open-quarter 404 as empty; _xml_date raises _BadField for invalid optional dates; test uses real form.idx header; census docstring updated
