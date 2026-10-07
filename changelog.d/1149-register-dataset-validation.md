- store.research.register_dataset itself refuses a sealed split name outside RESEARCH_SPLITS and checks a sealed full/none against the dataset's whole event span (#1149), closing the gap only the dataset-register CLI closed before.
### Fixed
- store.research.register_dataset refuses an unknown sealed split name and checks a sealed full/none split against the dataset's whole [event_start, event_end], so a direct API caller (not only the CLI) is protected (#1149)
