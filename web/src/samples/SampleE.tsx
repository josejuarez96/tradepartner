import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import { sample, comparison } from "@/lib/data";
import { money, pct, pts, shortDate, signedMoney, tone } from "@/lib/format";
import { stats } from "./stats";
import "./sampleE.css";

const W = 1000, H = 300, PAD = 14;

/**
 * E: Robinhood's frame (one number, one big chart, a side list), made
 * research-grade: the backtest's expected range on the chart, rebalance and
 * start markers, and the statistics a researcher checks.
 */
export function SampleE() {
  const d = sample;
  const port = d.portfolio.equity;
  const own = port.map((p) => ({ date: p.date, v: p.index }));
  const cmp = comparison(own, d.benchmark.closes, null);
  const you = cmp.you.map((p) => p.value), spy = cmp.spy.map((p) => p.value);
  const dates = cmp.you.map((p) => p.date);
  const s = stats(own.map((p) => p.v), d.benchmark.closes.filter((c) => dates.includes(c.date)).map((c) => c.close));
  const gap = cmp.youRet - cmp.spyRet;
  const last = port.at(-1)!, prev = port.at(-2)!;
  const dayRel = last.index / prev.index - 1, dayAbs = prev.value * dayRel;
  const t = tone(cmp.youRet);

  // The backtest's expected path: level with the S&P (H1's prior), ±1σ of tracking error widening with √t.
  const TE = 0.06; // SAMPLE: tracking error the backtest expects, per year
  const hi = spy.map((v, i) => v + TE * Math.sqrt(i / 252));
  const lo = spy.map((v, i) => v - TE * Math.sqrt(i / 252));
  const all = [...you, ...hi, ...lo];
  const min = Math.min(...all), max = Math.max(...all);
  const x = (i: number) => (i / (you.length - 1)) * W;
  const y = (v: number) => PAD + (1 - (v - min) / (max - min)) * (H - PAD * 2);
  const line = (vs: number[]) => vs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("");
  const band = `${line(hi)}${lo.map((v, i) => [i, v] as const).reverse().map(([i, v]) => `L${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join("")}Z`;
  const marks = [
    { date: "2026-08-31", label: "Rebalance" }, { date: "2026-09-01", label: "b3 starts" },
    { date: "2026-09-30", label: "Rebalance" }, { date: "2026-10-09", label: "main starts" },
  ].map((m) => ({ ...m, i: dates.indexOf(m.date) })).filter((m) => m.i >= 0)
    // Events a few sessions apart share one tick and one label.
    .reduce<{ date: string; label: string; i: number }[]>((acc, m) => {
      const prev = acc.at(-1);
      if (prev && m.i - prev.i <= 3) prev.label = `${prev.label}, ${m.label.toLowerCase()}`;
      else acc.push({ ...m });
      return acc;
    }, []);

  const books = d.books.map((b) => {
    const c = comparison(b.equity.map((p) => ({ date: p.date, v: p.value })), d.benchmark.closes, null);
    const vs = b.equity.map((p) => p.value), lo2 = Math.min(...vs), hi2 = Math.max(...vs);
    const sp = vs.length > 4 ? vs.map((v, i) => `${i ? "L" : "M"}${((i / (vs.length - 1)) * 64).toFixed(1)} ${(2 + (1 - (v - lo2) / (hi2 - lo2 || 1)) * 20).toFixed(1)}`).join("") : null;
    return { b, c, sp, strat: d.strategies.find((x) => x.id === b.strategy_id)!.name };
  });
  const onPaper = d.research.ideas.filter((i) => i.stage === "on_paper");

  return (
    <div className={`sE sE-${t}`}>
      <header className="sE-top">
        <span className="sE-brand">TradePartner</span>
        <nav><a className="on">Overview</a><a>Books</a><a>Research <b>5</b></a></nav>
        <span className="sE-acct">Paper account, sample data</span>
      </header>

      <div className="sE-wrap">
        <main className="sE-main">
          <p className="sE-label">All books</p>
          <p className="sE-total num">{money(last.value)}</p>
          <p className={`sE-day num ${tone(dayRel)}`}>{signedMoney(dayAbs)} ({pct(dayRel)}) <span>Friday</span></p>

          <div className="sE-chart">
            <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
              <path d={band} className="band" />
              <path d={line(spy)} className="spy" />
              <path d={line(you)} className="you" />
              {marks.map((m) => <line key={m.date + m.label} x1={x(m.i)} x2={x(m.i)} y1={H - 10} y2={H} className="tick" />)}
            </svg>
            <div className="sE-marks">{marks.map((m) => <span key={m.date + m.label} style={{ left: `${(m.i / (you.length - 1)) * 100}%` }}>{m.label}</span>)}</div>
          </div>
          <div className="sE-legend num">
            <span className="you">You {pct(cmp.youRet)}</span>
            <span className="spy">S&amp;P 500 {pct(cmp.spyRet)}</span>
            <span className="band">Backtest's expected range, ±1σ</span>
            <span className="ticks">Ticks: rebalances and book starts</span>
          </div>
          <div className="sE-ranges"><button>1W</button><button>1M</button><button disabled>3M</button><button className="on">All</button></div>

          <section className="sE-sec">
            <h2>Statistics <span>since {shortDate(dates[0])}, {s.days} trading days</span></h2>
            <dl className="sE-stats num">
              <div><dt>Excess vs S&amp;P</dt><dd className={tone(gap)}>{gap >= 0 ? "+" : "−"}{pts(gap)}</dd></div>
              <div><dt>Luck check (H1)</dt><dd>73% <small>could be luck</small></dd></div>
              <div><dt>Volatility, yearly</dt><dd>{(s.vol * 100).toFixed(1)}%</dd></div>
              <div><dt>Max drawdown</dt><dd className="loss">{pct(s.maxDD, 1)}</dd></div>
              <div><dt>Sharpe, yearly</dt><dd>{s.sharpe.toFixed(2)}</dd></div>
              <div><dt>Beta to S&amp;P</dt><dd>{s.beta.toFixed(2)}</dd></div>
              <div><dt>Tracking error</dt><dd>{(s.te * 100).toFixed(1)}%</dd></div>
              <div><dt>Within expected range</dt><dd className="gain">Yes</dd></div>
            </dl>
            <p className="sE-note">Computed from daily closes, after costs, time-weighted so new money is not a gain. Sharpe uses a zero risk-free rate. 49 days is too short to judge skill; the expected range is what the backtest said to expect.</p>
          </section>

          <section className="sE-sec">
            <h2>Needs you <span className="att">5</span></h2>
            <a className="sE-card">
              <span><b>Run the short-term momentum test</b><small>Everything is built. One overnight run answers whether last month's busiest winners keep rising after costs.</small></span>
              <span className="chev">›</span>
            </a>
            <a className="sE-more">4 more research decisions ›</a>
          </section>
        </main>

        <aside className="sE-rail">
          <section>
            <h2>Books</h2>
            {books.map(({ b, c, sp, strat }) => (
              <a key={b.id} className="sE-row">
                <span className="n"><b>{b.name}</b><small>{strat}</small></span>
                {sp ? <svg viewBox="0 0 64 24" className={`spark ${tone(c.youRet)}`}><path d={sp} /></svg> : <small className="young">day {b.equity.length}</small>}
                <span className={`pill num ${tone(c.youRet)}`}>{pct(c.youRet)}</span>
              </a>
            ))}
          </section>
          <section>
            <h2>On paper, taking their exam</h2>
            {onPaper.map((i) => (
              <a key={i.id} className="sE-row exam">
                <span className="n"><b>{i.name}</b><small className="num">{i.exam?.kind === "paper" ? `${i.exam.done} of ${i.exam.of} ${i.exam.unit}` : "exam on hold"}</small></span>
                <span className="luck num">{i.result ? `${Math.round(i.result.luck * 100)}%` : "—"}<small>luck check</small></span>
              </a>
            ))}
          </section>
        </aside>
      </div>
    </div>
  );
}
