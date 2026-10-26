- Paper settle reads broker fills from submitted_at so clock skew cannot hide them ([#2216](https://github.com/josejuarez96/tradepartner/issues/2216)).
### Fixed
- Paper settle includes fills timestamped by the broker before our pending stamp, bounded by the window start (#2216).
