- Restamp pre-check also covers rows before the window's first session (#1524)
### Fixed
- restamp pre-check: a pre-window row within 5 h after close(S-1) or a non-session started_at now fails the check (#1524)
