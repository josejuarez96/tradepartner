import { useMemo, useState } from "react";
import type { Alert, AppData, Book } from "../lib/types";
import { RANGES, comparison, lastChange, rangeStart, type RangeKey } from "../lib/data";
import { clock, longDate, money, pct, pts, shortDate, signedMoney, tone, weekdayDate } from "../lib/format";
import { ReturnChart } from "../components/ReturnChart";
import { PeriodTiles } from "../components/PeriodTiles";
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
  const own = useMemo(() => port.map((p) => ({ date: p.date, v: p.index })), [port]);

  const periods = useMemo(
    () => RANGES.map((r) => {
      const from = rangeStart(lastDate, r.key);
      // A period longer than your history has no honest number of its own.
      const covered = !from || from >= own[0].date;
      return { key: r.key, label: r.label, ret: covered ? comparison(own, data.benchmark.closes, from).youRet : null };
    }),
    [own, data.benchmark.closes, lastDate],
  );
  const cmp = useMemo(
    () => comparison(own, data.benchmark.closes, rangeStart(lastDate, range)),
    [own, data.benchmark.closes, lastDate, range],
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
        <div className="hero__main">
          <p id="hero-label" className="micro">All books</p>
          <p className="hero__value num">{money(total)}</p>
          {today && (
            <p className="hero__today">
              <span className={`num ${tone(today.rel)}`}>
                {signedMoney(today.abs)} ({pct(today.rel)})
              </span>
              <span className="hero__when">{weekdayDate(lastDate)}</span>
            </p>
          )}
        </div>
        <HeroStatus data={data} />
      </section>

      <section className="card perf" aria-labelledby="perf-title">
        <header className="perf__head">
          <div>
            <h2 id="perf-title" className="section-title">Against the market</h2>
            <p className="perf__sub">You and the S&amp;P 500, both from 0% at the start</p>
          </div>
          <PeriodTiles label="Time range" periods={periods} value={range} onChange={setRange} />
        </header>

        <dl className="perf__stats">
          <div className="stat">
            <dt><span className={`key key--${tone(cmp.youRet)}`} aria-hidden />You</dt>
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
          tone={tone(cmp.youRet)}
          onScrub={setHover}
          label={`Your return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)} over ${rangeLong}.`}
        />
      </section>

      <section className="card books" aria-labelledby="books-title">
        <header className="books__head">
          <h2 id="books-title" className="section-title">Books</h2>
          <p className="books__count">{summary(data.books)}</p>
        </header>
        <div className="booktable" role="table" aria-label="Books">
          <div className="bookrow bookrow--head" role="row">
            <span role="columnheader" className="micro">Book</span>
            <span role="columnheader" className="micro">Status</span>
            <span role="columnheader" className="micro col-trend">Since start</span>
            <span role="columnheader" className="micro col-num">Value</span>
            <span role="columnheader" className="micro col-num">Return</span>
            <span role="columnheader" className="micro col-num">vs S&amp;P 500</span>
            <span aria-hidden />
          </div>
          {data.books.map((b) => (
            <BookRow key={b.id} book={b} data={data} flagged={data.alerts.some((a) => a.book_id === b.id)} />
          ))}
        </div>
      </section>
    </div>
  );
}

function summary(books: Book[]) {
  const stopped = books.filter((b) => b.status.state === "stopped").length;
  const running = books.length - stopped;
  return stopped ? `${running} running · ${stopped} stopped` : `${running} running`;
}

/** Right side of the hero: is anything waiting on me, what happens next, how fresh is this. */
function HeroStatus({ data }: { data: AppData }) {
  const next = data.books
    .filter((b) => b.status.state === "running")
    .sort((a, b) => a.next_run.at.localeCompare(b.next_run.at))[0];
  const n = data.alerts.length;
  return (
    <div className="hstatus">
      {n === 0 ? (
        <p className="hstatus__line">
          <span className="hstatus__dot hstatus__dot--ok" aria-hidden />
          Nothing needs you
        </p>
      ) : (
        <p className="hstatus__line hstatus__line--attention">
          <span className="hstatus__dot hstatus__dot--attention" aria-hidden />
          {n === 1 ? "1 thing needs you" : `${n} things need you`}
        </p>
      )}
      {next && (
        <p className="hstatus__meta">
          Next: <b>{next.name}</b>, {weekdayDate(next.next_run.at).split(",")[0]} {clock(next.next_run.at)}
        </p>
      )}
      <p className="hstatus__meta">Prices as of {weekdayDate(data.as_of).split(",")[0]} close</p>
    </div>
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
              <span className="alert__when num">{weekdayDate(a.at).split(",")[0]} {clock(a.at)}</span>
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

  let status;
  if (stopped) {
    status = (
      <span className="bstatus bstatus--attention">
        <Icon name="pause" size={14} />
        Stopped by {book.status.by === "you" ? "you" : "the safety check"} at {clock(book.status.at!)}
      </span>
    );
  } else if (flagged) {
    status = <span className="bstatus bstatus--attention"><Icon name="alert" size={14} />Needs a look</span>;
  } else {
    status = (
      <span className="bstatus">
        <span className="bstatus__dot" aria-hidden />
        {book.next_run.what} {weekdayDate(book.next_run.at).replace(/^\w+, /, "")}
      </span>
    );
  }

  return (
    <a
      className="bookrow"
      role="row"
      href={`#books/${book.id}`}
      aria-label={`${book.name}, ${money(value)}, ${pct(cmp.youRet)} since ${longDate(book.started_on)}. Open details.`}
    >
      <span role="cell" className="bookrow__id">
        <span className="bookrow__name">{book.name}</span>
        <span className="bookrow__strategy">{strat?.name} · {book.cadence}</span>
      </span>
      <span role="cell" className="bookrow__status">{status}</span>
      <span role="cell" className="col-trend">
        {young ? <span className="bookrow__young">Day {book.equity.length}</span> : <Sparkline points={book.equity} tone={tone(cmp.youRet)} />}
      </span>
      <span role="cell" className="col-num num bookrow__value">{money(value)}</span>
      <span role="cell" className={`col-num num bookrow__pct ${tone(cmp.youRet)}`}>{pct(cmp.youRet)}</span>
      <span role="cell" className={`col-num num bookrow__vs ${young ? "" : tone(gap)}`}>
        {young ? "—" : `${gap >= 0 ? "+" : "−"}${pts(gap)}`}
      </span>
      <span className="bookrow__chev" aria-hidden><Icon name="chevron" /></span>
    </a>
  );
}
