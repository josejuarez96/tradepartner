import { Skeleton } from "../components/Skeleton";
import "./Overview.css";
import "./OverviewStates.css";

/** Loading: the page's real shape in grey, so nothing jumps when numbers arrive. */
export function OverviewLoading() {
  return (
    <div className="overview" aria-busy="true" aria-label="Loading your books">
      <section className="lede">
        <Skeleton w="92%" h={40} />
        <div style={{ marginTop: "var(--space-2)" }}><Skeleton w="60%" h={40} /></div>
        <div style={{ marginTop: "var(--space-5)" }}><Skeleton w={260} h={18} /></div>
      </section>
      <section className="section">
        <div className="chart__bar"><Skeleton w={300} h={16} /><Skeleton w={150} h={20} /></div>
        <Skeleton w="100%" h={300} />
      </section>
      <section className="section">
        <div className="section__head"><Skeleton w={80} h={24} /></div>
        {[0, 1, 2].map((i) => (
          <div key={i} className="state-row"><Skeleton w="28%" h={16} /><Skeleton w={110} h={16} /></div>
        ))}
      </section>
    </div>
  );
}

/** Error: what happened in plain words, what still holds, one thing to do. */
export function LoadError({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <div className="state" role="alert">
      <p className="state__kicker mono">can't load</p>
      <h1 className="state__title serif">This page can't reach TradePartner on your computer.</h1>
      <p className="state__body">
        Your strategies keep running on their schedule. Only this view is affected. If it keeps happening, the
        computer running TradePartner may be asleep or offline.
      </p>
      <button className="btn btn--solid" onClick={onRetry} disabled={retrying} aria-live="polite">
        {retrying ? "trying again…" : "try again"}
      </button>
    </div>
  );
}

/** Empty: first run, before any book exists. */
export function NoBooks() {
  return (
    <div className="state">
      <p className="state__kicker mono">no books yet</p>
      <h1 className="state__title serif">Nothing is trading yet.</h1>
      <p className="state__body">
        When a strategy starts trading on paper, this page will tell you how it's doing against the S&amp;P 500.
      </p>
      <a className="btn" href="#strategies">see strategies</a>
    </div>
  );
}
