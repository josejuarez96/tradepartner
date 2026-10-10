import { useMemo, useState, type ReactNode } from "react";
import type { Alert, AppData, Book } from "../lib/types";
import { RANGES, comparison, lastChange, rangeStart, type RangeKey } from "../lib/data";
import { clock, longDate, money, pct, pts, shortDate, signedMoney, tone, weekdayDate } from "../lib/format";
import { ReturnChart } from "../components/ReturnChart";
import "./Overview.css";

const PHRASE: Record<RangeKey, (from: string) => string> = {
  "1W": () => "This past week",
  "1M": () => "This past month",
  "3M": () => "These past three months",
  ALL: (from) => `Since ${shortDate(from)}`,
};

/** Screen 1, "How am I doing?": answered in one sentence, then the numbers behind it. */
export function Overview({ data }: { data: AppData }) {
  const [range, setRange] = useState<RangeKey>("ALL");
  const [hover, setHover] = useState<string | null>(null);

  const port = data.portfolio.equity;
  const today = lastChange(port);
  const total = port.at(-1)!.value;
  const lastDate = port.at(-1)!.date;
  const own = useMemo(() => port.map((p) => ({ date: p.date, v: p.index })), [port]);
  // A range longer than your history has no honest number of its own.
  const available = (k: RangeKey) => { const f = rangeStart(lastDate, k); return !f || f >= own[0].date; };

  const cmp = useMemo(
    () => comparison(own, data.benchmark.closes, rangeStart(lastDate, range)),
    [own, data.benchmark.closes, lastDate, range],
  );
  const at = hover ? cmp.you.findIndex((p) => p.date === hover) : -1;
  const youAt = at >= 0 ? cmp.you[at].value : cmp.youRet;
  const spyAt = at >= 0 ? cmp.spy[at].value : cmp.spyRet;
  const from = cmp.you[0]?.date ?? lastDate;
  const to = at >= 0 ? cmp.you[at].date : lastDate;

  return (
    <div className="overview">
      {data.alerts.length > 0 && <Attention alerts={data.alerts} books={data.books} />}

      <section className="lede" aria-label="How you're doing">
        <h1 className="lede__sentence serif">
          <Sentence lead={PHRASE[range](from)} you={cmp.youRet} spy={cmp.spyRet} />
        </h1>
        <p className="lede__figures mono">
          <span className="lede__total">{money(total)}</span>
          {today && (
            <span>
              <span className={tone(today.rel)}>{signedMoney(today.abs)} ({pct(today.rel)})</span>
              <span className="lede__muted"> on {weekdayDate(lastDate).replace(",", "")}</span>
            </span>
          )}
        </p>
        {data.alerts.length === 0 && <Quiet data={data} />}
      </section>

      <section className="section chart" aria-label="Your return against the S&P 500">
        <div className="chart__bar">
          <p className="legend mono" aria-live="off">
            <span className="legend__item"><i className="legend__you" aria-hidden />you <b className={tone(youAt)}>{pct(youAt)}</b></span>
            <span className="legend__item"><i className="legend__spy" aria-hidden />S&amp;P 500 <b>{pct(spyAt)}</b></span>
            <span className="legend__span">{shortDate(from)} – {shortDate(to)}</span>
          </p>
          <div className="ranges mono" role="radiogroup" aria-label="Time range">
            {RANGES.map((r) => {
              const ok = available(r.key);
              return (
                <button
                  key={r.key}
                  role="radio"
                  aria-checked={r.key === range}
                  aria-disabled={!ok || undefined}
                  title={ok ? undefined : "Not enough history yet"}
                  className="ranges__opt"
                  onClick={() => ok && setRange(r.key)}
                >
                  {r.label.toLowerCase()}
                </button>
              );
            })}
          </div>
        </div>
        <ReturnChart
          you={cmp.you}
          spy={cmp.spy}
          onScrub={setHover}
          label={`Your return ${pct(cmp.youRet)} against the S&P 500 ${pct(cmp.spyRet)}, ${shortDate(from)} to ${shortDate(lastDate)}.`}
        />
      </section>

      <section className="section" aria-labelledby="books-title">
        <header className="section__head">
          <h2 id="books-title" className="section__title">Books</h2>
          <p className="section__aside">{summary(data.books)}</p>
        </header>
        <div className="ledger" role="table" aria-label="Books">
          <div className="ledger__row ledger__row--head" role="row">
            <span role="columnheader">Book</span>
            <span role="columnheader">Next</span>
            <span role="columnheader" className="r">Value</span>
            <span role="columnheader" className="r">Since start</span>
            <span role="columnheader" className="r">vs S&amp;P 500</span>
          </div>
          {data.books.map((b) => (
            <BookRow key={b.id} book={b} data={data} flagged={data.alerts.some((a) => a.book_id === b.id)} />
          ))}
        </div>
      </section>
    </div>
  );
}

/** "Since Aug 3 you're up 0.79%, 2.3 points ahead of the S&P 500." */
function Sentence({ lead, you, spy }: { lead: string; you: number; spy: number }) {
  const t = tone(you);
  const move: ReactNode = t === "flat"
    ? <>you're <span className="flat">flat</span></>
    : <>you're <span className={t}>{t === "gain" ? "up" : "down"} {pct(Math.abs(you)).slice(1)}</span></>;
  const gap = you - spy;
  const rel = tone(gap) === "flat"
    ? "level with the S&P 500"
    : `${pts(gap).replace(" pts", " points")} ${gap > 0 ? "ahead of" : "behind"} the S&P 500`;
  return <>{lead} {move}, {rel}.</>;
}

function summary(books: Book[]) {
  const stopped = books.filter((b) => b.status.state === "stopped").length;
  const running = books.length - stopped;
  return stopped ? `${running} running, ${stopped} stopped` : `${running} running`;
}

/** When nothing is wrong the page says so once, quietly, and says what happens next. */
function Quiet({ data }: { data: AppData }) {
  const next = data.books
    .filter((b) => b.status.state === "running")
    .sort((a, b) => a.next_run.at.localeCompare(b.next_run.at))[0];
  return (
    <p className="quiet">
      Nothing needs you.
      {next && <> Next run: <span className="mono">{next.name}</span>, {weekdayDate(next.next_run.at).split(",")[0]} {clock(next.next_run.at)}.</>}
      {" "}Prices as of {weekdayDate(data.as_of).split(",")[0]}'s close.
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
            <p className="alert__kicker mono">needs you · {weekdayDate(a.at).split(",")[0].toLowerCase()} {clock(a.at)}</p>
            <h2 className="alert__title serif">{a.title}</h2>
            <p className="alert__detail">{a.detail}</p>
            <p className="alert__todo"><b>What to do:</b> {a.todo}</p>
            {book && <a className="alert__link mono" href={`#books/${book.id}`}>open {book.name} →</a>}
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

  let next: ReactNode;
  if (stopped) next = <span className="ledger__stopped">stopped by {book.status.by === "you" ? "you" : "the safety check"}, {clock(book.status.at!)}</span>;
  else if (flagged) next = <span className="flag">needs you</span>;
  else next = <>{(book.next_run.what ?? "next run").toLowerCase()}, {shortDate(book.next_run.at).toLowerCase()}</>;

  return (
    <a
      className="ledger__row"
      role="row"
      href={`#books/${book.id}`}
      aria-label={`${book.name}, ${money(value)}, ${pct(cmp.youRet)} since ${longDate(book.started_on)}. Open details.`}
    >
      <span role="cell" className="ledger__book">
        <span className="ledger__name mono">{book.name}</span>
        <span className="ledger__strategy">{strat?.name}, {book.cadence}</span>
      </span>
      <span role="cell" className="ledger__next mono">{next}</span>
      <span role="cell" className="r num ledger__value">{money(value)}</span>
      <span role="cell" className={`r num ${tone(cmp.youRet)}`}>
        {pct(cmp.youRet)}
        <span className="ledger__since">{young ? `day ${book.equity.length}` : `since ${shortDate(book.started_on).toLowerCase()}`}</span>
      </span>
      <span role="cell" className={`r num ledger__vs ${young ? "" : tone(gap)}`}>
        {young ? "—" : `${gap >= 0 ? "+" : "−"}${pts(gap)}`}
      </span>
    </a>
  );
}
