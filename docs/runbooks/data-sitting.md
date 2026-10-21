# Run sheet: the one data sitting

Owner and orchestrator only, on the owner's job checkout and real store. Target **2026-11-05**, only after T162's ten-session span has finished and `paper shakedown` exit 0 has been pasted on #1352. A halt restarts that span and moves this sitting. The orchestrator runs each step on the owner's go; the owner decides after each dry run and any stop rule. Follow [named data releases](data-releases.md) for the release record and restore procedure, and [scheduling](scheduling.md) to stop and restart jobs. This sheet records the 2026-10-18 one-sitting order and its 2026-10-20 timing amendment in [the data-foundation plan](../plans/data-foundation.md).

## Before the first write

- [ ] Confirm T162's pasted `paper shakedown` exit 0, the agreed censuses, and the owner's answers to data-foundation spec questions 31 and 35. Check #1974's decision and the running census **before** the a3 step. Do not treat its 37 pre-2003 rows as approved residue without that decision.
- [ ] Confirm T189b, T191, T186/T186b, T190/T190d, T192/T192b, T193 and T194 are merged; T172d's `symbol_changes` backfill is recorded. T195 is parked. T189c and T184h are not gates. Check the release PRs' status before pulling them.
- [ ] Confirm no ingest or sweep is running. `df -h` must show at least **10 GB free**, enough for the full store copy and FSN extraction. Save both window ids and hypothesis slugs from `uv run tradepartner paper status --all` and the owner registry.
- [ ] **Owner question 1 — how will each book close?** Resolve the conflict between T197 step (2), which names `paper stop`, and spec question 31(a), which names `abandon`. Record the chosen route per book on #1682 before touching a window. Both routes **end the existing paper book's record** and a new window starts a new record. Normal `stop` **sells the book's holdings** through the paper order path, with fills and possible trading costs; it may carry only documented `dust`/`untradable` residues. `abandon` makes no trades but leaves the holdings at Alpaca; the owner must reset that paper account in Alpaca's dashboard before a new start, discarding those paper holdings. Neither route preserves the current window's continuous record.
- [ ] For a book using **stop**, complete this on a session *before the sitting's release work*, while its submit window is open. The owner requests the stop, runs the book's ordinary stop run in the submit window, lets the collector reach terminal order events, then calls `stop` again to close. Confirm `paper stop: closed` and the reconciliation and residues in `paper status`. If liquidation or reconciliation cannot complete, stop the sitting and get an owner decision; do not assume the account is flat:

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

  Save the **printed copy path and any printed log-copy path** as `<baseline-copy>` and `<baseline-wal-copy>`. Check the manifest and record the paths, sizes and hashes. The baseline contains the two ended windows; restoring it will restore that ended state. Paste starting coverage, health, closure method, stop/reconciliation ids, broker positions or residues, and baseline path on T197's PR. **Close once here; restart once after all six steps.**

Each named release needs a `before` and `after` record. The `--backup` path is the **copy path printed by `uv run tradepartner store backup` immediately before that release**; `decision data-release open` checks its latest `ingested_at` against the live store. For every backup, record each printed copy/log/manifest path, the copy's `ls -lh` size and `shasum -a 256` hash; repeat for its WAL copy if present. The command refuses an existing target or busy store and verifies the copy byte for byte. Its filename has minute precision: if another backup already used that minute, wait for the next UTC minute and rerun; never reuse or overwrite the old copy. Keep every copy. For every `close`, use the first and last **actual XNYS sessions touched**, not guessed calendar dates. Stop if those are unknown. Never open the next release while one remains open.

## 1. T197: `restate-1682` (#1682)

Purpose: delete only the reviewed never-true master rows (lever B).

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

## 4. T198: `rederive-1682` (#1682)

Purpose: flip the two EDGAR switches, bump both cache versions, rederive rows, rebuild classification, and fill price holes.

- [ ] Check the T198 hold PR contains only the flip and bumps and is pulled **at this step**. T184h stays out unless its own gates and questions 22–24 are satisfied. Record `health --coverage` first. The plan's key expects C1/C3 **265**, H **183**, E2 **270**, and F **142** member-months as diagnostic targets; E1's **144** is excluded because T195 is parked. The actual expected row and bar counts come from the agreed censuses: **census first**.
- [ ] Take the release checkpoint, open, then dry-run and review each write:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rederive-1682 --backup <rederive-copy> --reason "#1682 C1 C3 H E2 F; T195 parked"
  uv run tradepartner ingest --source edgar --dry-run
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
- [ ] Stop for a common CIK losing its primary id, a class gaining a unit/note/warrant title, a newly unparseable universe CIK, changed `filing_name` or `snapshot_static` `known_at`, a T197 key reappearing, a traded-row refusal, a hole count outside the census, or failed health. Follow **Restore and gaps** using `<rederive-copy>`. Paste EDGAR listing/shares diff against the backup, retract and repair counts, hole and resolver counts, coverage bucket before/after, `figi_types` if run, and release ids on T198's PR and #1682.

## 5. T170d: DTB3 backfill (#2011, PR #2084)

Purpose: load FRED DTB3 history, then land the already reviewed default flip. It is outside a data release because the rate table is outside the data vintage.

- [ ] Census first for row counts; the PR expects **zero revisions**. Run the owner's PR #2084 steps in order:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner ingest --source fred --backfill --since 2016-01-04 --dry-run
  uv run tradepartner ingest --source fred --backfill --since 2016-01-04
  uv run tradepartner health --check
  ```

- [ ] Save the printed path as `<dtb3-copy>` before the dry run. Check five H.15 observation dates (2016-01-04, 2020-03-16, 2022-03-11, 2024-01-02, latest stored); `rates_as_of` at the 2022-03-15 close must return 2022-03-11. Run PR #2084's read-only hypotheses SQL for non-default risk-free keys; expect **no rows**. Stop on revisions, missing/unexpected rates, a nonempty SQL result or failed health. Follow **Restore and gaps** using `<dtb3-copy>` if preserving approved earlier releases, or `<baseline-copy>` for a full unwind; keep jobs stopped and record which release state remains. Paste dry-run and run-row counts, latest observation/vintage, five source checks, as-of check, SQL and health on PR #2084 before its owner merges the flip.

## 6. T214 merge (PR #1968, `hold`)

Purpose: attach `data.release_id` to new registrations at the release boundary.

- [ ] Read the latest PR #1968 review and #1958/#1970 resolution. No store command or dry run exists for a merge. Merge and pull **only inside this sitting** after T198's release is closed and its fingerprint evidence can be compared. A pre-release registration must retain its stored hash; a new release registration names a **closed** release and keeps `gap.*` and `adjust.*` frozen under the 2026-10-19 spec decision. Record the re-recorded fixture fingerprints beside the release's. If the PR is not ready, **do not merge**: D8 lapses and the plan needs its dated strike note. Paste the decision and fingerprint comparison on PR #1968/T198.

## Restart and close

- [ ] Confirm every opened release has an `after` row, `uv run tradepartner health --check` exits 0, and `uv run tradepartner health --coverage` has the reviewed final buckets. List every stale trial and replay only H1 and a kill-ladder rung-4 survivor per ADR 0023. Record delete-only affected sweep decisions under [named data releases](data-releases.md); each rerun counts in N.
- [ ] After the owner accepts the final state, make **each account eligible for its new start**. For a normally `closed` window, the book's stop run has sold its holdings; check the broker is flat apart from the stop row's matched `dust`/`untradable` residues, with no open orders. For an `abandoned` window, the owner resets that book's Alpaca paper account in the dashboard until it has **zero positions and zero open orders**. Check whether the reset changed `account_id` or issued new keys. Confirm the new account id for `main` and `daily`; if keys changed, the owner updates only `.env`: `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_API_SECRET` for `main`, `ALPACA_PAPER_BOOKS__DAILY__API_KEY` and `ALPACA_PAPER_BOOKS__DAILY__API_SECRET` for `daily`. Record names and account ids, never key values. A non-flat account or mismatched account id stops restart.
- [ ] Start **both** paper windows once using the saved hypothesis slugs, then restart scheduled jobs. `paper reconcile` cannot run between closure and start: it refuses `no_window`; `abandon` already recorded its final reconciliation.

  ```bash
  uv run tradepartner paper start --book main --hypothesis <saved-main-slug>
  uv run tradepartner paper start --book daily --hypothesis <saved-daily-slug>
  uv run tradepartner paper status --all
  uv run tradepartner health --check
  ```

  A refused start stops restart; leave jobs stopped and follow the [paper scheduling runbook](scheduling.md). Paste old/new window ids, final stop/abandon reconciliations, broker flatness or matched residues, account-id checks, final health and coverage, all release ids, and job restart evidence on the sitting PRs. The new windows start a new span; the earlier T162 pass remains the gate for *this* sitting.

## Restore and gaps

With every job still stopped and no DuckDB process holding the store, save the **failed live store and WAL first** using `uv run tradepartner store backup`; label its printed paths as failed-state evidence on the release PR/issue. Then restore the chosen checkpoint. `store backup` names a copy `<store>.bak-YYYYMMDD-HHMM` and a WAL copy `<copy>.wal` when one exists. Use their **printed paths**, not a guessed release filename:

```bash
CHECKPOINT_COPY="<printed-copy-path>"
CHECKPOINT_WAL_COPY="<printed-wal-copy-path-or-empty-string>"  # use "" when none was printed
rm -f data/tradepartner.duckdb.wal
cp "$CHECKPOINT_COPY" data/tradepartner.duckdb
if [ -n "$CHECKPOINT_WAL_COPY" ]; then cp "$CHECKPOINT_WAL_COPY" data/tradepartner.duckdb.wal; fi
uv run tradepartner health --check
```

Keep all copies. **Restoring a pre-open checkpoint removes that release's `before` row and every later release row or store write.** Do not run `decision data-release close` after restoring: it refuses `no release is open`. Record the rollback and lost release ids on the PR/issue using the saved failed-state copy as evidence. A later attempt uses a fresh release name and, for a3, a fresh precheck. A full unwind uses `<baseline-copy>` and restores both windows to the ended state saved there; it does **not** reopen them. Closing a newly reopened release merely to journal rollback writes an `after` row into `data_vintage` and stales trials, so that is an owner decision, not an automatic recovery step. The general [release runbook](data-releases.md) has the same restore correction.

**Gaps to resolve before execution:**

- The plan says **one backup**, but `decision data-release open` requires a backup of each release's *current* state. This sheet takes one baseline plus named checkpoints. The owner must accept that reconciliation of the plan and registry contract before the sitting.
- #1553/#1582 report an a3 `after` record already written; #1974's pre-2003 decision and census are pending. Verify the real registry and decision before any a3 write.
- **Owner question 1:** T197 step (2) says `paper stop`, while spec question 31(a) says `abandon`; choose and record the route for each book. `stop` sells the paper holdings and ends that window's record; `abandon` leaves positions and requires the owner to reset each account before restart, also ending the old record. An abandoned account must be strictly flat with no open orders, and its new account id and keys must be checked. After either closure, `paper reconcile` refuses `no_window`.
- Restoring a pre-open checkpoint erases its release rows, so `close` cannot journal the rollback afterwards. Record it on the PR/issue and decide separately whether a new closed rollback release is needed; that `after` row stales trials. The general release runbook is corrected in this PR.
- T170d needs a checkpoint even though it is outside a named release; save and verify `<dtb3-copy>` before its FRED write.
- T198 calls for an EDGAR dry-run diff against the backup; the CLI prints run summaries, not a complete row diff. Prepare and review a read-only comparison query before applying.
- No repo command implements the owner-local `dupdiv.py` index check, H.15 hand check, `rates_as_of` probe, or automatic comparison of T214 fingerprints. Record the actual owner queries and outputs with the evidence; do not infer a passing result from `health`.
