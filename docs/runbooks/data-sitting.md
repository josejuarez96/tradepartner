# Run sheet: the one data sitting

Owner and orchestrator only, on the owner's job checkout and real store. Target: the owner's next sitting date, superseding Monday 2026-11-09 (#3245). Check the current windows with `paper status --all` before any closure. The orchestrator runs each step on the owner's go; the owner decides after each dry run and any stop rule. Follow [named data releases](data-releases.md) for the release record and restore procedure, and [scheduling](scheduling.md) to stop and restart jobs. This sheet records the 2026-10-18 one-sitting order and its 2026-10-20 timing amendment in [the data-foundation plan](../plans/data-foundation.md), and the 2026-11-06 Audit F additions (#3114, #3115, #3116, #3117). A rehearsal on a store copy (2026-11-08 evening, steps 1 to 8, #3235) gave the measured times, counts and refusals quoted below as "the rehearsal". A second rehearsal (2026-11-09, steps 9 to 17 and the after-the-sitting dry parts, code `5a619888`) gave those quoted as "the re-rehearsal". It changed the order (step 9 now runs before steps 6 and 7) and several stop rules (#3245). The "final rehearsal" is the 2026-11-14 to 16 run recorded on #3456, ending at code `75c20216` (#3476). It ran every step through 17 and the close; each step passed or stopped where this sheet says to stop. The real sitting is Tuesday 2026-11-17.

**This sheet is the complete list.** Every store repair a merged PR defines for "the sitting" is a step below, in the order to run them. A repair not listed here is not part of this sitting. Run the steps in the order of the table below, which is not the number order: **step 9 runs right after step 5, before steps 6, 7 and 8** (#3245). Step numbers stay as they are because issues cite them. Never reorder beyond that or merge two releases.

| Step | Release | Source | Code |
|---|---|---|---|
| 1 | `restate-1682` | T197, #1682 | pinned checkout (1fe51c9f) |
| 2 | `dupdiv-1613` | T186c, #1613 | done 11-05: verify, skip |
| 3 | `restamp-1382a3` | #1382 | done (toml `after`): verify, skip |
| 4 | no release (FRED) | T170d, #2011 | done 11-05: verify, skip |
| 5 | no release: **the pull** and the schema migration | all of origin/main, at or after `e3a8e092` (#3241) | pinned → origin/main |
| 9 | `rederive-1682`, **run here, right after step 5** | T198 (#2975), plus the first EDGAR write of #3064, #3095 and, if merged, #3091 | origin/main |
| 6 | `classstart-3080` (after step 9) | #3090 (79-id backfill) | origin/main |
| 7 | `biob-3082` (after step 9) | #3086 (BIO.B holes) | origin/main |
| 8 | `rename-listings-3057` (after step 9) | #3068 | origin/main |
| 10 | `splice-3056` (only if residual) | #3064 | origin/main |
| 11 | `form25-3084` (only if residual) | #3095 | origin/main |
| 12 | `spinoff-3058` | #3067 | origin/main |
| 13 | `actions-3054` | #3054, #3073 | origin/main |
| 14 | `actions-3074` (only if rows) | #3074, #3085 | origin/main |
| 15 | `rename-listings-3057b` (only if residual) | #3068 after step 13 | origin/main |
| 16 | no release: candidate 5's exposures | #3116, #3176 (#3110 skipped) | origin/main |
| 17 | no release: gap sign-off replay | #3117 | origin/main |

**The coverage line, after every post-pull repair step (T393, ADR 0029).** After each step that applies a repair under a release, and before its `data-release close`, run `uv run tradepartner decision data-release coverage --name <release> --record`. **Exit 1 is a stop**: lost sessions over a configured limit, a security losing bars no other id holds, a retracted row without a receiving id, or a bar or dividend without an owner. Do not close; name the rows on the step's issue and ask the owner. `data-release close` refuses without a passing record for the store as it stands, so rerun the line after any later write. Step 9 posts its residual count on #1682; step 10 posts its separate residual count on #3056. A step that opens no release (empty dry runs) runs no line. Step 1 uses pinned `1fe51c9f`, which has no `data-release coverage` command; steps 2 and 3 are already done. Orphan bars `repair-resolution` retracts under the owner's exact `--allow-orphans` count are recorded against the release and shown as `accepted by the owner's count N on <release>`; the line exits 0 for exactly those and 1 for any other unreceived retraction.

**Why step 9 goes first (re-rehearsal).** Step 9's rebuild re-keys and retires ids: it retracted P&G's `0000080424:common-stock` (and two more of step 6's 79 ids), and its `repair-resolution` then retracted all of P&G's 2,743 bars, undoing step 6's fill. Its store-wide fill dry run listed P&G under the new id `0000080424` for all 130 months to fetch (the re-rehearsal stopped the apply at month 8, so a full refetch is unmeasured). So steps 6, 7 and 8 run after it, and **every id list and expected count in them is built from the store after step 9**, never from a census taken before it. The re-rehearsal's step 6, 7 and 8 figures below were measured before step 9; treat them as upper bounds, not expectations.

**Time budget (re-rehearsal, measured).** Add the steps you will run; the figures assume nothing stops.
- **Machine time for the sitting: about 10 to 12 hours.** Start about 20:00 ET on Tuesday 2026-11-17, after the nightly ingest (done by 19:53 ET), and finish before 08:00 ET on Wednesday. The final rehearsal's step 17 ran at peak RSS 8.8 GB with 6.7 GB minimum free disk; the owner's go and stop waits come on top.
- Owner go or stop decisions at every step boundary have no measured duration; add that wait to the sitting schedule.
- Step 9: first EDGAR dry run about **2 h** (1 h 52 m: the cache version bumps re-extract every FSN period and refetch every cover page; network-bound, free disk drops about 2.5 GiB); every later EDGAR dry run or write about 28 m; `master-retract` dry run about 25 m; the `--apply` reruns the whole EDGAR fetch pass before it judges, about 25 m more; store-wide hole fill about **3.7 h** (130 months at about 101 s; it re-asks Alpaca for holes it never serves; the run prints one progress line per month, #3239). Step 9 alone is about 7.5 h.
- Step 10: about 58 m of dry runs. Step 11 is **skipped when step 10 opens no release** (its two dry runs are step 10's first two). Step 12: about 24 m. Step 13: about 2 h (the refetch apply is about 66 m, #3311). Steps 14 and 15: about 3 m. Step 16: about 2 m. Step 17: the daily replay about 75 m.
- Steps 6, 7: the rehearsal's 2 h 52 m and about 2.7 h were measured before #3234/#3239 made a month cost what it writes. Step 6's final rehearsal apply wrote zero bars; its elapsed time was not recorded on #3456. Step 7 has not been re-measured. Do not plan on the old figures or on a saving.
- Health on origin/main now takes about 22 s (`health --check`) and 52 s (`--coverage`), as #3233 intended; the 12-minute figure in the first rehearsal is gone.

**Counts per release.** After each `close`, post one comment on #1682 headed with the release name and its `before`/`after` decision ids. It lists every command run in that release, in order, each with its dry-run count and its applied count. A release's figures are the counts of its own commands only; never fold a later step's counts into an earlier release.

## Reading the store (read-only SQL)

Every query this sheet gives runs through `tpsql`: SQL on stdin, parameters as arguments (`$1`, `$2`), one read-only connection, the column names on the first line. Define both helpers in the shell you use, and re-define them in every new terminal:

```bash
tpsql() { uv run python -c 'import os, sys; from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; cm = open_read_only(get_settings()); conn = cm.__enter__(); b = os.environ.get("BEFORE_COPY"); b and conn.execute(f"ATTACH {b!r} AS before (READ_ONLY)"); cur = conn.execute(sys.stdin.read(), sys.argv[1:]); print([d[0] for d in cur.description]); [print(r) for r in cur.fetchall()]; cm.__exit__(None, None, None)' "$@"; }
health_rows() { local out="$HOME/tradepartner-probes/sitting-inputs/health-rows-$1.txt"; [ ! -e "$out" ] || { echo "health evidence exists: $out"; return 1; }; uv run python -c 'from datetime import UTC, datetime; from tradepartner.config import get_settings; from tradepartner.health import integrity_checks; from tradepartner.store.db import open_read_only; s = get_settings(); cm = open_read_only(s); conn = cm.__enter__(); names = {"non_overlapping_listings", "no_bars_after_delisting", "old_id_bars_after_successor"}; checks = [c for c in integrity_checks(conn, datetime.now(UTC), s) if c.rule in names]; [print(c.rule, "COUNT", c.violations.height) for c in checks]; [print(c.rule, sorted(row.items())) for c in checks for row in c.violations.iter_rows(named=True)]; cm.__exit__(None, None, None)' > "$out" || { rm -f "$out"; return 1; }; [ "$(grep -c " COUNT " "$out")" -eq 3 ] || { rm -f "$out"; return 1; }; cat "$out"; }
```

With the environment variable `BEFORE_COPY=<a backup copy path>` set for one call, the queries below can also read that copy as `before.<table>` (read-only). Jobs stopped, so nothing else writes. The queries were checked against `store/schema.py` and a temp store built from it; they were not run on the real store.

## Before the first write

- [ ] **Coverage count fix (#3477, #3479), before closing the books.** The `accept-unreceived` fix (it must use coverage price precision) is merged as #3480, commit `dde99818`; the pinned checkout `1fe51c9f` predates it, so confirm it reached origin/main. Run `git fetch origin`, then `git merge-base --is-ancestor dde99818 origin/main`. Stop before book closure and step 1 if that exits nonzero. After the step 5 pull, run `git merge-base --is-ancestor dde99818 HEAD`; stop before step 9 if that exits nonzero. A **count mismatch** in steps 9 or 10 remains a stop even after the fix.

- [ ] **Ownerless row precheck.** `data-release coverage --record` checks the whole store for bars and live dividends whose id has no `securities` row. Run this read-only query before step 1; it must print no data rows. `BENCH:RSP` is the check-only id excluded by the coverage rule. A row is a stop: name it on #1682 and ask the owner before the sitting.

  ```bash
  tpsql <<'SQL'
  SELECT 'prices_daily' AS source, p.security_id FROM prices_daily p
  WHERE p.security_id NOT IN (SELECT security_id FROM securities)
    AND p.security_id <> 'BENCH:RSP'
  UNION
  SELECT 'corporate_actions', a.security_id FROM corporate_actions a
  WHERE a.action_type = 'dividend' AND NOT a.cancelled
    AND a.security_id NOT IN (SELECT security_id FROM securities)
    AND a.security_id <> 'BENCH:RSP'
  ORDER BY source, security_id
  SQL
  ```

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
  - `C` is the copies this sheet keeps on the volume. That is the baseline, the automatic pre-migration copy of step 5's migration, and one checkpoint each for steps 6, 7, 8, 9, 12 and 13 (8 copies). Add one each for steps 10, 11, 14 and 15 if they write, and one failed-state copy for a restore (13 copies at most).
  - `X` is the FSN re-extraction headroom of step 9's first EDGAR write. This sheet carries 7.6 GB, the earlier "10 GB for one copy plus extraction" less one copy. That figure is not measured.
  - `F` is `ingest.min_free_disk_gb` (5 GB). Ingest refuses below it.

  With every copy kept on the volume: 13 × 2.4 + 7.6 + 5 ≈ **44 GB**. If the owner moves each closed release's copy off the volume, only the open release's checkpoint and a failed-state copy stay. That needs 2 × 2.4 + 7.6 + 5 ≈ **17.4 GB** on the volume and up to about 26 GB on the other one (11 closed copies), or 31 GB if the last checkpoint and the failed-state copy move too. The pre-migration copy moves like a closed release's copy, before step 9's `store backup`. A copy moves only after its release is closed and its `shasum -a 256` is recorded, and moves with its WAL copy and manifest. Re-hash it at the destination; the hashes must match. Record the new path beside the old one. On 2026-11-06 the volume had 11–12 GiB free, below both figures and below `health.warn_free_disk_gb` (15). Free space or attach the second volume before the sitting. Before every later `store backup`, re-run `df -h` and stop if free space is under `S × (copies still to take) + F`, plus `X` until step 9 is closed.

  **Measured in the rehearsal** (S = 2.41 GB, no WAL, 12.63 GiB free at the start). Each `store backup` took about 7 s and 2.25 GiB of free space. The migration's pre-migration copy took the same, in about 4 s. The rehearsal deleted each closed release's copy, which this sheet does not allow without the owner's word. Even so, its low point through step 8 was **7.78 GiB free**, right after the migration, with two copies on the volume: the pre-migration copy and step 6's checkpoint. The fills in steps 6 and 7 grew the store by under 0.1 GiB, but free space dipped 0.9 to 1.4 GiB while each fill ran. Step 1's EDGAR dry run grew the EDGAR cache by 0.26 GiB. **Measured in the re-rehearsal** (20.5 GiB free at the start): step 9's first EDGAR dry run grew the EDGAR cache by about **2.5 GiB** (all of it there; later runs under 0.3 GiB) and the EDGAR write added about 0.05 GiB to the store, so `X` is about 2.6 GB, well under the 7.6 GB carried above. The store-wide fill added 0.02 GiB for 8 months (about 0.4 GiB extrapolated to 130). A checkpoint copy still costs about 2.3 GiB each. Keep the 7.6 GB figure until the real sitting confirms it.
- [ ] **Missing release backups (#3115).** The `before` rows of store releases 18 (`roles-2691`, `data/tradepartner.duckdb.bak-20261031-2319`) and 20 (`restamp-1382b-retry`, `data/tradepartner.duckdb.bak-20261104-0225`) name backups that are not on disk; `data/` holds no backup copy at all. Those releases cannot be restored or audited against their `before` state. Before the sitting, record on each release's issue where its copy went (deleted or moved). Delete none of this sitting's copies without the owner's word.
- [ ] **Owner question 1 — how will each book close?** Resolve the conflict between T197 step (2), which names `paper stop`, and spec question 31(a), which names `abandon`. Record the chosen route per book on #1682 before touching a window. Both routes **end the existing paper book's record** and a new window starts a new record. Normal `stop` **sells the book's holdings** through the paper order path, with fills and possible trading costs; it may carry only documented `dust`/`untradable` residues. `abandon` makes no trades but leaves the holdings at Alpaca; the owner must reset that paper account in Alpaca's dashboard before a new start, discarding those paper holdings. Neither route preserves the current window's continuous record.
- [ ] For a book using **stop**, complete this on a session *before the sitting's release work*, while its submit window is open. The owner requests the stop, runs the book's ordinary stop run in the submit window, lets the collector reach terminal order events, then calls `stop` again to close. Confirm `paper stop: closed` and the reconciliation and residues in `paper status`. If liquidation or reconciliation cannot complete, stop the sitting and get an owner decision; do not assume the account is flat. State on 2026-11-06: daily's window 5 is closed. Main's window 6 is `requested`: the 11-06 stop run sold all 91 (all filled, reconciliation 74 ok), and `paper stop` closes it only after Monday's 08:05 run (state on 2026-11-06; check `paper status --all` for the current state).

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

  In the rehearsal, `store backup` took 7 s, `health --coverage` 47 s and `health --check` 19 s on the pinned checkout.
- [ ] With scheduled jobs stopped for the sitting, expect `store_stale` in health. Record its value; do not confuse it with a new integrity failure.
- [ ] **Open health item.** Check #3474 before the sitting and record the owner's initial decision on #1682 about proceeding while its three non-dividend failures remain. After step 9 and every step run after it (6, 7, 8, then 10–17), run `health_rows <step number>` from the shell function above, even when the step writes nothing. On a skipped step (6, 7, 8, 10, 11, 14 or 15), run `uv run tradepartner health --check` and `health_rows <step number>` before moving on, since its apply block will not run. It saves a `COUNT` line for each of the three rules plus every violation row as `health-rows-<step number>.txt`; stop if it fails or the file lacks three count lines. Show the file to the owner. Step 9 establishes the first approved file: the owner posts its full lines and `shasum -a 256 ~/tradepartner-probes/sitting-inputs/health-rows-9.txt` on #1682, then the operator copies it to `health-rows-accepted-9.txt` in the same folder. For each later step, compare its saved file with the **previous step in run order** (9, 6, 7, 8, 10 through 17) approved file using `diff -u <(sort ~/tradepartner-probes/sitting-inputs/health-rows-accepted-<previous>.txt) <(sort ~/tradepartner-probes/sitting-inputs/health-rows-<current>.txt)`. Show added or removed lines to the owner; each step gets a fresh go or stop decision on #1682. On go, post the current file’s hash and copy it to `health-rows-accepted-<current>.txt` for the next step. A multi-day sitting can change health rows through time alone, even when a step writes nothing; read and approve those diffs too. At restart, compare `health-rows-final.txt` with the last approved step file the same way. The helper refuses to overwrite an existing file; on any rerun after a restore, use a fresh suffix such as `health_rows 9-rerun1`; the next step compares with that latest approved rerun file. Do not treat a rehearsal failure as a pass.
- [ ] **Owner answers needed before step 1 (each is a stop met mid-sitting otherwise, #3245).** Record each on #1682: (1) how the refreshed `store/dividend_reviews.csv` (post-step-9 ids; step 13 stops on a stale entry such as APA's) reaches the checkout, given that nothing merged later is pulled mid-sitting, and the "no `pending` line remains after step 13" expectation depends on it; (2) the margin over 87 allowed for `new` split rows in step 13; (3) the links to the owner's `--allow-traded` decisions for COP, GTX and GPOR's successor (step 9 checks each). The owner approves the rebuilt id lists at steps 6 and 14. Without (1) the sitting is expected to halt at step 13. Confirm `test -f ~/tradepartner-probes/sitting-inputs/final-s9-restate-stop.md` succeeds and that the file describes the 24 accepted rows from the #1682 decision; stop if that comparison evidence is absent.
- [ ] **Baseline health failures.** The baseline store already fails `health --check`. In the rehearsal it exited 1 with `health check failed: duplicate_dividends`. The integrity block showed `duplicate_dividends: FAIL (2 rows)` and the warning `1 listed duplicate dividend keys are pending repair`; no other rule failed. The real store gave this, measured read-only on 2026-11-09 at the pinned 1fe51c9f with the snippet below:
  - **The violation (the FAIL's 2 rows): FCX, `0000831259`, ex-date 2026-10-15, 0.075**, with two identities (`c7a92302-…`, `e490e9c7-…`, both known 2026-10-14T20:00Z). This is #2972's open key. FCX's 2026-10-15 dividend is stored three times: a 0.15 combined record, plus the 0.075 base and the 0.075 variable. The 0.15 record is a different key, outside this one; the two 0.075 records are the two identities here. No reviewed-list row covers the key on origin/main, and a list row needs exactly two identities on the key. No step of this sheet repairs it, and none can be added mid-sitting. **The owner records a decision on #2972 before step 1: accept the key as a known residue for this sitting, with its repair after the sitting.** Without that decision, stop before step 1.
  - **The pending key (the warning): PFLT, `0001504619`, ex-date 2026-10-15, 0.0033**, with two identities. It is a listed `duplicate` (retract id `764fdbfa-…`) in `store/dividend_reviews.csv`. A pending key is a warning, never a failure. Step 13's `repair-dividends` retracts it: that step runs with no `--to`, so it retracts every listed duplicate still in the store.
  - **STX** (`0001137789`, 2021-05-11) is not pending on the pinned code, where the list holds it as `unresolved`. origin/main's list makes it a `duplicate` (#3054), so after step 5's pull it should show as a second `pending` key, which step 13 also retracts. If it shows as a `violation` instead (the retract id is not among its live identities), stop and ask on #3054.

  List the findings read-only, and save the output with the baseline. Rerun it after step 5's migration:

  ```bash
  uv run python -c 'from datetime import datetime, UTC; from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; from tradepartner.health import duplicate_dividends; from tradepartner.store.dividend_reviews import reviewed_groups; cm = open_read_only(get_settings()); conn = cm.__enter__(); b = duplicate_dividends(conn, datetime.now(UTC), reviewed_groups()); [print(g.group_class, g.security_id, g.ex_date, g.amount, [(i.source_action_id, i.known_at.isoformat()) for i in g.identities]) for g in b.groups if g.group_class in ("violation", "pending")]; cm.__exit__(None, None, None)'
  ```

  The 2026-11-09 baseline had exactly the FCX `violation` and PFLT `pending` lines above; after migration it also had STX `pending`. On 2026-11-14 the real store gained PNNT `0001383414`, ex-date 2026-11-13, twice at 0.04: a base and a supplemental dividend, both with record date 2026-11-13 ([8-K](https://www.sec.gov/Archives/edgar/data/0001383414/000117184326007074/exh_991.htm)). It is an expected `violation` on the pinned checkout in steps 1 to 4. #3460 added its `distinct` row to `dividend_reviews.csv`; confirm the row is in the step 5 pull and that this PNNT violation clears on the rerun after migration. If either check fails, stop before step 9 and ask on #2972. PNNT pays base and supplemental dividends monthly. An unreviewed later month's pair is a `violation`: stop before the next write (step 1 on the baseline run; step 9 on the rerun after migration) unless the owner explicitly accepts that key on #2972. Check each month against its evidence and reviewed-list entry; do not assume a later month is reviewed. Any other line is a stop. Paste the output and the PNNT review status on #2972 and #3054.

  Save the baseline's `health check failed:` line, the FAIL row count and every violation key as the **baseline failing rules**. Rerun the snippet whenever `duplicate_dividends` still fails. Before step 9, stop on an unexplained new rule or violation key. After step 13, no `pending` line may remain without an owner decision. FCX (#2972) is accepted residue only with the owner's decision above; review PNNT by month as described above. From step 9 on, use the #3474 decision below.
  **`old_id_bars_after_successor` (#3251).** It flags bars on an old id after a same-CIK, same-class successor's first listing day. It is one of the three observed failures after step 9 and step 6. Record its rows and ask the owner under #3474; do not assume step 10 will clear it.
  **OTC bars do not make a transfer (#3252, PR #3253).** When a Form 25 ends a listing, only sessions that a listing row on an exchange covers count as the security trading on (`_on_exchange` in `store/delistings.py`; the row covering a session is the latest row starting on or before it, and `OTC` and `NONE` rows are off-exchange). A tail of OTC-only bars after the filing leaves the line `delisted`, with no transfer, so `no_bars_after_delisting` can still flag those bars. Do not read an OTC tail as a missed transfer.
- [ ] **Legacy releases (Audit F row 10): run the import, expect a refusal.** `restamp-1382a2`, `restamp-1382a3` and `ops-1549` changed data, but they exist only in `data/releases.toml`, not in the store's `owner_decisions`. So `data_releases(conn)` and `data-release open`'s checks do not see them. With no release open, run the import once:

  ```bash
  uv run tradepartner decision data-release import-file data/releases.toml
  ```

  There is no dry run. The command checks every entry first and refuses, listing every problem and writing nothing. **Expect it to refuse.** In the rehearsal it exited 1 with `release refused:` and only shape problems, which are checked first: `record` entries that lack `['trial']` (entries 8, 17, 18, 19) and `after` entries with unknown keys `['backup_path']` (entries 10, 12, 14, 16). A copy of the file holding only the three releases is refused too, on shape: `ops-1549`'s `record` lacks a trial. With `ops-1549` left out as well, the a2 and a3 entries are refused on date: `an after made at 2026-10-11/12 is after the store gained the release commands (2026-10-09 ...); record it with close`. So `import-file` cannot import these three releases. Paste the printed refusal on #1382 and continue with the three unimported. How, or whether, they reach the store is an owner decision on #1382, outside this sitting. If the command instead prints `imported` lines, paste them and the decision ids on #1382.

Each named release needs a `before` and `after` record. The `--backup` path is the **copy path printed by `uv run tradepartner store backup` immediately before that release**; `decision data-release open` checks its latest `ingested_at` against the live store. For every backup, record each printed copy/log/manifest path, the copy's `ls -lh` size and `shasum -a 256` hash; repeat for its WAL copy if present. The command refuses an existing target or busy store and verifies the copy byte for byte. Its filename has minute precision: if another backup already used that minute, wait for the next UTC minute and rerun; never reuse or overwrite the old copy. Keep every copy. For every `close`, use the first and last **actual XNYS sessions touched**, not guessed calendar dates. Stop if those are unknown. No command prints "sessions touched", so derive them as follows. Move any date that is not an XNYS session to the next session.
- `delete-master-rows` (step 1): the earliest and latest date among the dry run's found lines, using the `listings` rows' `valid_from` and the `delistings` rows' `filed_at` date.
- `ingest ... --fill-holes` (steps 6, 7, 9, 10): the dry run's hole months bound the range, but only the apply knows which sessions got bars. After the apply, with jobs stopped so nothing else writes, read the first and last session it wrote:

  ```bash
  uv run python -c 'import sys; from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; cm = open_read_only(get_settings()); conn = cm.__enter__(); print(conn.execute("SELECT MIN(session), MAX(session), COUNT(*) FROM prices_daily WHERE ingested_at >= CAST(? AS TIMESTAMPTZ)", [sys.argv[1]]).fetchone()); cm.__exit__(None, None, None)' <apply start, UTC ISO time>
  ```

  Take `<apply start>` from `date -u +%Y-%m-%dT%H:%M:%SZ` run just before the apply.
- `repair-rename-listings` (steps 8, 15): the first and last `from <valid_from>` on the dry run's `+` lines.
- `repair-dividends` and `repair-spinoff-splits` (steps 12, 13): the span their summaries print (`on N ex-dates A..B`). `refetch-actions` (steps 13, 14): the ex-date on each `would write` line.
- `spinoff-backfill` (step 12) prints no ex-dates, only one line per month with its rows added. Use the first session of the earliest month whose line shows rows above 0.
- `repair-resolution` (steps 9, 10): the earliest and latest of the per-security `N bar rows A..B` spans in its dry run. `master-retract` (steps 9 to 11): the earliest and latest `valid_from` in the listing keys its dry run prints. Neither summary line prints sessions. A post-apply `prices_daily` read cannot see their deletions.
- `ingest --source edgar` writes (steps 3 and 9): a full rebuild can change listing rows on any date. Use the sheet's stated `--from`, and for `--to` the last XNYS session completed before the write, or the latest session any other command in the release touched if later.
- If a release's fill writes zero bars and no other command in that release touches a session (steps 6, 7 or 10 may reach this case), read `<last committed backfill day>` with the read-only query in step 13. Check it and the store's last bar session with the command below. It moves a non-session day to the next XNYS session and refuses if that day is after the newest stored bar session. On refusal, leave the release open and ask the owner; later releases cannot open. Use its printed session for both `--from` and `--to`, and state "no bars written" in the reason. The `after` row still stales trials even though no bars changed; record that effect. Do not invent a touched span. Record the dry-run holes and applied zero on the step's issue. If the release has any other write, use its actual touched span instead.

  ```bash
  uv run python -c 'import sys; from datetime import date; from tradepartner.calendar import is_session, next_session; from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; day = date.fromisoformat(sys.argv[1]); day = day if is_session(day) else next_session(day); cm = open_read_only(get_settings()); conn = cm.__enter__(); last = conn.execute("SELECT MAX(session) FROM prices_daily").fetchone()[0]; cm.__exit__(None, None, None); assert last is not None and day <= last, "close day is after the newest stored session"; print(day)' <last committed backfill day>
  ```
- Take the earliest and latest over every command in the release. Never open the next release while one remains open. In steps 1 to 5, compare health with the baseline above. From step 9 on, record the actual failures and the owner's decision under #3474 before proceeding.

## 1. T197: `restate-1682` (#1682)

Purpose: retract only the reviewed never-true master rows (lever B): `delete-master-rows` moves them into `retracted_rows` (restorable; T392, ADR 0029) instead of deleting them. The command's name stays. Runs on the pinned checkout, before the pull.

- [ ] Record `uv run tradepartner health --coverage` before and after the release, and compare its `index misses by bucket` line bucket by bucket. That line counts **member-sessions** on `health`'s own sample sessions (the last session of each `coverage.sample_months` month). The census's **98 member-months** was counted over the 86 month-end index key, so no `health` figure equals it: do not compare the two. Compare the bucket line before against after, and read every other coverage line that changes. In the rehearsal the bucket line was identical before and after. Delisted names fell 2777 → 2772 (PG25, CAT/29, GM/26, HMC/22C and TPTA gone), `snapshot_static` reliance rose 1166 → 1167, and listed-name counts per sample month fell by 3 to 6.
- [ ] Use the manifest reviewed on #1682, with no other rows: `~/tradepartner-probes/sitting-inputs/manifest-restate-1682.csv` (33 rows plus a header). Open, dry-run, read every found and refused row, then apply only on the owner's go:

  ```bash
  uv run tradepartner paper status --all
  uv run tradepartner decision data-release open --name restate-1682 --backup <baseline-copy> --reason "#1682 reviewed lever B manifest"
  uv run tradepartner delete-master-rows --manifest ~/tradepartner-probes/sitting-inputs/manifest-restate-1682.csv --release restate-1682 --dry-run
  uv run tradepartner delete-master-rows --manifest ~/tradepartner-probes/sitting-inputs/manifest-restate-1682.csv --release restate-1682
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner health --check
  uv run tradepartner health --coverage
  uv run tradepartner decision data-release close --name restate-1682 --from <earliest-touched-session> --to <last-touched-session> --reason "#1682 delete-only; list touched sessions and affected sweeps"
  ```

- [ ] **Paper window refusal.** While a paper window is open, the `delete-master-rows` dry run refuses every row, each with `an open paper window (first session ...) read it` or `known inside the open paper window (first session ...)`, and ends `33 manifest rows refused: fix the manifest`. The manifest is fine in that case; the cause is the open window. `data-release open` does not check windows, so it accepts first. That refusal means the precondition in **Before the first write** failed: a window is not closed, so the baseline was taken against an open window, and paper jobs may still be loaded. **Stop the sitting.** Run no `paper` command (no `stop`, no `run`) while `restate-1682` is open, because a stop run submits sells and rule 5 of [named data releases](data-releases.md) keeps a release out of an open window. Confirm the paper jobs are unloaded and `pgrep -f "tradepartner "` is empty, then ask the owner how to handle the open release (for example, restore the baseline as in **Restore and gaps**) before any window is closed and a new baseline taken. To make sure this refusal is never the first signal, run `uv run tradepartner paper status --all` right before step 1's `data-release open`, and stop there unless both windows show closed or abandoned. In the rehearsal, with both windows closed, it printed `would retract 25 listings and 8 delistings rows of 33 manifest rows; refused 0` (5 s), and the apply printed the same counts as `retracted` (4 s).
- [ ] **Pinned EDGAR dry run fails on Cboe BZX.** On the pinned checkout `ingest --source edgar --dry-run` fails after about 25 minutes with `edgar: failed, 0 rows ... FilingFailuresError: ... unaccepted filing failures`, every failure being `dei:SecurityExchangeName ... 'Cboe BZX Exchange, Inc.' is not a registered securities exchange name` (8 10-Qs in the rehearsal, such as 0001193125-26-450234). The real 11-08 nightly failed the same way. #3231 fixes this on origin/main, so only steps after the pull have the fix. When that is the only failure: paste the output on #1682 and do not retry. Do not raise `edgar.min_failed_filings` to get past it: it is a build-fingerprint setting, and the sheet changes no setting. With the owner's go, continue, and make this dry run's two checks with the query above after step 9's first post-pull EDGAR write (see step 9). Write `EDGAR dry-run checks deferred to step 9 (Cboe BZX, #3231)` in `restate-1682`'s close reason and in its #1682 comment. Any other EDGAR failure is a stop.
- [ ] The dry run must match the manifest census exactly with **zero refused**. The EDGAR dry run must leave the note's Form 25 unmatched and must not rederive a retracted key. The dry run prints one total and answers neither check, so make them by query (read-only; any row it prints is a rederived key, and the delistings half is the Form 25 rows coming back). It reads the manifest file and the store as it stands, so run it after the `delete-master-rows` apply for the first look and again after step 9's EDGAR write:

  ```bash
  cat > ~/tradepartner-probes/sitting-inputs/restate-keys.sql <<'SQL'
  WITH m AS (SELECT * FROM read_csv($1, header = true, all_varchar = true))
  SELECT 'listings' AS tbl, l.security_id, l.ticker, l.exchange, l.valid_from, l.known_at, l.ingested_at
  FROM listings l JOIN m ON m."table" = 'listings' AND l.security_id = m.security_id
    AND l.ticker = m.ticker AND l.exchange = m.exchange AND l.valid_from = CAST(m.valid_from AS DATE)
  UNION ALL
  SELECT 'delistings', d.security_id, d.form, d.exchange, CAST(d.filed_at AS DATE), d.known_at, d.ingested_at
  FROM delistings d JOIN m ON m."table" = 'delistings' AND d.security_id = m.security_id
    AND d.form = m.form AND d.exchange = m.exchange AND d.class_title = m.class_title
    AND d.filed_at = CAST(m.filed_at AS TIMESTAMPTZ)
  ORDER BY 1, 2
  SQL
  tpsql ~/tradepartner-probes/sitting-inputs/manifest-restate-1682.csv < ~/tradepartner-probes/sitting-inputs/restate-keys.sql | tee ~/tradepartner-probes/sitting-inputs/restate-keys-step1.txt
  ```

  Expect no rows. The `filename_form25_unmatched` counter that the plan's wording points at is a run counter in the EDGAR run message, not a table; the query above is what this sheet can check (open question in #3245). When the Cboe BZX failure above applies, these two EDGAR checks move to step 9 and are not this step's stop. Stop on any other row, refusal, reappearance, failed health check or unpromised coverage change. With jobs stopped, follow **Restore and gaps** using the baseline; record the rollback on the PR/issue and retry under a **new** release name after review. Paste before/after bucket counts, found/refused rows, EDGAR dry-run output and release ids on T197's PR and #1682.

## 2. T186c: `dupdiv-1613` (#1613)

Purpose: retract reviewed duplicate dividends through the safe ex-date bound (`repair-dividends` moves them into `retracted_rows`, restorable; T390).

**Status: done.** The owner recorded it on #1682 (2026-11-05 03:37Z) as store decisions 12 and 13. Confirm both rows (`before` and `after`) exist, read-only, and paste their ids on #1613. Then skip this step. Run the commands below only if the rows are missing, and only on the owner's go.

- [ ] Confirm questions 27 and 28 are answered on #1613. `health` must show zero unreviewed violations: add every missing group to the reviewed list as `duplicate`, `distinct` or `unresolved` with evidence. Set `<bound>` to the session before the earliest formation start of a decision in either former window. Census: **169 listed** plus newly reviewed duplicates; **8 deferred** at the plan's bound, or the actual bound's count; **0 already repaired, 0 stale**. Expected post-check: violations 0, accepted **11** plus new distinct entries, unresolved **3** plus new unresolved entries, pending equal deferred.
- [ ] Take the release checkpoint and run:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name dupdiv-1613 --backup <dupdiv-copy> --reason "#1613 reviewed dividend duplicates"
  uv run tradepartner repair-dividends --release dupdiv-1613 --to <bound> --dry-run
  uv run tradepartner repair-dividends --release dupdiv-1613 --to <bound> --expect-action-rows <dry-run-row-count>
  uv run tradepartner health --check
  uv run tradepartner decision data-release close --name dupdiv-1613 --from 2019-09-27 --to <bound> --reason "#1613 retracted rows, deferred keys and sweep decision"
  ```

- [ ] Stop on a stale list entry, a count different from the reviewed list, a mismatch between dry run and apply, or failed health. Follow **Restore and gaps** using `<dupdiv-copy>` and re-review #1613. The owner's read-only `dupdiv.py` must recompute AIV 2020-11 and TDG 2025-09 from raw bars plus one dividend each; this is an owner-local check, not a repo command. Paste the list, dry-run and run-row counts, health block, two month checks and release ids on T186c's PR.

## 3. #1382: `restamp-1382a3` store step

Purpose: apply the already repaired cache's manifest to stored filing stamps. The phase-B release was cut.

**Status: done.** Its `before` and `after` are in `data/releases.toml` (the `after` made 2026-10-12T20:42:23Z), but not in the store's `owner_decisions`. The legacy import above refuses the file, so it does not put them in the store. Confirm both entries in the toml, read-only, paste them on #1382 and skip this step. The commands below are for a3 not yet applied.

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

Purpose: move the job checkout from the pinned `1fe51c9f` to origin/main once, so every later step runs the merged repairs. No release. Its one store write is the schema migration below, outside any release; the pull itself writes nothing to the store.

- [ ] **Pull at or after `e3a8e092` (#3241).** Before it, the cross-day failure rule counted stored failures that parsed fine this run, so every EDGAR dry run failed (`FilingFailuresError ... 9 accessions across days`) until a non-dry run pruned `failed_filings.json`. From `e3a8e092` on, no hand-prune of `failed_filings.json` is needed or allowed. Check `git merge-base --is-ancestor e3a8e092 HEAD` after the pull; stop if it fails. #3231 (the Cboe BZX parse) is in the same range.
- [ ] The pull is **all of origin/main**, not a T198-only diff. The count grows as PRs merge (over 107 commits by 2026-11-09), so record it rather than expect a figure. The commits include T198's flip and FSN/COVER bumps (#2975), the schema 47 fill cursor (#3029), and ingest behaviour changes #3073, #3064, #3068, #3090, #3086 and #3095. `BUILD_RULES_VERSION` is 8 (#3064, #3095, #3091, #3192), so step 9's first `ingest --source edgar` is a full master rebuild. #2975's bumps make it re-extract every FSN period too. Read its census with that in mind (step 9).
- [ ] Record what is pulled. Only what is merged at this moment is in the sitting; nothing merged later is pulled mid-sitting.
  - **Open on 2026-11-06:** #3071 (shares split dating), the #3070 flip that follows it and #3091 (issuer-class tags, schema 48). Steps 9 and 17 depend on whether each is in. In the rehearsal's pull (to 56dc2579), #3071, #3091 and #3176 were in and the #3070 flip was not (`universe.shares_split_dating` still `false`; it still is on origin/main at d00c00e4). The #3116 exposure-backfill tool will not come (#3133 closed). Step 16 needs only #3176's kill-line copy, `docs/research/2026-10-31-candidate-5-kill-line.md`, to be in the pull.
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

  Stop if the merge is not a fast-forward or the working tree is dirty. Paste both hashes, the commit count and which of #3071, the #3070 flip, #3091 and #3176 are in, on #1682. Record the pull hash beside the final-rehearsal comparisons in steps 6 and 9; #3456 did not record the final rehearsal's own code hash.
- [ ] **Migrate the store, right after the pull.** The store is at schema 46 and origin/main needs 49. `decision data-release open` does not migrate: at 46 it refuses with ``store schema_version is 46, this code expects 49; run `tradepartner ingest` to back up and migrate the store``. `ingest --dry-run` refuses too. Only a non-dry `ingest` and the write-mode `paper` commands migrate (`store/migrate.py`, called from `cli.py`). There is no `store migrate` command. A plain `ingest` is a full nightly write, and the paper commands touch the books, so use the one ingest entry point that fetches nothing: a borrow-snapshot import from an empty folder. It needs no secret and makes no request. It migrates, then applies zero files and writes no run row:

  ```bash
  mkdir -p ~/tradepartner-probes/sitting-inputs/empty-assets
  ls -A ~/tradepartner-probes/sitting-inputs/empty-assets   # must print nothing
  df -h data
  uv run tradepartner ingest --source alpaca_assets --import-dir ~/tradepartner-probes/sitting-inputs/empty-assets
  ls -lh data/tradepartner.duckdb.bak-*
  grep -l '"pre_migration"' data/tradepartner.duckdb.bak-*.manifest.json
  ```

  Expect exit 0 and `alpaca_assets import: 0 applied, 0 skipped (at or before the latest observation), 0 refused`. Before migrating, `migrate` writes an automatic **pre-migration copy** beside the store (`<store>.bak-YYYYMMDD-HHMM`, manifest reason `pre_migration`). The rehearsal migrated by calling `store.migrate.migrate` directly, not through this command: that took about 4 s and 2.25 GiB. The command and its expected output line are verified against the code and a temp store (the same `migrate` call), not on a store copy. The pre-migration copy counts as one of the disk precheck's copies. The `ls` lists every copy in `data/`. The `grep` prints every `pre_migration` manifest in `data/`. The sweep keeps two, so an older one may print too. **Exactly one** printed line must carry this run's UTC minute (`bak-YYYYMMDD-HHMM` within a minute of the command); that copy is the pre-migration copy. Record any other line as an older copy. If no line, or more than one, carries this run's minute, stop. Save its path as `<pre-migration-copy>`, with its size and `shasum -a 256` hash; it is the last copy at schema 46. The migration also deletes older `pre_migration` copies beyond `store.pre_migration_backups_to_keep` (2). It never touches a `store backup` checkpoint. Then confirm the version read-only, and stop unless it prints 49:

  ```bash
  uv run python -c 'from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; from tradepartner.store.schema import stored_version; cm = open_read_only(get_settings()); conn = cm.__enter__(); print(stored_version(conn)); cm.__exit__(None, None, None)'
  ```

  A refusal (disk below `ingest.min_free_disk_gb`, a busy store, a backup error) writes nothing; stop and ask. Step 9's checkpoint is then at schema 49. Restoring the pre-migration copy or any earlier copy with origin/main code needs this migration again before any release command.
- [ ] **`health` on origin/main.** In the first rehearsal `health --check` took 11 to 12 minutes after the pull; with #3233 the re-rehearsal measured 22 s. If it takes minutes, the pull lacks #3233: stop and check.
- [ ] **#3070 `universe.shares_split_dating` flip (conditional).** This step applies only if the flip PR, which follows #3071, is in the pull; otherwise the switch stays `false` and this sitting does nothing for it. It needs no store write: 115 shares facts straddle a split, and 62 of them are read as already split once it is on. It moves the pinned twin fingerprints, so list the trials it stales under [named data releases](data-releases.md), and run step 17 after it.
- [ ] The two local id lists this sheet uses are in a durable folder outside the repo (both are **pre-step-9** lists; step 6 and step 14 rebuild them from the store after step 9): `~/tradepartner-probes/sitting-inputs/ids3053.txt` (step 6, 79 ids) and `~/tradepartner-probes/sitting-inputs/spyacts3074-ids.txt` (step 14, 320 ids). Each is one comma-separated line. The old scratchpad copy of the first, `starts3053/ids.txt`, began with a count line (`79`), which glued the count onto P&G's id; never use it. Check both files before step 6:

  ```bash
  head -c 40 ~/tradepartner-probes/sitting-inputs/ids3053.txt; echo
  tr ',' '\n' < ~/tradepartner-probes/sitting-inputs/ids3053.txt | grep -c .
  tr ',' '\n' < ~/tradepartner-probes/sitting-inputs/spyacts3074-ids.txt | grep -c .
  ```

  The first line must start with `0000080424:common-stock,` (no count line), then the counts must be **79** and **320**. Stop on anything else.

## 9. T198: `rederive-1682` (#1682) — runs right after step 5

Purpose: flip the two EDGAR switches, bump both cache versions, rederive rows, rebuild classification, and fill price holes. **This is the first release after the pull**, before steps 6, 7 and 8 (#3245): it re-keys ids, so everything those steps fetch or add is built from the store it leaves.

- [ ] **Census mixes other merged PRs' effects.** This is the first post-pull `ingest --source edgar`. It derives under every merged rule at once, so its EDGAR diff, retract list, resolution repair and hole list are not T198's alone. Each rule rides on the same derivation, so none can have a release of its own, and rederive-1682's counts include all of them:
  - **T198 (#2975).** The plan's key expects C1/C3 **265**, H **183**, E2 **270** and F **142** member-months, as diagnostic targets. E1's **144** is excluded because T195 is parked.
  - **#3064, the post-bankruptcy successor (master rule).** Up to 33 cross-exchange successors, an upper bound. GPOR's old id loses its bar sessions (**1,375** in the plan, **1,383** in the re-rehearsal, 2021-05-18 to 2026-11-06), refetched under `0000874499@2021-08-09` from 08-09. WW's rule-7 cut (`0000105319` 5 and `0001844505` 3 bar sessions) did **not** happen in the re-rehearsal: `repair-resolution` never listed WW. Read that as unsettled, not as done.
  - **#3095, issuer HTML Form 25s and the unit/comma class rule.** The re-rehearsal's dry run printed **409** `unread HTML delistings` (the sheet's earlier ~800 was a guess) and **2,085** pre-XML delistings. The EDGAR dry run prints **one total** (19,076 rows; 114,545 with `--rebuild-classifications`) and no per-table delistings or listings counts: take those by the query below.
  - **#3091.** The rebuild deletes and re-adds every classification row (102,330 deleted), so the printed figure is not the moved count. The moved rows are read by the query below. The earlier estimate was about **12,460** rows to `no_sic_or_periodic`, and at most **6,889** bars since 2016 in those spans.
  - **#3068.** Should add nothing beyond step 8's rows, but step 8 now runs after this step, so its rows are read there against what this step already derived.
  - **#3090 and #3086.** Their holes are filled after this step (steps 6 and 7), from the store as it stands.

  Record each count against the source above that explains it, and name any count none of them explains. T184h stays out unless its own gates and questions 22–24 are satisfied. Record `health --coverage` first.
- [ ] **Time.** The first EDGAR dry run is about **2 h** and rebuilds the caches (FSN v7 re-extraction, then the cover-page refetch, network-bound); do it first and early. After that, an EDGAR dry run or write is about 28 m. Do not read the long first dry run as a hang.
- [ ] **Disk before the first EDGAR dry run (#3289).** That dry run grows the EDGAR cache by about **3.5 GB** (3.4 GiB in rehearsal pass 5, which took free space from 8.0 to 4.6 GiB; the re-rehearsal measured 2.5 GiB), and a dry run refuses when free space is under `ingest.min_free_disk_gb` (`F`, 5 GB; pass 5's next dry run refused at 4.8 GB). The backup precheck above does not cover it. Run `df -h data` right before it and **stop unless free space is at least `F` + 3.5 GB (8.5 GB)**, after step 9's own `store backup`. Free space or move closed releases' copies off the volume first (with the owner's word, see the disk precheck).
- [ ] **Holdings check, both books, before any write.** Both windows are closed, so each book's latest `positions_daily` rows must be **flat**, not merely "no open lot": the closed book's last row can still show its last held day unless the stop wrote zero rows, which is exactly what this finds. Run with no argument: it must print nothing (open lots and held positions together; the open-lot arm reads only each book's current lot set, the rows at that book's latest `lots.known_at` (the rule `outcomes._book_stamp` uses; every earlier stamp is a superseded rebuild and must not count), nets that set's `disposals` and is a candidate list, since the ledger's own `open_lots` also folds adjustments). Then run it once per later id list:

  ```bash
  cat > ~/tradepartner-probes/sitting-inputs/holdings-step9.sql <<'SQL'
  WITH latest AS (
    SELECT book_id, MAX(session) AS session FROM positions_daily GROUP BY book_id
  ), pos AS (
    SELECT p.book_id, p.security_id, p.quantity, 'latest positions_daily ' || CAST(p.session AS VARCHAR) AS why
    FROM positions_daily p JOIN latest l ON p.book_id = l.book_id AND p.session = l.session
    WHERE p.security_id IS NOT NULL AND p.quantity <> 0
  ), cur AS (
    SELECT book_id, MAX(known_at) AS known_at FROM lots GROUP BY book_id
  ), openlots AS (
    SELECT l.book_id, l.security_id, l.quantity - COALESCE(SUM(d.quantity), 0) AS quantity, 'open lot ' || CAST(l.lot_id AS VARCHAR) AS why
    FROM lots l JOIN cur c ON l.book_id = c.book_id AND l.known_at = c.known_at
    LEFT JOIN disposals d ON d.lot_id = l.lot_id AND d.book_id = l.book_id AND d.known_at = l.known_at
    GROUP BY l.book_id, l.security_id, l.lot_id, l.quantity
    HAVING l.quantity - COALESCE(SUM(d.quantity), 0) > 1e-9
  )
  SELECT * FROM (SELECT * FROM pos UNION ALL SELECT * FROM openlots)
  WHERE $1 = '' OR list_contains(string_split($1, ','), security_id)
  ORDER BY book_id, security_id
  SQL
  tpsql "" < ~/tradepartner-probes/sitting-inputs/holdings-step9.sql
  tpsql <<'SQL'
  SELECT book_id, MAX(session) AS latest_session FROM positions_daily GROUP BY book_id ORDER BY book_id
  SQL
  ```

  The first call (argument `""`) must print **no row**; a row means a book may still hold the id, and the sitting stops there (the 08:05 stop run and `paper stop` did not finish). The open-lot arm can print a lot the ledger has folded through an adjustment, so cross-check any such row against `uv run tradepartner paper status --book <book>` (positions); only the owner clears a row that the status shows as flat, and records that on #1682. Run this check read-only on the real store before the sitting and paste its output on #1682, so the expected output is known. The second shows each book's latest session, so a missing or stale book is visible. For each later list, run `tpsql "$(sort -u ~/tradepartner-probes/sitting-inputs/book-ids.txt | paste -sd, -)" < ~/tradepartner-probes/sitting-inputs/holdings-step9.sql` after appending its ids.
- [ ] Take the release checkpoint and snapshot the book ids' listings before any EDGAR write (#3064, #3095):
  - **BOOK IDS.** With both books flat the call above is empty, so BOOK IDS are the ids each book **last held**: the securities of each book's latest `positions_daily` session with a non-zero quantity, and any security with a lot in `lots` for that book. The command below saves one id per line to `~/tradepartner-probes/sitting-inputs/book-ids.txt`; append the ids in the next bullet. `listings` has no `end_session` column. A listing's end is derived from the listings, delistings and bars known at a time by `store.delistings.listing_ends_as_of`, the same read the paper planner uses for forced exits (`execution/planning.py` `_ended`, through `execution/exits.py`'s `listings_at`). Snapshot every listing row of those ids with its derived status and end. Stop (i) below fires on a change to the **current** listing of one of these last-held ids; the added ids (GPOR and the successors) are expected to change and are read, not stopped on, unless a book holds them:

    ```bash
    tpsql <<'SQL' | sed -n "s/^('[^']*', '\([^']*\)').*/\1/p" > ~/tradepartner-probes/sitting-inputs/book-ids.txt
    SELECT DISTINCT p.book_id, p.security_id FROM positions_daily p
    JOIN (SELECT book_id, MAX(session) AS session FROM positions_daily WHERE quantity <> 0 AND security_id IS NOT NULL GROUP BY book_id) l
      ON p.book_id = l.book_id AND p.session = l.session
    WHERE p.quantity <> 0 AND p.security_id IS NOT NULL
    UNION SELECT DISTINCT book_id, security_id FROM lots
    ORDER BY 1, 2
    SQL
    ```

    Then snapshot every listing row of those ids with its derived status and end.

    ```bash
    IDS=~/tradepartner-probes/sitting-inputs/book-ids.txt uv run python -c 'import os; from datetime import datetime, UTC; from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; from tradepartner.store.delistings import listing_ends_as_of; s = get_settings(); ids = sorted({x.strip() for x in open(os.environ["IDS"]).read().replace(",", "\n").split() if x.strip()}); cm = open_read_only(s); conn = cm.__enter__(); f = listing_ends_as_of(conn, datetime.now(UTC), s, ids).sort("security_id", "valid_from", "exchange", "ticker"); rows = list(f.iter_rows(named=True)); mx = {}; [mx.__setitem__(r["security_id"], max(mx.get(r["security_id"], r["valid_from"]), r["valid_from"])) for r in rows]; [print(r["security_id"], r["ticker"], r["exchange"], r["valid_from"], r["status"], r["end_session"], r["delisting_form"], r["effective_on"], "current" if r["valid_from"] == mx[r["security_id"]] else "-") for r in rows]; cm.__exit__(None, None, None)' > ~/tradepartner-probes/sitting-inputs/book-ends-before.txt
    ```

    One line per listing row: id, ticker, exchange, `valid_from`, status (`listed`, `delisted` or `transferred`), `end_session` (`None` when listed or unknown), the ending form and its effective day, then `current` for the id's row with the latest `valid_from` (an approximation of the row the planner reads as current; for an id with two rows from one day, read both) and `-` for the others, so the diff shows it directly. Run it once here, then **again after the dry runs in the block below and before the EDGAR write**. Add the old ids the dry runs name (next bullet) to `book-ids.txt` first, so every id has a before line. The second run overwrites `book-ends-before.txt`.
  - Add #3064's ids `0000874499`, `0000105319` and `0001844505`, the dry runs' **successor** ids (the EDGAR dry run does not name them as such; its `chain_candidates` list, 22 in the re-rehearsal, is the nearest thing, so add those) and every id `repair-resolution`'s dry run names (49 in the re-rehearsal). Run `printf '%s\n' 0000874499 0000105319 0001844505 >> ~/tradepartner-probes/sitting-inputs/book-ids.txt`, then the saved-list extraction below. Check the extracted file against the dry runs and run `tpsql "$(sort -u ~/tradepartner-probes/sitting-inputs/book-ids.txt | paste -sd, -)" < ~/tradepartner-probes/sitting-inputs/holdings-step9.sql`. An open lot or held position on any of them is a **STOP**: do not write, and report it on #3056.

  ```bash
  set -o pipefail  # keep pipeline failures visible for this terminal
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rederive-1682 --backup <rederive-copy> --reason "#1682 C1 C3 H E2 F; T195 parked; first post-pull EDGAR write (#3064 #3095)"
  uv run tradepartner paper status --all
  uv run tradepartner ingest --source edgar --dry-run | tee ~/tradepartner-probes/sitting-inputs/edgar-dry-step9.txt
  uv run tradepartner ingest --source edgar --rebuild-classifications --dry-run | tee ~/tradepartner-probes/sitting-inputs/edgar-rebuild-dry-step9.txt
  uv run tradepartner repair-resolution --dry-run | tee ~/tradepartner-probes/sitting-inputs/repair-before-step9.txt
  grep -Eoh '[0-9]{10}(:[a-z0-9-]+|@[0-9-]+)?' ~/tradepartner-probes/sitting-inputs/{edgar-dry-step9,edgar-rebuild-dry-step9,repair-before-step9}.txt | sort -u >> ~/tradepartner-probes/sitting-inputs/book-ids.txt
  ```

- [ ] **Approve the saved pre-write ID list.** Read the three saved dry runs and `book-ids.txt`; add any id the extractor missed. The dry runs must not fail on Cboe BZX or an unaccepted filing failure for a filing parsed this run; if either does, stop before the write (#3231, #3241). Rerun the holdings check with `tpsql "$(sort -u ~/tradepartner-probes/sitting-inputs/book-ids.txt | paste -sd, -)" < ~/tradepartner-probes/sitting-inputs/holdings-step9.sql` and the BOOK IDS snapshot before the EDGAR write. A held id stops the sitting.

  ```bash
  date -u +%Y-%m-%dT%H:%M:%SZ   # <edgar write start>
  uv run tradepartner ingest --source edgar --rebuild-classifications
  ```

  This dry run must not fail on `'Cboe BZX Exchange, Inc.'` or on `unaccepted filing failures` for filings that parsed this run: if it does, the pull lacks #3231 or #3241, so stop. Do not hand-prune `failed_filings.json`. If step 1's pinned EDGAR dry run failed on the Cboe BZX names, make its two checks with step 1's query now, after the write. The 24 notes-only `restate-1682` rows in the final rehearsal are accepted under the owner's "Carry on" decision on #1682. The raw query can also show retracted listing revisions; the live-key diff in **Re-derived restatements** below decides the stop. A live key outside the accepted 24 is a **stop-and-ask**, not a restore: `<rederive-copy>` cannot undo `restate-1682`, and under `BUILD_RULES_VERSION` 8 the cause may be another merged rule. Paste it on #1682 and write nothing more until the owner decides. If the EDGAR ingest refuses on the build fingerprint and asks for `--rebuild-index`, stop and ask; do not add it from this sheet.
- [ ] **What the write did (read-only, right after it).** `<edgar write start>` is the UTC time noted just before the write.

  ```bash
  tpsql <edgar write start> <<'SQL'
  SELECT 'delistings' AS tbl, COUNT(*) AS rows_written, COUNT(DISTINCT security_id) AS ids
  FROM delistings WHERE ingested_at >= CAST($1 AS TIMESTAMPTZ)
  UNION ALL
  SELECT 'listings', COUNT(*), COUNT(DISTINCT security_id)
  FROM listings WHERE ingested_at >= CAST($1 AS TIMESTAMPTZ)
  SQL
  BEFORE_COPY=<rederive-copy> tpsql <<'SQL'
  SELECT b.rule AS rule_before, COUNT(*) AS rows_moved, COUNT(DISTINCT c.security_id) AS ids
  FROM classifications c
  JOIN before.classifications b ON b.security_id = c.security_id AND b.known_at = c.known_at AND b.rule <> c.rule
  WHERE c.rule = 'no_sic_or_periodic'
  GROUP BY 1 ORDER BY 2 DESC
  SQL
  tpsql <edgar write start> <<'SQL' | sed -n "s/^('\(.*\)',)\$/\1/p" > ~/tradepartner-probes/sitting-inputs/changed-ids.txt
  SELECT security_id FROM listings WHERE ingested_at >= CAST($1 AS TIMESTAMPTZ)
  UNION
  SELECT security_id FROM delistings WHERE ingested_at >= CAST($1 AS TIMESTAMPTZ)
  ORDER BY 1
  SQL
  ```

  The first prints the per-table rows written (paste on #1682 and #3095). The second prints the #3091 moved classification rows by their old rule (rows with the same id and instant whose rule changed to `no_sic_or_periodic`). The third saves the ids this write changed, one bare id per line. Step 9's fill check below and steps 6 to 8 use this list.
- [ ] **Re-derived restatements.** Read the 24 `restate-1682` rows on notes-only issuers from the final rehearsal as accepted residue under the owner's [2026-11-14 decision](https://github.com/josejuarez96/tradepartner/issues/1682#issuecomment-6885138012). After step 9, run `tpsql ~/tradepartner-probes/sitting-inputs/manifest-restate-1682.csv < ~/tradepartner-probes/sitting-inputs/restate-keys.sql | tee ~/tradepartner-probes/sitting-inputs/restate-keys-step9.txt` for the audit trail. That raw query can include a retracted listing revision; the live-key diff below is the stop check. The evidence `~/tradepartner-probes/sitting-inputs/final-s9-restate-stop.md` describes the 24 rows in prose; the script deliberately stops if its cited summary wording changes, so the owner verifies the accepted key file produced below against its listing and delisting names and posts its hash on #1682 before using the diff. Any other live returning key is a stop. Record the actual rows and count at the sitting; the accepted set does not grow by inference.

  Save the 24 accepted manifest keys and the actual returned keys in the same format, then diff them. The four excluded CIKs are the 9 manifest rows the decision's evidence says did **not** return on their primary id (PG, VZ, EXC and EXPE). This is a check on the real post-step-9 store, not a prediction that it must repeat the rehearsal:

  ```bash
  (
  set -e  # stop this evidence block if generation fails
  rm -f ~/tradepartner-probes/sitting-inputs/restate-accepted-keys.txt ~/tradepartner-probes/sitting-inputs/restate-actual-keys.txt ~/tradepartner-probes/sitting-inputs/restate-rekeyed-keys.txt
  uv run python - <<'PY'
  import csv
  import json
  from collections import Counter
  from pathlib import Path
  from tradepartner.config import get_settings
  from tradepartner.store.db import open_read_only

  root = Path.home() / "tradepartner-probes/sitting-inputs"
  rows = list(csv.DictReader((root / "manifest-restate-1682.csv").open()))
  evidence = (root / "final-s9-restate-stop.md").read_text()
  assert "24 of the 33 manifest rows came back" in evidence
  assert "Delistings (6 of 8)" in evidence and "Listings (18 of 25)" in evidence
  excluded = {"0000080424", "0000732712", "0001109357", "0001324424"}
  fields = ("table", "security_id", "ticker", "exchange", "valid_from", "form", "class_title", "filed_at")
  key = lambda row: json.dumps([row[name] for name in fields], ensure_ascii=False)
  cm = open_read_only(get_settings())
  conn = cm.__enter__()
  def found(row):
      if row["table"] == "listings":
          latest = conn.execute(
              "SELECT retracted FROM listings WHERE security_id = ? AND ticker = ? AND exchange = ? AND valid_from = CAST(? AS DATE) ORDER BY known_at DESC, ingested_at DESC LIMIT 1",
              [row["security_id"], row["ticker"], row["exchange"], row["valid_from"]],
          ).fetchone()
          return latest is not None and not latest[0]
      return conn.execute(
          "SELECT 1 FROM delistings WHERE security_id = ? AND form = ? AND exchange = ? AND class_title = ? AND filed_at = CAST(? AS TIMESTAMPTZ) LIMIT 1",
          [row["security_id"], row["form"], row["exchange"], row["class_title"], row["filed_at"]],
      ).fetchone() is not None
  accepted = sorted(key(row) for row in rows if row["security_id"][:10] not in excluded)
  actual = sorted(key(row) for row in rows if found(row))
  rekeyed = []
  for row in rows:
      cik = row["security_id"][:10]
      if row["table"] == "listings":
          matches = conn.execute(
              "SELECT security_id, retracted FROM listings WHERE substr(security_id, 1, 10) = ? AND ticker = ? AND exchange = ? AND valid_from = CAST(? AS DATE) QUALIFY row_number() OVER (PARTITION BY security_id ORDER BY known_at DESC, ingested_at DESC) = 1",
              [cik, row["ticker"], row["exchange"], row["valid_from"]],
          ).fetchall()
          ids = [sid for sid, retracted in matches if not retracted]
      else:
          matches = conn.execute(
              "SELECT DISTINCT security_id FROM delistings WHERE substr(security_id, 1, 10) = ? AND form = ? AND exchange = ? AND class_title = ? AND filed_at = CAST(? AS TIMESTAMPTZ)",
              [cik, row["form"], row["exchange"], row["class_title"], row["filed_at"]],
          ).fetchall()
          ids = [sid for (sid,) in matches]
      rekeyed.extend((key(row), sid) for sid in ids if sid != row["security_id"])
  cm.__exit__(None, None, None)
  table_counts = Counter(row["table"] for row in rows if row["security_id"][:10] not in excluded)
  unexpected_rekeyed = [(old_key, sid) for old_key, sid in rekeyed if json.loads(old_key)[1][:10] not in excluded]
  print("accepted count:", len(accepted), "table counts:", table_counts)
  print("accepted keys:", *accepted, sep="\n")
  print("re-keyed keys:", *((old_key, sid) for old_key, sid in rekeyed), sep="\n")
  print("unexpected re-keyed sources:", unexpected_rekeyed)
  assert len(accepted) == 24, "manifest differs from the owner's 24-key decision"
  assert table_counts == {"listings": 18, "delistings": 6}, "manifest table counts differ: stop and ask on #1682"
  assert not unexpected_rekeyed, "unexpected re-keyed source: stop and ask on #1682"
  (root / "restate-accepted-keys.txt").write_text("\n".join(accepted) + "\n")
  (root / "restate-actual-keys.txt").write_text("\n".join(actual) + "\n")
  (root / "restate-rekeyed-keys.txt").write_text("\n".join(json.dumps(x) for x in sorted(rekeyed)) + "\n")
  print("accepted", len(accepted), "actual", len(actual), "rekeyed", len(rekeyed))
  PY
  shasum -a 256 ~/tradepartner-probes/sitting-inputs/restate-accepted-keys.txt
  )
  ```

- [ ] **Owner approves the accepted key list and hash before comparison.** Check its names against `final-s9-restate-stop.md` and post the hash on #1682. The final rehearsal found 9 re-keyed matches on the four excluded CIKs; read the actual `restate-rekeyed-keys.txt` and its printed count. Stop and ask on #1682 if that count differs or a match is unfamiliar. Only after this approval, run the comparison below. If the evidence script failed, do not run it; the output files were removed before the script.

  ```bash
  (
  set -e
  diff -u ~/tradepartner-probes/sitting-inputs/restate-accepted-keys.txt ~/tradepartner-probes/sitting-inputs/restate-actual-keys.txt || true
  uv run python - <<'PY'
  from pathlib import Path
  root = Path.home() / "tradepartner-probes/sitting-inputs"
  accepted = set((root / "restate-accepted-keys.txt").read_text().splitlines())
  actual = set((root / "restate-actual-keys.txt").read_text().splitlines())
  extra = sorted(actual - accepted)
  missing = sorted(accepted - actual)
  print("unexpected returned keys:", extra)
  print("accepted keys absent:", missing)
  assert not extra, "unexpected live restatement key: stop and ask on #1682"
  PY
  )
  ```

  An unexpected returned key is a stop-and-ask on #1682, not an automatic restore. Record an accepted key that is absent; its absence is allowed. Read `restate-rekeyed-keys.txt` as well: the final rehearsal's evidence names 2 PG note-class rows and 7 VZ/EXC/EXPE class-id rows on different ids. Stop and ask on any other re-keyed match. Keep all three files and the diff with the counts comment.

- [ ] **BOOK CHECK** (#3095; read-only, right after the EDGAR write, before any other command). Forced exits come from a book id's **current** listing end (`execution/exits.py`), so the stop is narrow:
  - (a) `tpsql <edgar write start> "<book ids, comma-separated>"` with `SELECT security_id, form, filed_at, effective_on FROM delistings WHERE ingested_at >= CAST($1 AS TIMESTAMPTZ) AND list_contains(string_split($2, ','), security_id)`. Read every row (the re-rehearsal had 17 on BOOK IDS).
  - (b) Re-run the BOOK IDS snapshot with output to `book-ends-after.txt`, then `diff ~/tradepartner-probes/sitting-inputs/book-ends-before.txt ~/tradepartner-probes/sitting-inputs/book-ends-after.txt`. **Stop only on** (i) a change to a BOOK ID's **current** listing (a new, earlier or changed end, a status change, or a row that disappears and makes an older `delisted` row current), or (ii) any change to an id the holdings check shows a book **holds**. **List every other diff line for reading** and paste it on #3084: an old, non-current row gaining its real Form 25 end at an exchange transfer whose next row already exists (PANW, XRX, MXL, COHR and others in the re-rehearsal), a new listing row on an id not held, or a `transferred` status. Forced exits ignore those.
  - **A real delisting is not a stop when no book holds the id.** In the re-rehearsal, ROKU `0001428439` (NASDAQ, 25-NSE filed 2026-11-06, effective 2026-11-16) showed as a current-listing end. It is a real delisting by acquisition (the filing cites 17 CFR 240.12d2-2(a)(3), the same day's 8-K reports completion of the acquisition and change in control), the store's end is right, and no book held it. Read each such line, check it against the holdings check, and continue when no book holds the id. If a book does, it is a stop.

  On a stop, restore as in **Restore and gaps** using `<rederive-copy>`, after saving the failed state, and report it on #3084. Keep paper jobs unloaded throughout. Do not run `data-release close` after the restore. Then continue:

  Apply `master-retract` and `repair-resolution` **only if** their dry runs find rows. A new Form 25 can end a listing earlier than today, so the retract list need not be empty: each listed row should be a listing after its new Form 25's effective day. **The `master-retract` apply reruns the whole EDGAR fetch pass before it judges (about 25 m).** A network drop there refuses it with nothing written; rerun it.
  - **Traded rows.** The re-rehearsal's retract dry run: 286 rows (36 securities, 250 listings, 0 chains), kept 2 on `master.keep_successors`, and a refusal on traded rows **COP `0001163165` NYSE 2023-11-02 and GTX `0001735707` NASDAQ 2021-07-29** (neither held at the latest positions). GPOR's successor `0000874499@2021-08-09` is also on the final rehearsal's allow-traded list. **The owner checks and records the approval for every refused id, including GPOR, on #1682 before the apply.** `--allow-traded` is one flag for the entire apply: pass it only when every traded id in the refusal is approved and not held. Any other traded id is a stop and a decision on #1682.
  - **Not judged.** The retract dry run names CIKs whose filings failed or were quarantined (`0000712034`, `0001841666` in the re-rehearsal; `0001841666` is APA). Run the holdings check on each: a held CIK stops step 9, because its rows were not judged.
  - **Holdings against the post-retract list.** After the retract, the `repair-resolution` dry run names many more ids than before (197 securities, 102,489 bar rows and 563 action rows in the re-rehearsal, against 49 before). Run the holdings check on **that** list too, before `repair-resolution` applies. A held id is a stop; in the re-rehearsal two were held on the copy (NANO/ONTO `0000704532`, CDEV/PR `0001658566`), which is how this gap was found.
  - **Orphan stop (#3265, #3264).** Read the `repair-resolution` dry run's `ORPHAN <id> (<name>): N bar keys <first>..<last> that no id receives` lines and its summary's `N orphan bar keys`. An orphan is a bar the repair would retract that no other id receives, so a refetch cannot bring it back (the re-rehearsal lost 26,889 keys on 81 securities this way: APA, MAR to today, BATRA/BATRK, LLYVA/LLYVK, DDS, TCBI, IBOC, CECO, CAE). **If any orphan line appears, do not apply.** The apply refuses on it by itself, and `--allow-orphans <count>` is never passed at the sitting unless the owner has posted that exact count, read from this dry run, on #3264. Without his count, leave `rederive-1682` open and stop the sitting; later releases cannot open. Report the stop on #3264. A retracted `:class` id whose CIK still lists the ticker counts as moved, not an orphan (so the class-keyed ids below do not trip it). Read every `DUPLICATE <id> (<name>): N bar keys <first>..<last> duplicate of <owner>` line and record its total separately on #3264 (#3463): the owner already holds the same bar, so it is not an orphan and its count must not be included in `--allow-orphans`. An orphan-free dry run applies as written.
  - **Class-keyed ids.** The retract list can hold `common-stock` class ids (P&G `0000080424:common-stock`, `0001824403:common-stock`, `0001846235:common-stock`), and `repair-resolution` then retracts their bars. That is expected: the rebuild derives them as the plain CIK id, and the fill below refetches them. Steps 6 and 7 run after this step for that reason.
  - **Store-wide fill.** Its dry run lists every hole in the store, and most of it is long-standing (ids Alpaca has no bars for: 109,853 holes over 5,464 securities, 17,199 not fetchable, in the re-rehearsal). There is no census for that figure, so **judge only the holes on ids this step changed**: the ids in `changed-ids.txt`, the retract and `repair-resolution` ids, and the `chain_candidates`. Append the retract, `repair-resolution` and `chain_candidates` ids to `changed-ids.txt`, one per line. Filter the saved dry run to them with (a substring match: a bare CIK also matches its `:class` and `@` ids, which only widens what is read) `grep -F -f ~/tradepartner-probes/sitting-inputs/changed-ids.txt ~/tradepartner-probes/sitting-inputs/fill-dry-step9.txt` and read what is left. Those ids' holes should be the months the step retracted or moved (P&G `0000080424: 2016-01..2026-10 (130 months)`, GPOR `0000874499@2021-08-09: 2021-08..2026-10 (63 months)`). Holes on any other id are not a stop. Budget about **3.7 h** for the apply: it re-asks Alpaca for many holes it never serves (130 months at about 101 s in the re-rehearsal, 13,130 s; it prints a progress line per month). After it, an id this step changed that still has a hole in a month Alpaca should serve is read, not a stop; Alpaca-absent months stay.
  - Run `uv run tradepartner ingest --source openfigi` only if T189c's owner run is approved and include its `figi_types` block. `health`'s `spliced_series` is report-only: read what is left (106 in the re-rehearsal after step 9, from 90 before). CBL stays until its Form 25 is in (#3065). Do not run the `paper reconcile` lines from #3064's and #3095's PR bodies here: `paper reconcile` refuses `no_window` while both windows are closed. The restart covers it.
  ```bash
  uv run tradepartner master-retract | tee ~/tradepartner-probes/sitting-inputs/retract-step9.txt
  grep -Eoh '[0-9]{10}(:[a-z0-9-]+|@[0-9-]+)?' ~/tradepartner-probes/sitting-inputs/retract-step9.txt | sort -u >> ~/tradepartner-probes/sitting-inputs/changed-ids.txt
  ```

- [ ] **Approve the retract dry run before applying.** Read the traded-row and not-judged rules above. Run the holdings check on every refused and not-judged id. Get the owner's approval for each traded id on #1682; stop on an unapproved or held id. If the dry run has no rows, skip the apply.

  ```bash
  uv run tradepartner master-retract --apply --expect-rows <dry-run-rows> --expect-digest <dry-run-digest> --release rederive-1682
  uv run tradepartner repair-resolution --dry-run | tee ~/tradepartner-probes/sitting-inputs/repair-after-retract-step9.txt
  grep -Eoh '[0-9]{10}(:[a-z0-9-]+|@[0-9-]+)?' ~/tradepartner-probes/sitting-inputs/repair-after-retract-step9.txt | sort -u >> ~/tradepartner-probes/sitting-inputs/changed-ids.txt
  grep -Eoh '[0-9]{10}(:[a-z0-9-]+|@[0-9-]+)?' ~/tradepartner-probes/sitting-inputs/repair-after-retract-step9.txt | sort -u >> ~/tradepartner-probes/sitting-inputs/book-ids.txt
  ```

- [ ] **Approve the repair dry run before applying.** Run the holdings check on its complete post-retract id list. Read its `ORPHAN` and `DUPLICATE` lines under the rules above. A held id or an orphan without the owner's exact count on #3264 stops the sitting. If the dry run has no rows, skip the apply.

  ```bash
  uv run tradepartner repair-resolution --expect-bar-rows <dry-run-bars> --expect-action-rows <dry-run-actions> --release rederive-1682   # add --allow-orphans only with the owner's exact count below
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --dry-run | tee ~/tradepartner-probes/sitting-inputs/fill-dry-step9.txt
  ```

- [ ] **Approve the fill dry run before applying.** Add the retract, repair and chain-candidate ids to `changed-ids.txt`; filter the saved dry run and read the changed ids' holes as described above. Stop and ask on an unexplained hole on a changed id.

  ```bash
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes
  uv run tradepartner decision data-release coverage --name rederive-1682 | tee ~/tradepartner-probes/sitting-inputs/coverage-step9.txt  # read-only; exit 1 is expected if keys remain
  ```

- [ ] **Check read-only coverage before unreceived acceptance.** Its exit 1 is acceptable only for the listed `NOT RECEIVED` spans: require `0 not moved`, total unmoved sessions no higher than the printed limit, and no `OWNERLESS` line. Any other failure stops the sitting; do not ask for an acceptance count.

  ```bash
  uv run tradepartner decision data-release accept-unreceived --name rederive-1682 | tee ~/tradepartner-probes/sitting-inputs/accept-unreceived-step9.txt  # lists residual keys and count; exits 1 without writing
  awk -F, '/^RETRACTED prices_daily/ && /sessions NOT RECEIVED/ {spans++; for (i=1; i<=NF; i++) if ($i ~ /sessions NOT RECEIVED/) {gsub(/^ +/, "", $i); split($i, a, " "); n+=a[1]}} END {print n+0, "sessions in", spans+0, "spans"}' ~/tradepartner-probes/sitting-inputs/coverage-step9.txt
  ```


  **`data-release close` checks coverage, nothing else** (T393, ADR 0029). It writes the `after` row for the sessions you name and refuses a release that changed bars, actions or master rows unless `data-release coverage --name <name> --record` passed on the store as it is now. It does not look at health, `repair-resolution` or the fill (`store/registry.py` `close_data_release`; the rehearsal closed `rederive-1682` with 67 orphans unrepaired and no fill). Run it **only after** `repair-resolution` has applied (or its dry run found nothing and the orphan stop below is clear), the fill is done, and `data-release coverage` exited 0. Every later release (steps 6, 7, 8 and 10 to 15) can open only once this one is closed, so a premature close lets them start on an unrepaired store. If the orphan stop below applies, leave `rederive-1682` open and ask the owner; do not close to carry on.

- [ ] **Decide on residual unreceived bars after the fill (#3464).** Use the read-only coverage output saved above (exit 1 is expected if keys remain). Coverage prints `X of Y sessions NOT RECEIVED` on each `RETRACTED prices_daily` line, not a total. Use the `awk` sum printed in the command block above. Compare that sum with the `accept-unreceived` listing count. A mismatch is a stop (#3477, #3479): record both outputs on #1682 and #3477 and leave the release open. The owner posts the exact matching count on #1682 before the `--count` command below is run. This is separate from the `repair-resolution` orphan count posted on #3264. If the count is zero, record that and skip the write. If the owner does not accept the listed keys, leave the release open; coverage must not be forced through. The command refuses a mismatched count without writing. Run `coverage --record` after the acceptance, then close only on exit 0.

  ```bash
  uv run tradepartner health
  uv run tradepartner health --check
  health_rows 9
  ```

  Only after the owner posts the exact residual count on #1682, run the next line; skip this line if the count is zero. The health block above runs either way.

  ```bash
  uv run tradepartner decision data-release accept-unreceived --name rederive-1682 --count <owner-approved-exact-count>
  ```
- [ ] **Stale keys after step 9 (#3387).** The rebuild can move a successor's `<cik>@<date>` id (#3381), and an entry keyed by the old id then matches nothing, silently (the APA `dividend_reviews.csv` case, #3263). Run `uv run tradepartner stale-keys` (read-only). It must print `no stale keys` and exit 0. **Stop on any hit** (exit 1): it lists stale ids in `dividend_reviews.csv`, `listing_reviews.csv`, `spinoff_splits.csv`, `alpaca.accepted_relistings` or `master.keep_successors`, and kept successors replaced for the same listing span. Re-key an entry whose id left the store to the rebuilt id in a PR before step 13; do not carry on past step 13 with a hit. **Read superseded hits; never drop a keep entry without the owner.**
- [ ] **Health after step 9.** Record the actual `non_overlapping_listings`, `no_bars_after_delisting` and `old_id_bars_after_successor` failures, plus any `duplicate_dividends` and `store_stale` result. These three integrity failures remained after step 6 in the final rehearsal. The owner records the decision to proceed or stop under open item #3474 on #1682; no row count is assumed.
- [ ] Stop for a common CIK losing its primary id **other than the class-keyed ids named above**, a class gaining a unit/note/warrant title, a newly unparseable universe CIK, changed `filing_name` or `snapshot_static` `known_at`, a traded-row refusal on an id without the owner's approval (COP, GTX and GPOR's successor are named above), a held id in the BOOK CHECK or the holdings checks, or a health result outside the owner's #3474 decision. Follow **Restore and gaps** using `<rederive-copy>` for these stops. A T197 key returning other than the 24 accepted notes-only rows is a separate stop-and-ask, with no restore, as explained after the EDGAR write. Paste the EDGAR listing/shares diff against the backup, split by source PR as above. Also paste the per-table rows written, the moved classification rows, the retract and repair counts, the hole and resolver counts, `spliced_series` before/after, coverage buckets before/after, `figi_types` if run, the BOOK CHECK result and release ids, on T198's PR, #1682, #3056 and #3084.

- [ ] **Owner health go for step 9.** Review the step’s findings and `health-rows-9.txt` under the #3474 rule above and record the #1682 go before `coverage --record` or close.

  ```bash
  uv run tradepartner health --coverage
  uv run tradepartner decision data-release coverage --name rederive-1682 --record  # exit 1: stop, do not close
  uv run tradepartner decision data-release close --name rederive-1682 --from 2016-01-04 --to <last-touched-session> --reason "#1682 actual rows by source PR, read-time merges and trial replay rule"
  ```

## 6. #3080: `classstart-3080` (79-id class-window backfill, #3090) — runs after step 9

Purpose: fetch the bars of the class ids that #3090's rule leads from their company's first filing. Each was its company's only equity id, which until step 9 got bars only from its first cover page (P&G `0000080424:common-stock` had bars only from 2024-08-05). **It runs after step 9, so its ids and counts come from the store step 9 left**, not from #3090's measurement on the old store.

- [ ] **Rebuild the id list from the store after step 9.** Step 9 retired some of the 79 class-keyed ids (P&G, `0001824403:common-stock` and `0001846235:common-stock` in the re-rehearsal) and re-derived them as the plain CIK id; its store-wide fill should already have refetched P&G `0000080424` (its dry run listed 130 months). Confirm that from this step's own dry run. List which of the 79 still exist and which ids each CIK now holds (`still_listed` false means retired):

  ```bash
  cat > ~/tradepartner-probes/sitting-inputs/id-rebuild.sql <<'SQL'
  WITH asked AS (SELECT unnest(string_split($1, ',')) AS asked_id),
  latest AS (
    SELECT security_id, ticker, retracted,
      row_number() OVER (PARTITION BY security_id, ticker, exchange, valid_from ORDER BY known_at DESC, ingested_at DESC) AS rn
    FROM listings
  ),
  live AS (SELECT DISTINCT security_id, ticker FROM latest WHERE rn = 1 AND NOT retracted)
  SELECT a.asked_id,
    EXISTS (SELECT 1 FROM live l WHERE l.security_id = a.asked_id) AS still_listed,
    (SELECT string_agg(DISTINCT l.security_id, ',') FROM live l
      WHERE substr(l.security_id, 1, 10) = substr(a.asked_id, 1, 10)
        AND l.ticker IN (SELECT x.ticker FROM listings x WHERE x.security_id = a.asked_id)) AS ids_of_that_cik
  FROM asked a ORDER BY still_listed, a.asked_id
  SQL
  tpsql "$(cat ~/tradepartner-probes/sitting-inputs/ids3053.txt)" < ~/tradepartner-probes/sitting-inputs/id-rebuild.sql
  cat > ~/tradepartner-probes/sitting-inputs/id-rebuild.py <<'PY'
  from pathlib import Path
  import sys
  from tradepartner.config import get_settings
  from tradepartner.store.db import open_read_only

  root = Path.home() / "tradepartner-probes/sitting-inputs"
  source, target = map(Path, sys.argv[1:3])
  original = (root / source).read_text().strip().split(",")
  has_spy = "BENCH:SPY" in original
  asked = ",".join(x for x in original if x != "BENCH:SPY")
  sql = (root / "id-rebuild.sql").read_text()
  with open_read_only(get_settings()) as conn:
      rows = conn.execute(sql, [asked]).fetchall()
  mapped, retired, dropped = [], [], []
  for old_id, still_listed, same_ticker_ids in rows:
      if still_listed:
          mapped.append(old_id)
      elif same_ticker_ids:
          new_ids = sorted(same_ticker_ids.split(","))
          mapped.extend(new_ids)
          retired.append((old_id, new_ids))
      else:
          dropped.append(old_id)
  result = ",".join(sorted(set(mapped) | ({"BENCH:SPY"} if has_spy else set())))
  (root / target).write_text(result + "\n")
  print("rebuilt ids:", result)
  print("unique:", len(result.split(",")), "retired:", retired, "dropped:", dropped)
  PY
  uv run python ~/tradepartner-probes/sitting-inputs/id-rebuild.py ids3053.txt ids3053-after9.txt
  ```

  The script writes `ids3053-after9.txt` as one comma-separated line with no count line: each still-listed id as is, and for a retired id only the live id(s) of its CIK with that retired id's ticker in `ids_of_that_cik`. It prints the full line, its unique count, and every retired and dropped id. Do not add another class or the CIK's notes. A retired id whose CIK has no same-ticker live id is dropped and named on #3080. The final rehearsal mapped PG, RSVR and IMAQ to their base ids: 3 retired, 0 dropped. The real store may differ; a different mapping or count is a stop-and-ask for the owner, not proof of bad data. Do not silently drop an id. **The owner approves the printed list and mappings before the dry run** (open question in #3245: #3090's rule may no longer lead from the same ids).
- [ ] **Dry run first; no release is needed for it.** It lists each id's month-holes and fetches nothing. The final rehearsal still listed 2,082 holes after step 9's fill, but Alpaca served none; use the real dry-run count. If the dry run lists no hole, open no release, paste the output on #3080, then run `uv run tradepartner health --check` and `health_rows 6` for the owner's next go.

  ```bash
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security "$(cat ~/tradepartner-probes/sitting-inputs/ids3053-after9.txt)" --dry-run
  ```

  `--since 2016-01-04` must match the store's backfill runs; hole filling reads only committed months of runs with the same `--since`. If the dry run prints `not fetched, unknown id` for any id, the list is wrong: stop (the old `ids3053.txt` once began with a count line that glued itself to P&G's id and dropped it silently, with exit 0). Holes on an id outside the file cannot appear (`--security` limits it).
- [ ] **Owner go or skip before opening `classstart-3080`.** Show the dry-run holes and the final rehearsal's zero-bar result on #3080. The owner decides whether to open the release and ask Alpaca again, knowing that an empty apply still needs a close record and stales trials. Record the decision before `store backup` and `data-release open`; on skip, open no release, record the skipped holes, then run `uv run tradepartner health --check` and `health_rows 6` for the owner's next go.
- [ ] If the dry run lists holes, write them under their own release:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name classstart-3080 --backup <classstart-copy> --reason "#3080 only-class first-span lead, ids rebuilt after step 9"
  date -u +%Y-%m-%dT%H:%M:%SZ   # <apply start>
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security "$(cat ~/tradepartner-probes/sitting-inputs/ids3053-after9.txt)"
  uv run tradepartner health --check
  health_rows 6
  ```

- [ ] **Owner health go for step 6.** Review `health-rows-6.txt` under the #3474 rule above and record the #1682 go. **Only if the release was opened**, run coverage and close. The no-hole, skip and apply paths above are alternatives; run `health_rows 6` once.

  ```bash
  uv run tradepartner decision data-release coverage --name classstart-3080 --record
  uv run tradepartner decision data-release close --name classstart-3080 --from <earliest-touched-session> --to <last-touched-session> --reason "#3080 bars added per id"
  ```

  If the apply writes **no bars**, it has no touched session to supply to the usual close line. After passing `coverage --record`, instead run `uv run tradepartner decision data-release close --name classstart-3080 --from <checked XNYS session> --to <same checked XNYS session> --reason "#3080 no bars written; Alpaca returned none for the listed holes"`. Read `<last committed backfill day>` with the query in step 13, then use the session printed by the zero-bar XNYS check in **Before the first write**, below the touched-session rules, for both `--from` and `--to`; do not pass the raw cursor day. The final rehearsal's dry run listed 2,082 holes over 66 securities and the apply wrote zero bars; paste the real dry-run hole count and applied zero on #3080. Stop on a bar for an id outside the file, a failed coverage record, or a health result the owner has not accepted under the #3474 decision. Follow **Restore and gaps** using `<classstart-copy>`. Paste the dry-run hole list size, the bars written, and each listed id's first stored bar after the run on #3080.
- [ ] **Pre-step-9 figures (the rehearsal; not expectations now).** The dry run took about 260 s (it builds the resolver) and printed `3066 holes (security, month) over 76 securities in 130 months; 324 between stored bars (22 securities); 395 lead gaps; 48 holes the resolver cannot assign`. The apply took **2 h 52 m** (10,342 s): one committed chunk per month of the window, about 75 to 80 s each whatever the ids or holes, and 21,798 rows over 130 `filled` chunks. #3234/#3239 have since changed the per-month cost (a month costs what it writes, and a progress line prints per month); the new time is not measured. Progress can also be read in `ingestion_runs` (`chunk_cursor` `holes;since=2016-01-04;through=<month end>`).

## 7. #3082: `biob-3082` (BIO.B holes, #3086) — runs after step 9

Purpose: fill Bio-Rad class B's holes now that `alpaca.class_symbols` maps `BIOB` to Alpaca's `BIO.B`. It runs after step 9, whose store-wide fill may already have filled BIO.B's months, so this step only writes what is left. Its own release when it writes, so its count is not mixed into rederive-1682's.

- [ ] **Census from the store after step 9.** Run the dry run first; no release is needed for it. The pre-step-9 rehearsal printed `87 holes (security, month) over 1 securities in 87 months; 43 between stored bars; 36 lead gaps` and `BIOb: 2019-08..2026-10 (87 months)`. The 43 between stored bars are the earlier census's months (2019-08 to 2023-02, 2023-04, 2023-08); the rest are months outside the stored bars, up to 2026-10; the earlier census said 45. Check that BIO.B's id `0000012208:class-b-common-stock` still exists first (the query in step 6 with that id); if step 9 re-keyed it, use the new id and stop-and-ask on #3082 if the CIK holds none. If the dry run lists no hole, open no release, paste the output on #3082, then run `uv run tradepartner health --check` and `health_rows 7` for the owner's next go. If it lists holes on more than this one security, or a month outside what step 9 left, stop and ask.

  ```bash
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security 0000012208:class-b-common-stock --dry-run
  ```

  `--since` must be 2016-01-04 (`--since 2019-08-06` finds no holes). The dry run took about 265 s before #3234.
- [ ] **Owner go or skip before opening `biob-3082`.** Show the dry-run holes on #3082. The owner decides whether to fetch them now, knowing an empty apply still needs a close record and stales trials. Record the decision before the backup and open; on skip, open no release, record the skipped holes, then run `uv run tradepartner health --check` and `health_rows 7` for the owner's next go.
- [ ] If it lists holes:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name biob-3082 --backup <biob-copy> --reason "#3082 BIOb listing mapped to BIO.B"
  date -u +%Y-%m-%dT%H:%M:%SZ   # <apply start>
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security 0000012208:class-b-common-stock
  uv run tradepartner health --check
  health_rows 7
  ```

- [ ] **Owner health go for step 7.** Review `health-rows-7.txt` under the #3474 rule above and record the #1682 go. **Only if the release was opened**, run coverage and close. The no-hole, skip and apply paths above are alternatives; run `health_rows 7` once.

  ```bash
  uv run tradepartner decision data-release coverage --name biob-3082 --record
  uv run tradepartner decision data-release close --name biob-3082 --from <earliest-touched-session> --to <last-touched-session> --reason "#3082 holes and bars filled"
  ```

  If it writes no bars, use the zero-bar date check in **Before the first write** and step 6's same-day close pattern, with `biob-3082` as the release name; leave the release open on a refused date. Stop on a bar written to any other security, or a health result outside the owner's #3474 decision. Follow **Restore and gaps** using `<biob-copy>`. Paste the hole count and bars written on #3082. For the first ~52 min of the old apply (2016-01 to 2019-07) no chunk row appeared; that cost (about 2.7 h in all) was measured before #3234 and is not re-measured. 22 bars are stored from 2019-08-06 to 2024-02-15; the bar count of the fill is known only after it (2019-08 got 8 bars and 2019-09 got 5 in the rehearsal).

## 8. #3057: `rename-listings-3057` (#3068) — runs after step 9

Purpose: add the back-dated new-ticker listing rows that stored `symbol_changes` imply, where no cover page gave one (PKI→RVTY, CHK→EXE). Step 9's full rebuild derives the same rows through `build_master`, so after step 9 this step should find **fewer** rows than the rehearsal's 668, which was counted before it. Its census comes from its own dry run on the store after step 9.

- [ ] Dry run first. Expect an RVTY row (`0000031791`, from 2023-05-16) and an EXE row (`0000895126`, from 2024-10-02), unless step 9 already derived them (then they are absent from the list; check them by query in `listings` instead). The rehearsal's dry run (1 s, before step 9) printed `would add 668 new-ticker listing rows of 1649 name changes; refused: exchange_differs 122, new_ticker_held 29, no_evidence 600, old_ticker_not_current 79`, with RVTY known 2026-11-03T00:44:48Z and EXE 2026-04-28T20:03:56Z. For comparison, step 15's dry run in the re-rehearsal (after step 13's name changes) printed 18 rows of 1709 name changes (`exchange_differs 124, new_ticker_held 29, no_evidence 521, old_ticker_not_current 100`). The four refusal reasons are `no_evidence`, `new_ticker_held`, `exchange_differs` (the evidence is on another exchange) and `old_ticker_not_current` (the class showed another ticker that day). The dry run lists every row and refusal. **Read each row**, and the refusals by reason. A refusal reason not in this list is a stop-and-ask. A row that would give a ticker another security holds is a stop.
- [ ] Write only what the dry run lists. If it lists no row, open no release, paste the output on #3057, then run `uv run tradepartner health --check` and `health_rows 8` for the owner's next go.

  ```bash
  uv run tradepartner repair-rename-listings --release rename-listings-3057 --dry-run
  # only when the dry run lists rows:
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rename-listings-3057 --backup <rename-copy> --reason "#3057 new-ticker listing rows from stored symbol changes"
  uv run tradepartner repair-rename-listings --release rename-listings-3057 --expect-rows <dry-run-rows>
  uv run tradepartner health --check
  ```

- [ ] **Fill the new rows' holes.** A new listing row counts as fetched in its months, so it can open holes that step 9's fill (run before this step) never saw. After the apply, append the `+` ids of the dry run to `~/tradepartner-probes/sitting-inputs/changed-ids.txt`, then run `uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes --security "<those ids, comma-separated>" --dry-run`. If it lists holes, record `date -u +%Y-%m-%dT%H:%M:%SZ` just before the fill apply, then run the same command without `--dry-run` under this release. Read its written sessions with the query in **Before the first write**, using that start time; include the first and last in `--from`/`--to`. If it lists no holes, say so on the issue. Step 15 uses this same start-time check.
- [ ] After the fill check, run `health_rows 8` once and get the owner's #1682 decision. **Only if the release was opened**, run `uv run tradepartner decision data-release coverage --name rename-listings-3057 --record`. Stop on exit 1. Then run `uv run tradepartner decision data-release close --name rename-listings-3057 --from <earliest-touched-session> --to <last-touched-session> --reason "#3057 rows added and refused by reason"`. On the no-row path, use the health record made above; do not call `health_rows 8` again.
- [ ] **A listing write can add a health failure of its own.** In the re-rehearsal this step's write added CEIN NYSE from 2024-08-08 (known 2024-08-26), which made `non_overlapping_listings` fail on CEI's NYSE row (Form 25 filed 2024-08-16, more than `master.transfer_window_sessions` after CEIN's start). Name every new failing row on #3057 and ask the owner under #3474 before proceeding.
- [ ] Follow **Restore and gaps** using `<rename-copy>` on a stop. Paste the dry-run list, refusals by reason and the applied count on #3057.

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

  **Orphan stop applies here too (#3265):** if this step's `repair-resolution` dry run prints any `ORPHAN` line, do not apply, and never pass `--allow-orphans` without the owner's count posted on #3264 (step 9's orphan stop).
  Expect no new successor, no retract row and no resolution row, except that VLD's and ADEX's old-id bars may still be listed by `repair-resolution` if step 9 left them. Run `health --check` here too. Record every failing row and the owner's decision under #3474; do not assume this unrehearsed step clears `old_id_bars_after_successor`. The fill dry run still lists the long-standing Alpaca-absent holes (109,051 over 5,462 securities in the re-rehearsal, which had stopped its fill at 2016-08): judge only the holes on the ids step 9 changed, as there (`changed-ids.txt`). GPOR's old id should end at its own last print. WW's 07-07..07-11 bars were not cut in the re-rehearsal (`repair-resolution` never listed WW); read that and ask. If every dry run is empty, open no release, paste the outputs and `spliced_series` on #3056, then run `uv run tradepartner health --check` and `health_rows 10` for the owner's next go. The re-rehearsal's dry runs were empty (EDGAR 29 m, `master-retract` 26 m, `repair-resolution` 37 s, fill 283 s, about 58 m in all).
- [ ] **Residual repair-resolution rows (#3472, #3476).** The #3472 and #3476 code is in the pull, so step 9's `repair-resolution` apply already includes the old-id and sibling-refused rows; step 10's dry run is expected to list none. If it lists rows anyway, follow this step's #3056 splice path: (1) `--allow-orphans` only with the owner's exact orphan count posted on #3264, as in step 9 (never read from memory or from step 9's count); (2) scope the fill to the named ids with `--fill-holes --security "<those ids, comma-separated>"` instead of the store-wide fill; (3) if coverage still fails after the fill, `accept-unreceived --count` only with the owner's exact count posted on #3056. Record the rows and the owner's decisions on #3056.
- [ ] **accept-unreceived and coverage must agree (#3479, fixed by #3480).** Before the fix, `accept-unreceived` matched without `store.release_move_price_decimals` and could list a different set than coverage (#3477). In steps 9 and 10, compare the `accept-unreceived` listing count with the coverage `NOT RECEIVED` sum; if the counts differ, stop, post both outputs, leave the release open and do not pass `--count`.
- [ ] Only if a dry run lists rows, repeat step 9's BOOK IDS snapshot with the listed ids added, writing `book-ends-before-10.txt` (step 9's files stay as they are). Then write under its own release. Right after the `master-retract` and `repair-resolution` lines, take `book-ends-after-10.txt` and diff the two. Any diff line on a BOOK ID is a STOP, as in step 9's BOOK CHECK (b), together with BOOK CHECK (a) on this release's `ingested_at`:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name splice-3056 --backup <splice-copy> --reason "#3056 post-bankruptcy successor splice, residual after rederive-1682"
  uv run tradepartner master-retract --apply --expect-rows <n> --expect-digest <d> --release splice-3056
  uv run tradepartner repair-resolution --expect-bar-rows <n> --expect-action-rows <n> --release splice-3056
  date -u +%Y-%m-%dT%H:%M:%SZ   # <apply start>
  uv run tradepartner ingest --source alpaca --backfill --since 2016-01-04 --fill-holes   # on the residual path above, add --security "<those ids, comma-separated>"
  set -o pipefail  # keep pipeline failures visible for this terminal
  uv run tradepartner decision data-release coverage --name splice-3056 | tee ~/tradepartner-probes/sitting-inputs/coverage-step10.txt  # read-only; exit 1 is expected if keys remain (the recorded line comes after any acceptance)
  ```

- [ ] **Check step 10 read-only coverage before unreceived acceptance.** Require `0 not moved`, total unmoved sessions no higher than the printed limit, and no `OWNERLESS` line. Exit 1 is acceptable only for the listed `NOT RECEIVED` spans; anything else stops the sitting.

  ```bash
  uv run tradepartner decision data-release accept-unreceived --name splice-3056 | tee ~/tradepartner-probes/sitting-inputs/accept-unreceived-step10.txt  # list residual keys; exits 1 without writing
  awk -F, '/^RETRACTED prices_daily/ && /sessions NOT RECEIVED/ {spans++; for (i=1; i<=NF; i++) if ($i ~ /sessions NOT RECEIVED/) {gsub(/^ +/, "", $i); split($i, a, " "); n+=a[1]}} END {print n+0, "sessions in", spans+0, "spans"}' ~/tradepartner-probes/sitting-inputs/coverage-step10.txt
  uv run tradepartner health
  uv run tradepartner health --check
  health_rows 10
  ```

- [ ] After the fill, use the `awk` sum above from read-only `coverage-step10.txt` and compare it with the `accept-unreceived` listing count. A mismatch is a stop (#3477, #3479); leave the release open and post both outputs on #3056. The owner reviews every residual key, posts the exact matching count on #3056, and decides whether to run `accept-unreceived --count`. If there are none, record zero and skip the write. A mismatched `--count` writes nothing; leave the release open on refusal. Run this only with the owner's exact count posted on #3056:

  ```bash
  uv run tradepartner decision data-release accept-unreceived --name splice-3056 --count <owner-approved-exact-count>
  ```

- [ ] **Owner health go for step 10.** Review `health-rows-10.txt` under the #3474 rule above and record the #1682 go before coverage or close. `coverage --record` must run after any acceptance.

  ```bash
  uv run tradepartner decision data-release coverage --name splice-3056 --record  # exit 1: stop, do not close
  uv run tradepartner decision data-release close --name splice-3056 --from <first> --to <last> --reason "#3056 counts"
  ```

  Run only the lines whose dry run listed rows. A residual EDGAR write means step 9's derivation did not settle: stop and ask before writing it. Follow **Restore and gaps** using `<splice-copy>`.

## 11. #3084: `form25-3084` (residual only, #3095)

**Skip this step when step 10 opened no release.** Its two dry runs below are step 10's first two on an unchanged store (nothing wrote between), so repeating them costs about 55 m for nothing. On this skip, run `uv run tradepartner health --check` and `health_rows 11`, get the owner's #1682 go, then continue to step 12.

Purpose: apply what is left of #3095's Form 25 repair after step 9. Its EDGAR write and `master-retract` were part of step 9, which ran #3095's BOOK CHECK and STOP.

- [ ] Dry runs first; no release is needed for them:

  ```bash
  uv run tradepartner ingest --source edgar --dry-run
  uv run tradepartner master-retract
  ```

  The second EDGAR run reads the HTML Form 25s from the `DELISTING_VERSION` cache and the unread ones from `delisting_html_unread/v1/`, so it fetches none again. Expect no new delistings row and no retract row. If both are empty, open no release, paste the outputs on #3084, then run `uv run tradepartner health --check` and `health_rows 11` for the owner's next go.
- [ ] Only if `master-retract` lists rows: repeat step 9's BOOK IDS snapshot with the listed ids added, writing `book-ends-before-11.txt`. Right after the `master-retract --apply` line, take `book-ends-after-11.txt` and diff the two. Any diff line on a BOOK ID is a STOP, as in step 9's BOOK CHECK (b):

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name form25-3084 --backup <form25-copy> --reason "#3084 issuer HTML Form 25s, unit/comma class titles, residual after rederive-1682"
  uv run tradepartner master-retract --apply --expect-rows <n> --expect-digest <d> --release form25-3084
  uv run tradepartner health
  uv run tradepartner health --check
  health_rows 11
  ```

- [ ] **Owner health go for step 11.** Review `health-rows-11.txt` under the #3474 rule above and record the #1682 go before coverage or close.

  ```bash
  uv run tradepartner decision data-release coverage --name form25-3084 --record
  uv run tradepartner decision data-release close --name form25-3084 --from <first> --to <last> --reason "#3084 counts"
  ```

  A residual EDGAR write means step 9's derivation did not settle: stop and ask. On a STOP, follow #3095's restore, which is **Restore and gaps** using `<form25-copy>`. Keep paper jobs unloaded and confirm `pgrep -f "tradepartner "` is empty, save the failed state first, check the manifest's `wal_bytes`, and do not run `data-release close`. Record the rollback and removed release id on #3084. T248f's merger scan (`ingest --source alpaca --actions-backfill --since 2020-06-01`) is owner-run and not part of this sitting.

## 12. #3058: `spinoff-3058` (#3067)

Purpose: retract the 33 spin-offs stored as splits (2016-04-04..2021-11-02), which put fake returns into their parents' 2016–2021 prices. Then ask Alpaca per symbol for the pre-2020-11 spin-offs the store never received. It runs before steps 13 and 14, so their split counts read a store without the fake splits. Ingest already refuses the listed rows, so a refetch never brings them back. `adjust.apply_spin_offs` stays `false`; no registered trial moves except through this release's vintage.

- [ ] Census first. The repair dry run printed `would retract 33 spin-off split rows of 33 identities on 30 ex-dates 2016-04-04..2021-11-02; entries 33, already repaired 0, stale 0` in the re-rehearsal (2 s). Run the repair before the backfill. The backfill dry run takes about 11 m, prints one line per month (58 months) and **no per-row lines**, so the hand checks cannot be made in it; make them by query after the write (below). **Do not add up the dry run's month lines**: they repeat the same late rows (the dry run rolls back), so the re-rehearsal's sum was 15 rows while the apply wrote 3. Put only the apply's count in the close reason.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name spinoff-3058 --backup <spinoff-copy> --reason "#3058 spin-offs stored as splits; pre-floor spin-off backfill"
  uv run tradepartner repair-spinoff-splits --release spinoff-3058 --dry-run
  uv run tradepartner repair-spinoff-splits --release spinoff-3058 --expect-action-rows 33
  uv run tradepartner spinoff-backfill --since 2016-01-04 --through 2020-10-31 --dry-run
  uv run tradepartner spinoff-backfill --since 2016-01-04 --through 2020-10-31 --release spinoff-3058
  uv run tradepartner health --check
  health_rows 12
  ```


- [ ] **Hand checks, after the write, by query.** The dry run cannot show them. The apply is expected to write 3 rows in all (the re-rehearsal: `0001681622` ex 2017-01-30, WAB `0000943452` ex 2019-02-25, `0001820877` ex 2020-12-15). Check the five dates the plan names, any security:

  ```bash
  tpsql <<'SQL'
  SELECT security_id, action_type, ex_date, ratio_or_amount, known_at, ingested_at FROM corporate_actions
  WHERE action_type = 'spinoff' AND ex_date IN (DATE '2016-10-03', DATE '2018-10-01', DATE '2018-10-29', DATE '2019-02-25', DATE '2020-04-01')
  ORDER BY ex_date, security_id
  SQL
  ```

  **Expected:** the GE→WAB row (2019-02-25) is present. **HON's three rows (2016-10-03, 2018-10-01, 2018-10-29) and HWM→ARNC (2020-04-01) are expected to be absent, and their absence is not a stop.** Before about 2020-11 Alpaca typed these distributions as splits and its spin-off endpoint has no record of them (it returned no spin-off actions at all for 2016-10, 2018-10 and 2020-04), so `spinoff-backfill` cannot recover them. They are the rows `repair-spinoff-splits` retracted (`store/spinoff_splits.csv`). Storing them is #3247. Paste the query's output on #3058 and say so.
- [ ] Stop when the repair dry run shows a count other than 33 or any stale entry. Also stop when the backfill dry run lists `split_spinoff_collisions` above 0: review those rows (add them to the CSV, or decide on #3066) before the real run, which refuses that month. Stop too on a health result outside the owner's #3474 decision. Follow **Restore and gaps** using `<spinoff-copy>`. Paste both dry runs, the retracted and added counts, the hand-check query output and release ids on #3058.

- [ ] **Owner health go for step 12.** Review the step’s findings and `health-rows-12.txt` under the #3474 rule above and record the #1682 go before coverage or close.

  ```bash
  uv run tradepartner decision data-release coverage --name spinoff-3058 --record
  uv run tradepartner decision data-release close --name spinoff-3058 --from <earliest-touched-ex-date> --to 2021-11-02 --reason "#3058 33 split rows retracted, N spin-offs added"
  ```

## 13. #3054: `actions-3054` (missing splits and dividends)

Purpose: store the pre-2020 name changes, then ask Alpaca again for the splits and dividends of committed months, so a renamed security's pre-rename symbol carries its actions (`ListingResolver.action_alias`). Also retract the listed duplicate dividends still in the store (STX 2021-05-11, PFLT 2026-10-15 `0001504619`, and any other the reviewed list holds once step 9's ids are in).

- [ ] **Gate: do not start unless step 9's `repair-resolution` was applied** (or its dry run found nothing), step 9's fill is done and `rederive-1682` is closed. Without it the refetch writes rows a second time under the CIK's new id: the rehearsal's lane B (`repair-resolution` refused on 67 orphans, #3288) found all 63 rekeyed splits still live under the old same-CIK id and 113 of 804 `new` dividends duplicating a live dividend on another id of the same CIK and ex-date (the `collision` tag is per `security_id` and does not see them). `open actions-3054` also refuses while `rederive-1682` is open. If step 9 stopped short of that, stop here and ask.
- [ ] **`<last committed backfill day>`** is the through-day of the newest committed alpaca backfill month (2026-11-02 in the re-rehearsal). Read it, do not guess it:

  ```bash
  tpsql <<'SQL'
  SELECT MAX(regexp_extract(chunk_cursor, 'through=(.*)$', 1)) AS last_committed_backfill_day
  FROM ingestion_runs
  WHERE source = 'alpaca' AND mode = 'backfill' AND status = 'ok' AND chunk_cursor LIKE 'since=%'
  SQL
  ```

  The step-9 and later hole fills write `mode = 'holes'` rows, which this leaves out.
- [ ] Census first: dry-run both steps and read the counts before writing. The measurement (#3053, 11-02 store) put 307 missing dividends and 61 missing splits under an old symbol the master never lists. That census covers **only actions before the rename**. After step 9 the refetch also writes rows the census never counted, so it is not a ceiling on splits (below). Rows with no rename (SPY 2016-03-18, 2018-06-15) are expected to stay missing unless Alpaca serves them now.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name actions-3054 --backup <actions-copy> --reason "#3054 name changes 2016-2020 and actions refetch"
  uv run tradepartner ingest --source alpaca --actions-backfill --name-changes-only --since 2016-01-01 --through 2020-05-31 --dry-run
  uv run tradepartner ingest --source alpaca --actions-backfill --name-changes-only --since 2016-01-01 --through 2020-05-31
  uv run tradepartner refetch-actions --since 2016-01-04 --through <last committed backfill day> --release actions-3054 --dry-run | tee ~/tradepartner-probes/sitting-inputs/refetch-3054-dry.txt
  # read the stop rules below; only then:
  uv run tradepartner refetch-actions --since 2016-01-04 --through <last committed backfill day> --release actions-3054 --skip-collisions   # after the owner's read of the list on #3054 (#3484); add --allow-collision for any judged distinct
  uv run tradepartner repair-dividends --release actions-3054 --dry-run
  uv run tradepartner repair-dividends --release actions-3054 --expect-action-rows <dry-run-row-count>
  uv run tradepartner health --check
  health_rows 13
  ```


  Time (re-rehearsal): name-change dry run and apply about 9 m each (53 months); the `refetch-actions` dry run about **33 m** (54 m with other lanes running) and its apply about **66 m** (#3303: a real run first makes a rolled-back dry pass over every month, then writes), both printing nothing until the end. The step is about 2 h.
- [ ] **Stop before writing on any `collision` or `revision` row.** The dry run tags each line `new`, `revision` or `collision`. A `collision` is a new id on a type and ex-date the security already holds live, so the dividend would apply twice; a `revision` is a stored identity with changed values stamped at the run. The re-rehearsal's dry run had 34 collisions and 25 revisions among 914 lines, and writing them took `duplicate_dividends` from 2 to 21 rows. Count them from the saved dry run:

  ```bash
  grep -cE '^  (would write|refused) collision ' ~/tradepartner-probes/sitting-inputs/refetch-3054-dry.txt
  grep -E '^  (would write (collision|revision)|refused collision) ' ~/tradepartner-probes/sitting-inputs/refetch-3054-dry.txt
  ```

  Both counts must be 0, or the owner has read every listed line and approved it on #3054. Since **#3246**, `refetch-actions` refuses collision rows (the dry run prints them as `refused collision ...`; a real run refuses the **whole run before writing anything in any month** and exits 1, #3303/#3309; a month it cannot check, such as a rolled-back pre-pass, refuses it the same way, and an up-front collision refusal leaves no failed `ingestion_runs` row, though a closed-release refusal still does) unless a `distinct` group of the reviewed dividend list or `--allow-collision <security>:<ex-date>` covers them, so this check is the sitting's read, not its only guard. A `revision` is still not refused. Some collisions are false positives (EXE's regular and special dividend on one day); that is the owner's read, not a waiver of the stop. Paste the list on #3054 and do not write without that word. **#3484:** after the owner reads the list on #3054, the real run adds `--skip-collisions`: every collision no `--allow-collision` or `distinct` group covers is left unwritten (the stored row and its `known_at` stay), all other rows write, and the run prints one `skipped collision <security> <type> <ex-date> <amount> known_at=<stamp>` line per skip and counts them in its summary. The dry run with the flag prints `would skip collision ...`. Any collision the owner judges distinct goes in `--allow-collision <security>:<ex-date>` instead. Without the flag the whole run is still refused. Save the skipped lines and paste them on #3054, and record the skipped count there too. The final rehearsal's dry run had 33 collisions on 15 securities (most repeat a stored dividend under a new Alpaca id; a few differ in amount, such as `0001654795` from 2023-08 and `0001545851`). Before the flag, a real run refused the whole job and would have written none of its 131 dividends, 53 splits and 27 revisions. **The owner reads these collision rows on #3054 first**; only then does the real run take `--skip-collisions`.
- [ ] **Splits: re-keyed rows are counted apart from new ones.** The dry run's last line names the rows per type (`would add N dividend, M split`). Do not compare M with the census's 61 + 26 = 87. After step 9, `repair-resolution` retracted split rows under retired ids and the refetch writes them again under the new id: in the re-rehearsal 63 of the 164 split rows were such re-keyed copies (all 2021–2026), and 101 were rows the store never had (28 from 2016–2020, 73 from 2021–2026, clustered on small issuers with recurring events such as UWHR's annual stock dividends). Classify each split line against the pre-step-9 copy (`<rederive-copy>`): a split is **re-keyed** when that copy holds a split on the same CIK (the first 10 characters of the id) and ex-date, **new** otherwise.

  ```bash
  awk '$1=="would" && $3=="new" && $5=="split" {print $4","$6}' ~/tradepartner-probes/sitting-inputs/refetch-3054-dry.txt | sed '1i\
  security_id,ex_date' > ~/tradepartner-probes/sitting-inputs/refetch-3054-splits.csv
  BEFORE_COPY=<rederive-copy> tpsql ~/tradepartner-probes/sitting-inputs/refetch-3054-splits.csv <<'SQL'
  WITH d AS (SELECT security_id, CAST(ex_date AS DATE) AS ex_date FROM read_csv($1, header = true, all_varchar = true))
  SELECT d.security_id, d.ex_date,
    CASE WHEN EXISTS (SELECT 1 FROM before.corporate_actions b
           WHERE b.action_type = 'split' AND b.ex_date = d.ex_date AND substr(b.security_id, 1, 10) = substr(d.security_id, 1, 10))
         THEN 'rekeyed' ELSE 'new' END AS kind
  FROM d ORDER BY kind, d.security_id, d.ex_date
  SQL
  ```

  **Stop when the `new` split rows exceed 87 plus a margin the owner agrees on #3054 before the run** (the margin is an open question in #3245: the re-rehearsal's 101 would fail the bare 87), or when a `new` split lands on a name with no matching raw price gap (the owner reads the name's raw bars around the ex-date; no command does it). Report the `rekeyed` rows separately and do not count them against the census. Two of the 63 re-keyed rows were still live under the old id in the re-rehearsal, so the split then exists under two ids: list those too (a `rekeyed` row whose old id still holds the split) and read them. Sixteen of the 101 new rows fell before their id's first listing; read any such row.
- [ ] **`repair-dividends` and the reviewed list.** It expects STX and PFLT, plus any other duplicate the list holds that the store still has, and **refuses a real run on any stale entry** (`... the retract identity has no row and 0 live identities hold the key`). The re-rehearsal got 6 rows (PFLT, STX, three `0001496048` rows and `0001737924`) and 1 stale entry (APA's `0000006769` 2021-01-21, whose dividend moved off the CIK's old id in step 9), so STX and PFLT were not retracted. **Refresh the reviewed dividend list (`store/dividend_reviews.csv`) against the post-step-9 ids before this step.** How that lands mid-sitting, given that nothing merged later is pulled, is an open question in #3245. Any stale entry, and any count other than the list's, is a stop. It does not touch FCX's #2972 key. The close's `--to` is the latest ex-date any command in this release touched; PFLT's retract makes that at least 2026-10-15.
- [ ] Name-change step: read `symbol_change_backdated` and `actions_unresolved` on each month's run row. Paste both summaries, the collision and revision counts, the re-keyed/new split table, the repair count and `health --check` on #3054. Record the owner's decision on any health failure beyond those already accepted under #3474; the re-rehearsal's `duplicate_dividends` 2 to 21 came from the collisions above.

- [ ] **Owner health go for step 13.** Review the step’s findings and `health-rows-13.txt` under the #3474 rule above and record the #1682 go before coverage or close.

  ```bash
  uv run tradepartner decision data-release coverage --name actions-3054 --record  # exit 1: stop, do not close
  uv run tradepartner decision data-release close --name actions-3054 --from 2016-01-04 --to <last touched ex-date, at least 2026-10-15> --reason "#3054 added actions by type, retracts"
  ```

## 14. #3074: `actions-3074` (span refetch for actions with no rename)

Purpose: tell vendor absence from late processing for the rows step 13 cannot reach, then store what Alpaca serves. Alpaca filters on `process_date`. A month request asks only `alpaca.actions_process_lag_days` (90) past the month, so a record Alpaca processed later is in no month's answer. `refetch-actions --span` asks once over the whole range for the named securities. It asks every symbol each one held and every process date up to today (`backfill.span_settings`). Timing is unchanged: a first-seen row is stamped at the close of the session before its ex-date.

- [ ] Run it after step 13's release is closed **and step 13 wrote every month.** The census depends on it: step 14's candidates are the actions step 13 did not write, and the rehearsal's 85 would-add rows all had ex-dates from 2019-08 on, because step 13 halted at its first collision month (2019-08). A step 13 that stopped early leaves its tail in this step's count, so the figures cannot be judged until it completes. **Check the input file first:** `~/tradepartner-probes/sitting-inputs/spyacts3074-ids.txt` must name `0001486159@2020-11-23` (OAS successor, which the repairs make live) and not `0001486159@2021-03-08`, which they retire. Replace the old id; the count stays 320. **Then rebuild the ids against the store after step 9:** use step 6's saved SQL and script. The script leaves `BENCH:SPY` as is, checks the other 319 ids against live same-ticker listings, writes the comma-separated file, and prints every retirement or drop. Record any drop on #3074. The original list has 320 ids, including SPY. Stop if the rebuilt count differs and ask the owner; the owner approves the printed list and mappings before the dry run. The original list is SPY plus the 319 securities of the #3053 measurement's "no later rename" and "old symbol known" groups (320 ids, checked in step 5; the figures stay local). HTA (`0001360604`) is left out: its 26 "missing" dividends are old Healthcare Realty's, not HTA's (#3074 PR).

  ```bash
  uv run python ~/tradepartner-probes/sitting-inputs/id-rebuild.py spyacts3074-ids.txt spyacts3074-after9.txt
  uv run tradepartner refetch-actions --span --since 2016-01-04 --through <last committed backfill day> --release actions-3074 --security "$(cat ~/tradepartner-probes/sitting-inputs/spyacts3074-after9.txt)" --dry-run
  ```

- [ ] **Read step 14's dry run before any write.** It prints `new`, `revision` and `collision` rows. Check SPY's two dates. Stop and open no release on any `revision` or `collision`, or a new split with no matching raw price gap; use the detailed rules below. **Without `--skip-collisions`, a dry run that shows collision or revision rows means no write and no release.** The final rehearsal's dry run showed 4 collisions (`0000895126` x2, `0000107140`, `0001547546`) and so blocked 59 new rows; with the flag the real day writes them. If it lists no row, use the no-release path below. Only after this check, run the apply block. **#3484:** once the owner has read the listed collisions, the apply line may add `--skip-collisions` (it works with `--span`): each uncovered collision is left unwritten with its `skipped collision ...` line, the other rows write, and a collision the owner judges distinct goes in `--allow-collision <security>:<ex-date>`. Paste the skipped lines on #3074 and record the skipped count. The flag covers collisions only; a `revision` still needs the owner's read before any write.

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name actions-3074 --backup <actions3074-copy> --reason "#3074 span refetch, no-rename actions"
  uv run tradepartner refetch-actions --span --since 2016-01-04 --through <last committed backfill day> --release actions-3074 --security "$(cat ~/tradepartner-probes/sitting-inputs/spyacts3074-after9.txt)" --skip-collisions   # only after the owner reads the collisions (#3484)
  uv run tradepartner health --check
  health_rows 14
  ```

- [ ] If step 14's dry run lists no row, open no release, run `uv run tradepartner health --check` and `health_rows 14`, and get the owner's #1682 go before step 15.

- [ ] **Owner health go for step 14.** Review `health-rows-14.txt` under the #3474 rule above and record the #1682 go. **Only if the release was opened**, run coverage and close.

  ```bash
  uv run tradepartner decision data-release coverage --name actions-3074 --record
  uv run tradepartner decision data-release close --name actions-3074 --from 2016-01-04 --to <last touched ex-date> --reason "#3074 added actions by type"
  ```

- [ ] Read the dry run before writing. It prints one `would write <tag> <security> <type> <ex-date> <amount> known_at=<stamp>` line per row, then the totals. The tag is `new`, `revision` (a stored identity with changed values, stamped at the run) or `collision` (a new id on a type and ex-date the security already holds live, which would apply the event twice). Since #3246 a real run refuses collisions (nothing written for the month, exit 1), and the dry run also prints them as `refused collision ...`; only a `distinct` group of the reviewed dividend list, or `--allow-collision <security>:<ex-date>` after you review it, lets one through. The census is at most 225 + 74 dividends and 70 + 26 splits; in the re-rehearsal most of it was absorbed by step 13's store-wide refetch (7,080 actions for 319 names, `added dividend=1`, 27 s, and SPY's two dates were not served). Check SPY first:
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

  If it lists no row, open no release, paste the output on #3057, then run `uv run tradepartner health --check` and `health_rows 15` for the owner's next go. If it lists rows, read each one as in step 8, then:

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name rename-listings-3057b --backup <rename-b-copy> --reason "#3057 rows from step 13's name changes"
  uv run tradepartner repair-rename-listings --release rename-listings-3057b --expect-rows <dry-run-rows>
  uv run tradepartner health --check
  ```

  After the apply, fill its new rows' holes as in step 8 (**Fill the new rows' holes**). Then run `health_rows 15` and get the owner's #1682 decision. Run `uv run tradepartner decision data-release coverage --name rename-listings-3057b --record`; stop on exit 1. Close with `uv run tradepartner decision data-release close --name rename-listings-3057b --from <earliest-touched-session> --to <last-touched-session> --reason "#3057 residual rows and refusals by reason"`, using the fill's actual span if it wrote bars. Stop rules and restore as in step 8, using `<rename-b-copy>`. The re-rehearsal's dry run printed 18 rows of 1709 name changes (2019-11-06..2020-05-29) and the write added no `non_overlapping_listings` row.

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

  Any other figure is a stop. Paste every printed output on #3116: the two exposure ids against launches 2 and 3, the before and after listings, and N. On #3110, note that #3092 was not recorded, and why (above). Citing the ids in the desk-read note and in `docs/research/claims.toml` is a later PR. Run `uv run tradepartner health --check` and `health_rows 16` before the owner's next go.

## 17. Gap sign-off replay (#3117)

Purpose: give each book a survivorship-gap sign-off computed on the post-sitting store. `paper start` requires that some `gap_signoff` row points at an ok, non-synthetic in-sample trial of the hypothesis, and once the store holds a closed data release that the trial ran on the released store: its recorded `data_vintage` must be at or after the `made_at` of the newest release reaching its `data_cutoff`, and a trial with no `data_cutoff` or `data_vintage` fails closed (`execution/window.py`, `_gap_signoff_refusal`, #3178). It also refuses with `release_open` while any release is open (#3188). The code guard does not read the gap figure's meaning or the sign-off's window, so the replay below and the SQL check stay as a cross-check. The current sign-offs are from before steps 1–15: main (hypothesis 1) rests on decision 4, and daily (hypothesis 9) rests on trial 17 and decision 7, which was never replayed. **Restart nothing on a sign-off computed before the sitting.**

- [ ] **Run the replay alone, with swap and disk checked first.** Nothing else of the project may run: no ingest, lab, backtest or refetch window, no second replay. On the 16 GB rehearsal machine the daily replay (RSS 2 to 3.4 GB in the earlier runs; **6.34 GB** in the later solo run) beside two other jobs grew macOS swap, which lives on the data volume, to 11.3 GB of 12 GB, and free disk fell from 12 to 3.7 GiB, under `F`. The `S x C + X + F` precheck has no swap term. Before the replay run `pgrep -f "tradepartner "` (must print nothing), `sysctl vm.swapusage` and `df -h data`, and **stop unless free space is at least `F` + 4 GB (9 GB) plus the swap the replay can still add** (macOS grows swap on demand, so there is no fixed ceiling: watch `sysctl vm.swapusage`, and stop the replay if free space nears `F`). Recheck `df -h data` while it runs; a replay interrupted for disk is recorded `failed` and counts in N.
- [ ] **The job checkout must have no untracked files** before any replay. A trial records `code_dirty = true` when it does (the re-rehearsal's trial 37 did, from an untracked folder). Check `git status --porcelain` prints nothing, including untracked entries, and move any scratch folder out. Run this after every release above is closed (and after the #3070 flip, if it was pulled). Read both current sign-offs, read-only:

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

  The daily replay took about 75 m (4,488 s, RSS up to 3.4 GB). Run alone on the copy later it again took 75 m and peaked at about **6.5 GB RAM** (6.34 GB), swap rose to 7.0 GB and free disk dipped from 9.2 to 7.0 GiB, with gap_max_count_share 0.0444 against the 0.05 line. Plan for ~6.5 GB RAM, nothing else running, and the swap and free-disk precheck above. In the re-rehearsal it gave gap_max_count_share 0.0537 against the 0.05 threshold, on a store whose fill had stopped at 2016-08 and whose step-9 deletions were unfilled; whether the real, fully filled store passes is not known until the sitting. Require each new trial to be `ok`, `in_sample` and not synthetic, with `gap_max_count_share` **under** its hypothesis's `count_share_threshold`. A refused or failed replay, or a figure at or over the threshold, stops that book's restart; record it on #3117. Never add `--override-gap`, `--spend-holdout` or `--holdout-repeat` from this sheet. If a replay refuses on a frozen-key drift (for example after the #3070 flip), stop and ask; do not re-register.
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
- [ ] Run `uv run tradepartner health --check` and `health_rows 17` after the replays. Show the saved rows to the owner before the restart decision.

## Restart and close

- [ ] Confirm every opened release has an `after` row and run `uv run tradepartner health --check` and `health_rows final`. Require three count lines in the saved file. If health fails, compare every failing row with the owner's recorded decisions, including #3474's still-open `non_overlapping_listings`, `no_bars_after_delisting` and `old_id_bars_after_successor` item and the reviewed dividend keys. Stop restart on any unapproved failure. The owner explicitly decides whether restart can proceed with the accepted failures still present; a release close alone does not grant that approval. A health FAIL also makes `paper shakedown`'s line E.7 ("health integrity rules all pass", `execution/shakedown.py`) report FAIL; record it, do not call it a pass. Confirm, too, that `uv run tradepartner health --coverage` has the reviewed final buckets. Confirm the counts comment for every release is on #1682. List every stale trial. Besides step 17's two gap replays, replay only H1 and a kill-ladder rung-4 survivor, per ADR 0023. Record retract-only affected sweep decisions under [named data releases](data-releases.md); each rerun counts in N.
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
- [ ] **Then, after the sitting.** Four owner runs whose code is merged are not part of this sheet: T246, T248f, T164e and T324. Run them in the order and with the checks in [after the sitting](after-the-sitting.md). Read its entry point **before** `paper start` above: its T164e step is a named release, which rule 5 of [named data releases](data-releases.md) keeps out of an open paper window, so the owner chooses whether its first steps run before that start.

## Restore and gaps

With every job still stopped and no DuckDB process holding the store, save the **failed live store and WAL first** using `uv run tradepartner store backup`; label its printed paths as failed-state evidence on the release PR/issue. Then restore the chosen checkpoint. Current `store backup` names a copy `<store>.bak-YYYYMMDD-HHMM` and a WAL copy `<copy>.wal` when one exists; older backups use `<store>.wal.bak-YYYYMMDD-HHMM`. Use their **printed paths**, not a guessed release filename. A copy moved off the volume (see the disk precheck) is restored from its recorded new path:

```bash
CHECKPOINT_COPY="<printed-copy-path>"
CHECKPOINT_WAL_COPY="<printed-wal-copy-path-or-empty-string>"  # use "" when none was printed
rm -f data/tradepartner.duckdb.wal
cp "$CHECKPOINT_COPY" data/tradepartner.duckdb
if [ -n "$CHECKPOINT_WAL_COPY" ]; then cp "$CHECKPOINT_WAL_COPY" data/tradepartner.duckdb.wal; fi
```

Before opening the restored store, compare the checkpoint manifest's `wal_bytes` with the restored WAL's size (`stat -f %z data/tradepartner.duckdb.wal` on macOS). A numeric `wal_bytes` requires a WAL of that size; `null` requires no restored WAL. Stop on a mismatch.

**A schema-46 copy.** A copy taken before step 5's migration (the baseline or the pre-migration copy) is at schema 46. Under origin/main code, `health --check` and every release command refuse on it until it is migrated again. So, after the copy and the `wal_bytes` check, and before anything else:
1. Move the original `<pre-migration-copy>` out of `data/`, with its manifest and its WAL copy, if any. Re-hash it and record the new path. The migration's sweep keeps only two `pre_migration` copies in the store's folder and may delete it.
2. Re-run step 5's migration command and its version check. Count the new pre-migration copy in the disk check. Its name has minute precision, like the failed-state copy just taken. If the migration refuses on an existing target, wait for the next UTC minute and rerun.

Then check health (compare a copy taken before step 9 with the recorded baseline; for a later copy, record every failing row and apply the owner's #3474 decision):

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
- **Restart sign-offs (#3117).** `paper start` refuses a sign-off whose trial predates the newest release reaching it, or has no `data_vintage` (`_gap_signoff_refusal`, #3178), and refuses while a release is open (`release_open`). Step 17's replay and SQL check remain a cross-check, and the replay is what produces a sign-off that passes.
- No repo command implements the owner-local `dupdiv.py` index check, H.15 hand check, `rates_as_of` probe, or automatic comparison of T214 fingerprints. Record the actual owner queries and outputs with the evidence; do not infer a passing result from `health`.
