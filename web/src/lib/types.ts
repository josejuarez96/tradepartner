/** Shape of sample/app-data.json, i.e. what the local API is expected to serve. */
export interface Point { date: string; value: number }
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
}

/** expected_price: the price when the order was decided; cost_bp: how much worse the fill was. */
export interface Order {
  id: string; placed_at: string; side: "buy" | "sell"; symbol: string; shares: number; filled_shares: number;
  status: "filled" | "partial" | "open" | "cancelled" | "rejected";
  expected_price: number; fill_price: number | null; cost_bp: number | null; why: string; note?: string;
}

/** The strategy's rule in numbers, so the screen can say why it holds what it holds. */
export interface Rule {
  universe: number; measure: string; hold: number; sell_below: number; weight: number; tolerance: number; check: string;
}

export interface Run { at: string; outcome?: "ok" | "failed" | "skipped"; summary?: string; what?: string }

export interface Book {
  id: string; name: string; strategy_id: string; cadence: "daily" | "weekly" | "monthly";
  started_on: string; start_equity: number; status: BookStatus; equity: Point[]; cash: number;
  positions: Position[]; orders: Order[]; last_run: Run; next_run: Run;
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

export interface Research { waiting: WaitingItem[]; ideas: Idea[]; families: Family[]; lessons: Lesson[] }
