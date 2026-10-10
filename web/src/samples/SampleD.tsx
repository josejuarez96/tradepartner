import "@fontsource-variable/source-sans-3";
import { money, pct, pts, shortDate, signedMoney, tone } from "@/lib/format";
import { comparison, rangeStart, sample, type RangeKey } from "@/lib/data";
import { model, paths } from "./model";
import "./sampleD.css";

/** D — a brokerage statement in the spirit of Fidelity / Schwab: plain, tabular, trustworthy. */
export function SampleD() {
  const m = model();
  const p = paths([m.cmp.you.map((x) => x.value), m.cmp.spy.map((x) => x.value)], 1000, 180);
  const own = sample.portfolio.equity.map((x) => ({ date: x.date, v: x.index }));
  const last = own.at(-1)!.date;
  const rows: [string, RangeKey][] = [["Past week", "1W"], ["Past month", "1M"], [`Since ${shortDate(m.start)}`, "ALL"]];
  return (
    <div className="sD">
      <header className="sD-bar"><span className="sD-brand">TradePartner</span><nav><a className="on">Summary</a><a>Books</a><a>Research</a></nav><span className="sD-acct">Paper account (sample data)</span></header>
      <main className="sD-main">
        <h1>Account summary</h1>
        <p className="sD-asof">Prices as of Friday, October 9 close</p>
        <section className="sD-box">
          <h2>Balances</h2>
          <dl className="sD-kv">
            <div><dt>Total value, all books</dt><dd className="n big">{money(m.total)}</dd></div>
            <div><dt>Change on Friday</dt><dd className={`n ${tone(m.today.rel)}`}>{signedMoney(m.today.abs)} ({pct(m.today.rel)})</dd></div>
            <div><dt>Items needing your attention</dt><dd>None</dd></div>
          </dl>
        </section>
        <section className="sD-box">
          <h2>Performance against the S&amp;P 500</h2>
          <table className="sD-t"><thead><tr><th>Period</th><th className="r">Your books</th><th className="r">S&amp;P 500</th><th className="r">Difference</th></tr></thead>
            <tbody>{rows.map(([label, k]) => { const c = comparison(own, sample.benchmark.closes, rangeStart(last, k)); const g = c.youRet - c.spyRet;
              return <tr key={k}><td>{label}</td><td className={`r n ${tone(c.youRet)}`}>{pct(c.youRet)}</td><td className={`r n ${tone(c.spyRet)}`}>{pct(c.spyRet)}</td><td className={`r n ${tone(g)}`}>{g > 0 ? "+" : "−"}{pts(g)}</td></tr>; })}</tbody></table>
          <svg viewBox="0 0 1000 180" preserveAspectRatio="none" className="sD-chart"><line x1="0" x2="1000" y1={p.zero} y2={p.zero} className="zero" /><path d={p.lines[1]} className="spy" /><path d={p.lines[0]} className="you" /></svg>
          <p className="sD-legend"><i className="you" />Your books <i className="spy" />S&amp;P 500</p>
        </section>
        <section className="sD-box">
          <h2>Books</h2>
          <table className="sD-t"><thead><tr><th>Book</th><th>Strategy</th><th className="r">Market value</th><th className="r">Return since start</th><th>Next scheduled run</th></tr></thead>
            <tbody>{m.books.map((b) => <tr key={b.id}><td className="b">{b.name}</td><td>{b.strategy}</td><td className="r n">{money(b.value)}</td><td className={`r n ${tone(b.ret)}`}>{pct(b.ret)}</td><td>{b.next}</td></tr>)}</tbody></table>
        </section>
        <section className="sD-box">
          <h2>Research: waiting on you (5)</h2>
          <ol className="sD-ol">{m.waiting.map((w) => <li key={w.id}><span>{w.title}</span><span className="m">{w.effort}</span></li>)}</ol>
        </section>
      </main>
    </div>
  );
}
