# Run sheet: the one data sitting

Owner and orchestrator only, on the owner's job checkout and real store. Target **Monday 2026-11-09**, after book main's 08:05 stop run lets `paper stop` close window 6 (daily's window 5 closed 2026-11-06). The orchestrator runs each step on the owner's go; the owner decides after each dry run and any stop rule. Follow [named data releases](data-releases.md) for the release record and restore procedure, and [scheduling](scheduling.md) to stop and restart jobs. This sheet records the 2026-10-18 one-sitting order and its 2026-10-20 timing amendment in [the data-foundation plan](../plans/data-foundation.md), and the 2026-11-06 Audit F additions (#3114, #3115, #3116, #3117).

**This sheet is the complete list.** Every store repair a merged PR defines for "the sitting" is a step below, in the order to run them. A repair not listed here is not part of this sitting. Run the steps in number order; never reorder or merge two releases.

| Step | Release | Source | Code |
|---|---|---|---|
| 1 | `restate-1682` | T197, #1682 | pinned checkout (1fe51c9f) |
| 2 | `dupdiv-1613` | T186c, #1613 | done 11-05: verify, skip |
| 3 | `restamp-1382a3` | #1382 | done (toml `after`): verify, skip |
| 4 | no release (FRED) | T170d, #2011 | done 11-05: verify, skip |
| 5 | no release: **the pull** | all of origin/main | pinned → origin/main |
| 6 | `classstart-3080` | #3090 (79-id backfill) | origin/main |
| 7 | `biob-3082` | #3086 (BIO.B holes) | origin/main |
| 8 | `rename-listings-3057` | #3068 | origin/main |
| 9 | `rederive-1682` | T198 (#2975), plus the first EDGAR write of #3064, #3095 and, if merged, #3091 | origin/main |
| 10 | `splice-3056` (only if residual) | #3064 | origin/main |
| 11 | `form25-3084` (only if residual) | #3095 | origin/main |
| 12 | `spinoff-3058` | #3067 | origin/main |
| 13 | `actions-3054` | #3054, #3073 | origin/main |
| 14 | `actions-3074` (only if rows) | #3074, #3085 | origin/main |
| 15 | `rename-listings-3057b` (only if residual) | #3068 after step 13 | origin/main |
| 16 | no release: candidate 5's exposures | #3116, #3176 (#3110 skipped) | origin/main |
| 17 | no release: gap sign-off replay | #3117 | origin/main |

**Counts per release.** After each `close`, post one comment on #1682 headed with the release name and its `before`/`after` decision ids. It lists every command run in that release, in order, each with its dry-run count and its applied count. A release's figures are the counts of its own commands only; never fold a later step's counts into an earlier release.

## Before the first write

- [ ] Confirm T162's pasted `paper shakedown` exit 0, the agreed censuses, and the owner's answers to data-foundation spec questions 31 and 35. Check #1974's decision and the running census **before** the a3 step. Do not treat its 37 pre-2003 rows as approved residue without that decision.
- [ ] Confirm T189b, T191, T186/T186b, T190/T190d, T192/T192b, T193 and T194 are merged; T172d's `symbol_changes` backfill is recorded. T195 is parked. T189c and T184h are not gates. Check the release PRs' status before pulling them.
- [ ] Confirm no ingest or sweep is running (`pgrep -f "tradepartner "` empty). Save both window ids and hypothesis slugs from `uv run tradepartner paper status --all` and the owner registry.
- [ ] **Disk precheck (#3115).** `store backup` always writes its copy beside the store (`<store>.bak-YYYYMMDD-HHMM`, plus a WAL copy and manifest), so every checkpoint below lands on the data volume. Compute the requirement and **stop if `df -h` shows less free space**:

  ```bash
  df -h data
  ls -lh data/tradepartner.duckdb data/tradepartner.duckdb.wal
  ```

  Required free space = `S × C + X + F`. Each term:
  - `S` is the store plus WAL size now. It was 2.4 GB on 2026-11-06.
  - `C` is the copies this sheet keeps on the volume. That is the baseline plus one checkpoint each for steps 6, 7, 8, 9, 12 and 13 (7 copies). Add one each for steps 10, 11, 14 and 15 if they write, and one failed-state copy for a restore (12 copies at most).
  - `X` is the FSN re-extraction headroom of step 9's first EDGAR write. This sheet carries 7.6 GB, the earlier "10 GB for one copy plus extraction" less one copy. That figure is not measured.
  - `F` is `ingest.min_free_disk_gb` (5 GB). Ingest refuses below it.

  With every copy kept on the volume: 12 × 2.4 + 7.6 + 5 ≈ **42 GB**. If the owner moves each closed release's copy off the volume, only the open release's checkpoint and a failed-state copy stay. That needs 2 × 2.4 + 7.6 + 5 ≈ **17.4 GB** on the volume and about 29 GB on the other one. A copy moves only after its release is closed and its `shasum -a 256` is recorded, and moves with its WAL copy and manifest. Re-hash it at the destination; the hashes must match. Record the new path beside the old one. On 2026-11-06 the volume had 11–12 GiB free, below both figures and below `health.warn_free_disk_gb` (15). Free space or attach the second volume before the sitting. Before every later `store backup`, re-run `df -h` and stop if free space is under `S × (copies still to take) + F`, plus `X` until step 9 is closed.
- [ ] **Missing release backups (#3115).** The `before` rows of store releases 18 (`roles-2691`, `data/tradepartner.duckdb.bak-20261031-2319`) and 20 (`restamp-1382b-retry`, `data/tradepartner.duckdb.bak-20261104-0225`) name backups that are not on disk; `data/` holds no backup copy at all. Those releases cannot be restored or audited against their `before` state. Before the sitting, record on each release's issue where its copy went (deleted or moved). Delete none of this sitting's copies without the owner's word.
- [ ] **Owner question 1 — how will each book close?** Resolve the conflict between T197 step (2), which names `paper stop`, and spec question 31(a), which names `abandon`. Record the chosen route per book on #1682 before touching a window. Both routes **end the existing paper book's record** and a new window starts a new record. Normal `stop` **sells the book's holdings** through the paper order path, with fills and possible trading costs; it may carry only documented `dust`/`untradable` residues. `abandon` makes no trades but leaves the holdings at Alpaca; the owner must reset that paper account in Alpaca's dashboard before a new start, discarding those paper holdings. Neither route preserves the current window's continuous record.
- [ ] For a book using **stop**, complete this on a session *before the sitting's release work*, while its submit window is open. The owner requests the stop, runs the book's ordinary stop run in the submit window, lets the collector reach terminal order events, then calls `stop` again to close. Confirm `paper stop: closed` and the reconciliation and residues in `paper status`. If liquidation or reconciliation cannot complete, stop the sitting and get an owner decision; do not assume the account is flat. State on 2026-11-06: daily's window 5 is closed. Main's window 6 is `requested`: the 11-06 stop run sold all 91 (all filled, reconciliation 74 ok), and `paper stop` closes it only after Monday's 08:05 run.

  ```bash
  uv run tradepartner paper stop --book <main-or-daily> --reason "one data sitting after T162"
  uv run tradepartner paper run --book <main-or-daily>
  uv run tradepartner paper stop --book <main-or-daily> --reason "one data sitting after T162"
  uv run tradepartner paper status --book <main-or-daily>
  ```

- [ ] For a book using **abandon**, wait for the day's collect run and terminal journal events, then run it **outside the submit window**. It refuses `open_orders` while an order lacks a terminal event; resolve that through the book's own collector/resume path before retrying. `abandon` runs its final reconciliation itself. Confirm its abandoned window id and recorded residues:

  ```bash
  uv run tradepartner paper abandon --book <main-or-daily> --reason "one data sitting after T162; owner chose account reset"
  uv run tradepartner paper status --book <main-or-daily>
  ```

- [ ] With both windows confirmed closed or abandoned, stop scheduled ingest and paper jobs as in [scheduling](scheduling.md). Confirm no `paper run` is in progress. Do not start another until both windows have restarted. Record the live store path and size. **Now** take the full baseline copy, including its WAL, with `store backup`:

  ```bash
  df -h
  uv run tradepartner store backup
  uv run tradepartner health --coverage
  uv run tradepartner health --check
  ```

  Save the **printed copy path and any printed log-copy path** as `<baseline-copy>` and `<baseline-wal-copy>`. Check the manifest and record the paths, sizes and hashes. The baseline contains the two ended windows; restoring it will restore that ended state. Paste starting coverage, health, closure method, stop/reconciliation ids, broker positions or residues, and baseline path on T197's PR. **Close once here; restart once after all the steps.**
- [ ] **Import the legacy releases (Audit F row 10).** `restamp-1382a2`, `restamp-1382a3` and `ops-1549` changed data, but they exist only in `data/releases.toml`, not in the store's `owner_decisions`. So `data_releases(conn)` and `data-release open`'s checks do not see them. With no release open, import them once:

  ```bash
  uv run tradepartner decision data-release import-file data/releases.toml
  ```

  There is no dry run. The command checks every entry first and refuses, listing every problem and writing nothing, when an entry is malformed or a (name, stage) pair is already stored. It also refuses an `after` made on or after the version-18 migration, or a file that would leave a release open. If the only problems are names already in the store, make a copy of the file beside the original holding only the three releases' `[[release]]` tables, and import that copy. Any other problem stops this item: leave the three unimported and record it on #1382. Imported `after` rows move no vintage. Paste the printed `imported` lines and decision ids on #1382.

Each named release needs a `before` and `after` record. The `--backup` path is the **copy path printed by `uv run tradepartner store backup` immediately before that release**; `decision data-release open` checks its latest `ingested_at` against the live store. For every backup, record each printed copy/log/manifest path, the copy's `ls -lh` size and `shasum -a 256` hash; repeat for its WAL copy if present. The command refuses an existing target or busy store and verifies the copy byte for byte. Its filename has minute precision: if another backup already used that minute, wait for the next UTC minute and rerun; never reuse or overwrite the old copy. Keep every copy. For every `close`, use the first and last **actual XNYS sessions touched**, not guessed calendar dates. Stop if those are unknown. Never open the next release while one remains open.

## 1. T197: `restate-1682` (#1682)

Purpose: delete only the reviewed never-true master rows (lever B). Runs on the pinned checkout, before the pull.

- [ ] Record `uv run tradepartner health --coverage` over the configured twelve sample months and the 86 month-end index key. The census expects lever B to explain **98 member-months**; compare actual bucket counts, not just the total. Use the manifest reviewed on #1682, with no other rows.
- [ ] Open, dry-run, read every found and refused row, then apply only on the owner's go:

  ```bash
  uv run tradepartner decision data-release open --name restate-1682 --backup <baseline-copy> --reason "#1682 reviewed lever B manifest"
  uv run tradepartner delete-master-rows --manifest <reviewed-master-manifest.csv> --release restate-1682 --dry-run
  uv run tradepartner delete-master-rows --manifest <reviewed-master-manifest.csv> --release restate-1682
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner health --check
  uv run tradepartner health --coverage
  uv run tradepartner decision data-release close --name restate-1682 --from <earliest-touched-session> --to <last-touched-session> --reason "#1682 delete-only; list touched sessions and affected sweeps"
  ```

- [ ] The dry run must match the manifest census exactly with **zero refused**; the EDGAR dry run must leave the note's Form 25 unmatched and must not rederive a deleted key. Stop on any other row, refusal, reappearance, failed health check or unpromised coverage change. With jobs stopped, follow **Restore and gaps** using the baseline; record the rollback on the PR/issue and retry under a **new** release name after review. Paste before/after bucket counts, found/refused rows, EDGAR dry-run output and release ids on T197's PR and #1682.

## 2. T186c: `dupdiv-1613` (#1613)

Purpose: delete reviewed duplicate dividends through the safe ex-date bound.

**Status: done.** The owner recorded it on #1682 (2026-11-05 03:37Z) as store decisions 12 and 13. Confirm both rows (`before` and `after`) exist, read-only, and paste their ids on #1613. Then skip this step. Run the commands below only if the rows are missing, and only on the owner's go.

- [ ] Confirm questions 27 and 28 are answered on #1613. `health` must show zero unreviewed violations: add every missing group to the reviewed list as `duplicate`, `distinct` or `unresolved` with evidence. Set `<bound>` to the session before the earliest formation start of a decision in either former window. Census: **169 listed** plus newly reviewed duplicates; **8 deferred** at the plan's bound, or the actual bound's count; **0 already repaired, 0 stale**. Expected post-check: violations 0, accepted **11** plus new distinct entries, unresolved **3** plus new unresolved entries, pending equal deferred.
- [ ] Take the release checkpoint and run:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name dupdiv-1613 --backup <dupdiv-copy> --reason "#1613 reviewed dividend duplicates"
  uv run tradepartner repair-dividends --release dupdiv-1613 --to <bound> --dry-run
  uv run tradepartner repair-dividends --release dupdiv-1613 --to <bound> --expect-action-rows <dry-run-row-count>
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name dupdiv-1613 --from 2019-09-27 --to <bound> --reason "#1613 deleted rows, deferred keys and sweep decision"
  ```

- [ ] Stop on a stale list entry, a count different from the reviewed list, a mismatch between dry run and apply, or failed health. Follow **Restore and gaps** using `<dupdiv-copy>` and re-review #1613. The owner's read-only `dupdiv.py` must recompute AIV 2020-11 and TDG 2025-09 from raw bars plus one dividend each; this is an owner-local check, not a repo command. Paste the list, dry-run and run-row counts, health block, two month checks and release ids on T186c's PR.

## 3. #1382: `restamp-1382a3` store step

Purpose: apply the already repaired cache's manifest to stored filing stamps. The phase-B release was cut.

**Status: done.** Its `after` is in `data/releases.toml`, and the legacy import above puts it in the store. The commands below are for a store where it is not.

- [ ] First reconcile #1553/#1582's reported a3 `after` record (4,978 changes) with the owner's release registry and manifest. If the store step is **already closed**, paste its evidence and skip the write; never apply it twice. If not, obtain #1974's dated decision about the 37 pre-2003 rows and its census. Any unresolved decision stops this step.
- [ ] If still needed, use the frozen `v<PARSER_VERSION>.restamp-1382a3.manifest.csv`, not a newly generated manifest. The read-only precheck must pass and its hash belongs in the `before` reason. Take a release checkpoint, then:

  ```bash
  uv run python scripts/restamp_precheck.py --release restamp-1382a3 --out <a3-release-dir>
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name restamp-1382a3 --backup <a3-copy> --reason "#1382 a3 manifest and passing precheck hash"
  uv run tradepartner ingest --source edgar --restamp-from <a3-manifest.csv> --restamp-window-earlier-ok <a3-release-dir>/precheck.restamp-1382a3.json --rebuild-statement-facts --dry-run
  uv run tradepartner ingest --source edgar --restamp-from <a3-manifest.csv> --restamp-window-earlier-ok <a3-release-dir>/precheck.restamp-1382a3.json --rebuild-statement-facts
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name restamp-1382a3 --from <earliest-touched-session> --to <last-touched-session> --reason "#1382 a3 changed instants, trial notes and #1974 decision"
  ```

- [ ] Census first for moved rows per table and refused CIKs; do not invent a target count from the old cache pass. Require zero unapproved refusals, a passing precheck at apply time, `health --check` exit 0 and the filing-date sweep's zero provably early store-backing stamps for covered CIKs. Stop on any changed read instant, later move, unapproved #1974 residue or refused CIK. Follow **Restore and gaps** using `<a3-copy>`, then get a fresh precheck before retry. Paste precheck hash, dry-run/apply counts, refused CIKs, sweep, health, trial notes and ids on #1382; note any accepted non-universe residue under ADR 0023 D1.

## 4. T170d: DTB3 backfill (#2011, PR #2084)

Purpose: load FRED DTB3 history, then land the already reviewed default flip. It is outside a data release because the rate table is outside the data vintage.

**Status: done.** Per the owner on #1682 (2026-11-05), `rates` holds DTB3 from 2016. Confirm read-only that the earliest stored DTB3 observation is 2016-01-04 or earlier, paste it on #2011, and skip this step. The commands below are for a store where it is not.

- [ ] Census first for row counts; the PR expects **zero revisions**. Run the owner's PR #2084 steps in order:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner ingest --source fred --backfill --since 2016-01-04 --dry-run
  uv run tradepartner ingest --source fred --backfill --since 2016-01-04
  uv run tradepartner health --check
  ```

- [ ] Save the printed path as `<dtb3-copy>` before the dry run. Check five H.15 observation dates (2016-01-04, 2020-03-16, 2022-03-11, 2024-01-02, latest stored); `rates_as_of` at the 2022-03-15 close must return 2022-03-11. Run PR #2084's read-only hypotheses SQL for non-default risk-free keys; expect **no rows**. Stop on revisions, missing/unexpected rates, a nonempty SQL result or failed health. Follow **Restore and gaps** using `<dtb3-copy>` if preserving approved earlier releases, or `<baseline-copy>` for a full unwind; keep jobs stopped and record which release state remains. Paste dry-run and run-row counts, latest observation/vintage, five source checks, as-of check, SQL and health on PR #2084 before its owner merges the flip.

## 5. The pull: all of origin/main

Purpose: move the job checkout from the pinned `1fe51c9f` to origin/main once, so every later step runs the merged repairs. No release: this step writes nothing to the store.

- [ ] The pull is **all of origin/main**, not a T198-only diff. On 2026-11-06 that was 54+ commits. They include T198's flip and FSN/COVER bumps (#2975), the schema 47 fill cursor (#3029), and ingest behaviour changes #3073, #3064, #3068, #3090, #3086 and #3095. `BUILD_RULES_VERSION` is 6 (#3064, then #3095), so step 9's first `ingest --source edgar` is a full master rebuild. #2975's bumps make it re-extract every FSN period too. Read its census with that in mind (step 9).
- [ ] Record what is pulled. Only what is merged at this moment is in the sitting; nothing merged later is pulled mid-sitting.
  - **Open on 2026-11-06:** #3071 (shares split dating), the #3070 flip that follows it and #3091 (issuer-class tags, schema 48). Steps 9 and 17 depend on whether each is in. The #3116 exposure-backfill tool will not come (#3133 closed). Step 16 needs only #3176's kill-line copy, `docs/research/2026-10-31-candidate-5-kill-line.md`, to be in the pull.
  - **T214 (PR #1968):** closed without merging, so there is nothing to merge. D8's lapse needs its dated strike note in the data-foundation plan, outside this sheet.

  ```bash
  git rev-parse HEAD
  git fetch origin
  git switch main
  git merge --ff-only origin/main
  git rev-parse HEAD
  git log --oneline 1fe51c9f..HEAD | wc -l
  uv sync
  ```

  Stop if the merge is not a fast-forward or the working tree is dirty. Paste both hashes, the commit count and which of #3071, the #3070 flip, #3091 and #3176 are in, on #1682.
- [ ] **Schema.** The store is at 46 and origin/main needs 47 (48 if #3091 is in). The migration runs on the first store write after the pull, which is step 6's `decision data-release open`. Step 6's checkpoint is therefore the last copy at schema 46. Restoring it with origin/main code migrates it again on the next write.
- [ ] **#3070 `universe.shares_split_dating` flip (conditional).** This step applies only if the flip PR, which follows #3071, is in the pull; otherwise the switch stays `false` and this sitting does nothing for it. It needs no store write: 115 shares facts straddle a split, and 62 of them are read as already split once it is on. It moves the pinned twin fingerprints, so list the trials it stales under [named data releases](data-releases.md), and run step 17 after it.
- [ ] Copy the two local id lists this sheet uses, `starts3053/ids.txt` (step 6, 79 ids) and `spyacts3074-ids.txt` (step 14), from the orchestrator's scratchpad under `/private/tmp` to a durable local folder outside the repo. Do this before the sitting: `/private/tmp` does not survive a restart. Check that `tr ',' '\n' < <ids file> | grep -c .` prints 79 for the first.

## 6. #3080: `classstart-3080` (79-id class-window backfill, #3090)

Purpose: fetch the bars of the 79 class ids that #3090's rule now leads from their company's first filing. Each is its company's only equity id, which until now got bars only from its first cover page (P&G `0000080424:common-stock` has bars only from 2024-08-05). It runs before step 9, so its count is its own and matches #3090's measurement on today's store.

- [ ] Census first: the dry run lists each id's month-holes and fetches nothing. Expect holes only on the 79 ids in the file (lead windows in `gained2.json` beside it), every window ending on or after 2016-01-04.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name classstart-3080 --backup <classstart-copy> --reason "#3080 only-class first-span lead, 79 ids"
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security "$(cat <79-ids file>)" --dry-run
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security "$(cat <79-ids file>)"
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name classstart-3080 --from <earliest-touched-session> --to <last-touched-session> --reason "#3080 bars added per id"
  ```

- [ ] `--since 2016-01-04` must match the store's backfill runs; hole filling reads only committed months of runs with the same `--since`. Stop on a hole or bar for an id outside the file, or failed health. Follow **Restore and gaps** using `<classstart-copy>`. Paste the dry-run hole list size, the bars written, and P&G's first stored bar after the run on #3080.

## 7. #3082: `biob-3082` (BIO.B holes, #3086)

Purpose: fill Bio-Rad class B's holes now that `alpaca.class_symbols` maps `BIOB` to Alpaca's `BIO.B`. Its own release, run before step 9's store-wide fill, so its count is not mixed into rederive-1682's.

- [ ] Census first: expect 1 security and **45 month-holes**: the 43 months 2019-08 to 2023-02, plus 2023-04 and 2023-08. 22 bars are already stored from 2019-08-06 to 2024-02-15. The dry run lists holes and fetches nothing, so the bar count is known only after the fill. Expect about 80 bars; any number up to the window's ~940 sessions is plausible.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name biob-3082 --backup <biob-copy> --reason "#3082 BIOb listing mapped to BIO.B"
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security 0000012208:class-b-common-stock --dry-run
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security 0000012208:class-b-common-stock
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name biob-3082 --from <earliest-touched-session> --to <last-touched-session> --reason "#3082 holes and bars filled"
  ```

- [ ] `--since` must be 2016-01-04 (`--since 2019-08-06` finds no holes). Stop on a bar written to any other security, or failed health. Follow **Restore and gaps** using `<biob-copy>`. Paste the hole count and bars written on #3082.

## 8. #3057: `rename-listings-3057` (#3068)

Purpose: add the back-dated new-ticker listing rows that stored `symbol_changes` imply, where no cover page gave one (PKI→RVTY, CHK→EXE). Run before step 9 so these rows are this release's. Step 9's full rebuild derives the same rows through `build_master`, so it should add none of them again.

- [ ] Census first. Expect at least +1 RVTY row (`0000031791`, from 2023-05-16, known 2026-11-02) and +1 EXE row (`0000895126`, from 2024-10-02, known 2026-04-28). Other stored changes may add a row or be refused with a reason (`no_evidence`, `new_ticker_held`); the dry run lists all of them. Read each one.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rename-listings-3057 --backup <rename-copy> --reason "#3057 new-ticker listing rows from stored symbol changes"
  uv run tradepartner repair-rename-listings --release rename-listings-3057 --dry-run
  uv run tradepartner repair-rename-listings --release rename-listings-3057 --expect-rows <dry-run-rows>
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name rename-listings-3057 --from <earliest-touched-session> --to <last-touched-session> --reason "#3057 rows added and refused by reason"
  ```

- [ ] Stop when RVTY or EXE is missing from the dry run, a row would give a ticker another security holds, or health fails. Follow **Restore and gaps** using `<rename-copy>`. Paste the dry-run list, refusals by reason and the applied count on #3057.

## 9. T198: `rederive-1682` (#1682)

Purpose: flip the two EDGAR switches, bump both cache versions, rederive rows, rebuild classification, and fill price holes.

- [ ] **Census mixes other merged PRs' effects.** This is the first post-pull `ingest --source edgar`. It derives under every merged rule at once, so its EDGAR diff, retract list, resolution repair and hole list are not T198's alone. Each rule rides on the same derivation, so none can have a release of its own, and rederive-1682's counts include all of them:
  - **T198 (#2975).** The plan's key expects C1/C3 **265**, H **183**, E2 **270** and F **142** member-months, as diagnostic targets. E1's **144** is excluded because T195 is parked.
  - **#3064, the post-bankruptcy successor (master rule).** Up to 33 cross-exchange successors, an upper bound; the EDGAR dry run lists them. GPOR's old id loses **1,375** bar sessions, refetched under `0000874499@2021-08-09` from 08-09. The rule-7 cut removes **8** bar sessions: WW `0000105319` 5 and `0001844505` 3. Before the sitting, `health` `spliced_series` showed 89 gaps on 88 ids at 5x.
  - **#3095, issuer HTML Form 25s and the unit/comma class rule.** About 2,660 HTML Form 25 fetches (about 5 min) and about 800 `unread HTML delistings`. Delistings rise by roughly 1,000–2,000, and the class rule adds at most 126 gains.
  - **#3091 (only if in the pull).** The rule-8 wait moves about **12,460** classification rows (12,452 securities) to `no_sic_or_periodic`. At most **6,889** bars since 2016 fall in those spans. It closes 509 SPAC-shell `common_default` spans, and every row gets an `issuer_class` tag. It needs this step's `--rebuild-classifications`; without the rebuild, an incremental ingest would stamp changed tags at its own time.
  - **#3068.** Should add nothing beyond step 8's rows.
  - **#3090 and #3086.** Their holes were filled in steps 6 and 7. A hole the store-wide fill lists again on those ids is a month Alpaca has no bars for.

  Record each count against the source above that explains it, and name any count none of them explains. The actual expected row and bar counts come from the agreed censuses: **census first**. T184h stays out unless its own gates and questions 22–24 are satisfied. Record `health --coverage` first.
- [ ] Take the release checkpoint and snapshot the books before any EDGAR write (#3064, #3095). Both windows are closed, so expect no open lot, but run the check anyway:
  - **BOOK IDS** (read-only SQL): every `security_id` with an open lot or a latest `positions_daily` row, per book, with each id's current listing `end_session` (NULL if open). Save the list.
  - Add #3064's ids `0000874499`, `0000105319` and `0001844505`, and every old id the EDGAR dry run makes a successor of or that `repair-resolution`'s dry run names. An open lot on any of them is a **STOP**: do not write, and report it on #3056.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rederive-1682 --backup <rederive-copy> --reason "#1682 C1 C3 H E2 F; T195 parked; first post-pull EDGAR write (#3064 #3095)"
  uv run tradepartner paper status --all
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner ingest --source edgar --rebuild-classifications --dry-run
  uv run tradepartner repair-resolution --dry-run
  uv run tradepartner ingest --source edgar --rebuild-classifications
  ```

  Read `unread HTML delistings`, `pre-XML delistings` and the delistings/listings row counts in the EDGAR dry run. If the EDGAR ingest refuses on the build fingerprint and asks for `--rebuild-index`, stop and ask; do not add it from this sheet.
- [ ] **BOOK CHECK** (#3095; read-only SQL, right after the EDGAR write, before any other command):
  - (a) `SELECT security_id, form, filed_at, effective_on FROM delistings WHERE ingested_at = <this run's ingested_at> AND security_id IN (<BOOK IDS>);`
  - (b) Listing ends are the source of forced exits (`execution/exits.py`). Check for any `listings` row on a BOOK ID whose `end_session` is new, or earlier than in the pre-write snapshot.

  Any row in (a) or (b) is a **STOP**: restore as in **Restore and gaps** using `<rederive-copy>`, after saving the failed state, and report it on #3084. Keep paper jobs unloaded throughout. Do not run `data-release close` after the restore. Then continue:

  ```bash
  uv run tradepartner master-retract
  uv run tradepartner master-retract --apply --expect-rows <dry-run-rows> --expect-digest <dry-run-digest> --release rederive-1682
  uv run tradepartner repair-resolution --dry-run
  uv run tradepartner repair-resolution --expect-bar-rows <dry-run-bars> --expect-action-rows <dry-run-actions> --release rederive-1682
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --dry-run
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes
  uv run tradepartner health
  uv run tradepartner health --check
  uv run tradepartner health --coverage
  uv run tradepartner decision data-release close --name rederive-1682 --from 2016-01-04 --to <last-touched-session> --reason "#1682 actual rows by source PR, read-time merges and trial replay rule"
  ```

  Apply `master-retract` and `repair-resolution` **only if** their dry runs find rows. A new Form 25 can end a listing earlier than today, so the retract list need not be empty: each listed row should be a listing after its new Form 25's effective day. A traded-row refusal requires a decision on #1682; never add `--allow-traded` from this sheet. Run `uv run tradepartner ingest --source openfigi` only if T189c's owner run is approved and include its `figi_types` block. `health`'s `spliced_series` is report-only: read what is left. CBL stays until its Form 25 is in (#3065). Do not run the `paper reconcile` lines from #3064's and #3095's PR bodies here: `paper reconcile` refuses `no_window` while both windows are closed. The restart covers it.
- [ ] Stop for a common CIK losing its primary id, a class gaining a unit/note/warrant title, a newly unparseable universe CIK, changed `filing_name` or `snapshot_static` `known_at`, a T197 key reappearing, a traded-row refusal, a hole count outside the census, or failed health. Follow **Restore and gaps** using `<rederive-copy>`. Paste the EDGAR listing/shares diff against the backup, split by source PR as above. Also paste the retract and repair counts, the hole and resolver counts, `spliced_series` before/after, coverage buckets before/after, `figi_types` if run, the BOOK CHECK result and release ids, on T198's PR, #1682, #3056 and #3084.

## 10. #3056: `splice-3056` (residual only, #3064)

Purpose: apply what is left of #3064's post-bankruptcy repair after step 9. Step 9's EDGAR write already applied the master rule, and its `master-retract`, `repair-resolution` and `--fill-holes` ran store-wide. So this step only proves nothing is left, and writes only what is.

- [ ] Dry runs first; no release is needed for them:

  ```bash
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner master-retract
  uv run tradepartner repair-resolution --dry-run
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --dry-run
  uv run tradepartner health
  ```

  Expect no new successor, no retract row, no resolution row and no new hole. GPOR's old id should end at its own last print, and WW's 07-07..07-11 bars should be gone. If every dry run is empty, open no release and paste the outputs and `spliced_series` on #3056.
- [ ] Only if a dry run lists rows, re-run step 9's BOOK IDS check on the listed ids, then write under its own release:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name splice-3056 --backup <splice-copy> --reason "#3056 post-bankruptcy successor splice, residual after rederive-1682"
  uv run tradepartner master-retract --apply --expect-rows <n> --expect-digest <d> --release splice-3056
  uv run tradepartner repair-resolution --expect-bar-rows <n> --expect-action-rows <n> --release splice-3056
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes
  uv run tradepartner health
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name splice-3056 --from <first> --to <last> --reason "#3056 counts"
  ```

  Run only the lines whose dry run listed rows. A residual EDGAR write means step 9's derivation did not settle: stop and ask before writing it. Follow **Restore and gaps** using `<splice-copy>`.

## 11. #3084: `form25-3084` (residual only, #3095)

Purpose: apply what is left of #3095's Form 25 repair after step 9. Its EDGAR write and `master-retract` were part of step 9, which ran #3095's BOOK CHECK and STOP.

- [ ] Dry runs first; no release is needed for them:

  ```bash
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner master-retract
  ```

  The second EDGAR run reads the HTML Form 25s from the `DELISTING_VERSION` cache and the unread ones from `delisting_html_unread/v1/`, so it fetches none again. Expect no new delistings row and no retract row. If both are empty, open no release and paste the outputs on #3084.
- [ ] Only if `master-retract` lists rows: re-run step 9's BOOK IDS check and BOOK CHECK on the listed ids, then:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name form25-3084 --backup <form25-copy> --reason "#3084 issuer HTML Form 25s, unit/comma class titles, residual after rederive-1682"
  uv run tradepartner master-retract --apply --expect-rows <n> --expect-digest <d> --release form25-3084
  uv run tradepartner health
  uv run tradepartner decision data-release close --name form25-3084 --from <first> --to <last> --reason "#3084 counts"
  ```

  A residual EDGAR write means step 9's derivation did not settle: stop and ask. On a STOP, follow #3095's restore, which is **Restore and gaps** using `<form25-copy>`. Keep paper jobs unloaded and confirm `pgrep -f "tradepartner "` is empty, save the failed state first, check the manifest's `wal_bytes`, and do not run `data-release close`. Record the rollback and removed release id on #3084. T248f's merger scan (`ingest --source alpaca --actions-backfill --since 2020-06-01`) is owner-run and not part of this sitting.

## 12. #3058: `spinoff-3058` (#3067)

Purpose: delete the 33 spin-offs stored as splits (2016-04-04..2021-11-02), which put fake returns into their parents' 2016–2021 prices. Then ask Alpaca per symbol for the pre-2020-11 spin-offs the store never received. It runs before steps 13 and 14, so their split counts read a store without the fake splits. Ingest already refuses the listed rows, so a refetch never brings them back. `adjust.apply_spin_offs` stays `false`; no registered trial moves except through this release's vintage.

- [ ] Census first. The repair dry run should print `would delete 33 rows of 33 identities, 2016-04-04..2021-11-02, stale 0`. The backfill's count is known only from its dry run, which prints per-month spin-off counts at about 5 requests a month. Check these by hand in the backfill dry run: HON's 3 rows (2016-10-03, 2018-10-01, 2018-10-29), GE→WAB 2019-02-25 and HWM→ARNC 2020-04-01. Run the repair before the backfill.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name spinoff-3058 --backup <spinoff-copy> --reason "#3058 spin-offs stored as splits; pre-floor spin-off backfill"
  uv run tradepartner repair-spinoff-splits --release spinoff-3058 --dry-run
  uv run tradepartner repair-spinoff-splits --release spinoff-3058 --expect-action-rows 33
  uv run tradepartner spinoff-backfill --since 2016-01-04 --through 2020-10-31 --dry-run
  uv run tradepartner spinoff-backfill --since 2016-01-04 --through 2020-10-31 --release spinoff-3058
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name spinoff-3058 --from <earliest-touched-ex-date> --to 2021-11-02 --reason "#3058 33 split rows deleted, N spin-offs added"
  ```

- [ ] Stop when the repair dry run shows a count other than 33 or any stale entry. Also stop when the backfill dry run lists `split_spinoff_collisions` above 0: review those rows (add them to the CSV, or decide on #3066) before the real run, which refuses that month. Stop too on failed health. Follow **Restore and gaps** using `<spinoff-copy>`. Paste both dry runs, the deleted and added counts, the three hand checks and release ids on #3058.

## 13. #3054: `actions-3054` (missing splits and dividends)

Purpose: store the pre-2020 name changes, then ask Alpaca again for the splits and dividends of committed months, so a renamed security's pre-rename symbol carries its actions (`ListingResolver.action_alias`). Also retract the STX 2021-05-11 duplicate, which is now on the reviewed list.

- [ ] Census first: dry-run both steps and read the counts before writing. The measurement (#3053, 11-02 store) put 307 missing dividends and 61 missing splits under an old symbol the master never lists. That is the most this can recover. Rows with no rename (SPY 2016-03-18, 2018-06-15) are expected to stay missing unless Alpaca serves them now.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name actions-3054 --backup <actions-copy> --reason "#3054 name changes 2016-2020 and actions refetch"
  uv run tradepartner ingest --source alpaca --actions-backfill --name-changes-only --since 2016-01-01 --through 2020-05-31 --dry-run
  uv run tradepartner ingest --source alpaca --actions-backfill --name-changes-only --since 2016-01-01 --through 2020-05-31
  uv run tradepartner refetch-actions --since 2016-01-04 --through <last committed backfill day> --release actions-3054 --dry-run
  uv run tradepartner refetch-actions --since 2016-01-04 --through <last committed backfill day> --release actions-3054
  uv run tradepartner repair-dividends --release actions-3054 --dry-run
  uv run tradepartner repair-dividends --release actions-3054 --expect-action-rows <dry-run-row-count>
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name actions-3054 --from 2016-01-04 --to <last touched ex-date> --reason "#3054 added actions by type, STX retract"
  ```

- [ ] Name-change step: read `symbol_change_backdated` and `actions_unresolved` on each month's run row. Refetch step: the dry run's last line names the rows per type (`would add N dividend, M split`). Stop when splits outnumber the census's 61 + 26, or when an added split lands on a name with no matching raw price gap. Paste both summaries, the repair count and `health --check` on #3054. `repair-dividends` expects exactly STX's one row plus any duplicates the list already holds and the store still has.

## 14. #3074: `actions-3074` (span refetch for actions with no rename)

Purpose: tell vendor absence from late processing for the rows step 13 cannot reach, then store what Alpaca serves. Alpaca filters on `process_date`. A month request asks only `alpaca.actions_process_lag_days` (90) past the month, so a record Alpaca processed later is in no month's answer. `refetch-actions --span` asks once over the whole range for the named securities. It asks every symbol each one held and every process date up to today (`backfill.span_settings`). Timing is unchanged: a first-seen row is stamped at the close of the session before its ex-date.

- [ ] Run it after step 13's release is closed. The ids are SPY (`BENCH:SPY`) plus the 319 securities of the #3053 measurement's "no later rename" and "old symbol known" groups. The list is a local file from the #3074 diagnosis (`spyacts3074-ids.txt`, comma-separated; the figures stay local). HTA (`0001360604`) is left out: its 26 "missing" dividends are old Healthcare Realty's, not HTA's (#3074 PR).

  ```bash
  uv run tradepartner refetch-actions --span --since 2016-01-04 --through <last committed backfill day> --release actions-3074 --security "$(cat <ids file>)" --dry-run
  # only when the dry run lists rows to write:
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name actions-3074 --backup <actions-copy> --reason "#3074 span refetch, no-rename actions"
  uv run tradepartner refetch-actions --span --since 2016-01-04 --through <last committed backfill day> --release actions-3074 --security "$(cat <ids file>)"
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name actions-3074 --from 2016-01-04 --to <last touched ex-date> --reason "#3074 added actions by type"
  ```

- [ ] Read the dry run before writing. It prints one `would write <tag> <security> <type> <ex-date> <amount> known_at=<stamp>` line per row, then the totals. The tag is `new`, `revision` (a stored identity with changed values, stamped at the run) or `collision` (a new id on a type and ex-date the security already holds live, which would apply the event twice). The census is at most 225 + 74 dividends and 70 + 26 splits. Check SPY first:
  - **`new BENCH:SPY dividend 2016-03-18` and `2018-06-15` listed:** late processing. Write, and paste both lines on #3074.
  - **Not listed:** Alpaca does not serve them. Do not enter them by hand; the fallback proposed on the #3074 PR is a separate, reviewed change.
  - **Stop and do not write when:** any line is tagged `revision` or `collision`, or a `new` split lands on a name with no matching raw price gap. Paste the dry run on #3074 instead, and open no release. The dry run needs none.
- [ ] Paste the run row message (its resolver summary counts the unresolved rows by symbol) and the summary line on #3074. Unresolved rows under a security's later symbol are evidence that Alpaca files history under the new symbol; they are never written.

## 15. #3057: `rename-listings-3057b` (residual after step 13)

Purpose: add the new-ticker rows implied by the 2016–2020 name changes that step 13 stored. Step 8 ran before those changes existed, and with jobs stopped no EDGAR build has read them since.

- [ ] Dry run first; it needs no open release:

  ```bash
  uv run tradepartner repair-rename-listings --release rename-listings-3057b --dry-run
  ```

  If it lists no row, open no release and paste the output on #3057. If it lists rows, read each one as in step 8, then:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rename-listings-3057b --backup <rename-b-copy> --reason "#3057 rows from step 13's name changes"
  uv run tradepartner repair-rename-listings --release rename-listings-3057b --expect-rows <dry-run-rows>
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name rename-listings-3057b --from <earliest-touched-session> --to <last-touched-session> --reason "#3057 residual rows and refusals by reason"
  ```

  Stop rules and restore as in step 8, using `<rename-b-copy>`.

## 16. Desk-read exposures: candidate 5 (#3116, #3176)

Purpose: write the two `research_exposures` rows for candidate 5's store launches (#2689). That table had 0 rows on 2026-11-07. Profitability's N is 1 (B3's trial 3) where the rule gives 3. Each launch is one `store` record with 1 configuration, so N goes from 1 to 3. These are research-registry rows, not store facts: no data release, and no `store backup` of their own. The #3116 backfill tool will not exist (#3133 closed as oversized), so this step uses the existing `tradepartner exposure record`.

**Not recorded here** (an `open_data` record adds 0 to N, research-registry spec "How it enters N"):
- **#3092 (#3110).** The record is not cheap, so it is skipped. Its kill line is in the issue body, and `--kill-line-url` takes only an issue comment or a file at a commit. Its window and configuration count also need calls nobody has made. Leave #3110 open and note the skip there.
- **CBOperProf, I1, issuance/Q1, XFIN, composite and cfp** stay on #3116, not recorded.

**No dry run.** `exposure record` has none, and every call writes the real store. The rules that protect it:
- It refuses with exit 2 and writes nothing on a bad value: a probe hash mismatch, a probe that isn't verified, a naive time, a window after `read_at`, a missing `store_max_ingested_at` pin, a family or verdict outside its set, or a holdout overlap without a reason.
- A repeat with the same candidate, probe hash and `read_at` prints the first id and writes nothing, so re-running a command is safe.
- There is no delete. A wrong row stays, so do every check below first.

Every value below comes from the record named beside it:

| Field | Launch 2 | Launch 3 | Evidence |
|---|---|---|---|
| kill line | [#2689 comment 6548103186](https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186), posted 2026-10-31T19:29:02Z, never edited | same | `gh api repos/josejuarez96/tradepartner/issues/comments/6548103186` (`created_at` = `updated_at`) |
| kill-line copy | `docs/research/2026-10-31-candidate-5-kill-line.md`, the comment body byte for byte, SHA-256 `7a2b91fb5ce5427548091a5efd3d669d138b05f68103fd1eeb7810b5f91a15d6` | same | the same `gh api` call |
| probe | the frozen file, SHA-256 `916339ee…a770`, gone from disk. It is rebuilt by reverting the crash-only hunk. Its hash is in the kill line, so `probe_verified` | `~/tradepartner-probes/cbl/probe.py`, SHA-256 `bb32fdb0…fb08b`, mtime 2026-10-31T19:31:44Z, before the launch, so `probe_verified` | audit [comment 6548223852](https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548223852) §1; the desk read's launch ledger |
| `read_at` (launch start) | 2026-10-31T19:29:43.089Z. It crashed at 19:31:30.937Z: `RuntimeError: no last close for held 0001235010` | 2026-10-31T19:31:49.105Z. It finished at 19:36:01.497Z | the builder's Codex session log, `~/.codex/sessions/2026/10/31/rollout-2026-10-31T15-29-17-01a19370-0f6b-7a60-8734-9b20e045d300.jsonl` (tool-call times). Launch 1 failed in `uv` before Python started and read nothing |
| window | 2020-08-31 to 2023-12-29 | same | kill line; spec backfill table |
| configurations, verdict | 1, `incomplete` | 1, `fail` | spec backfill table; desk read |
| family | `profitability` | same | spec backfill table. The store's profitability holdout is 2024-01-01..2026-09-30 (read-only, 2026-11-07), so there is no overlap and no `--holdout-reason` |
| store read | `data/tradepartner.duckdb.bak-20261031-0441`, read-only. The path is relative to the main checkout; the launch read `/Users/josejuarez/Projects/tradepartner/data/…` | same | the `--store` argument in the session log |
| `store_max_ingested_at` | `2026-10-31T00:03:40.029464+00:00`, declared | same | that copy is gone, so the value was rebuilt read-only from the live store on 2026-11-07. The spec's fallback (line 169) filters on `ingested_at <= read_at`, but that would count rows written after the copy was taken. The filter here is the copy's time, 04:41Z: `store backup` names copies in UTC. 00:03:40.029464Z is the newest `ingested_at` before it. Nothing wrote in the gap from 00:03:40Z to 16:21:12Z: no `ingestion_runs` row started, and no data release was open (the store's release log, read 2026-11-07: the last before it, `stockdiv-1618`, closed 10-14T19:38Z; the next, `roles-2691`, opened 10-31T23:27Z). The only releases since are `roles-2691` (10-31T23:27Z-23:40Z) and `restamp-1382b-retry` (11-04T02:25Z-07:20Z); `dupdiv-1613` closed 10-14 and `restamp-1382a3` has no release row. Both restamp rows to their own, later clock, which can move rows out of the gap but never into it. 2.67M rows still carry 00:03:40.029464Z, so the max holds. Had `0441` been EDT (08:41Z), the gap would give the same value |
| inputs | none hashed. `companyfacts.zip` was replaced 2026-11-07T00:31:10Z, so today's hash is not the hash that was read; it is a declared pin instead | same | `stat` |
| desk read, audit | `docs/research/2026-10-31-cash-profitability-long-only-desk-read.md`, audit comment 6548223852 | same | #2704 |

- [ ] **Read-only checks first.** Run from the job checkout root (the kill-line copy path is relative). Stop if any output differs from the expected value given beside it.

  ```bash
  uv run tradepartner exposures
  uv run tradepartner exposures --candidates --since 2026-10-30
  uv run python -c 'from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; from tradepartner.backtest.results import family_n_split; cm = open_read_only(get_settings()); conn = cm.__enter__(); print(family_n_split(conn, "profitability")); print(conn.execute("SELECT exposure_id, candidate_ref, read_at, probe_sha256 FROM research_exposures WHERE candidate_ref = ? ORDER BY exposure_id", ["#2689"]).fetchall()); print(conn.execute("SELECT DISTINCT holdout_start, holdout_end FROM hypotheses WHERE family = ? ORDER BY 1", ["profitability"]).fetchall()); cm.__exit__(None, None, None)'
  printf '%s  %s\n' 7a2b91fb5ce5427548091a5efd3d669d138b05f68103fd1eeb7810b5f91a15d6 docs/research/2026-10-31-candidate-5-kill-line.md bb32fdb0587cfecc35e9cafd41926ab9d760c19271364fdebe55463b357fb08b ~/tradepartner-probes/cbl/probe.py | shasum -a 256 -c
  TZ=UTC stat -f '%Sm %N' -t '%Y-%m-%dT%H:%M:%SZ' ~/tradepartner-probes/cbl/probe.py
  ```

  Expected:
  - Each `exposures` command prints its header line and no rows (or see **Rows already there**).
  - The snippet prints `FamilyN(trials=1, research=0, exposures=0)`, then `[]` (or see **Rows already there**), then `[(datetime.date(2024, 1, 1), datetime.date(2026, 9, 30))]`.
  - **Rows already there.** Say the only rows are `#2689` rows whose `read_at` and `probe_sha256` equal launch 2's (19:29:43.089Z, `916339ee…`) or launch 3's (19:31:49.105Z, `bb32fdb0…`). Then this step already ran: run only the commands for a launch that has no row, then go to **After**. Any other row, for any candidate, is a stop. Paste it on #3116 and write nothing, because a new record beside it could not be deleted.
  - A different holdout line is a stop: the write would refuse or count a holdout spend.
  - `shasum -c` prints `OK` for both files.
  - The mtime is `2026-10-31T19:31:44Z`. A later mtime means the file was touched, and launch 3 would be refused. Stop.

- [ ] **Rebuild the frozen probe** beside the current one. It writes only `~/tradepartner-probes/cbl/probe-frozen-916339ee.py`. It refuses unless the hunk is found exactly once and the result hashes to the kill line's `916339ee…a770`. A rerun rewrites the same bytes.

  ```bash
  uv run python -c 'import hashlib, pathlib; d = pathlib.Path.home() / "tradepartner-probes/cbl"; s = (d / "probe.py").read_bytes(); i = b" " * 12; old = i + b"# The holding value is already marked at the previous month close.\n" + i + b"# If no later traded bar is available, settle at that last close.\n" + i + b"cash += value\n"; assert s.count(old) == 1, "crash-fix hunk not found exactly once"; r = s.replace(old, i + b"raise RuntimeError(f\x27no last close for held {sid}\x27)\n"); h = hashlib.sha256(r).hexdigest(); assert h == "916339eea57d431558532680662516285cdf16b6e3c0c52edad69086089ba770", h; out = d / "probe-frozen-916339ee.py"; assert not out.exists() or out.read_bytes() == r, "a different probe-frozen file is already there"; out.write_bytes(r); print(out, h)'
  ```

- [ ] **Record launch 2**, the crashed run on the frozen probe:

  ```bash
  uv run tradepartner exposure record --candidate "#2689" --source store --family profitability \
    --kill-line-url "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186" \
    --kill-line-file docs/research/2026-10-31-candidate-5-kill-line.md \
    --kill-line-posted-at 2026-10-31T19:29:02+00:00 \
    --probe ~/tradepartner-probes/cbl/probe-frozen-916339ee.py \
    --probe-sha256 916339eea57d431558532680662516285cdf16b6e3c0c52edad69086089ba770 \
    --read-at 2026-10-31T19:29:43.089+00:00 \
    --window-start 2020-08-31 --window-end 2023-12-29 \
    --pin store_max_ingested_at=2026-10-31T00:03:40.029464+00:00 \
    --pin store_file=data/tradepartner.duckdb.bak-20261031-0441 \
    --pin "companyfacts_zip=data/edgar_cache/bulk/companyfacts.zip (sha256 at read not recorded; file replaced 2026-11-07T00:31:10Z)" \
    --configurations 1 --verdict incomplete \
    --desk-read docs/research/2026-10-31-cash-profitability-long-only-desk-read.md \
    --audit-url "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548223852" \
    --note "candidate 5 launch 2 (launch 1 failed in uv before Python). read_at is the tool-call start in Codex session 01a19370-0f6b-7a60-8734-9b20e045d300; crashed 19:31:30.937Z with RuntimeError no last close for held 0001235010, a return exposure per the audit. Probe rebuilt by reverting the crash-only hunk; its hash is the kill line's. store_max_ingested_at: the 0441 copy is gone; rebuilt read-only from the live store on 2026-11-07 as the newest ingested_at before 04:41Z; the filter is the copy time, not read_at, since rows written after 04:41Z were not in the copy; no ingestion run started 00:03:40Z-16:21:12Z on 10-31 and no data release was open in that gap (stockdiv-1618 closed 10-14, roles-2691 opened 10-31T23:27Z); the only releases since, roles-2691 and restamp-1382b-retry, restamp only to their own later clock (roles-2691 restamped 30,439 Form 4 rows), and 2.67M rows keep 00:03:40.029464Z (#3176)"
  ```

- [ ] **Record launch 3**, the completed run on the audited probe:

  ```bash
  uv run tradepartner exposure record --candidate "#2689" --source store --family profitability \
    --kill-line-url "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186" \
    --kill-line-file docs/research/2026-10-31-candidate-5-kill-line.md \
    --kill-line-posted-at 2026-10-31T19:29:02+00:00 \
    --probe ~/tradepartner-probes/cbl/probe.py \
    --probe-sha256 bb32fdb0587cfecc35e9cafd41926ab9d760c19271364fdebe55463b357fb08b \
    --read-at 2026-10-31T19:31:49.105+00:00 \
    --window-start 2020-08-31 --window-end 2023-12-29 \
    --pin store_max_ingested_at=2026-10-31T00:03:40.029464+00:00 \
    --pin store_file=data/tradepartner.duckdb.bak-20261031-0441 \
    --pin "companyfacts_zip=data/edgar_cache/bulk/companyfacts.zip (sha256 at read not recorded; file replaced 2026-11-07T00:31:10Z)" \
    --configurations 1 --verdict fail \
    --desk-read docs/research/2026-10-31-cash-profitability-long-only-desk-read.md \
    --audit-url "https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548223852" \
    --note "candidate 5 launch 3, after the crash-only edit (probe mtime 19:31:44Z). read_at is the tool-call start in Codex session 01a19370-0f6b-7a60-8734-9b20e045d300; finished 19:36:01.497Z, output in out.txt. store_max_ingested_at as launch 2's record (#3176)"
  ```

  Each command prints `exposure <id>`. A refusal exits 2 and a busy store exits 1. Either way nothing is written: stop and paste the output on #3116. If launch 2 wrote and launch 3 refused, launch 2's row stays (there is no delete), and N is 2. Paste both outputs on #3116 and run nothing more here.

- [ ] **After.** Re-run the read-only checks:

  ```bash
  uv run tradepartner exposures
  uv run tradepartner exposures --candidates --since 2026-10-30
  uv run python -c 'from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; from tradepartner.backtest.results import family_n_split; cm = open_read_only(get_settings()); conn = cm.__enter__(); print(family_n_split(conn, "profitability")); print(conn.execute("SELECT exposure_id, kill_line_basis, kill_line_sha256 FROM research_exposures WHERE candidate_ref = ? ORDER BY exposure_id", ["#2689"]).fetchall()); cm.__exit__(None, None, None)'
  ```

  Expected:
  - `exposures` prints two rows, newest first, exactly these after the id:
    - `#2689  store  profitability  2026-10-31T19:31:49.105000+00:00  2020-08-31..2023-12-29  1  fail  -  verified  real`
    - `#2689  store  profitability  2026-10-31T19:29:43.089000+00:00  2020-08-31..2023-12-29  1  incomplete  -  verified  real`
  - One candidate line: `#2689  2026-10-31T19:29:43.089000+00:00  2  incomplete,fail  no`.
  - `FamilyN(trials=1, research=0, exposures=2)`, so profitability N goes from **1 to 3**. Both rows show `posted_before_read` and kill-line hash `7a2b91fb5ce5427548091a5efd3d669d138b05f68103fd1eeb7810b5f91a15d6`.

  Any other figure is a stop. Paste every printed output on #3116: the two exposure ids against launches 2 and 3, the before and after listings, and N. On #3110, note that #3092 was not recorded, and why (above). Citing the ids in the desk-read note and in `docs/research/claims.toml` is a later PR.

## 17. Gap sign-off replay (#3117)

Purpose: give each book a survivorship-gap sign-off computed on the post-sitting store. `paper start` checks only that some `gap_signoff` row points at an ok in-sample trial of the hypothesis (`execution/window.py`, `_gap_signoff_ok`). It does not check which store that trial read. The current sign-offs are from before steps 1–15: main (hypothesis 1) rests on decision 4, and daily (hypothesis 9) rests on trial 17 and decision 7, which was never replayed. **Restart nothing on a sign-off computed before the sitting.**

- [ ] Run this after every release above is closed (and after the #3070 flip, if it was pulled). Read both current sign-offs, read-only:

  ```sql
  SELECT od.decision_id, od.hypothesis_id, od.trial_id, od.made_at, od.values_json, od.reason,
         t.started_at, r.gap_max_count_share
  FROM owner_decisions od
  JOIN trials t ON t.trial_id = od.trial_id
  JOIN trial_results r ON r.trial_id = od.trial_id
  WHERE od.kind = 'gap_signoff' AND od.hypothesis_id IN (1, 9)
  ORDER BY od.decision_id;
  ```

  Save each row's `start_session`, `end_session` and `count_share_threshold` from `values_json`.
- [ ] **Item to resolve: main's decision 4.** It stores `gap_max_count_share` **0.0878** against a `count_share_threshold` of **0.05**, while its reason says "about 0.036 (#1216), under 0.05". The owner records on #3117 which figure decision 4 meant. The stored row stays as written, since decisions are append-only. If main restarts at all (only as a relabelled execution shakedown; see **Restart and close**), the replay below governs it, not either old figure. If main stays closed, its replay is not needed.
- [ ] Replay each hypothesis over its signed-off window, with no holdout flag and no gap override. Each replay is logged in the trial registry and counts in N.

  ```bash
  uv run tradepartner backtest <saved-main-slug> --start <decision-4 start_session> --end <decision-4 end_session> --note "#3117 post-sitting gap replay"
  uv run tradepartner backtest <saved-daily-slug> --start <trial-17 start_session> --end <trial-17 end_session> --note "#3117 post-sitting gap replay of trial 17"
  uv run tradepartner trials
  ```

  Require each new trial to be `ok`, `in_sample` and not synthetic, with `gap_max_count_share` **under** its hypothesis's `count_share_threshold`. A refused or failed replay, or a figure at or over the threshold, stops that book's restart; record it on #3117. Never add `--override-gap`, `--spend-holdout` or `--holdout-repeat` from this sheet. If a replay refuses on a frozen-key drift (for example after the #3070 flip), stop and ask; do not re-register.
- [ ] Sign off each passing replay, then check that the newest sign-off for each hypothesis is a trial started after the last release's `after` row:

  ```bash
  uv run tradepartner decision gap-signoff --trial <main replay trial> --reason "#3117 post-sitting replay; gap <figure> under <threshold>"
  uv run tradepartner decision gap-signoff --trial <daily replay trial> --reason "#3117 post-sitting replay of trial 17; gap <figure> under <threshold>"
  ```

  ```sql
  SELECT MAX(made_at) FROM owner_decisions
  WHERE kind = 'data_release' AND json_extract_string(values_json, '$.stage') = 'after';
  ```

  Each newest `gap_signoff` trial's `started_at` must be later than that time. Paste both replays (trial id, window, gap figure, threshold) and both decision ids on #3117.

## Restart and close

- [ ] Confirm every opened release has an `after` row, `uv run tradepartner health --check` exits 0, and `uv run tradepartner health --coverage` has the reviewed final buckets. Confirm the counts comment for every release is on #1682. List every stale trial. Besides step 17's two gap replays, replay only H1 and a kill-ladder rung-4 survivor, per ADR 0023. Record delete-only affected sweep decisions under [named data releases](data-releases.md); each rerun counts in N.
- [ ] **Which books restart (orchestrator decision on #3117, 2026-11-06).** H1 is retired by its own rule (TP-12, PR #3123).
  - **Daily** restarts only once step 17's replay of its gap sign-off on the post-sitting store has passed and been signed off.
  - **Main stays closed.** It may restart only if it is first relabelled as an execution shakedown whose results are never read as H1 evidence, and that relabel is recorded on #3117 before `paper start`. Without it, skip main's account check and its `paper start` below.
  - A book whose replay did not pass stays stopped.
- [ ] After the owner accepts the final state, make **each account eligible for its new start**. For a normally `closed` window, the book's stop run has sold its holdings; check the broker is flat apart from the stop row's matched `dust`/`untradable` residues, with no open orders. For an `abandoned` window, the owner resets that book's Alpaca paper account in the dashboard until it has **zero positions and zero open orders**. Check whether the reset changed `account_id` or issued new keys. Confirm the new account id for `main` and `daily`; if keys changed, the owner updates only `.env`: `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET` for `main`, `ALPACA_PAPER_BOOKS__DAILY__API_KEY` and `ALPACA_PAPER_BOOKS__DAILY__API_SECRET` for `daily`. Record names and account ids, never key values. A non-flat account or mismatched account id stops restart.
- [ ] Start daily's window once, and main's only under the relabel above, using the saved hypothesis slugs. Then restart scheduled jobs; keep main's paper job unloaded while main stays closed. `paper reconcile` cannot run between closure and start: it refuses `no_window`; `abandon` already recorded its final reconciliation.

  ```bash
  uv run tradepartner paper start --book daily --hypothesis <saved-daily-slug>
  # only if main was relabelled as an execution shakedown on #3117:
  uv run tradepartner paper start --book main --hypothesis <saved-main-slug>
  uv run tradepartner paper status --all
  uv run tradepartner health --check
  ```

  A refused start stops restart; leave jobs stopped and follow the [paper scheduling runbook](scheduling.md). Paste old/new window ids, final stop/abandon reconciliations, broker flatness or matched residues, account-id checks, final health and coverage, all release ids, and job restart evidence on the sitting PRs. The new windows start a new span; the earlier T162 pass remains the gate for *this* sitting.

## Restore and gaps

With every job still stopped and no DuckDB process holding the store, save the **failed live store and WAL first** using `uv run tradepartner store backup`; label its printed paths as failed-state evidence on the release PR/issue. Then restore the chosen checkpoint. Current `store backup` names a copy `<store>.bak-YYYYMMDD-HHMM` and a WAL copy `<copy>.wal` when one exists; older backups use `<store>.wal.bak-YYYYMMDD-HHMM`. Use their **printed paths**, not a guessed release filename. A copy moved off the volume (see the disk precheck) is restored from its recorded new path:

```bash
CHECKPOINT_COPY="<printed-copy-path>"
CHECKPOINT_WAL_COPY="<printed-wal-copy-path-or-empty-string>"  # use "" when none was printed
rm -f data/tradepartner.duckdb.wal
cp "$CHECKPOINT_COPY" data/tradepartner.duckdb
if [ -n "$CHECKPOINT_WAL_COPY" ]; then cp "$CHECKPOINT_WAL_COPY" data/tradepartner.duckdb.wal; fi
```

Before opening the restored store, compare the checkpoint manifest's `wal_bytes` with the restored WAL's size (`stat -f %z data/tradepartner.duckdb.wal` on macOS). A numeric `wal_bytes` requires a WAL of that size; `null` requires no restored WAL. Stop on a mismatch. Then check health:

```bash
uv run tradepartner health --check
```

Keep all copies. **Restoring a pre-open checkpoint removes that release's `before` row and every later release row or store write.** Do not run `decision data-release close` after restoring: it refuses `no release is open`. Record the rollback and lost release ids on the PR/issue using the saved failed-state copy as evidence. A later attempt uses a fresh release name and, for a3, a fresh precheck. A full unwind uses `<baseline-copy>` and restores both windows to the ended state saved there; it does **not** reopen them. Closing a newly reopened release merely to journal rollback writes an `after` row into `data_vintage` and stales trials, so that is an owner decision, not an automatic recovery step. The general [release runbook](data-releases.md) has the same restore correction.

**Gaps to resolve before execution:**

- The plan says **one backup**, but `decision data-release open` requires a backup of each release's *current* state. This sheet takes one baseline plus named checkpoints. The owner must accept that reconciliation of the plan and registry contract before the sitting.
- #1553/#1582 report an a3 `after` record already written; #1974's pre-2003 decision and census are pending. Verify the real registry and decision before any a3 write.
- **Owner question 1:** T197 step (2) says `paper stop`, while spec question 31(a) says `abandon`; choose and record the route for each book. `stop` sells the paper holdings and ends that window's record; `abandon` leaves positions and requires the owner to reset each account before restart, also ending the old record. An abandoned account must be strictly flat with no open orders, and its new account id and keys must be checked. After either closure, `paper reconcile` refuses `no_window`.
- Restoring a pre-open checkpoint erases its release rows, so `close` cannot journal the rollback afterwards. Record it on the PR/issue and decide separately whether a new closed rollback release is needed; that `after` row stales trials. The general release runbook is corrected in this PR.
- T170d needs a checkpoint even though it is outside a named release; save and verify `<dtb3-copy>` before its FRED write.
- T198 calls for an EDGAR dry-run diff against the backup; the CLI prints run summaries, not a complete row diff. Prepare and review a read-only comparison query before applying. That query must split the diff by source PR (step 9's list).
- **EDGAR-derived changes share one release.** #2975, #3064's master rule, #3095, #3068's `build_master` wiring and #3091 (if merged) all change what `ingest --source edgar` derives. The pull brings them together, so their store effect lands in step 9's single EDGAR write, and rederive-1682's census mixes them. Steps 10 and 11 hold only their residuals. Attribution inside step 9 is by row kind against each PR's measured counts, not by release.
- **Disk (#3115).** 11–12 GiB free on 2026-11-06 is below the precheck's 17.4 GB minimum. Releases 18 and 20 name backups that no longer exist.
- **Restart sign-offs (#3117).** `paper start` does not check that a sign-off's trial read the current store. Step 17 and its SQL check are the only guard until code enforces it.
- No repo command implements the owner-local `dupdiv.py` index check, H.15 hand check, `rates_as_of` probe, or automatic comparison of T214 fingerprints. Record the actual owner queries and outputs with the evidence; do not infer a passing result from `health`.
