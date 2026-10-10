/** Shape of sample/app-data.json, i.e. what the local API is expected to serve. */
export interface Point { date: string; value: number }
export interface PortfolioPoint extends Point { index: number }

export interface BookStatus {
  state: "running" | "stopped";
  by?: "you" | "safety";
  at?: string;
  reason?: string;
}

export interface Position {
  symbol: string; name: string; weight: number; target_weight: number; value: number; unrealized_pnl: number;
}

export interface Run { at: string; outcome?: "ok" | "failed" | "skipped"; summary?: string; what?: string }

export interface Book {
  id: string; name: string; strategy_id: string; cadence: "daily" | "weekly" | "monthly";
  started_on: string; start_equity: number; status: BookStatus; equity: Point[]; cash: number;
  positions: Position[]; orders: unknown[]; last_run: Run; next_run: Run;
}

export interface Strategy {
  id: string; name: string; idea: string; on_paper: boolean;
  backtest: { period: string; annual_return: number; benchmark_annual_return: number; max_drawdown: number; verdict: string };
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
  result?: { status: "done"; window: string; vs_spy: number; luck: number; tries: number; cost_drag: number; sample?: string[] };
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
