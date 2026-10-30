- SEC snapshot ticker changes require two matching fetches before a dated listing is visible ([#2295](https://github.com/josejuarez96/tradepartner/issues/2295)).
### Fixed
- A one-fetch SEC ticker anomaly no longer creates a snapshot rename; the second matching fetch confirms it without look-ahead (#2295).
