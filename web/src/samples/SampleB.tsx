import "@fontsource-variable/figtree";
import { money, pct, pts, shortDate, signedMoney, tone } from "@/lib/format";
import { model, paths } from "./model";
import "./sampleB.css";

/** B — consumer finance in the spirit of Robinhood / Wealthsimple: one number, one chart, lots of air. */
export function SampleB() {
  const m = model();
  const p = paths([m.cmp.you.map((x) => x.value), m.cmp.spy.map((x) => x.value)], 1000, 300);
  const gap = m.cmp.youRet - m.cmp.spyRet;
  const spark = (s: number[]) => paths([s], 80, 28).lines[0];
  return (
    <div className={`sB sB-${tone(m.cmp.youRet)}`}>
      <header className="sB-top"><span className="sB-brand">TradePartner</span><span className="sB-tag">Paper account, sample data</span></header>
      <main className="sB-main">
        <p className="sB-total">{money(m.total)}</p>
        <p className={`sB-today ${tone(m.today.rel)}`}>{signedMoney(m.today.abs)} ({pct(m.today.rel)}) <span>Today</span></p>
        <svg viewBox="0 0 1000 300" preserveAspectRatio="none" className="sB-chart">
          <path d={p.lines[1]} className="spy" /><path d={p.lines[0]} className="you" />
        </svg>
        <div className="sB-ranges"><button>1W</button><button>1M</button><button disabled>3M</button><button className="on">All</button></div>
        <p className="sB-vs">Since {shortDate(m.start)} you're {pct(m.cmp.youRet)}. The S&amp;P 500 is {pct(m.cmp.spyRet)}, so you're {pts(gap)} ahead.</p>
        <div className="sB-ok">Nothing needs you right now</div>
        <h2>Books</h2>
        <ul className="sB-list">{m.books.map((b) => (
          <li key={b.id}><div><p className="n">{b.name}</p><p className="s">{b.strategy}</p></div>
            {!b.young && <svg viewBox="0 0 80 28" className={`sB-spark ${tone(b.ret)}`}><path d={spark(b.spark)} /></svg>}
            <span className={`sB-pill ${tone(b.ret)}`}>{pct(b.ret)}</span></li>
        ))}</ul>
        <h2>Waiting on you</h2>
        <ul className="sB-list">{m.waiting.map((w) => <li key={w.id}><div><p className="n">{w.title}</p><p className="s">{w.effort}</p></div><span className="chev">›</span></li>)}</ul>
      </main>
    </div>
  );
}
