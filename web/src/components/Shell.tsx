import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import "./Shell.css";

export type Screen = "overview" | "books" | "strategies";
const NAV: { key: Screen; label: string; icon: IconName }[] = [
  { key: "overview", label: "Overview", icon: "overview" },
  { key: "books", label: "Books", icon: "books" },
  { key: "strategies", label: "Strategies", icon: "strategies" },
];

interface Props {
  screen: Screen;
  mode: "paper" | "live";
  sample: boolean;
  attention: number;
  children: ReactNode;
  footer?: ReactNode;
}

/** Top bar on desktop, top bar + bottom tabs on phone. Mode is always visible: paper money vs real money. */
export function Shell({ screen, mode, sample, attention, children, footer }: Props) {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar__inner">
          <a className="brand" href="#overview" aria-label="TradePartner, overview">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
              <rect width="32" height="32" rx="8" fill="var(--ink)" />
              <path d="M8 21l5-6 4 3 7-8" fill="none" stroke="var(--ink-inverse)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span>TradePartner</span>
          </a>
          <nav className="topnav" aria-label="Main">
            {NAV.map((n) => (
              <a key={n.key} href={`#${n.key}`} className="topnav__link" aria-current={n.key === screen ? "page" : undefined}>
                {n.label}
                {n.key === "overview" && attention > 0 && <span className="dot" aria-label={`${attention} need you`} />}
              </a>
            ))}
          </nav>
          <div className="topbar__tags">
            {sample && <span className="tag tag--sample" title="Every number on this page is made up">Sample data</span>}
            <span className={`tag tag--${mode}`}>{mode === "paper" ? "Paper money" : "Real money"}</span>
          </div>
        </div>
      </header>
      <main className="page">{children}</main>
      {footer && <footer className="pagefoot">{footer}</footer>}
      <nav className="tabbar" aria-label="Main">
        {NAV.map((n) => (
          <a key={n.key} href={`#${n.key}`} className="tabbar__link" aria-current={n.key === screen ? "page" : undefined}>
            <span className="tabbar__icon">
              <Icon name={n.icon} size={20} />
              {n.key === "overview" && attention > 0 && <span className="dot dot--tab" aria-label={`${attention} need you`} />}
            </span>
            {n.label}
          </a>
        ))}
      </nav>
    </div>
  );
}
