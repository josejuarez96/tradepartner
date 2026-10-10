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
  portfolio: { equity: PortfolioPoint[] };
  strategies: Strategy[]; books: Book[]; alerts: Alert[];
}
