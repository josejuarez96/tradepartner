# Fixture universe (T5)

Synthetic, seeded, deterministic data for `tests/conftest.py`'s `fixture_store`.
24 securities (22 synthetic companies + SPY/MTUM benchmarks), 2018-01-02
through 2021-12-31 on the XNYS calendar. Covers every required case in
[spec req 13](../../../docs/specs/data-foundation.md).

**Regenerate:**

```bash
uv run python scripts/make_fixture_universe.py
```

Deterministic (fixed seed, no wall clock): regeneration is byte-identical
to the committed CSVs (`tests/test_fixture_universe.py`'s
`test_regeneration_is_content_equal_to_committed_csvs`).

**Calendar facts used by every case below:** the holiday is
2019-07-04 (Independence Day, no XNYS session); the half day is
2018-11-23 (day after Thanksgiving, close 18:00 UTC instead of 21:00 UTC).
`gap.missing_tail_sessions=5`, `master.transfer_window_sessions=5`,
`universe.max_shares_age_days=400` (config defaults).

| security_id | cik | case(s) | key dates (T probes for later tasks) |
|---|---|---|---|
| `PLAIN1`, `PLAIN2` | 0001000001, 0001000002 | plain commons, full 2018-01-02..2021-12-31 bar history; statistical filler and a control group | — |
| `TRUNC1` | 0001000003 | delisting with **truncated** history: last bar 2018-08-06, Form 25 filed 2018-09-18 — 29 sessions before the last session before the filing (> `gap.missing_tail_sessions`) | last bar 2018-08-06; filed_at 2018-09-18 |
| `NEARN1` | 0001000004 | delisting **within N**: last bar 2018-08-06, Form 25 filed 2018-08-10 — 3 sessions before the last session before the filing (≤ N, not missing) | last bar 2018-08-06; filed_at 2018-08-10 |
| `MRGR1` | 0001000005 | **clean merger**: last bar 2018-08-06 is exactly the session before the Form 25 filed 2018-08-07 (gap = 0, not missing) | last bar 2018-08-06; filed_at 2018-08-07 |
| `NSE1` | 0001000006 | **Form 25-NSE** delisting | filed_at 2018-10-05 |
| `ZETA_COM`, `ZETA_PFD` | 0001000007 | Form 25 on the **preferred class** (`ZETA_PFD`, "6% Cumulative Preferred Stock"); the common (`ZETA_COM`) stays listed through 2021-12-31 | `ZETA_PFD` filed_at 2019-02-26 |
| `XFER1` | 0001000008 | **exchange transfer**: Form 25 (NYSE) filed 2019-08-06; new listing (NASDAQ) `valid_from` 2019-08-09 (3 sessions later, within `master.transfer_window_sessions`) but `known_at` 2019-08-16 (8 sessions after the filing) | probe T between 2019-08-06 and 2019-08-16 (e.g. 2019-08-12) sees "delisted"; a probe after 2019-08-16 sees the transfer |
| `TIKR1` | 0001000009 | **same-company ticker change**: one `security_id`, listings "OLDT"→"NEWT" on the same exchange (NASDAQ) | second listing `valid_from` 2019-12-02 |
| `REUSE_OLD` / `REUSE_NEW` | 0001000010 / 0001000011 | **ticker reused** by a different company: both list ticker "DUPL", different `security_id`/`cik`, non-overlapping windows | `REUSE_OLD` delisted 2018-06-18 (last bar before); `REUSE_NEW` listed from 2020-05-21 |
| `KAPPA_A` / `KAPPA_B` | 0001000012 (shared) | **dual-class** company: two `security_id`s, one `cik`, both `security_type='common'`; per-class `shares_outstanding` facts via `class_member` ("ClassA"/"ClassB") | facts known_at 2019-01-24 |
| `SPLIT1` | 0001000013 | a **split** (2-for-1, ex-date 2019-03-14): raw close visibly halves at the ex-date session; `known_at` = close of the prior session | ex_date 2019-03-14 |
| `SPLIT2` | 0001000014 | split (1.5-for-1) **between a shares filing and a later month-end T**: fact known_at 2019-04-11 < ex_date 2019-05-24 < T = 2019-06-28 | T = last session of June 2019 (2019-06-28) |
| `SPLIT3` | 0001000015 | split (4-for-1) **known before T with ex-date after T**: explicit announcement known_at 2020-03-11 < T = 2020-03-31 < ex_date 2020-05-21 | T = last session of March 2020 (2020-03-31) |
| `DIVR1` | 0001000016 | **revised dividend**: two rows, ex_date 2018-12-31; first known_at = close before ex-date (2018-12-28), amount 0.20; second known_at = its own ingested_at (2019-02-11), amount 0.25 | ex_date 2018-12-31 |
| `REST1` | 0001000017 | **restated shares fact**: two `facts` rows, same `as_of_date` (2019-01-30), known_at 2019-02-06 (12,000,000) then 2019-03-28 (11,750,000) | as_of_date 2019-01-30 |
| `STALE1` | 0001000018 | **stale shares fact**: known_at 2018-02-14, no later fact; 562 days before month-end T = 2019-08-30 (> `universe.max_shares_age_days`) | T = last session of August 2019 (2019-08-30) |
| `UNCL1` | 0001000019 | **unclassifiable** name: `classifications.rule = 'unclassifiable'`, no SIC | — |
| `STAT1` | 0001000020 | **`snapshot_static`-only pre-2019 listing**: `valid_from` 2017-03-01, `known_at` (fetch time) 2019-10-16 | valid_from 2017-03-01 |
| `SPY`, `MTUM` | 0000900001, 0000900002 | **benchmarks**, `benchmark=true`, seeded (`provenance='snapshot_static'`, `source='config'`, not derived from EDGAR per spec req 3); full bar history plus quarterly dividends | — |

All other securities (`PLAIN1`, `PLAIN2`, and every security above not
otherwise noted) carry a plain `classifications` row (`security_type='common'`,
`rule='sic_default'`).

## Known gap this fixture works around

`tests/conftest.py`'s `load_universe_fixtures` reads every CSV cell through
`read_csv(..., all_varchar=true)` with no `nullstr` override. DuckDB maps
*any* empty cell — quoted or not — to `NULL` under that default, which
`facts.class_member NOT NULL DEFAULT ''` then rejects (confirmed by hand;
no `read_csv` option is exposed by that call to opt out). Every `facts` row
in this fixture therefore has a non-empty `class_member` ("ClassA"/"ClassB"
for the dual-class case, `"common"` elsewhere) rather than the schema's own
default `''`. Filed as a follow-up issue since `conftest.py` is a T4 file,
outside T5's scope.
