- #1309: the accept_rejections fence now refuses app-level context_settings and any default_map in cli.py, so no default map can set --accept-rejections.
### Fixed
- accept_rejections fence refuses app-level context_settings, `**` into Typer/add_typer/callback, and any default_map in cli.py (#1309).
