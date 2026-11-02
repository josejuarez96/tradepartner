- Incremental EDGAR reads the daily files of a quarter that settled during a key-off gap
### Fixed
- Incremental EDGAR: after a key-off gap, the first key-on run reads the daily files of a quarter that settled meanwhile, so the row keeps the daily listed_on and list_position and the files are recorded (#2864)
