- Pytest keeps only failed tests' tmp_path dirs (tmp_path_retention_policy = "failed"), cutting a full basetemp from ~5 GB to near 0 when green.
### Added
- pyproject.toml: tmp_path_retention_policy = "failed" under [tool.pytest.ini_options].
