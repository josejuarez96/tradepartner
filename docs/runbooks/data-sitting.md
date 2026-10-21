# Run sheet: the one data sitting

Owner and orchestrator only, on the owner's job checkout and real store. Target **2026-11-05**, only after T162's ten-session span has finished and `paper shakedown` exit 0 has been pasted on #1352. A halt restarts that span and moves this sitting. The orchestrator runs each step on the owner's go; the owner decides after each dry run and any stop rule. Follow [named data releases](data-releases.md) for the release record and restore procedure, and [scheduling](scheduling.md) to stop and restart jobs. This sheet records the 2026-10-18 one-sitting order and its 2026-10-20 timing amendment in [the data-foundation plan](../plans/data-foundation.md).

## Before the first write

- [ ] Confirm T162's pasted `paper shakedown` exit 0, the agreed censuses, and the owner's answers to data-foundation spec questions 31 and 35. Check #1974's decision and the running census **before** the a3 step. Do not treat its 37 pre-2003 rows as approved residue without that decision.
- [ ] Confirm T189b, T191, T186/T186b, T190/T190d, T192/T192b, T193 and T194 are merged; T172d's `symbol_changes` backfill is recorded. T195 is parked. T189c and T184h are not gates. Check the release PRs' status before pulling them.
- [ ] Stop scheduled ingest and paper jobs as in [scheduling](scheduling.md); confirm no ingest, sweep or `paper run` is in progress. Do not start another until both windows have restarted. `df -h` must show at least **10 GB free**, enough for the full store copy and FSN extraction.
- [ ] Save the current window ids and hypothesis slugs for `main` and `daily` from `uv run tradepartner paper status --all` and the owner registry. Record the live store path and its size. Take a **full file backup**, including any WAL, before the first release. Keep it through the sitting; never overwrite it:

  ```bash
  df -h
  cp -n data/tradepartner.duckdb data/tradepartner.restate-1682.duckdb
  [ ! -f data/tradepartner.duckdb.wal ] || cp -n data/tradepartner.duckdb.wal data/tradepartner.restate-1682.duckdb.wal
  uv run tradepartner health --coverage
  uv run tradepartner health --check
  uv run tradepartner paper abandon --book main --reason "one data sitting after T162"
  uv run tradepartner paper abandon --book daily --reason "one data sitting after T162"
  ```

  Check the backup exists and record its path, size and hash. Paste the starting coverage buckets, health output, and both abandoned window ids on the T197 release PR. **Abandon once, here; restart once, after all six steps.** `paper stop` is not the abandonment command.

Each named release needs a `before` and `after` record. The `--backup` path must be a readable store copy taken immediately before that release; `decision data-release open` checks its latest `ingested_at` against the live store. Take the named checkpoint below if the baseline no longer matches. Keep every copy. For every `close`, use the first and last **actual XNYS sessions touched**, not guessed calendar dates. Stop if those are unknown. Never open the next release while one remains open.

## 1. T197: `restate-1682` (#1682)

Purpose: delete only the reviewed never-true master rows (lever B).

- [ ] Record `uv run tradepartner health --coverage` over the configured twelve sample months and the 86 month-end index key. The census expects lever B to explain **98 member-months**; compare actual bucket counts, not just the total. Use the manifest reviewed on #1682, with no other rows.
- [ ] Open, dry-run, read every found and refused row, then apply only on the owner's go:

  ```bash
  uv run tradepartner decision data-release open --name restate-1682 --backup data/tradepartner.restate-1682.duckdb --reason "#1682 reviewed lever B manifest"
  uv run tradepartner delete-master-rows --manifest <reviewed-master-manifest.csv> --release restate-1682 --dry-run
  uv run tradepartner delete-master-rows --manifest <reviewed-master-manifest.csv> --release restate-1682
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner health --check
  uv run tradepartner health --coverage
  uv run tradepartner decision data-release close --name restate-1682 --from <earliest-touched-session> --to <last-touched-session> --reason "#1682 delete-only; list touched sessions and affected sweeps"
  ```

- [ ] The dry run must match the manifest census exactly with **zero refused**; the EDGAR dry run must leave the note's Form 25 unmatched and must not rederive a deleted key. Stop on any other row, refusal, reappearance, failed health check or unpromised coverage change. With jobs stopped, restore the baseline store and WAL as below, record the release as rolled back, and retry under a **new** release name after review. Paste before/after bucket counts, found/refused rows, EDGAR dry-run output and release ids on T197's PR and #1682.

## 2. T186c: `dupdiv-1613` (#1613)

Purpose: delete reviewed duplicate dividends through the safe ex-date bound.

- [ ] Confirm questions 27 and 28 are answered on #1613. `health` must show zero unreviewed violations: add every missing group to the reviewed list as `duplicate`, `distinct` or `unresolved` with evidence. Set `<bound>` to the session before the earliest formation start of a decision in either former window. Census: **169 listed** plus newly reviewed duplicates; **8 deferred** at the plan's bound, or the actual bound's count; **0 already repaired, 0 stale**. Expected post-check: violations 0, accepted **11** plus new distinct entries, unresolved **3** plus new unresolved entries, pending equal deferred.
- [ ] Take the release checkpoint and run:

  ```bash
  cp -n data/tradepartner.duckdb data/tradepartner.dupdiv-1613.duckdb
  [ ! -f data/tradepartner.duckdb.wal ] || cp -n data/tradepartner.duckdb.wal data/tradepartner.dupdiv-1613.duckdb.wal
  uv run tradepartner decision data-release open --name dupdiv-1613 --backup data/tradepartner.dupdiv-1613.duckdb --reason "#1613 reviewed dividend duplicates"
  uv run tradepartner repair-dividends --release dupdiv-1613 --to <bound> --dry-run
  uv run tradepartner repair-dividends --release dupdiv-1613 --to <bound> --expect-action-rows <dry-run-row-count>
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name dupdiv-1613 --from 2019-09-27 --to <bound> --reason "#1613 deleted rows, deferred keys and sweep decision"
  ```

- [ ] Stop on a stale list entry, a count different from the reviewed list, a mismatch between dry run and apply, or failed health. Restore the **dupdiv checkpoint**, close as rolled back, and re-review #1613. The owner's read-only `dupdiv.py` must recompute AIV 2020-11 and TDG 2025-09 from raw bars plus one dividend each; this is an owner-local check, not a repo command. Paste the list, dry-run and run-row counts, health block, two month checks and release ids on T186c's PR.

## 3. #1382: `restamp-1382a3` store step

Purpose: apply the already repaired cache's manifest to stored filing stamps. The phase-B release was cut.

- [ ] First reconcile #1553/#1582's reported a3 `after` record (4,978 changes) with the owner's release registry and manifest. If the store step is **already closed**, paste its evidence and skip the write; never apply it twice. If not, obtain #1974's dated decision about the 37 pre-2003 rows and its census. Any unresolved decision stops this step.
- [ ] If still needed, use the frozen `v<PARSER_VERSION>.restamp-1382a3.manifest.csv`, not a newly generated manifest. The read-only precheck must pass and its hash belongs in the `before` reason. Take a release checkpoint, then:

  ```bash
  uv run python scripts/restamp_precheck.py --release restamp-1382a3 --out <a3-release-dir>
  cp -n data/tradepartner.duckdb data/tradepartner.restamp-1382a3.duckdb
  [ ! -f data/tradepartner.duckdb.wal ] || cp -n data/tradepartner.duckdb.wal data/tradepartner.restamp-1382a3.duckdb.wal
  uv run tradepartner decision data-release open --name restamp-1382a3 --backup data/tradepartner.restamp-1382a3.duckdb --reason "#1382 a3 manifest and passing precheck hash"
  uv run tradepartner ingest --source edgar --restamp-from <a3-manifest.csv> --restamp-window-earlier-ok <a3-release-dir>/precheck.restamp-1382a3.json --rebuild-statement-facts --dry-run
  uv run tradepartner ingest --source edgar --restamp-from <a3-manifest.csv> --restamp-window-earlier-ok <a3-release-dir>/precheck.restamp-1382a3.json --rebuild-statement-facts
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name restamp-1382a3 --from <earliest-touched-session> --to <last-touched-session> --reason "#1382 a3 changed instants, trial notes and #1974 decision"
  ```

- [ ] Census first for moved rows per table and refused CIKs; do not invent a target count from the old cache pass. Require zero unapproved refusals, a passing precheck at apply time, `health --check` exit 0 and the filing-date sweep's zero provably early store-backing stamps for covered CIKs. Stop on any changed read instant, later move, unapproved #1974 residue or refused CIK. Restore this checkpoint, close as rolled back, and get a fresh precheck before retry. Paste precheck hash, dry-run/apply counts, refused CIKs, sweep, health, trial notes and ids on #1382; note any accepted non-universe residue under ADR 0023 D1.

## 4. T198: `rederive-1682` (#1682)

Purpose: flip the two EDGAR switches, bump both cache versions, rederive rows, rebuild classification, and fill price holes.

- [ ] Check the T198 hold PR contains only the flip and bumps and is pulled **at this step**. T184h stays out unless its own gates and questions 22–24 are satisfied. Record `health --coverage` first. The plan's key expects C1/C3 **265**, H **183**, E2 **270**, and F **142** member-months as diagnostic targets; E1's **144** is excluded because T195 is parked. The actual expected row and bar counts come from the agreed censuses: **census first**.
- [ ] Take the release checkpoint, open, then dry-run and review each write:

  ```bash
  cp -n data/tradepartner.duckdb data/tradepartner.rederive-1682.duckdb
  [ ! -f data/tradepartner.duckdb.wal ] || cp -n data/tradepartner.duckdb.wal data/tradepartner.rederive-1682.duckdb.wal
  uv run tradepartner decision data-release open --name rederive-1682 --backup data/tradepartner.rederive-1682.duckdb --reason "#1682 C1 C3 H E2 F; T195 parked"
  uv run tradepartner ingest --source edgar --rebuild-classifications --dry-run
  uv run tradepartner ingest --source edgar --rebuild-classifications
  uv run tradepartner master-retract
  uv run tradepartner master-retract --apply --expect-rows <dry-run-rows> --expect-digest <dry-run-digest> --release rederive-1682
  uv run tradepartner repair-resolution --dry-run
  uv run tradepartner repair-resolution --expect-bar-rows <dry-run-bars> --expect-action-rows <dry-run-actions> --release rederive-1682
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --dry-run
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes
  uv run tradepartner health --check
  uv run tradepartner health --coverage
  uv run tradepartner decision data-release close --name rederive-1682 --from 2016-01-04 --to <last-touched-session> --reason "#1682 actual rows, read-time merges and trial replay rule"
  ```

  Apply `master-retract` and `repair-resolution` **only if** their dry runs find rows. A traded-row refusal requires a decision on #1682; never add `--allow-traded` from this sheet. Run `uv run tradepartner ingest --source openfigi` only if T189c's owner run is approved and include its `figi_types` block.
- [ ] Stop for a common CIK losing its primary id, a class gaining a unit/note/warrant title, a newly unparseable universe CIK, changed `filing_name` or `snapshot_static` `known_at`, a T197 key reappearing, a traded-row refusal, a hole count outside the census, or failed health. Restore this checkpoint and close as rolled back. Paste EDGAR listing/shares diff against the backup, retract and repair counts, hole and resolver counts, coverage bucket before/after, `figi_types` if run, and release ids on T198's PR and #1682.

## 5. T170d: DTB3 backfill (#2011, PR #2084)

Purpose: load FRED DTB3 history, then land the already reviewed default flip. It is outside a data release because the rate table is outside the data vintage.

- [ ] Census first for row counts; the PR expects **zero revisions**. Run the owner's PR #2084 steps in order:

  ```bash
  uv run tradepartner ingest --source fred --backfill --since 2016-01-04 --dry-run
  uv run tradepartner ingest --source fred --backfill --since 2016-01-04
  uv run tradepartner health --check
  ```

- [ ] Check five H.15 observation dates (2016-01-04, 2020-03-16, 2022-03-11, 2024-01-02, latest stored); `rates_as_of` at the 2022-03-15 close must return 2022-03-11. Run PR #2084's read-only hypotheses SQL for non-default risk-free keys; expect **no rows**. Stop on revisions, missing/unexpected rates, a nonempty SQL result or failed health. Restore the baseline if the sitting must be unwound, or the pre-backfill checkpoint if preserving approved earlier releases; keep jobs stopped and record which release state remains. Paste dry-run and run-row counts, latest observation/vintage, five source checks, as-of check, SQL and health on PR #2084 before its owner merges the flip.

## 6. T214 merge (PR #1968, `hold`)

Purpose: attach `data.release_id` to new registrations at the release boundary.

- [ ] Read the latest PR #1968 review and #1958/#1970 resolution. No store command or dry run exists for a merge. Merge and pull **only inside this sitting** after T198's release is closed and its fingerprint evidence can be compared. A pre-release registration must retain its stored hash; a new release registration names a **closed** release and keeps `gap.*` and `adjust.*` frozen under the 2026-10-19 spec decision. Record the re-recorded fixture fingerprints beside the release's. If the PR is not ready, **do not merge**: D8 lapses and the plan needs its dated strike note. Paste the decision and fingerprint comparison on PR #1968/T198.

## Restart and close

- [ ] Confirm every opened release has an `after` row, `uv run tradepartner health --check` exits 0, and `uv run tradepartner health --coverage` has the reviewed final buckets. List every stale trial and replay only H1 and a kill-ladder rung-4 survivor per ADR 0023. Record delete-only affected sweep decisions under [named data releases](data-releases.md); each rerun counts in N.
- [ ] After the owner accepts the final state, reconcile and restart **both** paper windows once, using their saved hypothesis slugs, then restart scheduled jobs:

  ```bash
  uv run tradepartner paper reconcile --book main
  uv run tradepartner paper reconcile --book daily
  uv run tradepartner paper start --book main --hypothesis <saved-main-slug>
  uv run tradepartner paper start --book daily --hypothesis <saved-daily-slug>
  uv run tradepartner paper status --all
  uv run tradepartner health --check
  ```

  A reconciliation mismatch or refused start stops restart; leave jobs stopped and follow the paper runbook. Paste old/new window ids, reconciliations, final health and coverage, all release ids, and job restart evidence on the sitting PRs. The new windows start a new span; the earlier T162 pass remains the gate for *this* sitting.

## Restore and gaps

With every job still stopped and no DuckDB process holding the store, restore the relevant checkpoint using the [release runbook](data-releases.md):

```bash
rm -f data/tradepartner.duckdb.wal
cp data/tradepartner.<release>.duckdb data/tradepartner.duckdb
[ ! -f data/tradepartner.<release>.duckdb.wal ] || cp data/tradepartner.<release>.duckdb.wal data/tradepartner.duckdb.wal
uv run tradepartner health --check
```

Keep the backup and close the failed release as rolled back. A retry needs a fresh name and, for a3, a fresh precheck. A full unwind uses `restate-1682` as `<release>` and requires documenting all intervening release records as rolled back.

**Gaps to resolve before execution:**

- The plan says **one backup**, but `decision data-release open` requires a backup of each release's *current* state. This sheet takes one baseline plus named checkpoints. The owner must accept that reconciliation of the plan and registry contract before the sitting.
- #1553/#1582 report an a3 `after` record already written; #1974's pre-2003 decision and census are pending. Verify the real registry and decision before any a3 write.
- The T198 plan calls `paper reconcile` read-only. The current CLI's `paper reconcile` writes a reconciliation row; its behavior must be accepted for the restart, and it cannot serve as a read-only check.
- T198 calls for an EDGAR dry-run diff against the backup; the CLI prints run summaries, not a complete row diff. Prepare and review a read-only comparison query before applying.
- No repo command implements the owner-local `dupdiv.py` index check, H.15 hand check, `rates_as_of` probe, or automatic comparison of T214 fingerprints. Record the actual owner queries and outputs with the evidence; do not infer a passing result from `health`.
