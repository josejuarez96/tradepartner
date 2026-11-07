- #3193: every CLI _fail that prints an exception's text now goes through the secret scrub; exposures --candidates refuses --family/--candidate; an open_data holdout overlap prints overlaps-holdout, not spends-holdout (#3189 docs-9..11).
### Fixed
- CLI errors that print an exception's text now scrub secrets from it; `exposures --candidates` refuses `--family`/`--candidate` instead of ignoring them; an `open_data` record over a holdout is labelled `overlaps-holdout`, not `spends-holdout` (#3193, #3189).
