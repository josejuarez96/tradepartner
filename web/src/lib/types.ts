/** Shape of sample/app-data.json, i.e. what the local API is expected to serve. */
export interface Point { date: string; value: number }
/** A book's daily value with its open, high and low, for candles. */
export interface ValuePoint extends Point { open?: number; high?: number; low?: number }
export interface Bar { date: string; open: number; high: number; low: number; close: number; volume: number }
export interface PortfolioPoint extends Point { index: number }

export interface BookStatus {
  state: "running" | "stopped";
  by?: "you" | "safety";
  /** For a safety stop: the rule that tripped. */
  rule?: string;
  at?: string;
  reason?: string;
}

export interface Position {
  symbol: string; name: string; weight: number; target_weight: number; value: number; unrealized_pnl: number;
  shares: number; price: number; avg_cost: number; bought_on: string;
  /** Rank today on the strategy's own measure (1 is best), and that measure's value. */
  rank: number; signal: number;
  /** SAMPLE: daily candles and rank history over the book's life. */
  bars: Bar[]; ranks: { date: string; rank: number }[];
}

/** expected_price: the price when the order was decided; cost_bp: how much worse the fill was. */
export interface Order {
  id: string; placed_at: string; side: "buy" | "sell"; symbol: string; shares: number; filled_shares: number;
  status: "filled" | "partial" | "open" | "cancelled" | "rejected";
  expected_price: number; fill_price: number | null; cost_bp: number | null; why: string; note?: string;
}

/**
 * The strategy's rule as the engine runs it (the hypothesis file's fields):
 * rank the largest `universe` stocks on `measure`, hold the top `top_fraction`
 * at equal weight, trade every holding back to equal weight at each rebalance.
 */
export interface Rule {
  universe: number; measure: string; top_fraction: number; per_side_bps: number; fill_price: "close" | "open"; check: string;
}

/** One rebalance of a book: names in and out, and the small trades back to equal weight. */
export interface BookRebalance { date: string; entries: number; exits: number; reweighted: number; skipped_dust: number; cost_usd: number }

export interface Run { at: string; outcome?: "ok" | "failed" | "skipped"; summary?: string; what?: string }

export interface Book {
  id: string; name: string; strategy_id: string; cadence: "daily" | "weekly" | "monthly";
  started_on: string; start_equity: number; status: BookStatus; equity: ValuePoint[]; cash: number;
  positions: Position[]; orders: Order[]; rebalances: BookRebalance[]; last_run: Run; next_run: Run;
  /** SAMPLE: the yearly spread around the S&P this book's backtest expects. */
  expected_tracking_error: number;
}

export interface Strategy {
  id: string; name: string; idea: string; on_paper: boolean;
  backtest: { period: string; annual_return: number; benchmark_annual_return: number; max_drawdown: number; verdict: string; cost_bp: number };
  rule: Rule;
}

export interface Alert {
  id: string; book_id?: string; kind: "run_failed" | "run_skipped" | "stale_data" | "account_mismatch";
  title: string; detail: string; todo: string; at: string;
}

export interface AppData {
  sample: boolean; note: string; as_of: string; updated_at: string; account_mode: "paper" | "live";
  benchmark: { symbol: string; name: string; closes: { date: string; close: number }[] };
  portfolio: { equity: PortfolioPoint[]; expected_tracking_error: number };
  strategies: Strategy[]; books: Book[]; alerts: Alert[];
  research: Research;
}

export type Stage = "on_paper" | "ready" | "blocked" | "exploring" | "parked" | "retired";

export interface Idea {
  id: string; code: string; name: string; family: string; stage: Stage; idea: string; book_id?: string;
  evidence?: { for: number; mixed: number; against: number; untested?: number; note: string };
  expected?: string; stop_rule?: string;
  /** vs_spy: excess return a year over the S&P 500; luck: chance the edge is real after counting every try (DSR). */
  result?: { status: "done"; window: string; vs_spy: number; luck: number; tries: number; distinct: number; periods: number; period: string; cost_drag: number; sample?: string[] };
  exam?: { kind: "paper" | "holdout"; label: string; done?: number; of?: number; unit?: string; status?: string; note?: string };
  blocked_by?: string[]; parked?: string; next?: string; cost: string;
}

export interface WaitingItem {
  id: string; idea_id?: string; kind: "run" | "answer" | "decide" | "record";
  title: string; why: string; effort: string; step: string;
}

export interface Family {
  id: string; name: string; tries: number; luck_bar_note?: string;
  exam: "spent" | "unspent"; exam_date?: string; promotions: [number, number]; sample?: string[];
}

export interface Lesson { id: string; text: string; grade: "supported" | "mixed" | "against" | "untested"; source: string }

/** A name at a rebalance: its rank and score on the measure (null if it left the universe). */
export interface Ranked { s: string; r: number | null; sig: number | null }

/** One rebalance of a backtest, as the engine saw it, stage by stage. */
export interface ReplayRebalance {
  date: string;
  /** Stage 2, universe: the thousand, the names each rule excluded, the names left with a score. */
  n_universe: number; excluded: { rule: string; n: number }[]; n_scored: number;
  /** Stage 3, signal: how the scores spread (counts per bin) and the score of the last name held. */
  hist: number[]; cut: number; top: Ranked[]; near: (Ranked & { held: boolean })[];
  /** Stages 4 to 6: how many are held, who came in and went out, how many kept names traded back to equal weight. */
  hold: number; entries: Ranked[]; exits: Ranked[]; reweighted: number; notional: number; cost_usd: number;
  held: string[];
}

/** A backtest, step by step: the daily value of the strategy (v) and the S&P (b), and every rebalance. */
export interface Replay {
  idea_id: string; sample: boolean; trial: number;
  window: { start: string; end: string }; holdout: { start: string; end: string }; start_equity: number;
  rule: Omit<Rule, "check">; bins: { lo: number; hi: number; n: number };
  days: { date: string; v: number; b: number }[];
  rebalances: ReplayRebalance[];
  /** The same run's result at every cost level the engine evaluates. */
  cost_levels: { bp: number; vs_spy: number }[]; annual_turnover: number;
  end: { vs_spy: number; luck: number; tries: number; distinct: number; periods: number };
  identity: { params: string; code: string; data_cutoff: string };
}

export interface Research { waiting: WaitingItem[]; ideas: Idea[]; families: Family[]; lessons: Lesson[]; replays: Record<string, Replay> }
