import { useMemo, useState } from "react";
import type { Alert, AppData, Book } from "../lib/types";
import { RANGES, comparison, lastChange, rangeStart, type RangeKey } from "../lib/data";
import { clock, longDate, money, pct, pts, shortDate, signedMoney, tone, weekdayDate } from "../lib/format";
import { ReturnChart } from "../components/ReturnChart";
import { Segmented } from "../components/Segmented";
import { Sparkline } from "../components/Sparkline";
import { Icon } from "../components/Icon";
import "./Overview.css";

/** Screen 1, "How am I doing?": total value, return against the market, and each book in one line. */
export function Overview({ data }: { data: AppData }) {
  const [range, setRange] = useState<RangeKey>("ALL");
  const [hover, setHover] = useState<string | null>(null);

  const port = data.portfolio.equity;
  const today = lastChange(port);
  const total = port.at(-1)!.value;
  const lastDate = port.at(-1)!.date;

  const cmp = useMemo(
    () => comparison(port.map((p) => ({ date: p.date, v: p.index })), data.benchmark.closes, rangeStart(lastDate, range)),
    [port, data.benchmark.closes, lastDate, range],
  );
  const at = hover ? cmp.you.findIndex((p) => p.date === hover) : -1;
  const youRet = at >= 0 ? cmp.you[at].value : cmp.youRet;
  const spyRet = at >= 0 ? cmp.spy[at].value : cmp.spyRet;
  const gap = youRet - spyRet;
  const from = cmp.you[0]?.date;
  const to = at >= 0 ? cmp.you[at].date : lastDate;
  const rangeLong = RANGES.find((r) => r.key === range)!.long;

  return (
    <div className="overview">
      {data.alerts.length > 0 && <Attention alerts={data.alerts} books={data.books} />}

      <section className="hero" aria-labelledby="hero-label">
        <p id="hero-label" className="eyebrow">All books</p>
        <p className="hero__value num">{money(total)}</p>
        {today && (
          <p className="hero__today">
            <span className={`num ${tone(today.rel)}`}>
              {signedMoney(today.abs)} ({pct(today.rel)})
            </span>
            <span className="hero__when">{weekdayDate(lastDate)}</span>
          </p>
        )}
        {data.alerts.length === 0 && <AllClear data={data} />}
      </section>

      <section className="card perf" aria-labelledby="perf-title">
        <header className="perf__head">
          <h2 id="perf-title" className="section-title">Against the market</h2>
          <Segmented label="Time range" options={RANGES} value={range} onChange={setRange} />
        </header>

        <dl className="perf__stats">
          <div className="stat">
            <dt><span className="key key--you" aria-hidden />You</dt>
            <dd className={`num stat__value ${tone(youRet)}`}>{pct(youRet)}</dd>
          </div>
          <div className="stat">
            <dt><span className="key key--spy" aria-hidden />S&amp;P 500</dt>
            <dd className="num stat__value">{pct(spyRet)}</dd>
          </div>
          <div className="stat stat--verdict">
            <dt className="num">{from && `${shortDate(from)} – ${shortDate(to)}`}</dt>
            <dd className={tone(gap)}>
              {tone(gap) === "flat" ? "Level with the market" : gap > 0 ? `Ahead by ${pts(gap)}` : `Behind by ${pts(gap)}`}
            </dd>
          </div>
        </dl>

        <ReturnChart
          you={cmp.you}
          spy={cmp.spy}
          onScrub={setHover}
          label={`Your return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)} over ${rangeLong}.`}
        />
      </section>

      <section className="books" aria-labelledby="books-title">
        <header className="books__head">
          <h2 id="books-title" className="section-title">Books</h2>
          <p className="books__count">{summary(data.books)}</p>
        </header>
        <ul className="card booklist">
          {data.books.map((b) => (
            <li key={b.id}>
              <BookRow book={b} data={data} flagged={data.alerts.some((a) => a.book_id === b.id)} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function summary(books: Book[]) {
  const stopped = books.filter((b) => b.status.state === "stopped").length;
  const running = books.length - stopped;
  return stopped ? `${running} running, ${stopped} stopped` : `${running} running`;
}

function AllClear({ data }: { data: AppData }) {
  const next = data.books
    .filter((b) => b.status.state === "running")
    .sort((a, b) => a.next_run.at.localeCompare(b.next_run.at))[0];
  return (
    <p className="allclear">
      <span className="allclear__ok"><Icon name="check" size={14} /></span>
      <span>
        Nothing needs you.
        {next && <> Next: <b>{next.name}</b>, {weekdayDate(next.next_run.at).split(",")[0]} {clock(next.next_run.at)}.</>}
      </span>
    </p>
  );
}

function Attention({ alerts, books }: { alerts: Alert[]; books: Book[] }) {
  return (
    <section className="attention" aria-label={`${alerts.length} ${alerts.length === 1 ? "thing needs" : "things need"} you`}>
      {alerts.map((a) => {
        const book = books.find((b) => b.id === a.book_id);
        return (
          <article key={a.id} className="alert" role="status">
            <span className="alert__icon"><Icon name="alert" size={18} /></span>
            <div className="alert__body">
              <h2 className="alert__title">{a.title}</h2>
              <p className="alert__detail">{a.detail}</p>
              <p className="alert__todo"><b>What to do:</b> {a.todo}</p>
            </div>
            <div className="alert__side">
              <span className="alert__when num">{clock(a.at)}</span>
              {book && <a className="btn btn--quiet" href={`#books/${book.id}`}>Open {book.name}</a>}
            </div>
          </article>
        );
      })}
    </section>
  );
}

function BookRow({ book, data, flagged }: { book: Book; data: AppData; flagged: boolean }) {
  const strat = data.strategies.find((s) => s.id === book.strategy_id);
  const cmp = comparison(book.equity.map((p) => ({ date: p.date, v: p.value })), data.benchmark.closes, null);
  const value = book.equity.at(-1)!.value;
  const young = book.equity.length < 5;
  const gap = cmp.youRet - cmp.spyRet;
  const stopped = book.status.state === "stopped";

  return (
    <a className="book" href={`#books/${book.id}`} aria-label={`${book.name}, ${money(value)}, ${pct(cmp.youRet)} since ${longDate(book.started_on)}. Open details.`}>
      <div className="book__id">
        <p className="book__name">
          {book.name}
          <span className="book__strategy">{strat?.name}</span>
        </p>
        {flagged && !stopped ? (
          <p className="book__status book__status--stopped">
            <Icon name="alert" size={14} />
            <span>Needs a look, see above</span>
          </p>
        ) : stopped ? (
          <p className="book__status book__status--stopped">
            <Icon name="pause" size={14} />
            <span>Stopped by {book.status.by === "you" ? "you" : "the safety check"} at {clock(book.status.at!)}</span>
          </p>
        ) : (
          <p className="book__status">
            {book.next_run.what} {weekdayDate(book.next_run.at)}
          </p>
        )}
      </div>

      <div className="book__spark">
        {young ? <span className="book__young">Too early for a trend</span> : <Sparkline points={book.equity} />}
      </div>

      <div className="book__money">
        <p className="num book__value">{money(value)}</p>
        <p className="book__since">since {shortDate(book.started_on)}</p>
      </div>

      <div className="book__ret">
        <p className={`num book__pct ${tone(cmp.youRet)}`}>{pct(cmp.youRet)}</p>
        <p className="num book__vs">{young ? `Day ${book.equity.length}` : `${gap >= 0 ? "+" : "−"}${pts(gap)} vs S&P`}</p>
      </div>

      <span className="book__chev" aria-hidden><Icon name="chevron" /></span>
    </a>
  );
}
