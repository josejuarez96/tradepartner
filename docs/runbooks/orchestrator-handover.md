# Runbook: orchestrator handover and takeover

The procedure behind [teams.md, Orchestrator](../ways-of-working/teams.md#orchestrator). There is exactly one orchestrator session at a time; it retires only after this handover is complete, and its successor starts from the handover comment.

## Retiring, in this order

1. **List every live team** with its issue, its PR (or its issue alone, when no PR exists yet), its state (building, in review, ready, blocked), and the model it runs on.
2. **Stop its live teams.** They keep their claims for the successor to re-spawn.
3. **Release or park what the teams that died earlier still hold.** `team.py release` refuses a claim held by another team (claim protocol rule 7), so the orchestrator spawns a short session in the dead team's own directory, which releases or parks as that team, with the handoff comment on the issue; the orchestrator does not enter the directory. A claim that cannot be reached that way (the directory is gone) is listed in the handover for Jose's `release --force`; retirement waits on the listing, not on him acting.
4. **Write the handover as a comment on the one standing issue titled exactly `Orchestrator log`** (`gh issue list --search "Orchestrator log in:title"`; the first orchestrator to hand over opens it, once, and it stays open), never as a memory note. The comment carries: the list from step 1; the claims left for Jose from step 3; the owner decisions pending, and the ones he made in chat that no issue records yet; the merge queue in order; and the statement that the retiring session's teams have stopped. A memory note may point at the comment; it never replaces it.

## Taking over

1. **First act:** a one-line "taking over" comment on `Orchestrator log`, so a session that finds a takeover newer than the last handover knows an orchestrator is live.
2. Between steps 1 and 2 of the session protocol's Start, **read the latest handover comment**.
3. **Re-spawn each live team of the retired orchestrator from that team's own directory** with "continue PR #N" (or "continue issue #N" where no PR exists yet) and the team's assignment. The spawned session works there; the orchestrator does not enter it. A team's final report reaches only the session that spawned it, so a team left under a retired orchestrator is a team nobody hears from.

Nothing else changes for the teams: the same claim, the same branch, the same PR.
