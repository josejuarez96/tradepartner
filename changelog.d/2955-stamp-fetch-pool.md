- repair_stamps SGML fetches on a bounded pool (--fetch-workers, default 4): ~6.8 req/s vs 1.8 on a fake 0.55 s SEC latency; journal, manifest and counts byte-identical to one at a time; resume unchanged (#2955)
### Changed
- `repair_stamps.py run` and `restamp-unrepaired` ask SGML headers on a bounded pool (`--fetch-workers`, default 4, 1 to 8) behind the one EDGAR limiter; the journal, manifests and counts are those of the one-at-a-time path (#2955)
