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

const last = (s) => s.at(-1).value;

// Holdings, SAMPLE. Each one carries why the rule holds it: its rank today on
// the strategy's own measure, and that measure's value.
const pos = (book, symbol, name, weight, target, rank, signal, price, avg_cost, bought_on) => {
  const value = r2(last(book) * weight);
  const shares = Math.round(value / price);
  return { symbol, name, weight, target_weight: target, value, shares, price, avg_cost, bought_on, rank, signal,
    unrealized_pnl: r2(shares * (price - avg_cost)) };
};
const dailyPositions = [
  pos(daily, "NVDA", "NVIDIA", 0.112, 0.1, 1, 1.42, 191.2, 158.4, "2026-08-03"),
  pos(daily, "AVGO", "Broadcom", 0.104, 0.1, 2, 1.18, 342.6, 309.9, "2026-08-03"),
  pos(daily, "PLTR", "Palantir", 0.101, 0.1, 3, 1.05, 171.3, 178.2, "2026-09-14"),
  pos(daily, "GE", "GE Aerospace", 0.098, 0.1, 4, 0.81, 288.4, 276.1, "2026-08-03"),
  pos(daily, "META", "Meta Platforms", 0.099, 0.1, 5, 0.74, 742.1, 693.5, "2026-08-21"),
  pos(daily, "NFLX", "Netflix", 0.097, 0.1, 6, 0.69, 1214.5, 1188.0, "2026-09-02"),
  pos(daily, "JPM", "JPMorgan Chase", 0.091, 0.1, 7, 0.52, 309.8, 307.1, "2026-10-09"),
  pos(daily, "ANET", "Arista Networks", 0.096, 0.1, 8, 0.49, 141.7, 137.2, "2026-10-05"),
  pos(daily, "ORCL", "Oracle", 0.093, 0.1, 11, 0.44, 288.9, 296.3, "2026-09-23"),
  pos(daily, "TSLA", "Tesla", 0.088, 0.1, 14, 0.38, 433.0, 441.8, "2026-10-06"),
];
const b3Names = [
  ["AAPL", "Apple", 0.47, 254.6, 241.0], ["MSFT", "Microsoft", 0.41, 512.3, 519.8], ["V", "Visa", 0.62, 351.2, 344.0],
  ["MA", "Mastercard", 0.58, 589.4, 576.2], ["ADBE", "Adobe", 0.55, 352.8, 371.5], ["LLY", "Eli Lilly", 0.51, 812.0, 768.3],
  ["ABBV", "AbbVie", 0.49, 228.7, 219.4], ["KO", "Coca-Cola", 0.46, 67.9, 69.2], ["PEP", "PepsiCo", 0.45, 142.3, 146.8],
  ["PG", "Procter & Gamble", 0.44, 154.1, 157.0], ["HD", "Home Depot", 0.43, 401.7, 396.5], ["LOW", "Lowe's", 0.42, 251.9, 249.0],
  ["TJX", "TJX Companies", 0.42, 144.6, 139.8], ["ORLY", "O'Reilly Automotive", 0.40, 102.5, 98.1], ["AZO", "AutoZone", 0.40, 4120.0, 3995.0],
  ["MCD", "McDonald's", 0.39, 301.4, 306.2], ["TXN", "Texas Instruments", 0.38, 182.6, 191.0], ["QCOM", "Qualcomm", 0.38, 166.2, 158.9],
  ["NKE", "Nike", 0.37, 71.3, 74.8], ["COST", "Costco", 0.36, 921.5, 934.0],
];
const b3Positions = b3Names
  .map(([s, n, gpa, price, cost], k) => pos(b3, s, n, r2((0.0494 + ((k * 7) % 5 - 2) * 0.0009) * 1000) / 1000, 0.05, k + 1, gpa, price, cost, "2026-09-01"))
  .sort((a, b) => a.rank - b.rank);

// Orders, SAMPLE. expected_price is the price when the order was decided (the
// open); cost_bp is how much worse the fill was, the number the backtest assumed.
let oid = 0;
const ord = (placed_at, side, symbol, shares, status, expected_price, fill_price, why, filled = status === "filled" ? shares : 0, note) => ({
  id: `o${++oid}`, placed_at, side, symbol, shares, filled_shares: filled, status, expected_price, fill_price,
  cost_bp: fill_price ? Math.round(((side === "buy" ? fill_price - expected_price : expected_price - fill_price) / expected_price) * 1e4 * 10) / 10 : null,
  why, ...(note ? { note } : {}),
});
const dailyOrders = [
  ord("2026-10-09T13:25:00Z", "buy", "JPM", 31, "filled", 309.4, 309.62, "Entered the top 10 (rank 7)"),
  ord("2026-10-09T13:25:00Z", "sell", "CRWD", 19, "filled", 488.1, 487.85, "Fell to rank 23, past the sell line at 20"),
  ord("2026-10-06T13:25:00Z", "buy", "TSLA", 21, "filled", 441.5, 441.8, "Entered the top 10 (rank 9)"),
  ord("2026-10-06T13:25:00Z", "sell", "UBER", 104, "filled", 92.3, 92.21, "Fell to rank 25, past the sell line at 20"),
  ord("2026-10-05T13:25:00Z", "buy", "ANET", 72, "filled", 137.0, 137.2, "Second try after Friday's order went unfilled"),
  ord("2026-10-02T13:25:00Z", "buy", "ANET", 72, "cancelled", 133.9, null, "Entered the top 10 (rank 8)", 0, "Price moved past the limit before it filled; cancelled at the close"),
  ord("2026-10-02T13:25:00Z", "sell", "COIN", 26, "filled", 351.0, 350.38, "Fell to rank 21, past the sell line at 20"),
  ord("2026-09-23T13:25:00Z", "buy", "ORCL", 33, "filled", 296.0, 296.3, "Entered the top 10 (rank 10)"),
  ord("2026-09-23T13:25:00Z", "sell", "SHOP", 62, "filled", 158.6, 158.41, "Fell to rank 22, past the sell line at 20"),
  ord("2026-09-22T13:25:00Z", "sell", "NVDA", 6, "filled", 172.1, 171.96, "Trim: weight had drifted to 12.3%, over 2 points from 10%"),
];
const sep30 = "2026-09-30T13:05:00Z";
const b3Orders = [
  ord(sep30, "sell", "GOOGL", 18, "filled", 243.1, 242.83, "Fell to rank 29, out of the top 20"),
  ord(sep30, "sell", "UNH", 9, "filled", 352.4, 351.9, "Fell to rank 24, out of the top 20"),
  ord(sep30, "buy", "ORLY", 49, "filled", 98.0, 98.1, "Entered the top 20 (rank 14)"),
  ord(sep30, "buy", "QCOM", 31, "filled", 158.7, 158.9, "Entered the top 20 (rank 18)"),
  ...["AAPL", "MSFT", "V", "MA", "LLY", "ABBV", "KO", "HD", "TJX", "COST"].map((sym, k) => {
    const px = b3Names.find((n) => n[0] === sym)[4];
    const side = k % 3 ? "sell" : "buy";
    return ord(sep30, side, sym, 1 + (k % 4), "filled", px, r2(px * (1 + (side === "buy" ? 1 : -1) * (2 + (k % 4)) / 1e4)), "Trim or top up back to 5%");
  }),
];

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
      result: { status: "done", window: "Aug 2020 – Dec 2023", vs_spy: 0.011, luck: 0.7262, tries: 2, distinct: 1, periods: 40, period: "monthly", cost_drag: 0.0125, sample: ["vs_spy"] },
      exam: { kind: "paper", label: "Paper book main", done: 0, of: 6, unit: "monthly rebalances" },
      next: "First rebalance Fri, Oct 30.", cost: "—" },
    { id: "dm", code: "S1·d", name: "Daily momentum", family: "momentum", stage: "on_paper", book_id: "daily",
      idea: "The same momentum idea, rechecked every day before the open.",
      evidence: { for: 1, mixed: 2, against: 1, note: "Faster versions mostly lose their edge to trading costs." },
      expected: "Below monthly after costs; worth seeing on paper because the backtest can't model daily fills well.",
      stop_rule: "Stop if paper tracking drifts more than the agreed tolerance from its backtest.",
      result: { status: "done", window: "Aug 2020 – Dec 2023", vs_spy: 0.004, luck: 0.41, tries: 8, distinct: 6, periods: 846, period: "daily", cost_drag: 0.041, sample: ["vs_spy", "luck", "cost_drag"] },
      exam: { kind: "paper", label: "Paper book daily", done: 48, of: 63, unit: "trading days" },
      next: "Exam ends around Oct 30.", cost: "—" },
    { id: "b3", code: "B3", name: "Profitability", family: "profitability", stage: "on_paper", book_id: "b3",
      idea: "Own companies that turn the most gross profit per dollar of assets. Reshuffle monthly.",
      evidence: { for: 2, mixed: 1, against: 0, note: "Well supported, but in a narrow form; long-only results are weaker." },
      expected: "A small edge over the S&P 500, smaller than published.",
      stop_rule: "Retire if it trails the S&P 500 by more than 1 point a year in-sample.",
      result: { status: "done", window: "Aug 2020 – Dec 2023", vs_spy: -0.006, luck: 0.38, tries: 1, distinct: 1, periods: 40, period: "monthly", cost_drag: 0.006, sample: ["vs_spy", "luck", "cost_drag"] },
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

// ---- Terminal-chart extras, SAMPLE ----------------------------------------
// Open/high/low for each book's daily value, so the book chart can draw candles.
for (const series of books) {
  for (let k = 0; k < series.length; k++) {
    const p = series[k], prev = k ? series[k - 1].value : p.value;
    const o = r2(prev * (1 + 0.0025 * gauss()));
    const span = Math.abs(p.value - o) + p.value * 0.003 * Math.abs(gauss());
    p.open = o; p.high = r2(Math.max(o, p.value) + span * 0.5); p.low = r2(Math.min(o, p.value) - span * 0.5);
  }
}

// Daily candles and rank history for every holding, ending at today's price
// and rank; every fill lands inside its day's range.
function candles(book, position, orders, rule) {
  const dates = book.map((p) => p.date);
  const n = dates.length;
  const closes = new Array(n);
  closes[n - 1] = position.price;
  for (let k = n - 2; k >= 0; k--) closes[k] = closes[k + 1] / (1 + 0.004 + 0.018 * gauss());
  // Pull the path toward the average cost around the purchase date.
  const buy = dates.indexOf(position.bought_on);
  if (buy >= 0) {
    const f = position.avg_cost / closes[buy];
    for (let k = 0; k <= buy; k++) closes[k] *= f;
    for (let k = buy + 1; k < n - 1; k++) closes[k] *= 1 + (f - 1) * (1 - (k - buy) / (n - 1 - buy));
  }
  const bars = dates.map((date, k) => {
    const c = closes[k], o = k ? closes[k - 1] * (1 + 0.006 * gauss()) : c * (1 + 0.006 * gauss());
    const span = Math.abs(c - o) + c * 0.012 * Math.abs(gauss());
    return { date, open: r2(o), high: r2(Math.max(o, c) + span * 0.4), low: r2(Math.min(o, c) - span * 0.4), close: r2(c),
      volume: Math.round((8 + 4 * Math.abs(gauss())) * 1e6) };
  });
  for (const o of orders.filter((x) => x.symbol === position.symbol && x.fill_price)) {
    const b = bars.find((x) => x.date === o.placed_at.slice(0, 10));
    if (b) { b.low = Math.min(b.low, r2(o.fill_price * 0.997)); b.high = Math.max(b.high, r2(o.fill_price * 1.003)); }
  }
  // Walk the rank back from today's; while held it never passes the sell line, and on the day it was bought it was inside the top.
  const ranks = new Array(n);
  let r = position.rank;
  for (let k = n - 1; k >= 0; k--) {
    const cap = dates[k] > position.bought_on ? rule.sell_below : dates[k] === position.bought_on ? rule.hold : rule.sell_below + 10;
    r = Math.max(1, Math.min(cap, r));
    ranks[k] = Math.round(r);
    r += 1.1 * gauss();
  }
  return { bars, ranks: dates.map((date, k) => ({ date, rank: ranks[k] })) };
}
const dailyRule = { hold: 10, sell_below: 20 }, b3Rule = { hold: 20, sell_below: 20 };
for (const p of dailyPositions) Object.assign(p, candles(daily, p, dailyOrders, dailyRule));
for (const p of b3Positions) Object.assign(p, candles(b3, p, b3Orders, b3Rule));

// ---- Backtest replay for monthly momentum (H1), SAMPLE ---------------------
// The window and rule are H1's; the universe, prices and trades are invented.
// Rebalances fall on the last NYSE session of each month.
const nyseHolidays = new Set([
  "2019-09-02", "2019-11-28", "2019-12-25", "2020-01-01", "2020-01-20", "2020-02-17", "2020-04-10", "2020-05-25", "2020-07-03",
  "2020-09-07", "2020-11-26", "2020-12-25", "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31", "2021-07-05",
  "2021-09-06", "2021-11-25", "2021-12-24", "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20", "2022-07-04",
  "2022-09-05", "2022-11-24", "2022-12-26", "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19",
  "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
]);
const rsessions = [];
for (let d = new Date(Date.UTC(2019, 7, 1)); d <= new Date(Date.UTC(2023, 11, 29)); d.setUTCDate(d.getUTCDate() + 1)) {
  const iso = d.toISOString().slice(0, 10);
  if (d.getUTCDay() % 6 !== 0 && !nyseHolidays.has(iso)) rsessions.push(iso);
}
const monthEnds = rsessions.filter((d, k) => k === rsessions.length - 1 || rsessions[k + 1].slice(0, 7) !== d.slice(0, 7));
const universe = ["AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK.B", "JPM", "V", "UNH", "JNJ", "XOM", "PG", "MA",
  "HD", "CVX", "LLY", "ABBV", "MRK", "PEP", "KO", "AVGO", "COST", "WMT", "MCD", "CSCO", "TMO", "ACN", "ABT", "ADBE", "CRM",
  "NFLX", "AMD", "TXN", "NKE", "ORCL", "QCOM", "INTC", "CAT", "DE", "GE", "AMAT", "LRCX", "COP", "SLB", "NOW", "ISRG", "BKNG", "UBER"];
// Monthly returns: market plus a slowly drifting stock-specific trend, so past winners tend to keep winning a little.
const months = monthEnds.length;
const mkt = Array.from({ length: months }, () => 0.009 + 0.045 * gauss());
const trend = universe.map(() => 0.01 * gauss());
const mret = universe.map((_, i) => mkt.map((m) => { trend[i] = 0.85 * trend[i] + 0.006 * gauss(); return m * (0.8 + 0.4 * rand()) + trend[i] + 0.06 * gauss(); }));
const rule = { universe: 500, hold: 20, sell_below: 30, weight: 0.05 };
const first = monthEnds.indexOf("2020-08-31");
const rebalanceIdx = []; for (let m = first; m < months - 1; m++) rebalanceIdx.push(m); // Aug 2020 .. Nov 2023: 40
const signalAt = (i, m) => mret[i].slice(m - 11, m).reduce((a, r) => a * (1 + r), 1) - 1; // months m-11..m-1: past year, skipping the last month
let held = new Set();
const rebalances = [];
const monthlyPort = [];
for (const m of rebalanceIdx) {
  const ranked = universe.map((s, i) => ({ s, i, sig: signalAt(i, m) })).sort((a, b) => b.sig - a.sig).map((x, k) => ({ ...x, r: k + 1 }));
  const rankOf = new Map(ranked.map((x) => [x.s, x.r]));
  const trades = [];
  for (const s of [...held]) if (rankOf.get(s) > rule.sell_below) { held.delete(s); trades.push({ side: "sell", s, r: rankOf.get(s), why: `Fell to rank ${rankOf.get(s)}, past the sell line at ${rule.sell_below}` }); }
  for (const x of ranked) { if (held.size >= rule.hold) break; if (!held.has(x.s)) { held.add(x.s); trades.push({ side: "buy", s: x.s, r: x.r, why: rebalances.length ? `Entered the top ${rule.hold} (rank ${x.r})` : `Starting position (rank ${x.r})` }); } }
  for (const t of trades) t.cost_bp = Math.round((6 + 8 * rand()) * 10) / 10;
  const sold = new Set(trades.filter((t) => t.side === "sell").map((t) => t.s)), bought = new Set(trades.filter((t) => t.side === "buy").map((t) => t.s));
  const ranking = ranked.filter((x) => x.r <= 35 || sold.has(x.s)).map((x) => ({
    s: x.s, r: x.r, sig: Math.round(x.sig * 1000) / 1000,
    st: bought.has(x.s) ? "bought" : sold.has(x.s) ? "sold" : held.has(x.s) ? "held" : "out",
  }));
  const gross = [...held].reduce((a, s) => a + mret[universe.indexOf(s)][m + 1], 0) / held.size;
  const costFrac = trades.reduce((a, t) => a + rule.weight * t.cost_bp / 1e4, 0);
  monthlyPort.push({ m, r: gross - costFrac, costFrac });
  rebalances.push({ date: monthEnds[m], trades, ranking, cost_frac: costFrac });
}
// Daily paths between month ends, then calibrated to H1's recorded result:
// S&P 11.2% a year, strategy 1.1 points a year ahead after costs.
const rdays = rsessions.filter((d) => d >= monthEnds[first] && d <= monthEnds[months - 1]);
function dailyPath(monthly) {
  const out = []; let v = 1;
  monthly.forEach((r, j) => {
    const days = rdays.filter((d) => d > monthEnds[first + j] && d <= monthEnds[first + j + 1]);
    const noise = days.map(() => 0.009 * gauss()); const mean = noise.reduce((a, x) => a + x, 0) / days.length;
    const step = Math.log(1 + r) / days.length;
    days.forEach((d, k) => { v *= Math.exp(step + noise[k] - mean); out.push(v); });
  });
  return [1, ...out];
}
const stratRaw = dailyPath(monthlyPort.map((x) => x.r));
const spyRaw = dailyPath(rebalanceIdx.map((m) => mkt[m + 1]));
const years = (rdays.length - 1) / 252;
const tilt = (path, annual) => { const k = Math.pow((1 + annual) ** years / path.at(-1), 1 / (path.length - 1)); return path.map((x, j) => x * k ** j); };
const strat = tilt(stratRaw, 0.112 + 0.011), spyR = tilt(spyRaw, 0.112);
const replay = {
  idea_id: "h1", sample: true, window: "Aug 31, 2020 to Dec 29, 2023", start_equity: 100000, cost_bp_assumed: 10, rule,
  days: rdays.map((d, j) => ({ date: d, v: r2(100000 * strat[j]), b: r2(100000 * spyR[j]) })),
  rebalances: rebalances.map((r) => ({ ...r, cost_usd: r2(r.cost_frac * 100000 * strat[rdays.indexOf(r.date)]) })).map(({ cost_frac, ...r }) => r),
  end: { vs_spy: 0.011, luck: 0.7262, tries: 2, trial: "sample trial" },
};
research.replays = { h1: replay };

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
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.141, benchmark_annual_return: 0.112, max_drawdown: -0.187, verdict: "pass", cost_bp: 10 }, on_paper: true,
      rule: { universe: 500, measure: "past-year return, skipping the last month", hold: 20, sell_below: 30, weight: 0.05, tolerance: 0.01, check: "on the last trading day of each month" } },
    { id: "daily-momentum", name: "Daily momentum", idea: "Same idea as monthly momentum, but rechecked every day before the open.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.128, benchmark_annual_return: 0.112, max_drawdown: -0.224, verdict: "pass", cost_bp: 10 }, on_paper: true,
      rule: { universe: 500, measure: "past-year return, skipping the last month", hold: 10, sell_below: 20, weight: 0.1, tolerance: 0.02, check: "every trading day before the open" } },
    { id: "profitability", name: "Profitability", idea: "Own companies that turn the most gross profit per dollar of assets.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.119, benchmark_annual_return: 0.112, max_drawdown: -0.162, verdict: "pass", cost_bp: 10 }, on_paper: true,
      rule: { universe: 500, measure: "gross profit divided by total assets", hold: 20, sell_below: 20, weight: 0.05, tolerance: 0.01, check: "on the last trading day of each month" } },
  ],
  books: [
    { id: "main", name: "main", strategy_id: "h1-monthly-momentum", cadence: "monthly", started_on: "2026-10-09", start_equity: 100008.9,
      status: { state: "running" }, equity: main, cash: last(main), positions: [], orders: [], expected_tracking_error: 0.06,
      last_run: { at: "2026-10-09T13:05:00Z", outcome: "ok", summary: "Opened the book. Holding cash until the first rebalance." },
      next_run: { at: "2026-10-30T13:05:00Z", what: "First rebalance" } },
    { id: "daily", name: "daily", strategy_id: "daily-momentum", cadence: "daily", started_on: "2026-08-03", start_equity: 100000,
      status: { state: "running" }, equity: daily, cash: r2(last(daily) * 0.021), positions: dailyPositions, orders: dailyOrders, expected_tracking_error: 0.08, last_run: { at: "2026-10-09T13:25:00Z", outcome: "ok", summary: "Sold 1, bought 1. 2 orders filled." },
      next_run: { at: "2026-10-12T13:25:00Z", what: "Daily check" } },
    { id: "b3", name: "b3", strategy_id: "profitability", cadence: "monthly", started_on: "2026-09-01", start_equity: 100000,
      status: { state: "running" }, equity: b3, cash: r2(last(b3) * 0.012), positions: b3Positions, orders: b3Orders, expected_tracking_error: 0.05, last_run: { at: "2026-09-30T13:05:00Z", outcome: "ok", summary: "Rebalanced 20 names. 14 orders filled." },
      next_run: { at: "2026-10-30T13:05:00Z", what: "Monthly rebalance" } },
  ],
  alerts: [],
  research,
};

writeFileSync(new URL("./app-data.json", import.meta.url), JSON.stringify(data, null, 1) + "\n");
console.log("sessions", sessions.length, "portfolio", portfolio.length, "last", portfolio.at(-1));
