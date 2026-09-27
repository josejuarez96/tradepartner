# 0010. Phase 4 risk rules: the limits, when they are checked, and how an order id is derived

**Status:** Proposed  ·  **Date:** 2026-09-27  ·  **Issue:** #254 (spec open question 12, decided on #247)

## Context

ADR 0005 names "Risk rules (Phase 4 ADR)" as the record of what stands between a signal and an order, and the development process requires an ADR for any change to a risk rule. ADR 0007 decided that rejection is an allowlist and everything else halts, that a duplicate client order id is an idempotent replay whose derivation "the Phase 4 spec owns" (point 3), that a `ValueError` from building a request halts (point 6), and that the clock is checked "before the first `submit` of a run" (point 5). The Phase 4 spec ([paper-trading.md](../specs/paper-trading.md), reqs 3 to 5) now defines the wrapper in enough detail that three things need a decision record rather than a spec sentence: which limits are the risk rules, when "before the first submit" happens in a two-phase run, and what the id derivation is. The owner chose option (a) of the spec's question 12 on #247 (2026-09-27): a short ADR, with ADR 0009 accepted first (#251).

Two facts shape the rules. Paper executes each rebalance in two phases on one session: sells before the open, then buys sized from the cash the sells actually raised, never from buying power. And an order attempt is journaled before it is submitted, so the journal, not the broker, is the source of truth for what was tried.

## Options considered

1. **Record the rules in this ADR, checks per phase, id from the journal** (this ADR). Pro: one place names every limit and its config key; the per-phase reading matches how buys are sized; the id derivation is a pure function of the journal, so a crash cannot produce two live orders for one decision. Con: a rule change needs a new ADR, which is the point.
2. **Let the spec be the record.** Pro: no extra document. Con: the spec is a Draft that Phase 6 will amend; a risk rule buried in a 190-line requirement is easy to change without noticing, and ADR 0005 and the process both ask for an ADR.
3. **Check once per run, before the sells, as ADR 0007 point 5 reads literally.** Pro: simpler. Con: buys do not exist as requests until the sells have filled, so a once-per-run check would validate buy requests that are not yet sized, or skip validating them.

## Decision

We will record the Phase 4 risk rules here. Every limit is a named key under `risk.*` in config; the whole section is frozen into the paper window at `paper start` and read from the window afterwards (spec req 14). No literal lives in code.

**1. The limits.** A batch that breaks one of these halts with `LimitBreachError` naming the rule, before any submit:
- per-name target weight ≤ `risk.max_position_weight`;
- per-order notional ≤ `risk.max_order_notional_fraction` × equity;
- gross exposure after the batch ≤ `risk.max_gross_exposure` (no leverage; the charter's rule);
- every sell quantity ≤ the reconciled held quantity, rounded down to `alpaca.quantity_decimals` (long-only, no shorts, ever);
- buys sized after the modelled cost (`costs.*`) within `account().cash`, never `buying_power`;
- order count ≤ `risk.max_orders_per_run`.

Per-name conditions skip the name with a journaled reason instead of halting (not tradable, a whole-share quantity that rounds to zero, a notional below `risk.min_order_notional`, a listing that ended). Skips other than `dust` and `untradable` above `risk.max_skips_per_run` halt with `SkipCapError` and abandon the rebalance: many skips are a fault in disguise. Asynchronous rejections above `risk.max_rejections_per_run`, or every order of a run rejected, halt with `RejectionCapError`. A drawdown below the window's peak by more than `risk.max_drawdown` engages the kill switch for an owner review, released with a reason; it is not an exit rule. Cash left after execution above `risk.max_unspent_cash_fraction` of equity is alerted, not halted. Reconciliation tolerances are `risk.reconcile_quantity_tolerance` and `risk.reconcile_cash_tolerance`; a mismatch outside them is a `ReconciliationError`. A whole-share buy is checked against cash at close(S−1) plus `risk.whole_share_price_buffer`.

**2. When the checks run: before each phase's first submit, not once per run.** ADR 0007 point 5's "before the first `submit` of a run" is read per phase. Before the sells phase and again before the buys phase, in this order, the wrapper: reads the kill-switch state and stops if engaged; runs the clock pre-check (well-formed; not earlier than the last `ok` ingestion run's finish; not later than close(S) plus `risk.clock_max_sessions_late` sessions; wrapper and adapter hold one clock object; every later reading monotonic, or `ClockError`); builds and validates every request of the phase (a `ValueError` halts, ADR 0007 point 6); checks the phase's batch against the limits above; writes the phase's `orders` rows with their `pending` events and commits. Only then does it submit. The reason is that buys are sized from the cash the sells raised, so buy requests cannot be validated before the sells have filled; a once-per-run check would either validate unsized requests or skip them.

**3. The client order id is a pure function of the journal.** Every attempt's id is `f"{paper.order_id_prefix}-{S:%Y%m%d}-{security_id}-{side}-{attempt}"`, where S is the run's session and `attempt` is 1 plus the count of `orders` rows already journaled on S for that (security, side) across every decision. A test pins the derivation and its length against the broker's recorded limit. Consequences the spec relies on: two decisions on one name can never share an id; an attempt is journaled before it is submitted and a journaled attempt is never re-submitted, so a crash cannot produce two live orders for one decision; and a `DuplicateClientOrderIdError` can only mean the journal and the broker disagree, which is why ADR 0007 point 3 treats an identical duplicate as a replay and a differing one as a halt. Every later attempt for a decision is sized for the previous attempt's remainder.

**4. What a halt does** is ADR 0007's minimum plus spec req 4: engage the kill switch, journal the fault, best-effort cancel this run's acknowledged non-terminal orders with every outcome journaled, write the alert and the run's `halted` row, re-raise. `StaleDataError` halts the run without engaging the switch, because the next ingest cures it.

**5. Changing a rule.** A new limit, a changed default, a change to the check order or to the id derivation is a new ADR that supersedes the affected point here; the spec and the frozen window then follow it. The Phase 6 live ADR is expected to supersede points 1 and 4 for live money and must say what it keeps.

## Consequences

- Good: every rule that can stop an order has a name, a key and a place; the two-phase check matches how money actually moves; the id rule makes crash recovery a journal read rather than a guess.
- Bad / accepted risks: a batch that trips a limit abandons the whole rebalance for that session rather than trading the compliant part, by design; the defaults (`risk.max_position_weight = 0.05`, `risk.max_order_notional_fraction = 0.05`, `risk.max_gross_exposure = 1.0`, `risk.max_orders_per_run = 250`, `risk.max_rejections_per_run = 5`, `risk.max_skips_per_run = 10`, `risk.max_drawdown = 0.30`, `risk.min_order_notional = 1.0`, `risk.whole_share_price_buffer = 0.02`, `risk.max_unspent_cash_fraction = 0.05`, `risk.clock_max_sessions_late = 1`, `risk.max_broker_clock_skew_seconds = 60`, `risk.reconcile_quantity_tolerance = 1e-6`, `risk.reconcile_cash_tolerance = 0.01`) are the spec's reasoning for H1 at paper scale, and `risk.min_order_notional` rests on an unverified note until the recording task confirms Alpaca's minimum.
- Reversibility: cheap for a default (config, frozen per window, so a running window keeps its values); costly for the check order or the id derivation once a window has orders under them (the journal's replay logic depends on both), so those change only between windows.
- Revisit if: the recording task's precision, id-length or minimum-notional answers differ from the assumptions; Probe 3 changes the two-phase timing; the Phase 6 live ADR is written.
