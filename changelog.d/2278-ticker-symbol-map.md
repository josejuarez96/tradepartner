- T285 (#2185): reconcile, planning, the run and the wrapper share one symbol map; a renamed held name trades under the symbol reconcile mapped, and an ambiguous one is skipped or kept open with a note, never sent under its old ticker.
### Fixed
- Paper: after a ticker change the order path sends a held name under the new symbol reconcile mapped, not the old ticker; a name the change leaves ambiguous is skipped (or its forced exit kept open) per name with a run note, never sent under the old symbol (#2185).
