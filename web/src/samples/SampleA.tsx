import "@fontsource-variable/public-sans";
import { money, pct, pts, shortDate, signedMoney, tone } from "@/lib/format";
import { model, paths } from "./model";
import "./sampleA.css";

/** A — product UI in the spirit of Linear / Stripe: light, crisp, dense, calm. */
export function SampleA() {
  const m = model();
  const p = paths([m.cmp.you.map((x) => x.value), m.cmp.spy.map((x) => x.value)], 1000, 220);
  const gap = m.cmp.youRet - m.cmp.spyRet;
  return (
    <div className="sA">
      <aside className="sA-side">
        <p className="sA-brand">TradePartner</p>
        <nav>
          <a className="on">Overview</a><a>Books</a><a>Research <span className="sA-count">5</span></a>
        </nav>
        <p className="sA-acct">Paper account</p>
      </aside>
      <main className="sA-main">
        <header className="sA-head"><h1>Overview</h1><span className="sA-tag">Sample data</span></header>
        <section className="sA-metrics">
          <div><p className="l">Total value</p><p className="v">{money(m.total)}</p><p className={`s ${tone(m.today.rel)}`}>{signedMoney(m.today.abs)} ({pct(m.today.rel)}) Friday</p></div>
          <div><p className="l">Since {shortDate(m.start)}</p><p className={`v ${tone(m.cmp.youRet)}`}>{pct(m.cmp.youRet)}</p><p className="s">{pts(gap)} ahead of the S&amp;P 500 ({pct(m.cmp.spyRet)})</p></div>
          <div><p className="l">Needs you</p><p className="v">Nothing</p><p className="s">Next run Monday 9:25 am</p></div>
        </section>
        <section className="sA-panel">
          <div className="sA-panel-h"><h2>Return vs S&amp;P 500</h2><div className="sA-seg"><button>1W</button><button>1M</button><button disabled>3M</button><button className="on">All</button></div></div>
          <svg viewBox="0 0 1000 220" preserveAspectRatio="none" className="sA-chart">
            <line x1="0" x2="1000" y1={p.zero} y2={p.zero} className="zero" />
            <path d={p.lines[1]} className="spy" /><path d={p.lines[0]} className="you" />
          </svg>
          <p className="sA-legend"><i className="k you" />You {pct(m.cmp.youRet)}<i className="k spy" />S&amp;P 500 {pct(m.cmp.spyRet)}</p>
        </section>
        <section className="sA-panel">
          <div className="sA-panel-h"><h2>Books</h2></div>
          <table className="sA-table">
            <thead><tr><th>Book</th><th>Strategy</th><th className="r">Value</th><th className="r">Return</th><th className="r">vs S&amp;P</th><th>Next run</th></tr></thead>
            <tbody>{m.books.map((b) => (
              <tr key={b.id}><td className="b">{b.name}</td><td className="m">{b.strategy}</td><td className="r n">{money(b.value)}</td>
                <td className={`r n ${tone(b.ret)}`}>{pct(b.ret)}</td><td className={`r n ${b.young ? "m" : tone(b.gap)}`}>{b.young ? "—" : `${b.gap > 0 ? "+" : "−"}${pts(b.gap)}`}</td><td className="m">{b.next}</td></tr>
            ))}</tbody>
          </table>
        </section>
        <section className="sA-panel">
          <div className="sA-panel-h"><h2>Waiting on you</h2><span className="m">5</span></div>
          <ul className="sA-list">{m.waiting.map((w) => <li key={w.id}><span className="t">{w.title}</span><span className="m">{w.effort}</span></li>)}</ul>
        </section>
      </main>
    </div>
  );
}
