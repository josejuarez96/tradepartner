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

// ---- Names, SAMPLE ----------------------------------------------------------
// Real large-cap tickers so the screens read naturally. Which ones a book holds,
// their ranks, signals, prices and trades are all invented.
const NAMES = [
  ["AAPL", "Apple"], ["MSFT", "Microsoft"], ["NVDA", "NVIDIA"], ["AMZN", "Amazon"], ["GOOGL", "Alphabet"], ["META", "Meta Platforms"],
  ["AVGO", "Broadcom"], ["TSLA", "Tesla"], ["BRK.B", "Berkshire Hathaway"], ["JPM", "JPMorgan Chase"], ["LLY", "Eli Lilly"], ["V", "Visa"],
  ["UNH", "UnitedHealth"], ["XOM", "Exxon Mobil"], ["MA", "Mastercard"], ["COST", "Costco"], ["HD", "Home Depot"], ["PG", "Procter & Gamble"],
  ["JNJ", "Johnson & Johnson"], ["ORCL", "Oracle"], ["NFLX", "Netflix"], ["ABBV", "AbbVie"], ["BAC", "Bank of America"], ["CRM", "Salesforce"],
  ["KO", "Coca-Cola"], ["CVX", "Chevron"], ["WMT", "Walmart"], ["AMD", "AMD"], ["MRK", "Merck"], ["PEP", "PepsiCo"], ["TMO", "Thermo Fisher"],
  ["ADBE", "Adobe"], ["LIN", "Linde"], ["ACN", "Accenture"], ["MCD", "McDonald's"], ["CSCO", "Cisco"], ["ABT", "Abbott"], ["WFC", "Wells Fargo"],
  ["GE", "GE Aerospace"], ["IBM", "IBM"], ["PM", "Philip Morris"], ["INTU", "Intuit"], ["NOW", "ServiceNow"], ["QCOM", "Qualcomm"], ["TXN", "Texas Instruments"],
  ["CAT", "Caterpillar"], ["DHR", "Danaher"], ["ISRG", "Intuitive Surgical"], ["AMGN", "Amgen"], ["VZ", "Verizon"], ["GS", "Goldman Sachs"],
  ["AMAT", "Applied Materials"], ["BKNG", "Booking"], ["PFE", "Pfizer"], ["SPGI", "S&P Global"], ["RTX", "RTX"], ["UBER", "Uber"], ["NEE", "NextEra Energy"],
  ["T", "AT&T"], ["CMCSA", "Comcast"], ["LOW", "Lowe's"], ["HON", "Honeywell"], ["MS", "Morgan Stanley"], ["UNP", "Union Pacific"], ["PGR", "Progressive"],
  ["AXP", "American Express"], ["BLK", "BlackRock"], ["COP", "ConocoPhillips"], ["SYK", "Stryker"], ["ETN", "Eaton"], ["BSX", "Boston Scientific"],
  ["TJX", "TJX Companies"], ["VRTX", "Vertex"], ["C", "Citigroup"], ["LMT", "Lockheed Martin"], ["PANW", "Palo Alto Networks"], ["ADP", "ADP"],
  ["MDT", "Medtronic"], ["SCHW", "Charles Schwab"], ["BMY", "Bristol-Myers Squibb"], ["ADI", "Analog Devices"], ["GILD", "Gilead"], ["MU", "Micron"],
  ["SBUX", "Starbucks"], ["DE", "Deere"], ["CB", "Chubb"], ["LRCX", "Lam Research"], ["MMC", "Marsh McLennan"], ["PLD", "Prologis"], ["ANET", "Arista Networks"],
  ["KLAC", "KLA"], ["BA", "Boeing"], ["KKR", "KKR"], ["SO", "Southern Co"], ["MO", "Altria"], ["ICE", "Intercontinental Exchange"], ["SHW", "Sherwin-Williams"],
  ["DUK", "Duke Energy"], ["ELV", "Elevance Health"], ["CI", "Cigna"], ["APH", "Amphenol"], ["MCK", "McKesson"], ["CME", "CME Group"], ["ZTS", "Zoetis"],
  ["TT", "Trane"], ["CDNS", "Cadence Design"], ["SNPS", "Synopsys"], ["PH", "Parker-Hannifin"], ["WM", "Waste Management"], ["CTAS", "Cintas"],
  ["EQIX", "Equinix"], ["MSI", "Motorola Solutions"], ["PYPL", "PayPal"], ["CMG", "Chipotle"], ["CRWD", "CrowdStrike"], ["ORLY", "O'Reilly Automotive"],
  ["ITW", "Illinois Tool Works"], ["NOC", "Northrop Grumman"], ["GD", "General Dynamics"], ["MMM", "3M"], ["USB", "U.S. Bancorp"], ["APD", "Air Products"],
  ["EMR", "Emerson"], ["MAR", "Marriott"], ["CEG", "Constellation Energy"], ["ECL", "Ecolab"], ["PNC", "PNC"], ["FDX", "FedEx"], ["AJG", "Arthur J. Gallagher"],
  ["TDG", "TransDigm"], ["CSX", "CSX"], ["ROP", "Roper"], ["NSC", "Norfolk Southern"], ["HCA", "HCA Healthcare"], ["AZO", "AutoZone"], ["COF", "Capital One"],
  ["ABNB", "Airbnb"], ["FTNT", "Fortinet"], ["WELL", "Welltower"], ["TFC", "Truist"], ["AFL", "Aflac"], ["SLB", "Schlumberger"], ["PCAR", "PACCAR"],
  ["GM", "General Motors"], ["DLR", "Digital Realty"], ["OKE", "ONEOK"], ["HLT", "Hilton"], ["SRE", "Sempra"], ["TRV", "Travelers"], ["ADSK", "Autodesk"],
  ["URI", "United Rentals"], ["JCI", "Johnson Controls"], ["BK", "BNY Mellon"], ["CPRT", "Copart"], ["SPG", "Simon Property"], ["MPC", "Marathon Petroleum"],
  ["AEP", "American Electric Power"], ["PSX", "Phillips 66"], ["ROST", "Ross Stores"], ["GWW", "W.W. Grainger"], ["KMB", "Kimberly-Clark"], ["FICO", "FICO"],
  ["AMP", "Ameriprise"], ["PWR", "Quanta Services"], ["MET", "MetLife"], ["ALL", "Allstate"], ["FAST", "Fastenal"], ["NEM", "Newmont"], ["O", "Realty Income"],
  ["D", "Dominion Energy"], ["PAYX", "Paychex"], ["CMI", "Cummins"], ["AIG", "AIG"], ["LHX", "L3Harris"], ["KR", "Kroger"], ["HWM", "Howmet"],
  ["MSCI", "MSCI"], ["TEL", "TE Connectivity"], ["VLO", "Valero"], ["F", "Ford"], ["PRU", "Prudential"], ["KDP", "Keurig Dr Pepper"], ["AME", "AMETEK"],
  ["CCI", "Crown Castle"], ["CTVA", "Corteva"], ["ODFL", "Old Dominion"], ["EW", "Edwards Lifesciences"], ["OXY", "Occidental"], ["IT", "Gartner"],
  ["CBRE", "CBRE"], ["DHI", "D.R. Horton"], ["LEN", "Lennar"], ["VRSK", "Verisk"], ["IR", "Ingersoll Rand"], ["YUM", "Yum! Brands"], ["EXC", "Exelon"],
  ["A", "Agilent"], ["CTSH", "Cognizant"], ["GIS", "General Mills"], ["SYY", "Sysco"], ["MNST", "Monster Beverage"], ["XEL", "Xcel Energy"], ["IDXX", "IDEXX"],
  ["HES", "Hess"], ["DD", "DuPont"], ["NUE", "Nucor"], ["GLW", "Corning"], ["HPQ", "HP"], ["EA", "Electronic Arts"], ["ACGL", "Arch Capital"],
  ["RMD", "ResMed"], ["DAL", "Delta Air Lines"], ["EFX", "Equifax"], ["MLM", "Martin Marietta"], ["VMC", "Vulcan Materials"], ["IQV", "IQVIA"],
  ["DOW", "Dow"], ["HIG", "Hartford"], ["ED", "Consolidated Edison"], ["EBAY", "eBay"], ["WAB", "Wabtec"], ["ROK", "Rockwell Automation"],
  ["FANG", "Diamondback Energy"], ["DXCM", "Dexcom"], ["AVB", "AvalonBay"], ["TSCO", "Tractor Supply"], ["MCHP", "Microchip"], ["XYL", "Xylem"],
  ["ON", "ON Semiconductor"], ["CDW", "CDW"], ["ANSS", "Ansys"], ["PLTR", "Palantir"], ["DECK", "Deckers"], ["AXON", "Axon"], ["VST", "Vistra"],
  ["TTWO", "Take-Two"], ["NVR", "NVR"], ["PHM", "PulteGroup"], ["BR", "Broadridge"], ["HUBB", "Hubbell"], ["STLD", "Steel Dynamics"], ["NTAP", "NetApp"],
];
// Deterministic shuffle, so each book draws its own names.
const shuffled = (arr) => { const a = [...arr]; for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(rand() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };

// ---- Books, SAMPLE, following the engine's real rule -------------------------
// The rule (docs/hypotheses/h1-momentum-12-1.md, b3-gross-profitability.md):
// rank the 1000 largest US stocks on the measure, hold the top 10%, equal
// weight; every rebalance trades each holding back to its equal weight; costs
// 15 bp per side. No buffer: a name that drops out of the top 10% is sold at
// the next rebalance.
const RULES = {
  momentum: { universe: 1000, measure: "past-year return, skipping the last month", top_fraction: 0.1, per_side_bps: 15, fill_price: "close" },
  profitability: { universe: 1000, measure: "gross profit divided by total assets", top_fraction: 0.1, per_side_bps: 15, fill_price: "close" },
};
const scoredToday = 968; // names with a score today (the rest lack a year of prices or a filing)
const HOLD = Math.round(scoredToday * 0.1); // 97

function bookPositions(series, pool, cashFrac, signalOf, bought_on, monthly) {
  const total = last(series), invested = total * (1 - cashFrac);
  const held = pool.slice(0, HOLD);
  // Rank today: a daily book was rebalanced this morning, so it holds exactly
  // the top names; a monthly book has drifted since its last rebalance.
  // Rank at the book's last rebalance: the paper journal's signals rows exist
  // only on rebalance sessions, so a monthly book shows September's ranks.
  const ranks = held.map((_, k) => k + 1);
  return held.map(([symbol, name], k) => {
    const weight = (1 - cashFrac) / HOLD * (1 + (monthly ? 0.06 : 0.008) * gauss());
    const value = r2(total * weight);
    const price = r2(20 + 400 * rand() ** 2);
    const avg_cost = r2(price / (1 + (monthly ? 0.03 : 0.06) * gauss() + 0.01));
    const shares = Math.round((value / price) * 100) / 100;
    const since = monthly ? bought_on : series[Math.floor(rand() * (series.length - 1))].date;
    return { symbol, name, weight, target_weight: 1 / HOLD, value, shares, price, avg_cost, bought_on: since, rank: ranks[k],
      signal: signalOf(ranks[k]), unrealized_pnl: r2(shares * (price - avg_cost)) };
  }).sort((a, b) => a.rank - b.rank).map((p, _k, all) => {
    // Weights and values sum exactly to the invested amount.
    const sum = all.reduce((a, x) => a + x.weight, 0), weight = p.weight * (1 - cashFrac) / sum;
    return { ...p, weight, value: r2(total * weight), shares: Math.round((total * weight / p.price) * 100) / 100 };
  });
}
const momSignal = (r) => Math.round((1.6 * Math.exp(-r / 45) + 0.18) * 1000) / 1000;
const gpaSignal = (r) => Math.round((0.66 * Math.exp(-r / 120) + 0.05) * 1000) / 1000;
const dailyPool = shuffled(NAMES), b3Pool = shuffled(NAMES);
const dailyPositions = bookPositions(daily, dailyPool, 0.004, momSignal, null, false);
const b3Positions = bookPositions(b3, b3Pool, 0.006, gpaSignal, "2026-09-01", true);

// Orders, SAMPLE: the names that entered and left at each rebalance, one order
// each. The small trades back to equal weight are one summary line per
// rebalance (the engine places them as orders too; trades under the minimum
// order size are skipped as dust). expected_price is the price when the order
// was decided; cost_bp is how much worse the fill was.
let oid = 0;
const ord = (placed_at, side, symbol, shares, status, expected_price, fill_price, why, filled = status === "filled" ? shares : 0, note) => ({
  id: `o${++oid}`, placed_at, side, symbol, shares, filled_shares: filled, status, expected_price, fill_price,
  cost_bp: fill_price ? Math.round(((side === "buy" ? fill_price - expected_price : expected_price - fill_price) / expected_price) * 1e4 * 10) / 10 : null,
  why, ...(note ? { note } : {}),
});
const fill = (px, side) => r2(px * (1 + (side === "buy" ? 1 : -1) * (2 + 10 * rand()) / 1e4));
function swaps(at, held, pool, n, value) {
  const out = [];
  const leaving = pool.slice(HOLD + 5, HOLD + 5 + n);
  leaving.forEach(([s], k) => { const px = r2(30 + 300 * rand()); out.push(ord(at, "sell", s, Math.round(value / px), "filled", px, fill(px, "sell"), `Fell to rank ${HOLD + 4 + Math.round(3 + 25 * rand())}, out of the top ${HOLD}`)); });
  held.filter((p) => p.bought_on === at.slice(0, 10)).slice(0, n).forEach((p) => out.push(ord(at, "buy", p.symbol, Math.round(value / p.price), "filled", p.avg_cost, fill(p.avg_cost, "buy"), `Entered the top ${HOLD} (rank ${p.rank})`)));
  return out;
}
const dailyDays = daily.map((p) => p.date).slice(-6).reverse();
// Make sure each recent day has at least one entry to show.
dailyDays.forEach((d, k) => { dailyPositions[(k * 13 + 40) % HOLD].bought_on = d; });
const dailyRebalances = dailyDays.map((d, k) => ({ date: d, entries: 1 + (k % 3), exits: 1 + (k % 3), reweighted: 58 + Math.round(20 * rand()), skipped_dust: Math.round(6 * rand()),
  cost_usd: r2(40 + 60 * rand()) }));
const dailyOrders = dailyDays.flatMap((d, k) => swaps(`${d}T13:35:00Z`, dailyPositions, dailyPool.slice(k * 4), dailyRebalances[k].entries, last(daily) / HOLD));
// One order that went unfilled, so that state is designed.
const unfilledAt = `${dailyDays[3]}T13:35:00Z`;
dailyOrders.push(ord(unfilledAt, "buy", dailyPositions[22].symbol, Math.round(last(daily) / HOLD / dailyPositions[22].price), "cancelled", dailyPositions[22].price, null,
  `Entered the top ${HOLD} (rank ${dailyPositions[22].rank})`, 0, "The price moved past the limit before it filled; cancelled at the close and retried next session"));
dailyOrders.sort((a, b) => b.placed_at.localeCompare(a.placed_at));
const b3Rebalances = [{ date: "2026-09-30", entries: 9, exits: 9, reweighted: 86, skipped_dust: 2, cost_usd: 118.4 }];
b3Positions.slice(80, 89).forEach((p) => { p.bought_on = "2026-09-30"; });
const b3Orders = swaps("2026-09-30T13:35:00Z", b3Positions, b3Pool, 9, last(b3) / HOLD);

// The lab (stages, trials, results, decisions) lives in sample/lab.json since
// the rebuild around the owner's routines; this file keeps the books and H1's
// replay for the workstation view ("how it was computed").
const research = {};


// ---- Charts for each holding, SAMPLE ----------------------------------------
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
// and rank. A daily book holds a name only while it ranks in the top; a
// monthly book's ranks drift between rebalances.
function candles(book, position, orders, monthly) {
  const dates = book.map((p) => p.date);
  const n = dates.length;
  const closes = new Array(n);
  closes[n - 1] = position.price;
  for (let k = n - 2; k >= 0; k--) closes[k] = closes[k + 1] / (1 + 0.003 + 0.017 * gauss());
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
      volume: Math.round((4 + 3 * Math.abs(gauss())) * 1e6) };
  });
  for (const o of orders.filter((x) => x.symbol === position.symbol && x.fill_price)) {
    const b = bars.find((x) => x.date === o.placed_at.slice(0, 10));
    if (b) { b.low = Math.min(b.low, r2(o.fill_price * 0.997)); b.high = Math.max(b.high, r2(o.fill_price * 1.003)); }
  }
  const ranks = new Array(n);
  let r = position.rank;
  for (let k = n - 1; k >= 0; k--) {
    const heldThen = position.bought_on && dates[k] >= position.bought_on;
    r = Math.max(1, monthly || !heldThen ? r : Math.min(HOLD, r));
    if (dates[k] === position.bought_on) r = Math.min(r, HOLD);
    ranks[k] = Math.round(r);
    r += 2.2 * gauss();
  }
  return { bars, ranks: dates.map((date, k) => ({ date, rank: ranks[k] })) };
}
for (const p of dailyPositions) Object.assign(p, candles(daily, p, dailyOrders, false));
for (const p of b3Positions) Object.assign(p, candles(b3, p, b3Orders, true));

// ---- Backtest replay for monthly momentum (H1), SAMPLE ----------------------
// H1's real rule and window: the 1000 largest US stocks, ranked on the past
// year's return skipping the last month, the top 10% held at equal weight,
// rebalanced on the last session of each month, 15 bp per side, filled at the
// close, Aug 31, 2020 to Dec 29, 2023. The universe, prices and trades are
// invented and calibrated to H1's recorded result.
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
const months = monthEnds.length;
const U = 1000;
// Named stocks carry the trends that reach the top; the unnamed rest of the
// thousand sit lower down, so every name near the cut has a ticker.
const uname = (i) => (i < NAMES.length ? NAMES[i][0] : null);
const mkt = Array.from({ length: months }, () => 0.009 + 0.045 * gauss());
const trend = Array.from({ length: U }, (_, i) => (i < NAMES.length ? 0.012 * gauss() : -0.05 + 0.012 * gauss()));
const mret = trend.map((_, i) => mkt.map((m) => {
  trend[i] = i < NAMES.length ? 0.85 * trend[i] + 0.0055 * gauss() : 0.97 * trend[i] + 0.03 * -0.05 + 0.002 * gauss();
  return m * (0.8 + 0.4 * rand()) + trend[i] + (i < NAMES.length ? 0.06 : 0.025) * gauss();
}));
const first = monthEnds.indexOf("2020-08-31");
const rebalanceIdx = []; for (let m = first; m < months - 1; m++) rebalanceIdx.push(m); // Aug 2020 .. Nov 2023: 40
const signalAt = (i, m) => mret[i].slice(m - 11, m).reduce((a, r) => a * (1 + r), 1) - 1;
const BINS = { lo: -0.8, hi: 2.0, n: 28 };
let held = new Set();
let weights = new Map();
const rebalances = [];
const monthlyPort = [];
for (const m of rebalanceIdx) {
  // Universe: the thousand, minus names without a year of prices or a price on the day (ADR 0006's rules, sample counts).
  const noHistory = 12 + Math.round(18 * rand());
  const excluded = new Set(); while (excluded.size < noHistory) { const i = NAMES.length + Math.floor(rand() * (U - NAMES.length)); excluded.add(i); }
  const scored = [];
  for (let i = 0; i < U; i++) if (!excluded.has(i)) scored.push({ i, sig: signalAt(i, m) });
  scored.sort((a, b) => b.sig - a.sig);
  scored.forEach((x, k) => { x.r = k + 1; });
  const hold = Math.round(scored.length * 0.1);
  const top = new Set(scored.slice(0, hold).map((x) => x.i));
  const exits = [...held].filter((i) => !top.has(i)).map((i) => scored.find((x) => x.i === i) ?? { i, r: null, sig: null }); // null rank: dropped from the universe
  const entries = scored.slice(0, hold).filter((x) => !held.has(x.i));
  // Back to equal weight: every kept name trades its drift away.
  let reweightNotional = 0, reweighted = 0;
  for (const i of top) if (held.has(i)) { const d = Math.abs((weights.get(i) ?? 1 / hold) - 1 / hold); reweightNotional += d; if (d > 0.0002) reweighted++; }
  const swapNotional = (exits.length + entries.length) / hold;
  const notional = rebalances.length ? swapNotional + reweightNotional : 1; // the first rebalance buys everything
  const costFrac = notional * 15 / 1e4;
  // Next month: equal-weight return of the held names, and the drifted weights.
  const rets = [...top].map((i) => [i, mret[i][m + 1]]);
  const gross = rets.reduce((a, [, r]) => a + r, 0) / hold;
  weights = new Map(rets.map(([i, r]) => [i, (1 / hold) * (1 + r) / (1 + gross)]));
  held = top;
  monthlyPort.push({ m, r: gross - costFrac, costFrac, notional });
  const hist = Array(BINS.n).fill(0);
  for (const x of scored) hist[Math.max(0, Math.min(BINS.n - 1, Math.floor(((x.sig - BINS.lo) / (BINS.hi - BINS.lo)) * BINS.n)))]++;
  const row = (x) => ({ s: uname(x.i), r: x.r, sig: x.sig == null ? null : Math.round(x.sig * 1000) / 1000 });
  rebalances.push({
    // Field names follow trial_rebalances: n_universe, n_excluded_no_history, n_targets, turnover, cost_paid, the exit counts.
    date: monthEnds[m], n_universe: U, n_excluded_no_history: noHistory,
    n_delisting_exits: rand() < 0.15 ? 1 : 0, n_stale_exits: 0, n_missing_fill: rand() < 0.08 ? 1 : 0,
    n_scored: scored.length, n_targets: hold, cut: Math.round(scored[hold - 1].sig * 1000) / 1000, hist,
    top: scored.slice(0, 8).map(row),
    near: scored.slice(hold - 8, hold + 8).map((x) => ({ ...row(x), held: top.has(x.i) })),
    entries: entries.map(row), exits: exits.map(row), reweighted, turnover: Math.round(notional * 1000) / 1000,
    held: [...top].map(uname),
  });
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
// Cost sensitivity: the engine evaluates every cost level from the same run.
const annualNotional = monthlyPort.slice(1).reduce((a, x) => a + x.notional, 0) / (monthlyPort.length - 1) * 12;
const costLevels = [0, 15, 30, 60, 100].map((bp) => ({ bp, vs_spy: Math.round((0.011 + (15 - bp) * annualNotional / 1e4) * 10000) / 10000 }));
const replay = {
  idea_id: "h1", sample: true, trial: 2, window: { start: "2020-08-31", end: "2023-12-29" }, start_equity: 100000,
  rule: RULES.momentum, holdout: { start: "2024-01-01", end: "2026-09-30" },
  days: rdays.map((d, j) => ({ date: d, v: r2(100000 * strat[j]), b: r2(100000 * spyR[j]) })),
  bins: BINS,
  rebalances: rebalances.map((r) => ({ ...r, cost_paid: r2(monthlyPort[rebalances.indexOf(r)].costFrac * 100000 * strat[rdays.indexOf(r.date)]) })),
  cost_levels: costLevels, annual_turnover: Math.round(annualNotional * 100) / 100,
  end: { vs_spy: 0.011, dsr: 0.7262, n_trials: 2, distinct: 1, periods: 40 },
  identity: { params: "sample", code: "sample", data_cutoff: "2023-12-29" },
};
// Workstation extras, both derived from the run's daily equity (trial_equity):
// month-by-month returns and the fall from the running peak.
{
  const days = replay.days;
  const ends = [...days.filter((d, k) => k === days.length - 1 || days[k + 1].date.slice(0, 7) !== d.date.slice(0, 7))];
  replay.monthly = ends.slice(1).map((d, k) => ({ month: d.date.slice(0, 7), s: Math.round((d.v / ends[k].v - 1) * 10000) / 10000, b: Math.round((d.b / ends[k].b - 1) * 10000) / 10000 }));
  let pv = 0, pb = 0;
  replay.drawdown = days.map((d) => { pv = Math.max(pv, d.v); pb = Math.max(pb, d.b); return { date: d.date, s: Math.round((d.v / pv - 1) * 10000) / 10000, b: Math.round((d.b / pb - 1) * 10000) / 10000 }; });
}
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
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.123, benchmark_annual_return: 0.112, max_drawdown: -0.187, verdict: "pass", cost_bp: 15 }, on_paper: true,
      rule: { ...RULES.momentum, check: "on the last trading day of each month" } },
    { id: "daily-momentum", name: "Daily momentum", idea: "Same idea as monthly momentum, but rechecked every day before the open.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.116, benchmark_annual_return: 0.112, max_drawdown: -0.224, verdict: "pass", cost_bp: 15 }, on_paper: true,
      rule: { ...RULES.momentum, check: "every trading day before the open" } },
    { id: "profitability", name: "Profitability", idea: "Own companies that turn the most gross profit per dollar of assets.",
      backtest: { period: "2020-08-31 to 2023-12-29", annual_return: 0.106, benchmark_annual_return: 0.112, max_drawdown: -0.162, verdict: "pass", cost_bp: 15 }, on_paper: true,
      rule: { ...RULES.profitability, check: "on the last trading day of each month" } },
  ],
  books: [
    { id: "main", name: "main", strategy_id: "h1-monthly-momentum", cadence: "monthly", started_on: "2026-10-09", start_equity: 100008.9,
      status: { state: "running" }, equity: main, cash: last(main), positions: [], orders: [], rebalances: [], expected_tracking_error: 0.06,
      last_run: { at: "2026-10-09T13:05:00Z", outcome: "ok", summary: "Opened the book. Holding cash until the first rebalance." },
      next_run: { at: "2026-10-30T13:05:00Z", what: "First rebalance" } },
    { id: "daily", name: "daily", strategy_id: "daily-momentum", cadence: "daily", started_on: "2026-08-03", start_equity: 100000,
      status: { state: "running" }, equity: daily, cash: r2(last(daily) * 0.004), positions: dailyPositions, orders: dailyOrders, rebalances: dailyRebalances, expected_tracking_error: 0.08, last_run: { at: "2026-10-09T13:35:00Z", outcome: "ok", summary: `Sold ${dailyRebalances[0].exits}, bought ${dailyRebalances[0].entries}, traded ${dailyRebalances[0].reweighted} back to equal weight.` },
      next_run: { at: "2026-10-12T13:35:00Z", what: "Daily rebalance" } },
    { id: "b3", name: "b3", strategy_id: "profitability", cadence: "monthly", started_on: "2026-09-01", start_equity: 100000,
      status: { state: "running" }, equity: b3, cash: r2(last(b3) * 0.006), positions: b3Positions, orders: b3Orders, rebalances: b3Rebalances, expected_tracking_error: 0.05, last_run: { at: "2026-09-30T13:35:00Z", outcome: "ok", summary: "Sold 9, bought 9, traded 86 back to equal weight." },
      next_run: { at: "2026-10-30T13:35:00Z", what: "Monthly rebalance" } },
  ],
  alerts: [],
  research,
};

writeFileSync(new URL("./app-data.json", import.meta.url), JSON.stringify(data, null, 1) + "\n");
console.log("sessions", sessions.length, "portfolio", portfolio.length, "last", portfolio.at(-1));
