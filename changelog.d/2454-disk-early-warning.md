- health warns below health.warn_free_disk_gb (15 GB); ingest refuses below ingest.min_free_disk_gb (5 GB)
### Added
- Early low-disk warning in health and a free-disk floor checked once before ingest starts (exit 1, /fail heartbeat for the scheduled shape)
