- T188 (#1635): the paper run halts on a collapsed universe: UniverseCollapseError before any decision when n_universe < paper.min_universe_names or < (1 - paper.max_universe_drop_fraction) x the window's last plan; live keys.
### Added
- The paper run halts (kill switch engaged, nothing submitted) when the plan's universe collapses: under `paper.min_universe_names` (default 1) or below `1 - paper.max_universe_drop_fraction` (default 0.5) of the window's last plan; a journaled plan under the floor is never re-used (T188, #1550).
