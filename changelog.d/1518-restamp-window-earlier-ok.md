- T166e (#1518): `ingest --restamp-from` takes `--restamp-window-earlier-ok <precheck.json>`: in-window late stamps move earlier to their SGML time under T166f's rule and a current passing pre-check; all else in the window aborts.
### Added
- `tradepartner ingest --restamp-window-earlier-ok PRECHECK` (#1497, T166e): the store restamp moves an in-window late EDGAR stamp earlier to its SGML header time only with a passing, current pre-check for the manifest's release; a stale file names the cache backup to restore.
