import raw from "../../sample/lab.json";

/**
 * Shape of sample/lab.json: what the local API would serve from the registry
 * (`hypotheses`, `trials`, `trial_results`, `trial_metrics`), the lab tables
 * (`sweeps`, `sweep_variants`, `sweep report`), `owner_decisions` and the
 * paper journal (`paper_run_results`, `reconciliations`, `alerts`,
 * `kill_switch`, `paper check`). Field names follow the engine's.
 */
export type StageKey = "idea" | "testing" | "holdout" | "paper" | "live";
export type Stage = StageKey | "parked" | "retired";
export const STAGES: { key: StageKey; label: string }[] = [
  { key: "idea", label: "Idea" },
  { key: "testing", label: "Testing" },
  { key: "holdout", label: "Holdout" },
  { key: "paper", label: "Paper" },
  { key: "live", label: "Live" },
];

export type RunStatus = "ok" | "halted" | "stale" | "skipped_kill_switch" | "crashed" | "failed";
export type ReconStatus = "ok" | "mismatch" | "pending_unresolved" | "fills_lagging";
export type TrialStatus = "ok" | "failed" | "refused_window" | "refused_holdout" | "refused_gap" | "refused_variant" | "needs_gap";

export interface Health {
  ingest: { session: string; finished_at: string; status: "ok" | "failed" | "stale" };
  runs: { book_id: string; session: string; finished_at: string; status: RunStatus }[];
  reconciliations: { book_id: string; at: string; status: ReconStatus }[];
  alerts: { at: string; book_id: string; kind: "missed_run" | "halted" | "reconciliation" | "stale_data"; detail: string }[];
  next: { what: string; at: string };
}

export interface Waiting {
  id: string; kind: "trial_finished" | "decision_due" | "draft_pr"; strategy_id: string; tab?: string;
  text: string; detail: string; pr?: { number: number; state: "draft" | "open" | "ready"; checks: string }; sample?: string[];
}

export interface Family {
  id: string; name: string; n_trials: number; sr_star: number; sr_star_next: number; v: number;
  holdout: { state: "unspent" | "spent" | "forward"; spends_used: number; cap: number; spent_at?: string; spent_by?: string; trial_id?: number };
  sample?: string[];
}

export interface Tracking {
  book_id: string; strategy_id: string; through_session: string; paper_return: number; backtest_return: number; gap: number; tolerance: number;
  rebalances_done: number; min_rebalances: number; unit: string; first_rebalance?: string; forward?: boolean; sample?: string[];
}

export interface KillEvent { book_id: string; at: string; action: "engaged" | "released"; by: string; reason: string; reconciliation?: string; command: string }

export interface Threshold { key: string; value: string; on?: string }
export interface Spec {
  kind: "hypothesis" | "sweep"; doc_path: string; in_sample_start: string;
  holdout: { start: string; end: string; kind: string };
  frozen: [string, string][]; thresholds: Threshold[]; kill: string; budget: string; sample?: string[];
}

export interface Trial {
  trial_id: number | null; run_at: string; kind: "in_sample" | "holdout" | "tracking"; status: TrialStatus; label: string;
  n_trials?: number; dsr?: number; dsr_excess?: number; excess_cagr_spy?: number; message?: string; note?: string;
}

export interface Check { label: string; term: string; value: string; against: string; pass: boolean | null; read: string }
export interface Result {
  kind: "sweep" | "trial"; title: string; finished_at: string; n_trials: number; sr_star: number; sr_star_mark?: number; argmax?: string;
  checks: Check[]; costs: { bp: number; excess: number; base?: boolean }[];
  verdict: { rule: "promote" | "retire" | "nothing" | "stays"; text: string; then: string };
  variants?: { label: string; excess: number; dsr_excess: number; argmax?: boolean }[];
  identity: { code_version: string; data_cutoff: string; params_sha256: string };
  sample?: string[];
}

export interface Decision { at: string; kind: string; reason: string; by: string; detail?: string }

export interface Strategy {
  id: string; code: string; name: string; family: string; stage: Stage; summary: string; book_id?: string;
  waits_on?: { who: "you" | "engine" | "data" | "agent"; text: string }; blocked?: string; prior?: string;
  registered_at?: string; params_sha256?: string;
  latest?: { n_trials: number; sr_star: number; dsr?: number; dsr_excess?: number; excess_cagr_spy: number; label: string };
  evidence?: { id: string; grade: string; text: string }[];
  spec?: Spec; trials?: Trial[]; result?: Result; decisions?: Decision[];
  holdout_result?: { trial_id: number; excess_cagr_spy: number; reason: string };
  holdout_refusal?: { code: TrialStatus; message: string; breaches: [string, number][] };
  parked_reason?: string; unpark_when?: string; sample?: string[];
}

export interface Lab {
  note: string; as_of: string; development_boundary: string; health: Health; waiting: Waiting[]; families: Family[];
  tracking: Tracking[]; kill_switch_events: KillEvent[]; strategies: Strategy[];
}

export const lab = raw as unknown as Lab;

export const family = (id: string) => lab.families.find((f) => f.id === id);
export const strategy = (id: string) => lab.strategies.find((s) => s.id === id);
export const stageIndex = (s: Stage) => STAGES.findIndex((x) => x.key === s);

/** Tabs a strategy has reached, in the order of its life. */
export function tabsFor(s: Strategy): { key: string; label: string }[] {
  const i = stageIndex(s.stage);
  const t = [{ key: "evidence", label: "Evidence" }];
  if (s.registered_at) t.push({ key: "runs", label: "Runs" });
  if (s.result) t.push({ key: "result", label: "Result" });
  if (s.registered_at && (i >= 1 || s.stage === "retired")) t.push({ key: "holdout", label: "Holdout" });
  if (s.book_id) t.push({ key: "paper", label: "Paper" });
  if (s.decisions?.length) t.push({ key: "decisions", label: "Decisions" });
  return t;
}

/** "+0.9 pp" from a fraction a year. */
export const pp = (v: number, d = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)} pp`;
export const num = (v: number, d = 2) => `${v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`;
export const shortSha = (s: string) => `${s.slice(0, 6)}…${s.slice(-4)}`;
