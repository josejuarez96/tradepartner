- T64b window stop, abandon, kill and override writer (#484, PR #495): a paper window can now close with its residues listed or be abandoned by the owner; kill needs no run lock.
### Added
- execution.window: stop (requested, then closed, refused under the switch, open orders, missing outcomes, a failed reconciliation or a holding above its residue), owner-only abandon, kill and the override writer shared by the page and the CLI; spec req 14/16 and the Data section amended (T64b, #484).
