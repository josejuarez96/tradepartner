- Sweep trials reuse one code and data snapshot per read group and one close-time vintage scan per group (#3344).
### Changed
- Sweep registry fact scans and code identity reads run once per read group, preserving each variant’s recorded snapshot (#3344).
