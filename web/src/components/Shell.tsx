import type { ReactNode } from "react";
import { ThemeSwitch } from "./ThemeSwitch";
import "./Shell.css";

export type Screen = "overview" | "books" | "strategies";
const NAV: { key: Screen; label: string }[] = [
  { key: "overview", label: "overview" },
  { key: "books", label: "books" },
  { key: "strategies", label: "strategies" },
];

interface Props {
  screen: Screen;
  mode: "paper" | "live";
  sample: boolean;
  attention: number;
  children: ReactNode;
  footer?: ReactNode;
}

/** A plain masthead: name, three words of navigation, and which money this is. Text tabs on phone. */
export function Shell({ screen, mode, sample, attention, children, footer }: Props) {
  const link = (n: (typeof NAV)[number], cls: string) => (
    <a key={n.key} href={`#${n.key}`} className={cls} aria-current={n.key === screen ? "page" : undefined}>
      {n.label}
      {n.key === "overview" && attention > 0 && <span className="navcount" aria-label={`, ${attention} need you`}>{attention}</span>}
    </a>
  );
  return (
    <div className="shell">
      <header className="mast">
        <div className="mast__inner">
          <a className="wordmark" href="#overview">tradepartner</a>
          <nav className="mast__nav" aria-label="Main">{NAV.map((n) => link(n, "mast__link"))}</nav>
          <p className="mast__mode">
            <span>{mode === "paper" ? "paper account" : "live account"}</span>
            {sample && <span className="mast__sample" title="Every number on this page is made up">sample data</span>}
          </p>
          <ThemeSwitch />
        </div>
      </header>
      <main className="page">{children}</main>
      {footer && <footer className="pagefoot">{footer}</footer>}
      <nav className="tabbar" aria-label="Main">{NAV.map((n) => link(n, "tabbar__link"))}</nav>
    </div>
  );
}
