// Builds sample/app-data.json: deterministic, clearly-marked SAMPLE data shaped
// like what the local API will serve. Run: node sample/generate.mjs
import { writeFileSync } from "node:fs";

let seed = 20261010;
const rand = () => ((seed = (seed * 1664525 + 1013904223) % 2 ** 32) / 2 ** 32);
const gauss = () => Math.sqrt(-2 * Math.log(rand() || 1e-9)) * Math.cos(2 * Math.PI * rand());
const r2 = (x) => Math.round(x * 100) / 100;

// NYSE sessions 2026-07-01..2026-10-09 (Jul 3 observed holiday, Labor Day Sep 7).
const holidays = new Set(["2026-07-03", "2026-09-07"]);
const sessions = [];
for (let d = new Date(Date.UTC(2026, 6, 1)); d <= new Date(Date.UTC(2026, 9, 9)); d.setUTCDate(d.getUTCDate() + 1)) {
  const iso = d.toISOString().slice(0, 10);
  if (d.getUTCDay() % 6 !== 0 && !holidays.has(iso)) sessions.push(iso);
}

const spyRet = sessions.map(() => 0.0004 + 0.008 * gauss());
let spy = 612.4;
const spyCloses = sessions.map((date, i) => ({ date, close: r2((spy *= 1 + (i ? spyRet[i] : 0))) }));

function equity(start, startEquity, beta, alpha, vol, pinned) {
  const i0 = sessions.indexOf(start);
  let v = startEquity;
  return sessions.slice(i0).map((date, k) => {
    if (k) v *= pinned ? 1 + 0.00002 : 1 + alpha + beta * spyRet[i0 + k] + vol * gauss();
    return { date, value: r2(v) };
  });
}

const daily = equity("2026-08-03", 100000, 1.1, 0.0003, 0.006, false);
const b3 = equity("2026-09-01", 100000, 0.85, 0.0002, 0.004, false);
const main = equity("2026-10-09", 100008.9, 0, 0, 0, true);
const books = [main, daily, b3];

// Portfolio: summed value plus a time-weighted return index (new money is not a gain).
const portfolio = [];
let idx = 100;
for (const date of sessions) {
  const today = books.map((s) => s.find((p) => p.date === date)).filter(Boolean);
  if (!today.length) continue;
  const value = today.reduce((a, p) => a + p.value, 0);
  if (portfolio.length) {
    const prevDate = portfolio.at(-1).date;
    let prev = 0, now = 0;
    for (const s of books) {
      const a = s.find((p) => p.date === prevDate), b = s.find((p) => p.date === date);
      if (a && b) { prev += a.value; now += b.value; }
    }
    idx *= now / prev;
  }
  portfolio.push({ date, value: r2(value), index: Math.round(idx * 10000) / 10000 });
}

const pos = (symbol, name, weight, target, value, pnl) => ({ symbol, name, weight, target_weight: target, value, unrealized_pnl: pnl });
const last = (s) => s.at(-1).value;


// Research lab, as the owner sees it. Names, stages, blockers and the H1 luck
// check (DSR 0.7262, psr basis) come from the repo's docs; every other number
// is SAMPLE and marked as such in the file's note.
const research = {
  waiting: [
    { id: "w1", idea_id: "b10", kind: "run", title: "Run the short-term momentum test",
      why: "Everything is built. One overnight run answers whether last month's busiest winners keep rising after costs.",
      effort: "15 min, then overnight", step: "Start the overnight run" },
    { id: "w2", idea_id: "b9", kind: "answer", title: "Answer 7 questions on earnings-reaction drift",
      why: "Hold length, which filings count, how to treat restatements. The test can't be registered until these are settled.",
      effort: "30 min", step: "Answer the 7 open questions" },
    { id: "w3", kind: "decide", title: "Set the development boundary",
      why: "No backtest will read past this date. Recommended: Dec 29, 2023, where your first tests already stop.",
      effort: "2 min", step: "Record the boundary with a reason" },
    { id: "w4", idea_id: "b4", kind: "decide", title: "Momentum + profitability: test now or wait?",
      why: "Your notes disagree: one says run it now, the newer decision says wait for a profitability finalist.",
      effort: "2 min", step: "Keep it parked, or run it now" },
    { id: "w5", idea_id: "h1", kind: "record", title: "Write down what monthly momentum taught you",
      why: "Its test and exam are done, but nothing is recorded, so your evidence still only shows other people's studies.",
      effort: "10 min", step: "Record what this test showed" },
  ],
  ideas: [
    { id: "h1", code: "H1", name: "Monthly momentum", family: "momentum", stage: "on_paper", book_id: "main",
      idea: "Own the stocks that rose most over the past year, skipping the last month. Reshuffle monthly.",
      evidence: { for: 3, mixed: 2, against: 0, note: "Strong in older studies; weaker after 2010 once costs are counted." },
      expected: "About level with the S&P 500 after costs. Mainly a test that the machine works end to end.",
      stop_rule: "Retire if it trails the S&P 500 by more than 1 point a year and the luck check is under 50%.",
      result: { status: "done", window: "Aug 2020 – Dec 2023", vs_spy: 0.011, luck: 0.7262, tries: 2, cost_drag: 0.0125, sample: ["vs_spy"] },
      exam: { kind: "paper", label: "Paper book main", done: 0, of: 6, unit: "monthly rebalances" },
      next: "First rebalance Fri, Oct 30.", cost: "—" },
    { id: "dm", code: "S1·d", name: "Daily momentum", family: "momentum", stage: "on_paper", book_id: "daily",
      idea: "The same momentum idea, rechecked every day before the open.",
      evidence: { for: 1, mixed: 2, against: 1, note: "Faster versions mostly lose their edge to trading costs." },
      expected: "Below monthly after costs; worth seeing on paper because the backtest can't model daily fills well.",
      stop_rule: "Stop if paper tracking drifts more than the agreed tolerance from its backtest.",
      result: { status: "done", window: "Aug 2020 – Dec 2023", vs_spy: 0.004, luck: 0.41, tries: 8, cost_drag: 0.041, sample: ["vs_spy", "luck", "cost_drag"] },
      exam: { kind: "paper", label: "Paper book daily", done: 48, of: 63, unit: "trading days" },
      next: "Exam ends around Oct 30.", cost: "—" },
    { id: "b3", code: "B3", name: "Profitability", family: "profitability", stage: "on_paper", book_id: "b3",
      idea: "Own companies that turn the most gross profit per dollar of assets. Reshuffle monthly.",
      evidence: { for: 2, mixed: 1, against: 0, note: "Well supported, but in a narrow form; long-only results are weaker." },
      expected: "A small edge over the S&P 500, smaller than published.",
      stop_rule: "Retire if it trails the S&P 500 by more than 1 point a year in-sample.",
      result: { status: "done", window: "Aug 2020 – Dec 2023", vs_spy: -0.006, luck: 0.38, tries: 1, cost_drag: 0.006, sample: ["vs_spy", "luck", "cost_drag"] },
      exam: { kind: "holdout", label: "Final exam (2024 – Sep 2026)", status: "unspent", note: "On hold until profitability has a finalist." },
      next: "Paper book running; exam on hold.", cost: "—" },
    { id: "b10", code: "B10", name: "Short-term momentum", family: "momentum", stage: "ready",
      idea: "Last month's winners among the most-traded stocks. Reshuffle monthly.",
      evidence: { for: 0, mixed: 0, against: 2, note: "One study says yes; two independent checks say it's about zero after costs." },
      expected: "About zero to slightly negative after costs at 15 bp.",
      stop_rule: "Retire both versions if neither beats the S&P 500 in-sample.",
      cost: "S" },
    { id: "b9", code: "B9", name: "Earnings-reaction drift", family: "new", stage: "blocked",
      idea: "Buy stocks the market cheered most around their earnings release, hold for 20 days.",
      evidence: { for: 0, mixed: 1, against: 1, note: "Mixed: the effect exists but much of it has faded since publication." },
      expected: "About zero to negative after costs.",
      stop_rule: "Written once its questions are answered.",
      blocked_by: ["Filing-events data (T164c–e)", "Filing clock defect #1382", "7 open questions for you"],
      next: "Unblocks when the filing data lands.", cost: "M" },
    { id: "b3b", code: "B3b", name: "Cash profitability", family: "profitability", stage: "exploring",
      idea: "Like profitability, but using operating cash flow instead of gross profit.",
      evidence: { for: 1, mixed: 0, against: 0, note: "Supported as a stand-in; the exact measure isn't graded yet." },
      next: "After profitability's exam.", cost: "S" },
    { id: "b5", code: "B5", name: "Filing changes → fundamentals", family: "new", stage: "exploring",
      idea: "When a company quietly rewrites its annual report, do its next results change?",
      evidence: { for: 0, mixed: 0, against: 0, untested: 3, note: "Not enough evidence either way yet." },
      blocked_by: ["Filing text ingest"], next: "Needs filing text first.", cost: "L" },
    { id: "b7", code: "B7", name: "Insider buying", family: "new", stage: "exploring",
      idea: "Follow clusters of executives buying their own company's stock.",
      evidence: { for: 0, mixed: 1, against: 1, note: "Routine insider buys don't predict much; clustered, opportunistic ones might." },
      blocked_by: ["Form 4 data (+3 years)"], next: "Needs insider-filing data.", cost: "L" },
    { id: "b4", code: "B4", name: "Momentum + profitability", family: "combined", stage: "parked",
      idea: "Hold stocks that score well on both momentum and profitability.",
      parked: "Waiting for a profitability finalist, so the combined test has something to stand on.", cost: "S" },
    { id: "b2", code: "B2", name: "Trend filter", family: "momentum", stage: "parked",
      idea: "Sit in cash when the market is below its 10-month average.",
      parked: "Lowers risk but also return. Your goal is beating the S&P 500, so it's off the list.", cost: "S" },
  ],
  families: [
    { id: "momentum", name: "Momentum", tries: 9, luck_bar_note: "Each new try raises the bar the next result has to clear.",
      exam: "spent", exam_date: "2026-10-06", promotions: [1, 2], sample: ["tries"] },
    { id: "profitability", name: "Profitability", tries: 1, exam: "unspent", promotions: [0, 2] },
    { id: "combined", name: "Momentum + profitability", tries: 0, exam: "unspent", promotions: [0, 2] },
  ],
  lessons: [
    { id: "HO-1", text: "12-1 momentum beat the market in long historical studies.", grade: "supported", source: "Research handoff" },
    { id: "G1-1", text: "After 2010, net of costs, momentum's edge is weak and uneven.", grade: "mixed", source: "Momentum after 2010 review" },
    { id: "QI-6", text: "Gross profitability predicts returns, in a narrow, long-short form.", grade: "supported", source: "Quant investing brief" },
    { id: "SH-5", text: "Short-term earnings reactions mostly don't survive realistic costs.", grade: "against", source: "Short-horizon candidates" },
  ],
};

const data = {
  sample: true,
  note: "SAMPLE DATA for the UI prototype. Not real positions, prices or results.",
  as_of: "2026-10-09T20:00:00Z",
  updated_at: "2026-10-09T21:12:00Z",
  account_mode: "paper",
  benchmark: { symbol: "SPY", name: "S&P 500 (SPY)", closes: spyCloses },
  portfolio: { equity: portfolio, expected_tracking_error: 0.06 /* SAMPLE: the yearly spread around the S&P the backtests expect */ },
  strategies: [
    { id: "h1-monthly-momentum", name: "Monthly momentum", idea: "Own the stocks that rose most over the past year, skipping the last month. Reshuffle once a month.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.141, benchmark_annual_return: 0.112, max_drawdown: -0.187, verdict: "pass" }, on_paper: true },
    { id: "daily-momentum", name: "Daily momentum", idea: "Same idea as monthly momentum, but rechecked every day before the open.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.128, benchmark_annual_return: 0.112, max_drawdown: -0.224, verdict: "pass" }, on_paper: true },
    { id: "profitability", name: "Profitability", idea: "Own companies that turn the most gross profit per dollar of assets.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.119, benchmark_annual_return: 0.112, max_drawdown: -0.162, verdict: "pass" }, on_paper: true },
  ],
  books: [
    { id: "main", name: "main", strategy_id: "h1-monthly-momentum", cadence: "monthly", started_on: "2026-10-09", start_equity: 100008.9,
      status: { state: "running" }, equity: main, cash: last(main), positions: [], orders: [],
      last_run: { at: "2026-10-09T13:05:00Z", outcome: "ok", summary: "Opened the book. Holding cash until the first rebalance." },
      next_run: { at: "2026-10-30T13:05:00Z", what: "First rebalance" } },
    { id: "daily", name: "daily", strategy_id: "daily-momentum", cadence: "daily", started_on: "2026-08-03", start_equity: 100000,
      status: { state: "running" }, equity: daily, cash: r2(last(daily) * 0.021),
      positions: [
        pos("NVDA", "NVIDIA", 0.112, 0.1, r2(last(daily) * 0.112), 1840.22),
        pos("AVGO", "Broadcom", 0.104, 0.1, r2(last(daily) * 0.104), 960.4),
        pos("PLTR", "Palantir", 0.101, 0.1, r2(last(daily) * 0.101), -412.75),
        pos("META", "Meta Platforms", 0.099, 0.1, r2(last(daily) * 0.099), 655.1),
        pos("GE", "GE Aerospace", 0.098, 0.1, r2(last(daily) * 0.098), 233.9),
      ],
      orders: [], last_run: { at: "2026-10-09T13:25:00Z", outcome: "ok", summary: "Sold 1, bought 1. 2 orders filled." },
      next_run: { at: "2026-10-12T13:25:00Z", what: "Daily check" } },
    { id: "b3", name: "b3", strategy_id: "profitability", cadence: "monthly", started_on: "2026-09-01", start_equity: 100000,
      status: { state: "running" }, equity: b3, cash: r2(last(b3) * 0.012),
      positions: [
        pos("AAPL", "Apple", 0.051, 0.05, r2(last(b3) * 0.051), 210.3),
        pos("MSFT", "Microsoft", 0.049, 0.05, r2(last(b3) * 0.049), -88.1),
      ],
      orders: [], last_run: { at: "2026-09-30T13:05:00Z", outcome: "ok", summary: "Rebalanced 20 names. 14 orders filled." },
      next_run: { at: "2026-10-30T13:05:00Z", what: "Monthly rebalance" } },
  ],
  alerts: [],
  research,
};

writeFileSync(new URL("./app-data.json", import.meta.url), JSON.stringify(data, null, 1) + "\n");
console.log("sessions", sessions.length, "portfolio", portfolio.length, "last", portfolio.at(-1));
