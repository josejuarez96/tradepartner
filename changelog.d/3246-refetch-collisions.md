- refetch-actions refuses collision rows unless reviewed distinct or --allow-collision (#3246)
### Added
- refetch-actions now refuses dividend/split collision rows (dry run lists them as refused; a real run writes nothing for the month and exits 1) unless the reviewed list marks them distinct or --allow-collision names them (#3246)
