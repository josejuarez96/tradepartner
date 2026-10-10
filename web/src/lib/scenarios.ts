import type { AppData } from "./types";

/** Prototype-only variations on the sample, so every state can be reviewed. */
export function scenario(base: AppData, state: string | null): AppData {
  const d: AppData = structuredClone(base);
  if (state === "empty") {
    d.books = [];
    d.portfolio.equity = [];
  }
  if (state === "alert") {
    d.alerts = [{
      id: "a1", book_id: "daily", kind: "run_skipped", at: "2026-10-09T13:25:00Z",
      title: "daily didn't trade this morning",
      detail: "Its prices stopped at Thursday's close, so it skipped the run rather than trade on old numbers. Nothing was bought or sold.",
      todo: "Nothing yet. Tonight's update usually fixes this. If this is still here at 9 am Monday, check the price feed.",
    }];
  }
  if (state === "stopped") {
    const b3 = d.books.find((b) => b.id === "b3")!;
    b3.status = { state: "stopped", by: "you", at: "2026-10-09T13:41:00Z", reason: "Earnings week, want to watch first" };
  }
  if (state === "safety") {
    const daily = d.books.find((b) => b.id === "daily")!;
    daily.status = {
      state: "stopped", by: "safety", at: "2026-10-09T19:12:00Z", rule: "Daily loss limit",
      reason: "The book fell 3.4% today; the limit is 3%.",
    };
  }
  return d;
}
