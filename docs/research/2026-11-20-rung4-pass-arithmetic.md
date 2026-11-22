# Research Report: Can the route as written produce a survivor? The rung-4 pass arithmetic

**Brief:** #3669  ·  **Date:** 2026-11-20  ·  **Status:** COMPLETE as arithmetic on the written rules and on synthetic numbers. No store was opened, no return series was read, no backtest was run, nothing was registered  ·  **Agent/model:** team passmath; rules read from `docs/` and `src/` at the working tree of 2026-11-20; two small scripts (Appendix) that import one pure function from the repo and draw random numbers

## Answer
**Verdict:** NOT SUPPORTED that the route as written can produce a survivor from E1 or IRRX. PARTLY SUPPORTED for a different kind of idea: a low-turnover book passes rung 4 easily, but so does noise, and the forward exam then needs an edge far larger than any prior in the claims register  ·  **Confidence:** high on the cost arithmetic and on what the pass rule reduces to (both are read off the rules and the code); medium on the pass probabilities (they rest on volatility assumptions, each named below with its sensitivity); low on anything about the true size of an effect

1. **Rung 4 is a cost test, not an evidence test.** A new root family starts at N = 1. At N = 1 the code sets the deflated-Sharpe bar SR\* to zero, so `dsr_excess ≥ 0.5` only asks "is the net Sharpe above zero". Line 40 then reads: *net return after the harsh cost is above the yardstick*. A book whose true net edge is exactly zero passes about half the time. The spec says the same thing for IRRX: "the 50 bp cost does the filtering and a pass is not statistical evidence".
2. **So the cost cell decides, and it decides through turnover.** The harsh cell charges 2 × one-sided turnover × 50 bp. A book that replaces itself every month pays about 12 %/yr. A book that turns over half its names a year pays 0.5 %/yr. No plausible edge clears the first. Almost anything clears the second.
3. **IRRX: the chance of a pass is under 1 %, most likely about 1 in 1,000 or less.** Its published gross is 29 bp/month on the 50/50 book. Its cost drag at full monthly replacement is **50 bp/month (6.0 %/yr) at 25 bp per side** and **100 bp/month (12.0 %/yr) at 50 bp per side** (35 and 70 bp/month if turnover is 0.7). At the harsh cell the expected net is −72 bp/month. The pass chance is 0.0001 % on an 84-month window at turnover 1.0 and 0.3 % at turnover 0.7. It breaks even at 14.5 bp per side. The paper basis is 60.
4. **E1: the chance of a pass is about 0.2 % if the return is ever read, and the read is not expected to happen.** The prior is 20 to 35 bp gross per event. The rule needs a net mean of +30 bp after a 100 bp round trip, so 130 bp gross, and in practice about 147 bp because the t floor binds at the expected event count. On the design's own expected count (about 20,000 events), stage 4a (c) fails **at any bunching factor**: the best case is n_eff = 2,000 against the 2,500 needed. So E1 is `deferred: power` before any return is read.
5. **Which rule binds.** Monthly long/short at monthly turnover: the cost cell. Event study under E1's rule: the cost cell first, the event count second. Low-turnover long-only: nothing binds at rung 4; what binds is the forward exam (ADR 0016), which needs a paper-net information ratio of about 1.6 for an even chance. Line 40's deflated-Sharpe condition does not bind for any new root family. The window length binds nowhere: a longer window does not move any 50 % point, and it *lowers* the pass chance of an idea whose net edge is negative.
6. **No small rule change rescues E1 or IRRX.** Deciding rung 4 at the 25 bp base lifts IRRX to 7 to 32 % and E1 to 4 to 7 %. Both then meet the exam door at 60 bp, where IRRX nets −91 bp/month and E1 needs 150 bp gross. Only a measured cost near 12 bp per side or less makes either one a real question, and that is a cost-work result, not a rule edit.
7. **Loosening the cost cell is cheap in false passes only for high-turnover books.** For IRRX the null (no gross edge) passes 0.04 % at 25 bp and 11 % at 8.8 bp (84 months). For a low-turnover long-only book the null already passes 39 to 45 % at the harsh cell. The forward exam is the designed control (at most 4.9 % for four noise finalists). It does that job. The price is that it also fails nearly every real edge of ordinary size.
8. **The idea with the best odds** has all four of these: a hold of a quarter or longer per position (low turnover), a gross edge per bet of at least twice its round-trip cost (at 50 bp per side: 200 bp or more per event), several hundred effectively independent bets (n_eff ≥ about 250 for a 20-session hold, ≥ about 770 for a 60-session hold), and names that can be traded mid-session in a band with a measured cost near 6 to 12 bp. Nothing on today's list has all four. FC-1 comes closest on edge size and fails on event count.

**One line:** the route can produce a rung-4 pass only for low-turnover ideas, where a pass means little; it will not produce one for E1 or IRRX; and an exam survivor needs an edge three times the size of anything in the register. Stop building IRRX's engine and E1's read on the current cost cell, and put the next build hour into the cost measurement or a high-edge, low-turnover event idea.

### Options for the owner (each with its arithmetic)

| # | Option | What it does to the pass chance | What it costs in false passes (no true gross edge) | Our reading |
|---|---|---|---|---|
| 1 | **Stop IRRX's build now** (spec open question 41, option (c)) | Pass chance today 0.0001 % to 0.3 %. T414 to T419 is seven PRs for a read "expected to kill" | none | **Recommended.** The break-even is 14.5 bp per side at turnover 1.0 and 21 bp at 0.7. Revisit only if the re-measured paper basis for the over-$200M band is under about 15 bp |
| 2 | **Leave E1 deferred; do not start its window in 2019** | (c1) cannot pass on 20,000 events. On 30,000 events (window from mid-2016 after T184h) it passes only if bunching B ≤ 1.2. If read anyway: 0.1 to 0.9 % | none | **Recommended.** Run the counts once (they are cheap and already built), then stop |
| 3 | **Decide rung 4 at the 25 bp base; leave the harsh and paper costs to the exam door** | IRRX 7 % (84 mo, turnover 1.0) to 32 % (0.7). E1 4 to 7 %. Long-only at 4×/yr turnover: 30 % → 47 % for a 2 %/yr gross edge | IRRX 0.04 to 0.9 %. E1 0.4 to 2 %. Long-only 4×/yr: 16 % → 30 % | Moves the kill from rung 4 to the exam door. It saves nothing while the paper basis is 60 bp. Worth doing only together with option 4 |
| 4 | **Require a mid-session cost band, measured on paper fills, before any idea with turnover above about 2×/yr is built** | At 12 bp per side (twice the 6.1 bp quoted mid-session over $200M): IRRX 59 %; E1 18 %. At 6.1 bp: IRRX 85 %, E1 25 % | IRRX 5 % (12 bp) to 19 % (6.1 bp). E1 6.5 % to 10 % | **Recommended as the gate that matters.** The 6.1 bp is quoted, not filled, and reversal decays within a day (SH-4's half-life of 0.54 days), so IRRX's gross at a mid-session fill is unknown and likely lower |
| 5 | **Prefer low-turnover event ideas with gross ≥ 2 × round trip and n_eff ≥ N** | 80 % power at E1's rule and a 100 bp round trip needs: gross 200 bp → n_eff ≥ 255 (20-session hold) or 765 (60-session); gross 150 bp → 1,330 or 3,985; gross 300 bp → 65 or 195 | under 0.5 % at n_eff ≥ 250 | **Recommended as the search rule.** Raw events are 2 to 4 times n_eff (clustering and overlap). Write it into stage 4a (c) as a power line at the idea's own effect, not at the +30 bp floor |
| 6 | **Lengthen the window after T184h (40 → 84 months)** | The 80 % line falls: long-only (TE 8 %) from drag + 3.7 %/yr to drag + 2.5 %/yr; long/short from drag + 19 bp/month to drag + 13. No 50 % point moves. E1's n_eff goes from about 1,000 to about 1,500, still under 2,500 | Slightly lower (a noise long-only book: 43 % → 39 %) | Helps a little where the net edge is positive. Not worth waiting for on its own |
| 7 | **Say in the ADR what rung 4 is** | none | none | Line 40 at N = 1 is "net above the yardstick at the harsh cost". If the owner wants rung 4 to carry evidence, the cheapest honest bar is a t floor on the net mean (closed form, P = Φ(net IR × √years − 1): t ≥ 1.0 cuts the low-turnover null's 39 to 45 % to about 10 to 12 %, and cuts a real gross IR-0.5 edge from 77 to 85 % down to 39 to 52 %). If not, record that the exam is the only evidence test |

Top three: **1, 4 and 5.**

## Evidence

### What the rules say (quoted)

| Rule | Text | Where |
|---|---|---|
| Line 40, rung 4 | "a harsh backtest on the current store at the harsh settings (cost doubled, one-session delay, open fills), **killed if net excess CAGR over SPY < 0 or `dsr_excess` < 0.5 under those settings**" | `docs/decisions/0023-break-first-fast-books-paper-exam.md:40` |
| Harsh cost | "The decision cell is at the harsh cost: at least 50 bp per side, which is twice a base of at least 25 bp (+24.2 against the open, rounded up). That holds for trials and event studies alike." | same file, line 177 |
| Pass rule | "The pass rule is line 40's, read at the registry's N. For an event study it is the rule its experiment file freezes, at the harsh cost." | line 178 |
| N | "A new root family starts at N = 1, so the registry's deflated Sharpe sees almost no search." | line 190 |
| N + k | "N + k is printed, not a gate. This is a deliberate choice. As a gate, it would stop any 40-month read from reaching an exam" | line 198 |
| Exam door | "the paper basis still decides at the exam door" | line 185 |
| SR\* | "SR\* = sqrt(V) \* ((1 - gamma) \* Phi^-1(1 - 1/N) + gamma \* Phi^-1(1 - 1/(N\*e))). Zero for a single trial" | `src/tradepartner/backtest/metrics.py:373-389` |
| DSR with one pair | `if len(pair_sharpes) < 2: return DeflatedSharpe(basis, "psr", n_trials, None, 0.0, 0.0, psr_zero, psr_zero)` | `metrics.py:469-470` |
| V floor at promotion | `return max(rules.min_sharpe_variance_annual, MONTHS_PER_YEAR / periods)` (`family_v_floor`), with `min_sharpe_variance_annual = 0.04` | `src/tradepartner/store/lab_registry.py:630-650`, `src/tradepartner/config.py:455` |
| IRRX kill line (draft) | "KILL if the book's net `excess_cagr_rf` at the decision cell is below 0, or its `alpha_spy_annual` is at or below 0 …, or its `dsr` on the `raw` basis … is below 0.5, at the registry's N. At N = 1 (SR\* = 0) the DSR bar is about 'Sharpe above 0', so the 50 bp cost does the filtering and a pass is not statistical evidence" | `docs/specs/backtest.md:551` |
| IRRX turnover and screen | "take one-sided turnover as 1.0 of gross per month (an assumption, not measured…)"; "The break-even cost is about 14.5 bp per side at turnover 1.0 (about 21 bp at 0.7)" | `docs/specs/backtest.md:500` |
| IRRX window | "`in_sample_start` is the later of 2017-01-31 … and the first month-end from which T329's delister-coverage report shows coverage"; "fewer than 36 month-ends makes the idea `deferred: power`" | `docs/specs/backtest.md:544` |
| E1 line | "The mean net spread is at least **+30 bp** per event **and** its overlap-calibrated t is at least **1.0**, on **each** of the four readings"; net of "**50 bp per side** (100 bp a round trip) on the treated leg" | `docs/research/2026-11-16-e1-earnings-reaction-design.md:112-114` |
| E1 power | "`n_eff = n_thesis / (2 × B)` … The need is `n_eff ≥ (1,500 / 30)² = 2,500` (sd of a 60-session matched spread assumed at 1,500 bp, UNGRADED; floor +30 bp). … `n_thesis ≈ 4,000` and `n_eff ≈ 1,000` at `B ≈ 2`. **E1 is expected to be `deferred: power`**" | same file, line 133 |
| Stage 4a (c) | "n = (sd / effect)^2. n is effective, not raw" | `docs/ways-of-working/research-program.md:46` |
| Event kill lines | "a line fires if any listed reading fails it, and a statistic that cannot be computed … counts as a kill" | `docs/specs/research-registry.md:90` |
| Forward exam | "t ≥ **4.76 / 3.37 / 2.75** at 12 / 24 / 36 months"; "Four noise finalists at skew −0.85 give 1 − (1 − 0.0125)⁴ = **4.9%**" | `docs/decisions/0016-development-boundary-and-forward-exams.md:197, 206` |
| Fresh data | "its pass rate is the test's rate under the null, whatever the screen did" | ADR 0016, line 120 |
| Exam power, the repo's own run | "No exam design that keeps four ideas under 5% can confirm an IR 0.5 edge within 3 years." | ADR 0016, line 192 |
| Audit B | "The in-sample screen cannot discriminate in either direction at these costs and lengths." | ADR 0016, line 82 |

### What line 40 reduces to (derived from the rules)

- DSR ≥ 0.5 means Φ(z) ≥ 0.5, so z ≥ 0, so the sample Sharpe is at least SR\* (the denominator of z is positive). At N = 1, SR\* = 0.
- So for a new root family the rule is: **net excess CAGR ≥ 0 and net excess Sharpe ≥ 0**. Both say "the net mean is above the yardstick". The CAGR leg is a little stricter (by about TE²/2 a year, the volatility drag of the active return).
- With monthly net mean μ and monthly standard deviation σ over T months:

  **P(pass) = Φ((μ_net − b) × √T / σ)**, with **μ_net = gross − 2 × turnover × cost per side** and **b = SR\*_annual × σ / √12** (zero at N = 1).

- Gross needed for a pass chance p: **gross = cost drag + b + z_p × σ / √T**, z = 0 at 50 %, 0.84 at 80 %.
- "Effective independent bets" for a trial are its months. In information-ratio terms: 50 % needs a net IR of 0 (N = 1); 80 % needs net IR ≥ 0.84 / √years, which is **0.46 at 40 months and 0.32 at 84 months**.

SR\* at other N, for reference (annual Sharpe; our computation with the repo's function; the 60 and 100 columns reproduce the ADR's table, 1.28 / 1.39 / 0.89):

| Window | N = 1 | N = 3, V = 12/T | N = 15, V = 0.04 | N + k = 60, V = 12/T |
|---|---:|---:|---:|---:|
| 40 months | 0 | 0.47 | 0.35 | 1.28 |
| 84 months | 0 | 0.32 | 0.35 | 0.89 |

### Volatility assumptions (every probability below depends on these)

| Shape | Value used | Where it comes from | Sensitivity shown |
|---|---|---|---|
| Monthly long/short 50/50 book | σ = 1.40 %/month on equity | **Derived from SH-4:** spread sd = 0.58 × √252 / 3.29 = 2.80 %/month (252 months, 2001-2021, value-weighted); the book carries half the spread | 1.0 and 2.5 %/month. Our equal-weight, 1,000-name book may differ from the paper's value-weighted one |
| Long-only book against SPY | tracking error 8 %/yr | Audit B's noise model (ADR 0016, line 121) and B3's measured 8.6 %/yr (`2026-10-10-b11-beta-alpha-split.md`) | 15 %/yr (H1's measured 15.4 %/yr) |
| Event, 60-session matched spread | sd = 1,500 bp per event | The E1 design's own figure, UNGRADED | 1,000 and 2,000 bp |
| Event, 20-session matched spread | sd = 866 bp | **Assumption:** 1,500 × √(20/60) | none beyond the 60-session range |
| SPY, for the simulations only | 0.9 %/month mean, 4.5 %/month sd | **Assumption** (typical values). It cancels in the excess and matters only through the CAGR leg | not varied |
| Returns | independent months, normal | **Assumption.** One check with Student-t (5 df), Audit B's noise: 42.7 % against 42.7 % | fat tails change nothing visible at rung 4 |

### Shape 1: monthly long/short trial (the IRRX working kill line, N = 1)

Units are bp per month on the book's equity. Drag = 2 × turnover × cost + 1.25 (borrow: 30 bp/yr on the 0.5 short leg).

| One-sided turnover per month | Cost per side | Drag | Gross needed, 40 months (50 % / 80 %) | Gross needed, 84 months (50 % / 80 %) |
|---|---:|---:|---:|---:|
| 0.25 | 50 | 26 | 26 / 45 | 26 / 39 |
| 0.50 | 50 | 51 | 51 / 70 | 51 / 64 |
| 0.70 | 50 | 71 | 71 / 90 | 71 / 84 |
| 1.00 | 50 | 101 | **101 / 120** | **101 / 114** |
| 1.00 | 25 | 51 | 51 / 70 | 51 / 64 |
| 1.00 | 8.8 | 19 | 19 / 38 | 19 / 32 |

- Double these for the long/short **spread** (the published number): at turnover 1.0 and 50 bp the spread must be about 2.0 %/month, against a published 0.58.
- Line 40 **as written** (excess over SPY) adds SPY's premium over the T-bill to every row, which a net-zero book cannot earn. Spec open question 40 says so and proposes the T-bill yardstick used here.

### Shape 2: monthly long-only trial against SPY (line 40, N = 1)

Gross excess return needed, %/yr. Drag = 2 × annual one-sided turnover × cost. The CAGR leg adds TE²/2 (0.3 %/yr at TE 8 %, 1.1 %/yr at 15 %).

| One-sided turnover per year | Drag at 50 bp | TE 8 %: 40 months (50 % / 80 %) | TE 8 %: 84 months | TE 15 %: 40 months | TE 15 %: 84 months |
|---|---:|---:|---:|---:|---:|
| 0.25× | 0.25 | 0.6 / 4.3 | 0.6 / 3.1 | 1.4 / 8.3 | 1.4 / 6.1 |
| 0.5× | 0.5 | 0.8 / 4.5 | 0.8 / 3.4 | 1.6 / 8.5 | 1.6 / 6.4 |
| 1.2× (an index-like momentum book) | 1.2 | 1.5 / 5.2 | 1.5 / 4.1 | 2.3 / 9.2 | 2.3 / 7.1 |
| 4× | 4.0 | 4.3 / 8.0 | 4.3 / 6.9 | 5.1 / 12.0 | 5.1 / 9.9 |
| 12× (full monthly replacement) | 12.0 | 12.3 / 16.0 | 12.3 / 14.9 | 13.1 / 20.0 | 13.1 / 17.9 |

Pass chance by true gross edge (simulation, 200,000 draws per cell, TE 8 %, cost 50 bp):

| Turnover | True gross 0 (noise) | 2 %/yr (IR 0.25) | 4 %/yr (IR 0.5) |
|---|---:|---:|---:|
| 0.5×, 40 months | **43 %** | 61 % | 77 % |
| 0.5×, 84 months | **39 %** | 65 % | 85 % |
| 1.2×, 40 months | 37 % | 55 % | 72 % |
| 4×, 40 months | 16 % | 30 % | 47 % |
| 12×, 40 months (closed form) | 0.25 % | | 2.9 % |

- A noise book passes 39 to 45 % of the time at low turnover. That matches Audit B's direction ("19.0%" per idea through promotion, rung 4 and rung 5 at H1-like costs).
- In an existing family the bar is not zero. At N = 15 and V = 0.04 (SR\* 0.35) the same 0.5× book needs 3.7 %/yr gross for 50 % and 7.3 for 80 % (40 months); noise passes 23 % and an IR-0.5 edge 56 %.
- If N + k were a gate (it is not), the 40-month bar would be 11.1 %/yr gross for 50 %. That is the ADR's "would stop any 40-month read".

### Shape 3: event study under E1's rule (net ≥ +30 bp and t ≥ 1.0; 100 bp round trip)

- SE = sd / √n_eff. The pass needs the sample net mean ≥ max(30, SE). So **gross needed = 100 + max(30, SE) + z × SE**.
- The t floor binds over the +30 bp floor whenever n_eff < (sd/30)² = 2,500 (at sd 1,500).
- A true net effect exactly at the floor passes at most 50 % of the time, whatever the count.

| Hold, sd | n_eff | SE, bp | Gross needed, 50 % / 80 % | Null pass (true gross 0) | Pass if true net is 0 |
|---|---:|---:|---:|---:|---:|
| 60 sessions, 1,500 | 250 | 95 | 195 / 275 | 2.0 % | 16 % |
| 60, 1,500 | 500 | 67 | 167 / 224 | 0.6 % | 16 % |
| 60, 1,500 | 1,000 (E1, 2019 window) | 47 | **147 / 187** | 0.09 % | 16 % |
| 60, 1,500 | 1,500 (E1, 2016 window) | 39 | 139 / 171 | 0.02 % | 16 % |
| 60, 1,500 | 2,500 | 30 | 130 / 155 | 0.001 % | 16 % |
| 60, 1,500 | 10,000 | 15 | 130 / 143 | 0 | 2 % |
| 20 sessions, 866 | 250 | 55 | 155 / 201 | 0.2 % | 16 % |
| 20, 866 | 1,000 | 27 | 130 / 153 | 0 | 14 % |
| 60, 1,000 | 1,000 | 32 | 132 / 158 | 0.002 % | 16 % |
| 60, 2,000 | 1,000 | 63 | 163 / 217 | 0.5 % | 16 % |

Smallest n_eff for a given true gross effect (E1's rule, 100 bp round trip):

| True gross per event | 20-session hold: 50 % / 80 % | 60-session hold: 50 % / 80 % |
|---|---:|---:|
| 150 bp | 300 / 1,330 | 900 / 3,985 |
| 200 bp | 75 / 255 | 225 / 765 |
| 300 bp | 20 / 65 | 60 / 195 |

### E1 on the map

- **Prior (SH-6, the design's item (d)):** 20 to 35 bp gross per 60-session long event. Needed: 130 bp at the floor, about 147 bp at n_eff 1,000. The prior is a fifth of the bar.
- **Stage 4a (c1), n_eff = 0.2 × N / (2 × B):**

  | Eligible events N | B = 1.0 | B = 1.2 | B = 1.5 | B = 2.0 (expected) |
  |---|---:|---:|---:|---:|
  | 20,000 (2019 window, the pinned design) | 2,000 | 1,667 | 1,333 | **1,000** |
  | 30,000 (mid-2016 window, after T184h) | 3,000 | 2,500 | 2,000 | 1,500 |

  The gate is 2,500. The pinned window cannot reach it. The longer window reaches it only at B ≤ 1.2, against an expected 2. (The brief's "about 6,000 events per thesis against about 10,000 needed" is the #2892 draft's raw count at a /4 correction: 1,500 against 2,500 effective. Same conclusion.)
- **P(pass | the return is read):**

  | True gross per event | n_eff 1,000: one reading | n_eff 1,000: all five cells | n_eff 2,500: all five cells |
  |---|---:|---:|---:|
  | 20 bp | 0.36 % | 0.11 % | 0.003 % |
  | 27.5 bp (mid prior) | 0.57 % | **0.19 %** | 0.009 % |
  | 35 bp | 0.89 % | 0.30 % | 0.02 % |
  | 55 bp (twice the prior) | 2.6 % | 1.0 % | 0.2 % |
  | 110 bp (four times) | 22 % | 12 % | 15 % |
  | 130 bp (exactly the bar) | 36 % | 23 % | 36 % |

  "All five cells" is the kill line's conjunction: four readings plus the 2020+ cell. It assumes the readings correlate at 0.9 (**an assumption**; a higher correlation moves the figure toward the one-reading column).
- **A generous prior** (70 % at the published size, 25 % at twice, 5 % at four times; **our weights, not graded**) gives about 1.0 % at n_eff 1,000 and 0.8 % at 2,500.
- **Before any read:** P(pass) = P(4a (c) passes) × P(pass | read). The first factor is close to zero on the design's own expectation. **Headline: about 0.2 % if read, under 1 % on a generous prior, about zero as written.**
- **After a pass** the idea still owes one counted trial under line 40 against SPY (ADR amendment item 3), at N = 3 for the family (2 configurations + 1). The stored `dsr_excess` is then PSR(0), but promotion's high-water mark uses the floored V: SR\* about 0.39 to 0.47.

### IRRX on the map

**Cost drag, explicitly** (drag = 2 × one-sided turnover × cost per side; turnover in units of the book's gross per month, the spec's item 2):

| Cost per side | Turnover 1.0: bp/month | %/yr (× 12) | Turnover 0.7: bp/month | %/yr |
|---|---:|---:|---:|---:|
| 8.8 bp (arrival, over $200M) | 17.6 | 2.1 | 12.3 | 1.5 |
| **25 bp (base)** | **50** | **6.0** | 35 | 4.2 |
| **50 bp (harsh, decides)** | **100** | **12.0** | 70 | 8.4 |
| 60 bp (paper basis) | 120 | 14.4 | 84 | 10.1 |

- Gross: 0.58 %/month of spread is 29 bp/month on the book, 3.5 %/yr. Borrow adds 1.25 bp/month.
- Net at the harsh cell: 29 − 100 − 1.25 = **−72 bp/month** (−42 at turnover 0.7). At 25 bp: −22 (−7).
- Break-even cost per side: 29 / (2 × 1.0) = **14.5 bp** (20.7 at 0.7).
- The best published net figure (0.19 %/month of spread, t 1.41) is 9.5 bp/month on the book, at a cost model that is not ours.

**P(pass)** (closed form; the three-part kill line simulated beside it where shown):

| True gross on the book | Turnover | Cost | 84 months (the spec's window) | 40 months |
|---|---|---:|---:|---:|
| 29 bp (published) | 1.0 | 50 | **0.0001 %** (sim 0 in 200,000) | 0.05 % |
| 29 bp | 0.7 | 50 | **0.3 %** (sim 0.2 %) | 2.8 % (sim 2.3 %) |
| 29 bp | 1.0 | 25 | 7 % (sim 6 %) | 16 % |
| 29 bp | 0.7 | 25 | 32 % (sim 28 %) | 37 % |
| 29 bp | 1.0 | 8.8 | 75 % (sim 71 %) | 68 % |
| 12 bp (QI-2's 58 % post-publication decline; SH-4's sample ends 2021) | 1.0 | 50 | 0 | 0.003 % |
| 12 bp | 0.7 | 50 | 0.005 % | 0.4 % |
| 58 bp (twice published) | 1.0 | 50 | 0.2 % | 2.5 % |
| 58 bp | 0.7 | 50 | 19 % | 28 % |
| 0 (null) | 1.0 | 25 / 8.8 | 0.04 % / 11 % | 1.0 % / 20 % |

- Sensitivity to σ at 29 bp, turnover 1.0, 50 bp, 84 months: 0 at 1.0 %/month, 0.0001 % at 1.4, 0.4 % at 2.5.
- A longer window **lowers** the chance (0.05 % → 0.0001 %), because more months make a negative mean more certain.
- **Headline: under 1 %; 0.0001 % to 0.3 % on the spec's own window and turnover range.** Only a 10 % weight on "the true effect is double and turnover is 0.7" lifts it near 2 %.
- G2-S10 (median anomaly 7 bp/month after 2005, non-microcap) and SH-2 (industry-relative reversal nets −0.80 %/month, 1963-2013) both sit on the low side of the 29 bp prior.

### What binds, and the smallest change

| Shape | Binding rule | Smallest change that makes a plausible effect detectable | False-pass cost of that change (no gross edge) |
|---|---|---|---|
| Monthly long/short at monthly turnover (IRRX) | **Cost cell.** Drag 100 bp/month against a 29 bp prior | A cost per side at or under about 12 bp, measured (not a rule edit). At 12.2 bp: 59 % | 0.0000 % → 4.6 % (84 months); at 8.8 bp, 11 % |
| Monthly long-only, low turnover | **Nothing at rung 4.** Downstream: the exam bound (t ≥ 2.75 at 36 months is a paper-net IR of 1.59; 2.38 at 24; 4.76 at 12) | None at rung 4. At the exam, any loosening spends the 4.9 % budget, which is already spent | Already 39 to 45 % at rung 4; at most 1.25 % per exam |
| Monthly long-only, turnover ≥ 4×/yr | **Cost cell** (4 to 12 %/yr) | Base cost in the cell (option 3): halves the drag | 16 % → 30 % at 4×/yr |
| Event study, E1's rule | **Cost cell first** (130 bp gross against 20 to 35), **event count second** (n_eff 1,000 against 2,500; the t floor binds, not the +30 bp floor) | An idea with gross ≥ 200 bp per event, or a cost near 12 bp per side. Dropping the +30 bp floor alone changes nothing at n_eff 1,000 (the t floor already binds) | At 25 bp per side: 0.09 % → 2.0 %. At 12.2 bp: 6.5 % |
| Any shape | Line 40's DSR leg | Not binding at N = 1. It binds in an old family (N = 15: SR\* 0.35) and would bind hard as an N + k gate (SR\* 1.28) | n/a |
| Any shape | Window length | 40 → 84 months tightens the 80 % line by a quarter. It moves no 50 % point | slightly lower |

### The exam, as a cross-check (leg (i) only, normal months, our simulation; an upper bound)

| Paper-net IR against the benchmark | 0 | 0.5 | 0.75 | 1.0 | 1.5 | 2.0 |
|---|---:|---:|---:|---:|---:|---:|
| P(pass by month 36) | 0.5 % | 3.8 % | 8.4 % | 17 % | 43 % | 73 % |

- The repo's own strictness run reports less for an IR measured before the 60 bp paper drag: 0.84 % at IR 0.5 and 2.43 % at 0.75 (ADR 0016's table). Both runs say the same thing: the exam needs an IR near 1.6 for an even chance.
- End to end, a real low-turnover edge of gross IR 0.5: rung 4 about 77 to 85 %, exam about 1 to 4 %, so **about 1 to 3 % per idea**. A noise book: 39 to 45 % × at most 1.25 %, so **at most 0.6 %**. The route separates the two by a factor of about 3 to 5, at a very low level.
- For an event book, net IR ≈ (gross − round trip) / sd × √(n_eff per year). At 200 bp gross, 1,500 bp sd and 300 effective events a year: 1.15 at a 100 bp round trip, 0.92 at the paper basis (120 bp), 2.0 at a 24 bp round trip. This is against matched peers; against SPY the book also carries market-relative noise, so these are upper bounds.

## Caveats & gaps
- **Every probability is conditional on a volatility assumption.** The table above names each. The cost arithmetic and the "what the rule reduces to" findings do not depend on them.
- **Months are treated as independent and normal.** Real reversal and event returns cluster. That widens the true SE and pushes every pass chance toward the "pass if true net is 0" figure, not toward a higher one for E1 or IRRX.
- **IRRX's turnover (1.0) is the spec's assumption, not a measurement.** The read's own break-even needs turnover under about 0.29 a month at 50 bp, which a one-month reversal cannot have.
- **The five-cell E1 figure assumes a 0.9 correlation between readings.** The one-reading column is the upper bound.
- **The priors are published averages over other universes and execution times.** The design files list why ours should be lower (next-open fills, the $200M floor, a size-screened universe). We applied no extra haircut except the QI-2 row for IRRX.
- **The exam table is a simplified check** (one leg, normal months, no skew, no minimum-fill void). It is not a recalibration of ADR 0016.
- **Rung 5 and the exam door are not modelled** beyond the cost arithmetic. Both can only lower a pass chance.

## Rules we found ambiguous (not resolved here)
1. **The yardstick for a net-zero book.** Line 40 says "net excess CAGR over SPY". The IRRX draft replaces it with the T-bill, `alpha_spy_annual` and the `raw` DSR basis. That is spec open question 40, not yet the owner's answer. As written, line 40 kills any market-neutral book.
2. **Is the one-session delay still in the harsh cell?** Line 40 lists "cost doubled, one-session delay, open fills". The 2026-11-16 amendment's cell names only the cost. The IRRX draft strikes the delay; for a monthly book the engine's delay is a whole period (#1820). No rule says what a monthly long-only trial does.
3. **"`dsr_excess` under those settings."** The code computes the DSR from the **base-level** metrics only. It is at the harsh cost only if 50 bp is the registered base (IRRX does this). A file registered at another base with 50 as a sensitivity level would store a `dsr_excess` that is not the harsh one.
4. **"At least 50 bp … twice a base of at least 25 bp."** A floor, or twice whatever the band's base is re-measured at? And for a band with no measure (under $200M)?
5. **"Read at the registry's N" has three different V's.** The stored trial row uses the raw V and falls back to PSR(0) with fewer than two pairs. Promotion's high-water mark uses the floored V (#3127). The printed N + k figure uses max(V, 0.04, 12/T). So one trial has an SR\* of 0, about 0.4 and about 1.3 depending on where it is read.
6. **From an event study to the counted trial** (amendment item 3). The trial is judged against SPY under line 40, not against matched peers, and nothing says how the event book is weighted or what its idle cash earns. An event-study pass does not predict that trial's result.

## Proposed claims (not added to `claims.toml`)

| Proposed id | Statement | Kind | Grade | Scope |
|---|---|---|---|---|
| PM-1 | For a new root family (N = 1) ADR 0023's line-40 pass rule reduces to "net excess return at the harsh cost is above the yardstick"; a book with zero true net edge passes about half the time, and a zero-gross-edge long-only book at 0.5×/yr turnover passes 39 to 45 %. | fact (derived from `metrics.py` and the ADR) plus simulation | SUPPORTED | Rules at 2026-11-20; TE 8 to 15 %/yr; 40 and 84 months; synthetic normal returns |
| PM-2 | IRRX's expected cost drag at one-sided turnover 1.0 a month is 50 bp/month (6.0 %/yr) at 25 bp per side and 100 bp/month (12.0 %/yr) at 50 bp per side, against a published gross of 29 bp/month on the 50/50 book; its chance of passing the harsh cell is 0.0001 % to 0.3 % on an 84-month window. | arithmetic | SUPPORTED (arithmetic); UNGRADED (probability) | Turnover 0.7 to 1.0 (assumed, the spec's); σ 1.40 %/month derived from SH-4; no return read |
| PM-3 | E1 cannot pass stage 4a (c1) on its pinned 2019 window at any bunching factor (n_eff ≤ 2,000 against 2,500), and if read at n_eff 1,000 its pass chance at the SH-6 prior is about 0.2 % (0.1 to 0.9 %). | arithmetic | SUPPORTED (4a arithmetic, on the design's expected 20,000 events); UNGRADED (probability) | sd 1,500 bp (the design's UNGRADED figure); reading correlation 0.9 assumed |
| PM-4 | Under E1's rule shape and a 100 bp round trip, 80 % power needs a true gross of at least 200 bp per event with n_eff ≥ 255 (20-session hold) or ≥ 765 (60-session hold); at 150 bp it needs 1,330 or 3,985. | arithmetic | UNGRADED | sd 866 / 1,500 bp assumed |
| PM-5 | The forward return exam passes a paper-net IR of 0.5 at most about 4 % of the time and needs an IR of about 1.6 for an even chance. | simulation | UNGRADED | Leg (i) only, normal months; consistent with ADR 0016's quoted strictness run |

## Sources
- `docs/decisions/0023-break-first-fast-books-paper-exam.md` (line 40; amendments 2026-10-30, 2026-11-06, 2026-11-07, 2026-11-16)
- `docs/decisions/0016-development-boundary-and-forward-exams.md` (amendments 2026-11-06 and 2026-11-07)
- `docs/ways-of-working/research-program.md` (stage 4a)
- `docs/specs/backtest.md` (#3504 amendment, items 2, 7 to 13; open questions 40, 41; #1820 amendment)
- `docs/specs/research-registry.md` (kill lines, effective n)
- `docs/research/2026-11-16-e1-earnings-reaction-design.md`, `2026-11-02-earnings-event-family-design.md`, `2026-11-02-cost-by-liquidity-band.md`, `2026-10-30-paper-fill-cost-arrival.md`, `2026-10-18-b9-kill-b3-retirement.md`, `2026-10-10-b11-beta-alpha-split.md`
- `docs/research/claims.toml`: QI-2, G2-S10, SH-1, SH-2, SH-4, SH-6, IL-1, IL-2, TP-10, TP-11
- `src/tradepartner/backtest/metrics.py` (`expected_max_sharpe`, `probabilistic_sharpe`, `deflated_sharpe`), `src/tradepartner/backtest/results.py` (`result_statistics`), `src/tradepartner/store/lab_registry.py` (`family_v_floor`, `floored_variance`), `src/tradepartner/config.py` (`promotion_min_dsr_excess = 0.5`, `min_sharpe_variance_annual = 0.04`)

## Appendix: the scripts' key code

Two scratch scripts, not committed. Seed 3669, 200,000 draws per simulated cell. To re-run: save the block as `passmath.py` in any directory outside the repo and run `uv run python <path>/passmath.py` from a checkout. It opens no store and reads no file.

```python
import math
from statistics import NormalDist
import numpy as np
from tradepartner.backtest.metrics import expected_max_sharpe  # pure; reads nothing

N01 = NormalDist(); Phi = N01.cdf
SEED, REPS = 3669, 200_000

def p_closed(mu_net_m, sigma_m, T, sr_star_annual=0.0):
    """P(pass) = Phi((mu_net - bar) * sqrt(T) / sigma); bar = monthly mean that gives SR = SR*."""
    bar = sr_star_annual / math.sqrt(12) * sigma_m
    return Phi((mu_net_m - bar) * math.sqrt(T) / sigma_m)

def need_gross(drag_m, sigma_m, T, power, sr_star_annual=0.0, extra=0.0):
    """Gross needed = drag + extra (TE^2/2 for the CAGR leg) + SR* bar + z_power * sigma / sqrt(T)."""
    return (drag_m + extra + sr_star_annual / math.sqrt(12) * sigma_m
            + N01.inv_cdf(power) * sigma_m / math.sqrt(T))

def sim_long_only(gross_excess_y, turnover_y, cost_bps, te_y, T, sr_star_annual=0.0, beta=1.0):
    """Line 40: net excess CAGR over SPY >= 0 and dsr_excess >= 0.5 (<=> excess Sharpe >= SR*)."""
    rng = np.random.default_rng(SEED)
    spy = rng.normal(0.009, 0.045, (REPS, T))                 # ASSUMPTION
    drag_m = 2 * turnover_y * cost_bps / 1e4 / 12
    active = gross_excess_y / 12 - drag_m + te_y / math.sqrt(12) * rng.standard_normal((REPS, T))
    strat = beta * spy + active; ex = strat - spy
    cagr_ok = np.log1p(strat).sum(1) >= np.log1p(spy).sum(1)
    dsr_ok = ex.mean(1) / ex.std(1, ddof=1) >= sr_star_annual / math.sqrt(12)
    return float((cagr_ok & dsr_ok).mean())

def sim_long_short(gross_book_bp_m, turnover_m, cost_bps, sigma_book_m, T, borrow_bp_y=30.0):
    """IRRX draft line: excess_cagr_rf >= 0, alpha_spy_annual > 0, raw DSR >= 0.5 (N = 1)."""
    rng = np.random.default_rng(SEED)
    rf = 0.002                                                # ASSUMPTION; cancels
    spy_ex = rng.normal(0.007, 0.045, (REPS, T))
    drag = 2 * turnover_m * cost_bps + 0.5 * borrow_bp_y / 12  # bp/month on equity
    ex = (gross_book_bp_m - drag) / 1e4 + sigma_book_m * rng.standard_normal((REPS, T))
    cagr_ok = np.log1p(rf + ex).sum(1) >= T * math.log1p(rf)
    xm = spy_ex - spy_ex.mean(1, keepdims=True)
    b = (xm * ex).sum(1) / (xm ** 2).sum(1)
    return float((cagr_ok & (ex.mean(1) - b * spy_ex.mean(1) > 0) & (ex.mean(1) >= 0)).mean())

def p_event(gross_bp, n_eff, sd_bp, rt_cost_bp=100.0, floor=30.0, t_floor=1.0):
    """E1 rule, one reading: sample net mean >= max(floor, t_floor * SE)."""
    se = sd_bp / math.sqrt(n_eff)
    return Phi((gross_bp - rt_cost_bp - max(floor, t_floor * se)) / se)

def need_event_gross(n_eff, sd_bp, power, rt_cost_bp=100.0, floor=30.0, t_floor=1.0):
    se = sd_bp / math.sqrt(n_eff)
    return rt_cost_bp + max(floor, t_floor * se) + N01.inv_cdf(power) * se

def sim_event_e1(gross_bp, n_eff, sd_bp, rho=0.9, sub_share=0.79, rt=100.0, floor=30.0):
    """Four readings correlated at rho (ASSUMPTION) plus the nested 2020+ raw cell."""
    rng = np.random.default_rng(SEED)
    se, mu = sd_bp / math.sqrt(n_eff), gross_bp - rt
    common = rng.standard_normal(REPS); ok = np.ones(REPS, bool)
    f = lambda s, e: mu + s * (math.sqrt(rho) * common + math.sqrt(1 - rho) * e)
    for _ in range(3):
        ok &= f(se, rng.standard_normal(REPS)) >= max(floor, se)
    se_sub = se / math.sqrt(sub_share)
    m_sub = f(se_sub, rng.standard_normal(REPS))
    m_rest = f(se / math.sqrt(1 - sub_share), rng.standard_normal(REPS))
    m_raw = sub_share * m_sub + (1 - sub_share) * m_rest
    return float((ok & (m_raw >= max(floor, se)) & (m_sub >= max(floor, se_sub))).mean())

def sim_exam(ir_net_annual, bounds=((12, 4.762), (24, 3.367), (36, 2.750))):
    """ADR 0016's group-sequential exam, leg (i) only, with the futility stops."""
    rng = np.random.default_rng(SEED)
    x = ir_net_annual / math.sqrt(12) + rng.standard_normal((REPS, 36))
    alive, passed = x[:, :6].sum(1) >= 0, np.zeros(REPS, bool)
    for L, bnd in bounds:
        t = x[:, :L].mean(1) / x[:, :L].std(1, ddof=1) * math.sqrt(L)
        passed |= alive & ~passed & (t >= bnd)
        alive &= (x[:, :L].sum(1) >= 0) | passed
    return float(passed.mean())

SIG = 0.58 * math.sqrt(252) / 3.29 / 2 * 100      # IRRX book sd, bp/month, from SH-4 (= 140)
# Examples that reproduce the headline cells:
#   p_closed(29 - (2*1.0*50 + 1.25), SIG, 84)      -> 1.1e-06   IRRX, harsh, turnover 1.0
#   p_closed(29 - (2*0.7*50 + 1.25), SIG, 84)      -> 0.0028    IRRX, harsh, turnover 0.7
#   p_event(27.5, 1000, 1500)                      -> 0.0057    E1, one reading
#   sim_event_e1(27.5, 1000, 1500)                 -> 0.0019    E1, five cells
#   sim_long_only(0.0, 0.5, 50, 0.08, 40)          -> 0.427     noise, low-turnover long-only
#   expected_max_sharpe(60, 12/40)                 -> 1.285     the ADR's N + k bar
#   sim_exam(0.5)                                  -> 0.038
```

Scratch copies and full outputs (not durable): `/tmp/claude-0/-home-user-tradepartner/71ea5d85-853d-543d-88fe-25522f9365dd/scratchpad/passmath/passmath.py`, `options.py`, `out.txt`, `options_out.txt`.
