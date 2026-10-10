import type { ReactNode } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

/**
 * The glossary of interaction.md section 9: the term the UI uses, one line
 * that teaches it, and where the engine keeps it. Terms are never renamed;
 * they are explained here, at the point of a decision.
 */
export const GLOSSARY: Record<string, { term: string; line: string; source: string }> = {
  registration: { term: "Pre-registration", line: "The parameters are frozen before any run; params_sha256 identifies them. A change is a new variant, registered and counted.", source: "hypothesis register, sweep register; hypotheses.registered_at, params_sha256" },
  params_sha256: { term: "params_sha256", line: "The fingerprint of the frozen parameters. The same parameters always give the same hash, so a rerun of a changed rule cannot hide.", source: "hypotheses.params_sha256; backtest/frozen.py" },
  trial: { term: "Trial", line: "Every run is a trial, including refused ones. Refused runs are logged but do not count in N.", source: "trials, trial_results, trial_metrics" },
  n_trials: { term: "N, the number of trials", line: "How many ok in-sample trials the family has run. Each one makes a high Sharpe easier to find by chance.", source: "trial_results.n_trials; results.family_n" },
  sr_star: { term: "SR*", line: "The Sharpe ratio you would expect from the best of N trials with no real edge. It rises with N and with the spread V of the family's Sharpes.", source: "trial_results.sr_star; metrics.expected_max_sharpe(N, V)" },
  dsr: { term: "Deflated Sharpe ratio (DSR)", line: "The probability that the observed Sharpe beats SR*, after N trials, skew and kurtosis. With one distinct parameter set SR* is 0 and DSR equals the PSR.", source: "trial_results.dsr, dsr_excess; metrics.deflated_sharpe (Bailey and López de Prado 2014)" },
  dsr_excess: { term: "dsr_excess", line: "The DSR of the returns above SPY. Promotion floors are written on it.", source: "trial_results.dsr_excess" },
  promote_at_least: { term: "promote_at_least", line: "The dsr_excess floor the argmax must clear to be promoted, declared before the sweep ran. It is read at the family's SR* high-water mark, so later trials cannot lower it.", source: "sweeps.promote_at_least; sweep promote" },
  retire_below: { term: "retire_below", line: "The line on the selection statistic below which the sweep promotes nothing and retires. Declared before the run.", source: "sweeps.retire_below; sweep report" },
  expected_range_pp: { term: "expected_range_pp", line: "The prior's plausible range for excess over SPY, in pp a year, written before the run. Reported, not a gate.", source: "sweeps.expected_range_lo_pp, expected_range_hi_pp" },
  family: { term: "Family", line: "Variants of one idea count together: they share N and SR*.", source: "config.FAMILIES, FAMILY_PARENTS; ADR 0014" },
  budget: { term: "Trial budget", line: "How many variants the file declares. All of them count in the family's N.", source: "the file's [grid]; sweeps.n_variants" },
  boundary: { term: "Development boundary", line: "No in-sample run reads a session after it. Moving it is a new owner decision, never an edit.", source: "owner_decisions.development_boundary; ADR 0016" },
  holdout: { term: "Holdout", line: "Out-of-sample data the family never read in development. Spent once per family, on a frozen finalist, with a reason.", source: "holdout.start, holdout.end; owner_decisions.holdout_spend" },
  forward: { term: "Forward holdout", line: "A holdout that starts after registration: no session of it existed when the rule was designed. The paper book is its evidence of record.", source: "ADR 0016 point 4; lab status 'forward'" },
  gap: { term: "Survivorship gap", line: "The share of the universe whose delisting the store may have missed, at each rebalance. Gated on holdout runs.", source: "trial_rebalances.gap_count_share; gap.count_share_threshold" },
  red_flag: { term: "Red flag", line: "Raised when excess over SPY is implausibly high; it blocks promotion until an audit clears it.", source: "trial_results.red_flag; metrics.red_flag_excess_cagr_pp" },
  cost_sensitivity: { term: "Cost sensitivity", line: "The same trial at 0, 15, 30, 60 and 100 bp a side. The base level is the file's costs.per_side_bps.", source: "trial_metrics per cost_per_side_bps" },
  tracking: { term: "Tracking check", line: "Does the book track its backtest recomputed over the same sessions, within the tolerance (ADR 0005).", source: "paper check 'tracking'; paper report; paper.min_rebalances" },
  kill_switch: { term: "Kill switch", line: "Holdings stay. Every later run submits nothing until it is released, after a reconciliation with status ok.", source: "kill_switch; paper kill, paper resume" },
  reconciliation: { term: "Reconciliation", line: "The broker account checked against the journal after each run.", source: "reconciliations.status: ok, mismatch, pending_unresolved, fills_lagging" },
  quiet: { term: "Quiet interval", line: "Runs write to the store only outside the evening ingest and every book's submit window.", source: "backtest/quiet.py" },
  refusal: { term: "Refusal", line: "The engine's answer when a run breaks a rule. The code and its message are shown word for word.", source: "holdout.decide: refused_window, refused_holdout, refused_gap, refused_variant, needs_gap" },
};

/** An ⓘ that teaches one term, in place, by the repo's method. */
export function TermInfo({ k, children }: { k: keyof typeof GLOSSARY | string; children?: ReactNode }) {
  const g = GLOSSARY[k];
  if (!g) return null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          aria-label={`What ${g.term} means`}
          className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -my-2 -mx-0.5 inline-grid size-7 shrink-0 place-items-center rounded-full align-middle outline-none focus-visible:ring-2 max-sm:size-9"
        >
          <Info className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent collisionPadding={12} align="start" className="w-[min(92vw,360px)] font-sans text-[13px] leading-relaxed shadow-none">
        <p className="font-medium">{g.term}</p>
        <p className="mt-1">{g.line}</p>
        {children}
        <p className="text-muted-foreground mt-2 text-xs">Engine: <span className="num">{g.source}</span></p>
      </PopoverContent>
    </Popover>
  );
}

/** The DSR in full: the formula from backtest/metrics.py, behind one disclosure. */
export function DsrMaths() {
  return (
    <details className="mt-2">
      <summary className="text-muted-foreground hover:text-foreground cursor-pointer text-xs">The formula</summary>
      <div className="num mt-2 space-y-1.5 text-[11.5px] leading-snug">
        <p>DSR = Φ( (SR − SR*) · √(T − 1) / √(1 − γ₃·SR + (γ₄ − 1)/4 · SR²) )</p>
        <p>SR* = √V · ( (1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)) )</p>
        <p className="text-muted-foreground font-sans">
          SR: Sharpe per period; T: periods; γ₃, γ₄: skew and kurtosis; N: trials in the family; V: variance of their Sharpes; γ: Euler's constant.
        </p>
      </div>
    </details>
  );
}
