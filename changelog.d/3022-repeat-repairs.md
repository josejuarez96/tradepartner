- Raw re-derive repeat repairs (#3022): repair entries carry writer_mode and replaces; a replaced entry never matches; bulk zips read once
### Fixed
- `archive rederive`: a repair that puts back a row's earlier value can be re-derived again (the replaced entry no longer matches), a repaired row maps only under its original writer mode, and each bulk zip is read and parsed once (#3022)
