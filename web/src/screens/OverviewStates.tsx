import { Skeleton } from "../components/Skeleton";
import { Icon } from "../components/Icon";
import "./Overview.css";
import "./OverviewStates.css";

/** Loading: the page's real shape in grey, so nothing jumps when numbers arrive. */
export function OverviewLoading() {
  return (
    <div className="overview" aria-busy="true" aria-label="Loading your books">
      <section className="hero">
        <Skeleton w={72} h={14} />
        <div style={{ marginTop: "var(--space-3)" }}><Skeleton w="min(320px, 80%)" h={40} /></div>
        <div style={{ marginTop: "var(--space-2)" }}><Skeleton w={180} h={16} /></div>
      </section>
      <section className="card perf">
        <div className="perf__head"><Skeleton w={150} h={18} /><Skeleton w={132} h={32} r="var(--radius-pill)" /></div>
        <div className="perf__stats"><Skeleton w={72} h={44} /><Skeleton w={72} h={44} /></div>
        <Skeleton w="100%" h={240} r="var(--radius-md)" />
      </section>
      <section className="books">
        <div className="books__head"><Skeleton w={60} h={18} /></div>
        <div className="card booklist">
          {[0, 1, 2].map((i) => (
            <div key={i} className="state-row"><Skeleton w="40%" h={16} /><Skeleton w={96} h={16} /></div>
          ))}
        </div>
      </section>
    </div>
  );
}

/** Error: what happened in plain words, what still holds, one thing to do. */
export function LoadError({ onRetry, retrying }: { onRetry: () => void; retrying: boolean }) {
  return (
    <div className="state card" role="alert">
      <span className="state__icon state__icon--attention"><Icon name="alert" size={20} /></span>
      <h1 className="state__title">Can't reach TradePartner on this computer</h1>
      <p className="state__body">
        The app couldn't load your books. Your strategies keep running on their schedule; this only affects what you see here.
      </p>
      <button className="btn btn--primary" onClick={onRetry} disabled={retrying} aria-live="polite">
        <Icon name="refresh" size={14} /> {retrying ? "Trying again…" : "Try again"}
      </button>
      <p className="state__hint">If this keeps happening, the computer running TradePartner may be asleep or offline.</p>
    </div>
  );
}

/** Empty: first run, before any book exists. */
export function NoBooks() {
  return (
    <div className="state card">
      <span className="state__icon"><Icon name="books" size={20} /></span>
      <h1 className="state__title">No books running yet</h1>
      <p className="state__body">
        Once a strategy starts trading on paper, its value and how it compares with the S&amp;P 500 show up here.
      </p>
      <a className="btn btn--quiet" href="#strategies">See strategies</a>
    </div>
  );
}
