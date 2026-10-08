- Every security_id is read as an instrument id, and the master's own id derivation refuses an input that would put ':' into a derived id (ADR 0015 seam 6).
### Added
- The instrument-id rule in store/master.py's docstring, the INSTRUMENT_ID_SEPARATOR and BENCHMARK_PREFIX constants benchmark() builds its id from, and the guard that primary_security_id and the {base}-{n} class-id derivation raise ValueError rather than mint a prefixed id.
