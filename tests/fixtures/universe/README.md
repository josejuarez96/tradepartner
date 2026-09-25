# Fixture universe (T5)

Synthetic, seeded, deterministic data for `tests/conftest.py`'s `fixture_store`.
25 securities (23 synthetic companies + SPY/MTUM benchmarks) within the
2018-01 to 2021-12 XNYS calendar window. Covers every required case in
[spec req 13](../../../docs/specs/data-foundation.md).

**Regenerate:**

```bash
uv run python scripts/make_fixture_universe.py
```

Deterministic (fixed seed, no wall clock): regeneration is byte-identical
to the committed CSVs (`tests/test_fixture_universe.py`'s
`test_regeneration_is_content_equal_to_committed_csvs`). The dates below
were read back from the generated store, not hand-computed — see that
test and `test_control_group_satisfies_universe_rules_6_and_7_at_a_shared_t`
for how to reproduce them.

**Calendar facts used by every case below:** the holiday is 2019-07-04
(Independence Day, no XNYS session); the half day is 2018-11-23 (day after
Thanksgiving, close 18:00 UTC instead of 21:00 UTC; `ZETA_COM`'s bars cover
it). `gap.missing_tail_sessions=5`, `master.transfer_window_sessions=5`,
`universe.max_shares_age_days=400`, `universe.min_history_months=12`
(config defaults). Filing/fetch instants default to 18:30 UTC — always
before that session's close in both DST states, so
`last_completed_session(filed_at)` never lands on the filing's own session
by accident (see `scripts/make_fixture_universe.py:_dt`).

**Each security's bar window is bounded to what its own case needs**, not
the full 2018-2021 range (a first draft gave everyone `s[0:]`, ~2 MB of
CSV — the repo's `check-added-large-files` pre-commit hook caps it at
500 KB). Every listing's `valid_from` matches its first recorded bar, so no
row claims a company was listed over a period this fixture has no price
data for.

## Universe-rule control group

`PLAIN1`, `PLAIN2` and `CTRL1` are the only securities with continuous bars
covering every session in the 12 calendar months before, and a fresh
`shares_outstanding` fact as of, **T = 2019-03-29** — i.e. they are the
only names ADR 0006 rules 1–8 (`universe_as_of`) can actually admit. `CTRL1`
then delists (Form 25 filed 2019-04-26, after T), so it can also serve a
"was in the universe, later left" probe. No other security here is a rule
6/7 qualifier at any T — a first draft had *zero* such securities (every
case's bars were a tight window around its own event only), which would
have made `universe_as_of` empty at every T; see
`test_control_group_satisfies_universe_rules_6_and_7_at_a_shared_t`.

| security_id | cik | bars | notes |
|---|---|---|---|
| `PLAIN1` | 0001000001 | 2018-01-02..2019-04-25 (330) | shares facts known_at 2018-01-02, 2019-01-15 |
| `PLAIN2` | 0001000002 | 2018-01-02..2019-04-25 (330) | shares facts known_at 2018-01-02, 2019-01-15 |
| `CTRL1` | 0001000021 | 2018-01-02..2019-04-25 (330) | shares fact known_at 2018-10-17; Form 25 filed 2019-04-26 |

## Required spec req 13 cases

| security_id | cik | case(s) | key dates (T probes for later tasks) |
|---|---|---|---|
| `TRUNC1` | 0001000003 | delisting with **truncated** history: last bar 2018-08-06, Form 25 filed 2018-09-18 — 29 sessions before the last completed session as of the filing (> `gap.missing_tail_sessions`) | last bar 2018-08-06; filed_at 2018-09-18 |
| `NEARN1` | 0001000004 | delisting **within N**: last bar 2018-08-06, Form 25 filed 2018-08-10 — 3 sessions before the last completed session as of the filing (≤ N, not missing) | last bar 2018-08-06; filed_at 2018-08-10 |
| `MRGR1` | 0001000005 | **clean merger**: last bar 2018-08-06 is exactly the last completed session as of the Form 25 filed 2018-08-07 (gap = 0, not missing) | last bar 2018-08-06; filed_at 2018-08-07 |
| `NSE1` | 0001000006 | **Form 25-NSE** delisting | filed_at 2018-10-17 |
| `ZETA_COM`, `ZETA_PFD` | 0001000007 | Form 25 on the **preferred class** (`ZETA_PFD`, "6% Cumulative Preferred Stock", filed 2019-03-14); the common (`ZETA_COM`) has bars through 2019-05-09, well past the filing | `ZETA_PFD` filed_at 2019-03-14 |
| `XFER1` | 0001000008 | **exchange transfer**: Form 25 (NYSE) filed 2019-08-06; new listing (NASDAQ) `valid_from` 2019-08-09 (3 sessions later, within `master.transfer_window_sessions`) but `known_at` 2019-08-16 (well after the filing's own known_at) | probe T between 2019-08-06 and 2019-08-16 sees "delisted"; a probe after 2019-08-16 sees the transfer |
| `TIKR1` | 0001000009 | **same-company ticker change**: one `security_id`, listings "OLDT" (from 2019-11-13) → "NEWT" (from 2019-12-27) on the same exchange (NASDAQ) | second listing `valid_from` 2019-12-27 |
| `REUSE_OLD` / `REUSE_NEW` | 0001000010 / 0001000011 | **ticker reused** by a different company: both list ticker "DUPL", different `security_id`/`cik`, non-overlapping windows | `REUSE_OLD` delisted 2018-06-25 (last bar 2018-06-22, before); `REUSE_NEW` listed from 2020-05-21 |
| `KAPPA_A` / `KAPPA_B` | 0001000012 (shared) | **dual-class** company: two `security_id`s, one `cik`, both `security_type='common'`; per-class `shares_outstanding` facts via `class_member` ("ClassA"/"ClassB"), known_at 2019-02-06, inside both classes' bar coverage (2018-01-02..2019-03-13) | facts known_at 2019-02-06 |
| `SPLIT1` | 0001000013 | a **split** (2-for-1, ex-date 2019-03-14): raw close visibly halves at the ex-date session; `known_at` = close of the prior session | ex_date 2019-03-14 |
| `SPLIT2` | 0001000014 | split (1.5-for-1) **between a shares filing and a later month-end T**: fact known_at 2019-04-11 < ex_date 2019-05-24 < T = 2019-06-28; bars (2018-05-25..2019-07-22) cover every session in the 12 months before T | T = last session of June 2019 (2019-06-28) |
| `SPLIT3` | 0001000015 | split (4-for-1) **known before T with ex-date after T**: explicit announcement known_at 2020-03-11 < T = 2020-03-31 < ex_date 2020-05-21; a fresh shares fact (known_at 2019-12-27) and bars (2019-03-14..2020-07-02) covering every session in the 12 months before T are both present | T = last session of March 2020 (2020-03-31) |
| `DIVR1` | 0001000016 | **revised dividend**: two rows, ex_date 2018-12-31; first known_at = close before ex-date (2018-12-28), amount 0.20; second known_at = its own ingested_at (2019-02-11), amount 0.25 | ex_date 2018-12-31 |
| `REST1` | 0001000017 | **restated shares fact**: two `facts` rows, same `as_of_date` (2019-01-30), known_at 2019-02-06 (12,000,000) then 2019-03-28 (11,750,000) | as_of_date 2019-01-30 |
| `STALE1` | 0001000018 | **stale shares fact**: known_at 2018-02-14, no later fact; 562 days before month-end T = 2019-08-30 (> `universe.max_shares_age_days`); bars in two windows (2018-01-02..2018-02-28, then 2018-08-29..2019-09-17 — every session in the 12 months before T) with a documented gap between them | T = last session of August 2019 (2019-08-30) |
| `UNCL1` | 0001000019 | **unclassifiable** name: `classifications.rule = 'unclassifiable'`, no SIC | — |
| `STAT1` | 0001000020 | **`snapshot_static`-only pre-2019 listing**: `valid_from` 2017-03-01, `known_at` (fetch time) 2019-10-16 | valid_from 2017-03-01 |
| `SPY`, `MTUM` | 0000900001, 0000900002 | **benchmarks**, `benchmark=true`, seeded (`provenance='snapshot_static'`, `source='config'` on every row, not derived from EDGAR per spec req 3); bars plus quarterly dividends | — |

All other securities (`PLAIN1`, `PLAIN2`, `CTRL1`, and every security above
not otherwise noted) carry a plain `classifications` row
(`security_type='common'`, `rule='sic_default'`).

## Known gap this fixture works around

`tests/conftest.py`'s `load_universe_fixtures` loads every CSV via
`read_csv(..., all_varchar=true)` with no `nullstr` override. DuckDB maps
*any* empty cell — quoted or not — to `NULL` under that default, which
`facts.class_member NOT NULL DEFAULT ''` then rejects (confirmed by hand;
no `read_csv` option is exposed by that call to opt out). Every `facts` row
in this fixture therefore has a non-empty `class_member` ("ClassA"/"ClassB"
for the dual-class case, `"common"` elsewhere) rather than the schema's own
default `''`. Filed as issue #32 for a T4 follow-up, since `conftest.py` is
outside T5's scope.
