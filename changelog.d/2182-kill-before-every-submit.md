- A paper kill mid-phase now stops the phase's remaining submits: the switch is re-read before every submit, unsent orders close not_received, the run ends skipped_kill_switch not ok (#2182).
### Fixed
- paper run: an owner kill landing mid-phase stops the phase's remaining orders and the run is journaled skipped_kill_switch, not ok (#2182).
