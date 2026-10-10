import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import { money, pct, pts, shortDate, signedMoney, tone } from "@/lib/format";
import { model, paths } from "./model";
import "./sampleC.css";

/** C — a dense dark terminal in the spirit of TradingView / Bloomberg: many numbers, chart as the hero. */
export function SampleC() {
  const m = model();
  const you = m.cmp.you.map((x) => x.value), spy = m.cmp.spy.map((x) => x.value);
  const p = paths([you, spy], 1000, 340, 12);
  const gap = m.cmp.youRet - m.cmp.spyRet;
  const ticks = [-0.06, -0.04, -0.02, 0];
  const all = [...you, ...spy], lo = Math.min(...all), hi = Math.max(...all);
  const ty = (v: number) => 12 + (1 - (v - lo) / (hi - lo)) * (340 - 24);
  return (
    <div className="sC">
      <header className="sC-bar">
        <span className="sC-brand">TradePartner</span>
        <span className="sC-q"><span className="l">Total</span><span className="n">{money(m.total)}</span><span className={`n ${tone(m.today.rel)}`}>{pct(m.today.rel)}</span></span>
        <span className="sC-q"><span className="l">Since {shortDate(m.start)}</span><span className={`n ${tone(m.cmp.youRet)}`}>{pct(m.cmp.youRet)}</span></span>
        <span className="sC-q"><span className="l">S&amp;P 500</span><span className={`n ${tone(m.cmp.spyRet)}`}>{pct(m.cmp.spyRet)}</span></span>
        <span className="sC-q"><span className="l">Needs you</span><span className="n">0</span></span>
        <span className="sC-tag">Paper account, sample data</span>
      </header>
      <div className="sC-grid">
        <section className="sC-panel sC-chartp">
          <div className="sC-ph"><span>Return vs S&amp;P 500</span><span className="sC-r"><b>1W</b><b>1M</b><b className="off">3M</b><b className="on">All</b></span></div>
          <div className="sC-chartwrap">
            <svg viewBox="0 0 1000 340" preserveAspectRatio="none" className="sC-chart">
              {ticks.map((t) => <line key={t} x1="0" x2="1000" y1={ty(t)} y2={ty(t)} className="grid" />)}
              <path d={`${p.lines[0]}L1000 340L0 340Z`} className="area" />
              <path d={p.lines[1]} className="spy" /><path d={p.lines[0]} className="you" />
            </svg>
            <div className="sC-axis">{ticks.map((t) => <span key={t} style={{ top: `${(ty(t) / 340) * 100}%` }}>{pct(t, 1)}</span>)}</div>
          </div>
          <p className="sC-foot"><span className="you">You {pct(m.cmp.youRet)}</span><span className="spy">S&amp;P 500 {pct(m.cmp.spyRet)}</span><span>{pts(gap)} ahead</span><span>{signedMoney(m.today.abs)} Friday</span></p>
        </section>
        <section className="sC-panel">
          <div className="sC-ph"><span>Books</span><span className="m">3 running</span></div>
          <table className="sC-t"><thead><tr><th>Book</th><th className="r">Value</th><th className="r">Return</th><th className="r">vs S&amp;P</th></tr></thead>
            <tbody>{m.books.map((b) => <tr key={b.id}><td>{b.name}<small>{b.strategy}</small></td><td className="r n">{money(b.value)}</td>
              <td className={`r n ${tone(b.ret)}`}>{pct(b.ret)}</td><td className={`r n ${b.young ? "m" : tone(b.gap)}`}>{b.young ? "—" : `${b.gap > 0 ? "+" : "−"}${pts(b.gap)}`}</td></tr>)}</tbody></table>
        </section>
        <section className="sC-panel">
          <div className="sC-ph"><span>Waiting on you</span><span className="att">5</span></div>
          <ul className="sC-l">{m.waiting.map((w) => <li key={w.id}><span>{w.title}</span><span className="m">{w.effort}</span></li>)}</ul>
        </section>
      </div>
    </div>
  );
}
