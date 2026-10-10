- #1312: the --accept-rejections boundary test now also fences the CLI frameworks: only cli.py may import typer or click (static imports, dynamic import strings, sys.modules lookups).
### Added
- Boundary rule: only `cli.py` may import `typer` or `click`, so no helper outside the `--accept-rejections` fence can configure the Typer app (#1312).
